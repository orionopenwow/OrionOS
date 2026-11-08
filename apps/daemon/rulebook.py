"""One Rulebook (toolkit tool 6): the same instructions for every coding agent.

The project's rules are written once, in `.orion/rules.md`. `sync()` rewrites the files each agent reads:

    CLAUDE.md                  Claude Code
    AGENTS.md                  Codex, Antigravity and other agents that read AGENTS.md
    GEMINI.md                  Gemini CLI
    .cursor/rules/orion.mdc    Cursor (always applied)

Each starts with a "generated from .orion/rules.md, edit there" header. Anything written between
`<!-- rulebook:keep:start -->` and `<!-- rulebook:keep:end -->` in a target is kept on every sync (notes for that
one agent). A hand-written target without markers is moved inside the keep markers the first time, so nothing is
lost. Output is deterministic: syncing twice changes nothing.

Rule format in .orion/rules.md: sections are `## ` headings; each rule is one list item starting with its id,
`- [no-push] Never push without asking.` (indented lines continue a rule). Ids are what violations refer to.

Use it
  * CLI, no daemon needed:  node scripts/rulebook/sync.mjs   (or: python apps/daemon/rulebook.py sync|check|list|add)
  * Daemon routes (local only): GET /rulebook/rules, POST /rulebook/rules {"text", "section"?}, POST /rulebook/sync,
    POST /rulebook/propose (agents: becomes an approval), POST|GET /rulebook/violations,
    DELETE /rulebook/violations/{rule_id}
  * MCP tools: rulebook_list (read), rulebook_add_rule (needs approval: an agent can't change every agent's
    instructions on its own), rulebook_sync (rewrites the generated files from rules.md).

Contract for Proof Gate ("rules an agent keeps breaking")
    record_violation(rule_id: str, agent: str, evidence: str, *, repo_root=None, data_dir=None) -> dict
        rule_id   an id from .orion/rules.md, e.g. "no-push"
        agent     who broke it, e.g. "claude-code", "codex", "gemini", "cursor"
        evidence  short plain text of what happened (secrets are redacted; the first 300 characters are kept)
      returns {"rule_id", "text", "count", "agents": {agent: count}, "flagged", "newly_flagged", "threshold"}
      raises KeyError if rule_id isn't in the rulebook.
      A rule is flagged once it has been broken FLAG_AT (3) times in total; the moment it is flagged the owner
      gets one alert. Counts live in <data_dir>/rulebook_violations.json (machine-local, never committed).
    HTTP: POST /rulebook/violations {"rule_id", "agent", "evidence"} -> the same JSON (404 for an unknown rule).
"""

import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

RULES_FILE = Path(".orion") / "rules.md"
FLAG_AT = 3
KEEP_START = "<!-- rulebook:keep:start -->"
KEEP_END = "<!-- rulebook:keep:end -->"
GENERATED_MARK = "Generated from .orion/rules.md by Orion's One Rulebook"
TARGETS: tuple[tuple[str, str], ...] = (
    ("CLAUDE.md", "Claude Code"),
    ("AGENTS.md", "Codex, Antigravity and other agents"),
    ("GEMINI.md", "Gemini CLI"),
    (".cursor/rules/orion.mdc", "Cursor"),
)
RULE_RE = re.compile(r"^- \[([a-z0-9][a-z0-9-]{0,47})\]\s+(.+)$")
_lock = threading.Lock()


@dataclass
class RuleItem:
    id: str
    text: str
    section: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "section": self.section}


# =====================================================================================  paths
def default_repo_root() -> Path:
    try:
        from config import settings
        return Path(settings.repo_root)
    except Exception:
        return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    try:
        from config import settings
        return Path(settings.data_dir)
    except Exception:
        return Path.home() / ".orion"


def rules_path(repo_root: Optional[Path] = None) -> Path:
    return Path(repo_root or default_repo_root()) / RULES_FILE


# =====================================================================================  parse
def parse(text: str) -> list[RuleItem]:
    rules: list[RuleItem] = []
    section = "General"
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        m = RULE_RE.match(line)
        if m:
            rules.append(RuleItem(m.group(1), m.group(2).strip(), section))
        elif rules and line.startswith("  ") and line.strip() and not line.lstrip().startswith("- "):
            rules[-1].text += " " + line.strip()
        elif not line.strip() or line.startswith("#"):
            continue
    return rules


