# Apply-OrionUpdate.ps1: puts an Orion update that Claude delivered into this folder, safely.
#   "Apply Orion update.cmd" runs this. It reads _incoming\orion-update.tgz and _incoming\orion-update.manifest.
# Before writing anything it checks every file it will replace is still the version the update was made against
# (so edits you or another tool made since are never silently lost), keeps a backup of every replaced file in
# _incoming\backup-<date>\, then unpacks the update with Windows' built-in tar. Nothing is deleted, nothing is
# committed or pushed.
param(
  [string]$Bundle = "",
  [string]$Manifest = "",
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Bundle)   { $Bundle   = Join-Path $Repo "_incoming\orion-update.tgz" }
if (-not $Manifest) { $Manifest = Join-Path $Repo "_incoming\orion-update.manifest" }

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

if (-not (Test-Path $Bundle) -or -not (Test-Path $Manifest)) {
  Say "No update waiting. Claude puts it in the _incoming folder as orion-update.tgz and orion-update.manifest." Yellow
  exit 1
}
if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
  Say "This needs Windows 10 (1803) or newer, which has tar built in." Red
  exit 1
}

# The same fingerprint Claude used: SHA-256 of the file with carriage returns removed (line endings don't count).
function FileHash([string]$path) {
  $bytes = [IO.File]::ReadAllBytes($path)
  $lf = New-Object System.Collections.Generic.List[byte] ($bytes.Length)
  foreach ($b in $bytes) { if ($b -ne 13) { $lf.Add($b) } }
  $sha = [Security.Cryptography.SHA256]::Create()
  return ([BitConverter]::ToString($sha.ComputeHash($lf.ToArray())) -replace "-", "").ToLowerInvariant()
}

$changed = @(); $added = @(); $conflicts = @()
foreach ($line in Get-Content -LiteralPath $Manifest -Encoding UTF8) {
  if (-not $line.Trim() -or $line.StartsWith("#")) { continue }
  $parts = $line -split "  ", 2
  if ($parts.Count -ne 2) { continue }
  $expect = $parts[0].Trim(); $rel = $parts[1].Trim()
  if ($rel.Contains("..") -or [IO.Path]::IsPathRooted($rel)) { Say "Skipping a strange path in the manifest: $rel" Yellow; continue }
  $full = Join-Path $Repo ($rel -replace "/", "\")
  if ($expect -eq "NEW") {
    $added += $rel
    if (Test-Path -LiteralPath $full) { $conflicts += "$rel (new in this update, but a file with that name is already here)" }
    continue
  }
  $changed += $rel
  if (-not (Test-Path -LiteralPath $full)) { continue }   # gone: it will simply be written
  if ((FileHash $full) -ne $expect) { $conflicts += "$rel (changed since Claude's last update)" }
}

Say ""
Say "Orion update: $($changed.Count) files updated, $($added.Count) new files." Cyan
if ($conflicts.Count -gt 0 -and -not $Force) {
  Say ""
  Say "These files were changed here since the update was made:" Yellow
  $conflicts | ForEach-Object { Say "  - $_" Yellow }
  Say ""
  Say "If you go ahead, each one is backed up first, so nothing is lost." Gray
  $answer = Read-Host "Type YES to apply anyway, or press Enter to stop"
  if ($answer -ne "YES") { Say "Stopped. Nothing was changed. Tell Claude which files are listed above." Yellow; exit 2 }
}

# Back up every file that will be replaced.
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $Repo "_incoming\backup-$stamp"
$saved = 0
foreach ($rel in ($changed + $added)) {
  $full = Join-Path $Repo ($rel -replace "/", "\")
  if (Test-Path -LiteralPath $full) {
    $dest = Join-Path $backup ($rel -replace "/", "\")
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $full -Destination $dest -Force
    $saved++
  }
}
if ($saved -gt 0) { Say "Backed up $saved files to _incoming\backup-$stamp" Gray }

# Unpack.
& tar.exe -xzf $Bundle -C $Repo
if ($LASTEXITCODE -ne 0) { Say "Unpacking failed (tar exit $LASTEXITCODE). Your backup is in _incoming\backup-$stamp." Red; exit 3 }

# Check every file landed.
$missing = @()
foreach ($rel in ($changed + $added)) { if (-not (Test-Path -LiteralPath (Join-Path $Repo ($rel -replace "/", "\")))) { $missing += $rel } }
if ($missing.Count -gt 0) {
  Say "These files didn't arrive:" Red; $missing | ForEach-Object { Say "  - $_" Red }; exit 4
}
$done = Join-Path $Repo "_incoming\orion-update.applied-$stamp.manifest"
Move-Item -LiteralPath $Manifest -Destination $done -Force
Say ""
Say "Done. Orion is up to date. Next: double-click Test Orion.cmd (close any old Orion windows first)." Green
exit 0
