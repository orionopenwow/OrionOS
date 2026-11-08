"""Backup with restore drills (docs/ORION_TOOLKIT_PLAN.md, tool 16).

Encrypted, versioned backups with restic, to a USB drive folder and (optionally) an S3-compatible bucket:
  * your projects (the folders agents may use, from mcp.json "roots", plus ~/Projects if it exists),
  * Orion's data folder (~/.orion), without its secrets folder,
  * your home folder (on a USB stick that's the vault's home), without caches, trash and app downloads,
  * Blender files (~/Blender and anything listed under "blender" in backup.json; .blend files in the folders above
    are already included),
  * database dumps, when DB Time Machine is set up (its dump hook is called first, if present).

The restic password is made on first use and kept in ~/.orion/secrets/backup-password (only you can read it);
it never goes into a file the repo tracks. Bucket keys come from the environment that holds Orion's other keys
(~/.orion/.env or ~/.config/orion/daemon.env): ORION_BACKUP_S3_KEY_ID and ORION_BACKUP_S3_SECRET.

The new part: once a month Orion restores a random sample from the newest backup, opens it in a Clean Box and
checks the files (checksums, and that they open). The result is a card in the Queue and a line in the change
history; a failed drill, or backups older than 3 days, also reach your phone.

    orion-ctl backup now | status | restore [SNAPSHOT] [--include PATH] [--target DIR] | drill
    orion-ctl backup set --usb /media/you/Drive/orion-backup [--s3 s3:https://host/bucket/orion]
    orion-ctl backup recovery | password

Settings (no secrets) live in ~/.orion/backup.json; the last results in ~/.orion/backup_state.json.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import gzip
import hashlib
import json
import logging
import os
import random
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

log = logging.getLogger("orion.backup")

try:   # routes resolve their annotations from module globals (this file uses `from __future__ import annotations`)
    from fastapi import Request
except ImportError:   # pragma: no cover - the command-line tools work without FastAPI
    Request = Any  # type: ignore

DAY = 86400
STALE_DAYS = 3
DRILL_EVERY_DAYS = 30
SAMPLE_FILES = 12
SAMPLE_MAX_BYTES = 200 * 1024 * 1024
CHECK_EVERY_S = 3600
CARD_KIND = "backup_drill"
EXCLUDES = [".cache", ".local/share/Trash", ".local/share/flatpak", ".var/app/*/cache", ".local/share/Steam",
            ".local/share/containers", "node_modules", "target", ".venv", "venv", "__pycache__", "*.tmp",
            ".orion/secrets", ".orion/drills", ".orion/restores", ".mozilla/firefox/*/cache2"]
RECOVERY = """How to get your files back (Orion backups)

Your backups are encrypted with restic. To read them you need two things:
  1. The backup itself: the folder on your USB drive ({usb}){s3}.
  2. The backup password. Orion keeps it in {pw_file}.
     Write it down or keep a copy in your vault: without it nobody, not even you, can open the backups.
     See it with:  orion-ctl backup password

On this computer:   orion-ctl backup restore           (puts the newest backup in ~/Restored, never over your files)
On a new computer:  install restic (sudo apt install restic), plug in the USB drive, then run
     restic -r "<the backup folder on the drive>" restore latest --target ~/Restored
  and type the password when it asks. Copy back what you need from ~/Restored.
