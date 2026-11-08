"""Exposure Map (toolkit tool 20): listeners, addresses, firewall and Tailscale -> who can reach what."""
import copy
import json

import exposure_map as E
from sec_fakes import FakeShell, fixture


def shell(ufw=True, serve=True):
    runs = {"ss -tulpnH": (0, fixture("ss.txt")), "ip -j addr": (0, fixture("ip_addr.json")),
            "tailscale status --json": (0, fixture("tailscale_status.json")),
            "tailscale debug prefs": (0, json.dumps({"ShieldsUp": False})),
            "tailscale serve status --json": (0, fixture("tailscale_serve.json") if serve else "{}")}
    files = {str(E.UFW_DUMP): fixture("ufw.txt")} if ufw else {}
    return FakeShell(runs, tools=["ss", "ip", "tailscale"], files=files)


def by_port(m, port, proto="tcp"):
    return [r for r in m["listeners"] if r["port"] == port and r["proto"] == proto]


def test_parsers():
    rows = E.parse_ss(fixture("ss.txt"))
    assert len(rows) == 11                                   # the ESTAB line is ignored
    dns = rows[0]
    assert (dns["address"], dns["iface"], dns["port"], dns["process"]) == ("127.0.0.53", "lo", 53, "systemd-resolve")
    assert any(r["address"] == "::" and r["port"] == 22 for r in rows)
    fw = E.parse_ufw(fixture("ufw.txt"))
    assert fw["known"] and fw["active"] and fw["default_in"] == "deny"
    ssh = [r for r in fw["rules"] if r["port"] == 22]
    assert ssh[0]["iface"] == "tailscale0" and len(ssh) == 1      # the v6 copy is folded in
    assert any(r["action"] == "DENY" and r["port"] == 3306 for r in fw["rules"])
    assert E.parse_ufw("ERROR: You need to be root to run this script")["known"] is False
    ifs = E.parse_ip_addr(json.loads(fixture("ip_addr.json")))
    assert {(i["iface"], i["kind"]) for i in ifs if not i["virtual"]} >= {("wlp2s0", "lan"), ("tailscale0", "tailscale")}
    assert next(i for i in ifs if i["iface"] == "podman0")["virtual"]
    ts = E.parse_tailscale(json.loads(fixture("tailscale_status.json")), {"ShieldsUp": True},
                           json.loads(fixture("tailscale_serve.json")))
    assert ts["online"] and ts["shields_up"] and ts["funnel"][0]["port"] == 3000


def test_reachability_table():
    m = E.build_map(shell(), machine="orionos")
    assert by_port(m, 9120)[0]["widest"] == "local"
    phone = by_port(m, 9121)[0]
    assert phone["reach"] == ["lan", "tailscale"] and phone["why"] == "firewall lets it through"
    ssh = by_port(m, 22)
    assert all(r["reach"] == ["tailscale"] for r in ssh)
    assert by_port(m, 3306)[0]["reach"] == ["local"] and by_port(m, 3306)[0]["why"] == "firewall blocks it"
    assert by_port(m, 8080)[0]["reach"] == ["local"]          # bound to Tailscale, but no rule lets it in
    funnel = by_port(m, 3000)[0]
    assert funnel["widest"] == "internet" and "Funnel" in funnel["why"]
    assert m["listeners"][0]["port"] == 3000                  # widest first
    assert by_port(m, 5353, "udp")[0]["reach"] == ["lan"]
    text = E.answer(m)
    assert text.startswith("On orionos, 5 things can be reached") and "the internet" in text
    assert "router forwards" in text


def test_without_firewall_rules_everything_bound_widely_counts():
    m = E.build_map(shell(ufw=False, serve=False), machine="x")
    assert by_port(m, 3306)[0]["reach"] == ["lan", "tailscale"] and "unknown" in by_port(m, 3306)[0]["why"]
    assert any("can't read the firewall" in n for n in m["notes"])


def test_diff_new_and_wider():
    old = E.build_map(shell(serve=False), machine="orionos")
    new = E.build_map(shell(serve=True), machine="orionos")
    assert E.diff(None, new) == []
    ch = E.diff(old, new)
    assert [(c["kind"], c["row"]["port"]) for c in ch] == [("wider", 3000)]
    extra = copy.deepcopy(new)
    extra["listeners"].append({**by_port(new, 9121)[0], "port": 8443, "process": "nginx", "widest": "lan"})
    extra["listeners"].append({**by_port(new, 9121)[0], "port": 50000, "proto": "udp", "process": "firefox"})
    ch = E.diff(new, extra)
    assert [(c["kind"], c["row"]["port"]) for c in ch] == [("new", 8443)]   # short-lived UDP ports are ignored
    assert "New: nginx on port 8443/tcp" in E.describe_changes("orionos", ch)


def test_windows_report():
    m = E.map_from_report(json.loads(fixture("windows_report.json")))
    assert m["machine"] == "GAMING-PC"
    assert by_port(m, 3389)[0]["widest"] == "lan"             # firewall allows it from anywhere; the PC is behind NAT
    assert by_port(m, 135)[0]["reach"] == ["local"]           # blocked by the default
    assert by_port(m, 9120)[0]["widest"] == "local"
    assert by_port(m, 27036)[0]["widest"] == "lan"           # a rule for the program, not a port
    assert by_port(m, 9121)[0]["widest"] == "local"          # that program rule is only for Steam
    assert all(i["iface"] != "vEthernet (WSL)" for i in m["interfaces"])


def test_lan_scan_only_own_private_network():
    ifs = E.parse_ip_addr(json.loads(fixture("ip_addr.json")))
    ifs.append({"iface": "eth1", "address": "203.0.113.4", "prefix": 24, "kind": "internet", "virtual": False})
    ifs.append({"iface": "eth2", "address": "10.0.0.5", "prefix": 8, "kind": "lan", "virtual": False})
    assert E.scan_targets(ifs) == ["192.168.1.0/24"]
    sh = FakeShell({"nmap -sT --top-ports 100": (0, fixture("nmap.xml"))}, tools=["nmap"])
    r = E.lan_scan(sh, ifs)
    assert r["ok"] and r["targets"] == ["192.168.1.0/24"]
    nmap_call = next(c for c in sh.calls if c[0] == "nmap")
    assert nmap_call[-1] == "192.168.1.0/24" and "-sT" in nmap_call
    hosts = {h["address"]: h for h in r["hosts"]}
    assert [p["port"] for p in hosts["192.168.1.1"]["open"]] == [53, 80] and "192.168.1.31" not in hosts
    assert "router.lan: 53 (domain), 80 (http)" in r["text"]
    old = {"hosts": [{"address": "192.168.1.1", "open": [{"port": 53}]}]}
    assert E.lan_diff(old, r) == ["router.lan port 80 (http)", "192.168.1.30 port 631 (ipp)"]
    assert E.lan_scan(FakeShell(), ifs)["ok"] is False
