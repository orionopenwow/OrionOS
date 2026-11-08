"""Agent safety: toolkit tools 7, 8 and 9 wired into the daemon and the MCP bus.

  * Package Reality Check (package_check.py): new dependencies are checked before they're installed.
  * Injection Firewall (injection_firewall.py): agents don't take orders from web pages, issues or emails.
  * Key Proxy (key_proxy.py): agents hold placeholders; the real keys stay inside Orion.

This module holds the glue, so main.py needs only a few lines:
  * `AgentSafety(daemon)` owns the three services, hooks Package Reality Check into the runner's start checks and
    Proof Gate's extra checks, and starts/stops the Key Proxy;
  * `KINDS` / `decide()` handle the approval cards; `chat()` answers "which agents used my OpenAI key today?";
  * `make_router()` mounts /package-check, /firewall, /key-proxy and /agent-safety/status;
  * `precheck()` / `postprocess()` are called by mcp_tools.OrionTools.call for every MCP tool call.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import injection_firewall as ifw
import key_proxy as kp
import package_check as pc

log = logging.getLogger("orion.agent_safety")

KINDS = {pc.CARD_KIND, ifw.CARD_KIND}
COMMAND_TOOLS = {"runner_start", "grimoire_run"}


class AgentSafety:
    def __init__(self, daemon: Any, data_dir: Any = None) -> None:
        from config import settings
        self.d = daemon
        data_dir = Path(data_dir or settings.data_dir)
        self.packages = pc.PackageCheck(data_dir, daemon)
        self.firewall = ifw.FirewallService(data_dir, daemon)
        meter = getattr(getattr(daemon, "orchestrator", None), "meter", None)
        self.keys = kp.KeyProxy(data_dir, meter=meter)
        runner = getattr(getattr(daemon, "ext", None), "runner", None)
        self._runner = runner if runner is not None and hasattr(runner, "start_checks") else None
        if self._runner is not None:
            self._runner.start_checks.append(self.runner_check)
        try:
            import proof_gate
            proof_gate.EXTRA_CHECKS.append(self.proof_check)
        except Exception:
            pass

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        try:
            await self.keys.start()
        except Exception:
            log.exception("Key Proxy failed to start")

    async def stop(self) -> None:
        await self.keys.stop()
        if self._runner is not None and self.runner_check in self._runner.start_checks:
            self._runner.start_checks.remove(self.runner_check)
        try:
            import proof_gate
            if self.proof_check in proof_gate.EXTRA_CHECKS:
                proof_gate.EXTRA_CHECKS.remove(self.proof_check)
        except Exception:
            pass

    # ------------------------------------------------------------ hooks
    async def runner_check(self, command: str, cwd: Optional[str]) -> Optional[str]:
        rep = await self.packages.check_command(command, cwd, origin=f"command: {command[:160]}")
        return None if rep.get("allowed", True) else rep["summary"]

    async def proof_check(self, src: dict[str, Any], base: str, changed: list[str], copy: Any) -> list[str]:
        if not any(pc.manifest_kind(c) for c in changed or []):
            return []
        repo = Path(src.get("repo_root") or copy)
        rep = await self.packages.check_git(repo, base, worktree=Path(copy),
                                            origin=f"Proof Gate: {src.get('label') or src.get('agent') or repo.name}")
        if rep.get("allowed", True):
            return []
        names = [p["name"] for p in rep["packages"] if p["verdict"] in ("block", "unknown")]
        return [f"It adds package{'s' if len(names) != 1 else ''} Orion doesn't trust: {', '.join(names[:4])} "
                "(see the Queue)"]

    async def decide(self, item: dict[str, Any], decision: str, text: str = "") -> None:
        if item.get("kind") == pc.CARD_KIND:
            await self.packages.decide(item, decision)
        elif item.get("kind") == ifw.CARD_KIND:
            await self.firewall.decide(item, decision)

    async def chat(self, text: str) -> Optional[str]:
        """Answers for "which agents used my OpenAI key today?"; None for anything else."""
        q = kp.parse_usage_question(text)
        if q is None:
            return None
        provider, period = q
        return self.keys.usage(provider, kp.since_from(period, self.keys.clock()), "",
                               kp.PERIOD_WORDS.get(period, ""))["answer"]

    def status(self) -> dict[str, Any]:
        return {"packages": {**self.packages.settings(), "recent": self.packages.recent()[:10]},
                "firewall": self.firewall.status(), "keys": self.keys.status()}


# =====================================================================================  routes
def make_router(get_safety: Callable[[], Optional[AgentSafety]]):
    from fastapi import APIRouter

    def need() -> AgentSafety:
        s = get_safety()
        if s is None:
            raise RuntimeError("Orion is still starting.")
        return s

    router = APIRouter()
    router.include_router(pc.make_router(lambda: need().packages))
    router.include_router(ifw.make_router(lambda: need().firewall))
    router.include_router(kp.make_router(lambda: need().keys))

    @router.get("/agent-safety/status", tags=["toolkit"])
    async def status() -> Any:
        return need().status()

    return router


# =====================================================================================  MCP bus hooks
def mcp_tools(tools: Any) -> list[Any]:
    return [*pc.mcp_tools(tools), *ifw.mcp_tools(tools), *kp.mcp_tools(tools)]


def _session_firewall(tools: Any) -> ifw.Firewall:
    fw = getattr(tools, "firewall", None)
    if fw is None:
        fw = ifw.Firewall()
        tools.firewall = fw
    return fw


async def precheck(tools: Any, tool: Any, args: dict[str, Any]) -> Optional[str]:
    """Before an MCP tool runs: the Injection Firewall, then Package Reality Check. A string = refuse with it."""
    from mcp_tools import DaemonUnavailable
    fw = _session_firewall(tools)
    session = f"Coding agent (MCP, process {os.getpid()})"
    v = fw.check(tool.name, args, tool.risk)
    key = ifw.call_key(tool.name, args)
    if v.blocked:
        allowed = False
        try:
            status, data = await _call(tools, "/firewall/allowance", {"key": key}, 3)
            allowed = status < 400 and bool(data.get("allowed"))
            if not allowed:
                await _call(tools, "/firewall/report", {"verdict": v.to_dict(), "session": session, "key": key}, 3)
        except DaemonUnavailable:
            pass
        if not allowed:
            return _blocked_text(v.to_dict())
    elif tool.risk in ("run", "approval") or ifw.rule_hits("", tool.name):
        try:
            status, data = await _call(tools, "/firewall/check", {"tool": tool.name, "args": args, "risk": tool.risk,
                                                                 "session": session}, 3)
            if status < 400 and data.get("blocked"):
                return _blocked_text(data)
        except DaemonUnavailable:
            pass
    if tool.name in COMMAND_TOOLS and isinstance(args.get("command"), str):
        return await _package_precheck(tools, "/package-check/command",
                                       {"command": args["command"], "cwd": str(args.get("project") or "")},
                                       lambda: pc.parse_command(args["command"])[0])
    if tool.name == "git_commit":
        try:
            project = str(tools.project(args))
        except Exception:
            return None
        return await _package_precheck(tools, "/package-check/staged", {"repo": project}, None)
    return None


async def _package_precheck(tools: Any, path: str, body: dict[str, Any],
                            offline_wants: Optional[Callable[[], list[pc.Want]]]) -> Optional[str]:
    from mcp_tools import DaemonUnavailable
    try:
        status, data = await _call(tools, path, body, 60)
    except DaemonUnavailable:
        if offline_wants is None:
            return None
        res = [{"eco": w.eco, "name": w.name, "info": None, **pc.judge(w, None, time.time(), "allow")}
               for w in offline_wants()]
        if any(r["verdict"] == "block" for r in res):
            return pc.describe(res) + "\n(Orion isn't running, so only the look-alike test ran. Start Orion to " \
                                      "approve it.)"
        return None
    if status >= 400 or data.get("allowed", True):
        return None
    return data.get("summary") or "Package Reality Check stopped this."


async def _call(tools: Any, path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict]:
    try:
        return await tools.backend.call("POST", path, body, timeout=timeout)
    except TypeError:                     # a backend without per-call timeouts
        return await tools.backend.call("POST", path, body)


def _blocked_text(v: dict[str, Any]) -> str:
    m = (v.get("matches") or [{}])[0]
    return (f"Injection Firewall stopped this call. {v.get('reason', '')}\n"
            f"Source ({m.get('source', 'outside text')}): “{m.get('quote', '')}”\n"
            "Nothing was run. The user has a card in Orion's Queue: if they allow it, the same call will work once. "
            "Don't try to get around this; ask the user instead.")


def postprocess(tools: Any, tool: Any, args: dict[str, Any], text: str) -> str:
    """After an MCP tool ran: remember outside text and frame it as data."""
    src = ifw.untrusted_source(tool.name, args)
    if not src:
        return text
    _session_firewall(tools).tracker.note(text, f"{src} (via {tool.name})")
    return ifw.wrap(text, src)
