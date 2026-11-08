"""Rescue: crash counter, undo planner, report scrubbing, unit files, scripts.   python -m pytest -q os/rescue/tests"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RESCUE = HERE.parent
REPO = RESCUE.parent.parent
sys.path.insert(0, str(RESCUE))

from orion_rescue import crashes, report, undo  # noqa: E402
from orion_rescue.__main__ import OPEN_PANEL, main  # noqa: E402


# ------------------------------------------------------------------ crash counter
def test_quick_crashes_open_the_panel_on_the_third():
    st = {"crashes": [], "rescues": 0}
    st, r1 = crashes.decide(st, ran_for=3, now=1000)
    st, r2 = crashes.decide(st, ran_for=5, now=1010)
    assert not r1 and not r2
    st, r3 = crashes.decide(st, ran_for=2, now=1020)
    assert r3 and st["crashes"] == [] and st["rescues"] == 1


def test_a_healthy_run_starts_the_count_again():
    st = {"crashes": [1000, 1010], "rescues": 0}
    st, r = crashes.decide(st, ran_for=600, now=1700)
    assert not r and st["crashes"] == []


def test_old_crashes_fall_out_of_the_window():
    st = {"crashes": [0, 10], "rescues": 0}
    st, r = crashes.decide(st, ran_for=1, now=10_000)
    assert not r and st["crashes"] == [10_000]


def test_record_and_reset_use_the_state_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("ORION_RESCUE_AFTER", raising=False)
    assert crashes.record(1, now=100) is False
    assert crashes.record(1, now=101) is False
    assert crashes.record(1, now=102) is True
    assert crashes.summary()["rescues"] == 1
    crashes.record(1, now=103)
    crashes.reset()
    assert crashes.summary()["recent_crashes"] == 0


def test_limit_can_be_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ORION_RESCUE_AFTER", "1")
    assert crashes.record(0, now=5) is True


def test_broken_state_file_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    crashes.state_path().write_text("{not json")
    assert crashes.record(1, now=1) is False


def test_cli_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ORION_RESCUE_AFTER", "2")
    assert main(["record-crash", "4"]) == 0
    assert main(["record-crash", "4"]) == OPEN_PANEL
    assert main(["record-crash", "999"]) == 0


# ------------------------------------------------------------------ undo planner
class FakeProbe(undo.Probe):
    def __init__(self, profile="baremetal", slot=False, snap=None, changes=None, exists=()):
        self._p, self._slot, self._snap, self._ch, self._ex = profile, slot, snap, changes, set(exists)

    def profile(self):
        return self._p

    def exists(self, path):
        return path in self._ex

    def slot_can_rollback(self):
        return self._slot

    def snapshot_status(self):
        return self._snap

    def changes(self):
        return self._ch


CHANGES = [
    {"id": "c3", "kind": "setting", "what": "Turned on night light", "undo": {"kind": "x", "label": "Turn it off"}},
    {"id": "c2", "kind": "app_update", "what": "Updated Firefox", "undo": {"kind": "argv", "sensitive": False}},
    {"id": "c1", "kind": "app_update", "what": "Updated GIMP", "undo": None},
]


def test_stick_with_slot_rollback_goes_back_a_slot():
    p = FakeProbe("image", slot=True, snap={"undo_to": "s1", "backend": "vault"}, exists={undo.SLOT})
    plan = undo.plan_last_update(p)
    assert plan.kind == "slot" and plan.argv == ["pkexec", undo.SLOT, "rollback"]


def test_stick_without_slot_rollback_uses_the_vault_snapshot():
    p = FakeProbe("image", slot=False, snap={"undo_to": "s1", "backend": "vault"}, exists={undo.SLOT})
    plan = undo.plan_last_update(p)
    assert plan.kind == "snapshot" and plan.argv == ["pkexec", undo.SNAPSHOT, "undo"]
    assert "settings" in plan.detail


def test_installed_btrfs_uses_the_snapshot():
    plan = undo.plan_last_update(FakeProbe(snap={"undo_to": "42", "backend": "btrfs"}))
    assert plan.kind == "snapshot" and "system" in plan.detail


def test_no_snapshot_falls_back_to_the_last_update_in_the_history():
    plan = undo.plan_last_update(FakeProbe(snap={"undo_to": None}, changes=CHANGES))
    assert plan.kind == "change" and plan.change_id == "c2" and "Firefox" in plan.title


def test_last_change_prefers_the_history_then_the_snapshot():
    assert undo.plan_last_change(FakeProbe(changes=CHANGES)).change_id == "c3"
    already = [dict(CHANGES[0], undone_by="u1"), CHANGES[1]]
    assert undo.plan_last_change(FakeProbe(changes=already)).change_id == "c2"
    plan = undo.plan_last_change(FakeProbe(changes=None, snap={"undo_to": "7", "backend": "btrfs"}))
    assert plan.kind == "snapshot"


def test_nothing_to_undo_says_so():
    for plan in (undo.plan_last_update(FakeProbe()), undo.plan_last_change(FakeProbe(changes=[]))):
        assert plan.kind == "none" and not plan.possible and plan.detail


def test_sensitive_change_mentions_the_phone():
    ch = [{"id": "x", "kind": "freeze", "what": "Froze Orion", "undo": {"kind": "unfreeze", "sensitive": True}}]
    assert "phone" in undo.plan_last_change(FakeProbe(changes=ch)).detail


def test_slot_rollback_detection(tmp_path, monkeypatch):
    f = tmp_path / "orion-slot"
    f.write_text("case x in\n  status) status ;;\nesac\n")
    monkeypatch.setattr(undo, "SLOT", str(f))
    assert undo.Probe().slot_can_rollback() is False
    f.write_text("case x in\n  status) status ;;\n  rollback) rollback ;;\nesac\n")
    assert undo.Probe().slot_can_rollback() is True
    # the real orion-slot in this tree (the updates work adds rollback; until then: not offered)
    real = REPO / "os/image/files/usr/local/bin/orion-slot"
    monkeypatch.setattr(undo, "SLOT", str(real))
    assert undo.Probe().slot_can_rollback() == ("\n  rollback)" in real.read_text())


def test_terminal_command_keeps_the_window_open():
    plan = undo.UndoPlan("snapshot", "t", argv=["pkexec", "/usr/local/bin/orion-snapshot", "undo"])
    argv = undo.terminal_command(plan)
    assert argv[-1].startswith("pkexec /usr/local/bin/orion-snapshot undo;") and "read _" in argv[-1]


def test_change_undo_without_a_daemon(monkeypatch):
    monkeypatch.setattr(undo.daemon_api, "request", lambda *a, **k: None)
    ok, msg = undo.run(undo.UndoPlan("change", "t", change_id="c1"))
    assert not ok and "isn't answering" in msg


def test_change_undo_pending_on_the_phone(monkeypatch):
    monkeypatch.setattr(undo.daemon_api, "request",
                        lambda m, p, b=None, timeout=4: {"ok": True, "pending": True, "message": "Waiting"})
    assert undo.run(undo.UndoPlan("change", "t", change_id="c1")) == (True, "Waiting")


# ------------------------------------------------------------------ report
def test_report_hides_secrets():
    key = "sk-" + "proj-" + "A" * 24
    text = report.scrub(f"token={key}\npassword: hunter22\nAuthorization Bearer abcdefgh1234\nnormal line")
    assert key not in text and "hunter22" not in text and "abcdefgh1234" not in text
    assert "normal line" in text


def test_report_saved_privately_and_sent(tmp_path, monkeypatch):
    sent = {}
    monkeypatch.setattr(report.daemon_api, "request",
                        lambda m, p, b=None, timeout=4: sent.update(path=p, body=b) or {"ok": True})
    monkeypatch.setattr(report.Path, "home", staticmethod(lambda: tmp_path))
    ok, msg = report.send("OrionOS rescue report\nline two\nline three")
    assert ok and sent["path"] == "/apps/notify" and len(sent["body"]["body"]) <= 600
    files = list((tmp_path / "Orion reports").iterdir())
    assert len(files) == 1 and oct(files[0].stat().st_mode & 0o777) == "0o600"


def test_report_without_daemon_says_where_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(report.daemon_api, "request", lambda *a, **k: None)
    monkeypatch.setattr(report.Path, "home", staticmethod(lambda: tmp_path))
    ok, msg = report.send("x\ny")
    assert not ok and "Orion reports" in msg


def test_report_build_with_fake_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(report.daemon_api, "alive", lambda: False)
    out = report.build(run=lambda argv: "apikey=" + "x" * 10 if "journalctl" in argv else "")
    assert "OrionOS rescue report" in out and "x" * 10 not in out and "answering: no" in out


# ------------------------------------------------------------------ scripts and session files
@pytest.mark.parametrize("script", ["orion-rescue", "orion-rescue-session", "orion-journal-vault"])
def test_shell_scripts_parse(script):
    assert subprocess.run(["sh", "-n", str(RESCUE / script)]).returncode == 0


def test_hud_wrapper_opens_the_panel_after_crashes(tmp_path):
    """orion-hud with a HUD that dies at once: the rescue command is called with --after-crash."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    (bindir / "orion").write_text("#!/bin/sh\nexit 1\n")
    rescue = bindir / "orion-rescue"
    rescue.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$1" in
  record-crash) n=$(grep -c record-crash {log}); [ "$n" -ge 3 ] && exit 10; exit 0 ;;
  --after-crash) exit 0 ;;
