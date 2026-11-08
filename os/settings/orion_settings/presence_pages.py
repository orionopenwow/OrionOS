"""Settings > Privacy: the "Camera presence" group (after-1.0 plan §6). Logic in presence_settings.py."""
from __future__ import annotations

from typing import Any, Optional

from . import presence_settings as PS


def presence_group(page: Any, st: Optional[dict[str, Any]]) -> None:
    g = page.group("Camera presence", "Lock when you leave, greet you back, and hand gestures. All on this computer; "
                                      "pictures are never saved or sent. A camera light shows in Orion while it's on.")
    if st is None:
        g.add(page.row("Orion's brain isn't answering", PS.NOT_RUNNING, icon="dialog-information-symbolic"))
        return
    r = page.sys.r
    page.switch(g, "Camera presence", PS.summary(st), bool(st.get("on")), lambda on: PS.set_on(r, on),
                sensitive=not st.get("missing"))
    cfg = st.get("config") or {}
    for key, title, sub in PS.SWITCHES:
        page.switch(g, title, sub, bool(cfg.get(key)), lambda v, k=key: PS.set_option(r, k, v))