"""


class BackupError(Exception):
    pass


# =====================================================================================  paths and settings
def data_dir() -> Path:
    from config import settings
    return Path(settings.data_dir)


def config_path() -> Path:
    return data_dir() / "backup.json"


def state_path() -> Path:
    return data_dir() / "backup_state.json"


def password_file() -> Path:
    return data_dir() / "secrets" / "backup-password"


def _read_json(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def load_config() -> dict[str, Any]:
    c = {"usb": "", "s3": "", "enabled": True, "extra": [], "blender": [], "home": True,
         "keep": {"daily": 7, "weekly": 5, "monthly": 12}, "drill_every_days": DRILL_EVERY_DAYS}
    c.update(_read_json(config_path()))
    return c


def save_config(changes: dict[str, Any]) -> dict[str, Any]:
    c = load_config()
    for k in ("usb", "s3"):
        if k in changes:
            v = str(changes[k] or "").strip()
            if k == "s3" and v and not v.startswith("s3:"):
                raise BackupError("The bucket address starts with s3: (for example s3:https://host/bucket/orion).")
            if k == "usb" and v and not Path(os.path.expanduser(v)).is_absolute():
                raise BackupError("Give the full path of the folder on the USB drive.")
            if any(ch in v for ch in "\n\r\x00"):
                raise BackupError("That address isn't valid.")
            c[k] = v
    if "enabled" in changes:
        c["enabled"] = bool(changes["enabled"])
    _write_json(config_path(), c)
    return c


def load_state() -> dict[str, Any]:
    s = {"last_backup": None, "last_drill": None, "history": [], "stale_alert_at": 0}
    s.update(_read_json(state_path()))
    return s


def save_state(s: dict[str, Any]) -> None:
    s["history"] = s.get("history", [])[-60:]
    _write_json(state_path(), s)


def ensure_password() -> Path:
    """Make the repository password on first use (owner-only file); return its path."""
    p = password_file()
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_urlsafe(32) + "\n")
    return p


def restic_bin() -> Optional[str]:
    env = os.environ.get("ORION_RESTIC")
    if env:
        return env if os.access(env, os.X_OK) else None
    return shutil.which("restic")


def targets(cfg: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    if cfg.get("usb"):
        out.append({"name": "USB drive", "repo": os.path.expanduser(cfg["usb"]), "kind": "usb"})
    if cfg.get("s3"):
        out.append({"name": "Cloud bucket", "repo": cfg["s3"], "kind": "s3"})
    return out


def sources(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """What gets backed up, as (label, path) for folders that exist."""
    home = Path.home()
    picks: list[tuple[str, Path]] = []
    try:
        from mcp_tools import McpConfig
        for r in McpConfig.load().resolved_roots():
            picks.append(("Projects", r))
    except Exception:
        pass
    for name in ("Projects", "projects"):
        picks.append(("Projects", home / name))
    picks.append(("Orion's data", data_dir()))
    if cfg.get("home", True):
        picks.append(("Home folder (vault)", home))
    for b in [home / "Blender", *[Path(os.path.expanduser(x)) for x in cfg.get("blender") or []]]:
        picks.append(("Blender files", b))
    for x in cfg.get("extra") or []:
        picks.append(("Extra", Path(os.path.expanduser(str(x)))))
    out, seen = [], set()
    for label, p in picks:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp.is_dir() and str(rp) not in seen:
            seen.add(str(rp))
            out.append({"label": label, "path": str(rp)})
    return out


def database_dumps(dest: Path) -> list[str]:
    """Ask DB Time Machine (if it exists) for fresh dumps; returns the dump files. Never fails the backup."""
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    for mod_name in ("db_time_machine", "db_timemachine"):
        try:
            mod = __import__(mod_name)
        except Exception:
            continue
        hook = getattr(mod, "backup_dump", None) or getattr(mod, "dump_for_backup", None)
        if hook is None:
            continue
        try:
            res = hook(dest)
            return [str(x) for x in (res or [])]
        except Exception as exc:
            log.warning("database dump hook failed: %s", exc)
            return []
    cmd = os.environ.get("ORION_BACKUP_DB_HOOK", "").strip()
    if cmd:
        try:
            subprocess.run([cmd, str(dest)], timeout=3600, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("database dump hook failed: %s", exc)
        return [str(p) for p in dest.iterdir()]
    return []


# =====================================================================================  restic
class Restic:
    def __init__(self, repo: str, kind: str = "usb", binary: Optional[str] = None) -> None:
        self.bin = binary or restic_bin()
        if not self.bin:
            raise BackupError("restic isn't installed. Install it with: sudo apt install restic")
        self.repo, self.kind = repo, kind

    def env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("RESTIC_", "AWS_"))}
        e["RESTIC_REPOSITORY"] = self.repo
        e["RESTIC_PASSWORD_FILE"] = str(ensure_password())
        if self.kind == "s3":
            kid, sec = os.environ.get("ORION_BACKUP_S3_KEY_ID", ""), os.environ.get("ORION_BACKUP_S3_SECRET", "")
            if not (kid and sec):
                raise BackupError("The bucket's keys aren't set. Add ORION_BACKUP_S3_KEY_ID and "
                                  "ORION_BACKUP_S3_SECRET to ~/.config/orion/daemon.env.")
            e["AWS_ACCESS_KEY_ID"], e["AWS_SECRET_ACCESS_KEY"] = kid, sec
        return e

    def run(self, *args: str, timeout: float = 6 * 3600) -> subprocess.CompletedProcess:
        try:
            return subprocess.run([self.bin, *args], env=self.env(), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise BackupError("restic took too long and was stopped")
        except OSError as exc:
            raise BackupError(f"restic couldn't start: {exc}")

    @staticmethod
    def why(p: subprocess.CompletedProcess) -> str:
        lines = [x for x in (p.stderr or p.stdout or "").strip().splitlines() if x.strip()]
        return lines[-1][:200] if lines else f"exit code {p.returncode}"

    def ensure_repo(self) -> bool:
        """True when the repository was just made."""
        if self.kind == "usb":
            parent = Path(self.repo).parent
            if not parent.exists():
                raise BackupError(f"The USB drive isn't plugged in ({parent} is missing).")
        if self.run("cat", "config", timeout=120).returncode == 0:
            return False
        if self.kind == "usb":
            Path(self.repo).mkdir(parents=True, exist_ok=True)
        p = self.run("init", timeout=300)
        if p.returncode != 0:
            raise BackupError("Couldn't make the backup: " + self.why(p))
        if self.kind == "usb":
            try:
                (Path(self.repo).parent / "HOW-TO-RESTORE-ORION.txt").write_text(recovery_text(), encoding="utf-8")
            except OSError:
                pass
        return True

    def backup(self, paths: list[str], tags: list[str]) -> dict[str, Any]:
        args = ["backup", "--json", "--exclude-caches", "--one-file-system"]
        for t in tags:
            args += ["--tag", t]
        for x in EXCLUDES:
            args += ["--exclude", x]
        p = self.run(*args, *paths)
        summary: dict[str, Any] = {}
        for line in (p.stdout or "").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("message_type") == "summary":
                summary = m
        if p.returncode not in (0, 3) or not summary:   # 3 = done, but some files couldn't be read
            raise BackupError("The backup didn't finish: " + self.why(p))
        return {"snapshot": summary.get("snapshot_id", ""), "files_new": summary.get("files_new", 0),
                "files_changed": summary.get("files_changed", 0), "bytes_added": summary.get("data_added", 0),
                "files": summary.get("total_files_processed", 0), "partial": p.returncode == 3}

    def forget(self, keep: dict[str, int]) -> None:
        self.run("forget", "--tag", "orion", "--prune", "--keep-daily", str(keep.get("daily", 7)),
                 "--keep-weekly", str(keep.get("weekly", 5)), "--keep-monthly", str(keep.get("monthly", 12)))

    def snapshots(self) -> list[dict[str, Any]]:
        p = self.run("snapshots", "--json", "--tag", "orion", timeout=300)
        if p.returncode != 0:
            raise BackupError("Couldn't read the backups: " + self.why(p))
        try:
            snaps = json.loads(p.stdout or "[]") or []
        except ValueError:
            snaps = []
        return sorted(snaps, key=lambda s: s.get("time", ""))

    def ls(self, snapshot: str) -> list[dict[str, Any]]:
        p = self.run("ls", "--json", snapshot, timeout=1800)
        if p.returncode != 0:
            raise BackupError("Couldn't list the backup: " + self.why(p))
        out = []
        for line in (p.stdout or "").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("type") == "file" and m.get("path"):
                out.append(m)
        return out

    def restore(self, snapshot: str, target: Path, includes: Optional[list[str]] = None) -> None:
        args = ["restore", snapshot, "--target", str(target)]
        for i in includes or []:
            args += ["--include", i]
        p = self.run(*args)
        if p.returncode != 0:
            raise BackupError("The restore didn't finish: " + self.why(p))

    def check(self) -> tuple[bool, str]:
        p = self.run("check", timeout=3 * 3600)
        return p.returncode == 0, "" if p.returncode == 0 else self.why(p)


# =====================================================================================  backup now
def recovery_text(cfg: Optional[dict[str, Any]] = None) -> str:
    cfg = cfg or load_config()
    return RECOVERY.format(usb=cfg.get("usb") or "not set yet",
                           s3=f", or the cloud bucket ({cfg['s3']})" if cfg.get("s3") else "",
                           pw_file=password_file())


def _record_change(what: str, why: str, ok: bool, **extra: Any) -> None:
    """A line in the readable change history. Uses the shared helper when another part of Orion provides it."""
    for mod_name in ("change_history", "changes", "history"):
        try:
            mod = __import__(mod_name)
        except Exception:
            continue
        fn = getattr(mod, "record_change", None)
        if fn is None:
            continue
        try:
            fn(source="backup", what=what, why=why, ok=ok, **extra)
            return
        except TypeError:
            try:
                fn("backup", what)
                return
            except Exception:
                pass
        except Exception:
            pass
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    p = base / "orion" / "changes" / "backup.jsonl"
    entry = {"id": f"chg-{uuid.uuid4().hex[:10]}", "ts": time.time(),
             "time": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "source": "backup",
             "kind": "backup", "what": what, "why": why, "ok": ok, **extra}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def backup_now(why: str = "you asked") -> dict[str, Any]:
    cfg = load_config()
    tgts = targets(cfg)
    if not tgts:
        raise BackupError("No backup place is set yet. Choose a USB drive folder in Settings, Backups "
                          "(or: orion-ctl backup set --usb /media/…/orion-backup).")
    srcs = sources(cfg)
    dumps_dir = data_dir() / "backup-dumps"
    dumps = database_dumps(dumps_dir)
    paths = [s["path"] for s in srcs]
    if dumps and str(dumps_dir) not in paths and not any(str(dumps_dir).startswith(p + "/") for p in paths):
        paths.append(str(dumps_dir))
    results, problems = [], []
    for t in tgts:
        try:
            r = Restic(t["repo"], t["kind"])
            r.ensure_repo()
            res = r.backup(paths, ["orion"])
            r.forget(cfg.get("keep") or {})
            results.append({"target": t["name"], **res})
        except BackupError as exc:
            problems.append(f"{t['name']}: {exc}")
    st = load_state()
    ok = bool(results) and not problems
    rec = {"at": time.time(), "ok": ok, "why": why, "results": results, "problems": problems,
           "sources": [s["label"] for s in srcs], "database_dumps": len(dumps)}
    if results:
        st["last_backup"] = rec
    st["last_attempt"] = rec
    st.setdefault("history", []).append({"kind": "backup", "at": rec["at"], "ok": ok,
                                         "problems": problems[:3]})
    save_state(st)
    if not results:
        raise BackupError("The backup failed. " + " ".join(problems))
    return rec


# =====================================================================================  restore drill
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def opens(p: Path) -> tuple[bool, str]:
    """Does the restored file look like a working file of its type? (True, '') or (False, why)."""
    ext = p.suffix.lower()
    try:
        with p.open("rb") as fh:
            head = fh.read(16)
        if ext in (".zip", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".jar", ".kra", ".ora"):
            with zipfile.ZipFile(p) as z:
                bad = z.testzip()
            return (bad is None, f"damaged part {bad}" if bad else "")
        if ext in (".json",):
            json.loads(p.read_text(encoding="utf-8"))
        elif ext in (".gz", ".tgz"):
            with gzip.open(p, "rb") as g:
                while g.read(1 << 20):
                    pass
        elif ext in (".png",):
            return head.startswith(b"\x89PNG\r\n\x1a\n"), "not a PNG picture"
        elif ext in (".jpg", ".jpeg"):
            return head.startswith(b"\xff\xd8"), "not a JPEG picture"
        elif ext == ".pdf":
            data = p.read_bytes()
            return data.startswith(b"%PDF") and b"%%EOF" in data[-2048:], "not a complete PDF"
        elif ext in (".blend",):
            if head.startswith(b"\x1f\x8b"):        # compressed .blend
                with gzip.open(p, "rb") as g:
                    head = g.read(7)
            elif head.startswith(b"\x28\xb5\x2f\xfd"):   # zstd-compressed (Blender 3+): can't open without zstd
                return True, ""
            return head.startswith(b"BLENDER"), "not a Blender file"
        elif ext in (".db", ".sqlite", ".sqlite3"):
            if head.startswith(b"SQLite format 3"):
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                try:
                    r = con.execute("PRAGMA integrity_check").fetchone()
                finally:
                    con.close()
                return (r and r[0] == "ok"), "database damaged"
        elif ext in (".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".rs", ".ts", ".tsx", ".js", ".sql", ".lua",
                     ".cpp", ".h", ".c", ".kt", ".sh", ".css", ".html"):
            p.read_text(encoding="utf-8")
        else:
            with p.open("rb") as fh:
                while fh.read(1 << 20):
                    pass
    except (OSError, ValueError, zipfile.BadZipFile, EOFError, sqlite3.Error, UnicodeDecodeError) as exc:
        return False, f"{type(exc).__name__}"
    return True, ""


def pick_sample(files: list[dict[str, Any]], n: int = SAMPLE_FILES, rng: Optional[random.Random] = None
                ) -> list[dict[str, Any]]:
    rng = rng or random.Random()
    ok = [f for f in files if 0 < int(f.get("size") or 0) <= SAMPLE_MAX_BYTES]
    if len(ok) <= n:
        return ok
    # Prefer a mix: some precious kinds (Blender, databases, documents) when there are any.
    precious = [f for f in ok if Path(f["path"]).suffix.lower() in (".blend", ".sql", ".db", ".sqlite", ".pdf",
                                                                    ".docx", ".psd", ".png", ".json")]
    first = rng.sample(precious, min(len(precious), n // 3))
    rest = [f for f in ok if f not in first]
    return first + rng.sample(rest, n - len(first))


def run_drill(box: Any = None, rng: Optional[random.Random] = None, n: int = SAMPLE_FILES) -> dict[str, Any]:
    """Restore a random sample from the newest backup, open it in a Clean Box, check every file."""
    cfg = load_config()
    tgts = targets(cfg)
    if not tgts:
        raise BackupError("No backup place is set yet, so there's nothing to test.")
    t = tgts[0]
    r = Restic(t["repo"], t["kind"])
    snaps = r.snapshots()
    if not snaps:
        raise BackupError("There are no backups to test yet.")
    snap = snaps[-1]
    sample = pick_sample(r.ls(snap.get("short_id") or snap["id"]), n, rng)
    problems: list[str] = []
    checked: list[dict[str, Any]] = []
    snap_time = snap.get("time", "")
    with tempfile.TemporaryDirectory(prefix="orion-drill-") as tmp:
        target = Path(tmp)
        if sample:
            r.restore(snap.get("short_id") or snap["id"], target, [f["path"] for f in sample])
        hashes: dict[str, str] = {}
        for f in sample:
            got = target / f["path"].lstrip("/")
            row = {"path": f["path"], "ok": True, "why": ""}
            if not got.is_file():
                row.update(ok=False, why="missing after restore")
            elif got.stat().st_size != int(f.get("size") or 0):
                row.update(ok=False, why="wrong size")
            else:
                hashes[f["path"]] = _sha256(got)
                good, why = opens(got)
                if not good:
                    row.update(ok=False, why=why or "doesn't open")
                orig = Path(f["path"])
                try:   # unchanged since the backup? then the copy must match it exactly
                    st = orig.stat()
                    if st.st_size == int(f.get("size") or 0) and f.get("mtime") and \
                            abs(st.st_mtime - _ts(f["mtime"])) < 1 and _sha256(orig) != hashes[f["path"]]:
                        row.update(ok=False, why="differs from the original")
                except OSError:
                    pass
            checked.append(row)
        where = "temporary folder"
        box_note = ""
        if box is None:
            from clean_box import CleanBox
            box = CleanBox()
        if sample and box.available():
            from clean_box import BoxError, Mount
            try:
                res = box.run("cd /mnt/restored && find . -type f -print0 | xargs -0 -r sha256sum",
                              mounts=[Mount(str(target), "/mnt/restored")], timeout=900)
                where = "clean box"
                if not res.ok:
                    problems.append("The Clean Box couldn't read the restored files.")
                seen = {}
                for line in res.output.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and len(parts[0]) == 64:
                        name = parts[1].strip().lstrip("*")
                        seen["/" + (name[2:] if name.startswith("./") else name)] = parts[0]
                for row in checked:
                    want = hashes.get(row["path"])
                    if want and seen.get(row["path"]) != want:
                        row.update(ok=False, why="the clean machine read it differently")
            except BoxError as exc:
                box_note = f"Clean Box unavailable ({exc}); checked in a temporary folder instead."
        elif sample:
            box_note = "Podman isn't installed, so the files were checked in a temporary folder instead of a Clean Box."
    struct_ok, struct_why = r.check()
    if not struct_ok:
        problems.append(f"restic's own check found a problem: {struct_why}")
    bad = [c for c in checked if not c["ok"]]
    problems += [f"{c['path']}: {c['why']}" for c in bad[:10]]
    ok = not problems and bool(checked)
    if not checked:
        problems.append("The newest backup has no files to test.")
    rec = {"at": time.time(), "ok": ok, "target": t["name"], "snapshot": snap.get("short_id") or snap.get("id"),
           "snapshot_time": snap_time, "checked": len(checked), "failed": len(bad), "where": where,
           "note": box_note, "problems": problems, "files": checked}
    st = load_state()
    st["last_drill"] = {k: v for k, v in rec.items() if k != "files"}
    st.setdefault("history", []).append({"kind": "drill", "at": rec["at"], "ok": ok, "problems": problems[:3]})
    save_state(st)
    _record_change("Restore drill " + ("passed" if ok else "FAILED"),
                   f"monthly test: restored {len(checked)} random files from the {t['name']} backup "
                   f"and checked them in a {where}", ok, checks=[f"{len(checked) - len(bad)} of {len(checked)} "
                                                                 "files open and match"],
                   undo="nothing to undo: the drill only reads the backup")
    return rec


def _ts(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    try:   # restic: 2026-10-16T21:19:00.123456789+01:00 -> trim the nanoseconds
        if "." in s:
            main, rest = s.split(".", 1)
            frac = "".join(ch for ch in rest if ch.isdigit())[:6]
            tz = rest[len("".join(ch for ch in rest if ch.isdigit())):]
            s = f"{main}.{frac}{tz}"
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def restore(snapshot: str = "latest", include: Optional[list[str]] = None, target: Optional[str] = None
            ) -> dict[str, Any]:
    """Restore into a NEW folder (default ~/Restored/<date>); never over your files."""
    cfg = load_config()
    tgts = targets(cfg)
    if not tgts:
        raise BackupError("No backup place is set yet.")
    dest = Path(os.path.expanduser(target)) if target else \
        Path.home() / "Restored" / _dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    if dest.exists() and any(dest.iterdir()):
        raise BackupError(f"{dest} isn't empty. Pick a new folder so nothing of yours is overwritten.")
    dest.mkdir(parents=True, exist_ok=True)
    t = tgts[0]
    Restic(t["repo"], t["kind"]).restore(snapshot or "latest", dest, include)
    _record_change(f"Restored a backup into {dest}", "you asked", True, undo=f"delete {dest} if you don't need it")
    return {"ok": True, "target": str(dest), "from": t["name"], "snapshot": snapshot or "latest"}


def status() -> dict[str, Any]:
    cfg = load_config()
    st = load_state()
    now = time.time()
    last = st.get("last_backup") or {}
    age = (now - last["at"]) / DAY if last.get("at") else None
    drill = st.get("last_drill") or {}
    return {
        "configured": bool(targets(cfg)), "restic": bool(restic_bin()), "usb": cfg.get("usb", ""),
        "s3": cfg.get("s3", ""), "s3_keys_set": bool(os.environ.get("ORION_BACKUP_S3_KEY_ID")
                                                       and os.environ.get("ORION_BACKUP_S3_SECRET")),
        "enabled": cfg.get("enabled", True),
        "last_backup": last or None, "age_days": round(age, 1) if age is not None else None,
        "stale": age is None or age > STALE_DAYS, "last_attempt": st.get("last_attempt"),
        "last_drill": drill or None,
        "drill_due": not drill or now - drill.get("at", 0) > cfg.get("drill_every_days", DRILL_EVERY_DAYS) * DAY,
        "sources": sources(cfg), "password_file": str(password_file()),
        "password_made": password_file().exists(), "summary": summary_line(cfg, st, now),
    }


def summary_line(cfg: dict[str, Any], st: dict[str, Any], now: float) -> str:
    if not targets(cfg):
        return "Backups aren't set up yet: choose a USB drive folder."
    last = st.get("last_backup") or {}
    if not last:
        return "No backup yet."
    hours = (now - last["at"]) / 3600
    when = f"{int(hours)} hours ago" if hours < 48 else f"{int(hours / 24)} days ago"
    drill = st.get("last_drill") or {}
    d = ("never tested yet" if not drill else
         "the last restore test passed" if drill.get("ok") else "the last restore test FAILED")
    return f"Last backup {when}; {d}."


# =====================================================================================  daemon service
class BackupService:
    """Hourly: back up once a day, warn when backups are older than 3 days, run the monthly drill, and clear
    Clean Boxes whose time is up."""

    def __init__(self, daemon: Any) -> None:
        self.daemon = daemon
        self.box: Any = None          # a CleanBox for drills (None: a fresh one each time)
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None and os.environ.get("ORION_BACKUP_SCHEDULE", "1") != "0":
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="backup")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(300)
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("backup check failed")
            await asyncio.sleep(CHECK_EVERY_S)

    async def tick(self, now: Optional[float] = None) -> dict[str, Any]:
        now = now or time.time()
        did: dict[str, Any] = {}
        try:
            from clean_box import CleanBox
            did["boxes_removed"] = await asyncio.to_thread(CleanBox().sweep, now)
        except Exception:
            pass
        cfg = load_config()
        if not targets(cfg) or not cfg.get("enabled", True) or not restic_bin():
            return did
        st = load_state()
        last = (st.get("last_backup") or {}).get("at", 0)
        tried = (st.get("last_attempt") or {}).get("at", 0)
        if now - last > DAY and now - tried > 6 * 3600:
            try:
                did["backup"] = await self.backup("daily backup")
            except BackupError as exc:
                did["backup_error"] = str(exc)
            st = load_state()
            last = (st.get("last_backup") or {}).get("at", 0)
        if now - last > STALE_DAYS * DAY and now - st.get("stale_alert_at", 0) > DAY:
            days = "any backup yet" if not last else f"a backup for {int((now - last) / DAY)} days"
            _alert("Backups are behind", f"There hasn't been {days}. Plug in the backup drive or open Settings, "
                                         "Backups.")
            st["stale_alert_at"] = now
            save_state(st)
            did["stale_alert"] = True
        drill = st.get("last_drill") or {}
        if last and now - drill.get("at", 0) > cfg.get("drill_every_days", DRILL_EVERY_DAYS) * DAY:
            try:
                did["drill"] = await self.drill()
            except BackupError as exc:
                did["drill_error"] = str(exc)
        return did

    async def backup(self, why: str = "you asked") -> dict[str, Any]:
        async with self._lock:
            try:
                rec = await asyncio.to_thread(backup_now, why)
            except BackupError as exc:
                await self._log(f"Backup failed: {exc}", "warn")
                raise
        await self._log("Backup done." if rec["ok"] else "Backup partly done: " + "; ".join(rec["problems"]))
        return rec

    async def drill(self) -> dict[str, Any]:
        async with self._lock:
            try:
                rec = await asyncio.to_thread(run_drill, self.box)
            except BackupError as exc:
                rec = {"at": time.time(), "ok": False, "checked": 0, "failed": 0, "problems": [str(exc)],
                       "where": "", "note": ""}
                st = load_state()
                st["last_drill"] = rec
                st.setdefault("history", []).append({"kind": "drill", "at": rec["at"], "ok": False,
                                                     "problems": rec["problems"]})
                save_state(st)
        await self.card(rec)
        if not rec["ok"]:
            _alert("Backup restore test failed", "; ".join(rec["problems"])[:300] or "Open Orion for details.")
        return rec

    async def card(self, rec: dict[str, Any]) -> None:
        d = self.daemon
        if d is None or not hasattr(d, "approvals"):
            return
        title = ("Restore test passed" if rec["ok"] else "Restore test FAILED")
        lines = [f"Orion restored {rec.get('checked', 0)} random files from your newest backup"
                 + (f" ({rec.get('target')})" if rec.get("target") else "")
                 + (f" and opened them in a {rec['where']}." if rec.get("where") else ".")]
        if rec["ok"]:
            lines.append("Every file opened and matched. Your backups work.")
        else:
            lines += ["What went wrong:", *[f"- {p}" for p in rec.get("problems", [])[:10]]]
        if rec.get("note"):
            lines.append(rec["note"])
        try:
            item = d.approvals.add(CARD_KIND, platform="Backups", channel="Backups", author="Backup drill",
                                   category="normal" if rec["ok"] else "urgent", title=title,
                                   original="\n".join(lines), draft="Approve = seen. Reject = dismiss.",
                                   drill={k: rec.get(k) for k in ("ok", "checked", "failed", "where", "snapshot")})
            await d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
        except Exception:
            log.exception("Could not post the drill card")

    async def _log(self, msg: str, level: str = "info") -> None:
        try:
            await self.daemon.bus.log(msg, level)
        except Exception:
            pass


def _alert(title: str, body: str) -> None:
    try:
        import alerts
        alerts.emit("system", title, body, ref="backup")
    except Exception:
        pass


_service: Optional[BackupService] = None


def start(daemon: Any) -> BackupService:
    """One line in the daemon's start(): the hourly backup / drill / Clean Box sweep loop, plus the drill card."""
    global _service
    _service = BackupService(daemon)
    _service.start()
    return _service


