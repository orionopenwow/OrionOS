"""Supply-Chain Scan (docs/ORION_TOOLKIT_PLAN.md, tool 19): known holes in what you depend on.

Daily, Orion checks each project's Rust crates, Python and npm packages against the public advisory databases,
using whichever of these tools are installed (none is required; provisioning installs osv-scanner):

    osv-scanner   every lockfile it knows; with call analysis it also says whether your code really calls the
                  broken part (Go and Rust)                       os/image/os-security.sh installs it (pinned)
    cargo-audit   Cargo.lock                                      cargo install cargo-audit
    pip-audit     requirements*.txt, or the project's .venv        pipx install pip-audit
    npm audit     package-lock.json                               comes with npm

Findings from every tool are merged (the same advisory found twice is shown once) and explained in plain words:
what is affected, whether your code uses it (when a tool could tell), and the version that fixes it.
New serious findings reach your phone at once and start Dependency Autopilot for that project; everything else
goes into one summary card a week.

The OrionOS image: os/image/scan-image.sh (called by build.sh when syft and grype are installed) writes an SBOM
and a grype report next to the image. "Orion, is the USB image safe to ship?" reads the newest report.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from sec_common import Shell, loads, plural

SEVERITIES = ("critical", "high", "medium", "low", "unknown")
SERIOUS = ("critical", "high")
LOCKFILES = ("Cargo.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt", "poetry.lock",
             "uv.lock", "Pipfile.lock", "go.sum", "composer.lock", "Gemfile.lock")
SKIP_DIRS = {"node_modules", "target", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".tox"}


# =====================================================================================  severity
def severity_from_score(score: Any) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    return "critical" if s >= 9 else "high" if s >= 7 else "medium" if s >= 4 else "low" if s > 0 else "unknown"


def norm_severity(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("moderate",):
        return "medium"
    if s in SEVERITIES:
        return s
    if s in ("negligible", "info"):
        return "low"
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return severity_from_score(s)
    return "unknown"


def worst(a: str, b: str) -> str:
    return a if SEVERITIES.index(a) <= SEVERITIES.index(b) else b


def finding(tool: str, id_: str, package: str, version: str, ecosystem: str, severity: str, summary: str,
            fixed: Optional[str], called: Optional[bool] = None, aliases: Iterable[str] = (), file: str = "",
            url: str = "") -> dict[str, Any]:
    return {"tool": tool, "id": id_, "aliases": sorted({a for a in aliases if a and a != id_}),
            "package": package, "version": version, "ecosystem": ecosystem, "severity": severity,
            "summary": (summary or "").strip().split("\n")[0][:240], "fixed": fixed or None, "called": called,
            "file": file, "url": url}


# =====================================================================================  parsers (one per tool)
def _osv_fixed(vuln: dict[str, Any], pkg: str) -> Optional[str]:
    fixes = []
    for aff in vuln.get("affected") or []:
        name = ((aff.get("package") or {}).get("name") or pkg)
        if name != pkg:
            continue
        for r in aff.get("ranges") or []:
            for ev in r.get("events") or []:
                if ev.get("fixed"):
                    fixes.append(str(ev["fixed"]))
    return _lowest(fixes)


def _lowest(versions: list[str]) -> Optional[str]:
    if not versions:
        return None

    def key(v: str) -> tuple:
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v.lstrip("v"))[:4])
    return sorted(set(versions), key=key)[0]


def parse_osv(data: Any) -> list[dict[str, Any]]:
    """osv-scanner --format json (v1 and v2), with or without call analysis."""
    out: list[dict[str, Any]] = []
    for res in (data or {}).get("results") or []:
        src = str(((res.get("source") or {}).get("path")) or "")
        for p in res.get("packages") or []:
            pk = p.get("package") or {}
            name, ver, eco = str(pk.get("name", "")), str(pk.get("version", "")), str(pk.get("ecosystem", ""))
            groups = p.get("groups") or []
            vulns = {v.get("id"): v for v in p.get("vulnerabilities") or []}
            seen: set[str] = set()
            for g in groups or [{"ids": [i]} for i in vulns]:
                ids = [i for i in g.get("ids") or [] if i]
                if not ids:
                    continue
                main = next((i for i in ids if i.startswith(("GHSA", "RUSTSEC", "PYSEC", "GO-"))), ids[0])
                v = vulns.get(main) or next((vulns[i] for i in ids if i in vulns), {})
                analysis = g.get("experimentalAnalysis") or g.get("analysis") or {}
                called: Optional[bool] = None
                if isinstance(analysis, dict) and analysis:
                    calls = [a.get("called") for a in analysis.values() if isinstance(a, dict) and "called" in a]
                    if calls:
                        called = any(calls)
                sev = severity_from_score(g.get("max_severity")) if g.get("max_severity") else \
                    norm_severity((v.get("database_specific") or {}).get("severity"))
                aliases = set(ids) | set(g.get("aliases") or []) | set(v.get("aliases") or [])
                seen |= set(ids)
                out.append(finding("osv-scanner", main, name, ver, eco, sev, v.get("summary") or v.get("details", ""),
                                   _osv_fixed(v, name), called, aliases, src,
                                   f"https://osv.dev/{main}"))
    return out


def parse_cargo_audit(data: Any, file: str = "Cargo.lock") -> list[dict[str, Any]]:
    out = []
    for v in ((data or {}).get("vulnerabilities") or {}).get("list") or []:
        adv, pk = v.get("advisory") or {}, v.get("package") or {}
        patched = [re.sub(r"^[^\d]*", "", p.split(",")[0]) for p in (v.get("versions") or {}).get("patched") or []]
        cvss = adv.get("cvss")
        sev = "unknown"
        if isinstance(cvss, (int, float)):
            sev = severity_from_score(cvss)
        elif isinstance(adv.get("severity"), str):
            sev = norm_severity(adv["severity"])
        out.append(finding("cargo-audit", adv.get("id", ""), pk.get("name", ""), str(pk.get("version", "")),
                           "crates.io", sev, adv.get("title") or adv.get("description", ""), _lowest(patched),
                           None, adv.get("aliases") or [], file, adv.get("url") or ""))
    return out


def parse_pip_audit(data: Any, file: str = "") -> list[dict[str, Any]]:
    deps = data.get("dependencies") if isinstance(data, dict) else data
    out = []
    for d in deps or []:
        for v in d.get("vulns") or []:
            out.append(finding("pip-audit", v.get("id", ""), d.get("name", ""), str(d.get("version", "")), "PyPI",
                               norm_severity(v.get("severity")) if v.get("severity") else "unknown",
                               v.get("description", ""), _lowest([str(x) for x in v.get("fix_versions") or []]),
                               None, v.get("aliases") or [], file))
    return out


def parse_npm_audit(data: Any, file: str = "package-lock.json") -> list[dict[str, Any]]:
    """npm audit --json (npm 7+). Packages flagged only because something they use is affected are folded into
    that package's finding."""
    out = []
    for name, v in ((data or {}).get("vulnerabilities") or {}).items():
        via = [x for x in v.get("via") or [] if isinstance(x, dict)]
        if not via:
            continue            # only affected through another package; that one has its own entry
        fix = v.get("fixAvailable")
        for adv in via:
            url = str(adv.get("url") or "")
            gid = url.rsplit("/", 1)[-1] if "/advisories/" in url else f"npm-{adv.get('source', '')}"
            fixed = None
            rng = str(adv.get("range") or v.get("range") or "")
            m = re.match(r"^<\s*(\d[\w.\-]*)$", rng.strip())
            if m:
                fixed = m.group(1)
            elif isinstance(fix, dict) and fix.get("name") == name:
                fixed = str(fix.get("version") or "") or None
            f = finding("npm audit", gid, adv.get("name") or name, "", "npm", norm_severity(adv.get("severity")),
                        adv.get("title", ""), fixed, None, [], file, url)
            f["range"] = rng
            if isinstance(fix, dict) and fix.get("isSemVerMajor"):
                f["fix_note"] = f"needs {fix.get('name')} {fix.get('version')} (a major update)"
            elif fix is False:
                f["fix_note"] = "no fix published yet"
            out.append(f)
    return out