def read_rules(repo_root: Optional[Path] = None) -> tuple[str, list[RuleItem]]:
    p = rules_path(repo_root)
    if not p.exists():
        raise FileNotFoundError(f"There's no {RULES_FILE.as_posix()} in {p.parent.parent}.")
    text = p.read_text(encoding="utf-8")
    return text, parse(text)


def list_rules(repo_root: Optional[Path] = None, data_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    _, rules = read_rules(repo_root)
    counts = _load_violations(data_dir)
    out = []
    for r in rules:
        v = counts.get(r.id, {})
        out.append({**r.as_dict(), "violations": int(v.get("count", 0)), "flagged": bool(v.get("flagged"))})
    return out


# =====================================================================================  generate
def _header(agent: str) -> str:
    return (f"<!-- {GENERATED_MARK} for {agent}. Don't edit the rules here: edit .orion/rules.md, then run "
            f"`node scripts/rulebook/sync.mjs` (or ask Orion to sync the rules). Notes between the two "
            f"rulebook:keep markers at the end are kept on every sync. -->")


def _body(source: str) -> str:
    """rules.md minus its editing notes (the block between <!-- note --> comments is for humans editing it)."""
    text = re.sub(r"<!--.*?-->\n?", "", source, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def extract_keep(existing: Optional[str]) -> str:
    if not existing:
        return ""
    a, b = existing.find(KEEP_START), existing.find(KEEP_END)
    if a != -1 and b > a:
        return existing[a + len(KEEP_START):b].strip("\n")
    if GENERATED_MARK in existing:
        return ""                     # generated before, markers removed by hand: nothing to keep
    return existing.strip("\n")       # a hand-written file: keep all of it


def render(target: str, agent: str, source: str, keep: str) -> str:
    parts = []
    if target.endswith(".mdc"):
        parts.append("---\ndescription: Orion project rules for every agent (generated from .orion/rules.md)\n"
                     "globs:\nalwaysApply: true\n---\n")
    parts.append(_header(agent) + "\n\n")
    parts.append(_body(source))
    parts.append(f"\n## Notes for {agent} only\n\n{KEEP_START}\n")
    if keep.strip():
        parts.append(keep.rstrip("\n") + "\n")
    parts.append(f"{KEEP_END}\n")
    return "".join(parts)


def plan_sync(repo_root: Optional[Path] = None) -> dict[str, str]:
    """{target path: new content} for every target whose content would change."""
    root = Path(repo_root or default_repo_root())
    source, rules = read_rules(root)
    ids = [r.id for r in rules]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"These rule ids are used twice in .orion/rules.md: {', '.join(dupes)}")
    out: dict[str, str] = {}
    for target, agent in TARGETS:
        p = root / target
        existing = p.read_text(encoding="utf-8") if p.exists() else None
        new = render(target, agent, source, extract_keep(existing))
        if new != existing:
            out[target] = new
    return out


def sync(repo_root: Optional[Path] = None) -> dict[str, Any]:
    """Rewrite the generated files. Returns {"changed": [...], "unchanged": [...]}."""
    root = Path(repo_root or default_repo_root())
    with _lock:
        changes = plan_sync(root)
        for target, content in changes.items():
            p = root / target
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(content.encode("utf-8"))      # LF on every OS: identical output everywhere
            tmp.replace(p)
    return {"changed": sorted(changes), "unchanged": sorted(t for t, _ in TARGETS if t not in changes)}


def check(repo_root: Optional[Path] = None) -> list[str]:
    """Targets that are out of date with .orion/rules.md."""
    return sorted(plan_sync(repo_root))


# =====================================================================================  add a rule
def _slug(text: str, taken: set[str]) -> str:
    stop = {"a", "an", "the", "to", "of", "and", "or", "in", "on", "for", "it", "is", "be", "any", "anything",
            "always", "you", "your", "with", "without", "from", "that", "this"}
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop] or ["rule"]
    base = "-".join(words[:4])[:40].strip("-") or "rule"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def clean_rule_text(text: str) -> str:
    t = " ".join(str(text or "").split())
    t = re.sub(r"^(orion,?\s*)?(please\s+)?(add (a )?rule:?\s*)", "", t, flags=re.I).strip()
    if len(t) < 3:
        raise ValueError("Say what the rule is.")
    if len(t) > 400:
        raise ValueError("Keep a rule under 400 characters.")
    if "<!--" in t or "-->" in t or t.startswith("#"):
        raise ValueError("A rule is plain text (no headings or HTML comments).")
    return t[0].upper() + t[1:] if t[0].islower() else t