async def stop() -> None:
    if _service is not None:
        await _service.stop()


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    """The drill card is information only: approve = seen, reject = dismissed."""
    updated = daemon.approvals.update(item["id"], status="rejected" if decision == "reject" else "handled")
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})


# =====================================================================================  routes
def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/backup", tags=["toolkit"])

    def svc() -> BackupService:
        global _service
        if _service is None:
            _service = BackupService(get_daemon())
        return _service

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    @router.get("/status")
    async def get_status() -> Any:
        return await asyncio.to_thread(status)

    @router.post("/now")
    async def post_now() -> Any:
        try:
            return {"ok": True, "backup": await svc().backup("you asked")}
        except BackupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.post("/drill")
    async def post_drill() -> Any:
        rec = await svc().drill()
        return {"ok": rec["ok"], "drill": {k: v for k, v in rec.items() if k != "files"}}

    @router.post("/restore")
    async def post_restore(request: Request) -> Any:
        b = await body_of(request)
        inc = b.get("include")
        try:
            return await asyncio.to_thread(restore, str(b.get("snapshot") or "latest"),
                                           [str(x) for x in inc] if isinstance(inc, list) else None,
                                           str(b["target"]) if b.get("target") else None)
        except BackupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/config")
    async def get_config() -> Any:
        return load_config()

    @router.post("/config")
    async def post_config(request: Request) -> Any:
        b = await body_of(request)
        if any(k in b for k in ("key", "secret", "password", "s3_key_id", "s3_secret")):
            return JSONResponse({"error": "Keys aren't taken here. Put the bucket keys in "
                                          "~/.config/orion/daemon.env (ORION_BACKUP_S3_KEY_ID, "
                                          "ORION_BACKUP_S3_SECRET)."}, status_code=400)
        try:
            return {"ok": True, "config": save_config(b)}
        except BackupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @router.get("/recovery")
    async def get_recovery() -> Any:
        return {"text": recovery_text()}

    return router


