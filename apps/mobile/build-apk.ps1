<#
.SYNOPSIS
  Builds Orion Mobile (Android) into Orion-Mobile.apk using the Android Studio already on this PC.
  If a phone is plugged in with USB debugging on, it installs it straight onto the phone.
  Needs: Android Studio (it brings Java 17+ and the Android SDK). Nothing else.
  Or the Java 17 + Android SDK that "Start Here.cmd" sets up (Microsoft OpenJDK 17, %LOCALAPPDATA%\Orion\android-sdk).
  -NoInstall: only build; Start Here.cmd asks before installing on a phone.
#>
param([switch]$NoInstall)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
function Say($m, $c = "Cyan") { Write-Host "[Orion Mobile] $m" -ForegroundColor $c }
function Fail($m, $hint = "") {
  Say $m "Red"
  if ($hint) { Say $hint "Yellow" }
  exit 1
}

# 0. The project files themselves.
foreach ($f in @("gradlew.bat", "gradle\wrapper\gradle-wrapper.jar", "gradle\wrapper\gradle-wrapper.properties", "app\build.gradle.kts")) {
  if (-not (Test-Path (Join-Path $Here $f))) {
    Fail "A project file is missing: apps\mobile\$f" "Get a fresh copy of the Orion folder and run this again."
  }
}

# 1. Java 17 or newer: Android Studio ships its own (jbr). Standard, per-user and JetBrains Toolbox installs.
$candidates = @(
  "$env:ProgramFiles\Android\Android Studio\jbr",
  "$env:ProgramFiles\Android\Android Studio\jre",
  "$env:LOCALAPPDATA\Programs\Android Studio\jbr",
  "${env:ProgramFiles(x86)}\Android\Android Studio\jbr"
)
# [start-here] Microsoft OpenJDK 17 (winget install Microsoft.OpenJDK.17), as Start Here.cmd offers it
$candidates += Get-ChildItem "$env:ProgramFiles\Microsoft" -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | ForEach-Object { $_.FullName }
$toolbox = Join-Path $env:LOCALAPPDATA "JetBrains\Toolbox\apps"
if (Test-Path $toolbox) {
  $candidates += Get-ChildItem $toolbox -Recurse -Directory -Filter "jbr" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "AndroidStudio|android-studio" } | ForEach-Object { $_.FullName }
}
$candidates += $env:JAVA_HOME
$jdk = $null
foreach ($c in $candidates) {
  if ($c -and (Test-Path (Join-Path $c "bin\java.exe"))) { $jdk = $c; break }
}
if (-not $jdk) {
  Fail "Couldn't find Java. It comes with Android Studio." "Install Android Studio from developer.android.com/studio, open it once, then run this again."
}
$verLine = (& (Join-Path $jdk "bin\java.exe") -version 2>&1 | Select-Object -First 1) -as [string]
$major = 0
if ($verLine -match 'version "(\d+)(\.(\d+))?') {
  $major = [int]$Matches[1]
  if ($major -eq 1 -and $Matches[3]) { $major = [int]$Matches[3] }   # old "1.8" style
}
if ($major -lt 17) {
  Fail "The Java at $jdk is version $major. Orion Mobile needs Java 17 or newer." "Update Android Studio (it brings a newer Java), or point JAVA_HOME at Java 17+."
}
$env:JAVA_HOME = $jdk
Say "Java $major`: $jdk"

# 2. Android SDK: Android Studio's first-run wizard puts it in %LOCALAPPDATA%\Android\Sdk.
$sdk = $null
foreach ($c in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, "$env:LOCALAPPDATA\Android\Sdk", "$env:LOCALAPPDATA\Orion\android-sdk")) {
  if ($c -and (Test-Path (Join-Path $c "platform-tools"))) { $sdk = $c; break }
}
if (-not $sdk) {
  Fail "No Android SDK yet." "Open Android Studio, finish its setup wizard (Standard install), then run this again."
}
Say "Android SDK: $sdk"
if (-not (Test-Path (Join-Path $sdk "platforms\android-34"))) {
  Say "Android 14 (API 34) SDK platform isn't installed yet; the build will try to download it." "Yellow"
  Say "If that fails: Android Studio > Settings > Languages & Frameworks > Android SDK, tick Android 14.0 (API 34), OK." "Yellow"
}
$lp = "sdk.dir=" + ($sdk -replace '\\', '\\' -replace ':', '\:')
Set-Content -Path (Join-Path $Here "local.properties") -Value $lp -Encoding ascii

# 3. Android SDK licences: you read and accept them yourself (nothing types "y" for you). Android Studio's setup
#    or Start Here.cmd usually did this already; then this step is skipped.
$sdkm = Get-ChildItem (Join-Path $sdk "cmdline-tools") -Recurse -Filter "sdkmanager.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($sdkm -and -not (Test-Path (Join-Path $sdk "licenses\android-sdk-license"))) {
  Say "Android asks you to accept its SDK licences. Read each one and type y to accept it."
  $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  & $sdkm.FullName "--sdk_root=$sdk" --licenses
  $ErrorActionPreference = $prev
}

# 4. Build (the first build downloads Gradle and Android build tools: a few minutes).
$version = ""
$gradle = Get-Content (Join-Path $Here "app\build.gradle.kts") -Raw
if ($gradle -match 'versionName\s*=\s*"([^"]+)"') { $version = $Matches[1] }
Say "Building Orion Mobile $version... (first time: 3-10 minutes)"
Push-Location $Here
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& .\gradlew.bat assembleDebug --no-daemon
$code = $LASTEXITCODE
$ErrorActionPreference = $prev
Pop-Location
if ($code -ne 0) {
  Say "Build failed. Copy the red text above and send it to Claude." "Red"
  Say "If it mentions licences: open Android Studio > Settings > Android SDK > SDK Tools, tick 'Android SDK Command-line Tools', OK, then run this again." "Yellow"
  Say "If it can't download Gradle or plugins: check the internet connection, then run this again." "Yellow"
  exit 1
}
$apk = Join-Path $Here "app\build\outputs\apk\debug\app-debug.apk"
if (-not (Test-Path $apk)) { Fail "The build said it worked, but there's no APK at $apk." "Send this message to Claude." }
$out = Join-Path $Repo "Orion-Mobile.apk"
Copy-Item $apk $out -Force
Say "Built Orion Mobile $version" "Green"
Say "APK: $out" "Green"

# 5. Install directly if a phone is connected over USB with USB debugging on.
$adb = Join-Path $sdk "platform-tools\adb.exe"
if (-not $NoInstall -and (Test-Path $adb)) {
  $devices = (& $adb devices) | Where-Object { $_ -match "\tdevice$" }
  if ($devices) {
    Say "Phone found over USB: installing..."
    & $adb install -r $out
    if ($LASTEXITCODE -eq 0) { Say "Installed. Open Orion on your phone." "Green" }
    else { Say "Installing over USB didn't work. Copy $out to the phone and tap it instead." "Yellow" }
  } else {
    Say "No phone on USB. Copy Orion-Mobile.apk to your phone (USB, Google Drive or email) and tap it to install." "Yellow"
  }
}
exit 0
