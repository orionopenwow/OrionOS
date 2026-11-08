"""Orion Studio on the MCP bus (docs/ORIONOS_COMPLETE_PLAN.md Part 2, build order step 5).

The Craft apps (PhotoCraft, PdfCraft, from the ArtCraft team) ship their own MCP servers. When one is installed,
Orion starts it over stdio and offers its tools to coding agents as `photocraft.<tool>` / `pdfcraft.<tool>`, with
Orion's usual rules in front:

  * tools that only look (the server marks them read-only, or the name says get/list/info...) run straight away;
  * tools that write save to a NEW file by default: Orion fills in an output path next to the original, or, for
    tools that change a file in place, works on a copy and tells the agent where the copy is;
  * writing over an original (the output already exists, or the agent sets overwrite_original), tools the server
    marks destructive, and any path outside the folders agents may use (mcp.json "roots") need the owner's
    approval: a boardroom decision (`mcp.craft_write`), run by the daemon only after a signed OK.

When an app isn't installed, its `<app>.about` tool answers "not installed, get it in Get apps".

Which apps exist comes from os/getapps/catalog.json ("studio" apps that have agent hooks). How each is started:
  1. ~/.config/orion/craft.json  {"photocraft": {"command": ["/path/to/photocraft-cli", "mcp"]}}  (or "off": true)
  2. `<prefix>-cli mcp` on PATH (the plan: "MCP server (`photocraft-cli mcp`)")
  3. the Flatpak (ai.storyteller.<prefix>):  flatpak run --command=<prefix>-cli <id> mcp
  4. an AppImage in ~/Applications or ~/.local/bin named <Prefix>*.AppImage, started with "mcp"
Options 3 and 4 follow the apps' README conventions as far as we know them; craft.json overrides any guess.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("orion.craft")

try:   # routes resolve their annotations from module globals (this file uses `from __future__ import annotations`)
    from fastapi import Request
except ImportError:   # pragma: no cover - the command-line tools work without FastAPI
    Request = Any  # type: ignore

TASK_TYPE = "mcp.craft_write"
CONFIG_PATH = Path(os.path.expanduser("~/.config/orion/craft.json"))
LIST_TIMEOUT_S = 15
CALL_TIMEOUT_S = 300
PROTOCOL = "2025-06-18"
NOT_INSTALLED = "{name} isn't installed. Get it in Get apps (Orion Studio), then try again."

# Used when the catalog can't be read (it lists the same two today).
FALLBACK_APPS = [
    {"name": "PhotoCraft", "flatpak_id": "ai.storyteller.photocraft", "bundle_prefix": "photocraft"},
    {"name": "PdfCraft", "flatpak_id": "ai.storyteller.pdfcraft", "bundle_prefix": "pdfcraft"},
]


class CraftError(Exception):
    pass


# =====================================================================================  which apps, how to start
@dataclass
class CraftApp:
    name: str            # PhotoCraft
    ns: str              # photocraft (tool prefix, CLI prefix)
    flatpak_id: str
    command: Optional[list[str]] = None   # how to start its MCP server; None = not installed
    how: str = ""                         # plain words: where it was found


def catalog_apps() -> list[dict[str, Any]]:
    """Studio apps with agent hooks (a CLI / MCP server), from Get apps' catalog."""
    try:
        import get_apps
        studio = (get_apps._catalog().get("studio") or {}).get("apps") or []
    except Exception:
        studio = []
    apps = [a for a in studio if a.get("agent_hooks") and a.get("bundle_prefix") and not a.get("coming_soon")]
    return apps or FALLBACK_APPS


