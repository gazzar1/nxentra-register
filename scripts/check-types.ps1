# Mypy spine type-check gate (A97) - PowerShell wrapper.
# Delegates to scripts/check-types.py so the spine file list lives in one place.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
python scripts/check-types.py
exit $LASTEXITCODE
