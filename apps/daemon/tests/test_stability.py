"""Stability pass before the first phone + PC test session (2026-11).

Each test here pins a bug found by actually starting the daemon and hitting every route."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

DAEMON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DAEMON))

FASTAPI_PARAM_TYPES = ("Request", "WebSocket", "Response", "BackgroundTasks", "UploadFile")


def _module_names(tree: ast.Module) -> set[str]:
    """Names bound at module level, including inside top-level try/except blocks."""
    names: set[str] = set()

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Try):
                visit(node.body)
                for h in node.handlers:
                    visit(h.body)
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)

    visit(tree.body)
    return names


def test_route_parameter_types_resolve_from_module_globals():
    """With `from __future__ import annotations`, FastAPI reads `req: Request` as the string "Request" and looks it
    up in the module's globals. If Request was only imported inside make_router, FastAPI treats `req` as a required
    query parameter: every POST answers 422 and /openapi.json crashes with a 500 (found on /stardust/* and
    /outreach/*)."""
    bad = []
    for f in sorted(DAEMON.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and
                     any(a.name == "annotations" for a in n.names) for n in tree.body)
        if not future:
            continue
        top = _module_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and
                       d.func.attr in ("get", "post", "put", "delete", "patch", "websocket")
                       for d in node.decorator_list):
                continue
            for arg in node.args.args + node.args.kwonlyargs:
                ann = arg.annotation
                if isinstance(ann, ast.Name) and ann.id in FASTAPI_PARAM_TYPES and ann.id not in top:
                    bad.append(f"{f.name}:{node.lineno} {node.name}({arg.arg}: {ann.id})")
    assert not bad, "route parameter type not importable from module globals: " + ", ".join(bad)


def test_stardust_and_outreach_routes_take_the_request_body():
    from fastapi import FastAPI
    from fastapi.openapi.utils import get_openapi

    import outreach_engine
    import stardust

    app = FastAPI()
    app.include_router(stardust.make_router(lambda: None))
    app.include_router(outreach_engine.make_router(lambda: None))
    spec = get_openapi(title="t", version="1", routes=app.routes)   # crashed before the fix
    for path, ops in spec["paths"].items():
        for op in ops.values():
            assert not any(p.get("name") == "req" for p in op.get("parameters", [])), path


def test_boardroom_state_with_task_types_added_after_start(tmp_path):
    """A task type registered after the learner was built (missions, rulebook, freeze...) had no row, so
    /boardroom/state answered 500 on every fresh install."""
    import boardroom
    from boardroom import AutonomyLearner, Store, TaskType

    learner = AutonomyLearner(Store(tmp_path / "b.db"))
    key = "test.late_type"
    boardroom.TASK_TYPES.setdefault(key, TaskType(key, "Late type", "code", reversible=True, money=False))
    try:
        rows = {r["task_type"]: r for r in learner.summary()}
        assert rows[key]["approvals"] == 0
        learner.record(key, "approve")
        assert learner.row(key)["approvals"] == 1
    finally:
        boardroom.TASK_TYPES.pop(key, None)


def test_daemon_starts_when_new_parts_fail_to_import(tmp_path):
    """Each new part (self-reprogramming, phone pairing, desktop skills, core signals, desktop shell) is loaded
    through module_guard: if one can't import (missing add-on, Windows-only gap, a bad self-made change), only that
    part is switched off and the daemon still builds its app and answers /health/full."""
    import os
    import subprocess

    broken = ["self_program", "phone_pair", "desktop_skills_hub", "desktop_skill", "core_signals", "desktop_shell"]
    code = (
        "import sys, json\n"
        f"for m in {broken!r}: sys.modules[m] = None\n"
        f"sys.path.insert(0, {str(DAEMON)!r})\n"
        "import main, module_guard\n"
        "from fastapi.testclient import TestClient\n"
        "off = sorted(f['module'] for f in module_guard.failed())\n"
        "assert main.daemon.selfprog is None and main.daemon.phone_pair is None\n"
        "assert type(main.daemon.skills.desktop).__name__ == '_NoDesktop'\n"
        "c = TestClient(main.app)\n"   # no lifespan: only the routes
        "print(json.dumps({'off': off}))\n"
    )
    env = {**os.environ, "ORION_DATA_DIR": str(tmp_path), "ORION_TOKEN_FILE": str(tmp_path / "t"),
           "ORION_ENV_FILE": str(tmp_path / "none.env"), "EMBEDDING_BACKEND": "hash", "ORION_KEY_PROXY": "0"}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180, env=env,
                       cwd=str(DAEMON))
    assert r.returncode == 0, r.stderr[-1500:]
    import json
    off = json.loads(r.stdout.strip().splitlines()[-1])["off"]
    for m in ("self_program", "phone_pair", "desktop_skills_hub", "core_signals", "desktop_shell"):
        assert m in off, (m, off)


def test_protected_core_sees_through_odd_path_spellings():
    """"apps/daemon/./x" and "apps//daemon/x" are the same file on disk; they must be the same file to the rules."""
    import protected_core as pc

    rule = next(r for r in pc.rules() if "*" not in r["path"] and r["symbols"] is None)
    p = rule["path"]
    head, _, tail = p.partition("/")
    for spelling in (p, f"./{p}", f"{head}//{tail}", f"{head}/./{tail}", p.replace("/", "\\"), "/" + p):
        assert pc.is_protected_path(spelling), spelling
        assert pc.check([{"path": spelling, "action": "modify", "before": "", "after": "x = 1\n"}]), spelling


def test_self_made_change_paths_stay_inside_the_repo():
    import pytest
    import self_program as sp

    assert sp._clean_rel("apps\\daemon\\.\\x.py") == "apps/daemon/x.py"
    for bad in ("../outside.py", "apps/../../outside.py", "C:/Windows/x.py", "~/x.py", ""):
        with pytest.raises(sp.SelfProgramError):
            sp._clean_rel(bad)


def test_mission_logs_are_capped_and_old_missions_leave_no_files(tmp_path, monkeypatch):
    import missions as M

    mc = M.MissionControl(tmp_path / "data")
    monkeypatch.setattr(M, "LOG_FILE_CAP", 2_000)
    m = {"id": "m1", "state": "running", "created": 1}
    mc.missions["m1"] = m
    for _ in range(200):
        mc.log_line(m, "x" * 50)
    assert mc._log_path("m1").stat().st_size < 2_000 + 200
    # finished missions beyond KEEP_FINISHED are dropped together with their log and diff
    monkeypatch.setattr(M, "KEEP_FINISHED", 1)
    m["state"] = next(iter(M.FINISHED))
    mc.missions["m2"] = {"id": "m2", "state": m["state"], "created": 2}
    (mc.dir / "diffs").mkdir(parents=True, exist_ok=True)
    (mc.dir / "diffs" / "m1.diff").write_text("d", encoding="utf-8")
    mc._save()
    assert "m1" not in mc.missions and "m2" in mc.missions
    assert not mc._log_path("m1").exists() and not (mc.dir / "diffs" / "m1.diff").exists()


NPM_SHIM = "\r\n".join([
    "@ECHO off", "GOTO start", ":find_dp0", "SET dp0=%~dp0", "EXIT /b", ":start", "SETLOCAL", "CALL :find_dp0",
    'IF EXIST "%dp0%\\node.exe" (', '  SET "_prog=%dp0%\\node.exe"', ") ELSE (", '  SET "_prog=node"', ")",
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*', ""])


def test_windows_agents_skip_cmd_exe(tmp_path):
    """On Windows, npm installs Claude Code / Codex / Gemini CLI as .cmd shims. Started directly, cmd.exe cut the
    mission prompt at its first line break and read & | > as commands; run node with the shim's script instead."""
    import missions as M

    shim = tmp_path / "claude.cmd"
    shim.write_text(NPM_SHIM, encoding="utf-8")
    script = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("//", encoding="utf-8")
    prompt = "Fix the login & sign-up pages\nThen run the tests > out.txt"
    out = M.win_unshim([str(shim), "-p", prompt], which=lambda _n: "C:/node/node.exe")
    assert out[0] == "C:/node/node.exe" and Path(out[1]) == script and out[2:] == ["-p", prompt]
    # a shim Orion can't read through is refused when the text would be mangled; plain text still runs
    odd = tmp_path / "odd.cmd"
    odd.write_text("@echo off\r\nsomething %*\r\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        M.win_unshim([str(odd), prompt])
    assert M.win_unshim([str(odd), "plain words"]) == [str(odd), "plain words"]
    assert M.win_unshim(["/usr/bin/claude", prompt]) == ["/usr/bin/claude", prompt]
