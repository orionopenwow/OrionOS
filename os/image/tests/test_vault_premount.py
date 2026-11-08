"""10orion_vault (casper-premount) with fake commands: what it tells casper through /conf/param.conf (review H4), and
the one-time clean-up of stale package layers when a new system slot starts (review H1)."""
import os
import subprocess
from pathlib import Path

import pytest

from fakes import make, path_with

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "os/image/files/usr/share/initramfs-tools/scripts/casper-premount/10orion_vault"
CASPER = Path("/var/tmp/orion-image/rootfs/usr/share/initramfs-tools/scripts")


@pytest.fixture()
def boot(tmp_path):
    b = tmp_path / "bin"
    mapper = tmp_path / "mapper-orionvault"
    make(b, "blkid", """
a = ' '.join(sys.argv[1:])
if 'LABEL=OrionVault' in a and os.environ.get('FAKE_VAULT') == '1':
    print('/dev/sdz3')
elif a == '-L ORIONSYS':
    print('/dev/sdz2')
""")
    make(b, "plymouth", f"""
if sys.argv[1:2] == ['ask-for-password']:
    if os.environ.get('FAKE_UNLOCK') == '1':
        open({str(mapper)!r}, 'w').close()
        sys.exit(0)
    sys.exit(1)
""")
    for n in ("udevadm", "modprobe", "mount", "umount", "sleep", "cryptsetup"):
        make(b, n, "")
    vault = tmp_path / "vault"
    sysm = tmp_path / "sys"
    (sysm / "slots/a").mkdir(parents=True)
    (sysm / "slots/a/VERSION").write_text("1.4.0\n")
    (tmp_path / "cmdline").write_text("boot=casper live-media-path=/slots/a persistent orion.slot=a quiet\n")
    env = {**os.environ, "PATH": path_with(b), "ORION_VAULT_PARAM_CONF": str(tmp_path / "param.conf"),
           "ORION_VAULT_CMDLINE": str(tmp_path / "cmdline"), "ORION_VAULT_MAPPER": str(mapper),
           "ORION_VAULT_MNT": str(vault), "ORION_VAULT_SYSMNT": str(sysm)}

    class B:
        tmp, v = tmp_path, vault

        @staticmethod
        def run(**extra):
            r = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env={**env, **extra}, timeout=30)
            p = tmp_path / "param.conf"
            return r, (p.read_text() if p.exists() else "")
    return B


def test_try_orion_keeps_nothing_and_adopts_nothing(boot):
    (boot.tmp / "cmdline").write_text("boot=casper orion.slot=a orion.try=1\n")
    r, param = boot.run()
    assert r.returncode == 0 and 'NOPERSISTENT="Yes"' in param and 'PERSISTENT=""' in param


def test_no_vault_yet_means_no_persistence(boot):
    r, param = boot.run()
    assert 'NOPERSISTENT="Yes"' in param and "root_persistence" not in param


def test_locked_vault_never_falls_back_to_another_writable(boot):
    r, param = boot.run(FAKE_VAULT="1", FAKE_UNLOCK="0")
    assert r.returncode == 0
    assert 'NOPERSISTENT="Yes"' in param and "root_persistence" not in param


def test_open_vault_is_the_only_persistence(boot):
    r, param = boot.run(FAKE_VAULT="1", FAKE_UNLOCK="1")
    assert r.returncode == 0
    assert 'root_persistence="../../mapper/orionvault"' in param
    assert 'home_persistence="../../null"' in param and "snapshot_label=" in param
    assert "NOPERSISTENT" not in param


def test_param_conf_is_valid_shell_that_casper_reads_back(boot):
    _, param = boot.run(FAKE_VAULT="1", FAKE_UNLOCK="1")
    out = subprocess.run(["sh", "-c", f". {boot.tmp / 'param.conf'}; echo $root_persistence $home_persistence"],
                         capture_output=True, text=True).stdout.split()
    assert out == ["../../mapper/orionvault", "../../null"]


def test_new_slot_clears_stale_package_layers_once(boot):
    u = boot.v / "upper"
    for d in ("usr/lib/python3/dist-packages/old", "usr/local/bin", "var/lib/dpkg/info", "var/lib/flatpak/app",
              "home/orion", "etc/NetworkManager", "opt/orion/daemon"):
        (u / d).mkdir(parents=True)
    (u / "usr/local/bin/mine").write_text("kept\n")
    (u / "home/orion/notes.txt").write_text("kept\n")
    r, param = boot.run(FAKE_VAULT="1", FAKE_UNLOCK="1")
    assert r.returncode == 0, r.stderr
    assert not (u / "usr/lib").exists() and not (u / "var/lib/dpkg").exists() and not (u / "opt/orion").exists()
    assert (u / "usr/local/bin/mine").exists() and (u / "home/orion/notes.txt").exists()
    assert (u / "var/lib/flatpak/app").is_dir() and (u / "etc/NetworkManager").is_dir()
    assert (boot.v / "orion/slot-seen").read_text().strip() == "a:1.4.0"
    # the same slot version again: nothing is cleared
    (u / "usr/lib/later").mkdir(parents=True)
    boot.run(FAKE_VAULT="1", FAKE_UNLOCK="1")
    assert (u / "usr/lib/later").is_dir()


@pytest.mark.skipif(not (CASPER / "casper-helpers").exists(), reason="the image's casper scripts aren't here")
def test_casper_really_reads_these_names():
    """Checked against the casper in the built image (read-only): the variables exist and are read lazily, and every
    premount script is followed by a read of /conf/param.conf."""
    helpers = (CASPER / "casper-helpers").read_text()
    casper = (CASPER / "casper").read_text()
    assert "root_persistence=\n" in helpers and 'if [ -z "$root_persistence" ]' in helpers
    assert 'if [ -e "/dev/disk/by-label/${pers_label}" ]; then' in helpers
    for v in ('home_persistence="home-rw"', 'root_snapshot_label="casper-sn"', 'home_snapshot_label="home-sn"'):
        assert v in casper
    assert 'if [ -n "$parentdev" ] && [ -z "$NOPERSISTENT" ]; then' in casper     # no partition of its own
    hooks = Path("/var/tmp/orion-image/rootfs/usr/share/initramfs-tools/hook-functions").read_text()
    assert '[ -e /conf/param.conf ] && . /conf/param.conf' in hooks
