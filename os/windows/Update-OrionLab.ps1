<#
.SYNOPSIS
  Puts the latest Orion code from this folder into the running orion-lab VM as an update disc.
  Then, inside the lab, run: sudo orion-update   (see os\README.md, "Updating the lab").
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VmRoot = Join-Path $env:USERPROFILE "VirtualBox VMs\OrionOS"
function Say($m, $c = "Cyan") { Write-Host "[OrionOS] $m" -ForegroundColor $c }
$VBox = $null
foreach ($cand in @("$env:VBOX_MSI_INSTALL_PATH\VBoxManage.exe", "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe")) {
  if ($cand -and (Test-Path $cand)) { $VBox = $cand; break }
}
if (-not $VBox) { Say "VirtualBox not found." "Red"; exit 1 }

# Bundle (same layout as New-OrionVMs.ps1)
$Work = Join-Path $VmRoot "cache\update-$(Get-Date -Format yyyyMMddHHmmss)"
$Stage = Join-Path $Work "bundle"
New-Item -ItemType Directory -Force -Path "$Stage\apps", "$Stage\dist", "$Work\disc" | Out-Null
Copy-Item -Recurse (Join-Path $Repo "apps\daemon") "$Stage\apps\daemon"
Copy-Item -Recurse (Join-Path $Repo "apps\boardroom") "$Stage\apps\boardroom"
Copy-Item -Recurse (Join-Path $Repo "os") "$Stage\os"
foreach ($junk in @("apps\daemon\.venv", "apps\daemon\.env", "apps\daemon\__pycache__", "apps\daemon\tests\__pycache__", "apps\daemon\.pytest_cache", "os\dist")) {
  $p = Join-Path $Stage $junk; if (Test-Path $p) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
}
$Deb = Get-ChildItem (Join-Path $Repo "os\dist") -Filter "*.deb" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Deb) { Copy-Item $Deb.FullName "$Stage\dist\" }
Set-Content -Path "$Stage\BUNDLE_VERSION" -Value (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK") -Encoding ascii
& tar.exe -czf (Join-Path $Work "disc\orion-bundle.tar.gz") -C $Stage .
if ($LASTEXITCODE -ne 0) { throw "tar failed" }

# Disc image labelled ORIONUPD
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
$iso = Join-Path $VmRoot "orion-lab-update.iso"
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fsi.FileSystemsToCreate = 3; $fsi.FreeMediaBlocks = 0; $fsi.VolumeName = "ORIONUPD"
$fsi.Root.AddTree((Join-Path $Work "disc"), $false)
$img = $fsi.CreateResultImage()
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $VBox storageattach orion-lab --storagectl SATA --port 1 --device 0 --type dvddrive --medium emptydrive --forceunmount 2>&1 | Out-Null
$ErrorActionPreference = $prev
if (Test-Path $iso) { Remove-Item $iso -ErrorAction SilentlyContinue }
[OrionIso]::Write($iso, $img.ImageStream, $img.BlockSize, $img.TotalBlocks)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($img)
[void][Runtime.InteropServices.Marshal]::ReleaseComObject($fsi)
[GC]::Collect(); [GC]::WaitForPendingFinalizers()
Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue

$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $VBox storageattach orion-lab --storagectl SATA --port 1 --device 0 --type dvddrive --medium $iso --forceunmount | Out-Host
$code = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($code -ne 0) { Say "Couldn't insert the update disc into orion-lab (is it running?)." "Red"; exit 1 }
Say "Update disc inserted into orion-lab." "Green"
Write-Host ""
Write-Host "Now, in the orion-lab window: press Ctrl+Alt+T and run"
Write-Host "    su -P - orion-admin -c 'sudo orion-update'"
Write-Host "(first time only, if orion-update isn't found yet:)"
Write-Host "    su -P - orion-admin -c 'sudo sh -c ""mount -L ORIONUPD /mnt && tar -xzf /mnt/orion-bundle.tar.gz -C /opt/orion-src && umount /mnt && bash /opt/orion-src/os/lab/provision.sh""'"
