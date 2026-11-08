"""Tests for the hardware report card (os/hardware/orion_hwcheck.py) and the graphics-card detection Sway starts with
(os/lab/bin/orion-gpu-env), on made-up /sys and /proc trees.
   python3 -m pytest os/hardware/tests
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import orion_hwcheck as H  # noqa: E402

GPU_ENV = HERE.parents[1] / "lab" / "bin" / "orion-gpu-env"
WPCTL_LAPTOP = """PipeWire 'pipewire-0' [1.0.5, orion@orionos, cookie:1]
 └─ Clients:
        33. WirePlumber                         [1.0.5, orion@orionos, pid:1234]

Audio
 ├─ Devices:
 │      42. Meteor Lake-P HD Audio Controller    [alsa]
 │
 ├─ Sinks:
 │  *   48. Meteor Lake-P HD Audio Controller Speaker [vol: 0.40]
 │      49. Meteor Lake-P HD Audio Controller Headphones [vol: 0.40]
 │
 ├─ Sources:
 │  *   50. Meteor Lake-P HD Audio Controller Digital Microphone [vol: 1.00]
 │
 ├─ Filters:
 │
 └─ Streams:

Video
 ├─ Devices:
 │      60. Integrated Camera                   [v4l2]
"""
WPCTL_DUMMY = """Audio
 ├─ Devices:
 │
 ├─ Sinks:
 │  *   34. Dummy Output                        [vol: 1.00]
 │
 ├─ Sources:
 │
