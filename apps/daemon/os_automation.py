"""OS & Desktop Automation Engine for Orion.

Provides autonomous desktop interaction: mouse movements, clicks, typing,
hotkeys, scrolling, drags, multi-step workflow execution, and window utilities
with built-in fail-safe guards.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger("orion.automation")

import linux_desktop

if linux_desktop.is_wayland():
    # OrionOS (Sway): pyautogui only speaks X11, so use the Sway/wtype implementation instead.
    pyautogui = linux_desktop.SwayAutoGUI()  # type: ignore[assignment]
    PYAUTOGUI_AVAILABLE = linux_desktop.SwayAutoGUI.available()
    if not PYAUTOGUI_AVAILABLE:
        log.warning("Wayland session without Sway IPC; screen mouse/keyboard automation disabled")
else:
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # Moving mouse to any corner stops execution immediately
        pyautogui.PAUSE = 0.05
        PYAUTOGUI_AVAILABLE = True
    except Exception:  # ImportError, or Xlib errors when there is no display at all
        PYAUTOGUI_AVAILABLE = False
        log.warning("pyautogui not available; screen mouse/keyboard automation disabled")


def open_url(url: str) -> bool:
    try:
        webbrowser.open(url)
        return True
    except Exception as exc:
        log.warning("Failed to open URL %s: %s", url, exc)
        return False


def show_os_notification(title: str, message: str) -> bool:
    try:
        if sys.platform == "win32":
            cmd = (
                f'[reflection.assembly]::loadwithpartialname("System.Windows.Forms"); '
                f'[System.Windows.Forms.MessageBox]::Show("{message}", "{title}")'
            )
            subprocess.Popen(["powershell", "-Command", cmd], creationflags=subprocess.CREATE_NO_WINDOW)
            return True
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'display notification "{message}" with title "{title}"'])
            return True
        return linux_desktop.notify(title, message)
    except Exception as exc:
        log.warning("Failed to show OS notification: %s", exc)
        return False


@dataclass
class WorkflowStepResult:
    step_index: int
    action: str
    success: bool
    detail: str = ""


class DesktopAutomationEngine:
    """Executes mouse, keyboard, and macro workflows on the desktop."""

    def __init__(self) -> None:
        self.is_available = PYAUTOGUI_AVAILABLE
        self._abort_flag = False

    def abort(self) -> None:
        self._abort_flag = True

    def get_screen_size(self) -> tuple[int, int]:
        if not self.is_available:
            return (1920, 1080)
        return pyautogui.size()

    def get_mouse_position(self) -> tuple[int, int]:
        if not self.is_available:
            return (0, 0)
        return pyautogui.position()

    def move_to(self, x: int, y: int, duration: float = 0.2) -> bool:
        if not self.is_available:
            return False
        try:
            pyautogui.moveTo(x, y, duration=max(0.05, duration))
            return True
        except Exception as exc:
            log.error("move_to(%d, %d) failed: %s", x, y, exc)
            return False

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1) -> bool:
        if not self.is_available:
            return False
        try:
            if x is not None and y is not None:
                pyautogui.click(x=x, y=y, clicks=clicks, button=button)
            else:
                pyautogui.click(clicks=clicks, button=button)
            return True
        except Exception as exc:
            log.error("click(%s, %s, %s) failed: %s", x, y, button, exc)
            return False

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self.click(x=x, y=y, clicks=2)

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self.click(x=x, y=y, button="right")

    def type_text(self, text: str, interval: float = 0.02) -> bool:
        if not self.is_available or not text:
            return False
        try:
            # For multiline or special characters, clipboard paste is often safer,
            # but pyautogui.write handles standard ascii nicely.
            pyautogui.write(text, interval=interval)
            return True
        except Exception as exc:
            log.error("type_text failed: %s", exc)
            return False

    def press_key(self, key: str) -> bool:
        if not self.is_available or not key:
            return False
        try:
            key_name = key.lower().strip()
            # Normalize common names
            key_map = {
                "enter": "enter",
                "return": "enter",
                "esc": "esc",
                "escape": "escape",
                "backspace": "backspace",
                "tab": "tab",
                "space": "space",
                "up": "up",
                "down": "down",
                "left": "left",
                "right": "right",
                "win": "win",
                "windows": "win",
                "cmd": "command",
                "ctrl": "ctrl",
                "alt": "alt",
                "shift": "shift",
            }
            resolved = key_map.get(key_name, key_name)
            pyautogui.press(resolved)
            return True
        except Exception as exc:
            log.error("press_key(%s) failed: %s", key, exc)
            return False

    def hotkey(self, *keys: str) -> bool:
        if not self.is_available or not keys:
            return False
        try:
            clean_keys = [k.lower().strip() for k in keys if k.strip()]
            pyautogui.hotkey(*clean_keys)
            return True
        except Exception as exc:
            log.error("hotkey(%s) failed: %s", keys, exc)
            return False

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        if not self.is_available:
            return False
        try:
            pyautogui.scroll(clicks, x=x, y=y)
            return True
        except Exception as exc:
            log.error("scroll(%d) failed: %s", clicks, exc)
            return False

    def drag(self, from_x: int, from_y: int, to_x: int, to_y: int, duration: float = 0.4) -> bool:
        if not self.is_available:
            return False
        try:
            pyautogui.moveTo(from_x, from_y)
            pyautogui.dragTo(to_x, to_y, duration=duration, button="left")
            return True
        except Exception as exc:
            log.error("drag(%d,%d -> %d,%d) failed: %s", from_x, from_y, to_x, to_y, exc)
            return False

    def execute_single_action(self, action: dict[str, Any]) -> tuple[bool, str]:
        """Execute a single declarative action."""
        act = action.get("action", "").lower().strip()
        try:
            if act == "click":
                x, y = action.get("x"), action.get("y")
                btn = action.get("button", "left")
                clicks = int(action.get("clicks", 1))
                ok = self.click(x=x, y=y, button=btn, clicks=clicks)
                return ok, f"Clicked at ({x}, {y}) button={btn}"
            elif act == "double_click":
                x, y = action.get("x"), action.get("y")
                ok = self.double_click(x=x, y=y)
                return ok, f"Double-clicked at ({x}, {y})"
            elif act == "right_click":
                x, y = action.get("x"), action.get("y")
                ok = self.right_click(x=x, y=y)
                return ok, f"Right-clicked at ({x}, {y})"
            elif act == "move":
                x, y = action.get("x", 0), action.get("y", 0)
                dur = float(action.get("duration", 0.2))
                ok = self.move_to(x=x, y=y, duration=dur)
                return ok, f"Moved to ({x}, {y})"
            elif act == "type":
                text = str(action.get("text", ""))
                interval = float(action.get("interval", 0.02))
                ok = self.type_text(text=text, interval=interval)
                return ok, f"Typed: {text[:40]}"
            elif act == "press":
                key = str(action.get("key", ""))
                ok = self.press_key(key)
                return ok, f"Pressed key: {key}"
            elif act == "hotkey":
                keys = action.get("keys", [])
                if isinstance(keys, str):
                    keys = [k.strip() for k in keys.split("+")]
                ok = self.hotkey(*keys)
                return ok, f"Pressed hotkey: {'+'.join(keys)}"
            elif act == "scroll":
                clicks = int(action.get("clicks", 0))
                ok = self.scroll(clicks)
                return ok, f"Scrolled {clicks} clicks"
            elif act == "drag":
                fx, fy = int(action.get("from_x", 0)), int(action.get("from_y", 0))
                tx, ty = int(action.get("to_x", 0)), int(action.get("to_y", 0))
                ok = self.drag(fx, fy, tx, ty)
                return ok, f"Dragged from ({fx},{fy}) to ({tx},{ty})"
            elif act == "wait":
                secs = float(action.get("seconds", 1.0))
                time.sleep(secs)
                return True, f"Waited {secs}s"
            elif act == "open_url":
                url = str(action.get("url", ""))
                ok = open_url(url)
                return ok, f"Opened URL: {url}"
            elif act == "notify":
                title = str(action.get("title", "Orion"))
                body = str(action.get("body", ""))
                ok = show_os_notification(title, body)
                return ok, f"Showed notification: {title}"
            else:
                return False, f"Unknown action: {act}"
        except Exception as exc:
            return False, f"Action {act} error: {exc}"

    def execute_workflow(
        self,
        steps: list[dict[str, Any]],
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Execute an automated desktop workflow step by step."""
        self._abort_flag = False
        results: list[dict[str, Any]] = []
        total = len(steps)
        started_at = time.time()

        for idx, step in enumerate(steps):
            if self._abort_flag:
                log.info("Workflow aborted by user/failsafe at step %d", idx)
                return {
                    "completed": False,
                    "aborted": True,
                    "executed_steps": idx,
                    "total_steps": total,
                    "duration_s": round(time.time() - started_at, 2),
                    "results": results,
                }

            success, detail = self.execute_single_action(step)
            step_res = {
                "step_index": idx,
                "action": step.get("action", "unknown"),
                "success": success,
                "detail": detail,
            }
            results.append(step_res)

            if on_progress:
                try:
                    on_progress({
                        "event": "WORKFLOW_STEP_COMPLETE",
                        "step_index": idx,
                        "total_steps": total,
                        "step": step_res,
                    })
                except Exception as exc:
                    log.warning("Progress callback error: %s", exc)

            # Brief pause between steps for OS UI stability
            delay = float(step.get("delay_after", 0.15))
            time.sleep(delay)

        return {
            "completed": all(r["success"] for r in results),
            "aborted": False,
            "executed_steps": len(results),
            "total_steps": total,
            "duration_s": round(time.time() - started_at, 2),
            "results": results,
        }

    async def execute_workflow_async(
        self,
        steps: list[dict[str, Any]],
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Execute workflow in a background thread to keep async event loop responsive."""
        return await asyncio.to_thread(self.execute_workflow, steps, on_progress)


# Global singleton instance
desktop_automation = DesktopAutomationEngine()
