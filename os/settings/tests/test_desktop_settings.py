"""Settings: admin jobs through pkexec instead of a terminal (admin.py, H5), and the data behind "Keyboard & windows"
and "This PC's hardware" (desktop_settings.py)."""
from __future__ import annotations

import json
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from orion_settings import admin as A  # noqa: E402
from orion_settings import desktop_settings as D  # noqa: E402
from orion_settings.backend import Result, System  # noqa: E402

POLICY = HERE.parent / "polkit" / "ai.orion.admin.policy"
HELPER = HERE.parent / "helpers" / "orion-settings-admin"


class Fake:
    """Answers by the first word(s) of a command; records everything (and stdin) it was given."""

    def __init__(self, answers=None, tools=(), files=None, http=None):
        self.answers, self.tools, self.files, self.http_answers = answers or {}, set(tools), files or {}, http or {}
        self.calls, self.stdin = [], []

    def which(self, name):
        return name in self.tools

    def read(self, path):
        return self.files.get(path)

    def run(self, argv, timeout=20, secret=None, env=None):
        self.calls.append(list(argv))
        for k, v in self.answers.items():
            if " ".join(argv).startswith(k):
                return v
        return Result(0, "")

    def run_input(self, argv, text, timeout=120):
        self.stdin.append(text)
        return self.run(argv, timeout)

    def http(self, method, url, body=None, timeout=4):
        self.calls.append([method, url.rsplit(":9120", 1)[-1]])
        return self.http_answers.get(url.rsplit(":9120", 1)[-1])


def mk(tmp_path, **kw):
    f = Fake(**kw)
    return System(f, home=str(tmp_path)), f


# ------------------------------------------------------------------ admin.py
def test_terminal_command_prefers_pkexec_with_the_old_way_as_fallback():
    cmd = A.terminal_command(["/usr/local/bin/orion-install-disk"], pkexec=True)
    assert cmd.startswith("pkexec /usr/local/bin/orion-install-disk; rc=$?;")
    assert "rc -eq 126" in cmd and "su -P - orion-admin -c 'sudo /usr/local/bin/orion-install-disk'" in cmd
    assert A.terminal_command(["/usr/local/bin/orion-install-disk"], pkexec=False) == \
        "su -P - orion-admin -c 'sudo /usr/local/bin/orion-install-disk'"
    # a program without a polkit action never goes through pkexec
    assert A.terminal_command(["/usr/bin/something"], pkexec=True).startswith("su -P")
    # the line is valid shell
    subprocess.run(["sh", "-n", "-c", A.terminal_command(["/usr/local/bin/orion-slot", "update"], True)], check=True)


def test_pkexec_ready_needs_pkexec_the_policy_and_a_password_window(tmp_path):
    files = {A.POLICY_FILE: "<policyconfig/>"}
    s, _ = mk(tmp_path, tools={"pkexec"}, files=files, answers={"pgrep": Result(0, "1234\n")})
    assert A.pkexec_ready(s)
    s, _ = mk(tmp_path, tools={"pkexec"}, files=files, answers={"pgrep": Result(1, "")})
    assert not A.pkexec_ready(s)                       # no polkit agent running: the terminal way
    s, _ = mk(tmp_path, tools={"pkexec"}, files={}, answers={"pgrep": Result(0, "1\n")})
    assert not A.pkexec_ready(s)                       # no policy installed


def test_set_password_goes_through_stdin_never_the_command_line(tmp_path):
    s, f = mk(tmp_path)
    pw = "orbit" + "-quiet-" + "lamp"                  # built at runtime ([fake-secrets])
    ok, msg = A.set_password(s, pw)
    assert ok and "Password set" in msg
    assert f.calls[-1] == ["pkexec", A.ADMIN_HELPER, "set-password"] and f.stdin == [pw + "\n"]
    assert all(pw not in " ".join(c) for c in f.calls)
    assert (tmp_path / ".config" / "orion" / "lock-ready").exists()
    s, f = mk(tmp_path, answers={"pkexec": Result(126, "", "")})
    assert A.set_password(s, pw) == (False, "Cancelled: nothing changed.")
    assert A.password_problem("abc", "abc") == "Use at least 8 characters."
    assert A.password_problem("abcdefgh", "abcdefgx") == "The two passwords aren't the same."
    assert A.password_problem("abcdefg\nh", "abcdefg\nh").startswith("A password can't")
    assert A.password_problem("abcdefgh", "abcdefgh") is None


def test_login_mode_and_has_password(tmp_path):
    s, f = mk(tmp_path, answers={"pkexec": Result(0, "Done. OrionOS will start straight into Orion.\nmore\n"),
                                 "passwd -S orion": Result(0, "orion P 10/17/2026 0 99999 7 -1\n")})
    assert A.login_mode(s, "auto") == (True, "Done. OrionOS will start straight into Orion.")
    assert f.calls[-1] == ["pkexec", A.ADMIN_HELPER, "login", "auto"]
    assert A.has_password(f, "orion") is True
    f.answers["passwd -S orion"] = Result(0, "orion L 10/17/2026 0 99999 7 -1\n")
    assert A.has_password(f, "orion") is False
    f.answers["passwd -S orion"] = Result(1, "")
    assert A.has_password(f, "orion") is None


