"""Skill Recorder (after-1.0 plan §1, browser part): raw browser events -> a readable, signed skill.

Pipeline
  1. The Chrome extension (apps/extension/recorder.js) records DOM actions with several selectors per element
     and sends them as raw events. Private fields never leave the page; this module filters again anyway.
  2. `build_draft()` turns raw events into numbered steps in plain words, spots values that will change
     (file names, versions, dates, search terms) and turns them into variables the user can rename.
  3. `SkillStore` saves the confirmed skill as versioned JSON, signed with an HMAC key kept in the daemon data
     dir, so a skill edited outside Orion is detected and refused.
  4. Replay lives in skill_replay.py; every step that sends, buys, deletes, posts or submits a form is marked
     `risky` here and always waits for the user's OK there.

Also here: Playwright export/import and simple repeated-task spotting.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

try:
    from harvester import looks_secret
except Exception:  # pragma: no cover - harvester pulls optional deps in some builds
    def looks_secret(text: str) -> bool:
        return False

SCHEMA = 1
# automation_id and ocr are desktop selectors (desktop_skill.py); the browser export skips kinds it doesn't know
SELECTOR_ORDER = ("testid", "automation_id", "role", "label", "text", "placeholder", "css", "ocr", "coords")

# ---------------------------------------------------------------- privacy rules
SENSITIVE_PAGE = re.compile(
    r"(log[ -_]?in|sign[ -_]?in|signin|password|passwd|2fa|two[- ]factor|mfa|otp|verification|"
    r"1password|bitwarden|lastpass|keepass|dashlane|vault\b|bank|banking|credential|/auth\b|oauth|sso)", re.I)
CARD_HINT = re.compile(r"(card.?num|cc.?num|credit.?card|cvv|cvc|csc|security.?code|expir|exp.?date)", re.I)
CODE_HINT = re.compile(r"(code|otp|2fa|mfa|totp|token|verification|one.?time|pin\b|passcode)", re.I)
PASSWORD_HINT = re.compile(r"(passw|pwd\b|passphrase|secret)", re.I)


def sensitive_field_reason(target: dict[str, Any]) -> Optional[str]:
    """Why a field must never be recorded (password / card / one-time code), else None."""
    if not target:
        return None
    ftype = str(target.get("type") or "").lower()
    auto = str(target.get("autocomplete") or "").lower()
    hints = " ".join(str(target.get(k) or "") for k in ("name", "label", "field_name", "field_id", "placeholder"))
    if ftype == "password" or "password" in auto or PASSWORD_HINT.search(hints):
        return "password"
    if auto.startswith("cc-") or " cc-" in auto or CARD_HINT.search(hints):
        return "card"
    if "one-time-code" in auto:
        return "code"
    inputmode = str(target.get("inputmode") or "").lower()
    try:
        maxlen = int(target.get("maxlength") or 0)
    except (TypeError, ValueError):
        maxlen = 0
    numeric = inputmode == "numeric" or ftype in ("tel", "number")
    if numeric and 0 < maxlen <= 8 and CODE_HINT.search(hints + " " + str(target.get("nearby") or "")):
        return "code"
    if CODE_HINT.search(str(target.get("field_name") or "") + " " + str(target.get("field_id") or "")) and \
            re.search(r"(otp|2fa|mfa|totp|one.?time)", hints, re.I):
        return "code"
    return None


PRIVATE_PARAM = re.compile(r"(^pw$|^pin$|pass|pwd|card|cc.?num|cvv|cvc|csc|otp|2fa|mfa|token|secret|one.?time|auth|session|code)", re.I)


def scrub_url(url: str) -> str:
    """Blank query/fragment parameters that look private (a GET form puts field values into the URL)."""
    return re.sub(r"([?&#])([^=&#\s]+)=([^&#\s]*)",
                  lambda m: f"{m.group(1)}{m.group(2)}=" if PRIVATE_PARAM.search(m.group(2)) else m.group(0), url)


def page_looks_sensitive(url: str = "", title: str = "") -> bool:
    """Judged on host + path + title (not the query string, which may just carry a scrubbed parameter)."""
    try:
        p = urlsplit(url or "")
        where = f"{p.netloc}{p.path}" if p.scheme else (url or "")
    except ValueError:
        where = url or ""
    return bool(SENSITIVE_PAGE.search(f"{where} {title}"))


# ---------------------------------------------------------------- risk rules
RISKY_WORDS = re.compile(
    r"\b(send|buy|pay|purchase|checkout|check out|place order|order now|delete|remove|erase|post|publish|"
    r"submit|confirm|transfer|tweet|unsubscribe)\b", re.I)


def classify_risk(step: dict[str, Any]) -> Optional[str]:
    """Plain-words reason this step needs the user's OK every time, or None."""
    if step.get("surface") == "desktop":  # desktop skills have their own rules (desktop_skill.py)
        from desktop_skill import classify_risk as desktop_risk
        return desktop_risk(step)
    action = step.get("action")
    t = step.get("target") or {}
    if action == "submit" or step.get("submits"):
        return "submits a form"
    if action == "press" and str(step.get("key", "")).lower() == "enter" and t.get("in_form"):
        return "submits a form"
    if action == "click":
        ftype = str(t.get("type") or "").lower()
        tag = str(t.get("tag") or "").lower()
        if tag == "input" and ftype in ("submit", "image"):
            return "submits a form"
        if tag == "button" and t.get("in_form") and ftype in ("", "submit"):
            return "submits a form"
    words = " ".join(str(t.get(k) or "") for k in ("name", "text")) if action in ("click", "press", "submit") else ""
    m = RISKY_WORDS.search(words)
    if m:
        return f"looks like it will {m.group(1).lower()}"
    return None


