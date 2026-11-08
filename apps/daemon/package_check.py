"""Package Reality Check (toolkit tool 7): catch made-up and look-alike packages before they are installed.

AI models sometimes suggest packages that don't exist, and attackers register those names (or near-misses of
popular ones). Every new dependency an agent adds is checked:

  * does it exist on the registry (PyPI, npm, crates.io)?
  * how old is it, how many downloads does it have (npm and crates.io publish this; PyPI doesn't), who publishes it?
  * is its name one or two letters off a popular package? (package_top_lists.json, bundled so this works
    offline; rebuild it with scripts/package-check/update_top_lists.py)

Suspicious ones are blocked and an approval card explains why in plain words. Approving the card means "I trust
these packages": the agent can try again. Packages on the popular list pass without a network call.

Where it runs
  * MCP tools: runner_start / grimoire_run commands and git_commit's staged manifests (agent_safety.precheck)
  * the runner: every command Orion starts (ProcessRunner.start_checks), so approved runs are checked too
  * Proof Gate: manifests and lockfiles changed by the agent's branch
  * git pre-commit (.githooks/pre-commit -> scripts/package-check/pre-commit.mjs), next to Secret Shield

Registry lookups use httpx with short timeouts and a one-day cache. When the registries can't be reached the
answer is "couldn't check", and the setting `offline` decides: "ask" (default, blocked until you approve) or
"allow".
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import subprocess
import time
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from fastapi import APIRouter, Request  # Request must be a module name: annotations are strings here
from fastapi.responses import JSONResponse

log = logging.getLogger("orion.package_check")

TOP_LISTS_PATH = Path(__file__).resolve().with_name("package_top_lists.json")
ECOSYSTEMS = ("pypi", "npm", "crates")
REGISTRY_NAME = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io"}
CACHE_TTL_S = 24 * 3600
MISS_TTL_S = 3600            # "doesn't exist" is cached for less time: it may get published
LOOKUP_TIMEOUT_S = 6.0
MAX_PACKAGES = 150           # per check; the rest are listed as "not checked"
CONCURRENCY = 8
DAY = 86400.0
UA = "orion-package-reality-check/1.0"
CARD_KIND = "package_check"

# ===================================================================================== names
def norm_name(eco: str, name: str) -> str:
    name = (name or "").strip()
    if eco == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    if eco == "crates":
        return name.lower().replace("_", "-")
    return name.lower()


def label(eco: str, name: str) -> str:
    return f"{name} ({REGISTRY_NAME.get(eco, eco)})"


_TOP: dict[str, Any] = {}


def top_lists(path: Optional[Path] = None) -> dict[str, list[str]]:
    p = Path(path or TOP_LISTS_PATH)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {e: [] for e in ECOSYSTEMS}
    hit = _TOP.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("lists") or {}
    except (OSError, ValueError):
        raw = {}
    lists = {e: [norm_name(e, n) for n in raw.get(e, []) if isinstance(n, str)] for e in ECOSYSTEMS}
    _TOP[str(p)] = (mtime, lists)
    return lists


def _popular_set(eco: str) -> set[str]:
    key = ("set", eco)
    lists = top_lists()
    hit = _TOP.get(key)
    if hit and hit[0] is lists:
        return hit[1]
    s = set(lists.get(eco, []))
    _TOP[key] = (lists, s)
    return s


def is_popular(eco: str, name: str) -> bool:
    return norm_name(eco, name) in _popular_set(eco)


def distance(a: str, b: str, limit: int = 3) -> int:
    """Damerau-Levenshtein (optimal string alignment) distance, stopping early above `limit`."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _squash(s: str) -> str:
    return re.sub(r"[-_.@/]", "", s)


def lookalike(eco: str, name: str) -> Optional[str]:
    """The popular package this name imitates, or None. Popular names themselves never match."""
    n = norm_name(eco, name)
    if n in _popular_set(eco) or len(n) < 4:
        return None
    sq = _squash(n)
    best: Optional[tuple[int, str]] = None
    for p in top_lists().get(eco, []):
        if p == n:
            return None
        if _squash(p) == sq:                        # same letters, different separators: "python_dateutil"
            return p
        limit = 1 if len(p) < 9 else 2
        if abs(len(p) - len(n)) > limit:
            continue
        d = distance(n, p, limit)
        if d <= limit and (best is None or d < best[0]):
            best = (d, p)
    if best:
        return best[1]
    if eco == "npm" and n.startswith("@") and "/" in n:   # "@evil/lodash": a popular name under a stranger's scope
        bare = n.split("/", 1)[1]
        if bare in _popular_set("npm") and len(bare) >= 4:
            return bare
    return None


# ===================================================================================== parsing
@dataclass
class Want:
    eco: str
    name: str
    spec: str = ""
    where: str = ""            # the command or file it came from
    source: str = "registry"   # registry | url (git/url: can't be checked) | local (a folder: fine)
    note: str = ""
    direct: bool = True        # False for lockfile entries (pulled in by other packages)

    def key(self) -> str:
        return f"{self.eco}:{norm_name(self.eco, self.name)}"


_PY_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")
_URLISH = re.compile(r"^(git\+|hg\+|svn\+|bzr\+|https?://|ftp://|file:|git://|ssh://|github:|gitlab:|bitbucket:)", re.I)
_LOCAL = re.compile(r"^(\.{1,2}([\\/]|$)|/|~|[A-Za-z]:[\\/])|\.(whl|tar\.gz|zip|tgz)$", re.I)