def add_rule(text: str, section: str = "General", rule_id: Optional[str] = None,
             repo_root: Optional[Path] = None, do_sync: bool = True) -> dict[str, Any]:
    """Add one rule to .orion/rules.md (under `section`, created if needed) and sync every agent's file."""
    root = Path(repo_root or default_repo_root())
    text = clean_rule_text(text)
    section = " ".join(str(section or "General").split())[:60] or "General"
    if "#" in section or "<" in section:
        raise ValueError("Section names are plain words.")
    with _lock:
        source, rules = read_rules(root)
        for r in rules:
            if r.text.rstrip(".").lower() == text.rstrip(".").lower():
                return {"rule": r.as_dict(), "added": False, "changed": []}
        taken = {r.id for r in rules}
        if rule_id:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", rule_id) or rule_id in taken:
                raise ValueError("That rule id is invalid or already used.")
        rid = rule_id or _slug(text, taken)
        lines = source.splitlines()
        new_line = f"- [{rid}] {text}"
        # Find the section; insert after its last rule (or right after its heading).
        start = next((i for i, l in enumerate(lines) if l.startswith("## ") and l[3:].strip().lower() ==
                      section.lower()), None)
        if start is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", f"## {section}", new_line]
        else:
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            last = max((i for i in range(start + 1, end) if RULE_RE.match(lines[i]) or
                        (lines[i].startswith("  ") and lines[i].strip())), default=start)
            lines.insert(last + 1, new_line)
            section = lines[start][3:].strip()
        p = rules_path(root)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))
        tmp.replace(p)
    changed = sync(root)["changed"] if do_sync else []
    return {"rule": {"id": rid, "text": text, "section": section}, "added": True, "changed": changed}


# =====================================================================================  violations
def _violations_path(data_dir: Optional[Path]) -> Path:
    return Path(data_dir or default_data_dir()) / "rulebook_violations.json"


def _load_violations(data_dir: Optional[Path]) -> dict[str, Any]:
    p = _violations_path(data_dir)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_violations(data_dir: Optional[Path], data: dict[str, Any]) -> None:
    p = _violations_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _redact(text: str) -> str:
    try:
        from mcp_tools import redact
        return redact(text)
    except Exception:
        return text


def record_violation(rule_id: str, agent: str, evidence: str, *, repo_root: Optional[Path] = None,
                     data_dir: Optional[Path] = None) -> dict[str, Any]:
    """Count one broken rule (see the module docstring for the contract)."""
    rule_id = str(rule_id or "").strip()
    _, rules = read_rules(repo_root)
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        raise KeyError(rule_id)
    agent = " ".join(str(agent or "unknown agent").split())[:60] or "unknown agent"
    evidence = _redact(" ".join(str(evidence or "").split())[:2000])[:300]
    with _lock:
        data = _load_violations(data_dir)
        v = data.setdefault(rule_id, {"count": 0, "agents": {}, "recent": [], "flagged": False})
        v["count"] = int(v.get("count", 0)) + 1
        v["agents"][agent] = int(v["agents"].get(agent, 0)) + 1
        v["recent"] = (v.get("recent", []) + [{"at": round(time.time(), 3), "agent": agent,
                                                "evidence": evidence}])[-10:]
        newly = v["count"] >= FLAG_AT and not v.get("flagged")
        if newly:
            v["flagged"] = True
            v["flagged_at"] = round(time.time(), 3)
        _save_violations(data_dir, data)
    if newly:
        worst = max(v["agents"].items(), key=lambda kv: kv[1])[0]
        try:
            import alerts
            alerts.emit("system", "An agent keeps breaking a rule",
                        f"“{rule.text[:120]}” has been broken {v['count']} times (mostly by {worst}). "
                        "Consider making it stricter or adding a check.", ref=f"rulebook:{rule_id}")
        except Exception:
            pass
    return {"rule_id": rule_id, "text": rule.text, "count": v["count"], "agents": dict(v["agents"]),
            "flagged": bool(v["flagged"]), "newly_flagged": newly, "threshold": FLAG_AT}


def violations(data_dir: Optional[Path] = None) -> dict[str, Any]:
    return _load_violations(data_dir)


def clear_violations(rule_id: str, data_dir: Optional[Path] = None) -> bool:
    with _lock:
        data = _load_violations(data_dir)
        if rule_id not in data:
            return False
        del data[rule_id]
        _save_violations(data_dir, data)
    return True


