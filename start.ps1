# start.ps1
# Run this after cloning. It sets up the project (venv, deps, DB) if needed, then starts the server.
# If nothing happens: open PowerShell, cd to this folder, run: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# Then run: .\start.ps1

Set-Location $PSScriptRoot
Write-Host "Starting..." -ForegroundColor Cyan

$venvPath = ".\.venv"
$activateScript = "$venvPath\Scripts\Activate.ps1"

if (-not (Test-Path $activateScript)) {
    Write-Host "First run: creating virtual environment and installing dependencies..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Could not create .venv. Make sure Python 3 is installed and on your PATH." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    & $activateScript
    pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: pip install failed." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Running migrations..."
    python manage.py migrate
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: migrate failed." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "Setup complete. Starting server..." -ForegroundColor Green
} else {
    & $activateScript
}

Write-Host "Ensuring PDF/OCR deps (pymupdf, easyocr)... first time may take a minute." -ForegroundColor Gray
pip install pymupdf easyocr numpy --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "Warning: PDF/OCR install failed; scanned PDF upload may not work." -ForegroundColor Yellow }

Write-Host "Starting Django server..." -ForegroundColor Green
python manage.py runserver
if ($LASTEXITCODE -ne 0) {
    Write-Host "Server exited. Press Enter to close." -ForegroundColor Yellow
    Read-Host
}
