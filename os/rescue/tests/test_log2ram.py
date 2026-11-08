"""USB stick logs in RAM (orion-log2ram) and how they agree with the saved logs (orion-journal-vault, H15).
python -m pytest -q os/rescue/tests"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RESCUE = Path(__file__).resolve().parents[1]
OS = RESCUE.parent
SCRIPT = RESCUE / "orion-log2ram"


def fake_tools(bindir: Path) -> None:
    """mount --bind copies, a tmpfs mount empties the folder (a fresh RAM disk), umount/journalctl do nothing."""
    bindir.mkdir()
    (bindir / "mount").write_text("""#!/bin/sh
if [ "$1" = --bind ]; then cp -a "$2/." "$3/"; exit 0; fi
for last; do :; done
find "$last" -mindepth 1 -delete; echo "$*" >> "$FAKE_LOG"; exit 0
""")
    for n in ("umount", "journalctl"):
        (bindir / n).write_text("#!/bin/sh\necho \"$0 $*\" >> \"$FAKE_LOG\"\nexit 0\n")
    for f in bindir.iterdir():
        f.chmod(0o755)


def env_for(tmp_path, vault=True):
    bindir = tmp_path / "bin"
    if not bindir.exists():
        fake_tools(bindir)
    vdev = tmp_path / "orionvault"
    if vault:
        vdev.write_text("")
    return dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin", ORION_LOGDIR=str(tmp_path / "log"),
                ORION_LOG_HDD=str(tmp_path / "hdd"), ORION_LOG2RAM_MARK=str(tmp_path / "mark"),
                ORION_VAULT_DEV=str(vdev), ORION_LOG2RAM_CONF=str(tmp_path / "none.conf"),
                FAKE_LOG=str(tmp_path / "calls"))


def run(env, *args):
    return subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_script_parses():
    assert subprocess.run(["sh", "-n", str(SCRIPT)]).returncode == 0
    assert os.access(SCRIPT, os.X_OK)


def test_start_copies_saved_logs_into_ram_and_sync_copies_back(tmp_path):
    env = env_for(tmp_path)
    log = tmp_path / "log"
    (log / "journal").mkdir(parents=True)
    (log / "journal" / "old.journal").write_text("last boot")
    r = run(env, "start")
    assert r.returncode == 0 and "logs in RAM" in r.stdout
    assert (log / "journal" / "old.journal").read_text() == "last boot"      # last boot still readable
    assert "size=128M" in (tmp_path / "calls").read_text()
    assert (tmp_path / "mark").read_text().strip() == "128M"
    (log / "journal" / "new.journal").write_text("this boot")
    assert run(env, "sync").returncode == 0
    assert (tmp_path / "hdd" / "journal" / "new.journal").read_text() == "this boot"
    assert "Logs are in RAM" in run(env, "status").stdout
    (log / "syslog").write_text("at shutdown")
    assert run(env, "stop").returncode == 0
    assert (tmp_path / "hdd" / "syslog").read_text() == "at shutdown" and not (tmp_path / "mark").exists()
    assert "umount -l" in (tmp_path / "calls").read_text()


def test_nothing_happens_with_the_vault_locked(tmp_path):
    env = env_for(tmp_path, vault=False)
    r = run(env, "start")
    assert r.returncode == 0 and "vault locked" in r.stdout and not (tmp_path / "mark").exists()
    assert run(env, "sync").returncode == 0 and not (tmp_path / "hdd").exists()
    assert "locked" in run(env, "status").stdout


def test_journal_vault_agrees_with_ram_logs():
    """H15 keeps logs; with log2ram the journal cap must fit the RAM disk, and log2ram must run first."""
    jv = (RESCUE / "orion-journal-vault").read_text()
    assert "/run/orion-log2ram" in jv and "JOURNAL_MAX:-48M" in jv and "Storage=persistent" in jv
    unit = (RESCUE / "orion-log2ram.service").read_text()
    assert "Before=orion-journal-vault.service" in unit and "ConditionPathExists=/dev/mapper/orionvault" in unit
    assert "ExecStop=/opt/orion/rescue/orion-log2ram stop" in unit


def test_installed_for_the_usb_image_only():
    sh = (OS / "image/os-rescue.sh").read_text()
    block = sh[sh.index('if [ "$PROFILE" = image ]; then'):]
    assert "orion-log2ram.service" in block and "orion-log2ram-sync.timer" in block
    assert 'install -m 755 "$OS/rescue/orion-log2ram"' in sh


@pytest.mark.parametrize("unit", ["orion-log2ram.service", "orion-log2ram-sync.service", "orion-log2ram-sync.timer"])
def test_units_verify(tmp_path, unit):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze not available")
    for u in ("orion-log2ram.service", "orion-log2ram-sync.service", "orion-log2ram-sync.timer"):
        (tmp_path / u).write_text((RESCUE / u).read_text())
    r = subprocess.run(["systemd-analyze", "verify", "--man=no", str(tmp_path / unit)], capture_output=True, text=True)
    bad = [ln for ln in (r.stderr + r.stdout).splitlines()
           if ln.strip() and "is not executable" not in ln and "system bus" not in ln and "No such file" not in ln
           and "orion-journal-vault" not in ln]
    assert not bad, bad
