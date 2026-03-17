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

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1).IPAddress
if (-not $ip) { $ip = "YOUR_PC_IP" }

Write-Host "Starting Django server (localhost + same Wi-Fi)..." -ForegroundColor Green
Write-Host "  On this PC:  http://127.0.0.1:8000" -ForegroundColor Gray
Write-Host "  On phone:    http://${ip}:8000  (same Wi-Fi)" -ForegroundColor Gray
Write-Host ""
python manage.py runserver 0.0.0.0:8000
if ($LASTEXITCODE -ne 0) {
    Write-Host "Server exited. Press Enter to close." -ForegroundColor Yellow
    Read-Host
}
