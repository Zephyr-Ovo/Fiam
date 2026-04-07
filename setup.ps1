# fiam — per-user environment setup
# Run this once per Windows user account (e.g. Aurora, Iris).
# .venv is gitignored and user-specific — each user needs their own.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  fiam — environment setup" -ForegroundColor Cyan
Write-Host ""

# Check uv is available
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  Error: uv not found. Install from https://docs.astral.sh/uv/" -ForegroundColor Red
    exit 1
}

# Recreate .venv for current user
Write-Host "  Creating .venv for $env:USERNAME ..."
uv venv --python 3.11 --clear .venv
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Error: uv venv failed" -ForegroundColor Red
    exit 1
}

# Install dependencies
Write-Host "  Installing dependencies ..."
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Error: uv sync failed" -ForegroundColor Red
    exit 1
}

# Set HF_HOME for this session
$env:HF_HOME = Join-Path (Get-Location) ".cache\huggingface"

Write-Host ""
Write-Host "  Done. .venv ready for $env:USERNAME" -ForegroundColor Green
Write-Host "  Next: uv run python scripts/fiam.py init" -ForegroundColor DarkGray
Write-Host ""
