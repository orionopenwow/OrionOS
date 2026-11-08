"""Day-One Preload Kit (Feature Bible, Part II).

Orion wakes up already knowing you. Five human-editable seed files live in ~/.orion/preload/:

  identity_and_rules.md     who you are, how you talk, collaborators, hard boundaries
                            -> every bullet becomes a permanent Tier-0 rule injected into prompts
  workspace_manifest.json   your repos: path, start command, ports, tags      -> Projects tab
  grimoire_scripts.json     one-liners / macros bound to voice triggers         -> Tools tab / Grimoire
  comms_routing.json        Discord channels, VIP contacts, approval-only channels, muted groups
  conditioning_deck.json    calisthenics / yoga / breathwork sets, focus block length -> pacing arc

They are created from the shipped seed bundle (apps/daemon/preload_seed.json, or a
~/.orion/preload/seed_bundle.json you drop in) the first time Orion runs, re-read whenever you
edit them (no restart needed), and extended by the harvester (harvester.py) without ever
overwriting what you wrote.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("orion.preload")

FILES = (
    "identity_and_rules.md",
    "workspace_manifest.json",
    "grimoire_scripts.json",
    "comms_routing.json",
    "conditioning_deck.json",
)

DEFAULT_COMMS_ROUTING: dict[str, Any] = {
    "discord": {
        "triage_channels": ["general", "realm-pitches", "bug-reports", "support"],
        "approval_required_channels": ["realm-pitches", "bug-reports", "support"],
        "channel_ids": {},
    },
    # Example entry: {"name": "Alex", "platforms": ["telegram", "discord"], "aliases": []}
    "vip_contacts": [],
    "low_priority_threads": [],
    "digest_times": ["12:00", "18:00"],
    "_help": "vip_contacts spawn orbiting satellites; low_priority_threads (names or thread keys) are muted "
             "during deep work and summarised at digest_times.",
}

DEFAULT_CONDITIONING: dict[str, Any] = {
    "focus_block_minutes": 90,
    "warn_at_minutes": 60,
    "routines": [
        {"id": "calisthenics_upper", "category": "calisthenics", "duration_min": 5,
         "title": "Upper-body micro-set", "detail": "25 push-ups and a 60-second plank.",
         "voice_prompt": "90 minutes locked in. Let's knock out 25 push-ups and a 60-second plank to reset your posture."},
        {"id": "deep_squats", "category": "calisthenics", "duration_min": 4,
         "title": "Deep squat ladder", "detail": "3 rounds of 15 deep squats, 30 s rest.",
         "voice_prompt": "Legs time. Three rounds of fifteen deep squats."},
        {"id": "spine_wrist_mobility", "category": "yoga", "duration_min": 5,
         "title": "Wrist & spine decompression", "detail": "Wrist flexor stretches, cat-cows, thoracic openers.",
         "voice_prompt": "Heavy coding block finished. Time for 5 minutes of wrist decompression, cat-cows, and thoracic openers."},
        {"id": "hip_flexors", "category": "yoga", "duration_min": 4,
         "title": "Hip flexor opener", "detail": "Low lunge 45 s each side, pigeon 45 s each side.",
         "voice_prompt": "Hips are tight from sitting. Low lunges and pigeon, about four minutes."},
        {"id": "box_breathing_reset", "category": "breathwork", "duration_min": 3,
         "title": "Box breathing", "detail": "Follow the vortex: in 4, hold 4, out 4, hold 4.",
         "voice_prompt": "Queue is clear. Follow the purple vortex for 3 minutes of 4-4-4-4 box breathing."},
        {"id": "physiological_sigh", "category": "breathwork", "duration_min": 2,
         "title": "Physiological sighs", "detail": "Double inhale through the nose, long exhale. 10 rounds.",
         "voice_prompt": "Quick nervous-system reset. Ten physiological sighs."},
        {"id": "hydrate", "category": "hydration", "duration_min": 1,
         "title": "Hydration reset", "detail": "A full glass of water, eyes off the screen.",
         "voice_prompt": "Grab a full glass of water and give your eyes a break."},
    ],
    "hydration_goal_glasses": 8,
    "bedtime": "23:00",
    "wind_down_minutes": 90,
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "item"


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def parse_rules(markdown: str) -> list[str]:
    """Every bullet / numbered line in identity_and_rules.md is one rule."""
    rules = []
    for line in markdown.splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
        if m:
            rule = m.group(1).strip()
            if len(rule) >= 4 and not rule.startswith("<!--"):
                rules.append(rule[:400])
    return rules


class Preload:
    def __init__(self, data_dir: Path, daemon_dir: Path, repo_root: Path) -> None:
        self.dir = data_dir / "preload"
        self.daemon_dir = daemon_dir
        self.repo_root = repo_root
        self._lock = threading.Lock()
        self.state_file = self.dir / ".ingested.json"
        self.data: dict[str, Any] = {}
        self._mtimes: dict[str, float] = {}
        self.ensure_templates()
        self.reload()

    # ------------------------------------------------------------ seed
    def _seed_bundle(self) -> dict[str, Any]:
        for candidate in (self.dir / "seed_bundle.json", self.daemon_dir / "preload_seed.json"):
            try:
                if candidate.exists():
                    return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("Bad seed bundle %s: %s", candidate, exc)
        return {}

    def ensure_templates(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        seed = self._seed_bundle()
        profile = seed.get("user_profile", {})
        templates: dict[str, Any] = {
            "identity_and_rules.md": self._identity_template(profile),
            "workspace_manifest.json": {"workspaces": seed.get("workspaces", []), "battle_stations": []},
            "grimoire_scripts.json": {"commands": seed.get("grimoire_commands", [])},
            "comms_routing.json": DEFAULT_COMMS_ROUTING,
            "conditioning_deck.json": self._conditioning_template(seed),
        }
        for name, body in templates.items():
            path = self.dir / name
            if path.exists():
                continue
            text = body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False)
            path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
            log.info("Created preload seed %s", path)

    @staticmethod
    def _identity_template(profile: dict[str, Any]) -> str:
        alias = str(profile.get("alias") or "").strip()
        name_rule = f"- Call me {alias}.\n" if alias else "- Never use my name in anything you write or say.\n"
        style = profile.get("communication_style", "Direct, casual, zero corporate fluff.")
        return f"""# Identity & Rules for Orion

