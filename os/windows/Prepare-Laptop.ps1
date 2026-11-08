<#
.SYNOPSIS
  Gets this laptop ready for OrionOS on its own partition (dual boot with Windows). Changes nothing on your disk.

.DESCRIPTION
  1. Checks: free space, UEFI + Secure Boot, BitLocker / Device Encryption, Fast Startup, the boot-menu key.
  2. If the drive is encrypted, shows the recovery key and makes sure you've saved it.
  3. Turns off Fast Startup (with your OK) so Windows and OrionOS can share the drive safely.
  4. Opens Disk Management and tells you exactly what to click to make room. YOU do the shrink.
  5. Downloads Ubuntu 24.04 (checksum-verified) and opens Rufus to make the installer stick. YOU press Start.
  6. Writes "OrionOS install steps.txt" to your Desktop with the rest.
#>
param([int]$SizeGB = 100)

$ErrorActionPreference = "Stop"
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }
function Ask($q) { $a = Read-Host "$q [Y/n]"; return ($a -eq "" -or $a -match "^[Yy]") }

# ------------------------------------------------------------------ admin (needed to read encryption and boot settings)
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Say "Windows will ask for admin permission (to read encryption and boot settings)."
  Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-SizeGB", $SizeGB
  exit 0
}

Write-Host ""
Say "Checking this laptop for OrionOS (nothing is changed without asking)." "Green"
$problems = @()

# ------------------------------------------------------------------ 1. space
$cdrive = Get-Partition -DriveLetter C
$disk = Get-Disk -Number $cdrive.DiskNumber
$used = (Get-Partition -DiskNumber $disk.Number | Measure-Object -Property Size -Sum).Sum
$unalloc = [math]::Round(($disk.Size - $used) / 1GB)
$vol = Get-Volume -DriveLetter C
$freeGB = [math]::Round($vol.SizeRemaining / 1GB)
Say ("Disk: {0} ({1} GB). C: has {2} GB free; {3} GB not yet used by any partition." -f $disk.FriendlyName, [math]::Round($disk.Size / 1GB), $freeGB, $unalloc)
$needShrink = $unalloc -lt 64
if ($needShrink -and $freeGB -lt ($SizeGB + 30)) {
  $problems += "C: needs at least $($SizeGB + 30) GB free to give OrionOS $SizeGB GB (it has $freeGB). Free some space, or use a smaller size (-SizeGB 64)."
}

# ------------------------------------------------------------------ 2. UEFI + Secure Boot
$uefi = $env:firmware_type -eq "UEFI"
if (-not $env:firmware_type) { $uefi = (bcdedit /enum "{current}" | Select-String "winload.efi") -ne $null }
$sb = $null; try { $sb = Confirm-SecureBootUEFI } catch { }
Say ("Firmware: {0}; Secure Boot: {1}" -f $(if ($uefi) { "UEFI (good)" } else { "Legacy BIOS" }), $(if ($sb) { "on (fine: Ubuntu is signed)" } elseif ($sb -eq $false) { "off" } else { "unknown" }))
if (-not $uefi) { $problems += "This laptop starts Windows in old BIOS mode. Dual boot still works, but ask Claude before continuing." }

# ------------------------------------------------------------------ 3. encryption
$enc = $false
try {
  $bl = Get-BitLockerVolume -MountPoint "C:" -ErrorAction Stop
  $enc = $bl.ProtectionStatus -eq "On" -or $bl.VolumeStatus -ne "FullyDecrypted"
} catch {
  $txt = (manage-bde -status C: 2>$null) -join "`n"
  $enc = $txt -match "Protection On" -or $txt -match "Percentage Encrypted:\s+[1-9]"
}
if ($enc) {
  Write-Host ""
  Say "C: is encrypted (BitLocker / Device Encryption). Changing partitions can make Windows ask for the RECOVERY KEY." "Yellow"
  $keys = (manage-bde -protectors -get C: -Type RecoveryPassword 2>$null) -join "`n"
  $m = [regex]::Matches($keys, "\d{6}(-\d{6}){7}")
  if ($m.Count -gt 0) {
    Say "Your recovery key (write it on paper or take a photo with your phone, NOT a file on this PC):" "Yellow"
    $m | ForEach-Object { Write-Host "    $($_.Value)" -ForegroundColor White }
  }
  Say "It's also in your Microsoft account: https://account.microsoft.com/devices/recoverykey" "Yellow"
  $ok = Read-Host "Type SAVED once the key is written down somewhere off this laptop"
  if ($ok -ne "SAVED") { Say "Stopping here. Run this again when the recovery key is saved." "Red"; Read-Host "Press Enter"; exit 1 }
}

# ------------------------------------------------------------------ 4. Fast Startup
$pw = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power"
$fast = (Get-ItemProperty -Path $pw -Name HiberbootEnabled -ErrorAction SilentlyContinue).HiberbootEnabled -eq 1
if ($fast) {
  Say "Windows Fast Startup is on. It leaves the disk half-asleep, which can corrupt files shared with OrionOS." "Yellow"
  if (Ask "Turn Fast Startup off? (Windows starts a few seconds slower)") {
    Set-ItemProperty -Path $pw -Name HiberbootEnabled -Value 0
    Say "Fast Startup is off." "Green"
  } else { $problems += "Fast Startup is still on. Turn it off before installing (Control Panel > Power Options)." }
}

