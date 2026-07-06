param(
  [int]$Port = 0
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if ($Port -le 0) {
  $Port = if ($env:AFSIM_LLM_PORT) { [int]$env:AFSIM_LLM_PORT } else { 8766 }
}

$pidFile = Join-Path $projectRoot ".fastapi_server.pid"
$processIds = New-Object System.Collections.Generic.List[int]

function Add-ProcessId {
  param([int]$TargetProcessId)

  if ($TargetProcessId -gt 0 -and -not $processIds.Contains($TargetProcessId)) {
    $processIds.Add($TargetProcessId)
  }
}

function Test-IsAfsimLlmProcess {
  param([int]$TargetProcessId)

  try {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo) {
      return $false
    }
    $commandLine = [string]$processInfo.CommandLine
    return ($commandLine -like "*uvicorn*" -and $commandLine -like "*app.main:app*")
  } catch {
    return $false
  }
}

function Stop-ProcessTree {
  param([int]$RootProcessId)

  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootProcessId" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    Stop-ProcessTree -RootProcessId ([int]$child.ProcessId)
  }

  $proc = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
  if ($null -ne $proc) {
    Stop-Process -Id $RootProcessId -Force
    Write-Host "Stopped process $RootProcessId."
  }
}

if (Test-Path -LiteralPath $pidFile) {
  $pidText = (Get-Content -Raw -LiteralPath $pidFile).Trim()
  if ($pidText) {
    Add-ProcessId -TargetProcessId ([int]$pidText)
  }
}

try {
  $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($connection in $connections) {
    $ownerProcessId = [int]$connection.OwningProcess
    if (Test-IsAfsimLlmProcess -TargetProcessId $ownerProcessId) {
      Add-ProcessId -TargetProcessId $ownerProcessId
    }
  }
} catch {
}

if ($processIds.Count -eq 0) {
  Write-Host "AFSIM_LLM is not running on port $Port."
  if (Test-Path -LiteralPath $pidFile) {
    Remove-Item -LiteralPath $pidFile -Force
  }
  exit 0
}

foreach ($targetProcessId in $processIds) {
  Stop-ProcessTree -RootProcessId $targetProcessId
}

if (Test-Path -LiteralPath $pidFile) {
  Remove-Item -LiteralPath $pidFile -Force
}

Write-Host "AFSIM_LLM stopped."
