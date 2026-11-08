"""Boot counting on installed drives (feature 7): orion-bootcount with a fake grub-editenv, GRUB's own counting
snippet run through a shell, and orion-snapshot's fallback entry / rollback while started from a snapshot."""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import EDITENV, grubenv, make

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


BC = load(ROOT / "os/dualboot/orion-bootcount", "orion_bootcount_cli")
SNAP = load(ROOT / "os/lab/bin/orion-snapshot", "orion_snapshot_cli2")


# =====================================================================================  GRUB's side, run for real
def grub_snippet() -> str:
    out = subprocess.run(["sh", str(ROOT / "os/dualboot/01_orion_bootcount")], capture_output=True, text=True,
                         env={**os.environ, "PATH": f"{HERE / 'fakebin'}:{os.environ['PATH']}"}).stdout
    return out


@pytest.fixture(scope="module", autouse=True)
def fake_grub_probe():
    d = HERE / "fakebin"
    d.mkdir(exist_ok=True)
    p = d / "grub-probe"
    p.write_text("#!/bin/sh\ncase \"$1\" in --target=fs) echo ext2 ;; *) echo ;; esac\n")
    p.chmod(0o755)
    yield
    p.unlink()
    d.rmdir()


def start(state: dict, boot_once: str = "") -> tuple[dict, str]:
    """One computer start: GRUB's snippet with the saved notes; returns the new notes and the chosen default."""
    script = grub_snippet()
    assert "save_env" in script
    sh = re.sub(r"^(\s*)set (\w+)=(.*)$", r"\1\2=\3", script, flags=re.M)
    pre = "".join(f"{k}='{v}'\n" for k, v in state.items())
    post = ('save_env() { for k in "$@"; do eval "echo SAVE $k=\\$$k"; done; }\n')
    r = subprocess.run(["sh", "-c", post + pre + f"default=0\nboot_once='{boot_once}'\n" + sh + "\necho DEFAULT=$default"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = dict(state)
    for line in r.stdout.splitlines():
        if line.startswith("SAVE "):
            k, v = line[5:].split("=", 1)
            new[k] = v
    return new, r.stdout.strip().splitlines()[-1].split("=", 1)[1]


def test_two_starts_then_the_snapshot_from_before():
    s = {"orion_trial": "1.3.0", "orion_tries": "2", "orion_fallback": "12"}
    s, d = start(s)
    assert s["orion_tries"] == "1" and d == "0"
    s, d = start(s)
    assert s["orion_tries"] == "0" and d == "0"
    s, d = start(s)                       # never healthy twice: the earlier system starts by itself
    assert s["orion_failed"] == "1.3.0" and s["orion_trial"] == "" and d == "orion-fallback"
    s, d = start(s)                       # and keeps starting until you choose
    assert d == "orion-fallback"
    _, d = start(s, boot_once="true")     # except a one-time choice (Restart into Windows)
    assert d == "0"


def test_a_healthy_start_stops_the_counting():
    s, d = start({})
    assert d == "0" and s == {}


def test_snippet_is_off_where_grub_cant_write():
    d = HERE / "fakebin"
    (d / "grub-probe").write_text("#!/bin/sh\ncase \"$1\" in --target=fs) echo btrfs ;; *) echo ;; esac\n")
    out = grub_snippet()
    (d / "grub-probe").write_text("#!/bin/sh\ncase \"$1\" in --target=fs) echo ext2 ;; *) echo ;; esac\n")
    assert "save_env" not in out and "off" in out


# =====================================================================================  orion-bootcount
@pytest.fixture()
def bc(tmp_path):
    b = tmp_path / "bin"
    editenv = make(b, "grub-editenv", EDITENV)
    (tmp_path / "snapshots/12/snapshot").mkdir(parents=True)
    grubd = tmp_path / "01_orion_bootcount"
    grubd.write_text("x")
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/vmlinuz root=UUID=r ro quiet\n")
    environ = {"ORION_BC_GRUBENV": str(tmp_path / "grubenv"), "ORION_BC_EDITENV": str(editenv),
               "ORION_BC_UPDATE_GRUB": str(make(b, "update-grub", "")),
               "ORION_BC_HEALTHY": str(make(b, "orion-healthy", "sys.exit(0 if os.environ.get('FAKE_HEALTHY') == '1' else 1)\n")),
               "ORION_BC_SNAPSHOT": str(make(b, "orion-snapshot", "print('ok')\n")),
               "ORION_BC_NOTIFY": str(make(b, "notify-send", "")),
               "ORION_BC_CMDLINE": str(tmp_path / "cmdline"), "ORION_BC_SNAPSHOTS_DIR": str(tmp_path / "snapshots"),
               "ORION_BC_GRUBD": str(grubd), "ORION_BC_BOOT_FSTYPE": "ext4", "ORION_BC_AS_ROOT": "1",
               "ORION_BC_LOG": str(tmp_path / "bc.log")}

    class T:
        tmp = tmp_path
        env = staticmethod(lambda **kw: BC.Env({**environ, **kw}))

        @staticmethod
        def notes():
            return grubenv(tmp_path / "grubenv")

        @staticmethod
        def calls():
            f = tmp_path / "calls.txt"
            return f.read_text().splitlines() if f.exists() else []
    return T


def test_arm_good_and_status(bc, monkeypatch):
    res = BC.arm(bc.env(), "12", "1.3.0")
    assert res["armed"] and bc.notes() == {"orion_trial": "1.3.0", "orion_tries": "2", "orion_fallback": "12"}
    assert any(c.startswith("update-grub") for c in bc.calls())
    assert BC.status(bc.env())["state"] == "trial"
    res = BC.good(bc.env(), 0)
    assert not res["kept"] and "isn't up yet" in res["message"]
    monkeypatch.setenv("FAKE_HEALTHY", "1")
    res = BC.good(bc.env(), 5)
    assert res["kept"] and "orion_trial" not in bc.notes()
    assert any(c.startswith("orion-healthy --wait 5 --stable 60") for c in bc.calls())


def test_arm_explains_why_it_cant(bc):
    assert "no snapshot" in BC.arm(bc.env(), "99", "1.3.0")["message"]
    assert "isn't a separate plain partition" in BC.arm(bc.env(ORION_BC_BOOT_FSTYPE="btrfs"), "12", "1")["message"]
    assert bc.notes() == {}


def test_fell_back_explained_kept_or_tried_again(bc, capsys):
    (bc.tmp / "grubenv").write_text("orion_failed=1.3.0\norion_fallback=12\norion_trial=\n")
    (bc.tmp / "cmdline").write_text("root=UUID=r rootflags=subvol=@snapshots/12/snapshot ro orion.snapshot=12 orion.fallback=1\n")
    st = BC.status(bc.env())
    assert st["state"] == "fell-back" and st["on_fallback_boot"] and "didn't reach the Orion screen" in st["words"]
    assert BC.good(bc.env(), 0)["kept"] is False                 # a fallback start never confirms anything
    assert BC.notify(bc.env())["shown"] and any(c.startswith("notify-send -u critical") for c in bc.calls())
    res = BC.try_again(bc.env())
    assert "try the update to 1.3.0 again" in res["message"]
    assert bc.notes()["orion_trial"] == "1.3.0" and bc.notes()["orion_tries"] == "2" and "orion_failed" not in bc.notes()
    (bc.tmp / "grubenv").write_text("orion_failed=1.3.0\norion_fallback=12\n")
    res = BC.keep_earlier(bc.env())
    assert "Restart now" in res["message"] and bc.notes() == {}
    assert "orion-snapshot restore 12 --yes" in bc.calls()


def test_commands_need_admin(bc, capsys):
    if os.geteuid() != 0:
        assert BC.main(["arm", "--snapshot", "12"], bc.env(ORION_BC_AS_ROOT="0")) == 2
    assert BC.main(["status", "--json"], bc.env()) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "idle"


# =====================================================================================  orion-snapshot's part
@pytest.fixture()
def snapenv(tmp_path):
    b = tmp_path / "bin"
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "vmlinuz-6.8.0-47-generic").write_text("")
    (boot / "initrd.img-6.8.0-47-generic").write_text("")
    snaps = tmp_path / "snapshots"
    (snaps / "12/snapshot/usr/lib/modules/6.8.0-47-generic").mkdir(parents=True)
    listing = {"root": [{"number": 12, "type": "single", "date": "2026-10-16 09:00:00",
                         "description": "before updating Orion to 1.3.0", "userdata": {"orion": "pre-update"}}]}
    top = tmp_path / "top"
    (top / "@").mkdir(parents=True)
    (top / "@/marker").write_text("updated system\n")
    (top / "@snapshots/12/snapshot").mkdir(parents=True)
    conf = tmp_path / "snapper-root"
    conf.write_text("x")
    (tmp_path / "grubenv").write_text("orion_fallback=12\n")
    environ = {"ORION_SNAP_CMDLINE": str(tmp_path / "cmdline"),
               "ORION_SNAP_SNAPPER": str(make(b, "snapper", f"import json\nprint(json.dumps({listing!r}) if 'list' in sys.argv else '13')\n")),
               "ORION_SNAP_FINDMNT": str(make(b, "findmnt", f"""
a = ' '.join(sys.argv[1:])
out = {{'-no FSTYPE /': os.environ.get('FAKE_ROOT_FS', 'btrfs'), '-no OPTIONS /': 'rw,subvol=/@',
       '-no SOURCE /': '/dev/mapper/orion-root[/@]', '-no UUID /': 'ROOT-UUID', '-no UUID {boot}': 'BOOT-UUID',
       '-no SOURCE {snaps}': '/dev/mapper/orion-root[/@snapshots]', '-no UUID {snaps}': 'ROOT-UUID'}}
print(out.get(a, ''))
""")),
               "ORION_SNAP_BTRFS": str(make(b, "btrfs", "os.makedirs(sys.argv[4]) if sys.argv[2:3] == ['snapshot'] else None\n")),
               "ORION_SNAP_MOUNT": str(make(b, "mount", "")), "ORION_SNAP_UMOUNT": str(make(b, "umount", "")),
               "ORION_SNAP_UPDATE_GRUB": str(make(b, "update-grub", "")),
               "ORION_SNAP_SNAPPER_CONF": str(conf), "ORION_SNAP_SNAPSHOTS_DIR": str(snaps),
               "ORION_SNAP_BOOT": str(boot), "ORION_SNAP_TOP": str(top),
               "ORION_SNAP_INDEX": str(tmp_path / "index.json"), "ORION_SNAP_CHANGES": str(tmp_path / "ch.jsonl"),
               "ORION_SNAP_CONF": str(tmp_path / "none.conf"), "ORION_SNAP_NOW": "1760000000",
               "ORION_SNAP_GRUBENV": str(tmp_path / "grubenv"),
               "ORION_SNAP_GRUB_EDITENV": str(make(b, "grub-editenv", EDITENV))}
    (tmp_path / "cmdline").write_text("root=UUID=ROOT-UUID ro quiet\n")
    return type("S", (), {"tmp": tmp_path, "top": top, "env": staticmethod(lambda: SNAP.Env(environ))})


def test_fallback_entry_has_a_fixed_id_and_comes_before_the_submenu(snapenv):
    out = SNAP.grub_entries(snapenv.env())
    assert out.startswith('menuentry "OrionOS, as it was before the last update" --class orionos --id orion-fallback {')
    assert "rootflags=subvol=@snapshots/12/snapshot ro orion.snapshot=12 orion.fallback=1" in out
    assert out.index("orion-fallback") < out.index("submenu")


def test_keep_earlier_works_while_started_from_the_snapshot(snapenv, monkeypatch):
    (snapenv.tmp / "cmdline").write_text("rootflags=subvol=@snapshots/12/snapshot ro orion.snapshot=12 orion.fallback=1\n")
    monkeypatch.setenv("FAKE_ROOT_FS", "overlay")
    env = snapenv.env()
    assert SNAP.backend(env) == "btrfs"
    res = SNAP.restore(env, "12")
    assert "becomes the system at the next start" in res["message"]
    kept = [p.name for p in snapenv.top.iterdir() if p.name.startswith("@before-rollback-")]
    assert len(kept) == 1 and (snapenv.top / kept[0] / "marker").exists() and (snapenv.top / "@").is_dir()
