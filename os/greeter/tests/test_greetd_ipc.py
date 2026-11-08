"""greetd IPC client against a fake greetd on a UNIX socket.  Run: python -m pytest -q os/greeter/tests"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from greetd_ipc import GreetdClient, GreetdError  # noqa: E402

L = struct.Struct("=I")


class FakeGreetd:
    """Answers each request with handler(request, state) -> reply dict (or None to hang up)."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.state = {}
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "greetd.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _recv(self, c, n):
        b = b""
        while len(b) < n:
            chunk = c.recv(n - len(b))
            if not chunk:
                return None
            b += chunk
        return b

    def _run(self):
        c, _ = self.srv.accept()
        with c:
            while True:
                h = self._recv(c, 4)
                if h is None:
                    return
                req = json.loads(self._recv(c, L.unpack(h)[0]))
                self.requests.append(req)
                rep = self.handler(req, self.state)
                if rep is None:
                    return
                data = json.dumps(rep).encode()
                c.sendall(L.pack(len(data)) + data)

    def close(self):
        self.srv.close()


SECRET = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "}
OK = {"type": "success"}
AUTH_ERR = {"type": "error", "error_type": "auth_error", "description": "pam_authenticate: AUTH_ERR"}


def pam(good="hunter2", extra=()):
    """A PAM-ish conversation: optional extra prompts first, then the password."""
    def h(req, st):
        t = req["type"]
        if t == "create_session":
            st["queue"] = list(extra) + [SECRET]
            return st["queue"].pop(0)
        if t == "post_auth_message_response":
            if st.get("queue"):
                return st["queue"].pop(0)
            if req["response"] == good:
                return OK
            return AUTH_ERR
        if t == "cancel_session":
            return OK
        if t == "start_session":
            return OK
        return {"type": "error", "error_type": "error", "description": "bad request"}
    return h


@pytest.fixture
def fake():
    servers = []

    def make(handler):
        s = FakeGreetd(handler)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def test_success_then_start(fake):
    g = fake(pam())
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("orion", "hunter2")
        assert r.ok and r.reason == ""
        assert c.start_session(["/usr/local/bin/orion-session"], ["ORION_LOGIN_HANDOFF=blackhole"])["type"] == "success"
    types = [q["type"] for q in g.requests]
    assert types == ["create_session", "post_auth_message_response", "start_session"]
    assert g.requests[0]["username"] == "orion"
    assert g.requests[2]["cmd"] == ["/usr/local/bin/orion-session"]


def test_wrong_password_cancels_and_retry_works(fake):
    g = fake(pam())
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("orion", "nope")
        assert not r.ok and r.reason == "wrong_password" and r.message == "That's not it"
        assert "nope" not in r.message
        assert c.authenticate("orion", "hunter2").ok
    types = [q["type"] for q in g.requests]
    assert types == ["create_session", "post_auth_message_response", "cancel_session",
                     "create_session", "post_auth_message_response"]


def test_info_messages_then_password(fake):
    info = {"type": "auth_message", "auth_message_type": "info", "auth_message": "Welcome back"}
    warn = {"type": "auth_message", "auth_message_type": "error", "auth_message": "Caps might be on"}
    g = fake(pam(extra=[info, warn]))
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("orion", "hunter2")
    assert r.ok and r.notes == ["Welcome back", "Caps might be on"]
    responses = [q["response"] for q in g.requests if q["type"] == "post_auth_message_response"]
    assert responses == [None, None, "hunter2"]  # info prompts are acknowledged with null, password sent once


def test_second_secret_prompt_is_not_answered_with_the_password(fake):
    otp = {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Verification code:"}

    def h(req, st):
        if req["type"] == "create_session":
            return SECRET
        if req["type"] == "post_auth_message_response":
            return otp
        return OK
    g = fake(h)
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("orion", "hunter2")
    assert not r.ok and r.reason == "needs_more" and "Verification code" in r.message
    sent = [q.get("response") for q in g.requests if q["type"] == "post_auth_message_response"]
    assert sent == ["hunter2"]
    assert g.requests[-1]["type"] == "cancel_session"


def test_visible_prompt_needs_text_login(fake):
    vis = {"type": "auth_message", "auth_message_type": "visible", "auth_message": "Username:"}
    g = fake(lambda req, st: vis if req["type"] == "create_session" else OK)
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("orion", "x")
    assert r.reason == "needs_more"
    assert [q["type"] for q in g.requests] == ["create_session", "cancel_session"]


def test_other_error_is_reported_plainly(fake):
    err = {"type": "error", "error_type": "error", "description": "user is unknown"}
    g = fake(lambda req, st: err if req["type"] == "create_session" else OK)
    with GreetdClient(g.path, timeout=5) as c:
        r = c.authenticate("ghost", "x")
    assert not r.ok and r.reason == "error" and r.message == "user is unknown"


def test_greetd_hangs_up(fake):
    g = fake(lambda req, st: None)
    with GreetdClient(g.path, timeout=5) as c:
        with pytest.raises(GreetdError) as e:
            c.authenticate("orion", "hunter2")
    assert "hunter2" not in str(e.value)


def test_wire_format_is_native_length_prefix(fake):
    g = fake(pam())
    c = GreetdClient(g.path, timeout=5)
    c.connect()
    raw = c._sock
    body = json.dumps({"type": "create_session", "username": "orion"}).encode()
    raw.sendall(struct.pack("=I", len(body)) + body)
    n = struct.unpack("=I", raw.recv(4))[0]
    assert json.loads(raw.recv(n))["auth_message_type"] == "secret"
    c.close()


def test_missing_socket_env(monkeypatch):
    monkeypatch.delenv("GREETD_SOCK", raising=False)
    with pytest.raises(GreetdError):
        GreetdClient()
