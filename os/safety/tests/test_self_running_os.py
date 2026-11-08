"""Self-running, OS side (complete plan step 4): the root helper's allow-lists, the security-update gate, the
self-test runner (with a fake QEMU boot script) and orion-slot's channel reading. Nothing real is touched."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "os/safety/orion-self-helper"
GATE = ROOT / "os/safety/orion-self-gate"
SELFTEST = ROOT / "os/lab/bin/orion-selftest"
SLOT = ROOT / "os/image/files/usr/local/bin/orion-slot"


def sh(argv, env=None, **kw):
    e = {**os.environ, **(env or {})}
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=e, timeout=60, **kw)


# =====================================================================================  root helper
def test_helper_allow_list(tmp_path):
    env = {"ORION_SELF_TEST": "1", "ORION_SELF_ETC": str(tmp_path)}
    r = sh(["sh", HELPER, "restart-unit", "NetworkManager.service"], env)
    assert r.returncode == 0 and "would run: systemctl restart NetworkManager.service" in r.stdout
    assert sh(["sh", HELPER, "restart-unit", "orion-daemon.service"], env).returncode == 0
    for bad in ("sshd.service", "greetd.service", "orion-x;rm.service", "../x.service", ""):
        assert sh(["sh", HELPER, "restart-unit", bad], env).returncode == 2, bad
    assert sh(["sh", HELPER, "anything"], env).returncode == 2


def test_helper_channel_and_levels(tmp_path):
    env = {"ORION_SELF_TEST": "1", "ORION_SELF_ETC": str(tmp_path)}
    assert sh(["sh", HELPER, "channel", "beta"], env).returncode == 0
    assert (tmp_path / "channel").read_text().strip() == "beta"
    assert sh(["sh", HELPER, "channel", "nightly"], env).returncode == 2
    assert sh(["sh", HELPER, "level", "app_update", "3"], env).returncode == 0
    d = json.loads((tmp_path / "self-levels.json").read_text())
    assert d["levels"]["app_update"]["level"] == 3 and d["levels"]["app_update"]["at"] > 0
    for kind, lvl in (("protected_core", "3"), ("daemon_code", "2"), ("system_version", "2"), ("app_update", "9")):
        assert sh(["sh", HELPER, "level", kind, lvl], env).returncode == 2, kind


# =====================================================================================  security-update gate
def test_gate(tmp_path):
    env = {"ORION_SELF_LEVELS_ADMIN": str(tmp_path / "admin.json"), "ORION_DATA_DIR": str(tmp_path / "data"),
           "ORION_FREEZE_MARK": str(tmp_path / "frozen-mark"), "ORION_USER": "nobody-here-xyz"}
    (tmp_path / "data").mkdir()
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 0      # default: just do it
    assert sh([sys.executable, GATE, "system_version"], env).returncode == 1
    (tmp_path / "data/self_levels.json").write_text(json.dumps({"levels": {"security_update": {"level": 1, "at": 5}}}))
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 1
    (tmp_path / "admin.json").write_text(json.dumps({"levels": {"security_update": {"level": 2, "at": 9}}}))
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 1      # the user's "always ask" still holds
    (tmp_path / "data/self_levels.json").unlink()
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 0      # admin says "tell me after"
    (tmp_path / "data/frozen").write_text("x")
    r = sh([sys.executable, GATE, "security_update"], env)
    assert r.returncode == 1 and "frozen" in r.stdout


def test_gate_admin_file_wins_over_user_file(tmp_path):
    """The user's file is writable by any program the user runs: it may lower a level here, never raise it."""
    env = {"ORION_SELF_LEVELS_ADMIN": str(tmp_path / "admin.json"), "ORION_DATA_DIR": str(tmp_path / "data"),
           "ORION_FREEZE_MARK": str(tmp_path / "frozen-mark"), "ORION_USER": "nobody-here-xyz"}
    (tmp_path / "data").mkdir()
    admin = tmp_path / "admin.json"
    user = tmp_path / "data/self_levels.json"
    admin.write_text(json.dumps({"levels": {"security_update": {"level": 1, "at": 5}}}))
    # a newer user entry trying to raise it back to "just do it" is ignored
    user.write_text(json.dumps({"levels": {"security_update": {"level": 3, "at": 9e12}}}))
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 1
    # without an admin entry the plan's default is the ceiling: a user "3" for system_version (default 1) stays 1
    user.write_text(json.dumps({"levels": {"ai_model": {"level": 3, "at": 9e12}, "system_version": {"level": 3}}}))
    assert sh([sys.executable, GATE, "system_version"], env).returncode == 1       # default 1, can't be raised
    # the user lowering below the admin's level works at once
    admin.write_text(json.dumps({"levels": {"security_update": {"level": 3, "at": 5}}}))
    user.write_text(json.dumps({"levels": {"security_update": {"level": 1, "at": 1}}}))
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 1
    user.write_text("not json")
    assert sh([sys.executable, GATE, "security_update"], env).returncode == 0


