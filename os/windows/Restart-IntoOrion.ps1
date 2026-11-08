<#
.SYNOPSIS
  Restarts this PC into OrionOS once. Windows stays the normal choice; the next restart after that is Windows again.

.DESCRIPTION
  OrionOS's installer keeps Windows first in the PC's boot list (safest with BitLocker) and adds "OrionOS" second.
  This sets the PC's one-time "boot next" choice to OrionOS (bcdedit bootsequence on the firmware boot manager),
  the same thing as picking it in the boot menu by hand. It changes nothing permanently.
#>
param([switch]$Yes)
$ErrorActionPreference = "Stop"
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Say "Windows will ask for admin permission (to choose what starts next)."
  $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
  if ($Yes) { $a += "-Yes" }
  Start-Process powershell.exe -Verb RunAs -ArgumentList $a
  exit 0
}

# Find the firmware entry called OrionOS
$text = (& bcdedit /enum firmware) -join "`n"
$guid = $null
foreach ($block in ($text -split "`n\s*`n")) {
  if ($block -match "(?im)^description\s+OrionOS\s*$" -and $block -match "(?im)^identifier\s+(\{[0-9a-f\-]+\})") { $guid = $Matches[1]; break }
}
if (-not $guid) {
  Say "OrionOS isn't in this PC's boot list. Install it first (see docs\install\3-side-by-side-with-windows.md)," "Yellow"
  Say "or start it from the boot menu key (F12, F9, F11 or Esc) while the PC starts." "Yellow"
  Read-Host "Press Enter to close" | Out-Null
  exit 1
}

if (-not $Yes) {
  Say "Your PC will restart into OrionOS now. Save your work first."
  $r = Read-Host "Restart now? [Y/n]"
  if ($r -and $r -notmatch "^[Yy]") { Say "Nothing changed."; exit 0 }
}
& bcdedit /set "{fwbootmgr}" bootsequence $guid | Out-Null
if ($LASTEXITCODE -ne 0) { Say "Windows wouldn't set the one-time choice. Use the boot menu key instead." "Red"; exit 1 }
Say "Restarting into OrionOS..." "Green"
& shutdown.exe /r /t 3 /c "Restarting into OrionOS"
