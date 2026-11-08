<#
.SYNOPSIS
  Copies your stuff from Windows to a USB drive or external disk, ready for OrionOS ("Move my stuff to Orion").

.DESCRIPTION
  Makes a folder called OrionMove on the drive you pick, with:
    files\     Documents, Desktop, Pictures, Videos, Music (and Downloads if you say so)
    extra\     any other folder you add (for example your WoW 1.12 client folder)
    bookmarks\ the bookmark files of Chrome, Edge, Brave and Firefox (never passwords, cookies or history)
    wifi\      the names of your Wi-Fi networks. Passwords only if you say yes, and then only inside
               passwords.enc, locked with a passphrase you choose
    apps\      the list of your installed apps, most used first, and what to use for each on OrionOS
    browser\   your Chrome / Edge / Brave set-up for Firefox: bookmarks (Firefox's import format), extensions and
               their Firefox versions, search engines, Tampermonkey userscripts (Export-ForOrion.ps1). Never
               passwords, cookies, sign-ins, history or what scripts saved
    boardroom\ the orion-boardroom VirtualBox disk, only if you ask (it keeps the boardroom's signing key)
    manifest.json, SHA256SUMS (a checksum of every file, so OrionOS can tell nothing got damaged), README.txt
  It only copies. Nothing on Windows (or in Chrome) is changed or deleted.
  -KitOnly makes just the move kit (apps, browser, Wi-Fi names; not your files). That's the choice for a dual-boot
  PC (put it on the Windows drive: OrionOS reads your files from there itself) or a VM's shared folder.
  On OrionOS, "orion-move-in" (Settings > Bring my stuff from Windows) finds the folder and moves you in.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File os\windows\Move-ToOrion.ps1
  powershell -ExecutionPolicy Bypass -File os\windows\Move-ToOrion.ps1 -Target E:\ -Downloads -Extra "C:\Games\WoW 1.12"
  powershell -ExecutionPolicy Bypass -File os\windows\Move-ToOrion.ps1 -KitOnly -Target C:\
#>
param(
  [string]$Target = "",
  [switch]$Downloads,
  [string[]]$Extra = @(),
  [switch]$WifiPasswords,
  [switch]$Boardroom,
  [switch]$KitOnly,
  [string]$TampermonkeyZip = "",
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
$script:MoveHere = $PSScriptRoot
. (Join-Path $PSScriptRoot "Check-OrionReady.ps1")   # app list helpers (nothing runs)
. (Join-Path $PSScriptRoot "Export-ForOrion.ps1")    # [move-kit] most-used apps + Chrome to Firefox (nothing runs)

# ================================================================== functions (tests dot-source this file)
# Locks bytes with a passphrase in the same format as:  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -md sha256
# ("Salted__", 8 random bytes, then AES-256-CBC; key and IV from PBKDF2-HMAC-SHA256). OrionOS opens it with openssl.
function Protect-OrionBytes([byte[]]$Plain, [string]$Pass) {
  $salt = New-Object byte[] 8
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($salt)
  $kdf = New-Object System.Security.Cryptography.Rfc2898DeriveBytes($Pass, $salt, 200000, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $kb = $kdf.GetBytes(48)
  $aes = [System.Security.Cryptography.Aes]::Create()
  $aes.Mode = [System.Security.Cryptography.CipherMode]::CBC
  $aes.Padding = [System.Security.Cryptography.PaddingMode]::PKCS7
  $aes.Key = [byte[]]$kb[0..31]; $aes.IV = [byte[]]$kb[32..47]
  $ct = $aes.CreateEncryptor().TransformFinalBlock($Plain, 0, $Plain.Length)
  $head = [System.Text.Encoding]::ASCII.GetBytes("Salted__")
  return [byte[]]($head + $salt + $ct)
}

# Wi-Fi profiles from an "netsh wlan export profile" folder -> list of {name, ssid, auth, key}.
function Read-OrionWifiExport([string]$Dir) {
  $out = @()
  foreach ($f in @(Get-ChildItem -Path $Dir -Filter *.xml -ErrorAction SilentlyContinue)) {
    try { [xml]$x = Get-Content -Raw -Encoding UTF8 $f.FullName } catch { continue }
    $p = $x.WLANProfile
    if (-not $p) { continue }
    $ssid = "$($p.SSIDConfig.SSID.name)"; if (-not $ssid) { $ssid = "$($p.name)" }
    $auth = "$($p.MSM.security.authEncryption.authentication)"
    # Only a key Windows exported in clear text (key=clear, as admin); an encrypted blob is useless elsewhere.
    $key = ""
    if ("$($p.MSM.security.sharedKey.protected)" -eq "false") { $key = "$($p.MSM.security.sharedKey.keyMaterial)" }
    $out += [pscustomobject]@{ name = "$($p.name)"; ssid = $ssid; auth = $auth; key = $key }
  }
  return $out
}

# "relative/path" for SHA256SUMS (forward slashes, like Linux's sha256sum).
function Get-OrionRelPath([string]$Root, [string]$Path) {
  $r = $Path.Substring($Root.TrimEnd('\').Length).TrimStart('\')
  return ($r -replace '\\', '/')
}

if ($MyInvocation.InvocationName -eq ".") { return }

# ================================================================== the move
function Say($m, $c = "Cyan") { Write-Host "[Orion] $m" -ForegroundColor $c }
function Ask($q, [bool]$Default = $true) {
  if ($Yes) { return $Default }
  $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
  $a = Read-Host "$q $hint"
  if ($a -eq "") { return $Default }
  return ($a -match "^[Yy]")
}
function Stop-Here($m) { Say $m "Red"; if (-not $Yes) { Read-Host "Press Enter to close" | Out-Null }; exit 1 }
function Get-FolderSize([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return 0 }
  $s = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -Attributes !ReparsePoint -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
  if ($s) { return [int64]$s } else { return 0 }
}
function GB([int64]$b) { return [math]::Round($b / 1GB, 1) }

Write-Host ""
Say "Move my stuff to Orion: copies your files to a USB drive or external disk. Nothing on Windows is deleted." "Green"

# ------------------------------------------------------------------ 1. where to
$sysLetter = "$env:SystemDrive".TrimEnd(':', '\')
if (-not $Target) {
  $vols = @(Get-Volume | Where-Object { $_.DriveLetter -and "$($_.DriveLetter)" -ne $sysLetter -and $_.FileSystem -and $_.DriveType -in @("Removable", "Fixed") } | Sort-Object DriveLetter)
  Say "Where should your stuff go?"
  Write-Host ("  0) {0}:  this PC's Windows drive: just the move kit (apps, browser), for OrionOS next to Windows" -f $sysLetter)
  $i = 1
  foreach ($v in $vols) {
    $usb = ""
    try { $dsk = Get-Partition -DriveLetter $v.DriveLetter | Get-Disk; if ($dsk.BusType -eq "USB") { $usb = "  (USB)" } } catch { }
    Write-Host ("  {0}) {1}:  {2}  {3}, {4} GB free{5}" -f $i, $v.DriveLetter, $(if ($v.FileSystemLabel) { $v.FileSystemLabel } else { "(no name)" }), $v.FileSystem, (GB $v.SizeRemaining), $usb)
    $i++
  }
  if (-not $vols.Count) { Say "(No USB drive or other disk is plugged in. For your files, plug one in and run this again.)" "Yellow" }
  Write-Host "  Or type a folder (for example a virtual machine's shared folder)."
  $ans = (Read-Host "Number or folder").Trim('"', ' ')
  $n = -1
  if ($ans -match '^\d+$') { $n = [int]$ans }
  if ($n -eq 0) { $Target = "$($sysLetter):\" }
  elseif ($n -ge 1 -and $n -le $vols.Count) { $Target = "$($vols[$n - 1].DriveLetter):\" }
  elseif ($ans -and (Test-Path -LiteralPath $ans -PathType Container)) {
    $Target = $ans
    if (-not $KitOnly) { $KitOnly = Ask "Just the move kit (apps, browser), without your files?" $true }
  }
  else { Stop-Here "That's not one of the numbers or a folder." }
}
if (-not (Test-Path -LiteralPath $Target)) { Stop-Here "Can't find $Target." }
$tvol = $null; if ($Target -match '^[A-Za-z]:') { try { $tvol = Get-Volume -DriveLetter ($Target.Substring(0, 1)) } catch { } }
if ($Target -match '^[A-Za-z]:' -and $Target.Substring(0, 1) -eq $sysLetter -and -not $KitOnly) {
  # [move-kit] The Windows drive itself: only the small kit makes sense (OrionOS next to Windows reads your files there).
  Say "That's the drive Windows is on, so only the move kit goes there (apps, browser set-up, Wi-Fi names)." "Yellow"
  Say "OrionOS next to Windows reads your files straight from this drive (Settings > Open Windows files)." "Yellow"
  $KitOnly = $true
}
if ($KitOnly) { $Downloads = $false; $Extra = @(); $Boardroom = $false }

# ------------------------------------------------------------------ 2. what
$folders = [ordered]@{
  Documents = [Environment]::GetFolderPath("MyDocuments")
  Desktop   = [Environment]::GetFolderPath("Desktop")
  Pictures  = [Environment]::GetFolderPath("MyPictures")
  Videos    = [Environment]::GetFolderPath("MyVideos")
  Music     = [Environment]::GetFolderPath("MyMusic")
}
if ($KitOnly) { $folders = [ordered]@{} }
$dl = Join-Path $env:USERPROFILE "Downloads"
try { $p = (New-Object -ComObject Shell.Application).NameSpace("shell:Downloads").Self.Path; if ($p) { $dl = $p } } catch { }
if (-not $KitOnly -and -not $Downloads -and (Test-Path -LiteralPath $dl)) {
  Say ("Downloads has {0} GB." -f (GB (Get-FolderSize $dl)))
  $Downloads = Ask "Bring Downloads too?" $false
}
if ($Downloads) { $folders["Downloads"] = $dl }
if (-not $KitOnly -and -not $Yes -and -not $Extra.Count) {
  Say "Any other folder to bring? For example your WoW 1.12 client folder (OrionOS can run it), or a projects folder."
  while ($true) {
    $e = (Read-Host "Paste a folder path, or press Enter to skip").Trim('"', ' ')
    if (-not $e) { break }
    if (Test-Path -LiteralPath $e -PathType Container) { $Extra += $e } else { Say "Can't find that folder." "Yellow" }
  }
}
if (-not $WifiPasswords -and -not $Yes) {
  Say "Wi-Fi: the names of your networks always come along. Their passwords only if you say yes."
  Say "They'd be locked with a passphrase you choose, and OrionOS asks for it when you move in."
  $WifiPasswords = Ask "Bring Wi-Fi passwords too?" $false
}
$VBox = $null
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
$brDisk = $null
if ($VBox -and -not $KitOnly) {
  $info = (& $VBox showvminfo orion-boardroom --machinereadable 2>$null) -join "`n"
  if ($LASTEXITCODE -eq 0 -and $info) {
    $m = [regex]::Match($info, '"SATA-0-0"="([^"]+\.(vdi|vmdk))"')
    if ($m.Success) { $brDisk = $m.Groups[1].Value }
    if ($brDisk -and -not $Boardroom -and -not $Yes) {
      Say "Your orion-boardroom VM can come along too, with its signing key (still locked by its passphrase)."
      $Boardroom = Ask "Bring the boardroom VM? (about $(GB (Get-Item $brDisk).Length) GB)" $false
    }
    if ($Boardroom -and $info -notmatch 'VMState="(poweroff|aborted|saved)"') {
      Stop-Here "Shut down orion-boardroom first (in VirtualBox: Machine > ACPI Shutdown), then run this again."
    }
  }
}
if ($Boardroom -and -not $brDisk) { Say "No orion-boardroom VM found; skipping it." "Yellow"; $Boardroom = $false }

# ------------------------------------------------------------------ 3. room
$need = [int64]0
foreach ($k in $folders.Keys) { $need += Get-FolderSize $folders[$k] }
foreach ($e in $Extra) { $need += Get-FolderSize $e }
if ($Boardroom) { $need += (Get-Item $brDisk).Length }
$free = if ($tvol) { [int64]$tvol.SizeRemaining } else { [int64]0 }
Say ("That's about {0} GB. The drive has {1} GB free." -f (GB $need), (GB $free))
if ($tvol -and $need -gt $free -and -not $KitOnly) { Stop-Here "Not enough room. Use a bigger drive, or leave Downloads or some folders out." }
if ($tvol -and $tvol.FileSystem -eq "FAT32") {
  $big = @()
  foreach ($src in @($folders.Values) + $Extra) { if (Test-Path -LiteralPath $src) { $big += @(Get-ChildItem -LiteralPath $src -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Length -ge 4GB }) } }
  if ($Boardroom -and (Get-Item $brDisk).Length -ge 4GB) { $big += Get-Item $brDisk }
  if ($big.Count) { Stop-Here ("This drive uses FAT32, which can't hold files over 4 GB (for example $($big[0].Name)). Format it as exFAT first, or use another drive.") }
}

# [move-kit] Tampermonkey's own export is the sure way to bring userscripts (only its .user.js files are taken).
$zips = @()
if ($TampermonkeyZip) { if (Test-Path -LiteralPath $TampermonkeyZip) { $zips += $TampermonkeyZip } else { Say "Can't find $TampermonkeyZip; going on without it." "Yellow" } }
else {
  $found = @(Find-OrionUserscriptZips)
  if ($found.Count -and (Ask "Use your Tampermonkey export $($found[0].Name) (from Downloads) for your userscripts?" $true)) { $zips += $found[0].FullName }
  elseif (-not $found.Count -and -not $Yes) {
    Say "Tip: for every Tampermonkey script, first do Tampermonkey > Utilities > Zip > Export in Chrome; it's picked up from Downloads."
    Say "Without it, Orion still looks for your scripts in Tampermonkey's files (best effort)."
  }
}

$Move = Join-Path $Target "OrionMove"
if (Test-Path -LiteralPath $Move) {
  if (-not (Ask "There's already an OrionMove folder on this drive. Add to it? (Files already there are kept.)" $true)) { Stop-Here "Stopped. Nothing copied." }
}
if (-not (Ask "Copy now?" $true)) { Stop-Here "Stopped. Nothing copied." }
foreach ($d in @("files", "extra", "bookmarks", "wifi", "apps", "browser")) { New-Item -ItemType Directory -Force -Path (Join-Path $Move $d) | Out-Null }

# ------------------------------------------------------------------ 4. files
$copied = [ordered]@{}; $warnings = @()
function Copy-Tree([string]$Src, [string]$Dst, [string]$Label) {
  Say "Copying $Label ..."
  & robocopy.exe $Src $Dst /E /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XF desktop.ini Thumbs.db | Out-Null
  if ($LASTEXITCODE -ge 8) { $script:warnings += "Some files in $Label couldn't be copied (robocopy code $LASTEXITCODE)."; Say "Some files in $Label couldn't be copied." "Yellow" }
}
foreach ($k in $folders.Keys) {
  $src = $folders[$k]
  if (-not $src -or -not (Test-Path -LiteralPath $src)) { continue }
  Copy-Tree $src (Join-Path $Move "files\$k") $k
  $copied[$k] = $src
}
$extras = @()
foreach ($e in $Extra) {
  $name = Split-Path $e -Leaf
  Copy-Tree $e (Join-Path $Move "extra\$name") $name
  $isWow = (Test-Path (Join-Path $e "WoW.exe")) -or (Test-Path (Join-Path $e "Wow.exe"))
  $extras += [pscustomobject]@{ name = $name; wow_client = [bool]$isWow }
}

# ------------------------------------------------------------------ 5. bookmarks (bookmark files only)
$bm = @()
$chromes = @{ chrome = "$env:LOCALAPPDATA\Google\Chrome\User Data"; edge = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"; brave = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data" }
foreach ($b in $chromes.Keys) {
  foreach ($f in @(Get-ChildItem -Path $chromes[$b] -Filter "Bookmarks" -Recurse -Depth 1 -File -ErrorAction SilentlyContinue)) {
    $prof = ($f.Directory.Name -replace '[^A-Za-z0-9 _-]', '')
    $dst = Join-Path $Move "bookmarks\$b-$prof.json"
    Copy-Item -LiteralPath $f.FullName $dst -Force
    $bm += [pscustomobject]@{ browser = $b; profile = $prof; file = "bookmarks/$b-$prof.json"; format = "chromium-json" }
  }
}
foreach ($p in @(Get-ChildItem -Path "$env:APPDATA\Mozilla\Firefox\Profiles" -Directory -ErrorAction SilentlyContinue)) {
  $last = Get-ChildItem -Path (Join-Path $p.FullName "bookmarkbackups") -Filter "*.jsonlz4" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $last) { continue }
  $prof = ($p.Name -replace '[^A-Za-z0-9._-]', '')
  Copy-Item -LiteralPath $last.FullName (Join-Path $Move "bookmarks\firefox-$prof.jsonlz4") -Force
  $bm += [pscustomobject]@{ browser = "firefox"; profile = $prof; file = "bookmarks/firefox-$prof.jsonlz4"; format = "mozlz4-json" }
}
Say "Bookmarks: $($bm.Count) browser profile(s)."

# ------------------------------------------------------------------ 6. Wi-Fi
$wifi = @(); $wifiLocked = $false
$tmp = Join-Path $env:TEMP ("orion-wifi-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
  if ($WifiPasswords) { & netsh.exe wlan export profile "folder=$tmp" key=clear | Out-Null } else { & netsh.exe wlan export profile "folder=$tmp" | Out-Null }
  $wifi = @(Read-OrionWifiExport $tmp)
} catch { Say "Couldn't read Wi-Fi networks (no Wi-Fi on this PC?)." "Yellow" }
finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }   # only our own temporary export
$names = @($wifi | ForEach-Object { [pscustomobject]@{ ssid = $_.ssid; auth = $_.auth; has_password = [bool]($WifiPasswords -and $_.key) } })
[IO.File]::WriteAllText((Join-Path $Move "wifi\networks.json"), (ConvertTo-Json -InputObject @($names) -Depth 4), (New-Object Text.UTF8Encoding($false)))
if ($WifiPasswords -and -not @($wifi | Where-Object { $_.key }).Count -and $wifi.Count) {
  Say "Windows only shows Wi-Fi passwords to an admin. To bring them, right-click 'Move my stuff to Orion.cmd' > Run as administrator." "Yellow"
}
if ($WifiPasswords -and @($wifi | Where-Object { $_.key }).Count) {
  while ($true) {
    $p1 = Read-Host "Choose a passphrase for the Wi-Fi passwords (8+ characters)" -AsSecureString
    $p2 = Read-Host "Type it again" -AsSecureString
    $a = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p1))
    $b = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($p2))
    if ($a -eq $b -and $a.Length -ge 8) { break }
    Say "They didn't match or were too short." "Yellow"
  }
  $plain = @{ networks = @($wifi | Where-Object { $_.key } | ForEach-Object { @{ ssid = $_.ssid; auth = $_.auth; key = $_.key } }) } | ConvertTo-Json -Depth 4
  $bytes = Protect-OrionBytes ([Text.Encoding]::UTF8.GetBytes($plain)) $a
  [IO.File]::WriteAllBytes((Join-Path $Move "wifi\passwords.enc"), $bytes)
  $plain = $null; $a = $null; $b = $null; $wifi = $null
  $wifiLocked = $true
}
Say "Wi-Fi: $($names.Count) network name(s)$(if ($wifiLocked) { ', passwords locked with your passphrase' })."

# ------------------------------------------------------------------ 7. apps
$map = Read-OrionMap "app-map.json"
$plan = @(Get-OrionAppPlan $map (Get-OrionInstalledApps $map))
[IO.File]::WriteAllText((Join-Path $Move "apps\installed.json"), (ConvertTo-Json -InputObject @($plan) -Depth 4), (New-Object Text.UTF8Encoding($false)))
Say "Apps: $($plan.Count) listed, with what to use on OrionOS."
# [move-kit] most used first, plus the browser set-up for Firefox (read-only; never passwords or cookies)
Say "Working out which apps you use most, and reading your browser set-up (nothing in Chrome changes) ..."
$kit = $null
try { $kit = Export-OrionKit $Move $plan $zips } catch { $warnings += "The move kit's apps/browser part didn't finish: $($_.Exception.Message)"; Say "The apps/browser part didn't finish: $($_.Exception.Message)" "Yellow" }

# ------------------------------------------------------------------ 8. boardroom VM
if ($Boardroom) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Move "boardroom") | Out-Null
  $ext = [IO.Path]::GetExtension($brDisk)
  Say "Copying the boardroom VM's disk ..."
  Copy-Item -LiteralPath $brDisk (Join-Path $Move "boardroom\orion-boardroom$ext") -Force
}

# ------------------------------------------------------------------ 9. manifest, checksums, readme
$manifest = [ordered]@{
  format = 1; made = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"); made_by = "Move-ToOrion.ps1"
  computer = $env:COMPUTERNAME; windows = "$((Get-CimInstance Win32_OperatingSystem).Caption)".Trim()
  folders = @($copied.Keys); extra = @($extras); bookmarks = @($bm)
  wifi = [ordered]@{ networks = "wifi/networks.json"; passwords = $(if ($wifiLocked) { "wifi/passwords.enc" } else { $null }); cipher = "openssl aes-256-cbc pbkdf2 sha256 iter 200000" }
  apps = "apps/installed.json"
  kit = $kit; kit_only = [bool]$KitOnly
  boardroom = $(if ($Boardroom) { "boardroom/orion-boardroom$([IO.Path]::GetExtension($brDisk))" } else { $null })
  warnings = @($warnings)
}
[IO.File]::WriteAllText((Join-Path $Move "manifest.json"), ($manifest | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
$readme = @"
OrionMove: your stuff from Windows, ready for OrionOS
======================================================
Made $($manifest.made) on $env:COMPUTERNAME by "Move my stuff to Orion.cmd". Nothing on Windows was deleted.

On OrionOS: plug in this drive, then Settings (Ctrl+Alt+S) > Bring my stuff from Windows
(or type  orion-move-in  in a terminal). It checks every file against SHA256SUMS, copies your files into
your home folders (never over a file that's already there), imports your bookmarks into Firefox, adds your
Wi-Fi networks, and offers the Linux versions of your apps.

What's in the move kit
$(if ($kit) { (Get-OrionKitSummary $kit) -join "`r`n" })

$script:PasswordHelp
$(if ($wifiLocked) { "wifi\passwords.enc holds your Wi-Fi passwords, locked with your passphrase. Keep this drive safe." })
$(if ($Boardroom) { "boardroom\ holds the boardroom VM. Its signing key is locked by its passphrase, but keep this drive safe" + "`r`nand wipe it once you've moved in." })
"@
Set-Content -Path (Join-Path $Move "README.txt") -Value $readme -Encoding UTF8

Say "Writing checksums (so OrionOS can tell nothing got damaged) ..."
$sums = New-Object System.Collections.Generic.List[string]
foreach ($f in @(Get-ChildItem -LiteralPath $Move -Recurse -File -Force -ErrorAction SilentlyContinue)) {
  if ($f.Name -eq "SHA256SUMS" -and $f.DirectoryName -eq $Move) { continue }
  $h = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLower()
  $sums.Add("$h  $(Get-OrionRelPath $Move $f.FullName)")
}
[IO.File]::WriteAllText((Join-Path $Move "SHA256SUMS"), (($sums -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Say "Done: $Move ($($sums.Count) files). Nothing on Windows was changed or deleted." "Green"
if ($kit) { foreach ($l in (Get-OrionKitSummary $kit)) { Say $l } }
Say "Passwords are never copied. To bring them: see README.txt in $Move (or just sign in to your password manager in Firefox)."
foreach ($w in $warnings) { Say $w "Yellow" }
Say "Next: on OrionOS, plug in this drive and open Settings > Bring my stuff from Windows (or run orion-move-in)." "Green"
if ($KitOnly) { Say "On OrionOS next to Windows, the first start finds the kit on the Windows drive by itself." "Green" }
if (-not $Yes) { Read-Host "Press Enter to close" | Out-Null }
