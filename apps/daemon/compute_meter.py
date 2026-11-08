"""Compute meter (the "gas meter"): tokens and an estimated dollar cost per model, today and this session.

The orchestrator calls ComputeMeter.record() after every model call. Daily totals are kept in
<data_dir>/compute_meter.json so the meter survives restarts. Costs are worked out when you look,
from the price table below, so editing a price fixes old numbers too.

PRICES ARE ESTIMATES. They are US dollars per one million tokens and they go out of date. To change
them, write ~/.config/orion/prices.json (or point ORION_PRICES_FILE at another file), for example:

    {"claude-sonnet-4-5": {"in": 3.0, "out": 15.0}, "my-model": {"in": 0.5, "out": 1.5}}

A key matches any model name that starts with it (the longest match wins). Local models run through
Ollama are always $0.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.compute")

# Editable estimates: USD per 1M tokens (input, output). Longest prefix wins.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-5": {"in": 5.0, "out": 25.0},
    "claude-opus-4-1": {"in": 15.0, "out": 75.0},
    "claude-opus-4": {"in": 15.0, "out": 75.0},
    "claude-sonnet-4": {"in": 3.0, "out": 15.0},
    "claude-3-7-sonnet": {"in": 3.0, "out": 15.0},
    "claude-3-5-sonnet": {"in": 3.0, "out": 15.0},
    "claude-haiku-4": {"in": 1.0, "out": 5.0},
    "claude-3-5-haiku": {"in": 0.8, "out": 4.0},
    "claude-3-haiku": {"in": 0.25, "out": 1.25},
    "claude": {"in": 3.0, "out": 15.0},
    # OpenAI
    "gpt-4o-mini": {"in": 0.15, "out": 0.6},
    "gpt-4o": {"in": 2.5, "out": 10.0},
    "gpt-4.1-nano": {"in": 0.1, "out": 0.4},
    "gpt-4.1-mini": {"in": 0.4, "out": 1.6},
    "gpt-4.1": {"in": 2.0, "out": 8.0},
    "gpt-5-nano": {"in": 0.05, "out": 0.4},
    "gpt-5-mini": {"in": 0.25, "out": 2.0},
    "gpt-5": {"in": 1.25, "out": 10.0},
    "o4-mini": {"in": 1.1, "out": 4.4},
    "o3": {"in": 2.0, "out": 8.0},
    "text-embedding-3-small": {"in": 0.02, "out": 0.0},
    "text-embedding-3-large": {"in": 0.13, "out": 0.0},
    # Google
    "gemini-1.5-flash": {"in": 0.075, "out": 0.3},
    "gemini-1.5-pro": {"in": 1.25, "out": 5.0},
    "gemini-2.0-flash": {"in": 0.1, "out": 0.4},
    "gemini-2.5-flash-lite": {"in": 0.1, "out": 0.4},
    "gemini-2.5-flash": {"in": 0.3, "out": 2.5},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.0},
    "gemini": {"in": 0.3, "out": 2.5},
}
# Used for a cloud model we have no price for. Deliberately on the high side so it is not ignored.
UNKNOWN_CLOUD_PRICE = {"in": 3.0, "out": 15.0}

SHORT_OUTPUT_TOKENS = 80     # a call that answers in fewer tokens than this looks like a yes/no or label job
LONG_PROMPT_TOKENS = 6000    # prompts bigger than this are "long"
KEEP_DAYS = 60
PRICES_NOTE = "Prices are rough estimates. Change them in ~/.config/orion/prices.json."


def prices_path() -> Path:
    env = os.environ.get("ORION_PRICES_FILE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "orion" / "prices.json"


def load_prices(path: Optional[Path] = None) -> dict[str, dict[str, float]]:
    """Defaults plus the user's overrides. A bad file is ignored (logged), never fatal."""
    prices = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    path = path if path is not None else prices_path()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for name, p in raw.items():
                    if isinstance(p, dict) and not str(name).startswith("_"):
                        prices[str(name).lower()] = {"in": float(p.get("in", 0) or 0), "out": float(p.get("out", 0) or 0)}
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Could not read prices file %s: %s", path, exc)
    return prices


def price_for(model: str, provider: str, prices: dict[str, dict[str, float]]) -> tuple[dict[str, float], bool]:
    """(price per 1M tokens, known). Local Ollama models are free."""
    if provider == "ollama":
        return {"in": 0.0, "out": 0.0}, True
    name = (model or "").lower()
    if "/" in name:  # e.g. "models/gemini-1.5-flash"
        name = name.rsplit("/", 1)[-1]
    best = ""
    for key in prices:
        if name.startswith(key) and len(key) > len(best):
            best = key
    if best:
        return prices[best], True
    return dict(UNKNOWN_CLOUD_PRICE), False


