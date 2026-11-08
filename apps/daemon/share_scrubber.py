"""Share Scrubber (toolkit tool 23): share without leaking.

Before a screenshot, log, crash report or snippet leaves the machine, this takes out what shouldn't go with it:
  * secrets: Secret Shield's patterns (secret_shield_rules.json) plus Orion's general secret filter
    (mcp_tools.redact, which ends with harvester.looks_secret)
  * email addresses
  * home folders: /home/<user>/..., /Users/<user>/... and C:\\Users\\<user>\\... become ~/...
  * IP addresses (127.x and ::1 stay: they only mean "this computer")
  * your server names: every Host alias and HostName in ~/.ssh/config
In a picture the same words are found with Tesseract (OCR) and blurred with the guide-card blur.

Where it runs by itself (Orion's own sharing paths):
  * guide cards for the phone (guide_cards.py blurs with word_is_sensitive)
  * crash reports sent to a model (crash_healer.redact)
  * anything put in the approval queue: attachments, snippets, logs and crash reports are fully scrubbed; message
    and post text has secrets, home folders and server names taken out (approval_queue.add)
And by hand: "Share clean" (Super+Shift+C, or the button on a screenshot's notification) runs
`orion-share-scrub`, which cleans the clipboard (text or picture) or one file.

Command line (used by os/lab/bin/orion-share-scrub):
  share_scrubber.py clipboard        clean the clipboard in place (wl-paste / wl-copy)
  share_scrubber.py file PATH        write PATH-clean.<ext> next to it and copy it to the clipboard
  share_scrubber.py text             read stdin, print the clean text
"""

import io
import ipaddress
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

SECRET = "[redacted]"
EMAIL = "[email]"
IP = "[ip]"
SERVER = "[server]"
MAX_TEXT = 2_000_000

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\b")
HOME_RE = re.compile(r"(?<![\w.~-])(?:/home/|/Users/)(?!\[)[^/\s\"'`:;,)]+|[A-Za-z]:\\+Users\\+(?!Public\b)[^\\\s\"'`:;,)]+",
                     re.I)
IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
IPV6_RE = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
# A word right after one of these is a value (a password typed after "password:" is a secret even if it's short).
KEY_WORD_RE = re.compile(r"(?i)(pass(word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|key)[:=]$")


@dataclass
class Counts:
    secrets: int = 0
    emails: int = 0
    paths: int = 0
    ips: int = 0
    servers: int = 0

    def total(self) -> int:
        return self.secrets + self.emails + self.paths + self.ips + self.servers

    def words(self) -> str:
        bits = [(self.secrets, "secret"), (self.emails, "email address"), (self.paths, "home folder"),
                (self.ips, "IP address"), (self.servers, "server name")]
        said = [f"{n} {w}{'es' if n != 1 and w.endswith('ss') else 's' if n != 1 else ''}" for n, w in bits if n]
        if not said:
            return "Nothing private found."
        return "Took out " + (", ".join(said[:-1]) + " and " + said[-1] if len(said) > 1 else said[0]) + "."

    def as_dict(self) -> dict[str, int]:
        return {"secrets": self.secrets, "emails": self.emails, "paths": self.paths, "ips": self.ips,
                "servers": self.servers, "total": self.total()}


def ssh_hosts(path: Optional[Path] = None) -> list[str]:
    """Server names from ~/.ssh/config: Host aliases (not wildcards) and HostName values."""
    path = path or Path.home() / ".ssh" / "config"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: set[str] = set()
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or parts[0].startswith("#"):
            continue
        key, val = parts[0].lower(), parts[1].split("#", 1)[0]
        if key == "host":
            out.update(v for v in val.split() if not any(c in v for c in "*?!"))
        elif key == "hostname":
            out.add(val.strip())
    keep = set()
    for h in out:
        h = h.strip()
        if len(h) < 3 or h.lower() in ("localhost", "github.com", "gitlab.com", "bitbucket.org"):
            continue
        try:
            ipaddress.ip_address(h)
            continue          # addresses are caught as IPs
        except ValueError:
            keep.add(h)
    return sorted(keep, key=len, reverse=True)


def _is_loopback(s: str) -> bool:
    try:
        a = ipaddress.ip_address(s)
    except ValueError:
        return False
    return a.is_loopback or a.is_unspecified


def _valid_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


