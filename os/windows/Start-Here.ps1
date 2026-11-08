<#
.SYNOPSIS
  Start Here: one calm wizard that takes you from "Claude sent an update" to "my phone and PC are tested".

.DESCRIPTION
  "Start Here.cmd" runs this. It goes one step at a time, asks one question at a time, and Enter always picks the
  recommended answer (the capital letter in [Y/n]). Type Q at any question to stop; your progress is kept in
  %LOCALAPPDATA%\Orion\start-here.json, so running it again carries on where you stopped. Type R at the first
  question (or pass -Restart) to start over.

    1. Update        put Claude's update in place (Apply-OrionUpdate.ps1), then restart this wizard if it changed
    2. Save          save your Orion work to GitHub on its own branch (only if you say yes)
    3. Tools         Node.js, Python, Rust, Claude Code (offers to install it), Codex and Gemini (just reported)
    4. Start Orion   Start Orion.cmd + the Test Center (like Test Orion.cmd), then waits until Orion answers
    5. Phone app     builds Orion Mobile (offers to set up Java 17 and the Android SDK), then installs it over USB or
                     shares it on your Wi-Fi with a QR code for 15 minutes
    6. Pair          opens the Phone panel in the Orion window and waits for your phone
    7. Phone checks  waits while your phone runs its checks (Menu > Test on the phone)
    8. The rest      the Test Center's Next card, live, until you're done; then copies the report for Claude
    9. OrionOS VM    offers to start Try OrionOS VM.cmd

  It never approves, sends, merges or pushes anything for you, except "Save to GitHub" when you say yes to it.
  It never prints keys.

  -DryRun walks every step without changing anything on this PC: no programs are run, nothing is installed or
  downloaded, nothing is copied to the clipboard; it keeps its progress in a separate start-here.dryrun.json.
  It only reads from Orion (GET); with ORION_START_HERE_FAKE_DAEMON=1 (tests with a stand-in Orion) it also sends
  the few requests that would open windows or share the app. ORION_START_HERE_ANSWERS="y|n||q" answers the
  questions in order (for tests).