def cost_of(tokens_in: int, tokens_out: int, price: dict[str, float]) -> float:
    return (tokens_in * price.get("in", 0.0) + tokens_out * price.get("out", 0.0)) / 1_000_000


def _blank() -> dict[str, Any]:
    return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "short_calls": 0, "short_in": 0, "short_out": 0,
            "long_calls": 0}


def _add(row: dict[str, Any], tokens_in: int, tokens_out: int) -> None:
    row["calls"] += 1
    row["tokens_in"] += tokens_in
    row["tokens_out"] += tokens_out
    if tokens_out and tokens_out < SHORT_OUTPUT_TOKENS:
        row["short_calls"] += 1
        row["short_in"] += tokens_in
        row["short_out"] += tokens_out
    if tokens_in > LONG_PROMPT_TOKENS:
        row["long_calls"] += 1


class ComputeMeter:
    def __init__(self, store: Optional[Path] = None, prices_file: Optional[Path] = None,
                 clock: Any = time.time) -> None:
        self.store = store
        self.prices_file = prices_file
        self.clock = clock
        self.started_at = clock()
        self._lock = threading.Lock()
        # key "provider|model" -> counters
        self.session: dict[str, dict[str, Any]] = {}
        self.days: dict[str, dict[str, dict[str, Any]]] = {}
        self.saved: dict[str, dict[str, int]] = {}   # day -> {source: tokens saved}, e.g. Context Pack (context_pack.py)
        self._prices: Optional[dict[str, dict[str, float]]] = None
        self._prices_mtime: Optional[float] = None
        self._load()

    # ------------------------------------------------------------ prices
    def prices(self) -> dict[str, dict[str, float]]:
        path = self.prices_file if self.prices_file is not None else prices_path()
        try:
            mtime: Optional[float] = path.stat().st_mtime
        except OSError:
            mtime = None
        if self._prices is None or mtime != self._prices_mtime:
            self._prices = load_prices(path)
            self._prices_mtime = mtime
        return self._prices

    # ------------------------------------------------------------ storage
    def _today(self) -> str:
        return date.fromtimestamp(self.clock()).isoformat()

    def _load(self) -> None:
        if not self.store or not self.store.is_file():
            return
        try:
            raw = json.loads(self.store.read_text(encoding="utf-8"))
            days = raw.get("days", {}) if isinstance(raw, dict) else {}
            for day, models in days.items():
                if isinstance(models, dict):
                    self.days[str(day)] = {k: {**_blank(), **v} for k, v in models.items() if isinstance(v, dict)}
            saved = raw.get("saved", {}) if isinstance(raw, dict) else {}
            self.saved = {str(d): {str(k): int(v) for k, v in s.items()} for d, s in saved.items() if isinstance(s, dict)}
        except (OSError, ValueError) as exc:
            log.warning("Compute meter file unreadable, starting fresh: %s", exc)

    def _save(self) -> None:
        if not self.store:
            return
        for old in sorted(self.days)[:-KEEP_DAYS]:
            self.days.pop(old, None)
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store.with_suffix(".tmp")
            for old in sorted(self.saved)[:-KEEP_DAYS]:
                self.saved.pop(old, None)
            tmp.write_text(json.dumps({"version": 1, "days": self.days, "saved": self.saved}), encoding="utf-8")
            os.replace(tmp, self.store)
        except OSError as exc:
            log.warning("Could not save compute meter: %s", exc)

    # ------------------------------------------------------------ recording
    def record(self, model: str, provider: str, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """One model call. provider: anthropic | openai | gemini | ollama."""
        model = (model or "unknown")[:80]
        provider = (provider or "cloud")[:20]
        tokens_in, tokens_out = max(0, int(tokens_in or 0)), max(0, int(tokens_out or 0))
        key = f"{provider}|{model}"
        with self._lock:
            _add(self.session.setdefault(key, _blank()), tokens_in, tokens_out)
            _add(self.days.setdefault(self._today(), {}).setdefault(key, _blank()), tokens_in, tokens_out)
            self._save()

    def record_saving(self, source: str, tokens: int) -> None:
        """Tokens a tool kept out of a prompt (Context Pack: brief vs whole repo). Shown as `saved` in snapshot()."""
        tokens = max(0, int(tokens or 0))
        with self._lock:
            day = self.saved.setdefault(self._today(), {})
            day[str(source)[:40]] = day.get(str(source)[:40], 0) + tokens
            self._save()

    # ------------------------------------------------------------ views
    def _period(self, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
        prices = self.prices()
        models = []
        for key, r in rows.items():
            provider, _, model = key.partition("|")
            price, known = price_for(model, provider, prices)
            models.append({
                "model": model, "provider": provider, "local": provider == "ollama",
                "calls": r["calls"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"],
                "cost": round(cost_of(r["tokens_in"], r["tokens_out"], price), 6),
                "short_calls": r["short_calls"],
                "short_cost": round(cost_of(r["short_in"], r["short_out"], price), 6),
                "long_calls": r["long_calls"],
                "price_in": price["in"], "price_out": price["out"], "price_known": known,
            })
        models.sort(key=lambda m: (m["cost"], m["tokens_in"] + m["tokens_out"]), reverse=True)
        return {
            "cost": round(sum(m["cost"] for m in models), 6),
            "tokens_in": sum(m["tokens_in"] for m in models),
            "tokens_out": sum(m["tokens_out"] for m in models),
            "calls": sum(m["calls"] for m in models),
            "models": models,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            today_key = self._today()
            today = {"date": today_key, **self._period(self.days.get(today_key, {}))}
            session = {"since": self.started_at, **self._period(self.session)}
            history = [{"date": d, "cost": self._period(self.days[d])["cost"]} for d in sorted(self.days)[-7:]]
            saved = dict(self.saved.get(today_key, {}))
        return {"today": today, "session": session, "history": history, "tips": tips(today, session),
                "note": PRICES_NOTE, "saved": {"today": sum(saved.values()), "by_tool": saved}}

    def brief(self) -> dict[str, Any]:
        """Small summary for the telemetry stream."""
        s = self.snapshot()
        return {"today_cost": s["today"]["cost"], "session_cost": s["session"]["cost"],
                "today_tokens": s["today"]["tokens_in"] + s["today"]["tokens_out"],
                "session_tokens": s["session"]["tokens_in"] + s["session"]["tokens_out"]}


def _money(x: float) -> str:
    return f"${x:.2f}" if x >= 0.01 else "under a cent"


def tips(today: dict[str, Any], session: dict[str, Any]) -> list[str]:
    """Plain-word ideas for spending less. Looks at today, or this session if today is empty."""
    period = today if today["calls"] else session
    total = period["cost"]
    out: list[str] = []
    if total <= 0 or not period["models"]:
        return out
    cloud = [m for m in period["models"] if not m["local"]]
    has_local = any(m["local"] for m in period["models"])

    # 1. lots of spend on short label-style answers from one cloud model
    for m in cloud:
        if m["short_calls"] >= 5 and m["short_cost"] >= 0.25 * total and m["short_calls"] >= 0.5 * m["calls"]:
            out.append(f"Most calls to {m['model']} give short answers, like sorting or yes/no. "
                       f"A local model could do these for free (saves about {_money(m['short_cost'])}).")
            break

    # 2. very long prompts
    calls = period["calls"]
    long_calls = sum(m["long_calls"] for m in cloud)
    avg_in = period["tokens_in"] / calls if calls else 0
    if long_calls >= 3 and long_calls >= 0.3 * max(1, sum(m["calls"] for m in cloud)):
        out.append(f"{long_calls} prompts were very long. Sending less context (fewer memories, shorter "
                   f"screen text) would cut the cost.")
    elif avg_in > LONG_PROMPT_TOKENS / 2 and cloud:
        out.append(f"Prompts average {int(avg_in):,} tokens. Trimming context would cut the cost.")

    # 3. one pricey model takes most of the bill
    if cloud:
        top = cloud[0]
        if top["cost"] >= 0.7 * total and top["price_in"] >= 2.5 and top["calls"] >= 10:
            out.append(f"{top['model']} is {round(100 * top['cost'] / total)}% of the bill. "
                       f"A smaller model may be good enough for everyday chat.")

    # 4. output much bigger than input
    if period["tokens_out"] > 2 * max(1, period["tokens_in"]) and period["tokens_out"] > 20_000:
        out.append("Answers are long. Asking for shorter replies would save money.")

    if not has_local and cloud and total >= 0.05:
        out.append("No local model has run yet. Turning on Ollama lets simple jobs run for free.")
    return out[:4]
