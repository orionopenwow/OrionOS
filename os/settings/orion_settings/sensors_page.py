"""Settings > Privacy dashboard (OrionOS 1.0 plan, K.): everything Orion can see, hear or keep, each with its state
and a one-tap off, plus "Turn everything off". Turning things on stays on each feature's own page.
Data: sensors_data.py."""
from __future__ import annotations

from typing import Any

from . import sensors_data as D
from .pages import Page


class SensorsPage(Page):
    key, title, icon, section = "sensors", "Privacy dashboard", "security-medium-symbolic", "System"
    blurb = ("Everything Orion can see, hear or keep, in one place. Each one can be switched off here; switching on "
             "is done on its own page.")

    def load(self) -> Any:
        return D.dashboard(self.sys.r, self.sys.privacy())

    def _off(self, sid: str) -> None:
        if sid == "camera":   # Settings' own camera switch (works even when Orion's brain is off)
            self.win.act(lambda: self.sys.set_camera(False), after=self.refresh)
        else:
            self.win.act(lambda: D.turn_off(self.sys.r, sid), after=self.refresh)

    def _all_off(self, d: dict[str, Any]) -> None:
        def go() -> tuple[bool, str]:
            ok, msg = D.turn_all_off(self.sys.r) if d["brain"] else (True, "")
            cam = next((s for s in d["sensors"] if s["id"] == "camera" and s.get("on")), None)
            if cam is not None and not d["brain"]:
                ok, msg = self.sys.set_camera(False)
            return ok, msg or "Everything is off."
        self.win.act(go, after=self.refresh)

    def fill(self, d: dict[str, Any]) -> None:
        g = self.group("Right now", d["words"])
        g.add(self.row("Turn everything off", "Screen reading, microphone, camera, recorders and the inbox mirror",
                       icon="action-unavailable-symbolic",
                       suffix=[self.button("Turn all off", lambda: self._all_off(d), style="destructive-action")]
                       if d["on"] else [self.pill("all off", "ok")]))
        lg = self.group("What Orion can use")
        for s in d["sensors"]:
            text, kind = D.state_words(s)
            sub = str(s.get("what") or "")
            if not s.get("on") and s.get("available", True) and s.get("how_on"):
                sub += f" To turn on: {s['how_on']}."
            suffix = [self.pill(text, kind)]
            if s.get("can_off") or (s["id"] == "camera" and s.get("on")):
                suffix.append(self.button("Turn off", lambda sid=s["id"]: self._off(sid)))
            lg.add(self.row(str(s.get("label", s["id"])), sub, icon="security-high-symbolic", suffix=suffix))
