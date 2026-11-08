<#
  Setup-OrionOS.ps1 - makes the Orion OS work area (run once, from "Set up Orion OS.cmd" in the Orion OS folder).

    Orion OS\working   the Orion repo, branch "work": where development happens (backed up to GitHub hourly)
    Orion OS\stable    always the latest milestone that passed all of GitHub's tests: demos and daily use
    Orion OS\logs      what the save/milestone cycle did

  It also puts simple buttons in the Orion OS folder (Save my work now, Make a milestone, Run stable Orion, ...)
  and, if you say yes, two scheduled tasks: an hourly backup of working and a nightly milestone attempt.
  Safe to run again: it skips what already exists.
#>
param(
  [string]$RepoUrl = "https://github.com/orionopenwow/OrionOS.git",
  [string]$OldFolder = "",
  [switch]$Yes
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if ((Split-Path -Leaf $Root) -eq "tools") { $Root = Split-Path -Parent $Root }
$Working = Join-Path $Root "working"
$Stable = Join-Path $Root "stable"

function Say([string]$t, [string]$c = "Gray") { Write-Host $t -ForegroundColor $c }
function Good([string]$t) { Say "  OK  $t" "Green" }
function Bad([string]$t) { Say "  !!  $t" "Red" }
function Ask([string]$q, [bool]$def) {
  if ($Yes) { return $def }
  $hint = "[y/N]"; if ($def) { $hint = "[Y/n]" }
  $a = Read-Host "  > $q $hint"
  if (-not $a) { return $def }
  return $a.Trim().ToLower().StartsWith("y")
}

Say ""
Say "  Orion OS - set up the working and stable folders" "Cyan"
Say "  in $Root"
Say ""

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Bad "Git isn't installed. Get it from https://git-scm.com/download/win (all the default choices are fine), then run this again."
  exit 1
}

# 1. working: clone, then the "work" branch
if (-not (Test-Path -LiteralPath (Join-Path $Working ".git"))) {
  Say "  Copying the Orion repo from $RepoUrl into 'working' (a few minutes)..."
  & git clone $RepoUrl $Working
  if ($LASTEXITCODE -ne 0) { Bad "The copy didn't work. Check the internet connection and that you're signed in to GitHub (Git asks the first time)."; exit 1 }
}
Good "working folder ready."
Push-Location $Working
try {
  & git rev-parse --verify --quiet refs/heads/work *> $null
  if ($LASTEXITCODE -ne 0) {
    & git ls-remote --exit-code --heads origin work *> $null
    if ($LASTEXITCODE -eq 0) { & git switch -q -c work --track origin/work } else { & git switch -q -c work }
  } else { & git switch -q work }
  Good "working is on the 'work' branch (main only moves at milestones)."
  if (Get-Command node -ErrorAction SilentlyContinue) {
    & node scripts\secret-shield\install-hooks.mjs | Out-Null
    Good "Secret Shield checks every save before it can be pushed."
  } else {
    Say "  Node.js isn't installed yet, so the Secret Shield check isn't on. Install Node.js LTS from https://nodejs.org and run this again." "Yellow"
  }
} finally { Pop-Location }

# 2. Local-only files from the old Orion folder (never in git): settings/keys stay on this PC
if (-not $OldFolder) {
  $guess = Join-Path $env:USERPROFILE "Documents\antigravity\wonderful-meitner"
  if (Test-Path -LiteralPath $guess) { $OldFolder = $guess }
}
if ($OldFolder -and (Test-Path -LiteralPath $OldFolder)) {
  $items = @("apps\daemon\.env", ".orion-signing") | Where-Object { (Test-Path -LiteralPath (Join-Path $OldFolder $_)) -and -not (Test-Path -LiteralPath (Join-Path $Working $_)) }
  if ($items.Count -gt 0) {
    Say "  Your old Orion folder has local-only files git never carries (your settings/keys): $($items -join ', ')"
    if (Ask "Copy them into the working folder (they stay on this PC and are never pushed)?" $true) {
      foreach ($i in $items) { Copy-Item -LiteralPath (Join-Path $OldFolder $i) -Destination (Join-Path $Working $i) -Recurse -Force }
      Good "Copied."
    }
  }
}

# 3. Buttons in the Orion OS folder
$ms = "working\os\windows\milestones\Milestones.ps1"
function Button([string]$name, [string]$body) {
  $p = Join-Path $Root $name
  $text = "@echo off`r`ncd /d `"%~dp0`"`r`n$body`r`n"
  [IO.File]::WriteAllText($p, $text, (New-Object System.Text.UTF8Encoding $false))
}
Button "Save my work now.cmd" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ms`" -Action save`r`npause"
Button "Make a milestone.cmd" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ms`" -Action milestone`r`npause"
Button "Roll stable back.cmd" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ms`" -Action rollback`r`npause"
Button "Orion OS status.cmd" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ms`" -Action status`r`npause"
Button "Run stable Orion.cmd" "if not exist `"stable\Start Orion.cmd`" (echo There's no stable version yet. Make a milestone first. & pause & exit /b 1)`r`ncall `"stable\Start Orion.cmd`""
Button "Run working Orion.cmd" "call `"working\Start Orion.cmd`""
Button "Open working Start Here.cmd" "call `"working\Start Here.cmd`""
Good "Buttons added to the Orion OS folder."

# 4. stable folder (latest milestone, or main until the first milestone exists)
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root $ms) -Action stable
if ($LASTEXITCODE -eq 0) { Good "stable folder ready." } else { Bad "The stable folder wasn't made this time; 'Make a milestone' makes it later." }

# 5. Automatic cycle (Windows Task Scheduler, runs as you, no admin needed)
$psExe = (Get-Command powershell.exe).Source
$msFull = Join-Path $Root $ms
if (Ask "Back up the working folder to GitHub every hour (only when something changed)?" $true) {
  $act = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$msFull`" -Action save -Quiet"
  $trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 1)
  $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName "Orion OS - Save work" -Action $act -Trigger $trg -Settings $set -Description "Commits and pushes Orion OS\working to the 'work' branch (Secret Shield checks first)." -Force | Out-Null
  Good "Hourly backup scheduled."
}
if (Ask "Every night at 3:00, make a milestone if all of GitHub's tests pass (stable then updates itself)?" $true) {
  $act = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$msFull`" -Action milestone -Quiet"
  $trg = New-ScheduledTaskTrigger -Daily -At "03:00"
  $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun:$false -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName "Orion OS - Nightly milestone" -Action $act -Trigger $trg -Settings $set -Description "Makes a milestone of Orion OS\working only if every GitHub check passed, then updates Orion OS\stable." -Force | Out-Null
  Good "Nightly milestone scheduled."
}

Say ""
Say "  Done. In the Orion OS folder:" "Cyan"
Say "    working\               develop here (Claude and your agents work here too)"
Say "    stable\                the last tested version: use 'Run stable Orion.cmd' for demos"
Say "    Save my work now.cmd   back up to GitHub right now"
Say "    Make a milestone.cmd   wait for GitHub's tests; if all pass, publish main + update stable"
Say "    Roll stable back.cmd   put stable on an earlier milestone"
Say "    Orion OS status.cmd    what's saved, pushed and running"
Say ""
exit 0
