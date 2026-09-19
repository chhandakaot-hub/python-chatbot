# Starts the app with the project's venv, whether or not it is activated.
$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "venv not found at $python - create it with: python -m venv venv"
    exit 1
}

& $python -m uvicorn app.main:app --reload --port 8000