"""
ASOUND = " 0 [sofhdadsp      ]: sof-hda-dsp - sof-hda-dsp\n                      LENOVO-21KC\n"


def write(root: Path, rel: str, text: str = "") -> Path:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def link(root: Path, rel: str, target: str) -> None:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, p)


def card(root: Path, n: int, driver: str, vendor: str) -> None:
    write(root, f"/sys/class/drm/card{n}/device/vendor", vendor + "\n")
    link(root, f"/sys/class/drm/card{n}/device/driver", f"../../../bus/pci/drivers/{driver}")


def laptop(root: Path, *, wpctl: str = WPCTL_LAPTOP, modules: str = "i915 1 0 - Live 0x0\n") -> None:
    """A typical 2024 Intel laptop that works."""
    card(root, 1, "i915", "0x8086")
    (root / "sys/class/drm/card1-eDP-1").mkdir(parents=True)
    write(root, "/proc/modules", modules)
    write(root, "/proc/cmdline", "BOOT_IMAGE=/vmlinuz boot=casper orion.slot=a orion.try=1 quiet\n")
    write(root, "/proc/asound/cards", ASOUND)
    write(root, "/sys/class/net/wlp0s20f3/wireless/.keep")
    link(root, "/sys/class/net/wlp0s20f3/device/driver", "../../../bus/pci/drivers/iwlwifi")
    (root / "sys/class/bluetooth/hci0").mkdir(parents=True)
    (root / "usr/lib/x86_64-linux-gnu/spa-0.2/bluez5").mkdir(parents=True)
    write(root, "/sys/class/video4linux/video0/name", "Integrated Camera: Integrated C\n")
    write(root, "/sys/class/video4linux/video1/name", "Integrated Camera: Integrated C\n")
    write(root, "/sys/power/state", "freeze mem disk\n")
    write(root, "/sys/power/mem_sleep", "[s2idle] deep\n")
    write(root, "/sys/class/power_supply/BAT0/energy_full", "52000000\n")
    write(root, "/sys/class/power_supply/BAT0/energy_full_design", "57000000\n")
    write(root, "/sys/class/power_supply/BAT0/cycle_count", "112\n")
    write(root, "/proc/bus/input/devices", 'I: Bus=0018\nN: Name="ELAN06FA:00 04F3:31BE Touchpad"\n\n')
    root.joinpath("wpctl.txt").write_text(wpctl)


def probe(root: Path, outputs=None, vm: bool = False) -> H.Probe:
    def run(argv):
        if argv[0] == "wpctl":
            p = root / "wpctl.txt"
            return (0, p.read_text()) if p.exists() else (127, "")
        if argv[:2] == ["swaymsg", "-r"]:
            import json
            if argv[-1] == "get_outputs" and outputs is not None:
                return 0, json.dumps(outputs)
            if argv[-1] == "get_inputs":
                return 0, "[]"
        return 127, ""
    env = {"ORION_HWCHECK_VM": "1" if vm else "0"}
    if outputs is not None:
        env["ORION_HWCHECK_SWAY"] = "1"
    return H.Probe(str(root), run=run, env=env)


def rows(rep):
    return {r["id"]: r for r in rep["rows"]}


def test_working_laptop_is_safe_to_install(tmp_path):
    laptop(tmp_path)
    outs = [{"name": "eDP-1", "active": True, "scale": 1.5, "model": "x"}]
    rep = H.report(probe(tmp_path, outputs=outs), suggest=lambda o: 1.5)
    r = rows(rep)
    assert rep["safe"] and rep["headline"] == "Safe to install on this PC"
    assert rep["try_mode"] and rep["usb"] and rep["laptop"]
    assert r["gpu"]["status"] == "ok" and "Intel (i915)" in r["gpu"]["text"]
    assert r["nvidia"]["status"] == "na"
    assert r["wifi"]["status"] == "ok" and "iwlwifi" in r["wifi"]["text"]
    assert r["bluetooth"]["status"] == "ok"
    assert r["speakers"]["text"] == "Sound out: Meteor Lake-P HD Audio Controller Speaker."
    assert r["mic"]["status"] == "ok"
    assert r["webcam"]["status"] == "ok"
    assert r["suspend"]["status"] == "ok" and "light sleep" in r["suspend"]["text"]
    assert r["battery"]["text"].startswith("Battery health 91%, 112 charge cycles")
    assert r["scaling"]["text"] == "Built-in screen at 150%."
    assert r["touchpad"]["status"] == "ok"
    assert [x["id"] for x in rep["rows"]] == H.ORDER
    title, body = H.card_text(rep)
    assert title == "Safe to install on this PC" and body.startswith("Everything important works.")


def test_broken_laptop_gets_a_plain_red_list(tmp_path):
    laptop(tmp_path, wpctl=WPCTL_DUMMY)
    # no Bluetooth audio part, no touchpad, no sleep, a worn battery, an unknown Wi-Fi chip, text too small
    import shutil
    shutil.rmtree(tmp_path / "usr/lib/x86_64-linux-gnu/spa-0.2")
    (tmp_path / "proc/bus/input/devices").write_text('N: Name="AT Translated Set 2 keyboard"\n')
    (tmp_path / "sys/power/state").write_text("freeze disk\n")
    (tmp_path / "sys/class/power_supply/BAT0/energy_full").write_text("30000000\n")
    shutil.rmtree(tmp_path / "sys/class/net/wlp0s20f3")
    write(tmp_path, "/sys/bus/pci/devices/0000:02:00.0/class", "0x028000\n")
    write(tmp_path, "/sys/bus/pci/devices/0000:02:00.0/vendor", "0x14e4\n")
    outs = [{"name": "eDP-1", "active": True, "scale": 1}]
    rep = H.report(probe(tmp_path, outputs=outs), suggest=lambda o: 2)
    r = rows(rep)
    assert not rep["safe"]
    assert rep["headline"] == "Not ready yet: 5 things to fix before installing"
    assert r["speakers"]["status"] == "fail" and "firmware-sof-signed" in r["speakers"]["fix"]
    assert r["mic"]["status"] == "fail"
    assert r["wifi"]["status"] == "fail" and "ubuntu-drivers" in r["wifi"]["fix"]
    assert r["suspend"]["status"] == "fail"
    assert r["touchpad"]["status"] == "fail"
    assert r["bluetooth"]["status"] == "warn" and "libspa-0.2-bluetooth" in r["bluetooth"]["fix"]
    assert r["battery"]["status"] == "warn" and "53%" in r["battery"]["text"]
    assert r["scaling"]["status"] == "warn" and "200%" in r["scaling"]["fix"]
    title, body = H.card_text(rep)
    assert title.startswith("Not ready yet") and len(body.splitlines()) == 5 and "Fix: " in body


def test_nvidia_states(tmp_path):
    laptop(tmp_path)
    card(tmp_path, 0, "nouveau", "0x10de")
    write(tmp_path, "/proc/modules", "nouveau 1 0 - Live 0x0\ni915 1 0 - Live 0x0\n")
    assert rows(H.report(probe(tmp_path)))["nvidia"]["status"] == "warn"
    (tmp_path / "sys/class/drm/card0/device/driver").unlink()
    link(tmp_path, "/sys/class/drm/card0/device/driver", "../../../bus/pci/drivers/nvidia")
    write(tmp_path, "/proc/modules", "nvidia_drm 1 0 - Live 0x0\nnvidia 1 0 - Live 0x0\ni915 1 0 - Live 0x0\n")
    write(tmp_path, "/sys/module/nvidia/version", "580.178.04\n")
    write(tmp_path, "/sys/module/nvidia_drm/parameters/modeset", "N\n")
    r = rows(H.report(probe(tmp_path)))["nvidia"]
    assert r["status"] == "fail" and "nvidia-drm.modeset=1" in r["fix"]
    write(tmp_path, "/sys/module/nvidia_drm/parameters/modeset", "Y\n")
    r = rows(H.report(probe(tmp_path)))["nvidia"]
    assert r["status"] == "ok" and "580.178.04" in r["text"]


def test_basic_driver_only_and_vm(tmp_path):
    write(tmp_path, "/sys/class/drm/card0/device/vendor", "0x10de\n")
    link(tmp_path, "/sys/class/drm/card0/device/driver", "../../../bus/platform/drivers/simple-framebuffer")
    write(tmp_path, "/proc/modules", "")
    r = rows(H.report(probe(tmp_path)))
    assert r["gpu"]["status"] == "fail" and r["nvidia"]["status"] == "fail"
    vm = H.report(probe(tmp_path, vm=True))
    assert vm["vm"] and rows(vm)["gpu"]["status"] == "na"
    assert rows(vm)["suspend"]["status"] == "na" and rows(vm)["wifi"]["status"] == "na"


def test_wpctl_parser():
    n = H.parse_wpctl(WPCTL_LAPTOP)
    assert n["sinks"] == ["Meteor Lake-P HD Audio Controller Speaker", "Meteor Lake-P HD Audio Controller Headphones"]
    assert n["sources"] == ["Meteor Lake-P HD Audio Controller Digital Microphone"]
    assert H.parse_wpctl(WPCTL_DUMMY) == {"sinks": ["Dummy Output"], "sources": []}


# ------------------------------------------------------------------ orion-gpu-env (what orion-session starts Sway with)
def gpu_env(tmp_path, modules: str, cards: list[tuple[int, str]]) -> dict[str, str]:
    root = tmp_path / "g"
    for n, drv in cards:
        link(root, f"/sys/class/drm/card{n}/device/driver", f"../../../bus/pci/drivers/{drv}")
        (root / f"sys/class/drm/card{n}-HDMI-A-1").mkdir(parents=True, exist_ok=True)
    write(root, "/proc/modules", modules)
    out = subprocess.run(["sh", str(GPU_ENV)], capture_output=True, text=True, check=True,
                         env={"PATH": os.environ["PATH"], "ORION_PROC_MODULES": str(root / "proc/modules"),
                              "ORION_DRM_DIR": str(root / "sys/class/drm")}).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith("#"))


def test_gpu_env_standard_intel(tmp_path):
    env = gpu_env(tmp_path, "i915 4096 0 - Live 0x0\n", [(0, "i915")])
    assert env == {"ORION_SWAY_FLAGS": "", "ORION_GPU": "standard"}


def test_gpu_env_nvidia_only(tmp_path):
    env = gpu_env(tmp_path, "nvidia_drm 1 0 - Live 0x0\nnvidia 2 0 - Live 0x0\n", [(0, "nvidia")])
    assert env["ORION_SWAY_FLAGS"] == "--unsupported-gpu" and env["ORION_GPU"] == "nvidia"
    assert env["WLR_NO_HARDWARE_CURSORS"] == "1" and env["GBM_BACKEND"] == "nvidia-drm"
    assert env["__GLX_VENDOR_LIBRARY_NAME"] == "nvidia"


def test_gpu_env_hybrid_keeps_intel_for_the_screen(tmp_path):
    env = gpu_env(tmp_path, "nvidia 2 0 - Live 0x0\ni915 1 0 - Live 0x0\n", [(0, "nvidia"), (1, "i915")])
    assert env["ORION_SWAY_FLAGS"] == "--unsupported-gpu" and env["ORION_GPU"] == "hybrid"
    assert env["WLR_DRM_DEVICES"] == "/dev/dri/card1:/dev/dri/card0"
    assert "GBM_BACKEND" not in env and "__GLX_VENDOR_LIBRARY_NAME" not in env


def test_gpu_env_nouveau_needs_nothing(tmp_path):
    env = gpu_env(tmp_path, "nouveau 1 0 - Live 0x0\n", [(0, "nouveau")])
    assert env["ORION_SWAY_FLAGS"] == ""


def test_orion_session_passes_the_flags():
    s = (HERE.parents[1] / "lab" / "bin" / "orion-session").read_text()
    assert "/opt/swayfx/bin/sway $ORION_SWAY_FLAGS" in s and "exec sway $ORION_SWAY_FLAGS" in s
    assert "orion-gpu-env" in s and "xkb_layout us" not in s


def test_wpctl_parser_on_the_settings_sample():
    text = (HERE.parents[1] / "settings" / "tests" / "samples" / "wpctl-status-0.4.txt").read_text()
    n = H.parse_wpctl(text)
    assert n["sinks"] == ["Built-in Audio Analog Stereo", "USB Headset Analog Stereo"]
    assert n["sources"] == ["Built-in Audio Analog Stereo", "USB Headset Mono"]
