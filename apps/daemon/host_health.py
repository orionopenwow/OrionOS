"""Host Health (docs/ORION_TOOLKIT_PLAN.md, tool 21): a weekly security check-up for each machine.

Every Sunday Orion gives each machine a score out of 100 and the three most useful fixes, in plain words.

    Lynis        the security audit (score = Lynis' hardening index, then lowered for the problems below)
    AIDE         file watch: reports changes to important system files (real hardware only; skipped in VMs)
    fail2ban / CrowdSec   brute-force protection for SSH and logins, on the server only and only if you opt in
    upower       battery wear (laptops)
    smartctl     disk health (SMART); skipped in VMs

Lynis, AIDE, smartctl and fail2ban need admin rights. On OrionOS a weekly root timer (orion-security-dump,
installed by os/image/os-security.sh) runs them and leaves the reports in /var/lib/orion/security/, readable by
Orion. Without the timer Orion runs Lynis in its limited non-admin mode, and skips the rest.

"Orion, how healthy is the laptop?"
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from sec_common import Shell, in_vm, loads, machine_name, plural, profile

DUMP_DIR = Path("/var/lib/orion/security")
HARDEN = "sudo bash /opt/orion-src/os/common/harden.sh"

# Lynis test ids -> plain words. Anything not here uses Lynis' own sentence.
LYNIS_PLAIN = {
    "SSH-7408": "Tighten SSH settings (OrionOS's hardening does it: " + HARDEN + ").",
    "SSH-7440": "Limit who may log in over SSH (AllowUsers in /etc/ssh/sshd_config.d/).",
    "AUTH-9230": "Use stronger password hashing rounds (SHA_CRYPT_MIN_ROUNDS in /etc/login.defs).",
    "AUTH-9262": "Install a password strength checker: sudo apt install libpam-pwquality.",
    "AUTH-9286": "Set a maximum password age in /etc/login.defs (PASS_MAX_DAYS).",
    "AUTH-9328": "Make new files private by default: set UMASK 027 in /etc/login.defs.",
    "BOOT-5122": "Set a GRUB password so nobody can change boot options at the keyboard.",
    "PKGS-7392": "Install the waiting security updates: sudo apt update && sudo apt upgrade.",
    "PKGS-7370": "Install debsums to check installed files: sudo apt install debsums.",
    "PKGS-7394": "Install apt-show-versions so updates can be checked: sudo apt install apt-show-versions.",
    "FIRE-4512": "Turn the firewall on: sudo ufw enable (OrionOS sets the rules for you).",
    "FIRE-4590": "Turn the firewall on: sudo ufw enable.",
    "HRDN-7230": "Install a malware scanner if you want one (rkhunter or ClamAV).",
    "HRDN-7222": "Only admins should run compilers: sudo chmod o-x /usr/bin/gcc* /usr/bin/cc.",
    "FINT-4350": "Turn on file watch (AIDE) so changes to system files are noticed.",
    "ACCT-9622": "Turn on process accounting: sudo apt install acct.",
    "ACCT-9626": "Turn on system statistics: sudo apt install sysstat.",
    "ACCT-9628": "Turn on auditd to log security events: sudo apt install auditd.",
    "KRNL-5820": "Disable core dumps (they can leak memory contents): add '* hard core 0' to /etc/security/limits.conf.",
    "KRNL-6000": "Apply the recommended kernel safety settings (sysctl); see Lynis' report for the list.",
    "LOGG-2154": "Send logs to a second machine so an intruder can't wipe them (optional).",
    "MAIL-8818": "Hide the mail server's version banner.",
    "NETW-3200": "Turn off network protocols you don't use (dccp, sctp, rds, tipc).",
    "USB-1000": "Block USB storage if this machine shouldn't take USB drives (optional).",
    "STRG-1846": "Block FireWire storage if you don't use it (optional).",
    "TIME-3104": "Turn on clock sync: sudo timedatectl set-ntp true.",
    "DEB-0280": "Install libpam-tmpdir for private temp folders: sudo apt install libpam-tmpdir.",
    "DEB-0880": "Install fail2ban to slow down password guessing (on servers).",
}


# =====================================================================================  parsers
def parse_lynis(text: Optional[str]) -> Optional[dict[str, Any]]:
    """/var/log/lynis-report.dat -> {score, warnings[], suggestions[], tests}."""
    if not text:
        return None
    score = None
    warnings, suggestions = [], []
    tests = 0
    for line in text.splitlines():
        if line.startswith("hardening_index="):
            try:
                score = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("lynis_tests_done="):
            try:
                tests = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith(("warning[]=", "suggestion[]=")):
            kind, _, rest = line.partition("[]=")
            parts = rest.split("|")
            tid = parts[0].strip()
            text_ = parts[1].strip() if len(parts) > 1 else ""
            detail = parts[2].strip() if len(parts) > 2 and parts[2].strip() not in ("-", "") else ""
            item = {"id": tid, "text": text_, "detail": detail,
                    "plain": LYNIS_PLAIN.get(tid) or (text_.rstrip(".") + ".")}
            (warnings if kind == "warning" else suggestions).append(item)
    if score is None and not warnings and not suggestions:
        return None
    return {"score": score, "warnings": warnings, "suggestions": suggestions, "tests": tests}


def parse_aide(text: Optional[str]) -> Optional[dict[str, Any]]:
    """`aide --check` output -> {changed, added, removed, files[]}; None when there's no report."""
    if not text:
        return None
    if re.search(r"AIDE found NO differences|All files match AIDE database", text, re.I):
        return {"changed": 0, "added": 0, "removed": 0, "files": [], "ok": True}
    if "AIDE" not in text and "Summary" not in text:
        return None

    def n(label: str) -> int:
        m = re.search(rf"{label} entries:\s*(\d+)", text, re.I)
        return int(m.group(1)) if m else 0
    files = re.findall(r"^(?:changed|added|removed):\s*(\S+)", text, re.M)
    files += re.findall(r"^[fdl][ <>=+\-.A-Za-z]{0,20}:\s*(/\S+)", text, re.M)
    seen: list[str] = []
    for f in files:
        if f not in seen:
            seen.append(f)
    out = {"changed": n("Changed"), "added": n("Added"), "removed": n("Removed"), "files": seen[:20]}
    out["ok"] = not (out["changed"] or out["added"] or out["removed"])
    return out


