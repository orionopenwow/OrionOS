"""Minimal greetd IPC client for the Orion login screen.

greetd speaks length-prefixed JSON over the UNIX socket in $GREETD_SOCK: a 4-byte unsigned length in the host's
byte order, then that many bytes of UTF-8 JSON. Requests: create_session, post_auth_message_response,
start_session, cancel_session. Responses: success, error, auth_message.

The password passes through here once and is never logged or kept: callers hand it in, it is sent on the first
"secret" prompt, and the local reference is dropped right after.
"""
from __future__ import annotations

import json
import os
import socket
import struct
from dataclasses import dataclass, field

_LEN = struct.Struct("=I")  # native byte order, as greetd expects
MAX_MESSAGE = 1 << 20


class GreetdError(Exception):
    """The socket broke or greetd sent something we don't understand."""


@dataclass
class LoginResult:
    ok: bool
    # "wrong_password" | "needs_more" | "error" (only when ok is False)
    reason: str = ""
    # Plain-words text for the screen (never contains the password).
    message: str = ""
    # Info/error texts PAM showed along the way ("Your password expires in 3 days").
    notes: list[str] = field(default_factory=list)


class GreetdClient:
    def __init__(self, path: str | None = None, timeout: float = 30.0):
        self.path = path or os.environ.get("GREETD_SOCK", "")
        if not self.path:
            raise GreetdError("GREETD_SOCK is not set (the login screen must be started by greetd)")
        self.timeout = timeout
        self._sock: socket.socket | None = None

    # ---- transport -------------------------------------------------------------------------------------
    def connect(self) -> None:
        if self._sock is None:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(self.path)
            self._sock = s

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise GreetdError("greetd closed the connection")
            buf.extend(chunk)
        return bytes(buf)

    def request(self, payload: dict) -> dict:
        self.connect()
        assert self._sock is not None
        data = json.dumps(payload).encode("utf-8")
        try:
            self._sock.sendall(_LEN.pack(len(data)) + data)
            (length,) = _LEN.unpack(self._recv_exact(_LEN.size))
            if length > MAX_MESSAGE:
                raise GreetdError("reply from greetd is too large")
            reply = json.loads(self._recv_exact(length).decode("utf-8"))
        except (OSError, ValueError) as e:
            self.close()
            raise GreetdError(f"talking to greetd failed: {type(e).__name__}") from None
        finally:
            del data  # holds the password for post_auth_message_response
        if not isinstance(reply, dict) or "type" not in reply:
            raise GreetdError("unexpected reply from greetd")
        return reply

    # ---- requests --------------------------------------------------------------------------------------
    def create_session(self, username: str) -> dict:
        return self.request({"type": "create_session", "username": username})

    def respond(self, response: str | None) -> dict:
        return self.request({"type": "post_auth_message_response", "response": response})

    def start_session(self, cmd: list[str], env: list[str] | None = None) -> dict:
        return self.request({"type": "start_session", "cmd": cmd, "env": env or []})

    def cancel_session(self) -> None:
        try:
            self.request({"type": "cancel_session"})
        except GreetdError:
            pass

    # ---- the whole check -------------------------------------------------------------------------------
    def authenticate(self, username: str, password: str | None) -> LoginResult:
        """Check the password. On success the session is authenticated and waits for start_session().

        password=None is the no-password start ("Start Orion again" while OrionOS starts without a password): it
        only succeeds when PAM lets the user in without asking (os/greeter/orion-login-auto-ok). If PAM asks for a
        password after all, the result is reason "needs_password" and the screen shows the field.

        On any failure the half-made session is cancelled, so the next try starts clean.
        """
        notes: list[str] = []
        secret: str | None = password
        password_less = password is None
        del password
        try:
            reply = self.create_session(username)
            while True:
                kind = reply.get("type")
                if kind == "success":
                    return LoginResult(True, notes=notes)
                if kind == "error":
                    self.cancel_session()
                    if reply.get("error_type") == "auth_error":
                        return LoginResult(False, "wrong_password", "That's not it", notes)
                    return LoginResult(False, "error", _plain(reply.get("description")) or
                                       "The login didn't work. Try again.", notes)
                if kind != "auth_message":
                    self.cancel_session()
                    return LoginResult(False, "error", "The login service answered oddly. Try again.", notes)
                mtype = reply.get("auth_message_type")
                text = _plain(reply.get("auth_message"))
                if mtype in ("info", "error"):
                    if text:
                        notes.append(text)
                    reply = self.respond(None)
                elif mtype == "secret" and secret is not None:
                    answer, secret = secret, None  # sent exactly once
                    reply = self.respond(answer)
                    del answer
                elif mtype == "secret" and password_less:
                    self.cancel_session()
                    return LoginResult(False, "needs_password", "Type your password, then press Enter.", notes)
                else:
                    # A second password prompt, a one-time code, a "change your password now"... The single field
                    # can't answer that; the text login can.
                    self.cancel_session()
                    hint = f" ({text.rstrip(':')})" if text else ""
                    return LoginResult(False, "needs_more",
                                       f"Your account needs more than a password{hint}. "
                                       "Use the text login: Ctrl+Alt+F2.", notes)
        finally:
            secret = None


def _plain(v) -> str:
    return v.strip() if isinstance(v, str) else ""