# ---------------------------------------------------------------- selectors
def order_selectors(selectors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Most robust first (test id, role + name, label, visible text, placeholder, CSS path); coordinates last.
    Stable within a kind, unknown kinds before coordinates, duplicates dropped."""
    seen: set[str] = set()
    out = []
    for s in selectors or []:
        if not isinstance(s, dict) or not s.get("kind"):
            continue
        key = json.dumps(s, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    rank = {k: i for i, k in enumerate(SELECTOR_ORDER)}
    return sorted(out, key=lambda s: rank.get(s["kind"], len(SELECTOR_ORDER) - 1.5))


def target_label(t: dict[str, Any]) -> str:
    for k in ("name", "label", "text", "placeholder"):
        v = re.sub(r"\s+", " ", str((t or {}).get(k) or "")).strip()
        if v:
            return v[:60]
    return ""


def _target_key(t: dict[str, Any]) -> str:
    sels = order_selectors((t or {}).get("selectors") or [])
    for s in sels:
        if s["kind"] != "coords":
            return json.dumps(s, sort_keys=True)
    return target_label(t)


# ---------------------------------------------------------------- events -> steps
TEXT_TYPES = {"", "text", "search", "email", "url", "tel", "number", "date", "datetime-local", "month", "week",
              "time", "color", "range"}
KEEP_TARGET = ("selectors", "role", "name", "text", "tag", "type", "label", "placeholder", "in_form", "href",
               "testid")


def _clean_target(t: dict[str, Any]) -> dict[str, Any]:
    out = {k: t[k] for k in KEEP_TARGET if k in t and t[k] not in (None, "")}
    out["selectors"] = order_selectors(t.get("selectors") or [])
    return out


def _is_text_field(t: dict[str, Any]) -> bool:
    tag = str(t.get("tag") or "").lower()
    return (tag == "input" and str(t.get("type") or "").lower() in TEXT_TYPES) or tag == "textarea" \
        or bool(t.get("editable")) or tag == "select"


def normalize_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Raw extension events -> clean steps. Returns (steps, number of private things skipped)."""
    steps: list[dict[str, Any]] = []
    skipped = 0
    last_t = 0.0
    for ev in sorted((e for e in events if isinstance(e, dict)), key=lambda e: float(e.get("t") or 0)):
        action = ev.get("action")
        t = float(ev.get("t") or 0)
        target = ev.get("target") or {}
        if ev.get("sensitive") or ev.get("paused") or sensitive_field_reason(target) or \
                (action != "navigate" and page_looks_sensitive(ev.get("page_url", ""), ev.get("page_title", ""))):
            skipped += 1
            continue
        prev = steps[-1] if steps else None
        recent = prev is not None and (t - last_t) <= 6000
        step: Optional[dict[str, Any]] = None
        if action == "navigate":
            url = scrub_url(str(ev.get("url") or ""))
            if not url.startswith(("http://", "https://")) or page_looks_sensitive(url, ev.get("title", "")):
                if url:
                    skipped += 1
                continue
            if prev and prev["action"] in ("click", "press", "submit") and recent:
                step = {"action": "wait_load"}
            elif prev and prev["action"] == "wait_load" and recent:
                last_t = t
                continue  # redirect chain after one action
            elif prev and prev["action"] == "open" and prev.get("url") == url:
                continue
            else:
                step = {"action": "open", "url": url}
        elif action == "fill":
            value = ev.get("value")
            ask = False
            if isinstance(value, str) and looks_secret(value):
                value, ask = None, True
            key = _target_key(target)
            if prev and prev["action"] in ("fill", "click") and _target_key(prev.get("target") or {}) == key:
                steps.pop()  # keep the final value only; a click into a text box is just focus
            step = {"action": "fill", "target": _clean_target(target), "value": value}
            if ask:
                step["ask"] = True
        elif action == "click":
            if _is_text_field(target) or str(target.get("type") or "").lower() in ("checkbox", "radio", "file") \
                    or target.get("label_for_control"):
                continue  # focus clicks / toggles are recorded by their change event
            if prev and prev["action"] == "click" and _target_key(prev.get("target") or {}) == _target_key(target) \
                    and (t - last_t) < 400:
                continue  # double click
            step = {"action": "click", "target": _clean_target(target)}
        elif action == "submit":
            if prev and prev["action"] in ("click", "press") and (t - last_t) <= 2000:
                prev["submits"] = True
                last_t = t
                continue
            step = {"action": "submit", "target": _clean_target(target)}
        elif action == "press":
            if str(ev.get("key")) != "Enter":
                continue
            step = {"action": "press", "key": "Enter", "target": _clean_target(target)}
        elif action == "select":
            step = {"action": "select", "target": _clean_target(target), "value": ev.get("value"),
                    "option_text": ev.get("option_text")}
            if prev and prev["action"] == "select" and _target_key(prev.get("target") or {}) == _target_key(target):
                steps.pop()
        elif action == "check":
            step = {"action": "check", "target": _clean_target(target), "checked": bool(ev.get("checked", True))}
        elif action == "upload":
            names = [os.path.basename(str(n).replace("\\", "/")) for n in (ev.get("files") or []) if n]
            step = {"action": "upload", "target": _clean_target(target), "files": names[:10]}
        elif action == "download":
            step = {"action": "download", "filename": os.path.basename(str(ev.get("filename") or "").replace("\\", "/"))}
        elif action == "wait_text":
            text = re.sub(r"\s+", " ", str(ev.get("text") or "")).strip()[:120]
            if not text:
                continue
            step = {"action": "wait_text", "text": text}
        elif action == "wait_load":
            if prev and prev["action"] == "wait_load":
                continue
            step = {"action": "wait_load"}
        else:
            continue
        last_t = t
        steps.append(step)
    for i, s in enumerate(steps, 1):
        s["id"] = f"s{i}"
        reason = classify_risk(s)
        s["risky"] = bool(reason)
        if reason:
            s["risk_reason"] = reason
    return steps, skipped


# ---------------------------------------------------------------- plain words
ROLE_WORD = {"button": "button", "link": "link", "tab": "tab", "menuitem": "menu item", "checkbox": "box",
             "radio": "option", "option": "option", "textbox": "box", "combobox": "list", "searchbox": "search box"}


def short_url(url: str) -> str:
    try:
        p = urlsplit(url)
        path = p.path.rstrip("/")
        out = p.netloc.removeprefix("www.") + path
        return out[:80] + ("…" if len(out) > 80 else "")
    except ValueError:
        return url[:80]


def _shown(value: Any) -> str:
    v = str(value if value is not None else "")
    if re.fullmatch(r"\{\{\w+\}\}", v):
        return v  # a variable: "{{version}}"
    return f"“{v[:50]}”"


def describe_step(step: dict[str, Any]) -> str:
    if step.get("surface") == "desktop":
        from desktop_skill import describe_step as desktop_describe
        return desktop_describe(step)
    a = step.get("action")
    t = step.get("target") or {}
    name = target_label(t)
    what = f"“{name}”" if name else "the highlighted spot"
    role = ROLE_WORD.get(str(t.get("role") or ""), "")
    if a == "open":
        return f"Go to {short_url(str(step.get('url', '')))}"
    if a == "click":
        return f"Click the {what} {role}".rstrip() if name else "Click the highlighted spot"
    if a == "fill":
        if step.get("ask"):
            return f"Type the private value into {what} (Orion asks you each time)"
        return f"Type {_shown(step.get('value'))} into {what}"
    if a == "select":
        return f"Choose {_shown(step.get('option_text') or step.get('value'))} in {what}"
    if a == "check":
        if str(t.get("type")) == "radio":
            return f"Pick {what}"
        return f"{'Tick' if step.get('checked', True) else 'Untick'} {what}"
    if a == "upload":
        files = ", ".join(step.get("files") or []) or "a file"
        return f"Pick the file {files} for {what} (you do this one)"
    if a == "press":
        return f"Press Enter in {what}"
    if a == "submit":
        return "Submit the form"
    if a == "download":
        return f"Wait for the download {step.get('filename') or ''} to start".replace("  ", " ")
    if a == "wait_load":
        return "Wait until the page has loaded"
    if a == "wait_text":
        return f"Wait until “{step.get('text', '')}” appears"
    return str(a)


def card_lines(skill: dict[str, Any]) -> list[str]:
    lines = []
    for i, s in enumerate(skill.get("steps") or [], 1):
        line = f"{i}. {describe_step(s)}"
        if s.get("risky"):
            line += f" — needs your OK ({s.get('risk_reason', 'risky')})"
        lines.append(line)
    return lines


# ---------------------------------------------------------------- variables
VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.\-]+)?$")
DATE_RE = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|"
    r"\d{1,2} (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{2,4}|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2},? \d{2,4})$", re.I)