def test_policy_matches_the_programs_settings_runs():
    doc = xml.dom.minidom.parse(str(POLICY))
    paths = {}
    for a in doc.getElementsByTagName("action"):
        for ann in a.getElementsByTagName("annotate"):
            if ann.getAttribute("key") == "org.freedesktop.policykit.exec.path":
                paths[ann.firstChild.data.strip()] = a.getAttribute("id")
        assert "auth_admin" in a.getElementsByTagName("allow_active")[0].firstChild.data
        assert a.getElementsByTagName("allow_any")[0].firstChild.data == "no"
    assert paths == {k.replace(str(A.HELPERS), "/opt/orion/settings/helpers"): v for k, v in A.POLICY_TOOLS.items()}


def test_admin_helper_refuses_anything_else(tmp_path):
    subprocess.run(["sh", "-n", str(HELPER)], check=True)
    r = subprocess.run(["sh", str(HELPER), "rm", "-rf", "/"], capture_output=True, text=True)
    assert r.returncode != 0                             # not root here, and not a known job anyway
    text = HELPER.read_text()
    assert "chpasswd" in text and "PKEXEC_UID" in text and "orion-admin|orion-agent" in text


# ------------------------------------------------------------------ desktop_settings.py
def test_keyboard_data_and_change(tmp_path):
    cur = json.dumps({"layout": "gb", "variant": "", "name": "English (UK)"})
    lst = json.dumps([{"value": "gb", "name": "English (UK)"}, {"value": "us:intl", "name": "English (US, intl.)"}])
    s, f = mk(tmp_path, tools={"orion-keyboard"}, answers={"orion-keyboard current": Result(0, cur),
                                                            "orion-keyboard list": Result(0, lst),
                                                            "orion-keyboard set": Result(0, "Keyboard: German.\n")})
    kb = D.keyboard(s)
    assert kb["current"] == "gb" and ("us:intl", "English (US, intl.)") in kb["choices"]
    assert D.set_keyboard(s, "de") == (True, "Keyboard: German.")
    assert f.calls[-1] == ["orion-keyboard", "set", "de"]
    assert D.set_keyboard(s, "de; reboot")[0] is False
    assert D.keyboard(mk(tmp_path)[0]) == {"available": False}


def test_window_buttons(tmp_path):
    s, f = mk(tmp_path, tools={"orion-windows", "orion-shortcuts"},
              answers={"orion-windows buttons status": Result(0, "off\n"),
                       "orion-windows buttons on": Result(0, "Window buttons are on.\n")})
    assert D.windows(s) == {"available": True, "buttons": False, "shortcuts": True}
    assert D.set_buttons(s, True) == (True, "Window buttons are on.")


def test_hardware_report_and_phone(tmp_path):
    rep = {"safe": True, "headline": "Safe to install on this PC", "warnings": 2,
           "rows": [{"id": "gpu", "label": "Graphics", "status": "ok", "text": "Intel", "fix": ""}]}
    s, f = mk(tmp_path, tools={"orion-check-hardware"}, answers={"orion-check-hardware": Result(0, json.dumps(rep))},
              http={"/hardware/report/send": {"sent": True}})
    assert D.hardware(s)["safe"]
    assert D.headline(rep) == ("Safe to install on this PC",
                               "Everything important works. 2 things worth knowing below.", "ok")
    assert D.headline({"safe": False, "headline": "Not ready yet: 1 thing"})[2] == "warn"
    assert D.send_hardware(s) == (True, "Sent to your phone.")
    f.http_answers["/hardware/report/send"] = {"sent": False, "message": "No phone is paired yet."}
    assert D.send_hardware(s) == (False, "No phone is paired yet.")
    f.http_answers.clear()
    assert D.send_hardware(s)[0] is False
    assert D.hardware(mk(tmp_path)[0]) is None
    assert set(D.ICONS) >= {"gpu", "wifi", "speakers", "touchpad"}


# ------------------------------------------------------------------ Gravity mode (after-1.0 plan §3)
def test_gravity_mode_setting(tmp_path):
    s, f = mk(tmp_path)
    assert D.gravity(s) == {"available": False}                     # no orion-gravity: no section
    st = {"wanted": False, "active": False, "reason": "Gravity mode is off.", "session": "sway",
          "hyprland_installed": True, "compositor": "sway"}
    s, f = mk(tmp_path, tools={"orion-gravity"},
              answers={"orion-gravity status": Result(0, json.dumps(st)),
                       "orion-gravity on": Result(0, "Gravity mode is paused on battery.\n"),
                       "orion-gravity session hyprland": Result(1, "", "Hyprland isn't installed")})
    g = D.gravity(s)
    assert g["available"] and g["on"] is False and g["installed"] and not g["hyprland"]   # off by default
    assert D.set_gravity(s, True) == (True, "Gravity mode is paused on battery.")
    assert f.calls[-1] == ["orion-gravity", "on"]
    ok, _msg = D.set_hyprland(s, True)
    assert not ok and f.calls[-1] == ["orion-gravity", "session", "hyprland"]
    D.set_hyprland(s, False)
    assert f.calls[-1] == ["orion-gravity", "session", "sway"]
