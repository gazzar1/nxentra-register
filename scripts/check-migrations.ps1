# =============================================================================
# Migration Health Check (A93) - Windows / PowerShell variant
# =============================================================================
# Mirror of scripts/check-migrations.sh for Windows-native dev. See that file
# for context.
#
# Usage:
#   .\scripts\check-migrations.ps1           # Both checks
#   .\scripts\check-migrations.ps1 -Fast     # Only --check, skip migrate
# =============================================================================
param([switch]$Fast)

# PowerShell 5.1 wraps every native-stderr line in a NativeCommandError under
# "Stop", and Django logs to stderr at DEBUG. Run with Continue and read
# $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Output "==> [1/2] python manage.py makemigrations --check --dry-run"
Push-Location backend
try {
    python manage.py makemigrations --check --dry-run *>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "makemigrations --check failed (exit $LASTEXITCODE)"
        exit 1
    }
} finally {
    Pop-Location
}
Write-Output "    OK: model state matches migration files."

if ($Fast) {
    Write-Output "==> Skipping migrate-from-zero (-Fast). Done."
    exit 0
}

$tmpDb = [System.IO.Path]::GetTempFileName() + ".sqlite3"
try {
    Write-Output "==> [2/2] migrate from zero against $tmpDb"
    $env:DATABASE_URL = "sqlite:///$tmpDb"
    python backend/manage.py migrate --no-input *>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "migrate failed (exit $LASTEXITCODE)"
        exit 1
    }
    Write-Output "    OK: every migration applies cleanly on a fresh DB."
} finally {
    Remove-Item $tmpDb -ErrorAction SilentlyContinue
    Remove-Item ($tmpDb + "-journal") -ErrorAction SilentlyContinue
    $env:DATABASE_URL = $null
}

Write-Output ""
Write-Output "Migration health: GREEN."
