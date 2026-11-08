"""Living next to Windows (H14, feature 3): firmware entries, boot order for the installer, GRUB's Windows entry,
"Restart into Windows" with fake efibootmgr/grub-reboot/systemctl, Windows partitions, the root helper's and the
polkit rule's limits, and orion-healthy."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import orion_dualboot as D
from fakes import calls, make, path_with

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

EFI = """BootCurrent: 0003
Timeout: 0 seconds
BootOrder: 0000,0003,2001,2002
Boot0000* Windows Boot Manager\tHD(1,GPT,1f2e,0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)RC
Boot0003* OrionOS\tHD(1,GPT,1f2e,0x800,0x32000)/File(\\EFI\\orionos\\shimx64.efi)
Boot0004  OrionOS Old\tHD(1,GPT,1f2e,0x800,0x32000)/File(\\EFI\\old\\shimx64.efi)
Boot2001* EFI USB Device\tRC
Boot2002* EFI DVD/CDROM\tRC
"""
GRUBCFG = """menuentry 'OrionOS' --class orionos $menuentry_id_option 'gnulinux-simple-abc' {
}
submenu 'Advanced options for OrionOS' $menuentry_id_option 'gnulinux-advanced-abc' {
}
menuentry 'Windows Boot Manager (on /dev/nvme0n1p1)' --class windows --class os $menuentry_id_option 'osprober-efi-1F2E-3D4C' {
	insmod part_gpt
}
"""


def test_parse_firmware_entries():
    info = D.parse_efibootmgr(EFI)
    assert info["order"] == ["0000", "0003", "2001", "2002"] and info["current"] == "0003"
    assert info["entries"]["0000"]["label"] == "Windows Boot Manager"
    assert D.windows_entry(info) == "0000"
    assert D.entries_named(info, "OrionOS") == ["0003"]            # not "OrionOS Old"


def test_boot_order_keeps_windows_first_by_default():
    order = ["0000", "2001", "2002", "0003"]
    assert D.boot_order(order, "0003", "windows", "0000") == ["0000", "0003", "2001", "2002"]
    assert D.boot_order(order, "0003", "orionos", "0000") == ["0003", "0000", "2001", "2002"]
    assert D.boot_order(["2001"], "0005", "windows", "") == ["0005", "2001"]        # no Windows: OrionOS first


def test_grub_windows_entry():
    assert D.grub_windows_entry(GRUBCFG) == "osprober-efi-1F2E-3D4C"
    assert D.grub_windows_entry("menuentry 'OrionOS' {\n}\n") == ""


def test_windows_partitions():
    lsblk = {"blockdevices": [{"path": "/dev/nvme0n1", "type": "disk", "children": [
        {"path": "/dev/nvme0n1p1", "type": "part", "fstype": "vfat", "size": 104857600},
        {"path": "/dev/nvme0n1p3", "type": "part", "fstype": "BitLocker", "size": 250 * 1024 ** 3, "label": None},
        {"path": "/dev/nvme0n1p4", "type": "part", "fstype": "ntfs", "size": 900 * 1024 ** 2,
         "parttype": "de94bba4-06d1-4d40-a16a-bfd50179d6ac"},
        {"path": "/dev/nvme0n1p5", "type": "part", "fstype": "ntfs", "size": 100 * 1024 ** 3, "label": "Data"}]}]}
    parts = D.windows_parts(json.dumps(lsblk))
    assert [(p["path"], p["bitlocker"]) for p in parts] == [("/dev/nvme0n1p3", True), ("/dev/nvme0n1p5", False)]


@pytest.fixture()
def pc(tmp_path):
    b = tmp_path / "bin"
    (tmp_path / "efi.txt").write_text(EFI)
    (tmp_path / "grub.cfg").write_text(GRUBCFG)
    (tmp_path / "firmware").mkdir()
    (tmp_path / "cmdline").write_text("root=UUID=x ro quiet\n")
    environ = {"ORION_DB_EFIBOOTMGR": str(make(b, "efibootmgr", f"""
if not args:
    print(open({str(tmp_path / 'efi.txt')!r}).read())
sys.exit(int(os.environ.get('FAKE_RC', '0')))
""")), "ORION_DB_GRUB_REBOOT": str(make(b, "grub-reboot", "")), "ORION_DB_SYSTEMCTL": str(make(b, "systemctl", "")),
        "ORION_DB_GRUBCFG": str(tmp_path / "grub.cfg"), "ORION_DB_CMDLINE": str(tmp_path / "cmdline"),
        "ORION_DB_EFIVARS": str(tmp_path / "firmware")}
    return type("P", (), {"tmp": tmp_path, "bin": b, "env": staticmethod(lambda **kw: D.Env({**environ, **kw}))})


def test_restart_into_windows_prefers_the_firmware(pc):
    res = D.restart_windows(pc.env())
    assert res["ok"] and res["method"] == "bootnext"
    c = calls(pc.bin)
    assert "efibootmgr -q -n 0000" in c and "systemctl reboot" in c and not any("grub-reboot" in l for l in c)


def test_restart_into_windows_falls_back_to_grub(pc):
    (pc.tmp / "efi.txt").write_text("BootOrder: 0003\nBoot0003* OrionOS\tHD()\n")
    res = D.restart_windows(pc.env())
    assert res["ok"] and res["method"] == "grub"
    assert "grub-reboot osprober-efi-1F2E-3D4C" in calls(pc.bin)
    assert "BitLocker" in D.status(pc.env())["words"]


def test_no_windows_no_restart(pc):
    (pc.tmp / "efi.txt").write_text("BootOrder: 0003\nBoot0003* OrionOS\tHD()\n")
    (pc.tmp / "grub.cfg").write_text("menuentry 'OrionOS' {\n}\n")
    res = D.restart_windows(pc.env())
    assert not res["ok"] and "No Windows" in res["message"]
    assert "systemctl reboot" not in calls(pc.bin)


def test_failure_to_set_windows_doesnt_restart(pc):
    env = pc.env()
    env.efibootmgr = str(make(pc.bin, "efibootmgr2", f"""
