"""Cards for self-changes that ask first (docs/ORIONOS_COMPLETE_PLAN.md Part 3: levels, self-repair, updates).

When decide(kind) says "ask", the change becomes a card in Orion's queue (HUD and phone): what Orion wants to do,
why, and what it already checked. Approve runs the action; Reject leaves everything as it is. Kinds that need
your fingerprint (Orion's own brain, the protected core) become a phone decision instead of a queue card.

Each yes or no is counted per kind; after a long run of yeses the autonomy learner may *suggest* raising that
kind (self_levels.suggest_raise), never more.

Actions are registered by the modules that own them: `register(name, async fn(daemon, args) -> result dict)`.
The result dict has at least {"ok": bool, "message": str}.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

import self_levels

log = logging.getLogger("orion.self_cards")

CARD_KIND = "self_change"
CHANGE_TASK = "self.change"
Action = Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]]
ACTIONS: dict[str, Action] = {}


def register(name: str, fn: Action) -> None:
    ACTIONS[name] = fn


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("id", "status", "kind", "title", "change_kind", "action", "key", "result")}


def waiting(daemon: Any, key: str) -> Optional[dict[str, Any]]:
    """The card (or phone decision) already waiting for this key, if any."""
    approvals = getattr(daemon, "approvals", None)
    if approvals is not None:
        for item in approvals.pending():
            if item.get("kind") == CARD_KIND and item.get("key") == key:
                return item
    board = getattr(daemon, "board", None)
    if board is not None:
        try:
            for d in board.pending():
                if d["task_type"] == CHANGE_TASK and d["packet"]["details"].get("key") == key:
                    return {"id": d["id"], "status": "pending", "kind": CHANGE_TASK, "key": key}
        except Exception:
            pass
    return None


def _register_task_type() -> None:
    import boardroom
    from boardroom import TaskType
    boardroom.TASK_TYPES.setdefault(CHANGE_TASK, TaskType(CHANGE_TASK, "A change Orion wants to make to itself",
                                                          "system", reversible=False, money=False, sensitive=True))


async def propose(daemon: Any, change_kind: str, title: str, body: str, action: str, args: dict[str, Any],
                  key: str, *, proposer: str = "Orion") -> Optional[dict[str, Any]]:
    """Put an "ask" card up (once per key). Returns the card, or None when it couldn't be shown."""
    if action not in ACTIONS:
        raise KeyError(action)
    have = waiting(daemon, key)
    if have is not None:
        return have
    k = self_levels.KINDS.get(change_kind)
    if k is not None and k.fingerprint:
        board = getattr(daemon, "board", None)
        if board is None:
            return None
        _register_task_type()
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (CHANGE_TASK,))
        dec = await board.propose(CHANGE_TASK, title[:200],
                                  {"key": key, "change_kind": change_kind, "action": action, "args": args,
                                   "what": body[:1500]}, proposer=proposer, risk="high", simulated=False)
        return {"id": dec["id"], "status": "pending", "kind": CHANGE_TASK, "key": key}
    approvals = getattr(daemon, "approvals", None)
    if approvals is None:
        return None
    item = approvals.add(CARD_KIND, platform="Orion", channel="Self-running", author=proposer[:60],
                         category="normal", title=title[:160], original=body[:3000],
                         draft="Approve = go ahead. Reject = leave it as it is.",
                         change_kind=change_kind, action=action, args=args, key=key)
    try:
        await daemon.bus.publish({"event": "APPROVAL_REQUIRED", "item": item})
    except Exception:
        pass
    try:
        import alerts
        if change_kind == "hint":    # something for you to do, not a yes/no on a change: no urgent buzz
            alerts.emit("system", title[:120], body[:300], ref=item["id"])
        else:
            alerts.emit("approval", title[:120], "Orion would like your OK. Open the card to see what it checked.",
                        ref=item["id"])
    except Exception:
        pass
    return item


async def run_action(daemon: Any, action: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = ACTIONS.get(action)
    if fn is None:
        return {"ok": False, "message": "Orion doesn't know how to do that any more."}
    try:
        res = await fn(daemon, args)
    except Exception as exc:
        log.exception("self-change action %s failed", action)
        return {"ok": False, "message": f"It didn't work: {type(exc).__name__}."}
    return res if isinstance(res, dict) else {"ok": bool(res), "message": ""}


async def _after_verdict(daemon: Any, change_kind: str, approved: bool) -> None:
    try:
        nxt = self_levels.current().record_verdict(change_kind, approved)
        if nxt is not None:
            await self_levels.suggest_raise(daemon, change_kind, nxt)
    except Exception:
        log.exception("autonomy learner note failed")


async def decide_card(daemon: Any, item: dict[str, Any], decision: str) -> None:
    """Approve (or Edit) = run it now (unless Orion got frozen meanwhile); Reject = nothing happens."""
    item_id = item["id"]
    if decision == "reject":
        if daemon.approvals.claim(item_id, "rejected") is not None:
            updated = daemon.approvals.update(item_id, status="rejected")
            await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})
            await _after_verdict(daemon, str(item.get("change_kind", "")), False)
        return
    import freeze
    if freeze.is_frozen():
        await daemon.bus.log(freeze.blocked("make this change") or freeze.MESSAGE, "warn")
        return
    if daemon.approvals.claim(item_id, "sending") is None:
        await daemon.bus.log(f"Approval {item_id} is no longer pending", "warn")
        return
    res = await run_action(daemon, str(item.get("action", "")), dict(item.get("args") or {}))
    updated = daemon.approvals.update(item_id, status="approved" if res.get("ok") else "failed",
                                      result=str(res.get("message", ""))[:300])
    await daemon.bus.publish({"event": "APPROVAL_RESOLVED", "item": updated})
    await daemon.bus.log(str(res.get("message") or "Done."), "info" if res.get("ok") else "warn")
    await _after_verdict(daemon, str(item.get("change_kind", "")), True)


def install_executor(daemon: Any) -> None:
    """Phone-approved changes (fingerprint kinds) run here."""
    _register_task_type()
    board = getattr(daemon, "board", None)
    if board is None:
        return
    try:
        board.store.x("INSERT OR IGNORE INTO autonomy(task_type) VALUES(?)", (CHANGE_TASK,))
    except Exception:
        pass

    def execute(d: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import freeze
        if freeze.is_frozen():
            raise ValueError(freeze.MESSAGE)
        det = d["packet"]["details"]
        if det.get("action") not in ACTIONS:
            raise ValueError("Orion doesn't know how to do that any more.")
        asyncio.get_running_loop().create_task(run_action(daemon, det["action"], dict(det.get("args") or {})))
        return {"started": True}

    board.executors[CHANGE_TASK] = execute
