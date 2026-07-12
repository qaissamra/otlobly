# Otlobly-orders — Full Codebase Security & Reliability Audit

Original audit was analysis-only; every finding cites file:line.

## ✅ Remediation status (updated 2026-07-12 — all merged to `main` & verified live)

The "this week" **and** "this month" priorities are shipped across four PRs and
confirmed running in production on otlobly.co (Deposits + P&L pages load, money
totals tie out to the cent):

- **PR #2** — Security: stored XSS (A-1), order-code race (B-1), worker-token
  `compare_digest` (A-3), ProxyFix (part of A-2).
- **PR #3** — Hardening: atomic store writes (B-3), `OTLOBLY_SECRET` boot guard
  (A-4), boot-reconcile-once (C-1), tests for money/auth/OTP (D-2).
- **PR #4** — Exact Decimal money in pricing + the ledger (B-4).
- (PR #1 was the unrelated website/pricing WIP, merged alongside.)

Status tags below: **✅ FIXED** · **⚠️ PARTIAL** (intentional scope) · **⛔ DEFERRED**
(the "can defer" bucket — still open, none are open doors).

## Architecture summary

- **Framework**: single Flask app (`app.py`, 2972 lines — a god-file holding every route),
  served by gunicorn `--workers 2 --timeout 120` (`Procfile:1`, `render.yaml:16`).
- **Data**: SQLite (`db.py`, WAL) is the live source of truth — orders/customers/payments/
  leads/catalog stored as indexed columns + a `data_json` blob. Several stores are still
  plain JSON files on the data disk: `purchases.json`, `trash.json`, `config.json`,
  `activity.jsonl` (`paths.py` → `OTLOBLY_DATA_DIR`, Render disk at /var/data).
- **Auth**: staff via Flask-Login + role perms (`auth.py`, admin/sales/fulfillment),
  pbkdf2 password hashes. Customer portal via session `cust_phone` set by WhatsApp-OTP /
  email-code / magic-link (KV nonces in the `settings` table). Worker + backup via bearer token.
- **Deploy**: Render Blueprint auto-deploys `main`; secrets in Render env; nightly off-site
  backup pulled by `backup_pull.py` from `/api/backup`.
- **External APIs**: SerpAPI (price import), Meta Graph (ad spend `meta.py`, leads `meta_leads.py`,
  WhatsApp `notify.py`), Resend (email `mailer.py`), GAASH/parcelsapp (tracking `tracking.py`),
  local AZ/Multilogin tool on 127.0.0.1 (`az.py`), background Meta sync thread (`meta_sync.py`).
- **Critical flows traced**: (1) public order intake → `api_quote_request`/`api_catalog_checkout`/
  `api_order_intake` → `make_order`/`store.new_order` → `db.upsert_order`. (2) quote/money →
  `api_quote` → `pricing.apply_markup` → deposit ledger `_record_payment`/`db.add_payment`.
  (3) staff dashboard render → `report.build` → `web/index.html render()/#rows`. (4) customer
  login → OTP/magic-link → `session['cust_phone']` → `api_customer_orders`. (5) supply→demand →
  `api_purchase` → `purchases.save_full`/`apply_to_orders` → order status auto-flip.
- **Assumption**: Render passes traffic through its LB/proxy; app has no ProxyFix, so
  `request.remote_addr` is not the true client IP.

## Findings (severity-ordered within category)

### A. Security
- **✅ FIXED (PR #2)** · **[HIGH] Stored XSS: customer name rendered unescaped in the staff dashboard.**
  Every interpolated field in `render()`/`renderPicking` now runs through `poEsc()`;
  also extended to `showProfile()` (name/address/notes) found in the same sweep.
  `web/index.html:1218` (`<b>${o.customer||"—"}</b>`), `:1223` (`value="${o.profile_box||''}"`),
  `renderPicking` `:1271` (`<td>${o.customer}</td>`). Name arrives unsanitized from public
  endpoints `app.py:760`, `:805`, `:2093` → `store.py:52` → `report.py:33`. The default staff
  view is "Orders" (`web/index.html:393` homeBtn on) which runs `render()`. An attacker submits
  a name like `<img src=x onerror=fetch('/api/users',{method:'POST',...})>` on the public site;
  when an admin opens the dashboard it runs in the admin session (create users, reset passwords,
  pull the PII+password-hash backup). Fix: HTML-escape every interpolated field in `render()`/
  `renderPicking` with the existing `poEsc()` (already used in `neRowHtml`); ideally escape at one
  choke point.

- **⚠️ PARTIAL (PR #2)** · **[MEDIUM] Rate limiting is unreliable (in-memory storage, 2 workers, no ProxyFix).**
  ProxyFix(x_for=1) added, so limits now key on the real client IP (the material fix).
  Shared limiter store deliberately NOT added — stayed on 2 workers (owner call) rather
  than pull in Redis; the per-record DB attempt caps are the real brute-force defense
  (proven by `test_otp_login.py`'s 429 lock). Revisit only if scaling past one instance.
  `app.py:107` `Limiter(get_remote_address, app=app, default_limits=[])` — no `storage_uri` →
  `memory://` per process; with `--workers 2` each worker has its own counters and they reset every
  deploy. No ProxyFix, so `get_remote_address` sees the proxy IP, not the client. OTP throttles
  (`app.py:2461` 5/10min, `:2488`, wa-login `:2358`) are therefore either global (all clients share
  the proxy-IP bucket) or effectively doubled. Fix: add ProxyFix(x_for=1), point Flask-Limiter at a
  shared store (the SQLite/Redis), keep the per-record attempt cap as defense-in-depth.

- **✅ FIXED (PR #2)** · **[MEDIUM] Worker/backup token compared with `==` (non-constant-time).**
  `_worker_ok` now uses `hmac.compare_digest`, matching `_backup_ok`.
  `_worker_ok` `app.py:1736` `return tok and auth_h == f"Bearer {tok}"`. Contrast `_backup_ok`
  `app.py:1778` which correctly uses `hmac.compare_digest`. Guards `/api/worker/queue`,
  `/api/worker/result`, `/api/worker/seed` (can overwrite any order). Fix: `hmac.compare_digest`.

- **✅ FIXED (PR #3)** · **[LOW] `OTLOBLY_SECRET` falls back to a hardcoded value.** `app.py:97`
  (`"dev-secret-change-me"`). Now refuses to boot when `OTLOBLY_SECURE` is set (production)
  and no secret is present; the dev default is kept only for local runs.

- **⛔ DEFERRED** · **[LOW] Public tracking leaks a customer's item list by phone number.** `api_track`
  `app.py:2247` → `_shipments_for` returns item titles/images for any phone entered. Self-service
  by design, but no ownership proof; anyone with a phone number sees what that person ordered.

- **⛔ DEFERRED** · **[LOW] Login/OTP/draft KV rows never purged from `settings`.** `otp:`/`walogin:`/`logintoken:`/
  `emailotp:`/`draft:` keys (`app.py:1974`, `:2365`, `:2473`, `:2528`…) are overwritten to
  `{"used":True}` but never deleted (no `DELETE FROM settings` anywhere). Unbounded growth.

### B. Data integrity
- **✅ FIXED (PR #2)** · **[MEDIUM/HIGH] Order-code generation races → silent order overwrite.** `_next_code`
  `db.py:236` reads all codes and returns `max+1` in Python; two concurrent creates (e.g. website
  checkout + intake across the 2 workers) get the same `OTL-####`, and `upsert_order`
  `db.py:274` uses `ON CONFLICT(order_code) DO UPDATE SET data_json=excluded...` — so the second
  insert silently overwrites the first order instead of erroring. Fix: allocate the id inside one
  transaction (or an AUTOINCREMENT-derived code) and use a plain INSERT for new orders.

- **⚠️ PARTIAL (PR #3)** · **[MEDIUM] JSON stores use whole-file read-modify-write with no locking under 2 workers.**
  `purchases.py:33-40`, `trash.py:38-45`, `cfg.py:66-69`. Concurrent PO edits (`api_purchase`
  `app.py:1393`), cart-cost writes (`cfg.save`, `app.py:528`), or trash ops lose one another's
  writes. POs carry tracking numbers and customer matches. Atomic writes shipped (see B-3) —
  torn-file corruption is gone — but true concurrent-edit locking (lost-update prevention) is
  NOT closed; that needs moving these stores into SQLite. Low probability (admin-only, low
  concurrency); deferred to a SQLite migration.

- **✅ FIXED (PR #3)** · **[MEDIUM] `purchases.save`/`trash.save`/`cfg.save` are non-atomic `write_text`.**
  New `paths.write_json_atomic()` (temp + fsync + `os.replace`) now backs all three saves, so a
  crash or second worker can never leave a truncated store.

- **✅ FIXED (PR #4)** · **[MEDIUM] All money is float, not Decimal.** `pricing.apply_markup` `pricing.py:41`,
  `_to_usd` `app.py:1072`, `deposit_total_for_order` `db.py:656`, report/pnl sums. New `money.py`
  does the arithmetic in `Decimal` (half-up to cents) at pricing, the ledger, fx, and the
  report/revenue rollups; values are still stored/returned as rounded floats (no format change,
  verified no Decimal leaks into JSON). Live check: ₪50→$13.51 and the P&L batches sum exactly to
  revenue. (`meta.py` ad-spend + `pnl` COGS sub-sums left as-is — few rows, own selftests.)

- **Backup/restore story (checked): solid.** `/api/backup` `app.py:1781` snapshots the DB via
  SQLite's online backup API (WAL-safe) + whitelisted JSON/images + a row-count manifest;
  `backup_pull.py` verifies the zip and manifest before trusting it, 30-day retention. The gap is
  that a lost host between nightly pulls loses up to a day, and the JSON stores in the zip may be a
  mid-write torn file (see atomic-write finding).

### C. Reliability
- **✅ FIXED (PR #3)** · **[MEDIUM] Boot-time reconciliation runs at import, per worker, over all data.**
  `_link_unlinked_deposits` `app.py:2927`, `_backfill_customers_from_orders` `:2934`,
  `_reconcile_pos_to_orders` `:2961`. New `db.claim_once()` (atomic on the settings PK) now lets
  exactly one worker run all three once per deploy, ending the boot race on `purchases.save`.

- **⛔ DEFERRED** · **[LOW] Synchronous long calls tie up 1 of 2 workers.** `run_script("clickup.py")` with
  `timeout=600` inside `/api/clickup` `app.py:1686`; `meta_leads.sync()` (many 25s Graph calls)
  inside `/api/meta/leads` `app.py:1332`. With only 2 workers, one slow call halves capacity.

- **Error handling (checked): mostly intentional.** Bare/broad `except` blocks are consistently
  used to keep webhooks and logging from 500-ing (`app.py:2417`, `activity.py:84`), which is
  reasonable; external calls in `tracking.py`/`meta.py`/`notify.py`/`az.py` all set timeouts.

### D. Code quality & maintainability
- **⛔ DEFERRED** · **[MEDIUM] `app.py` is a 2972-line god-file** — every route, auth glue, portal, webhooks, and
  backup in one module. Hard to test in isolation.
- **✅ FIXED (PR #2 + #3)** · **[MEDIUM] No automated tests for money or auth.** Was: only ad-hoc `--selftest`.
  Now `test_order_code_race.py` (race → no overwrite), `test_money_and_auth.py` (markup, deposit
  netting incl. a 100×$0.01 drift proof, the role→permission matrix), and `test_otp_login.py`
  (OTP request→verify, enumeration guard, 429 attempt cap) plus `money.py --selftest`.
- **✅ FIXED (PR #2)** · **[LOW] Duplicated order renderers with inconsistent escaping** — `render()`/`#rows`
  (`web/index.html:1202`) vs `neRowHtml` (`:1804`); the escaping divergence (the XSS above) is
  closed. The two renderers still coexist (not merged) but both now escape.
- **⛔ DEFERRED** · **[LOW] Dead JSON-store restorers still wired.** `trash.py:156-169` writes to the retired
  `store.py`/`customers.py` JSON stores; `app.py:169` overrides them for the DB, but the dead paths
  remain and could be re-connected by mistake.

### E. Performance
- **⛔ DEFERRED** · **[MEDIUM] Read endpoints deserialize and scan ALL orders in Python.** `db.list_orders`
  `db.py:251` loads every `data_json`; `report.build`, `pnl.*`, `_match_customer` `app.py:2289`,
  `_phone_pairs` `:2159`, `_order_for_phone` `:1091` all iterate the full list. The `status`/
  `customer` indexes exist (`db.py:114`) but reads never use SQL filtering. Degrades at 10× volume.
- **⛔ DEFERRED** · **[LOW] Per-request full scans in the customer portal & login.** `_match_customer`
  (`app.py:2289`) runs on every OTP request/verify; portal loads scan all orders + all POs.
- **⛔ DEFERRED** · **[LOW] `next_order_code`/`next_customer_code` full-column scan + max()** (`db.py:236`) on
  every create. (Note: the *correctness* race this caused, B-1, is fixed; the full-scan
  *performance* aspect remains.)

## Prioritized fix order — progress
- **This week (do not ship public intake without these): ✅ ALL DONE**
  1. ✅ Escape customer-controlled fields in `render()`/`renderPicking` (HIGH XSS, A-1) — PR #2.
  2. ✅ Fix the order-code race → silent overwrite (B-1): plain INSERT in a transaction — PR #2.
  3. ✅ `hmac.compare_digest` in `_worker_ok` (A-3) — PR #2; ✅ ProxyFix (A-2) — PR #2
     (shared limiter store intentionally skipped, see A-2).
- **This month: ✅ CORE DONE (2 items intentionally deferred)**
  4. ⚠️ `purchases`/`trash`/`config` — atomic-write shipped (B-3, PR #3); full SQLite move (B-2) deferred.
  5. ✅ Decimal money in ledger + pricing (B-4) — PR #4; ✅ guard `OTLOBLY_SECRET` on boot (A-4) — PR #3.
  6. ✅ Boot reconciliation → one-shot via `claim_once` (C-1) — PR #3; ⛔ async `/api/clickup` +
     `/api/meta/leads` (C-2) deferred (low priority, UI-coupled).
  7. ✅ Tests for markup, deposit netting, OTP verify, permission decorator (D-2) — PR #2/#3.
- **Can defer (still open, none are open doors):** SQL-filtered reads/indexes (E-1/E-3),
  settings-KV TTL sweep (A-6), split app.py (D-1), remove dead JSON restorers (D-4),
  public-track ownership check (A-5), full JSON-store→SQLite move (B-2), async offload (C-2).

## Verdict

**Original (2026-07):** Conditionally NO for production as-is. The business-critical money and
backup design is sound (pbkdf2 hashes, WAL-safe verified backups, role redaction, signed webhooks,
anti-enumeration login, dev-code guards), but three concrete defects are live: a stored-XSS path
from the public website into the admin session, an order-code race that can silently overwrite a
real order under the configured 2 workers, and rate limiting that is effectively decorative behind
the proxy. Fix A-1, B-1, A-2/A-3 first; after that this is a reasonable small-scale production app
whose remaining issues are hardening and scale, not open doors.

**Updated (2026-07-12) — ✅ YES for production.** All three blocking defects are fixed, merged, and
verified live: the stored XSS is escaped (A-1), new-order creation is a race-safe transactional
INSERT (B-1), the worker token is constant-time (A-3), and ProxyFix makes rate limiting key on the
real client IP (A-2). The "this month" hardening also landed — atomic store writes (B-3), boot-once
reconcile (C-1), the `OTLOBLY_SECRET` boot guard (A-4), exact Decimal money (B-4), and a real test
suite for money/auth/OTP (D-2). Confirmed on otlobly.co: healthy boot, Deposits + P&L load, money
totals tie out to the cent. What remains is the explicitly-deferred "can defer" bucket — scale
(SQL-filtered reads), a full JSON-store→SQLite move (B-2), async offload of two admin buttons (C-2),
a settings-KV sweep (A-6), an app.py split (D-1), and two low-risk niceties (A-5, D-4) — all
hardening/scale, not open doors.
