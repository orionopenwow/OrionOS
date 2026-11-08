<#
.SYNOPSIS
  Lets Orion Mobile on your home Wi-Fi reach the OrionOS lab (no Tailscale needed at home).

.DESCRIPTION
  1. Finds this PC's Wi-Fi (or Ethernet) address.
  2. Forwards port 9121 on this PC to the lab VM (only the lab's signed-approval port, nothing else).
  3. Adds a Windows Firewall rule that allows it from your home network only (Private networks, local subnet).
     Needs one admin prompt. If Windows thinks your Wi-Fi is a Public network, it offers to mark it Private.
  4. Tells the lab this address, so "Pair my phone" shows the right QR code.

  Run it again if your PC's address changes. Undo everything:  powershell -File Open-PhoneAccess.ps1 -Remove

  -ThisPC: Orion runs right here on Windows (Start Orion.cmd), not in the lab VM. Only steps 1 and 3: find the
  home network and let phones on it reach port 9121 on this PC. The HUD's Phone panel runs this with
  "Let my phone in".
#>
param([switch]$Remove, [switch]$ThisPC)

$ErrorActionPreference = "Stop"
$Net = "OrionNet"
$RuleName = "OrionOS phone access (port 9121)"
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }
function Ask($q) { $a = Read-Host "$q [Y/n]"; return ($a -eq "" -or $a -match "^[Yy]") }

$VBox = $null
if (-not $ThisPC) {
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
if (-not $VBox) { Say "VirtualBox isn't installed. Run Start OrionOS.cmd first." "Red"; exit 1 }
}
function Done($code) { if ($ThisPC) { Read-Host "Press Enter to close this window" | Out-Null }; exit $code }
function VB { $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"; try { $o = (& $VBox @args 2>&1) -join "`n"; return @{ code = $LASTEXITCODE; out = $o } } finally { $ErrorActionPreference = $p } }
function Elevated($script) {
  $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
  $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList "-NoProfile", "-EncodedCommand", $enc
  return $p.ExitCode
}

# ------------------------------------------------------------------ undo
if ($Remove) {
  if (-not $ThisPC) {
    VB natnetwork modify --netname $Net --port-forward-4 delete phone | Out-Null
    VB guestproperty delete orion-lab /Orion/PhoneURL | Out-Null
  }
  $null = Elevated "Remove-NetFirewallRule -DisplayName '$RuleName' -ErrorAction SilentlyContinue"
  Say "Phone access over Wi-Fi is closed. (Tailscale still works if you set it up.)" "Green"
  exit 0
}

# ------------------------------------------------------------------ 1. this PC's home-network address
$cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" -and $_.InterfaceAlias -notmatch "VirtualBox|vEthernet|Tailscale|VPN|Loopback" } |
  Sort-Object { if ($_.InterfaceAlias -match "Wi-?Fi|Wireless|WLAN") { 0 } else { 1 } } | Select-Object -First 1
if (-not $cfg) { Say "This PC doesn't seem to be on a home network (no Wi-Fi or Ethernet with internet)." "Red"; Done 1 }
$ip = ($cfg.IPv4Address | Select-Object -First 1).IPAddress
$prof = Get-NetConnectionProfile -InterfaceIndex $cfg.InterfaceIndex -ErrorAction SilentlyContinue
Say "This PC is $ip on '$($cfg.InterfaceAlias)' ($($prof.Name), $($prof.NetworkCategory) network)."

$makePrivate = $false
if ($prof -and $prof.NetworkCategory -ne "Private") {
  Say "Windows treats this network as Public, so it blocks phones from reaching this PC." "Yellow"
  Say "Only mark it Private if it's your own home Wi-Fi (not a coffee shop, school or hotel)." "Yellow"
  if (Ask "Is '$($prof.Name)' your home network? Mark it Private") { $makePrivate = $true }
  else { Say "OK, nothing changed. At home, run this again; away from home, use Tailscale instead." "Yellow"; Done 0 }
}

if (-not $ThisPC) {

# ------------------------------------------------------------------ 2. forward port 9121 to the lab
$nets = (VB list natnetworks).out
if ($nets -notmatch "NetworkName:\s+$Net") { Say "The OrionOS network doesn't exist yet. Run Start OrionOS.cmd first." "Red"; exit 1 }
VB natnetwork modify --netname $Net --port-forward-4 delete phone | Out-Null
$r = VB natnetwork modify --netname $Net --port-forward-4 "phone:tcp:[0.0.0.0]:9121:[10.77.0.10]:9121"
if ($r.code -ne 0) { Say "Couldn't add the port-forward: $($r.out)" "Red"; exit 1 }
# The NAT network service picks up new forwards on restart; the VMs' network blinks for a second.
VB natnetwork stop --netname $Net | Out-Null
VB natnetwork start --netname $Net | Out-Null
Say "Port 9121 on this PC now leads to the lab's approval port."
}

# ------------------------------------------------------------------ 3. firewall (one admin prompt)
$fw = @"
`$ErrorActionPreference = 'Stop'
try {
  $(if ($makePrivate) { "Set-NetConnectionProfile -InterfaceIndex $($cfg.InterfaceIndex) -NetworkCategory Private" })
  Remove-NetFirewallRule -DisplayName '$RuleName' -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName '$RuleName' -Description 'Orion Mobile approvals to the OrionOS lab VM (signed requests only)' ``
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9121 -Profile Private -RemoteAddress LocalSubnet | Out-Null
  exit 0
} catch { exit 1 }
"@
Say "Windows will ask for admin permission to add the firewall rule (home network only)."
if ((Elevated $fw) -ne 0) { Say "The firewall rule wasn't added (permission declined?). Run this again and click Yes." "Red"; Done 1 }
Say "Firewall: phones on your home network can reach port 9121; nothing else is opened." "Green"

if ($ThisPC) {
  Write-Host ""
  Say "Done. Go back to Orion's Phone panel: it checks again by itself, then shows the QR code for your phone." "Green"
  Say "Your phone must be on this same Wi-Fi ('$($prof.Name)')." "Green"
  Done 0
}

# ------------------------------------------------------------------ 4. tell the lab
$url = "http://${ip}:9121"
$r = VB guestproperty set orion-lab /Orion/PhoneURL $url
if ($r.code -ne 0) { Say "Couldn't tell the lab the address (is orion-lab created?): $($r.out)" "Yellow" }

Write-Host ""
Say "Done. Next:" "Green"
Say "  1. In orion-lab: Ctrl+Alt+S > Pair my phone (or Ctrl+Alt+T: orion-ctl pair-phone)." "Green"
Say "  2. On your phone (same Wi-Fi): scan the QR code, tap Pair, and check the fingerprints match." "Green"
Say "If your PC's address changes later, run Open Phone Access.cmd again. A fixed address in your router avoids that." "Green"
