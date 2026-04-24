$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "frontend-new"

Write-Host "[safe-start] Building frontend-new into frontend/ for dashboard serving..."
Push-Location $frontendDir
try {
  npm run build:legacy
} finally {
  Pop-Location
}

$env:ARCEUS_SAFE_STARTUP = "1"
$env:ARCEUS_FORCE_MODE = "paper"
$env:ARCEUS_SCALP_FORCE_SIM = "1"
$env:ARCEUS_SERVER_PORT = "8081"

Write-Host "[safe-start] SAFE STARTUP active:"
Write-Host "  - forcing paper mode"
Write-Host "  - disabling live API keys for this session"
Write-Host "  - enabling scalp sim mode when scalp is enabled"
Write-Host "  - engine still starts stopped until START is pressed"
Write-Host "  - dashboard served on http://localhost:8081"
Write-Host ""

Push-Location $repoRoot
try {
  python -m backend.server.main
} finally {
  Pop-Location
}