# ------------------------------------------------------------------ 5. boot-menu key for this make
$cs = Get-CimInstance Win32_ComputerSystem
$make = "$($cs.Manufacturer) $($cs.Model)"
$key = switch -Regex ($cs.Manufacturer) {
  "Dell" { "F12" } "HP|Hewlett" { "Esc, then F9" } "Lenovo" { "F12 (or Fn+F12; some models: the small Novo button)" }
  "ASUS" { "Esc (or F8)" } "Acer" { "F12 (turn on 'F12 Boot Menu' in BIOS first if nothing appears)" }
  "MSI|Micro-Star" { "F11" } "Microsoft" { "hold Volume Down while pressing Power" } "Samsung" { "F10" }
  "Gigabyte" { "F12" } "Razer" { "F12" } "Framework" { "F12" } "Toshiba|Dynabook" { "F12" }
  default { "F12, F11, F9 or Esc (one of these; watch the first screen)" }
}
Say "This is a $make. Its boot-menu key: $key"

# ------------------------------------------------------------------ problems so far
if ($problems.Count) {
  Write-Host ""
  $problems | ForEach-Object { Say "Fix first: $_" "Red" }
  Read-Host "Press Enter to close"; exit 1
}

# ------------------------------------------------------------------ 6. make room (you click)
Write-Host ""
if ($needShrink) {
  Say "Next: make room for OrionOS. Disk Management opens now. Do exactly this:" "Green"
  Say "  1. Right-click the C: box (bottom half of the window) > Shrink Volume..." "Green"
  Say ("  2. In 'Enter the amount of space to shrink in MB' type: {0}" -f ($SizeGB * 1024)) "Green"
  Say "  3. Click Shrink. A black 'Unallocated' box appears. Leave it empty: Ubuntu's installer uses it." "Green"
  Start-Process diskmgmt.msc
  Read-Host "Press Enter here when the Unallocated space is there"
} else {
  Say "There's already $unalloc GB of unallocated space: no shrinking needed." "Green"
}

# ------------------------------------------------------------------ 7. Ubuntu + Rufus
$dl = Join-Path $env:USERPROFILE "Downloads"
$base = "https://releases.ubuntu.com/24.04/"
Say "Finding the newest Ubuntu 24.04 release..."
$sums = Join-Path $env:TEMP "ubuntu-SHA256SUMS"
Invoke-WebRequest -UseBasicParsing -Uri ($base + "SHA256SUMS") -OutFile $sums
$line = Get-Content $sums | Where-Object { $_ -match "ubuntu-24\.04(\.\d+)?-desktop-amd64\.iso" } | Select-Object -Last 1
if (-not $line -or $line -notmatch "^([0-9a-f]{64})\s+\*?(\S+)$") { Say "Couldn't read Ubuntu's release list." "Red"; exit 1 }
$hash = $Matches[1]; $isoName = $Matches[2]; $iso = Join-Path $dl $isoName
if ((Test-Path $iso) -and ((Get-FileHash $iso -Algorithm SHA256).Hash.ToLower() -eq $hash)) {
  Say "$isoName is already downloaded and checks out."
} else {
  Say "Downloading $isoName (about 6 GB; keep this window open)..."
  $ProgressPreference = "SilentlyContinue"
  Invoke-WebRequest -UseBasicParsing -Uri ($base + $isoName) -OutFile $iso
  if ((Get-FileHash $iso -Algorithm SHA256).Hash.ToLower() -ne $hash) { Remove-Item $iso; Say "Download was damaged; run this again." "Red"; exit 1 }
  Say "Downloaded and verified." "Green"
}
$rufus = Get-Command rufus.exe -ErrorAction SilentlyContinue
if (-not $rufus) {
  $rufus = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "rufus*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $rufus) {
  Say "Installing Rufus (the tool that makes the installer stick)..."
  winget install --id Rufus.Rufus -e --accept-source-agreements --accept-package-agreements | Out-Host
  $rufus = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter "rufus*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
Write-Host ""
Say "Plug in a USB stick you don't need (8 GB or more; it will be ERASED)." "Yellow"
Read-Host "Press Enter when it's plugged in"
Say "Rufus opens next. Check: Device = your stick, Boot selection = $isoName. Press START, accept the defaults (ISO mode), wait for READY." "Green"
$rpath = if ($rufus.Source) { $rufus.Source } else { $rufus.FullName }
if ($rpath) { Start-Process $rpath -ArgumentList "-i", "`"$iso`"" } else { Say "Couldn't start Rufus; get it from https://rufus.ie" "Red" }

# ------------------------------------------------------------------ 8. the rest, on paper
$steps = @"
OrionOS on this laptop - the rest of the steps
================================================
1. Leave the Ubuntu stick plugged in. Restart the laptop and tap $key right away.
2. Pick the USB stick (it may say UEFI: <name of the stick>).
3. Choose "Try or Install Ubuntu", then "Install Ubuntu".
4. When asked how to install: "Install Ubuntu alongside Windows Boot Manager"
   (or "Manual" and pick the Unallocated space). NEVER "Erase disk".
5. Create your Ubuntu user, finish, restart, remove the stick.
6. At the boot menu pick Ubuntu. Log in, open Terminal (Ctrl+Alt+T) and paste:

   wget -O /tmp/install-orion.sh https://raw.githubusercontent.com/orionopenwow/OrionOS/main/os/baremetal/install-orion.sh && sudo bash /tmp/install-orion.sh

7. It asks for an orion-admin password, sets up OrionOS (10-20 min) and restarts into Orion.
8. Pair your phone: Ctrl+Alt+S > Pair my phone (same Wi-Fi as the laptop).

Every start: the menu lets you choose OrionOS (Ubuntu) or Windows Boot Manager.
$(if ($enc) { "If Windows asks for a BitLocker recovery key after this, type the key you saved." })
"@
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "OrionOS install steps.txt"
Set-Content -Path $out -Value $steps -Encoding UTF8
Write-Host ""
Say "Saved the rest of the steps to your Desktop: OrionOS install steps.txt (also take a photo of it)." "Green"
Read-Host "Press Enter to close"
