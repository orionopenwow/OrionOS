"""Backup with restore drills, tested with a stand-in restic (a folder of copies) and a stand-in Clean Box."""
import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

import backup as bk
from mcp_tools import McpConfig

FAKE_RESTIC = r'''
import fnmatch, hashlib, json, os, shutil, sys, time, uuid
from pathlib import Path
a = sys.argv[1:]
open(os.environ["FAKE_RESTIC_LOG"], "a").write(json.dumps({"argv": a, "repo": os.environ.get("RESTIC_REPOSITORY"),
    "pwfile": os.environ.get("RESTIC_PASSWORD_FILE"), "aws": bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))}) + "\n")
assert os.environ.get("RESTIC_PASSWORD_FILE") and "RESTIC_PASSWORD" not in os.environ
repo = Path(os.environ["RESTIC_REPOSITORY"]) if not os.environ["RESTIC_REPOSITORY"].startswith("s3:") else \
    Path(os.environ["FAKE_S3_DIR"])
cmd = a[0]
def opt(name):
    return [a[i + 1] for i, x in enumerate(a) if x == name]
def snaps():
    p = repo / "snapshots.json"
    return json.loads(p.read_text()) if p.exists() else []
if cmd == "cat":
    sys.exit(0 if (repo / "config").exists() else 10)
if cmd == "init":
    repo.mkdir(parents=True, exist_ok=True); (repo / "config").write_text("x"); sys.exit(0)
if cmd == "backup":
    excl = opt("--exclude")
    skip_next = {"--tag", "--exclude"}
    paths, i = [], 1
    while i < len(a):
        if a[i] in skip_next: i += 2; continue
        if a[i].startswith("--"): i += 1; continue
        paths.append(a[i]); i += 1
    sid = uuid.uuid4().hex[:8]
    store = repo / "data" / sid
    n = 0
    for p in paths:
        for root, dirs, files in os.walk(p):
            for f in files:
                full = Path(root) / f
                parts = full.parts
                bad = any(fnmatch.fnmatch("/".join(parts[j:j + len(x.split("/"))]), x)
                          for x in excl for j in range(len(parts)))
                if bad: continue
                dst = store / str(full).lstrip("/")
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(full, dst); n += 1
    s = snaps(); s.append({"id": sid * 8, "short_id": sid, "time": "2026-10-16T21:19:00.123456789+00:00",
                           "paths": paths, "tags": opt("--tag")})
    (repo / "snapshots.json").write_text(json.dumps(s))
    print(json.dumps({"message_type": "status", "percent_done": 0.5}))
    print(json.dumps({"message_type": "summary", "snapshot_id": sid, "files_new": n, "files_changed": 0,
                      "data_added": 1234, "total_files_processed": n}))
    sys.exit(0)
if cmd == "forget":
    sys.exit(0)
if cmd == "snapshots":
    print(json.dumps(snaps())); sys.exit(0)
if cmd == "ls":
    sid = a[-1]; store = repo / "data" / sid
    print(json.dumps({"struct_type": "snapshot", "id": sid}))
    for root, dirs, files in os.walk(store):
        for f in files:
            full = Path(root) / f
            orig = "/" + str(full.relative_to(store))
            st = full.stat()
            print(json.dumps({"name": f, "type": "file", "path": orig, "size": st.st_size,
                              "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)) + ".5+00:00"}))
    sys.exit(0)
if cmd == "restore":
    sid = a[1]
    if sid == "latest": sid = snaps()[-1]["short_id"]
    store = repo / "data" / sid
    target = Path(opt("--target")[0]); inc = opt("--include")
    for root, dirs, files in os.walk(store):
        for f in files:
            full = Path(root) / f
            orig = "/" + str(full.relative_to(store))
            if inc and orig not in inc: continue
            dst = target / orig.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = full.read_bytes()
            if os.environ.get("FAKE_RESTIC_DAMAGE") and orig.endswith(".json"):
                data = b"{broken"
            dst.write_bytes(data)
    sys.exit(0)
if cmd == "check":
    sys.exit(1 if os.environ.get("FAKE_RESTIC_BROKEN") else 0)
sys.exit(0)
'''