#>
param(
  [switch]$DryRun,
  [switch]$Restart,
  [switch]$Relaunched,
  [string]$Api = "",
  [string]$StateFile = "",
  [int]$WaitCapSeconds = 0
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = Split-Path -Parent (Split-Path -Parent $Here)
$IsWin = ($env:OS -eq "Windows_NT")
if (-not $Api) { $Api = $env:ORION_API_URL }
if (-not $Api) { $Api = "http://127.0.0.1:9120" }
$Api = $Api.TrimEnd("/")
$FakeDaemon = ($env:ORION_START_HERE_FAKE_DAEMON -eq "1")
if ($WaitCapSeconds -le 0 -and $env:ORION_START_HERE_WAIT_CAP) { $WaitCapSeconds = [int]$env:ORION_START_HERE_WAIT_CAP }

# ------------------------------------------------------------------ how it looks
$Fancy = [bool]$env:WT_SESSION -or ($PSVersionTable.PSVersion.Major -ge 6)
if ($Fancy) { $Tick = [string][char]0x2713; $Cross = [string][char]0x2717 } else { $Tick = [string][char]0x221A; $Cross = [string][char]0x00D7 }
$Dot = [string][char]0x2022
$Spin = @("|", "/", "-", "\")
try { $Host.UI.RawUI.WindowTitle = "Start Here - Orion" } catch { }

function Say([string]$text = "", [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Good([string]$text) { Write-Host "  $Tick $text" -ForegroundColor Green }
function Info([string]$text) { Write-Host "  $Dot $text" -ForegroundColor Gray }
function Warn([string]$text) { Write-Host "  $Dot $text" -ForegroundColor Yellow }
function Bad([string]$text) { Write-Host "  $Cross $text" -ForegroundColor Red }
function Dry([string]$text) { Write-Host "  (dry run) $text" -ForegroundColor DarkCyan }
function Width() { try { $w = [Console]::WindowWidth; if ($w -gt 20) { return $w } } catch { }; return 100 }

# ------------------------------------------------------------------ questions (one at a time; Enter = recommended)
$Answers = $null
if ($null -ne $env:ORION_START_HERE_ANSWERS) {
  $Answers = New-Object System.Collections.Queue
  foreach ($a in ($env:ORION_START_HERE_ANSWERS -split "\|")) { $Answers.Enqueue($a) }
}
function Interactive() {
  if ($null -ne $Answers) { return $false }
  try { return -not [Console]::IsInputRedirected } catch { return $false }
}
function ReadAnswer([string]$prompt) {
  if ($null -ne $Answers) {
    $a = ""
    if ($Answers.Count -gt 0) { $a = [string]$Answers.Dequeue() }
    Write-Host "  > $prompt $a" -ForegroundColor Cyan
    return $a.Trim()
  }
  Write-Host "  > $prompt " -ForegroundColor Cyan -NoNewline
  $a = Read-Host
  if ($null -eq $a) { $a = "" }
  return $a.Trim()
}
# Returns $true for yes. Q stops the wizard (progress is kept).
function Ask([string]$question, [bool]$recommendYes = $true) {
  $hint = "[y/N]"
  if ($recommendYes) { $hint = "[Y/n]" }
  while ($true) {
    $a = ReadAnswer "$question $hint"
    if ($a -match "^[Qq]") { throw "START-HERE-QUIT" }
    if ($a -eq "") { return $recommendYes }
    if ($a -match "^[Yy]") { return $true }
    if ($a -match "^[Nn]") { return $false }
    Info "Type y or n (or just press Enter for the recommended one)."
  }
}
function WaitEnter([string]$what) {
  $a = ReadAnswer "$what (press Enter)"
  if ($a -match "^[Qq]") { throw "START-HERE-QUIT" }
}
# Non-blocking: did they press Enter (or Ctrl+C / S / Esc) to move on?
function KeyToMoveOn() {
  if (-not (Interactive)) { return $false }
  try {
    while ([Console]::KeyAvailable) {
      $k = [Console]::ReadKey($true)
      if ($k.Key -eq "Enter" -or $k.Key -eq "Escape" -or $k.Key -eq "S") { return $true }
      if ($k.Key -eq "C" -and ($k.Modifiers -band [ConsoleModifiers]::Control)) { return $true }
      if ($k.Key -eq "Q") { throw "START-HERE-QUIT" }
    }
  } catch {
    if ($_.Exception.Message -eq "START-HERE-QUIT") { throw }
  }
  return $false
}
function CtrlCAsKey([bool]$on) { try { if (Interactive) { [Console]::TreatControlCAsInput = $on } } catch { } }
function ClearLine() { Write-Host ("`r" + (" " * ((Width) - 1)) + "`r") -NoNewline }
function Status([string]$text, [int]$tick) {
  $t = "  " + $Spin[$tick % 4] + " " + $text
  $max = (Width) - 2
  if ($t.Length -gt $max) { $t = $t.Substring(0, $max - 3) + "..." }
  Write-Host ("`r" + $t.PadRight($max)) -NoNewline -ForegroundColor Cyan
}

# ------------------------------------------------------------------ progress (resume)
if (-not $StateFile) {
  $base = $env:LOCALAPPDATA
  if (-not $base) { $base = Join-Path $HOME ".local/share" }
  $name = "start-here.json"
  if ($DryRun) { $name = "start-here.dryrun.json" }
  $StateFile = Join-Path (Join-Path $base "Orion") $name
}
function NewState() { return @{ version = 1; started = (Get-Date).ToString("s"); steps = @{} } }
function LoadState() {
  $s = NewState
  if (Test-Path -LiteralPath $StateFile) {
    try {
      $j = [IO.File]::ReadAllText($StateFile) | ConvertFrom-Json
      if ($j.started) { $s.started = [string]$j.started }
      if ($j.steps) { foreach ($p in $j.steps.PSObject.Properties) { $s.steps[$p.Name] = [string]$p.Value } }
    } catch { }
  }
  return $s
}
function SaveState() {
  $dir = Split-Path -Parent $StateFile
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [IO.File]::WriteAllText($StateFile, ($State | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
}
function StepState([string]$id) { if ($State.steps.ContainsKey($id)) { return $State.steps[$id] }; return "" }
function SetStep([string]$id, [string]$result) { $State.steps[$id] = $result; SaveState }

# ------------------------------------------------------------------ talking to Orion (this PC only)
function TokenFiles() {
  $files = @()
  if ($env:ORION_TOKEN_FILE) { $files += $env:ORION_TOKEN_FILE }
  if (-not $IsWin) {
    $rt = $env:XDG_RUNTIME_DIR
    if ($rt) { $files += (Join-Path (Join-Path $rt "orion") "daemon.token") }
  }
  $data = $env:ORION_DATA_DIR
  if ($data) { $data = $data -replace "^~", $HOME } else { $data = Join-Path $HOME ".orion" }
  $files += (Join-Path $data "daemon.token")
  return $files
}
function Token() {
  foreach ($f in (TokenFiles)) {
    try { if ($f -and (Test-Path -LiteralPath $f)) { $t = ([IO.File]::ReadAllText($f)).Trim(); if ($t) { return $t } } } catch { }
  }
  return ""
}
# @{ ok; status; json }. The key is sent, never printed. Replies are read as UTF-8 by hand (Windows PowerShell 5.1
# would guess the wrong text encoding).
function Call([string]$method, [string]$path, $body = $null, [int]$timeout = 6) {
  $sending = ($method -ne "GET")
  if ($sending -and $DryRun -and -not $FakeDaemon) {
    Dry "would ask Orion: $method $path"
    return @{ ok = $false; status = 0; json = $null; dry = $true }
  }
  $headers = @{}
  $t = Token
  if ($t) { $headers["Authorization"] = "Bearer $t" }
  $args2 = @{ Uri = "$Api$path"; Method = $method; Headers = $headers; TimeoutSec = $timeout; UseBasicParsing = $true }
  if ($PSVersionTable.PSVersion.Major -ge 6) { $args2["NoProxy"] = $true }
  if ($sending) {
    $args2["ContentType"] = "application/json"
    if ($null -eq $body) { $body = @{} }
    $args2["Body"] = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress -Depth 5))
  }
  try {
    $r = Invoke-WebRequest @args2
    $text = [Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())
    $json = $null
    if ($text) { try { $json = $text | ConvertFrom-Json } catch { $json = $null } }
    return @{ ok = $true; status = [int]$r.StatusCode; json = $json }
  } catch {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch { }
    return @{ ok = $false; status = $code; json = $null }
  }
}
function OrionUp() { $r = Call "GET" "/health" $null 3; return $r.ok }
function OrionReady() { $r = Call "GET" "/tests" $null 5; return ($r.ok -and $null -ne $r.json -and $null -ne $r.json.groups) }
function PairedPhones() {
  $r = Call "GET" "/phones"
  if ($r.ok -and $r.json -and $r.json.phones) { return @($r.json.phones) }
  return @()
}
function AllItems($view) {
  $out = @()
  if ($view -and $view.groups) { foreach ($g in $view.groups) { foreach ($it in $g.items) { $out += $it } } }
  return $out
}
function ItemStatus($view, [string]$id) {
  foreach ($it in (AllItems $view)) { if ($it.id -eq $id) { return [string]$it.status } }
  return ""
}

# ------------------------------------------------------------------ running things
function HostExe() { try { return (Get-Process -Id $PID).Path } catch { return "powershell.exe" } }
function Has([string]$cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function RunCmd([string]$file, [string[]]$extra = @()) {
  # A .cmd from the Orion folder, in this window. Returns its exit code.
  $full = Join-Path $Repo $file
  if ($DryRun) { Dry "would run: $file $($extra -join ' ')"; return 0 }
  try { return (StartAttached $full $extra) }   # attached to this console: its messages and pauses show
  catch { Bad "Couldn't start $file : $($_.Exception.Message)"; return 1 }
}
# Run another script or program right in this console (its messages and questions show normally), then leave its
# exit code in $script:LastCode. They must NOT be called as `$x = RunPs ...`: capturing their output would swallow
# what the program prints (and its questions), which is how the first version hid the update step's messages.
$script:LastCode = 0
function QuoteArgs([string[]]$list) {
  return (@($list) | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' '
}
# Start-Process attaches the program straight to this console window, so its messages and questions always show,
# even when the step around it is itself being captured (a plain `& program` inside a function is not enough).
function StartAttached([string]$exe, [string[]]$argList) {
  $path = $exe
  $found = Get-Command $exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found -and $found.Source) { $path = $found.Source }
  $q = QuoteArgs $argList
  if ($path -match '\.(cmd|bat)$') {          # npm and friends are .cmd files: run them through cmd.exe
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList ('/d /c ""' + $path + '" ' + $q + '"') -NoNewWindow -Wait -PassThru
  } elseif ($q) {
    $p = Start-Process -FilePath $path -ArgumentList $q -NoNewWindow -Wait -PassThru
  } else {
    $p = Start-Process -FilePath $path -NoNewWindow -Wait -PassThru
  }
  return $p.ExitCode
}
function RunPs([string]$file, [string[]]$extra = @()) {
  $full = $file
  if (-not [IO.Path]::IsPathRooted($file)) { $full = Join-Path $Repo $file }
  if ($DryRun) { Dry "would run: $file $($extra -join ' ')"; $script:LastCode = 0; return }
  try { $script:LastCode = StartAttached (HostExe) (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $full) + @($extra)) }
  catch { Bad "Couldn't start $file : $($_.Exception.Message)"; $script:LastCode = 1 }
}
function RunExe([string]$exe, [string[]]$argList) {
  if ($DryRun) { Dry "would run: $exe $($argList -join ' ')"; $script:LastCode = 0; return }
  try { $script:LastCode = StartAttached $exe $argList }
  catch { Bad "Couldn't start $exe : $($_.Exception.Message)"; $script:LastCode = 1 }
}
function NewWindow([string]$what, [string]$command) {
  if ($DryRun) { Dry "would open a new window for: $what"; return }
  Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $command -WorkingDirectory $Repo | Out-Null
}
function WaitLimit([int]$seconds) {
  if ($WaitCapSeconds -gt 0 -and $WaitCapSeconds -lt $seconds) { return $WaitCapSeconds }
  return $seconds
}
function RefreshPath() {
  if (-not $IsWin) { return }
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine"); $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$m;$u"
}

# =====================================================================================  the steps
$Steps = @(
  @{ id = "update"; title = "Put Claude's update in place" },
  @{ id = "save"; title = "Save your work to GitHub (optional)" },
  @{ id = "tools"; title = "Check the tools Orion needs" },
  @{ id = "start"; title = "Start Orion and the Test Center" },
  @{ id = "app"; title = "Put Orion Mobile on your phone" },
  @{ id = "pair"; title = "Pair your phone with this PC" },
  @{ id = "phonechecks"; title = "Let your phone check itself" },
  @{ id = "rest"; title = "Follow the Test Center to the end" },
  @{ id = "vm"; title = "Try OrionOS in a test computer (optional)" }
)

# ------------------------------------------------------------------ 1. update
function Step-update {
  $manifest = Join-Path (Join-Path $Repo "_incoming") "orion-update.manifest"
  if (-not (Test-Path -LiteralPath $manifest)) { Good "No update waiting. Orion is as Claude last left it."; return "done" }
  Info "Claude left an update in the _incoming folder."
  Info "It checks every file first and backs up anything it replaces."
  $me = $PSCommandPath
  $before = (Get-FileHash -LiteralPath $me -Algorithm SHA256).Hash
  RunPs (Join-Path "os" (Join-Path "windows" "Apply-OrionUpdate.ps1")); $code = $script:LastCode
  if ($code -ne 0) {
    Bad "The update wasn't put in place (it says why above). Nothing was lost."
    if (-not (Ask "Carry on with the rest anyway?" $false)) { throw "START-HERE-QUIT" }
    return "failed"
  }
  Good "Update in place."
  $after = (Get-FileHash -LiteralPath $me -Algorithm SHA256).Hash
  if ($after -ne $before -and -not $Relaunched) {
    SetStep "update" "done"
    Info "This wizard was updated too. Starting the new one..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $me, "-Relaunched")
    if ($DryRun) { $argList += "-DryRun" }
    $relaunchCode = StartAttached (HostExe) $argList   # attached: the new wizard talks to you directly
    exit $relaunchCode
  }
  return "done"
}

# ------------------------------------------------------------------ 2. save to GitHub
function Step-save {
  if ((StepState "save") -eq "done") { Good "Saved to GitHub earlier."; return "done" }
  if (-not (Has "git")) {
    Info "Git isn't installed on this PC, so there's nothing to save to GitHub. That's fine: skipping."
    Info "(If you want this later: get Git from git-scm.com, then run Start Here again.)"
    return "skipped"
  }
  if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $Repo ".git") "HEAD"))) {
    Info "This folder isn't connected to GitHub yet (Publish to GitHub.cmd does that once). Skipping."
    return "skipped"
  }
  Info "This puts your work on its own new branch on GitHub (never main) and opens a pull request to review."
  Info "Secret Shield checks it first; keys and your .env are never included."
  if (-not (Ask "Save your Orion work to GitHub now?" $true)) { Info "Not saved. You can do it any time with Save Orion work to GitHub.cmd."; return "skipped" }
  $code = RunCmd "Save Orion work to GitHub.cmd"
  if ($code -eq 0) { Good "Saved."; return "done" }
  Warn "It didn't save (the reason is above). Carrying on: this step is optional."
  return "failed"
}

