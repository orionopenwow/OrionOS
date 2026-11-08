"""Release Butler (Toolkit tool 11, docs/ORION_TOOLKIT_PLAN.md): one phrase to a signed release.

"Orion, release Orion 1.1" (or "release Benilla 0.4.2", or "dry run release OpenWoW 2.0"):

  1. changelog   from the commits since the last tag: git-cliff when it's installed, otherwise Orion's own
                 grouping of conventional commits (feat / fix / perf / docs / ...) into plain headings;
  2. bump        the version in the files that hold it (for Orion: the same files as scripts/release.mjs);
  3. tests       the full tests, through Proof Gate, in a throwaway copy of the release branch;
  4. build       the project's build hooks (.orion/release.json "build": [...]). For Orion itself the hooks call
                 the existing scripts' checks only: the USB image is built by GitHub Actions when the os-v tag
                 is pushed (.github/workflows/image.yml), never on this machine by the butler;
  5. sign        minisign signatures for the release files, with the existing release key flow
                 (ORION_RELEASE_KEY = path of the minisign secret key, ORION_RELEASE_KEY_PASSWORD optional,
                 exactly like os/image/package-release.sh). Orion never reads the key itself;
  6. draft       the patch notes and the exact `gh release create --draft` command, kept on this machine.

All of that happens in a separate git worktree on a new branch `release/v<version>`, so your own checkout is never
touched. Nothing leaves the machine until you approve the boardroom decision `release.push` (push the branch and
tag, and create the *draft* GitHub release). Making the draft public is a second decision, `release.publish`.
Both are sensitive: they always ask, on your phone or the boardroom.

Dry run: steps 1-2 and the plan for 3-6, with no files written and no commands run.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.release")

TASK_PUSH = "release.push"
TASK_PUBLISH = "release.publish"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?([-+][0-9A-Za-z.\-+]+)?$")
CONVENTIONAL = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<desc>.+)$")
GROUPS = [  # (heading, commit types)
    ("Heads up: changes that may need you to act", ()),
    ("New", ("feat", "feature", "add")),
    ("Fixes", ("fix", "bugfix", "hotfix")),
    ("Faster", ("perf",)),
    ("Security", ("security", "sec")),
    ("Docs", ("docs", "doc")),
    ("Behind the scenes", ("refactor", "chore", "ci", "build", "test", "tests", "style", "deps")),
    ("Other changes", ()),
]
SKIP_SUBJECT = re.compile(r"^(Merge (branch|pull request)|release: )", re.I)
# Orion's own version files, the same list as scripts/release.mjs.
ORION_JSON = ["package.json", "apps/desktop/package.json", "packages/shared-types/package.json",
              "apps/desktop/src-tauri/tauri.conf.json", "apps/extension/manifest.json"]
ORION_TEXT = [
    ("apps/desktop/src-tauri/Cargo.toml", r'(?m)^version = "[^"]+"', 'version = "{v}"'),
    ("packages/shared-types/src/index.ts", r"ORION_VERSION = '[^']+'", "ORION_VERSION = '{v}'"),
    ("apps/daemon/main.py", r'os\.getenv\("ORION_APP_VERSION"\) or "[^"]+"', 'os.getenv("ORION_APP_VERSION") or "{v}"'),
]
ORION_HOOKS = [  # checks only: the image itself is built by GitHub Actions from the os-v tag
    {"name": "Image scripts check", "command": "bash -n os/image/build.sh os/image/package-release.sh"},
]


class ReleaseError(Exception):
    pass


def normalize_version(raw: str) -> str:
    m = SEMVER.match((raw or "").strip())
    if not m:
        raise ReleaseError(f"“{raw}” isn't a version number (like 1.1 or 1.1.0).")
    return f"{m.group(1)}.{m.group(2)}.{m.group(3) or 0}{m.group(4) or ''}"


def is_orion(repo: Path) -> bool:
    return (repo / "apps/daemon/main.py").exists() and (repo / "apps/desktop/src-tauri/tauri.conf.json").exists()


# =====================================================================================  git helpers
async def sh(argv: list[str], cwd: Path, timeout: float = 120, env: Optional[dict[str, str]] = None,
             stdin: Optional[bytes] = None) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})})
    except OSError as exc:
        return 127, f"couldn't start {argv[0]}: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def git(repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    return await sh(["git", *args], repo, timeout)


async def git_as_someone(repo: Path, *args: str, timeout: float = 120) -> tuple[int, str]:
    """git commit/tag, falling back to "Orion" as the author when this machine has no git identity set."""
    code, out = await git(repo, *args, timeout=timeout)
    if code != 0 and ("user.email" in out or "who you are" in out):
        code, out = await git(repo, "-c", "user.name=Orion", "-c", "user.email=orion@localhost", *args,
                              timeout=timeout)
    return code, out


async def last_tag(repo: Path) -> str:
    code, out = await git(repo, "describe", "--tags", "--abbrev=0")
    return out.strip() if code == 0 else ""


async def commits_since(repo: Path, tag: str, ref: str = "HEAD") -> list[dict[str, str]]:
    rng = f"{tag}..{ref}" if tag else ref
    code, out = await git(repo, "log", rng, "--no-merges", "--pretty=format:%h%x1f%s%x1f%b%x1e", "-n", "400")
    if code != 0:
        return []
    commits = []
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) >= 2 and parts[1].strip():
            commits.append({"sha": parts[0].strip(), "subject": parts[1].strip(),
                            "body": parts[2].strip() if len(parts) > 2 else ""})
    return commits


# =====================================================================================  changelog
def group_commits(commits: list[dict[str, str]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {h: [] for h, _ in GROUPS}
    for c in commits:
        subj = c["subject"]
        if SKIP_SUBJECT.match(subj):
            continue
        m = CONVENTIONAL.match(subj)
        if m:
            kind = m.group("type").lower()
            desc = m.group("desc").strip()
            desc = desc[:1].upper() + desc[1:]
            scope = m.group("scope")
            line = f"{scope}: {desc}" if scope else desc
            if m.group("bang") or "BREAKING CHANGE" in c.get("body", ""):
                groups[GROUPS[0][0]].append(f"{line} ({c['sha']})")
            heading = next((h for h, kinds in GROUPS if kind in kinds), "Other changes")
        else:
            line, heading = subj, "Other changes"
        groups[heading].append(f"{line} ({c['sha']})")
    return {h: items for h, items in groups.items() if items}


def builtin_changelog(name: str, version: str, commits: list[dict[str, str]], when: Optional[str] = None) -> str:
    groups = group_commits(commits)
    lines = [f"## {name} {version} ({when or date.today().isoformat()})", ""]
    if not groups:
        lines += ["Small fixes and tidying.", ""]
    for heading, items in groups.items():
        lines += [f"### {heading}", *[f"- {i}" for i in items], ""]
    return "\n".join(lines).rstrip() + "\n"


async def make_changelog(repo: Path, name: str, version: str, tag: str,
                         commits: list[dict[str, str]]) -> tuple[str, str]:
    """(markdown, which tool wrote it)."""
    cliff = shutil.which("git-cliff")
    if cliff:
        code, out = await sh([cliff, "--unreleased", "--tag", f"v{version}", "--strip", "all"], repo, 60)
        if code == 0 and out.strip():
            return out.strip() + "\n", "git-cliff"
    return builtin_changelog(name, version, commits), "Orion's commit grouper"


def prepend_changelog(existing: str, section: str) -> str:
    if not existing.strip():
        return "# Changelog\n\n" + section
    m = re.match(r"(# [^\n]*\n+)", existing)
    if m:
        return m.group(1) + section + "\n" + existing[m.end():]
    return section + "\n" + existing


# =====================================================================================  version bumps
def _json_version(text: str, version: str) -> Optional[str]:
    new, n = re.subn(r'^(\s*"version"\s*:\s*)"[^"]*"', lambda m: f'{m.group(1)}"{version}"', text, count=1,
                     flags=re.M)
    return new if n else None


def _toml_section_version(text: str, sections: tuple[str, ...], version: str) -> Optional[str]:
    lines = text.splitlines(True)
    current = ""
    for i, line in enumerate(lines):
        h = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if h:
            current = h.group(1).strip()
            continue
        if current in sections and re.match(r'^\s*version\s*=\s*"[^"]*"', line):
            lines[i] = re.sub(r'"[^"]*"', f'"{version}"', line, count=1)
            return "".join(lines)
    return None


def bump_plan(repo: Path, version: str) -> list[dict[str, Any]]:
    """[{path, before, after}] for every file that holds this project's version."""
    out: list[dict[str, Any]] = []

    def add(rel: str, after: Optional[str], before: str) -> None:
        if after is not None and after != before:
            out.append({"path": rel, "before": before, "after": after})

    def read(rel: str) -> Optional[str]:
        try:
            return (repo / rel).read_text(encoding="utf-8")
        except OSError:
            return None

    if is_orion(repo):
        for rel in ORION_JSON:
            t = read(rel)
            if t is not None:
                add(rel, _json_version(t, version), t)
        for rel, rx, rep in ORION_TEXT:
            t = read(rel)
            if t is not None:
                new, n = re.subn(rx, rep.format(v=version), t, count=1)
                add(rel, new if n else None, t)
        return out
    t = read("package.json")
    if t is not None:
        add("package.json", _json_version(t, version), t)
    t = read("Cargo.toml")
    if t is not None:
        add("Cargo.toml", _toml_section_version(t, ("package", "workspace.package"), version), t)
    t = read("pyproject.toml")
    if t is not None:
        add("pyproject.toml", _toml_section_version(t, ("project", "tool.poetry"), version), t)
    for rel in ("src-tauri/tauri.conf.json", "tauri.conf.json"):
        t = read(rel)
        if t is not None:
            add(rel, _json_version(t, version), t)
    return out


