"""orion-snapshot (os/lab/bin/orion-snapshot): btrfs + snapper with fake commands, vault copies with real rsync in
temporary folders. Nothing touches the real system."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "os/lab/bin/orion-snapshot"


def load():
    loader = importlib.machinery.SourceFileLoader("orion_snapshot_cli", str(SCRIPT))
    spec = importlib.util.spec_from_loader("orion_snapshot_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


S = load()


def fake(tmp: Path, name: str, body: str) -> str:
    p = tmp / "bin" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(f"#!{sys.executable}\nimport json, os, sys\nargs = sys.argv[1:]\n"
                 f"open({str(tmp / 'calls.txt')!r}, 'a').write({name!r} + ' ' + ' '.join(args) + '\\n')\n" + body)
    p.chmod(0o755)
    return str(p)


def calls(tmp: Path) -> list[str]:
    f = tmp / "calls.txt"
    return f.read_text().splitlines() if f.exists() else []


# =====================================================================================  vault (USB stick)
@pytest.fixture()
def vault(tmp_path):
    root = tmp_path / "root"
    (root / "home/orion/.cache").mkdir(parents=True)
    (root / "home/orion/notes.txt").write_text("first notes\n")
    (root / "home/orion/big.bin").write_bytes(b"x" * 200_000)
    (root / "home/orion/.cache/junk").write_text("cache\n")
    (root / "etc").mkdir()
    (root / "etc/hostname").write_text("orionos\n")
    (root / "var/lib/orion").mkdir(parents=True)
    (root / "var/lib/orion/state.json").write_text("{}\n")
    (tmp_path / "vault").mkdir()
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/slots/a/vmlinuz orion.slot=a quiet\n")
    environ = {"ORION_SNAP_CMDLINE": str(tmp_path / "cmdline"), "ORION_SNAP_ROOT_FSTYPE": "overlay",
               "ORION_SNAP_VAULT": str(tmp_path / "vault"), "ORION_SNAP_SYSROOT": str(root),
               "ORION_SNAP_INDEX": str(tmp_path / "index.json"), "ORION_SNAP_CHANGES": str(tmp_path / "changes.jsonl"),
               "ORION_SNAP_CONF": str(tmp_path / "snapshots.conf"), "ORION_SNAP_NOW": "1760000000"}
    if not shutil.which("rsync"):
        pytest.skip("rsync isn't installed here")
    return SimpleNamespace(root=root, tmp=tmp_path, environ=environ, env=lambda **kw: S.Env({**environ, **kw}))


def test_vault_take_uses_hard_links_and_skips_caches(vault):
    env = vault.env()
    assert S.backend(env) == "vault"
    first = S.take(env, "nightly", "")
    assert first["ok"] and first["snapshot"]["id"].endswith("-nightly")
    sid1 = first["snapshot"]["id"]
    data1 = vault.tmp / "vault/orion-snapshots" / sid1 / "data"
    assert (data1 / "home/orion/notes.txt").read_text() == "first notes\n"
    assert (data1 / "etc/hostname").exists() and (data1 / "var/lib/orion/state.json").exists()
    assert not (data1 / "home/orion/.cache/junk").exists()                       # caches aren't worth the space
    (vault.root / "home/orion/notes.txt").write_text("second notes\n")
    env2 = vault.env(ORION_SNAP_NOW="1760003600")
    sid2 = S.take(env2, "pre-update", "before apt")["snapshot"]["id"]
    data2 = vault.tmp / "vault/orion-snapshots" / sid2 / "data"
    assert (data2 / "home/orion/notes.txt").read_text() == "second notes\n"
    # unchanged files cost nothing: the same file on disk as in the first copy
    assert (data2 / "home/orion/big.bin").stat().st_ino == (data1 / "home/orion/big.bin").stat().st_ino
    st = S.status(env2)
    assert st["backend"] == "vault" and st["count"] == 2 and st["undo_to"] == sid2
    assert "Snapshots use" in st["space"]["words"]
    idx = json.loads((vault.tmp / "index.json").read_text())
    assert idx["count"] == 2 and idx["last_take"]["pre-update"] == 1760003600
    hist = [json.loads(l) for l in (vault.tmp / "changes.jsonl").read_text().splitlines()]
    assert len(hist) == 1 and hist[0]["undo"]["snapshot"] == sid2                # nightly ones aren't noted
    assert S.take(env2, "pre-update", "again", debounce=300)["skipped"]           # apt runs dpkg several times


def test_vault_restore_one_file_and_everything(vault):
    env = vault.env()
    sid = S.take(env, "manual", "before an experiment")["snapshot"]["id"]
    (vault.root / "home/orion/notes.txt").write_text("broken\n")
    (vault.root / "home/orion/new.txt").write_text("made later\n")
    (vault.root / "etc/hostname").unlink()
    (vault.root / "home/orion/.cache/junk").write_text("newer cache\n")
    env2 = vault.env(ORION_SNAP_NOW="1760000500")
    msg = S.restore(env2, sid, "/home/orion/notes.txt")["message"]
    assert "Put back /home/orion/notes.txt" in msg
    assert (vault.root / "home/orion/notes.txt").read_text() == "first notes\n"
    assert (vault.root / "home/orion/notes.txt.before-restore-1760000500").read_text() == "broken\n"
    with pytest.raises(S.Failed):
        S.restore(env2, sid, "/usr/bin/python3")                                 # not covered on the stick
    with pytest.raises(S.Failed):
        S.restore(env2, sid, "/home/../etc/shadow-missing")
    with pytest.raises(S.Failed):
        S.restore(env2, "20990101-000000-manual")
    env3 = vault.env(ORION_SNAP_NOW="1760000900")
    S.restore(env3, sid)
    assert (vault.root / "etc/hostname").read_text() == "orionos\n"
    assert not (vault.root / "home/orion/new.txt").exists()
    assert (vault.root / "home/orion/.cache/junk").read_text() == "newer cache\n"   # excluded folders untouched
    kinds = [s["kind"] for s in S.vault_list(env3)]
    assert "pre-restore" in kinds                                                  # a restore can be undone too


def test_vault_limits_and_space(vault, monkeypatch):
    (vault.tmp / "snapshots.conf").write_text('VAULT_KEEP="2"\n')
    for i in range(3):
        S.take(vault.env(ORION_SNAP_NOW=str(1760000000 + i * 100)), "nightly", "")
    assert len(S.vault_list(vault.env())) == 2                                    # oldest went first
    # over the space cap: oldest removed, never the newest
    monkeypatch.setattr(S, "vault_space", lambda env: {"snapshots_used": 10, "cap": 1, "vault_free": 10 ** 12,
                                                       "vault_total": 10 ** 12, "words": ""})
    gone = S.vault_prune(vault.env())
    assert len(gone) == 1 and len(S.vault_list(vault.env())) == 1
    # not enough room on the vault: refuse, honestly
    real = shutil.disk_usage
    monkeypatch.setattr(S.shutil, "disk_usage", lambda p: SimpleNamespace(total=real(p).total, used=0, free=10))
    with pytest.raises(S.Failed, match="free on the vault"):
        S.take(vault.env(ORION_SNAP_NOW="1760009999"), "manual", "")


def test_no_snapshots_here_is_said_plainly(tmp_path):
    env = S.Env({"ORION_SNAP_CMDLINE": str(tmp_path / "none"), "ORION_SNAP_ROOT_FSTYPE": "ext4",
                 "ORION_SNAP_INDEX": str(tmp_path / "i.json"), "ORION_SNAP_SNAPPER_CONF": str(tmp_path / "nope")})
    assert S.backend(env) == "none"
    res = S.take(env, "pre-update", "")
    assert res["skipped"] and "aren't set up" in res["message"]
    with pytest.raises(S.Failed):
        S.undo(env)


# =====================================================================================  btrfs + snapper
@pytest.fixture()
def btrfs(tmp_path):
    snaps = tmp_path / "snapshots"
    boot = tmp_path / "boot"
    boot.mkdir()
    for k in ("6.8.0-45-generic", "6.8.0-47-generic"):
        (boot / f"vmlinuz-{k}").write_text("")
        (boot / f"initrd.img-{k}").write_text("")
    for num, kernel in (("5", "6.8.0-45-generic"), ("6", "6.8.0-40-generic"), ("7", "6.8.0-47-generic")):
        (snaps / num / "snapshot/usr/lib/modules" / kernel).mkdir(parents=True)
    listing = {"root": [
        {"number": 0, "type": "single", "date": "", "description": "current"},
        {"number": 5, "type": "single", "date": "2026-10-01 03:31:00", "description": "nightly",
         "cleanup": "timeline", "userdata": {"orion": "nightly"}},
        {"number": 6, "type": "single", "date": "2026-10-02 10:00:00", "description": "old kernel"},
        {"number": 7, "type": "single", "date": "2026-10-03 12:00:00", "description": "before installing packages",
         "cleanup": "number", "userdata": {"orion": "pre-update"}}]}
    top = tmp_path / "top"
    (top / "@").mkdir(parents=True)
    (top / "@/marker").write_text("current system\n")
    (top / "@snapshots/7/snapshot").mkdir(parents=True)
    snapper = fake(tmp_path, "snapper", f"""