def parse_pip_spec(spec: str, where: str = "") -> Optional[Want]:
    s = spec.strip()
    if not s or s.startswith("#"):
        return None
    if _URLISH.match(s) or " @ " in s:
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*@\s*(\S+)", s)
        return Want("pypi", m.group(1) if m else s[:80], "", where, "url", f"comes from {m.group(3) if m else s[:80]}")
    if _LOCAL.search(s):
        return Want("pypi", s[:80], "", where, "local")
    m = _PY_NAME.match(s)
    if not m:
        return None
    return Want("pypi", m.group(1), (m.group(3) or "").split(";")[0].strip(), where)


def parse_npm_spec(spec: str, where: str = "") -> Optional[Want]:
    s = spec.strip()
    if not s:
        return None
    if _LOCAL.search(s) or s.startswith(("file:", "link:", "workspace:")):
        return Want("npm", s[:80], "", where, "local")
    if _URLISH.match(s) or re.match(r"^[\w.-]+/[\w.-]+(#.*)?$", s):   # url, git, or "user/repo" (GitHub)
        return Want("npm", s[:80], "", where, "url", f"comes from {s[:80]}")
    if "@npm:" in s:                                     # alias@npm:real@1.2
        s = s.split("@npm:", 1)[1]
    if s.startswith("@"):
        parts = s[1:].split("@", 1)
        name, ver = "@" + parts[0], (parts[1] if len(parts) > 1 else "")
    else:
        name, _, ver = s.partition("@")
    if not re.fullmatch(r"(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*", name, re.I):
        return None
    return Want("npm", name, ver, where)


def parse_crate_spec(spec: str, where: str = "") -> Optional[Want]:
    name, _, ver = spec.strip().partition("@")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name or ""):
        return None
    return Want("crates", name, ver, where)


_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PIP_VALUE_FLAGS = {"-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-t", "--target", "--prefix",
                    "--root", "-f", "--find-links", "--platform", "--python-version", "--implementation", "--abi",
                    "--only-binary", "--no-binary", "--progress-bar", "--log", "--cache-dir", "--src",
                    "--upgrade-strategy", "--trusted-host", "--proxy", "--timeout", "--retries", "--group", "-G",
                    "--python", "-p", "--extra", "--optional", "--source", "--index", "--default-index",
                    "--config-settings", "-C", "--report", "--python-platform", "--with", "-w"}
_NPM_VALUE_FLAGS = {"--registry", "--prefix", "-w", "--workspace", "--tag", "--omit", "--include", "--cache",
                    "--userconfig", "--before", "--filter", "-C", "--dir", "--cwd", "--package", "-p"}
_CARGO_VALUE_FLAGS = {"--features", "-F", "--rename", "--package", "-p", "--manifest-path", "--target", "--branch",
                      "--tag", "--rev", "--version", "--vers", "--root", "--index", "--registry", "--bin",
                      "--example", "--profile", "-j", "--jobs", "--target-dir", "--config", "-Z", "--lockfile-path"}


def _strip_prefix(argv: list[str]) -> list[str]:
    while argv and (_ENV_ASSIGN.match(argv[0]) or argv[0] in ("sudo", "time", "exec", "command", "nice", "env")):
        argv = argv[1:]
    return argv


def _positionals(args: list[str], value_flags: set[str]) -> tuple[list[str], dict[str, list[str]]]:
    pos: list[str] = []
    flags: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos += args[i + 1:]
            break
        if a.startswith("-"):
            k, eq, v = a.partition("=")
            if eq:
                flags.setdefault(k, []).append(v)
            elif k in value_flags and i + 1 < len(args):
                flags.setdefault(k, []).append(args[i + 1])
                i += 1
            elif k in ("-r", "--requirement", "-e", "--editable", "--path", "--git") and i + 1 < len(args):
                flags.setdefault(k, []).append(args[i + 1])
                i += 1
            else:
                flags.setdefault(k, []).append("")
        else:
            pos.append(a)
        i += 1
    return pos, flags


def _pip_tool(argv: list[str]) -> Optional[list[str]]:
    """The arguments after 'install'/'add' for the Python installers, or None."""
    a = argv
    base = Path(a[0]).name.lower() if a else ""
    if re.fullmatch(r"(python|python3(\.\d+)?|py)(\.exe)?", base) and len(a) > 2 and a[1] == "-m":
        a = a[2:]
        base = a[0].lower()
    base = re.sub(r"\.exe$", "", base)
    if re.fullmatch(r"pip(3(\.\d+)?)?", base) and len(a) > 1 and a[1] == "install":
        return a[2:]
    if base == "uv" and len(a) > 2 and a[1] == "pip" and a[2] == "install":
        return a[3:]
    if base == "uv" and len(a) > 1 and a[1] == "add":
        return a[2:]
    if base == "uv" and len(a) > 2 and a[1] == "tool" and a[2] == "install":
        return a[3:]
    if base in ("poetry", "pdm") and len(a) > 1 and a[1] == "add":
        return a[2:]
    if base in ("pipx", "pipenv") and len(a) > 1 and a[1] == "install":
        return a[2:]
    if base in ("uvx",) and len(a) > 1:
        return [x for x in a[1:2] if not x.startswith("-")]
    return None


_NPM_INSTALL = {"install", "i", "add", "in", "ins", "inst", "insta", "instal", "isnt", "isntall", "isntal"}


def _npm_tool(argv: list[str]) -> Optional[list[str]]:
    if not argv:
        return None
    base = re.sub(r"(\.cmd|\.exe)$", "", Path(argv[0]).name.lower())
    rest = argv[1:]
    if base in ("npx", "bunx", "pnpx"):
        return [x for x in rest if not x.startswith("-")][:1]          # runs a package: check that one
    if base in ("pnpm", "yarn") and rest[:1] == ["dlx"]:
        return [x for x in rest[1:] if not x.startswith("-")][:1]
    if base == "npm" and rest[:1] in (["exec"], ["x"]):
        return [x for x in rest[1:] if not x.startswith("-")][:1]
    if base == "yarn" and rest[:2] == ["global", "add"]:
        return rest[2:]
    if base in ("npm", "pnpm", "bun") and rest and rest[0] in _NPM_INSTALL:
        return rest[1:]
    if base == "yarn" and rest[:1] == ["add"]:
        return rest[1:]
    return None


