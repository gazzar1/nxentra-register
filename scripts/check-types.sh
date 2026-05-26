#!/usr/bin/env bash
# Mypy spine type-check gate (A97). Delegates to the Python wrapper so the
# spine file list is defined in one place.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python scripts/check-types.py
