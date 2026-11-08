#!/usr/bin/env python3
"""OrionOS "Get apps": Flatpak apps from Flathub, with safe updates (docs/ORIONOS_COMPLETE_PLAN.md Part 1 and 3).

The `orion-apps` command (os/getapps/bin/orion-apps runs this file):

    orion-apps open [APP_ID]          open the Get apps window (optionally on one app)
    orion-apps updates --json         apps with an update waiting, plus paused (masked) ones
    orion-apps update-all [--json] [--auto]
                                      update every app safely: note each app's current version, update it, check
                                      that it still starts, and put the old version back if it doesn't
    orion-apps install APP_ID [--json]    install one app from Flathub (the owner's action)
    orion-apps remove APP_ID [--json]
    orion-apps installed --json | search WORDS --json | picks --json | info APP_ID --json
    orion-apps resume APP_ID          let a paused app update again (after a rollback)
    orion-apps history --json         every change Get apps made (the "readable change history", Part 3 item 6)
    orion-apps status --json          Flatpak and Flathub set up? automatic updates on? last run?
    orion-apps config [auto-update auto|notify|off]
    orion-apps install-bundle FILE    an Orion Studio Flatpak bundle downloaded from its official release page,
                                      checked against the SHA256SUMS.txt next to it

Plain words everywhere: what the owner reads is the `message` of each result.
Tests run this against a stand-in `flatpak` (ORION_FLATPAK=/path/to/stub); nothing here needs the network
except Flatpak itself.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

HERE = Path(__file__).resolve().parent
CATALOG = Path(os.environ.get("ORION_APPS_CATALOG", HERE / "catalog.json"))
REMOTE = "flathub"
APP_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$")
HEALTH_TIMEOUT_S = 90
MODES = ("auto", "notify", "off")


# =====================================================================================  paths and config
def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def config_path() -> Path:
    return Path(os.environ.get("ORION_APPS_CONFIG") or _xdg("XDG_CONFIG_HOME", ".config") / "orion" / "apps.json")


def history_path() -> Path:
    """JSON lines, one change per line (Part 3 "readable change history" reads this)."""
    return Path(os.environ.get("ORION_APPS_HISTORY") or
                _xdg("XDG_STATE_HOME", ".local/state") / "orion" / "changes" / "apps.jsonl")


def last_run_path() -> Path:
    return history_path().with_name("apps-last-run.json")


DEFAULT_CONFIG = {
    # Plan Part 3, permission levels: app and Flatpak updates are level 2 "tell me after" by default.
    #   auto   = update weekly, then tell you what changed      (level 2, the default)
    #   notify = only tell you that updates are waiting          (level 1: you press Update)
    #   off    = no weekly check at all
    "auto_update": "auto",
}


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    if cfg["auto_update"] not in MODES:
        cfg["auto_update"] = DEFAULT_CONFIG["auto_update"]
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def valid_app_id(app_id: str) -> bool:
    return bool(app_id) and len(app_id) <= 255 and bool(APP_ID_RE.match(app_id))


# =====================================================================================  catalog
def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"picks": [], "studio": {"apps": []}}


def pick_by_id(app_id: str, catalog: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    catalog = catalog or load_catalog()
    for p in catalog.get("picks", []):
        if p.get("id") == app_id:
            return p
    for p in catalog.get("studio", {}).get("apps", []):
        if p.get("flatpak_id") == app_id:
            return p
    return None


# =====================================================================================  Flatpak
Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


def _default_runner(argv: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(argv, 124, exc.stdout or "", (exc.stderr or "") + "\ntimed out")
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", "flatpak is not installed")


def _rows(text: str, ncols: int, header_first: str = "") -> list[list[str]]:
    """Flatpak prints tab-separated columns when it isn't writing to a terminal (no header then; one is skipped
    if a version prints it anyway)."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if header_first and cols[0].strip() == header_first:
            continue
        cols += [""] * (ncols - len(cols))
        rows.append([c.strip() for c in cols[:ncols]])
    return rows


def unreachable(r: "subprocess.CompletedProcess[str]") -> bool:
    text = (r.stderr or "") + (r.stdout or "")
    return any(w in text for w in ("Unable to load summary", "While fetching", "Could not resolve", "Timeout was reached",
                                   "Could not connect", "Failure when receiving data"))


OFFLINE = ("Orion couldn't reach Flathub (is the internet connected?), so nothing was changed.")


