# Copy .env.prod from this PC to the Ubuntu VM (VirtualBox SSH port-forward 2222).
# Usage: right-click Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File .\scripts\sync-env-to-vm.ps1

$ErrorActionPreference = "Stop"

# Script lives in repo/scripts/ — project root is one level up
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$LocalEnv = Join-Path $ProjectRoot ".env.prod"
$RemoteUser = "reeche"
$RemoteHost = "127.0.0.1"
$RemotePort = 2222
$RemotePath = "/home/reeche/Thesis_for_Deployment/.env.prod"

if (-not (Test-Path $LocalEnv)) {
    Write-Error "Missing file: $LocalEnv"
    exit 1
}

Write-Host "Copying:" -ForegroundColor Cyan
Write-Host "  $LocalEnv"
Write-Host "  -> ${RemoteUser}@${RemoteHost}:${RemotePath}"
Write-Host ""

& scp -P $RemotePort $LocalEnv "${RemoteUser}@${RemoteHost}:${RemotePath}"

if ($LASTEXITCODE -ne 0) {
    Write-Error "scp failed (exit $LASTEXITCODE). Check: VM running, SSH port $RemotePort, path on VM exists."
    exit $LASTEXITCODE
}

Write-Host "Done. On the VM run:" -ForegroundColor Green
Write-Host "  cd ~/Thesis_for_Deployment && sudo docker compose -f docker-compose.prod.yml up -d --build"
