#!/usr/bin/env bash
# =============================================================================
# Security Hardening Check
# =============================================================================
#
# Runs a pre-release security checklist:
#   1. Secrets scan (environment files, hardcoded credentials)
#   2. Dependency vulnerability scan (pip-audit / npm audit)
#   3. Django deployment checklist (manage.py check --deploy)
#   4. Authorization spot-check (permission decorators on views)
#   5. CORS / CSRF configuration review
#
# Usage:
#   ./scripts/security-check.sh
#
# Exit code 0 = all clear, 1 = issues found
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND="$PROJECT_ROOT/backend"
FRONTEND="$PROJECT_ROOT/frontend"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ISSUES=0

log_info()  { echo -e "${GREEN}[PASS]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; ISSUES=$((ISSUES + 1)); }
log_error() { echo -e "${RED}[FAIL]${NC}  $1"; ISSUES=$((ISSUES + 1)); }
log_step()  { echo -e "\n${GREEN}=== $1 ===${NC}"; }

# ---------------------------------------------------------------------------
# 1. Secrets scan
# ---------------------------------------------------------------------------
secrets_scan() {
    log_step "1. Secrets Scan"

    # Check for .env files committed
    if git -C "$PROJECT_ROOT" ls-files --error-unmatch .env .env.local .env.production 2>/dev/null; then
        log_error ".env file(s) are tracked by git! Remove them immediately."
    else
        log_info "No .env files tracked by git."
    fi

    # Check .gitignore includes .env
    if grep -q "\.env" "$PROJECT_ROOT/.gitignore" 2>/dev/null; then
        log_info ".gitignore includes .env patterns."
    else
        log_warn ".gitignore may not exclude .env files. Verify manually."
    fi

    # Scan for hardcoded secrets patterns
    SECRETS_PATTERN='(SECRET_KEY|PASSWORD|API_KEY|PRIVATE_KEY|ACCESS_TOKEN)\s*=\s*["\x27][^"\x27]{8,}'
    FOUND=$(grep -rn --include="*.py" --include="*.ts" --include="*.tsx" --include="*.js" \
        -E "$SECRETS_PATTERN" "$BACKEND" "$FRONTEND" 2>/dev/null \
        | grep -v "test" | grep -v "conftest" | grep -v "node_modules" \
        | grep -v "\.example" | grep -v "os\.environ" | grep -v "getenv" \
        | head -10 || true)

    if [ -n "$FOUND" ]; then
        log_warn "Potential hardcoded secrets found:"
        echo "$FOUND" | while IFS= read -r line; do
            echo "    $line"
        done
    else
        log_info "No hardcoded secrets detected."
    fi

    # Check for credentials in settings.py
    if grep -n "SECRET_KEY.*=.*['\"]" "$BACKEND/nxentra_backend/settings.py" 2>/dev/null \
        | grep -v "os.environ\|getenv\|config(" >/dev/null 2>&1; then
        log_error "SECRET_KEY appears hardcoded in settings.py!"
    else
        log_info "SECRET_KEY is loaded from environment."
    fi
}

# ---------------------------------------------------------------------------
# 2. Dependency vulnerability scan
# ---------------------------------------------------------------------------
dependency_scan() {
    log_step "2. Dependency Vulnerability Scan"

    # Python dependencies
    if command -v pip-audit &>/dev/null; then
        log_info "Running pip-audit..."
        if pip-audit -r "$BACKEND/requirements.txt" --strict 2>&1 | tail -5; then
            log_info "pip-audit: No known vulnerabilities."
        else
            log_warn "pip-audit found vulnerabilities. Review output above."
        fi
    else
        log_warn "pip-audit not installed. Run: pip install pip-audit"
    fi

    # Node dependencies
    if [ -f "$FRONTEND/package-lock.json" ] || [ -f "$FRONTEND/yarn.lock" ]; then
        log_info "Running npm audit..."
        (cd "$FRONTEND" && npm audit --production 2>&1 | tail -10) || \
            log_warn "npm audit found vulnerabilities. Run: cd frontend && npm audit"
    else
        log_warn "No lock file found in frontend/. Cannot run npm audit."
    fi
}

# ---------------------------------------------------------------------------
# 3. Django deployment checks
# ---------------------------------------------------------------------------
django_deploy_check() {
    log_step "3. Django Deployment Checks"

    if (cd "$BACKEND" && python manage.py check --deploy 2>&1); then
        log_info "Django deployment checks passed."
    else
        log_warn "Django deployment checks reported issues. Review above."
    fi
}

# ---------------------------------------------------------------------------
# 4. Authorization spot-check
# ---------------------------------------------------------------------------
authz_spot_check() {
    log_step "4. Authorization Spot-Check"

    # Check that views use authentication
    UNPROTECTED=$(grep -rn "class.*View\|class.*ViewSet" \
        "$BACKEND/accounting/views.py" \
        "$BACKEND/projections/views.py" \
        "$BACKEND/sales/views.py" \
        2>/dev/null | head -20 || true)

    VIEW_COUNT=$(echo "$UNPROTECTED" | grep -c "class" || true)

    # Check for authentication classes
    HAS_AUTH=$(grep -c "authentication_classes\|permission_classes\|IsAuthenticated\|get_actor" \
        "$BACKEND/accounting/views.py" \
        "$BACKEND/projections/views.py" \
        "$BACKEND/sales/views.py" \
        2>/dev/null || echo "0")

    if [ "$HAS_AUTH" -gt 0 ]; then
        log_info "Found $HAS_AUTH authentication/permission references across view files."
    else
        log_warn "No authentication decorators/classes found in views. Manual review needed."
    fi

    # Check that commands use actor-based authorization
    ACTOR_CHECKS=$(grep -c "require_permission\|actor\.perms\|check_permission" \
        "$BACKEND/accounting/commands.py" 2>/dev/null || echo "0")

    if [ "$ACTOR_CHECKS" -gt 0 ]; then
        log_info "Found $ACTOR_CHECKS permission checks in commands.py."
    else
        log_warn "No permission checks found in commands.py. Review authorization."
    fi
}

# ---------------------------------------------------------------------------
# 5. CORS / CSRF configuration
# ---------------------------------------------------------------------------
cors_csrf_check() {
    log_step "5. CORS / CSRF Configuration"

    # Check CORS settings
    if grep -q "CORS_ALLOW_ALL_ORIGINS.*True" "$BACKEND/nxentra_backend/settings.py" 2>/dev/null; then
        log_warn "CORS_ALLOW_ALL_ORIGINS is True. Restrict in production."
    else
        log_info "CORS is not wide-open (CORS_ALLOW_ALL_ORIGINS not True)."
    fi

    # Check DEBUG setting
    if grep -q 'DEBUG.*=.*True' "$BACKEND/nxentra_backend/settings.py" 2>/dev/null \
        | grep -v "os.environ\|getenv\|config(" >/dev/null 2>&1; then
        log_warn "DEBUG may be hardcoded to True. Ensure it's env-driven."
    else
        log_info "DEBUG is environment-driven."
    fi

    # Check ALLOWED_HOSTS
    if grep -q "ALLOWED_HOSTS.*\[\s*\"\*\"\s*\]" "$BACKEND/nxentra_backend/settings.py" 2>/dev/null; then
        log_warn "ALLOWED_HOSTS includes '*'. Restrict in production."
    else
        log_info "ALLOWED_HOSTS is not wide-open."
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
summary() {
    log_step "Security Check Summary"

    if [ "$ISSUES" -eq 0 ]; then
        echo -e "${GREEN}All checks passed. No issues found.${NC}"
    else
        echo -e "${YELLOW}$ISSUES issue(s) found. Review warnings above.${NC}"
        echo ""
        echo "Recommendations:"
        echo "  - Fix any FAIL items before release"
        echo "  - Review WARN items and address if applicable"
        echo "  - Run this script again after fixes"
    fi

    exit $(( ISSUES > 0 ? 1 : 0 ))
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "============================================="
echo "  Nxentra Security Hardening Check"
echo "  $(date)"
echo "============================================="

secrets_scan
dependency_scan
django_deploy_check
authz_spot_check
cors_csrf_check
summary
