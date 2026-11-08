"""Exposure Map (docs/ORION_TOOLKIT_PLAN.md, tool 20): what can the outside reach?

For each machine Orion builds a table of what is listening on which port, which address it binds (this
computer only, your home network, Tailscale, or the internet) and what the firewall lets through, then works out
who can actually reach it. The map is kept over time: a new listener, or something that suddenly faces the
internet, becomes a card (and a phone alert when it's the internet).

Sources (each optional; the map says what it couldn't see):
    ss -tulpnH                   listening sockets and their programs
    ip -j addr                   which interface owns which address
    ufw status verbose/numbered  firewall rules. ufw needs admin rights, so OrionOS's root timer
                                 (orion-security-dump, os/image/os-security.sh) copies them hourly to
                                 /var/lib/orion/security/ufw-status.txt; the daemon reads that copy.
    tailscale status --json      this machine's Tailscale addresses; `tailscale debug prefs` for Shields Up,
    tailscale serve status --json    and Funnel, which publishes a local port to the whole internet.
    nmap -sT --top-ports 100     a polite scan of your own home network only, weekly, and only if you turn it
                                 on (Settings > Security check-up). Never public or other networks.

Router port-forwards can't be seen from inside, so "your home network" means "and the internet too, if your
router forwards that port".

Other machines: the Windows PC reports its listeners with os/windows/Report-Listeners.ps1 (POST
/exposure/report); any Linux machine can print its own map with `python3 exposure_map.py`.
"""
from __future__ import annotations

import ipaddress
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from sec_common import Shell, loads, machine_name, plural

UFW_DUMP = Path("/var/lib/orion/security/ufw-status.txt")
REACH_ORDER = ("internet", "lan", "tailscale", "local")
REACH_WORDS = {"local": "this computer only", "lan": "your home network", "tailscale": "your Tailscale devices",
               "internet": "the internet"}
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
VIRTUAL_IFACES = re.compile(r"^(docker\d*|podman\d*|virbr\d*|br-|veth|cni|vboxnet|lxc|lxd|flannel|cali|tun)")
MAX_SCAN_HOSTS = 1024


# =====================================================================================  addresses
def classify_ip(addr: str, iface: str = "") -> str:
    """local | lan | tailscale | internet | any (a wildcard bind)."""
    a = addr.strip("[]").split("%")[0]
    if a in ("*", "0.0.0.0", "::", ""):
        return "any"
    if iface.startswith("tailscale"):
        return "tailscale"
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return "lan"
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "local"
    if ip.version == 4 and ip in TAILSCALE_V4 or ip.version == 6 and ip in TAILSCALE_V6:
        return "tailscale"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "internet"


