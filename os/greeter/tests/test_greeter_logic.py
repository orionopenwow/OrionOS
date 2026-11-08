"""Login screen: greetd config generation, the two buttons, the no-password start, the PAM helper, the page.
Run: python -m pytest -q os/greeter/tests"""
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
G = HERE.parent
REPO = G.parent.parent
sys.path.insert(0, str(G))
sys.path.insert(0, str(HERE))

import greetd_config as C  # noqa: E402
import greeter_logic as L  # noqa: E402
from greetd_ipc import GreetdClient  # noqa: E402
from test_greetd_ipc import FakeGreetd  # noqa: E402


# ------------------------------------------------------------------ greetd config
def test_auto_mode_config():
    cfg = tomllib.loads(C.render("auto"))
    assert cfg["default_session"]["command"] == C.GREETER_START   # the graphical screen, never bare agreety
    assert cfg["default_session"]["user"] == "_greetd"
    assert cfg["initial_session"]["user"] == "orion"
    assert cfg["initial_session"]["command"].endswith("/usr/local/bin/orion-session")
    assert cfg["terminal"]["vt"] == 1


def test_password_mode_has_no_autologin():
    cfg = tomllib.loads(C.render("password", user="sam"))
    assert "initial_session" not in cfg
    assert cfg["default_session"]["command"] == C.GREETER_START


def test_guest_wrapper_goes_in_front():
    cfg = tomllib.loads(C.render("auto", login_next=True))
    assert cfg["default_session"]["command"] == f"{C.LOGIN_NEXT} {C.GREETER_START}"


@pytest.mark.parametrize("bad", [("other", "orion"), ("auto", "bad user"), ("auto", ""), ("auto", 'x"y')])
def test_bad_input_refused(bad):
    with pytest.raises(ValueError):
        C.render(*bad)


def test_write_is_atomic_and_readable(tmp_path):
    p = tmp_path / "greetd" / "config.toml"
    assert C.main(["write", "password", "--path", str(p)]) == 0
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    assert "initial_session" not in tomllib.loads(p.read_text())
    assert C.main(["write", "nonsense"]) == 2


def test_shipped_templates_match_the_generator():
    """The static copies (lab greetd.toml, greetd-password.toml) say the same as the generator."""
    lab = tomllib.loads((REPO / "os/lab/greetd.toml").read_text())
    pw = tomllib.loads((G / "greetd-password.toml").read_text())
    assert lab == tomllib.loads(C.render("auto"))
    assert pw == tomllib.loads(C.render("password"))
    assert "agreety" not in (REPO / "os/lab/greetd.toml").read_text().split("[default_session]")[1]


def test_agreety_is_only_the_fallback():
    start = (G / "orion-greeter-start").read_text()
    assert "exec /usr/sbin/agreety" in start and "cage -s --" in start
    assert subprocess.run(["sh", "-n", str(G / "orion-greeter-start")]).returncode == 0
    assert subprocess.run(["sh", "-n", str(G / "orion-login-mode")]).returncode == 0
    assert "greetd_config.py" in (G / "orion-login-mode").read_text()


# ------------------------------------------------------------------ the two buttons
def test_session_commands(tmp_path):
    rescue = tmp_path / "orion-rescue-session"
    rescue.write_text("#!/bin/sh\n")
    conf = {"RESCUE_CMD": str(rescue)}
    assert L.session_cmd(conf, "orion") == [L.DEFAULT_SESSION]
    assert L.session_cmd(conf, "rescue") == [str(rescue)]
    assert L.session_cmd({"RESCUE_CMD": str(tmp_path / "missing")}, "rescue") == [L.DEFAULT_SESSION]
    assert L.session_cmd({"SESSION_CMD": "/x/s --flag"}, "nonsense") == ["/x/s", "--flag"]
    assert L.session_env("orion") == ["ORION_LOGIN_HANDOFF=blackhole"]
    assert "ORION_LOGIN_HANDOFF=blackhole" not in L.session_env("rescue")