class FakeBox:
    """A Clean Box that 'runs' the checksum command on the host folder (no Podman needed)."""

    def __init__(self, corrupt=False):
        self.corrupt, self.runs = corrupt, []

    def available(self):
        return True

    def run(self, cmd, mounts=(), timeout=600, **k):
        import subprocess
        from clean_box import BoxResult
        self.runs.append((cmd, [m.arg() for m in mounts]))
        host = mounts[0].host
        out = subprocess.run("cd '%s' && find . -type f -print0 | xargs -0 -r sha256sum" % host, shell=True,
                             capture_output=True, text=True).stdout
        if self.corrupt:
            out = "0" * 64 + out[64:]
        return BoxResult(0, out)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "Projects" / "game"
    proj.mkdir(parents=True)
    (proj / "world.json").write_text(json.dumps({"zones": 3}))
    (proj / "notes.md").write_text("hello")
    (proj / "hero.blend").write_bytes(b"BLENDER-v300" + b"\0" * 50)
    (home / ".cache").mkdir()
    (home / ".cache" / "junk.bin").write_bytes(b"junk")
    data = home / ".orion"
    data.mkdir()
    (data / "memory.json").write_text("{}")
    usb = tmp_path / "usb"
    usb.mkdir()
    exe = tmp_path / "restic"
    exe.write_text(f"#!{sys.executable}\n" + FAKE_RESTIC)
    exe.chmod(0o755)
    log = tmp_path / "restic.log"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("ORION_RESTIC", str(exe))
    monkeypatch.setenv("FAKE_RESTIC_LOG", str(log))
    monkeypatch.setenv("FAKE_S3_DIR", str(tmp_path / "s3"))
    monkeypatch.delenv("ORION_BACKUP_S3_KEY_ID", raising=False)
    monkeypatch.delenv("ORION_BACKUP_S3_SECRET", raising=False)
    monkeypatch.setattr(bk, "data_dir", lambda: data)
    monkeypatch.setattr(McpConfig, "load", classmethod(lambda cls, *a, **k: McpConfig(roots=[home / "Projects"])))
    import alerts
    sent = []
    monkeypatch.setattr(alerts, "emit", lambda *a, **k: sent.append(a))
    return {"home": home, "proj": proj, "data": data, "usb": usb, "log": log, "alerts": sent, "tmp": tmp_path}


def restic_calls(env):
    return [json.loads(x) for x in env["log"].read_text().splitlines()] if env["log"].exists() else []


def test_not_set_up_says_what_to_do(env):
    with pytest.raises(bk.BackupError) as e:
        bk.backup_now()
    assert "USB drive" in str(e.value)
    st = bk.status()
    assert not st["configured"] and st["stale"] and "aren't set up" in st["summary"]


def test_backup_password_sources_and_recovery(env):
    bk.save_config({"usb": str(env["usb"] / "orion-backup")})
    rec = bk.backup_now()
    assert rec["ok"] and rec["results"][0]["target"] == "USB drive"
    pw = bk.password_file()
    assert pw.exists() and stat.S_IMODE(pw.stat().st_mode) == 0o600 and len(pw.read_text().strip()) >= 40
    first = pw.read_text()
    bk.backup_now()
    assert pw.read_text() == first                                   # made once, kept
    calls = restic_calls(env)
    assert [c["argv"][0] for c in calls][:3] == ["cat", "init", "backup"]
    assert all(c["pwfile"] == str(pw) for c in calls)
    assert all(first.strip() not in json.dumps(c) for c in calls)    # never on a command line
    bargs = next(c["argv"] for c in calls if c["argv"][0] == "backup")
    assert str(env["home"]) in bargs and str(env["proj"].parent) in bargs
    stored = list((env["usb"] / "orion-backup" / "data").rglob("*"))
    names = {p.name for p in stored if p.is_file()}
    assert {"world.json", "hero.blend", "memory.json"} <= names
    assert "junk.bin" not in names and "backup-password" not in names   # caches and the password stay out
    assert (env["usb"] / "HOW-TO-RESTORE-ORION.txt").exists()
    text = bk.recovery_text()
    assert "orion-ctl backup restore" in text and "restic -r" in text and first.strip() not in text
    st = bk.status()
    assert st["configured"] and not st["stale"] and st["age_days"] == 0.0