def parse_ss(text: str) -> list[dict[str, Any]]:
    """`ss -tulpnH` lines -> [{proto, address, port, process, pid}]."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("tcp", "udp"):
            continue
        state = parts[1]
        if parts[0] == "tcp" and state != "LISTEN":
            continue
        local = parts[4]
        m = re.match(r"^(.*):(\d+|\*)$", local)
        if not m or m.group(2) == "*":
            continue
        addr, port = m.group(1), int(m.group(2))
        iface = ""
        if "%" in addr:
            addr, iface = addr.split("%", 1)
        addr = addr.strip("[]")
        proc = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        out.append({"proto": parts[0], "address": addr, "iface": iface, "port": port,
                    "process": proc.group(1) if proc else "", "pid": int(proc.group(2)) if proc else None})
    return _dedupe(out)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in rows:
        k = (r["proto"], r["address"], r["port"], r.get("iface", ""))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def parse_ip_addr(data: Any) -> list[dict[str, Any]]:
    """`ip -j addr` -> [{iface, address, prefix, kind}] (kind: local/lan/tailscale/internet)."""
    out = []
    for link in data or []:
        name = str(link.get("ifname", ""))
        for a in link.get("addr_info") or []:
            addr = str(a.get("local", ""))
            if not addr:
                continue
            out.append({"iface": name, "address": addr, "prefix": int(a.get("prefixlen", 32)),
                        "kind": classify_ip(addr, name), "virtual": bool(VIRTUAL_IFACES.match(name))})
    return out


# =====================================================================================  firewall
def parse_ufw(text: Optional[str]) -> dict[str, Any]:
    """`ufw status verbose` and/or `ufw status numbered` -> {known, active, default_in, rules[]}."""
    if not text or "Status:" not in text:
        return {"known": False, "active": None, "default_in": None, "rules": []}
    active = bool(re.search(r"^Status:\s*active", text, re.M))
    m = re.search(r"^Default:\s*(\w+)\s*\(incoming\)", text, re.M)
    default_in = m.group(1).lower() if m else "deny"
    rules, seen = [], set()
    for line in text.splitlines():
        line = re.sub(r"^\[\s*\d+\]\s*", "", line.rstrip())
        m = re.match(r"^(?P<to>.+?)\s{2,}(?P<action>ALLOW|DENY|REJECT|LIMIT)(?: (?P<dir>IN|OUT|FWD))?\s{2,}"
                     r"(?P<frm>.+?)(?:\s+#\s*(?P<comment>.*))?$", line)
        if not m or (m.group("dir") or "IN") != "IN":
            continue
        to, frm = m.group("to").strip(), m.group("frm").strip()
        iface = ""
        im = re.search(r"\s+on\s+(\S+)", to)
        if im:
            iface = im.group(1)
            to = to[:im.start()].strip()
        to = to.replace(" (v6)", "").strip()
        frm = frm.replace(" (v6)", "").strip()
        port, proto, to_addr = None, None, ""
        pm = re.search(r"(?:^|\s)(\d+)(?::\d+)?(?:/(tcp|udp))?$", to)
        if pm:
            port, proto = int(pm.group(1)), pm.group(2)
            to_addr = to[:pm.start()].strip()
        elif to not in ("Anywhere",):
            to_addr = to
        key = (port, proto, iface, frm, m.group("action"))
        if key in seen:
            continue
        seen.add(key)
        rules.append({"port": port, "proto": proto, "iface": iface, "from": frm, "to": to_addr,
                      "action": m.group("action"), "comment": (m.group("comment") or "").strip()})
    return {"known": True, "active": active, "default_in": default_in, "rules": rules}


def source_reach(frm: str, iface: str) -> set[str]:
    """Who a firewall rule's 'From' lets in."""
    if iface.startswith("tailscale"):
        return {"tailscale"}
    if frm in ("Anywhere", "Anywhere (v6)", "any"):
        return {"internet", "lan", "tailscale"}
    try:
        net = ipaddress.ip_network(frm.split()[0], strict=False)
    except ValueError:
        return {"lan"}
    if net.prefixlen == 0:
        return {"internet", "lan", "tailscale"}
    if (net.version == 4 and net.subnet_of(TAILSCALE_V4)) or (net.version == 6 and net.subnet_of(TAILSCALE_V6)):
        return {"tailscale"}
    if net.is_private:
        return {"lan"}
    return {"internet"}


def firewall_allows(fw: dict[str, Any], port: int, proto: str, process: str = "") -> Optional[set[str]]:
    """Which kinds of sender the firewall lets reach this port; None = firewall unknown. Windows rules can be
    for a program instead of a port ("process")."""
    if not fw.get("known"):
        return None
    if not fw.get("active") or fw.get("default_in") == "allow":
        return {"internet", "lan", "tailscale"}
    allowed: set[str] = set()
    for r in fw["rules"]:
        if r["action"] not in ("ALLOW", "LIMIT"):
            continue
        if r["port"] is not None and r["port"] != port:
            continue
        if r["proto"] and r["proto"] != proto:
            continue
        if r.get("process") and r["process"].lower() != (process or "").lower():
            continue
        allowed |= source_reach(r["from"], r["iface"])
    return allowed


# =====================================================================================  Tailscale
def parse_tailscale(status: Any, prefs: Any = None, serve: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"present": bool(status), "ips": [], "online": False, "shields_up": False, "funnel": []}
    if isinstance(status, dict):
        me = status.get("Self") or {}
        out["ips"] = list(me.get("TailscaleIPs") or [])
        out["online"] = bool(me.get("Online")) or status.get("BackendState") == "Running"
        out["peers"] = len(status.get("Peer") or {})
    if isinstance(prefs, dict):
        out["shields_up"] = bool(prefs.get("ShieldsUp"))
    if isinstance(serve, dict):
        allow = {k for k, v in (serve.get("AllowFunnel") or {}).items() if v}
        for hostport, web in (serve.get("Web") or {}).items():
            if hostport not in allow:
                continue
            for path, h in ((web or {}).get("Handlers") or {}).items():
                m = re.search(r":(\d+)", str(h.get("Proxy") or ""))
                if m:
                    out["funnel"].append({"public": hostport, "path": path, "port": int(m.group(1))})
        for port, tcp in (serve.get("TCP") or {}).items():
            fwd = str((tcp or {}).get("TCPForward") or "")
            m = re.search(r":(\d+)$", fwd)
            if m and any(h.endswith(f":{port}") for h in allow):
                out["funnel"].append({"public": f"port {port}", "path": "", "port": int(m.group(1))})
    return out


