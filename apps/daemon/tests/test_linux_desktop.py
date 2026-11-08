"""OrionOS Linux adapter: command building and graceful fallbacks (no Wayland session needed)."""
import time

import linux_desktop as ld


def test_headless_reports_unavailable(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("SWAYSOCK", raising=False)
    assert ld.is_wayland() is False
    assert ld.grab_screen() is None
    assert ld.active_window_title() is None
    assert ld.idle_seconds() is None


def test_idle_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(ld, "is_wayland", lambda: True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert ld.idle_seconds() == 0.0  # no marker: swayidle says the user is active
    (tmp_path / ld.IDLE_FILE_NAME).write_text(str(int(time.time()) - 60))
    assert 64 <= ld.idle_seconds() <= 67  # 60 s since the marker + the 5 s swayidle timeout


def test_hotkey_and_typing_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(ld, "_run", lambda args, **k: calls.append(args) or "")
    g = ld.SwayAutoGUI()
    g.hotkey("ctrl", "alt", "t")
    g.write("hi", interval=0.01)
    g.press("enter")
    assert calls[0] == ["wtype", "-s", "120", "-M", "ctrl", "-M", "alt", "-k", "t", "-m", "alt", "-m", "ctrl"]
    assert calls[1][:3] == ["wtype", "-s", "120"] and calls[1][-2:] == ["--", "hi"]
    assert calls[2] == ["wtype", "-s", "120", "-k", "Return"]
    g.write("fast")  # default speed: no "-d 0", which wtype rejects
    assert "-d" not in calls[3]


def test_mouse_uses_sway_seat_cursor(monkeypatch):
    sent = []
    monkeypatch.setattr(ld, "swaymsg", lambda *a, **k: sent.append(a) or "")
    g = ld.SwayAutoGUI()
    g.PAUSE = 0
    g.click(10, 20, button="right")
    g.scroll(-2)
    assert sent[0] == ("seat", "-", "cursor", "set", "10", "20")
    assert sent[1][-2:] == ("press", "button3") and sent[2][-2:] == ("release", "button3")
    assert [s[-1] for s in sent[3:]] == ["button5"] * 4