# ------------------------------------------------------------------ 3. tools
function Ver([string]$exe, [string]$flag = "--version") {
  if (-not (Has $exe)) { return "" }
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try {
    $all = @(& $exe $flag 2>&1)
    $o = ""
    if ($all.Count -gt 0) { $o = [string]$all[0] }
    if ($LASTEXITCODE -eq 0 -and $o) { return $o.Trim() }
  } catch { } finally { $ErrorActionPreference = $prev }
  return ""
}
function OfferWinget([string]$what, [string]$id, [string]$size) {
  if (-not (Has "winget")) { return $false }
  if (-not (Ask "Install $what for you now with Windows' own installer ($size)?" $true)) { return $false }
  RunExe "winget" @("install", "--id", $id, "-e"); $code = $script:LastCode
  RefreshPath
  return ($code -eq 0)
}
function Step-tools {
  $missing = $false
  # Node.js and Python: Start Orion.cmd needs them and sets everything else up itself (npm packages, Python venv).
  $node = Ver "node"
  if ($node) { Good "Node.js $node" } else {
    Bad "Node.js isn't installed. Orion needs it to start."
    if (-not (OfferWinget "Node.js (LTS)" "OpenJS.NodeJS.LTS" "about 30 MB")) { Info "Get the LTS version from nodejs.org, then run Start Here again."; $missing = $true }
  }
  $venv = Join-Path $Repo "apps\daemon\.venv\Scripts\python.exe"
  $py = Ver "python"
  if (-not $py -and $IsWin) { $py = Ver "py" }
  if (-not $py -and -not $IsWin) { $py = Ver "python3" }
  if ($py -match "Python 3") { Good "$py" }
  elseif ($IsWin -and (Test-Path -LiteralPath $venv)) { Good "Python (Orion's own copy is already set up)" }
  else {
    Bad "Python 3 isn't installed. Orion's brain runs on it."
    if (-not (OfferWinget "Python 3.12" "Python.Python.3.12" "about 30 MB")) { Info "Get Python 3.12 from python.org (tick 'Add python.exe to PATH'), then run Start Here again."; $missing = $true }
  }
  $target = Join-Path $Repo "apps\desktop\src-tauri\target"
  if (Ver "cargo") { Good "Rust (builds the Orion window)" }
  elseif ($IsWin -and (Test-Path -LiteralPath $target)) { Good "The Orion window is already built" }
  else {
    Warn "Rust isn't installed. The first start builds the Orion window with it."
    if (-not (OfferWinget "Rust" "Rustlang.Rustup" "about 300 MB with its tools")) { Info "Get it from rustup.rs, then run Start Here again."; $missing = $true }
  }

  # Claude Code
  $claude = Ver "claude"
  if ($claude) {
    Good "Claude Code $claude"
  } else {
    Warn "Claude Code isn't installed. Orion uses it for Projects (Start a project with Claude)."
    if (-not (Has "npm") -and -not $DryRun) {
      Info "It installs with Node.js's npm, which isn't here yet. Run Start Here again after Node.js is installed."
    } elseif (Ask "Install Claude Code now (npm install -g @anthropic-ai/claude-code, about 1 minute)?" $true) {
      RunExe "npm" @("install", "-g", "@anthropic-ai/claude-code"); $code = $script:LastCode
      RefreshPath
      if ($code -eq 0) { Good "Claude Code installed."; $claude = "installed"; $State.steps["claude_signin"] = "" } else { Bad "That didn't install (the reason is above). You can skip it for now." }
    } else {
      Info "Skipped. Projects with Claude won't work until it's installed."
    }
  }
  if ($claude -and (StepState "claude_signin") -ne "done") {
    Info "Claude Code needs you to sign in once. A new window opens with it: follow what it says there."
    if (Ask "Open that window now?" $true) {
      NewWindow "signing in to Claude Code" "claude"
      WaitEnter "When you've signed in (or closed that window)"
      SetStep "claude_signin" "done"
      Good "Claude Code is ready."
    }
  }

  # Other helpers: only reported
  foreach ($pair in @(@("codex", "Codex"), @("gemini", "Gemini CLI"))) {
    if (Has $pair[0]) { Good "$($pair[1]) found" } else { Info "$($pair[1]) not found (optional)" }
  }
  if ($missing) {
    Bad "Something Orion needs is missing (see above)."
    if (Ask "Carry on anyway?" $false) { return "failed" }
    throw "START-HERE-QUIT"
  }
  return "done"
}

