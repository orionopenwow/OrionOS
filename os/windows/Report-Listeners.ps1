<#
.SYNOPSIS
  Tells Orion what this Windows PC has open, for the Exposure Map ("Orion, what's open on the PC?").

.DESCRIPTION
  Only useful when Orion runs on this PC. It lists the ports programs listen on (Get-NetTCPConnection -State Listen
  and the UDP endpoints), this PC's addresses, and the Windows Firewall's inbound allow rules, and sends them to
  Orion on this computer (http://127.0.0.1:9120/exposure/report). Nothing goes anywhere else. Orion then works
  out who can reach each port: this PC only, your home network, Tailscale, or the internet, and makes a card
  when something new opens up.

  Run it once:                powershell -ExecutionPolicy Bypass -File Report-Listeners.ps1
  Every hour, automatically:  powershell -ExecutionPolicy Bypass -File Report-Listeners.ps1 -Install
  Stop the hourly report:     powershell -ExecutionPolicy Bypass -File Report-Listeners.ps1 -Remove
#>
param([switch]$Install, [switch]$Remove, [string]$Orion = "http://127.0.0.1:9120")

$ErrorActionPreference = "Stop"
$TaskName = "Orion Exposure Map report"
function Say($m, $c = "Cyan") { Write-Host "[Orion] $m" -ForegroundColor $c }

if ($Remove) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Say "The hourly report is off."
  exit 0
}
if ($Install) {
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Orion $Orion"
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Force | Out-Null
  Say "Orion gets this PC's open ports every hour (task: $TaskName)."
}

$names = @{}
Get-Process | ForEach-Object { $names[[int]$_.Id] = $_.ProcessName }

$listeners = @()
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  $listeners += @{ proto = "tcp"; address = $_.LocalAddress; port = [int]$_.LocalPort
                   process = $names[[int]$_.OwningProcess]; pid = [int]$_.OwningProcess }
}
Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -lt 49152 } | ForEach-Object {
  $listeners += @{ proto = "udp"; address = $_.LocalAddress; port = [int]$_.LocalPort
                   process = $names[[int]$_.OwningProcess]; pid = [int]$_.OwningProcess }
}

$interfaces = @()
Get-NetIPAddress -ErrorAction SilentlyContinue | Where-Object { $_.AddressState -eq "Preferred" } | ForEach-Object {
  $interfaces += @{ iface = $_.InterfaceAlias; address = ($_.IPAddress -replace "%.*$", ""); prefix = [int]$_.PrefixLength }
}

# The firewall: on when every active network's profile has it on. Inbound is blocked unless a rule allows it.
$active = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { "$($_.NetworkCategory)" -replace "Authenticated", "" })
$profiles = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue | Where-Object { $active -contains $_.Name -or $active.Count -eq 0 })
$enabled = ($profiles.Count -gt 0) -and -not ($profiles | Where-Object { -not $_.Enabled })
$defaultAllow = [bool]($profiles | Where-Object { "$($_.DefaultInboundAction)" -eq "Allow" })

$allow = @()
foreach ($rule in Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction SilentlyContinue) {
  $ports = $rule | Get-NetFirewallPortFilter
  $addr = $rule | Get-NetFirewallAddressFilter
  $app = $rule | Get-NetFirewallApplicationFilter
  $from = "Anywhere"
  $remote = @($addr.RemoteAddress)
  if ($remote -contains "LocalSubnet" -or $remote -contains "LocalSubnet4") { $from = "192.168.0.0/16" }
  elseif ($remote.Count -gt 0 -and $remote[0] -ne "Any") { $from = ($remote[0] -split "-")[0] }
  $program = ""
  if ($app.Program -and $app.Program -ne "Any") { $program = [IO.Path]::GetFileNameWithoutExtension($app.Program) }
  $proto = "$($ports.Protocol)".ToLower()
  if ($proto -notin @("tcp", "udp")) { $proto = "" }
  $local = @($ports.LocalPort)
  if ($local.Count -gt 0 -and $local[0] -match "^\d+$") {
    foreach ($p in $local) {
      if ($p -match "^\d+$") { $allow += @{ name = $rule.DisplayName; port = [int]$p; proto = $proto; from = $from; process = $program } }
    }
  } elseif ($program) {
    $allow += @{ name = $rule.DisplayName; port = $null; proto = $proto; from = $from; process = $program }
  }
}

$report = @{
  machine = $env:COMPUTERNAME; source = "windows"; listeners = $listeners; interfaces = $interfaces
  firewall = @{ enabled = [bool]$enabled; default_inbound = $(if ($defaultAllow) { "allow" } else { "block" }); allow = $allow }
}
# Orion's per-start key (it changes every time Orion starts), from the private file in your Orion data folder.
$dataDir = if ($env:ORION_DATA_DIR) { $env:ORION_DATA_DIR } else { Join-Path $env:USERPROFILE ".orion" }
$tokenFile = if ($env:ORION_TOKEN_FILE) { $env:ORION_TOKEN_FILE } else { Join-Path $dataDir "daemon.token" }
$headers = @{}
if (Test-Path -LiteralPath $tokenFile) { $headers["Authorization"] = "Bearer " + (Get-Content -LiteralPath $tokenFile -Raw).Trim() }
try {
  $res = Invoke-RestMethod -Method Post -Uri "$Orion/exposure/report" -ContentType "application/json" -Headers $headers `
    -Body ($report | ConvertTo-Json -Depth 6 -Compress) -TimeoutSec 20
  Say $res.text "Green"
} catch {
  Say "Orion isn't answering on $Orion, so nothing was sent. (Is Orion running on this PC?)" "Yellow"
  exit 1
}
