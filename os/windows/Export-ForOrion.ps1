<#
.SYNOPSIS
  The "Orion move kit" part of "Move my stuff to Orion": your most-used apps and your Chrome set-up, ready for
  OrionOS and Firefox. Read-only: nothing on Windows or in Chrome is changed.

.DESCRIPTION
  Move-ToOrion.ps1 dot-sources this file and calls Export-OrionKit. It adds to the OrionMove folder:
    apps\ranked.json        your installed apps, most used first (how often Windows says you started each one:
                            the UserAssist counts in your own registry, plus Prefetch dates when Windows lets a
                            normal user read them), each with what to use on OrionOS (app-map.json)
    apps\winget.json        the package ids "winget export" lists, when winget is installed
    browser\bookmarks-*.html   your Chrome / Edge / Brave bookmarks in the file format Firefox imports
    browser\extensions.json    your browser extensions (name, id, version) and the Firefox version of each
                               (extension-map.json); ones Orion doesn't know are listed for you to look up
    browser\search-engines.json  your default and custom search engines (names and addresses only)
    browser\userscripts\*.user.js  your Tampermonkey / Violentmonkey scripts: found by reading the extension's
                               storage files (best effort), and from Tampermonkey's own zip export if you made one
                               (only the .user.js files are taken from it; its saved script values are left out)
  Never read or copied: saved passwords, cookies, sign-ins, autofill, card details, history, sessions, and the
  values scripts save. The kit's README says how to move your passwords yourself.

  Run on its own, it makes just the kit (no files) through Move-ToOrion.ps1 -KitOnly.
#>
param([string]$To = "", [string]$UserscriptZip = "")

$script:KitHere = $PSScriptRoot
if (-not (Get-Command Read-OrionMap -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot "Check-OrionReady.ps1") }

# ================================================================== small helpers
function ConvertFrom-OrionRot13([string]$Text) {
  $sb = New-Object System.Text.StringBuilder
  foreach ($c in $Text.ToCharArray()) {
    $n = [int]$c
    if ($n -ge 65 -and $n -le 90) { $n = (($n - 65 + 13) % 26) + 65 }
    elseif ($n -ge 97 -and $n -le 122) { $n = (($n - 97 + 13) % 26) + 97 }
    [void]$sb.Append([char]$n)
  }
  return $sb.ToString()
}

# Windows writes some UserAssist paths with a known-folder id in front ({6D809377-...}\Google\Chrome\...).
$script:KnownFolders = @{
  "{6D809377-6AF0-444B-8957-A3773F02200E}" = "C:\Program Files"
  "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}" = "C:\Program Files (x86)"
  "{F38BF404-1D43-42F2-9305-67DE0B28FC23}" = "C:\Windows"
  "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}" = "C:\Windows\System32"
  "{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}" = "C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
  "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}" = "Start Menu\Programs"
  "{9E3995AB-1F9C-4F13-B827-48B24B6C7174}" = "Taskbar"
}
function Expand-OrionKnownFolder([string]$Path) {
  foreach ($k in $script:KnownFolders.Keys) {
    if ($Path.StartsWith($k, [StringComparison]::OrdinalIgnoreCase)) { return $script:KnownFolders[$k] + $Path.Substring($k.Length) }
  }
  return $Path
}

# "C:\...\Discord.lnk" -> "discord"; an app id like "com.squirrel.Discord.Discord" stays as it is (lower case).
function Get-OrionLeaf([string]$Path) {
  $leaf = ($Path -split '[\\/]')[-1]
  $leaf = $leaf -replace '(?i)\.(exe|lnk|url)$', ''
  return $leaf.ToLower()
}

function ConvertTo-OrionNorm([string]$Text) { return ($Text.ToLower() -replace '[^a-z0-9+]', '') }

# One UserAssist value (Windows 7 and later: 72 bytes; XP: 16 bytes) -> run count, time in focus, last start.
function Read-OrionUserAssistValue([byte[]]$Data) {
  $r = [ordered]@{ runs = 0; focus_ms = 0; last_run = $null }
  if (-not $Data) { return [pscustomobject]$r }
  if ($Data.Length -ge 68) {
    $r.runs = [BitConverter]::ToInt32($Data, 4)
    $r.focus_ms = [BitConverter]::ToInt32($Data, 12)
    $ft = [BitConverter]::ToInt64($Data, 60)
  } elseif ($Data.Length -ge 16) {
    $r.runs = [math]::Max(0, [BitConverter]::ToInt32($Data, 4) - 5)
    $ft = [BitConverter]::ToInt64($Data, 8)
  } else { $ft = 0 }
  if ($r.runs -lt 0) { $r.runs = 0 }
  if ($r.focus_ms -lt 0) { $r.focus_ms = 0 }
  if ($ft -gt 0 -and $ft -lt 2650467743999999999) {
    try { $r.last_run = [DateTime]::FromFileTimeUtc($ft).ToString("yyyy-MM-ddTHH:mm:ssZ") } catch { }
  }
  return [pscustomobject]$r
}

