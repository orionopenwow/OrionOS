"""Two checks shared by Orion's small 127.0.0.1 file servers (Projects' "Play it", the self-change preview).

  host_ok      the request's Host header must be 127.0.0.1:<port> or localhost:<port>, so a web page can't reach
               the server through a name it controls (DNS rebinding)
  inside       the file asked for must really be inside the served folder after symlinks are followed
"""
from __future__ import annotations

import os
from typing import Any


def host_ok(handler: Any) -> bool:
    port = handler.server.server_address[1]
    host = (handler.headers.get("Host") or "").strip().lower()
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def inside(directory: str, path: str) -> bool:
    root = os.path.realpath(directory)
    real = os.path.realpath(path)
    try:
        return os.path.commonpath([root, real]) == root
    except ValueError:   # different drives on Windows
        return False


def refuse(handler: Any) -> bool:
    """True (and an error already sent) when the request fails either check."""
    if not host_ok(handler):
        handler.send_error(403, "Wrong host")
        return True
    if not inside(handler.directory, handler.translate_path(handler.path)):
        handler.send_error(404)
        return True
    return False