def ref_to_pattern(ref: str) -> str:
    """runtime/org.freedesktop.Platform/x86_64/23.08 -> org.freedesktop.Platform/x86_64/23.08 (what mask takes)."""
    parts = ref.split("/")
    return "/".join(parts[1:]) if len(parts) == 4 and parts[0] in ("app", "runtime") else ref


class Flatpak:
    """The few Flatpak commands Get apps needs, always on one installation (system-wide by default)."""

    def __init__(self, binary: Optional[str] = None, installation: Optional[str] = None,
                 runner: Optional[Runner] = None) -> None:
        self.binary = binary or os.environ.get("ORION_FLATPAK") or "flatpak"
        inst = installation or os.environ.get("ORION_APPS_INSTALLATION") or "system"
        self.where = "--user" if inst == "user" else "--system"
        self.runner = runner or _default_runner
        self.last_error = ""     # why the last check of the remote failed ("" = it worked)

    def available(self) -> bool:
        return os.path.isabs(self.binary) and os.access(self.binary, os.X_OK) or bool(shutil.which(self.binary))

    def run(self, *args: str, timeout: float = 120) -> "subprocess.CompletedProcess[str]":
        return self.runner([self.binary, *args], timeout)

    # ------------------------------------------------------------------ reading
    def remotes(self) -> list[dict[str, str]]:
        r = self.run("remotes", self.where, "--columns=name,url,options", timeout=30)
        return [{"name": a, "url": b, "options": c} for a, b, c in _rows(r.stdout, 3, "Name")] if r.returncode == 0 else []

    def installed(self, kind: str = "app") -> list[dict[str, str]]:
        r = self.run("list", self.where, f"--{kind}", "--columns=application,name,version,branch,origin,ref,size",
                     timeout=60)
        if r.returncode != 0:
            return []
        keys = ("id", "name", "version", "branch", "origin", "ref", "size")
        return [dict(zip(keys, row)) for row in _rows(r.stdout, len(keys), "Application ID")]

    def updates(self, kind: str = "app") -> list[dict[str, str]]:
        r = self.run("remote-ls", self.where, "--updates", f"--{kind}",
                     "--columns=application,name,version,branch,origin,ref,download-size", timeout=180)
        self.last_error = "" if r.returncode == 0 else (_tail(r, 300) or "flatpak remote-ls failed")
        if r.returncode != 0:
            return []
        keys = ("id", "name", "version", "branch", "origin", "ref", "download_size")
        return [dict(zip(keys, row)) for row in _rows(r.stdout, len(keys), "Application ID")]

    def commit(self, ref: str) -> str:
        r = self.run("info", self.where, "--show-commit", ref, timeout=30)
        return r.stdout.strip().splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else ""

    def runtime_of(self, app_id: str) -> str:
        r = self.run("info", self.where, "--show-runtime", app_id, timeout=30)
        rt = r.stdout.strip()
        return f"runtime/{rt}" if r.returncode == 0 and rt and not rt.startswith("runtime/") else rt

    def masked(self) -> list[str]:
        r = self.run("mask", self.where, timeout=30)
        if r.returncode != 0:
            return []
        out = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and not line.lower().startswith(("masked patterns", "no masked")):
                out.append(line)
        return out

    def search(self, words: str) -> list[dict[str, str]]:
        r = self.run("search", "--columns=application,name,description,version,remotes", words, timeout=60)
        if r.returncode != 0:
            return []
        keys = ("id", "name", "summary", "version", "remotes")
        rows = [dict(zip(keys, row)) for row in _rows(r.stdout, len(keys), "Application ID")]
        return [x for x in rows if x["id"] and not x["id"].lower().startswith("no matches")]

    def remote_has(self, app_id: str, remote: str = REMOTE) -> Optional[bool]:
        """True / False, or None when the remote couldn't be reached."""
        r = self.run("remote-info", self.where, remote, app_id, timeout=60)
        if r.returncode == 0:
            return True
        return None if unreachable(r) else False

    # ------------------------------------------------------------------ changing
    def install(self, app_id: str, remote: str = REMOTE,
                progress: Optional[Callable[[Optional[int], str], None]] = None) -> "subprocess.CompletedProcess[str]":
        args = ("install", self.where, "-y", "--noninteractive", remote, app_id)
        return self.stream(args, progress) if progress else self.run(*args, timeout=3600)

    def stream(self, args: Iterable[str], progress: Callable[[Optional[int], str], None],
               timeout: float = 3600) -> "subprocess.CompletedProcess[str]":
        """Run a long command and report each line (and the last percentage seen) as it goes."""
        argv = [self.binary, *args]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
        except FileNotFoundError:
            return subprocess.CompletedProcess(argv, 127, "", "flatpak is not installed")
        out, buf, deadline = [], b"", time.time() + timeout
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    out.append(line)
                    pct = re.findall(r"(\d{1,3})%", line)
                    progress(min(100, int(pct[-1])) if pct else None, line)
            if time.time() > deadline:
                proc.kill()
                break
        code = proc.wait()
        text = "\n".join(out[-200:])
        return subprocess.CompletedProcess(argv, code, text, "")

    def install_bundle(self, path: str) -> "subprocess.CompletedProcess[str]":
        return self.run("install", self.where, "-y", "--noninteractive", "--bundle", path, timeout=3600)

    def uninstall(self, app_id: str) -> "subprocess.CompletedProcess[str]":
        return self.run("uninstall", self.where, "-y", "--noninteractive", app_id, timeout=600)

    def update(self, ref: Optional[str] = None, commit: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
        args = ["update", self.where, "-y", "--noninteractive"]
        if commit:
            args.append(f"--commit={commit}")
        if ref:
            args.append(ref)
        return self.run(*args, timeout=3600)

    def mask(self, pattern: str, remove: bool = False) -> bool:
        args = ["mask", self.where] + (["--remove"] if remove else []) + [pattern]
        return self.run(*args, timeout=30).returncode == 0

    def healthy(self, app_id: str) -> bool:
        """Quick health check: the app's sandbox starts and runs a no-op inside it (no window needed)."""
        return self.run("run", "--command=true", app_id, timeout=HEALTH_TIMEOUT_S).returncode == 0


# =====================================================================================  history + notify
def record(kind: str, **fields: Any) -> dict[str, Any]:
    """One line in the change history: what changed, why, which checks it passed, and how to undo it."""
    entry = {"id": f"chg-{uuid.uuid4().hex[:10]}", "ts": time.time(),
             "time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "source": "get-apps", "kind": kind, **fields}
    p = history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def read_history(limit: int = 200) -> list[dict[str, Any]]:
    try:
        lines = history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))


