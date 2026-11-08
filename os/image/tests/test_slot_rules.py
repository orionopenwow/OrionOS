"""orion-slot's safety rules (review H2/H3) with fake commands: never write over the only slot that works, never
install an older version by accident, and only keep a slot once Orion is healthy."""
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from fakes import EDITENV, calls, grubenv, make, path_with

ROOT = Path(__file__).resolve().parents[3]
SLOT = ROOT / "os/image/files/usr/local/bin/orion-slot"


@pytest.fixture()
def stick(tmp_path):
    b = tmp_path / "bin"
    make(b, "grub-editenv", EDITENV)
    make(b, "minisign", "sys.exit(0 if os.environ.get('FAKE_SIG', 'ok') == 'ok' else 1)\n")
    make(b, "orion-healthy", "sys.exit(0 if os.environ.get('FAKE_HEALTHY') == '1' else 1)\n")
    make(b, "logger", "")
    sysd = tmp_path / "sys"
    for s, v in (("a", "1.2.0"), ("b", "1.1.0")):
        (sysd / "slots" / s).mkdir(parents=True)
        (sysd / "slots" / s / "VERSION").write_text(v + "\n")
    efi = tmp_path / "efi"
    (efi / "EFI/orion").mkdir(parents=True)
    (efi / "EFI/orion/grubenv").write_text("slot=a\n")
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/slots/a/vmlinuz boot=casper persistent orion.slot=a quiet\n")
    (tmp_path / "release.pub").write_text("untrusted comment: test\nRWTEST\n")
    env = {**os.environ, "PATH": path_with(b), "ORION_SLOT_TEST": "1", "ORION_SLOT_SYS": str(sysd),
           "ORION_SLOT_EFI": str(efi), "ORION_SLOT_CMDLINE": str(tmp_path / "cmdline"),
           "ORION_SLOT_PUB": str(tmp_path / "release.pub"), "ORION_SLOT_CONF": str(tmp_path / "none.conf"),
           "TMPDIR": str(tmp_path)}

    class S:
        tmp, bin, sys, envfile = tmp_path, b, sysd, efi / "EFI/orion/grubenv"

        @staticmethod
        def run(*args, **extra):
            return subprocess.run(["bash", str(SLOT), *args], capture_output=True, text=True,
                                  env={**env, **extra}, timeout=60)

        @staticmethod
        def release(version):
            src = tmp_path / f"rel-{version}"
            src.mkdir()
            for f in ("vmlinuz", "initrd", "filesystem.squashfs"):
                (src / f).write_text(f"{f} {version}\n")
            (src / "VERSION").write_text(version + "\n")
            t = tmp_path / f"slot-{version}.tar"
            with tarfile.open(t, "w") as tar:
                for f in ("vmlinuz", "initrd", "filesystem.squashfs", "VERSION"):
                    tar.add(src / f, arcname=f)
            (tmp_path / f"slot-{version}.tar.minisig").write_text("sig\n")
            return t
    return S


def test_a_newer_signed_release_goes_into_the_other_slot(stick):
    r = stick.run("apply", str(stick.release("1.3.0")))
    assert r.returncode == 0, r.stderr
    assert (stick.sys / "slots/b/VERSION").read_text().strip() == "1.3.0"
    assert grubenv(stick.envfile) == {"slot": "a", "next": "b", "tries": "1"}


def test_bad_signature_changes_nothing(stick):
    r = stick.run("apply", str(stick.release("1.3.0")), FAKE_SIG="bad")
    assert r.returncode != 0 and "Signature check FAILED" in r.stderr
    assert (stick.sys / "slots/b/VERSION").read_text().strip() == "1.1.0"


def test_refuses_while_an_update_waits_to_be_tried(stick):
    stick.envfile.write_text("slot=a\nnext=b\ntries=1\n")
    r = stick.run("apply", str(stick.release("1.3.0")))
    assert r.returncode != 0 and "already waiting to be tried" in r.stderr
    assert (stick.sys / "slots/b/VERSION").read_text().strip() == "1.1.0"


def test_refuses_on_an_unconfirmed_slot(stick):
    # running b (just tried, not yet confirmed): a is the last version that worked and must not be overwritten
    (stick.tmp / "cmdline").write_text("boot=casper persistent orion.slot=b\n")
    stick.envfile.write_text("slot=a\n")
    r = stick.run("apply", str(stick.release("1.3.0")))
    assert r.returncode != 0 and "hasn't been confirmed yet" in r.stderr
    assert (stick.sys / "slots/a/VERSION").read_text().strip() == "1.2.0"
    r = stick.run("update")
    assert r.returncode != 0 and "hasn't been confirmed yet" in r.stderr


def test_refuses_older_or_same_versions_unless_asked(stick):
    for v in ("1.2.0", "1.1.9", "0.9.0"):
        r = stick.run("apply", str(stick.release(v)))
        assert r.returncode != 0 and "isn't newer than the running 1.2.0" in r.stderr, v
    assert (stick.sys / "slots/b/VERSION").read_text().strip() == "1.1.0"
    r = stick.run("apply", str(stick.tmp / "slot-1.1.9.tar"), "--allow-downgrade")
    assert r.returncode == 0, r.stderr
    assert (stick.sys / "slots/b/VERSION").read_text().strip() == "1.1.9"


def test_unpacked_release_needs_a_signed_version_file(stick):
    src = stick.tmp / "dir"
    src.mkdir()
    for f in ("vmlinuz", "initrd", "filesystem.squashfs"):
        (src / f).write_text("x\n")
        (src / f"{f}.minisig").write_text("sig\n")
    (src / "VERSION").write_text("9.9.9\n")
    r = stick.run("apply", str(src))
    assert r.returncode != 0 and "VERSION.minisig" in r.stderr


def test_mark_good_needs_a_healthy_orion(stick):
    (stick.tmp / "cmdline").write_text("boot=casper persistent orion.slot=b\n")
    stick.envfile.write_text("slot=a\nnext=b\ntries=0\n")
    r = stick.run("mark-good")
    assert r.returncode != 0 and "isn't healthy" in r.stderr
    assert grubenv(stick.envfile)["slot"] == "a"
    r = stick.run("mark-good", "--wait", "5", FAKE_HEALTHY="1")
    assert r.returncode == 0, r.stderr
    assert grubenv(stick.envfile) == {"slot": "b"}
    assert any(c.startswith("orion-healthy --wait 5 --stable 60") for c in calls(stick.bin))
