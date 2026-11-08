"""The Orion layer, swap history and the boot guard (docs/ORIONOS_AFTER_1_0_PLAN.md §7, Phase 8 "Living Orion").

Standard library only: main.py calls activate() before it imports anything else, so a broken self-made change can
be rolled back even when the rest of the daemon no longer imports.

Two ways a self-made change goes live (self_program.py picks one):

  checkout mode   Orion runs from a writable git checkout (developer machines, Windows, ORION_REPO_ROOT).
                  An approved change is written into the checkout; the files it replaced are kept in
                  <data>/selfprog/changes/<id>/backup so it can be put back.
  layer mode      Orion runs from the read-only OrionOS image (/opt/orion/daemon). The image is never touched.
                  Self-made changes live in the "Orion layer": <data>/orion-layer/gens/<n>/apps/daemon is a full
                  copy of the daemon with the changes in it, and <data>/orion-layer/current holds the number of the
                  generation in use (0 = none, run the image as it is). At start, activate() re-runs the daemon from
                  the current generation (one exec, same process id, so systemd doesn't notice). Undo = point
                  `current` back at the previous generation and restart. Screen (HUD) changes need the source
                  checkout: the image's HUD is a packaged app with its screens built in.

  ORION_LAYER_MODE=1 / 0 forces a mode; ORION_LAYER_DIR moves the layer.

Swap history (<data>/selfprog/history.json, newest last, the last HISTORY_KEEP kept) and probation
(<data>/selfprog/probation.json): after a swap that changed the daemon, the next start is on probation for
PROBATION_S (2 minutes). The boot guard here rolls the swap back if the daemon starts again before it ever answered
an authenticated /health (it crashed or hung while starting); self_program.ProbationWatch rolls it back if /health
fails during the 2 minutes.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROBATION_S = 120
HISTORY_KEEP = 12
GENS_KEEP = 5
COPY_SKIP = {"__pycache__", ".venv", "venv", ".pytest_cache", "node_modules"}


# =====================================================================================  where things live
def data_dir(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(os.path.expanduser(os.environ.get("ORION_DATA_DIR", "") or "~/.orion"))


def selfprog_dir(dd: Optional[Path] = None) -> Path:
    return data_dir(dd) / "selfprog"


def layer_dir(dd: Optional[Path] = None) -> Path:
    explicit = os.environ.get("ORION_LAYER_DIR", "").strip()
    return Path(os.path.expanduser(explicit)) if explicit else data_dir(dd) / "orion-layer"


def layer_mode(repo_root: Path, daemon_dir: Path) -> bool:
    """True when the running copy can't be written (the OrionOS image), so changes go into the Orion layer."""
    forced = os.environ.get("ORION_LAYER_MODE", "").strip()
    if forced in ("0", "1"):
        return forced == "1"
    if (Path(repo_root) / ".git").exists() and (Path(repo_root) / "apps" / "daemon").is_dir():
        return not os.access(str(daemon_dir), os.W_OK)
    return True


def _read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# =====================================================================================  layer generations
def current_gen(dd: Optional[Path] = None) -> int:
    try:
        return max(0, int((layer_dir(dd) / "current").read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return 0


def gen_dir(dd: Optional[Path], n: int) -> Path:
    return layer_dir(dd) / "gens" / str(int(n))


def set_gen(dd: Optional[Path], n: int) -> None:
    p = layer_dir(dd) / "current"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("current.tmp")
    tmp.write_text(str(int(n)), encoding="utf-8")
    os.replace(tmp, p)


def _copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in COPY_SKIP or n.endswith(".pyc")])


def new_gen(dd: Optional[Path], base_root: Path, files: dict[str, bytes]) -> int:
    """A new generation = a full copy of `base_root` (a folder laid out like the repo: apps/daemon/...) with
    `files` ({repo-relative path: new bytes}) written over it. Does NOT make it current."""
    gens = layer_dir(dd) / "gens"
    gens.mkdir(parents=True, exist_ok=True)
    taken = [int(p.name) for p in gens.iterdir() if p.name.isdigit()]
    n = max(taken + [current_gen(dd)]) + 1
    dest = gen_dir(dd, n)
    tmp = gens / f".{n}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    _copytree(Path(base_root) / "apps" / "daemon", tmp / "apps" / "daemon")
    for rel, body in files.items():
        rel = rel.replace("\\", "/")
        if not rel.startswith("apps/daemon/") or ".." in Path(rel).parts:
            raise ValueError(f"only daemon files can go into the Orion layer: {rel}")
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    os.replace(tmp, dest)
    return n


def prune_gens(dd: Optional[Path], keep: int = GENS_KEEP) -> None:
    gens = layer_dir(dd) / "gens"
    if not gens.is_dir():
        return
    cur = current_gen(dd)
    nums = sorted(int(p.name) for p in gens.iterdir() if p.name.isdigit())
    for n in nums[:-keep]:
        if n != cur:
            shutil.rmtree(gens / str(n), ignore_errors=True)


# =====================================================================================  history + probation
def history(dd: Optional[Path] = None) -> list[dict[str, Any]]:
    h = _read_json(selfprog_dir(dd) / "history.json", {})
    swaps = h.get("swaps") if isinstance(h, dict) else None
    return swaps if isinstance(swaps, list) else []


