# Frontend Deploy Runbook

## Overview

The Nxentra frontend is a Next.js app served via PM2 (`nxentra-web`) on the
DigitalOcean droplet. This runbook covers atomic deploys, build-ID verification,
rollback, and "what to check when prod looks broken."

The 2026-05-02 dry-run found the droplet serving HTML referencing one build ID
while `_next/static/<build-id>/` on disk held a different one — every page on
`app.nxentra.com` 404'd on `_buildManifest.js`. The atomic-deploy script and
this runbook exist so that failure mode does not recur silently.

---

## 1. Standard Deploy

```bash
ssh deploy@<droplet>
cd /var/www/nxentra_app
./scripts/deploy-frontend.sh
```

The script is fail-fast end-to-end:

1. `git fetch + reset --hard origin/main` (so dirty state from a prior aborted
   deploy doesn't bleed into the new one).
2. `rm -rf .next/` then `npm ci` — refuses to use the previous build artifact.
3. `npm run build` — verifies `.next/BUILD_ID` exists before proceeding.
   If the build silently failed, the script exits before pm2 restart, so the
   merchant continues to see the previous (working) build.
4. `pm2 restart nxentra-web --update-env`.
5. Health check: `curl http://127.0.0.1:3000/` until it returns 200 or
   the timeout elapses (default 30s).
6. Build-ID match check: extracts the `"buildId"` from the served HTML
   and compares against `.next/BUILD_ID`. Mismatch ⇒ exit non-zero.

Flags:

- `--skip-pull` — when you already pulled by hand and want to re-run the build.
- `--dry-run` — print the steps without executing.

---

## 2. What to Check When Prod Looks Broken

If `app.nxentra.com` is returning 404s or showing a stale UI:

```bash
# Step 1: did pm2 actually restart?
pm2 status nxentra-web
# If status is "errored" or "stopped", look at the recent logs:
pm2 logs nxentra-web --lines 100 --err
```

```bash
# Step 2: is the served build_id the same as the on-disk build_id?
curl -s http://127.0.0.1:3000/ | grep -oE '"buildId":"[^"]+"' | head -1
cat /var/www/nxentra_app/frontend/.next/BUILD_ID
```

If these don't match, the served process is running off a stale `.next/`. Run:

```bash
pm2 restart nxentra-web --update-env
```

If they still don't match, the process picked up an inconsistent partial
build. Re-run `./scripts/deploy-frontend.sh` to wipe `.next/` and rebuild.

```bash
# Step 3: do the static assets the served HTML references actually exist?
SERVED=$(curl -s http://127.0.0.1:3000/ | grep -oE '_next/static/[^"]+/_buildManifest.js' | head -1)
ls -la "/var/www/nxentra_app/frontend/$SERVED"
```

If `ls` 404s, that's the partial-deploy bug from the 2026-05-02 dry-run. Run
the deploy script — it will fail fast on `BUILD_ID` mismatch before pm2 picks
up a half-built tree.

---

## 3. Rollback

```bash
cd /var/www/nxentra_app
git log --oneline -10
git reset --hard <previous-good-sha>
./scripts/deploy-frontend.sh --skip-pull
```

The deploy script will rebuild from the rolled-back tree (no `git fetch`
because of `--skip-pull`). Health check at the end verifies the rollback is
serving correctly.

---

## 4. Common Failure Modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `npm ci` exits "lockfile drift" | someone edited `package.json` without `npm install` | Run `npm install` locally, commit the new `package-lock.json`, retry |
| `.next/BUILD_ID missing` after build | OOM during build, or `npm run build` exited nonzero | Check `pm2 logs`, free memory (`free -m`), retry. If droplet is OOM-bound, consider scaling. |
| Health check times out | port 3000 already in use, or app crashed at boot | `pm2 logs nxentra-web --err`, fix root cause, restart |
| Build-ID mismatch after restart | process started serving the old `.next/` because of file caching | Run `pm2 restart nxentra-web --update-env` again, or `pm2 delete nxentra-web && pm2 start ecosystem.config.js` |
| 9 restarts/hour in `pm2 status` (memory leak) | unhandled crash in a Next.js route, or development reloader still on | Check A38 ticket — investigate `pm2 logs --err`. Bump `max-memory-restart` as a stop-gap. |

---

## 5. After Every Deploy

- Smoke-test the wizard / signup / reconciliation pages in an incognito tab.
- Watch `pm2 status` for 5 minutes — stable memory and zero restarts is the bar.
- If a Sentry alert fires within 10 minutes, treat it as a deploy regression
  and roll back before debugging.
