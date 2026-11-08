"""Export-ForOrion.ps1 (the "Orion move kit"): most-used apps, Chrome extensions -> Firefox, bookmarks -> Firefox's
import format, search engines and Tampermonkey userscripts read out of LevelDB files. Everything runs against a
synthetic Chrome profile made here (no real data). Needs pwsh (PowerShell 7) for the functional tests; the parse,
CRLF and ASCII checks need nothing.

    python3 -m pytest os/windows/tests -q
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WIN = REPO / "os" / "windows"
EXPORT = WIN / "Export-ForOrion.ps1"
MOVE = WIN / "Move-ToOrion.ps1"
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="pwsh isn't installed")
TM = "dhdgffkkebhmkfjojejmpbldmpobfkfo"


# ============================================================================ LevelDB fixture writers
def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def snappy(data: bytes) -> bytes:
    """A small, valid Snappy compressor (literals + 2-byte-offset copies), enough to test the reader."""
    out = bytearray(varint(len(data)))
    lit = bytearray()

    def flush():
        nonlocal lit
        while lit:
            chunk, lit = lit[:256], lit[256:]
            n = len(chunk) - 1
            out.extend(bytes([n << 2]) if n < 60 else bytes([60 << 2, n]))
            out.extend(chunk)

    table: dict[bytes, int] = {}
    i = 0
    while i < len(data):
        key = data[i:i + 4]
        j = table.get(key) if len(key) == 4 else None
        table[key] = i
        if j is not None and i - j < 65536:
            ln = 4
            while i + ln < len(data) and data[j + ln] == data[i + ln] and ln < 64:
                ln += 1
            flush()
            out.extend(bytes([((ln - 1) << 2) | 2]) + struct.pack("<H", i - j))
            i += ln
        else:
            lit.append(data[i])
            i += 1
    flush()
    return bytes(out)


def block(entries: list[tuple[bytes, bytes]]) -> bytes:
    b = bytearray()
    for k, v in entries:
        b += varint(0) + varint(len(k)) + varint(len(v)) + k + v
    b += struct.pack("<I", 0) + struct.pack("<I", 1)
    return bytes(b)


def ldb_table(entries: list[tuple[bytes, bytes]]) -> bytes:
    f = bytearray()
    data = snappy(block(entries))
    off = 0
    f += data + b"\x01" + b"\0\0\0\0"
    meta = block([])
    meta_off = len(f)
    f += meta + b"\x00" + b"\0\0\0\0"
    index = block([(b"\xff", varint(off) + varint(len(data)))])
    idx_off = len(f)
    f += index + b"\x00" + b"\0\0\0\0"
    footer = varint(meta_off) + varint(len(meta)) + varint(idx_off) + varint(len(index))
    footer += b"\0" * (40 - len(footer)) + struct.pack("<Q", 0xDB4775248B80FB57)
    return bytes(f + footer)


def log_file(records: list[bytes]) -> bytes:
    """LevelDB .log: 32 KiB blocks, records split into FIRST/MIDDLE/LAST fragments across blocks."""
    B = 32768
    out = bytearray()
    for rec in records:
        first = True
        while True:
            left = B - (len(out) % B)
            if left < 7:
                out += b"\0" * left
                left = B
            room = left - 7
            frag, rec = rec[:room], rec[room:]
            last = not rec
            typ = 1 if first and last else 2 if first else 4 if last else 3
            out += b"\0\0\0\0" + struct.pack("<H", len(frag)) + bytes([typ]) + frag
            first = False
            if last:
                break
    return bytes(out)


def batch(key: bytes, value: bytes) -> bytes:
    return struct.pack("<QI", 7, 1) + b"\x01" + varint(len(key)) + key + varint(len(value)) + value


def userscript(name: str, body: str = "console.log('hi');", ns: str = "local") -> str:
    return (f"// ==UserScript==\n// @name         {name}\n// @namespace    {ns}\n// @version      1.0\n"
            f"// @match        https://example.com/*\n// ==/UserScript==\n{body}\n")


# ============================================================================ the synthetic Windows user
@pytest.fixture()
def fake_user(tmp_path):
    ud = tmp_path / "Chrome" / "User Data"
    prof = ud / "Default"
    (prof).mkdir(parents=True)
    (ud / "Local State").write_text(json.dumps({"profile": {"info_cache": {"Default": {"name": "Work"}}}}))
    (prof / "Bookmarks").write_text(json.dumps({"roots": {
        "bookmark_bar": {"type": "folder", "name": "Bookmarks bar", "children": [
            {"type": "url", "name": "Example & Co", "url": "https://example.com/?a=1&b=2"},
            {"type": "url", "name": "bad", "url": "javascript:alert(1)"},
            {"type": "folder", "name": "Dev <tools>", "children": [
                {"type": "url", "name": "", "url": "https://dev.example.org/"}]}]},
        "other": {"type": "folder", "name": "Other", "children": []}}}))
    (prof / "Preferences").write_text(json.dumps({"default_search_provider_data": {"template_url_data": {
        "short_name": "Startpage", "keyword": "sp", "url": "https://www.startpage.com/do/search?q={searchTerms}"}}}))
    (prof / "Web Data").write_bytes(b"SQLite format 3\0junk\0https://search.example.net/find?q={searchTerms}&x=1\0"
                                    b"\0{google:baseURL}search?q={searchTerms}\0https://www.google.com/search?q={searchTerms}{google:RLZ}\0")
    # never read: a "Login Data" file with a fake secret, to prove it doesn't end up in the kit
    (prof / "Login Data").write_bytes(b"password=" + b"not-" + b"a-real-one")
    ext = prof / "Extensions"

    def add_ext(eid, name, version="1.0", locale=None, theme=False):
        d = ext / eid / f"{version}_0"
        d.mkdir(parents=True)
        man = {"name": name, "version": version, "manifest_version": 3}
        if theme:
            man["theme"] = {"colors": {}}
        if locale:
            man["default_locale"] = "en"
            (d / "_locales" / "en").mkdir(parents=True)
            (d / "_locales" / "en" / "messages.json").write_text(json.dumps({"extName": {"message": locale}}))
        (d / "manifest.json").write_text(json.dumps(man))
    add_ext("cjpalhdlnbpafiamejdnhcphjbkeiagm", "uBlock Origin", "1.60.0")
    (ext / "cjpalhdlnbpafiamejdnhcphjbkeiagm" / "1.9.0_0").mkdir()          # an older version folder, no manifest
    add_ext(TM, "__MSG_extName__", "5.3", locale="Tampermonkey")
    add_ext("bmnlcjabgnpnenekpadlanbbkooimhnj", "Honey")
    add_ext("nmmhkkegccagdldgiimedpiccmgmieda", "Chrome Web Store Payments")
    add_ext("aaaabbbbccccddddeeeeffffgggghhhh", "Some Unknown Helper")
    add_ext("ppppoooonnnnmmmmllllkkkkjjjjiiii", "Night Theme", theme=True)
    # Tampermonkey storage: one script in a compressed .ldb table (JSON string), one fragmented across .log blocks,
    # one stored by IndexedDB as a V8 UTF-16 string; and a GM value with a fake token that must never be copied.
    les = prof / "Local Extension Settings" / TM
    les.mkdir(parents=True)
    s1 = userscript("Table Script", "var x = 'a\"b';\n" * 40)
    (les / "000005.ldb").write_bytes(ldb_table([(b"@source#1", json.dumps(s1).encode()),
                                                (b"@value#1", json.dumps({"token": "tok-" + "fake"}).encode())]))
    s2 = userscript("Log Script", "// long\n" + ("y" * 70000))
    (les / "000007.log").write_bytes(log_file([batch(b"@source#2", json.dumps(s2).encode())]))
    idb = prof / "IndexedDB" / f"chrome-extension_{TM}_0.indexeddb.leveldb"
    idb.mkdir(parents=True)
    s3 = userscript("Wide Script", "alert('\u00e9');")
    raw16 = s3.encode("utf-16-le")
    (idb / "000003.log").write_bytes(log_file([batch(b"\x00\x01k", b"\xff\x0f" + b"c" + varint(len(raw16)) + raw16)]))
    # Tampermonkey's own zip export: the .user.js is taken, the storage file never
    zp = tmp_path / "tampermonkey-backup-chrome.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Zip Script.user.js", userscript("Zip Script"))
        z.writestr("Table Script.user.js", userscript("Table Script", "// the zip copy wins"))
        z.writestr("Zip Script.storage.json", json.dumps({"data": {"apikey": "k-" + "fake"}}))
        z.writestr("Zip Script.options.json", "{}")
    return {"root": tmp_path, "ud": ud, "prof": prof, "zip": zp}


def run_ps(script: str, timeout: int = 240) -> subprocess.CompletedProcess:
    full = f"$ErrorActionPreference='Stop'; . '{EXPORT}'; " + script
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", full], capture_output=True, text=True,
                          timeout=timeout)


# ============================================================================ tests
def test_files_are_crlf_ascii_and_51_safe():
    for f in (EXPORT, MOVE):
        raw = f.read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), f"{f.name} must be CRLF"
        assert all(b < 128 for b in raw), f"{f.name} must be plain ASCII"
        code = re.sub(r"<#.*?#>", "", "\n".join(l for l in f.read_text().splitlines() if not l.lstrip().startswith("#")),
                      flags=re.S)
        code = re.sub(r"@'.*?'@", "", code, flags=re.S)          # the C# block
        for pat in (r"\?\?", r"\?\.", r"\s&&\s", r"\s\|\|\s", r"ForEach-Object\s+-Parallel"):
            assert not re.search(pat, code), (f.name, pat)


def test_never_touches_secret_stores():
    text = EXPORT.read_text()
    for bad in ("Login Data", "Cookies", "Local Storage", "ProtectedData", "Unprotect", "History\"", "Network\\"):
        assert bad not in text, bad
    assert "storage.json" in text and "never read" in text      # the zip's saved values are named and skipped


def test_extension_map_is_sound():
    m = json.loads((WIN / "extension-map.json").read_text(encoding="utf-8"))
    names, ids = set(), set()
    for x in m["extensions"]:
        assert x["firefox"] in ("install", "builtin", "skip", "none"), x
        assert x["name"] not in names
        names.add(x["name"])
        for i in x["chrome"]:
            assert re.match(r"^[a-p]{32}$", i) and i not in ids, i
            ids.add(i)
        for pat in x["match"]:
            re.compile(pat, re.I)
        if x["firefox"] == "install":
            assert re.match(r"^[a-z0-9-]+$", x["amo"]), x
    for i in m["ignore"]:
        assert re.match(r"^[a-p]{32}$", i)
    for want in ("uBlock Origin", "Bitwarden", "Dark Reader", "Tampermonkey", "Violentmonkey", "1Password",
                 "Grammarly", "Honey", "React Developer Tools", "JSON viewer"):
        assert want in names
    assert next(x for x in m["extensions"] if x["name"] == "Honey")["firefox"] == "skip"


def test_powershell_parses():
    if not PWSH:
        pytest.skip("needs pwsh")
    for f in (EXPORT, MOVE):
        r = subprocess.run([PWSH, "-NoProfile", "-Command",
                            f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{f}',[ref]$null,"
                            f"[ref]$e); if ($e) {{ $e | % {{ $_.ToString() }}; exit 1 }}"], capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr


@needs_pwsh
def test_rot13_userassist_and_ranking():
    r = run_ps(r"""
