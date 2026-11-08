<#
.SYNOPSIS
  Creates the two OrionOS virtual machines in VirtualBox: orion-lab (Orion as the OS) and
  orion-boardroom (the sign-off station). Run it with Windows PowerShell 5.1 (the .cmd launcher does).

.DESCRIPTION
  1. Finds VirtualBox 7 (offers to install it with winget if missing).
  2. Downloads the official Ubuntu 24.04 cloud image once and checks its SHA-256.
  3. Packs this repo's daemon, boardroom client, OrionOS scripts and the HUD .deb into a bundle.
  4. Builds a small first-boot disc per VM (cloud-init "CIDATA") with a random admin password.
  5. Creates a private NAT network "OrionNet" (10.77.0.0/24): lab = .10, boardroom = .11, and the
     Windows host's own 127.0.0.1 appears as 10.77.0.2 (so the lab can use Ollama running on Windows).
  6. Creates and starts both VMs. First boot installs everything (15-30 min), then reboots:
       orion-lab        -> boots straight into the Orion HUD
       orion-boardroom  -> boots into the sign-off console

  Nothing is shared between Windows and the VMs: clipboard, drag-and-drop and shared folders are off.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File os\windows\New-OrionVMs.ps1
  powershell -ExecutionPolicy Bypass -File os\windows\New-OrionVMs.ps1 -TailscaleAuthKey tskey-auth-xxxx