# =====================================================================================  the map
def reach_of(listener: dict[str, Any], ifaces: list[dict[str, Any]], fw: dict[str, Any], ts: dict[str, Any]
             ) -> dict[str, Any]:
    bind = classify_ip(listener["address"], listener.get("iface", ""))
    real = [i for i in ifaces if not i["virtual"] and i["kind"] != "local"]
    if bind == "any":
        if listener.get("iface"):
            kinds = {i["kind"] for i in real if i["iface"] == listener["iface"]}
        else:
            kinds = {i["kind"] for i in real}
            if ts.get("ips"):
                kinds.add("tailscale")
    else:
        kinds = {bind}
    if bind == "local" or not kinds:
        reach, why = {"local"}, "only listens on this computer"
    else:
        allowed = firewall_allows(fw, listener["port"], listener["proto"], listener.get("process", ""))
        if allowed is None:
            reach, why = kinds, "firewall rules unknown"
        else:
            reach = kinds & allowed
            why = "firewall lets it through" if reach else "firewall blocks it"
            if not reach:
                reach = {"local"}
        if "tailscale" in reach and ts.get("shields_up"):
            reach.discard("tailscale")
            why += "; Tailscale Shields Up blocks Tailscale"
            reach = reach or {"local"}
    if any(f["port"] == listener["port"] for f in ts.get("funnel") or []):
        reach.add("internet")
        why = "shared to the whole internet with Tailscale Funnel"
    widest = next(k for k in REACH_ORDER if k in reach)
    return {**listener, "bind": "every address" if bind == "any" else REACH_WORDS.get(bind, bind),
            "reach": sorted(reach, key=REACH_ORDER.index), "widest": widest, "why": why}


def build_map(sh: Optional[Shell] = None, ufw_text: Optional[str] = None, machine: str = "") -> dict[str, Any]:
    sh = sh or Shell()
    notes: list[str] = []
    listeners: list[dict[str, Any]] = []
    if sh.which("ss"):
        rc, out = sh.run(["ss", "-tulpnH"], timeout=20)
        listeners = parse_ss(out) if rc == 0 else []
        if rc != 0:
            notes.append("ss couldn't list the open ports.")
    else:
        notes.append("ss isn't installed, so the open ports can't be listed.")
    ifaces: list[dict[str, Any]] = []
    if sh.which("ip"):
        rc, out = sh.run(["ip", "-j", "addr"], timeout=10, stderr=False)
        ifaces = parse_ip_addr(loads(out, []))
    if ufw_text is None:
        ufw_text = sh.read(str(UFW_DUMP))
        if not ufw_text and sh.which("ufw"):
            rc, out = sh.run(["ufw", "status", "verbose"], timeout=10)
            ufw_text = out if rc == 0 else None
    fw = parse_ufw(ufw_text)
    if not fw["known"]:
        notes.append("Orion can't read the firewall rules (they need admin rights; OrionOS copies them every hour), "
                     "so reachability assumes no firewall.")
    ts: dict[str, Any] = {"present": False, "ips": [], "funnel": []}
    if sh.which("tailscale"):
        _, st = sh.run(["tailscale", "status", "--json"], timeout=10, stderr=False)
        _, pr = sh.run(["tailscale", "debug", "prefs"], timeout=10, stderr=False)
        _, sv = sh.run(["tailscale", "serve", "status", "--json"], timeout=10, stderr=False)
        ts = parse_tailscale(loads(st), loads(pr), loads(sv))
    rows = [reach_of(x, ifaces, fw, ts) for x in listeners]
    rows.sort(key=lambda r: (REACH_ORDER.index(r["widest"]), r["port"], r["proto"]))
    return {"machine": machine or machine_name(), "at": round(time.time(), 3), "listeners": rows,
            "interfaces": [i for i in ifaces if not i["virtual"]], "firewall": fw, "tailscale": ts,
            "notes": notes, "source": "this computer"}


