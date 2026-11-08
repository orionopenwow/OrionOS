"""orion-voice-setup (first-run "Choose Orion's voice", also Settings > Orion's voice) against a stand-in Orion brain
on 127.0.0.1: samples play, the pick + speed + warmth are saved, a designed voice can be used, cloned voices need a
yes to the consent line, and skipping marks the step done. The Voicebox recipe follows Voicebox's own steps and
binds to 127.0.0.1 only."""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

OS = Path(__file__).resolve().parents[2]
SCRIPT = OS / "lab/bin/orion-voice-setup"
pytestmark = pytest.mark.skipif(not (shutil.which("jq") and shutil.which("curl")), reason="needs jq and curl")


def tiny_wav() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 240)
    return base64.b64encode(buf.getvalue()).decode()


class Brain:
    def __init__(self, voicebox: bool):
        self.posts: list[tuple[str, dict]] = []
        engines = {
            "active": {"engine": "kokoro", "voice": "af_nicole", "speed": 0.92, "warmth": 0, "delivery": "",
                       "chosen": False},
            "chosen": False,
            "engines": [
                {"id": "kokoro", "available": True, "voices": [{"id": "af_nicole", "name": "Nicole"},
                                                               {"id": "am_adam", "name": "Adam"}]},
                {"id": "voicebox", "available": voicebox,
                 "voices": [{"id": "p-clone", "name": "My voice", "kind": "cloned"}] if voicebox else []},
            ],
            "voicebox": {"reachable": voicebox},
        }
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                assert self.headers.get("authorization", "").startswith("Bearer ")
                self._send(engines)

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                me.posts.append((self.path, body))
                if self.path == "/voice/preview":
                    return self._send({"ok": True, "engine": body.get("engine", "kokoro"), "note": "",
                                       "audio_base64": tiny_wav()})
                if self.path == "/voice/design":
                    return self._send({"ok": True, "voice": {"id": "p-new"}, "message": "Made a new voice."})
                return self._send({"ok": True, "message": "Saved."})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.api = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def run(brain: Brain, answers: list[str], tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    played = tmp_path / "played"
    (bin_dir / "pw-play").write_text(f"#!/bin/sh\necho x >> {played}\n")
    (bin_dir / "pw-play").chmod(0o755)
    (bin_dir / "systemctl").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/systemctl\n")
    (bin_dir / "systemctl").chmod(0o755)
    tok = tmp_path / "daemon.token"
    tok.write_text("test-token")
    path = f"{bin_dir}:/usr/bin:/bin"
    env = {"PATH": path, "HOME": str(tmp_path), "ORION_API": brain.api, "ORION_TOKEN_FILE": str(tok)}
    return subprocess.run(["bash", str(SCRIPT), *args], input="\n".join(answers) + "\n", text=True,
                          capture_output=True, env=env, timeout=60)


def saved(brain: Brain) -> dict:
    return [b for p, b in brain.posts if p == "/voice/set"][-1]


def test_pick_a_kokoro_voice_with_speed_and_warmth(tmp_path):
    b = Brain(voicebox=False)
    try:
        # now? yes / play samples? yes / pick 2 / faster / soft and warm / try: Enter / install Voicebox? no
        r = run(b, ["", "", "2", "f", "2", "", "n"], tmp_path)
    finally:
        b.close()
    assert r.returncode == 0, r.stderr
    s = saved(b)
    assert s["engine"] == "kokoro" and s["voice"] == "am_adam" and s["speed"] == 1.1 and s["warmth"] == 0.7
    assert s["chosen"] is True
    assert len((tmp_path / "played").read_text().split()) == 3      # two samples + the try-it sentence
    assert "Voicebox" in r.stdout and not (tmp_path / "systemctl").exists()


def test_install_voicebox_uses_the_recipe(tmp_path):
    b = Brain(voicebox=False)
    try:
        r = run(b, ["", "n", "", "", "", "", "y"], tmp_path)
    finally:
        b.close()
    assert r.returncode == 0
    assert (tmp_path / "systemctl").read_text().strip() == "start orion-recipe@voicebox.service"


def test_design_a_voice_with_voicebox(tmp_path):
    b = Brain(voicebox=True)
    try:
        # now / no samples / keep / normal / as recorded / describe / use it / delivery / try: Enter
        r = run(b, ["", "n", "", "", "", "a warm, low, friendly voice", "", "calm and unhurried", ""], tmp_path)
    finally:
        b.close()
    assert r.returncode == 0, r.stderr
    design = [b2 for p, b2 in b.posts if p == "/voice/design"][0]
    assert design["description"] == "a warm, low, friendly voice" and design["use"] is False
    s = saved(b)
    assert s["engine"] == "voicebox" and s["voice"] == "p-new" and s["delivery"] == "calm and unhurried"


def test_cloned_voice_needs_consent(tmp_path):
    b = Brain(voicebox=True)
    try:
        # pick 3 (the cloned Voicebox voice), skip design, no delivery, then say no to the consent question
        r = run(b, ["", "n", "3", "", "", "", "", "n", ""], tmp_path)
    finally:
        b.close()
    assert "Only use your own voice or a voice you have permission to use." in r.stdout
    s = saved(b)
    assert s["engine"] == "kokoro" and s["consent"] is False


def test_skip_marks_the_step_done(tmp_path):
    b = Brain(voicebox=False)
    try:
        r = run(b, ["n"], tmp_path)
        r2 = run(b, [], tmp_path, "--skip")
    finally:
        b.close()
    assert r.returncode == 0 and r2.returncode == 0
    assert [p for p, _ in b.posts] == ["/voice/set", "/voice/set"] and saved(b) == {"chosen": True}


def test_only_this_computer(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ORION_API": "http://192.168.1.9:9120"}
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 1 and "this computer" in r.stdout


def test_firstrun_and_settings_offer_the_step():
    assert "orion-voice-setup" in (OS / "lab/bin/orion-firstrun").read_text()
    assert '"orion-voice-setup"' in (OS / "settings/orion_settings/pages.py").read_text()
    assert os.access(SCRIPT, os.X_OK) and b"\r\n" not in SCRIPT.read_bytes()


def test_voicebox_recipe_is_local_and_from_github_and_pypi():
    t = (OS / "lab/recipes/voicebox.sh").read_text()
    assert "git clone -q --depth 1 https://github.com/jamiepine/voicebox.git" in t
    assert "--host 127.0.0.1 --port 17493" in t and "0.0.0.0" not in t
    assert subprocess.run(["sh", "-n", str(OS / "lab/recipes/voicebox.sh")]).returncode == 0
    assert b"\r\n" not in (OS / "lab/recipes/voicebox.sh").read_bytes()