def parse_command(command: str, cwd: Optional[Path] = None) -> tuple[list[Want], list[str]]:
    """Packages a shell command would install, and notes (custom package index, requirement files...)."""
    wants: list[Want] = []
    notes: list[str] = []
    for seg in _SEGMENT_SPLIT.split(command or ""):
        try:
            argv = _strip_prefix(shlex.split(seg, posix=True))
        except ValueError:
            continue
        if not argv:
            continue
        where = " ".join(argv)[:200]
        args = _pip_tool(argv)
        if args is not None:
            pos, flags = _positionals(args, _PIP_VALUE_FLAGS)
            for k in ("-i", "--index-url", "--extra-index-url", "--index", "--default-index", "-f", "--find-links"):
                for v in flags.get(k, []):
                    notes.append(f"uses an extra package source ({v[:80]}): packages there aren't checked against PyPI")
            for v in flags.get("-e", []) + flags.get("--editable", []):
                w = parse_pip_spec(v, where)
                if w and w.source != "local":
                    wants.append(w)
            for p in pos:
                w = parse_pip_spec(p, where)
                if w:
                    wants.append(w)
            for req in flags.get("-r", []) + flags.get("--requirement", []):
                text = _read_inside(cwd, req)
                if text is None:
                    notes.append(f"couldn't read {req}, so its packages weren't checked")
                else:
                    wants += [Want(w.eco, w.name, w.spec, req, w.source, w.note) for w in requirements_wants(text)]
            continue
        args = _npm_tool(argv)
        if args is not None:
            pos, flags = _positionals(args, _NPM_VALUE_FLAGS)
            for v in flags.get("--registry", []):
                notes.append(f"uses another npm registry ({v[:80]}): packages there aren't checked against npm")
            for p in pos:
                w = parse_npm_spec(p, where)
                if w:
                    wants.append(w)
            continue
        base = re.sub(r"\.exe$", "", Path(argv[0]).name.lower())
        if base == "cargo" and len(argv) > 1 and argv[1] in ("add", "install"):
            pos, flags = _positionals(argv[2:], _CARGO_VALUE_FLAGS)
            git = (flags.get("--git") or [""])[0]
            path = (flags.get("--path") or [""])[0]
            for v in flags.get("--registry", []) + flags.get("--index", []):
                notes.append(f"uses another crate registry ({v[:80]}): crates there aren't checked against crates.io")
            if argv[1] == "install" and path:
                continue
            for p in pos:
                w = parse_crate_spec(p, where)
                if not w:
                    continue
                if git:
                    w.source, w.note = "url", f"comes from {git[:80]}"
                elif path:
                    w.source = "local"
                wants.append(w)
    return wants, notes


def _read_inside(cwd: Optional[Path], rel: str) -> Optional[str]:
    if cwd is None:
        return None
    try:
        base = Path(cwd).resolve()
        p = (base / rel).resolve()
        if not p.is_relative_to(base) or p.stat().st_size > 1_000_000:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


# ===================================================================================== manifests
def requirements_wants(text: str, where: str = "") -> list[Want]:
    out = []
    for line in text.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        w = parse_pip_spec(line, where)
        if w:
            out.append(w)
    return out


def _toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text or "")
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _pep508_list(items: Any, where: str) -> list[Want]:
    out = []
    for s in items or []:
        if isinstance(s, str):
            w = parse_pip_spec(s, where)
            if w:
                out.append(w)
    return out


def _cargo_table(tbl: Any, where: str) -> list[Want]:
    out = []
    for key, spec in (tbl or {}).items():
        name = key
        source, note = "registry", ""
        if isinstance(spec, dict):
            name = str(spec.get("package") or key)
            if spec.get("path"):
                source = "local"
            elif spec.get("git"):
                source, note = "url", f"comes from {str(spec['git'])[:80]}"
            elif spec.get("workspace"):
                continue
            elif spec.get("registry"):
                source, note = "url", f"comes from the {spec['registry']} registry"
        out.append(Want("crates", name, "", where, source, note))
    return out


def manifest_kind(path: str) -> Optional[str]:
    n = Path(path.replace("\\", "/")).name.lower()
    if n == "package.json":
        return "package.json"
    if n in ("package-lock.json", "npm-shrinkwrap.json"):
        return "package-lock"
    if n == "yarn.lock":
        return "yarn.lock"
    if n == "pnpm-lock.yaml":
        return "pnpm-lock"
    if re.fullmatch(r"requirements[\w.-]*\.(txt|in)", n) or n == "constraints.txt":
        return "requirements"
    if n == "pyproject.toml":
        return "pyproject"
    if n == "pipfile":
        return "pipfile"
    if n in ("poetry.lock", "uv.lock", "pdm.lock"):
        return "pylock"
    if n == "cargo.toml":
        return "cargo.toml"
    if n == "cargo.lock":
        return "cargo.lock"
    return None


