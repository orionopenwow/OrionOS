"""Cheap checks of the USB-stick scripts that can't run here (they need real disks): orion-install-disk's order of
safety steps (review B5), the apt guard (H1) and orion-welcome's admin account on every path (H6)."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = ROOT / "os/image/files"
INSTALL = (FILES / "usr/local/bin/orion-install-disk").read_text()


def test_installer_finds_partitions_by_sector_and_checks_before_formatting():
    assert "sort -V | tail -n2" not in INSTALL                       # the old "highest numbers" rule is gone
    sel = INSTALL.index("partsel select")
    empty = INSTALL.index('blkid -p "$p"')
    first_format = min(INSTALL.index("mkfs.ext4 -q -F -L orion-boot"), INSTALL.index("luksFormat"))
    assert sel < empty < first_format
    assert INSTALL.index("esp_room \"$ESP\"") < INSTALL.index('sfdisk -q --append "$DISK"')
    assert INSTALL.index("partsel disk --size-bytes") < INSTALL.index('wipefs -aq "$DISK"')   # erase: size first


def test_installer_cleans_up_logs_and_makes_folders():
    assert "trap cleanup EXIT" in INSTALL and "cryptsetup close orion-root" in INSTALL
    assert 'mkdir -p "$T/etc/orion" "$T/etc/default/grub.d"' in INSTALL
    assert "orion-install-logs" in INSTALL and '"$T/var/log/orion-install-disk.log"' in INSTALL
    for tool in ("grub-install", "update-grub", "os-prober", "efibootmgr"):
        assert re.search(rf"for c in [^;]*\b{tool}\b", INSTALL, re.S), tool


def test_installer_leaves_nothing_of_the_stick_active():
    for gone in ("usr/local/bin/orion-slot", "orion-slot-good.service", "orion-welcome.service",
                 "01orion-slot-guard", "90-orion-usb.conf", "10orion_vault"):
        assert gone in INSTALL.split("Nothing of the stick's own start-up")[1].split("cat >")[0], gone


def test_installer_windows_first_by_default_and_bitlocker_explained():
    assert 'boot-order --efibootmgr "$W/efi.txt" --new "$NEWE" --first "$FIRST"' in INSTALL
    assert "aka.ms/myrecoverykey" in INSTALL and "--no-nvram" in INSTALL
    assert 'read -rp "1 or 2: " a; [ "$a" = 2 ] && FIRST=orionos || FIRST=windows' in INSTALL


def test_installer_swap_and_keymap():
    assert "btrfs filesystem mkswapfile" in INSTALL and "chattr +C" in INSTALL and "@swap" in INSTALL
    assert "resume_offset" in INSTALL and "KEYMAP=y" in INSTALL


def test_apt_guard_only_on_the_stick(tmp_path):
    guard = FILES / "usr/local/sbin/orion-apt-guard"
    stick, disk = tmp_path / "stick", tmp_path / "disk"
    stick.write_text("boot=casper persistent orion.slot=b\n")
    disk.write_text("root=UUID=x ro quiet\n")
    run = lambda cl, **e: subprocess.run(["sh", str(guard)], capture_output=True, text=True,
                                         env={**os.environ, "ORION_APT_GUARD_CMDLINE": str(cl), **e})
    r = run(stick)
    assert r.returncode == 1 and "whole-system updates" in r.stderr and "Get apps" in r.stderr
    assert run(disk).returncode == 0
    assert run(stick, ORION_INSTALLING="1").returncode == 0
    r = run(stick, ORION_APT_ON_STICK="1")
    assert r.returncode == 0 and "anyway" in r.stderr
    conf = (FILES / "etc/apt/apt.conf.d/01orion-slot-guard").read_text()
    assert 'DPkg::Pre-Invoke { "/usr/local/sbin/orion-apt-guard"; };' in conf
    for u in ("apt-daily", "apt-daily-upgrade", "unattended-upgrades"):
        assert "ConditionKernelCommandLine=!orion.slot" in (FILES / f"etc/systemd/system/{u}.service.d/60-orion-slot.conf").read_text()


def test_welcome_makes_the_admin_account_on_every_path():
    w = (FILES / "usr/local/bin/orion-welcome").read_text()
    body = w.split("clear\n", 1)[1]
    exits = [m.start() for m in re.finditer(r"exit 0", body)]
    assert body.count("ensure_admin") >= 5
    # every "exit 0" is either after ensure_admin on the same line/branch, or the reboot paths
    for line in body.splitlines():
        if "exit 0" in line and "reboot" not in line and "welcome-done" not in line:
            assert "ensure_admin" in line or line.strip() == "exit 0", line
    svc = (FILES / "etc/systemd/system/orion-welcome.service").read_text()
    assert "ConditionKernelCommandLine=persistent" not in svc            # "Try Orion" gets it too