def parse_upower(text: Optional[str]) -> Optional[dict[str, Any]]:
    """`upower -i <battery>` -> {health %, state, cycles}."""
    if not text or "battery" not in text.lower():
        return None

    def num(label: str) -> Optional[float]:
        m = re.search(rf"^\s*{label}:\s*([\d.,]+)", text, re.M)
        return float(m.group(1).replace(",", ".")) if m else None
    cap = num("capacity")
    full, design = num("energy-full"), num("energy-full-design")
    if cap is None and full and design:
        cap = round(full / design * 100, 1)
    if cap is None:
        return None
    st = re.search(r"^\s*state:\s*(\S+)", text, re.M)
    cyc = num("charge-cycles")
    return {"health": round(cap, 1), "state": st.group(1) if st else "", "cycles": int(cyc) if cyc else None}


def parse_smart(data: Any) -> Optional[dict[str, Any]]:
    """`smartctl -j -H -A /dev/X` -> {device, model, passed, wear %, problems[]}."""
    if not isinstance(data, dict) or "device" not in data:
        return None
    dev = (data.get("device") or {}).get("name", "")
    model = data.get("model_name") or data.get("model_family") or ""
    passed = (data.get("smart_status") or {}).get("passed")
    problems: list[str] = []
    wear = None
    nv = data.get("nvme_smart_health_information_log") or {}
    if nv:
        wear = nv.get("percentage_used")
        if nv.get("media_errors"):
            problems.append(f"{nv['media_errors']} media errors")
        if nv.get("critical_warning"):
            problems.append("the drive reports a critical warning")
        if (nv.get("available_spare") is not None and nv.get("available_spare_threshold") is not None
                and nv["available_spare"] <= nv["available_spare_threshold"]):
            problems.append("spare space is used up")
    for a in (data.get("ata_smart_attributes") or {}).get("table") or []:
        raw = (a.get("raw") or {}).get("value", 0) or 0
        if a.get("id") in (5, 197, 198) and raw > 0:
            problems.append(f"{raw} {a.get('name', 'bad sectors').replace('_', ' ').lower()}")
        if a.get("id") in (177, 231, 233) and a.get("value") is not None and wear is None:
            wear = max(0, 100 - int(a["value"]))
    if passed is False:
        problems.insert(0, "the drive's own health check FAILED")
    return {"device": dev, "model": model, "passed": passed, "wear": wear, "problems": problems}