def describe(rules: list[dict[str, Any]]) -> str:
    """Plain-words listing."""
    if not rules:
        return "The rulebook is empty."
    out, section = [], None
    for r in rules:
        if r["section"] != section:
            section = r["section"]
            out.append(f"\n{section}:")
        flag = f"  (broken {r['violations']} times{', keeps being broken' if r['flagged'] else ''})" \
            if r.get("violations") else ""
        out.append(f"  [{r['id']}] {r['text']}{flag}")
    return "\n".join(out).strip()


# =====================================================================================  routes
TASK_KEY = "mcp.rulebook_add"   # "mcp." so the MCP tool approval_status can read it


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(TASK_KEY, TaskType(TASK_KEY, "Add a rule for every coding agent", "code",
                                                       reversible=True, money=False, sensitive=True))


def _execute_add(d: dict[str, Any]) -> dict[str, Any]:
    det = d["packet"]["details"]
    try:
        res = add_rule(det["text"], det.get("section") or "General", repo_root=Path(det["project"]))
        return {"state": "done", **res}
    except (ValueError, FileNotFoundError) as exc:
        return {"state": "failed", "error": str(exc)}


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/rulebook", tags=["toolkit"])
    _register_task_type()
    try:
        board = get_daemon().board
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (TASK_KEY,))
        board.executors[TASK_KEY] = _execute_add
    except Exception:
        pass

    def err(msg: str, status: int = 400, **extra: Any) -> JSONResponse:
        return JSONResponse({"error": msg, **extra}, status_code=status)

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def project_of(raw: Any) -> Path:
        from mcp_tools import McpConfig, resolve_project
        if not raw:
            return default_repo_root()
        return resolve_project(str(raw), McpConfig.load())

    @router.get("/rules")
    async def rules_list(project: str = "") -> Any:
        from mcp_tools import ToolError
        try:
            items = list_rules(project_of(project))
        except (FileNotFoundError, ToolError) as exc:
            return err(str(exc), 404)
        return {"rules": items, "text": describe(items)}

    @router.post("/rules")
    async def rules_add(request: Request) -> Any:
        """The owner adding a rule from the HUD or by voice ("Orion, add a rule: never push without asking")."""
        from mcp_tools import ToolError
        b = await body_of(request)
        try:
            return add_rule(str(b.get("text") or ""), str(b.get("section") or "General"),
                            repo_root=project_of(b.get("project")))
        except (ValueError, FileNotFoundError, ToolError) as exc:
            return err(str(exc))

    @router.post("/sync")
    async def rules_sync(request: Request) -> Any:
        from mcp_tools import ToolError
        b = await body_of(request)
        try:
            return sync(project_of(b.get("project")))
        except (ValueError, FileNotFoundError, ToolError) as exc:
            return err(str(exc))

    @router.post("/propose")
    async def rules_propose(request: Request) -> Any:
        """A coding agent asking to add a rule: becomes a decision the owner signs."""
        from mcp_tools import ToolError
        b = await body_of(request)
        try:
            project = project_of(b.get("project"))
            text = clean_rule_text(str(b.get("text") or ""))
            read_rules(project)
        except (ValueError, FileNotFoundError, ToolError) as exc:
            return err(str(exc))
        d = get_daemon()
        if getattr(d, "board", None) is None:
            return err("Orion is still starting.", 503)
        section = " ".join(str(b.get("section") or "General").split())[:60]
        dec = await d.board.propose(TASK_KEY, f"Add a rule for every agent in {project.name}: “{text[:90]}”",
                                    {"project": str(project), "text": text, "section": section,
                                     "files": [t for t, _ in TARGETS] + [RULES_FILE.as_posix()],
                                     "why_it_asks": "it changes the instructions every coding agent reads"},
                                    proposer="Coding agent (MCP)", risk="medium", simulated=False)
        return {"decision": {"id": dec["id"], "summary": dec["summary"], "status": dec["status"]}}

    @router.post("/violations")
    async def violation_add(request: Request) -> Any:
        from mcp_tools import ToolError
        b = await body_of(request)
        if not b.get("rule_id"):
            return err("rule_id is required.")
        try:
            return record_violation(str(b["rule_id"]), str(b.get("agent") or ""), str(b.get("evidence") or ""),
                                    repo_root=project_of(b.get("project")))
        except KeyError:
            return err("That rule isn't in the rulebook.", 404)
        except (FileNotFoundError, ToolError) as exc:
            return err(str(exc), 404)

    @router.get("/violations")
    async def violation_list() -> Any:
        return {"violations": violations(), "threshold": FLAG_AT}

    @router.delete("/violations/{rule_id}")
    async def violation_clear(rule_id: str) -> Any:
        return {"cleared": clear_violations(rule_id)}

    return router


