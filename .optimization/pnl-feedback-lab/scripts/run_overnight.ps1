# PnL Feedback Lab — overnight batch (Windows)
#
# Default: opens a NEW console window so closing Cursor/IDE does not stop the job.
# Uses SetThreadExecutionState via Python (prevents system idle sleep while the process runs).
# Does not stop manual Sleep, lid-close suspend, or forced shutdown.
#
# Usage (from anywhere):
#   .\run_overnight.ps1              # ~10h, 360 runs (--overnight)
#   .\run_overnight.ps1 -Mega        # ~10h, 600 runs (--overnight-mega), largest dataset
#   .\run_overnight.ps1 -Attach      # run in this window instead of Start-Process
#
# Optional: Settings > System > Power > Screen > turn off "never" if you need the display on all night.

param(
    [switch]$Mega,
    [switch]$Attach
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$batch = Join-Path $repo ".optimization\pnl-feedback-lab\scripts\run_pnl_lab_batch.py"
if (-not (Test-Path $batch)) { throw "Missing $batch" }

$argList = @($batch)
if ($Mega) {
    $argList += "--overnight-mega"
} else {
    $argList += "--overnight"
}

if ($Attach) {
    Set-Location $repo
    $log = Join-Path $repo ".optimization\pnl-feedback-lab\runs\overnight_console_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
    & python $argList 2>&1 | Tee-Object -FilePath $log
} else {
    Start-Process -FilePath "python" -ArgumentList $argList -WorkingDirectory $repo -WindowStyle Normal
    Write-Host "Started overnight lab in a new window (python $($argList -join ' '))."
}