FILE_RE = re.compile(r"^[^/\\:*?\"<>|\s][^/\\:*?\"<>|]*\.[A-Za-z0-9]{1,6}$")
SEARCH_HINT = re.compile(r"(search|query|find|lookup|filter|\bq\b)", re.I)
SEARCH_PARAMS = ("q", "query", "search", "s", "k", "term", "keywords", "search_query")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def variable_kind(value: str, target: Optional[dict[str, Any]] = None) -> Optional[str]:
    v = (value or "").strip()
    if not v or len(v) > 200:
        return None
    if VERSION_RE.match(v):
        return "version"
    if DATE_RE.match(v):
        return "date"
    if FILE_RE.match(v) and "@" not in v and not v.startswith(("http:", "https:")):
        return "file"
    t = target or {}
    hint = " ".join(str(t.get(k) or "") for k in ("role", "type", "name", "label", "placeholder"))
    if t.get("role") == "searchbox" or str(t.get("type")) == "search" or SEARCH_HINT.search(hint):
        return "search"
    return None


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:24].strip("_")
    if not s or s[0].isdigit():
        s = fallback
    return s


def detect_variables(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Find values that will change next time and replace them with {{name}} placeholders.
    Returns (new steps, variables)."""
    steps = json.loads(json.dumps(steps))
    variables: list[dict[str, Any]] = []
    by_value: dict[str, str] = {}

    def add(value: str, kind: str, label: str, step_id: str) -> str:
        if value in by_value:
            name = by_value[value]
            var = next(v for v in variables if v["name"] == name)
            if step_id not in var["used_in"]:
                var["used_in"].append(step_id)
            return name
        base = _slug(label, kind) if kind == "search" and label else kind
        name, n = base, 2
        while any(v["name"] == name for v in variables):
            name, n = f"{base}_{n}", n + 1
        variables.append({"name": name, "label": (label or kind.title())[:60], "kind": kind, "example": value,
                          "used_in": [step_id]})
        by_value[value] = name
        return name

    for s in steps:
        t = s.get("target") or {}
        if s["action"] in ("fill", "select") and isinstance(s.get("value"), str) and not s.get("ask"):
            kind = variable_kind(s["value"], t if s["action"] == "fill" else {})
            if kind:
                name = add(s["value"], kind, target_label(t), s["id"])
                s["value"] = "{{" + name + "}}"
                if s["action"] == "select":
                    s.pop("option_text", None)
        elif s["action"] == "upload" and s.get("files"):
            names = []
            for f in s["files"]:
                names.append("{{" + add(f, "file", target_label(t) or "File", s["id"]) + "}}")
            s["files"] = names
        elif s["action"] == "open":
            try:
                params = parse_qsl(urlsplit(s["url"]).query)
            except ValueError:
                params = []
            for k, v in params:
                if k.lower() in SEARCH_PARAMS and v and v not in by_value:
                    add(v, "search", "search", s["id"])
    # second pass: reuse known values wherever they reappear (URLs, waits, download names)
    for s in steps:
        for value, name in sorted(by_value.items(), key=lambda kv: -len(kv[0])):
            ph = "{{" + name + "}}"
            hit = False
            if s["action"] == "open":
                url = s["url"]
                for enc in {quote_plus(value), quote(value), value}:
                    if enc and enc in url:
                        url = url.replace(enc, ph)
                        hit = True
                s["url"] = url
            elif s["action"] == "wait_text" and value in s.get("text", ""):
                s["text"] = s["text"].replace(value, ph)
                hit = True
            elif s["action"] == "download" and value in s.get("filename", ""):
                s["filename"] = s["filename"].replace(value, ph)
                hit = True
            if hit:
                var = next(v for v in variables if v["name"] == name)
                if s["id"] not in var["used_in"]:
                    var["used_in"].append(s["id"])
    return steps, variables


def _map_strings(obj: Any, fn) -> Any:
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_map_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in ("target",) else _map_strings(v, fn)) for k, v in obj.items()}
    return obj


def apply_variable_edits(skill: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    """User confirmation of variables: rename ({name, new_name}), relabel ({label}) or keep the recorded value
    fixed ({keep_fixed: true})."""
    skill = json.loads(json.dumps(skill))
    variables = {v["name"]: v for v in skill.get("variables") or []}
    for e in edits or []:
        name = str(e.get("name") or "")
        var = variables.get(name)
        if not var:
            continue
        if e.get("keep_fixed"):
            example = str(var.get("example", ""))
            skill["steps"] = [_replace_placeholder(s, name, example) for s in skill["steps"]]
            del variables[name]
            continue
        if e.get("label"):
            var["label"] = str(e["label"])[:60]
        new = _slug(str(e.get("new_name") or ""), "")
        if new and new != name and new not in variables:
            skill["steps"] = [_replace_placeholder(s, name, "{{" + new + "}}") for s in skill["steps"]]
            var["name"] = new
            variables = {(new if k == name else k): v for k, v in variables.items()}
    skill["variables"] = list(variables.values())
    return skill


def _replace_placeholder(step: dict[str, Any], name: str, value: str) -> dict[str, Any]:
    ph = "{{" + name + "}}"

    def fn(s: str) -> str:
        if ph not in s:
            return s
        return s.replace(ph, value)
    return _map_strings(step, fn)


def render_step(step: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """Fill {{placeholders}} with this run's values (URL parts are URL-encoded)."""
    def sub(s: str, encode: bool) -> str:
        return PLACEHOLDER.sub(lambda m: (quote_plus if encode else str)(str(values.get(m.group(1), m.group(0)))), s)
    out = json.loads(json.dumps(step))
    for k in ("value", "text", "filename", "option_text"):
        if isinstance(out.get(k), str):
            out[k] = sub(out[k], False)
    if isinstance(out.get("url"), str):
        out["url"] = sub(out["url"], True)
    if isinstance(out.get("files"), list):
        out["files"] = [sub(str(f), False) for f in out["files"]]
    out["description"] = describe_step(out)
    return out


# ---------------------------------------------------------------- drafts
def _slug_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:40] or f"skill_{uuid.uuid4().hex[:6]}"