def manifest_wants(path: str, text: str) -> list[Want]:
    """Every dependency a manifest or lockfile names."""
    kind = manifest_kind(path)
    where = path
    if not text or kind is None:
        return []
    out: list[Want] = []
    if kind == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        for sect in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            for name, ver in ((data.get(sect) if isinstance(data, dict) else None) or {}).items():
                ver = str(ver)
                if ver.startswith("npm:"):
                    w = parse_npm_spec(ver[4:], where)
                elif ver.startswith(("file:", "link:", "workspace:", "portal:", ".", "/")):
                    w = Want("npm", name, "", where, "local")
                elif _URLISH.match(ver) or re.match(r"^[\w.-]+/[\w.-]+(#.*)?$", ver):
                    w = Want("npm", name, "", where, "url", f"comes from {ver[:80]}")
                else:
                    w = Want("npm", name, ver, where)
                if w:
                    out.append(w)
    elif kind == "package-lock":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        pk = data.get("packages") if isinstance(data, dict) else None
        if isinstance(pk, dict):
            for key, meta in pk.items():
                if "node_modules/" not in key or not isinstance(meta, dict) or meta.get("link"):
                    continue
                name = str(meta.get("name") or key.rsplit("node_modules/", 1)[1])
                resolved = str(meta.get("resolved") or "")
                if resolved and not resolved.startswith("https://registry.npmjs.org/"):
                    if resolved.startswith(("file:", "./", "../")):
                        continue
                    out.append(Want("npm", name, "", where, "url", f"comes from {resolved[:80]}"))
                else:
                    out.append(Want("npm", name, str(meta.get("version") or ""), where))
        elif isinstance(data, dict):
            def walk(deps: Any) -> None:
                for name, meta in (deps or {}).items():
                    out.append(Want("npm", name, str((meta or {}).get("version") or ""), where))
                    walk((meta or {}).get("dependencies"))
            walk(data.get("dependencies"))
    elif kind == "yarn.lock":
        for m in re.finditer(r'^"?((?:@[^@\s"/]+/)?[^@\s",]+)@', text, re.M):
            out.append(Want("npm", m.group(1), "", where))
    elif kind == "pnpm-lock":
        for m in re.finditer(r"^\s{2}'?/?((?:@[^@\s/']+/)?[^@\s/:'()]+)@[^:\s]+'?:\s*$", text, re.M):
            out.append(Want("npm", m.group(1), "", where))
    elif kind == "requirements":
        out = requirements_wants(text, where)
    elif kind == "pyproject":
        data = _toml(text)
        proj = data.get("project") or {}
        out += _pep508_list(proj.get("dependencies"), where)
        for items in (proj.get("optional-dependencies") or {}).values():
            out += _pep508_list(items, where)
        for items in (data.get("dependency-groups") or {}).values():
            out += _pep508_list([i for i in items if isinstance(i, str)], where)
        tool = data.get("tool") or {}
        out += _pep508_list((tool.get("uv") or {}).get("dev-dependencies"), where)
        poetry = tool.get("poetry") or {}
        tables = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
        tables += [(g or {}).get("dependencies") for g in (poetry.get("group") or {}).values()]
        for tbl in tables:
            for name, spec in (tbl or {}).items():
                if name.lower() == "python":
                    continue
                if isinstance(spec, dict) and spec.get("path"):
                    continue
                if isinstance(spec, dict) and (spec.get("git") or spec.get("url")):
                    out.append(Want("pypi", name, "", where, "url", f"comes from {spec.get('git') or spec.get('url')}"))
                else:
                    out.append(Want("pypi", name, "", where))
    elif kind == "pipfile":
        data = _toml(text)
        for sect in ("packages", "dev-packages"):
            for name, spec in (data.get(sect) or {}).items():
                if isinstance(spec, dict) and (spec.get("path") or spec.get("file")):
                    continue
                if isinstance(spec, dict) and spec.get("git"):
                    out.append(Want("pypi", name, "", where, "url", f"comes from {spec['git']}"))
                else:
                    out.append(Want("pypi", name, "", where))
    elif kind == "pylock":
        for pkg in _toml(text).get("package") or []:
            if not isinstance(pkg, dict) or not pkg.get("name"):
                continue
            src = pkg.get("source")
            if isinstance(src, dict) and (src.get("editable") or src.get("virtual") or src.get("path")
                                          or src.get("directory")):
                continue
            if isinstance(src, dict) and (src.get("git") or src.get("url")):
                out.append(Want("pypi", pkg["name"], "", where, "url", f"comes from {src.get('git') or src.get('url')}"))
                continue
            out.append(Want("pypi", str(pkg["name"]), str(pkg.get("version") or ""), where))
    elif kind == "cargo.toml":
        data = _toml(text)
        for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
            out += _cargo_table(data.get(sect), where)
        for tgt in (data.get("target") or {}).values():
            for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
                out += _cargo_table((tgt or {}).get(sect), where)
        out += _cargo_table((data.get("workspace") or {}).get("dependencies"), where)
    elif kind == "cargo.lock":  # (lockfiles: see the `direct` flag set below)
        for pkg in _toml(text).get("package") or []:
            src = str(pkg.get("source") or "")
            if not src:
                continue                                   # a crate in this workspace
            if src.startswith("registry+https://github.com/rust-lang/crates.io-index") or \
                    src.startswith("sparse+https://index.crates.io"):
                out.append(Want("crates", str(pkg.get("name")), str(pkg.get("version") or ""), where))
            else:
                out.append(Want("crates", str(pkg.get("name")), "", where, "url", f"comes from {src[:80]}"))
    if kind in ("package-lock", "yarn.lock", "pnpm-lock", "pylock", "cargo.lock"):
        for w in out:
            w.direct = False
    return out


def new_dependencies(path: str, old: str, new: str) -> list[Want]:
    """Dependencies in `new` that weren't in `old` (by name, so version bumps don't count)."""
    before = {w.key() for w in manifest_wants(path, old or "")}
    seen: set[str] = set()
    out = []
    for w in manifest_wants(path, new or ""):
        k = w.key() + ("|" + w.source if w.source != "registry" else "")
        if w.key() in before or k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out


