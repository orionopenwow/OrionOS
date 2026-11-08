<#
.SYNOPSIS
  Boot the OrionOS 1.0 USB image in a VirtualBox VM, to try it before putting it on a stick.

.DESCRIPTION
  1. Joins the downloaded image parts (os\image\out\vm\orionos-1.0.0.vdi.001, .002, ...) into one virtual disk and
     checks its SHA-256.
  2. Grows the disk to 32 GB so the first-start screen has room to make the encrypted vault (like a real stick).
  3. Creates the VM "OrionOS 1.0 (test)": UEFI, 4 CPUs (or fewer), half your RAM up to 8 GB, VMSVGA graphics,
     tablet mouse, NAT network. It's separate from the orion-lab / orion-boardroom VMs.
  4. Starts it.

  Run it again any time to start the VM. When a newer image has been copied in (a different checksum), the old
  test VM is replaced automatically. Pass -Reset to delete the test VM and start over from the image.
#>
param([switch]$Reset)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$Dir = Join-Path $Repo "os\image\out\vm"
$Name = "OrionOS 1.0 (test)"
$Disk = Join-Path $Dir "orionos-1.0.0.vdi"
$SumFile = Join-Path $Dir "orionos-1.0.0.vdi.sha256"
$UsedFile = Join-Path $Dir "orionos-1.0.0.vdi.in-use"   # checksum of the image the test VM was made from
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }

# ------------------------------------------------------------------ VirtualBox
$VBox = $null
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
if (-not $VBox) {
  Say "VirtualBox isn't installed. Install VirtualBox 7 from virtualbox.org (or run 'winget install Oracle.VirtualBox'), then run this again." "Red"
  exit 1
}
function VB { $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"; try { $o = (& $VBox @args 2>&1) -join "`n"; if ($LASTEXITCODE -ne 0) { throw "VBoxManage $($args -join ' ') failed:`n$o" }; $o } finally { $ErrorActionPreference = $p } }

try { if ([bool](Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
  Say "Windows' hypervisor (Hyper-V) is on, so VirtualBox runs in a slow mode. OrionOS will still boot, just slowly." "Yellow"
  Say "'Start OrionOS.cmd' can turn it off for you if you want full speed." "Yellow"
} } catch { }

$exists = (VB list vms) -match [regex]::Escape("`"$Name`"")
$want = if (Test-Path $SumFile) { ((Get-Content $SumFile -Raw).Trim() -split '\s+')[0].ToLower() } else { "" }
$used = if (Test-Path $UsedFile) { (Get-Content $UsedFile -Raw).Trim().ToLower() } else { "" }
if (-not $Reset -and $want -and ($exists -or (Test-Path $Disk)) -and $want -ne $used) {
  Say "A newer OrionOS image is here: replacing the old test VM (anything saved inside it is lost)." "Yellow"
  $Reset = $true
}
if ($Reset -and $exists) {
  Say "Deleting the old test VM..."
  try { VB controlvm "$Name" poweroff | Out-Null; Start-Sleep 2 } catch { }
  VB unregistervm "$Name" --delete | Out-Null
  $exists = $false
}
if ($Reset -and (Test-Path $Disk)) { Remove-Item $Disk -Force }

# ------------------------------------------------------------------ 1. join the parts
if (-not $exists -and -not (Test-Path $Disk)) {
  $list = Join-Path $Dir "orionos-1.0.0.vdi.parts"
  if (Test-Path $list) { $parts = Get-Content $list | Where-Object { $_.Trim() } | ForEach-Object { Get-Item (Join-Path $Dir $_.Trim()) } }
  else { $parts = Get-ChildItem $Dir -Filter "orionos-1.0.0.vdi.*" | Where-Object { $_.Name -match '\.\d{3}$' } | Sort-Object Name }
  if (-not $parts) { Say "No image parts found in $Dir" "Red"; exit 1 }
  Say "Joining $($parts.Count) parts into one disk (about 2 GB)..."
  $out = [System.IO.File]::Create($Disk)
  try { foreach ($p in $parts) { $in = [System.IO.File]::OpenRead($p.FullName); try { $in.CopyTo($out) } finally { $in.Close() } } }
  finally { $out.Close() }
  if ($want) {
    Say "Checking the disk..."
    $got = (Get-FileHash $Disk -Algorithm SHA256).Hash.ToLower()
    if ($want -ne $got) { Remove-Item $Disk -Force; Say "The joined disk is damaged (checksum mismatch). Ask Claude to resend the parts." "Red"; exit 1 }
    Say "Disk OK." "Green"
    Set-Content -Path $UsedFile -Value $want
  }
  Say "Making room for the encrypted vault (growing the disk to 32 GB; it only uses what's written)..."
  VB modifymedium disk "$Disk" --resize 32768 | Out-Null
}

# ------------------------------------------------------------------ 2. create the VM
if (-not $exists) {
  $ramMB = [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
  $vmRam = [Math]::Max(4096, [Math]::Min(8192, [int]($ramMB / 2)))
  $cores = [Environment]::ProcessorCount
  $cpus = [Math]::Max(2, [Math]::Min(4, [int]($cores / 2)))
  Say "Creating '$Name' ($cpus CPUs, $vmRam MB RAM)..."
  VB createvm --name "$Name" --ostype Ubuntu_64 --register | Out-Null
  VB modifyvm "$Name" --memory $vmRam --cpus $cpus --firmware efi --graphicscontroller vmsvga --vram 128 `
    --mouse usbtablet --usb-ohci on --nic1 nat --audio-enabled on --audio-out on --audio-in on --rtc-use-utc on | Out-Null
  VB storagectl "$Name" --name "SATA" --add sata --controller IntelAhci --portcount 1 | Out-Null
  VB storageattach "$Name" --storagectl "SATA" --port 0 --device 0 --type hdd --medium "$Disk" | Out-Null
}

# ------------------------------------------------------------------ 3. start
Say "Starting OrionOS. In the boot menu pick 'OrionOS' (or 'Try Orion' to keep nothing)." "Green"
Say "If the screen stays black, restart the VM and pick 'OrionOS (safe graphics)'." "Green"
Say "Release the mouse from the VM with the right Ctrl key." "Green"
VB startvm "$Name" --type gui | Out-Null