$o = [ordered]@{}
$o.rot = ConvertFrom-OrionRot13 'P:\Hfref\Qvfpbeq.yax'
$o.expand = Expand-OrionKnownFolder '{6D809377-6AF0-444B-8957-A3773F02200E}\Google\Chrome\Application\chrome.exe'
$b = New-Object byte[] 72
[BitConverter]::GetBytes([int]42).CopyTo($b, 4); [BitConverter]::GetBytes([int]600000).CopyTo($b, 12)
[BitConverter]::GetBytes([DateTime]::new(2026,11,1,0,0,0,[DateTimeKind]::Utc).ToFileTimeUtc()).CopyTo($b, 60)
$o.ua = Read-OrionUserAssistValue $b
$map = Read-OrionMap 'app-map.json'
$apps = @(
  [pscustomobject]@{ name = 'Discord'; version = '1'; publisher = ''; source = 'installed' },
  [pscustomobject]@{ name = 'Microsoft Visual Studio Code (User)'; version = '1'; publisher = ''; source = 'installed' },
  [pscustomobject]@{ name = 'VLC media player'; version = '3'; publisher = ''; source = 'installed' },
  [pscustomobject]@{ name = 'Notepad++ (64-bit x64)'; version = '8'; publisher = ''; source = 'installed' },
  [pscustomobject]@{ name = 'Microsoft Visual C++ 2015 Redistributable'; version = '1'; publisher = ''; source = 'installed' })
