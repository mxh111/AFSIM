$ErrorActionPreference = "Stop"

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath ".venv")) {
  python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

$hostName = if ($env:AFSIM_LLM_HOST) { $env:AFSIM_LLM_HOST } else { "127.0.0.1" }
$port = if ($env:AFSIM_LLM_PORT) { $env:AFSIM_LLM_PORT } else { "8000" }

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host $hostName --port $port --reload