def save_history(dd: Optional[Path], swaps: list[dict[str, Any]]) -> None:
    _write_json(selfprog_dir(dd) / "history.json", {"swaps": swaps[-HISTORY_KEEP:]})


def update_swap(dd: Optional[Path], swap_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    swaps = history(dd)
    for s in swaps:
        if s.get("id") == swap_id:
            s.update(fields)
            save_history(dd, swaps)
            return s
    return None


def probation(dd: Optional[Path] = None) -> Optional[dict[str, Any]]:
    p = _read_json(selfprog_dir(dd) / "probation.json", None)
    return p if isinstance(p, dict) and p.get("swap_id") else None


def set_probation(dd: Optional[Path], p: Optional[dict[str, Any]]) -> None:
    path = selfprog_dir(dd) / "probation.json"
    if p is None:
        try:
            path.unlink()
        except OSError:
            pass
    else:
        _write_json(path, p)


def start_probation(dd: Optional[Path], swap: dict[str, Any], *, needs_daemon: bool, needs_ui: bool,
                    now: Optional[float] = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    p = {"swap_id": swap["id"], "started": now, "deadline": now + PROBATION_S, "needs_daemon": bool(needs_daemon),
         "needs_ui": bool(needs_ui), "boots": 0, "healthy_seen": False, "ui_ok": False}
    set_probation(dd, p)
    return p


# =====================================================================================  rollback
def rollback(dd: Optional[Path], swap_id: str, reason: str, *, now: Optional[float] = None) -> dict[str, Any]:
    """Put back what a swap replaced. Works without the rest of the daemon (the boot guard uses it)."""
    swaps = history(dd)
    swap = next((s for s in swaps if s.get("id") == swap_id), None)
    if swap is None:
        raise KeyError(swap_id)
    if swap.get("status") == "rolled_back":
        return swap
    if swap.get("mode") == "layer":
        set_gen(dd, int(swap.get("prev_gen") or 0))
    else:
        root = Path(swap["root"])
        backup = Path(swap["backup"])
        for f in swap.get("files", []):
            rel = str(f["path"]).replace("\\", "/")
            if ".." in Path(rel).parts:
                continue
            target = root / rel
            if f.get("action") == "create":
                try:
                    target.unlink()
                except OSError:
                    pass
            else:
                src = backup / rel
                if src.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target)
    swap.update({"status": "rolled_back", "rolled_back_at": time.time() if now is None else now,
                 "rollback_reason": reason[:300]})
    save_history(dd, swaps)
    p = probation(dd)
    if p and p.get("swap_id") == swap_id:
        set_probation(dd, None)
    return swap


def boot_guard(dd: Optional[Path] = None, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """Called once per daemon start, before anything else is imported. Returns the swap it rolled back, if any."""
    p = probation(dd)
    if not p or not p.get("needs_daemon"):
        return None
    now = time.time() if now is None else now
    late = now > float(p.get("deadline", 0)) + 30
    if (int(p.get("boots", 0)) >= 1 and not p.get("healthy_seen")) or (late and not p.get("healthy_seen")):
        why = "Orion didn't start properly after the change, so it was put back automatically."
        try:
            return rollback(dd, str(p["swap_id"]), why, now=now)
        except KeyError:
            set_probation(dd, None)
            return None
    if late:                       # it was healthy and the window is long over: keep it
        update_swap(dd, str(p["swap_id"]), status="kept")
        set_probation(dd, None)
        return None
    p["boots"] = int(p.get("boots", 0)) + 1
    p["healthy_seen"] = False
    set_probation(dd, p)
    return None


# =====================================================================================  start-up hook
def activate(main_file: Optional[str] = None, dd: Optional[Path] = None) -> None:
    """main.py's first call. `main_file` is main.py's __file__ when it runs as the program (None on import)."""
    if main_file is None or os.environ.get("ORION_IMPORT_CHECK") == "1":
        return
    try:
        rolled = boot_guard(dd)
        if rolled:
            sys.stderr.write(f"[orion] {rolled.get('rollback_reason')}\n")
    except Exception as exc:  # never stop Orion from starting
        sys.stderr.write(f"[orion] boot guard skipped: {exc}\n")
    here = Path(main_file).resolve()
    base = os.environ.get("ORION_LAYER_BASE") or ""
    n = current_gen(dd)
    in_layer = layer_dir(dd).resolve() in here.parents
    if not n and not in_layer:
        return                      # no layer in use: run as is
    if not base:
        if in_layer:
            return                  # started from a generation by hand; nothing to compare against
        base = str(here.parent)
    target = (gen_dir(dd, n) / "apps" / "daemon" / "main.py") if n else Path(base) / "main.py"
    if not target.exists() or target.resolve() == here:
        return
    if os.name == "nt":
        sys.stderr.write("[orion] the Orion layer is only used on OrionOS; running the installed copy\n")
        return
    os.environ["ORION_LAYER_BASE"] = base
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(str(target.parent))
    os.execv(sys.executable, [sys.executable, str(target)] + sys.argv[1:])