# ===================================================================================== registries
@dataclass
class Info:
    eco: str
    name: str
    exists: Optional[bool] = None         # None = couldn't check
    created: Optional[float] = None
    downloads: Optional[int] = None
    downloads_period: str = ""
    publisher: str = ""
    versions: Optional[int] = None
    error: str = ""
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("eco", "name", "exists", "created", "downloads", "downloads_period",
                                              "publisher", "versions", "error", "checked_at")}


def _iso(ts: str) -> Optional[float]:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


class Registry:
    """Public registry APIs, read-only, through httpx (short timeouts)."""

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None, timeout: float = LOOKUP_TIMEOUT_S) -> None:
        self.timeout = timeout
        self.client_factory = client_factory

    def _client(self) -> Any:
        import httpx
        if self.client_factory is not None:
            return self.client_factory()
        return httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": UA, "Accept": "application/json"},
                                 follow_redirects=True)

    async def lookup(self, eco: str, name: str) -> Info:
        import httpx
        try:
            async with self._client() as c:
                if eco == "pypi":
                    return await self._pypi(c, name)
                if eco == "npm":
                    return await self._npm(c, name)
                if eco == "crates":
                    return await self._crates(c, name)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            return Info(eco, name, None, error=f"couldn't reach {REGISTRY_NAME.get(eco, eco)} ({type(exc).__name__})")
        return Info(eco, name, None, error="unknown package type")

    @staticmethod
    def _miss_or_error(eco: str, name: str, status: int) -> Optional[Info]:
        if status == 404:
            return Info(eco, name, False)
        if status >= 400:
            return Info(eco, name, None, error=f"{REGISTRY_NAME[eco]} answered {status}")
        return None

    async def _pypi(self, c: Any, name: str) -> Info:
        r = await c.get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
        if (bad := self._miss_or_error("pypi", name, r.status_code)) is not None:
            return bad
        d = r.json()
        info = d.get("info") or {}
        times = [_iso(f.get("upload_time_iso_8601") or "") for files in (d.get("releases") or {}).values()
                 for f in files or []]
        times = [t for t in times if t]
        who = info.get("author") or info.get("maintainer") or ""
        if not who:
            email = info.get("author_email") or info.get("maintainer_email") or ""
            who = email.split("<")[0].strip().strip('"') or email
        return Info("pypi", name, True, min(times) if times else None, None, "", str(who)[:80],
                    len(d.get("releases") or {}))

    async def _npm(self, c: Any, name: str) -> Info:
        r = await c.get("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@"))
        if (bad := self._miss_or_error("npm", name, r.status_code)) is not None:
            return bad
        d = r.json()
        created = _iso(((d.get("time") or {}).get("created")) or "")
        maint = d.get("maintainers") or []
        who = (maint[0].get("name") if maint and isinstance(maint[0], dict) else "") or ""
        downloads = None
        try:
            s = await c.get("https://registry.npmjs.org/-/v1/search",
                            params={"text": name, "size": 5})
            if s.status_code == 200:
                for o in s.json().get("objects") or []:
                    if (o.get("package") or {}).get("name") == name:
                        downloads = int((o.get("downloads") or {}).get("weekly") or 0)
                        break
        except Exception:
            pass
        return Info("npm", name, True, created, downloads, "last week" if downloads is not None else "", who[:80],
                    len(d.get("versions") or {}))

    async def _crates(self, c: Any, name: str) -> Info:
        r = await c.get(f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}")
        if (bad := self._miss_or_error("crates", name, r.status_code)) is not None:
            return bad
        d = r.json().get("crate") or {}
        who = ""
        try:
            o = await c.get(f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}/owners")
            if o.status_code == 200:
                users = o.json().get("users") or []
                who = (users[0].get("login") or users[0].get("name") or "") if users else ""
        except Exception:
            pass
        recent = d.get("recent_downloads")
        return Info("crates", name, True, _iso(d.get("created_at") or ""),
                    int(recent) if recent is not None else (int(d["downloads"]) if d.get("downloads") is not None else None),
                    "last 90 days" if recent is not None else "in total", str(who)[:80], d.get("num_versions"))


# ===================================================================================== judging
def judge(w: Want, info: Optional[Info], now: float, offline: str = "ask") -> dict[str, Any]:
    """{verdict: ok|warn|block|unknown, reasons: [plain sentences]} for one package."""
    reg = REGISTRY_NAME.get(w.eco, w.eco)
    if w.source == "local":
        return {"verdict": "ok", "reasons": ["a folder on this computer"]}
    if w.source == "url":
        return {"verdict": "unknown", "reasons": [f"It {w.note or 'comes from outside the registry'}, so Orion "
                                                  "can't check who made it."]}
    blocks: list[str] = []
    warns: list[str] = []
    if is_popular(w.eco, w.name):
        return {"verdict": "ok", "reasons": ["a well-known package"]}
    twin = lookalike(w.eco, w.name)
    if twin:
        (blocks if w.direct else warns).append(
            f"Its name is very close to “{twin}”, a popular package. Attackers register names like this and wait "
            "for a typo.")
    if info is None or info.exists is None:
        if blocks or (warns and not w.direct):
            return {"verdict": "block", "reasons": blocks + warns}
        why = (info.error if info else "") or f"{reg} couldn't be reached"
        return {"verdict": "unknown" if offline == "ask" else "ok",
                "reasons": [f"Orion couldn't check it: {why}."]}
    if info.exists is False:
        blocks.append(f"It doesn't exist on {reg}. AI models sometimes make up package names, and attackers then "
                      "register them.")
        return {"verdict": "block", "reasons": blocks + warns}
    if info.created:
        age = (now - info.created) / DAY
        if age < 7:
            blocks.append(f"It was first published {max(0, int(age))} day{'s' if int(age) != 1 else ''} ago.")
        elif age < 30:
            warns.append(f"It's new: first published {int(age)} days ago.")
        if info.versions == 1 and age < 90:
            warns.append("It has only one version.")
    if info.downloads is not None:
        low = 100 if w.eco == "npm" else 300
        if info.downloads < low:
            warns.append(f"Hardly anyone uses it: {info.downloads} download{'s' if info.downloads != 1 else ''} "
                         f"{info.downloads_period}.")
    if not info.publisher and w.eco != "pypi":
        warns.append("Orion couldn't see who publishes it.")
    if blocks or len(warns) >= 2:
        return {"verdict": "block", "reasons": blocks + warns}
    if warns:
        return {"verdict": "warn", "reasons": warns}
    return {"verdict": "ok", "reasons": []}


