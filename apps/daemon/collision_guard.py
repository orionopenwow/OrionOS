"""Collision Guard (Orion Toolkit tool 2): stop agents (and you) stepping on each other.

Orion keeps a live map of who is changing which files, per git repository:

  * each coding agent Orion can see (agent_watch / the swarm): its folder, branch and the files it edited;
  * claims: "agent X is about to touch these files/globs" (POST /toolkit/collisions/claim or the MCP tool
    collision_claim), so a warning can come *before* the edit;
  * you: changes in any worktree of a tracked repo that no agent is working in (git status).

When two parties touch the same file, it warns (a `collision.warning` event for the HUD, which draws a red
line between the two robots, plus a phone alert), suggests a separate `git worktree` with the exact command,
and an order for merging. "Orion, is it safe to start?" is check(): a plain yes, or the list of conflicts.

Cheap on purpose: `git status` per worktree at most every SCAN_TTL_S seconds and only for repos where an
agent or a claim is active; GIT_OPTIONAL_LOCKS=0 so it never takes the index lock an agent may need;
node_modules, target, .git, build output and Orion's own .orion folder are ignored. Only file paths are
read, never file contents.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

log = logging.getLogger("orion.collision")

SCAN_TTL_S = 6.0            # one `git status` per worktree per this many seconds at most
WORKTREE_TTL_S = 30.0       # `git worktree list` cache
CLAIM_TTL_S = 4 * 3600.0    # a claim expires after this unless renewed
MAX_CHANGES = 3000          # changed paths kept per worktree
CONFIRM_TICKS = 2           # a conflict must be seen this many updates in a row before it is announced
IGNORED_PARTS = {"node_modules", "target", ".git", ".orion", "dist", "build", ".venv", "venv", "__pycache__",
                 ".next", ".cache", ".pytest_cache", ".mypy_cache"}
OWNER_LABEL = "You"

Publish = Callable[[dict[str, Any]], Awaitable[None]]


# =====================================================================================  git helpers
def git(cwd: Any, *args: str, timeout: float = 20) -> tuple[int, str]:
    """Run git without hooks, pager, colour, prompts or optional locks. (code, stdout+stderr)."""
    try:
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "color.ui=false", "-c", "core.pager=cat",
                            "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
                           stdin=subprocess.DEVNULL, errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr if r.returncode else "")


def toplevel(path: Any) -> Optional[Path]:
    """The worktree root that contains `path`, or None if it isn't inside a git checkout."""
    p = Path(os.path.expanduser(str(path or "")))
    if not str(path or "").strip() or not p.exists():
        return None
    code, out = git(p if p.is_dir() else p.parent, "rev-parse", "--show-toplevel", timeout=10)
    return Path(out.strip()).resolve() if code == 0 and out.strip() else None


def repo_key(worktree: Path) -> Optional[str]:
    """Identifies the repository across all its worktrees (its shared .git folder)."""
    code, out = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=10)
    if code != 0 or not out.strip():
        return None
    return str(Path(out.strip()).resolve())


def current_branch(worktree: Path) -> str:
    code, out = git(worktree, "rev-parse", "--abbrev-ref", "HEAD", timeout=10)
    return out.strip() if code == 0 else ""


def ignored(rel: str) -> bool:
    return any(part in IGNORED_PARTS for part in Path(rel).parts)


def parse_porcelain_z(raw: str) -> list[str]:
    """Paths from `git status --porcelain=v1 -z` (renames give the new path)."""
    out: list[str] = []
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            i += 1   # the next field is the old path
        out.append(path)
    return out


def worktree_changes(worktree: Path) -> list[str]:
    """Changed and new files in one worktree, relative to its root (ignored folders left out)."""
    code, out = git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=30)
    if code != 0:
        return []
    paths = [p.rstrip("/") for p in parse_porcelain_z(out) if p and not ignored(p)]
    return paths[:MAX_CHANGES]


