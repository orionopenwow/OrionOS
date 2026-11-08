"""orion-boardroom-vm's first-boot seed (no VM is made), and syntax checks for the from-Windows shell scripts.

    python3 -m pytest os/lab/tests -q
"""
from __future__ import annotations

import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "os/lab"


def test_seed_matches_the_windows_one(tmp_path):
    out = tmp_path / "seed"
    r = subprocess.run([str(LAB / "bin/orion-boardroom-vm"), "seed-preview", str(out)], capture_output=True, text=True,
                       env=dict(os.environ, ORION_SRC=str(REPO)), timeout=60)
    assert r.returncode == 0, r.stderr
    ud = (out / "user-data").read_text()
    assert ud.startswith("#cloud-config\n") and "\r" not in ud
    assert not re.search(r"__[A-Z_]+__", ud.split("\n", 3)[3])   # every placeholder filled in (past the comment)
    assert "expire: true" in ud                            # the one-time password must be changed at first login
    lines = ud.splitlines()
    i = next(n for n, l in enumerate(lines) if "os/boardroom/provision.sh" in l)
    assert lines[i + 1] == "  - [bash, /opt/orion-src/orion-kvm/serial-console.sh]"
    net = (out / "network-config").read_text()
    assert "10.77.0.11/24" in net and "via: 10.77.0.1" in net
    assert "local-hostname: orion-boardroom" in (out / "meta-data").read_text()
    with tarfile.open(out / "orion-bundle.tar.gz") as t:
        names = set(t.getnames())
    for want in ("./apps/boardroom/orion_boardroom.py", "./os/boardroom/provision.sh", "./os/common/harden.sh",
                 "./orion-kvm/serial-console.sh"):
        assert want in names
    assert not any("daemon" in n for n in names)           # the boardroom gets only its own part of Orion


@pytest.mark.parametrize("script", ["bin/orion-boardroom-vm", "bin/orion-wow-client", "bin/orion-firstrun"])
def test_bash_syntax(script):
    assert subprocess.run(["bash", "-n", str(LAB / script)]).returncode == 0


@pytest.mark.parametrize("script", ["recipes/windows-apps.sh", "recipes/virtual-machines.sh", "bin/orion-settings"])
def test_sh_syntax(script):
    assert subprocess.run(["sh", "-n", str(LAB / script)]).returncode == 0


def test_new_scripts_are_lf_and_executable():
    for p in ("bin/orion-move-in", "bin/orion-boardroom-vm", "bin/orion-wow-client"):
        f = LAB / p
        assert b"\r" not in f.read_bytes() and os.access(f, os.X_OK), p


def test_wow_client_help_and_no_setup(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "cfg"))
    r = subprocess.run([str(LAB / "bin/orion-wow-client"), "help"], capture_output=True, text=True, env=env)
    assert "orion-wow-client setup" in r.stdout and "never ships" in r.stdout
    r = subprocess.run([str(LAB / "bin/orion-wow-client"), "status"], capture_output=True, text=True, env=env)
    assert "Not set up yet" in r.stdout
    r = subprocess.run([str(LAB / "bin/orion-wow-client"), "setup"], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "No WoW client folder" in r.stderr
