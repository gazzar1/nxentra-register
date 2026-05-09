#!/usr/bin/env bash
# =============================================================================
# Atomic Frontend Deploy Script
# =============================================================================
#
# A18: Wraps the Next.js + PM2 deploy chain so partial failures don't leave
# /var/www/nxentra_app serving HTML referencing one build ID while
# _next/static/<build-id>/ on disk has another. Every step is fail-fast and
# the next step only runs if the previous one succeeded — `set -euo pipefail`
# plus explicit checks.
#
# Background: 2026-05-02 dry-run found the droplet serving HTML for build A
# while the static folder held build B. Every page on app.nxentra.com 404'd
# on _buildManifest.js. Root cause: a previous deploy ran `npm run build`,
# crashed somewhere in the chain, and pm2 restart was not retried.
#
# Run as the user that owns /var/www/nxentra_app (NOT root).
#
# Usage:
#   ./scripts/deploy-frontend.sh                # Full deploy
#   ./scripts/deploy-frontend.sh --skip-pull    # Use already-pulled tree
#   ./scripts/deploy-frontend.sh --dry-run      # Print steps, do not execute
#
# =============================================================================
set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
APP_ROOT="${APP_ROOT:-/var/www/nxentra_app}"
FRONTEND_DIR="${FRONTEND_DIR:-$APP_ROOT/frontend}"
PM2_PROCESS_NAME="${PM2_PROCESS_NAME:-nxentra-web}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_REMOTE="${GIT_REMOTE:-origin}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:3000/}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-30}"

# -----------------------------------------------------------------------------
# Colors / output
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

step() { printf "${BLUE}==>${NC} %s\n" "$1"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
fail() { printf "${RED}✗${NC} %s\n" "$1" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Args
# -----------------------------------------------------------------------------
SKIP_PULL=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --skip-pull) SKIP_PULL=1 ;;
    --dry-run)   DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,/^# =\{4\}/p' "$0" | sed 's/^# //;s/^#$//'
      exit 0
      ;;
    *) fail "Unknown argument: $arg" ;;
  esac
done

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf "  ${YELLOW}DRY${NC} %s\n" "$*"
  else
    "$@"
  fi
}

# -----------------------------------------------------------------------------
# Pre-flight
# -----------------------------------------------------------------------------
[[ -d "$APP_ROOT" ]]      || fail "APP_ROOT does not exist: $APP_ROOT"
[[ -d "$FRONTEND_DIR" ]]  || fail "FRONTEND_DIR does not exist: $FRONTEND_DIR"
command -v pm2 >/dev/null || fail "pm2 is not on PATH"
command -v npm >/dev/null || fail "npm is not on PATH"
command -v git >/dev/null || fail "git is not on PATH"

cd "$APP_ROOT"
[[ -d .git ]] || fail "$APP_ROOT is not a git repo"

# Refuse to run as root — global node_modules ownership creep is a source of
# the same partial-state bug this script exists to prevent.
if [[ $EUID -eq 0 ]]; then
  fail "Do not run this as root. Run as the deploy user."
fi

step "Pre-flight OK (app=$APP_ROOT, branch=$GIT_BRANCH)"

# -----------------------------------------------------------------------------
# 1. Refresh source
# -----------------------------------------------------------------------------
if [[ $SKIP_PULL -eq 0 ]]; then
  step "git fetch + checkout $GIT_BRANCH"
  run git fetch --quiet "$GIT_REMOTE" "$GIT_BRANCH"
  run git checkout "$GIT_BRANCH"
  run git reset --hard "$GIT_REMOTE/$GIT_BRANCH"
  ok "Tree at $(git rev-parse --short HEAD)"
else
  warn "Skipping git pull (--skip-pull). HEAD: $(git rev-parse --short HEAD)"
fi

# -----------------------------------------------------------------------------
# 2. Frontend build (atomic)
# -----------------------------------------------------------------------------
cd "$FRONTEND_DIR"

step "Wiping previous build (.next/) for atomic replacement"
run rm -rf .next

step "Installing dependencies (npm ci)"
# Use ci, not install — fails fast on lockfile drift instead of silently
# resolving to a different tree than the one we tested.
run npm ci --silent

step "Building Next.js production bundle"
run npm run build

# Sanity: confirm the build ID file exists. If it doesn't, the build silently
# failed and pm2 restart would serve the previous .next/ — exactly the
# partial-state failure A18 prevents.
if [[ $DRY_RUN -eq 0 ]]; then
  if [[ ! -f .next/BUILD_ID ]]; then
    fail ".next/BUILD_ID missing after build — refusing to restart pm2"
  fi
  BUILD_ID=$(cat .next/BUILD_ID)
  ok "Build complete (id=$BUILD_ID)"
else
  ok "Build complete (dry run)"
fi

# -----------------------------------------------------------------------------
# 3. Restart pm2 process
# -----------------------------------------------------------------------------
step "Restarting pm2 process $PM2_PROCESS_NAME"
run pm2 restart "$PM2_PROCESS_NAME" --update-env

# -----------------------------------------------------------------------------
# 4. Health check
# -----------------------------------------------------------------------------
step "Health check: $HEALTH_URL (timeout ${HEALTH_TIMEOUT}s)"

if [[ $DRY_RUN -eq 1 ]]; then
  ok "Skipped health check (dry run)"
else
  HEALTH_OK=0
  for i in $(seq 1 "$HEALTH_TIMEOUT"); do
    if curl --silent --fail --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      HEALTH_OK=1
      ok "Health check passed after ${i}s"
      break
    fi
    sleep 1
  done

  if [[ $HEALTH_OK -ne 1 ]]; then
    warn "Health check failed — process is up but $HEALTH_URL is not responding"
    warn "Check: pm2 logs $PM2_PROCESS_NAME --lines 100"
    fail "Deploy considered failed. Build ID: ${BUILD_ID:-unknown}"
  fi
fi

# -----------------------------------------------------------------------------
# 5. Confirm served build matches built build
# -----------------------------------------------------------------------------
if [[ $DRY_RUN -eq 0 && -n "${BUILD_ID:-}" ]]; then
  step "Verifying served build_id matches disk"
  SERVED_ID=$(curl --silent --max-time 5 "$HEALTH_URL" | grep -oE '"buildId":"[^"]+"' | head -1 | cut -d'"' -f4 || true)
  if [[ -z "$SERVED_ID" ]]; then
    warn "Could not extract buildId from served HTML — skipping match check"
  elif [[ "$SERVED_ID" != "$BUILD_ID" ]]; then
    fail "Mismatch: disk has $BUILD_ID, server is serving $SERVED_ID. Try: pm2 restart $PM2_PROCESS_NAME"
  else
    ok "Served build matches disk ($BUILD_ID)"
  fi
fi

ok "Frontend deploy complete"