def describe(results: list[dict[str, Any]], notes: Iterable[str] = (), skipped: int = 0) -> str:
    """Plain words for a person (and for the agent that was stopped)."""
    blocked = [r for r in results if r["verdict"] in ("block", "unknown")]
    lines: list[str] = []
    if blocked:
        lines.append("Package Reality Check stopped this: " +
                     ("one package needs a look." if len(blocked) == 1 else f"{len(blocked)} packages need a look."))
    elif results:
        lines.append("Package Reality Check: every new package looks real.")
    for r in results:
        if r["verdict"] == "ok":
            continue
        word = {"block": "blocked", "unknown": "couldn't be checked", "warn": "allowed, but note"}[r["verdict"]]
        lines.append(f"- {label(r['eco'], r['name'])}: {word}. " + " ".join(r["reasons"]))
        i = r.get("info") or {}
        facts = []
        if i.get("publisher"):
            facts.append(f"published by {i['publisher']}")
        if i.get("created"):
            facts.append("first released " + time.strftime("%Y-%m-%d", time.gmtime(i["created"])))
        if i.get("downloads") is not None:
            facts.append(f"{i['downloads']:,} downloads {i.get('downloads_period', '')}".strip())
        if facts:
            lines.append("    (" + ", ".join(facts) + ")")
    for n in notes:
        lines.append(f"Note: it {n}.")
    if skipped:
        lines.append(f"{skipped} more package(s) weren't checked (too many at once).")
    if blocked:
        lines.append("If you trust them, approve the card in Orion's Queue and try again. Otherwise check the "
                     "spelling or pick a well-known package.")
    return "\n".join(lines)