def test_page_config(tmp_path):
    c = tmp_path / "login.conf"
    c.write_text("LOGIN_MODE=password\nREDUCED_MOTION=1\n# comment\nRESCUE_CMD=/nope\n")
    cfg = L.page_config(L.read_conf(str(c)), {"user": "orion", "name": "Orion", "avatar": ""})
    assert cfg["mode"] == "password" and cfg["reducedMotion"] is True and cfg["rescue"] is False
    assert L.page_config({}, {"user": "o"}, exists=lambda p: True)["mode"] == "auto"
    assert L.clean_target("rescue") == "rescue" and L.clean_target("../x") == "orion" and L.clean_target(5) == "orion"


# ------------------------------------------------------------------ no-password start (greetd_ipc)
def test_no_password_start_when_pam_lets_you_in():
    fake = FakeGreetd(lambda req, st: {"type": "success"})
    r = GreetdClient(fake.path).authenticate("orion", None)
    assert r.ok


def test_no_password_start_but_pam_asks():
    def h(req, st):
        if req["type"] == "create_session":
            return {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"}
        return {"type": "success"}
    fake = FakeGreetd(h)
    r = GreetdClient(fake.path).authenticate("orion", None)
    assert not r.ok and r.reason == "needs_password"
    assert fake.requests[-1]["type"] == "cancel_session"


# ------------------------------------------------------------------ PAM helper
def _auto_ok(tmp_path, conf_text, env):
    conf = tmp_path / "login.conf"
    conf.write_text(conf_text)
    src = (G / "orion-login-auto-ok").read_text().replace("CONF=/etc/orion/login.conf", f"CONF={conf}")
    src = src.replace("\"0 644\"", f"\"{os.getuid()} 644\"")
    conf.chmod(0o644)
    script = tmp_path / "auto-ok"
    script.write_text(src)
    base = {"PATH": "/usr/bin:/bin", "PAM_TYPE": "auth", "PAM_SERVICE": "greetd", "PAM_USER": "orion"}
    return subprocess.run(["sh", str(script)], env={**base, **env}).returncode


def test_pam_helper(tmp_path):
    assert _auto_ok(tmp_path, "LOGIN_MODE=auto\n", {}) == 0
    assert _auto_ok(tmp_path, "LOGIN_MODE=password\n", {}) == 1
    assert _auto_ok(tmp_path, "LOGIN_MODE=auto\n", {"PAM_USER": "orion-agent"}) == 1
    assert _auto_ok(tmp_path, "LOGIN_MODE=auto\nLOGIN_USER=sam\n", {"PAM_USER": "sam"}) == 0
    assert _auto_ok(tmp_path, "LOGIN_MODE=auto\n", {"PAM_SERVICE": "sudo"}) == 1
    assert _auto_ok(tmp_path, "LOGIN_MODE=auto\n", {"PAM_TYPE": "account"}) == 1


def test_pam_helper_refuses_a_conf_others_could_write(tmp_path):
    conf = tmp_path / "login.conf"
    conf.write_text("LOGIN_MODE=auto\n")
    conf.chmod(0o666)
    src = (G / "orion-login-auto-ok").read_text().replace("CONF=/etc/orion/login.conf", f"CONF={conf}")
    src = src.replace("\"0 644\"", f"\"{os.getuid()} 644\"")
    (tmp_path / "s").write_text(src)
    env = {"PATH": "/usr/bin:/bin", "PAM_TYPE": "auth", "PAM_SERVICE": "greetd", "PAM_USER": "orion"}
    assert subprocess.run(["sh", str(tmp_path / "s")], env=env).returncode == 1


# ------------------------------------------------------------------ the page
def test_page_has_the_two_buttons_and_parses():
    html = (G / "web/index.html").read_text()
    assert 'id="go-orion"' in html and "Start Orion again" in html
    assert 'id="go-rescue"' in html and "Rescue desktop" in html
    if shutil.which("node"):
        js = G / "web/greeter.js"
        r = subprocess.run(["node", "-e", f"new Function(require('fs').readFileSync({str(js)!r}, 'utf8'))"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    js = (G / "web/greeter.js").read_text()
    assert "nopass: true" in js and "needs_password" in js and "target" in js


def test_greeter_launcher_parses():
    import ast
    src = (G / "orion-greeter").read_text()
    ast.parse(src)
    assert "L.session_cmd(self.conf, self.target)" in src and 'msg.get("nopass") is True' in src
