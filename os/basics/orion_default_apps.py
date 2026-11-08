#!/usr/bin/env python3
"""OrionOS default apps: the "Try with Craft" switch (docs/ORIONOS_COMPLETE_PLAN.md Part 2).

The mature apps stay the default while the Craft apps are alpha (os/image/os-apps.sh writes them). For each file
type there is one switch, OFF unless you turn it on:

  pdf     PDFs open in PdfCraft     (instead of the simple PDF viewer)
  images  pictures open in PhotoCraft (instead of the image viewer)

Turning it on changes only your own ~/.config/mimeapps.list: the Craft app becomes the default for that type and
the previous app is kept as the second choice in "Open With". What was there before is remembered in
~/.config/orion/try-with-craft.json, so turning it off puts back exactly that (or, if you had no choice of your
own, removes the line so the system default applies again). It only turns on when the Craft app is installed.

Command line:
  orion_default_apps.py status [--json]
  orion_default_apps.py on pdf|images
  orion_default_apps.py off pdf|images
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

KINDS: dict[str, dict[str, Any]] = {
    "pdf": {"app": "PdfCraft", "flatpak_id": "ai.storyteller.pdfcraft", "fallback": "org.gnome.Evince.desktop",
            "label": "PDFs",
            "mimes": ["application/pdf", "application/x-pdf"]},
    "images": {"app": "PhotoCraft", "flatpak_id": "ai.storyteller.photocraft", "fallback": "org.gnome.eog.desktop",
               "label": "Pictures",
               "mimes": ["image/png", "image/jpeg", "image/pjpeg", "image/gif", "image/webp", "image/bmp",
                         "image/tiff", "image/x-tga", "image/vnd.adobe.photoshop", "image/x-photoshop"]},
}
DEFAULT_SECTION = "Default Applications"
ADDED_SECTION = "Added Associations"


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")


def mimeapps_path() -> Path:
    return config_home() / "mimeapps.list"


def state_path() -> Path:
    return config_home() / "orion" / "try-with-craft.json"


def application_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local/share")
    dirs = [data_home / "applications", data_home / "flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications")]
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(Path(d) / "applications")
    return dirs


def desktop_file(kind: str) -> str:
    return KINDS[kind]["flatpak_id"] + ".desktop"


def craft_installed(kind: str) -> bool:
    name = desktop_file(kind)
    return any((d / name).is_file() for d in application_dirs())


# =====================================================================================  mimeapps.list editing
def parse(text: str) -> list[tuple[Optional[str], str]]:
    """Lines tagged with their section (None before the first header); comments and order are kept."""
    out: list[tuple[Optional[str], str]] = []
    section: Optional[str] = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        out.append((section, line))
    return out


def get(lines: list[tuple[Optional[str], str]], section: str, key: str) -> Optional[str]:
    for sec, line in lines:
        if sec == section and "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return None


def put(lines: list[tuple[Optional[str], str]], section: str, key: str,
        value: Optional[str]) -> list[tuple[Optional[str], str]]:
    """Set (or with value None remove) key in section, adding the section at the end when it's missing."""
    out: list[tuple[Optional[str], str]] = []
    done = False
    for sec, line in lines:
        if sec == section and "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() == key:
            if value is not None and not done:
                out.append((sec, f"{key}={value}"))
                done = True
            continue
        out.append((sec, line))
    if value is None or done:
        return out
    # insert after the section's last key line (or create the section)
    idx = None
    for i, (sec, _line) in enumerate(out):
        if sec == section:
            idx = i
    if idx is None:
        if out and out[-1][1].strip():
            out.append((None, ""))
        out.append((section, f"[{section}]"))
        out.append((section, f"{key}={value}"))
    else:
        while idx > 0 and not out[idx][1].strip() and out[idx][0] == section:
            idx -= 1
        out.insert(idx + 1, (section, f"{key}={value}"))
    return out


def render(lines: list[tuple[Optional[str], str]]) -> str:
    return "\n".join(line for _s, line in lines) + "\n"


def _apps(value: Optional[str]) -> list[str]:
    return [a for a in (value or "").split(";") if a.strip()]


# =====================================================================================  state + switch
def _read_state() -> dict[str, Any]:
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".orion-new")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def status() -> dict[str, dict[str, Any]]:
    st = _read_state()
    return {k: {"on": bool((st.get(k) or {}).get("on")), "app": v["app"], "label": v["label"],
                "installed": craft_installed(k)} for k, v in KINDS.items()}


def set_try_craft(kind: str, on: bool) -> tuple[bool, str]:
    if kind not in KINDS:
        return False, "Pick pdf or images."
    info = KINDS[kind]
    st = _read_state()
    cur = st.get(kind) or {}
    path = mimeapps_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    lines = parse(text)
    craft = desktop_file(kind)
    if on:
        if cur.get("on"):
            return True, f"{info['label']} already open in {info['app']}."
        if not craft_installed(kind):
            return False, f"{info['app']} isn't installed. Get it in Get apps (Orion Studio) first."
        previous = {m: get(lines, DEFAULT_SECTION, m) for m in info["mimes"]}
        previous_added = {m: get(lines, ADDED_SECTION, m) for m in info["mimes"]}
        for m in info["mimes"]:
            second = _apps(previous[m]) or [info["fallback"]]
            lines = put(lines, DEFAULT_SECTION, m, ";".join([craft] + [a for a in second if a != craft]) + ";")
            added = [craft] + [a for a in second + _apps(previous_added[m]) if a != craft]
            lines = put(lines, ADDED_SECTION, m, ";".join(dict.fromkeys(added)) + ";")
        st[kind] = {"on": True, "previous": previous, "previous_added": previous_added}
        msg = f"{info['label']} now open in {info['app']} (alpha). The usual app is still in Open With."
    else:
        if not cur.get("on"):
            return True, f"{info['label']} already open in the usual app."
        for m in info["mimes"]:
            # put back only lines that still say what we wrote (a later choice of yours wins)
            if _apps(get(lines, DEFAULT_SECTION, m))[:1] == [craft]:
                lines = put(lines, DEFAULT_SECTION, m, (cur.get("previous") or {}).get(m))
            if _apps(get(lines, ADDED_SECTION, m))[:1] == [craft]:
                lines = put(lines, ADDED_SECTION, m, (cur.get("previous_added") or {}).get(m))
        st[kind] = {"on": False}
        msg = f"{info['label']} open in the usual app again."
    _write(path, render(lines))
    _write(state_path(), json.dumps(st, indent=2))
    return True, msg


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        st = status()
        if "--json" in argv:
            print(json.dumps(st))
        else:
            for k, v in st.items():
                state = "on" if v["on"] else "off"
                extra = "" if v["installed"] else f" ({v['app']} not installed)"
                print(f"{v['label']}: try with {v['app']} is {state}{extra}")
        return 0
    if cmd in ("on", "off") and len(argv) > 2:
        ok, msg = set_try_craft(argv[2], cmd == "on")
        print(msg)
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
