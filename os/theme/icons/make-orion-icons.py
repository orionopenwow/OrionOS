#!/usr/bin/env python3
"""Draw the "Orion" icon theme's folder and place icons (Art Update §4: large Orion folders in violet, blue and cyan
gradients; sidebar Places each with its own colour). Original artwork, CC0. Everything else is inherited from
Papirus-Dark (then Adwaita, hicolor).

    python3 os/theme/icons/make-orion-icons.py      -> os/theme/icons/Orion/scalable/{places,devices}/*.svg
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Orion")

# gradient stops (top-left -> bottom-right), back plate colour
FOLDER = ("#8E72FF", "#6E8BFF", "#00D2F0")          # the generic Orion folder: violet -> blue -> cyan
PLACES = {
    "home":      ("#B09CFF", "#7C5CFF", "#6247E0"),   # violet (Accretion)
    "desktop":   ("#9DB0FF", "#6E8BFF", "#4A63D9"),   # blue
    "documents": ("#7FF2FF", "#00C8E8", "#0092B8"),   # cyan (Photon)
    "download":  ("#9BFFD0", "#3FE69C", "#1FA874"),   # aurora mint
    "music":     ("#FF9DBB", "#FF5C8A", "#D63A6A"),   # rose
    "pictures":  ("#FFE1AA", "#FFC66B", "#E39B33"),   # ember
    "videos":    ("#D2C6FF", "#9D85FF", "#7457E6"),   # soft violet
    "projects":  ("#00E5FF", "#6E8BFF", "#7C5CFF"),   # photon -> violet (Orion's projects/)
    "templates": ("#C9D0FF", "#8C96D8", "#5E68A8"),   # starlight
    "public":    ("#9DB0FF", "#00C8E8", "#0092B8"),   # blue -> cyan
    "remote":    ("#7FF2FF", "#6E8BFF", "#4A63D9"),   # cyan -> blue
    "captures":  ("#FFB0C8", "#9D85FF", "#6E8BFF"),   # rose -> violet (Orion's Captures/)
}

E = 'fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"'
EMBLEMS = {
    "home": f'<path {E} d="M24 41.5 32 34l8 7.5M26.5 39.5V47h11v-7.5"/>',
    "desktop": f'<rect {E} x="23" y="33" width="18" height="11.5" rx="2"/><path {E} d="M29 48.5h6M32 44.5v4"/>',
    "documents": f'<path {E} d="M25 35h14M25 40.5h14M25 46h9"/>',
    "download": f'<path {E} d="M32 32.5v11M27 39l5 5 5-5M24.5 48h15"/>',
    "music": f'<path {E} d="M30 45.5V33.5l9-2v11"/><circle cx="27.6" cy="45.6" r="2.6" fill="#FFFFFF"/><circle cx="36.6" cy="42.6" r="2.6" fill="#FFFFFF"/>',
    "pictures": f'<path {E} d="M23.5 47l6-7 4.5 5 3-3 3.5 5z"/><circle cx="37.5" cy="35.5" r="2.4" fill="#FFFFFF"/>',
    "videos": '<path fill="#FFFFFF" d="M28.5 33.5v14l11.5-7z"/>',
    "projects": f'<path {E} d="M27.5 35l-5 5.2 5 5.3M36.5 35l5 5.2-5 5.3M33.5 33.5l-3 13.5"/>',
    "templates": f'<rect {E} x="24.5" y="33" width="15" height="14" rx="2" stroke-dasharray="3 2.4"/>',
    "public": f'<circle cx="26" cy="40.5" r="2.6" fill="#FFFFFF"/><circle cx="38" cy="34.5" r="2.6" fill="#FFFFFF"/><circle cx="38" cy="46.5" r="2.6" fill="#FFFFFF"/><path {E} d="M28.4 39.3l7.2-3.6M28.4 41.7l7.2 3.6"/>',
    "remote": f'<circle {E} cx="32" cy="40" r="7.5"/><path {E} d="M24.5 40h15M32 32.5c-3.6 4.4-3.6 10.6 0 15M32 32.5c3.6 4.4 3.6 10.6 0 15"/>',
    "captures": f'<path {E} d="M24 36.5v-2.5a2 2 0 0 1 2-2h2.5M35.5 32h2.5a2 2 0 0 1 2 2v2.5M40 43.5v2.5a2 2 0 0 1-2 2h-2.5M28.5 48h-2.5a2 2 0 0 1-2-2v-2.5"/><circle cx="32" cy="40" r="3" fill="#FFFFFF"/>',
}


def folder(name, colours, emblem="", open_=False):
    c1, c2, c3 = colours
    gid = name.replace("-", "")
    # back plate with a tab, then a glassy front plate with a lit top edge and a faint orbit ring
    front = ('<path d="M3.5 27.5a4 4 0 0 1 4-4h49a4 4 0 0 1 4 4.6l-2.4 21.5a5 5 0 0 1-5 4.4H10.9a5 5 0 0 1-5-4.4z"'
             if open_ else '<rect x="4" y="20" width="56" height="34" rx="5.5"')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="f{gid}" x1="6" y1="18" x2="58" y2="56" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="{c1}"/><stop offset="0.55" stop-color="{c2}"/><stop offset="1" stop-color="{c3}"/>
    </linearGradient>
    <linearGradient id="b{gid}" x1="0" y1="8" x2="0" y2="30" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="{c3}"/><stop offset="1" stop-color="#262D50"/>
    </linearGradient>
    <radialGradient id="g{gid}" cx="18" cy="22" r="40" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.35"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <path d="M6 14.5A4.5 4.5 0 0 1 10.5 10h13.2a3 3 0 0 1 2.1.9l4.4 4.4a3 3 0 0 0 2.1.9h21.2a4.5 4.5 0 0 1 4.5 4.5V30H6z" fill="url(#b{gid})"/>
  <rect x="9" y="15" width="46" height="14" rx="2" fill="#D8DEFF" opacity="0.22"/>
  {front} fill="url(#f{gid})"/>
  {front} fill="url(#g{gid})"/>
  <path d="M9.5 21.2h45" stroke="#FFFFFF" stroke-opacity="0.55" stroke-width="1.2" stroke-linecap="round"/>
  <ellipse cx="46" cy="47" rx="10" ry="3.2" fill="none" stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="1" transform="rotate(-14 46 47)"/>
  <circle cx="54.6" cy="44.2" r="1.1" fill="#FFFFFF" fill-opacity="0.55"/>
  <g opacity="0.92">{emblem}</g>
</svg>
'''


def trash(full=False):
    paper = ('<path d="M23 18l4-9 5 4 6-6 4 11z" fill="#FFC66B" opacity="0.9"/>' if full else "")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="t" x1="16" y1="16" x2="50" y2="58" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#3A4170"/><stop offset="1" stop-color="#151A30"/>
    </linearGradient>
  </defs>
  {paper}
  <path d="M15 22h34l-3.2 30.5a4 4 0 0 1-4 3.5H22.2a4 4 0 0 1-4-3.5z" fill="url(#t)" stroke="#7C5CFF" stroke-opacity="0.7" stroke-width="1.4"/>
  <rect x="12" y="17" width="40" height="6" rx="3" fill="#262D50" stroke="#9D85FF" stroke-opacity="0.8" stroke-width="1.4"/>
  <path d="M26 29v20M32 29v20M38 29v20" stroke="#D8DEFF" stroke-opacity="0.45" stroke-width="2" stroke-linecap="round"/>
</svg>
'''


def vault():
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="v" x1="8" y1="8" x2="56" y2="56" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#9D85FF"/><stop offset="1" stop-color="#3A2B99"/>
    </linearGradient>
  </defs>
  <rect x="8" y="10" width="48" height="44" rx="9" fill="url(#v)"/>
  <rect x="8" y="10" width="48" height="44" rx="9" fill="none" stroke="#FFFFFF" stroke-opacity="0.25"/>
  <circle cx="32" cy="32" r="12.5" fill="#05070E" stroke="#00E5FF" stroke-width="2.4"/>
  <circle cx="32" cy="32" r="5" fill="#05070E" stroke="#D8DEFF" stroke-width="2"/>
  <path d="M32 19.5v4M32 40.5v4M19.5 32h4M40.5 32h4" stroke="#00E5FF" stroke-width="2" stroke-linecap="round"/>
  <rect x="12" y="52" width="9" height="4" rx="1.5" fill="#262D50"/><rect x="43" y="52" width="9" height="4" rx="1.5" fill="#262D50"/>
</svg>
'''


def usb():
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="u" x1="16" y1="18" x2="48" y2="60" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#6E8BFF"/><stop offset="1" stop-color="#7C5CFF"/>
    </linearGradient>
  </defs>
  <rect x="24" y="4" width="16" height="15" rx="2" fill="#C3C9EE"/>
  <rect x="28" y="8" width="3" height="4" fill="#3A4170"/><rect x="33" y="8" width="3" height="4" fill="#3A4170"/>
  <rect x="18" y="18" width="28" height="42" rx="7" fill="url(#u)"/>
  <circle cx="32" cy="40" r="8" fill="#05070E"/>
  <circle cx="32" cy="40" r="8" fill="none" stroke="#00E5FF" stroke-width="2"/>
  <circle cx="35" cy="37" r="1.4" fill="#FFFFFF"/>
</svg>
'''


def main():
    places = os.path.join(OUT, "scalable", "places")
    devices = os.path.join(OUT, "scalable", "devices")
    os.makedirs(places, exist_ok=True)
    os.makedirs(devices, exist_ok=True)
    files = {}
    files["folder"] = folder("folder", FOLDER)
    files["folder-open"] = folder("folder-open", FOLDER, open_=True)
    for alias in ("inode-directory", "folder-blue", "folder-violet"):
        files[alias] = files["folder"]
    names = {
        "home": ["user-home", "folder-home", "go-home"],   # Thunar's sidebar Home uses go-home
        "desktop": ["user-desktop", "folder-desktop"],
        "documents": ["folder-documents", "folder-text"],
        "download": ["folder-download", "folder-downloads"],
        "music": ["folder-music", "folder-sound"],
        "pictures": ["folder-pictures", "folder-images", "folder-image"],
        "videos": ["folder-videos", "folder-video"],
        "projects": ["folder-projects", "folder-development", "folder-code"],
        "templates": ["folder-templates"],
        "public": ["folder-publicshare", "folder-public"],
        "remote": ["folder-remote", "folder-network"],
        "captures": ["folder-captures", "folder-screenshots"],
    }
    for key, aliases in names.items():
        svg = folder(key, PLACES[key], EMBLEMS[key])
        for a in aliases:
            files[a] = svg
    files["user-trash"] = trash(False)
    files["user-trash-full"] = trash(True)
    for n, svg in files.items():
        with open(os.path.join(places, n + ".svg"), "w") as f:
            f.write(svg)
    for n, svg in {"orion-vault": vault(), "orion-usb": usb()}.items():
        with open(os.path.join(devices, n + ".svg"), "w") as f:
            f.write(svg)
    print(f"{len(files)} place icons, 2 device icons -> {OUT}")


if __name__ == "__main__":
    main()