def test_usb_unplugged_and_bucket_without_keys(env):
    bk.save_config({"usb": str(env["tmp"] / "not-plugged" / "orion-backup"), "s3": "s3:https://example.test/b/o"})
    with pytest.raises(bk.BackupError) as e:
        bk.backup_now()
    assert "isn't plugged in" in str(e.value) and "keys aren't set" in str(e.value)
    with pytest.raises(bk.BackupError):
        bk.save_config({"s3": "https://no-prefix"})
    os.environ["ORION_BACKUP_S3_KEY_ID"] = "id-" + "fake"
    os.environ["ORION_BACKUP_S3_SECRET"] = "sec-" + "fake-" + "x" * 8
    try:
        rec = bk.backup_now()
    finally:
        os.environ.pop("ORION_BACKUP_S3_KEY_ID")
        os.environ.pop("ORION_BACKUP_S3_SECRET")
    assert not rec["ok"] and rec["results"][0]["target"] == "Cloud bucket" and rec["problems"]
    assert any(c["aws"] for c in restic_calls(env))


def test_database_hook(env, monkeypatch):
    mod = type(sys)("db_time_machine")
    mod.backup_dump = lambda dest: [Path(dest, "world.sql").write_text("-- dump") and Path(dest, "world.sql")]
    monkeypatch.setitem(sys.modules, "db_time_machine", mod)
    bk.save_config({"usb": str(env["usb"] / "r")})
    rec = bk.backup_now()
    assert rec["database_dumps"] == 1
    assert any(p.name == "world.sql" for p in (env["usb"] / "r").rglob("*"))


def test_restore_never_overwrites(env):
    bk.save_config({"usb": str(env["usb"] / "r")})
    bk.backup_now()
    res = bk.restore()
    dest = Path(res["target"])
    assert dest.parent == env["home"] / "Restored"
    assert (dest / str(env["proj"] / "world.json").lstrip("/")).read_text() == json.dumps({"zones": 3})
    with pytest.raises(bk.BackupError):
        bk.restore(target=str(dest))                                  # not empty: refused
    hist = (env["tmp"] / "state" / "orion" / "changes" / "backup.jsonl").read_text()
    assert "Restored a backup" in hist


def test_drill_passes_in_a_clean_box(env):
    bk.save_config({"usb": str(env["usb"] / "r")})
    bk.backup_now()
    box = FakeBox()
    import random
    rec = bk.run_drill(box=box, rng=random.Random(1))
    assert rec["ok"], rec["problems"]
    assert rec["where"] == "clean box" and rec["checked"] >= 3 and box.runs
    assert box.runs[0][1][0].endswith(":/mnt/restored:ro")
    assert bk.load_state()["last_drill"]["ok"]
    hist = [json.loads(x) for x in (env["tmp"] / "state" / "orion" / "changes" / "backup.jsonl").read_text()
            .splitlines()]
    assert hist[-1]["what"] == "Restore drill passed" and hist[-1]["ok"]


def test_drill_catches_damage(env, monkeypatch):
    bk.save_config({"usb": str(env["usb"] / "r")})
    bk.backup_now()
    monkeypatch.setenv("FAKE_RESTIC_DAMAGE", "1")
    rec = bk.run_drill(box=FakeBox())
    assert not rec["ok"] and any("world.json" in p for p in rec["problems"])
    monkeypatch.delenv("FAKE_RESTIC_DAMAGE")
    rec = bk.run_drill(box=FakeBox(corrupt=True))
    assert not rec["ok"] and any("differently" in p for p in rec["problems"])
    monkeypatch.setenv("FAKE_RESTIC_BROKEN", "1")
    rec = bk.run_drill(box=FakeBox())
    assert not rec["ok"] and any("restic's own check" in p for p in rec["problems"])


def test_drill_without_podman_says_so(env):
    bk.save_config({"usb": str(env["usb"] / "r")})
    bk.backup_now()

    class NoBox:
        def available(self):
            return False
    rec = bk.run_drill(box=NoBox())
    assert rec["ok"] and rec["where"] == "temporary folder" and "Podman isn't installed" in rec["note"]


