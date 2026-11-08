<#
.SYNOPSIS
  Checks whether this PC is ready for OrionOS. Changes nothing.

.DESCRIPTION
  Reports, in plain words (and saves "Orion readiness report.txt" to your Desktop):
    - start-up mode (UEFI) and Secure Boot
    - disks: size, free space and partitions; BitLocker / Device Encryption
    - memory
    - Wi-Fi, Bluetooth, graphics, sound, touchpad and fingerprint chips, checked against a list of how well they
      work on Linux (hardware-map.json), and which OrionOS image to use (standard or nvidia)
    - your installed apps: which have a Linux version, a web version, run under Wine/Proton, or have no
      replacement (app-map.json)
  and ends with one of: "Ready to replace Windows", "Ready for side by side", or "Keep Windows for now because ...".

  Other scripts dot-source this file to reuse its functions (Move-ToOrion.ps1); then nothing runs.
#>
param([switch]$NoElevate, [string]$Out = "")

$ErrorActionPreference = "Stop"
$script:OrionHere = $PSScriptRoot

# ================================================================== functions (also used by Move-ToOrion.ps1)
function Read-OrionMap([string]$Name) {
  $p = Join-Path $script:OrionHere $Name
  return (Get-Content -Raw -Encoding UTF8 $p | ConvertFrom-Json)
}

# "PCI\VEN_8086&DEV_2723&..." -> Bus PCI, Vendor 8086, Device 2723 (lower case). Also USB, HDAUDIO and ACPI ids.
function ConvertTo-OrionId([string]$PnpId) {
  $r = @{ Bus = ""; Vendor = ""; Device = ""; Acpi = "" }
  if (-not $PnpId) { return $r }
  $u = $PnpId.ToUpper()
  if ($u -match '^PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})') { $r.Bus = "PCI"; $r.Vendor = $Matches[1].ToLower(); $r.Device = $Matches[2].ToLower() }
  elseif ($u -match '^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})') { $r.Bus = "USB"; $r.Vendor = $Matches[1].ToLower(); $r.Device = $Matches[2].ToLower() }
  elseif ($u -match '^HDAUDIO\\.*VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})') { $r.Bus = "HDA"; $r.Vendor = $Matches[1].ToLower(); $r.Device = $Matches[2].ToLower() }
  elseif ($u -match '^ACPI\\([0-9A-Z]+)') { $r.Bus = "ACPI"; $r.Acpi = $Matches[1] }
  return $r
}

# Which kind of chip a Windows device is (wifi, ethernet, bluetooth, gpu, sound, touchpad, fingerprint), or "".
function Get-OrionKind([string]$Class, [string]$Name, [string]$PnpId) {
  $id = "$PnpId".ToUpper()
  $phys = $id -match '^(PCI|USB)\\'
  if ($id -match '^ACPI\\(CSC3551|TIAS2781)') { return "sound" }
  switch ("$Class") {
    "Net" {
      if (-not $phys) { return "" }
      if ($Name -match 'Wi-?Fi|Wireless|WLAN|802\.11') { return "wifi" }
      if ($Name -match 'Ethernet|GbE|Gigabit|2\.5G|Family Controller|Network Connection|LAN') { return "ethernet" }
      return ""
    }
    "Display" { if ($id -match '^PCI\\') { return "gpu" } else { return "" } }
    "Bluetooth" { if ($phys) { return "bluetooth" } else { return "" } }
    "MEDIA" { if ($id -match '^(HDAUDIO\\FUNC_01|PCI\\)') { return "sound" } else { return "" } }
    "Biometric" { return "fingerprint" }
  }
  if ($Name -match 'touch ?pad|TrackPad|Precision Touchpad') { return "touchpad" }
  return ""
}

# First rule in hardware-map.json that fits this chip.
function Find-OrionRule($Rules, [string]$Kind, $Id) {
  foreach ($r in $Rules) {
    if ($r.kind -ne $Kind) { continue }
    if ($r.bus -and $r.bus -ne $Id.Bus) { continue }
    if ($r.vendor -and $r.vendor -ne $Id.Vendor) { continue }
    if ($r.device -and (@($r.device) -notcontains $Id.Device)) { continue }
    if ($r.acpi -and -not ($Id.Acpi -and $Id.Acpi.StartsWith($r.acpi))) { continue }
    return $r
  }
  return $null
}