# =====================================================================================  MCP tool
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import Tool, _schema

    async def backup_status(_a: dict[str, Any]) -> str:
        s = await asyncio.to_thread(status)
        lines = [s["summary"]]
        d = s.get("last_drill") or {}
        if d:
            lines.append(f"Last restore test: {'passed' if d.get('ok') else 'failed'}, {d.get('checked', 0)} files "
                         f"checked in a {d.get('where') or 'temporary folder'}.")
        db = (s.get("last_backup") or {}).get("database_dumps")
        if db is not None:
            lines.append(f"Database dumps in the last backup: {db}.")
        return "\n".join(lines)

    return [Tool("backup_status", "Backup status",
                 "When the last backup ran, whether the last restore test passed, and whether backups are behind.",
                 "read", _schema({}), backup_status)]


# =====================================================================================  command line
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orion-ctl backup", description="Orion backups with restore drills.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("now", help="back up now")
    s = sub.add_parser("status", help="when the last backup and restore test ran")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("restore", help="restore into a new folder (never over your files)")
    r.add_argument("snapshot", nargs="?", default="latest")
    r.add_argument("--include", action="append")
    r.add_argument("--target")
    sub.add_parser("drill", help="test a restore now")
    c = sub.add_parser("set", help="where backups go")
    c.add_argument("--usb")
    c.add_argument("--s3")
    c.add_argument("--off", action="store_true")
    c.add_argument("--on", action="store_true")
    sub.add_parser("recovery", help="how to get your files back")
    sub.add_parser("password", help="show the backup password (to write down)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "now":
            rec = backup_now("you asked")
            for res in rec["results"]:
                mb = int(res.get("bytes_added") or 0) / 1e6
                print(f"{res['target']}: done ({res.get('files', 0)} files, {mb:.1f} MB new).")
            for p in rec["problems"]:
                print("Problem: " + p)
            return 0 if rec["ok"] else 1
        if a.cmd == "status":
            st = status()
            if a.json:
                print(json.dumps(st, indent=2))
            else:
                print(st["summary"])
                print("USB drive folder: " + (st["usb"] or "not set"))
                print("Cloud bucket: " + (st["s3"] or "not set"))
                if not st["restic"]:
                    print("restic isn't installed yet: sudo apt install restic")
            return 0
        if a.cmd == "restore":
            res = restore(a.snapshot, a.include, a.target)
            print(f"Restored into {res['target']}. Copy back what you need; your own files weren't touched.")
            return 0
        if a.cmd == "drill":
            rec = run_drill()
            print(("Restore test passed: " if rec["ok"] else "Restore test FAILED: ") +
                  f"{rec['checked'] - rec['failed']} of {rec['checked']} files opened and matched ({rec['where']}).")
            for p in rec["problems"]:
                print("  " + p)
            if rec.get("note"):
                print(rec["note"])
            return 0 if rec["ok"] else 1
        if a.cmd == "set":
            ch: dict[str, Any] = {}
            if a.usb is not None:
                ch["usb"] = a.usb
            if a.s3 is not None:
                ch["s3"] = a.s3
            if a.off or a.on:
                ch["enabled"] = bool(a.on)
            cfg = save_config(ch)
            print(f"Saved. USB drive folder: {cfg['usb'] or 'not set'}; cloud bucket: {cfg['s3'] or 'not set'}.")
            if cfg["s3"]:
                print("The bucket's keys go in ~/.config/orion/daemon.env as ORION_BACKUP_S3_KEY_ID and "
                      "ORION_BACKUP_S3_SECRET (never in a file you share).")
            return 0
        if a.cmd == "recovery":
            print(recovery_text())
            return 0
        if a.cmd == "password":
            p = ensure_password()
            if sys.stdin.isatty():
                ans = input("This shows the password that opens every backup. Nobody should see your screen. "
                            "Type yes: ")
                if ans.strip() != "yes":
                    print("Not shown.")
                    return 1
            print(p.read_text(encoding="utf-8").strip())
            return 0
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
