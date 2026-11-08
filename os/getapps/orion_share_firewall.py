#!/usr/bin/env python3
"""Root helper for Share files: opens LocalSend's port (53317, TCP and UDP) in the firewall, only to the local
networks this computer is on right now, and closes it again. Off by default.

Run only by systemd as orion-share-firewall.service (ExecStart=open, ExecStop=close), which the session user may
start and stop (polkit rule 50-orion-getapps.rules), and by orion-share-firewall-reset.service at boot (close), so
a power cut can't leave the port open. Takes no other input: the networks come from the machine itself.

    orion_share_firewall.py open | close | status
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys

PORT = 53317
TAG = "orion-share LocalSend"
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
# Not your home network: containers, VMs, VPNs and the like.
SKIP_IFACES = re.compile(r"^(lo|podman|docker|br-|veth|virbr|vboxnet|cni|flannel|tailscale|wg|tun|tap|zt)")
FALLBACK = ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]
# Test hooks (systemd starts the real helper with a clean environment, so these can't be set from a session).
UFW = os.environ.get("ORION_SHARE_UFW", "ufw")
IP = os.environ.get("ORION_SHARE_IP", "ip")


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def lan_networks() -> list[str]:
    """The private IPv4 networks of this computer's real network interfaces (Wi-Fi, Ethernet)."""
    r = _run([IP, "-4", "-o", "addr", "show", "scope", "global"])
    nets: list[str] = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface, cidr = parts[1].split("@")[0], parts[3]
        if SKIP_IFACES.match(iface):
            continue
        try:
            net = ipaddress.ip_interface(cidr).network
        except ValueError:
            continue
        if net.prefixlen < 8 or not any(net.subnet_of(p) for p in PRIVATE):
            continue
        if str(net) not in nets:
            nets.append(str(net))
    return nets


def rules() -> list[tuple[int, str]]:
    """(number, line) of the rules this helper added, from `ufw status numbered`."""
    r = _run([UFW, "status", "numbered"])
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"^\[\s*(\d+)\]\s+(.*)$", line)
        if m and TAG in m.group(2) and str(PORT) in m.group(2):
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def close() -> int:
    for num, _line in sorted(rules(), reverse=True):   # highest first: numbers shift as rules go
        _run([UFW, "--force", "delete", str(num)])
    return 0


def open_() -> int:
    close()
    nets = lan_networks() or FALLBACK
    ok = True
    for net in nets:
        for proto in ("tcp", "udp"):
            r = _run([UFW, "allow", "in", "from", net, "to", "any", "port", str(PORT), "proto", proto,
                      "comment", TAG])
            ok = ok and r.returncode == 0
    print(f"LocalSend port {PORT} open to {', '.join(nets)}")
    return 0 if ok else 1


def status() -> int:
    rs = rules()
    print(json.dumps({"open": bool(rs), "port": PORT, "rules": [line for _n, line in rs]}))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "open":
        return open_()
    if cmd == "close":
        return close()
    if cmd == "status":
        return status()
    print("usage: orion_share_firewall.py open|close|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