if not args:
    print(open({str(pc.tmp / 'efi.txt')!r}).read()); sys.exit(0)
sys.exit(1)
"""))
    res = D.restart_windows(env)
    assert not res["ok"] and "Couldn't set Windows" in res["message"]
    assert "systemctl reboot" not in calls(pc.bin)


def test_installer_boot_order_command(pc, capsys):
    assert D.main(["boot-order", "--efibootmgr", str(pc.tmp / "efi.txt"), "--new", "0003", "--first", "windows"]) == 0
    assert capsys.readouterr().out.strip() == "0000,0003,2001,2002"
    assert D.main(["entries", "--efibootmgr", str(pc.tmp / "efi.txt"), "--label", "OrionOS"]) == 0
    assert capsys.readouterr().out.strip() == "0003"


def test_helper_accepts_only_restart_windows():
    h = ROOT / "os/dualboot/orion-dualboot-helper"
    for bad in (["anything"], ["restart-windows", "--dry-run"], []):
        r = subprocess.run(["sh", str(h), *bad], capture_output=True, text=True)
        assert r.returncode == 2, bad


@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed")
def test_polkit_rule_allows_only_the_person_at_the_computer(tmp_path):
    js = tmp_path / "t.js"
    js.write_text("""
const fs = require("fs"); let rule;
const polkit = { Result: { YES: "yes", NO: "no", NOT_HANDLED: null }, addRule: f => { rule = f; } };
eval(fs.readFileSync(process.argv[2], "utf8"));
const act = (id, cl) => ({ id, lookup: k => (k === "command_line" ? cl : undefined) });
const me = { user: "orion", local: true, active: true };
const H = "/opt/orion/dualboot/orion-dualboot-helper";
const cases = [
  [act("ai.orion.dualboot.restart-windows", H + " restart-windows"), me, "yes"],
  [act("ai.orion.dualboot.restart-windows", H + " restart-windows extra"), me, null],
  [act("ai.orion.dualboot.restart-windows", H + " restart-windows"), { ...me, active: false }, null],
  [act("ai.orion.dualboot.restart-windows", H + " restart-windows"), { ...me, local: false }, null],
  [act("ai.orion.dualboot.restart-windows", H + " restart-windows"), { ...me, user: "orion-agent" }, null],
  [act("org.freedesktop.udisks2.encrypted-unlock-system", ""), me, null],
];
let bad = 0;
for (const [a, s, want] of cases) { const got = rule(a, s) ?? null; if (got !== want) { bad++; console.log("FAIL", JSON.stringify([a.id, s, got, want])); } }
process.exit(bad ? 1 : 0);
""")
    r = subprocess.run(["node", str(js), str(ROOT / "os/dualboot/polkit/51-orion-dualboot.rules")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# =====================================================================================  orion-healthy
@pytest.fixture()
def health(tmp_path):
    b = tmp_path / "bin"
    make(b, "curl", "print(os.environ.get('FAKE_CODE', '200'), end='')\n")
    make(b, "pgrep", "print('4242') if os.environ.get('FAKE_HUD') == '1' else sys.exit(1)\n")
    make(b, "ps", "print('  ' + os.environ.get('FAKE_AGE', '300'))\n")
    make(b, "sleep", "")
    env = {**os.environ, "PATH": path_with(b)}
    return lambda *a, **kw: subprocess.run(["sh", str(ROOT / "os/lab/bin/orion-healthy"), *a], capture_output=True,
                                           text=True, env={**env, **kw}, timeout=30)


def test_healthy_needs_brain_and_screen(health):
    assert health(FAKE_HUD="1").returncode == 0
    r = health(FAKE_HUD="0")
    assert r.returncode == 1 and "HUD" in r.stdout
    r = health(FAKE_HUD="1", FAKE_CODE="000")
    assert r.returncode == 1 and "doesn't answer" in r.stdout
    assert health(FAKE_HUD="1", FAKE_CODE="401").returncode == 0            # running, just wants its token
    assert health(FAKE_HUD="1", FAKE_AGE="5").returncode == 1              # restarting, not up
    assert health("--daemon-only", FAKE_HUD="0").returncode == 0
    assert health("--wait", "10", "--stable", "60", FAKE_HUD="1").returncode == 0
