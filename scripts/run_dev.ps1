param(
  [switch]$SkipInstall,
  [switch]$Reload
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$hostName = if ($env:AFSIM_LLM_HOST) { $env:AFSIM_LLM_HOST } else { "127.0.0.1" }
$port = if ($env:AFSIM_LLM_PORT) { [int]$env:AFSIM_LLM_PORT } else { 8766 }
$healthHost = if ($hostName -eq "0.0.0.0") { "127.0.0.1" } else { $hostName }
$healthUrl = "http://$healthHost`:$port/api/health"
$appUrl = "http://$healthHost`:$port"

$pidFile = Join-Path $projectRoot ".fastapi_server.pid"
$runtimeDir = Join-Path $projectRoot "runtime"
$stdoutLog = Join-Path $runtimeDir "uvicorn.log"
$stderrLog = Join-Path $runtimeDir "uvicorn.err.log"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Get-ListeningProcessId {
  param([int]$LocalPort)

  try {
    $connection = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($connection) {
      return [int]$connection.OwningProcess
    }
  } catch {
    return $null
  }
  return $null
}

$existingProcessId = Get-ListeningProcessId -LocalPort $port
if ($existingProcessId) {
  Set-Content -LiteralPath $pidFile -Value $existingProcessId
  Write-Host "AFSIM_LLM is already running."
  Write-Host "PID: $existingProcessId"
  Write-Host "URL: $appUrl"
  exit 0
}

if (-not (Test-Path -LiteralPath ".venv")) {
  Write-Host "Creating virtual environment..."
  python -m venv .venv
}

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
  throw "Python executable not found: $pythonExe"
}

if (-not $SkipInstall) {
  $env:NO_PROXY = "*"
  Write-Host "Installing Python dependencies..."
  & $pythonExe -m pip install --upgrade pip
  & $pythonExe -m pip install -r requirements.txt --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
}

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $hostName, "--port", [string]$port)
if ($Reload) {
  $uvicornArgs += "--reload"
}

if (Test-Path -LiteralPath $stdoutLog) { Remove-Item -LiteralPath $stdoutLog -Force }
if (Test-Path -LiteralPath $stderrLog) { Remove-Item -LiteralPath $stderrLog -Force }

$process = Start-Process `
  -FilePath $pythonExe `
  -ArgumentList $uvicornArgs `
  -WorkingDirectory $projectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

Set-Content -LiteralPath $pidFile -Value $process.Id

Write-Host "Starting AFSIM_LLM..."
Write-Host "PID: $($process.Id)"
Write-Host "URL: $appUrl"

for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  if ($process.HasExited) {
    Write-Host "AFSIM_LLM failed to start."
    if (Test-Path -LiteralPath $stderrLog) {
      Get-Content -Tail 40 -LiteralPath $stderrLog
    }
    exit 1
  }

  try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
      Write-Host "AFSIM_LLM is ready."
      exit 0
    }
  } catch {
  }
}

Write-Host "AFSIM_LLM was started, but health check did not respond in time."
Write-Host "Logs:"
Write-Host "  $stdoutLog"
Write-Host "  $stderrLog"
