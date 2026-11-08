"""Living Orion (self_program.py + orion_layer.py), Phase 8 §7: sandbox, checks, ghost preview, swap, undo,
automatic rollback, protected core, upstream branch. Temp git repos, fake checks and fake health only."""
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

import orion_layer
import protected_core
import self_program as sp_mod
from self_program import SelfProgram, SelfProgramError, detect_undo, gate_level


@pytest.fixture(autouse=True)
def _brain_changes_without_phone(request, monkeypatch):
    # Most tests here check swapping and rollback; the fingerprint rule for brain changes has its own tests below.
    if "fingerprint_rule" not in request.keywords:
        monkeypatch.setattr(sp_mod, "needs_fingerprint", lambda *k: False)

REPO = Path(__file__).resolve().parents[3]
HAS_GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="git is needed")


def run(coro):
    return asyncio.run(coro)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd),
                          capture_output=True, text=True, check=True).stdout


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


THING = "def a():\n    return 1\n"


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "orion"
    (root / "apps/daemon/tests").mkdir(parents=True)
    (root / "apps/desktop/src").mkdir(parents=True)
    (root / "apps/daemon/thing.py").write_text(THING, encoding="utf-8")
    (root / "apps/daemon/other.py").write_text("X = 1\n", encoding="utf-8")
    (root / "apps/daemon/tests/test_thing.py").write_text("import thing\n", encoding="utf-8")
    (root / "apps/daemon/tests/test_unrelated.py").write_text("import other\n", encoding="utf-8")
    (root / "apps/desktop/src/App.tsx").write_text("export const x = 1;\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


class Runner:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or set()

    def __call__(self, cmd, cwd, timeout, env=None):
        self.calls.append((list(cmd), Path(cwd), dict(env or {})))
        joined = " ".join(map(str, cmd))
        for word in self.fail:
            if word in joined:
                return 1, f"FAILED because of {word}"
        return 0, "3 passed"


class Events:
    def __init__(self):
        self.seen = []

    async def __call__(self, ev):
        self.seen.append(ev)

    def kinds(self):
        return [e["event"] for e in self.seen]


def make(repo, tmp_path, **kw):
    restarts = []
    kw.setdefault("runner", Runner())
    sp = SelfProgram(repo_root=repo, data_dir=tmp_path / "data", layer=False, restart=lambda: restarts.append(1),
                     screenshot=lambda url, out: False, admin_levels=tmp_path / "no-admin.json", **kw)
    sp.restarts = restarts
    return sp


def proposal(root, path, new, pid="patch-0000abcd", action=None):
    p = Path(root) / path
    before = p.read_text(encoding="utf-8") if p.exists() else ""
    action = action or ("modify" if p.exists() else "create")
    return {"id": pid, "request": "make a return 2", "summary": "a returns 2", "approach": "edit a()",
            "diff": f"--- a/{path}\n+++ b/{path}\n-{before}\n+{new}\n",
            "files": [{"path": path, "action": action, "additions": 1, "deletions": 1,
                       "original_sha": sha(before) if action == "modify" else None, "new_content": new}]}


# =====================================================================================  phrases
@pytest.mark.parametrize("text", ["Orion, undo that", "undo that", "undo", "Orion undo that please",
                                  "roll that back", "Hey Orion, revert the last change", "put it back",
                                  "orion: undo what you just did."])
def test_undo_phrases(text):
    assert detect_undo(text)


@pytest.mark.parametrize("text", ["undo the email I sent to the team yesterday", "how do I undo in photoshop",
                                  "can you explain git revert", "", "undo that " * 10])
def test_not_undo_phrases(text):
    assert not detect_undo(text)


# =====================================================================================  1-4: the full checkout flow
def test_full_flow_worktree_checks_swap(repo, tmp_path):
    (repo / "apps/daemon/other.py").write_text("X = 2  # my uncommitted edit\n", encoding="utf-8")
    ev = Events()
    sp = make(repo, tmp_path, publish=ev)
    prop = proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")
    c = run(sp.stage(prop))
    assert c["status"] == "ready", c
    assert c["kind"] == "daemon_code" and c["touches_daemon"] and not c["protected"]
    # the running copy was not touched while building and testing
    assert (repo / "apps/daemon/thing.py").read_text() == THING
    # the change was made in a separate git worktree on its own branch, with the uncommitted edit as its base
    assert "orion-self/patch-0000abcd" in git(repo, "branch", "--list")
    log = git(repo, "log", "--format=%s", "orion-self/patch-0000abcd")
    assert "Orion: a returns 2" in log and "Running copy of Orion" in log
    assert "my uncommitted edit" in git(repo, "show", "orion-self/patch-0000abcd:apps/daemon/other.py")
    names = [k["name"] for k in c["checks"]]
    assert names == ["compile", "import", "tests"]
    cmds = [" ".join(map(str, call[0])) for call in sp.runner.calls]
    assert any("import main" in x for x in cmds)
    pytest_cmd = next(x for x in cmds if "pytest" in x)
    assert "tests/test_thing.py" in pytest_cmd and "test_unrelated" not in pytest_cmd
    imp = next(call for call in sp.runner.calls if "import main" in " ".join(call[0]))
    assert imp[2]["ORION_IMPORT_CHECK"] == "1" and str(tmp_path / "data") in imp[2]["ORION_DATA_DIR"]
    assert str(imp[1]).startswith(str(tmp_path / "data" / "selfprog" / "worktrees"))
    # ghost preview data
    full = sp.public(sp.get(c["id"]))
    assert full["reason"] == "edit a()" and "+def a():" in full["diff"] and full["files"][0]["path"]
    assert "new_content" not in json.dumps(sp.state())
    # approve -> hot swap -> restart through the exit-75 path, probation started
    out = sp.approve(c["id"])
    assert out["status"] == "applied"
    assert (repo / "apps/daemon/thing.py").read_text() == "def a():\n    return 2\n"
    assert (repo / "apps/daemon/other.py").read_text().startswith("X = 2")
    assert sp.restarts == [1]
    p = orion_layer.probation(sp.data)
    assert p["needs_daemon"] and not p["needs_ui"]
    assert orion_layer.history(sp.data)[-1]["status"] == "probation"
    assert not (tmp_path / "data/selfprog/worktrees/patch-0000abcd").exists()        # sandbox cleaned up
    assert "orion-self/patch-0000abcd" in git(repo, "branch", "--list")                # branch kept
    assert "SELFPROG_SWAPPED" in ev.kinds()
    with pytest.raises(SelfProgramError):
        sp.approve(c["id"])                                                            # only once


def test_failed_tests_block_the_swap(repo, tmp_path):
    sp = make(repo, tmp_path, runner=Runner(fail={"pytest"}))
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    assert c["status"] == "failed" and "tests" in c["error"].lower()
    with pytest.raises(SelfProgramError, match="passed its checks"):
        sp.approve(c["id"])
    assert (repo / "apps/daemon/thing.py").read_text() == THING


def test_syntax_error_fails_before_running_anything(repo, tmp_path):
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a(:\n")))
    assert c["status"] == "failed" and c["checks"][0]["name"] == "compile"
    assert sp.runner.calls == []


def test_running_copy_changed_meanwhile(repo, tmp_path):
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    (repo / "apps/daemon/thing.py").write_text("def a():\n    return 7\n", encoding="utf-8")
    with pytest.raises(SelfProgramError, match="changed since"):
        sp.approve(c["id"])


def test_ui_change_skips_hud_checks_without_node_and_reloads(repo, tmp_path, monkeypatch):
    ev = Events()
    sp = make(repo, tmp_path, publish=ev, hud_connected=lambda: True)
    c = run(sp.stage(proposal(repo, "apps/desktop/src/App.tsx", "export const x = 2;\n")))
    assert c["kind"] == "ui_code" and c["status"] == "ready"
    skipped = {k["name"]: k for k in c["checks"]}
    assert skipped["hud_types"]["skipped"] and skipped["visual"]["skipped"]
    assert "aren't installed" in skipped["visual"]["detail"]
    sp.approve(c["id"])
    swapped = [e for e in ev.seen if e["event"] == "SELFPROG_SWAPPED"][0]
    assert swapped["reload_ui"] and not swapped["restart_daemon"] and sp.restarts == []
    assert orion_layer.probation(sp.data)["needs_ui"]
    sp.stop()


def test_hud_checks_and_visual_hook_when_tools_exist(repo, tmp_path, monkeypatch):
    shots = []

    def fake_shot(url, out):
        from PIL import Image
        shots.append(url)
        Image.new("RGB", (40, 20), (0, 0, 0) if "before" in str(out) else (0, 0, 0)).save(out)
        if "after" in str(out):
            img = Image.new("RGB", (40, 20), (0, 0, 0))
            img.paste((255, 255, 255), (0, 0, 20, 20))
            img.save(out)
        return True

    class HudRunner(Runner):
        def __call__(self, cmd, cwd, timeout, env=None):
            if "vite" in cmd:
                outdir = Path(cmd[cmd.index("--outDir") + 1])
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "index.html").write_text("<p>hud</p>", encoding="utf-8")
            return super().__call__(cmd, cwd, timeout, env)

    (repo / "apps/desktop/node_modules").mkdir()
    monkeypatch.setattr(sp_mod, "find_renderer", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(sp_mod.shutil, "which", lambda n: "/usr/bin/" + n)
    sp = make(repo, tmp_path, runner=HudRunner())
    sp.screenshot = fake_shot
    c = run(sp.stage(proposal(repo, "apps/desktop/src/App.tsx", "export const x = 2;\n")))
    checks = {k["name"]: k for k in c["checks"]}
    assert c["status"] == "ready", c
    assert checks["hud_types"]["ok"] and checks["hud_build"]["ok"]
    assert checks["visual"]["ok"] and not checks["visual"]["skipped"] and "50.0%" in checks["visual"]["detail"]
    assert len(shots) == 2 and all("/ghost-" in u for u in shots)
    assert sp.screen_path(c["id"], "after") and sp.screen_path(c["id"], "before")
    url = sp.preview_url(c["id"])
    import urllib.request
    assert b"hud" in urllib.request.urlopen(url, timeout=5).read()
    with pytest.raises(Exception):
        urllib.request.urlopen(url.split("/ghost-")[0] + "/index.html", timeout=5).read()   # only under the secret path
    sp.stop()


# =====================================================================================  5: undo + rollback
def test_undo_last_and_refusal_after_manual_edit(repo, tmp_path):
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    sp.approve(c["id"])
    (repo / "apps/daemon/thing.py").write_text("def a():\n    return 2  # mine\n", encoding="utf-8")
    with pytest.raises(SelfProgramError, match="edited after"):
        sp.undo_last()
    sp.undo_last(force=True)
    assert (repo / "apps/daemon/thing.py").read_text() == THING
    assert sp.get(c["id"])["status"] == "rolled_back"
    assert orion_layer.probation(sp.data) is None and sp.restarts == [1, 1]
    with pytest.raises(SelfProgramError, match="no change"):
        sp.undo_last()


def test_undo_removes_created_files(repo, tmp_path):
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/brand_new.py", "Y = 1\n")))
    sp.approve(c["id"])
    assert (repo / "apps/daemon/brand_new.py").exists()
    sp.undo_last()
    assert not (repo / "apps/daemon/brand_new.py").exists()


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def swapped(repo, tmp_path, health):
    clock = Clock()
    sp = make(repo, tmp_path, health=health, clock=clock)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    sp.approve(c["id"])
    return sp, clock


def test_auto_rollback_when_health_fails_after_swap(repo, tmp_path):
    answers = [True, True, False, False, False]
    sp, clock = swapped(repo, tmp_path, lambda: answers.pop(0))
    states = []
    for _ in range(5):
        clock.t += 5
        states.append(sp.watch_once())
    assert states[-1] == "rolled_back"
    assert (repo / "apps/daemon/thing.py").read_text() == THING
    assert "health check failed" in orion_layer.history(sp.data)[-1]["rollback_reason"]
    assert sp.restarts == [1, 1]


def test_auto_rollback_when_never_healthy_in_two_minutes(repo, tmp_path):
    sp, clock = swapped(repo, tmp_path, lambda: False)
    clock.t += 30
    assert sp.watch_once() == "waiting"
    clock.t += orion_layer.PROBATION_S
    assert sp.watch_once() == "rolled_back"
    assert (repo / "apps/daemon/thing.py").read_text() == THING


def test_kept_when_healthy_for_two_minutes(repo, tmp_path):
    sp, clock = swapped(repo, tmp_path, lambda: True)
    clock.t += 5
    assert sp.watch_once() == "waiting"
    clock.t += orion_layer.PROBATION_S
    assert sp.watch_once() == "kept"
    assert orion_layer.history(sp.data)[-1]["status"] == "kept" and orion_layer.probation(sp.data) is None
    assert (repo / "apps/daemon/thing.py").read_text().endswith("return 2\n")
    sp.undo_last()                                    # "undo that" still works after probation
    assert (repo / "apps/daemon/thing.py").read_text() == THING


def test_ui_rollback_when_screen_never_comes_back(repo, tmp_path):
    clock = Clock()
    sp = make(repo, tmp_path, hud_connected=lambda: True, clock=clock)
    sp.start = lambda: None                            # step the watch by hand (no background thread)
    c = run(sp.stage(proposal(repo, "apps/desktop/src/App.tsx", "export const x = 2;\n")))
    sp.approve(c["id"])
    sp.stop()
    clock.t += orion_layer.PROBATION_S + 1
    assert sp.watch_once() == "rolled_back"
    assert (repo / "apps/desktop/src/App.tsx").read_text() == "export const x = 1;\n"
    c2 = run(sp.stage(proposal(repo, "apps/desktop/src/App.tsx", "export const x = 3;\n", pid="patch-0000beef")))
    sp.approve(c2["id"])
    sp.stop()
    sp.ui_ok()
    clock.t += orion_layer.PROBATION_S + 1
    assert sp.watch_once() == "kept"


def test_boot_guard_rolls_back_when_daemon_doesnt_come_up(repo, tmp_path):
    sp, clock = swapped(repo, tmp_path, lambda: True)
    assert orion_layer.boot_guard(sp.data, now=clock.t + 1) is None       # first start of the new code
    assert orion_layer.probation(sp.data)["boots"] == 1
    rolled = orion_layer.boot_guard(sp.data, now=clock.t + 3)             # started again, never answered
    assert rolled and rolled["status"] == "rolled_back"
    assert (repo / "apps/daemon/thing.py").read_text() == THING


def test_boot_guard_keeps_a_change_that_was_healthy(repo, tmp_path):
    sp, clock = swapped(repo, tmp_path, lambda: True)
    orion_layer.boot_guard(sp.data, now=clock.t + 1)
    clock.t += 5
    sp.watch_once()                                                       # answered /health
    assert orion_layer.boot_guard(sp.data, now=clock.t + 2) is None        # a normal restart: keep probation
    assert orion_layer.probation(sp.data)["boots"] == 2


def test_revert_last_cli_uses_the_swap_history(repo, tmp_path, monkeypatch):
    import self_evolve
    from config import settings
    sp, _ = swapped(repo, tmp_path, lambda: True)
    monkeypatch.setattr(settings.__class__, "data_dir", property(lambda s: sp.data))
    assert self_evolve._revert_last_cli() == 0
    assert (repo / "apps/daemon/thing.py").read_text() == THING


def test_history_is_small(repo, tmp_path):
    hist = [{"id": f"swap-{i}", "status": "kept"} for i in range(40)]
    orion_layer.save_history(tmp_path, hist)
    assert len(orion_layer.history(tmp_path)) == orion_layer.HISTORY_KEEP


# =====================================================================================  6: protected core
def _protected_cases():
    out = []
    for r in protected_core.rules():
        path = r["path"].replace("*.nft*", "killswitch.nft.tmpl")
        out.append(pytest.param(path, r["symbols"], id=path))
    return out


def _protected_edit(path, symbols):
    real = REPO / path
    if path.endswith(".json"):
        before = real.read_text(encoding="utf-8")
        return before, before.replace('"version": 1', '"version": 2')
    if real.exists():
        before = real.read_text(encoding="utf-8")
    else:
        before = "#!/usr/bin/env python3\nprint('gate')\n"
    if not symbols:
        return before, before + "\n# changed by Orion\n"
    ranges = protected_core._ranges(before)
    start, end = ranges[symbols[0]][0]
    lines = before.splitlines(keepends=True)
    lines[start - 1] = lines[start - 1].rstrip("\n") + "  # changed by Orion\n"
    return before, "".join(lines)


class SignedBoard:
    """Stands in for boardroom.Boardroom.get(): decisions as a signed phone verdict leaves them."""

    def __init__(self):
        self.decisions = {}
        self.executors = {}
        self.on_decided = []

    def get(self, did):
        return self.decisions.get(did)

    def add(self, did, cid, diff_sha, status="approved", signer="phone:Pixel AB:CD", task=sp_mod.SELF_PROTECTED_TASK):
        self.decisions[did] = {"id": did, "task_type": task, "status": status, "signer": signer,
                               "packet": {"details": {"change_id": cid, "diff_sha256": diff_sha}}}


@pytest.mark.parametrize("path,symbols", _protected_cases())
def test_each_protected_path_is_refused_without_a_signature(tmp_path, path, symbols):
    root = tmp_path / "orion"
    before, after = _protected_edit(path, symbols)
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(before, encoding="utf-8")
    git(root, "init", "-q")
    board = SignedBoard()
    sp = make(root, tmp_path, board=board)
    c = sp.new_change(proposal(root, path, after))
    assert c["kind"] == "protected_core" and c["protected"], path
    c["status"] = "ready"                              # (the build itself is tested above)
    sp._save(c)
    # no signature, a made-up id, an unsigned verdict, a rejected one, another change's, another type's: all refused
    board.add("dec-unsigned", c["id"], c["diff_sha256"], signer="")
    board.add("dec-rejected", c["id"], c["diff_sha256"], status="rejected")
    board.add("dec-other", "patch-ffffffff", c["diff_sha256"])
    board.add("dec-stale", c["id"], "0" * 64)
    board.add("dec-type", c["id"], c["diff_sha256"], task="merge.code")
    for sig in (None, "dec-nope", "dec-unsigned", "dec-rejected", "dec-other", "dec-stale", "dec-type"):
        with pytest.raises(SelfProgramError, match="protected core"):
            sp.approve(c["id"], signature=sig)
        assert p.read_text(encoding="utf-8") == before
    # and never automatically, whatever the levels say
    (tmp_path / "admin.json").write_text(json.dumps({"levels": {"protected_core": {"level": 3}, "daemon_code":
                                                                {"level": 3}, "ui_code": {"level": 3}}}))
    sp.admin_levels = tmp_path / "admin.json"
    run(sp._after_ready(sp.get(c["id"])))
    assert p.read_text(encoding="utf-8") == before and sp.get(c["id"])["status"] == "ready"
    # with the phone's signed approval of exactly this change, it goes in
    board.add("dec-good", c["id"], c["diff_sha256"])
    sp.approve(c["id"], signature="dec-good")
    assert p.read_text(encoding="utf-8") == after


def test_protected_label_is_rechecked_at_approve_time(repo, tmp_path):
    """A change stored as harmless is still refused if what it writes touches the core."""
    sp = make(repo, tmp_path)
    (repo / "apps/daemon/api_token.py").write_text("T = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "token")
    c = sp.new_change(proposal(repo, "apps/daemon/api_token.py", "T = 2\n"))
    c.update(kind="daemon_code", protected=[], status="ready")
    sp._save(c)
    with pytest.raises(SelfProgramError, match="protected core"):
        sp.approve(c["id"])


def test_real_boardroom_signature_flow(repo, tmp_path):
    import boardroom
    board = boardroom.Boardroom(tmp_path / "board")
    sp = make(repo, tmp_path, board=board)
    sp.install_board()
    (repo / "apps/daemon/approval_queue.py").write_text("Q = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    c = run(sp.stage(proposal(repo, "apps/daemon/approval_queue.py", "Q = 2\n")))
    assert c["status"] == "ready" and c["decision"]                       # asked the phone by itself
    d = board.get(c["decision"])
    assert d["status"] == "pending" and d["task_type"] == sp_mod.SELF_PROTECTED_TASK
    assert boardroom.TASK_TYPES[sp_mod.SELF_PROTECTED_TASK].max_level == 1   # can never auto-run
    with pytest.raises(SelfProgramError):
        sp.approve(c["id"])                                               # the screen can't approve it
    run(board.verdict(d["id"], "approve", boardroom.packet_hash(d["packet"]), signer="phone:Pixel AB:CD"))
    assert (repo / "apps/daemon/approval_queue.py").read_text() == "Q = 2\n"
    assert sp.get(c["id"])["status"] == "applied"


def test_real_boardroom_unsigned_verdict_does_nothing(repo, tmp_path):
    import boardroom
    board = boardroom.Boardroom(tmp_path / "board")
    sp = make(repo, tmp_path, board=board)
    sp.install_board()
    (repo / "apps/daemon/approval_queue.py").write_text("Q = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    c = run(sp.stage(proposal(repo, "apps/daemon/approval_queue.py", "Q = 2\n")))
    d = board.get(c["decision"])
    run(board.verdict(d["id"], "approve", boardroom.packet_hash(d["packet"]), signer=""))
    assert (repo / "apps/daemon/approval_queue.py").read_text() == "Q = 1\n"
    assert "protected core" in sp.get(c["id"])["error"]


def test_old_direct_patch_path_refuses_the_core(tmp_path):
    from self_evolve import PatchError, SelfEvolveEngine
    root = tmp_path / "r"
    (root / "apps/daemon").mkdir(parents=True)
    (root / "apps/daemon/api_token.py").write_text("T = 1\n", encoding="utf-8")
    eng = SelfEvolveEngine(None, root, tmp_path / "d")
    prop = eng._build({"summary": "s", "edits": [{"path": "apps/daemon/api_token.py", "find": "T = 1",
                                                  "replace": "T = 2"}]}, {"apps/daemon/api_token.py": "T = 1\n"}, "x")
    eng.proposals[prop["id"]] = prop
    with pytest.raises(PatchError, match="protected core"):
        eng.apply(prop["id"])
    assert (root / "apps/daemon/api_token.py").read_text() == "T = 1\n"


# =====================================================================================  levels (admin file wins)
def _gate_script():
    path = REPO / "os/safety/orion-self-gate"
    loader = SourceFileLoader("orion_self_gate_sp", str(path))
    spec = importlib.util.spec_from_loader("orion_self_gate_sp", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.mark.skipif(sys.platform == "win32", reason="the system gate is Linux only")
@pytest.mark.parametrize("admin,mine", [(None, None), (2, None), (None, 2), (2, 1), (1, 2), (3, None), (None, 3)])
@pytest.mark.parametrize("kind", ["ui_code", "daemon_code", "protected_core"])
def test_levels_read_like_the_system_gate(tmp_path, monkeypatch, admin, mine, kind):
    data = tmp_path / "data"
    data.mkdir()
    adm = tmp_path / "admin.json"
    if admin is not None:
        adm.write_text(json.dumps({"levels": {kind: {"level": admin, "at": 1}}}))
    if mine is not None:
        (data / "self_levels.json").write_text(json.dumps({"levels": {kind: {"level": mine, "at": 2}}}))
    monkeypatch.setenv("ORION_SELF_LEVELS_ADMIN", str(adm))
    monkeypatch.setenv("ORION_DATA_DIR", str(data))
    monkeypatch.setenv("ORION_USER", "nobody-here-xyz")
    monkeypatch.setenv("ORION_FREEZE_MARK", str(tmp_path / "no-freeze"))
    assert gate_level(kind, data, adm) == _gate_script().level(kind)


def test_tell_me_after_swaps_screen_changes_by_itself(repo, tmp_path):
    adm = tmp_path / "admin.json"
    adm.write_text(json.dumps({"levels": {"ui_code": {"level": 2}, "daemon_code": {"level": 3}}}))
    sp = make(repo, tmp_path)
    sp.admin_levels = adm
    c = run(sp.stage(proposal(repo, "apps/desktop/src/App.tsx", "export const x = 2;\n")))
    assert c["status"] == "applied" and "tell me after" in c["applied_by"]
    d = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n", pid="patch-0000d00d")))
    assert d["status"] == "ready"                                         # daemon code never swaps by itself


def test_frozen_blocks_new_changes_but_not_undo(repo, tmp_path, monkeypatch):
    import freeze
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    sp.approve(c["id"])
    monkeypatch.setattr(freeze, "is_frozen", lambda: True)
    with pytest.raises(SelfProgramError, match="frozen"):
        sp.new_change(proposal(repo, "apps/daemon/other.py", "X = 5\n", pid="patch-0000f00d"))
    sp.undo_last()
    assert (repo / "apps/daemon/thing.py").read_text() == THING


# =====================================================================================  layer mode (OrionOS image)
@pytest.fixture()
def image(tmp_path, monkeypatch):
    img = tmp_path / "opt/orion/daemon"
    img.mkdir(parents=True)
    (img / "main.py").write_text("print('image')\n", encoding="utf-8")
    (img / "thing.py").write_text(THING, encoding="utf-8")
    (img / "rules.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("ORION_LAYER_DIR", raising=False)
    monkeypatch.delenv("ORION_LAYER_BASE", raising=False)
    return img


def make_layer(image, tmp_path, **kw):
    restarts = []
    sp = SelfProgram(repo_root=tmp_path / "opt", data_dir=tmp_path / "data", daemon_dir=image, layer=True,
                     runner=Runner(), screenshot=lambda u, o: False, restart=lambda: restarts.append(1),
                     admin_levels=tmp_path / "no-admin.json", **kw)
    sp.restarts = restarts
    return sp


def test_layer_mode_never_touches_the_image(image, tmp_path):
    sp = make_layer(image, tmp_path)
    view = sp.view_root()
    c = run(sp.stage(proposal(view, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    assert c["status"] == "ready" and c["mode"] == "layer"
    sp.approve(c["id"])
    assert (image / "thing.py").read_text() == THING                      # read-only image untouched
    n = orion_layer.current_gen(sp.data)
    gen = orion_layer.gen_dir(sp.data, n) / "apps/daemon"
    assert n == 1 and (gen / "thing.py").read_text().endswith("return 2\n")
    assert (gen / "rules.json").exists() and (gen / "main.py").exists()   # a full copy: data files come along
    assert sp.restarts == [1]
    # a second change builds on the first generation
    c2 = run(sp.stage(proposal(sp.view_root(), "apps/daemon/thing.py", "def a():\n    return 3\n",
                               pid="patch-0000aaaa")))
    sp.approve(c2["id"])
    assert orion_layer.current_gen(sp.data) == 2
    sp.undo_last()
    assert orion_layer.current_gen(sp.data) == 1
    sp.undo_last()
    assert orion_layer.current_gen(sp.data) == 0


def test_layer_mode_boot_guard_goes_back_a_generation(image, tmp_path):
    sp = make_layer(image, tmp_path)
    c = run(sp.stage(proposal(sp.view_root(), "apps/daemon/thing.py", "def a():\n    return 2\n")))
    sp.approve(c["id"])
    orion_layer.boot_guard(sp.data)
    orion_layer.boot_guard(sp.data)
    assert orion_layer.current_gen(sp.data) == 0


def test_layer_mode_refuses_screen_changes(image, tmp_path):
    sp = make_layer(image, tmp_path)
    view = sp.view_root()
    (view / "apps/desktop/src").mkdir(parents=True)
    (view / "apps/desktop/src/App.tsx").write_text("x\n", encoding="utf-8")
    c = sp.new_change(proposal(view, "apps/desktop/src/App.tsx", "y\n"))
    c["status"] = "ready"
    sp._save(c)
    with pytest.raises(SelfProgramError, match="source checkout"):
        sp.approve(c["id"])


def test_activate_runs_the_current_generation(image, tmp_path, monkeypatch):
    data = tmp_path / "data"
    calls = []
    monkeypatch.setattr(orion_layer.os, "execv", lambda exe, argv: calls.append(argv))
    monkeypatch.setattr(orion_layer.os, "chdir", lambda d: None)
    monkeypatch.setenv("ORION_DATA_DIR", str(data))
    orion_layer.activate(str(image / "main.py"), data)
    assert calls == []                                                    # no layer yet: run the image
    n = orion_layer.new_gen(data, _as_repo(image, tmp_path), {"apps/daemon/thing.py": b"X = 9\n"})
    orion_layer.set_gen(data, n)
    if os.name == "nt":
        pytest.skip("the layer is only used on OrionOS")
    orion_layer.activate(str(image / "main.py"), data)
    target = orion_layer.gen_dir(data, n) / "apps/daemon/main.py"
    assert calls and calls[-1][1] == str(target)
    calls.clear()
    orion_layer.activate(str(target), data)                               # already running it
    assert calls == []
    orion_layer.set_gen(data, 0)                                          # rolled back while running a generation
    orion_layer.activate(str(target), data)
    assert calls and calls[-1][1] == str(image / "main.py")
    calls.clear()
    monkeypatch.setenv("ORION_IMPORT_CHECK", "1")
    orion_layer.set_gen(data, n)
    orion_layer.activate(str(image / "main.py"), data)
    assert calls == []
    orion_layer.activate(None, data)
    assert calls == []


def _as_repo(image, tmp_path):
    root = tmp_path / "as-repo"
    if not root.exists():
        (root / "apps").mkdir(parents=True)
        orion_layer._copytree(image, root / "apps/daemon")
    return root


def test_layer_mode_detection(tmp_path, monkeypatch):
    monkeypatch.delenv("ORION_LAYER_MODE", raising=False)
    assert orion_layer.layer_mode(tmp_path / "opt", tmp_path / "opt/orion/daemon")       # no checkout: layer
    monkeypatch.setenv("ORION_LAYER_MODE", "0")
    assert not orion_layer.layer_mode(tmp_path, tmp_path)


# =====================================================================================  7: upstream (never pushed)
def test_offer_upstream_makes_a_local_branch(repo, tmp_path):
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    sp.approve(c["id"])
    out = sp.offer_upstream(c["id"])
    assert out["ok"] and out["branch"] == "orion-self/patch-0000abcd-pr"
    assert "return 2" in git(repo, "show", f"{out['branch']}:apps/daemon/thing.py")
    assert git(repo, "log", "-1", "--format=%s", out["branch"]).startswith("Orion: a returns 2")
    assert git(repo, "remote").strip() == ""                               # nothing pushed, no remote added
    assert "Nothing was pushed" in out["message"]
    body = Path(out["pull_request"]).read_text()
    assert "Why: edit a()" in body
    assert Path(out["patch"]).read_text().startswith("From ")


def test_offer_upstream_when_change_needs_uncommitted_edits(repo, tmp_path):
    (repo / "apps/daemon/thing.py").write_text("def a():\n    return 5\n", encoding="utf-8")
    sp = make(repo, tmp_path)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 6\n")))
    out = sp.offer_upstream(c["id"])
    assert not out["ok"] and "aren't committed" in out["message"]
    assert "-pr" not in git(repo, "branch", "--list")


# =====================================================================================  HTTP
def test_routes(repo, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sp = make(repo, tmp_path)
    app = FastAPI()
    app.include_router(sp_mod.make_router(lambda: sp))
    cl = TestClient(app)
    c = run(sp.stage(proposal(repo, "apps/daemon/thing.py", "def a():\n    return 2\n")))
    st = cl.get("/self/changes").json()
    assert st["mode"] == "checkout" and st["changes"][0]["id"] == c["id"] and "diff" not in st["changes"][0]
    assert "+def a():" in cl.get(f"/self/changes/{c['id']}").json()["change"]["diff"]
    assert cl.get("/self/changes/../../etc").status_code == 404
    assert cl.get(f"/self/changes/{c['id']}/preview").json() == {"url": None}
    assert cl.get(f"/self/changes/{c['id']}/screens/after").status_code == 404
    assert cl.post(f"/self/changes/{c['id']}/approve").json()["ok"]
    r = cl.post("/self/undo", json={})
    assert r.json().get("ok"), r.json()
    assert cl.post("/self/undo", json={}).status_code == 409
    assert cl.post("/self/probation/ui-ok").json() == {"ok": True}
    # protected: the screen gets a 403 with plain words
    (repo / "apps/daemon/api_token.py").write_text("T = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "t")
    p = run(sp.stage(proposal(repo, "apps/daemon/api_token.py", "T = 2\n", pid="patch-00001111")))
    r = cl.post(f"/self/changes/{p['id']}/approve")
    assert r.status_code == 403 and "fingerprint" in r.json()["message"]


@pytest.mark.fingerprint_rule
def test_brain_change_needs_the_phone(repo, tmp_path):
    """self_levels: "Orion changing its own brain - needs your fingerprint". The screen can't approve it."""
    import boardroom
    board = boardroom.Boardroom(tmp_path / "board")
    sp = make(repo, tmp_path, board=board)
    sp.install_board()
    (repo / "apps/daemon/brainy.py").write_text("X = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "b")
    c = run(sp.stage(proposal(repo, "apps/daemon/brainy.py", "X = 2\n")))
    assert c["status"] == "ready" and c["decision"]                       # asked the phone by itself
    with pytest.raises(SelfProgramError, match="fingerprint"):
        sp.approve(c["id"])
    d = board.get(c["decision"])
    assert "its own code" in json.dumps(d)
    run(board.verdict(d["id"], "approve", boardroom.packet_hash(d["packet"]), signer="phone:Pixel AB:CD"))
    assert (repo / "apps/daemon/brainy.py").read_text() == "X = 2\n"


@pytest.mark.fingerprint_rule
def test_needs_fingerprint_reads_the_levels():
    assert sp_mod.needs_fingerprint("daemon_code") is True
    assert sp_mod.needs_fingerprint("ui_code") is False