# What Windows remembers about the programs you start (your own registry; no admin needed).
function Get-OrionUserAssist {
  $out = @()
  $root = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
  foreach ($g in @(Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
    $key = Get-Item -Path (Join-Path $g.PSPath "Count") -ErrorAction SilentlyContinue
    if (-not $key) { continue }
    foreach ($vn in $key.GetValueNames()) {
      $name = ConvertFrom-OrionRot13 $vn
      if ($name -match '^UEME_') { continue }
      $v = Read-OrionUserAssistValue ([byte[]]$key.GetValue($vn))
      if ($v.runs -le 0 -and -not $v.last_run) { continue }
      $p = Expand-OrionKnownFolder $name
      $out += [pscustomobject]@{ path = $p; leaf = (Get-OrionLeaf $p); runs = $v.runs; focus_ms = $v.focus_ms; last_run = $v.last_run; from = "userassist" }
    }
  }
  return $out
}

# Prefetch: when each program last started. Usually admin-only; skipped quietly when it can't be read.
function Get-OrionPrefetch {
  $out = @()
  foreach ($f in @(Get-ChildItem -Path (Join-Path $env:windir "Prefetch") -Filter "*.pf" -ErrorAction SilentlyContinue)) {
    $leaf = ($f.Name -replace '(?i)\.exe-[0-9A-F]{8}\.pf$', '') -replace '(?i)\.pf$', ''
    $out += [pscustomobject]@{ path = $f.Name; leaf = $leaf.ToLower(); runs = 0; focus_ms = 0
                               last_run = $f.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ"); from = "prefetch" }
  }
  return $out
}

# Package ids from "winget export" (only when winget is installed; never installs or changes anything).
function Get-OrionWingetIds([string]$SaveTo = "") {
  $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $wg) { return @() }
  $tmp = Join-Path $env:TEMP ("orion-winget-" + [guid]::NewGuid().ToString("N") + ".json")
  try {
    $p = Start-Process -FilePath $wg.Source -ArgumentList @("export", "-o", "`"$tmp`"", "--accept-source-agreements", "--disable-interactivity") -WindowStyle Hidden -PassThru
    if (-not $p.WaitForExit(120000)) { try { $p.Kill() } catch { }; return @() }
    if (-not (Test-Path -LiteralPath $tmp)) { return @() }
    $raw = Get-Content -Raw -Encoding UTF8 -LiteralPath $tmp
    if ($SaveTo) { [IO.File]::WriteAllText($SaveTo, $raw, (New-Object Text.UTF8Encoding($false))) }
    return @(Read-OrionWingetExport $raw)
  } catch { return @() }
  finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }   # only our own temporary file
}
function Read-OrionWingetExport([string]$Json) {
  $ids = @()
  try { $j = $Json | ConvertFrom-Json } catch { return $ids }
  foreach ($s in @($j.Sources)) { foreach ($p in @($s.Packages)) { if ($p.PackageIdentifier) { $ids += "$($p.PackageIdentifier)" } } }
  return $ids
}

# ================================================================== most-used apps
$script:GenericLeaves = @("setup", "install", "installer", "uninstall", "unins000", "update", "updater", "launcher", "explorer",
  "cmd", "powershell", "pwsh", "notepad", "msiexec", "rundll32", "control", "taskmgr", "mmc", "regedit", "conhost", "dllhost",
  "svchost", "searchapp", "shellexperiencehost", "microsoft.windows.explorer", "microsoft.windows.controlpanel", "helper", "crashpad_handler")
$script:StopWords = @("microsoft", "google", "inc", "the", "for", "and", "x64", "x86", "64bit", "32bit", "version", "app", "desktop", "studio", "corporation", "llc", "ltd")

# Does one usage record (a program or shortcut name) belong to this app?
function Test-OrionUsageMatch([string]$AppName, $MapEntry, [string]$Leaf) {
  $leafN = ConvertTo-OrionNorm $Leaf
  if ($leafN.Length -lt 2) { return $false }
  if ($MapEntry -and $MapEntry.exe) {
    foreach ($e in @($MapEntry.exe)) { if ((ConvertTo-OrionNorm "$e") -eq $leafN) { return $true } }
  }
  if ($script:GenericLeaves -contains $Leaf.ToLower()) { return $false }
  $appN = ConvertTo-OrionNorm $AppName
  if ($leafN.Length -ge 4 -and $appN.Contains($leafN)) { return $true }
  $tokens = @(($AppName.ToLower() -split '[^a-z0-9+]+') | Where-Object { $_.Length -ge 3 -and $script:StopWords -notcontains $_ -and $_ -notmatch '^\d+$' })
  if ($tokens.Count -and $leafN.StartsWith($tokens[0])) { return $true }
  return $false
}

# $Plan: Get-OrionAppPlan output. $Usage: UserAssist + Prefetch records. Returns the apps, most used first.
function Get-OrionAppRanking($Map, $Plan, $Usage, [string[]]$WingetIds = @(), [DateTime]$Now = (Get-Date).ToUniversalTime()) {
  $byName = @{}; foreach ($a in $Map.apps) { $byName[$a.name] = $a }
  $rows = @(); $seen = @{}
  $items = @($Plan)
  foreach ($a in $Map.apps) {   # apps winget knows about that the uninstall list missed
    if (-not $a.winget -or $seen.ContainsKey($a.name)) { continue }
    $hit = @($a.winget | Where-Object { $WingetIds -contains $_ })
    if ($hit.Count -and -not @($items | Where-Object { $_.match -eq $a.name }).Count) {
      $items += [pscustomobject]@{ name = $a.name; match = $a.name; kind = $a.kind; how = $a.how; flatpak = "$($a.flatpak)"; recipe = "$($a.recipe)"; tool = "$($a.tool)"; web = "$($a.web)" }
    }
  }
  foreach ($p in $items) {
    $m = $null; if ($p.match -and $byName.ContainsKey($p.match)) { $m = $byName[$p.match] }
    $runs = 0; $focus = 0; $last = $null
    foreach ($u in @($Usage)) {
      if (-not (Test-OrionUsageMatch $p.name $m $u.leaf)) { continue }
      $runs += [int]$u.runs; $focus += [int64]$u.focus_ms
      if ($u.last_run -and (-not $last -or "$($u.last_run)" -gt $last)) { $last = "$($u.last_run)" }
    }
    $score = [double]$runs + [math]::Min($focus / 60000.0, 600.0) / 5.0
    if ($last) {
      $age = ($Now - [DateTime]::Parse($last, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal)).TotalDays
      if ($age -le 30) { $score += 20 } elseif ($age -le 90) { $score += 5 }
    }
    $wg = $false; if ($m -and $m.winget) { $wg = [bool]@($m.winget | Where-Object { $WingetIds -contains $_ }).Count }
    $install = [bool]("$($p.flatpak)" -or ("$($p.recipe)" -and "$($p.kind)" -ne "builtin"))
    $rows += [pscustomobject]@{ name = $p.name; match = $p.match; kind = $p.kind; how = $p.how; flatpak = "$($p.flatpak)"; recipe = "$($p.recipe)"
                                tool = "$($p.tool)"; web = "$($p.web)"; runs = $runs; focus_min = [int]($focus / 60000); last_run = $last
                                score = [math]::Round($score, 1); used = [bool]($runs -gt 0 -or $last); winget = $wg; install = $install }
    $seen[$p.name] = $true
  }
  return @($rows | Sort-Object -Property @{ Expression = "score"; Descending = $true }, @{ Expression = "name"; Descending = $false })
}

# ================================================================== Chrome (and Edge, Brave: same files)
function Get-OrionBrowserRoots {
  return [ordered]@{ chrome = "$env:LOCALAPPDATA\Google\Chrome\User Data"; edge = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
                     brave = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data" }
}

# Profiles in a "User Data" folder, with the names you gave them (Local State).
function Get-OrionChromeProfiles([string]$UserData) {
  $out = @()
  if (-not (Test-Path -LiteralPath $UserData)) { return $out }
  $names = @{}
  try {
    $ls = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $UserData "Local State") | ConvertFrom-Json
    foreach ($pr in $ls.profile.info_cache.PSObject.Properties) { $names[$pr.Name] = "$($pr.Value.name)" }
  } catch { }
  foreach ($d in @(Get-ChildItem -LiteralPath $UserData -Directory -ErrorAction SilentlyContinue | Sort-Object Name)) {
    if ($d.Name -ne "Default" -and $d.Name -notmatch '^Profile \d+$') { continue }
    if (-not ((Test-Path (Join-Path $d.FullName "Preferences")) -or (Test-Path (Join-Path $d.FullName "Bookmarks")))) { continue }
    $n = $names[$d.Name]; if (-not $n) { $n = $d.Name }
    $out += [pscustomobject]@{ dir = $d.Name; name = $n; path = $d.FullName }
  }
  return $out
}

function Read-OrionJsonFile([string]$Path) {
  try { return (Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json) } catch { return $null }
}

# "__MSG_appName__" -> the real name from the extension's _locales folder.
function Resolve-OrionExtensionName([string]$Dir, $Manifest, [string]$Text) {
  if ($Text -notmatch '^__MSG_(.+)__$') { return $Text }
  $key = $Matches[1]
  $locs = @()
  if ($Manifest.default_locale) { $locs += "$($Manifest.default_locale)" }
  $locs += @("en", "en_US", "en_GB")
  foreach ($l in $locs) {
    $msgs = Read-OrionJsonFile (Join-Path $Dir "_locales\$l\messages.json")
    if (-not $msgs) { continue }
    foreach ($pr in $msgs.PSObject.Properties) { if ($pr.Name -ieq $key -and $pr.Value.message) { return "$($pr.Value.message)" } }
  }
  return $Text
}

# Installed extensions of one profile: name, Chrome Web Store id, version. Reads manifest.json files only.
function Get-OrionChromeExtensions([string]$ProfilePath) {
  $out = @()
  foreach ($e in @(Get-ChildItem -LiteralPath (Join-Path $ProfilePath "Extensions") -Directory -ErrorAction SilentlyContinue)) {
    if ($e.Name -notmatch '^[a-p]{32}$') { continue }
    $ver = @(Get-ChildItem -LiteralPath $e.FullName -Directory -ErrorAction SilentlyContinue | Sort-Object -Property @{ Expression = { $v = $null; $n = ($_.Name -replace '_\d+$', ''); if ([version]::TryParse($n, [ref]$v)) { $v } else { [version]"0.0" } } } | Select-Object -Last 1)
    if (-not $ver.Count) { continue }
    $man = Read-OrionJsonFile (Join-Path $ver[0].FullName "manifest.json")
    if (-not $man) { continue }
    $name = Resolve-OrionExtensionName $ver[0].FullName $man "$($man.name)"
    $out += [pscustomobject]@{ id = $e.Name; name = $name; version = "$($man.version)"; theme = [bool]$man.theme }
  }
  return $out
}

# Each extension with what to do in Firefox (extension-map.json).
function Get-OrionExtensionPlan($ExtMap, $Exts) {
  $out = @()
  foreach ($x in @($Exts)) {
    if (@($ExtMap.ignore) -contains $x.id) { continue }
    if ($x.theme) {
      $out += [pscustomobject]@{ id = $x.id; name = $x.name; version = $x.version; firefox = "skip"; firefox_name = ""; amo = ""; note = "A Chrome theme. Firefox on OrionOS has its own look." }
      continue
    }
    $hit = $null
    foreach ($m in $ExtMap.extensions) { if (@($m.chrome) -contains $x.id) { $hit = $m; break } }
    if (-not $hit) { foreach ($m in $ExtMap.extensions) { foreach ($pat in @($m.match)) { if ($pat -and $x.name -match $pat) { $hit = $m; break } }; if ($hit) { break } } }
    if ($hit) {
      $out += [pscustomobject]@{ id = $x.id; name = $x.name; version = $x.version; firefox = "$($hit.firefox)"; firefox_name = "$($hit.name)"; amo = "$($hit.amo)"; note = "$($hit.note)" }
    } else {
      $out += [pscustomobject]@{ id = $x.id; name = $x.name; version = $x.version; firefox = "unknown"; firefox_name = ""; amo = ""
                                 note = "Not in Orion's list yet. Search for it at addons.mozilla.org." }
    }
  }
  return $out
}

# Chrome's Bookmarks file (JSON) -> the bookmark file format Firefox imports (Bookmarks > Import from HTML).
function ConvertTo-OrionBookmarksHtml($Data, [string]$Title = "From Chrome") {
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.AppendLine("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
  [void]$sb.AppendLine('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
  [void]$sb.AppendLine("<TITLE>Bookmarks</TITLE>")
  [void]$sb.AppendLine("<H1>Bookmarks</H1>")
  [void]$sb.AppendLine("<DL><p>")
  [void]$sb.AppendLine("    <DT><H3>" + [System.Net.WebUtility]::HtmlEncode($Title) + "</H3>")
  [void]$sb.AppendLine("    <DL><p>")
  $script:BmCount = 0
  $labels = [ordered]@{ bookmark_bar = "Bookmarks bar"; other = "Other bookmarks"; synced = "Mobile bookmarks" }
  foreach ($k in $labels.Keys) {
    $node = $Data.roots.$k
    if (-not $node -or -not @($node.children).Count) { continue }
    [void]$sb.AppendLine("        <DT><H3>" + $labels[$k] + "</H3>")
    [void]$sb.AppendLine("        <DL><p>")
    Add-OrionBookmarkNodes $sb @($node.children) 3
    [void]$sb.AppendLine("        </DL><p>")
  }
  [void]$sb.AppendLine("    </DL><p>")
  [void]$sb.AppendLine("</DL><p>")
  return $sb.ToString()
}
function Add-OrionBookmarkNodes($Sb, $Nodes, [int]$Depth) {
  $pad = "    " * $Depth
  foreach ($n in $Nodes) {
    if (-not $n) { continue }
    if ($n.type -eq "url") {
      $u = "$($n.url)"
      if ($u -notmatch '^(?i)(https?|ftp)://') { continue }   # javascript: and chrome:// links don't move
      $t = "$($n.name)"; if (-not $t) { $t = $u }
      [void]$Sb.AppendLine($pad + '<DT><A HREF="' + [System.Net.WebUtility]::HtmlEncode($u) + '">' + [System.Net.WebUtility]::HtmlEncode($t) + "</A>")
      $script:BmCount++
    } elseif ($n.type -eq "folder") {
      $t = "$($n.name)"; if (-not $t) { $t = "Folder" }
      [void]$Sb.AppendLine($pad + "<DT><H3>" + [System.Net.WebUtility]::HtmlEncode($t) + "</H3>")
      [void]$Sb.AppendLine($pad + "<DL><p>")
      Add-OrionBookmarkNodes $Sb @($n.children) ($Depth + 1)
      [void]$Sb.AppendLine($pad + "</DL><p>")
    }
  }
}

# Opens a file Chrome may have open, read-only, without locking it.
function Read-OrionBytesShared([string]$Path) {
  try {
    $fs = New-Object IO.FileStream($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try { $ms = New-Object IO.MemoryStream; $fs.CopyTo($ms); return $ms.ToArray() } finally { $fs.Dispose() }
  } catch { return $null }
}

# Search engines: the default one (Preferences) and the addresses of the others (scanned out of "Web Data";
# only addresses with {searchTerms} in them: no history, no autofill).
function Get-OrionSearchEngines([string]$ProfilePath) {
  $out = @(); $seen = @{}
  $pref = Read-OrionJsonFile (Join-Path $ProfilePath "Preferences")
  $d = $null; if ($pref) { $d = $pref.default_search_provider_data.template_url_data }
  if ($d -and $d.url) {
    $out += [pscustomobject]@{ name = "$($d.short_name)"; keyword = "$($d.keyword)"; url = "$($d.url)"; default = $true }
    try { $seen[([uri]("$($d.url)" -replace '\{[^}]*\}', 'x')).Host] = $true } catch { }
  }
  $bytes = Read-OrionBytesShared (Join-Path $ProfilePath "Web Data")
  if ($bytes) {
    $text = [Text.Encoding]::GetEncoding(28591).GetString($bytes)
    foreach ($m in [regex]::Matches($text, 'https://[A-Za-z0-9.\-]+(:\d+)?/[\x21-\x7e]{0,250}?\{searchTerms\}[\x21-\x7e]{0,120}')) {
      $u = $m.Value -replace '["''<>\\].*$', ''
      if ($u -match '\{google:' -or $u.Length -gt 300) { continue }
      $h = ""; try { $h = ([uri]($u -replace '\{[^}]*\}', 'x')).Host } catch { continue }
      if (-not $h -or $seen.ContainsKey($h)) { continue }
      $seen[$h] = $true
      $out += [pscustomobject]@{ name = $h; keyword = ""; url = $u; default = $false }
      if ($out.Count -ge 40) { break }
    }
  }
  return $out
}

# ================================================================== userscripts (Tampermonkey, Violentmonkey)
$script:ScriptExtIds = @("dhdgffkkebhmkfjojejmpbldmpobfkfo", "gcalenpjmijncebpfijmoaglllgpjagf", "jinjaccalgkegednnccohejagnlnfdag")

# Chrome keeps extension storage in LevelDB files: .log files (plain records) and .ldb tables (blocks usually
# compressed with Snappy). This small reader gets the raw bytes back so the userscripts in them can be found.
function Initialize-OrionLevelDb {
  if ("OrionLevelDb" -as [type]) { return }
  Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Collections.Generic;

public static class OrionLevelDb {
  static ulong Varint(byte[] b, ref int i, int end) {
    ulong v = 0; int shift = 0;
    while (i < end) {
      byte c = b[i++];
      v |= (ulong)(c & 0x7f) << shift;
      if ((c & 0x80) == 0) return v;
      shift += 7;
      if (shift > 63) throw new InvalidDataException("varint");
    }
    throw new InvalidDataException("varint end");
  }

  public static byte[] Snappy(byte[] src, int off, int len) {
    int i = off, end = off + len;
    ulong ulen = Varint(src, ref i, end);
    if (ulen > 64UL * 1024 * 1024) throw new InvalidDataException("too big");
    byte[] dst = new byte[(int)ulen];
    int d = 0;
    while (i < end) {
      int tag = src[i++];
      int t = tag & 3;
      if (t == 0) {
        int l = tag >> 2;
        if (l >= 60) {
          int nb = l - 59; l = 0;
          if (i + nb > end) throw new InvalidDataException("literal");
          for (int k = 0; k < nb; k++) l |= src[i++] << (8 * k);
        }
        l += 1;
        if (l < 0 || i + l > end || d + l > dst.Length) throw new InvalidDataException("literal");
        Buffer.BlockCopy(src, i, dst, d, l); i += l; d += l;
      } else {
        int l, o;
        if (t == 1) {
          if (i >= end) throw new InvalidDataException("copy");
          l = ((tag >> 2) & 7) + 4; o = ((tag >> 5) << 8) | src[i++];
        } else if (t == 2) {
          if (i + 2 > end) throw new InvalidDataException("copy");
          l = (tag >> 2) + 1; o = src[i] | (src[i + 1] << 8); i += 2;
        } else {
          if (i + 4 > end) throw new InvalidDataException("copy");
          l = (tag >> 2) + 1; o = src[i] | (src[i + 1] << 8) | (src[i + 2] << 16) | (src[i + 3] << 24); i += 4;
        }
        if (o <= 0 || o > d || d + l > dst.Length) throw new InvalidDataException("copy");
        for (int k = 0; k < l; k++) { dst[d] = dst[d - o]; d++; }
      }
    }
    if (d != dst.Length) throw new InvalidDataException("short");
    return dst;
  }

  static byte[] Block(byte[] f, long off, long size) {
    if (off < 0 || size < 0 || off + size + 5 > f.Length) return null;
    byte type = f[off + size];
    if (type == 0) { byte[] b = new byte[size]; Buffer.BlockCopy(f, (int)off, b, 0, (int)size); return b; }
    if (type == 1) { try { return Snappy(f, (int)off, (int)size); } catch (Exception) { return null; } }
    return null;
  }

  static List<byte[]> BlockValues(byte[] blk) {
    List<byte[]> vals = new List<byte[]>();
    if (blk.Length < 4) return vals;
    int nrest = BitConverter.ToInt32(blk, blk.Length - 4);
    long limit = (long)blk.Length - 4 - 4L * nrest;
    if (nrest < 0 || limit < 0) return vals;
    int i = 0;
    try {
      while (i < limit) {
        Varint(blk, ref i, (int)limit);
        ulong nonshared = Varint(blk, ref i, (int)limit);
        ulong vlen = Varint(blk, ref i, (int)limit);
        if ((long)i + (long)nonshared + (long)vlen > limit) break;
        i += (int)nonshared;
        byte[] v = new byte[(int)vlen];
        Buffer.BlockCopy(blk, i, v, 0, (int)vlen);
        vals.Add(v);
        i += (int)vlen;
      }
    } catch (InvalidDataException) { }
    return vals;
  }

  // Every data block of a .ldb / .sst table, decompressed.
  public static List<byte[]> TableBlocks(byte[] f) {
    List<byte[]> outp = new List<byte[]>();
    if (f == null || f.Length < 48) return outp;
    int foot = f.Length - 48;
    if (f[f.Length - 8] != 0x57 || f[f.Length - 1] != 0xdb) return outp;
    try {
      int i = foot;
      Varint(f, ref i, f.Length); Varint(f, ref i, f.Length);
      ulong io = Varint(f, ref i, f.Length), isz = Varint(f, ref i, f.Length);
      byte[] index = Block(f, (long)io, (long)isz);
      if (index == null) return outp;
      foreach (byte[] v in BlockValues(index)) {
        int j = 0;
        ulong bo = Varint(v, ref j, v.Length), bs = Varint(v, ref j, v.Length);
        byte[] blk = Block(f, (long)bo, (long)bs);
        if (blk != null) outp.Add(blk);
      }
    } catch (InvalidDataException) { }
    return outp;
  }

  // Every record of a .log file (fragments joined back together).
  public static List<byte[]> LogRecords(byte[] f) {
    List<byte[]> outp = new List<byte[]>();
    if (f == null) return outp;
    MemoryStream cur = new MemoryStream();
    const int B = 32768;
    int pos = 0;
    while (pos + 7 <= f.Length) {
      int left = B - (pos % B);
      if (left < 7) { pos += left; continue; }
      int len = f[pos + 4] | (f[pos + 5] << 8);
      byte type = f[pos + 6];
      if (type == 0 && len == 0) { pos += left; continue; }
      if (pos + 7 + len > f.Length || 7 + len > left) break;
      if (type == 1) { byte[] r = new byte[len]; Buffer.BlockCopy(f, pos + 7, r, 0, len); outp.Add(r); cur.SetLength(0); }
      else if (type == 2) { cur.SetLength(0); cur.Write(f, pos + 7, len); }
      else if (type == 3) { cur.Write(f, pos + 7, len); }
      else if (type == 4) { cur.Write(f, pos + 7, len); outp.Add(cur.ToArray()); cur.SetLength(0); }
      pos += 7 + len;
    }
    return outp;
  }

  static int IndexOf(byte[] hay, byte[] needle, int from) {
    for (int i = from; i <= hay.Length - needle.Length; i++) {
      if (hay[i] != needle[0]) continue;
      int k = 1;
      while (k < needle.Length && hay[i + k] == needle[k]) k++;
      if (k == needle.Length) return i;
    }
    return -1;
  }

  static string JsonString(byte[] b, int start) {
    // b[start-1] is the opening quote; read to the closing one and undo the JSON escapes.
    int i = start;
    while (i < b.Length) {
      if (b[i] == (byte)'\\') { i += 2; continue; }
      if (b[i] == (byte)'"') break;
      i++;
    }
    if (i >= b.Length) return null;
    string raw = Encoding.UTF8.GetString(b, start, i - start);
    StringBuilder sb = new StringBuilder(raw.Length);
    for (int k = 0; k < raw.Length; k++) {
      char c = raw[k];
      if (c != '\\' || k + 1 >= raw.Length) { sb.Append(c); continue; }
      char e = raw[++k];
      if (e == 'n') sb.Append('\n');
      else if (e == 'r') sb.Append('\r');
      else if (e == 't') sb.Append('\t');
      else if (e == 'b') sb.Append('\b');
      else if (e == 'f') sb.Append('\f');
      else if (e == 'u' && k + 4 < raw.Length) { sb.Append((char)Convert.ToInt32(raw.Substring(k + 1, 4), 16)); k += 4; }
      else sb.Append(e);
    }
    return sb.ToString();
  }

  // A V8 length prefix (tag, varint) that ends right before pos, or -1.
  static long V8Length(byte[] b, int pos, byte tag) {
    for (int k = 1; k <= 5 && pos - k - 1 >= 0; k++) {
      int s = pos - k;
      bool ok = true;
      for (int q = s; q < pos - 1; q++) if ((b[q] & 0x80) == 0) { ok = false; break; }
      if (!ok || (b[pos - 1] & 0x80) != 0 || b[s - 1] != tag) continue;
      long v = 0; int shift = 0;
      for (int q = s; q < pos; q++) { v |= (long)(b[q] & 0x7f) << shift; shift += 7; }
      return v;
    }
    return -1;
  }

  // Userscripts (text starting "// ==UserScript==") found anywhere in a buffer: JSON strings (chrome.storage),
  // V8 strings (IndexedDB, one-byte or UTF-16), or plain text.
  public static List<string> FindScripts(byte[] b) {
    List<string> outp = new List<string>();
    if (b == null) return outp;
    byte[] n8 = Encoding.ASCII.GetBytes("// ==UserScript==");
    byte[] n16 = Encoding.Unicode.GetBytes("// ==UserScript==");
    int p = 0;
    while ((p = IndexOf(b, n8, p)) >= 0) {
      string s = null;
      if (p > 0 && b[p - 1] == (byte)'"') s = JsonString(b, p);
      if (s == null) {
        long l = V8Length(b, p, 0x22);
        if (l > 0 && p + l <= b.Length) s = Encoding.GetEncoding(28591).GetString(b, p, (int)l);
      }
      if (s == null) {
        int e = p;
        while (e < b.Length && e - p < 4 * 1024 * 1024 && b[e] >= 0x09) e++;
        s = Encoding.UTF8.GetString(b, p, e - p);
      }
      if (s != null) outp.Add(s);
      p += n8.Length;
    }
    p = 0;
    while ((p = IndexOf(b, n16, p)) >= 0) {
      string s = null;
      long l = V8Length(b, p, 0x63);
      if (l > 0 && p + l <= b.Length) s = Encoding.Unicode.GetString(b, p, (int)(l - (l % 2)));
      if (s == null) {
        int e = p;
        while (e + 1 < b.Length && e - p < 8 * 1024 * 1024 && !(b[e] == 0 && b[e + 1] == 0)) e += 2;
        s = Encoding.Unicode.GetString(b, p, e - p);
      }
      outp.Add(s);
      p += n16.Length;
    }
    return outp;
  }
}
'@
}

function Get-OrionUserscriptMeta([string]$Text) {
  $meta = [ordered]@{ name = ""; namespace = ""; version = "" }
  $head = $Text
  $end = $Text.IndexOf("// ==/UserScript==")
  if ($end -gt 0) { $head = $Text.Substring(0, $end) }
  foreach ($k in @("name", "namespace", "version")) {
    $m = [regex]::Match($head, "(?m)^//\s*@$k\s+(.+?)\s*$")
    if ($m.Success) { $meta[$k] = $m.Groups[1].Value }
  }
  return [pscustomobject]$meta
}

# A found script: cut anything after the end of the script text, and keep only real ones.
function Test-OrionUserscript([string]$Text) {
  if (-not $Text) { return $false }
  return ($Text.StartsWith("// ==UserScript==") -and $Text.Contains("// ==/UserScript==") -and $Text -match '(?m)^//\s*@name\s+\S')
}

# Userscripts from one browser profile's Tampermonkey / Violentmonkey storage. Best effort: what LevelDB still
# holds, which can include an older copy of a script or one you deleted.
function Find-OrionUserscripts([string]$ProfilePath) {
  Initialize-OrionLevelDb
  $found = [ordered]@{}
  $dirs = @()
  foreach ($id in $script:ScriptExtIds) {
    $dirs += Join-Path $ProfilePath "Local Extension Settings\$id"
    $dirs += Join-Path $ProfilePath "IndexedDB\chrome-extension_$($id)_0.indexeddb.leveldb"
  }
  foreach ($d in $dirs) {
    if (-not (Test-Path -LiteralPath $d)) { continue }
    $files = @(Get-ChildItem -LiteralPath $d -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @(".ldb", ".sst", ".log") } | Sort-Object LastWriteTimeUtc)
    foreach ($f in $files) {
      $bytes = Read-OrionBytesShared $f.FullName
      if (-not $bytes) { continue }
      if ($f.Extension -eq ".log") { $bufs = [OrionLevelDb]::LogRecords($bytes) } else { $bufs = [OrionLevelDb]::TableBlocks($bytes) }
      foreach ($buf in $bufs) {
        foreach ($s in [OrionLevelDb]::FindScripts($buf)) {
          if (-not (Test-OrionUserscript $s)) { continue }
          $m = Get-OrionUserscriptMeta $s
          $found["$($m.name)|$($m.namespace)"] = [pscustomobject]@{ name = $m.name; namespace = $m.namespace; version = $m.version; source = $s; from = "storage" }
        }
      }
    }
  }
  return @($found.Values)
}

# Tampermonkey's own export (Utilities > Zip > Export): only the .user.js files are taken. Its *.storage.json files
# hold values your scripts saved (which can include sign-ins), so they are never read.
function Read-OrionUserscriptZip([string]$ZipPath) {
  $out = @()
  try { Add-Type -AssemblyName System.IO.Compression.FileSystem } catch { }
  $zip = $null
  try {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    foreach ($e in $zip.Entries) {
      if ($e.FullName -notmatch '(?i)\.user\.js$' -or $e.Length -gt 8MB) { continue }
      $r = New-Object IO.StreamReader($e.Open(), [Text.Encoding]::UTF8)
      try { $s = $r.ReadToEnd() } finally { $r.Dispose() }
      $s = $s.TrimStart([char]0xFEFF)
      if (-not (Test-OrionUserscript $s)) { continue }
      $m = Get-OrionUserscriptMeta $s
      $out += [pscustomobject]@{ name = $m.name; namespace = $m.namespace; version = $m.version; source = $s; from = "zip" }
    }
  } catch { } finally { if ($zip) { $zip.Dispose() } }
  return $out
}

function Get-OrionSafeFileName([string]$Name) {
  $n = ($Name -replace '[^A-Za-z0-9 ._-]', '_').Trim(' ', '.')
  if (-not $n) { $n = "script" }
  if ($n.Length -gt 80) { $n = $n.Substring(0, 80) }
  return $n
}

# Saves scripts as <name>.user.js (zip copies win over storage copies of the same script). Returns the file list.
function Save-OrionUserscripts($Scripts, [string]$Dir) {
  $best = [ordered]@{}
  foreach ($s in @($Scripts)) {
    $k = "$($s.name)|$($s.namespace)"
    if (-not $best.Contains($k) -or $s.from -eq "zip" -or ($best[$k].from -ne "zip")) { $best[$k] = $s }
  }
  $files = @(); $used = @{}
  if ($best.Count) { New-Item -ItemType Directory -Force -Path $Dir | Out-Null }
  foreach ($s in $best.Values) {
    $base = Get-OrionSafeFileName $s.name; $fn = "$base.user.js"; $i = 2
    while ($used.ContainsKey($fn.ToLower())) { $fn = "$base ($i).user.js"; $i++ }
    $used[$fn.ToLower()] = $true
    [IO.File]::WriteAllText((Join-Path $Dir $fn), $s.source, (New-Object Text.UTF8Encoding($false)))
    $files += [pscustomobject]@{ file = $fn; name = $s.name; namespace = $s.namespace; version = $s.version; from = $s.from }
  }
  return $files
}

# Tampermonkey's export zip in Downloads, newest first (its file names start with "tampermonkey-backup").
function Find-OrionUserscriptZips {
  $dl = Join-Path $env:USERPROFILE "Downloads"
  return @(Get-ChildItem -Path $dl -Filter "*.zip" -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '(?i)^(tampermonkey|violentmonkey)' } | Sort-Object LastWriteTime -Descending)
}

# ================================================================== the kit
function Write-OrionJson([string]$Path, $Object) {
  [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Object -Depth 6), (New-Object Text.UTF8Encoding($false)))
}

# Writes apps\ranked.json, apps\winget.json and browser\ into $Move. Returns what it made (for manifest.json).
function Export-OrionKit([string]$Move, $Plan = $null, [string[]]$ZipPaths = @(), $Roots = $null, $Usage = $null, $WingetIds = $null) {
  $map = Read-OrionMap "app-map.json"
  $extMap = Read-OrionMap "extension-map.json"
  New-Item -ItemType Directory -Force -Path (Join-Path $Move "apps"), (Join-Path $Move "browser") | Out-Null

  # ---- most-used apps
  if ($null -eq $Plan) { $Plan = @(Get-OrionAppPlan $map (Get-OrionInstalledApps $map)) }
  if ($null -eq $Usage) { $Usage = @(Get-OrionUserAssist) + @(Get-OrionPrefetch) }
  if ($null -eq $WingetIds) { $WingetIds = @(Get-OrionWingetIds (Join-Path $Move "apps\winget.json")) }
  $ranked = @(Get-OrionAppRanking $map $Plan $Usage $WingetIds)
  Write-OrionJson (Join-Path $Move "apps\ranked.json") ([ordered]@{ usage_known = [bool]@($Usage).Count; apps = @($ranked) })

  # ---- browsers
  if ($null -eq $Roots) { $Roots = Get-OrionBrowserRoots }
  $profiles = @(); $allExt = @(); $engines = @(); $scripts = @()
  foreach ($b in $Roots.Keys) {
    foreach ($p in @(Get-OrionChromeProfiles $Roots[$b])) {
      $tag = "$b-" + ($p.dir -replace '[^A-Za-z0-9_-]', '')
      $entry = [ordered]@{ browser = $b; profile = $p.dir; name = $p.name; bookmarks_html = $null; bookmarks = 0; extensions = 0; userscripts = 0 }
      $bmFile = Join-Path $p.path "Bookmarks"
      if (Test-Path -LiteralPath $bmFile) {
        $data = Read-OrionJsonFile $bmFile
        if ($data) {
          $label = (Get-Culture).TextInfo.ToTitleCase($b) + " (" + $p.name + ")"
          $html = ConvertTo-OrionBookmarksHtml $data "From $label"
          if ($script:BmCount -gt 0) {
            [IO.File]::WriteAllText((Join-Path $Move "browser\bookmarks-$tag.html"), $html, (New-Object Text.UTF8Encoding($false)))
            $entry.bookmarks_html = "browser/bookmarks-$tag.html"; $entry.bookmarks = $script:BmCount
          }
        }
      }
      $ext = @(Get-OrionExtensionPlan $extMap (Get-OrionChromeExtensions $p.path))
      foreach ($x in $ext) { $x | Add-Member -NotePropertyName browser -NotePropertyValue $b -Force; $x | Add-Member -NotePropertyName profile -NotePropertyValue $p.dir -Force }
      $allExt += $ext; $entry.extensions = $ext.Count
      foreach ($e in @(Get-OrionSearchEngines $p.path)) { $e | Add-Member -NotePropertyName browser -NotePropertyValue $b -Force; $engines += $e }
      $us = @(Find-OrionUserscripts $p.path)
      $entry.userscripts = $us.Count; $scripts += $us
      $profiles += [pscustomobject]$entry
    }
  }
  foreach ($z in @($ZipPaths)) { if ($z -and (Test-Path -LiteralPath $z)) { $scripts += @(Read-OrionUserscriptZip $z) } }
  $saved = @(Save-OrionUserscripts $scripts (Join-Path $Move "browser\userscripts"))

  # one line per extension (the same extension in two profiles once)
  $uniq = [ordered]@{}; foreach ($x in $allExt) { if (-not $uniq.Contains($x.id)) { $uniq[$x.id] = $x } }
  Write-OrionJson (Join-Path $Move "browser\extensions.json") @($uniq.Values)
  $eu = [ordered]@{}; foreach ($e in $engines) { $k = "$($e.url)"; if (-not $eu.Contains($k)) { $eu[$k] = $e } }
  Write-OrionJson (Join-Path $Move "browser\search-engines.json") @($eu.Values)
  Write-OrionJson (Join-Path $Move "browser\userscripts.json") @($saved)

  return [ordered]@{
    apps_ranked = "apps/ranked.json"; winget = $(if (Test-Path (Join-Path $Move "apps\winget.json")) { "apps/winget.json" } else { $null })
    apps_used = @($ranked | Where-Object { $_.used }).Count; apps_to_install = @($ranked | Where-Object { $_.install -and ($_.used -or -not @($Usage).Count) }).Count
    browser_profiles = @($profiles); extensions = "browser/extensions.json"; extensions_count = $uniq.Count
    firefox_installs = @($uniq.Values | Where-Object { $_.firefox -eq "install" }).Count
    unknown_extensions = @($uniq.Values | Where-Object { $_.firefox -eq "unknown" } | ForEach-Object { $_.name })
    search_engines = "browser/search-engines.json"; userscripts = "browser/userscripts.json"; userscripts_count = $saved.Count
    left_out = @("saved passwords", "cookies and sign-ins", "autofill and card details", "browsing history", "open tabs and sessions", "values your userscripts saved")
  }
}

# The plain words shown at the end and written into README.txt.
function Get-OrionKitSummary($Kit) {
  $lines = @()
  $lines += "Apps: $($Kit.apps_used) you use, $($Kit.apps_to_install) to install on OrionOS (most used first)."
  foreach ($p in @($Kit.browser_profiles)) {
    $lines += "Browser $($p.browser) ($($p.name)): $($p.bookmarks) bookmarks, $($p.extensions) extensions, $($p.userscripts) userscripts found."
  }
  $lines += "Firefox: $($Kit.firefox_installs) extensions to add, $($Kit.userscripts_count) userscripts to install in Tampermonkey."
  if (@($Kit.unknown_extensions).Count) { $lines += "Extensions to look up yourself: " + (@($Kit.unknown_extensions) -join ", ") }
  $lines += "Left out on purpose: " + (@($Kit.left_out) -join ", ") + "."
  return $lines
}

$script:PasswordHelp = @"
Your passwords (never in this kit)
  The move kit never reads or copies saved passwords, cookies or sign-ins. To bring your passwords:
  - If you use a password manager (Bitwarden, 1Password, ...): just sign in to it in Firefox. Done.
  - Otherwise, in Chrome: Settings > Passwords (Google Password Manager) > Settings > Export passwords.
    It saves a .csv file that is NOT locked: keep it off this drive, and delete it once Firefox has it.
    In Firefox on OrionOS: Passwords (about:logins) > the ... menu > Import from a File, pick the .csv.
Your Tampermonkey scripts
  This kit has the scripts Orion could find. The sure way: in Chrome, Tampermonkey > Utilities > Zip > Export,
  then run "Move my stuff to Orion" again (it picks the zip up from Downloads). In Firefox, Tampermonkey >
  Utilities > Import from file takes the same zip, including the values your scripts saved.
"@

if ($MyInvocation.InvocationName -eq ".") { return }

# Run on its own: make just the kit, through the main move script.
$fwd = @{ KitOnly = $true }
if ($To) { $fwd.Target = $To }
if ($UserscriptZip) { $fwd.TampermonkeyZip = $UserscriptZip }
& (Join-Path $PSScriptRoot "Move-ToOrion.ps1") @fwd
