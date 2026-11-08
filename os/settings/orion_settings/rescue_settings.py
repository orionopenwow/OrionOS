"""Settings > Lock & rescue, without GTK (tested in os/settings/tests/test_rescue_settings.py).

  - lock after N minutes away, screens off after M minutes (~/.config/orion/idle.conf, read by os/lab/bin/orion-idle)
  - your own "never read windows with these words" list for screen reading (~/.config/orion/capture-exclude.txt,
    read by apps/daemon/capture_privacy.py)
  - "Undo the last change" / "Undo the last update" (os/rescue/orion_rescue/undo.py, the same as the Rescue panel)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

LOCK_CHOICES = [("1", "After 1 minute"), ("2", "After 2 minutes"), ("5", "After 5 minutes"),
                ("10", "After 10 minutes"), ("15", "After 15 minutes"), ("30", "After 30 minutes"),
                ("0", "Never (only when I lock it or the computer sleeps)")]
OFF_CHOICES = [("2", "After 2 minutes"), ("5", "After 5 minutes"), ("10", "After 10 minutes"),
               ("15", "After 15 minutes"), ("30", "After 30 minutes"), ("60", "After 1 hour"), ("0", "Never")]
DEFAULTS = {"LOCK_AFTER": "5", "SCREEN_OFF_AFTER": "10"}


def _rescue_lib() -> None:
    for d in (os.environ.get("ORION_RESCUE_DIR", ""), "/opt/orion/rescue",
              str(Path(__file__).resolve().parents[2] / "rescue")):
        if d and (Path(d) / "orion_rescue").is_dir():
            if d not in sys.path:
                sys.path.insert(0, d)
            return


class LockRescue:
    def __init__(self, conf_dir: Optional[Path] = None) -> None:
        self.conf = conf_dir or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "orion"

    # ---- idle --------------------------------------------------------------------------------------
    def idle(self) -> dict[str, str]:
        out = dict(DEFAULTS)
        try:
            for line in (self.conf / "idle.conf").read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() in out and v.strip().isdigit():
                    out[k.strip()] = str(int(v.strip()))
        except OSError:
            pass
        return out

    def set_idle(self, key: str, minutes: str) -> tuple[bool, str]:
        if key not in DEFAULTS or not str(minutes).isdigit() or int(minutes) > 240:
            return False, "That isn't a time this can use."
        cur = self.idle()
        cur[key] = str(int(minutes))
        self.conf.mkdir(parents=True, exist_ok=True)
        text = ("# OrionOS idle settings (Settings > Lock & rescue; read by orion-idle). Minutes; 0 = never.\n"
                + "".join(f"{k}={v}\n" for k, v in cur.items()))
        tmp = self.conf / "idle.conf.new"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.conf / "idle.conf")
        self._restart_idle()
        if key == "LOCK_AFTER":
            return True, ("The screen won't lock by itself (it still locks before sleep)." if cur[key] == "0"
                          else f"The screen locks after {cur[key]} minute{'s' if cur[key] != '1' else ''} away.")
        return True, ("The screens stay on." if cur[key] == "0" else
                      f"The screens turn off after {cur[key]} minutes away.")

    @staticmethod
    def _restart_idle() -> None:
        try:
            subprocess.run(["systemctl", "--user", "try-restart", "orion-idle.service"], capture_output=True,
                           timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    # ---- screen reading: your own words -------------------------------------------------------------
    def exclude_words(self) -> list[str]:
        try:
            lines = (self.conf / "capture-exclude.txt").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]

    def set_exclude_words(self, text: str) -> tuple[bool, str]:
        words = []
        for w in text.replace("\n", ",").split(","):
            w = " ".join(w.split())[:60]
            if w and w.lower() not in (x.lower() for x in words):
                words.append(w)
        words = words[:200]
        self.conf.mkdir(parents=True, exist_ok=True)
        body = ("# Screen reading never looks at a window whose title contains one of these (one per line).\n"
                + "".join(w + "\n" for w in words))
        p = self.conf / "capture-exclude.txt"
        p.write_text(body, encoding="utf-8")
        os.chmod(p, 0o600)
        return True, (f"Saved: {len(words)} word{'s' if len(words) != 1 else ''} on your list." if words
                      else "Your own list is empty (the built-in exclusions still apply).")

    # ---- undo ----------------------------------------------------------------------------------------
    @staticmethod
    def plans() -> dict[str, Any]:
        _rescue_lib()
        try:
            from orion_rescue import undo
        except ImportError:
            return {"change": None, "update": None}
        return {"change": undo.plan_last_change(), "update": undo.plan_last_update()}

    @staticmethod
    def run(plan: Any) -> tuple[bool, str]:
        _rescue_lib()
        from orion_rescue import undo
        return undo.run(plan)
