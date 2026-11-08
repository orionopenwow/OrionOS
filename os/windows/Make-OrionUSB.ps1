<#
.SYNOPSIS
  Orion USB Maker: puts OrionOS on a USB stick.

.DESCRIPTION
  1. Finds the OrionOS image: one you built (os/image/out), one in Downloads, or the newest release from GitHub
     (downloaded in parts, signature-checked if minisign.exe is available, otherwise SHA-256 checked).
  2. Shows only USB drives. You pick one and type its number again to confirm: it will be ERASED.
  3. Writes the image and reads it back to check it.
  The first time the stick starts OrionOS, it offers to make your encrypted vault in the rest of the stick.

  Prefer a different tool? The .img works with balenaEtcher, or Rufus in "DD image" mode.
#>
param([string]$Image = "", [string]$Repo = "orionopenwow/OrionOS", [string]$Flavour = "standard", [switch]$NoVerify)

$ErrorActionPreference = "Stop"
function Say($m, $c = "Cyan") { Write-Host "[Orion USB] $m" -ForegroundColor $c }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Say "Windows will ask for admin permission (needed to write a whole USB drive)."
  $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Repo", $Repo, "-Flavour", $Flavour)
  if ($Image) { $a += @("-Image", "`"$Image`"") }
  if ($NoVerify) { $a += "-NoVerify" }
  Start-Process powershell.exe -Verb RunAs -ArgumentList $a
  exit 0
}
$Here = $PSScriptRoot
$RepoDir = (Resolve-Path (Join-Path $Here "..\..")).Path

# ------------------------------------------------------------------ 1. the image
if (-not $Image) {
  $cands = @(Get-ChildItem (Join-Path $RepoDir "os\image\out") -Filter "orionos-*-$Flavour.img" -ErrorAction SilentlyContinue) +
           @(Get-ChildItem (Join-Path $env:USERPROFILE "Downloads") -Filter "orionos-*-$Flavour.img" -ErrorAction SilentlyContinue)
  $Image = ($cands | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Image) {
  $dl = Join-Path $env:USERPROFILE "Downloads\OrionOS"
  New-Item -ItemType Directory -Force -Path $dl | Out-Null
  $base = "https://github.com/$Repo/releases/latest/download"
  $ProgressPreference = "SilentlyContinue"
  Say "Downloading the newest OrionOS ($Flavour) from GitHub (about 2.5 GB, in parts)..."
  $parts = (Invoke-WebRequest -UseBasicParsing "$base/orionos-$Flavour-img.parts").Content -split "`n" | Where-Object { $_.Trim() }
  $tarxz = Join-Path $dl "orionos-$Flavour.img.tar.xz"
  $out = [System.IO.File]::Create($tarxz)
  try {
    foreach ($p in $parts) {
      $p = $p.Trim()
      if ($p -notmatch '^orionos-[\w.-]+\.img\.tar\.xz\.part-[a-z]{2}$') { throw "Unexpected file in the release list: $p" }
      Say "  $p"
      $tmp = Join-Path $dl $p
      Invoke-WebRequest -UseBasicParsing "$base/$p" -OutFile $tmp
      $in = [System.IO.File]::OpenRead($tmp); $in.CopyTo($out); $in.Close(); Remove-Item $tmp
    }
  } finally { $out.Close() }
  $sums = (Invoke-WebRequest -UseBasicParsing "$base/SHA256SUMS-$Flavour").Content
  $want = ([regex]::Match($sums, "([0-9a-f]{64})\s+\*?orionos-[\w.-]+\.img\.tar\.xz")).Groups[1].Value
  $got = (Get-FileHash $tarxz -Algorithm SHA256).Hash.ToLower()
  if (-not $want -or $want -ne $got) { Remove-Item $tarxz; throw "The download is damaged (checksum mismatch). Run this again." }
  $minisign = Get-Command minisign.exe -ErrorAction SilentlyContinue
  $pub = Join-Path $RepoDir "os\image\release.pub"
  if ($minisign -and (Test-Path $pub)) {
    Invoke-WebRequest -UseBasicParsing "$base/orionos-$Flavour.img.tar.xz.minisig" -OutFile "$tarxz.minisig"
    & $minisign.Source -Vm $tarxz -p $pub -q
    if ($LASTEXITCODE -ne 0) { throw "Signature check FAILED. Don't use this download." }
    Say "Signature checked." "Green"
  } else { Say "Checksum OK. (Install minisign to also check the release signature.)" "Yellow" }
  Say "Unpacking..."
  tar.exe -xf $tarxz -C $dl
  Remove-Item $tarxz
  $Image = (Get-ChildItem $dl -Filter "orionos-*-$Flavour.img" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
$imgLen = (Get-Item $Image).Length
Say ("Image: {0} ({1:N1} GB)" -f (Split-Path $Image -Leaf), ($imgLen / 1GB))

# ------------------------------------------------------------------ 2. the stick
$usb = Get-Disk | Where-Object { $_.BusType -eq "USB" -and $_.Size -gt $imgLen }
if (-not $usb) { Say "No USB drive big enough found. Plug one in (32 GB or more recommended) and run this again." "Red"; Read-Host "Press Enter"; exit 1 }
Write-Host ""
$usb | ForEach-Object { Write-Host ("  Disk {0}:  {1}  ({2:N0} GB)" -f $_.Number, $_.FriendlyName, ($_.Size / 1GB)) }
Write-Host ""
$n = Read-Host "Which disk number is your USB stick?"
$disk = $usb | Where-Object { "$($_.Number)" -eq $n.Trim() }
if (-not $disk) { Say "That isn't one of the USB drives listed. Nothing was changed." "Red"; Read-Host "Press Enter"; exit 1 }
if ($disk.IsBoot -or $disk.IsSystem) { Say "That's the drive Windows runs from. Refusing." "Red"; exit 1 }
if ($disk.Size -lt 32GB) { Say "This stick is under 32 GB: OrionOS fits, but there'll be little room for your vault." "Yellow" }
Say ("EVERYTHING on Disk {0} ({1}, {2:N0} GB) will be erased." -f $disk.Number, $disk.FriendlyName, ($disk.Size / 1GB)) "Yellow"
$c = Read-Host "Type the disk number again to confirm"
if ($c.Trim() -ne "$($disk.Number)") { Say "Didn't match. Nothing was changed." "Red"; Read-Host "Press Enter"; exit 1 }

# ------------------------------------------------------------------ 3. write
Say "Preparing the stick..."
Clear-Disk -Number $disk.Number -RemoveData -RemoveOEM -Confirm:$false -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$path = "\\.\PhysicalDrive$($disk.Number)"
$bufSize = 4MB
$buf = New-Object byte[] $bufSize
$src = [System.IO.File]::OpenRead($Image)
$dst = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite, $bufSize, [System.IO.FileOptions]::WriteThrough)
$sw = [Diagnostics.Stopwatch]::StartNew(); $done = 0
try {
  while (($r = $src.Read($buf, 0, $bufSize)) -gt 0) {
    if ($r % 512 -ne 0) { $r2 = [math]::Ceiling($r / 512) * 512; [Array]::Clear($buf, $r, $r2 - $r); $r = $r2 }
    $dst.Write($buf, 0, $r)
    $done += $r
    if ($sw.ElapsedMilliseconds -gt 0) {
      Write-Progress -Activity "Writing OrionOS to the stick" -Status ("{0:N1} of {1:N1} GB, {2:N0} MB/s" -f ($done / 1GB), ($imgLen / 1GB), ($done / 1MB / $sw.Elapsed.TotalSeconds)) -PercentComplete ([math]::Min(100, $done * 100 / $imgLen))
    }
  }
  $dst.Flush()
} finally { $src.Close(); $dst.Close() }
Write-Progress -Activity "Writing OrionOS to the stick" -Completed
Say ("Written in {0:N0} minutes." -f $sw.Elapsed.TotalMinutes) "Green"

# ------------------------------------------------------------------ 4. read back and compare
if (-not $NoVerify) {
  Say "Checking what was written..."
  $a = [System.IO.File]::OpenRead($Image)
  $b = New-Object System.IO.FileStream($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite, $bufSize)
  $h1 = [Security.Cryptography.SHA256]::Create(); $h2 = [Security.Cryptography.SHA256]::Create()
  $buf2 = New-Object byte[] $bufSize; $left = $imgLen
  try {
    while ($left -gt 0) {
      $want = [int][math]::Min($bufSize, [math]::Ceiling($left / 512) * 512)
      $ra = $a.Read($buf, 0, [int][math]::Min($bufSize, $left))
      $rb = 0; while ($rb -lt $want) { $x = $b.Read($buf2, $rb, $want - $rb); if ($x -le 0) { break }; $rb += $x }
      [void]$h1.TransformBlock($buf, 0, $ra, $null, 0); [void]$h2.TransformBlock($buf2, 0, $ra, $null, 0)
      $left -= $ra
    }
    [void]$h1.TransformFinalBlock(@(), 0, 0); [void]$h2.TransformFinalBlock(@(), 0, 0)
  } finally { $a.Close(); $b.Close() }
  if ([BitConverter]::ToString($h1.Hash) -ne [BitConverter]::ToString($h2.Hash)) { Say "The stick doesn't match the image. Try another USB port or stick." "Red"; Read-Host "Press Enter"; exit 1 }
  Say "Verified." "Green"
}
Write-Host ""
Say "Your OrionOS stick is ready." "Green"
Say "Start any PC from it (boot-menu key: F12, F9, F11 or Esc). Choose 'OrionOS', and it will offer to make your encrypted vault." "Green"
Say "Windows may say the stick needs formatting: click Cancel. It's fine; Windows just can't read Linux drives." "Yellow"
Read-Host "Press Enter to close"