def _config() -> dict[str, Any]:
    path = Path(os.path.expanduser(os.environ.get("ORION_CRAFT_CONFIG") or str(CONFIG_PATH)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _flatpak_installed(app_id: str) -> bool:
    home = Path.home()
    return any((d / app_id / "current").exists() for d in (home / ".local/share/flatpak/app",
                                                           Path("/var/lib/flatpak/app")))


def detect(entry: dict[str, Any], cfg: Optional[dict[str, Any]] = None) -> CraftApp:
    ns = str(entry["bundle_prefix"]).lower()
    app = CraftApp(name=str(entry["name"]), ns=ns, flatpak_id=str(entry.get("flatpak_id") or ""))
    own = (cfg if cfg is not None else _config()).get(ns) or {}
    if own.get("off"):
        app.how = "turned off in craft.json"
        return app
    cmd = own.get("command")
    if isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
        app.command, app.how = list(cmd), "set in craft.json"
        return app
    cli = shutil.which(f"{ns}-cli")
    if cli:
        app.command, app.how = [cli, "mcp"], f"{ns}-cli"
        return app
    if app.flatpak_id and _flatpak_installed(app.flatpak_id) and shutil.which("flatpak"):
        app.command = ["flatpak", "run", f"--command={ns}-cli", app.flatpak_id, "mcp"]
        app.how = "Flatpak"
        return app
    for d in (Path.home() / "Applications", Path.home() / ".local/bin"):
        try:
            hits = sorted(p for p in d.iterdir() if p.name.lower().startswith(ns) and p.suffix == ".AppImage"
                          and os.access(p, os.X_OK))
        except OSError:
            continue
        if hits:
            app.command, app.how = [str(hits[-1]), "mcp"], "AppImage"
            return app
    return app


def installed_apps() -> list[CraftApp]:
    cfg = _config()
    return [detect(e, cfg) for e in catalog_apps()]


# =====================================================================================  a tiny MCP client (stdio)
class CraftSession:
    """One Craft MCP server process: handshake, tools/list, tools/call. Synchronous (run it in a thread)."""

    def __init__(self, argv: list[str], cwd: Optional[str] = None, timeout: float = CALL_TIMEOUT_S) -> None:
        self.argv, self.cwd, self.timeout = argv, cwd, timeout
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._err = b""
        self._next = 0

    def __enter__(self) -> "CraftSession":
        try:
            self.proc = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise CraftError(f"couldn't start it ({exc.strerror or exc})")
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        init = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "orion", "version": "1.0"}},
                            timeout=min(self.timeout, LIST_TIMEOUT_S))
        if not isinstance(init, dict):
            raise CraftError("it didn't answer the handshake")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *_a: Any) -> None:
        self.close()

    def _read_out(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _read_err(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._err = (self._err + line)[-2000:]

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise CraftError("it stopped unexpectedly" + self._why())

    def _why(self) -> str:
        tail = self._err.decode("utf-8", "replace").strip().splitlines()
        return f" ({tail[-1][:200]})" if tail else ""

    def request(self, method: str, params: dict[str, Any], timeout: Optional[float] = None) -> Any:
        self._next += 1
        rid = self._next
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CraftError("it took too long to answer")
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                raise CraftError("it took too long to answer")
            if line is None:
                raise CraftError("it stopped unexpectedly" + self._why())
            try:
                msg = json.loads(line)
            except ValueError:
                continue   # stray output: not ours
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:   # a request from the server (sampling, roots...): politely refuse
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32601, "message": "Orion doesn't offer that"}})
                continue
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise CraftError(str(err.get("message") or "it refused")[:300])
            return msg.get("result")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(50):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=LIST_TIMEOUT_S) or {}
            tools += [t for t in res.get("tools") or [] if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        res = self.request("tools/call", {"name": name, "arguments": args}) or {}
        parts = []
        for c in res.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif c.get("type") in ("image", "audio"):
                parts.append(f"[{c['type']} ({c.get('mimeType', '')}) returned]")
            elif c.get("type") in ("resource", "resource_link"):
                r = c.get("resource") or c
                parts.append(f"[file: {r.get('uri') or r.get('name') or ''}]")
        if not parts and res.get("structuredContent") is not None:
            parts.append(json.dumps(res["structuredContent"])[:5000])
        return "\n".join(parts).strip() or "(no output)", bool(res.get("isError"))

    def close(self) -> None:
        p = self.proc
        if p is None:
            return
        try:
            if p.stdin:
                p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform != "win32":
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    p.kill()
            except OSError:
                pass
            p.wait(timeout=5)
        self.proc = None


def list_remote_tools(app: CraftApp, cwd: Optional[str] = None) -> list[dict[str, Any]]:
    if not app.command:
        raise CraftError(NOT_INSTALLED.format(name=app.name))
    with CraftSession(app.command, cwd=cwd, timeout=LIST_TIMEOUT_S) as s:
        return s.list_tools()


def call_remote_tool(app: CraftApp, tool: str, args: dict[str, Any], cwd: Optional[str] = None,
                     timeout: float = CALL_TIMEOUT_S) -> tuple[str, bool]:
    if not app.command:
        raise CraftError(NOT_INSTALLED.format(name=app.name))
    with CraftSession(app.command, cwd=cwd, timeout=timeout) as s:
        return s.call_tool(tool, args)


# =====================================================================================  Orion's rules
PATH_KEY = re.compile(r"(^|_)(paths?|files?|filenames?|inputs?|outputs?|out|dest|destination|targets?|src|"
                      r"sources?|save_as|save_to|dir|folder|directory|images?|documents?|pdfs?|psd|layers_file)"
                      r"($|_)", re.I)
OUTPUT_KEY = re.compile(r"(^|_)(outputs?|out|dest|destination|save_as|save_to|target|export_to|result_path)"
                        r"($|_)", re.I)
READ_NAME = re.compile(r"^(get|list|info|read|inspect|describe|count|search|find|show|preview|metadata|"
                       r"extract_text|text|stats|histogram|validate|version|help)(_|$)", re.I)
OVERWRITE_ARG = "overwrite_original"


@dataclass
class Plan:
    args: dict[str, Any]
    needs_approval: bool = False
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    copies: list[tuple[str, str]] = field(default_factory=list)   # (original, new copy) made before the call
    new_files: list[str] = field(default_factory=list)


def is_read_only(tool: dict[str, Any]) -> bool:
    ann = tool.get("annotations") or {}
    if "readOnlyHint" in ann:
        return bool(ann.get("readOnlyHint"))
    return bool(READ_NAME.match(str(tool.get("name", ""))))


def _path_keys(tool: dict[str, Any], args: dict[str, Any]) -> list[str]:
    props = ((tool.get("inputSchema") or {}).get("properties") or {})
    keys = []
    for k, v in args.items():
        if k == OVERWRITE_ARG or not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str)
                                                                                           for x in v))):
            continue
        spec = props.get(k) or {}
        desc = str(spec.get("description") or "").lower()
        if PATH_KEY.search(k) or spec.get("format") in ("path", "file-path") or " path" in f" {desc}" \
                or "file" in desc.split():
            keys.append(k)
    return keys


