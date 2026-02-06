# start.ps1
# Run this after cloning. It sets up the project (venv, deps, DB) if needed, then starts the server.

$venvPath = ".\.venv"
$activateScript = "$venvPath\Scripts\Activate.ps1"

if (-not (Test-Path $activateScript)) {
    Write-Host "First run: creating virtual environment and installing dependencies..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: Could not create .venv. Make sure Python 3 is installed and on your PATH." -ForegroundColor Red
        exit 1
    }
    & $activateScript
    pip install -r requirements.txt --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: pip install failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "Running migrations..."
    python manage.py migrate
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: migrate failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "Setup complete. Starting server..." -ForegroundColor Green
} else {
    & $activateScript
}

python manage.py runserver
