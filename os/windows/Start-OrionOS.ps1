<#
.SYNOPSIS
  One click to get into OrionOS: fixes what's needed on Windows, creates the VMs if they don't exist, and starts them.

.DESCRIPTION
  1. Checks that VirtualBox can use the CPU directly. If Windows' hypervisor (Hyper-V) is running, VirtualBox is
     forced into a very slow mode where boot can freeze for ~40 minutes. With your OK it turns the hypervisor off
     (one admin prompt), and offers to restart Windows; this script then runs again by itself after you log in.
     Turning it off stops WSL2 / Docker Desktop until you turn it back on:  bcdedit /set hypervisorlaunchtype auto
  2. Creates the two VMs (New-OrionVMs.ps1) if they don't exist yet.
  3. Starts orion-boardroom and orion-lab.
  4. Optionally starts OrionOS every time you log in to Windows.
#>
param([switch]$AfterRestart, [switch]$AtLogin)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$Launcher = Join-Path $Repo "Start OrionOS.cmd"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunOnceKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce"
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }
function Ask($q) { $a = Read-Host "$q [Y/n]"; return ($a -eq "" -or $a -match "^[Yy]") }

# ------------------------------------------------------------------ VirtualBox
$VBox = $null
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
function VBText { $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"; try { (& $VBox @args 2>&1) -join "`n" } finally { $ErrorActionPreference = $p } }

# ------------------------------------------------------------------ 1. Hypervisor check
$hv = $false
try { $hv = [bool](Get-CimInstance Win32_ComputerSystem).HypervisorPresent } catch { }
if ($hv) {
  Say "Windows' hypervisor (Hyper-V) is running, so VirtualBox would run OrionOS very slowly (boot can freeze ~40 min)." "Yellow"
  Say "Fix: turn the hypervisor off. WSL2 and Docker Desktop stop working until you turn it back on." "Yellow"
  if (Ask "Turn it off now? (Windows asks for admin permission once)") {
    $p = Start-Process -FilePath "bcdedit.exe" -ArgumentList "/set", "hypervisorlaunchtype", "off" -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -ne 0) { Say "That didn't work (exit $($p.ExitCode)). Run 'bcdedit /set hypervisorlaunchtype off' in an admin Command Prompt." "Red"; exit 1 }
    Say "Done. It takes effect after a restart." "Green"
    if (Ask "Restart Windows now? OrionOS will start by itself after you log back in") {
      New-Item -Path $RunOnceKey -Force | Out-Null
      Set-ItemProperty -Path $RunOnceKey -Name "OrionOS-continue" -Value "`"$Launcher`" -AfterRestart"
      Restart-Computer -Force
      exit 0
    }
    Say "Restart Windows yourself, then double-click Start OrionOS.cmd again." "Yellow"
    exit 0
  }
  Say "Continuing in slow mode. Expect long boots." "Yellow"
}

# ------------------------------------------------------------------ 2. VirtualBox + VMs exist?
if (-not $VBox) {
  Say "VirtualBox isn't installed yet; the VM creator will offer to install it."
}
$vms = if ($VBox) { VBText list vms } else { "" }
if (-not $VBox -or $vms -notmatch '"orion-lab"' -or $vms -notmatch '"orion-boardroom"') {
  Say "Creating the OrionOS VMs first..."
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here "New-OrionVMs.ps1")
  exit $LASTEXITCODE
}

# ------------------------------------------------------------------ 3. Start them
# If phone access over Wi-Fi is on (Open Phone Access.cmd), refresh this PC's address for the lab's pairing QR.
if ((VBText list natnetworks) -match "phone:tcp:") {
  $cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" -and $_.InterfaceAlias -notmatch "VirtualBox|vEthernet|Tailscale|VPN|Loopback" } |
    Sort-Object { if ($_.InterfaceAlias -match "Wi-?Fi|Wireless|WLAN") { 0 } else { 1 } } | Select-Object -First 1
  if ($cfg) { $null = VBText guestproperty set orion-lab /Orion/PhoneURL ("http://" + ($cfg.IPv4Address | Select-Object -First 1).IPAddress + ":9121") }
}
$running = VBText list runningvms
if ($running -notmatch '"orion-lab"') {
  # Labs created before Oct 2026 had sound off: give the lab sound and your microphone (hot mic, Orion's voice).
  $null = VBText modifyvm orion-lab --audio-enabled on --audio-controller hda --audio-in on --audio-out on
}
foreach ($n in @("orion-boardroom", "orion-lab")) {
  if ($running -match "`"$n`"") { Say "$n is already running." ; continue }
  Say "Starting $n..."
  $out = VBText startvm $n --type gui
  if ($out -notmatch "successfully started") { Say "Couldn't start ${n}: $out" "Red" }
}

# ------------------------------------------------------------------ 4. Start at login?
$auto = (Get-ItemProperty -Path $RunKey -Name "OrionOS" -ErrorAction SilentlyContinue)
if (-not $auto -and -not $AfterRestart -and -not $AtLogin) {
  if (Ask "Start OrionOS automatically every time you log in to Windows?") {
    Set-ItemProperty -Path $RunKey -Name "OrionOS" -Value "`"$Launcher`" -AtLogin"
    Say "OK. Turn it off any time in Task Manager > Startup apps (OrionOS)." "Green"
  }
}

Write-Host ""
Say "OrionOS is starting." "Green"
Say "First time only: the boardroom asks for a passphrase, then Orion sends you a 'Set up your workspace' approval." "Green"
Say "Approve it on the boardroom (or your phone once paired) and Orion installs Discord, VS Code, Firefox and your dev tools." "Green"
Start-Sleep -Seconds 6