$plan = @(Get-OrionAppPlan $map $apps)
$usage = @(
  [pscustomobject]@{ leaf = 'code'; runs = 300; focus_ms = 0; last_run = '2026-11-05T00:00:00Z' },
  [pscustomobject]@{ leaf = 'discord'; runs = 50; focus_ms = 0; last_run = '2026-06-01T00:00:00Z' },
  [pscustomobject]@{ leaf = 'notepad++'; runs = 5; focus_ms = 0; last_run = $null },
  [pscustomobject]@{ leaf = 'setup'; runs = 999; focus_ms = 0; last_run = $null })
$now = [DateTime]::new(2026,11,7,0,0,0,[DateTimeKind]::Utc)
$o.rank = @(Get-OrionAppRanking $map $plan $usage @('Spotify.Spotify') $now)
$o.winget = @(Read-OrionWingetExport '{"Sources":[{"Packages":[{"PackageIdentifier":"Valve.Steam"},{"PackageIdentifier":"Discord.Discord"}]}]}')
$o | ConvertTo-Json -Depth 5 -Compress
""")
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["rot"] == "C:\\Users\\Discord.lnk"
    assert o["expand"] == "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    assert o["ua"]["runs"] == 42 and o["ua"]["focus_ms"] == 600000 and o["ua"]["last_run"] == "2026-11-01T00:00:00Z"
    names = [a["name"] for a in o["rank"]]
    assert names[0].startswith("Microsoft Visual Studio Code") and names[1] == "Discord"
    assert "Spotify" in names                                   # found by winget only
    vlc = next(a for a in o["rank"] if a["name"] == "VLC media player")
    assert vlc["used"] is False and vlc["install"] is True and vlc["flatpak"] == "org.videolan.VLC"
    npp = next(a for a in o["rank"] if a["name"].startswith("Notepad++"))
    assert npp["runs"] == 5 and npp["flatpak"] == "org.kde.kate"
    assert not any("Redistributable" in n for n in names)
    assert o["winget"] == ["Valve.Steam", "Discord.Discord"]


@needs_pwsh
def test_snappy_and_leveldb_reader(fake_user):
    data = (b"hello hello hello world " * 50) + bytes(range(256))
    comp = snappy(data)
    f = fake_user["root"] / "s.bin"
    f.write_bytes(comp)
    r = run_ps(f"Initialize-OrionLevelDb; $b=[IO.File]::ReadAllBytes('{f}'); "
               f"$d=[OrionLevelDb]::Snappy($b,0,$b.Length); [Convert]::ToBase64String($d)")
    assert r.returncode == 0, r.stderr
    import base64
    assert base64.b64decode(r.stdout.strip()) == data


@needs_pwsh
def test_userscripts_found_in_storage_and_zip(fake_user):
    out = fake_user["root"] / "kit"
    r = run_ps(f"""