def parse_grype(data: Any, file: str = "") -> list[dict[str, Any]]:
    out = []
    for m in (data or {}).get("matches") or []:
        v, a = m.get("vulnerability") or {}, m.get("artifact") or {}
        fix = v.get("fix") or {}
        fixed = _lowest([str(x) for x in fix.get("versions") or []]) if fix.get("state") == "fixed" else None
        rel = [r.get("id") for r in m.get("relatedVulnerabilities") or [] if r.get("id")]
        out.append(finding("grype", v.get("id", ""), a.get("name", ""), str(a.get("version", "")),
                           a.get("type", ""), norm_severity(v.get("severity")), v.get("description", ""), fixed,
                           None, rel, file, v.get("dataSource", "")))
    return out


# =====================================================================================  merging and words
def merge(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (package, version, advisory), whichever tools reported it."""
    merged: list[dict[str, Any]] = []
    for f in findings:
        ids = {f["id"], *f["aliases"]}
        hit = next((m for m in merged if m["package"].lower() == f["package"].lower()
                    and (not m["version"] or not f["version"] or m["version"] == f["version"])
                    and ids & {m["id"], *m["aliases"]}), None)
        if hit is None:
            merged.append({**f, "tools": [f["tool"]]})
            continue
        hit["tools"] = sorted(set(hit["tools"]) | {f["tool"]})
        hit["aliases"] = sorted(({*hit["aliases"], *ids}) - {hit["id"]})
        hit["severity"] = worst(hit["severity"], f["severity"]) if f["severity"] != "unknown" else hit["severity"]
        if hit["severity"] == "unknown":
            hit["severity"] = f["severity"]
        hit["version"] = hit["version"] or f["version"]
        hit["fixed"] = hit["fixed"] or f["fixed"]
        if hit.get("called") is None and f.get("called") is not None:
            hit["called"] = f["called"]
        if len(f["summary"]) > len(hit["summary"]) and not hit["summary"]:
            hit["summary"] = f["summary"]
    for m in merged:
        m["key"] = hashlib.sha256(f"{m.get('project', '')}|{m['package']}|{m['version']}|{m['id']}".encode()
                                  ).hexdigest()[:12]
    merged.sort(key=lambda m: (SEVERITIES.index(m["severity"]), m.get("called") is False, m["package"]))
    return merged


SEV_WORDS = {"critical": "a critical hole", "high": "a serious hole", "medium": "a moderate hole",
             "low": "a minor hole", "unknown": "a known hole"}


def explain(f: dict[str, Any]) -> str:
    """One finding in plain words."""
    ver = f" {f['version']}" if f.get("version") else ""
    where = f" in {f['project']}" if f.get("project") else ""
    head = f"{f['package']}{ver}{where} has {SEV_WORDS[f['severity']]} ({f['id']})"
    what = f": {f['summary'].rstrip('.')}." if f.get("summary") else "."
    if f.get("called") is True:
        use = " Your code does call the affected part, so fix this one soon."
    elif f.get("called") is False:
        use = " Your code doesn't seem to call the affected part (osv-scanner checked), so it's lower risk."
    else:
        use = ""
    if f.get("fixed"):
        fix = f" Fix: update {f['package']} to {f['fixed']} or newer."
    elif f.get("fix_note"):
        fix = f" Fix: {f['fix_note']}."
    else:
        fix = " There's no fixed version yet; watch for one."
    return head + what + use + fix


def summary_text(findings: list[dict[str, Any]], projects: list[str], tools: list[str]) -> str:
    names = ", ".join(projects) or "no projects"
    if not tools:
        return ("No checking tools are installed yet, so nothing was checked. Install osv-scanner (OrionOS does "
                "this for you) or cargo-audit / pip-audit; npm audit comes with npm.")
    if not findings:
        return f"Checked {names} with {', '.join(tools)}. No known holes."
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    parts = [f"{counts[s]} {s}" for s in SEVERITIES if counts[s]]
    fixable = sum(1 for f in findings if f.get("fixed"))
    lines = [f"Checked {names} with {', '.join(tools)}: {plural(len(findings), 'known hole')} "
             f"({', '.join(parts)}); {fixable} can be fixed by updating.", ""]
    lines += [f"- {explain(f)}" for f in findings[:25]]
    if len(findings) > 25:
        lines.append(f"... and {len(findings) - 25} more.")
    return "\n".join(lines)


# =====================================================================================  running the tools
def lockfiles(project: Path, depth: int = 3) -> list[Path]:
    out: list[Path] = []

    def walk(d: Path, level: int) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".") and level < depth:
                walk(e, level + 1)
            elif e.is_file() and (e.name in LOCKFILES or re.fullmatch(r"requirements[\w.-]*\.txt", e.name)):
                out.append(e)
    walk(Path(project), 0)
    return out


def tools_available(sh: Shell) -> dict[str, Optional[str]]:
    return {"osv-scanner": sh.which("osv-scanner"), "cargo-audit": sh.which("cargo-audit"), "pip-audit": sh.which("pip-audit"),
            "npm": sh.which("npm")}


def scan_project(project: Path, sh: Optional[Shell] = None, call_analysis: bool = True) -> dict[str, Any]:
    """Run every installed tool on one project; returns {findings, tools, notes}."""
    sh = sh or Shell()
    project = Path(project)
    have = tools_available(sh)
    found: list[dict[str, Any]] = []
    used: list[str] = []
    notes: list[str] = []
    locks = lockfiles(project)
    if have["osv-scanner"] and locks:
        argv = [have["osv-scanner"], "scan", "source", "-r", "--format", "json"]
        if call_analysis:
            argv.append("--call-analysis=all")
        rc, out = sh.run(argv + [str(project)], timeout=900, stderr=False)
        data = loads(out)
        if data is None and rc not in (0, 1):      # osv-scanner v1 has no "scan source"
            rc, out = sh.run([have["osv-scanner"], "--format", "json", "-r", str(project)], timeout=900, stderr=False)
            data = loads(out)
        if isinstance(data, dict):
            found += parse_osv(data)
            used.append("osv-scanner")
        else:
            notes.append("osv-scanner didn't give a readable answer.")
    for lock in locks:
        rel = str(lock.relative_to(project))
        if lock.name == "Cargo.lock" and have["cargo-audit"]:
            rc, out = sh.run([have["cargo-audit"], "audit", "--json", "--file", str(lock)], cwd=str(lock.parent),
                             timeout=600, stderr=False)
            data = loads(out)
            if isinstance(data, dict):
                found += parse_cargo_audit(data, rel)
                used.append("cargo-audit")
        elif lock.name == "package-lock.json" and have["npm"]:
            rc, out = sh.run([have["npm"], "audit", "--json", "--package-lock-only"], cwd=str(lock.parent),
                             timeout=600, stderr=False)
            data = loads(out)
            if isinstance(data, dict) and "vulnerabilities" in data:
                found += parse_npm_audit(data, rel)
                used.append("npm audit")
        elif lock.name.startswith("requirements") and have["pip-audit"]:
            rc, out = sh.run([have["pip-audit"], "-f", "json", "--progress-spinner", "off", "-r", str(lock)],
                             cwd=str(project), timeout=600, stderr=False)
            data = loads(out)
            if data is not None:
                found += parse_pip_audit(data, rel)
                used.append("pip-audit")
    if not locks:
        notes.append("No lockfiles found (Cargo.lock, package-lock.json, requirements.txt, ...).")
    for f in found:
        f["project"] = project.name
        f["project_path"] = str(project)
    return {"findings": found, "tools": sorted(set(used)), "notes": notes}


def scan(projects: list[Path], sh: Optional[Shell] = None, call_analysis: bool = True) -> dict[str, Any]:
    started = time.time()
    found: list[dict[str, Any]] = []
    tools: set[str] = set()
    notes: list[str] = []
    for p in projects:
        r = scan_project(p, sh, call_analysis)
        found += r["findings"]
        tools |= set(r["tools"])
        notes += [f"{Path(p).name}: {n}" for n in r["notes"]]
    merged: list[dict[str, Any]] = []
    for name in sorted({f["project"] for f in found}):
        merged += merge([f for f in found if f["project"] == name])
    merged.sort(key=lambda m: (SEVERITIES.index(m["severity"]), m.get("called") is False, m["project"]))
    names = [Path(p).name for p in projects]
    return {"at": round(started, 3), "seconds": round(time.time() - started, 1), "projects": names,
            "tools": sorted(tools), "findings": merged, "notes": notes,
            "summary": summary_text(merged, names, sorted(tools)),
            "serious": sum(1 for f in merged if f["severity"] in SERIOUS)}


# =====================================================================================  the OrionOS image
def image_dirs(repo_root: Path) -> list[Path]:
    env = os.environ.get("ORION_IMAGE_OUT")
    dirs = [Path(env)] if env else []
    return dirs + [Path(repo_root) / "os" / "image" / "out", Path(repo_root) / "dist"]


def newest_image_report(dirs: list[Path]) -> Optional[Path]:
    reports = []
    for d in dirs:
        try:
            reports += list(Path(d).glob("orionos-*-vulns.json"))
        except OSError:
            pass
    return max(reports, key=lambda p: p.stat().st_mtime) if reports else None


def image_verdict(report: Optional[Path]) -> dict[str, Any]:
    """'Is the USB image safe to ship?' Safe = no critical or serious hole that already has a fix (those mean
    the image was built from stale packages). Serious holes with no fix yet are listed but don't block."""
    if report is None:
        return {"safe": None, "answer": "I can't tell yet: no image scan found. Build the image with syft and grype "
                                        "installed (os/image/scan-image.sh runs them), then ask again."}
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"safe": None, "answer": f"The image scan {report.name} couldn't be read."}
    found = merge(parse_grype(data, report.name))
    blocking = [f for f in found if f["severity"] in SERIOUS and f.get("fixed")]
    waiting = [f for f in found if f["severity"] in SERIOUS and not f.get("fixed")]
    age_h = (time.time() - report.stat().st_mtime) / 3600
    when = f"{int(age_h)} hours ago" if age_h < 48 else f"{int(age_h / 24)} days ago"
    image = report.name.replace("-vulns.json", "")
    if blocking:
        lines = [f"Not yet. The {image} image (scanned {when}) has {plural(len(blocking), 'serious hole')} that "
                 "already have fixes, so rebuild it to pick up the updated packages:"]
        lines += [f"- {explain(f)}" for f in blocking[:10]]
        safe = False
    else:
        lines = [f"Yes. The {image} image (scanned {when}) has no serious holes with a fix available."]
        safe = True
    if waiting:
        lines.append(f"{plural(len(waiting), 'serious hole')} with no fix published yet "
                     f"({', '.join(sorted({f['package'] for f in waiting})[:6])}); nothing to do but wait.")
    if age_h > 7 * 24:
        lines.append("The scan is over a week old; rebuild or re-scan for a fresh answer.")
    return {"safe": safe, "answer": "\n".join(lines), "report": str(report), "blocking": len(blocking),
            "waiting": len(waiting), "total": len(found)}