def _abs(p: str, cwd: Path) -> Path:
    q = Path(os.path.expanduser(p))
    if not q.is_absolute():
        q = cwd / q
    try:
        return q.resolve(strict=False)
    except (OSError, RuntimeError):
        return q


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(p == r or p.is_relative_to(r) for r in roots)


def new_name(original: Path, tag: str = "orion") -> Path:
    """photo.png -> photo-orion.png, photo-orion-2.png ... (never an existing file)."""
    stem, suffix = original.stem, original.suffix
    for i in range(1, 1000):
        cand = original.with_name(f"{stem}-{tag}{'' if i == 1 else f'-{i}'}{suffix}")
        if not cand.exists():
            return cand
    raise CraftError("couldn't find a free file name next to the original")


def plan_call(tool: dict[str, Any], args: dict[str, Any], roots: list[Path], cwd: Path) -> Plan:
    """Decide how a Craft tool call may run. Pure apart from looking at which files exist."""
    args = dict(args)
    overwrite = bool(args.pop(OVERWRITE_ARG, False))
    plan = Plan(args=args)
    keys = _path_keys(tool, args)
    # 1. every path the call touches must be inside the allowed folders
    for k in keys:
        vals = args[k] if isinstance(args[k], list) else [args[k]]
        for v in vals:
            if not v or "://" in v:
                continue
            p = _abs(v, cwd)
            if not _inside(p, roots):
                plan.needs_approval = True
                plan.reasons.append(f"{v} is outside the folders agents may use")
    if is_read_only(tool):
        return plan
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True and not overwrite:
        plan.needs_approval = True
        plan.reasons.append("the app says this tool can destroy or remove things")
    if overwrite:
        plan.needs_approval = True
        plan.reasons.append("the agent asked to write over the original file")
        return plan
    props = ((tool.get("inputSchema") or {}).get("properties") or {})
    out_keys = [k for k in props if OUTPUT_KEY.search(k) and (props[k] or {}).get("type", "string") == "string"]
    in_keys = [k for k in keys if k not in out_keys]
    inputs = [_abs(v, cwd) for k in in_keys for v in (args[k] if isinstance(args[k], list) else [args[k]]) if v]
    if out_keys:
        k = out_keys[0]
        given = args.get(k)
        if isinstance(given, str) and given:
            p = _abs(given, cwd)
            if p.exists():
                plan.needs_approval = True
                plan.reasons.append(f"it would write over {given}")
            else:
                plan.new_files.append(str(p))
        else:
            first = next((p for p in inputs if p.is_file()), None)
            if first is not None:
                target = new_name(first)
                plan.args[k] = str(target)
                plan.new_files.append(str(target))
                plan.notes.append(f"Saved to a new file: {target} (the original is untouched).")
        return plan
    # 2. no output argument: the tool changes its input file in place -> work on a copy
    files = [(k, p) for k, p in zip(in_keys, (_abs(args[k], cwd) if isinstance(args[k], str) else None
                                              for k in in_keys)) if p is not None and p.is_file()]
    if len(files) == 1:
        k, p = files[0]
        copy = new_name(p)
        plan.copies.append((str(p), str(copy)))
        plan.args[k] = str(copy)
        plan.new_files.append(str(copy))
        plan.notes.append(f"Worked on a copy: {copy} (the original {p.name} is untouched).")
    elif len(files) > 1:
        plan.needs_approval = True
        plan.reasons.append("it may change several of your files in place")
    return plan


