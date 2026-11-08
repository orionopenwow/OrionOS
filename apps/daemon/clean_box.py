"""Clean Box: a fresh Ubuntu machine in seconds (docs/ORION_TOOLKIT_PLAN.md, tool 15).

A throwaway Ubuntu 24.04 container made with rootless Podman. Nothing of yours is inside unless you share a
folder; it has no network unless you ask (or the project's tools need installing); it's deleted when you're done.

    orion-ctl box new [--folder DIR] [--name NAME] [--network] [--desktop]
    orion-ctl box list
    orion-ctl box enter NAME [--keep]       (deleted when you leave, unless --keep)
    orion-ctl box rm NAME|all
    orion-ctl box run [--folder DIR] [--network] -- COMMAND...

Other modules use the Python API (restore drills now, self-repair later):

    CleanBox().run("sha256sum /mnt/data/*", mounts=[Mount("/tmp/restored", "/mnt/data")], timeout=300)

Project tools: when the shared folder has a mise.toml / .mise.toml / .tool-versions (the Project Runbook pins tool
versions with mise), the host's `mise` binary is lent to the box read-only and `mise install` runs inside it (that
needs the network, so such a box gets it). A justfile gets the host's `just` the same way.
`--desktop` uses distrobox instead (when installed) so windows and an editor can open from the box; that box gets
its own empty home folder, but distrobox lets it see more of this computer, so it's never the default.

Boxes carry the label orion.box=1 and an expiry time; anything left behind is removed by sweep() (the daemon calls
it hourly from the backup service's loop) after BOX_MAX_AGE_H hours.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

IMAGE = os.environ.get("ORION_CLEANBOX_IMAGE", "docker.io/library/ubuntu:24.04")
LABEL = "orion.box"
BOX_MAX_AGE_H = 24
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")
MAX_OUTPUT = 40_000
TOOL_FILES = ("mise.toml", ".mise.toml", ".tool-versions")


class BoxError(Exception):
    pass


@dataclass
class Mount:
    host: str
    target: str = ""            # inside the box; default /mnt/<folder name>
    read_only: bool = True

    def arg(self) -> str:
        host = str(Path(self.host).expanduser().resolve())
        if not Path(host).exists():
            raise BoxError(f"{self.host} doesn't exist")
        if ":" in host or "," in host:
            raise BoxError(f"{self.host} has a character Podman can't share (':' or ',')")
        target = self.target or f"/mnt/{Path(host).name or 'data'}"
        return f"{host}:{target}:{'ro' if self.read_only else 'rw'}"


@dataclass
class BoxResult:
    rc: int
    output: str
    timed_out: bool = False
    name: str = ""
    where: str = "clean box"
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


def podman() -> Optional[str]:
    env = os.environ.get("ORION_PODMAN")
    if env:
        return env if os.access(env, os.X_OK) else None
    return shutil.which("podman")


def distrobox() -> Optional[str]:
    return os.environ.get("ORION_DISTROBOX") or shutil.which("distrobox")


def _cap(text: str, limit: int = MAX_OUTPUT) -> str:
    return text if len(text) <= limit else text[: limit // 4] + "\n...(shortened)...\n" + text[-limit * 3 // 4:]


class CleanBox:
    def __init__(self, engine: Optional[str] = None, image: str = IMAGE) -> None:
        self.engine = engine or podman()
        self.image = image

    # ----------------------------------------------------------------- basics
    def available(self) -> bool:
        return bool(self.engine)

    def _need(self) -> str:
        if not self.engine:
            raise BoxError("Clean Box needs Podman, which isn't installed. Install it with: sudo apt install podman")
        return self.engine

    def _podman(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self._need(), *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise BoxError("Podman took too long to answer")

    def _base(self, name: str, mounts: Sequence[Mount], network: bool, workdir: Optional[str],
              env: Optional[dict[str, str]], ttl_h: float) -> list[str]:
        argv = ["--name", name, "--label", f"{LABEL}=1", "--label", f"{LABEL}.expires={int(time.time() + ttl_h * 3600)}",
                "--userns=keep-id", "--security-opt", "no-new-privileges", "--pids-limit", "2048",
                "--hostname", "cleanbox"]
        if not network:
            argv.append("--network=none")
        for m in mounts:
            argv += ["-v", m.arg()]
        for k, v in (env or {}).items():
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                raise BoxError(f"bad variable name {k!r}")
            argv += ["-e", f"{k}={v}"]
        if workdir:
            argv += ["-w", workdir]
        return argv

    # ----------------------------------------------------------------- one command, then gone
    def run(self, cmd: str, mounts: Sequence[Mount] = (), timeout: float = 600, network: bool = False,
            workdir: Optional[str] = None, env: Optional[dict[str, str]] = None) -> BoxResult:
        """Run `cmd` (bash -lc) in a brand-new box that is deleted afterwards, whatever happens."""
        engine = self._need()
        name = f"orion-box-run-{uuid.uuid4().hex[:8]}"
        argv = [engine, "run", "--rm", *self._base(name, mounts, network, workdir, env, ttl_h=max(1, timeout / 3600)),
                self.image, "bash", "-lc", cmd]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return BoxResult(p.returncode, _cap((p.stdout or "") + (p.stderr or "")), name=name)
        except subprocess.TimeoutExpired as exc:
            subprocess.run([engine, "rm", "-f", "-t", "0", name], capture_output=True, timeout=60)
            out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return BoxResult(124, _cap(out), timed_out=True, name=name)
        except OSError as exc:
            raise BoxError(f"Podman couldn't start: {exc}")

    # ----------------------------------------------------------------- boxes you step into
    def new(self, name: str = "", folder: Optional[str] = None, network: bool = False, desktop: bool = False,
            ttl_h: float = BOX_MAX_AGE_H) -> dict[str, Any]:
        name = name or f"box-{uuid.uuid4().hex[:6]}"
        if not NAME_RE.match(name):
            raise BoxError("Box names use small letters, digits, dots, dashes and underscores.")
        full = f"orion-{name}"
        mounts: list[Mount] = []
        notes: list[str] = []
        tools_cmd = ""
        if folder:
            src = Path(folder).expanduser().resolve()
            if not src.is_dir():
                raise BoxError(f"{folder} isn't a folder")
            mounts.append(Mount(str(src), "/work", read_only=False))
            notes.append(f"Shared: {src} as /work (changes there are real).")
            if any((src / f).exists() for f in TOOL_FILES) and shutil.which("mise"):
                mounts.append(Mount(str(Path(shutil.which("mise")).resolve()), "/usr/local/bin/mise"))
                tools_cmd = "cd /work && mise trust -a >/dev/null 2>&1; mise install"
                network = True
                notes.append("Installing the project's tools with mise (the box gets the network for that).")
            if (src / "justfile").exists() and shutil.which("just"):
                mounts.append(Mount(str(Path(shutil.which("just")).resolve()), "/usr/local/bin/just"))
        else:
            notes.append("Nothing of yours is inside.")
        if desktop:
            dbx = distrobox()
            if not dbx:
                raise BoxError("A desktop box needs distrobox, which isn't installed (sudo apt install distrobox).")
            home = Path(os.path.expanduser("~/.local/share/orion/boxes")) / name
            home.mkdir(parents=True, exist_ok=True)
            argv = [dbx, "create", "--yes", "--name", full, "--image", self.image, "--home", str(home),
                    "--additional-flags", f"--label {LABEL}=1 --label {LABEL}.expires={int(time.time() + ttl_h * 3600)}"]
            for m in mounts:
                argv += ["--volume", m.arg()]
            p = subprocess.run(argv, capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                raise BoxError("distrobox couldn't make the box: " + (p.stderr or p.stdout).strip()[-300:])
            notes.append("Desktop box: windows can open from it; it has its own empty home folder.")
        else:
            argv = ["run", "-d", *self._base(full, mounts, network, "/work" if folder else None, None, ttl_h),
                    self.image, "sleep", "infinity"]
            p = self._podman(*argv, timeout=900)
            if p.returncode != 0:
                raise BoxError("Podman couldn't make the box: " + (p.stderr or p.stdout).strip()[-300:])
        if tools_cmd:
            r = self._podman("exec", full, "bash", "-lc", tools_cmd, timeout=1800)
            notes.append("Project tools installed." if r.returncode == 0 else
                         "The project's tools didn't all install: " + (r.stderr or r.stdout).strip()[-200:])
        return {"name": name, "container": full, "desktop": desktop, "network": network, "notes": notes}

    def list(self) -> list[dict[str, Any]]:
        p = self._podman("ps", "-a", "--filter", f"label={LABEL}=1", "--format", "json")
        if p.returncode != 0:
            raise BoxError("Podman couldn't list boxes: " + p.stderr.strip()[-200:])
        try:
            rows = json.loads(p.stdout or "[]") or []
        except ValueError:
            rows = []
        out = []
        for r in rows:
            names = r.get("Names") or [r.get("Name", "")]
            nm = names[0] if isinstance(names, list) and names else str(names)
            labels = r.get("Labels") or {}
            out.append({"name": nm[len("orion-"):] if nm.startswith("orion-") else nm, "container": nm,
                        "state": r.get("State", ""), "created": r.get("Created") or r.get("CreatedAt"),
                        "expires": int(labels.get(f"{LABEL}.expires", 0) or 0)})
        return out

    def _container(self, name: str) -> str:
        if not NAME_RE.match(name) and not name.startswith("orion-"):
            raise BoxError("No box by that name.")
        return name if name.startswith("orion-") else f"orion-{name}"

    def enter_argv(self, name: str) -> list[str]:
        full = self._container(name)
        dbx = distrobox()
        info = self._podman("inspect", "--format", "{{index .Config.Labels \"manager\"}}", full)
        if info.returncode != 0:
            raise BoxError(f"No box called {name}. See: orion-ctl box list")
        if dbx and info.stdout.strip() == "distrobox":
            return [dbx, "enter", full]
        return [self._need(), "exec", "-it", full, "bash", "-l"]

    def rm(self, name: str) -> bool:
        if name == "all":
            return all([self.rm(b["container"]) for b in self.list()] or [True])
        p = self._podman("rm", "-f", "-t", "0", self._container(name))
        return p.returncode == 0

    def sweep(self, now: Optional[float] = None) -> list[str]:
        """Remove boxes whose time is up (left behind by a crash or a forgotten --keep)."""
        if not self.engine:
            return []
        now = now or time.time()
        gone = []
        try:
            for b in self.list():
                if b["expires"] and b["expires"] < now and self.rm(b["container"]):
                    gone.append(b["name"])
        except BoxError:
            pass
        return gone


# =====================================================================================  MCP tool
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import Tool, ToolError, _clamp, _schema, resolve_project

    S, B, I = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}

    async def clean_box_run(a: dict[str, Any]) -> str:
        import asyncio
        box = CleanBox()
        if not box.available():
            raise ToolError("Clean Box needs Podman, which isn't installed on this computer.")
        mounts = []
        if a.get("folder"):
            folder = resolve_project(a["folder"], tools.cfg)       # only folders agents may use
            mounts.append(Mount(str(folder), "/work", read_only=not a.get("writable")))
        timeout = _clamp(a.get("timeout_seconds"), 5, 3600, 300)
        try:
            r = await asyncio.to_thread(box.run, a["command"], mounts, timeout, bool(a.get("network")),
                                        "/work" if mounts else None)
        except BoxError as exc:
            raise ToolError(str(exc))
        head = ("Timed out and was stopped." if r.timed_out else f"Exit code {r.rc}.") + \
            " The box has been deleted."
        return tools.out(f"{head}\n{r.output}")

    return [Tool("clean_box_run", "Run in a Clean Box",
                 "Run a shell command in a brand-new throwaway Ubuntu 24.04 container (rootless Podman) that is "
                 "deleted afterwards. Nothing from this computer is inside except the folder you pass (read-only "
                 "unless writable), and there's no network unless you ask.", "run",
                 _schema({"command": S,
                          "folder": {**S, "description": "A project folder to share as /work (optional)"},
                          "writable": {**B, "description": "Let the command change the shared folder"},
                          "network": {**B, "description": "Give the box internet access"},
                          "timeout_seconds": {**I, "description": "5-3600, default 300"}}, ("command",)),
                 clean_box_run)]


# =====================================================================================  command line
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orion-ctl box", description="Throwaway Ubuntu boxes (Clean Box).")
    sub = ap.add_subparsers(dest="cmd")
    n = sub.add_parser("new", help="make a box")
    n.add_argument("--name", default="")
    n.add_argument("--folder")
    n.add_argument("--network", action="store_true")
    n.add_argument("--desktop", action="store_true")
    n.add_argument("--no-enter", action="store_true")
    sub.add_parser("list", help="your boxes")
    e = sub.add_parser("enter", help="open a shell in a box")
    e.add_argument("name")
    e.add_argument("--keep", action="store_true")
    r = sub.add_parser("rm", help="delete a box (or: all)")
    r.add_argument("name")
    x = sub.add_parser("run", help="run one command in a new box, then delete it")
    x.add_argument("--folder")
    x.add_argument("--network", action="store_true")
    x.add_argument("--timeout", type=int, default=600)
    x.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    box = CleanBox()
    try:
        if a.cmd == "new":
            info = box.new(a.name, a.folder, a.network, a.desktop)
            print(f"Made the clean box “{info['name']}”.")
            for line in info["notes"]:
                print("  " + line)
            if a.no_enter or not sys.stdin.isatty():
                print(f"Step in with: orion-ctl box enter {info['name']}")
                return 0
            return _enter(box, info["name"], keep=False)
        if a.cmd == "list":
            boxes = box.list()
            if not boxes:
                print("No clean boxes right now.")
            for b in boxes:
                left = max(0, int((b["expires"] - time.time()) / 3600)) if b["expires"] else 0
                print(f"{b['name']:<24} {b['state']:<10} deleted in about {left} h")
            return 0
        if a.cmd == "enter":
            return _enter(box, a.name, keep=a.keep)
        if a.cmd == "rm":
            ok = box.rm(a.name)
            print("Deleted." if ok else "Couldn't delete it (is the name right? orion-ctl box list)")
            return 0 if ok else 1
        if a.cmd == "run":
            cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
            if not cmd:
                print("usage: orion-ctl box run [--folder DIR] -- command ...")
                return 2
            mounts = [Mount(a.folder, "/work", read_only=False)] if a.folder else []
            res = box.run(" ".join(cmd), mounts, a.timeout, a.network, "/work" if mounts else None)
            sys.stdout.write(res.output)
            return 124 if res.timed_out else res.rc
    except BoxError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    ap.print_help()
    return 2


def _enter(box: CleanBox, name: str, keep: bool) -> int:
    rc = subprocess.call(box.enter_argv(name))
    if keep:
        print(f"Kept “{name}”. Delete it with: orion-ctl box rm {name}")
    else:
        box.rm(name)
        print(f"Deleted the clean box “{name}”.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