# ------------------------------------------------------------------ 4. start Orion + Test Center
function WaitForOrion([int]$minutes) {
  $deadline = (Get-Date).AddSeconds((WaitLimit ($minutes * 60)))
  $i = 0
  CtrlCAsKey $true
  try {
    while ((Get-Date) -lt $deadline) {
      if (OrionUp) {
        if (OrionReady) { ClearLine; return $true }
        Status "Orion is up; waiting for its key... (the first start can take several minutes)" $i
      } else {
        Status "Waiting for Orion to start... (the very first start can take several minutes)" $i
      }
      if (KeyToMoveOn) { ClearLine; return $false }
      Start-Sleep -Milliseconds 800
      $i++
    }
  } finally { CtrlCAsKey $false }
  ClearLine
  return $false
}
function Step-start {
  if (OrionReady) { Good "Orion is running and answers with its key." }
  else {
    Info "Starting Orion. The first time it sets itself up and builds its window: that can take 10 minutes or more."
    $code = RunCmd "Start Orion.cmd"
    if ($code -ne 0 -and $code -ne 3) { Bad "Orion didn't start (the reason is above)."; throw "START-HERE-QUIT" }
    if (-not $DryRun -and (Has "node")) {
      RunExe "node" @((Join-Path $Repo "scripts\test-orion.mjs"))   # attached: its "waiting for Orion" lines show
    } elseif ($DryRun) { Dry "would run: node scripts\test-orion.mjs" }
    if (-not (WaitForOrion 20)) {
      Bad "Orion didn't answer. Look in logs\orion-dev.log, or tell Claude what the Orion window shows."
      throw "START-HERE-QUIT"
    }
    Good "Orion is running and answers with its key."
  }
  $r = Call "GET" "/tests"
  if ($r.ok -and $r.json.summary) { Good "The Test Center is ready. Checks passed so far: $($r.json.summary.pass)." }
  return "done"
}

