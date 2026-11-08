"""The Rescue panel built against a stand-in GTK (no display here): every button is created, the undo plan fills
in, 'Restart into Windows' only shows when orion-restart-windows exists, and 'Try Orion again' resets the count."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class W:
    """Any GTK widget: remembers its kwargs, children and signal handlers; every other method is a no-op."""
    made = []

    def __init__(self, *a, **k):
        self.kw, self.children, self.handlers, self.label = k, [], {}, k.get("label", "")
        self.sensitive = k.get("sensitive", True)
        W.made.append(self)

    def append(self, c):
        self.children.append(c)

    def attach(self, c, *pos):
        self.children.append(c)

    def set_child(self, c):
        self.children.append(c)

    def connect(self, sig, fn):
        self.handlers[sig] = fn

    def set_label(self, t):
        self.label = t

    def get_label(self):
        return self.label

    def set_sensitive(self, v):
        self.sensitive = v

    def get_application(self):
        return self.app

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def panel(monkeypatch):
    gtk = types.SimpleNamespace(
        ApplicationWindow=W, Application=W, Box=W, Label=W, Grid=W, Button=W, ScrolledWindow=W, CssProvider=W,
        Image=types.SimpleNamespace(new_from_icon_name=lambda n: W(icon=n)),
        Orientation=types.SimpleNamespace(VERTICAL=1, HORIZONTAL=0), PolicyType=types.SimpleNamespace(NEVER=0),
        AccessibleProperty=types.SimpleNamespace(LABEL=0), StyleContext=W, STYLE_PROVIDER_PRIORITY_APPLICATION=1)
    glib = types.SimpleNamespace(idle_add=lambda fn: fn())
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    repo = types.ModuleType("gi.repository")
    repo.Gtk, repo.GLib = gtk, glib
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    sys.modules.pop("orion_rescue.panel", None)
    from orion_rescue import panel as P
    # run background work right away
    monkeypatch.setattr(P, "background", lambda fn, done: done(fn()))
    W.made = []
    yield P
    sys.modules.pop("orion_rescue.panel", None)


def make(P, monkeypatch, which=None, plan=None):
    monkeypatch.setattr(P.shutil, "which", lambda n: which.get(n) if which else None)
    monkeypatch.setattr(P.undo, "plan_last_update",
                        lambda: plan or P.undo.UndoPlan("none", "Nothing to undo yet", "Nothing."))
    app = W()
    win = P.RescueWindow(app, "crash")
    win.app = app
    return win


def test_buttons_and_wording(panel, monkeypatch):
    win = make(panel, monkeypatch)
    labels = [w.label for w in W.made if w.label]
    for t in ("The Orion screen keeps stopping", "Terminal", "Files", "Browser", "Undo the last update",
              "Send a report to my phone", "Try Orion again"):
        assert t in labels
    assert "Restart into Windows" not in labels
    assert win.undo_btn.sensitive is False   # nothing to undo


def test_windows_button_only_when_installed(panel, monkeypatch):
    make(panel, monkeypatch, which={"orion-restart-windows": "/usr/local/bin/orion-restart-windows"})
    assert "Restart into Windows" in [w.label for w in W.made if w.label]


def test_undo_plan_enables_the_button_and_runs(panel, monkeypatch):
    plan = panel.undo.UndoPlan("change", "Undo “Updated Firefox”", "Undoes it.", change_id="c2")
    win = make(panel, monkeypatch, plan=plan)
    assert win.undo_btn.sensitive is True and "Firefox" in win.undo_sub.label
    ran = []
    monkeypatch.setattr(panel.undo, "run", lambda p: ran.append(p) or (True, "Undone."))
    win.undo_btn.handlers["clicked"](win.undo_btn)
    assert ran == [plan] and win.status.label == "Undone."


def test_try_again_resets_and_quits(panel, monkeypatch):
    win = make(panel, monkeypatch)
    calls = []
    monkeypatch.setattr(panel.crashes, "reset", lambda: calls.append("reset"))
    win.app.quit = lambda: calls.append("quit")
    win.try_again()
    assert calls == ["reset", "quit"]
