"""A stand-in for Orion's daemon (port 9120) for the Start Here wizard's dry-run tests. Standard library only.

It needs the same key as the real one (Authorization: Bearer <token>) on everything but GET /health, and plays a
short story: after the Phone panel is opened a phone pairs a few polls later, the phone's checks finish, and once
the Test Center is opened every item passes. GET /__log (no key) lists the requests it saw.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps" / "daemon"))
import phone_app_share  # noqa: E402  (only its pure QR helper is used)

APK_URL = "http://192.168.1.20:9121/phone/app.apk"
ITEMS = [("daemon_up", "startup", "pc"), ("phone_paired", "phone", "pc"), ("phone_reaches", "phone", "both"),
         ("phone_fingerprint_key", "phone", "phone"), ("phone_battery", "phone", "phone"),
         ("approve_card", "phone", "both"), ("agents_found", "agents", "pc")]


class Story:
    def __init__(self, token: str) -> None:
        self.token = token
        self.log: list[tuple[str, str, str]] = []
        self.hud_opened = 0
        self.phone_polls = 0
        self.paired = False
        self.tests_open = False
        self.test_polls = 0
        self.share_polls = -1
        self.lock = threading.Lock()

    def tests(self) -> dict:
        self.test_polls += 1
        status = {iid: "todo" for iid, _, _ in ITEMS}
        status["daemon_up"] = "pass"
        if self.paired:
            status["phone_paired"] = "pass"
            if self.test_polls >= 2:
                status["phone_reaches"] = "pass"
                status["phone_fingerprint_key"] = "pass"
            if self.test_polls >= 3:
                status["phone_battery"] = "skip"
        if self.tests_open and self.test_polls >= 2:
            status = {k: ("skip" if k == "agents_found" else "pass") for k in status}
        groups: dict[str, list] = {}
        for iid, g, where in ITEMS:
            groups.setdefault(g, []).append({"id": iid, "group": g, "title": iid, "where": where, "kind": "auto",
                                             "status": status[iid], "detail": "", "next": f"Do the {iid} thing."})
        flat = [x for v in groups.values() for x in v]
        summary = {"pass": 0, "fail": 0, "todo": 0, "skip": 0}
        for x in flat:
            summary[x["status"]] += 1
        nxt = next((x for x in flat if x["status"] == "todo"), None)
        return {"groups": [{"id": g, "title": g, "items": v} for g, v in groups.items()], "next": nxt,
                "summary": summary, "running": False, "platform": "windows"}

    def share(self) -> dict:
        is_open = self.share_polls >= 0 and self.share_polls < 3
        if self.share_polls >= 0:
            self.share_polls += 1
        return {"open": is_open, "apk": True, "url": APK_URL if is_open else "", "until": 1.0,
                "qr_rows": phone_app_share.qr_rows(APK_URL) if is_open else [], "qr_svg": "",
                "downloads": 1 if self.share_polls >= 2 else 0, "lan": {"ok": True, "fix": None, "words": ""}}

    def handle(self, method: str, path: str, auth: str, body: dict) -> tuple[int, dict]:
        with self.lock:
            self.log.append((method, path, "key" if auth == f"Bearer {self.token}" else "nokey"))
            if method == "GET" and path == "/__log":
                return 200, {"log": self.log}
            if method == "GET" and path == "/health":
                return 200, {"ok": True}
            if auth != f"Bearer {self.token}":
                return 401, {"error": "Orion's key is needed"}
            if method == "GET" and path == "/tests":
                return 200, self.tests()
            if method == "POST" and path == "/tests/open":
                self.tests_open = True
                self.test_polls = 0
                return 200, {"ok": True}
            if method == "GET" and path == "/tests/report":
                return 200, {"markdown": "# Orion test report\n\n2026-11-07 · Windows\n"}
            if method == "GET" and path == "/phones":
                if self.hud_opened:
                    self.phone_polls += 1
                    if self.phone_polls >= 3 and not self.paired:
                        self.paired = True
                        self.test_polls = 0
                return 200, {"phones": [{"device_id": "p1", "name": "Test phone"}] if self.paired else []}
            if method == "POST" and path == "/hud/open":
                self.hud_opened += 1
                return 200, {"ok": True, "what": body.get("what"), "window": True}
            if method == "POST" and path == "/phones/app-share":
                self.share_polls = 0
                return 200, self.share()
            if method == "GET" and path == "/phones/app-share":
                return 200, self.share()
            if method == "POST" and path == "/phones/app-share/close":
                self.share_polls = 99
                return 200, {"open": False}
            return 404, {"error": "not here"}


def serve(token: str) -> tuple[ThreadingHTTPServer, Story]:
    story = Story(token)

    class H(BaseHTTPRequestHandler):
        def _go(self, method: str) -> None:
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            code, out = story.handle(method, self.path, self.headers.get("authorization") or "",
                                     body if isinstance(body, dict) else {})
            data = json.dumps(out).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json")   # no charset, like FastAPI
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._go("GET")

        def do_POST(self) -> None:
            self._go("POST")

        def log_message(self, *a) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, story
