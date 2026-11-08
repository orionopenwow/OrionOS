"""orion-sandbox (review H12): hands itself to orion-agent through sudo, checks its arguments, no -t without a tty."""
import os
import subprocess
from pathlib import Path

import pytest

SANDBOX = Path(__file__).resolve().parents[1] / "orion-sandbox"
SETUP = Path(__file__).resolve().parents[1] / "sandbox-setup.sh"


def fake_bin(tmp_path, user):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    log = tmp_path / "calls"
    log.unlink(missing_ok=True)
    (b / "id").write_text(f'#!/bin/sh\ncase "$1" in -un) echo {user} ;; -u) echo 4242 ;; *) echo 4242 ;; esac\n')
    for name in ("sudo", "podman"):
        (b / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
    for f in b.iterdir():
        f.chmod(0o755)
    return b, log


def run(tmp_path, user, args, cwd=None, extra_env=None):
    b, log = fake_bin(tmp_path, user)
    env = {"PATH": f"{b}:/usr/bin:/bin", "ORION_SANDBOX_WORKROOT": str(tmp_path / "work"), **(extra_env or {})}
    r = subprocess.run(["sh", str(SANDBOX), *args], cwd=cwd or tmp_path, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=20)
    return r, (log.read_text() if log.exists() else "")


def test_session_user_goes_through_sudo_as_orion_agent(tmp_path):
    r, calls = run(tmp_path, "orion", ["docker.io/library/node:20", "npm", "test"])
    assert r.returncode == 0, r.stderr
    assert calls.strip() == "sudo -n -u orion-agent -- /usr/local/bin/orion-sandbox docker.io/library/node:20 npm test"
    r, calls = run(tmp_path, "orion", ["img", "true"], extra_env={"ORION_SANDBOX_NET": "1"})
    assert "-- /usr/local/bin/orion-sandbox --net img true" in calls


def test_agent_side_runs_podman_without_a_tty_inside_the_work_folder(tmp_path):
    work = tmp_path / "work" / "heal" / "c1"
    work.mkdir(parents=True)
    r, calls = run(tmp_path, "orion-agent", ["python:3.12-slim", "sh", "-c", "pytest -q"], cwd=work)
    assert r.returncode == 0, r.stderr
    assert calls.startswith("podman run --rm --read-only")
    assert " -t" not in calls and " -it " not in calls
    assert f"-v {work.resolve()}:/work:O -w /work python:3.12-slim sh -c pytest -q" in calls
    assert "--network=none" in calls and "--cap-drop=ALL" in calls
    r, calls = run(tmp_path, "orion-agent", ["--net", "img", "true"], cwd=work)
    assert "--network=slirp4netns:allow_host_loopback=false" in calls


@pytest.mark.parametrize("image", ["--privileged", "img;rm", "a b", "$(x)"])
def test_agent_side_refuses_odd_image_names(tmp_path, image):
    work = tmp_path / "work" / "w"
    work.mkdir(parents=True)
    r, calls = run(tmp_path, "orion-agent", [image, "true"], cwd=work)
    assert r.returncode == 2 and calls == ""


def test_agent_side_refuses_folders_outside_the_shared_one(tmp_path):
    (tmp_path / "work").mkdir()
    for cwd in (tmp_path, tmp_path / "work"):
        r, calls = run(tmp_path, "orion-agent", ["img", "true"], cwd=cwd)
        assert r.returncode == 2 and "inside" in r.stderr and calls == ""


def test_setup_script_writes_a_narrow_sudoers_rule():
    text = SETUP.read_text()
    assert "NOPASSWD: /usr/local/bin/orion-sandbox\n" in text
    assert "visudo -cf" in text and "install -m 440" in text
    assert "install -d -m 2750" in text
    r = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.access(SANDBOX, os.X_OK)
