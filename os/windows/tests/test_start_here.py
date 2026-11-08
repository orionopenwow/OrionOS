"""Start Here.cmd / Start-Here.ps1: parses as PowerShell, keeps CRLF and ASCII (Windows PowerShell 5.1 reads a file
without a BOM as the old Windows code page), and its -DryRun walks every step against a stand-in Orion, saves its
progress, resumes and restarts. Runs with pwsh (PowerShell 7) on Linux; skipped where pwsh isn't installed."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_orion  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PS1 = REPO / "os" / "windows" / "Start-Here.ps1"
CMD = REPO / "Start Here.cmd"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh isn't installed")


def test_line_endings_and_ascii():
    for f in (PS1, CMD):
        raw = f.read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), f"{f.name} must be CRLF"
        assert all(b < 128 for b in raw), f"{f.name} must be plain ASCII"


def test_cmd_runs_the_wizard():
    text = CMD.read_text()
    assert "os\\windows\\Start-Here.ps1" in text and "-ExecutionPolicy Bypass" in text and "%*" in text


def test_no_powershell7_only_syntax():
    code = "\n".join(l for l in PS1.read_text().splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.S)
    for pat in (r"\?\?", r"\?\.", r"\s&&\s", r"\s\|\|\s", r"\)\s*\?\s*[^:]+\s:\s", r"ForEach-Object\s+-Parallel"):
        assert not re.search(pat, code), pat


def test_never_types_yes_for_licences_and_never_approves():
    for f in (PS1, REPO / "apps" / "mobile" / "build-apk.ps1"):
        t = f.read_text()
        assert '"y`n"' not in t and "| & $sdkm" not in t and "--accept" not in t, f.name
    t = PS1.read_text()
    assert "/confirm" not in t and "/verdict" not in t and "/result" not in t   # nothing approved or ticked for you


@needs_pwsh
def test_parses_with_powershell_parser():
    ps = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$t,[ref]$e);"
          "$e.Count" % PS1)
    out = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "0", out.stdout + out.stderr


# ===================================================================================== dry runs
def fake_bin(tmp: Path) -> Path:
    """A PATH with only the tools Start Orion needs (no git, no Claude Code, no winget, no adb)."""
    b = tmp / "bin"
    b.mkdir()
    for name, ver in (("node", "v22.11.0"), ("python3", "Python 3.12.7"), ("cargo", "cargo 1.82.0"), ("npm", "10.9.0")):
        p = b / name
        p.write_text(f"#!/bin/sh\necho '{ver}'\n")
        p.chmod(0o755)
    return b


def run_wizard(tmp: Path, port: int, answers: str, *extra: str, fake: bool = True) -> str:
    env = {k: v for k, v in os.environ.items()
           if k not in ("JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "LOCALAPPDATA", "WT_SESSION", "ORION_DATA_DIR")}
    env.update(PATH=str(tmp / "bin"), HOME=str(tmp / "home"), ORION_TOKEN_FILE=str(tmp / "daemon.token"),
               ORION_START_HERE_ANSWERS=answers, ORION_START_HERE_WAIT_CAP="5", NO_COLOR="1")
    env.pop("ORION_START_HERE_FAKE_DAEMON", None)
    if fake:
        env["ORION_START_HERE_FAKE_DAEMON"] = "1"
    r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(PS1), "-DryRun",
                        "-Api", f"http://127.0.0.1:{port}", "-StateFile", str(tmp / "state.json"), *extra],
                       capture_output=True, text=True, timeout=240, env=env, stdin=subprocess.DEVNULL)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "Something went wrong" not in out, out
    return out


def state(tmp: Path) -> dict:
    return json.loads((tmp / "state.json").read_text())["steps"]


@pytest.fixture
def world(tmp_path):
    (tmp_path / "home").mkdir()
    fake_bin(tmp_path)
    token = "fake-" + "token-" + "for-tests"
    (tmp_path / "daemon.token").write_text(token)
    srv, story = fake_orion.serve(token)
    yield tmp_path, srv.server_address[1], story, token
    srv.shutdown()


@needs_pwsh
def test_dry_run_full_flow_then_resume_then_restart(world):
    tmp, port, story, token = world
    # first run: stop at the Claude Code sign-in question (Q keeps the progress)
    out = run_wizard(tmp, port, "y|q")
    assert "Stopped. Your progress is saved" in out
    assert "No update waiting" in out and "Git isn't installed" in out
    assert "would run: npm install -g @anthropic-ai/claude-code" in out
    st = state(tmp)
    assert st["update"] == "done" and st["save"] == "skipped" and "tools" not in st

    # second run: welcome back, carries on from step 3 and walks to the end
    out = run_wizard(tmp, port, "|y|y||y|y||")
    assert "Welcome back" in out
    assert "would open a new window for: signing in to Claude Code" in out
    assert "Codex not found" in out and "Gemini CLI not found" in out
    assert "Orion is running and answers with its key" in out
    assert "would run: winget install --id Microsoft.OpenJDK.17 -e" in out
    assert "would run: sdkmanager --licenses   (you read and accept them)" in out
    assert "would run: apps/mobile/build-apk.ps1 -NoInstall" in out or "build-apk.ps1 -NoInstall" in out
    assert "http://192.168.1.20:9121/phone/app.apk" in out
    assert "\u2588" in out and "\u2580" in out                       # the QR code was drawn
    assert "Your phone downloaded the app" in out
    assert "The Phone panel is open in the Orion window" in out and "Your phone is paired" in out
    assert "Your phone has checked itself" in out
    assert "would copy the test report" in out
    assert "Skipped. Any time: double-click Try OrionOS VM.cmd" in out
    assert "All done for now" in out
    st = state(tmp)
    assert [st[k] for k in ("tools", "start", "app", "pair", "phonechecks", "rest", "vm")] == \
        ["done", "done", "done", "done", "done", "done", "skipped"]
    seen = [(m, p) for m, p, _ in story.log]
    for want in (("POST", "/phones/app-share"), ("POST", "/phones/app-share/close"), ("POST", "/hud/open"),
                 ("POST", "/tests/open"), ("GET", "/tests/report")):
        assert want in seen, want
    assert all(k == "key" for m, p, k in story.log if p not in ("/health", "/__log"))   # always sent Orion's key
    assert token not in out                                                         # and never printed it

    # third run: finished steps are ticked and skipped without questions
    out = run_wizard(tmp, port, "|n")
    assert "Saved to GitHub earlier" not in out
    assert "Orion Mobile was put on your phone earlier" in out and "Your phone is paired" in out
    assert "would run: npm install" not in out          # Claude Code still missing: asked again, answered no
    assert "Phone checks" not in out and "Waiting for your phone" not in out
    # R starts over: the state is cleared before the steps run again
    out = run_wizard(tmp, port, "r|q")
    st = state(tmp)
    assert "app" not in st and st["update"] == "done"


@needs_pwsh
def test_dry_run_without_the_fake_flag_sends_nothing(world):
    tmp, port, story, token = world
    out = run_wizard(tmp, port, "n|n|n|n", fake=False)
    assert "would ask Orion: POST /hud/open" in out
    assert "would ask Orion: POST /tests/open" in out
    assert not [x for x in story.log if x[0] != "GET"], story.log
    assert not (tmp / "home" / ".local" / "share" / "Orion" / "android-sdk").exists()


@needs_pwsh
def test_restart_switch(world):
    tmp, port, story, token = world
    run_wizard(tmp, port, "q")
    assert state(tmp)["update"] == "done"
    out = run_wizard(tmp, port, "q", "-Restart")
    assert "Welcome back" not in out


def test_programs_run_attached_so_their_messages_and_questions_show():
    """The first version captured child programs' output (`$code = RunPs ...`, `& exe` inside captured steps), which
    hid the update step's messages on the owner's PC. Every child program must go through StartAttached."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "Start-Here.ps1").read_text(encoding="utf-8")
    src = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"=\s*Run(Ps|Exe)\b", src), "RunPs/RunExe must not be captured; read $script:LastCode"
    direct = [l.strip() for l in src.splitlines() if re.match(r"\s*(\$\w+\s*=\s*)?&\s", l)]
    assert direct == ['$res = & ("Step-" + $s.id)'], direct
    assert "-NoNewWindow -Wait -PassThru" in src