# The app-map.json entry for an installed app's name; "ignore" for system parts; $null when it's not in the list.
function Find-OrionApp($Map, [string]$Name) {
  foreach ($pat in $Map.ignore) { if ($Name -match $pat) { return "ignore" } }
  foreach ($a in $Map.apps) {
    foreach ($pat in $a.match) { if ($Name -match $pat) { return $a } }
  }
  return $null
}

# Installed apps from the registry (all users + you) and Store apps that are in the map.
function Get-OrionInstalledApps($Map) {
  $keys = @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*")
  $seen = @{}; $list = @()
  foreach ($k in $keys) {
    foreach ($e in @(Get-ItemProperty -Path $k -ErrorAction SilentlyContinue)) {
      $n = "$($e.DisplayName)".Trim()
      if (-not $n -or $e.SystemComponent -eq 1 -or $e.ParentKeyName -or $seen.ContainsKey($n)) { continue }
      $seen[$n] = $true
      $list += [pscustomobject]@{ name = $n; version = "$($e.DisplayVersion)"; publisher = "$($e.Publisher)"; source = "installed" }
    }
  }
  try {
    foreach ($p in @(Get-AppxPackage -ErrorAction Stop | Where-Object { -not $_.IsFramework -and $_.SignatureKind -eq "Store" })) {
      $m = Find-OrionApp $Map $p.Name
      if ($m -and $m -ne "ignore" -and -not $seen.ContainsKey($m.name)) {
        $seen[$m.name] = $true
        $list += [pscustomobject]@{ name = $m.name; version = "$($p.Version)"; publisher = "$($p.Publisher)"; source = "store" }
      }
    }
  } catch { }
  return ($list | Sort-Object name)
}

# Each installed app with what to use on OrionOS (kind: builtin, linux, web, wine, proton, none, unknown).
function Get-OrionAppPlan($Map, $Apps) {
  $plan = @()
  foreach ($a in $Apps) {
    $m = Find-OrionApp $Map $a.name
    if ($m -eq "ignore") { continue }
    if ($m) {
      $plan += [pscustomobject]@{ name = $a.name; match = $m.name; kind = $m.kind; how = $m.how; flatpak = "$($m.flatpak)"
                                  recipe = "$($m.recipe)"; tool = "$($m.tool)"; web = "$($m.web)" }
    } else {
      $plan += [pscustomobject]@{ name = $a.name; match = ""; kind = "unknown"; how = "Not in Orion's list yet: search for it in Get apps."
                                  flatpak = ""; recipe = ""; tool = ""; web = "" }
    }
  }
  return $plan
}

# The final answer. $F: hashtable with Uefi, RamGB, DiskGB, FreeGB, UnallocGB, Devices (kind/status/name), Apps (plan).
function Get-OrionVerdict($F) {
  $keep = @(); $side = @()
  $hasCable = @($F.Devices | Where-Object { $_.kind -eq "ethernet" }).Count -gt 0
  if (-not $F.Uefi) { $keep += "this PC starts in old BIOS mode, and OrionOS needs UEFI start-up" }
  if ($F.RamGB -lt 4) { $keep += "it has $($F.RamGB) GB of memory and OrionOS needs at least 4 GB" }
  if ($F.DiskGB -lt 64) { $keep += "the drive is only $($F.DiskGB) GB and OrionOS needs at least 64 GB" }
  foreach ($d in @($F.Devices | Where-Object { $_.kind -eq "wifi" -and $_.status -eq "problem" })) {
    if ($hasCable) { $side += "the Wi-Fi chip ($($d.name)) doesn't work on Linux yet (a cable or USB Wi-Fi stick works)" }
    else { $keep += "the Wi-Fi chip ($($d.name)) doesn't work on Linux yet and there's no network socket (a USB Wi-Fi stick fixes this)" }
  }
  foreach ($d in @($F.Devices | Where-Object { $_.kind -eq "wifi" -and $_.status -eq "firmware" })) {
    $side += "the Wi-Fi chip ($($d.name)) may need an extra driver, so try OrionOS from the USB stick first"
  }
  $none = @($F.Apps | Where-Object { $_.kind -eq "none" } | ForEach-Object { $_.match } | Select-Object -Unique)
  if ($none.Count) { $side += ("these have no OrionOS replacement: " + ($none -join ", ")) }
  if ($keep.Count) { return @{ Verdict = "Keep Windows for now because " + ($keep -join "; and ") + "."; Reasons = $keep + $side; Level = "keep" } }
  if ($side.Count) {
    $room = $F.UnallocGB -ge 64 -or $F.FreeGB -ge 94
    if (-not $room) {
      return @{ Verdict = "Keep Windows for now because " + ($side -join "; and ") + ", and there isn't room for side by side yet (C: needs about 94 GB free; it has $($F.FreeGB) GB)."
                Reasons = $side; Level = "keep" }
    }
    return @{ Verdict = "Ready for side by side (keep Windows too) because " + ($side -join "; and ") + "."; Reasons = $side; Level = "side" }
  }
  return @{ Verdict = "Ready to replace Windows."; Reasons = @(); Level = "replace" }
}

