"""Pocket Orion: the setup script (syntax and --dry-run) and the small server it installs."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SETUP = ROOT / "os/phone/orion-pocket-setup.sh"
POCKET = ROOT / "os/phone/pocket/orion_pocket.py"
DAEMON = ROOT / "apps/daemon"


def meminfo(tmp_path, mb):
    p = tmp_path / f"meminfo-{mb}"
    p.write_text(f"MemTotal:       {mb * 1024} kB\nMemFree:        1000 kB\n")
    return p


def dry(tmp_path, mb, *extra):
    home = tmp_path / "home"
    env = {**os.environ, "ORION_POCKET_MEMINFO": str(meminfo(tmp_path, mb)), "HOME": str(tmp_path / "userhome")}
    r = subprocess.run(["bash", str(SETUP), "--dry-run", "--home", str(home), *extra],
                       capture_output=True, text=True, env=env, timeout=60)
    return r, home


def test_setup_script_parses():
    assert subprocess.run(["bash", "-n", str(SETUP)], capture_output=True).returncode == 0


def test_dry_run_changes_nothing_and_lists_the_plan(tmp_path):
    r, home = dry(tmp_path, 8000)
    assert r.returncode == 0, r.stderr + r.stdout
    out = r.stdout
    assert "dry run" in out and "Nothing was changed" in out
    assert not home.exists() and not (tmp_path / "userhome").exists()
    for f in ("phone_pair.py", "boardroom.py", "video_scout.py"):
        assert f"daemon/{f}" in out
    assert "venv" in out and "faster-whisper" in out and "yt-dlp" in out
    assert "orion-pocket.service" in out and "127.0.0.1:9121" in out
    assert "ollama pull" in out          # 8 GB: room for a small local model


def test_small_phone_skips_the_model_and_voice(tmp_path):
    r, _ = dry(tmp_path, 2000)
    assert r.returncode == 0
    assert "ollama" not in r.stdout.split("Local model skipped")[1]
    assert "faster-whisper" not in r.stdout and "Voice skipped" in r.stdout


def test_options(tmp_path):
    r, _ = dry(tmp_path, 8000, "--no-model", "--no-scout", "--port", "9333")
    assert r.returncode == 0 and "ollama" not in r.stdout and "yt-dlp" not in r.stdout
    assert "127.0.0.1:9333" in r.stdout
    assert dry(tmp_path, 8000, "--port", "80")[0].returncode == 2
    assert dry(tmp_path, 8000, "--bogus")[0].returncode == 2
    assert dry(tmp_path, 8000, "--source", str(tmp_path))[0].returncode == 1   # no Orion files there


def test_pocket_server_serves_signed_phone_routes(tmp_path):
    """Runs the pocket server's app in a child process with the repo's daemon files as its 'copied subset'."""
    pytest.importorskip("fastapi")
    pytest.importorskip("cryptography")
    home = tmp_path / "pocket"
    home.mkdir()
    (home / "daemon").symlink_to(DAEMON)
    script = textwrap.dedent(f"""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("orion_pocket", {str(POCKET)!r})
        op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
        app, board = op.build_app()
        from fastapi.testclient import TestClient
        sys.path.insert(0, {str(DAEMON)!r})
        from tests.test_phones import FakePhone, paired
        c = TestClient(app)
        assert c.get("/pocket/hello").json()["orion"] == "pocket"
        assert c.get("/phone/home").status_code == 401
        phone = FakePhone(); paired(board, c, phone)
        h = c.get("/phone/home", headers=phone.headers(phone.read, "GET", "/phone/home")).json()
        assert h["pc"]["name"] == "Pocket Orion" and h["approvals"] == [] and h["agents"] == []
        body = json.dumps({{"app": "SMS", "sender": "Sam", "text": "thanks!"}}).encode()
        r = c.post("/phone/draft", content=body, headers=phone.headers(phone.read, "POST", "/phone/draft", body))
        assert r.status_code == 200 and r.json()["draft"], r.text
        body = json.dumps({{"items": [{{"app": "SMS", "sender": "x", "text": "y"}}]}}).encode()
        r = c.post("/phone/inbox", content=body, headers=phone.headers(phone.read, "POST", "/phone/inbox", body))
        assert r.status_code == 403
        print("ok")
    """)
    env = {**os.environ, "ORION_POCKET_HOME": str(home), "ORION_POCKET_OLLAMA": "http://127.0.0.1:9"}
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120,
                       cwd=str(tmp_path))
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr


def test_pocket_runs_from_only_the_copied_files_and_refuses_pc_work(tmp_path):
    """The setup script copies a short list of files: the pocket must start from exactly those, keep approvals,
    agents and missions on the PC, and answer chat and voice in its own words."""
    pytest.importorskip("fastapi")
    pytest.importorskip("cryptography")
    import re as _re
    files = _re.search(r'FILES="([^"]+)"', SETUP.read_text()).group(1).split()
    home = tmp_path / "pocket"
    (home / "daemon").mkdir(parents=True)
    for f in files:
        (home / "daemon" / f).write_bytes((DAEMON / f).read_bytes())
    script = textwrap.dedent(f"""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("orion_pocket", {str(POCKET)!r})
        op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
        app, board = op.build_app()
        import video_scout, injection_firewall, freeze   # Scout's lazy imports are there too
        from fastapi.testclient import TestClient
        sys.path.append({str(DAEMON)!r})
        from tests.test_phones import FakePhone, paired
        c = TestClient(app)
        phone = FakePhone(); paired(board, c, phone)
        def post(path, data, key=None):
            body = json.dumps(data).encode()
            return c.post(path, content=body, headers=phone.headers(key or phone.read, "POST", path, body))
        r = post("/phone/chat", {{"text": "hello"}})
        assert r.status_code == 200 and "Pocket Orion" in r.json()["reply"], r.text
        r = post("/phone/chat", {{"text": "Orion, have the PC build a landing page"}})
        assert "PC is off" in r.json()["reply"], r.text
        for path, key in (("/phone/verdict", phone.sign), ("/phone/card/do", phone.sign),
                          ("/phone/mission", phone.read), ("/phone/mission/m1/approve_review", phone.sign),
                          ("/phone/agent", phone.read), ("/phone/hotmic", phone.read), ("/phone/unfreeze", phone.sign)):
            r = post(path, {{"id": "x", "state": "off", "title": "t"}}, key)
            assert r.status_code == 403 and "only happen on your PC" in r.json()["error"], (path, r.text)
        r = c.get("/phone/pending", headers=phone.headers(phone.read, "GET", "/phone/pending"))
        assert r.status_code == 403
        assert "phone_missions" not in [m for m in sys.modules if sys.modules[m] is not None]
        try:
            import faster_whisper
        except ImportError:
            r = c.post("/phone/voice", content=b"audio", headers={{**phone.headers(phone.read, "POST", "/phone/voice", b"audio"),
                                                                  "Content-Type": "audio/mp4"}})
            assert r.status_code == 503 and "Pocket Orion" in r.json()["error"], r.text
        print("ok")
    """)
    env = {**os.environ, "ORION_POCKET_HOME": str(home), "ORION_POCKET_OLLAMA": "http://127.0.0.1:9"}
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=120,
                       cwd=str(tmp_path))
    assert r.returncode == 0 and "ok" in r.stdout, r.stdout + r.stderr