# ------------------------------------------------------------------ 5. phone app
$MobileDir = Join-Path (Join-Path $Repo "apps") "mobile"
$OrionSdk = ""
if ($env:LOCALAPPDATA) { $OrionSdk = Join-Path $env:LOCALAPPDATA "Orion\android-sdk" }
elseif ($DryRun) { $OrionSdk = Join-Path $HOME ".local/share/Orion/android-sdk" }
function NewestApk() {
  $list = @()
  $top = Join-Path $Repo "Orion-Mobile.apk"
  if (Test-Path -LiteralPath $top) { $list += Get-Item -LiteralPath $top }
  $out = Join-Path $MobileDir (Join-Path "app" (Join-Path "build" (Join-Path "outputs" "apk")))
  if (Test-Path -LiteralPath $out) { $list += Get-ChildItem -LiteralPath $out -Recurse -Filter "*.apk" -File -ErrorAction SilentlyContinue }
  return ($list | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
}
function NewestSource() {
  $times = @()
  $src = Join-Path $MobileDir (Join-Path "app" "src")
  if (Test-Path -LiteralPath $src) {
    $f = Get-ChildItem -LiteralPath $src -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { $times += $f.LastWriteTime }
  }
  foreach ($g in @((Join-Path $MobileDir "app\build.gradle.kts"), (Join-Path $MobileDir "build.gradle.kts"), (Join-Path $MobileDir "settings.gradle.kts"))) {
    $p = $g -replace "\\", [IO.Path]::DirectorySeparatorChar
    if (Test-Path -LiteralPath $p) { $times += (Get-Item -LiteralPath $p).LastWriteTime }
  }
  if ($times.Count -eq 0) { return [datetime]::MinValue }
  return ($times | Sort-Object -Descending | Select-Object -First 1)
}
function JavaMajor([string]$jhome) {
  $java = Join-Path (Join-Path $jhome "bin") "java.exe"
  if (-not $IsWin) { $java = Join-Path (Join-Path $jhome "bin") "java" }
  if (-not (Test-Path -LiteralPath $java)) { return 0 }
  $line = ""
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { $all = @(& $java -version 2>&1); if ($all.Count -gt 0) { $line = [string]$all[0] } } catch { } finally { $ErrorActionPreference = $prev }
  if ($line -match 'version "(\d+)(\.(\d+))?') {
    $m = [int]$Matches[1]
    if ($m -eq 1 -and $Matches[3]) { $m = [int]$Matches[3] }
    return $m
  }
  return 0
}
function FindJdk() {
  # The same places build-apk.ps1 looks (Android Studio's own Java, Microsoft OpenJDK, JAVA_HOME).
  $c = @()
  if ($env:ProgramFiles) {
    $c += "$env:ProgramFiles\Android\Android Studio\jbr", "$env:ProgramFiles\Android\Android Studio\jre"
    $c += Get-ChildItem "$env:ProgramFiles\Microsoft" -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object { $_.FullName }
  }
  if ($env:LOCALAPPDATA) { $c += "$env:LOCALAPPDATA\Programs\Android Studio\jbr" }
  if ($env:JAVA_HOME) { $c += $env:JAVA_HOME }
  foreach ($h in $c) { if ($h -and (JavaMajor $h) -ge 17) { return $h } }
  return ""
}
function FindSdk() {
  foreach ($c in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, $(if ($env:LOCALAPPDATA) { "$env:LOCALAPPDATA\Android\Sdk" }), $OrionSdk)) {
    if ($c -and (Test-Path -LiteralPath (Join-Path $c "platform-tools"))) { return $c }
  }
  return ""
}
# Google's command-line tools for Windows (developer.android.com/studio, "Command line tools only").
$ToolsZip = "commandlinetools-win-11076708_latest.zip"
$ToolsUrl = "https://dl.google.com/android/repository/$ToolsZip"
$ToolsSha = "4d6931209eebb1bfb7c7e8b240a6a3cb3ab24479ea294f3539429574b1eec862"   # fallback only (see below)
# The real check: Google's own package list names the newest Windows command-line tools zip and its SHA-1.
$RepoXml = "https://dl.google.com/android/repository/repository2-3.xml"
function GoogleToolsInfo() {
  try {
    $pp = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
    $raw = (Invoke-WebRequest -Uri $RepoXml -UseBasicParsing).Content
    $ProgressPreference = $pp
    [xml]$x = $raw
    $pkg = $x.SelectNodes("//remotePackage") | Where-Object { $_.path -eq "cmdline-tools;latest" } | Select-Object -First 1
    if (-not $pkg) { return $null }
    foreach ($a in $pkg.SelectNodes(".//archive")) {
      $hostOs = $a.SelectSingleNode("host-os")
      if ($hostOs -and $hostOs.InnerText -eq "windows") {
        $sum = $a.SelectSingleNode("complete/checksum")
        $url = $a.SelectSingleNode("complete/url")
        if ($sum -and $url -and $url.InnerText -match '^[A-Za-z0-9._-]+\.zip$') {
          return @{ Zip = $url.InnerText; Sha1 = $sum.InnerText.Trim().ToLowerInvariant() }
        }
      }
    }
  } catch { }
  return $null
}
function SetUpJdk() {
  Info "Building the phone app needs Java 17. Microsoft's free OpenJDK 17 is about 180 MB and takes a few minutes."
  if (-not (Has "winget") -and -not $DryRun) {
    Info "Windows' installer (winget) isn't here. Install Android Studio from developer.android.com/studio instead (it brings Java), then run Start Here again."
    return $false
  }
  if (-not (Ask "Install Java 17 now? Windows' installer shows its own terms" $true)) { return $false }
  RunExe "winget" @("install", "--id", "Microsoft.OpenJDK.17", "-e"); $code = $script:LastCode
  RefreshPath
  if ($DryRun) { return $true }
  return ($code -eq 0 -and (FindJdk))
}
function SetUpSdk([string]$jdk) {
  Info "Building the phone app also needs Google's Android SDK. Orion can set it up in $OrionSdk"
  Info "That's Google's command-line tools (about 150 MB) plus the Android 14 parts (about 1 GB): 10 to 20 minutes."
  Info "Android shows you its licences; you read them and type y yourself to accept each one."
  if (-not (Ask "Set up the Android SDK now?" $true)) { return "" }
  if ($DryRun) {
    Dry "would download $ToolsUrl and check its SHA-256"
    Dry "would unpack it into $OrionSdk\cmdline-tools\latest"
    Dry "would run: sdkmanager --licenses   (you read and accept them)"
    Dry "would run: sdkmanager platform-tools platforms;android-34 build-tools;34.0.0"
    return $OrionSdk
  }
  $tmp = Join-Path $env:TEMP "orion-android-tools"
  New-Item -ItemType Directory -Force -Path $tmp | Out-Null
  $info = GoogleToolsInfo
  if ($info) { $ToolsZip = $info.Zip; $ToolsUrl = "https://dl.google.com/android/repository/$ToolsZip" }
  $zip = Join-Path $tmp $ToolsZip
  Info "Downloading Google's Android command-line tools..."
  try {
    $pp = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $ToolsUrl -OutFile $zip -UseBasicParsing
    $ProgressPreference = $pp
  } catch { Bad "The download didn't work: $($_.Exception.Message)"; return "" }
  if ($info) {
    $got1 = (Get-FileHash -LiteralPath $zip -Algorithm SHA1).Hash.ToLowerInvariant()
    if ($got1 -ne $info.Sha1) {
      Bad "The download doesn't match the fingerprint Google publishes for it, so it wasn't used. Try again later."
      return ""
    }
    Good "Download checked against Google's published fingerprint."
  } else {
  $got = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($got -ne $ToolsSha) {
    Warn "The download's fingerprint isn't the one this wizard knows. Google may have published a newer file."
    Info "Got:      $got"
    Info "Expected: $ToolsSha"
    Info "You can compare it on developer.android.com/studio (Command line tools only)."
    if (-not (Ask "Use it anyway?" $false)) { return "" }
  }
  }
  $unz = Join-Path $tmp "unzipped"
  if (Test-Path -LiteralPath $unz) { Remove-Item -LiteralPath $unz -Recurse -Force }
  Expand-Archive -LiteralPath $zip -DestinationPath $unz -Force
  $latest = Join-Path $OrionSdk "cmdline-tools\latest"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $latest) | Out-Null
  if (Test-Path -LiteralPath $latest) { Remove-Item -LiteralPath $latest -Recurse -Force }
  Move-Item -LiteralPath (Join-Path $unz "cmdline-tools") -Destination $latest
  $sdkm = Join-Path $latest "bin\sdkmanager.bat"
  $env:JAVA_HOME = $jdk
  Say ""
  Info "Now Android shows its licences. Read each one and type y to accept it (or n to stop)."
  RunExe $sdkm @("--sdk_root=$OrionSdk", "--licenses")   # attached: you read and answer each licence yourself
  if (-not (Test-Path -LiteralPath (Join-Path $OrionSdk "licenses\android-sdk-license"))) {
    Bad "The Android SDK licence wasn't accepted, so the phone app can't be built here. Nothing else changed."
    return ""
  }
  Info "Downloading the Android 14 parts (about 1 GB)..."
  RunExe $sdkm @("--sdk_root=$OrionSdk", "platform-tools", "platforms;android-34", "build-tools;34.0.0"); $code = $script:LastCode
  if ($code -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $OrionSdk "platform-tools"))) { Bad "The Android parts didn't finish downloading. Run Start Here again to retry."; return "" }
  Good "Android SDK ready."
  return $OrionSdk
}
function WriteLocalProperties([string]$sdk) {
  $lp = "sdk.dir=" + ($sdk -replace '\\', '\\' -replace ':', '\:')
  if ($DryRun) { Dry "would write apps\mobile\local.properties: $lp"; return }
  Set-Content -LiteralPath (Join-Path $MobileDir "local.properties") -Value $lp -Encoding ascii
}
function DrawQr($rows) {
  # Two QR rows per text line with half blocks; light squares are drawn (cameras read it as dark on light).
  $full = [string][char]0x2588; $upper = [string][char]0x2580; $lower = [string][char]0x2584
  $rows = @($rows)
  if ($rows.Count % 2 -eq 1) { $rows += ("0" * $rows[0].Length) }
  for ($y = 0; $y -lt $rows.Count; $y += 2) {
    $a = [string]$rows[$y]; $b = [string]$rows[$y + 1]
    $sb = New-Object Text.StringBuilder
    for ($x = 0; $x -lt $a.Length; $x++) {
      $la = ($a[$x] -eq [char]"0"); $lb = ($b[$x] -eq [char]"0")
      if ($la -and $lb) { [void]$sb.Append($full) } elseif ($la) { [void]$sb.Append($upper) } elseif ($lb) { [void]$sb.Append($lower) } else { [void]$sb.Append(" ") }
    }
    Write-Host ("    " + $sb.ToString()) -ForegroundColor White -BackgroundColor Black
  }
}
function ShareOverWifi() {
  $r = Call "POST" "/phones/app-share" @{ minutes = 15 }
  if ($r.dry) { Dry "would show a QR code for http://<this PC's Wi-Fi address>:9121/phone/app.apk"; WaitEnter "When Orion Mobile is installed on your phone"; return $true }
  if (-not $r.ok) {
    if ($r.status -eq 404) { Bad "Orion here is too old to share the app (update it), or the app isn't built." } else { Bad "Orion didn't answer the share request." }
    return $false
  }
  $s = $r.json
  if ($s.lan -and $s.lan.ok -eq $false -and $s.lan.fix -eq "open_access" -and $IsWin) {
    Warn "$($s.lan.words)"
    Info "Open Phone Access lets phones on your home Wi-Fi reach Orion's phone port (9121) on this PC, nothing else."
    Info "Windows asks for admin permission once."
    if (Ask "Let your phone in now?" $true) {
      RunPs (Join-Path "os" (Join-Path "windows" "Open-PhoneAccess.ps1")) @("-ThisPC")
      $r2 = Call "GET" "/phones/app-share"
      if ($r2.ok) { $s = $r2.json }
    }
  }
  if (-not $s.url) { Bad "This PC isn't on a network the phone can reach. Connect it to your Wi-Fi and run Start Here again."; return $false }
  Say ""
  Say "  On your phone (on the same Wi-Fi): open the camera and point it at this code." White
  Say ""
  DrawQr $s.qr_rows
  Say ""
  Info "Or type this in the phone's browser: $($s.url)"
  Info "Open the download and tap Install. Android may ask you to allow installs from your browser once."
  Info "The link works for 15 minutes."
  $deadline = (Get-Date).AddSeconds((WaitLimit (15 * 60)))
  $i = 0; $seen = $false
  CtrlCAsKey $true
  try {
    while ((Get-Date) -lt $deadline) {
      $st = Call "GET" "/phones/app-share"
      if ($st.ok -and $st.json.downloads -gt 0 -and -not $seen) { ClearLine; Good "Your phone downloaded the app. Tap Install on the phone."; $seen = $true }
      if ($st.ok -and -not $st.json.open) { break }
      $msg = "Waiting for your phone to download it... press Enter when Orion Mobile is installed"
      if ($seen) { $msg = "Press Enter when Orion Mobile is installed" }
      Status $msg $i
      if (KeyToMoveOn) { break }
      Start-Sleep -Milliseconds 1500
      $i++
    }
  } finally { CtrlCAsKey $false }
  ClearLine
  if (-not (Interactive) -and $null -ne $Answers) { WaitEnter "When Orion Mobile is installed on your phone" }
  $null = Call "POST" "/phones/app-share/close"
  return $true
}
function Step-app {
  if ((StepState "app") -eq "done") { Good "Orion Mobile was put on your phone earlier."; return "done" }
  if ((PairedPhones).Count -gt 0) { Good "A phone is already paired, so Orion Mobile is on it."; return "done" }
  $apk = NewestApk
  if ($apk -and $apk.LastWriteTime -ge (NewestSource)) {
    Good "The phone app is already built and up to date ($($apk.Name))."
  } else {
    if ($apk) { Info "The phone app changed since it was last built: building it again." } else { Info "The phone app isn't built yet." }
    $jdk = FindJdk
    if ($jdk) { Good "Java 17+ found." } else {
      if (-not (SetUpJdk)) { Warn "Skipping the phone app for now (no Java 17)."; return "skipped" }
      $jdk = FindJdk
      if (-not $jdk -and $DryRun) { $jdk = "(Java 17)" }
    }
    $sdk = FindSdk
    if ($sdk) { Good "Android SDK found." } else {
      $sdk = SetUpSdk $jdk
      if (-not $sdk) { Warn "Skipping the phone app for now (no Android SDK)."; return "skipped" }
      WriteLocalProperties $sdk
    }
    if (-not $DryRun -and $jdk) { $env:JAVA_HOME = $jdk }
    if ($sdk -and -not $DryRun) { $env:ANDROID_HOME = $sdk }
    Info "Building Orion Mobile (first time: 3 to 10 minutes)..."
    RunPs (Join-Path "apps" (Join-Path "mobile" "build-apk.ps1")) @("-NoInstall"); $code = $script:LastCode
    if ($code -ne 0) { Bad "The phone app didn't build (the reason is above). Copy the red text into your chat with Claude."; return "failed" }
    $apk = NewestApk
    if (-not $apk -and -not $DryRun) { Bad "The build finished but no app file appeared."; return "failed" }
    Good "Phone app built."
  }

  # Install: USB cable if a phone is plugged in with USB debugging on, otherwise over the Wi-Fi.
  $sdk = FindSdk
  $adb = ""
  if ($sdk) { $adb = Join-Path (Join-Path $sdk "platform-tools") "adb.exe" }
  if ($adb -and (Test-Path -LiteralPath $adb) -and -not $DryRun) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $devices = @((& $adb devices) | Where-Object { $_ -match "\tdevice$" })
    $ErrorActionPreference = $prev
    if ($devices.Count -gt 0) {
      Good "A phone is plugged in with USB debugging on."
      if (Ask "Install Orion Mobile on it over the cable now?" $true) {
        RunExe $adb @("install", "-r", (NewestApk).FullName); $code = $script:LastCode
        if ($code -eq 0) { Good "Installed. Open Orion on your phone."; return "done" }
        Warn "That didn't work over the cable. Let's do it over the Wi-Fi instead."
      }
    }
  }
  Info "Now let's put it on your phone over your Wi-Fi: no cable needed."
  if (ShareOverWifi) { Good "Orion Mobile is on your phone."; return "done" }
  return "failed"
}

