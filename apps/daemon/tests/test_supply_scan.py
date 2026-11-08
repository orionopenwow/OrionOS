"""Supply-Chain Scan (toolkit tool 19): each tool's JSON parsed, merged and explained in plain words."""
import json
import os
import time

import supply_scan as S
from sec_fakes import FIX, FakeShell, fixture


def load(name):
    return json.loads(fixture(name))


def test_osv_groups_aliases_fix_and_call_analysis():
    found = S.parse_osv(load("osv.json"))
    assert len(found) == 2
    t = next(f for f in found if f["package"] == "time")
    assert t["id"] == "GHSA-wcg3-cvx6-7396" and "RUSTSEC-2020-0071" in t["aliases"]
    assert t["fixed"] == "0.2.23" and t["called"] is False and t["severity"] == "medium"
    o = next(f for f in found if f["package"] == "openssl")
    assert o["called"] is True and o["severity"] == "high" and o["fixed"] == "0.10.60"


def test_cargo_pip_npm_grype_parsers():
    ca = S.parse_cargo_audit(load("cargo_audit.json"))
    assert [(f["package"], f["fixed"]) for f in ca] == [("time", "0.2.23"), ("h2", "0.3.24")]
    pa = S.parse_pip_audit(load("pip_audit.json"), "requirements.txt")
    assert pa[0]["package"] == "requests" and pa[0]["fixed"] == "2.31.0" and pa[0]["ecosystem"] == "PyPI"
    assert S.parse_pip_audit(load("pip_audit.json")["dependencies"])[0]["id"] == "PYSEC-2023-74"   # older list format
    na = S.parse_npm_audit(load("npm_audit.json"))
    assert {f["package"] for f in na} == {"semver", "vite"}          # make-dir is only affected through semver
    semver = next(f for f in na if f["package"] == "semver")
    assert semver["fixed"] == "5.7.2" and semver["severity"] == "medium" and semver["id"] == "GHSA-c2qf-rxjj-qqgw"
    vite = next(f for f in na if f["package"] == "vite")
    assert "major update" in vite["fix_note"] and vite["severity"] == "high"
    g = S.parse_grype(load("grype.json"))
    assert [(f["package"], f["severity"], f["fixed"]) for f in g] == [
        ("openssh-server", "high", "1:9.6p1-3ubuntu13.3"), ("libfoo1", "critical", None), ("bash", "low", "2.0")]


def test_merge_shows_the_same_hole_once_and_explains_it():
    found = S.parse_osv(load("osv.json")) + S.parse_cargo_audit(load("cargo_audit.json"))
    for f in found:
        f["project"] = "openwow"
    merged = S.merge(found)
    time_ = [m for m in merged if m["package"] == "time"]
    assert len(time_) == 1 and time_[0]["tools"] == ["cargo-audit", "osv-scanner"]
    assert merged[0]["package"] == "openssl"          # serious first
    words = S.explain(time_[0])
    assert "time 0.1.43 in openwow" in words and "doesn't seem to call" in words and "0.2.23 or newer" in words
    assert "does call the affected part" in S.explain(merged[0])
    text = S.summary_text(merged, ["openwow"], ["cargo-audit", "osv-scanner"])
    assert text.startswith("Checked openwow with cargo-audit, osv-scanner: 3 known holes")
    assert "No checking tools" in S.summary_text([], ["x"], [])


def test_scan_project_runs_the_installed_tools(tmp_path):
    p = tmp_path / "openwow"
    p.mkdir()
    (p / "Cargo.lock").write_text("# lock")
    (p / "requirements.txt").write_text("requests==2.25.0\n")
    web = p / "web"
    web.mkdir()
    (web / "package-lock.json").write_text("{}")
    (p / "node_modules").mkdir()
    (p / "node_modules" / "package-lock.json").write_text("{}")     # never looked at
    sh = FakeShell({"osv-scanner scan source": (1, fixture("osv.json")),
                    "cargo-audit audit --json": (1, fixture("cargo_audit.json")),
                    "npm audit --json": (1, fixture("npm_audit.json")),
                    "pip-audit -f json": (1, fixture("pip_audit.json"))},
                   tools=["osv-scanner", "cargo-audit", "npm", "pip-audit"])
    r = S.scan([p], sh)
    assert r["tools"] == ["cargo-audit", "npm audit", "osv-scanner", "pip-audit"]
    assert r["serious"] == 2 and all(f["project"] == "openwow" for f in r["findings"])
    assert sum(1 for c in sh.calls if c[0] == "npm") == 1
    assert ["osv-scanner", "scan", "source", "-r", "--format", "json", "--call-analysis=all", str(p)] in sh.calls
    nothing = S.scan([p], FakeShell())
    assert nothing["tools"] == [] and "No checking tools" in nothing["summary"]


def test_image_verdict(tmp_path):
    assert S.image_verdict(None)["safe"] is None
    bad = tmp_path / "orionos-1.0.0-standard-vulns.json"
    bad.write_text(fixture("grype.json"))
    v = S.image_verdict(S.newest_image_report([tmp_path]))
    assert v["safe"] is False and v["blocking"] == 1 and "rebuild" in v["answer"] and "openssh-server" in v["answer"]
    assert "no fix published yet" in v["answer"]
    good = tmp_path / "orionos-1.0.1-standard-vulns.json"
    good.write_text((FIX / "grype.json").read_text().replace('"state": "fixed"', '"state": "not-fixed"'))
    os.utime(bad, (time.time() - 100, time.time() - 100))
    v = S.image_verdict(S.newest_image_report([tmp_path]))
    assert v["safe"] is True and v["answer"].startswith("Yes.")