Every bullet below is a permanent rule Orion follows in every answer, draft and reply.
Edit freely — changes apply within a minute, no restart needed.

## Who I am
{name_rule}<!-- Say what you work on as a bullet, for example: - I build games and tools. -->

## How to talk to me
- {style}
- Default to the short answer; go deep only when I switch to Deep mode or ask.
- Announcements and community replies stay casual and human — no corporate fluff.

## Collaborators
<!-- Add the people you work with as bullets, for example: - Alex and Sam are close collaborators; treat their messages as high priority. -->

## Hard boundaries
- Never send a message, post or reply on my behalf without my approval.
- Never run destructive commands (deleting files, force-pushing, resetting repos) without asking first.
"""

    @staticmethod
    def _conditioning_template(seed: dict[str, Any]) -> dict[str, Any]:
        deck = copy.deepcopy(DEFAULT_CONDITIONING)
        profile = seed.get("user_profile", {})
        if profile.get("focus_block_minutes"):
            deck["focus_block_minutes"] = int(profile["focus_block_minutes"])
        known = {r["id"] for r in deck["routines"]}
        for r in seed.get("conditioning_routines", []):
            if r.get("id") not in known:
                deck["routines"].append({"title": r.get("id", "").replace("_", " ").title(), "detail": "", **r})
        return deck

    # ------------------------------------------------------------ load
    def _read_json(self, name: str, default: Any) -> Any:
        path = self.dir / name
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return copy.deepcopy(default)
        except (OSError, ValueError) as exc:
            log.warning("Ignoring invalid %s (%s) — fix the JSON and Orion will pick it up", path, exc)
            return self.data.get(name, copy.deepcopy(default))

    def changed(self) -> bool:
        mtimes = {}
        for name in FILES:
            try:
                mtimes[name] = (self.dir / name).stat().st_mtime
            except OSError:
                mtimes[name] = 0.0
        return mtimes != self._mtimes

    def reload(self) -> bool:
        """Re-read the seed files if any changed. Returns True when something changed."""
        with self._lock:
            if self._mtimes and not self.changed():
                return False
            identity = ""
            try:
                identity = (self.dir / "identity_and_rules.md").read_text(encoding="utf-8")
            except OSError:
                pass
            self.data = {
                "identity_and_rules.md": identity,
                "workspace_manifest.json": self._read_json("workspace_manifest.json", {"workspaces": []}),
                "grimoire_scripts.json": self._read_json("grimoire_scripts.json", {"commands": []}),
                "comms_routing.json": self._read_json("comms_routing.json", DEFAULT_COMMS_ROUTING),
                "conditioning_deck.json": self._read_json("conditioning_deck.json", DEFAULT_CONDITIONING),
            }
            self._mtimes = {}
            for name in FILES:
                try:
                    self._mtimes[name] = (self.dir / name).stat().st_mtime
                except OSError:
                    self._mtimes[name] = 0.0
            return True

    # ------------------------------------------------------------ views
    @property
    def rules(self) -> list[str]:
        return parse_rules(self.data.get("identity_and_rules.md", ""))

    def resolve_path(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (self.repo_root / p).resolve()

    @property
    def workspaces(self) -> list[dict[str, Any]]:
        out = []
        for ws in self.data.get("workspace_manifest.json", {}).get("workspaces", []) or []:
            if not isinstance(ws, dict) or not ws.get("path"):
                continue
            path = self.resolve_path(str(ws["path"]))
            out.append({
                "id": ws.get("id") or _slug(ws.get("name") or path.name),
                "name": ws.get("name") or path.name,
                "path": str(path),
                "exists": path.exists(),
                "start_cmd": ws.get("start_cmd") or "",
                "ports": [int(p) for p in ws.get("ports", []) if str(p).isdigit()],
                "tags": list(ws.get("tags", [])),
                "scripts": list(ws.get("scripts", [])),
                "harvested": bool(ws.get("harvested")),
            })
        return out

    @property
    def battle_stations(self) -> list[dict[str, Any]]:
        return [b for b in self.data.get("workspace_manifest.json", {}).get("battle_stations", []) or []
                if isinstance(b, dict) and b.get("name")]

    @property
    def grimoire(self) -> list[dict[str, Any]]:
        return [c for c in self.data.get("grimoire_scripts.json", {}).get("commands", []) or [] if isinstance(c, dict)]

    @property
    def comms_routing(self) -> dict[str, Any]:
        return self.data.get("comms_routing.json", DEFAULT_COMMS_ROUTING)

    @property
    def conditioning(self) -> dict[str, Any]:
        deck = self.data.get("conditioning_deck.json", DEFAULT_CONDITIONING)
        return deck if isinstance(deck, dict) else DEFAULT_CONDITIONING

    def summary(self) -> dict[str, Any]:
        return {
            "dir": str(self.dir),
            "rules": len(self.rules),
            "workspaces": len(self.workspaces),
            "grimoire": len(self.grimoire),
            "vips": len(self.comms_routing.get("vip_contacts", [])),
            "routines": len(self.conditioning.get("routines", [])),
        }

    # ------------------------------------------------------------ write-back (harvester, UI)
    def _write_json(self, name: str, data: Any) -> None:
        path = self.dir / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def save_comms_routing(self, routing: dict[str, Any]) -> None:
        """Persist UI changes (mute / unmute) back into comms_routing.json."""
        with self._lock:
            current = self._read_json("comms_routing.json", {})
            current["low_priority_threads"] = list(routing.get("low_priority_threads", []))
            self._write_json("comms_routing.json", current)

    def merge_workspaces(self, found: list[dict[str, Any]]) -> int:
        """Add harvested projects that aren't listed yet (matched by resolved path). Never edits existing ones."""
        with self._lock:
            manifest = self._read_json("workspace_manifest.json", {"workspaces": []})
            existing = manifest.setdefault("workspaces", [])
            known_paths = {str(self.resolve_path(str(w.get("path", "")))).lower() for w in existing if isinstance(w, dict)}
            known_ids = {w.get("id") for w in existing if isinstance(w, dict)}
            added = 0
            for ws in found:
                if str(Path(ws["path"])).lower() in known_paths:
                    continue
                ws = dict(ws)
                base, n = ws["id"], 2
                while ws["id"] in known_ids:
                    ws["id"] = f"{base}-{n}"
                    n += 1
                ws["harvested"] = True
                existing.append(ws)
                known_ids.add(ws["id"])
                added += 1
            if added:
                self._write_json("workspace_manifest.json", manifest)
        if added:
            self.reload()
        return added

    def merge_grimoire(self, commands: list[dict[str, Any]]) -> int:
        with self._lock:
            book = self._read_json("grimoire_scripts.json", {"commands": []})
            existing = book.setdefault("commands", [])
            known_ids = {c.get("id") for c in existing if isinstance(c, dict)}
            known_cmds = {c.get("command") for c in existing if isinstance(c, dict)}
            added = 0
            for cmd in commands:
                if cmd.get("command") in known_cmds or not cmd.get("id"):
                    continue
                cmd = dict(cmd)
                base, n = cmd["id"], 2
                while cmd["id"] in known_ids:
                    cmd["id"] = f"{base}_{n}"
                    n += 1
                existing.append(cmd)
                known_ids.add(cmd["id"])
                known_cmds.add(cmd.get("command"))
                added += 1
            if added:
                self._write_json("grimoire_scripts.json", book)
        if added:
            self.reload()
        return added

    def upsert_grimoire(self, command: dict[str, Any]) -> dict[str, Any]:
        """Add or replace one Grimoire entry by id (macro recorder, 'save as snippet', UI edits)."""
        with self._lock:
            book = self._read_json("grimoire_scripts.json", {"commands": []})
            existing = [c for c in book.setdefault("commands", []) if isinstance(c, dict)]
            command = dict(command)
            command["id"] = command.get("id") or _slug(command.get("description") or "macro")
            book["commands"] = [c for c in existing if c.get("id") != command["id"]] + [command]
            self._write_json("grimoire_scripts.json", book)
        self.reload()
        return command

    def add_battle_station(self, station: dict[str, Any]) -> None:
        with self._lock:
            manifest = self._read_json("workspace_manifest.json", {"workspaces": []})
            stations = [b for b in manifest.setdefault("battle_stations", []) if isinstance(b, dict)]
            manifest["battle_stations"] = [b for b in stations if b.get("name") != station.get("name")] + [station]
            self._write_json("workspace_manifest.json", manifest)
        self.reload()

    # ------------------------------------------------------------ ingestion bookkeeping
    def ingest_state(self) -> dict[str, str]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def mark_ingested(self, key: str, digest: str) -> None:
        state = self.ingest_state()
        state[key] = digest
        try:
            self.state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")
        except OSError:
            pass

    def identity_digest(self) -> str:
        return _sha(self.dir / "identity_and_rules.md")


async def ingest_preload(preload: Preload, memory: Any) -> dict[str, Any]:
    """Push identity rules (Tier-0) and the identity document into memory when they changed."""
    digest = preload.identity_digest()
    state = preload.ingest_state()
    result = {"rules": 0, "skipped": True}
    if digest and state.get("identity") != digest:
        rules = preload.rules
        await memory.replace_rules(rules, source="preload", tier=0)
        identity = preload.data.get("identity_and_rules.md", "")
        if identity.strip():
            await memory.ingest_memory("preload", identity[:7000])
        preload.mark_ingested("identity", digest)
        result = {"rules": len(rules), "skipped": False}
        log.info("Preload: %d Tier-0 rules loaded from identity_and_rules.md", len(rules))
    return result


__all__ = ["Preload", "ingest_preload", "parse_rules", "FILES"]