# Dot-sourced (". .\Check-OrionReady.ps1"): stop here, only the functions above are wanted.
if ($MyInvocation.InvocationName -eq ".") { return }

# ================================================================== the check
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $NoElevate) {
  Write-Host "[Orion] Windows will ask for admin permission (only to read Secure Boot and encryption settings; nothing is changed)." -ForegroundColor Cyan
  try {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-NoElevate"
    exit 0
  } catch { Write-Host "[Orion] No admin permission: checking what can be read without it." -ForegroundColor Yellow }
}

$script:Report = New-Object System.Collections.Generic.List[string]
function Line([string]$Text = "", [string]$Color = "Gray") { Write-Host $Text -ForegroundColor $Color; $script:Report.Add($Text) }
function Head([string]$Text) { Line ""; Line $Text "Cyan"; Line ("-" * $Text.Length) "Cyan" }
function Mark([string]$Status) { switch ($Status) { "good" { "[ok]  " } "firmware" { "[note]" } "problem" { "[no]  " } default { "[?]   " } } }
function Colour([string]$Status) { switch ($Status) { "good" { "Green" } "firmware" { "Yellow" } "problem" { "Red" } default { "Gray" } } }

$hw = Read-OrionMap "hardware-map.json"
$map = Read-OrionMap "app-map.json"
Line "Is this PC ready for OrionOS?  ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))" "White"
Line "Nothing on this PC was changed by this check."

# ------------------------------------------------------------------ start-up and Secure Boot
Head "Start-up"
$uefi = $env:firmware_type -eq "UEFI"
if (-not $env:firmware_type) { try { $uefi = [bool](bcdedit /enum "{current}" 2>$null | Select-String "winload.efi") } catch { } }
$sb = $null; try { $sb = Confirm-SecureBootUEFI } catch { }
if ($uefi) { Line "[ok]   Starts in UEFI mode (good)." "Green" } else { Line "[no]   Starts in old BIOS mode. OrionOS needs UEFI." "Red" }
if ($sb -eq $true) { Line "[ok]   Secure Boot is on. That's fine: OrionOS starts with it on." "Green" }
elseif ($sb -eq $false) { Line "[ok]   Secure Boot is off. That's fine too." "Green" }
else { Line "[?]    Couldn't read Secure Boot (needs admin). Either way works." }

