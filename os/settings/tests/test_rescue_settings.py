"""Settings > Lock & rescue without GTK: lock / screen-off times, your own screen-reading exclusions, undo plans."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orion_settings import rescue_settings as RS  # noqa: E402


def test_idle_defaults_and_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(RS.LockRescue, "_restart_idle", staticmethod(lambda: None))
    lr = RS.LockRescue(tmp_path)
    assert lr.idle() == {"LOCK_AFTER": "5", "SCREEN_OFF_AFTER": "10"}
    ok, msg = lr.set_idle("LOCK_AFTER", "2")
    assert ok and "2 minutes" in msg
    ok, msg = lr.set_idle("SCREEN_OFF_AFTER", "0")
    assert ok and "stay on" in msg
    assert lr.idle() == {"LOCK_AFTER": "2", "SCREEN_OFF_AFTER": "0"}
    assert "LOCK_AFTER=2" in (tmp_path / "idle.conf").read_text()
    assert lr.set_idle("LOCK_AFTER", "999")[0] is False
    assert lr.set_idle("NOPE", "1")[0] is False
    assert lr.set_idle("LOCK_AFTER", "1; rm")[0] is False
    ok, msg = lr.set_idle("LOCK_AFTER", "0")
    assert "before sleep" in msg


def test_choices_match_orion_idle():
    values = [v for v, _ in RS.LOCK_CHOICES + RS.OFF_CHOICES]
    assert all(v.isdigit() and int(v) <= 240 for v in values)
    assert "5" in [v for v, _ in RS.LOCK_CHOICES] and "10" in [v for v, _ in RS.OFF_CHOICES]


def test_exclude_words(tmp_path):
    lr = RS.LockRescue(tmp_path)
    assert lr.exclude_words() == []
    ok, msg = lr.set_exclude_words("Payroll, my  diary,payroll\nHealth portal")
    assert ok and "3 words" in msg
    assert lr.exclude_words() == ["Payroll", "my diary", "Health portal"]
    assert oct((tmp_path / "capture-exclude.txt").stat().st_mode & 0o777) == "0o600"
    ok, msg = lr.set_exclude_words("  ")
    assert "empty" in msg and lr.exclude_words() == []


def test_undo_plans_come_from_the_rescue_code(monkeypatch):
    plans = RS.LockRescue.plans()
    assert set(plans) == {"change", "update"}
    from orion_rescue import undo
    assert isinstance(plans["change"], undo.UndoPlan) and isinstance(plans["update"], undo.UndoPlan)


def test_page_registered():
    src = (Path(__file__).resolve().parent.parent / "orion_settings/pages.py").read_text()
    assert "LockRescuePage" in src
    page = (Path(__file__).resolve().parent.parent / "orion_settings/rescue_pages.py").read_text()
    assert "Undo the last change" in page and "Lock the screen" in page
