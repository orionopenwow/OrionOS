<#
  Milestones.ps1 - the Orion OS save-and-milestone cycle (Windows, PowerShell 5.1+).

  Folder layout (made by "Set up Orion OS.cmd" on the Desktop):
    Orion OS\working   a clone of the Orion repo on branch "work": where all development happens
    Orion OS\stable    always the latest milestone (a tested version): for demos and daily use
    Orion OS\logs      what the cycle did, one line per run

  Actions:
    -Action save       commit everything in working to "work" and push it (a backup). Secret Shield checks first.
    -Action milestone  save, then wait for GitHub's tests (CI) on that exact commit. Only if every check passed:
                       move "main" to it, tag it milestone-<date>, push both, and update the stable folder.
    -Action stable     put the stable folder on the latest milestone (or -Tag <name>).
    -Action rollback   pick an earlier milestone for the stable folder.
    -Action status     what's saved, what's pushed, which milestone stable runs.
  -Quiet: no questions (for the scheduled tasks). -NoWait: milestone doesn't wait for CI (it just reports).

  Never pushes a commit that Secret Shield stopped, never moves main to a commit whose checks failed, never
  throws away local changes in stable (they're stashed with a dated name first).
#>
param(
  [ValidateSet("save", "milestone", "stable", "rollback", "status")][string]$Action = "status",
  [string]$Tag = "",
  [switch]$Quiet,
  [switch]$NoWait,
  [int]$CiTimeoutMinutes = 45,
  [string]$ApiBase = "https://api.github.com"
)
$ErrorActionPreference = "Stop"

$Working = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$Root = Split-Path -Parent $Working
$Stable = Join-Path $Root "stable"
$Logs = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$LogFile = Join-Path $Logs "orion-os.log"

function Stamp() { return (Get-Date -Format "yyyy-MM-dd HH:mm") }
function Log([string]$text) { Add-Content -LiteralPath $LogFile -Value ("{0}  {1,-9} {2}" -f (Stamp), $Action, $text) }
function Say([string]$text, [string]$color = "Gray") { if (-not $Quiet -or $color -eq "Red") { Write-Host $text -ForegroundColor $color }; Log $text }
function Good([string]$text) { Say "  OK  $text" "Green" }
function Bad([string]$text) { Say "  !!  $text" "Red" }
function Ask([string]$q, [bool]$def) {
  if ($Quiet) { return $def }
  $hint = "[y/N]"; if ($def) { $hint = "[Y/n]" }
  $a = Read-Host "  > $q $hint"
  if (-not $a) { return $def }
  return $a.Trim().ToLower().StartsWith("y")
}

# git in a folder; returns output lines; throws with git's message on failure
function Invoke-Git([string]$dir, [string[]]$gitArgs, [switch]$Soft) {
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  $out = & git -C $dir @gitArgs 2>&1
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($code -ne 0 -and -not $Soft) { throw ("git " + ($gitArgs -join " ") + " failed: " + (($out | Out-String).Trim())) }
  $script:GitCode = $code
  return $out
}

function RepoSlug() {
  if ($env:ORION_REPO_SLUG) { return $env:ORION_REPO_SLUG }   # tests / forks
  $url = (Invoke-Git $Working @("remote", "get-url", "origin") | Select-Object -First 1).ToString().Trim()
  if ($url -match "github\.com[:/]+([^/]+)/([^/.]+?)(\.git)?/?$") { return "$($Matches[1])/$($Matches[2])" }
  throw "The working folder's GitHub address isn't recognised: $url"
}

function Save-Work() {
  if (-not (Test-Path -LiteralPath (Join-Path $Working ".git"))) { Bad "No working folder. Run 'Set up Orion OS.cmd' first."; exit 1 }
  $branch = (Invoke-Git $Working @("rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1).ToString().Trim()
  if ($branch -ne "work") {
    Invoke-Git $Working @("rev-parse", "--verify", "--quiet", "refs/heads/work") -Soft | Out-Null
    if ($script:GitCode -eq 0) { Invoke-Git $Working @("switch", "work") | Out-Null } else { Invoke-Git $Working @("switch", "-c", "work") | Out-Null }
    Say "  Switched the working folder to the 'work' branch (your changes came along)."
  }
  $changes = @(Invoke-Git $Working @("status", "--porcelain"))
  if ($changes.Count -gt 0) {
    Invoke-Git $Working @("add", "-A") | Out-Null
    # The Secret Shield hook (installed by setup) runs here and stops the commit if something looks like a key.
    $msg = "Auto-save " + (Stamp)
    Invoke-Git $Working @("commit", "-q", "-m", $msg) -Soft | Out-Null
    if ($script:GitCode -ne 0) {
      Invoke-Git $Working @("reset", "-q") -Soft | Out-Null
      Bad "Not saved: the commit was stopped (Secret Shield found something that looks like a key, or git refused). Nothing was pushed. Check logs\secret-shield in the working folder."
      exit 2
    }
    Good "Saved $($changes.Count) changed file(s)."
  } else { Say "  Nothing new to save." }
  Invoke-Git $Working @("push", "-q", "-u", "origin", "work") -Soft | Out-Null
  if ($script:GitCode -ne 0) { Bad "Saved on this PC, but the push to GitHub didn't go through (internet or GitHub sign-in?). It will retry next time."; exit 3 }
  $sha = (Invoke-Git $Working @("rev-parse", "--short", "HEAD") | Select-Object -First 1).ToString().Trim()
  Good "Backed up to GitHub (branch work, $sha)."
}

function Get-Checks([string]$slug, [string]$sha) {
  $h = @{ "Accept" = "application/vnd.github+json"; "User-Agent" = "orion-milestones" }
  return Invoke-RestMethod -Uri "$ApiBase/repos/$slug/commits/$sha/check-runs?per_page=100" -Headers $h -UseBasicParsing
}

function Wait-Ci([string]$slug, [string]$sha) {
  $deadline = (Get-Date).AddMinutes($CiTimeoutMinutes)
  Say "  Waiting for GitHub's tests on $($sha.Substring(0,7)) (usually 5-15 minutes)..."
  $seen = $false
  while ((Get-Date) -lt $deadline) {
    try { $r = Get-Checks $slug $sha } catch { Say "  (couldn't ask GitHub just now: $($_.Exception.Message))"; Start-Sleep -Seconds 30; continue }
    $runs = @($r.check_runs)
    if ($runs.Count -gt 0) {
      $seen = $true
      $open = @($runs | Where-Object { $_.status -ne "completed" })
      if ($open.Count -eq 0) {
        $bad = @($runs | Where-Object { @("success", "skipped", "neutral") -notcontains $_.conclusion })
        if ($bad.Count -eq 0) { return @{ Ok = $true; Failed = @() } }
        return @{ Ok = $false; Failed = @($bad | ForEach-Object { "$($_.name) ($($_.conclusion))" }) }
      }
    }
    if ($NoWait) { break }
    Start-Sleep -Seconds 30
  }
  if (-not $seen) { return @{ Ok = $false; Failed = @("no tests ran for this commit yet") } }
  return @{ Ok = $false; Failed = @("tests still running after $CiTimeoutMinutes minutes") }
}

function Make-Milestone() {
  Save-Work
  $slug = RepoSlug
  $sha = (Invoke-Git $Working @("rev-parse", "HEAD") | Select-Object -First 1).ToString().Trim()
  $last = Invoke-Git $Working @("describe", "--tags", "--exact-match", "--match", "milestone-*", $sha) -Soft
  if ($script:GitCode -eq 0) { Say "  This exact version is already milestone $last."; Update-Stable ""; return }
  $ci = Wait-Ci $slug $sha
  if (-not $ci.Ok) {
    Bad ("No milestone this time: " + ($ci.Failed -join "; ") + ". main and the stable folder are unchanged.")
    exit 4
  }
  Good "All of GitHub's tests passed."
  Invoke-Git $Working @("fetch", "-q", "origin", "main") -Soft | Out-Null
  if ($script:GitCode -eq 0) {
    Invoke-Git $Working @("merge-base", "--is-ancestor", "origin/main", $sha) -Soft | Out-Null
    if ($script:GitCode -ne 0) {
      Bad "main on GitHub has changes that aren't in your work branch. Merge them into work first (or ask Claude), then make the milestone again. Nothing was changed."
      exit 5
    }
  }
  $name = "milestone-" + (Get-Date -Format "yyyyMMdd-HHmm")
  Invoke-Git $Working @("tag", "-a", $name, "-m", ("Milestone " + (Stamp) + " (all checks passed)"), $sha) | Out-Null
  Invoke-Git $Working @("push", "-q", "origin", ($sha + ":refs/heads/main")) | Out-Null
  Invoke-Git $Working @("push", "-q", "origin", $name) | Out-Null
  Good "Milestone $name pushed (main now points to it)."
  Update-Stable $name
}

function Latest-Milestone([string]$dir) {
  $t = @(Invoke-Git $dir @("tag", "-l", "milestone-*", "--sort=-creatordate"))
  if ($t.Count -gt 0) { return $t[0].ToString().Trim() }
  return ""
}

function Update-Stable([string]$want) {
  if (-not (Test-Path -LiteralPath (Join-Path $Stable ".git"))) {
    $url = (Invoke-Git $Working @("remote", "get-url", "origin") | Select-Object -First 1).ToString().Trim()
    Say "  Making the stable folder (a separate copy for demos)..."
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & git clone -q $url $Stable 2>&1 | Out-Null
    $ErrorActionPreference = $prev
    if (-not (Test-Path -LiteralPath (Join-Path $Stable ".git"))) { Bad "Couldn't make the stable folder (git clone failed)."; exit 6 }
  }
  Invoke-Git $Stable @("fetch", "-q", "--tags", "--force", "origin") | Out-Null
  if (-not $want) { $want = Latest-Milestone $Stable }
  if (-not $want) { Say "  No milestones yet: the stable folder stays on main."; $want = "origin/main" }
  $dirty = @(Invoke-Git $Stable @("status", "--porcelain"))
  if ($dirty.Count -gt 0) {
    $keep = "stable-local-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Invoke-Git $Stable @("stash", "push", "-u", "-q", "-m", $keep) | Out-Null
    Say "  The stable folder had local changes; they're kept (git stash, named $keep)."
  }
  Invoke-Git $Stable @("checkout", "-q", "--detach", $want) | Out-Null
  # Local-only files (keys, settings) that git never carries: copy them from working once, if stable lacks them.
  foreach ($rel in @("apps\daemon\.env")) {
    $from = Join-Path $Working $rel; $to = Join-Path $Stable $rel
    if ((Test-Path -LiteralPath $from) -and -not (Test-Path -LiteralPath $to)) { Copy-Item -LiteralPath $from -Destination $to; Say "  Copied your local settings file $rel into stable." }
  }
  Set-Content -LiteralPath (Join-Path $Logs "stable-version.txt") -Value $want
  Good "The stable folder now runs $want."
}

function Rollback() {
  if (-not (Test-Path -LiteralPath (Join-Path $Stable ".git"))) { Bad "There's no stable folder yet."; exit 1 }
  Invoke-Git $Stable @("fetch", "-q", "--tags", "origin") | Out-Null
  $tags = @(Invoke-Git $Stable @("tag", "-l", "milestone-*", "--sort=-creatordate") | Select-Object -First 10)
  if ($tags.Count -eq 0) { Bad "No milestones yet."; exit 1 }
  if ($Tag) { Update-Stable $Tag; return }
  if ($Quiet) { Bad "Rollback needs you to pick a milestone (run it without -Quiet)."; exit 1 }
  Say "  Recent milestones (newest first):"
  for ($i = 0; $i -lt $tags.Count; $i++) { Write-Host ("    {0}. {1}" -f ($i + 1), $tags[$i]) }
  $pick = Read-Host "  > Which number should the stable folder run? (Enter = cancel)"
  if (-not $pick) { Say "  Nothing changed."; return }
  $n = 0
  if (-not [int]::TryParse($pick, [ref]$n) -or $n -lt 1 -or $n -gt $tags.Count) { Bad "That isn't one of the numbers."; exit 1 }
  Update-Stable $tags[$n - 1].ToString().Trim()
}

function Show-Status() {
  Write-Host ""
  Write-Host "  Orion OS folders: $Root" -ForegroundColor Cyan
  if (Test-Path -LiteralPath (Join-Path $Working ".git")) {
    $b = (Invoke-Git $Working @("rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1)
    $c = @(Invoke-Git $Working @("status", "--porcelain")).Count
    $ahead = (Invoke-Git $Working @("rev-list", "--count", "@{u}..HEAD") -Soft | Select-Object -First 1)
    if ($script:GitCode -ne 0) { $ahead = "not pushed yet" }
    Write-Host "  working: branch $b, $c unsaved change(s), commits not on GitHub: $ahead"
  } else { Write-Host "  working: not set up" }
  if (Test-Path -LiteralPath (Join-Path $Stable ".git")) {
    $v = "(unknown)"; $f = Join-Path $Logs "stable-version.txt"
    if (Test-Path -LiteralPath $f) { $v = (Get-Content -LiteralPath $f -TotalCount 1) }
    Write-Host "  stable:  runs $v"
  } else { Write-Host "  stable:  not made yet (the first milestone makes it)" }
  $tasks = @(); if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) { $tasks = @(Get-ScheduledTask -TaskName "Orion OS - *" -ErrorAction SilentlyContinue) }
  foreach ($t in $tasks) { Write-Host "  scheduled: $($t.TaskName) ($($t.State))" }
  if (Test-Path -LiteralPath $LogFile) { Write-Host ""; Write-Host "  Last entries in logs\orion-os.log:"; Get-Content -LiteralPath $LogFile -Tail 6 | ForEach-Object { Write-Host "    $_" } }
  Write-Host ""
}

try {
  switch ($Action) {
    "save" { Save-Work }
    "milestone" { Make-Milestone }
    "stable" { Update-Stable $Tag }
    "rollback" { Rollback }
    "status" { Show-Status }
  }
} catch {
  Bad $_.Exception.Message
  exit 9
}
exit 0
