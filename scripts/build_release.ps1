$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Version = if ($args.Count -gt 0) { $args[0] } else { "1.0.0" }
$ReleaseDir = Join-Path $Root "release"
$TempBase = Join-Path ([System.IO.Path]::GetTempPath()) "AutoBackupBuilds"
$BuildRoot = Join-Path $TempBase ("AutoBackup_build_" + [guid]::NewGuid().ToString("N"))
$DistDir = Join-Path $BuildRoot "dist"
$WorkDir = Join-Path $BuildRoot "work"
$PreferredZipPath = Join-Path $ReleaseDir "AutoBackup_v$Version.zip"

function Copy-ReleaseEntry {
  param(
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [Parameter(Mandatory = $true)][string]$DestinationPath
  )

  if (-not (Test-Path -LiteralPath $SourcePath)) {
    throw "Missing required release entry: $SourcePath"
  }

  $parent = Split-Path -Parent $DestinationPath
  if ($parent) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }

  $item = Get-Item -LiteralPath $SourcePath
  if ($item.PSIsContainer) {
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Recurse -Force
  } else {
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
  }
}

function Get-ReleaseZipPath {
  param(
    [Parameter(Mandatory = $true)][string]$PreferredPath
  )

  if (-not (Test-Path -LiteralPath $PreferredPath)) {
    return $PreferredPath
  }

  try {
    Remove-Item -LiteralPath $PreferredPath -Force -ErrorAction Stop
    return $PreferredPath
  } catch {
    $dir = Split-Path -Parent $PreferredPath
    $base = [System.IO.Path]::GetFileNameWithoutExtension($PreferredPath)
    $ext = [System.IO.Path]::GetExtension($PreferredPath)
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    return (Join-Path $dir ($base + "_" + $stamp + $ext))
  }
}

function Stage-ExternalTools {
  param(
    [Parameter(Mandatory = $true)][string]$AppDir
  )

  $editSource = Join-Path $Root "EDIT_1"
  $editTarget = Join-Path $AppDir "EDIT_1"
  $creatorSource = Join-Path $Root "Creator Now Cut 14112025"
  $creatorTarget = Join-Path $AppDir "Creator Now Cut 14112025"

  foreach ($target in @($editTarget, $creatorTarget)) {
    if (Test-Path -LiteralPath $target) {
      Remove-Item -LiteralPath $target -Recurse -Force
    }
  }

  New-Item -ItemType Directory -Force -Path $editTarget | Out-Null
  foreach ($relative in @(
    "main.py",
    "preview_engine.py",
    "queue_manager.py",
    "render_engine.py",
    "scene_detector.py",
    "style.qss",
    "ui_main.py",
    "video_engine.py"
  )) {
    Copy-ReleaseEntry (Join-Path $editSource $relative) (Join-Path $editTarget $relative)
  }

  New-Item -ItemType Directory -Force -Path $creatorTarget | Out-Null
  foreach ($relative in @(
    "01.CAT STOCK.bat",
    "02.EDIT.bat",
    "03.tachanh.bat",
    "04.tachmp3.bat",
    "05.gop_le.bat",
    "06.gop_chan.bat",
    "07.reset.bat",
    "08. xoa photo le.bat",
    "09. xoa photo chan.bat",
    "10. gop photo random.bat",
    "11. gop video stock random.bat",
    "START Creator Now Studio.bat",
    "creator_now_studio.py",
    "ffmpeg.exe",
    "bg"
  )) {
    Copy-ReleaseEntry (Join-Path $creatorSource $relative) (Join-Path $creatorTarget $relative)
  }
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $ReleaseDir, $DistDir, $WorkDir | Out-Null

$VersionFile = Join-Path $Root "app_version.py"
$VersionText = Get-Content -LiteralPath $VersionFile -Raw
if ($VersionText -notmatch "APP_VERSION\s*=\s*`"([^`"]+)`"") {
  throw "Cannot read APP_VERSION from app_version.py"
}
$AppVersion = $Matches[1]
if ($AppVersion -ne $Version) {
  throw "Build version mismatch. app_version.py has $AppVersion but build argument is $Version."
}

python -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath $DistDir `
  --workpath $WorkDir `
  AutoBackup.spec

$AppDistDir = Join-Path $DistDir "AutoBackup"
if (-not (Test-Path -LiteralPath $AppDistDir -PathType Container)) {
  throw "PyInstaller output not found: $AppDistDir"
}

Stage-ExternalTools -AppDir $AppDistDir

$ZipPath = Get-ReleaseZipPath -PreferredPath $PreferredZipPath

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

try {
  if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction Stop
  }
} catch {
  Write-Warning "Khong the don thu muc build tam: $BuildRoot"
}