# ------------------------------------------------------------------ 6. pair
function Step-pair {
  $p = PairedPhones
  if ($p.Count -gt 0) { Good "Your phone is paired."; return "done" }
  $r = Call "POST" "/hud/open" @{ what = "phone" }
  if ($r.ok -and $r.json -and $r.json.window -eq $false) { Info "The Orion window isn't open yet. When it is: Menu > Phone." }
  elseif ($r.ok) { Good "The Phone panel is open in the Orion window." }
  elseif (-not $r.dry) { Info "Open the Orion window, then Menu > Phone." }
  Say ""
  Say "  On your phone:" White
  Say "    1. Open Orion Mobile." White
  Say "    2. Tap Pair with my PC and point the camera at the QR code in the Orion window." White
  Say "    3. Check the five words match on both screens, then press Confirm in the Orion window." White
  Say ""
  $deadline = (Get-Date).AddSeconds((WaitLimit (30 * 60)))
  $i = 0
  CtrlCAsKey $true
  try {
    while ((Get-Date) -lt $deadline) {
      if ((PairedPhones).Count -gt 0) { ClearLine; Good "Your phone is paired."; return "done" }
      Status "Waiting for your phone... (Enter to skip)" $i
      if (KeyToMoveOn) { break }
      Start-Sleep -Milliseconds 2000
      $i++
    }
  } finally { CtrlCAsKey $false }
  ClearLine
  Info "Skipped for now. Pair any time from the Orion window: Menu > Phone."
  return "skipped"
}