def apply_copies(plan: Plan) -> None:
    for src, dst in plan.copies:
        if Path(dst).exists():
            raise CraftError(f"{dst} appeared in the meantime; nothing was changed")
        shutil.copy2(src, dst)


def describe_plan(app: CraftApp, tool: str, plan: Plan) -> str:
    why = "; ".join(plan.reasons) or "it changes files"
    return f"{app.name}: {tool} ({why})"


# =====================================================================================  MCP tools on the bus
def _find(ns: str) -> CraftApp:
    for a in installed_apps():
        if a.ns == ns:
            return a
    raise CraftError(f"{ns} isn't one of the Orion Studio apps.")


def mcp_tools(tools: Any) -> list[Any]:
    """Tools for mcp_tools.OrionTools: `<app>.about` always, plus the app's own tools when it's installed."""
    from mcp_tools import Tool, ToolError, _schema

    out: list[Any] = []
    for app in installed_apps():
        remote: list[dict[str, Any]] = []
        problem = ""
        if app.command:
            try:
                remote = list_remote_tools(app, cwd=_cwd(tools))
            except CraftError as exc:
                problem = f"{app.name} is installed ({app.how}) but its tool server didn't answer: {exc}."
        out.append(Tool(f"{app.ns}.about", f"About {app.name}",
                        f"Whether {app.name} is installed and which of its tools agents can use.", "read",
                        _schema({}), _about(app, remote, problem)))
        for t in remote:
            out.append(ProxyTool.make(tools, app, t))
    return out


def _cwd(tools: Any) -> str:
    try:
        return str(tools.project({}))
    except Exception:
        roots = tools.cfg.resolved_roots()
        return str(roots[0]) if roots else os.getcwd()


def _about(app: CraftApp, remote: list[dict[str, Any]], problem: str) -> Callable[[dict[str, Any]], Any]:
    async def handler(_a: dict[str, Any]) -> str:
        if not app.command:
            return NOT_INSTALLED.format(name=app.name)
        if problem:
            return problem
        names = ", ".join(f"{app.ns}.{t['name']}" for t in remote[:80])
        return (f"{app.name} is installed ({app.how}). {len(remote)} tools: {names}. Tools that change files save "
                "to a new file next to the original unless you set overwrite_original (which asks the user).")
    return handler