# ===================================================================================== service
class PackageCheck:
    """The daemon's checker: cache, settings, approved packages, recent checks and the approval card."""

    def __init__(self, data_dir: Path, daemon: Any = None, registry: Optional[Registry] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(data_dir) / "package_check.json"
        self.daemon = daemon
        self.registry = registry or Registry()
        self.clock = clock
        self.state: dict[str, Any] = {"offline": "ask", "enabled": True, "allowed": [], "cache": {}, "recent": []}
        try:
            if self.path.exists():
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._pending_cards: dict[str, str] = {}   # blocked-set key -> card id

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Could not save Package Reality Check state: %s", exc)

    # ------------------------------------------------------------ settings
    def settings(self) -> dict[str, Any]:
        return {"enabled": bool(self.state.get("enabled", True)), "offline": self.state.get("offline", "ask"),
                "allowed": list(self.state.get("allowed") or [])[-200:],
                "popular_list": {e: len(v) for e, v in top_lists().items()}}

    def configure(self, offline: Optional[str] = None, enabled: Optional[bool] = None) -> dict[str, Any]:
        if offline is not None:
            if offline not in ("ask", "allow"):
                raise ValueError("offline must be ask or allow")
            self.state["offline"] = offline
        if enabled is not None:
            self.state["enabled"] = bool(enabled)
        self._save()
        return self.settings()

    def allow(self, keys: Iterable[str]) -> None:
        cur = list(self.state.get("allowed") or [])
        for k in keys:
            if k not in cur:
                cur.append(k)
        self.state["allowed"] = cur[-2000:]
        self._save()

    # ------------------------------------------------------------ lookups
    async def info(self, eco: str, name: str) -> Info:
        key = f"{eco}:{norm_name(eco, name)}"
        hit = (self.state.get("cache") or {}).get(key)
        now = self.clock()
        if hit and now - float(hit.get("checked_at", 0)) < (CACHE_TTL_S if hit.get("exists") else MISS_TTL_S) \
                and hit.get("exists") is not None:
            return Info(**{k: hit.get(k) for k in Info.__dataclass_fields__ if k in hit})
        async with self._sem:
            info = await self.registry.lookup(eco, name)
        info.checked_at = now
        if info.exists is not None:
            cache = self.state.setdefault("cache", {})
            cache[key] = info.to_dict()
            if len(cache) > 3000:
                for k in sorted(cache, key=lambda k: cache[k].get("checked_at", 0))[:500]:
                    cache.pop(k, None)
        return info

    async def check(self, wants: list[Want], origin: str = "", notes: Iterable[str] = (),
                    card: bool = True) -> dict[str, Any]:
        """Check packages; post one approval card for anything blocked. Returns a report with `allowed`."""
        notes = list(dict.fromkeys(notes))
        if not self.state.get("enabled", True):
            return {"verdict": "off", "allowed": True, "packages": [], "summary": "Package Reality Check is off."}
        uniq: dict[str, Want] = {}
        for w in wants:
            uniq.setdefault(w.key() + "|" + w.source, w)
        todo = list(uniq.values())
        skipped = max(0, len(todo) - MAX_PACKAGES)
        todo = todo[:MAX_PACKAGES]
        allowed = set(self.state.get("allowed") or [])
        offline = self.state.get("offline", "ask")
        now = self.clock()

        async def one(w: Want) -> dict[str, Any]:
            if w.key() in allowed:
                return {"eco": w.eco, "name": w.name, "verdict": "ok", "reasons": ["you approved it"], "info": None,
                        "where": w.where}
            info = None
            if w.source == "registry" and not is_popular(w.eco, w.name):
                info = await self.info(w.eco, w.name)
            j = judge(w, info, now, offline)
            return {"eco": w.eco, "name": w.name, **j, "info": info.to_dict() if info else None, "where": w.where,
                    "source": w.source}

        results = await asyncio.gather(*(one(w) for w in todo))
        self._save()
        blocked = [r for r in results if r["verdict"] in ("block", "unknown")]
        verdict = "block" if any(r["verdict"] == "block" for r in results) else \
            "unknown" if blocked else "warn" if any(r["verdict"] == "warn" for r in results) else \
            "ok" if results else "none"
        report = {"verdict": verdict, "allowed": not blocked, "packages": list(results), "notes": notes,
                  "origin": origin[:200], "at": now, "skipped": skipped,
                  "summary": describe(list(results), notes, skipped) if results else
                  "No new packages to check."}
        if results:
            rec = {k: report[k] for k in ("verdict", "origin", "at")}
            rec["packages"] = [f"{r['eco']}:{r['name']}={r['verdict']}" for r in results][:30]
            self.state["recent"] = (list(self.state.get("recent") or []) + [rec])[-50:]
            self._save()
        if blocked and card:
            report["card_id"] = await self._card(blocked, report)
        return report

    async def check_command(self, command: str, cwd: Optional[str] = None, origin: str = "",
                            card: bool = True) -> dict[str, Any]:
        wants, notes = parse_command(command, Path(cwd) if cwd else None)
        if not wants:
            return {"verdict": "none", "allowed": True, "packages": [], "notes": notes,
                    "summary": "No packages are installed by this command."}
        return await self.check(wants, origin or f"command: {command[:160]}", notes, card)

    async def check_manifests(self, files: list[dict[str, Any]], origin: str = "", card: bool = True) -> dict[str, Any]:
        wants: list[Want] = []
        for f in files[:100]:
            path = str(f.get("path") or "")
            if manifest_kind(path):
                wants += new_dependencies(path, str(f.get("old") or ""), str(f.get("new") or ""))
        if not wants:
            return {"verdict": "none", "allowed": True, "packages": [], "summary": "No new packages."}
        return await self.check(wants, origin, (), card)

    async def check_git(self, repo: Path, base: str = "HEAD", head: Optional[str] = None,
                        worktree: Optional[Path] = None, origin: str = "", card: bool = True) -> dict[str, Any]:
        """New dependencies between `base` and the staged index (head None), a commit, or a worktree's files."""
        files = await asyncio.to_thread(git_manifest_changes, Path(repo), base, head, worktree)
        return await self.check_manifests(files, origin or f"changes in {Path(repo).name}", card)

    def recent(self) -> list[dict[str, Any]]:
        return list(reversed(self.state.get("recent") or []))

    # ------------------------------------------------------------ approval card
    async def _card(self, blocked: list[dict[str, Any]], report: dict[str, Any]) -> Optional[str]:
        d = self.daemon
        keys = sorted({f"{r['eco']}:{norm_name(r['eco'], r['name'])}" for r in blocked})
        sig = "|".join(keys)
        if d is None or not hasattr(d, "approvals"):
            return None
        old = self._pending_cards.get(sig)
        if old and (d.approvals.get(old) or {}).get("status") == "pending":
            return old
        names = ", ".join(label(r["eco"], r["name"]) for r in blocked[:5]) + ("…" if len(blocked) > 5 else "")
        title = f"Package check: {names}"
        try:
            item = d.approvals.add(CARD_KIND, platform="Package Reality Check", channel="Agent safety",
                                   author="Package Reality Check", category="urgent", title=title[:160],
                                   original=report["summary"][:4000],
                                   draft="Approve = I trust these packages; the agent can try again. "
                                         "Reject = keep them blocked.",
                                   packages=keys, origin=report.get("origin", ""))
            self._pending_cards[sig] = item["id"]
            await d.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
            try:
                import alerts
                alerts.emit("approval", "An agent wants a package Orion doesn't trust",
                            f"{names}. Approve or reject in Orion.", ref=item["id"])
            except Exception:
                pass
            return item["id"]
        except Exception:
            log.exception("Could not post the Package Reality Check card")
            return None

    async def decide(self, item: dict[str, Any], decision: str) -> None:
        d = self.daemon
        if decision in ("approve", "edit"):
            self.allow(item.get("packages") or [])
            status = "handled"
        else:
            status = "rejected"
        updated = d.approvals.update(item["id"], status=status)
        await d.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})