def daemon_token() -> str:
    """Orion's per-start API key (review B1; written 0600 by the daemon, apps/daemon/api_token.py). Root helpers
    (apt hooks, timers) look in the session user's runtime folder too. '' when Orion isn't running."""
    import pwd as _pwd
    cands = [os.environ.get("ORION_TOKEN_FILE", "")]
    uid = os.getuid() if hasattr(os, "getuid") else -1
    cands.append(os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}", "orion", "daemon.token"))
    homes = [os.path.expanduser(os.environ.get("ORION_DATA_DIR", "~/.orion"))]
    if uid == 0:
        try:
            pw = _pwd.getpwnam(os.environ.get("ORION_USER", "orion"))
            cands.append(f"/run/user/{pw.pw_uid}/orion/daemon.token")
            homes.append(os.path.join(pw.pw_dir, ".orion"))
        except KeyError:
            pass
    cands += [os.path.join(h, "daemon.token") for h in homes]
    for c in cands:
        try:
            with open(c, encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            continue
        if tok:
            return tok
    return ""


def daemon_headers(extra: Optional[dict] = None) -> dict:
    tok = daemon_token()
    return {**({"Authorization": f"Bearer {tok}"} if tok else {}), **(extra or {})}


def notify(title: str, body: str) -> None:
    """A desktop notification and, when Orion's brain is running, the phone (best effort, never fails)."""
    if os.environ.get("ORION_APPS_NO_NOTIFY") == "1":
        return
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "-a", "Get apps", "-i", "system-software-install", title, body],
                           timeout=10, capture_output=True)
        except Exception:
            pass
    try:
        import urllib.request
        port = os.environ.get("ORION_PORT", "9120")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/apps/notify", method="POST",
                                     data=json.dumps({"title": title, "body": body}).encode(),
                                     headers=daemon_headers({"Content-Type": "application/json"}))
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass


def _name(app_id: str, installed: Iterable[dict[str, str]] = ()) -> str:
    for a in installed:
        if a.get("id") == app_id and a.get("name"):
            return a["name"]
    p = pick_by_id(app_id)
    return (p or {}).get("name") or app_id


def _tail(r: "subprocess.CompletedProcess[str]", n: int = 600) -> str:
    return ((r.stderr or "") + (r.stdout or "")).strip()[-n:]


