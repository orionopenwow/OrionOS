"""B7 screen-reading exclusions + indicator, H15 module guard (/health/full), H9 phone approval token for unfreeze."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import capture_privacy as CP
import freeze
import module_guard


# ------------------------------------------------------------------ B7: what screen reading never looks at
@pytest.mark.parametrize("title,app_id,why", [
    ("Bitwarden", "", "a password manager"),
    ("Vault — KeePassXC", "", "a password manager"),
    ("", "org.keepassxc.KeePassXC", "a password manager"),
    ("1Password", "", "a password manager"),
    ("Accounts — Chase Online Banking — Mozilla Firefox", "firefox", "a banking or payment page"),
    ("PayPal: Summary", "", "a banking or payment page"),
    ("New Tab — Mozilla Firefox Private Browsing", "firefox", "a private browser window"),
    ("Google - Chromium (Incognito)", "", "a private browser window"),
    ("Sign in to GitHub", "", "a login or password prompt"),
    ("Enter password", "", "a login or password prompt"),
    ("", "swaylock", "the lock screen"),
])
def test_sensitive_windows_are_skipped(title, app_id, why):
    assert CP.why_skip_window(title, app_id) == why


@pytest.mark.parametrize("title", ["main.py — Visual Studio Code", "Inbox (3) — Thunderbird", "Orion", ""])
def test_ordinary_windows_are_read(title):
    assert CP.why_skip_window(title, "code") == ""


def test_own_words(tmp_path):
    (tmp_path / ".config/orion").mkdir(parents=True)
    (tmp_path / ".config/orion/capture-exclude.txt").write_text("# mine\nPayroll\n\nDiary\n")
    words = CP.own_words({"ORION_CAPTURE_EXCLUDE": "Health portal, "}, home=tmp_path)
    assert words == ["health portal", "payroll", "diary"]
    assert CP.why_skip_window("Q3 Payroll.xlsx — LibreOffice", "", words) == "a window on your own list"


def test_skip_reason_checks_every_visible_window_and_the_lock():
    wins = [{"title": "Editor", "app_id": "code"}, {"title": "Bitwarden", "app_id": "Bitwarden"}]
    assert CP.skip_reason("Editor", windows=wins, is_locked=False, words=[]) == "a password manager"
    assert CP.skip_reason("Editor", windows=[], is_locked=True, words=[]) == "the lock screen"
    assert CP.skip_reason("Editor", windows=[], is_locked=False, words=[]) == ""


def test_lock_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    proc = tmp_path / "proc"
    proc.mkdir()
    assert CP.locked(proc) is False
    (tmp_path / "orion-locked").write_text("")
    assert CP.locked(proc) is True


def test_lock_seen_from_proc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    p = tmp_path / "proc" / "4242"
    p.mkdir(parents=True)
    (p / "comm").write_text("swaylock\n")
    assert CP.locked(tmp_path / "proc") is True


def test_indicator_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    CP.indicate(True)
    assert (tmp_path / "orion-capture-on").exists()
    CP.indicate(False)
    assert not (tmp_path / "orion-capture-on").exists()


def test_capture_is_opt_in_by_default(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv("ORION_CAPTURE_ENABLED", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)  # the default, not a local .env's choice
    try:
        importlib.reload(config)
        assert config.Settings().capture_enabled is False
    finally:
        monkeypatch.setenv("ORION_CAPTURE_ENABLED", "false")
        importlib.reload(config)


def test_capture_engine_skips_before_grabbing(monkeypatch, tmp_path):
    import capture_engine as ce
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(ce, "configure_tesseract", lambda: False)
    eng = ce.PerceptualCaptureEngine(on_delta=lambda d: None)
    grabbed = []
    monkeypatch.setattr(eng, "_grab", lambda sct: grabbed.append(1))
    monkeypatch.setattr(ce, "active_window_title", lambda: "Bitwarden")
    monkeypatch.setattr(ce.capture_privacy, "visible_windows", lambda: [])
    monkeypatch.setattr(ce.capture_privacy, "locked", lambda: False)
    eng._tick(None)
    assert grabbed == [] and eng.last_skip_reason == "a password manager"
    assert eng.snapshot()["frames_skipped"] == 1


def test_capture_indicator_follows_pause(monkeypatch, tmp_path):
    import capture_engine as ce
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(ce, "configure_tesseract", lambda: False)
    eng = ce.PerceptualCaptureEngine(on_delta=lambda d: None)
    seen = []

    def tick(sct):
        seen.append((tmp_path / "orion-capture-on").exists())
        eng.stop()
    monkeypatch.setattr(eng, "_tick", tick)
    eng._run_loop(None)
    assert seen == [True] and not (tmp_path / "orion-capture-on").exists()   # removed when it stops


# ------------------------------------------------------------------ H15: one failing module switches only itself off
@pytest.fixture
def guard():
    module_guard._reset_for_tests()
    yield module_guard
    module_guard._reset_for_tests()


def test_failed_import_is_reported(guard, tmp_path, monkeypatch):
    (tmp_path / "broken_mod_for_test.py").write_text("raise RuntimeError('boom at import')\n")
    (tmp_path / "fine_mod_for_test.py").write_text("def make_router():\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert guard.load("broken_mod_for_test") is None
    fine = guard.load("fine_mod_for_test")
    assert fine is not None
    app = SimpleNamespace(include_router=lambda r: None)
    assert guard.router(app, "broken_mod_for_test", lambda: 1 / 0) is False   # skipped: already off
    assert guard.router(app, "fine_mod_for_test", fine.make_router) is True
    rep = guard.report()
    assert not rep["ok"] and rep["modules_off"][0]["module"] == "broken_mod_for_test"
    assert "boom at import" in rep["modules_off"][0]["error"] and "Everything else works" in rep["text"]
    assert "fine_mod_for_test" in rep["modules_ok"]


def test_failed_start_and_routes(guard):
    app = SimpleNamespace(include_router=lambda r: (_ for _ in ()).throw(ValueError("bad router")))
    assert guard.router(app, "routes_mod", lambda: object()) is False
    assert guard.start("starter", lambda: 1 / 0) is None
    assert guard.start("starter", lambda: "never runs again") is None

    async def bad():
        raise OSError("port in use")
    assert asyncio.run(guard.astart("async_mod", bad)) is None
    assert asyncio.run(guard.astart("ok_async", lambda: asyncio.sleep(0, result=7))) == 7
    names = [f["module"] for f in guard.failed()]
    assert names == ["routes_mod", "starter", "async_mod"]
    asyncio.run(guard.astop("starter", lambda: 1 / 0))       # a module that never started isn't stopped


def test_health_full_route(guard):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    guard.start("flaky", lambda: 1 / 0)
    app = FastAPI()
    daemon = SimpleNamespace(agents={"system_capturer": False},
                             capture=SimpleNamespace(is_alive=lambda: True, paused=True, last_skip_reason=""))
    app.include_router(guard.make_router(lambda: daemon))
    body = TestClient(app).get("/health/full").json()
    assert body["status"] == "degraded" and body["modules"]["modules_off"][0]["module"] == "flaky"
    assert body["screen_reading"]["on"] is False and body["agents"] == {"system_capturer": False}


def test_main_uses_the_guard():
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert "import module_guard" in src and 'G.load("secret_shield")' in src and "G.make_router" in src
    assert 'G.start("capture_engine", _capture)' in src


# ------------------------------------------------------------------ H9: unfreeze passes the phone's signed request
def test_unfreeze_passes_the_approval_token(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    monkeypatch.setattr(freeze, "HELPER", str(helper))
    monkeypatch.setattr(freeze.shutil, "which", lambda n: "/usr/bin/pkexec")
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    freeze._system_pause(False, fake_run, "/run/user/1000/orion-approvals/t.json")
    assert calls[-1] == ["pkexec", str(helper), "off", "--approval", "/run/user/1000/orion-approvals/t.json"]
    freeze._system_pause(False, fake_run)
    assert calls[-1] == ["pkexec", str(helper), "off"]          # no token: polkit asks for the password
    freeze._system_pause(True, fake_run, "/x")
    assert calls[-1] == ["pkexec", str(helper), "on"]           # freezing never needs one


def test_save_approval_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    class Req:
        method = "POST"
        url = SimpleNamespace(path="/phone/unfreeze")
        headers = {"x-orion-device": "phone-abc123", "x-orion-ts": "1700000000000", "x-orion-sig": "c2ln"}

        async def body(self):
            return b"{}"
    p = asyncio.run(freeze.save_approval_token(Req(), {"device_id": "phone-abc123"}))
    tok = json.loads(Path(p).read_text())
    assert tok == {"device": "phone-abc123", "method": "POST", "path": "/phone/unfreeze", "ts": "1700000000000",
                   "sig": "c2ln", "body_b64": "e30="}
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_boardroom_prints_the_sign_key_for_pinning(tmp_path, monkeypatch, capsys):
    import boardroom as br
    board = br.Boardroom(tmp_path)
    board.store.put("phones", json.dumps({"phone-abc123": {"name": "p", "read": "r", "sign": "SIGNKEY", "fp": "f",
                                                            "added": 0, "last_ts": 0}}))
    monkeypatch.setattr(br, "Boardroom", lambda d: board)
    assert br._admin(["boardroom.py", "phone-signkey", "phone-abc123"]) == 0
    assert capsys.readouterr().out.strip() == "SIGNKEY"
    assert br._admin(["boardroom.py", "phone-signkey", "nope"]) == 1