# ------------------------------------------------------------------ disks
Head "Drives"
$cpart = $null; try { $cpart = Get-Partition -DriveLetter C -ErrorAction Stop } catch { }
$diskGB = 0; $freeGB = 0; $unallocGB = 0
foreach ($d in @(Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.BusType -ne "USB" } | Sort-Object Number)) {
  $sizeGB = [math]::Round($d.Size / 1GB)
  $parts = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue)
  $used = ($parts | Measure-Object -Property Size -Sum).Sum
  $un = [math]::Round(($d.Size - $used) / 1GB)
  $isC = $cpart -and $cpart.DiskNumber -eq $d.Number
  Line ("Drive {0}: {1}, {2} GB, {3}{4}" -f $d.Number, "$($d.FriendlyName)".Trim(), $sizeGB, $d.PartitionStyle, $(if ($isC) { " (Windows is on this one)" } else { "" }))
  foreach ($p in $parts) {
    $what = switch -Regex ("$($p.GptType) $($p.Type)") {
      "c12a7328|System" { "start-up files (EFI)" } "e3c9e316|Reserved" { "reserved by Windows" }
      "de94bba4|Recovery" { "Windows recovery" } "0fc63daf" { "Linux" } default { "data" }
    }
    $letter = if ($p.DriveLetter -and "$($p.DriveLetter)" -match '[A-Z]') { "$($p.DriveLetter): " } else { "" }
    Line ("    part {0}: {1}{2}, {3}" -f $p.PartitionNumber, $letter, $what, $(if ($p.Size -ge 1GB) { "$([math]::Round($p.Size / 1GB)) GB" } else { "$([math]::Round($p.Size / 1MB)) MB" }))
  }
  if ($un -gt 0) { Line "    $un GB not used by any partition" }
  if ($isC) { $diskGB = $sizeGB; $unallocGB = $un }
}
try { $freeGB = [math]::Round((Get-Volume -DriveLetter C).SizeRemaining / 1GB) } catch { }
Line "C: has $freeGB GB free."
if ($unallocGB -ge 64) { Line "[ok]   $unallocGB GB is already free for OrionOS side by side." "Green" }
elseif ($freeGB -ge 94) { Line "[ok]   Enough room for side by side: 'Prepare Laptop for Orion.cmd' shows you how to make the space." "Green" }
else { Line "[note] Side by side needs about 94 GB free on C: (it has $freeGB GB). Replacing Windows doesn't need this." "Yellow" }

# ------------------------------------------------------------------ BitLocker
$enc = $null
try {
  $bl = Get-BitLockerVolume -MountPoint "C:" -ErrorAction Stop
  $enc = $bl.ProtectionStatus -eq "On" -or $bl.VolumeStatus -ne "FullyDecrypted"
} catch {
  try { $txt = (manage-bde -status C: 2>$null) -join "`n"; if ($txt) { $enc = $txt -match "Protection On" -or $txt -match "Percentage Encrypted:\s+[1-9]" } } catch { }
}
if ($enc -eq $true) {
  Line "[note] C: is encrypted (BitLocker / Device Encryption)." "Yellow"
  Line "       Before changing anything, save the RECOVERY KEY somewhere off this PC (paper or a phone photo)." "Yellow"
  Line "       'Prepare Laptop for Orion.cmd' shows the key and makes sure it's saved. It's also at" "Yellow"
  Line "       https://account.microsoft.com/devices/recoverykey" "Yellow"
} elseif ($enc -eq $false) { Line "[ok]   C: isn't encrypted." "Green" }
else { Line "[?]    Couldn't read encryption (needs admin)." }

# ------------------------------------------------------------------ memory
Head "Memory"
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($ramGB -ge 16) { Line "[ok]   $ramGB GB: plenty, including Orion's private on-device brain." "Green" }
elseif ($ramGB -ge 8) { Line "[ok]   $ramGB GB: good. Orion's on-device brain uses a smaller model." "Green" }
elseif ($ramGB -ge 4) { Line "[note] $ramGB GB: OrionOS runs, but use an API key for Orion's brain instead of the on-device one." "Yellow" }
else { Line "[no]   $ramGB GB: too little (OrionOS needs at least 4 GB)." "Red" }