# =====================================================================================  MCP tools
def mcp_tools(tools: Any) -> list[Any]:
    """Tool definitions for mcp_tools.OrionTools (imported lazily there)."""
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    S = {"type": "string"}

    def project(a: dict[str, Any]) -> Path:
        p = tools.project(a)
        if not rules_path(p).exists():
            raise ToolError(f"{p.name} has no {RULES_FILE.as_posix()} yet.")
        return p

    async def rulebook_list(a: dict[str, Any]) -> str:
        return tools.out(describe(list_rules(project(a))))

    async def rulebook_sync(a: dict[str, Any]) -> str:
        try:
            res = sync(project(a))
        except ValueError as exc:
            raise ToolError(str(exc))
        if not res["changed"]:
            return "Every agent's rules file already matches .orion/rules.md."
        return "Rewrote " + ", ".join(res["changed"]) + " from .orion/rules.md."

    async def rulebook_add_rule(a: dict[str, Any]) -> str:
        p = project(a)
        try:
            text = clean_rule_text(a["text"])
        except ValueError as exc:
            raise ToolError(str(exc))
        try:
            status, data = await tools.backend.call("POST", "/rulebook/propose",
                                                    {"project": str(p), "text": text,
                                                     "section": a.get("section") or "General"})
        except DaemonUnavailable:
            raise ToolError("Adding a rule needs the user's approval, but Orion isn't running so it can't ask. "
                            "Nothing was changed.")
        if status >= 400:
            raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
        dec = data.get("decision") or {}
        return (f"Waiting for your approval: {dec.get('summary')}.\nDecision {dec.get('id')}. Nothing has changed yet. "
                f"Check on it with approval_status(decision_id=\"{dec.get('id')}\").")

    proj = {"type": "string", "description": "Project folder with .orion/rules.md (default: the agent's folder)"}
    return [
        Tool("rulebook_list", "List the project rules",
             "The rules every coding agent follows in this project (from .orion/rules.md), with how often each "
             "has been broken.", "read", _schema({"project": proj}), rulebook_list),
        Tool("rulebook_add_rule", "Propose a new project rule",
             "Ask the user to add a rule to .orion/rules.md. Once approved, CLAUDE.md, AGENTS.md, GEMINI.md and "
             "Cursor's rules are all updated.", "approval",
             _schema({"text": {**S, "description": "The rule in plain words, e.g. 'Never push without asking.'"},
                      "section": {**S, "description": "Section heading (default General)"}, "project": proj},
                     ("text",)), rulebook_add_rule),
        Tool("rulebook_sync", "Sync every agent's rules file",
             "Rewrite CLAUDE.md, AGENTS.md, GEMINI.md and .cursor/rules/orion.mdc from .orion/rules.md (hand-written "
             "notes between the keep markers are preserved). Writes only those files.", "run",
             _schema({"project": proj}), rulebook_sync),
    ]


# =====================================================================================  CLI
def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="rulebook", description="Orion One Rulebook: write rules once, every agent "
                                                              "gets them.")
    ap.add_argument("command", choices=["sync", "check", "list", "add"])
    ap.add_argument("text", nargs="?", help="the rule, for add")
    ap.add_argument("--section", default="General")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    a = ap.parse_args(argv)
    root = Path(a.repo).resolve()
    try:
        if a.command == "sync":
            res = sync(root)
            print("Rewrote " + ", ".join(res["changed"]) + " from .orion/rules.md." if res["changed"]
                  else "Every agent's rules file already matches .orion/rules.md.")
        elif a.command == "check":
            stale = check(root)
            if stale:
                print("Out of date with .orion/rules.md: " + ", ".join(stale) + ". Run: node scripts/rulebook/sync.mjs")
                return 1
            print("Every agent's rules file matches .orion/rules.md.")
        elif a.command == "list":
            import os
            data = Path(os.path.expanduser(os.environ.get("ORION_DATA_DIR") or "~/.orion"))
            print(describe(list_rules(root, data_dir=data)))
        else:
            res = add_rule(a.text or "", a.section, repo_root=root)
            r = res["rule"]
            print(f"Added [{r['id']}] {r['text']} (section {r['section']})." if res["added"]
                  else f"Already a rule: [{r['id']}] {r['text']}")
            if res["changed"]:
                print("Updated " + ", ".join(res["changed"]) + ".")
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