def test_file_open_checks(tmp_path):
    import zipfile
    good = tmp_path / "a.docx"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("word/document.xml", "<x/>")
    assert bk.opens(good)[0]
    bad = tmp_path / "b.docx"
    bad.write_bytes(b"PK\x03\x04garbage")
    assert not bk.opens(bad)[0]
    (tmp_path / "c.png").write_bytes(b"\x89PNG\r\n\x1a\n....")
    assert bk.opens(tmp_path / "c.png")[0]
    (tmp_path / "d.blend").write_bytes(b"NOTBLEND")
    assert not bk.opens(tmp_path / "d.blend")[0]
    import sqlite3
    con = sqlite3.connect(tmp_path / "e.db")
    con.execute("create table t(x)")
    con.commit()
    con.close()
    assert bk.opens(tmp_path / "e.db")[0]


class FakeBus:
    def __init__(self):
        self.events, self.logs = [], []

    async def publish(self, e, **k):
        self.events.append(e)

    async def log(self, m, level="info"):
        self.logs.append((level, m))


class FakeDaemon:
    def __init__(self, tmp):
        from approval_queue import ApprovalQueue
        self.approvals = ApprovalQueue(Path(tmp) / "approvals.json")
        self.bus = FakeBus()


def test_service_schedules_backup_drill_alerts_and_card(env, monkeypatch):
    d = FakeDaemon(env["tmp"])
    svc = bk.BackupService(d)
    did = asyncio.run(svc.tick())
    assert did.get("backup") is None and not env["alerts"]          # not set up: nothing happens
    bk.save_config({"usb": str(env["usb"] / "r")})
    svc.box = FakeBox()
    did = asyncio.run(svc.tick())
    assert did["backup"]["ok"] and did["drill"]["ok"]
    card = d.approvals.pending()[0]
    assert card["kind"] == "backup_drill" and card["title"] == "Restore test passed"
    assert not env["alerts"]
    # three days on with the drive unplugged: one phone alert, not one an hour
    bk.save_config({"usb": str(env["tmp"] / "gone" / "r")})
    later = time.time() + 4 * 86400
    asyncio.run(svc.tick(now=later))
    asyncio.run(svc.tick(now=later + 3600))
    assert [a[1] for a in env["alerts"]] == ["Backups are behind"]
    # a failed drill reaches the phone and makes an urgent card
    bk.save_config({"usb": str(env["usb"] / "r")})
    monkeypatch.setenv("FAKE_RESTIC_BROKEN", "1")
    rec = asyncio.run(svc.drill())
    assert not rec["ok"] and env["alerts"][-1][1] == "Backup restore test failed"
    failed = next(c for c in d.approvals.pending() if c["title"] == "Restore test FAILED")
    asyncio.run(bk.decide_card(d, failed, "approve"))
    assert d.approvals.get(failed["id"])["status"] == "handled"
    assert d.approvals.get(card["id"])["status"] == "pending"


def test_routes(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(bk.make_router(lambda: FakeDaemon(env["tmp"])))
    c = TestClient(app)
    assert c.get("/backup/status").json()["configured"] is False
    r = c.post("/backup/config", json={"usb": str(env["usb"] / "r"), "secret": "x"})
    assert r.status_code == 400 and "daemon.env" in r.json()["error"]
    assert c.post("/backup/config", json={"usb": str(env["usb"] / "r")}).json()["ok"]
    assert c.post("/backup/now").json()["ok"]
    assert c.get("/backup/status").json()["last_backup"]["ok"]
    assert "orion-ctl backup restore" in c.get("/backup/recovery").json()["text"]
    assert c.post("/backup/restore", json={"target": str(env["home"])}).status_code == 400   # not empty


def test_cli_and_mcp(env, capsys):
    assert bk.main(["set", "--usb", str(env["usb"] / "r")]) == 0
    assert bk.main(["now"]) == 0 and "USB drive: done" in capsys.readouterr().out
    assert bk.main(["status"]) == 0 and "Last backup" in capsys.readouterr().out
    from mcp_tools import OrionTools
    tools = OrionTools(cfg=McpConfig(roots=[env["proj"]]), backend=object(), default_project=str(env["proj"]))
    text, err = asyncio.run(tools.call("backup_status", {}))
    assert not err and "Last backup" in text


def test_main_wires_backup():
    src = (Path(bk.__file__).parent / "main.py").read_text()
    assert "backup.make_router" in src and "backup.start(self)" in src and "backup.decide_card" in src