# ------------------------------------------------------------------ chips
Head "Chips (how well they work on Linux)"
$devices = @(); $seenDev = @{}
foreach ($e in @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue)) {
  $kind = Get-OrionKind "$($e.PNPClass)" "$($e.Name)" "$($e.PNPDeviceID)"
  if (-not $kind) { continue }
  $id = ConvertTo-OrionId "$($e.PNPDeviceID)"
  $key = "$kind|$($id.Bus)|$($id.Vendor)|$($id.Device)|$($id.Acpi)"
  if ($kind -eq "touchpad") { $key = "touchpad" }
  if ($seenDev.ContainsKey($key)) { continue }
  $seenDev[$key] = $true
  $rule = Find-OrionRule $hw.rules $kind $id
  $status = if ($rule) { $rule.status } else { "unknown" }
  $idText = if ($id.Vendor) { "$($id.Bus) $($id.Vendor):$($id.Device)" } elseif ($id.Acpi) { "ACPI $($id.Acpi)" } else { "" }
  $devices += [pscustomobject]@{ kind = $kind; name = "$($e.Name)".Trim(); id = $idText; status = $status
                                 note = "$($rule.note)"; flavour = "$($rule.flavour)" }
}
$labels = [ordered]@{ wifi = "Wi-Fi"; ethernet = "Wired network"; bluetooth = "Bluetooth"; gpu = "Graphics"; sound = "Sound"; touchpad = "Touchpad"; fingerprint = "Fingerprint reader" }
foreach ($k in $labels.Keys) {
  $these = @($devices | Where-Object { $_.kind -eq $k })
  if (-not $these.Count) { if ($k -eq "wifi") { Line "[?]    $($labels[$k]): none found." } ; continue }
  foreach ($d in $these) {
    Line ("{0} {1}: {2}{3}" -f (Mark $d.status), $labels[$k], $d.name, $(if ($d.id) { "  ($($d.id))" } else { "" })) (Colour $d.status)
    if ($d.note) { Line "       $($d.note)" }
  }
}
$flavour = if (@($devices | Where-Object { $_.kind -eq "gpu" -and $_.flavour -eq "nvidia" }).Count) { "nvidia" } else { "standard" }
Line ""
Line "Which OrionOS image: $flavour" "White"
if ($flavour -eq "nvidia") { Line "       (In 'Make Orion USB.cmd' add  -Flavour nvidia  , or download the nvidia image.)" }

# ------------------------------------------------------------------ apps
Head "Your apps on OrionOS"
$apps = Get-OrionInstalledApps $map
$plan = @(Get-OrionAppPlan $map $apps)
$groups = [ordered]@{ builtin = "Already in OrionOS (or not needed)"; linux = "Have a Linux version (Get apps installs them)"; web = "Use the web version"
                      wine = "Run under Bottles (Wine)"; proton = "Run in Steam with Proton"; none = "No replacement yet"; unknown = "Not in Orion's list (look in Get apps)" }
foreach ($g in $groups.Keys) {
  $these = @($plan | Where-Object { $_.kind -eq $g })
  if (-not $these.Count) { continue }
  $col = switch ($g) { "none" { "Red" } "unknown" { "Gray" } "wine" { "Yellow" } default { "Green" } }
  Line ""; Line "$($groups[$g]) ($($these.Count)):" $col
  if ($g -eq "unknown") { Line ("    " + (($these | ForEach-Object { $_.name }) -join ", ")); continue }
  foreach ($a in $these) { Line "    $($a.name): $($a.how)" }
}
if (-not $plan.Count) { Line "Couldn't read the installed-apps list." }

# ------------------------------------------------------------------ verdict
$v = Get-OrionVerdict @{ Uefi = $uefi; RamGB = $ramGB; DiskGB = $diskGB; FreeGB = $freeGB; UnallocGB = $unallocGB; Devices = $devices; Apps = $plan }
Head "Verdict"
$vc = switch ($v.Level) { "replace" { "Green" } "side" { "Cyan" } default { "Yellow" } }
Line $v.Verdict $vc
Line ""
switch ($v.Level) {
  "replace" { Line "Next: docs\install\4-replace-windows.md (Move my stuff, make the USB stick, install, move in)." }
  "side" { Line "Next: docs\install\3-side-by-side-with-windows.md (Prepare Laptop for Orion.cmd, then the USB stick)." }
  default { Line "You can still try OrionOS without changing anything: docs\install\1-try-it-in-a-vm.md or 2-usb-stick.md." }
}
if ($enc -eq $true) { Line "Remember: save the BitLocker recovery key before changing partitions." "Yellow" }

if (-not $Out) { $Out = Join-Path ([Environment]::GetFolderPath("Desktop")) "Orion readiness report.txt" }
try {
  Set-Content -Path $Out -Value $script:Report -Encoding UTF8
  Write-Host ""; Write-Host "[Orion] Saved this report to: $Out" -ForegroundColor Green
} catch { Write-Host "[Orion] Couldn't save the report: $($_.Exception.Message)" -ForegroundColor Yellow }
if (-not $env:ORION_NO_PAUSE) { Read-Host "Press Enter to close" | Out-Null }