# ===================================================================================== git helpers
def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def git_manifest_changes(repo: Path, base: str = "HEAD", head: Optional[str] = None,
                         worktree: Optional[Path] = None) -> list[dict[str, Any]]:
    """[{path, old, new}] for every changed manifest or lockfile."""
    if worktree is not None:
        code, names = _git(Path(worktree), "diff", "--name-only", "--no-renames", base)
    elif head is None:
        code, names = _git(repo, "diff", "--cached", "--name-only", "--no-renames", base)
        if code != 0:   # no commits yet
            code, names = _git(repo, "diff", "--cached", "--name-only", "--no-renames",
                               "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    else:
        code, names = _git(repo, "diff", "--name-only", "--no-renames", base, head)
    if code != 0:
        return []
    out = []
    for path in [n for n in names.splitlines() if n.strip()]:
        if not manifest_kind(path):
            continue
        _, old = _git(repo, "show", f"{base}:{path}")
        if worktree is not None:
            try:
                new = (Path(worktree) / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                new = ""
        elif head is None:
            _, new = _git(repo, "show", f":{path}")
        else:
            _, new = _git(repo, "show", f"{head}:{path}")
        out.append({"path": path, "old": old, "new": new})
    return out


# ===================================================================================== routes
def make_router(get_checker: Callable[[], PackageCheck]):

    router = APIRouter(prefix="/package-check", tags=["toolkit"])

    async def body(request: Request) -> dict[str, Any]:
        try:
            b = await request.json()
        except ValueError:
            b = None
        if not isinstance(b, dict):
            raise ValueError("Send a JSON object.")
        return b

    def err(msg: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=status)

    @router.post("/command")
    async def command(request: Request) -> Any:
        try:
            b = await body(request)
        except ValueError as exc:
            return err(str(exc))
        cmd = str(b.get("command") or "")
        if not cmd.strip() or len(cmd) > 8000:
            return err("Send the command (up to 8000 characters).")
        cwd = str(b.get("cwd") or "") or None
        if cwd:
            from mcp_tools import McpConfig, ToolError, resolve_project
            try:
                cwd = str(resolve_project(cwd, McpConfig.load()))
            except ToolError:
                cwd = None        # requirement files outside allowed folders aren't read
        return await get_checker().check_command(cmd, cwd, str(b.get("origin") or ""), card=b.get("card", True) is not False)

    @router.post("/packages")
    async def packages(request: Request) -> Any:
        try:
            b = await body(request)
            raw = b.get("packages") or []
            wants = []
            for p in raw[:MAX_PACKAGES]:
                if isinstance(p, str):
                    eco, _, name = p.partition(":")
                else:
                    eco, name = str(p.get("ecosystem") or ""), str(p.get("name") or "")
                eco = {"pip": "pypi", "python": "pypi", "cargo": "crates", "crate": "crates", "node": "npm"}.get(
                    eco.lower(), eco.lower())
                if eco not in ECOSYSTEMS or not name.strip():
                    raise ValueError("Each package is 'pypi:name', 'npm:name' or 'crates:name'.")
                wants.append(Want(eco, name.strip()[:214], "", "asked directly"))
        except ValueError as exc:
            return err(str(exc))
        return await get_checker().check(wants, str(b.get("origin") or "asked directly"),
                                         card=b.get("card", False) is True)

    @router.post("/manifests")
    async def manifests(request: Request) -> Any:
        """Used by the git pre-commit check: {files: [{path, old, new}], origin}."""
        try:
            b = await body(request)
        except ValueError as exc:
            return err(str(exc))
        files = [f for f in (b.get("files") or []) if isinstance(f, dict)]
        return await get_checker().check_manifests(files, str(b.get("origin") or "a commit")[:200])

    @router.post("/staged")
    async def staged(request: Request) -> Any:
        try:
            b = await body(request)
            from mcp_tools import McpConfig, resolve_project
            repo = resolve_project(str(b.get("repo") or b.get("project") or ""), McpConfig.load())
        except Exception as exc:
            return err(str(exc))
        return await get_checker().check_git(repo, origin=f"staged changes in {repo.name}")

    @router.get("/status")
    async def status() -> Any:
        c = get_checker()
        return {**c.settings(), "recent": c.recent()[:20]}

    @router.post("/settings")
    async def settings_(request: Request) -> Any:
        try:
            b = await body(request)
            return get_checker().configure(b.get("offline"), b.get("enabled") if "enabled" in b else None)
        except ValueError as exc:
            return err(str(exc))

    return router


# ===================================================================================== MCP tool
def mcp_tools(tools: Any) -> list[Any]:
    from mcp_tools import DaemonUnavailable, Tool, ToolError, _schema

    async def package_check(a: dict[str, Any]) -> str:
        pk = [p for p in a.get("packages") or [] if p.strip()]
        cmd = (a.get("command") or "").strip()
        if not pk and not cmd:
            raise ToolError("Give packages (like 'pypi:requests' or 'npm:left-pad') or an install command.")
        try:
            if cmd:
                status, data = await tools.backend.call("POST", "/package-check/command",
                                                        {"command": cmd, "card": False, "origin": "an agent asked"})
            else:
                status, data = await tools.backend.call("POST", "/package-check/packages", {"packages": pk})
            if status >= 400:
                raise ToolError(str(data.get("error") or f"Orion refused it ({status})."))
            return tools.out(data.get("summary") or "No packages to check.")
        except DaemonUnavailable:
            wants = parse_command(cmd)[0] if cmd else [
                Want(*(p.split(":", 1) if ":" in p else ("pypi", p))) for p in pk]
            res = [{"eco": w.eco, "name": w.name, "info": None, **judge(w, None, time.time(), "ask")} for w in wants
                   if w.eco in ECOSYSTEMS]
            return tools.out(describe(res) + "\n\n(Orion isn't running, so only the look-alike test ran.)")

    return [Tool("package_check", "Is this package real?",
                 "Package Reality Check: before adding a dependency, check that it exists on its registry (PyPI, "
                 "npm, crates.io), how old and how used it is, who publishes it, and that its name isn't a "
                 "look-alike of a popular package. Install commands run through Orion are checked automatically; "
                 "this lets you ask first.", "read",
                 _schema({"packages": {"type": "array", "items": {"type": "string"},
                                       "description": "Like 'pypi:requests', 'npm:left-pad', 'crates:serde'"},
                          "command": {"type": "string", "description": "An install command to check instead"}}),
                 package_check)]