def default_branch(top: Path) -> Optional[str]:
    """origin's default branch if known, else main, else master."""
    code, out = git(top, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    cands = ([out.strip()] if code == 0 and out.strip() else []) + ["main", "master"]
    for c in cands:
        if git(top, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")[0] == 0:
            return c
    return None


def branch_changes(worktree: Path) -> list[str]:
    """Files committed on this worktree's branch since it left the default branch (they clash at merge time)."""
    default = default_branch(worktree)
    if not default:
        return []
    code, mb = git(worktree, "merge-base", default, "HEAD", timeout=15)
    if code != 0 or not mb.strip():
        return []
    code, out = git(worktree, "diff", "--name-only", "--no-renames", mb.strip(), "HEAD", timeout=30)
    return [p for p in out.splitlines() if p.strip() and not ignored(p)][:MAX_CHANGES] if code == 0 else []


def list_worktrees(any_worktree: Path) -> list[tuple[Path, str]]:
    """[(worktree root, branch)] for every worktree of the repo; the main checkout comes first."""
    code, out = git(any_worktree, "worktree", "list", "--porcelain", timeout=15)
    if code != 0:
        return [(any_worktree, current_branch(any_worktree))]
    found: list[tuple[Path, str]] = []
    path: Optional[Path] = None
    branch = ""
    bare = False
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            path, branch, bare = Path(line[9:]).resolve(), "", False
        elif line.startswith("branch "):
            branch = line[7:].removeprefix("refs/heads/")
        elif line == "detached":
            branch = "(detached)"
        elif line == "bare":
            bare = True
        elif not line and path is not None:
            if not bare and path.is_dir():
                found.append((path, branch))
            path = None
    return found or [(any_worktree, current_branch(any_worktree))]


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "agent"


def plain_list(items: list[str], limit: int = 3) -> str:
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else "nothing"
    shown = items[:limit]
    rest = len(items) - len(shown)
    if rest > 0:
        return ", ".join(shown) + f" and {rest} more"
    return ", ".join(shown[:-1]) + " and " + shown[-1]


# =====================================================================================  model
@dataclass
class Party:
    id: str
    label: str
    kind: str                       # agent | claim | owner | asking
    repo: str                       # repo key (shared .git folder)
    worktree: str = ""
    branch: str = ""
    files: set[str] = field(default_factory=set)      # paths relative to the worktree root
    names: set[str] = field(default_factory=set)      # bare file names (agents only report names)
    patterns: list[str] = field(default_factory=list)  # claimed globs
    state: str = "working"
    started: float = 0.0

    def brief(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "worktree": self.worktree,
                "branch": self.branch, "files": sorted(self.files)[:40], "more_files": max(0, len(self.files) - 40),
                "names": sorted(self.names)[:20], "patterns": self.patterns[:20], "state": self.state}


def _glob_hit(path: str, pattern: str) -> bool:
    pattern = pattern.strip().lstrip("./")
    if not pattern:
        return False
    if fnmatch.fnmatch(path, pattern) or path == pattern.rstrip("/"):
        return True
    if pattern.endswith("/") or pattern.endswith("/**"):
        return path.startswith(pattern.rstrip("*").rstrip("/") + "/")
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(path, pattern[3:]) or fnmatch.fnmatch(Path(path).name, pattern[3:])
    return False


def overlap(a: Party, b: Party) -> list[str]:
    """What two parties would both touch (paths, or the claimed pattern / file name when that's all we know)."""
    hits: set[str] = set(a.files & b.files)
    for x, y in ((a, b), (b, a)):
        for pat in x.patterns:
            hits |= {f for f in y.files if _glob_hit(f, pat)}
            for other in y.patterns:
                if other == pat or _glob_hit(other, pat):
                    hits.add(other)
        if x.names:
            hits |= {f for f in y.files if Path(f).name in x.names}
            hits |= {pat for pat in y.patterns if Path(pat).name in x.names and "*" not in Path(pat).name}
    hits |= a.names & b.names
    return sorted(hits)


# =====================================================================================  guard
class CollisionGuard:
    def __init__(self, data_dir: Path, publish: Optional[Publish] = None, extra_repos: Iterable[Any] = (),
                 notify: Optional[Callable[[str, str], None]] = None, clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(data_dir)
        self.path = self.dir / "collision_claims.json"
        self.publish = publish
        self.notify = notify
        self.clock = clock
        self._lock = threading.RLock()
        self._repos: dict[str, Path] = {}        # repo key -> one worktree of it
        self._top_cache: dict[str, tuple[float, Optional[Path], Optional[str]]] = {}
        self._wt_cache: dict[str, tuple[float, list[tuple[Path, str]]]] = {}
        self._ch_cache: dict[str, tuple[float, list[str]]] = {}
        self.claims: list[dict[str, Any]] = self._load()
        self.active: dict[str, dict[str, Any]] = {}   # announced conflicts by id
        self._seen: dict[str, int] = {}
        self.last_agents: list[dict[str, Any]] = []
        extra = [p for p in os.environ.get("ORION_COLLISION_REPOS", "").split(";") if p.strip()]
        for p in [*extra_repos, *extra]:
            try:
                self.track(p)
            except Exception:
                pass

    # ------------------------------------------------------------ storage
    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [c for c in data if isinstance(c, dict) and c.get("id")] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.claims, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Could not save collision claims: %s", exc)

    # ------------------------------------------------------------ repos
    def _locate(self, path: Any) -> tuple[Optional[Path], Optional[str]]:
        """(worktree root, repo key) for a folder, cached for a minute."""
        key = str(path or "")
        hit = self._top_cache.get(key)
        now = self.clock()
        if hit and now - hit[0] < 60:
            return hit[1], hit[2]
        top = toplevel(path)
        rk = repo_key(top) if top else None
        self._top_cache[key] = (now, top, rk)
        if len(self._top_cache) > 256:
            for k in list(self._top_cache)[:128]:
                self._top_cache.pop(k, None)
        return top, rk

    def track(self, path: Any) -> Optional[str]:
        top, rk = self._locate(path)
        if top and rk:
            with self._lock:
                self._repos.setdefault(rk, top)
        return rk

    def repo_name(self, rk: str) -> str:
        wts = self._worktrees(rk)
        return wts[0][0].name if wts else Path(rk).parent.name

    def _worktrees(self, rk: str) -> list[tuple[Path, str]]:
        now = self.clock()
        hit = self._wt_cache.get(rk)
        if hit and now - hit[0] < WORKTREE_TTL_S:
            return hit[1]
        anchor = self._repos.get(rk)
        wts = list_worktrees(anchor) if anchor else []
        # Orion's own throwaway copies (Proof Gate, crash healer) live in its data folder: they aren't anyone's work
        own = self.dir.resolve()
        wts = [(p, b) for p, b in wts if p != own and own not in p.parents]
        self._wt_cache[rk] = (now, wts)
        return wts

    def _changes(self, wt: Path) -> list[str]:
        now = self.clock()
        hit = self._ch_cache.get(str(wt))
        if hit and now - hit[0] < SCAN_TTL_S:
            return hit[1]
        ch = worktree_changes(wt)
        ch += [p for p in branch_changes(wt) if p not in ch]
        self._ch_cache[str(wt)] = (now, ch)
        return ch

    # ------------------------------------------------------------ claims
    def _expire(self) -> None:
        now = self.clock()
        keep = [c for c in self.claims if c.get("expires", 0) > now]
        if len(keep) != len(self.claims):
            self.claims = keep
            self._save()

    def claim(self, party: str, repo: str, files: list[str], branch: str = "", ttl_s: float = CLAIM_TTL_S,
              note: str = "") -> dict[str, Any]:
        party = (party or "").strip()[:80]
        if not party:
            raise ValueError("Say who is claiming the files (party).")
        top, rk = self._locate(repo)
        if not top or not rk:
            raise ValueError(f"{repo} isn't inside a git repository.")
        pats = [self._relative(top, f) for f in files or [] if str(f).strip()][:200]
        if not pats:
            raise ValueError("List the files (or globs like src/ui/**) you are about to change.")
        with self._lock:
            self._repos.setdefault(rk, top)
            self._expire()
            # one live claim per party per repo: a new claim replaces the old one
            self.claims = [c for c in self.claims if not (c["party"].lower() == party.lower() and c["repo"] == rk)]
            c = {"id": f"clm-{uuid.uuid4().hex[:8]}", "party": party, "repo": rk, "worktree": str(top),
                 "branch": branch or current_branch(top), "patterns": pats, "note": note[:200],
                 "created": self.clock(), "expires": self.clock() + max(60.0, min(float(ttl_s), 7 * 86400.0))}
            self.claims.append(c)
            self._save()
        return c

    def release(self, claim_id: str = "", party: str = "", repo: str = "") -> int:
        rk = self._locate(repo)[1] if repo else None
        with self._lock:
            before = len(self.claims)
            self.claims = [c for c in self.claims if not (
                (claim_id and c["id"] == claim_id) or
                (party and c["party"].lower() == party.lower() and (rk is None or c["repo"] == rk)))]
            if len(self.claims) != before:
                self._save()
            return before - len(self.claims)

    @staticmethod
    def _relative(top: Path, f: str) -> str:
        f = str(f).strip().replace("\\", "/")
        p = Path(os.path.expanduser(f))
        if p.is_absolute():
            try:
                return p.resolve().relative_to(top).as_posix()
            except (ValueError, OSError):
                return p.as_posix()
        return f.lstrip("./") if not f.startswith("../") else f

    # ------------------------------------------------------------ the map
    def parties(self, agents: Optional[list[dict[str, Any]]] = None, only_repo: Optional[str] = None
                ) -> dict[str, list[Party]]:
        """Everyone changing something, grouped by repo key."""
        agents = self.last_agents if agents is None else agents
        with self._lock:
            self._expire()
            claims = list(self.claims)
        out: dict[str, list[Party]] = {}
        occupied: set[str] = set()
        by_tool: dict[tuple[str, str], Party] = {}

        located: list[tuple[dict[str, Any], Path, str]] = []
        for a in agents or []:
            top, rk = self._locate(a.get("cwd")) if a.get("cwd") else (None, None)
            if top and rk and not (only_repo and rk != only_repo):
                located.append((a, top, rk))
        per_wt: dict[str, int] = {}
        for _, top, _ in located:
            per_wt[str(top)] = per_wt.get(str(top), 0) + 1
        for a, top, rk in located:
            self._repos.setdefault(rk, top)
            names = {n for n in a.get("files") or [] if n}
            changes = self._changes(top)
            # alone in its folder: every change there is its work; sharing it: only the files it edited
            files = set(changes) if per_wt[str(top)] == 1 else {c for c in changes if Path(c).name in names}
            p = Party(id=str(a["id"]), label=str(a.get("name") or a["id"]), kind="agent", repo=rk,
                      worktree=str(top), branch=current_branch(top), files=files,
                      state=str(a.get("state") or "working"), started=float(a.get("started") or 0))
            out.setdefault(rk, []).append(p)
            occupied.add(str(top))
            by_tool.setdefault((str(a.get("tool", "")).lower(), rk), p)
            by_tool.setdefault((str(a["id"]).lower(), rk), p)

        for c in claims:
            rk = c["repo"]
            if only_repo and rk != only_repo:
                continue
            owner = by_tool.get((c["party"].lower(), rk))
            if owner is not None:   # a claim made by an agent we can see joins that robot
                owner.patterns += [x for x in c["patterns"] if x not in owner.patterns]
                continue
            out.setdefault(rk, []).append(Party(
                id=f"claim:{c['party']}", label=c["party"], kind="claim", repo=rk, worktree=c.get("worktree", ""),
                branch=c.get("branch", ""), patterns=list(c["patterns"]), started=float(c.get("created") or 0)))

        for rk in list(out):
            # you: every other worktree of a repo where something is going on
            for wt, branch in self._worktrees(rk):
                if str(wt) in occupied or any(p.kind == "claim" and p.worktree == str(wt) for p in out[rk]):
                    continue
                ch = self._changes(wt)
                if ch:
                    main = self._worktrees(rk)[0][0]
                    label = OWNER_LABEL if wt == main else f"{OWNER_LABEL} (in {wt.name})"
                    out[rk].append(Party(id=f"owner:{wt}", label=label, kind="owner", repo=rk, worktree=str(wt),
                                         branch=branch, files=set(ch)))
        return out

    # ------------------------------------------------------------ conflicts
    def _conflict(self, a: Party, b: Party, files: list[str]) -> dict[str, Any]:
        if b.kind == "owner" or (a.kind != "owner" and b.started and a.started and b.started < a.started):
            a, b = b, a   # keep "you" / the earlier party first
        same = bool(a.worktree) and a.worktree == b.worktree
        name = self.repo_name(a.repo)
        where = (" in the same folder, so they will overwrite each other" if same else
                 " on different branches, so merging them will clash")
        message = f"{a.label} and {b.label} are both changing {plain_list(files)}{where}."
        advice = self._advice(a, b, same)
        cid = hashlib.sha1("|".join(sorted([a.id, b.id]) + files).encode()).hexdigest()[:12]
        return {"id": cid, "a": a.id, "b": b.id, "a_label": a.label, "b_label": b.label, "repo": name,
                "repo_key": a.repo, "files": files, "same_checkout": same, "message": message, **advice}

    def _advice(self, a: Party, b: Party, same: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if same:
            # the party that isn't you, and started later, moves to its own worktree
            mover = b if b.kind != "owner" else a
            stay = a if mover is b else b
            wt = Path(mover.worktree or stay.worktree)
            s = slug(mover.label.split("·")[0] + "-" + (mover.label.split("·")[-1] if "·" in mover.label else ""))
            new_dir = wt.parent / f"{wt.name}-{s}"
            branch = f"orion/{s}"
            cmd = f"git -C {shlex.quote(str(wt))} worktree add {shlex.quote(str(new_dir))} -b {branch}"
            out["worktree_command"] = cmd
            out["worktree_advice"] = (f"Give {mover.label} its own copy before it carries on: {cmd} "
                                      f"Then start it again in {new_dir}.")
        first, second = sorted((a, b), key=lambda p: (p.state != "done", len(p.files) + len(p.patterns),
                                                      p.started or float("inf")))
        why = "it's finished" if first.state == "done" and second.state != "done" else (
            "it changes less" if len(first.files) + len(first.patterns) < len(second.files) + len(second.patterns)
            else "it started first")
        base = first.branch if first.branch and first.branch != "(detached)" else "main"
        out["merge_order"] = [first.id, second.id]
        out["merge_advice"] = (f"Merge {first.label}'s work first ({why}). Then bring {second.label}'s branch up "
                               f"to date (git rebase {base}) and merge it after.")
        return out

    def conflicts(self, agents: Optional[list[dict[str, Any]]] = None, only_repo: Optional[str] = None
                  ) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for rk, ps in self.parties(agents, only_repo).items():
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    a, b = ps[i], ps[j]
                    if a.kind == "owner" and b.kind == "owner":
                        continue
                    files = overlap(a, b)
                    if files:
                        found.append(self._conflict(a, b, files))
        return found

    # ------------------------------------------------------------ "is it safe to start?"
    def check(self, repo: str, files: Optional[list[str]] = None, party: str = "",
              agents: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        top, rk = self._locate(repo)
        if not top or not rk:
            return {"safe": False, "answer": f"I can't check {repo}: it isn't inside a git repository.",
                    "conflicts": []}
        self.track(top)
        name = self.repo_name(rk)
        parties = self.parties(agents, only_repo=rk).get(rk, [])
        me = (party or "").strip().lower()
        if files:
            asking = Party(id="asking", label=party or "This job", kind="asking", repo=rk, worktree=str(top),
                           branch=current_branch(top),
                           patterns=[self._relative(top, f) for f in files if str(f).strip()])
            others = [p for p in parties if p.id.lower() != me and p.label.lower() != me
                      and not p.label.lower().startswith(me + " ") and p.id != f"claim:{party}"]
            if not party:   # "you" asking about your own folder isn't a conflict with yourself
                others = [p for p in others if not (p.kind == "owner" and p.worktree == str(top))]
            hits = [(p, overlap(asking, p)) for p in others]
            conflicts = [self._conflict(asking, p, f) for p, f in hits if f]
        else:
            conflicts = self.conflicts(agents, only_repo=rk)
        busy = [p.label for p in parties if p.kind != "owner"]
        if not conflicts:
            who = f" ({plain_list(busy)} {'is' if len(busy) == 1 else 'are'} working there, on other files)" if busy else ""
            target = f"those files in {name}" if files else name
            return {"safe": True, "answer": f"Yes, it's safe to start on {target}: nobody else is changing them{who}.",
                    "conflicts": [], "repo": name, "parties": [p.brief() for p in parties]}
        lines = [f"Not yet. {len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''} in {name}:"]
        for c in conflicts:
            lines.append(f"- {c['message']}")
            if c.get("worktree_advice"):
                lines.append(f"  {c['worktree_advice']}")
            lines.append(f"  {c['merge_advice']}")
        return {"safe": False, "answer": "\n".join(lines), "conflicts": conflicts, "repo": name,
                "parties": [p.brief() for p in parties]}

    def snapshot(self, agents: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        groups = self.parties(agents)
        return {"repos": [{"repo": self.repo_name(rk), "parties": [p.brief() for p in ps]} for rk, ps in groups.items()],
                "claims": list(self.claims), "conflicts": list(self.active.values())}

    # ------------------------------------------------------------ live updates
    async def update(self, agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Called every few seconds with the swarm. Announces new conflicts once, clears gone ones."""
        import asyncio
        self.last_agents = list(agents)
        has_work = any(a.get("cwd") for a in agents) or bool(self.claims)
        found = await asyncio.to_thread(self.conflicts, agents) if has_work else []
        ids = {c["id"] for c in found}
        new: list[dict[str, Any]] = []
        for c in found:
            self._seen[c["id"]] = self._seen.get(c["id"], 0) + 1
            if c["id"] not in self.active and self._seen[c["id"]] >= CONFIRM_TICKS:
                self.active[c["id"]] = c
                new.append(c)
            elif c["id"] in self.active:
                self.active[c["id"]] = c
        for cid in list(self._seen):
            if cid not in ids:
                self._seen.pop(cid, None)
        gone = [self.active.pop(cid) for cid in list(self.active) if cid not in ids]
        for c in new:
            await self._emit({"event": "collision.warning", **c})
            if self.notify:
                try:
                    self.notify("Two agents are about to collide", c["message"] + " " + c["merge_advice"])
                except Exception:
                    pass
        for c in gone:
            await self._emit({"event": "collision.cleared", "id": c["id"], "a": c["a"], "b": c["b"],
                              "message": f"{c['a_label']} and {c['b_label']} are no longer changing the same files."})
        return new

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.publish:
            try:
                await self.publish(event)
            except Exception:
                log.debug("collision event not delivered", exc_info=True)

    def decorate(self, snapshot: dict[str, Any]) -> None:
        """agent_swarm decorator: adds the robot pairs that would collide (the HUD draws a red line)."""
        ids = {a["id"] for a in snapshot.get("agents", [])}
        snapshot["collisions"] = [{"id": c["id"], "a": c["a"], "b": c["b"], "files": c["files"][:6],
                                   "message": c["message"]}
                                  for c in self.active.values() if c["a"] in ids and c["b"] in ids]