$s = @(Find-OrionUserscripts '{fake_user["prof"]}')
$s += @(Read-OrionUserscriptZip '{fake_user["zip"]}')
$f = @(Save-OrionUserscripts $s '{out}')
$f | ConvertTo-Json -Compress
""")
    assert r.returncode == 0, r.stderr
    files = {x["name"]: x for x in json.loads(r.stdout)}
    assert set(files) == {"Table Script", "Log Script", "Wide Script", "Zip Script"}
    assert files["Table Script"]["from"] == "zip"                # the zip copy wins over the storage copy
    t = (out / "Table Script.user.js").read_text(encoding="utf-8")
    assert "the zip copy wins" in t
    log = (out / "Log Script.user.js").read_text(encoding="utf-8")
    assert log.startswith("// ==UserScript==") and log.count("y") >= 70000   # joined across log blocks
    wide = (out / "Wide Script.user.js").read_text(encoding="utf-8")
    assert "alert('\u00e9');" in wide
    every = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
    assert "tok-fake" not in every and "k-fake" not in every


@needs_pwsh
def test_whole_kit_from_a_fake_profile(fake_user):
    move = fake_user["root"] / "OrionMove"
    move.mkdir()
    r = run_ps(f"""
$map = Read-OrionMap 'app-map.json'
$plan = @(Get-OrionAppPlan $map @([pscustomobject]@{{ name = 'Spotify'; version = '1'; publisher = ''; source = 'installed' }}))
$roots = [ordered]@{{ chrome = '{fake_user["ud"]}' }}
$kit = Export-OrionKit '{move}' $plan @('{fake_user["zip"]}') $roots @() @()
@{{ kit = $kit; summary = @(Get-OrionKitSummary $kit) }} | ConvertTo-Json -Depth 6 -Compress
""")
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout)
    kit = res["kit"]
    assert kit["extensions_count"] == 5 and kit["userscripts_count"] == 4
    assert kit["unknown_extensions"] == ["Some Unknown Helper"]
    assert kit["browser_profiles"][0]["name"] == "Work" and kit["browser_profiles"][0]["bookmarks"] == 2
    ext = {x["name"]: x for x in json.loads((move / "browser/extensions.json").read_text(encoding="utf-8-sig"))}
    assert ext["uBlock Origin"]["firefox"] == "install" and ext["uBlock Origin"]["amo"] == "ublock-origin"
    assert ext["uBlock Origin"]["version"] == "1.60.0"
    assert ext["Tampermonkey"]["amo"] == "tampermonkey"           # name read from _locales
    assert ext["Honey"]["firefox"] == "skip" and ext["Night Theme"]["firefox"] == "skip"
    assert "Chrome Web Store Payments" not in ext
    html = (move / "browser/bookmarks-chrome-Default.html").read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    assert 'HREF="https://example.com/?a=1&amp;b=2">Example &amp; Co</A>' in html
    assert "Dev &lt;tools&gt;" in html and "javascript:" not in html
    eng = json.loads((move / "browser/search-engines.json").read_text(encoding="utf-8-sig"))
    urls = [e["url"] for e in eng]
    assert eng[0]["default"] and "startpage.com" in urls[0]
    assert "https://search.example.net/find?q={searchTerms}&x=1" in urls
    assert not any("google:" in u for u in urls)
    ranked = json.loads((move / "apps/ranked.json").read_text(encoding="utf-8-sig"))
    assert ranked["usage_known"] is False and ranked["apps"][0]["flatpak"] == "com.spotify.Client"
    # nothing secret anywhere in the kit
    blob = b"".join(p.read_bytes() for p in move.rglob("*") if p.is_file())
    for bad in (b"not-a-real-one", b"tok-fake", b"k-fake"):
        assert bad not in blob
    assert any("Left out on purpose" in l and "saved passwords" in l for l in res["summary"])