def _make_proxy_tool_class():
    from mcp_tools import Tool, ToolError

    class _ProxyTool(Tool):
        """A Craft tool shown with its own schema; Orion's rules run before every call."""

        app: CraftApp
        remote: dict[str, Any]

        @classmethod
        def make(cls, tools: Any, app: CraftApp, remote: dict[str, Any]) -> "_ProxyTool":
            name = f"{app.ns}.{remote['name']}"
            schema = dict(remote.get("inputSchema") or {"type": "object", "properties": {}})
            schema.setdefault("type", "object")
            props = dict(schema.get("properties") or {})
            ro = is_read_only(remote)
            if not ro:
                props[OVERWRITE_ARG] = {"type": "boolean", "description": "Write over the original file instead "
                                        "of saving a new one. Asks the user first."}
            schema["properties"] = props

            async def handler(a: dict[str, Any]) -> str:
                return await run_proxied(tools, app, remote, a)
            t = cls(name, remote.get("title") or f"{app.name}: {remote['name']}",
                    str(remote.get("description") or "")[:1500], "read" if ro else "run", schema, handler)
            t.app, t.remote = app, remote
            return t

        def validate(self, args: Any) -> dict[str, Any]:
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise ToolError("Arguments must be an object.")
            for key in self.schema.get("required") or []:
                if key not in args:
                    raise ToolError(f"Missing argument: {key}")
            return args

        def describe(self) -> dict[str, Any]:
            label = ("Read-only." if self.risk == "read" else
                     "Saves to a new file by default; writing over originals or outside your project folders "
                     "asks the user first.")
            return {"name": self.name, "title": self.title,
                    "description": f"{self.description} ({self.app.name}, Orion Studio) {label}".strip(),
                    "inputSchema": self.schema,
                    "annotations": {"title": self.title, "readOnlyHint": self.risk == "read",
                                    "destructiveHint": False, "idempotentHint": self.risk == "read",
                                    "openWorldHint": False},
                    "_meta": {"orion/risk": self.risk, "orion/app": self.app.name}}

    return _ProxyTool


class _LazyProxy:
    _cls = None

    def make(self, *a: Any) -> Any:
        if _LazyProxy._cls is None:
            _LazyProxy._cls = _make_proxy_tool_class()
        return _LazyProxy._cls.make(*a)


ProxyTool = _LazyProxy()


async def run_proxied(tools: Any, app: CraftApp, remote: dict[str, Any], args: dict[str, Any]) -> str:
    from mcp_tools import DaemonUnavailable, ToolError
    roots = tools.cfg.resolved_roots()
    cwd = Path(_cwd(tools))
    try:
        plan = plan_call(remote, args, roots, cwd)
    except CraftError as exc:
        raise ToolError(str(exc))
    if plan.needs_approval:
        body = {"app": app.ns, "tool": remote["name"], "arguments": args, "cwd": str(cwd)}
        try:
            status, data = await tools.backend.call("POST", "/craft/propose", body)
        except DaemonUnavailable:
            raise ToolError("This needs the user's approval (" + "; ".join(plan.reasons) + "), but Orion isn't "
                            "running so it can't ask. Nothing was changed. Save to a new file instead.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        dec = data.get("decision") or {}
        return (f"Waiting for the user's approval: {dec.get('summary', '')}. Nothing has changed yet. "
                f"Check with approval_status(decision_id=\"{dec.get('id')}\").")
    try:
        apply_copies(plan)
        text, is_error = await asyncio.to_thread(call_remote_tool, app, remote["name"], plan.args, str(cwd))
    except CraftError as exc:
        raise ToolError(f"{app.name}: {exc}")
    if is_error:
        raise ToolError(tools.out(f"{app.name} said: {text}"))
    return tools.out("\n".join([text, *plan.notes]))


# =====================================================================================  daemon side
def install(daemon: Any) -> None:
    """Register the approval task type and its executor (called once from the daemon's start())."""
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(TASK_TYPE, TaskType(TASK_TYPE, "Let a Craft app change your files", "code",
                                                        reversible=False, money=False, sensitive=True))
    board = getattr(daemon, "board", None)
    if board is None:
        return
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (TASK_TYPE,))
    except Exception:
        log.exception("craft autonomy row")
    import mcp_routes
    board.executors[TASK_TYPE] = lambda d: mcp_routes._spawn(d, _exec_approved(d))


