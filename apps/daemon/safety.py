"""The safety net, wired into the daemon in one place (docs/ORIONOS_COMPLETE_PLAN.md step 3: "Safety net before
anything automatic"): snapshots (snapshots.py), the health monitor (health_monitor.py), the kill switch
(freeze.py) and the change history (changes.py).

main.py needs only: `app.include_router(safety.make_router(lambda: daemon))`, `safety.start(self)` in start(),
`await safety.stop(self)` in stop(), and `freeze.handle_text` where typed or spoken text comes in.
"""
from __future__ import annotations

from typing import Any, Callable

import changes
import freeze
import health_monitor
import snapshots

_monitors: dict[int, health_monitor.HealthMonitor] = {}


def monitor(daemon: Any) -> health_monitor.HealthMonitor:
    m = _monitors.get(id(daemon))
    if m is None:
        _monitors.clear()
        m = _monitors[id(daemon)] = health_monitor.HealthMonitor(daemon)
        daemon.health = m
    return m


def make_router(get_daemon: Callable[[], Any]):
    from fastapi import APIRouter

    router = APIRouter()
    router.include_router(changes.make_router(get_daemon))
    router.include_router(freeze.make_router(get_daemon))
    router.include_router(snapshots.make_router(get_daemon))
    router.include_router(health_monitor.make_router(lambda: monitor(get_daemon())))
    return router


def start(daemon: Any) -> None:
    monitor(daemon).start()


async def stop(daemon: Any) -> None:
    await monitor(daemon).stop()
