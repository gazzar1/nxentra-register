#!/usr/bin/env bash
# =============================================================================
# Release Candidate Smoke Test
# =============================================================================
#
# Validates that a deployed instance is minimally functional:
#   1. Health endpoints respond
#   2. Authentication works (login flow)
#   3. Core API endpoints return valid responses
#   4. Frontend serves pages
#   5. Management commands (local only)
#
# Prerequisites:
#   - Backend running at BASE_URL (default: http://localhost:8000)
#   - Frontend running at FRONTEND_URL (default: http://localhost:3000)
#   - Test user credentials (TEST_EMAIL / TEST_PASSWORD)
#
# Usage:
#   ./scripts/rc-smoke-test.sh
#   BASE_URL=https://staging.nxentra.com ./scripts/rc-smoke-test.sh
#
# =============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
TEST_EMAIL="${TEST_EMAIL:-owner@test.com}"
TEST_PASSWORD="${TEST_PASSWORD:-testpass123}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
TOKEN=""

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASSED=$((PASSED + 1)); }
fail() { echo -e "${RED}[FAIL]${NC} $1"; FAILED=$((FAILED + 1)); }
skip() { echo -e "${YELLOW}[SKIP]${NC} $1"; SKIPPED=$((SKIPPED + 1)); }
step() { echo -e "\n${GREEN}--- $1 ---${NC}"; }

# Helper: HTTP GET, returns status code
http_get() {
    local url="$1"
    local headers="${2:-}"
    if [ -n "$headers" ]; then
        curl -s -o /dev/null -w "%{http_code}" -H "$headers" "$url" 2>/dev/null || echo "000"
    else
        curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000"
    fi
}

# Helper: HTTP GET, returns body
http_get_body() {
    local url="$1"
    local headers="${2:-}"
    if [ -n "$headers" ]; then
        curl -s -H "$headers" "$url" 2>/dev/null || echo "{}"
    else
        curl -s "$url" 2>/dev/null || echo "{}"
    fi
}

# ---------------------------------------------------------------------------
# 1. Health Endpoints
# ---------------------------------------------------------------------------
test_health() {
    step "1. Health Endpoints"

    # Liveness
    STATUS=$(http_get "$BASE_URL/_health/live")
    if [ "$STATUS" = "200" ]; then
        pass "Liveness probe: 200"
    else
        fail "Liveness probe: expected 200, got $STATUS"
    fi

    # Readiness
    STATUS=$(http_get "$BASE_URL/_health/ready")
    if [ "$STATUS" = "200" ]; then
        pass "Readiness probe: 200"
    else
        fail "Readiness probe: expected 200, got $STATUS"
    fi

    # Full health
    BODY=$(http_get_body "$BASE_URL/_health/full")
    OVERALL=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
    if [ "$OVERALL" = "healthy" ]; then
        pass "Full health: healthy"
    elif [ -n "$OVERALL" ]; then
        fail "Full health: $OVERALL (expected healthy)"
    else
        fail "Full health: no response or parse error"
    fi
}

# ---------------------------------------------------------------------------
# 2. Authentication
# ---------------------------------------------------------------------------
test_auth() {
    step "2. Authentication"

    RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/login/" \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"$TEST_EMAIL\", \"password\": \"$TEST_PASSWORD\"}" \
        2>/dev/null || echo "{}")

    TOKEN=$(echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('access', data.get('token', data.get('access_token', ''))))
" 2>/dev/null || echo "")

    if [ -n "$TOKEN" ] && [ "$TOKEN" != "None" ]; then
        pass "Login: obtained JWT token"
    else
        fail "Login: could not obtain token (check TEST_EMAIL/TEST_PASSWORD)"
        skip "Remaining authenticated tests skipped (no token)"
        return
    fi
}