#>
[CmdletBinding()]
param(
  [string]$TailscaleAuthKey = "",
  [string]$VmRoot = (Join-Path $env:USERPROFILE "VirtualBox VMs\OrionOS"),
  [int]$LabMemoryMB = 6144,
  [int]$LabCpus = 4,
  [int]$LabDiskGB = 40,
  [int]$BoardroomMemoryMB = 2048,
  [int]$BoardroomDiskGB = 12,
  [switch]$Recreate,
  [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is 10x slower with the progress bar
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ImageUrl = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.vmdk"
$SumsUrl = "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS"
$Net = "OrionNet"

function Say($msg, $color = "Cyan") { Write-Host "[OrionOS] $msg" -ForegroundColor $color }

# ------------------------------------------------------------------ 1. VirtualBox
$VBox = $null
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
if (-not $VBox) {
  Say "VirtualBox isn't installed. It's free (Oracle) and runs on Windows Home." "Yellow"
  $ans = Read-Host "Install it now with winget? [Y/n]"
  if ($ans -eq "" -or $ans -match "^[Yy]") {
    winget install -e --id Oracle.VirtualBox --accept-package-agreements --accept-source-agreements
    Say "VirtualBox installed. Restart Windows if it asks, then run this script again." "Green"
  } else {
    Say "Install VirtualBox 7 from https://www.virtualbox.org/wiki/Downloads, then run this again." "Yellow"
  }
  exit 1
}
# Windows PowerShell 5.1 turns a native program's error output into a fatal error when
# $ErrorActionPreference is "Stop", so both helpers relax it and judge VBoxManage by its exit code only.
function VB {
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $VBox @args; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
  if ($code -ne 0) { throw "VBoxManage $($args -join ' ') failed ($code)" }
}
function VBQuiet {
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { & $VBox @args 2>&1 | Out-Null; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
  return $code
}
$ver = (& $VBox --version)
Say "VirtualBox $ver"
if ($ver -notmatch "^7\.") { Say "VirtualBox 7.x is required (found $ver)." "Red"; exit 1 }

# ------------------------------------------------------------------ 2. Ubuntu cloud image
$Cache = Join-Path $VmRoot "cache"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$Vmdk = Join-Path $Cache "noble-server-cloudimg-amd64.vmdk"
# Save the checksum list to a file and read it as text (Windows PowerShell 5.1 returns this file as raw bytes).
$SumsFile = Join-Path $Cache "SHA256SUMS"
Invoke-WebRequest -UseBasicParsing $SumsUrl -OutFile $SumsFile
$want = $null
foreach ($line in (Get-Content $SumsFile)) {
  if ($line -match '^([0-9a-fA-F]{64})\s+\*?noble-server-cloudimg-amd64\.vmdk\s*$') { $want = $Matches[1].ToLower(); break }
}
if (-not $want) { throw "Couldn't find the image checksum in $SumsUrl" }
if ((Test-Path $Vmdk) -and ((Get-FileHash $Vmdk -Algorithm SHA256).Hash.ToLower() -ne $want)) {
  Say "Cached image is out of date; downloading again." "Yellow"
  Remove-Item $Vmdk
}
if (-not (Test-Path $Vmdk)) {
  Say "Downloading Ubuntu 24.04 cloud image (~570 MB) ..."
  Invoke-WebRequest -UseBasicParsing $ImageUrl -OutFile "$Vmdk.part"
  Move-Item -Force "$Vmdk.part" $Vmdk
}
$got = (Get-FileHash $Vmdk -Algorithm SHA256).Hash.ToLower()
if ($got -ne $want) { Remove-Item $Vmdk; throw "Image checksum mismatch (got $got, want $want). Deleted it; run again." }
Say "Image verified (SHA-256 $($want.Substring(0,16))...)" "Green"

# ------------------------------------------------------------------ 3. Bundle
$Deb = Get-ChildItem (Join-Path $Repo "os\dist") -Filter "*.deb" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Deb) { Say "No HUD .deb in os\dist: the lab will boot without the HUD. (Build it: see os\README.md)" "Yellow" }
$Stage = Join-Path $Cache "bundle"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path "$Stage\apps", "$Stage\dist" | Out-Null
Copy-Item -Recurse (Join-Path $Repo "apps\daemon") "$Stage\apps\daemon"
Copy-Item -Recurse (Join-Path $Repo "apps\boardroom") "$Stage\apps\boardroom"
Copy-Item -Recurse (Join-Path $Repo "os") "$Stage\os"
foreach ($junk in @("apps\daemon\.venv", "apps\daemon\.env", "apps\daemon\__pycache__", "apps\daemon\tests\__pycache__", "apps\daemon\.pytest_cache", "os\dist")) {
  $p = Join-Path $Stage $junk; if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
if ($Deb) { Copy-Item $Deb.FullName "$Stage\dist\" }
Set-Content -Path "$Stage\BUNDLE_VERSION" -Value (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK") -Encoding ascii
$Tar = Join-Path $Cache "orion-bundle.tar.gz"
if (Test-Path $Tar) { Remove-Item $Tar }
& tar.exe -czf $Tar -C $Stage .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
Say ("Bundle ready ({0:N1} MB)" -f ((Get-Item $Tar).Length / 1MB)) "Green"

# ------------------------------------------------------------------ 4. Seed discs (cloud-init NoCloud)
if (-not ("OrionIso" -as [type])) {
  Add-Type -TypeDefinition @"
using System; using System.IO; using System.Runtime.InteropServices.ComTypes;
public static class OrionIso {
  public static void Write(string path, object comStream, int blockSize, int blocks) {
    IStream s = (IStream)comStream; byte[] buf = new byte[blockSize];
    using (FileStream o = File.Create(path)) { while (blocks-- > 0) { s.Read(buf, blockSize, IntPtr.Zero); o.Write(buf, 0, blockSize); } }
  }
}
"@
}
function New-SeedIso([string]$Dir, [string]$IsoPath) {
  $fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
  $fsi.FileSystemsToCreate = 3          # ISO9660 + Joliet (Linux reads the Joliet names)
  $fsi.FreeMediaBlocks = 0              # no size cap
  $fsi.VolumeName = "CIDATA"            # cloud-init's NoCloud label
  $fsi.Root.AddTree($Dir, $false)
  $img = $fsi.CreateResultImage()
  [OrionIso]::Write($IsoPath, $img.ImageStream, $img.BlockSize, $img.TotalBlocks)
  # IMAPI2 keeps the source files open until its COM objects are released.
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($img)
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($fsi)
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
function New-Password {
  $chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $bytes = New-Object byte[] 20; $rng.GetBytes($bytes)
  -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
$SshKeys = @()
foreach ($k in @("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")) {
  $p = Join-Path $env:USERPROFILE ".ssh\$k"
  if (Test-Path $p) { $SshKeys += (Get-Content $p -Raw).Trim() }
}
$KeysYaml = ($SshKeys | ForEach-Object { '"' + ($_ -replace '"', '') + '"' }) -join ", "

function New-Seed([string]$Name, [string]$Template, [string]$Password, [string]$Ip) {
  $dir = Join-Path $Cache "seed-$Name"
  if (Test-Path $dir) { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue }
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $ts = "true"
  if ($TailscaleAuthKey) { $ts = "tailscale up --authkey='$TailscaleAuthKey' --hostname=$Name --ssh || true" }
  $ud = (Get-Content (Join-Path $Repo "os\cloud-init\$Template") -Raw)
  $ud = $ud.Replace("__SSH_KEYS__", $KeysYaml).Replace("__ADMIN_PASSWORD__", $Password).Replace("__TAILSCALE_UP__", $ts)
  # LF line endings and no BOM: cloud-init must see "#cloud-config" as the very first bytes.
  [IO.File]::WriteAllText((Join-Path $dir "user-data"), ($ud -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
  $md = "instance-id: $Name-$([guid]::NewGuid().ToString('N').Substring(0,8))`nlocal-hostname: $Name`n"
  [IO.File]::WriteAllText((Join-Path $dir "meta-data"), $md, (New-Object Text.UTF8Encoding($false)))
  # Static address on OrionNet so the two VMs always find each other (gateway/DNS proxy = .1).
  $nc = "version: 2`nethernets:`n  orionnet:`n    match: {name: `"en*`"}`n    dhcp4: false`n    addresses: [$Ip/24]`n    routes: [{to: default, via: 10.77.0.1}]`n    nameservers: {addresses: [1.1.1.1, 9.9.9.9, 10.77.0.1]}`n"
  [IO.File]::WriteAllText((Join-Path $dir "network-config"), $nc, (New-Object Text.UTF8Encoding($false)))
  Copy-Item $Tar (Join-Path $dir "orion-bundle.tar.gz")
  $iso = Join-Path $VmRoot "$Name-seed.iso"
  if (Test-Path $iso) { Remove-Item $iso }
  New-SeedIso $dir $iso
  Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue   # the plain-text seed folder isn't kept
  return $iso
}

# ------------------------------------------------------------------ 5. Private network
if (-not ((& $VBox natnetwork list) -match "Name:\s+$Net\b")) {
  Say "Creating NAT network $Net (10.77.0.0/24)"
  VB natnetwork add --netname $Net --network "10.77.0.0/24" --enable --dhcp on
}
# Host loopback -> 10.77.0.2 (Ollama on Windows); SSH from Windows: ssh -p 2222 orion-admin@127.0.0.1
if ((VBQuiet natnetwork modify --netname $Net --loopback-4 "127.0.0.1=2") -ne 0) {
  Say "Note: couldn't map Windows' 127.0.0.1 into OrionNet; Orion on the lab won't see Ollama on Windows yet (everything else works)." "Yellow"
}
VBQuiet natnetwork modify --netname $Net --port-forward-4 delete lab-ssh | Out-Null
VB natnetwork modify --netname $Net --port-forward-4 "lab-ssh:tcp:[127.0.0.1]:2222:[10.77.0.10]:22"
VBQuiet natnetwork modify --netname $Net --port-forward-4 delete boardroom-ssh | Out-Null
VB natnetwork modify --netname $Net --port-forward-4 "boardroom-ssh:tcp:[127.0.0.1]:2223:[10.77.0.11]:22"

# ------------------------------------------------------------------ 6. VMs
function New-OrionVm([string]$Name, [int]$MemMB, [int]$Cpus, [int]$DiskGB, [string]$Ip, [string]$Template, [int]$Vram) {
  $exists = (& $VBox list vms) -match "`"$Name`""
  if ($exists -and -not $Recreate) {
    Say "$Name already exists (use -Recreate to rebuild it from scratch). Skipping." "Yellow"
    return $null
  }
  if ($exists) {
    Say "Removing old $Name ..." "Yellow"
    VBQuiet controlvm $Name poweroff | Out-Null
    Start-Sleep -Seconds 2
    VB unregistervm $Name --delete
  }
  $password = New-Password
  $iso = New-Seed $Name $Template $password $Ip
  Say "Creating $Name ($MemMB MB RAM, $Cpus CPUs, $DiskGB GB disk, $Ip)"
  VB createvm --name $Name --ostype Ubuntu_64 --basefolder $VmRoot --register
  VB modifyvm $Name --memory $MemMB --cpus $Cpus --vram $Vram --graphicscontroller vmsvga `
    --accelerate-3d off --firmware bios --rtc-use-utc on --ioapic on `
    --nic1 natnetwork --nat-network1 $Net --nic-type1 virtio `
    --mouse usbtablet --usb-ohci on --audio-enabled off `
    --clipboard-mode disabled --drag-and-drop disabled `
    --uart1 0x3F8 4 --uart-mode1 file (Join-Path $VmRoot "$Name\console.log")
  $vdi = Join-Path $VmRoot "$Name\$Name.vdi"
  VB clonemedium disk $Vmdk $vdi --format VDI
  VB modifymedium disk $vdi --resize ($DiskGB * 1024)
  if ($Name -eq "orion-lab") {
    # The lab gets sound and your microphone (for Orion's voice and the hot mic). The boardroom stays silent.
    VB modifyvm $Name --audio-enabled on --audio-controller hda --audio-in on --audio-out on
  }
  VB storagectl $Name --name SATA --add sata --controller IntelAhci --portcount 2
  VB storageattach $Name --storagectl SATA --port 0 --device 0 --type hdd --medium $vdi
  VB storageattach $Name --storagectl SATA --port 1 --device 0 --type dvddrive --medium $iso
  # Belt and braces: also reserve the address in OrionNet's DHCP (the seed already sets it statically).
  VBQuiet dhcpserver modify --network=$Net --vm=$Name --nic=1 --fixed-address=$Ip | Out-Null
  return $password
}

$labPw = New-OrionVm "orion-lab" $LabMemoryMB $LabCpus $LabDiskGB "10.77.0.10" "lab-user-data.yaml" 128
$brPw = New-OrionVm "orion-boardroom" $BoardroomMemoryMB 2 $BoardroomDiskGB "10.77.0.11" "boardroom-user-data.yaml" 16

if (-not $NoStart) {
  foreach ($n in @("orion-boardroom", "orion-lab")) {
    if (-not ((& $VBox list runningvms) -match "`"$n`"")) { VB startvm $n --type gui }
  }
}

Write-Host ""
Say "Both VMs are installing themselves now (15-30 minutes; they reboot once when done)." "Green"
Say "Progress log on Windows: $VmRoot\orion-lab\console.log" "Green"
if ($labPw -or $brPw) {
  Write-Host ""
  Write-Host "  One-time admin passwords (user: orion-admin). You'll be asked to change them at first login:" -ForegroundColor Yellow
  if ($labPw) { Write-Host "    orion-lab        $labPw" -ForegroundColor Yellow }
  if ($brPw) { Write-Host "    orion-boardroom  $brPw" -ForegroundColor Yellow }
  Write-Host "  They aren't saved anywhere. Note them now." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next (also in os\README.md):"
Write-Host "  1. orion-boardroom console: choose the key passphrase. It then asks for a pairing code."
Write-Host "  2. orion-lab: press Ctrl+Alt+T for a terminal, run  orion-ctl pair-boardroom  and type that code on the boardroom."
Write-Host "  3. Both screens must show the same key fingerprint. Then approve or reject decisions on the boardroom."
