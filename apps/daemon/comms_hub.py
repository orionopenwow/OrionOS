"""Unified Comms Hub (V2).

One normalised stream for every personal conversation Orion can see:

  platform   route                  ingestion
  ---------  ---------------------  ---------------------------------------------------
  telegram   telegram_user          Telethon user-account bridge (true personal DMs, optional)
  telegram   telegram_api           Bot API: chats other than the owner's command chat
  discord    discord_bot            DMs sent to the Orion Discord bot
  discord    extension_cdp_inject   Chrome extension DOM observer on discord.com/channels/@me
  messenger  extension_cdp_inject   Chrome extension DOM observer on messenger.com

Replies typed in the Orion input bar are dispatched back through the route the thread
arrived on. Message history is appended to ~/.orion/comms/messages.jsonl and the most
recent messages per thread are restored on start.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("orion.comms")

PLATFORMS = ("telegram", "discord", "messenger", "phone")  # phone: read-only inbox mirror (phone_pair.py)
ROUTES = ("telegram_user", "telegram_api", "discord_bot", "extension_cdp_inject")
Sender = Callable[[dict[str, Any], str], Awaitable[bool]]
ECHO_WINDOW_S = 120.0
RESTORE_TAIL_BYTES = 4 * 1024 * 1024   # only the newest ~4 MB of history is replayed on start
COMPACT_AT_BYTES = 16 * 1024 * 1024    # rewrite the log down to what is in memory past this size


def _coerce_ts(value: Any) -> float:
    """Client-supplied timestamps may be missing, strings, ms epochs or garbage."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return time.time()
    if ts > 1e12:  # milliseconds
        ts /= 1000.0
    if not (946684800 <= ts <= time.time() + 86400):  # before 2000 or more than a day in the future
        return time.time()
    return ts


def thread_key(platform: str, thread_id: str) -> str:
    return f"{platform}:{thread_id}"