# ---------------------------------------------------------------------------
# 3. Core API Endpoints
# ---------------------------------------------------------------------------
test_api() {
    step "3. Core API Endpoints"

    if [ -z "$TOKEN" ] || [ "$TOKEN" = "None" ]; then
        skip "API tests skipped (no auth token)"
        return
    fi

    AUTH="Authorization: Bearer $TOKEN"

    # Chart of accounts
    STATUS=$(http_get "$BASE_URL/api/accounting/accounts/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        pass "GET /api/accounting/accounts/: $STATUS"
    else
        fail "GET /api/accounting/accounts/: expected 200, got $STATUS"
    fi

    # Journal entries
    STATUS=$(http_get "$BASE_URL/api/accounting/journal-entries/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        pass "GET /api/accounting/journal-entries/: $STATUS"
    else
        fail "GET /api/accounting/journal-entries/: expected 200, got $STATUS"
    fi

    # Fiscal periods
    STATUS=$(http_get "$BASE_URL/api/reports/periods/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        pass "GET /api/reports/periods/: $STATUS"
    else
        fail "GET /api/reports/periods/: expected 200, got $STATUS"
    fi

    # Reconciliation
    STATUS=$(http_get "$BASE_URL/api/reports/reconciliation/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        BODY=$(http_get_body "$BASE_URL/api/reports/reconciliation/" "$AUTH")
        BALANCED=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('balanced',''))" 2>/dev/null || echo "")
        if [ "$BALANCED" = "True" ]; then
            pass "Reconciliation: balanced"
        else
            fail "Reconciliation: imbalance detected"
        fi
    else
        fail "GET /api/reports/reconciliation/: expected 200, got $STATUS"
    fi

    # Trial balance
    STATUS=$(http_get "$BASE_URL/api/reports/trial-balance/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        pass "GET /api/reports/trial-balance/: $STATUS"
    else
        fail "GET /api/reports/trial-balance/: expected 200, got $STATUS"
    fi

    # Dashboard charts
    STATUS=$(http_get "$BASE_URL/api/reports/dashboard-charts/" "$AUTH")
    if [ "$STATUS" = "200" ]; then
        pass "GET /api/reports/dashboard-charts/: $STATUS"
    else
        fail "GET /api/reports/dashboard-charts/: expected 200, got $STATUS"
    fi
}

# ---------------------------------------------------------------------------
# 4. Frontend
# ---------------------------------------------------------------------------
test_frontend() {
    step "4. Frontend"

    STATUS=$(http_get "$FRONTEND_URL")
    if [ "$STATUS" = "200" ]; then
        pass "Frontend root: $STATUS"
    elif [ "$STATUS" = "302" ] || [ "$STATUS" = "301" ]; then
        pass "Frontend root: $STATUS (redirect, expected for auth)"
    else
        fail "Frontend root: expected 200/30x, got $STATUS"
    fi

    # Login page
    STATUS=$(http_get "$FRONTEND_URL/auth/login")
    if [ "$STATUS" = "200" ]; then
        pass "Frontend login page: $STATUS"
    else
        fail "Frontend login page: expected 200, got $STATUS"
    fi
}

# ---------------------------------------------------------------------------
# 5. Django management checks
# ---------------------------------------------------------------------------
test_management() {
    step "5. Management Commands"

    # Run reconciliation check via management command (if backend is local)
    if [ "$BASE_URL" = "http://localhost:8000" ]; then
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

        if (cd "$PROJECT_ROOT/backend" && python manage.py reconciliation_check --json --strict 2>/dev/null); then
            pass "reconciliation_check management command"
        else
            fail "reconciliation_check management command reported issues"
        fi
    else
        skip "Management commands skipped (remote deployment)"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
summary() {
    echo ""
    echo "============================================="
    echo "  Smoke Test Summary"
    echo "============================================="
    echo -e "  ${GREEN}Passed: $PASSED${NC}"
    echo -e "  ${RED}Failed: $FAILED${NC}"
    echo -e "  ${YELLOW}Skipped: $SKIPPED${NC}"
    echo "============================================="

    if [ "$FAILED" -eq 0 ]; then
        echo -e "${GREEN}RC SMOKE TEST: PASSED${NC}"
        exit 0
    else
        echo -e "${RED}RC SMOKE TEST: FAILED ($FAILED failures)${NC}"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "============================================="
echo "  Nxentra RC Smoke Test"
echo "  $(date)"
echo "  Backend:  $BASE_URL"
echo "  Frontend: $FRONTEND_URL"
echo "============================================="

test_health
test_auth
test_api
test_frontend
test_management
summary
