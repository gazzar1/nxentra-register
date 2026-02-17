#!/usr/bin/env bash
# =============================================================================
# Backup / Restore Drill Script
# =============================================================================
#
# Purpose: Validate that database backup and restore procedures work correctly.
# Run this before go-live and periodically in staging.
#
# Prerequisites:
#   - PostgreSQL client tools (pg_dump, pg_restore, psql)
#   - DATABASE_URL set to production/staging Postgres
#   - RESTORE_DATABASE_URL set to the restore-target database
#
# Usage:
#   ./scripts/backup-restore-drill.sh              # Full drill
#   ./scripts/backup-restore-drill.sh --backup-only # Just take backup
#   ./scripts/backup-restore-drill.sh --verify-only # Verify existing restore
#
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/nxentra_backup_${TIMESTAMP}.dump"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${GREEN}=== $1 ===${NC}"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight() {
    log_step "Pre-flight checks"

    if ! command -v pg_dump &>/dev/null; then
        log_error "pg_dump not found. Install PostgreSQL client tools."
        exit 1
    fi

    if [ -z "${DATABASE_URL:-}" ]; then
        log_error "DATABASE_URL not set. Export it before running."
        exit 1
    fi

    if [ -z "${RESTORE_DATABASE_URL:-}" ] && [ "${1:-}" != "--backup-only" ]; then
        log_error "RESTORE_DATABASE_URL not set. Export it for restore verification."
        log_info "Example: export RESTORE_DATABASE_URL=postgres://user:pass@localhost:5432/nxentra_restore"
        exit 1
    fi

    mkdir -p "$BACKUP_DIR"
    log_info "Backup directory: $BACKUP_DIR"
    log_info "Pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# Step 1: Take backup
# ---------------------------------------------------------------------------
take_backup() {
    log_step "Step 1: Taking database backup"

    log_info "Dumping to: $BACKUP_FILE"
    pg_dump "$DATABASE_URL" \
        --format=custom \
        --no-owner \
        --no-privileges \
        --verbose \
        --file="$BACKUP_FILE" \
        2>&1 | tail -5

    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log_info "Backup complete: $BACKUP_FILE ($BACKUP_SIZE)"

    # Record metadata
    cat > "$BACKUP_DIR/nxentra_backup_${TIMESTAMP}.meta.json" <<EOF
{
    "timestamp": "$TIMESTAMP",
    "file": "$BACKUP_FILE",
    "size": "$BACKUP_SIZE",
    "source": "$(echo "$DATABASE_URL" | sed 's/:\/\/.*@/:\/\/***@/')",
    "pg_dump_version": "$(pg_dump --version | head -1)"
}
EOF
    log_info "Metadata written."
}

# ---------------------------------------------------------------------------
# Step 2: Restore to target
# ---------------------------------------------------------------------------
restore_backup() {
    log_step "Step 2: Restoring backup to target database"

    log_info "Restoring to: $(echo "$RESTORE_DATABASE_URL" | sed 's/:\/\/.*@/:\/\/***@/')"

    # Drop and recreate target (safe because it's a test DB)
    log_warn "Dropping existing objects in restore target..."
    psql "$RESTORE_DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>/dev/null || true

    pg_restore "$RESTORE_DATABASE_URL" \
        --no-owner \
        --no-privileges \
        --verbose \
        "$BACKUP_FILE" \
        2>&1 | tail -10

    log_info "Restore complete."
}

# ---------------------------------------------------------------------------
# Step 3: Verify restore integrity
# ---------------------------------------------------------------------------
verify_restore() {
    log_step "Step 3: Verifying restore integrity"

    local errors=0

    # 3a: Table count comparison
    log_info "Checking table counts..."
    SOURCE_TABLES=$(psql "$DATABASE_URL" -t -c \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    RESTORE_TABLES=$(psql "$RESTORE_DATABASE_URL" -t -c \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")

    SOURCE_TABLES=$(echo "$SOURCE_TABLES" | xargs)
    RESTORE_TABLES=$(echo "$RESTORE_TABLES" | xargs)

    if [ "$SOURCE_TABLES" = "$RESTORE_TABLES" ]; then
        log_info "Table count matches: $SOURCE_TABLES tables"
    else
        log_error "Table count mismatch: source=$SOURCE_TABLES, restore=$RESTORE_TABLES"
        errors=$((errors + 1))
    fi

    # 3b: Row count on key tables
    log_info "Checking row counts on key tables..."
    for table in accounts_company events_businessevent accounting_journalentry accounting_account; do
        SRC=$(psql "$DATABASE_URL" -t -c "SELECT count(*) FROM $table;" 2>/dev/null | xargs)
        RST=$(psql "$RESTORE_DATABASE_URL" -t -c "SELECT count(*) FROM $table;" 2>/dev/null | xargs)

        if [ "$SRC" = "$RST" ]; then
            log_info "  $table: $SRC rows (match)"
        else
            log_error "  $table: source=$SRC, restore=$RST (MISMATCH)"
            errors=$((errors + 1))
        fi
    done

    # 3c: Event integrity (latest sequence numbers)
    log_info "Checking event stream integrity..."
    SRC_MAX=$(psql "$DATABASE_URL" -t -c \
        "SELECT COALESCE(MAX(id), 0) FROM events_businessevent;" | xargs)
    RST_MAX=$(psql "$RESTORE_DATABASE_URL" -t -c \
        "SELECT COALESCE(MAX(id), 0) FROM events_businessevent;" | xargs)

    if [ "$SRC_MAX" = "$RST_MAX" ]; then
        log_info "  Event max ID matches: $SRC_MAX"
    else
        log_error "  Event max ID mismatch: source=$SRC_MAX, restore=$RST_MAX"
        errors=$((errors + 1))
    fi

    # 3d: Run Django check on restored DB
    log_info "Running Django system checks against restored DB..."
    DATABASE_URL="$RESTORE_DATABASE_URL" \
        python "$PROJECT_ROOT/backend/manage.py" check --deploy 2>&1 | tail -5 || true

    # Summary
    log_step "Verification Summary"
    if [ "$errors" -eq 0 ]; then
        log_info "ALL CHECKS PASSED - Backup/restore drill successful."
    else
        log_error "$errors check(s) FAILED - Investigate before go-live."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Step 4: Cleanup
# ---------------------------------------------------------------------------
cleanup() {
    log_step "Cleanup"

    # Keep last 5 backups
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 5 ]; then
        REMOVE_COUNT=$((BACKUP_COUNT - 5))
        log_info "Removing $REMOVE_COUNT old backup(s)..."
        ls -1t "$BACKUP_DIR"/*.dump | tail -n "$REMOVE_COUNT" | while read -r f; do
            rm -f "$f" "${f%.dump}.meta.json"
            log_info "  Removed: $(basename "$f")"
        done
    fi

    log_info "Drill complete. Backup retained at: $BACKUP_FILE"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo "============================================="
    echo "  Nxentra Backup / Restore Drill"
    echo "  $(date)"
    echo "============================================="

    preflight "${1:-}"

    case "${1:-}" in
        --backup-only)
            take_backup
            ;;
        --verify-only)
            if [ -z "${BACKUP_FILE:-}" ]; then
                BACKUP_FILE=$(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
                if [ -z "$BACKUP_FILE" ]; then
                    log_error "No backup found in $BACKUP_DIR"
                    exit 1
                fi
                log_info "Using latest backup: $BACKUP_FILE"
            fi
            verify_restore
            ;;
        *)
            take_backup
            restore_backup
            verify_restore
            cleanup
            ;;
    esac
}

main "$@"