# ------------------------------------------------------------------ 7. phone checks
function PhoneProgress($view) {
  $items = @(AllItems $view | Where-Object { $_.where -eq "phone" })
  $doneN = @($items | Where-Object { $_.status -ne "todo" -and $_.status -ne "running" }).Count
  return @{ done = $doneN; total = $items.Count; reached = ((ItemStatus $view "phone_reaches") -eq "pass") }
}
function Step-phonechecks {
  $r = Call "GET" "/tests"
  if (-not $r.ok) { Bad "Orion isn't answering."; return "failed" }
  $pp = PhoneProgress $r.json
  if ((StepState "phonechecks") -eq "done" -or ($pp.reached -and $pp.done -ge $pp.total)) { Good "Your phone has checked itself."; return "done" }
  if ((PairedPhones).Count -eq 0) { Info "No phone is paired, so this waits for later."; return "skipped" }
  Say "  On your phone: Menu > Test. Follow what it says there; this window follows along." White
  $deadline = (Get-Date).AddSeconds((WaitLimit (30 * 60)))
  $i = 0
  CtrlCAsKey $true
  try {
    while ((Get-Date) -lt $deadline) {
      $r = Call "GET" "/tests"
      if ($r.ok) {
        $pp = PhoneProgress $r.json
        if ($pp.reached -and $pp.done -ge $pp.total) { ClearLine; Good "Your phone has checked itself ($($pp.done) of $($pp.total))."; return "done" }
        $reach = "not heard from your phone yet"
        if ($pp.reached) { $reach = "your phone reaches this PC" }
        Status "Phone checks: $($pp.done) of $($pp.total) done, $reach. (Enter to move on)" $i
      }
      if (KeyToMoveOn) { break }
      Start-Sleep -Milliseconds 2500
      $i++
    }
  } finally { CtrlCAsKey $false }
  ClearLine
  if ($pp.reached) { Good "Your phone reaches this PC. The rest of its checks carry on in the next step."; return "done" }
  Info "Moving on. The phone's checks also show in the Test Center."
  return "skipped"
}