# =====================================================================================  the safe update
def update_all(fp: Flatpak, mode: str = "manual", say: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Update every app (and then the shared runtimes) one at a time, each with a before/after health check.

    For each app: note its current commit and whether it starts now; update; if it started before and doesn't
    now, put the noted commit back (`flatpak update --commit=OLD`), pause its updates (`flatpak mask`), and say
    so in plain words. If the app's runtime changed and the old app still won't start, the runtime goes back
    too. Runtime-only updates come last, with every app checked before and after.
    """
    started = time.time()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    undo_point()
    installed = fp.installed("app")
    masked = set(fp.masked())
    app_updates = [u for u in fp.updates("app") if u.get("id")]
    results: list[dict[str, Any]] = []
    if fp.last_error:
        out = {"ok": False, "run": run_id, "mode": mode, "started": started, "finished": time.time(), "results": [],
               "offline": True, "summary": OFFLINE + " It tries again next week, or press Update all safely.",
               "detail": fp.last_error}
        out["message"] = out["summary"]
        return out

    for u in app_updates:
        app = u["id"]
        name = u.get("name") or _name(app, installed)
        if app in masked:
            results.append({"app": app, "name": name, "result": "paused",
                            "message": f"{name} is paused after an earlier update didn't start; not updated."})
            continue
        say(f"Updating {name}")
        old = fp.commit(app)
        rt = fp.runtime_of(app)
        rt_old = fp.commit(rt) if rt else ""
        before = fp.healthy(app)
        r = fp.update(app)
        new = fp.commit(app)
        if r.returncode != 0:
            changed = bool(new and old and new != old)
            res = {"app": app, "name": name, "result": "failed", "from_commit": old, "to_commit": new,
                   "message": f"{name} couldn't be updated" + (" (it was changed partly)." if changed else
                                                                 "; it's unchanged."), "detail": _tail(r)}
            results.append(res)
            record("app_update_failed", app=app, name=name, from_commit=old, to_commit=new, run=run_id,
                   why=f"weekly app updates ({mode})", result="failed", detail=res["detail"])
            continue
        if not new or new == old:
            results.append({"app": app, "name": name, "result": "unchanged", "from_commit": old, "to_commit": new,
                            "message": f"{name} was already up to date."})
            continue
        rt_new = fp.commit(rt) if rt else ""
        after = fp.healthy(app)
        checks = [{"name": "starts (flatpak run --command=true)", "before": before, "after": after}]
        if after or not before:
            note = "" if before else " (Orion couldn't check it: it didn't start before the update either)"
            res = {"app": app, "name": name, "result": "updated", "from_commit": old, "to_commit": new,
                   "version": u.get("version", ""), "checks": checks,
                   "message": f"{name} updated{(' to ' + u['version']) if u.get('version') else ''}{note}."}
            results.append(res)
            record("app_update", app=app, name=name, from_commit=old, to_commit=new, version=u.get("version", ""),
                   runtime=rt, runtime_from=rt_old, runtime_to=rt_new, run=run_id, checks=checks,
                   why=f"weekly app updates ({mode})" if mode == "auto" else "you pressed Update",
                   result="updated", undo=["flatpak", "update", fp.where, f"--commit={old}", app])
            continue

        # It started before and not now: put the old version back and pause it.
        say(f"{name} didn't start after the update; putting the previous version back")
        rb = fp.update(app, commit=old)
        rt_rolled = False
        back = fp.healthy(app)
        if not back and rt and rt_old and rt_new and rt_new != rt_old:
            if fp.update(rt, commit=rt_old).returncode == 0:
                rt_rolled = True
                fp.mask(ref_to_pattern(rt))
            back = fp.healthy(app)
        fp.mask(app)
        checks.append({"name": "starts after putting the old version back", "after": back})
        ok = rb.returncode == 0 and fp.commit(app) == old
        msg = (f"{name}'s update didn't start, so Orion put the previous version back"
               f"{' (and its shared parts)' if rt_rolled else ''} and paused its updates. "
               f"Get apps > Updates lets you try again." if ok else
               f"{name}'s update didn't start and the previous version couldn't be put back. "
               f"Open Get apps to reinstall it.")
        res = {"app": app, "name": name, "result": "rolled_back" if ok else "broken", "from_commit": old,
               "to_commit": new, "checks": checks, "runtime_rolled_back": rt_rolled, "message": msg}
        results.append(res)
        record("app_rollback", app=app, name=name, from_commit=new, to_commit=old, bad_commit=new, run=run_id,
               runtime=rt, runtime_rolled_back=rt_rolled, checks=checks, why="the update didn't start",
               result=res["result"], paused=True, undo=["orion-apps", "resume", app])

    # Shared runtimes that still have updates (apps above pulled the ones they needed).
    rt_updates = [u for u in fp.updates("runtime") if u.get("ref") or u.get("id")]
    rt_updates = [u for u in rt_updates if ref_to_pattern(u.get("ref") or u["id"]) not in masked]
    if rt_updates:
        say("Updating shared app parts (runtimes)")
        apps_now = [a["id"] for a in fp.installed("app") if a.get("id") and a["id"] not in masked]
        healthy_before = {a: fp.healthy(a) for a in apps_now}
        old_commits = {u.get("ref") or u["id"]: fp.commit(u.get("ref") or u["id"]) for u in rt_updates}
        r = fp.update()   # everything left: only runtimes now
        new_commits = {ref: fp.commit(ref) for ref in old_commits}
        changed = [ref for ref in old_commits if new_commits[ref] and new_commits[ref] != old_commits[ref]]
        broken = [a for a, ok in healthy_before.items() if ok and not fp.healthy(a)]
        if broken and changed:
            for ref in changed:
                fp.update(ref, commit=old_commits[ref])
                fp.mask(ref_to_pattern(ref))
            still = [a for a in broken if not fp.healthy(a)]
            names = ", ".join(_name(a, installed) for a in broken)
            msg = (f"An update to shared app parts stopped {names} from starting, so Orion put the previous parts "
                   f"back and paused them." + (" Some still don't start: open Get apps." if still else ""))
            results.append({"app": "", "name": "Shared app parts", "result": "rolled_back", "refs": changed,
                            "affected": broken, "message": msg})
            record("runtime_rollback", refs=changed, affected=broken, run=run_id, why="apps stopped starting",
                   from_commits={k: new_commits[k] for k in changed}, to_commits={k: old_commits[k] for k in changed},
                   result="rolled_back", paused=True, undo=["orion-apps", "resume"] + [ref_to_pattern(c) for c in changed])
        elif changed:
            results.append({"app": "", "name": "Shared app parts", "result": "updated", "refs": changed,
                            "message": f"{len(changed)} shared app part{'s' if len(changed) != 1 else ''} updated."})
            record("runtime_update", refs=changed, run=run_id, why=f"weekly app updates ({mode})",
                   from_commits={k: old_commits[k] for k in changed}, to_commits={k: new_commits[k] for k in changed},
                   checks=[{"name": "every app still starts", "after": True}], result="updated",
                   undo=[["flatpak", "update", fp.where, f"--commit={old_commits[k]}", k] for k in changed])
        elif r.returncode != 0:
            results.append({"app": "", "name": "Shared app parts", "result": "failed", "detail": _tail(r),
                            "message": "Shared app parts couldn't be updated; nothing changed."})

    summary = summarize(results)
    out = {"ok": True, "run": run_id, "mode": mode, "started": started, "finished": time.time(), "results": results,
           "summary": summary, "message": summary}
    try:
        last_run_path().parent.mkdir(parents=True, exist_ok=True)
        last_run_path().write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def summarize(results: list[dict[str, Any]]) -> str:
    if not results:
        return "Every app is up to date."
    upd = [r["name"] for r in results if r["result"] == "updated" and r.get("app")]
    bad = [r for r in results if r["result"] in ("rolled_back", "broken", "failed")]
    parts = []
    if upd:
        parts.append(f"Updated {len(upd)} app{'s' if len(upd) != 1 else ''}: {', '.join(upd[:6])}"
                     f"{' and more' if len(upd) > 6 else ''}.")
    parts += [r["message"] for r in bad]
    if not parts:
        return "Every app is up to date."
    return " ".join(parts)


# =====================================================================================  CLI actions
def _emit(data: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(data.get("message") or json.dumps(data, ensure_ascii=False, indent=1))
    return 0 if data.get("ok", True) else 1


def act_install(fp: Flatpak, app_id: str, check_remote: bool = True,
                progress: Optional[Callable[[Optional[int], str], None]] = None) -> dict[str, Any]:
    if not valid_app_id(app_id):
        return {"ok": False, "app": app_id, "message": f"“{app_id}” isn't an app name Flathub uses."}
    if any(a["id"] == app_id for a in fp.installed("app")):
        return {"ok": True, "app": app_id, "result": "already", "message": f"{_name(app_id)} is already installed."}
    found = fp.remote_has(app_id) if check_remote else True
    if found is None:
        return {"ok": False, "app": app_id, "offline": True, "message": OFFLINE}
    if not found:
        return {"ok": False, "app": app_id, "message": f"Flathub has no app called {app_id}. Nothing was installed."}
    r = fp.install(app_id, progress=progress)
    if r.returncode != 0:
        return {"ok": False, "app": app_id, "detail": _tail(r),
                "message": f"{_name(app_id)} couldn't be installed. Nothing was changed."}
    commit = fp.commit(app_id)
    healthy = fp.healthy(app_id)
    name = _name(app_id, fp.installed("app"))
    record("app_install", app=app_id, name=name, to_commit=commit, why="installed from Get apps",
           checks=[{"name": "starts (flatpak run --command=true)", "after": healthy}], result="installed",
           undo=["orion-apps", "remove", app_id])
    return {"ok": True, "app": app_id, "result": "installed", "commit": commit, "healthy": healthy,
            "message": f"{name} is installed." + ("" if healthy else " It didn't pass the quick start check, "
                                                                     "so it may not open.")}


def act_remove(fp: Flatpak, app_id: str) -> dict[str, Any]:
    if not valid_app_id(app_id):
        return {"ok": False, "app": app_id, "message": f"“{app_id}” isn't an app name."}
    installed = fp.installed("app")
    if not any(a["id"] == app_id for a in installed):
        return {"ok": True, "app": app_id, "result": "absent", "message": f"{app_id} isn't installed."}
    name = _name(app_id, installed)
    commit = fp.commit(app_id)
    r = fp.uninstall(app_id)
    if r.returncode != 0:
        return {"ok": False, "app": app_id, "detail": _tail(r), "message": f"{name} couldn't be removed."}
    record("app_remove", app=app_id, name=name, from_commit=commit, why="removed from Get apps", result="removed",
           undo=["orion-apps", "install", app_id])
    return {"ok": True, "app": app_id, "result": "removed", "message": f"{name} is removed. Your files from it stay "
                                                                       f"in ~/.var/app/{app_id}."}


def act_resume(fp: Flatpak, pattern: str) -> dict[str, Any]:
    ok = fp.mask(pattern, remove=True)
    if ok:
        record("app_resume", app=pattern, why="you let it update again", result="resumed",
               undo=["flatpak", "mask", fp.where, pattern])
    return {"ok": ok, "app": pattern, "message": (f"{_name(pattern)} will update again." if ok else
                                                  f"{pattern} wasn't paused.")}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def act_install_bundle(fp: Flatpak, path: str) -> dict[str, Any]:
    """Orion Studio: a .flatpak bundle downloaded from the app's official GitHub release. It must be listed, with
    the same checksum, in the release's SHA256SUMS.txt saved next to it, and be a Studio app."""
    f = Path(path).expanduser()
    if not f.is_file() or f.suffix != ".flatpak":
        return {"ok": False, "message": "Pick the .flatpak file you downloaded from the app's release page."}
    sums = f.with_name("SHA256SUMS.txt")
    if not sums.is_file():
        return {"ok": False, "message": "Save the release's SHA256SUMS.txt next to the .flatpak file first, so "
                                        "Orion can check the download isn't damaged or swapped."}
    want = ""
    for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == f.name:
            want = parts[0].lower()
    if not want:
        return {"ok": False, "message": f"{f.name} isn't listed in SHA256SUMS.txt."}
    if _sha256(f) != want:
        return {"ok": False, "message": f"{f.name} doesn't match its checksum. Download it again."}
    studio = [a for a in load_catalog().get("studio", {}).get("apps", []) if a.get("flatpak_id")]
    prefix = {a["bundle_prefix"]: a for a in studio if a.get("bundle_prefix")}
    app = next((a for p, a in prefix.items() if f.name.startswith(p + "-")), None)
    if app is None:
        return {"ok": False, "message": "That file isn't one of the Orion Studio apps."}
    r = fp.install_bundle(str(f))
    if r.returncode != 0:
        return {"ok": False, "detail": _tail(r), "message": f"{app['name']} couldn't be installed."}
    commit = fp.commit(app["flatpak_id"])
    record("app_install", app=app["flatpak_id"], name=app["name"], to_commit=commit, sha256=want,
           why="installed from its official release (Orion Studio)", result="installed",
           checks=[{"name": "SHA256SUMS.txt matches", "after": True}], undo=["orion-apps", "remove", app["flatpak_id"]])
    return {"ok": True, "app": app["flatpak_id"], "result": "installed",
            "message": f"{app['name']} is installed (checksum matched; it's alpha software)."}


def status(fp: Flatpak) -> dict[str, Any]:
    remotes = fp.remotes() if fp.available() else []
    last = {}
    try:
        last = json.loads(last_run_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg = load_config()
    fh = any(r["name"] == REMOTE for r in remotes)
    return {"flatpak": fp.available(), "flathub": fh, "remotes": remotes, "auto_update": cfg["auto_update"],
            "last_run": {k: last.get(k) for k in ("run", "mode", "finished", "summary")} if last else None,
            "message": ("Get apps is ready." if fh else "Flathub isn't set up yet (run os/image/os-getapps.sh).")
            + f" Weekly updates: {describe_mode(cfg['auto_update'])}."}


def describe_mode(mode: str) -> str:
    return {"auto": "on (update, then tell you)", "notify": "only tell you what's waiting", "off": "off"}[mode]


def undo_point() -> None:
    """A system snapshot before app updates (os/safety; polkit lets this user take one). Best effort."""
    exe = shutil.which("orion-snapshot")
    if exe and shutil.which("pkexec"):
        try:
            subprocess.run(["pkexec", exe, "take", "--kind", "pre-update", "--why", "before app updates",
                            "--debounce", "600", "--quiet"], capture_output=True, timeout=1800, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def frozen() -> bool:
    """Orion's kill switch is on (os/safety/orion-frozen): no automatic updates until it's unfrozen."""
    exe = shutil.which("orion-frozen")
    return bool(exe) and subprocess.run([exe], capture_output=True).returncode == 0


def daemon_decision(kind: str = "app_update") -> Optional[dict[str, Any]]:
    """Orion's permission level for app updates (apps/daemon/self_levels.py): {"decision": ask|tell|do, "frozen"}.
    None when Orion's brain isn't running; then the setting here decides alone."""
    forced = os.environ.get("ORION_SELF_DECIDE", "")
    if forced:
        return {"decision": "ask" if forced == "frozen" else forced, "frozen": forced == "frozen"}
    if os.environ.get("ORION_APPS_NO_DAEMON") == "1":
        return None
    try:
        import urllib.request
        port = os.environ.get("ORION_PORT", "9120")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/self/decide/{kind}", headers=daemon_headers())
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode() or "null")
        return d if isinstance(d, dict) and d.get("decision") in ("ask", "tell", "do") else None
    except Exception:
        return None


def weekly(fp: Flatpak) -> dict[str, Any]:
    """What the weekly timer runs: follows the auto_update setting and Orion's permission level for app updates
    ("always ask" only tells you what's waiting; "just do it" only notifies when something needed a fix)."""
    mode = load_config()["auto_update"]
    dec = daemon_decision()
    if frozen() or (dec and dec.get("frozen")):
        return {"ok": True, "mode": mode, "frozen": True, "message": "Orion is frozen, so no app updates this week."}
    if dec and dec["decision"] == "ask" and mode == "auto":
        mode = "notify"          # Orion's level for app updates is "always ask"
    quiet = bool(dec and dec["decision"] == "do")
    if mode == "off":
        return {"ok": True, "mode": mode, "message": "Weekly app updates are off."}
    if mode == "notify":
        ups = fp.updates("app")
        if fp.last_error:
            return {"ok": False, "mode": mode, "offline": True, "updates": [], "message": OFFLINE}
        if ups:
            names = ", ".join(u.get("name") or u["id"] for u in ups[:6])
            notify(f"{len(ups)} app update{'s' if len(ups) != 1 else ''} waiting",
                   f"{names}. Open Get apps > Updates to install them.")
        return {"ok": True, "mode": mode, "updates": ups, "message": f"{len(ups)} update(s) waiting."}
    out = update_all(fp, mode="auto")
    if out.get("offline"):
        return out          # nothing changed: no notification (it tries again next week)
    changed = [r for r in out["results"] if r["result"] != "unchanged" and r["result"] != "paused"]
    if changed:
        bad = any(r["result"] in ("rolled_back", "broken", "failed") for r in changed)
        if bad or not quiet:
            notify("App updates: something needed a fix" if bad else "Apps updated", out["summary"])
    return out


def open_window(app_id: str = "", page: str = "") -> int:
    ui = HERE / "getapps_ui.py"
    argv = [sys.executable or "python3", str(ui)]
    if page:
        argv += ["--page", page]
    if app_id:
        argv += ["--app", app_id]
    os.execv(argv[0], argv)
    return 0   # not reached


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orion-apps", description="Get apps: Flatpak apps from Flathub, updated "
                                                                 "safely.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("updates", "installed", "picks", "status", "history"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
    s = sub.add_parser("update-all")
    s.add_argument("--json", action="store_true")
    s.add_argument("--auto", action="store_true", help="the weekly run: follows the auto_update setting")
    for name in ("install", "remove", "info", "resume"):
        s = sub.add_parser(name)
        s.add_argument("app_id")
        s.add_argument("--json", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("install-bundle")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("open")
    s.add_argument("app_id", nargs="?", default="")
    s.add_argument("--page", default="", choices=["", "discover", "installed", "updates", "windows", "studio"])
    s = sub.add_parser("config")
    s.add_argument("key", nargs="?", choices=["auto-update"])
    s.add_argument("value", nargs="?", choices=list(MODES))
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fp = Flatpak()
    j = getattr(a, "json", False)

    if a.cmd == "open":
        return open_window(a.app_id, a.page)
    if a.cmd == "config":
        cfg = load_config()
        if a.key and a.value:
            cfg["auto_update"] = a.value
            save_config(cfg)
        return _emit({"ok": True, **cfg, "message": f"Weekly app updates: {describe_mode(cfg['auto_update'])}."}, j)
    if a.cmd == "picks":
        cat = load_catalog()
        inst = {x["id"] for x in fp.installed("app")} if fp.available() else set()
        picks = [{**p, "installed": p["id"] in inst} for p in cat.get("picks", [])]
        studio = cat.get("studio", {})
        apps = [{**s, "installed": bool(s.get("flatpak_id")) and s["flatpak_id"] in inst} for s in studio.get("apps", [])]
        return _emit({"ok": True, "picks": picks, "studio": {**studio, "apps": apps},
                      "message": f"{len(picks)} Orion picks, {len(apps)} Orion Studio apps."}, j)
    if a.cmd == "history":
        h = read_history()
        return _emit({"ok": True, "changes": h, "message": "\n".join(
            f"{c.get('time', '')}  {c.get('kind')}: {c.get('name') or c.get('app') or ''} ({c.get('result', '')})"
            for c in h) or "No changes yet."}, j)
    if a.cmd == "status":
        return _emit({"ok": True, **status(fp)}, j)
    if not fp.available():
        return _emit({"ok": False, "message": "Flatpak isn't installed. Run os/image/os-getapps.sh as root."}, j)
    if a.cmd == "updates":
        ups = fp.updates("app")
        if fp.last_error:
            return _emit({"ok": False, "offline": True, "updates": [], "runtime_updates": [], "paused": fp.masked(),
                          "message": "Orion couldn't reach Flathub to check for updates (is the internet "
                                     "connected?)."}, j)
        masked = fp.masked()
        rt = fp.updates("runtime")
        return _emit({"ok": True, "updates": ups, "runtime_updates": rt, "paused": masked, "checked_at": time.time(),
                      "message": (f"{len(ups)} app update{'s' if len(ups) != 1 else ''} waiting"
                                  + (f": {', '.join(u.get('name') or u['id'] for u in ups)}" if ups else "")
                                  + (f". Paused: {', '.join(masked)}" if masked else "") + ".")}, j)
    if a.cmd == "installed":
        apps = fp.installed("app")
        return _emit({"ok": True, "apps": apps, "message": "\n".join(f"{x['name']} ({x['id']}) {x['version']}"
                                                                      for x in apps) or "No apps installed."}, j)
    if a.cmd == "search":
        words = " ".join(a.words)[:100]
        res = fp.search(words)
        return _emit({"ok": True, "query": words, "results": res, "message": "\n".join(
            f"{x['name']} ({x['id']}): {x['summary']}" for x in res) or "Nothing found."}, j)
    if a.cmd == "info":
        if not valid_app_id(a.app_id):
            return _emit({"ok": False, "message": "That isn't an app name."}, j)
        inst = next((x for x in fp.installed("app") if x["id"] == a.app_id), None)
        pick = pick_by_id(a.app_id)
        return _emit({"ok": True, "app": a.app_id, "installed": inst, "pick": pick,
                      "commit": fp.commit(a.app_id) if inst else "",
                      "message": f"{(pick or inst or {}).get('name') or a.app_id}: "
                                 f"{'installed' if inst else 'not installed'}."}, j)
    if a.cmd == "install":
        return _emit(act_install(fp, a.app_id), j)
    if a.cmd == "remove":
        return _emit(act_remove(fp, a.app_id), j)
    if a.cmd == "resume":
        return _emit(act_resume(fp, a.app_id), j)
    if a.cmd == "install-bundle":
        return _emit(act_install_bundle(fp, a.file), j)
    if a.cmd == "update-all":
        out = weekly(fp) if a.auto else update_all(fp, say=(lambda m: None) if j else
                                                    (lambda m: print("· " + m, flush=True)))
        return _emit(out, j)
    return 2


if __name__ == "__main__":
    sys.exit(main())