esac
exit 0
""")
    for name in ("notify-send", "systemd-detect-virt", "sleep"):
        (bindir / name).write_text("#!/bin/sh\nexit 1\n" if name == "systemd-detect-virt" else "#!/bin/sh\nexit 0\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    src = (REPO / "os/lab/bin/orion-hud").read_text().replace("/usr/bin/orion /usr/bin/Orion", f"{bindir}/orion")
    hud = tmp_path / "orion-hud"
    hud.write_text(src)
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin")
    p = subprocess.Popen(["sh", str(hud)], env=env)
    try:
        for _ in range(100):
            if log.exists() and "--after-crash" in log.read_text():
                break
            subprocess.run(["sleep", "0.05"])
    finally:
        p.kill()
    calls = log.read_text().splitlines()
    assert calls[0] == "reset-crashes"
    assert "--after-crash" in calls and calls.index("--after-crash") == 4   # reset + 3 crashes, then the panel


def test_systemd_unit(tmp_path):
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze not available")
    u = tmp_path / "orion-journal-vault.service"
    u.write_text((RESCUE / "orion-journal-vault.service").read_text())
    r = subprocess.run(["systemd-analyze", "verify", "--man=no", str(u)], capture_output=True, text=True)
    bad = [ln for ln in (r.stderr + r.stdout).splitlines()
           if ln.strip() and "is not executable" not in ln and "system bus" not in ln]
    assert not bad, bad


def test_sway_rescue_config_has_no_hud():
    conf = (RESCUE / "sway-rescue.conf").read_text()
    assert "orion-hud" not in conf and "sway-session.target" not in conf and "orion-session-start" not in conf
    for needed in ("exec foot", "orion-files", "orion-browser", "orion-rescue --session", "before-sleep"):
        assert needed in conf