def plan_diff(plan: list[dict[str, Any]]) -> str:
    return "".join("".join(difflib.unified_diff(p["before"].splitlines(True), p["after"].splitlines(True),
                                                fromfile=f"a/{p['path']}", tofile=f"b/{p['path']}"))
                   for p in plan)


def release_config(repo: Path) -> dict[str, Any]:
    try:
        data = json.loads((repo / ".orion/release.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def build_hooks(repo: Path) -> list[dict[str, str]]:
    cfg = release_config(repo)
    hooks = cfg.get("build")
    if isinstance(hooks, list):
        out = []
        for h in hooks:
            if isinstance(h, str):
                out.append({"name": h[:60], "command": h})
            elif isinstance(h, dict) and h.get("command"):
                out.append({"name": str(h.get("name") or h["command"])[:60], "command": str(h["command"])})
        return out
    return list(ORION_HOOKS) if is_orion(repo) else []


# =====================================================================================  the butler
class ReleaseButler:
    def __init__(self, data_dir: Path, board: Any = None, proof: Any = None,
                 notify: Optional[Callable[[str, str], None]] = None) -> None:
        self.dir = Path(data_dir) / "release_butler"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.board = board
        self.proof = proof
        self.notify = notify or (lambda t, b: None)
        self._jobs: set[asyncio.Task] = set()

    # ------------------------------------------------------------ records
    def _path(self, rid: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{10}", rid or ""):
            raise ReleaseError("Unknown release.")
        return self.dir / f"{rid}.json"

    def get(self, rid: str) -> Optional[dict[str, Any]]:
        try:
            return json.loads(self._path(rid).read_text(encoding="utf-8"))
        except (OSError, ValueError, ReleaseError):
            return None

    def save(self, rec: dict[str, Any]) -> dict[str, Any]:
        rec["updated"] = time.time()
        self._path(rec["id"]).write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return rec

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        recs = []
        for p in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                recs.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return recs

    @staticmethod
    def step(rec: dict[str, Any], name: str, ok: Optional[bool], text: str, **extra: Any) -> None:
        rec["steps"] = [s for s in rec.get("steps", []) if s["name"] != name]
        rec["steps"].append({"name": name, "ok": ok, "text": text, **extra})

    # ------------------------------------------------------------ start
    async def start(self, repo: Path, version: str, name: str = "", dry_run: bool = False,
                    wait: bool = True) -> dict[str, Any]:
        repo = Path(repo)
        version = normalize_version(version)
        name = name or ("Orion" if is_orion(repo) else repo.name)
        code, top = await git(repo, "rev-parse", "--show-toplevel")
        if code != 0:
            raise ReleaseError(f"{repo.name} isn't a git repository, so there's nothing to release.")
        repo = Path(top.strip())
        tag = f"v{version}"
        code, _ = await git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
        if code == 0:
            raise ReleaseError(f"{name} {version} already exists (tag {tag}).")
        prev = await last_tag(repo)
        commits = await commits_since(repo, prev)
        changelog, by = await make_changelog(repo, name, version, prev, commits)
        plan = bump_plan(repo, version)
        rec: dict[str, Any] = {
            "id": uuid.uuid4().hex[:10], "repo": str(repo), "name": name, "version": version, "tag": tag,
            "previous_tag": prev, "dry_run": dry_run, "created": time.time(), "state": "preparing",
            "changelog": changelog, "changelog_by": by, "commits": len(commits),
            "bumps": [p["path"] for p in plan], "bump_diff": plan_diff(plan)[:12000],
            "hooks": build_hooks(repo), "orion": is_orion(repo), "steps": [],
        }
        self.step(rec, "changelog", True, f"Changelog from {len(commits)} commit(s) since "
                                          f"{prev or 'the first commit'}, written by {by}.")
        self.step(rec, "bump", bool(plan) or None,
                  f"Version {version} in {', '.join(rec['bumps'])}." if plan else
                  "No file holds a version number, so only the tag carries it.")
        key = os.environ.get("ORION_RELEASE_KEY", "")
        if dry_run:
            self.step(rec, "tests", None, "Would run the full tests through Proof Gate on release/" + tag + ".")
            self.step(rec, "build", None, ("Would run: " + "; ".join(h["command"] for h in rec["hooks"])) if
                      rec["hooks"] else "No build hooks for this project.")
            self.step(rec, "sign", None, "Would sign the release files with minisign." if key and
                      shutil.which("minisign") else "Would skip signing: no release key on this machine.")
            self.step(rec, "draft", None, f"Would draft the GitHub release {tag} with these notes.")
            rec["state"] = "dry_run"
            rec["message"] = self.describe(rec)
            return self.save(rec)
        self.save(rec)
        job = asyncio.get_running_loop().create_task(self._prepare(rec, plan))
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)
        if wait:
            await job
            return self.get(rec["id"]) or rec
        return rec

    async def _prepare(self, rec: dict[str, Any], plan: list[dict[str, Any]]) -> None:
        try:
            await self._prepare_inner(rec, plan)
        except Exception as exc:   # keep the record honest when something unexpected breaks
            log.exception("release %s failed", rec["id"])
            rec["state"] = "failed"
            rec["message"] = f"The release stopped: {type(exc).__name__}: {exc}"[:400]
            self.save(rec)
            self.notify(f"{rec['name']} {rec['version']}: release stopped", rec["message"])

    async def _prepare_inner(self, rec: dict[str, Any], plan: list[dict[str, Any]]) -> None:
        repo = Path(rec["repo"])
        branch = f"release/{rec['tag']}"
        wt = self.dir / "work" / rec["id"]
        wt.parent.mkdir(parents=True, exist_ok=True)
        code, out = await git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD", timeout=300)
        if code != 0:
            return self._fail(rec, "branch", f"Couldn't make the release branch {branch}: {out.strip()[-300:]}")
        rec.update({"worktree": str(wt), "branch": branch})
        for p in plan:
            (wt / p["path"]).write_text(p["after"], encoding="utf-8")
        cl = wt / "CHANGELOG.md"
        cl.write_text(prepend_changelog(cl.read_text(encoding="utf-8") if cl.exists() else "", rec["changelog"]),
                      encoding="utf-8")
        await git(wt, "add", "-A")
        msg = f"release: {rec['name']} {rec['tag']}"
        code, out = await git_as_someone(wt, "commit", "-m", msg)
        if code != 0:
            return self._fail(rec, "bump", "Couldn't commit the version bump: " + out.strip()[-300:])
        code, out = await git_as_someone(wt, "tag", "-a", rec["tag"], "-m", f"{rec['name']} {rec['tag']}")
        if code != 0:
            return self._fail(rec, "bump", "Couldn't tag the release: " + out.strip()[-300:])
        self.save(rec)
        # tests, through Proof Gate (it makes its own throwaway copy of the branch)
        if self.proof is None:
            self.step(rec, "tests", None, "Proof Gate isn't available, so the tests weren't run.")
        else:
            res = await self.proof.prove(target=branch, repo=str(repo), claim="release: tests pass")
            ok = res.get("verdict") == "green"
            self.step(rec, "tests", ok, res.get("reason") or ("Tests pass." if ok else "Tests failed."),
                      proof_id=res.get("id"))
            if not ok:
                return self._fail(rec, None, f"Tests didn't pass, so {rec['name']} {rec['version']} stays on this "
                                             f"machine. {res.get('reason', '')}".strip())
        self.save(rec)
        # build hooks
        for h in rec["hooks"]:
            code, out = await sh(["bash", "-c", h["command"]], wt, timeout=3600)
            if code != 0:
                self.step(rec, "build", False, f"{h['name']} failed: {out.strip()[-300:]}")
                return self._fail(rec, None, f"The build step “{h['name']}” failed.")
        self.step(rec, "build", True if rec["hooks"] else None,
                  ("Build hooks passed: " + ", ".join(h["name"] for h in rec["hooks"]) + ".") if rec["hooks"] else
                  "No build hooks for this project.")
        if rec["orion"]:
            rec["image_tag"] = f"os-{rec['tag']}"
            self.step(rec, "image", None, f"The USB image is built and signed by GitHub Actions when "
                                          f"{rec['image_tag']} is pushed (after your approval).")
        # release files and signatures
        out_dir = self.dir / "out" / rec["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = f"{re.sub(r'[^a-z0-9]+', '-', rec['name'].lower()).strip('-')}-{rec['version']}"
        tarball = out_dir / f"{slug}-source.tar.gz"
        await git(wt, "archive", "--format=tar.gz", f"--prefix={slug}/", "-o", str(tarball), rec["tag"])
        notes = out_dir / f"{slug}-notes.md"
        notes.write_text(rec["changelog"], encoding="utf-8")
        files = [str(p) for p in (notes, tarball) if p.exists()]
        rec["files"] = files
        rec["signatures"] = await self.sign(files)
        self.step(rec, "sign", bool(rec["signatures"]) or None,
                  f"Signed {len(rec['signatures'])} file(s) with minisign." if rec["signatures"] else
                  rec.get("sign_note", "Not signed: no release key on this machine (ORION_RELEASE_KEY)."))
        rec["notes_file"] = str(notes)
        rec["gh_command"] = self.gh_create_argv(rec)
        self.step(rec, "draft", True, f"Patch notes ready; the draft GitHub release {rec['tag']} is created when you "
                                      "approve.")
        rec["state"] = "ready"
        self.save(rec)
        await self.ask_push(rec)

    def _fail(self, rec: dict[str, Any], step: Optional[str], text: str) -> None:
        if step:
            self.step(rec, step, False, text)
        rec["state"] = "failed"
        rec["message"] = text
        self.save(rec)
        self.notify(f"{rec['name']} {rec['version']}: release stopped", text)

    async def sign(self, files: list[str]) -> list[str]:
        key = os.environ.get("ORION_RELEASE_KEY", "")
        mini = shutil.which("minisign")
        if not key or not mini:
            return []
        sigs = []
        pw = os.environ.get("ORION_RELEASE_KEY_PASSWORD")
        for f in files:
            argv = [mini, "-S", "-s", key, "-m", f] + ([] if pw else ["-W"])
            code, out = await sh(argv, Path(f).parent, 120, stdin=(pw + "\n").encode() if pw else None)
            if code == 0 and Path(f + ".minisig").exists():
                sigs.append(f + ".minisig")
            else:
                log.warning("minisign failed for %s: %s", f, out[-200:])
        return sigs

    @staticmethod
    def gh_create_argv(rec: dict[str, Any]) -> list[str]:
        argv = ["gh", "release", "create", rec["tag"], "--draft", "--verify-tag", "--title",
                f"{rec['name']} {rec['version']}", "--notes-file", rec.get("notes_file", "")]
        return argv + list(rec.get("files") or []) + list(rec.get("signatures") or [])

    async def ask_push(self, rec: dict[str, Any]) -> None:
        if self.board is None:
            return
        tags = [rec["tag"]] + ([rec["image_tag"]] if rec.get("image_tag") else [])
        dec = await self.board.propose(
            TASK_PUSH, f"Release {rec['name']} {rec['version']}: push it and draft the GitHub release?",
            {"release": rec["id"], "repo": rec["repo"], "branch": rec["branch"], "tags": tags,
             "tests": next((s["text"] for s in rec["steps"] if s["name"] == "tests"), ""),
             "notes": rec["changelog"][:3000], "signed_files": len(rec.get("signatures") or []),
             "does": f"git push origin {rec['branch']} {' '.join(tags)}; then creates a DRAFT GitHub release "
                     "(not public until you approve publishing it).",
             "why_it_asks": "it pushes to GitHub"},
            proposer="Release Butler", risk="high", simulated=False)
        rec["push_decision"] = dec["id"]
        rec["state"] = "waiting"
        self.save(rec)
        self.notify(f"{rec['name']} {rec['version']} is ready", "Tests passed. Approve the push on your phone.")

    # ------------------------------------------------------------ after approval
    def execute_push(self, d: dict[str, Any], spawn: Callable[[Awaitable[Any]], Any]) -> dict[str, Any]:
        rec = self.get(d["packet"]["details"].get("release", ""))
        if rec is None or rec.get("state") != "waiting":
            return {"state": "failed", "error": "That release isn't waiting any more."}
        spawn(self._push(rec, d["packet"]["details"]))
        return {"state": "started"}

    async def _push(self, rec: dict[str, Any], det: dict[str, Any]) -> dict[str, Any]:
        wt = Path(rec["worktree"])
        rec["state"] = "pushing"
        self.save(rec)
        for ref in [rec["branch"], *det.get("tags", [])]:
            if ref.startswith("os-"):
                await git_as_someone(wt, "tag", "-a", ref, "-m", f"{rec['name']} OS {rec['tag']}")
            code, out = await git(wt, "push", "origin", ref, timeout=600)
            if code != 0:
                self._fail(rec, "push", f"Couldn't push {ref}: {out.strip()[-300:]}")
                return {"state": "failed", "error": rec["message"]}
        self.step(rec, "push", True, f"Pushed {rec['branch']} and {', '.join(det.get('tags', []))}.")
        code, out = await sh(rec["gh_command"], wt, 600)
        if code != 0:
            self._fail(rec, "draft", f"Pushed, but the draft GitHub release couldn't be made: {out.strip()[-300:]}")
            return {"state": "failed", "error": rec["message"]}
        rec["draft_url"] = next((l.strip() for l in out.splitlines() if l.strip().startswith("http")), "")
        self.step(rec, "draft", True, "Draft GitHub release created" + (f": {rec['draft_url']}" if rec["draft_url"]
                                                                        else "."))
        rec["state"] = "drafted"
        rec["message"] = (f"{rec['name']} {rec['version']} is pushed and drafted on GitHub. Say “Orion, publish the "
                          f"{rec['name']} release” when you want it public.")
        self.save(rec)
        self.notify(f"{rec['name']} {rec['version']} drafted", rec["message"])
        return {"state": "done"}

    async def ask_publish(self, rid: str) -> dict[str, Any]:
        rec = self.get(rid)
        if rec is None or rec.get("state") != "drafted":
            raise ReleaseError("There's no drafted release to publish yet.")
        if self.board is None:
            raise ReleaseError("Publishing needs your approval, and Orion can't ask right now.")
        dec = await self.board.propose(
            TASK_PUBLISH, f"Publish {rec['name']} {rec['version']} on GitHub (make the draft public)?",
            {"release": rec["id"], "repo": rec["repo"], "tag": rec["tag"], "notes": rec["changelog"][:3000],
             "does": f"gh release edit {rec['tag']} --draft=false", "why_it_asks": "everyone can see it"},
            proposer="Release Butler", risk="high", simulated=False)
        rec["publish_decision"] = dec["id"]
        self.save(rec)
        return rec

    def execute_publish(self, d: dict[str, Any], spawn: Callable[[Awaitable[Any]], Any]) -> dict[str, Any]:
        rec = self.get(d["packet"]["details"].get("release", ""))
        if rec is None or rec.get("state") != "drafted":
            return {"state": "failed", "error": "That release isn't drafted."}

        async def go() -> None:
            code, out = await sh(["gh", "release", "edit", rec["tag"], "--draft=false"], Path(rec["worktree"]), 300)
            if code != 0:
                self._fail(rec, "publish", f"GitHub didn't publish it: {out.strip()[-300:]}")
                return
            self.step(rec, "publish", True, "Published on GitHub.")
            rec["state"] = "published"
            rec["message"] = f"{rec['name']} {rec['version']} is public."
            self.save(rec)
            self.notify(f"{rec['name']} {rec['version']} is out", "Published on GitHub.")
        spawn(go())
        return {"state": "started"}

    def on_decided(self, d: dict[str, Any]) -> None:
        """Boardroom hook: a declined push leaves the release on this machine, marked as declined."""
        if d.get("task_type") != TASK_PUSH or d.get("status") != "rejected":
            return
        rec = self.get(d["packet"]["details"].get("release", ""))
        if rec is not None and rec.get("state") == "waiting":
            rec["state"] = "declined"
            rec["message"] = (f"You declined pushing {rec['name']} {rec['version']}. The release branch "
                              f"{rec.get('branch')} stays on this computer; nothing was sent.")
            self.save(rec)

    def latest_drafted(self, name: str = "") -> Optional[dict[str, Any]]:
        for r in self.recent(50):
            if r.get("state") == "drafted" and (not name or r.get("name", "").lower() == name.lower()):
                return r
        return None

    # ------------------------------------------------------------ words
    @staticmethod
    def describe(rec: dict[str, Any]) -> str:
        head = {"dry_run": "Dry run", "ready": "Ready", "waiting": "Waiting for your OK", "failed": "Stopped",
                "drafted": "Drafted", "published": "Published", "preparing": "Preparing",
                "pushing": "Pushing"}.get(rec.get("state", ""), rec.get("state", ""))
        lines = [f"{head}: {rec['name']} {rec['version']} (tag {rec['tag']})."]
        for s in rec.get("steps", []):
            mark = "ok" if s["ok"] else ("-" if s["ok"] is None else "FAILED")
            lines.append(f"- {s['name']} [{mark}]: {s['text']}")
        if rec.get("message") and rec.get("state") != "dry_run":
            lines.append(rec["message"])
        return "\n".join(lines)


def _register_task_types() -> None:
    import boardroom
    from boardroom import TaskType
    for t in (TaskType(TASK_PUSH, "Push a release and draft it on GitHub", "code", reversible=False, money=False,
                       sensitive=True),
              TaskType(TASK_PUBLISH, "Publish a release on GitHub", "community", reversible=False, money=False,
                       sensitive=True)):
        boardroom.TASK_TYPES.setdefault(t.key, t)


_register_task_types()
