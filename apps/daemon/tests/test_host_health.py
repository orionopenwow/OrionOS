"""Host Health (toolkit tool 21): Lynis, AIDE, SMART, battery and brute-force reports -> a score and 3 fixes."""
import json

import host_health as H
from sec_fakes import FIX, FakeShell, fixture


def test_parsers():
    lyn = H.parse_lynis(fixture("lynis-report.dat"))
    assert lyn["score"] == 64 and lyn["tests"] == 262
    assert [w["id"] for w in lyn["warnings"]] == ["FIRE-4512", "PKGS-7392"]
    assert lyn["warnings"][0]["plain"].startswith("Turn the firewall on")
    assert lyn["suggestions"][2]["plain"] == "Some brand new check Orion has no words for."
    assert H.parse_lynis("") is None
    aide = H.parse_aide(fixture("aide-check.txt"))
    assert (aide["changed"], aide["added"], aide["removed"], aide["ok"]) == (2, 1, 0, False)
    assert aide["files"] == ["/etc/cron.d/strange-job", "/etc/passwd", "/usr/bin/sudo"]
    assert H.parse_aide("AIDE found NO differences between database and filesystem. Looks okay!!")["ok"]
    bat = H.parse_upower(fixture("upower.txt"))
    assert bat == {"health": 55.3, "state": "discharging", "cycles": 612}
    nv = H.parse_smart(json.loads(fixture("smart-nvme0n1.json")))
    assert nv["passed"] and nv["wear"] == 3 and nv["problems"] == []
    sda = H.parse_smart(json.loads(fixture("smart-sda.json")))
    assert sda["problems"] == ["24 reallocated sector ct"]
    br = H.parse_bruteforce(fixture("fail2ban-sshd.txt"), False, True)
    assert br == {"on": True, "tool": "fail2ban", "banned_now": 3, "banned_total": 41}


def sh():
    return FakeShell({"upower -e": (0, "/org/freedesktop/UPower/devices/line_power_AC\n"
                                       "/org/freedesktop/UPower/devices/battery_BAT0\n"),
                      "upower -i": (0, fixture("upower.txt")),
                      "systemctl is-active --quiet fail2ban": (0, ""),
                      "systemctl is-active --quiet crowdsec": (3, "")},
                     tools=["upower", "systemctl"])


def test_check_up_on_real_hardware():
    r = H.assess(H.gather(sh(), FIX, vm=False), "laptop")
    # 64 (Lynis) - 15 (disk with bad sectors) - 5 (file watch changes) - 3 (worn battery)
    assert r["score"] == 41 and r["word"] == "needs work"
    assert r["fixes"][0].startswith("Back up now and plan to replace ST2000DM008 (/dev/sda)")
    assert "File watch found (/etc/cron.d/strange-job" in r["fixes"][1]
    assert r["fixes"][2].startswith("Turn the firewall on")
    assert "Battery holds 55% of its original charge." in r["text"]
    assert "Brute-force protection is on (fail2ban, 3 addresses blocked right now)" in r["text"]
    assert r["text"].startswith("laptop: 41/100 (needs work).")


def test_vm_skips_aide_and_disks():
    info = H.gather(sh(), FIX, vm=True)
    assert info["aide"] is None and info["disks"] == []
    r = H.assess(info, "orion-lab")
    assert r["score"] == 61 and r["fixes"][0].startswith("Turn the firewall on")
    assert any("skipped in virtual machines" in n for n in info["notes"])


def test_no_reports_and_no_lynis(tmp_path):
    r = H.check(FakeShell(), tmp_path, vm=False)
    assert r["score"] == 70 and "rough guess" in r["text"] and "Lynis isn't installed" in r["text"]
    assert "Nothing to fix right now." in r["text"]


def test_server_without_bruteforce_protection():
    r = H.assess({"lynis": None, "aide": None, "battery": None, "disks": [], "profile": "server",
                  "bruteforce": {"on": False}, "notes": []}, "server")
    assert r["score"] == 65 and "ORION_BRUTEFORCE=1" in r["fixes"][0]
