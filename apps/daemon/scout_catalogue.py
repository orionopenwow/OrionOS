"""Video Scout: the starter WoW keyword list, the addon catalogue seed, and the optional GitHub refresh.

Your own additions live in data_dir/scout/catalogue.json and data_dir/scout/keywords.json (created on first
use, safe to edit). The GitHub refresh only runs when GITHUB_TOKEN (or ORION_GITHUB_TOKEN) is set: it searches
public repos that ship a WoW addon .toc file, at most once a day and at most a few pages, and adds new names to
the catalogue marked "github" with the day it first saw them (that is what "new projects" means on the board).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orion.scout.catalogue")

# name, aliases, category. Short aliases (4 letters or fewer) only match as whole words, case-sensitively.
SEED_ADDONS: list[tuple[str, list[str], str]] = [
    ("WeakAuras", ["WeakAuras", "Weak Auras", "WeakAura", "WA"], "ui"),
    ("Details! Damage Meter", ["Details", "Details Damage Meter"], "combat"),
    ("Deadly Boss Mods", ["Deadly Boss Mods", "DBM"], "raid"),
    ("BigWigs", ["BigWigs", "Big Wigs"], "raid"),
    ("Plater Nameplates", ["Plater", "Plater Nameplates"], "ui"),
    ("ElvUI", ["ElvUI", "Elv UI"], "ui"),
    ("Bartender4", ["Bartender", "Bartender4"], "ui"),
    ("Dominos", ["Dominos"], "ui"),
    ("Questie", ["Questie"], "questing"),
    ("Hekili", ["Hekili"], "combat"),
    ("OmniCD", ["OmniCD"], "pvp"),
    ("OmniBar", ["OmniBar"], "pvp"),
    ("Gladius", ["Gladius", "GladiusEx"], "pvp"),
    ("sArena", ["sArena"], "pvp"),
    ("Method Raid Tools", ["Method Raid Tools", "MRT", "Exorsus Raid Tools"], "raid"),
    ("Raider.IO", ["Raider.IO", "Raider IO", "RaiderIO"], "mythic+"),
    ("Mythic Dungeon Tools", ["Mythic Dungeon Tools", "MDT"], "mythic+"),
    ("TomTom", ["TomTom", "Tom Tom"], "questing"),
    ("Auctionator", ["Auctionator"], "gold"),
    ("TradeSkillMaster", ["TradeSkillMaster", "Trade Skill Master", "TSM"], "gold"),
    ("Bagnon", ["Bagnon"], "bags"),
    ("AdiBags", ["AdiBags", "Adi Bags"], "bags"),
    ("Baganator", ["Baganator"], "bags"),
    ("Cell", ["Cell raid frames", "Cell addon"], "raid frames"),
    ("VuhDo", ["VuhDo", "Vuhdo"], "raid frames"),
    ("Grid2", ["Grid2", "Grid 2"], "raid frames"),
    ("Shadowed Unit Frames", ["Shadowed Unit Frames", "SUF"], "ui"),
    ("Leatrix Plus", ["Leatrix Plus", "Leatrix"], "quality of life"),
    ("Leatrix Maps", ["Leatrix Maps"], "quality of life"),
    ("Simulationcraft", ["Simulationcraft", "SimC", "Simcraft"], "combat"),
    ("Pawn", ["Pawn addon"], "gear"),
    ("RareScanner", ["RareScanner", "Rare Scanner"], "collecting"),
    ("HandyNotes", ["HandyNotes", "Handy Notes"], "collecting"),
    ("AllTheThings", ["AllTheThings", "All The Things", "ATT"], "collecting"),
    ("Rarity", ["Rarity addon"], "collecting"),
    ("Masque", ["Masque"], "ui"),
    ("Chattynator", ["Chattynator"], "ui"),
    ("Platynator", ["Platynator"], "ui"),
    ("Edit Mode Expanded", ["Edit Mode Expanded"], "ui"),
    ("Narcissus", ["Narcissus"], "ui"),
    ("Zygor Guides", ["Zygor"], "questing"),
    ("GTFO", ["GTFO addon"], "combat"),
    ("Skada", ["Skada"], "combat"),
    ("Recount", ["Recount"], "combat"),
    ("Altoholic", ["Altoholic"], "alts"),
    ("SavedInstances", ["SavedInstances", "Saved Instances"], "alts"),
    ("Kui Nameplates", ["KuiNameplates", "Kui Nameplates"], "ui"),
    ("Opie", ["OPie"], "ui"),
    ("MoveAny", ["MoveAny", "Move Any"], "ui"),
    ("BtWQuests", ["BtWQuests"], "questing"),
]

# Words that make a WoW video worth a look even when no addon is named.
SEED_KEYWORDS: list[tuple[str, list[str]]] = [
    ("new addon", ["new addon", "brand new addon", "addon just released", "new mod"]),
    ("best addons", ["best addons", "top addons", "must have addons", "must-have addons", "addons you need"]),
    ("weakaura pack", ["weakaura pack", "wa pack", "import string"]),
    ("ui setup", ["ui setup", "ui guide", "ui tour", "my ui", "interface setup", "edit mode"]),
    ("wago", ["wago", "wago.io"]),
    ("curseforge", ["curseforge", "curse forge"]),
    ("github", ["github"]),
    ("open source", ["open source", "open-source"]),
    ("private server", ["private server", "emulator", "trinitycore", "azerothcore", "openwow"]),
    ("beta / PTR", ["beta", "ptr", "alpha build"]),
    ("patch", ["patch notes", "new patch", "hotfix"]),
    ("macro", ["macro", "macros"]),
    ("gold making", ["gold making", "goldmaking", "gold farm"]),
    ("mythic plus", ["mythic plus", "mythic+", "m+ route"]),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())


class Catalogue:
    """Addons + keywords: the seed plus your own files, merged by name."""

    def __init__(self, folder: Path, clock=time.time) -> None:
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.addons_path = self.dir / "catalogue.json"
        self.keywords_path = self.dir / "keywords.json"
        self.clock = clock
        if not self.keywords_path.exists():
            self.keywords_path.write_text(json.dumps(
                {"keywords": [{"name": n, "aliases": a} for n, a in SEED_KEYWORDS]}, indent=1), encoding="utf-8")

    # --------------------------------------------------------------- reading
    def _load_user(self) -> dict[str, Any]:
        try:
            data = json.loads(self.addons_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def addons(self) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for name, aliases, cat in SEED_ADDONS:
            out[name.lower()] = {"name": name, "aliases": aliases, "category": cat, "source": "seed"}
        for a in self._load_user().get("addons", []):
            if isinstance(a, dict) and str(a.get("name", "")).strip():
                name = _norm(str(a["name"]))[:80]
                aliases = [_norm(str(x))[:80] for x in a.get("aliases", []) if str(x).strip()] or [name]
                prev = out.get(name.lower(), {})
                out[name.lower()] = {**prev, **a, "name": name, "aliases": sorted(set(aliases + prev.get("aliases", [])))}
        return list(out.values())

    def keywords(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.keywords_path.read_text(encoding="utf-8"))
            items = data.get("keywords", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            items = []
        good = [{"name": str(k["name"]), "aliases": [str(x) for x in k.get("aliases", [k["name"]])]}
                for k in items if isinstance(k, dict) and k.get("name")]
        return good or [{"name": n, "aliases": a} for n, a in SEED_KEYWORDS]

    def terms(self) -> list[dict[str, Any]]:
        """Everything the matcher looks for: {"name", "aliases", "kind": addon|keyword}."""
        return ([{**a, "kind": "addon"} for a in self.addons()] +
                [{**k, "kind": "keyword"} for k in self.keywords()])

    def add_addon(self, name: str, *, aliases: Optional[list[str]] = None, source: str = "user",
                  url: str = "", first_seen: Optional[float] = None) -> bool:
        """Adds a name the catalogue didn't know. Returns False when it was already there."""
        name = _norm(name)[:80]
        if not name or any(a["name"].lower() == name.lower() for a in self.addons()):
            return False
        data = self._load_user()
        data.setdefault("addons", []).append({"name": name, "aliases": aliases or [name], "category": "",
                                              "source": source, "url": url,
                                              "first_seen": first_seen or self.clock()})
        self.addons_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return True

    def new_since(self, ts: float) -> list[dict[str, Any]]:
        return [a for a in self.addons() if a.get("source") == "github" and float(a.get("first_seen") or 0) >= ts]

    # --------------------------------------------------------------- GitHub refresh (optional)
    def refresh_due(self) -> bool:
        data = self._load_user()
        return self.clock() - float(data.get("github_refreshed", 0) or 0) > 86400

    async def refresh_from_github(self, http: Any = None, *, token: Optional[str] = None, pages: int = 2) -> dict[str, Any]:
        """Public code search for addon .toc files. Needs a GitHub token (code search requires one)."""
        token = token if token is not None else (os.getenv("ORION_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or "")
        if not token:
            return {"ok": False, "added": [], "message": "Add a GitHub token (GITHUB_TOKEN) to look for new addons on GitHub."}
        if not self.refresh_due():
            return {"ok": True, "added": [], "message": "Already checked GitHub in the last day."}
        own_http = http is None
        if own_http:
            import httpx
            http = httpx.AsyncClient(timeout=20)
        added: list[str] = []
        try:
            for page in range(1, max(1, min(pages, 5)) + 1):
                r = await http.get(
                    "https://api.github.com/search/code",
                    params={"q": '"## Interface" extension:toc', "per_page": 50, "page": page},
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                             "User-Agent": "Orion-VideoScout"},
                )
                if r.status_code in (403, 429):
                    log.info("GitHub search rate limit hit; stopping for today")
                    break
                if r.status_code != 200:
                    return {"ok": False, "added": added, "message": f"GitHub answered {r.status_code}."}
                for item in (r.json() or {}).get("items", []):
                    repo = item.get("repository") or {}
                    toc = str(item.get("name", ""))
                    name = toc[:-4] if toc.lower().endswith(".toc") else repo.get("name", "")
                    name = re.sub(r"[_-](Mainline|Classic|Vanilla|Wrath|Cata|TBC|Mists|BCC)$", "", name, flags=re.I)
                    if len(name) < 3 or not re.match(r"^[\w .!'-]+$", name):
                        continue
                    if self.add_addon(name, source="github", url=repo.get("html_url", "")):
                        added.append(name)
                await asyncio.sleep(2.5)   # be polite: code search allows ~10 requests a minute
        finally:
            if own_http:
                await http.aclose()
        data = self._load_user()
        data["github_refreshed"] = self.clock()
        self.addons_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return {"ok": True, "added": added, "message": f"Found {len(added)} addon names new to the catalogue."}
