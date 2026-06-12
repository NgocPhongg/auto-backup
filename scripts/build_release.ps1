param(
  [Parameter(Position = 0)]
  [string]$Version = "1.0.0",

  [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
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

function Resolve-FirstExistingPath {
  param(
    [Parameter(Mandatory = $true)][string[]]$Candidates,
    [string]$Label = "path",
    [switch]$Required
  )

  $checked = @()
  foreach ($candidate in $Candidates) {
    if (-not $candidate) {
      continue
    }
    $checked += $candidate
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  if ($Required) {
    $pathsText = $checked -join [Environment]::NewLine
    throw "Missing required $Label. Checked:`n$pathsText"
  }

  return $null
}

function Test-StealthFirefoxRuntime {
  param(
    [string]$RuntimeDir
  )

  if (-not $RuntimeDir) {
    return $false
  }

  foreach ($candidate in @(
    (Join-Path $RuntimeDir "firefox.exe"),
    (Join-Path $RuntimeDir "firefox\firefox.exe")
  )) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return $true
    }
  }

  return $false
}

function Remove-ReleaseNoise {
  param(
    [Parameter(Mandatory = $true)][string]$TargetRoot
  )

  foreach ($relative in @(
    "debug.log",
    "chrome_debug.log",
    "First Run",
    "Crashpad",
    "BrowserMetrics",
    "Default",
    "GrShaderCache",
    "Local Storage",
    "Session Storage",
    "ShaderCache",
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
    "User Data"
  )) {
    $candidate = Join-Path $TargetRoot $relative
    if (Test-Path -LiteralPath $candidate) {
      Remove-Item -LiteralPath $candidate -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}

function Invoke-PythonReleaseStep {
  param(
    [Parameter(Mandatory = $true)][string]$StepName,
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [hashtable]$Environment = @{}
  )

  Write-Host "==> $StepName"
  $previous = @{}

  try {
    foreach ($entry in $Environment.GetEnumerator()) {
      $previous[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key)
      [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value)
    }

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
      throw "Step failed ($StepName) with exit code $LASTEXITCODE."
    }
  } finally {
    foreach ($entry in $Environment.GetEnumerator()) {
      [Environment]::SetEnvironmentVariable($entry.Key, $previous[$entry.Key])
    }
  }
}

function Invoke-PreflightChecks {
  param(
    [Parameter(Mandatory = $true)][string]$WorkspaceRoot
  )

  Invoke-PythonReleaseStep `
    -StepName "Syntax check" `
    -Arguments @(".\codex_skills\autobackup-maintainer\scripts\check_project.py", $WorkspaceRoot)

  $offscreenEnv = @{ QT_QPA_PLATFORM = "offscreen" }

  Invoke-PythonReleaseStep `
    -StepName "Smoke UI (dashboard)" `
    -Arguments @(".\scripts\smoke_ui_quick.py") `
    -Environment $offscreenEnv

  Invoke-PythonReleaseStep `
    -StepName "Smoke UI (full main)" `
    -Arguments @(".\scripts\smoke_ui_quick.py", "--full-main") `
    -Environment $offscreenEnv
}

function Stage-ExternalTools {
  param(
    [Parameter(Mandatory = $true)][string]$AppDir
  )

  $editSource = Resolve-FirstExistingPath `
    -Candidates @((Join-Path $Root "EDIT_1")) `
    -Label "EDIT_1 source" `
    -Required
  $editTarget = Join-Path $AppDir "EDIT_1"

  $creatorSource = Resolve-FirstExistingPath `
    -Candidates @(
      (Join-Path $Root "Creator Now Cut 14112025"),
      (Join-Path $Root "Creator Now Cut")
    ) `
    -Label "Creator Now source" `
    -Required
  $creatorTarget = Join-Path $AppDir "Creator Now Cut 14112025"

  $chromeSource = Resolve-FirstExistingPath `
    -Candidates @((Join-Path $Root "chrome-win64")) `
    -Label "chrome-win64 runtime"
  if ($chromeSource -and -not (Test-Path -LiteralPath (Join-Path $chromeSource "chrome.exe") -PathType Leaf)) {
    Write-Warning "Bo qua chrome-win64 vi khong thay chrome.exe trong runtime portable."
    $chromeSource = $null
  }
  $chromeTarget = Join-Path $AppDir "chrome-win64"

  $stealthSource = Resolve-FirstExistingPath `
    -Candidates @((Join-Path $Root "stealth_firefox")) `
    -Label "stealth_firefox runtime"
  if ($stealthSource -and -not (Test-StealthFirefoxRuntime -RuntimeDir $stealthSource)) {
    Write-Warning "Bo qua stealth_firefox vi khong thay firefox.exe trong runtime."
    $stealthSource = $null
  }
  $stealthTarget = Join-Path $AppDir "stealth_firefox"

  foreach ($target in @($editTarget, $creatorTarget, $chromeTarget, $stealthTarget)) {
    if (Test-Path -LiteralPath $target) {
      Remove-Item -LiteralPath $target -Recurse -Force
    }
  }

  New-Item -ItemType Directory -Force -Path $editTarget | Out-Null
  foreach ($relative in @(
    "main.py",
    "overlay_layout.py",
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

  if ($chromeSource) {
    Copy-ReleaseEntry $chromeSource $chromeTarget
    Remove-ReleaseNoise -TargetRoot $chromeTarget
    Write-Host "Staged portable runtime: chrome-win64"
  } else {
    Write-Warning "Khong tim thay chrome-win64. Local Chrome tren may dich se fallback sang Chrome he thong neu co."
  }

  if ($stealthSource) {
    Copy-ReleaseEntry $stealthSource $stealthTarget
    Remove-ReleaseNoise -TargetRoot $stealthTarget
    Write-Host "Staged optional runtime: stealth_firefox"
  } else {
    Write-Warning "Khong tim thay stealth_firefox runtime. Backend Stealth Firefox se khong hoat dong tren may dich neu khong tu bo sung."
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

if (-not $SkipPreflight) {
  Invoke-PreflightChecks -WorkspaceRoot $Root
}

Write-Host "==> PyInstaller"
python -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath $DistDir `
  --workpath $WorkDir `
  AutoBackup.spec
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$AppDistDir = Join-Path $DistDir "AutoBackup"
if (-not (Test-Path -LiteralPath $AppDistDir -PathType Container)) {
  throw "PyInstaller output not found: $AppDistDir"
}

Stage-ExternalTools -AppDir $AppDistDir

Invoke-PythonReleaseStep `
  -StepName "Release audit" `
  -Arguments @(".\scripts\audit_release_bundle.py", $AppDistDir, "--source-root", $Root)

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

if (-not $compressed) {
  throw "Compress-Archive did not produce a zip file."
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