@dataclass
class Scrubber:
    hosts: Optional[list[str]] = None
    ssh_config: Optional[Path] = None
    extra_hosts: list[str] = field(default_factory=list)

    def server_names(self) -> list[str]:
        if self.hosts is None:
            self.hosts = ssh_hosts(self.ssh_config)
        return sorted(set(self.hosts) | set(self.extra_hosts), key=len, reverse=True)

    # ------------------------------------------------------------------ text
    def scrub_text(self, text: str, *, secrets: bool = True, emails: bool = True, paths: bool = True,
                   ips: bool = True, servers: bool = True, loose: bool = True,
                   counts: Optional[Counts] = None) -> str:
        """loose=False uses only Secret Shield's exact patterns (for messages you wrote: no false alarms on
        ordinary words like "passwords?")."""
        c = counts if counts is not None else Counts()
        text = str(text or "")[:MAX_TEXT]
        if not text:
            return text
        if secrets:
            text = self._secrets(text, c, loose)
        if servers:
            for h in self.server_names():
                rx = re.compile(r"(?<![\w.-])" + re.escape(h) + r"(?![\w-]|\.[A-Za-z0-9])", re.I)
                text, n = rx.subn(SERVER, text)
                c.servers += n
        if emails:
            text, n = EMAIL_RE.subn(EMAIL, text)
            c.emails += n
        if paths:
            def home(m: re.Match) -> str:
                c.paths += 1
                return "%USERPROFILE%" if "\\" in m.group(0) else "~"
            text = HOME_RE.sub(home, text)
        if ips:
            def ip(m: re.Match) -> str:
                s = m.group(0)
                if not _valid_ip(s) or _is_loopback(s):
                    return s
                if s.count(":") and (("::" not in s and s.count(":") < 7) or not any(ch.isdigit() for ch in s)):
                    return s      # times like 12:30:45 and code like a::b aren't addresses
                c.ips += 1
                return IP
            text = IPV4_RE.sub(ip, text)
            text = IPV6_RE.sub(ip, text)
        return text

    @staticmethod
    def _secrets(text: str, c: Counts, loose: bool = True) -> str:
        try:
            import secret_shield
            rules = secret_shield.load_rules()
            lines = text.split("\n")
            for i, line in enumerate(lines):
                spans = []
                for rule in rules.rules:
                    for m in rule.regex.finditer(line[:20000]):
                        g = rule.group if rule.group <= (m.re.groups or 0) else 0
                        val = m.group(g)
                        if not val or secret_shield._placeholder(val, rules):
                            continue
                        if rule.entropy and secret_shield.shannon(val) < rule.entropy:
                            continue
                        if rule.needs_digit_and_letter and not (re.search(r"\d", val) and re.search("[A-Za-z]", val)):
                            continue
                        sp = m.span(g)
                        if not any(a < sp[1] and sp[0] < b for a, b in spans):
                            spans.append(sp)
                if spans:
                    c.secrets += len(spans)
                    lines[i] = secret_shield.redact_spans(line, spans)
            text = "\n".join(lines)
        except Exception:
            pass
        if not loose:
            return text
        try:
            from mcp_tools import redact
            before = text.count(SECRET)
            text = redact(text)
            c.secrets += max(0, text.count(SECRET) - before)
        except Exception:
            pass
        return text

    def report(self, text: str, **kw: Any) -> tuple[str, Counts]:
        c = Counts()
        return self.scrub_text(text, counts=c, **kw), c

    # ------------------------------------------------------------------ pictures
    def word_is_sensitive(self, word: str, prev: str = "") -> bool:
        w = (word or "").strip().strip("()[]{}<>,;\"'")
        if not w:
            return False
        if KEY_WORD_RE.search(prev.strip()) and len(w) >= 4:
            return True
        return self.scrub_text(w) != w

    def sensitive_boxes(self, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out, prev = [], ""
        for w in words:
            if self.word_is_sensitive(w.get("text", ""), prev):
                out.append(w)
            prev = w.get("text", "")
        return out

    def scrub_image(self, img: Any, ocr: Optional[Callable[[Any], list[dict[str, Any]]]] = None) -> tuple[Any, int]:
        """Blur every word OCR finds that would be taken out of text. Returns (picture, how many words)."""
        from guide_cards import GuideCards, _ocr_words
        words = (ocr or _ocr_words)(img)
        boxes = self.sensitive_boxes(words)
        return GuideCards._blur_secrets(img, boxes), len(boxes)

    def scrub_png_bytes(self, data: bytes, ocr: Optional[Callable[[Any], list[dict[str, Any]]]] = None
                        ) -> tuple[bytes, int]:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        out, n = self.scrub_image(img, ocr)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue(), n

    # ------------------------------------------------------------------ files and the clipboard
    def scrub_file(self, path: Path, ocr: Optional[Callable[[Any], list[dict[str, Any]]]] = None
                   ) -> tuple[Path, str]:
        path = Path(path)
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            data, n = self.scrub_png_bytes(path.read_bytes(), ocr)
            out = path.with_name(path.stem + "-clean.png")
            out.write_bytes(data)
            return out, (f"Blurred {n} private word{'s' if n != 1 else ''}." if n else "Nothing private found.")
        text, c = self.report(path.read_text(encoding="utf-8", errors="replace"))
        out = path.with_name(path.stem + "-clean" + (path.suffix or ".txt"))
        out.write_text(text, encoding="utf-8")
        return out, c.words()

    def clean_clipboard(self, run: Callable[..., Any] = subprocess.run,
                        ocr: Optional[Callable[[Any], list[dict[str, Any]]]] = None) -> str:
        """Clean what's on the clipboard, in place. Plain words back for the notification."""
        try:
            types = run(["wl-paste", "--list-types"], capture_output=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return "The clipboard can't be read here (wl-paste isn't available)."
        types = (types.decode() if isinstance(types, bytes) else str(types or "")).split()
        image = next((t for t in ("image/png", "image/jpeg", "image/webp", "image/bmp") if t in types), None)
        if image:
            raw = run(["wl-paste", "--type", image], capture_output=True, timeout=10).stdout
            if not raw:
                return "The clipboard is empty."
            data, n = self.scrub_png_bytes(raw if isinstance(raw, bytes) else raw.encode("latin-1"), ocr)
            run(["wl-copy", "--type", "image/png"], input=data, timeout=10)
            return (f"Picture cleaned: blurred {n} private word{'s' if n != 1 else ''}. Paste it anywhere."
                    if n else "Picture checked: nothing private found.")
        if not any(t.startswith("text/") or t in ("TEXT", "STRING", "UTF8_STRING") for t in types):
            return "The clipboard is empty." if not types else "Only text and pictures can be cleaned."
        raw = run(["wl-paste", "--no-newline"], capture_output=True, timeout=10).stdout
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")
        clean, c = self.report(text)
        if clean != text:
            run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=clean.encode("utf-8"), timeout=10)
            return c.words() + " The clean copy is on the clipboard."
        return "Text checked: nothing private found."


_DEFAULT: Optional[Scrubber] = None


def default() -> Scrubber:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Scrubber()
    return _DEFAULT


def scrub_text(text: str, **kw: Any) -> str:
    return default().scrub_text(text, **kw)


# Orion's own sharing paths: what the approval queue holds before anything is sent or posted.
FULL_FIELDS = ("attachments", "attachment", "snippet", "snippets", "log", "logs", "crash_report", "report", "trace")
MESSAGE_FIELDS = ("draft_reply", "body", "text", "message")


def _scrub_value(v: Any, full: bool) -> Any:
    kw = {} if full else {"emails": False, "ips": False, "loose": False}
    if isinstance(v, str):
        p = Path(v) if len(v) < 1024 and "\n" not in v else None
        try:
            if full and p is not None and p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file():
                out, _ = default().scrub_file(p)
                return str(out)
        except OSError:
            pass
        return default().scrub_text(v, **kw)
    if isinstance(v, list):
        return [_scrub_value(x, full) for x in v]
    if isinstance(v, dict):
        return {k: _scrub_value(x, full) for k, x in v.items()}
    return v


def scrub_outgoing(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Clean an approval-queue item before it is stored (and so before it can be sent)."""
    try:
        out = dict(fields)
        for k in FULL_FIELDS:
            if k in out:
                out[k] = _scrub_value(out[k], True)
        for k in MESSAGE_FIELDS:
            if isinstance(out.get(k), str):
                out[k] = _scrub_value(out[k], False)
        return out
    except Exception:
        return fields


# =====================================================================================  HTTP (local)
def make_router(get_scrubber: Callable[[], Scrubber]):
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse, Response

    router = APIRouter(prefix="/share", tags=["toolkit"])

    @router.post("/scrub")
    async def scrub(request: Request) -> Any:
        try:
            b = await request.json()
        except ValueError:
            b = {}
        text = str((b or {}).get("text") or "") if isinstance(b, dict) else ""
        clean, c = get_scrubber().report(text)
        return {"text": clean, "counts": c.as_dict(), "words": c.words()}

    @router.post("/scrub-image")
    async def scrub_image(request: Request) -> Any:
        import asyncio
        data = await request.body()
        if not data or len(data) > 25 * 1024 * 1024:
            return JSONResponse({"error": "send one picture (PNG or JPEG, up to 25 MB)"}, status_code=400)
        try:
            out, n = await asyncio.to_thread(get_scrubber().scrub_png_bytes, data)
        except Exception:
            return JSONResponse({"error": "that isn't a picture Orion can read"}, status_code=400)
        return Response(out, media_type="image/png", headers={"X-Orion-Blurred": str(n), "Cache-Control": "no-store"})

    @router.post("/clipboard")
    async def clipboard() -> Any:
        import asyncio
        return {"message": await asyncio.to_thread(get_scrubber().clean_clipboard)}

    return router


# =====================================================================================  command line
def _notify(title: str, body: str) -> None:
    try:
        subprocess.run(["notify-send", "-a", "Orion", title, body], timeout=5, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "clipboard"
    s = default()
    if cmd == "clipboard":
        msg = s.clean_clipboard()
        _notify("Share clean", msg)
        print(msg)
        return 0
    if cmd == "file" and len(argv) > 1:
        try:
            out, msg = s.scrub_file(Path(argv[1]))
        except (OSError, ValueError) as exc:
            print(f"Couldn't clean {argv[1]}: {exc}")
            return 1
        try:
            mime = "image/png" if out.suffix == ".png" else "text/plain;charset=utf-8"
            with out.open("rb") as fh:
                subprocess.run(["wl-copy", "--type", mime], stdin=fh, timeout=10, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _notify("Share clean", f"{msg} Saved {out.name} and copied it.")
        print(out)
        return 0
    if cmd == "text":
        sys.stdout.write(s.scrub_text(sys.stdin.read()))
        return 0
    print(__doc__.split("Command line", 1)[-1])
    return 2


if __name__ == "__main__":
    sys.exit(main())
