"""Phone link soak test: a simulated phone hammering the PC's phone port through trouble.

    python phone_soak.py            # full run (about a minute)
    python phone_soak.py --short    # what the test suite runs (a few seconds)

The simulated phone speaks the real protocol (two P-256 keys, signed requests, the PC key pinned at pairing) and
follows the same rules as the Android app (apps/mobile/.../Link.kt): timeouts, bounded retries with backoff and
jitter, clock correction from X-Orion-Time, an "idem" key on every approval (a lost answer is fetched again by
sending the same bytes), and finding the PC again after an address change only if it proves it holds the pinned
key. Requests go through a proxy that drops connections, sometimes after the PC already did the work.

Along the way the PC is restarted, moves to a new address (a new port stands in for a new IP), the phone's clock
jumps ahead, and the drop rate spikes. At the end it checks:

  - every decision was approved exactly once (ledger debits = approvals, the ledger chain is intact)
  - every alert reached the phone exactly once, in order
  - no honest request was refused as a replay
  - how long the phone took to recover after each event (printed, and returned for the test)

No network beyond 127.0.0.1. Uses a throwaway data folder.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import http.client
import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ===================================================================================== the PC
class Lab:
    """The real phone port (boardroom.build_remote_app + phone_api + phone_pairing_ui hello) in its own thread."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.board: Any = None
        self.alerts: Any = None
        self.port = 0

    def start(self, port: int) -> None:
        import alerts as al
        import boardroom as br
        import phone_api
        import phone_link
        import phone_pairing_ui as ppu

        phone_link._idem_wait.clear()
        phone_link._inflight.clear()
        self.port = port
        ready = threading.Event()

        def main() -> None:
            loop = asyncio.new_event_loop()
            self.loop = loop
            asyncio.set_event_loop(loop)
            self.board = br.Boardroom(self.data_dir)
            self.alerts = al.AlertCenter(self.data_dir)
            hub = SimpleNamespace(alerts=self.alerts, cards=None, quick=None)
            self.board.remote_hooks.append(phone_api.register(hub))
            ui = ppu.attach(SimpleNamespace(board=self.board, alerts=self.alerts), zeroconf_mod=None,
                            run=lambda *a, **k: SimpleNamespace(stdout="", returncode=1),
                            route_ip=lambda: "127.0.0.1", port=port, platform="linux-test")
            ui.data_dir = self.data_dir
            loop.run_until_complete(self.board.start(bind="127.0.0.1", port=port))
            ready.set()
            loop.run_forever()
            loop.run_until_complete(self.board.stop())
            if self.board._server_task is not None:
                try:
                    loop.run_until_complete(asyncio.wait_for(self.board._server_task, 5))
                except Exception:
                    pass
            loop.close()

        self.thread = threading.Thread(target=main, daemon=True)
        self.thread.start()
        ready.wait(10)

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(10)
        self.loop = self.thread = None

    def call(self, coro_or_fn: Any) -> Any:
        if asyncio.iscoroutine(coro_or_fn):
            return asyncio.run_coroutine_threadsafe(coro_or_fn, self.loop).result(10)
        return coro_or_fn()


# ===================================================================================== a flaky network
class FlakyProxy:
    """One request per connection. Drops some connections before the PC sees them, and some after the PC
    answered (the answer is lost), so retries and idempotency get exercised."""

    def __init__(self, target_port: int, drop_before: float = 0.0, drop_after: float = 0.0) -> None:
        self.target = target_port
        self.drop_before = drop_before
        self.drop_after = drop_after
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        self.dropped_before = self.dropped_after = 0
        self.rng = random.Random(7)
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    @staticmethod
    def _read_request(c: socket.socket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = c.recv(65536)
            if not chunk:
                return data
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        n = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1].strip())
        while len(rest) < n:
            chunk = c.recv(65536)
            if not chunk:
                break
            rest += chunk
        return head + b"\r\n\r\n" + rest

    def _one(self, c: socket.socket) -> None:
        with c:
            c.settimeout(70)
            with self.lock:
                r = self.rng.random()
            if r < self.drop_before:
                with self.lock:
                    self.dropped_before += 1
                return
            try:
                req = self._read_request(c)
                with socket.create_connection(("127.0.0.1", self.target), timeout=70) as t:
                    t.sendall(req)
                    resp = b""
                    while True:
                        chunk = t.recv(65536)
                        if not chunk:
                            break
                        resp += chunk
            except OSError:
                return
            if r < self.drop_before + self.drop_after:
                with self.lock:
                    self.dropped_after += 1
                return   # the PC did the work; the phone never hears back
            try:
                c.sendall(resp)
            except OSError:
                pass


