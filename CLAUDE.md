# otlobly-orders

Otlobly's order-management app: concierge Amazon buying for customers in Palestine
(no credit cards — Otlobly orders, ships via GAASH, collects COD in USD).
Covers quotes, the To-order queue, Amazon purchase orders, deposits (عربون),
customer CRM + ID gallery, Meta leads, P&L, parcel tracking (OTL numbers), and a
customer portal with WhatsApp-OTP login.

## Stack
Python 3 / Flask + gunicorn · SQLite (db.py, WAL) · Flask-Login roles
(admin / sales / fulfillment) · single-page vanilla-JS UI in web/index.html ·
public pages in templates/ (track, order intake, account, login).

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
./.venv/bin/python app.py        # http://localhost:8789
```

## Main files
- app.py — the one Flask app (all API routes; dashboard.py is retired)
- db.py — SQLite schema + helpers (orders, customers, payments, meta_leads…)
- store.py / purchases.py / customers.py — business logic (orders, POs, CRM)
- report.py / pnl.py — money summary + P&L
- tracking.py — GAASH timeline + customer-friendly status remap
- meta_leads.py / notify.py — Meta DM+lead-form sync / WhatsApp OTP
- leluxe_goal.py / telegram_bot.py — Leluxe $30k/month goal engine (🎯 Goal view,
  daily Telegram digest, ordered_at backfill) + owner Telegram command bot; both
  daemons are gated by env LELUXE_DIGEST / LELUXE_TG_BOT, set ONLY in
  com.otlobly.app.plist (always-on local launchd service) — never on Render
- web/index.html — the whole staff UI
- com.otlobly.sync.plist — optional launchd ClickUp-sync job (paths point at ~/projects)
- backup_pull.py / com.otlobly.backup.plist — nightly off-site backup: pulls the
  live app's /api/backup zip (DB snapshot + JSON stores + ID/PO images) into
  ~/OtloblyBackups (worker-token auth, 30-day retention, logs to backup.log); since
  2026-09-05 the zip's DB must pass integrity_check here AND the server's manifest
  verdict must be ok, else it is filed as .zip.corrupt + Telegram (a damaged night
  can no longer be banked as ✅)
- docs_sweep.py / com.otlobly.docssweep.plist — 04:15 daily: asks GAASH per open
  parcel whether documents are requested, so the 📄 Docs tab + 🔔 bell are true
  each morning (worker-token POST /api/worker/docs_sweep, 3 parcels per call,
  loops until done, logs to docs_sweep.log)
- db_watch.py / com.otlobly.dbwatch.plist — every 10 min: asks the live app whether
  its DB still reads (`/api/health/db`) and Telegrams the owner the first time it
  does not. Deliberately OFF Render: it watches for the app dying, so anything
  in-process (alerts.py) is the wrong host — on 2026-09-04 a corruption at 12:17
  went unnoticed until 15:02 behind an always-200 /healthz
- dbrepair.py / gunicorn.conf.py — the database HEALS ITSELF. gunicorn's master
  runs `dbrepair.py preflight` before any worker exists (on_starting) and again in
  pre_fork whenever a worker left `otlobly.db.repair-requested` (it stops every
  worker first, so nobody holds the file). Preflight: quick_check → if corrupt,
  BUILD a clean file (db.init_db schema + table-by-table copy, rows past a dead
  page reached by reverse scan / rowid probes / intact indexes, missing core rows
  filled from the newest verified snapshot) → integrity_check → ONE os.replace;
  the damaged file stays as otlobly.db.corrupt-<ts> (+ -wal, + .report.json, all
  via /api/quarantined); one Telegram. Budget: 2 rebuilds/hour, then
  `otlobly.db.maintenance` (humans only). Workers detect corruption in ONE place —
  db.connect() returns a guarded connection whose errors go through
  db.report_corruption (quick_check confirms; a lock/full disk never triggers) —
  answer JSON 503 `{db_error:true}` on /api/*, and step aside. 🛑 RULE: never
  DROP/ALTER/REINDEX or rename/replace the live file on a corrupt DB — request a
  repair (that in-place surgery is how one damaged page became nine outages)
- db_sentinel.py — inside every worker: 10-min quick_check + an HOURLY snapshot
  (`otlobly.snapshot-<YYYYmmdd-HH>.db`, backup API) that gets a FULL integrity_check
  off the hot file and a `.ok` sidecar; dbrepair fills holes in a rebuilt core table
  from the newest `.ok` snapshot (≤1 h loss instead of "gone"); a failed check →
  request_repair. Newest 3 kept. `/api/restore` no longer swaps the file: it stages
  `otlobly.db.pending-restore` + requests a repair; the master applies it with no
  worker alive (old file kept as `otlobly.db.pre-restore-<ts>`). `/api/notifications`
  carries `db:{ok,repairing,maintenance}` from health.json for the UI banner
- web/index.html "honest failures": the global fetch wrapper (next to setOffline)
  reads every failed /api/* JSON answer once, records the reason (apiFailReason —
  session expired / no permission / database repairing / server error) for the
  "couldn't load" panes, and drives the 🩹 #dbBanner (setDbState) from the reply and
  from the 60 s bell poll's db:{…}. app.py answers /api/* 401/403/404/500 as JSON;
  pages keep their redirects. Never add a new "couldn't load" without ${apiFailReason()}
- account_rd.py — per-account RD history for AZ Studio's Accounts Tool
  (`/api/worker/account_rd`, worker token); AZ Studio joins it with the Multilogin fleet
- flag_machine.py — 🚩 watched Gmail inboxes ("action required" subject →
  dedicated flags-bot Telegram nag every minute until the owner replies done);
  daemon gated by env FLAG_MACHINE=1, set ONLY in the Render dashboard (like
  GAASH_MAILER — never the plist, never .env); bot token = env FLAGS_BOT_TOKEN

## Deploy
Render Blueprint (render.yaml): pushing to GitHub main auto-deploys
https://otlobly.onrender.com (gunicorn, persistent disk at /var/data,
health check /healthz — a deploy shows a ~60s 502 swap window).
Secrets live in Render env vars, never in the repo.

## Never commit
.env, otlobly.db*, customer_ids/ (ID photos — PII), po_images/, orders/customers
JSON data files (all already gitignored).