# =====================================================================================  orion-selftest
def load_selftest():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("orion_selftest", str(SELFTEST))
    spec = importlib.util.spec_from_loader("orion_selftest", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def ppm(path, flat=False):
    w, h = 40, 30
    px = bytes([0] * (w * h * 3)) if flat else bytes((i * 37) % 256 for i in range(w * h * 3))
    path.write_bytes(f"P6\n{w} {h}\n255\n".encode() + px)


def test_screenshot_check(tmp_path):
    st = load_selftest()
    ppm(tmp_path / "a.ppm")
    ppm(tmp_path / "b.ppm", flat=True)
    assert st.looks_alive(tmp_path / "a.ppm")[0]
    assert not st.looks_alive(tmp_path / "b.ppm")[0]
    assert not st.looks_alive(tmp_path / "missing.ppm")[0]


def test_can_vm_explains_why_not(tmp_path, monkeypatch):
    st = load_selftest()
    monkeypatch.setenv("ORION_QEMU", "no-such-qemu-xyz")
    assert st.can_vm() == {"ok": False, "why": "QEMU isn't installed"}
    bins = tmp_path / "bin"
    bins.mkdir()
    for b in ("qemu-fake", "mcopy-fake"):
        (bins / b).write_text("#!/bin/sh\n")
        (bins / b).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bins}:{os.environ['PATH']}")
    monkeypatch.setenv("ORION_QEMU", "qemu-fake")
    monkeypatch.setenv("ORION_MCOPY", "mcopy-fake")
    monkeypatch.setenv("ORION_KVM", str(tmp_path / "kvm"))
    assert "KVM" in st.can_vm()["why"]
    (tmp_path / "kvm").write_text("")
    ovmf = tmp_path / "ovmf"
    ovmf.mkdir()
    monkeypatch.setenv("ORION_OVMF_DIR", str(ovmf))
    assert "firmware" in st.can_vm()["why"]
    (ovmf / "OVMF_CODE_4M.secboot.fd").write_text("")
    (ovmf / "OVMF_VARS_4M.ms.fd").write_text("")
    monkeypatch.setenv("ORION_TEST_BOOT", str(tmp_path / "test-boot.sh"))
    monkeypatch.setattr(st, "SRC", tmp_path / "no-src")
    (tmp_path / "test-boot.sh").write_text("#!/bin/sh\n")
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal: 8000000 kB\nMemAvailable: 2000000 kB\n")
    monkeypatch.setenv("ORION_SELFTEST_MEMINFO", str(mem))
    assert "memory" in st.can_vm()["why"]
    mem.write_text("MemAvailable: 9000000 kB\n")
    monkeypatch.setenv("ORION_SELFTEST_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(st, "MIN_DISK_GB", 0.0)
    assert st.can_vm() == {"ok": True, "why": ""}


FAKE_BOOT = r"""#!/usr/bin/env bash
# fake os/image/test-boot.sh: records calls; "start" writes a booted serial log, "shot" a picture, "type" an answer
W="${ORION_TEST_DIR:?}"
echo "$*" >> "$W/calls"
case "$1" in
  start) mkdir -p "$W"; echo "[  OK  ] Reached target graphical.target - Graphical Interface." > "$W/serial.log" ;;
  shot) python3 -c "
import sys; w,h=40,30; open(sys.argv[1],'wb').write(b'P6\n40 30\n255\n'+bytes((i*37)%256 for i in range(w*h*3)))" "$W/$2.ppm" ;;
  type) printf '%s\nORION-HEALTH-END\n' "${FAKE_HEALTH:-}" >> "$W/serial.log" ;;
  stop) echo stopped >> "$W/calls" ;;
esac
"""


def test_vm_run_with_a_fake_qemu(tmp_path, monkeypatch):
    st = load_selftest()
    tb = tmp_path / "test-boot.sh"
    tb.write_text(FAKE_BOOT)
    tb.chmod(0o755)
    monkeypatch.setenv("ORION_TEST_BOOT", str(tb))
    monkeypatch.setenv("ORION_SELFTEST_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("ORION_SELFTEST_POLL", "0.05")
    monkeypatch.setenv("ORION_SELFTEST_SETTLE", "0")
    monkeypatch.setenv("FAKE_HEALTH", json.dumps({"status": "ok", "words": "fine", "checks": [
        {"id": "hud", "label": "Orion screen", "status": "ok"}], "turbulence": 0, "at": 1.5}))
    parts, info = st.vm(str(tmp_path / "new.img"))
    names = {p["name"]: p["ok"] for p in parts}
    assert names == {"VM boot": True, "VM screen": True, "VM health": True} and info["ran"]
    calls = (tmp_path / "work/vm/calls").read_text()
    assert "start" in calls and "shot selftest" in calls and "stopped" in calls       # always stopped
    monkeypatch.setenv("FAKE_HEALTH", json.dumps({"status": "fail", "checks": [
        {"id": "ai", "label": "AI model", "status": "fail"}], "at": 2}))
    parts, _ = st.vm(str(tmp_path / "new.img"))
    assert [p for p in parts if p["name"] == "VM health"][0]["ok"] is False


def test_run_without_vm_says_why_and_counts_only_real_parts(tmp_path, monkeypatch):
    st = load_selftest()
    monkeypatch.setattr(st, "SRC", tmp_path / "no-src")
    monkeypatch.setattr(st, "API", "http://127.0.0.1:9")            # nothing answers here
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("ORION_PW_PLAY", "no-such-player-xyz")
    res = st.run(no_vm="no KVM access")
    by = {p["name"]: p for p in res["parts"]}
    assert by["Orion's own tests"]["skipped"] and by["Screen check"]["skipped"] and by["Voice loopback"]["skipped"]
    assert by["Test run in a VM"]["skipped"] and "no KVM access" in by["Test run in a VM"]["message"]
    assert not by["Health checks"]["ok"] and res["ok"] is False             # the brain didn't answer


def test_voice_tone_roundtrip(tmp_path):
    st = load_selftest()
    st.tone(tmp_path / "t.wav", seconds=0.1)
    assert st.wav_rms(tmp_path / "t.wav") > 5000
    assert st.wav_rms(tmp_path / "missing.wav") == 0.0


# =====================================================================================  orion-slot channels
@pytest.mark.skipif(not shutil.which("curl"), reason="curl isn't installed")
def test_slot_reads_the_channel(tmp_path):
    (tmp_path / "channel").write_text("mine\n")
    builds = tmp_path / "builds"
    builds.mkdir()
    (builds / "orionos-standard.version").write_text("1.2.3\n")
    env = {"ORION_CHANNEL_FILE": str(tmp_path / "channel"), "MY_BUILDS": str(builds)}
    r = sh(["bash", SLOT, "latest"], env)
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert d["channel"] == "mine" and d["latest"] == "1.2.3"
    r = sh(["bash", SLOT, "fetch-image", str(tmp_path / "img"), "--expect", "9.9.9"], env)
    assert r.returncode != 0 and "Orion tests the new one first" in r.stderr
    # (an unknown word falls back to stable; not run here because stable would ask GitHub)
