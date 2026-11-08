"""Project Runway & Exp Bar Tracker (Feature Bible D6).

~/.orion/runway.json (edited from the HUD's Life tab, or by hand):
  {"currency": "USD",
   "balances":        [{"name": "Checking", "amount": 2400}],
   "monthly_costs":   [{"name": "Game server", "amount": 45, "due_day": 3}],
   "monthly_revenue": [{"name": "Ko-fi", "amount": 120}],
   "milestones":      [{"name": "Server costs covered", "target": 45, "metric": "monthly_revenue"}],
   "opencollective_slug": "",
   "kofi_manual_balance": 0}

Computes monthly burn, net, runway (months) and milestone progress for the Exp Bar. If an Open
Collective slug is set, its public balance is fetched (no login needed) and counted as a balance.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("orion.runway")

DEFAULT = {
    "currency": "USD",
    "balances": [],
    "monthly_costs": [],
    "monthly_revenue": [],
    "milestones": [{"name": "Hosting fully covered by supporters", "target": 0, "metric": "covered_ratio"}],
    "opencollective_slug": "",
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class Runway:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "runway.json"
        self._oc_cache: tuple[float, float] = (0.0, 0.0)  # (fetched_at, balance)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {**DEFAULT, **data}
        except (OSError, ValueError):
            return dict(DEFAULT)

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        clean = {**DEFAULT}
        clean["currency"] = str(data.get("currency") or "USD")[:8]
        for key in ("balances", "monthly_costs", "monthly_revenue"):
            clean[key] = [{"name": str(i.get("name", ""))[:80], "amount": _num(i.get("amount")),
                           **({"due_day": int(i["due_day"])} if str(i.get("due_day", "")).isdigit() else {})}
                          for i in data.get(key, []) if isinstance(i, dict)][:50]
        clean["milestones"] = [{"name": str(m.get("name", ""))[:80], "target": _num(m.get("target")),
                                "metric": str(m.get("metric") or "monthly_revenue")}
                               for m in data.get("milestones", []) if isinstance(m, dict)][:20]
        clean["opencollective_slug"] = str(data.get("opencollective_slug") or "")[:80]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        return clean

    async def opencollective_balance(self, slug: str) -> float:
        if not slug:
            return 0.0
        fetched, value = self._oc_cache
        if time.time() - fetched < 3600:
            return value
        query = {"query": "query($slug:String){account(slug:$slug){stats{balance{valueInCents}}}}",
                 "variables": {"slug": slug}}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post("https://api.opencollective.com/graphql/v2", json=query)
                cents = r.json()["data"]["account"]["stats"]["balance"]["valueInCents"]
                value = _num(cents) / 100
        except Exception as exc:
            log.info("Open Collective balance unavailable: %s", exc)
        self._oc_cache = (time.time(), value)
        return value

    async def summary(self) -> dict[str, Any]:
        data = self.load()
        oc = await self.opencollective_balance(data.get("opencollective_slug", ""))
        balance = sum(_num(b["amount"]) for b in data["balances"]) + oc
        costs = sum(_num(c["amount"]) for c in data["monthly_costs"])
        revenue = sum(_num(r["amount"]) for r in data["monthly_revenue"])
        net = revenue - costs
        runway = None if net >= 0 else (balance / -net if -net > 0 else None)
        metrics = {"monthly_revenue": revenue, "balance": balance, "net": net,
                   "covered_ratio": (revenue / costs) if costs else 0.0}
        milestones = []
        for m in data["milestones"]:
            metric = m.get("metric", "monthly_revenue")
            target = _num(m["target"]) or (costs if metric == "monthly_revenue" else 1.0)
            if metric == "covered_ratio":
                target = 1.0
            current = metrics.get(metric, 0.0)
            milestones.append({**m, "target": target, "current": round(current, 2),
                               "progress": round(min(1.0, current / target), 4) if target else 1.0})
        return {
            **data,
            "opencollective_balance": oc,
            "total_balance": round(balance, 2),
            "monthly_costs_total": round(costs, 2),
            "monthly_revenue_total": round(revenue, 2),
            "monthly_net": round(net, 2),
            "runway_months": round(runway, 1) if runway is not None else None,
            "milestone_progress": milestones,
        }
