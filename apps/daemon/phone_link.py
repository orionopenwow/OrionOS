"""A steadier phone <-> PC link (the phone port, 9121). Added for "make the phone to computer link more stable".

What this adds on the PC side (the phone side is apps/mobile/.../Link.kt):

  Server time   Every answer on the phone port carries X-Orion-Time (the PC's clock, in milliseconds). The phone
                uses it to correct its own timestamps, so a phone clock that is a few minutes off keeps working;
                one that is too far off gets a plain answer (phones.ClockSkewError).
  Idempotency   Approvals and other fingerprint-signed requests carry an "idem" key inside the signed body (and
                the same value in X-Orion-Idem). The PC keeps the answer for each key for a day. If the answer is
                lost on the way back (Wi-Fi drop, PC busy), the phone sends the very same bytes again and gets the
                stored answer: nothing is applied twice and nothing is lost, and no second fingerprint is needed.
  Limits        At most PER_PHONE_MAX requests in flight per phone (more get 429 with Retry-After), a time limit on
                reading a request body (half-open connections), keep-alive closed after KEEP_ALIVE_S.
  Watching      Every WATCH_EVERY_S the PC checks its own address. When it changes it announces itself again on the
                Wi-Fi (_orion._tcp), and rebinds the port if it was bound to one address. On Windows the firewall
                rule is checked once at start and shown in the Phone panel (status only, never elevates).
  Link status   Last seen, the phone's reported round trip and retries, clock offset, the address it came from.
  Fix it        diagnose(): the checks behind the "Fix the connection" button on both screens, in plain words.

Local routes (added to phone_pairing_ui's router):  GET /phones/link   GET /phones/link/check
Phone route (read key):                             POST /phone/link-check
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import threading
import time
from typing import Any, Optional

from fastapi import Request

log = logging.getLogger("orion.phone_link")

TIME_HEADER = "X-Orion-Time"
IDEM_HEADER = "x-orion-idem"
IDEM_TTL_S = 24 * 3600
IDEM_MAX = 300
IDEM_WAIT_S = 30.0
PER_PHONE_MAX = 8
BODY_TIMEOUT_S = 20.0
KEEP_ALIVE_S = 20
SERVER_CONCURRENCY = 96
WATCH_EVERY_S = 20.0
ADDR_CACHE_S = 60.0
ONLINE_S = 120

_inflight: dict[str, int] = {}          # device id -> requests in flight
_idem_wait: set[str] = set()            # idem keys being applied right now
_idem_lock = threading.Lock()           # several event loops may share this (tests, a second server)
_stats: dict[str, dict[str, Any]] = {}  # device id -> link facts seen by the PC
_state: dict[str, Any] = {"firewall": None, "ip": None, "ip_changed": None, "restarts": 0}
_addr_cache: tuple[float, dict[str, str]] | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


# ===================================================================================== idempotency
def _idem_all(store: Any) -> dict[str, Any]:
    try:
        return json.loads(store.get("phone_idem", "{}") or "{}")
    except ValueError:
        return {}


def idem_key(headers: Any, body: bytes) -> Optional[str]:
    """The key only counts when it is inside the signed body too (the header alone is not signed)."""
    h = (headers.get(IDEM_HEADER) or "").strip()
    if not h or len(h) > 64 or not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or str(data.get("idem", "")) != h:
        return None
    return h


def idem_get(store: Any, device_id: str, key: str) -> Optional[dict[str, Any]]:
    e = _idem_all(store).get(f"{device_id}:{key}")
    if not e or time.time() - float(e.get("t", 0)) > IDEM_TTL_S:
        return None
    return e


def idem_put(store: Any, device_id: str, key: str, status: int, body: bytes, path: str) -> None:
    with store.lock:
        allx = _idem_all(store)
        cut = time.time() - IDEM_TTL_S
        allx = {k: v for k, v in allx.items() if float(v.get("t", 0)) > cut}
        allx[f"{device_id}:{key}"] = {"t": time.time(), "status": status, "path": path,
                                      "body": body.decode("utf-8", "replace")}
        if len(allx) > IDEM_MAX:
            for k in sorted(allx, key=lambda k: allx[k]["t"])[: len(allx) - IDEM_MAX]:
                allx.pop(k, None)
        store.put("phone_idem", json.dumps(allx))


def _cached_response(e: dict[str, Any]):
    from fastapi.responses import Response
    return Response(e["body"].encode("utf-8"), status_code=int(e["status"]), media_type="application/json",
                    headers={"X-Orion-Replayed": "1"})


async def idem_begin(request: Any, store: Any, device_id: str, body: bytes):
    """Called by the phone auth check once a request is verified. Returns a stored answer, or None to go on."""
    key = idem_key(request.headers, body)
    if key is None:
        return None
    full = f"{device_id}:{key}"
    deadline = time.monotonic() + IDEM_WAIT_S
    while True:
        with _idem_lock:
            busy = full in _idem_wait
            if not busy:
                e = idem_get(store, device_id, key)
                if e is None:
                    _idem_wait.add(full)   # ours to apply; copies wait for the answer
                    request.state.orion_idem = (store, device_id, key)
                    return None
                break
        if time.monotonic() > deadline:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "still working on that; try again"}, status_code=503,
                                headers={"Retry-After": "2"})
        await asyncio.sleep(0.05)   # being applied right now: wait for its answer
    if e.get("path") != request.url.path:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "that request key was used for something else"}, status_code=409)
    return _cached_response(e)


def idem_replayed(request: Any, store: Any, device_id: str, body: bytes):
    """A byte-for-byte resend (the phone lost the answer): the stored answer, or None (then it is a replay)."""
    key = idem_key(request.headers, body)
    if key is None:
        return None
    e = idem_get(store, device_id, key)
    if e is None or e.get("path") != request.url.path:
        return None
    return _cached_response(e)


def _idem_end(request: Any, status: Optional[int], body: Optional[bytes]) -> None:
    info = getattr(request.state, "orion_idem", None)
    if not info:
        return
    store, device_id, key = info
    try:
        if status is not None and body is not None and status < 500 and status not in (401, 413, 429):
            idem_put(store, device_id, key, status, body, request.url.path)
    finally:
        with _idem_lock:
            _idem_wait.discard(f"{device_id}:{key}")


# ===================================================================================== middleware
def note(device_id: Optional[str], **facts: Any) -> None:
    if not device_id:
        return
    d = _stats.setdefault(device_id[:64], {})
    d.update({k: v for k, v in facts.items() if v is not None})


def install(app: Any, board: Any) -> None:
    """Server time on every answer, per-phone limits, idempotent answers. Called by boardroom.build_remote_app."""
    from fastapi.responses import JSONResponse
    from starlette.responses import Response

    @app.middleware("http")
    async def orion_link(request, call_next):
        dev = (request.headers.get("x-orion-device") or "")[:64]
        if dev:
            if _inflight.get(dev, 0) >= PER_PHONE_MAX:
                r = JSONResponse({"error": "too many requests at once from this phone; trying again"},
                                 status_code=429, headers={"Retry-After": "1"})
                r.headers[TIME_HEADER] = str(now_ms())
                return r
            _inflight[dev] = _inflight.get(dev, 0) + 1
        try:
            try:
                resp = await call_next(request)
            except Exception:
                _idem_end(request, None, None)
                raise
            if getattr(request.state, "orion_idem", None):
                chunks = [c async for c in resp.body_iterator]
                raw = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
                _idem_end(request, resp.status_code, raw)
                headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
                resp = Response(raw, status_code=resp.status_code, headers=headers, media_type=resp.media_type)
            resp.headers[TIME_HEADER] = str(now_ms())
            if dev:
                client = request.client.host if request.client else None
                ts = request.headers.get("x-orion-ts") or ""
                skew = None
                if ts.isdigit():
                    skew = int(ts) - now_ms()
                ok = resp.status_code < 400
                d = _stats.setdefault(dev, {})
                d["ip"] = client or d.get("ip")
                if ok:
                    d["ok_at"] = time.time()
                    if skew is not None:
                        d["skew_ms"] = skew
                else:
                    d["refused_at"] = time.time()
                    d["refused"] = int(d.get("refused", 0)) + 1
                    if skew is not None:
                        d["skew_ms"] = skew
            return resp
        finally:
            if dev:
                n = _inflight.get(dev, 1) - 1
                if n <= 0:
                    _inflight.pop(dev, None)
                else:
                    _inflight[dev] = n


async def read_body(request: Any) -> Optional[bytes]:
    """The request body with a time limit, so a half-open connection can't hold a worker. None on timeout."""
    try:
        return await asyncio.wait_for(request.body(), BODY_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None


def server_config_kw() -> dict[str, Any]:
    """uvicorn settings for the phone port."""
    return {"timeout_keep_alive": KEEP_ALIVE_S, "limit_concurrency": SERVER_CONCURRENCY}


# ===================================================================================== addresses
def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private and not _tailnet(ip)
    except ValueError:
        return False


def _tailnet(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def addresses(ui: Any, fresh: bool = False) -> dict[str, str]:
    """{"lan": url, "tailnet": url}: where the phone can reach this PC. Tailscale only when it's installed and up."""
    global _addr_cache
    now = time.time()
    if not fresh and _addr_cache and now - _addr_cache[0] < ADDR_CACHE_S:
        return dict(_addr_cache[1])
    out: dict[str, str] = {}
    try:
        ip = ui.route_ip()
    except Exception:
        ip = None
    if ip and _private(ip):
        out["lan"] = f"http://{ip}:{ui.port}"
    try:
        from phone_pairing_ui import _cmd
        lines = _cmd(ui.run, ["tailscale", "ip", "-4"], 4).splitlines()
        t = lines[0].strip() if lines else ""
        if t and _tailnet(t):
            out["tailnet"] = f"http://{t}:{ui.port}"
    except Exception:
        pass
    if ip and _tailnet(ip) and "tailnet" not in out:
        out["tailnet"] = f"http://{ip}:{ui.port}"
    _addr_cache = (now, out)
    return dict(out)


def same_subnet(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    """Same /24 (IPv4) or /64 (IPv6). None when one side is unknown or a tailnet address (always reachable)."""
    if not a or not b:
        return None
    try:
        x, y = ipaddress.ip_address(a), ipaddress.ip_address(b)
    except ValueError:
        return None
    if _tailnet(a) or _tailnet(b) or x.is_loopback or y.is_loopback:
        return None
    if x.version != y.version:
        return None
    pre = 24 if x.version == 4 else 64
    return ipaddress.ip_network(f"{a}/{pre}", strict=False) == ipaddress.ip_network(f"{b}/{pre}", strict=False)


# ===================================================================================== link status
def link_info(device_id: str, last_seen: Optional[float], facts: Optional[dict[str, Any]] = None,
              now: Optional[float] = None) -> dict[str, Any]:
    """One phone's link for the Phone panel: {"state","words","quality","rtt_ms","skew_s","ip"}."""
    now = now or time.time()
    s = _stats.get(device_id, {})
    f = facts or {}
    seen = max(float(last_seen or 0), float(s.get("ok_at") or 0))
    age = now - seen if seen else None
    rtt = f.get("rtt_ms")
    fails = int(f.get("fails") or 0)
    if age is None:
        state, words = "never", "Not connected yet"
    elif age < ONLINE_S:
        state, words = "connected", "Connected"
    elif age < 15 * 60:
        state, words = "reconnecting", "Reconnecting…"
    else:
        state, words = "away", "Not connected"
    quality = None
    if state == "connected":
        if isinstance(rtt, (int, float)) and rtt > 800 or fails >= 3:
            quality = "poor"
        elif isinstance(rtt, (int, float)) and rtt > 250 or fails >= 1:
            quality = "fair"
        else:
            quality = "good"
    skew = s.get("skew_ms")
    out = {"state": state, "words": words, "quality": quality, "last_seen": seen or None,
           "rtt_ms": rtt if isinstance(rtt, (int, float)) else None, "ip": s.get("ip"),
           "skew_s": round(skew / 1000, 1) if isinstance(skew, (int, float)) else None,
           "via": f.get("via")}
    if isinstance(skew, (int, float)) and abs(skew) > 60_000:
        out["clock_words"] = f"The phone's clock is {round(abs(skew) / 60000)} min off. Orion corrects for it."
    return out


def hello_facts(data: dict[str, Any]) -> dict[str, Any]:
    """What the phone tells about its link in POST /phone/hello (kept with its battery and network)."""
    f: dict[str, Any] = {}
    for k, hi in (("rtt_ms", 120_000), ("fails", 10_000), ("reconnects", 1_000_000)):
        v = data.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= hi:
            f[k] = int(v)
    if isinstance(data.get("pinned"), bool):
        f["pinned"] = data["pinned"]
    via = str(data.get("via", ""))
    if via in ("lan", "tailnet", "other"):
        f["via"] = via
    return f


# ===================================================================================== "Fix the connection"
def _check(cid: str, name: str, ok: Optional[bool], words: str, fix: str = "") -> dict[str, Any]:
    return {"id": cid, "name": name, "ok": ok, "words": words, "fix": fix}


def diagnose(ui: Any, device_id: Optional[str] = None, seen_ip: Optional[str] = None,
             fresh: bool = True) -> dict[str, Any]:
    """The PC's half of "Fix the connection": port, firewall, address, same network, Wi-Fi announcement, PC key,
    clock. Returns {"ok", "checks": [...], "say": the one thing to do first}."""
    checks: list[dict[str, Any]] = []
    port_on = bool(ui.port_running())
    checks.append(_check("port", "Phone port", port_on,
                         f"Listening on port {ui.port}." if port_on else "The phone port on this PC is off.",
                         "" if port_on else "Open the Phone panel and press Pair my phone, or restart Orion."))
    try:
        lan = ui.lan(fresh=fresh)
    except Exception as exc:
        lan = {"ok": None, "words": f"Couldn't check: {exc}", "fix_words": ""}
    checks.append(_check("firewall", "Firewall", lan.get("ok"), lan.get("words", ""), lan.get("fix_words", "")))
    addrs = addresses(ui, fresh=fresh)
    pc_ip = addrs.get("lan", "").split("//")[-1].rsplit(":", 1)[0] or None
    if addrs:
        words = "Phones reach this PC at " + " or ".join(addrs.values()) + "."
        checks.append(_check("address", "PC address", True, words))
    else:
        checks.append(_check("address", "PC address", False, "This PC isn't on a network.",
                             "Connect the PC to your Wi-Fi."))
    st = _stats.get(device_id or "", {})
    phone_ip = seen_ip or st.get("ip")
    same = same_subnet(pc_ip, phone_ip)
    if same is False:
        checks.append(_check("subnet", "Same network", False,
                             f"The phone came from {phone_ip}, which is a different network from this PC ({pc_ip}).",
                             "Join the phone to the same Wi-Fi as the PC (guest Wi-Fi networks keep devices apart)."))
    elif same is True:
        checks.append(_check("subnet", "Same network", True, "The phone and this PC are on the same Wi-Fi."))
    elif phone_ip and _tailnet(phone_ip):
        checks.append(_check("subnet", "Same network", True, "The phone reaches this PC through Tailscale."))
    announcing = getattr(ui, "_zc", None) is not None
    zc_mod = getattr(ui, "zc_mod", None) is not None
    checks.append(_check("announce", "Found on the Wi-Fi", announcing if zc_mod else None,
                         "This PC announces itself on the Wi-Fi, so the phone finds it after an address change."
                         if announcing else ("Not announcing right now." if zc_mod else
                                             "The Wi-Fi announcement isn't installed (pip install zeroconf). The "
                                             "phone can still use the saved address."),
                         "" if announcing or not zc_mod else "Pair a phone or open the Phone panel."))
    try:
        import pc_identity
        key_ok = bool(pc_identity.for_board(ui.board).public_b64())
    except Exception:
        key_ok = False
    facts = {}
    try:
        facts = ui._facts().get(device_id or "", {}) if device_id else {}
    except Exception:
        facts = {}
    pinned = facts.get("pinned")
    if not key_ok:
        checks.append(_check("key", "PC key", False, "This PC has no key for phones to check.",
                             "Restart Orion; it makes one."))
    elif pinned is False:
        checks.append(_check("key", "PC key", False, "The phone hasn't saved this PC's key yet.",
                             "Open Orion on the phone once while on the same Wi-Fi; it saves it."))
    else:
        checks.append(_check("key", "PC key", True, "The phone checks this PC's key before trusting a new address."))
    skew = st.get("skew_ms")
    if isinstance(skew, (int, float)):
        import phones
        off = abs(skew) / 1000
        if off > phones.MAX_CLOCK_SKEW_S:
            checks.append(_check("clock", "Clocks", False, phones.skew_words(skew / 1000),
                                 "Turn on automatic date and time on the phone."))
        elif off > 60:
            checks.append(_check("clock", "Clocks", True,
                                 f"The phone's clock is {round(off / 60)} min off; Orion corrects for it."))
    if _state.get("restarts"):
        checks.append(_check("restarts", "Phone port restarts", None,
                             f"The phone port restarted itself {_state['restarts']} time(s) since Orion started."))
    bad = [c for c in checks if c["ok"] is False]
    return {"ok": not bad, "checks": checks, "say": (bad[0]["fix"] or bad[0]["words"]) if bad else
            "Everything on the PC side looks right.", "server_time": now_ms(), "pc_ip": pc_ip,
            "seen_ip": phone_ip, "addresses": addrs, "firewall": _state.get("firewall")}


def phone_hook(ui: Any):
    """POST /phone/link-check (read key): the PC's checks for the phone's "Fix the connection" button."""
    def hook(app, phone_auth, json_body) -> None:
        @app.post("/phone/link-check")
        async def link_check(request: Request):
            who, err = await phone_auth(request, "read", 4096)
            if err is not None:
                return err
            seen = request.client.host if request.client else None
            return await asyncio.to_thread(diagnose, ui, who["device_id"], seen, False)

    return hook


def status() -> dict[str, Any]:
    return {"firewall": _state.get("firewall"), "ip": _state.get("ip"), "ip_changed": _state.get("ip_changed"),
            "restarts": _state.get("restarts", 0)}


def note_restart() -> None:
    _state["restarts"] = int(_state.get("restarts", 0)) + 1


# ===================================================================================== the watcher
class LinkWatch:
    """Re-announce on an address change, rebind a port tied to one address, check the firewall once at start."""

    def __init__(self, ui: Any, every_s: float = WATCH_EVERY_S) -> None:
        self.ui = ui
        self.every_s = every_s
        self.task: Optional[asyncio.Task] = None
        self.first = True

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.task = None

    async def tick(self) -> dict[str, Any]:
        ui = self.ui
        ip = await asyncio.to_thread(ui.route_ip)
        changed = _state.get("ip") is not None and ip != _state.get("ip")
        if self.first:
            self.first = False
            try:
                lan = await asyncio.to_thread(ui.lan, True)
                _state["firewall"] = lan
                if lan.get("ok") is False:
                    log.warning("Phone link: %s %s", lan.get("words"), lan.get("fix_words"))
            except Exception as exc:
                log.info("Phone link: firewall check skipped: %s", exc)
        if changed:
            log.warning("This PC's address changed (%s -> %s): announcing again for the phone", _state.get("ip"), ip)
            _state["ip_changed"] = time.time()
            global _addr_cache
            _addr_cache = None
            ui._lan_cache = None
            bind = getattr(ui.board, "_bind", "") or ""
            if bind and bind not in ("0.0.0.0", "::"):
                rebind = getattr(ui.board, "rebind", None)
                if rebind is not None:
                    await rebind()
        _state["ip"] = ip
        await asyncio.to_thread(ui.advertise)   # no-op when nothing changed; re-registers on a new address
        return {"ip": ip, "changed": changed}

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("Phone link watch: %s", exc)
            await asyncio.sleep(self.every_s)