def parse_bruteforce(fail2ban: Optional[str], crowdsec_active: bool, fail2ban_active: bool) -> dict[str, Any]:
    banned = None
    if fail2ban:
        m = re.search(r"Currently banned:\s*(\d+)", fail2ban)
        banned = int(m.group(1)) if m else None
        t = re.search(r"Total banned:\s*(\d+)", fail2ban)
        total = int(t.group(1)) if t else None
    else:
        total = None
    tool = "CrowdSec" if crowdsec_active else "fail2ban" if fail2ban_active else ""
    return {"on": bool(tool), "tool": tool, "banned_now": banned, "banned_total": total}


# =====================================================================================  gathering
def gather(sh: Optional[Shell] = None, dump_dir: Path = DUMP_DIR, vm: Optional[bool] = None) -> dict[str, Any]:
    sh = sh or Shell()
    vm = in_vm(sh) if vm is None else vm
    notes: list[str] = []
    dump = Path(dump_dir)
    lynis = parse_lynis(sh.read(str(dump / "lynis-report.dat")))
    if lynis is None and sh.which("lynis"):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="orion-lynis-") as tmp:
            rep = Path(tmp) / "report.dat"
            sh.run([sh.which("lynis") or "lynis", "audit", "system", "--quick", "--no-colors", "--pentest",
                    "--report-file", str(rep), "--logfile", str(Path(tmp) / "lynis.log")], timeout=1200)
            lynis = parse_lynis(sh.read(str(rep)))
        if lynis:
            notes.append("Lynis ran without admin rights, so some checks were skipped.")
    if lynis is None:
        notes.append("Lynis isn't installed (sudo apt install lynis), so there's no full audit.")
    aide = None
    if vm:
        notes.append("File watch (AIDE) is skipped in virtual machines.")
    else:
        aide = parse_aide(sh.read(str(dump / "aide-check.txt")))
    battery = None
    if sh.which("upower"):
        _, devs = sh.run(["upower", "-e"], timeout=10)
        bat = next((d.strip() for d in devs.splitlines() if "battery_BAT" in d or "/battery_" in d), None)
        if bat:
            _, info = sh.run(["upower", "-i", bat], timeout=10)
            battery = parse_upower(info)
    disks: list[dict[str, Any]] = []
    if not vm:
        for f in sorted(dump.glob("smart-*.json")) if dump.is_dir() else []:
            d = parse_smart(loads(sh.read(str(f)) or ""))
            if d:
                disks.append(d)
    fb_active = sh.run(["systemctl", "is-active", "--quiet", "fail2ban"], timeout=10)[0] == 0 \
        if sh.which("systemctl") else False
    cs_active = sh.run(["systemctl", "is-active", "--quiet", "crowdsec"], timeout=10)[0] == 0 \
        if sh.which("systemctl") else False
    brute = parse_bruteforce(sh.read(str(dump / "fail2ban-sshd.txt")), cs_active, fb_active)
    stamp = None
    try:
        stamp = (dump / "lynis-report.dat").stat().st_mtime
    except OSError:
        pass
    return {"lynis": lynis, "aide": aide, "battery": battery, "disks": disks, "bruteforce": brute, "vm": vm,
            "profile": profile(), "notes": notes, "audit_at": stamp}