# ===================================================================================== the phone
class Refused(Exception):
    def __init__(self, status: int, error: str) -> None:
        super().__init__(f"{status}: {error}")
        self.status, self.error = status, error


class SimPhone:
    """The Android app's link rules, in Python (keep in step with Link.kt)."""

    def __init__(self, device_id: str = "phone-soak-00000001", base_ms: float = 100.0) -> None:
        self.device_id = device_id
        self.read = ec.generate_private_key(ec.SECP256R1())
        self.sign = ec.generate_private_key(ec.SECP256R1())
        self.url_port = 0
        self.clock_error_ms = 0          # how wrong the phone's own clock is
        self.offset_ms = 0               # the correction learned from X-Orion-Time
        self.last_ts = 0
        self.ts_lock = threading.Lock()
        self.pc_key: Optional[bytes] = None
        self.pc_id = ""
        self.base_ms = base_ms
        self.resolver: Optional[Callable[[], list[int]]] = None   # "NSD": ports where PCs answer
        self.rng = random.Random(11)
        self.log: list[tuple[float, str]] = []
        self.replays = 0
        self.retries = 0
        self.resolves = 0
        self.down_since: Optional[float] = None
        self.recoveries: list[float] = []

    @staticmethod
    def der(key: Any) -> bytes:
        return key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    # ------------------------------------------------------------------ clocks
    def now_ms(self) -> int:
        return int(time.time() * 1000) + self.clock_error_ms

    def next_ts(self) -> str:
        with self.ts_lock:
            t = self.now_ms() + self.offset_ms
            if self.last_ts + 1 > t + 240_000:      # far ahead after a correction: start again from the PC's time
                self.last_ts = t - 1
            self.last_ts = max(t, self.last_ts + 1)
            return str(self.last_ts)

    def note_time(self, header: Optional[str], sent: int, got: int) -> None:
        if header and header.isdigit():
            mid = (sent + got) // 2
            self.offset_ms = int(header) - mid

    # ------------------------------------------------------------------ one exchange
    def _send(self, port: int, method: str, path: str, body: bytes, headers: dict[str, str],
              timeout: float) -> tuple[int, dict, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            sent = self.now_ms()
            conn.request(method, path, body=body or None, headers={**headers, "Connection": "close",
                                                                   "Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
            got = self.now_ms()
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            self.note_time(hdrs.get("x-orion-time"), sent, got)
            try:
                j = json.loads(raw) if raw else {}
            except ValueError:
                j = {}
            return r.status, j, hdrs
        finally:
            conn.close()

    def _signed(self, key: Any, method: str, path: str, body: bytes) -> dict[str, str]:
        import boardroom as br
        ts = self.next_ts()
        sig = key.sign(br.signing_message(method, path.split("?")[0], ts, body), ec.ECDSA(hashes.SHA256()))
        return {"X-Orion-Device": self.device_id, "X-Orion-Ts": ts, "X-Orion-Sig": base64.b64encode(sig).decode()}

    def call(self, method: str, path: str, body: Optional[dict] = None, key: str = "read",
             timeout: float = 5.0, attempts: int = 6) -> dict:
        """Read calls re-sign each try; a fingerprint call (key="sign") is signed once and resent as is."""
        raw = json.dumps(body).encode() if body is not None else b""
        k = self.read if key == "read" else self.sign
        extra: dict[str, str] = {}
        if key == "sign":
            extra["X-Orion-Idem"] = (body or {}).get("idem", "")
        fixed: Optional[dict[str, str]] = None
        last_err: Exception = RuntimeError("no try")
        skew_retry = True
        for i in range(attempts):
            if key == "sign":
                fixed = fixed or self._signed(k, method, path, raw)
                headers = {**fixed, **extra}
            else:
                headers = self._signed(k, method, path, raw)
            try:
                status, j, hdrs = self._send(self.url_port, method, path, raw, headers, timeout)
            except (OSError, http.client.HTTPException) as exc:
                last_err = exc
                self._went_down()
                self.retries += 1
                if i >= 1:
                    self.resolve()
                self._sleep(i)
                continue
            if status == 401 and "clock" in str(j.get("error", "")) and skew_retry:
                skew_retry = False      # X-Orion-Time already corrected the offset: sign again once
                fixed = None
                continue
            if status in (429, 502, 503):
                self.retries += 1
                self._sleep(i)
                continue
            if status == 401 and "replayed" in str(j.get("error", "")):
                self.replays += 1
            if status >= 400:
                self._came_back()
                raise Refused(status, str(j.get("error", "")))
            self._came_back()
            return j
        raise last_err

    def _sleep(self, attempt: int) -> None:
        cap = self.base_ms * 2 ** min(attempt, 5)
        time.sleep(self.rng.uniform(0, cap) / 1000.0)   # full jitter: phones never retry in step

    def _went_down(self) -> None:
        if self.down_since is None:
            self.down_since = time.monotonic()

    def _came_back(self) -> None:
        if self.down_since is not None:
            self.recoveries.append(time.monotonic() - self.down_since)
            self.down_since = None

    # ------------------------------------------------------------------ finding the PC again
    def hello(self, port: int) -> bool:
        import pc_identity
        nonce = base64.urlsafe_b64encode(os.urandom(24)).decode()
        raw = json.dumps({"nonce": nonce, "network": "wifi"}).encode()
        try:
            status, j, _ = self._send(port, "POST", "/phone/hello", raw, self._signed(self.read, "POST", "/phone/hello", raw), 2.0)
        except (OSError, http.client.HTTPException):
            return False
        if status != 200 or not self.pc_key:
            return False
        pc = j.get("pc") or {}
        if pc.get("id") != self.pc_id:
            return False
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        try:
            load_der_public_key(self.pc_key).verify(base64.b64decode(j.get("pc_sig", "")),
                                                    pc_identity.hello_message(self.pc_id, nonce),
                                                    ec.ECDSA(hashes.SHA256()))
        except Exception:
            return False
        return True

    def resolve(self) -> bool:
        """Like PcFinder.heal: look on the Wi-Fi, switch only to a PC that proves the pinned key."""
        if self.resolver is None:
            return False
        for port in self.resolver():
            if port != self.url_port and self.hello(port):
                self.url_port = port
                self.resolves += 1
                return True
        return False


# ===================================================================================== the run
def run(short: bool = True, verbose: bool = False) -> dict[str, Any]:
    import boardroom as br
    import phones as ph
    import pc_identity

    tmp = Path(tempfile.mkdtemp(prefix="orion-soak-"))
    lab = Lab(tmp)
    port_a, port_b = free_port(), free_port()
    lab.start(port_a)
    lab.call(lambda: lab.board.ledger.receive(1_000_000_00, "soak"))

    phone = SimPhone(base_ms=40 if short else 150)
    code = lab.board.phones.new_code()
    body = {"device_id": phone.device_id, "name": "Soak phone",
            "read_pub": base64.b64encode(phone.der(phone.read)).decode(),
            "sign_pub": base64.b64encode(phone.der(phone.sign)).decode(),
            "proof": ph.pairing_proof(code, phone.der(phone.read), phone.der(phone.sign))}
    phone.url_port = port_a
    st, j, _ = phone._send(port_a, "POST", "/phone/pair", json.dumps(body).encode(), {}, 5)
    assert st == 200, j
    assert pc_identity.pair_proof(code, base64.b64decode(j["pc_key"])) == j["pc_key_proof"]
    phone.pc_key = base64.b64decode(j["pc_key"])
    lab.board.phones.confirm(j["fingerprint"])
    phone.pc_id = lab.board.store.get("pc_id") or ""
    hello = phone.call("POST", "/phone/hello", {"network": "wifi"})
    phone.pc_id = (hello.get("pc") or {}).get("id", "")

    proxy = FlakyProxy(port_a, drop_before=0.10, drop_after=0.10)
    current = {"lab": port_a, "proxy": proxy}
    phone.url_port = proxy.port
    # "NSD": the proxy in front of wherever the PC is now (plus a stranger who can't prove the key)
    phone.resolver = lambda: [current["proxy"].port]

    n_decisions = 24 if short else 160
    proposed: list[str] = []
    approved: dict[str, int] = {}
    alerts_seen: list[int] = []
    errors: list[str] = []
    stop = threading.Event()
    events: list[tuple[str, float]] = []

    def propose_one(i: int) -> None:
        d = lab.call(lab.board.propose("payout.bounty", f"pay {i}", {"bucket": "bounties", "amount_cents": 1 + i % 3,
                                                                    "memo": f"soak {i}"}, "CFO"))
        proposed.append(d["id"])
        lab.call(lambda: lab.alerts.post("approval", f"Decision {i}", "soak"))

    def approver() -> None:
        while not stop.is_set() or len(approved) < len(proposed):
            try:
                res = phone.call("GET", "/phone/pending")
            except Exception as exc:
                errors.append(f"pending: {exc}")
                time.sleep(0.05)
                continue
            for d in res.get("pending", []):
                verdict = "reject" if d["task_type"] == "autonomy.promotion" else "approve"
                b = {"id": d["id"], "verdict": verdict, "packet_hash": d["packet_hash"], "note": "soak",
                     "idem": uuid.uuid4().hex}
                try:
                    out = phone.call("POST", "/phone/verdict", b, key="sign")
                    if verdict == "approve":
                        approved[d["id"]] = approved.get(d["id"], 0) + 1
                except Refused as exc:
                    if "already" in exc.error:   # decided by an earlier answer we never saw: must not happen
                        errors.append(f"verdict lost its answer: {exc}")
                    else:
                        errors.append(f"verdict: {exc}")
                except Exception as exc:
                    errors.append(f"verdict gave up: {exc}")
            if stop.is_set() and not res.get("pending"):
                break
            time.sleep(0.02)

    def alert_reader() -> None:
        since = 0
        while not stop.is_set():
            try:
                out = phone.call("GET", f"/phone/feed?since={since}&wait=1", timeout=8)
            except Exception as exc:
                errors.append(f"feed: {exc}")
                continue
            for a in out.get("alerts", []):
                alerts_seen.append(int(a["seq"]))
            since = max(since, int(out.get("last", since))) if not out.get("reset") else int(out.get("last", 0))

    def reader_noise() -> None:   # other screens polling at the same time: out-of-order timestamps
        while not stop.is_set():
            try:
                phone.call("POST", "/phone/hello", {"network": "wifi"})
            except Exception:
                pass
            time.sleep(0.03)

    threads = [threading.Thread(target=f, daemon=True) for f in (approver, alert_reader, reader_noise, reader_noise)]
    for t in threads:
        t.start()

    def measure(name: str, action: Callable[[], None]) -> None:
        before = len(phone.recoveries)
        t0 = time.monotonic()
        action()
        # recovery = until the phone next succeeds after the event
        deadline = time.monotonic() + 30
        ok_at = None
        while time.monotonic() < deadline:
            try:
                phone.call("GET", "/phone/pending", attempts=1, timeout=2)
                ok_at = time.monotonic()
                break
            except Exception:
                time.sleep(0.02)
        events.append((name, round((ok_at or deadline) - t0, 2)))
        if verbose:
            print(f"{name}: back after {events[-1][1]} s ({len(phone.recoveries) - before} outages)")

    per_phase = n_decisions // 6
    i = 0

    def batch() -> None:
        nonlocal i
        for _ in range(per_phase):
            propose_one(i)
            i += 1
            time.sleep(0.01 if short else 0.1)

    batch()

    def restart() -> None:
        lab.stop()
        time.sleep(0.3)
        lab.start(current["lab"])
    measure("PC restart", restart)
    batch()

    def move() -> None:   # the PC gets a new address; the old one goes dark
        lab.stop()
        lab.start(port_b)
        current["lab"] = port_b
        current["proxy"] = FlakyProxy(port_b, drop_before=0.10, drop_after=0.10)
    measure("PC address change", move)
    batch()

    def skew() -> None:
        phone.clock_error_ms = 400_000   # the phone's clock jumps almost 7 minutes ahead
    measure("phone clock jump", skew)
    batch()

    def storm() -> None:
        current["proxy"].drop_before, current["proxy"].drop_after = 0.35, 0.25
    measure("dropped connections", storm)
    batch()

    def sleep_wake() -> None:   # PC asleep for a moment (port gone), then back on the same address
        lab.stop()
        time.sleep(1.0 if short else 5.0)
        lab.start(current["lab"])
    measure("PC sleep and wake", sleep_wake)
    while i < n_decisions:
        propose_one(i)
        i += 1
    current["proxy"].drop_before, current["proxy"].drop_after = 0.05, 0.05

    deadline = time.monotonic() + (60 if short else 180)
    while time.monotonic() < deadline and len(approved) < len(proposed):
        time.sleep(0.05)
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(15)

    decisions = [lab.board.get(d) for d in proposed]
    debits = [e for e in lab.board.ledger.recent(10_000) if e["kind"] == "debit"]
    all_seqs = [r["seq"] for r in lab.alerts._q("SELECT seq FROM alerts ORDER BY seq")]
    result = {
        "decisions": len(proposed),
        "approved_once": sum(1 for d in decisions if d and d["status"] == "approved"),
        "still_pending": sum(1 for d in decisions if d and d["status"] == "pending"),
        "failed": sum(1 for d in decisions if d and d["status"] == "failed"),
        "applied_twice": sum(1 for v in approved.values() if v > 1),
        "debits": len(debits),
        "chain_ok": lab.board.ledger.verify_chain(),
        "alerts": len(all_seqs),
        "alerts_received": len(alerts_seen),
        "alerts_duplicated": len(alerts_seen) - len(set(alerts_seen)),
        "alerts_missing": len(set(all_seqs) - set(alerts_seen)),
        "alerts_in_order": alerts_seen == sorted(alerts_seen),
        "honest_replay_refusals": phone.replays,
        "retries": phone.retries,
        "re_resolved": phone.resolves,
        "dropped_before": proxy.dropped_before + current["proxy"].dropped_before,
        "dropped_after_answer": proxy.dropped_after + current["proxy"].dropped_after,
        "recovery_s": dict(events),
        "errors": errors[:10],
        "error_count": len(errors),
    }
    lab.stop()
    return result


def check(r: dict[str, Any]) -> list[str]:
    bad = []
    if r["approved_once"] != r["decisions"]:
        bad.append(f"{r['decisions'] - r['approved_once']} decision(s) not approved")
    if r["applied_twice"] or r["debits"] != r["decisions"]:
        bad.append(f"ledger has {r['debits']} debits for {r['decisions']} decisions")
    if not r["chain_ok"]:
        bad.append("ledger chain broken")
    if r["alerts_missing"] or r["alerts_duplicated"] or not r["alerts_in_order"]:
        bad.append(f"alerts: {r['alerts_missing']} missing, {r['alerts_duplicated']} twice")
    if r["honest_replay_refusals"]:
        bad.append(f"{r['honest_replay_refusals']} honest request(s) refused as replays")
    if any(e.startswith("verdict lost") for e in r["errors"]):
        bad.append("an approval's answer was lost")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--short", action="store_true")
    a = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.ERROR)
    res = run(short=a.short, verbose=True)
    print(json.dumps(res, indent=2))
    problems = check(res)
    print("OK" if not problems else "PROBLEMS: " + "; ".join(problems))
    raise SystemExit(1 if problems else 0)