async def _exec_approved(d: dict[str, Any]) -> dict[str, Any]:
    det = d["packet"]["details"]
    app = _find(det["app"])
    if not app.command:
        return {"state": "failed", "error": NOT_INSTALLED.format(name=app.name)}
    args = dict(det["arguments"])
    args.pop(OVERWRITE_ARG, None)
    try:
        text, is_error = await asyncio.to_thread(call_remote_tool, app, det["tool"], args, det.get("cwd") or None)
    except CraftError as exc:
        return {"state": "failed", "error": f"{app.name}: {exc}"}
    from mcp_tools import redact
    return {"state": "failed" if is_error else "done", "output": redact(text[-1500:])}


async def propose(daemon: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Re-check a call on the daemon side and, if it really needs approval, put it to the owner."""
    from mcp_tools import McpConfig
    ns, tool_name = str(body.get("app") or ""), str(body.get("tool") or "")
    args = body.get("arguments") or {}
    if not isinstance(args, dict):
        raise CraftError("arguments must be an object")
    app = _find(ns)
    if not app.command:
        raise CraftError(NOT_INSTALLED.format(name=app.name))
    remote = next((t for t in await asyncio.to_thread(list_remote_tools, app) if t["name"] == tool_name), None)
    if remote is None:
        raise CraftError(f"{app.name} has no tool called {tool_name}")
    cfg = McpConfig.load()
    roots = cfg.resolved_roots()
    cwd = Path(str(body.get("cwd") or (roots[0] if roots else Path.home())))
    plan = plan_call(remote, args, roots, cwd)
    if not plan.needs_approval:
        raise CraftError("this call doesn't need approval; the agent can run it directly")
    summary = describe_plan(app, tool_name, plan)[:200]
    details = {"app": app.ns, "app_name": app.name, "tool": tool_name, "arguments": args, "cwd": str(cwd),
               "why_it_asks": "; ".join(plan.reasons)}
    dec = await daemon.board.propose(TASK_TYPE, summary, details, proposer="Coding agent (MCP)", risk="high",
                                     simulated=False)
    return {"id": dec["id"], "summary": dec["summary"], "status": dec["status"]}


def status() -> list[dict[str, Any]]:
    return [{"name": a.name, "id": a.ns, "flatpak_id": a.flatpak_id, "installed": bool(a.command), "how": a.how}
            for a in installed_apps()]


def _default_apps_module():
    import importlib.util
    from config import settings
    for p in (Path("/opt/orion/basics/orion_default_apps.py"),
              Path(settings.repo_root) / "os/basics/orion_default_apps.py"):
        if p.exists():
            spec = importlib.util.spec_from_file_location("orion_default_apps", p)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader
            spec.loader.exec_module(mod)
            return mod
    return None


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/craft", tags=["studio"])

    @router.get("/status")
    async def craft_status() -> Any:
        mod = _default_apps_module()
        try_with = mod.status() if mod else {}
        return {"apps": status(), "try_with_craft": try_with}

    @router.post("/propose")
    async def craft_propose(request: Request) -> Any:
        try:
            body = await request.json()
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            return JSONResponse({"error": "Send a JSON object."}, status_code=400)
        try:
            dec = await propose(get_daemon(), body)
        except CraftError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"decision": dec}

    @router.post("/try-with-craft")
    async def try_with_craft(request: Request) -> Any:
        """{"kind": "pdf"|"images", "on": bool}: make the Craft app the default for that file type (or undo)."""
        try:
            body = await request.json()
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            return JSONResponse({"error": "Send a JSON object."}, status_code=400)
        mod = _default_apps_module()
        if mod is None:
            return JSONResponse({"error": "Default apps aren't set up on this computer."}, status_code=404)
        ok, msg = mod.set_try_craft(str(body.get("kind") or ""), bool(body.get("on")))
        if not ok:
            return JSONResponse({"error": msg}, status_code=400)
        return {"ok": True, "message": msg, "try_with_craft": mod.status()}

    return router