if 'list' in args:
    print(json.dumps({listing!r}))
elif 'create' in args:
    print('8')
""")
    findmnt = fake(tmp_path, "findmnt", f"""
a = ' '.join(args)
out = {{'-no FSTYPE /': 'btrfs', '-no OPTIONS /': os.environ.get('FAKE_OPTS', 'rw,noatime,compress=zstd:1,subvol=/@'),
       '-no SOURCE /': '/dev/mapper/orion-root[/@]', '-no UUID /': 'ROOT-UUID', '-no UUID {boot}': 'BOOT-UUID'}}
print(out.get(a, ''))
""")
    btrfs_cmd = fake(tmp_path, "btrfs", """
if os.environ.get('FAKE_BTRFS_FAIL'):
    sys.exit(1)
if args[:2] == ['subvolume', 'snapshot']:
    os.makedirs(args[3])
""")
    conf = tmp_path / "etc/snapper/configs/root"
    conf.parent.mkdir(parents=True)
    conf.write_text("x")
    environ = {"ORION_SNAP_CMDLINE": str(tmp_path / "cmdline"), "ORION_SNAP_SNAPPER": snapper,
               "ORION_SNAP_FINDMNT": findmnt, "ORION_SNAP_BTRFS": btrfs_cmd,
               "ORION_SNAP_MOUNT": fake(tmp_path, "mount", ""), "ORION_SNAP_UMOUNT": fake(tmp_path, "umount", ""),
               "ORION_SNAP_UPDATE_GRUB": fake(tmp_path, "update-grub", ""),
               "ORION_SNAP_SNAPPER_CONF": str(conf), "ORION_SNAP_SNAPSHOTS_DIR": str(snaps),
               "ORION_SNAP_BOOT": str(boot), "ORION_SNAP_TOP": str(top),
               "ORION_SNAP_INDEX": str(tmp_path / "index.json"), "ORION_SNAP_CHANGES": str(tmp_path / "ch.jsonl"),
               "ORION_SNAP_CONF": str(tmp_path / "none.conf"), "ORION_SNAP_NOW": "1760000000"}
    return SimpleNamespace(tmp=tmp_path, top=top, env=lambda **kw: S.Env({**environ, **kw}), environ=environ)


def test_btrfs_list_take_and_limits(btrfs):
    env = btrfs.env()
    assert S.backend(env) == "btrfs"
    snaps = S.btrfs_list(env)
    assert [s["id"] for s in snaps] == ["5", "6", "7"]
    assert [s["kind"] for s in snaps] == ["nightly", "manual", "pre-update"]
    assert S.status(env)["undo_to"] == "7"
    res = S.take(env, "pre-update", "before installing or updating system packages")
    assert res["snapshot"]["id"] == "8"
    c = calls(btrfs.tmp)
    assert any(l.startswith("snapper --no-dbus -c root create --type single --cleanup-algorithm number "
                            "--description before installing or updating system packages --userdata "
                            "orion=pre-update --print-number") for l in c)
    assert "snapper --no-dbus -c root cleanup number" in c and "snapper --no-dbus -c root cleanup timeline" in c
    assert not any(l.startswith("update-grub") for l in c)        # pre-update: the update itself refreshes the menu
    S.take(env, "nightly", "nightly")
    assert any("--cleanup-algorithm timeline" in l for l in calls(btrfs.tmp))
    assert any(l.startswith("update-grub") for l in calls(btrfs.tmp))


def test_btrfs_boot_menu_entries(btrfs):
    out = S.grub_entries(btrfs.env())
    assert out.startswith('submenu "OrionOS snapshots')
    assert "rootflags=subvol=@snapshots/7/snapshot ro orion.snapshot=7" in out
    assert "linux /vmlinuz-6.8.0-47-generic root=UUID=ROOT-UUID" in out and "search --no-floppy --fs-uuid --set=root BOOT-UUID" in out
    assert "# snapshot 6: its kernel is no longer in /boot" in out
    assert out.index("orion.snapshot=7") < out.index("orion.snapshot=5")           # newest first


def test_btrfs_rollback_keeps_the_current_system(btrfs):
    res = S.restore(btrfs.env(), "7")
    assert "becomes the system at the next start" in res["message"]
    kept = [p.name for p in btrfs.top.iterdir() if p.name.startswith("@before-rollback-")]
    assert len(kept) == 1 and (btrfs.top / kept[0] / "marker").exists()
    assert (btrfs.top / "@").is_dir() and not (btrfs.top / "@/marker").exists()
    assert any("cleanup-algorithm number --description before going back to snapshot 7" in l for l in calls(btrfs.tmp))
    assert json.loads((btrfs.tmp / "ch.jsonl").read_text().splitlines()[-1])["kind"] == "restore"


def test_btrfs_rollback_failure_puts_everything_back(btrfs, monkeypatch):
    monkeypatch.setenv("FAKE_BTRFS_FAIL", "1")
    with pytest.raises(S.Failed, match="couldn't be prepared"):
        S.restore(btrfs.env(), "7")
    assert (btrfs.top / "@/marker").exists()
    assert not [p for p in btrfs.top.iterdir() if p.name.startswith("@before-rollback-")]


def test_btrfs_rollback_refuses_unknown_layouts(btrfs, monkeypatch):
    monkeypatch.setenv("FAKE_OPTS", "rw,relatime")
    with pytest.raises(S.Failed, match="isn't laid out"):
        S.restore(btrfs.env(), "7")
    assert (btrfs.top / "@/marker").exists()


def test_setup_btrfs_writes_snapper_settings(tmp_path):
    conf = tmp_path / "etc/snapper/configs/root"
    env = S.Env({"ORION_SNAP_SNAPPER_CONF": str(conf)})
    S.setup_btrfs(env)
    text = conf.read_text()
    assert 'TIMELINE_CREATE="no"' in text and 'TIMELINE_LIMIT_DAILY="7"' in text and 'NUMBER_LIMIT="12"' in text
    assert 'SNAPPER_CONFIGS="root"' in (tmp_path / "etc/default/snapper").read_text()


def test_cli_json_and_plain_words(btrfs, capsys, monkeypatch):
    for k, v in btrfs.environ.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(S.os, "geteuid", lambda: 0)
    assert S.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "btrfs"
    assert S.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "3 snapshots of the system" in out and "Before an update" in out
    assert S.main(["restore", "99", "--yes", "--json"]) == 1
    assert "no snapshot 99" in json.loads(capsys.readouterr().out)["message"]


# =====================================================================================  kill switch shell helpers
def test_freeze_helper_and_frozen_check(tmp_path):
    import subprocess
    os_safety = SCRIPT.parents[2] / "safety"
    env = {**os.environ, "ORION_FREEZE_TEST": "1", "ORION_FREEZE_DROPIN": str(tmp_path / "apt/99orion-freeze"),
           "ORION_FREEZE_MARK": str(tmp_path / "orion/frozen"), "HOME": str(tmp_path / "home"),
           "ORION_DATA_DIR": str(tmp_path / "data")}
    helper, frozen = str(os_safety / "orion-freeze-helper"), str(os_safety / "orion-frozen")
    assert subprocess.run(["sh", frozen], env=env).returncode == 1
    r = subprocess.run(["sh", helper, "on"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "paused" in r.stdout
    assert 'APT::Periodic::Unattended-Upgrade "0";' in (tmp_path / "apt/99orion-freeze").read_text()
    assert subprocess.run(["sh", frozen], env=env).returncode == 0
    assert subprocess.run(["sh", helper, "off"], env=env, capture_output=True).returncode == 0
    assert not (tmp_path / "apt/99orion-freeze").exists()
    assert subprocess.run(["sh", frozen], env=env).returncode == 1
    (tmp_path / "data").mkdir()
    (tmp_path / "data/frozen").write_text("x")                  # Orion's own marker counts too
    assert subprocess.run(["sh", frozen], env=env).returncode == 0
    assert subprocess.run(["sh", helper, "rm -rf /"], env=env, capture_output=True).returncode == 2