def map_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """A map from another machine's own report (os/windows/Report-Listeners.ps1)."""
    ifaces = [{"iface": str(i.get("iface", "")), "address": str(i.get("address", "")),
               "prefix": int(i.get("prefix", 24) or 24), "kind": classify_ip(str(i.get("address", "")),
                                                                              str(i.get("iface", ""))),
               "virtual": bool(VIRTUAL_IFACES.match(str(i.get("iface", ""))) or
                               re.search(r"vEthernet|VirtualBox|VMware|Hyper-V", str(i.get("iface", ""))))}
              for i in report.get("interfaces") or [] if i.get("address")]
    fwr = report.get("firewall") or {}
    fw = {"known": "enabled" in fwr, "active": bool(fwr.get("enabled")),
          "default_in": "allow" if fwr.get("default_inbound") == "allow" else "deny",
          "rules": [{"port": r.get("port"), "proto": (r.get("proto") or "").lower() or None, "iface": "",
                     "from": r.get("from") or "Anywhere", "to": "", "action": "ALLOW", "comment": r.get("name", ""),
                     "process": str(r.get("process") or "")}
                    for r in fwr.get("allow") or []]}
    listeners = _dedupe([{"proto": str(x.get("proto", "tcp")).lower(), "address": str(x.get("address", "")),
                          "iface": "", "port": int(x.get("port", 0)), "process": str(x.get("process", "")),
                          "pid": x.get("pid")} for x in report.get("listeners") or [] if x.get("port")])
    ts = {"present": False, "ips": [i["address"] for i in ifaces if i["kind"] == "tailscale"], "funnel": []}
    rows = [reach_of(x, ifaces, fw, ts) for x in listeners]
    rows.sort(key=lambda r: (REACH_ORDER.index(r["widest"]), r["port"], r["proto"]))
    notes = [] if fw["known"] else ["That machine didn't report its firewall."]
    return {"machine": str(report.get("machine") or "another computer")[:60], "at": round(time.time(), 3),
            "listeners": rows, "interfaces": [i for i in ifaces if not i["virtual"]], "firewall": fw,
            "tailscale": ts, "notes": notes, "source": str(report.get("source") or "reported")[:40]}


# =====================================================================================  changes over time
def key(r: dict[str, Any]) -> str:
    return f"{r['proto']}/{r['port']}/{r.get('process') or '?'}"


def diff(old: Optional[dict[str, Any]], new: dict[str, Any]) -> list[dict[str, Any]]:
    """What changed that matters: a new listener others can reach, or something newly reachable from further
    out. Listeners on this computer only are never reported."""
    if not old:
        return []
    before = {key(r): r for r in old.get("listeners") or []}
    changes = []
    for r in new.get("listeners") or []:
        if r["widest"] == "local" or (r["proto"] == "udp" and r["port"] >= 32768):
            continue                # this computer only, or a short-lived UDP port (browsers, DNS lookups)
        prev = before.get(key(r))
        if prev is None:
            changes.append({"kind": "new", "row": r})
        elif REACH_ORDER.index(r["widest"]) < REACH_ORDER.index(prev.get("widest", "local")):
            changes.append({"kind": "wider", "row": r, "was": prev.get("widest")})
    return changes


def describe_row(r: dict[str, Any]) -> str:
    who = REACH_WORDS[r["widest"]]
    prog = r.get("process") or "an unknown program"
    return f"{prog} on port {r['port']}/{r['proto']}: reachable from {who} ({r['why']})"


def describe_changes(machine: str, changes: list[dict[str, Any]]) -> str:
    lines = []
    for c in changes:
        r = c["row"]
        if c["kind"] == "new":
            lines.append(f"- New: {describe_row(r)}.")
        else:
            lines.append(f"- Now reachable from {REACH_WORDS[r['widest']]} (was {REACH_WORDS.get(c['was'], c['was'])})"
                         f": {r.get('process') or 'a program'} on port {r['port']}/{r['proto']}.")
    return f"On {machine}:\n" + "\n".join(lines)


