#!/usr/bin/env bash
# =============================================================================
# Migration Health Check (A93)
# =============================================================================
#
# Two-part gate that defends migration sanity:
#   1. `makemigrations --check --dry-run` — model state matches migration files.
#   2. `migrate` against a throw-away SQLite DB — every migration applies
#      cleanly from zero (catches duplicate-column, missing-dependency,
#      bad-RunPython style bugs that --check won't surface).
#
# Background: a 2026-05-26 architectural review surfaced a reported
# `duplicate column name: warehouse_id` failure during SQLite test-DB
# migration. The bug was not reproducible at the time of A93, but the gate
# is what stops the next instance of it ever shipping.
#
# Devs should run this before pushing schema work. The fast half
# (`makemigrations --check`) also runs as a pre-push pre-commit hook
# automatically. The slow half (migrate-from-zero, ~40s) is manual to
# preserve a fast inner loop.
#
# Usage:
#   ./scripts/check-migrations.sh           # Both checks
#   ./scripts/check-migrations.sh --fast    # Only --check, skip migrate
#
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

FAST=""
if [[ "${1:-}" == "--fast" ]]; then
    FAST="1"
fi

echo "==> [1/2] python manage.py makemigrations --check --dry-run"
(cd backend && python manage.py makemigrations --check --dry-run)
echo "    OK: model state matches migration files."

if [[ -n "$FAST" ]]; then
    echo "==> Skipping migrate-from-zero (--fast). Done."
    exit 0
fi

# Use a throw-away SQLite file so we don't trash the dev DB.
TMP_DB="$(mktemp -u --suffix=.sqlite3 2>/dev/null || mktemp -t migration_check_XXXXXX.sqlite3)"
trap 'rm -f "$TMP_DB"' EXIT

echo "==> [2/2] migrate from zero against $TMP_DB"
DATABASE_URL="sqlite:///${TMP_DB}" python backend/manage.py migrate --no-input >/dev/null
echo "    OK: every migration applies cleanly on a fresh DB."

echo
echo "Migration health: GREEN."
