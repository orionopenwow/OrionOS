""""Next step" cards with a screenshot, for your phone (release plan, Step 4c).

A card is one instruction: a sentence, a cropped screenshot of the exact spot with a ring around what to click,
and two buttons: **Orion does it** (Orion clicks it; needs your fingerprint on the phone) or **I did it**.

Privacy rules, enforced here before any picture leaves the lab:
- If the screen looks like a sign-in, password, 2FA, payment or recovery screen, no picture is sent at all, only
  the sentence ("Screenshot withheld: sign-in screen").
- Anything on screen that looks like a key, token or password (the same filter Orion uses for its memory) is
  blurred.
- Pictures are deleted after 24 hours.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

KEEP_S = 24 * 3600
PAD = 90
MAX_W = 1080
SENSITIVE_RE = re.compile(
    r"pass(word|code|phrase)|sign[ -]?in|log[ -]?in|2fa|two[- ]factor|verification code|one[- ]time code|"
    r"authenticat|security code|recovery (key|code)|card number|cvv|cvc|expiry|bank|routing number|"
    r"social security|seed phrase|private key", re.I)

Grab = Callable[[], Any]           # -> PIL.Image or None
Title = Callable[[], Optional[str]]
Click = Callable[[int, int], None]


def _default_grab():
    import linux_desktop
    return linux_desktop.grab_screen()


def _default_title() -> Optional[str]:
    import linux_desktop
    return linux_desktop.active_window_title()


def _default_click(x: int, y: int) -> None:
    import linux_desktop
    linux_desktop.SwayAutoGUI().click(x, y)


def _ocr_words(img) -> list[dict[str, Any]]:
    try:
        import pytesseract
        d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    out = []
    for i, w in enumerate(d.get("text", [])):
        if w and w.strip():
            out.append({"text": w.strip(), "x": d["left"][i], "y": d["top"][i], "w": d["width"][i], "h": d["height"][i]})
    return out


class GuideCards:
    def __init__(self, data_dir: Path, grab: Grab = _default_grab, title: Title = _default_title,
                 click: Click = _default_click, ocr: Callable[[Any], list[dict[str, Any]]] = _ocr_words,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(data_dir) / "cards"
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.grab, self.title, self.click, self.ocr, self.clock = grab, title, click, ocr, clock

    # ------------------------------------------------------------------ making a card
    def create(self, text: str, region: Optional[tuple[int, int, int, int]] = None,
               target: Optional[tuple[int, int]] = None) -> dict[str, Any]:
        """region = (x, y, w, h) to show; target = (x, y) to ring and, if you allow it, click."""
        from harvester import looks_secret

        self.prune()
        cid = uuid.uuid4().hex[:16]
        meta: dict[str, Any] = {"id": cid, "ts": self.clock(), "text": text[:300], "state": "open",
                                "target": list(target) if target else None, "image": False, "withheld": None}
        img = None
        try:
            img = self.grab()
        except Exception:
            img = None
        if img is not None:
            words = self.ocr(img)
            screen_text = " ".join([self.title() or ""] + [w["text"] for w in words])
            if SENSITIVE_RE.search(screen_text):
                meta["withheld"] = "This looks like a sign-in or payment screen, so no picture was sent."
            else:
                try:   # Toolkit: Share Scrubber also blurs emails, IP addresses, home folders and server names
                    boxes = __import__("share_scrubber").default().sensitive_boxes(words)
                except Exception:
                    boxes = []
                boxes += [w for w in words if looks_secret(w["text"]) and w not in boxes]
                img = self._blur_secrets(img, boxes)
                img = self._frame(img, region, target)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                (self.dir / f"{cid}.jpg").write_bytes(buf.getvalue())
                os.chmod(self.dir / f"{cid}.jpg", 0o600)
                meta["image"] = True
        self._save(meta)
        return meta

    @staticmethod
    def _blur_secrets(img, boxes: list[dict[str, Any]]):
        if not boxes:
            return img
        from PIL import ImageFilter
        img = img.copy()
        for b in boxes:
            box = (max(0, b["x"] - 4), max(0, b["y"] - 4), b["x"] + b["w"] + 4, b["y"] + b["h"] + 4)
            img.paste(img.crop(box).filter(ImageFilter.GaussianBlur(12)), box[:2])
        return img

    @staticmethod
    def _frame(img, region, target):
        from PIL import ImageDraw
        W, H = img.size
        if region:
            x, y, w, h = region
        elif target:
            x, y, w, h = target[0] - 240, target[1] - 160, 480, 320
        else:
            x, y, w, h = 0, 0, W, H
        box = (max(0, x - PAD), max(0, y - PAD), min(W, x + w + PAD), min(H, y + h + PAD))
        img = img.convert("RGB")
        if target:
            d = ImageDraw.Draw(img)
            r = 34
            for i, col in enumerate(((0, 229, 255), (124, 92, 255))):
                rr = r + i * 8
                d.ellipse((target[0] - rr, target[1] - rr, target[0] + rr, target[1] + rr), outline=col, width=6)
        img = img.crop(box)
        if img.width > MAX_W:
            img = img.resize((MAX_W, round(img.height * MAX_W / img.width)))
        return img

    # ------------------------------------------------------------------ storage
    def _path(self, cid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", cid or ""):
            raise KeyError(cid)
        return self.dir / f"{cid}.json"

    def _save(self, meta: dict[str, Any]) -> None:
        p = self._path(meta["id"])
        p.write_text(json.dumps(meta))
        os.chmod(p, 0o600)

    def get(self, cid: str) -> dict[str, Any]:
        p = self._path(cid)
        if not p.exists():
            raise KeyError(cid)
        return json.loads(p.read_text())

    def image(self, cid: str) -> Optional[bytes]:
        self._path(cid)
        p = self.dir / f"{cid}.jpg"
        if not p.exists() or self.clock() - p.stat().st_mtime > KEEP_S:
            return None
        return p.read_bytes()

    def open_cards(self) -> list[dict[str, Any]]:
        self.prune()
        out = []
        for p in sorted(self.dir.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
            m = json.loads(p.read_text())
            if m.get("state") == "open":
                out.append(m)
        return out[:20]

    def prune(self) -> None:
        now = self.clock()
        for p in self.dir.iterdir():
            try:
                if p.suffix == ".jpg" and now - p.stat().st_mtime > KEEP_S:
                    p.unlink()
                elif p.suffix == ".json":
                    m = json.loads(p.read_text())
                    if now - m.get("ts", 0) > KEEP_S:
                        p.unlink()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ your answer from the phone
    def done(self, cid: str) -> dict[str, Any]:
        m = self.get(cid)
        m["state"] = "done"
        self._save(m)
        return m

    def do(self, cid: str) -> dict[str, Any]:
        """Orion clicks the card's target. Only reachable with the phone's fingerprint key."""
        m = self.get(cid)
        if m.get("state") != "open":
            raise ValueError("this step is already finished")
        if not m.get("target"):
            raise ValueError("this step has nothing for Orion to click; do it yourself and tap I did it")
        self.click(int(m["target"][0]), int(m["target"][1]))
        m["state"] = "done-by-orion"
        self._save(m)
        return m
