param(
  [Alias("KeepRecent")]
  [int]$KeepRuns = 30,
  [int]$KeepDays = 7,
  [switch]$Apply,
  [string]$ProjectRoot = (Join-Path $PSScriptRoot "..")
)

$ErrorActionPreference = "Stop"

$projectRootPath = (Resolve-Path -LiteralPath $ProjectRoot).Path
$runtimeRoot = Join-Path $projectRootPath "runtime"
$runsRoot = Join-Path $runtimeRoot "afsim_runs"
$workdirsRoot = Join-Path $runtimeRoot "afsim_workdirs"
$replayCacheRoot = Join-Path $runtimeRoot "workbench\replay_cache"
$generatedRoot = Join-Path $projectRootPath "generated_scenarios"
$cutoff = (Get-Date).AddDays(-[Math]::Max(0, $KeepDays))
$planned = New-Object System.Collections.Generic.List[object]

function Convert-ToFullPath {
  param([string]$Path)
  return [System.IO.Path]::GetFullPath($Path)
}

function Test-IsUnderPath {
  param(
    [string]$Path,
    [string]$Parent
  )
  $resolved = Convert-ToFullPath -Path $Path
  $parentResolved = Convert-ToFullPath -Path $Parent
  if (-not $parentResolved.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
    $parentResolved = $parentResolved + [System.IO.Path]::DirectorySeparatorChar
  }
  return $resolved.StartsWith($parentResolved, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-InCleanupRoots {
  param([string]$Path)
  $resolved = Convert-ToFullPath -Path $Path
  $runtimeResolved = Convert-ToFullPath -Path $runtimeRoot
  $generatedResolved = Convert-ToFullPath -Path $generatedRoot
  if ($resolved -eq $runtimeResolved -or $resolved -eq $generatedResolved) {
    throw "Refusing to delete cleanup root itself: $resolved"
  }
  if ((Test-IsUnderPath -Path $resolved -Parent $runtimeRoot) -or (Test-IsUnderPath -Path $resolved -Parent $generatedRoot)) {
    return
  }
  throw "Refusing to delete outside runtime/generated_scenarios: $resolved"
}

function Test-ManualKeep {
  param([System.IO.FileSystemInfo]$Item)
  if (-not $Item -or -not (Test-Path -LiteralPath $Item.FullName -PathType Container)) {
    return $false
  }
  foreach ($marker in @(".keep", "KEEP", "keep", "keep.txt", "retain", ".retain", "_keep")) {
    if (Test-Path -LiteralPath (Join-Path $Item.FullName $marker)) {
      return $true
    }
  }
  $runJson = Join-Path $Item.FullName "run.json"
  if (Test-Path -LiteralPath $runJson) {
    try {
      $payload = Get-Content -Raw -LiteralPath $runJson | ConvertFrom-Json
      if ($payload.keep -eq $true -or $payload.retain -eq $true -or $payload.pinned -eq $true -or $payload.manual_keep -eq $true) {
        return $true
      }
    } catch {
      return $false
    }
  }
  return $false
}

function Add-Plan {
  param(
    [string]$Kind,
    [string]$Path,
    [string]$Reason
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  Assert-InCleanupRoots -Path $Path
  $item = Get-Item -LiteralPath $Path
  $planned.Add([PSCustomObject]@{
    kind = $Kind
    path = $item.FullName
    reason = $Reason
    last_write_time = $item.LastWriteTime.ToString("o")
  }) | Out-Null
}

function Invoke-Plan {
  if ($planned.Count -eq 0) {
    Write-Host "No cleanup candidates. keep_runs=$KeepRuns keep_days=$KeepDays mode=$(if ($Apply) { 'apply' } else { 'dry-run' })"
    return
  }

  Write-Host "Cleanup candidates: $($planned.Count). keep_runs=$KeepRuns keep_days=$KeepDays mode=$(if ($Apply) { 'apply' } else { 'dry-run' })"
  foreach ($entry in $planned) {
    $prefix = if ($Apply) { "[delete]" } else { "[dry-run]" }
    Write-Host "$prefix $($entry.kind) $($entry.path) :: $($entry.reason)"
    if ($Apply) {
      Assert-InCleanupRoots -Path $entry.path
      if (Test-Path -LiteralPath $entry.path -PathType Container) {
        Remove-Item -LiteralPath $entry.path -Recurse -Force
      } elseif (Test-Path -LiteralPath $entry.path -PathType Leaf) {
        Remove-Item -LiteralPath $entry.path -Force
      }
    }
  }
}

Write-Host "AFSIM_LLM runtime cleanup"
Write-Host "project_root=$projectRootPath"
Write-Host "mode=$(if ($Apply) { 'apply' } else { 'dry-run' })"

if (-not (Test-Path -LiteralPath $runtimeRoot) -and -not (Test-Path -LiteralPath $generatedRoot)) {
  Write-Host "Nothing to scan: runtime and generated_scenarios are absent."
  exit 0
}

$runDirs = @()
if (Test-Path -LiteralPath $runsRoot) {
  $runDirs = Get-ChildItem -LiteralPath $runsRoot -Directory | Sort-Object LastWriteTime -Descending
}

$keptRunIds = @{}
$runDirs | Select-Object -First ([Math]::Max(0, $KeepRuns)) | ForEach-Object {
  $keptRunIds[$_.Name] = $true
}

$deletedRunIds = New-Object System.Collections.Generic.List[string]
foreach ($runDir in $runDirs) {
  if (Test-ManualKeep -Item $runDir) {
    $keptRunIds[$runDir.Name] = $true
    Write-Host "[keep] afsim_run $($runDir.FullName) :: manual keep marker"
    continue
  }
  $isRecent = $keptRunIds.ContainsKey($runDir.Name)
  $isYoung = $runDir.LastWriteTime -ge $cutoff
  if ($isRecent -or $isYoung) {
    continue
  }
  Add-Plan -Kind "afsim_run" -Path $runDir.FullName -Reason "older than $KeepDays days and outside newest $KeepRuns runs"
  $deletedRunIds.Add($runDir.Name) | Out-Null
  $workdir = Join-Path $workdirsRoot $runDir.Name
  if (Test-Path -LiteralPath $workdir) {
    Add-Plan -Kind "afsim_workdir" -Path $workdir -Reason "corresponds to removed run $($runDir.Name)"
  }
}

if (Test-Path -LiteralPath $workdirsRoot) {
  Get-ChildItem -LiteralPath $workdirsRoot -Directory | ForEach-Object {
    if ($keptRunIds.ContainsKey($_.Name)) {
      return
    }
    if (Test-ManualKeep -Item $_) {
      Write-Host "[keep] afsim_workdir $($_.FullName) :: manual keep marker"
      return
    }
    if ($_.LastWriteTime -ge $cutoff) {
      return
    }
    Add-Plan -Kind "afsim_workdir" -Path $_.FullName -Reason "orphaned or old workdir"
  }
}

if (Test-Path -LiteralPath $replayCacheRoot) {
  $deletedSet = @{}
  $deletedRunIds | ForEach-Object { $deletedSet[$_] = $true }
  Get-ChildItem -LiteralPath $replayCacheRoot -File -Filter "*.json" | ForEach-Object {
    $old = $_.LastWriteTime -lt $cutoff
    $linkedDeletedRun = $false
    foreach ($runId in $deletedSet.Keys) {
      if ($_.BaseName.StartsWith($runId, [System.StringComparison]::OrdinalIgnoreCase)) {
        $linkedDeletedRun = $true
        break
      }
    }
    if ($old -or $linkedDeletedRun) {
      Add-Plan -Kind "replay_cache" -Path $_.FullName -Reason "old cache or linked to removed run"
    }
  }
}

if (Test-Path -LiteralPath $generatedRoot) {
  Get-ChildItem -LiteralPath $generatedRoot -Directory | ForEach-Object {
    $name = $_.Name.ToLowerInvariant()
    $isTestScenario = $name.Contains("pytest") -or $name.Contains("smoke")
    if (-not $isTestScenario) {
      return
    }
    if (Test-ManualKeep -Item $_) {
      Write-Host "[keep] generated_scenario $($_.FullName) :: manual keep marker"
      return
    }
    if ($_.LastWriteTime -ge $cutoff) {
      return
    }
    Add-Plan -Kind "generated_scenario" -Path $_.FullName -Reason "old pytest/smoke generated scenario"
  }
}

Invoke-Plan
Write-Host "cleanup complete. planned=$($planned.Count) applied=$(if ($Apply) { 'true' } else { 'false' })"
