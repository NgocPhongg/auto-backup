$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Version = if ($args.Count -gt 0) { $args[0] } else { "1.0.0" }
$DistDir = Join-Path $Root "dist"
$ReleaseDir = Join-Path $Root "release"
$ZipPath = Join-Path $ReleaseDir "AutoBackup_v$Version.zip"

Set-Location $Root

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --name AutoBackup `
  --add-data "gologin_zeroprofile.zip;." `
  --add-data "proxy_auth_ext;proxy_auth_ext" `
  --add-data "proxy_ext_0;proxy_ext_0" `
  --hidden-import curl_cffi `
  --hidden-import msal `
  --hidden-import pandas `
  --hidden-import yt_dlp `
  --collect-all playwright `
  --collect-all gologin `
  main_gui.py

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
if (Test-Path $ZipPath) {
  Remove-Item -LiteralPath $ZipPath -Force
}

$SourcePath = Join-Path $DistDir "AutoBackup\*"
$compressed = $false
for ($i = 1; $i -le 3; $i++) {
  try {
    Start-Sleep -Seconds 5
    Compress-Archive -Path $SourcePath -DestinationPath $ZipPath -Force
    $compressed = $true
    break
  } catch {
    if ($i -eq 3) {
      throw
    }
    Start-Sleep -Seconds 5
  }
}

Write-Host "Build done:"
Write-Host $ZipPath
