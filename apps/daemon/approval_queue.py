"""Approval Queue — every autonomous action that speaks or writes on the user's behalf
waits here until the admin clicks [✓ Approve], [✎ Edit & Send] or [✕ Reject] in the
Planetary Context Window.

Item kinds:
  discord_prompt   a drafted reply to a community-server message (Discord triage)
  self_evolve      a generated code patch awaiting review

Pending items are persisted to ~/.orion/approvals.json so a daemon restart (including the
hot-reload after a self-evolve patch) never loses the queue.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.approvals")

MAX_RESOLVED = 100
ACTIVE_STATUSES = ("pending", "sending")


class ApprovalQueue:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.items: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.items = {i["id"]: i for i in data if isinstance(i, dict) and "id" in i and "status" in i}
                for item in self.items.values():
                    if item["status"] == "sending":  # daemon died mid-send: let the admin decide again
                        item["status"] = "pending"
                        item["error"] = "Daemon restarted while sending — check the channel before approving again."
                log.info("Approval queue restored: %d pending", len(self.pending()))
        except Exception as exc:
            log.warning("Could not read approval queue (%s); starting empty", exc)
            self.items = {}

    def _save(self) -> None:
        active = [i for i in self.items.values() if i["status"] in ACTIVE_STATUSES]
        resolved = sorted((i for i in self.items.values() if i["status"] not in ACTIVE_STATUSES),
                          key=lambda i: i.get("resolved_at", 0), reverse=True)[:MAX_RESOLVED]
        keep = active + resolved
        self.items = {i["id"]: i for i in keep}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(keep, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            log.warning("Could not persist approval queue: %s", exc)

    # ------------------------------------------------------------- API
    def add(self, kind: str, **fields: Any) -> dict[str, Any]:
        fields = __import__("share_scrubber").scrub_outgoing(kind, fields)  # Toolkit: Share Scrubber
        item = {
            "id": fields.pop("id", None) or f"apr-{uuid.uuid4().hex[:10]}",
            "kind": kind,
            "status": "pending",
            "created_at": time.time(),
            **fields,
        }
        with self._lock:
            self.items[item["id"]] = item
            self._save()
        return item

    def get(self, item_id: str) -> Optional[dict[str, Any]]:
        return self.items.get(item_id)

    def update(self, item_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self.items.get(item_id)
            if item is None:
                return None
            item.update(fields)
            if fields.get("status") and fields["status"] not in ACTIVE_STATUSES:
                item.setdefault("resolved_at", time.time())
            self._save()
            return item

    def claim(self, item_id: str, to_status: str = "sending") -> Optional[dict[str, Any]]:
        """Atomically move a pending item to `to_status`; None if it was not pending.
        Stops a double-click (two concurrent decisions) from posting a reply twice."""
        with self._lock:
            item = self.items.get(item_id)
            if item is None or item["status"] != "pending":
                return None
            item["status"] = to_status
            self._save()
            return dict(item)

    def snapshot(self, resolved_limit: int = 40) -> list[dict[str, Any]]:
        """Every active item plus the most recent resolved ones (what the UI needs on connect)."""
        active = [i for i in self.items.values() if i["status"] in ACTIVE_STATUSES]
        resolved = sorted((i for i in self.items.values() if i["status"] not in ACTIVE_STATUSES),
                          key=lambda i: i.get("resolved_at", i["created_at"]), reverse=True)[:resolved_limit]
        return sorted(active, key=lambda i: i["created_at"], reverse=True) + resolved

    def pending(self) -> list[dict[str, Any]]:
        return sorted((i for i in self.items.values() if i["status"] == "pending"),
                      key=lambda i: i["created_at"], reverse=True)

    def recent(self, limit: int = 40) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda i: i["created_at"], reverse=True)[:limit]