# =====================================================================================  score and words
def assess(info: dict[str, Any], machine: str = "") -> dict[str, Any]:
    """Score out of 100 and the three most useful fixes, worst first."""
    fixes: list[tuple[int, str]] = []        # (priority, plain words); lower = more urgent
    lyn = info.get("lynis")
    score = lyn["score"] if lyn and lyn.get("score") is not None else 70
    for d in info.get("disks") or []:
        name = f"{d.get('model') or 'the disk'} ({d['device']})"
        if d["passed"] is False or d["problems"]:
            score -= 15
            fixes.append((0, f"Back up now and plan to replace {name}: {', '.join(d['problems']) or 'it reports problems'}."))
        elif d.get("wear") is not None and d["wear"] >= 90:
            score -= 5
            fixes.append((2, f"{name} is {d['wear']}% worn; plan a replacement."))
    aide = info.get("aide")
    if aide and not aide.get("ok"):
        score -= 5
        what = ", ".join(aide["files"][:3]) or "system files"
        fixes.append((1, f"Check the system file changes File watch found ({what}). If you installed updates or "
                         "changed settings, it's expected; then run: sudo aide --update."))
    bat = info.get("battery")
    if bat and bat["health"] < 60:
        score -= 3
        fixes.append((3, f"The battery holds {bat['health']:.0f}% of its original charge; consider replacing it."))
    br = info.get("bruteforce") or {}
    if info.get("profile") == "server" and not br.get("on"):
        score -= 5
        fixes.append((1, "Turn on brute-force protection for SSH: run os/image/os-security.sh with "
                         "ORION_BRUTEFORCE=1 (installs fail2ban)."))
    if lyn:
        for w in lyn["warnings"]:
            fixes.append((1, w["plain"]))
        ranked = sorted(lyn["suggestions"], key=lambda s: (s["id"] not in LYNIS_PLAIN, s["id"]))
        for s in ranked:
            fixes.append((4 if s["id"] in LYNIS_PLAIN else 5, s["plain"]))
    seen: list[str] = []
    for _, text in sorted(fixes, key=lambda x: x[0]):
        if text not in seen:
            seen.append(text)
    score = max(0, min(100, int(score)))
    word = "good" if score >= 75 else "fair" if score >= 60 else "needs work"
    machine = machine or machine_name()
    parts = [f"{machine}: {score}/100 ({word})."]
    if lyn is None:
        parts.append("(No Lynis audit yet, so the score is a rough guess.)")
    if bat:
        parts.append(f"Battery holds {bat['health']:.0f}% of its original charge.")
    for d in info.get("disks") or []:
        ok = d["passed"] is not False and not d["problems"]
        worn = f", {d['wear']}% worn" if d.get("wear") is not None else ""
        parts.append(f"Disk {d['device']}: {'healthy' if ok else 'has problems'}{worn}.")
    if aide:
        parts.append("File watch: no changes." if aide.get("ok") else
                     f"File watch: {plural(aide['changed'], 'file')} changed, {aide['added']} added, "
                     f"{aide['removed']} removed.")
    if br.get("on"):
        parts.append(f"Brute-force protection is on ({br['tool']}"
                     + (f", {plural(br['banned_now'], 'address', 'addresses')} blocked right now" if br.get("banned_now")
                        else "") + ").")
    text = " ".join(parts)
    top = seen[:3]
    if top:
        text += "\nThe most useful fixes:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(top, 1))
    else:
        text += "\nNothing to fix right now."
    for n in info.get("notes") or []:
        text += f"\nNote: {n}"
    return {"machine": machine, "score": score, "word": word, "fixes": top, "all_fixes": len(seen), "text": text,
            "at": round(time.time(), 3), "info": info}


def check(sh: Optional[Shell] = None, dump_dir: Path = DUMP_DIR, vm: Optional[bool] = None) -> dict[str, Any]:
    return assess(gather(sh, dump_dir, vm))


if __name__ == "__main__":   # pragma: no cover
    import sys
    r = check()
    print(json.dumps(r, indent=1, default=str) if "--json" in sys.argv else r["text"])