class CommsHub:
    def __init__(self, data_dir: Path, publish: Callable[[dict[str, Any]], Awaitable[None]],
                 history_per_thread: int = 200) -> None:
        self.dir = data_dir / "comms"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "messages.jsonl"
        self.publish = publish
        self.history = history_per_thread
        self.threads: dict[str, dict[str, Any]] = {}
        self.senders: dict[str, Sender] = {}
        self._seen: deque[str] = deque(maxlen=5000)
        self._seen_set: set[str] = set()
        self._recent_out: dict[str, list[tuple[float, str]]] = {}
        self._lock = asyncio.Lock()
        # Feature Bible D2: routing rules (VIP satellites, muted low-priority threads) from comms_routing.json
        self.routing: dict[str, Any] = {}
        self._restore()

    # ------------------------------------------------------------ storage
    def _restore(self) -> None:
        if not self.log_path.exists():
            return
        try:
            size = self.log_path.stat().st_size
            with self.log_path.open("rb") as fh:
                if size > RESTORE_TAIL_BYTES:
                    fh.seek(size - RESTORE_TAIL_BYTES)
                    fh.readline()  # drop the partial first line
                raw = fh.read().decode("utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not read comms history: %s", exc)
            return
        restored = 0
        for line in raw.splitlines():
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict) or msg.get("platform") not in PLATFORMS:
                    continue
                thread = self._thread(msg["platform"], str(msg["thread_id"]),
                                      msg.get("thread_name") or str(msg["thread_id"]),
                                      msg.get("route", ""), msg.get("thread_url"))
                msg["timestamp"] = _coerce_ts(msg.get("timestamp"))
                self._remember(str(msg.get("dedupe_key") or msg.get("id")))
                thread["messages"].append(msg)
                thread["last_ts"] = max(thread["last_ts"], msg["timestamp"])
                restored += 1
            except Exception:
                continue  # one bad line never aborts the rest of the restore
        log.info("Comms hub restored %d messages in %d threads", restored, len(self.threads))
        if size > COMPACT_AT_BYTES:
            self._compact()

    def _compact(self) -> None:
        """Rewrite the log with only what is held in memory (bounded per thread)."""
        try:
            tmp = self.log_path.with_suffix(".jsonl.tmp")
            msgs = sorted((m for t in self.threads.values() for m in t["messages"]), key=lambda m: m["timestamp"])
            with tmp.open("w", encoding="utf-8") as fh:
                for m in msgs:
                    fh.write(json.dumps(m, ensure_ascii=False) + "\n")
            tmp.replace(self.log_path)
            log.info("Comms log compacted to %d messages", len(msgs))
        except Exception as exc:
            log.warning("Comms log compaction failed: %s", exc)

    def _append_log(self, msg: dict[str, Any]) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
            if self.log_path.stat().st_size > COMPACT_AT_BYTES:
                self._compact()
        except Exception as exc:
            log.debug("comms log append failed: %s", exc)

    def _remember(self, key: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(key)
        self._seen_set.add(key)

    def _thread(self, platform: str, thread_id: str, name: str, route: str,
                url: Optional[str] = None) -> dict[str, Any]:
        key = thread_key(platform, thread_id)
        t = self.threads.get(key)
        if t is None:
            t = {"key": key, "platform": platform, "thread_id": thread_id, "thread_name": name,
                 "route": route, "thread_url": url, "unread": 0, "last_ts": 0.0,
                 "messages": deque(maxlen=self.history)}
            self.threads[key] = t
        else:
            if name and name != thread_id:
                t["thread_name"] = name
            if route:
                t["route"] = route
            if url:
                t["thread_url"] = url
        return t

    # ------------------------------------------------------------ API
    def register_sender(self, route: str, sender: Sender) -> None:
        self.senders[route] = sender

    async def ingest(
        self,
        *,
        platform: str,
        thread_id: str,
        thread_name: str,
        author: str,
        text: str,
        route: str,
        direction: str = "in",
        timestamp: Optional[float] = None,
        external_id: Optional[str] = None,
        thread_url: Optional[str] = None,
        backfill: bool = False,
        _from_reply: bool = False,
    ) -> Optional[dict[str, Any]]:
        # Validate everything before touching dedupe state, so a malformed field can't
        # poison the dedupe set and lose the message forever.
        text = str(text or "").strip()
        if platform not in PLATFORMS or not text or not thread_id:
            return None
        thread_id = str(thread_id)[:128]
        thread_name = str(thread_name or thread_id)[:200]
        author = str(author or "Unknown")[:120]
        direction = "out" if direction == "out" else "in"
        if direction == "in":   # Toolkit: Injection Firewall remembers messages from others as untrusted text
            __import__("injection_firewall").note(text, f"a {platform} message from {author}")
        ts = _coerce_ts(timestamp)
        thread_url = str(thread_url)[:2048] if isinstance(thread_url, str) and thread_url.startswith("https://") else None
        external_id = str(external_id)[:200] if external_id not in (None, "") else None
        dedupe = f"{platform}:{thread_id}:{external_id}" if external_id else \
            hashlib.blake2b(f"{platform}{thread_id}{author}{text}{int(ts // 60)}".encode(),
                            digest_size=12).hexdigest()
        async with self._lock:
            if dedupe in self._seen_set:
                return None
            key = thread_key(platform, thread_id)
            # Our own reply echoing back through a bridge (DOM observer / Telethon outgoing event).
            # Only messages marked as ours are candidates, so a contact typing the same words still arrives.
            if not _from_reply and direction == "out":
                recent = self._recent_out.get(key, [])
                for i, (sent_ts, sent_text) in enumerate(recent):
                    if sent_text == text and time.time() - sent_ts < ECHO_WINDOW_S:
                        del recent[i]  # consume: a second identical send of ours is a real message
                        self._remember(dedupe)
                        return None
            self._remember(dedupe)
            thread = self._thread(platform, thread_id, thread_name or thread_id, route, thread_url)
            msg = {
                "id": f"msg-{uuid.uuid4().hex[:12]}",
                "dedupe_key": dedupe,
                "platform": platform,
                "thread_id": thread_id,
                "thread_key": key,
                "thread_name": thread["thread_name"],
                "thread_url": thread.get("thread_url"),
                "route": route,
                "author": author,
                "text": text[:6000],
                "direction": direction,
                "timestamp": ts,
                "backfill": bool(backfill),
            }
            thread["messages"].append(msg)
            thread["last_ts"] = max(thread["last_ts"], msg["timestamp"])
            flags = self.flags(thread)
            thread.update(flags)
            msg["vip"] = flags["vip"] or self._is_vip(author)
            if msg["direction"] == "in" and not backfill and not flags["muted"]:
                thread["unread"] += 1
            self._append_log(msg)
        await self.publish({"event": "COMMS_MESSAGE", "message": msg, "thread": self.thread_summary(thread)})
        return msg

    async def reply(self, key: str, text: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
        thread = self.threads.get(key)
        if thread is None:
            return False, f"Unknown thread {key}", None
        text = text.strip()
        if not text:
            return False, "Empty reply", None
        sender = self.senders.get(thread["route"])
        if sender is None:
            return False, f"No active bridge for route '{thread['route']}'", None
        try:
            ok = await sender(self.thread_summary(thread), text)
        except Exception as exc:
            log.warning("Comms reply via %s failed: %s", thread["route"], exc)
            return False, str(exc), None
        if not ok:
            return False, f"{thread['route']} refused the message", None
        now = time.time()
        recent = [(ts, t) for ts, t in self._recent_out.get(key, []) if now - ts < ECHO_WINDOW_S]
        recent.append((now, text))
        self._recent_out[key] = recent[-20:]
        msg = await self.ingest(platform=thread["platform"], thread_id=thread["thread_id"],
                                thread_name=thread["thread_name"], author="You", text=text,
                                route=thread["route"], direction="out", external_id=f"out-{uuid.uuid4().hex[:8]}",
                                _from_reply=True)
        return True, "sent", msg

    # ------------------------------------------------------------ routing (VIP / muted / lookup)
    def _vip_names(self) -> list[str]:
        names = []
        for v in self.routing.get("vip_contacts", []) or []:
            if isinstance(v, dict):
                names += [str(v.get("name", "")), *[str(a) for a in v.get("aliases", [])]]
            elif isinstance(v, str):
                names.append(v)
        return [n.lower() for n in names if len(n.strip()) >= 2]

    def _is_vip(self, name: str) -> bool:
        n = (name or "").lower()
        return any(v == n or v in n.split() or (len(v) > 3 and v in n) for v in self._vip_names())

    def flags(self, thread: dict[str, Any]) -> dict[str, bool]:
        muted_list = [str(x).lower() for x in self.routing.get("low_priority_threads", []) or []]
        name = str(thread.get("thread_name", "")).lower()
        return {
            "vip": self._is_vip(name),
            "muted": thread["key"].lower() in muted_list or any(m and m == name for m in muted_list),
        }

    def set_routing(self, routing: dict[str, Any]) -> None:
        self.routing = routing or {}
        for t in self.threads.values():
            t.update(self.flags(t))

    def set_muted(self, key: str, muted: bool) -> dict[str, Any]:
        name = str(self.threads.get(key, {}).get("thread_name", "")).lower()
        lst = [x for x in self.routing.setdefault("low_priority_threads", [])
               if str(x).lower() not in (key.lower(), name)]
        if muted:
            lst.append(key)
        self.routing["low_priority_threads"] = lst
        if key in self.threads:
            self.threads[key].update(self.flags(self.threads[key]))
            if muted:
                self.threads[key]["unread"] = 0
        return self.routing

    def find_thread(self, name: str, platform: Optional[str] = None) -> Optional[dict[str, Any]]:
        n = name.lower().strip()
        best, best_score = None, 0.0
        for t in self.threads.values():
            if platform and t["platform"] != platform:
                continue
            tn = t["thread_name"].lower()
            authors = {m["author"].lower() for m in list(t["messages"])[-30:] if m["direction"] == "in"}
            if tn == n or n in authors:
                score = 3.0
            elif n in tn.split() or tn.startswith(n) or any(a.startswith(n) for a in authors):
                score = 2.0
            elif n in tn:
                score = 1.0
            else:
                continue
            score += t["last_ts"] / 1e11  # tie-break: most recent conversation
            if score > best_score:
                best, best_score = t, score
        return best

    def messages_since(self, since: float, muted_only: bool = True) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for t in self.threads.values():
            if muted_only and not t.get("muted"):
                continue
            msgs = [m for m in t["messages"] if m["timestamp"] >= since and m["direction"] == "in"]
            if msgs:
                out[t["thread_name"]] = msgs
        return out

    def mark_read(self, key: str) -> None:
        if key in self.threads:
            self.threads[key]["unread"] = 0

    def context_for(self, key: str, limit: int = 8) -> list[str]:
        thread = self.threads.get(key)
        if not thread:
            return []
        return [f"{m['author']}: {m['text'][:400]}" for m in list(thread["messages"])[-limit:]]

    def style_samples(self, limit: int = 40) -> list[str]:
        """The user's own recent outgoing messages across every platform (for learning their typing style)."""
        out: list[tuple[float, str]] = []
        for t in self.threads.values():
            for m in list(t["messages"])[-30:]:
                if m["direction"] == "out" and len(m["text"].strip()) > 1:
                    out.append((m["timestamp"], m["text"].strip()))
        out.sort()
        return [txt for _, txt in out[-limit:]]

    def last_inbound(self, key: str) -> Optional[dict[str, Any]]:
        thread = self.threads.get(key)
        if not thread:
            return None
        for m in reversed(thread["messages"]):
            if m["direction"] == "in":
                return m
        return None

    @staticmethod
    def thread_summary(thread: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in thread.items() if k != "messages"}

    def snapshot(self, per_thread: int = 40) -> list[dict[str, Any]]:
        threads = sorted(self.threads.values(), key=lambda t: t["last_ts"], reverse=True)
        return [{**self.thread_summary(t), "messages": list(t["messages"])[-per_thread:]} for t in threads[:60]]