def build_draft(events: list[dict[str, Any]], *, start_url: str = "", title: str = "",
                source: str = "recorder") -> dict[str, Any]:
    steps, skipped = normalize_events(events)
    steps, variables = detect_variables(steps)
    host = short_url(start_url).split("/")[0] if start_url else ""
    name = f"New skill on {host}" if host else "New skill"
    draft = {
        "kind": "skill",
        "schema": SCHEMA,
        "draft_id": f"draft-{uuid.uuid4().hex[:8]}",
        "name": name,
        "description": title[:120] if title else "",
        "voice_triggers": [],
        "start_url": start_url,
        "steps": steps,
        "variables": variables,
        "skipped_private": skipped,
        "source": source,
    }
    draft["card"] = card_lines(draft)
    return draft


def finalize(draft: dict[str, Any], name: str, voice_triggers: list[str],
             variable_edits: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    skill = apply_variable_edits(draft, variable_edits or [])
    name = (name or draft.get("name") or "skill").strip()[:80]
    triggers = [re.sub(r"\s+", " ", t).strip()[:80] for t in voice_triggers if str(t).strip()][:6] or [name.lower()]
    now = time.time()
    return {
        "kind": "skill",
        "schema": SCHEMA,
        "id": _slug_id(name),
        "name": name,
        "description": skill.get("description") or "",
        "voice_triggers": triggers,
        "start_url": skill.get("start_url", ""),
        "steps": skill["steps"],
        "variables": skill.get("variables") or [],
        "source": skill.get("source", "recorder"),
        "created_at": now,
        "updated_at": now,
        "version": 1,
        **({"surface": "desktop", "start_app": skill.get("start_app", "")} if skill.get("surface") == "desktop" else {}),
    }


# ---------------------------------------------------------------- signed store
def _canonical(skill: dict[str, Any]) -> bytes:
    body = {k: v for k, v in skill.items() if k not in ("signature", "trusted")}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class SkillStore:
    """skills/<id>.json, each signed with HMAC-SHA256 (key: skills/.signing_key, created on first use).
    Older versions are kept in skills/history/<id>.v<N>.json."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "skills"
        self.history = self.dir / "history"
        self.key_path = self.dir / ".signing_key"

    def _key(self) -> bytes:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            tmp = self.key_path.with_suffix(".tmp")
            tmp.write_bytes(os.urandom(32))
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(self.key_path)
        return self.key_path.read_bytes()

    def sign(self, skill: dict[str, Any]) -> str:
        return hmac.new(self._key(), _canonical(skill), hashlib.sha256).hexdigest()

    def verify(self, skill: dict[str, Any]) -> bool:
        sig = str(skill.get("signature") or "")
        return bool(sig) and hmac.compare_digest(sig, self.sign(skill))

    def _path(self, skill_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9_]{1,40}", skill_id or ""):
            raise ValueError("bad skill id")
        return self.dir / f"{skill_id}.json"

    def _read(self, path: Path) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def load(self, skill_id: str) -> Optional[dict[str, Any]]:
        """The skill with `trusted` set: False means the file was changed outside Orion (never replay it)."""
        try:
            path = self._path(skill_id)
        except ValueError:
            return None
        data = self._read(path)
        if data is None:
            return None
        data["trusted"] = self.verify(data)
        return data

    def save(self, skill: dict[str, Any], *, bump: bool = True) -> dict[str, Any]:
        skill = {k: v for k, v in skill.items() if k not in ("signature", "trusted", "draft_id", "card",
                                                             "skipped_private")}
        skill["id"] = _slug_id(skill.get("id") or skill.get("name", ""))
        path = self._path(skill["id"])
        old = self._read(path) if path.exists() else None
        if old is not None:
            self.history.mkdir(parents=True, exist_ok=True)
            (self.history / f"{skill['id']}.v{int(old.get('version') or 1)}.json").write_text(
                json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
            if bump:
                skill["version"] = int(old.get("version") or 1) + 1
            skill["created_at"] = old.get("created_at", skill.get("created_at", time.time()))
        skill.setdefault("version", 1)
        skill["updated_at"] = time.time()
        skill["signature"] = self.sign(skill)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(skill, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return {**skill, "trusted": True}

    def delete(self, skill_id: str) -> bool:
        try:
            path = self._path(skill_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def all(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            s = self.load(p.stem)
            if s:
                out.append(s)
        return out

    def summaries(self) -> list[dict[str, Any]]:
        return [{
            "id": s.get("id"), "name": s.get("name"), "version": s.get("version"),
            "voice_triggers": s.get("voice_triggers") or [], "trusted": s["trusted"],
            "start_url": s.get("start_url", ""), "steps": len(s.get("steps") or []),
            "risky_steps": sum(1 for x in s.get("steps") or [] if x.get("risky")),
            "variables": s.get("variables") or [], "card": card_lines(s), "updated_at": s.get("updated_at"),
            "surface": s.get("surface", "browser"), "start_app": s.get("start_app", ""),
        } for s in self.all()]


def grimoire_entry(skill: dict[str, Any]) -> dict[str, Any]:
    """How a skill shows up in the Living Grimoire (kind `skill`)."""
    return {
        "id": "skill:" + str(skill.get("id")),
        "kind": "skill",
        "description": str(skill.get("name") or skill.get("id")),
        "voice_triggers": [str(t) for t in skill.get("voice_triggers") or []],
        "command": "",
        "steps": card_lines(skill),
        "requires_approval": any(s.get("risky") for s in skill.get("steps") or []),
        "trusted": bool(skill.get("trusted")),
        "version": skill.get("version"),
        "source": "skills",
    }


def apply_fix(skill: dict[str, Any], step_index: int, target: dict[str, Any], *, healed_kind: str = "") -> dict[str, Any]:
    """Save a better target for one step (the user clicked the right element, or a fallback selector worked).
    The new selectors go first; the old ones stay behind them as fallbacks."""
    skill = json.loads(json.dumps(skill))
    step = skill["steps"][step_index]
    old = (step.get("target") or {}).get("selectors") or []
    new_t = _clean_target(target or {})
    merged = order_selectors(list(new_t.get("selectors") or []) + [s for s in old if s.get("kind") != "coords"])
    # keep only the freshest coordinates
    coords = [s for s in new_t.get("selectors") or [] if s.get("kind") == "coords"][:1]
    merged = [s for s in merged if s.get("kind") != "coords"] + coords
    step["target"] = {**(step.get("target") or {}), **{k: v for k, v in new_t.items() if k != "selectors"},
                      "selectors": merged}
    reason = classify_risk(step)
    if reason:  # a fix can make a step risky, never the other way round
        step["risky"], step["risk_reason"] = True, step.get("risk_reason") or reason
    fixes = skill.setdefault("fixes", [])
    fixes.append({"step": step.get("id"), "at": time.time(), "how": healed_kind or "picked"})
    skill["fixes"] = fixes[-20:]
    return skill


# ---------------------------------------------------------------- Playwright export
def _py_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _var_const(name: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", name.upper())


def _py_value(template: str) -> str:
    parts = re.split(r"(\{\{\w+\}\})", str(template))
    exprs = []
    for p in parts:
        if not p:
            continue
        m = PLACEHOLDER.fullmatch(p)
        exprs.append(_var_const(m.group(1)) if m else _py_str(p))
    return " + ".join(exprs) or '""'


def _js_value(template: str) -> str:
    s = str(template).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    if not PLACEHOLDER.search(s):
        return json.dumps(str(template), ensure_ascii=False)
    return "`" + PLACEHOLDER.sub(lambda m: "${vars." + m.group(1) + "}", s) + "`"


def _locator(t: dict[str, Any], lang: str) -> Optional[str]:
    py = lang == "python"
    for s in order_selectors((t or {}).get("selectors") or []):
        k = s["kind"]
        if k == "testid" and s.get("value"):
            return f"page.{'get_by_test_id' if py else 'getByTestId'}({_py_str(s['value'])})"
        if k == "role" and s.get("role") and s.get("name"):
            if py:
                return f"page.get_by_role({_py_str(s['role'])}, name={_py_str(s['name'])}, exact=True)"
            return f"page.getByRole({_py_str(s['role'])}, {{ name: {_py_str(s['name'])}, exact: true }})"
        if k == "label" and s.get("value"):
            return f"page.get_by_label({_py_str(s['value'])}, exact=True)" if py else \
                f"page.getByLabel({_py_str(s['value'])}, {{ exact: true }})"
        if k == "text" and s.get("value"):
            return f"page.get_by_text({_py_str(s['value'])}, exact=True)" if py else \
                f"page.getByText({_py_str(s['value'])}, {{ exact: true }})"
        if k == "placeholder" and s.get("value"):
            return f"page.{'get_by_placeholder' if py else 'getByPlaceholder'}({_py_str(s['value'])})"
        if k == "css" and s.get("value"):
            return f"page.locator({_py_str(s['value'])})"
    return None


def _coords(t: dict[str, Any]) -> Optional[tuple[int, int]]:
    c = next((s for s in (t or {}).get("selectors") or [] if s.get("kind") == "coords"), None)
    return (int(c.get("x", 0)), int(c.get("y", 0))) if c else None


def export_playwright(skill: dict[str, Any], lang: str = "python") -> str:
    lang = "js" if lang in ("js", "javascript", "node") else "python"
    py = lang == "python"
    out: list[str] = []
    variables = skill.get("variables") or []
    name = skill.get("name", "skill")
    if py:
        out += ['"""Orion skill: ' + name.replace('"', "'") + f' (version {skill.get("version", 1)}).',
                "Exported from Orion. Steps that send, buy, delete, post or submit stop and ask you first.",
                '"""', "import sys", "", "from playwright.sync_api import sync_playwright", ""]
        if variables:
            out.append("# Values that change each time (pass new ones as NAME=value arguments)")
            for v in variables:
                out.append(f"{_var_const(v['name'])} = {_py_str(str(v.get('example', '')))}  # {v.get('label', '')}")
            out += ["for _arg in sys.argv[1:]:", '    _k, _, _v = _arg.partition("=")',
                    "    if _k.upper() in globals():", "        globals()[_k.upper()] = _v", ""]
        out += ["", "def confirm(what):",
                '    if input(f"Orion: about to {what}. Type yes to go ahead: ").strip().lower() != "yes":',
                '        sys.exit("Stopped before: " + what)', "", "", "def run(page):"]
        ind = "    "
    else:
        out += [f"// Orion skill: {name} (version {skill.get('version', 1)}).",
                "// Exported from Orion. Steps that send, buy, delete, post or submit stop and ask you first.",
                "const { chromium } = require('playwright');", "const readline = require('readline');", ""]
        out.append("// Values that change each time (override with NAME=value arguments)")
        out.append("const vars = {")
        for v in variables:
            out.append(f"  {v['name']}: {_py_str(str(v.get('example', '')))}, // {v.get('label', '')}")
        out += ["};", "for (const arg of process.argv.slice(2)) {", "  const [k, ...rest] = arg.split('=');",
                "  if (k in vars) vars[k] = rest.join('=');", "}", "",
                "function confirm(what) {",
                "  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });",
                "  return new Promise((resolve) => rl.question(`Orion: about to ${what}. Type yes to go ahead: `, (a) => {",
                "    rl.close();", "    if (a.trim().toLowerCase() !== 'yes') { console.log(`Stopped before: ${what}`); process.exit(1); }",
                "    resolve();", "  }));", "}", "", "async function run(page) {"]
        ind = "  "
    aw = "" if py else "await "
    body_start = len(out)
    for i, s in enumerate(skill.get("steps") or [], 1):
        out.append(f"{ind}# {i}. {describe_step(s)}" if py else f"{ind}// {i}. {describe_step(s)}")
        if s.get("risky"):
            what = _py_str(f"{describe_step(s)} ({s.get('risk_reason', '')})")
            out.append(f"{ind}{aw}confirm({what})")
        t = s.get("target") or {}
        loc = _locator(t, lang)
        xy = _coords(t)
        a = s["action"]
        val = (lambda x: _py_value(x) if py else _js_value(x))
        if a == "open":
            out.append(f"{ind}{aw}page.goto({val(s['url'])})")
        elif a == "wait_load":
            out.append(f"{ind}{aw}page.{'wait_for_load_state' if py else 'waitForLoadState'}()")
        elif a == "wait_text":
            out.append(f"{ind}{aw}page.get_by_text({val(s['text'])}).first.wait_for()" if py else
                       f"{ind}await page.getByText({val(s['text'])}).first().waitFor()")
        elif a == "download":
            hook = "page.expect_download()" if py else "page.waitForEvent('download')"
            out.append(f"{ind}{'#' if py else '//'} The download {s.get('filename', '')} starts here "
                       f"(wrap the step above in {hook} to save it).")
        elif loc is None and xy and a == "click":
            out.append(f"{ind}{aw}page.mouse.click({xy[0]}, {xy[1]})")
        elif loc is None:
            out.append(f"{ind}{'#' if py else '//'} (no usable selector recorded for this step)")
        elif a == "click":
            out.append(f"{ind}{aw}{loc}.click()")
        elif a == "fill":
            if s.get("ask"):
                prompt = 'input("Value for ' + target_label(t).replace('"', "'") + ': ")' if py else \
                    "await new Promise((r) => readline.createInterface({ input: process.stdin, output: process.stdout })" \
                    ".question('Value: ', r))"
                out.append(f"{ind}{aw}{loc}.fill({prompt})")
            else:
                out.append(f"{ind}{aw}{loc}.fill({val(s.get('value') or '')})")
        elif a == "select":
            v = s.get("option_text") or s.get("value") or ""
            out.append(f"{ind}{aw}{loc}.{'select_option' if py else 'selectOption'}({val(v)})")
        elif a == "check":
            out.append(f"{ind}{aw}{loc}.{'check' if s.get('checked', True) else 'uncheck'}()")
        elif a == "upload":
            files = s.get("files") or []
            arg = val(files[0]) if len(files) == 1 else ("[" + ", ".join(val(f) for f in files) + "]")
            out.append(f"{ind}{aw}{loc}.{'set_input_files' if py else 'setInputFiles'}({arg})")
        elif a == "press":
            out.append(f"{ind}{aw}{loc}.press(\"Enter\")")
        elif a == "submit":
            out.append(f"{ind}{aw}{loc}.evaluate(\"f => (f.form || f).requestSubmit()\")")
    if not py:
        out[body_start:] = [ln if ln.lstrip().startswith("//") else ln + ";" for ln in out[body_start:]]
    if py:
        out += ["", "", 'if __name__ == "__main__":', "    with sync_playwright() as p:",
                "        browser = p.chromium.launch(headless=False)", "        page = browser.new_page()",
                "        run(page)", "        browser.close()", ""]
    else:
        out += ["}", "", "(async () => {", "  const browser = await chromium.launch({ headless: false });",
                "  const page = await browser.newPage();", "  await run(page);", "  await browser.close();",
                "})();", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- Playwright import (best effort)
IMPORT_LIMITS = (
    "Import understands one page object called `page` and these calls: goto, click, fill/type, check/uncheck, "
    "select_option, set_input_files, press('Enter'), wait_for_load_state/wait_for_url, wait_for_selector('text=…') "
    "and expect(…get_by_text(…)).to_be_visible(); locators get_by_role/label/text/placeholder/test_id and "
    "locator('css'). Loops, conditions, frames, new tabs, popups, keyboard shortcuts and custom JavaScript are "
    "skipped and listed so you can redo them by recording.")

_STR = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)"""
_LOC = re.compile(
    r"(?:get_by_|getBy)(role|text|label|placeholder|test_id|TestId|Role|Text|Label|Placeholder)\(\s*" + _STR +
    r"(?:\s*,\s*(?:name\s*=\s*" + _STR + r"|\{[^}]*?name\s*:\s*" + _STR + r"[^}]*\}))?" + r"|locator\(\s*" + _STR + r"\s*\)")


def _s(m: re.Match, start: int) -> Optional[str]:
    for i in range(start, start + 3):
        if m.group(i) is not None:
            return bytes(m.group(i), "utf-8").decode("unicode_escape") if "\\" in m.group(i) else m.group(i)
    return None


def _target_from_css(sel: str) -> dict[str, Any]:
    if sel.startswith("text="):
        txt = sel[5:].strip("\"'")
        return {"text": txt, "selectors": [{"kind": "text", "value": txt}]}
    if sel.startswith(("data-testid=", "[data-testid=")):
        tid = re.sub(r"^\[?data-testid=|\]$", "", sel).strip("\"'")
        return {"testid": tid, "selectors": [{"kind": "testid", "value": tid}]}
    return {"selectors": [{"kind": "css", "value": sel}]}


def _parse_locator(chain: str) -> Optional[dict[str, Any]]:
    m = None
    for m in _LOC.finditer(chain):
        pass  # the last locator in a chain is the element acted on
    if not m:
        return None
    if m.group(1):
        kind = re.sub(r"(?<!^)(?=[A-Z])", "_", m.group(1)).lower()
        value = _s(m, 2) or ""
        name = _s(m, 5) or _s(m, 8)
        if kind == "role":
            t = {"role": value, "name": name or "", "selectors": [{"kind": "role", "role": value, "name": name or ""}]}
            if value in ("textbox", "searchbox", "combobox"):
                t["tag"] = "input"
            return t
        if kind == "test_id":
            return {"testid": value, "selectors": [{"kind": "testid", "value": value}]}
        if kind == "label":
            return {"label": value, "tag": "input", "selectors": [{"kind": "label", "value": value}]}
        if kind == "placeholder":
            return {"placeholder": value, "tag": "input", "selectors": [{"kind": "placeholder", "value": value}]}
        return {"text": value, "selectors": [{"kind": "text", "value": value}]}
    return _target_from_css(_s(m, 11) or "")


def import_playwright(code: str, name: str = "Imported skill") -> tuple[dict[str, Any], list[str]]:
    """Best-effort Playwright (Python or JS) -> skill draft. Returns (draft, skipped lines)."""
    events: list[dict[str, Any]] = []
    skipped: list[str] = []
    t = 0
    start_url = ""
    for n, raw in enumerate(code.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", "//", "import ", "from ", "const {", "const browser", "browser",
                                        "context", "page = ", "const page", "with ", "async ", "def ", "})",
                                        "}", "(async", "require(", "await browser", "await context", "run(",
                                        "await run(", "if __name__", "print(", "console.log")):
            continue
        if "page" not in line:
            skipped.append(f"line {n}: {line[:100]}")
            continue
        t += 1000
        ev: Optional[dict[str, Any]] = None
        m = re.search(r"\.goto\(\s*" + _STR, line)
        if m:
            url = _s(m, 1) or ""
            start_url = start_url or url
            ev = {"action": "navigate", "url": url}
        elif re.search(r"(wait_for_load_state|waitForLoadState|wait_for_url|waitForURL|wait_for_navigation|waitForNavigation)", line):
            ev = {"action": "wait_load"}
        elif re.search(r"(to_be_visible|toBeVisible|wait_for\(\)|waitFor\(\))", line) and re.search(r"(get_by_text|getByText)", line):
            m = re.search(r"(?:get_by_text|getByText)\(\s*" + _STR, line)
            ev = {"action": "wait_text", "text": _s(m, 1) or ""} if m else None
        elif re.search(r"(wait_for_selector|waitForSelector)\(\s*['\"]text=", line):
            m = re.search(r"text=([^'\"]+)", line)
            ev = {"action": "wait_text", "text": m.group(1).strip()} if m else None
        else:
            # page.click("css") / page.fill("css", "v") shorthand
            m = re.search(r"page\.(click|dblclick|fill|type|check|uncheck|select_option|selectOption|press|"
                          r"set_input_files|setInputFiles)\(\s*" + _STR + r"(?:\s*,\s*" + _STR + r")?", line)
            if m:
                verb, target, arg = m.group(1), _target_from_css(_s(m, 2) or ""), _s(m, 5)
            else:
                m2 = re.search(r"\.(click|dblclick|fill|type|press_sequentially|pressSequentially|check|uncheck|"
                               r"select_option|selectOption|press|set_input_files|setInputFiles)\(\s*(?:" + _STR +
                               r"|label\s*=\s*" + _STR + r"|\{\s*label\s*:\s*" + _STR + r"\s*\})?", line)
                target = _parse_locator(line[:m2.start()]) if m2 else None
                verb = m2.group(1) if m2 else ""
                arg = (_s(m2, 2) or _s(m2, 5) or _s(m2, 8)) if m2 else None
            if target is None or not verb:
                skipped.append(f"line {n}: {line[:100]}")
                continue
            if verb in ("click", "dblclick"):
                ev = {"action": "click", "target": {"tag": "button" if target.get("role") == "button" else "",
                                                    **target}}
            elif verb in ("fill", "type", "press_sequentially", "pressSequentially"):
                ev = {"action": "fill", "target": {"tag": "input", **target}, "value": arg or ""}
            elif verb in ("check", "uncheck"):
                ev = {"action": "check", "target": {"tag": "input", "type": "checkbox", **target},
                      "checked": verb == "check"}
            elif verb in ("select_option", "selectOption"):
                ev = {"action": "select", "target": {"tag": "select", **target}, "value": arg or "",
                      "option_text": arg or ""}
            elif verb in ("set_input_files", "setInputFiles"):
                ev = {"action": "upload", "target": {"tag": "input", "type": "file", **target},
                      "files": [arg] if arg else []}
            elif verb == "press":
                if (arg or "") != "Enter":
                    skipped.append(f"line {n}: {line[:100]}")
                    continue
                ev = {"action": "press", "key": "Enter", "target": {**target, "in_form": True}}
        if ev is None:
            skipped.append(f"line {n}: {line[:100]}")
            continue
        ev["t"] = t
        events.append(ev)
    draft = build_draft(events, start_url=start_url, source="playwright")
    draft["name"] = name
    draft["import_skipped"] = skipped
    draft["import_limits"] = IMPORT_LIMITS
    return draft, skipped


# ---------------------------------------------------------------- pattern spotting
def step_signature(step: dict[str, Any], host: str = "") -> str:
    if step.get("surface") == "desktop":
        from desktop_skill import step_signature as desktop_signature
        return desktop_signature(step)
    a = step.get("action", "")
    if a == "open":
        try:
            p = urlsplit(str(step.get("url", "")))
            return f"open|{p.netloc}{p.path}"
        except ValueError:
            return "open|"
    return f"{a}|{host}|{target_label(step.get('target') or {}).lower()}|{step.get('text', '')}"


def spot_patterns(signatures: list[str], *, min_len: int = 3, min_count: int = 3, max_len: int = 10) -> list[dict[str, Any]]:
    """Sequences of >= min_len steps that occur >= min_count times (non-overlapping) in the recent log.
    Longest first; shorter pieces of an already-found sequence are not reported again."""
    found: list[dict[str, Any]] = []
    n = len(signatures)
    for length in range(min(max_len, n // max(1, min_count)), min_len - 1, -1):
        counts: Counter = Counter()
        last_end: dict[tuple, int] = {}
        for i in range(n - length + 1):
            seq = tuple(signatures[i:i + length])
            if len(set(seq)) < 2:
                continue
            if i >= last_end.get(seq, -1):
                counts[seq] += 1
                last_end[seq] = i + length
        for seq, c in counts.most_common():
            if c < min_count:
                break
            if any(_contains(f["sequence"], seq) for f in found):
                continue
            found.append({"sequence": list(seq), "count": c, "length": length})
    return found


def _contains(big: list[str], small: tuple) -> bool:
    k = len(small)
    return any(tuple(big[i:i + k]) == small for i in range(len(big) - k + 1))


def suggestion_card(pattern: dict[str, Any]) -> dict[str, Any]:
    pretty = []
    for sig in pattern["sequence"]:
        parts = sig.split("|")
        if parts[0] == "open":
            pretty.append(f"Go to {parts[1]}")
        else:
            label = parts[2] if len(parts) > 2 else ""
            pretty.append(f"{parts[0].replace('_', ' ').title()} “{label}”" if label else parts[0].replace("_", " ").title())
    sid = hashlib.sha1("\n".join(pattern["sequence"]).encode()).hexdigest()[:10]
    return {"id": f"pattern-{sid}", "title": "You keep doing this — make it a skill?",
            "body": f"I've seen these {pattern['length']} steps {pattern['count']} times lately.",
            "steps": pretty, "count": pattern["count"], "at": datetime.now().isoformat(timespec="seconds")}