def answer(m: Optional[dict[str, Any]]) -> str:
    """'What's open on the server?' in plain words."""
    if not m:
        return "I haven't mapped that machine yet."
    rows = m.get("listeners") or []
    outside = [r for r in rows if r["widest"] != "local"]
    local_n = len(rows) - len(outside)
    if not outside:
        text = f"On {m['machine']}, nothing is reachable from other devices."
    else:
        text = f"On {m['machine']}, {plural(len(outside), 'thing')} can be reached from other devices:\n" + \
               "\n".join(f"- {describe_row(r)}" for r in outside[:20])
    if local_n:
        text += f"\n{plural(local_n, 'other service')} only listen on the computer itself (that's fine)."
    if any(r["widest"] == "internet" for r in outside):
        text += "\nAnything reachable from the internet should be something you meant to share."
    if any(r["widest"] == "lan" for r in outside):
        text += "\n(Home network means: and the internet too, if your router forwards that port.)"
    for n in m.get("notes") or []:
        text += f"\nNote: {n}"
    return text


# =====================================================================================  polite LAN scan
def scan_targets(ifaces: list[dict[str, Any]]) -> list[str]:
    """Your own home network(s) only: private IPv4 on a real interface, never more than 1024 addresses."""
    out = []
    for i in ifaces:
        if i.get("virtual") or i["kind"] != "lan" or ":" in i["address"] or i["iface"].startswith("tailscale"):
            continue
        try:
            net = ipaddress.ip_network(f"{i['address']}/{i['prefix']}", strict=False)
        except ValueError:
            continue
        if not net.is_private or net.is_link_local or net.num_addresses > MAX_SCAN_HOSTS or net.prefixlen < 22:
            continue
        if str(net) not in out:
            out.append(str(net))
    return out


def parse_nmap_xml(text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    hosts = []
    for h in root.findall("host"):
        if (h.find("status") is not None and h.find("status").get("state") != "up"):
            continue
        addr = next((a.get("addr") for a in h.findall("address") if a.get("addrtype") == "ipv4"), "")
        name_el = h.find("hostnames/hostname")
        ports = []
        for p in h.findall("ports/port"):
            st = p.find("state")
            if st is None or st.get("state") != "open":
                continue
            svc = p.find("service")
            ports.append({"port": int(p.get("portid", 0)), "proto": p.get("protocol", "tcp"),
                          "service": svc.get("name", "") if svc is not None else ""})
        if addr:
            hosts.append({"address": addr, "name": name_el.get("name", "") if name_el is not None else "",
                          "open": ports})
    return hosts


def lan_scan(sh: Optional[Shell] = None, ifaces: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    sh = sh or Shell()
    nmap = sh.which("nmap")
    if not nmap:
        return {"ok": False, "text": "The home network scan needs nmap (sudo apt install nmap).", "hosts": []}
    if ifaces is None:
        _, out = sh.run(["ip", "-j", "addr"], timeout=10, stderr=False)
        ifaces = parse_ip_addr(loads(out, []))
    targets = scan_targets(ifaces)
    if not targets:
        return {"ok": False, "text": "No home network to scan (Orion only ever scans your own private network).",
                "hosts": []}
    rc, out = sh.run([nmap, "-sT", "--top-ports", "100", "-T3", "--max-rate", "50", "--open", "-oX", "-",
                      *targets], timeout=1800, stderr=False)
    hosts = parse_nmap_xml(out)
    open_hosts = [h for h in hosts if h["open"]]
    lines = [f"Scanned {', '.join(targets)} (the 100 most common ports, gently): "
             f"{plural(len(open_hosts), 'device')} with open ports."]
    for h in open_hosts[:30]:
        ports = ", ".join(f"{p['port']}{' (' + p['service'] + ')' if p['service'] else ''}" for p in h["open"][:10])
        lines.append(f"- {h['name'] or h['address']}: {ports}")
    return {"ok": rc in (0, 1), "at": round(time.time(), 3), "targets": targets, "hosts": hosts,
            "text": "\n".join(lines)}


def lan_diff(old: Optional[dict[str, Any]], new: dict[str, Any]) -> list[str]:
    if not old:
        return []
    before = {(h["address"], p["port"]) for h in old.get("hosts") or [] for p in h["open"]}
    return [f"{h['name'] or h['address']} port {p['port']}{' (' + p['service'] + ')' if p['service'] else ''}"
            for h in new.get("hosts") or [] for p in h["open"] if (h["address"], p["port"]) not in before]


if __name__ == "__main__":   # pragma: no cover - print this machine's map
    import json
    import sys
    m = build_map()
    print(json.dumps(m, indent=1) if "--json" in sys.argv else answer(m))
