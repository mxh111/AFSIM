param(
  [int]$KeepRecent = 30,
  [int]$KeepDays = 7,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $projectRoot "runtime"
$runsRoot = Join-Path $runtimeRoot "afsim_runs"
$workdirsRoot = Join-Path $runtimeRoot "afsim_workdirs"
$replayCacheRoot = Join-Path $runtimeRoot "workbench\replay_cache"
$cutoff = (Get-Date).AddDays(-[Math]::Max(0, $KeepDays))

function Assert-InRuntime {
  param([string]$Path)
  $resolved = [System.IO.Path]::GetFullPath($Path)
  $runtimeResolved = [System.IO.Path]::GetFullPath($runtimeRoot)
  if (-not $resolved.StartsWith($runtimeResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to delete outside runtime: $resolved"
  }
}

function Test-ManualKeep {
  param([System.IO.DirectoryInfo]$RunDir)
  foreach ($marker in @(".keep", "KEEP", "keep", "retain", ".retain")) {
    if (Test-Path -LiteralPath (Join-Path $RunDir.FullName $marker)) {
      return $true
    }
  }
  $runJson = Join-Path $RunDir.FullName "run.json"
  if (Test-Path -LiteralPath $runJson) {
    try {
      $payload = Get-Content -Raw -LiteralPath $runJson | ConvertFrom-Json
      if ($payload.keep -eq $true -or $payload.retain -eq $true -or $payload.pinned -eq $true) {
        return $true
      }
    } catch {
      return $false
    }
  }
  return $false
}

function Remove-DirectorySafe {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  Assert-InRuntime -Path $Path
  if ($DryRun) {
    Write-Host "[dry-run] remove $Path"
  } else {
    Remove-Item -LiteralPath $Path -Recurse -Force
    Write-Host "removed $Path"
  }
}

function Remove-FileSafe {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  Assert-InRuntime -Path $Path
  if ($DryRun) {
    Write-Host "[dry-run] remove $Path"
  } else {
    Remove-Item -LiteralPath $Path -Force
    Write-Host "removed $Path"
  }
}

if (-not (Test-Path -LiteralPath $runtimeRoot)) {
  Write-Host "runtime does not exist: $runtimeRoot"
  exit 0
}

$runDirs = @()
if (Test-Path -LiteralPath $runsRoot) {
  $runDirs = Get-ChildItem -LiteralPath $runsRoot -Directory |
    Sort-Object LastWriteTime -Descending
}

$recentIds = @{}
$runDirs | Select-Object -First ([Math]::Max(0, $KeepRecent)) | ForEach-Object {
  $recentIds[$_.Name] = $true
}

$deletedIds = New-Object System.Collections.Generic.List[string]
foreach ($runDir in $runDirs) {
  if (Test-ManualKeep -RunDir $runDir) {
    Write-Host "keep marked run $($runDir.Name)"
    continue
  }
  $isRecent = $recentIds.ContainsKey($runDir.Name)
  $isYoung = $runDir.LastWriteTime -ge $cutoff
  if ($isRecent -or $isYoung) {
    continue
  }
  Remove-DirectorySafe -Path $runDir.FullName
  $deletedIds.Add($runDir.Name) | Out-Null
  Remove-DirectorySafe -Path (Join-Path $workdirsRoot $runDir.Name)
}

if (Test-Path -LiteralPath $workdirsRoot) {
  $knownRunIds = @{}
  if (Test-Path -LiteralPath $runsRoot) {
    Get-ChildItem -LiteralPath $runsRoot -Directory | ForEach-Object { $knownRunIds[$_.Name] = $true }
  }
  Get-ChildItem -LiteralPath $workdirsRoot -Directory | ForEach-Object {
    if ($knownRunIds.ContainsKey($_.Name)) {
      return
    }
    if ($_.LastWriteTime -ge $cutoff) {
      return
    }
    Remove-DirectorySafe -Path $_.FullName
  }
}

if (Test-Path -LiteralPath $replayCacheRoot) {
  $deletedSet = @{}
  $deletedIds | ForEach-Object { $deletedSet[$_] = $true }
  Get-ChildItem -LiteralPath $replayCacheRoot -File -Filter "*.json" | ForEach-Object {
    $stem = $_.BaseName
    $staleByRun = $deletedSet.ContainsKey($stem)
    $old = $_.LastWriteTime -lt $cutoff
    if ($staleByRun -or $old) {
      Remove-FileSafe -Path $_.FullName
    }
  }
}

Write-Host "cleanup complete. keep_recent=$KeepRecent keep_days=$KeepDays deleted_runs=$($deletedIds.Count)"
