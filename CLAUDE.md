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
  ~/OtloblyBackups (worker-token auth, 30-day retention, logs to backup.log)

## Deploy
Render Blueprint (render.yaml): pushing to GitHub main auto-deploys
https://otlobly.onrender.com (gunicorn, persistent disk at /var/data,
health check /healthz — a deploy shows a ~60s 502 swap window).
Secrets live in Render env vars, never in the repo.

## Never commit
.env, otlobly.db*, customer_ids/ (ID photos — PII), po_images/, orders/customers
JSON data files (all already gitignored).
