// PM2 ecosystem config for the Nxentra frontend (Next.js).
//
// A38 — codifies how `nxentra-web` should be started so the runtime
// behaviour is reproducible across deploys. Pre-A38 the process was
// started ad-hoc with `pm2 start npm -- start --name nxentra-web` and
// pm2 status showed ~9 restarts/hour over the dry-run window — a real
// signal that "no max_memory_restart, no max_restarts/min_uptime guard,
// nothing in writing about how this should run."
//
// Switch over (one-time, on the droplet):
//   cd /var/www/nxentra_app/frontend
//   pm2 delete nxentra-web        # safe — config below recreates it
//   pm2 start ecosystem.config.js
//   pm2 save                      # persist across droplet reboot
//
// Daily ops keeps using the same `pm2 restart nxentra-web` /
// `pm2 logs nxentra-web` commands; this file just defines what those
// commands operate on.

module.exports = {
  apps: [
    {
      name: "nxentra-web",
      cwd: __dirname,
      // `next start` (production server) — NOT `next dev`. The
      // OPS_PLAYBOOK has a note about a stray "Reloader is on" warning
      // in prod logs; that came from a different process, but we lock
      // this one down explicitly.
      script: "node_modules/next/dist/bin/next",
      args: "start --port 3000",
      // Single fork instance. Next.js handles its own concurrency; PM2
      // cluster mode would split the in-memory route cache and slow
      // first paints without a real CPU win on this 2-vCPU droplet.
      instances: 1,
      exec_mode: "fork",
      // Hard production env; refuse to silently boot in dev mode.
      env: {
        NODE_ENV: "production",
        PORT: "3000",
      },
      // --- Auto-restart guardrails ---
      autorestart: true,
      // Stop the respawn loop after 10 crashes that each die within
      // <10s of starting. Makes a real crash loop visible as
      // "errored / stopped" in `pm2 status` instead of an infinite
      // ~9-restarts-per-hour drain that quietly burns CPU + log disk.
      max_restarts: 10,
      min_uptime: "10s",
      // Cap memory before the OOM killer does. Next.js production
      // server steady-state is 80–150 MB on this droplet; 768 MB leaves
      // wide headroom but catches a runaway leak before swap thrash.
      max_memory_restart: "768M",
      // Brief cooldown so a recurring fast-fail doesn't pin a CPU.
      restart_delay: 2000,
      // No file watching in production — the deploy script re-runs
      // `pm2 restart` explicitly. `watch: true` historically caused
      // spurious restarts on log writes inside the cwd.
      watch: false,
      // Log paths — pm2 default is ~/.pm2/logs which gets unified with
      // other apps. Keep separate streams for easier `tail -f`.
      out_file: "/var/log/nxentra/web.out.log",
      error_file: "/var/log/nxentra/web.err.log",
      merge_logs: true,
      time: true,
    },
  ],
};