# ------------------------------------------------------------------ 8. the rest, live
function Step-rest {
  $r = Call "POST" "/tests/open" @{ run = $true }
  if ($r.ok) { Good "The Test Center is open in the Orion window and the automatic checks are running." }
  Info "Follow the Next card in the Orion window (or on your phone). This line keeps up with it."
  Info "Press Enter when you're done for now."
  $deadline = (Get-Date).AddSeconds((WaitLimit (4 * 3600)))
  $i = 0; $finished = $false
  CtrlCAsKey $true
  try {
    while ((Get-Date) -lt $deadline) {
      $v = Call "GET" "/tests"
      if ($v.ok -and $v.json.summary) {
        $s = $v.json.summary
        $nxt = ""
        if ($v.json.next) { $nxt = "Next: " + [string]$v.json.next.next }
        Status ("$Tick $($s.pass)  $Cross $($s.fail)  $Dot $($s.todo) to do  - $($s.skip) skipped   $nxt") $i
        if ([int]$s.todo -eq 0 -and -not $v.json.running) { $finished = $true; break }
      }
      if (KeyToMoveOn) { break }
      Start-Sleep -Milliseconds 3000
      $i++
    }
  } finally { CtrlCAsKey $false }
  ClearLine
  if (-not (Interactive) -and $null -ne $Answers -and -not $finished) { WaitEnter "I'm done for now" }
  $v = Call "GET" "/tests"
  if ($v.ok -and $v.json.summary) {
    $s = $v.json.summary
    Info "$($s.pass) passed, $($s.fail) didn't work, $($s.todo) still to do, $($s.skip) skipped."
  }
  $rep = Call "GET" "/tests/report"
  if ($rep.ok -and $rep.json.markdown) {
    if ($DryRun) { Dry "would copy the test report ($(([string]$rep.json.markdown).Length) characters) to the clipboard" }
    else {
      try { Set-Clipboard -Value ([string]$rep.json.markdown); Good "The test report is copied. Paste it into your chat with Claude." }
      catch { Warn "Couldn't copy it here. In the Orion window: Test Center > Copy report." }
    }
  } else { Warn "Couldn't get the report. In the Orion window: Test Center > Copy report." }
  if ($finished) { return "done" }
  return "skipped"
}

# ------------------------------------------------------------------ 9. OrionOS VM
function Step-vm {
  if ((StepState "vm") -eq "done") { Good "Started earlier."; return "done" }
  Info "This boots OrionOS in a test computer inside VirtualBox, separate from everything else (needs VirtualBox)."
  if (-not (Ask "Start Try OrionOS VM now?" $false)) { Info "Skipped. Any time: double-click Try OrionOS VM.cmd."; return "skipped" }
  if ($DryRun) { Dry "would open: Try OrionOS VM.cmd" } else { Start-Process -FilePath (Join-Path $Repo "Try OrionOS VM.cmd") -WorkingDirectory $Repo | Out-Null }
  Good "Try OrionOS VM is starting in its own window."
  return "done"
}

# =====================================================================================  main
function Mark([string]$result) {
  switch ($result) { "done" { return $Tick } "failed" { return $Cross } default { return $Dot } }
}
function Overview() {
  $n = 1
  foreach ($s in $Steps) {
    $res = StepState $s.id
    $color = "Gray"
    if ($res -eq "done") { $color = "Green" } elseif ($res -eq "failed") { $color = "Red" } elseif ($res -eq "skipped") { $color = "DarkGray" }
    Write-Host ("  " + (Mark $res) + " " + $n + ". " + $s.title) -ForegroundColor $color
    $n++
  }
}

$State = LoadState
if ($Restart) { $State = NewState; SaveState }
if (Interactive) { try { Clear-Host } catch { } }
Say ""
Say "  Orion - Start Here" Cyan
Say "  One step at a time. Enter picks the recommended answer. Q stops; run it again to carry on." DarkGray
if ($DryRun) { Say "  Dry run: nothing on this PC is changed." DarkCyan }
Say ""
$hasProgress = ($State.steps.Count -gt 0)
Overview
Say ""
try {
  if ($hasProgress -and -not $Relaunched) {
    $a = ReadAnswer "Welcome back. Press Enter to carry on, or R to start over"
    if ($a -match "^[Qq]") { throw "START-HERE-QUIT" }
    if ($a -match "^[Rr]") { $State = NewState; SaveState; Say ""; Overview; Say "" }
  }
  $n = 1
  foreach ($s in $Steps) {
    Say ""
    Say "  Step $n of $($Steps.Count): $($s.title)" Cyan
    $res = & ("Step-" + $s.id)
    SetStep $s.id ([string]$res)
    $n++
  }
  # [voice2] Orion's voice: open the voice page in the Orion window, or say where it is
  $v = Call "POST" "/hud/open" @{ what = "voice" }
  if ($v.ok -and $v.json -and $v.json.window -ne $false) { Good "Orion's voice is open in the Orion window: pick how Orion sounds." }
  elseif (-not $v.dry) { Info "Pick how Orion sounds any time: in the Orion window, Menu > Orion's voice." }
  Say ""
  Say "  All done for now:" Cyan
  Overview
  Say ""
  Say "  Run Start Here again any time: finished steps are skipped." DarkGray
  $exitCode = 0
} catch {
  if ($_.Exception.Message -eq "START-HERE-QUIT") {
    Say ""
    Say "  Stopped. Your progress is saved: double-click Start Here.cmd to carry on." Yellow
    $exitCode = 0
  } else {
    Say ""
    Bad "Something went wrong: $($_.Exception.Message)"
    Say "  Your progress is saved. Copy this message into your chat with Claude." Yellow
    $exitCode = 1
  }
}
exit $exitCode
