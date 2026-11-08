"""Share files: the firewall helper (stand-in ufw and ip) and orion-share's open/close logic."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GETAPPS = HERE.parent
sys.path.insert(0, str(GETAPPS))

IP_OUT = """1: lo    inet 127.0.0.1/8 scope host lo
2: wlp2s0    inet 192.168.1.23/24 brd 192.168.1.255 scope global dynamic wlp2s0
3: enp3s0    inet 10.20.30.4/16 brd 10.20.255.255 scope global enp3s0
4: tailscale0    inet 100.101.102.103/32 scope global tailscale0
5: podman0    inet 10.88.0.1/16 brd 10.88.255.255 scope global podman0
6: wwan0    inet 81.2.69.160/24 scope global wwan0
7: virbr0    inet 192.168.122.1/24 brd 192.168.122.255 scope global virbr0
"""

# A tiny stand-in ufw that keeps its rules in a JSON file and prints `ufw status numbered` like the real one.
FAKE_UFW = r'''
import json, os, sys
path = os.environ["FAKE_UFW_STATE"]
rules = json.load(open(path)) if os.path.exists(path) else ["9121/tcp ALLOW IN 10.77.0.11 # boardroom verdicts"]
a = sys.argv[1:]
if a[:2] == ["status", "numbered"]:
    print("Status: active\n\n     To                         Action      From\n     --                         ------      ----")
    for i, r in enumerate(rules, 1):
        print(f"[{i:2d}] {r}")
elif a[:2] == ["--force", "delete"]:
    del rules[int(a[2]) - 1]
elif a[0] == "allow":
    frm, port, proto, comment = a[a.index("from") + 1], a[a.index("port") + 1], a[a.index("proto") + 1], a[-1]
    rules.append(f"{port}/{proto} ALLOW IN {frm} # {comment}")
json.dump(rules, open(path, "w"))
'''


@pytest.fixture()
def fw(tmp_path, monkeypatch):
    ufw = tmp_path / "ufw"
    ufw.write_text(f"#!{sys.executable}\n{FAKE_UFW}")
    ufw.chmod(0o755)
    ip = tmp_path / "ip"
    ip.write_text("#!/bin/sh\ncat <<'EOF'\n" + IP_OUT + "EOF\n")
    ip.chmod(0o755)
    env = {**os.environ, "ORION_SHARE_UFW": str(ufw), "ORION_SHARE_IP": str(ip),
           "FAKE_UFW_STATE": str(tmp_path / "ufw.json")}

    def run(cmd):
        return subprocess.run([sys.executable, str(GETAPPS / "orion_share_firewall.py"), cmd], env=env,
                              capture_output=True, text=True, timeout=30)

    def rules():
        p = tmp_path / "ufw.json"
        return json.loads(p.read_text()) if p.exists() else []
    return run, rules


def test_open_only_to_real_home_networks(fw):
    run, rules = fw
    assert run("open").returncode == 0
    mine = [r for r in rules() if "orion-share LocalSend" in r]
    assert sorted(mine) == sorted(f"53317/{p} ALLOW IN {n} # orion-share LocalSend"
                                  for n in ("192.168.1.0/24", "10.20.0.0/16") for p in ("tcp", "udp"))
    # VPN, container, VM, public and loopback networks are never opened
    assert not any(x in " ".join(mine) for x in ("100.101", "10.88", "81.2", "192.168.122", "127.0"))
    st = json.loads(run("status").stdout)
    assert st["open"] is True and st["port"] == 53317 and len(st["rules"]) == 4


def test_close_removes_only_its_rules_and_open_is_idempotent(fw):
    run, rules = fw
    run("open")
    run("open")                                   # re-open doesn't stack duplicates
    assert len([r for r in rules() if "orion-share" in r]) == 4
    assert run("close").returncode == 0
    assert rules() == ["9121/tcp ALLOW IN 10.77.0.11 # boardroom verdicts"]
    assert json.loads(run("status").stdout)["open"] is False
    assert run("bogus").returncode == 2


def test_lan_networks_fallback(monkeypatch):
    import orion_share_firewall as f
    monkeypatch.setattr(f, "_run", lambda argv: subprocess.CompletedProcess(argv, 0, IP_OUT, ""))
    assert f.lan_networks() == ["192.168.1.0/24", "10.20.0.0/16"]


@pytest.fixture()
def share(tmp_path, monkeypatch):
    """orion-share with a stand-in systemctl (unit state in a file) and flatpak."""
    import orion_share
    state = tmp_path / "unit"
    log = tmp_path / "log"
    ctl = tmp_path / "systemctl"
    ctl.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$1" in
  start) touch {state} ;;
  stop) rm -f {state} ;;
  is-active) [ -f {state} ] ;;
esac
""")
    ctl.chmod(0o755)
    monkeypatch.setattr(orion_share, "SYSTEMCTL", str(ctl))
    fpstate = tmp_path / "fp.json"
    fpstate.write_text(json.dumps({"apps": {"org.localsend.localsend_app": {"name": "LocalSend", "commit": "a"}},
                                   "remote": {}}))
    stub = tmp_path / "flatpak"
    stub.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_flatpak.py'} \"$@\"\n")
    stub.chmod(0o755)
    monkeypatch.setenv("FAKE_FLATPAK_STATE", str(fpstate))
    monkeypatch.setenv("ORION_FLATPAK", str(stub))
    monkeypatch.setenv("ORION_APPS_NO_NOTIFY", "1")
    return orion_share, state, log


def test_launch_opens_port_and_closes_it_after(share):
    orion_share, state, log = share
    fp = orion_share.core.Flatpak()
    assert orion_share.launch(fp) == 0
    calls = log.read_text().split("\n")
    assert "start orion-share-firewall.service" in calls and "stop orion-share-firewall.service" in calls
    assert not state.exists()                               # closed again


def test_settings_toggle_keeps_it_open(share):
    orion_share, state, log = share
    assert orion_share.firewall(True)["firewall"] == "open"
    orion_share.launch(orion_share.core.Flatpak())
    assert state.exists()                                   # Settings said on: launching doesn't close it
    assert "stop" not in log.read_text()
    assert orion_share.firewall(False)["firewall"] == "closed"
    st = orion_share.status(orion_share.core.Flatpak())
    assert st["installed"] is True and st["firewall"] == "closed"
