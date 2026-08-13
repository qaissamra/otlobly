#!/usr/bin/env python3
"""
SQLite persistence for the hosted multi-user app. Concurrency-safe (WAL), pure
stdlib (sqlite3). Hybrid storage: indexed columns for querying + a `data_json`
blob that preserves the EXACT order/customer dict shape the existing modules
(report.py, pnl.py, messages.py, customers.enrich) already expect — so business
logic ports over untouched.

  python3 db.py --init      # create the schema (idempotent)
  python3 db.py --stats     # row counts
"""

import contextvars
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import money
from paths import data_path

# --------------------------------------------------------------------------- #
# Tenant scoping — the "current business" for the in-flight request.
# app.py sets this from the logged-in user's business_id in a before_request hook;
# db reads filter by it and writes tag it, so a broker tenant only ever sees/writes
# its OWN rows. Defaults to 1 (Otlobly) — correct for CLI/worker/background and,
# while Otlobly is the only business, a no-op (all rows are already business 1).
# --------------------------------------------------------------------------- #
_CURRENT_BUSINESS = contextvars.ContextVar("business_id", default=1)


def current_business():
    return _CURRENT_BUSINESS.get()


def set_current_business(business_id):
    _CURRENT_BUSINESS.set(int(business_id) if business_id else 1)

# OTLOBLY_DB pins an exact file; otherwise it lives in the data dir (the project
# folder locally, or the persistent disk in a hosted deploy via OTLOBLY_DATA_DIR).
DB_FILE = Path(os.environ.get("OTLOBLY_DB") or data_path("otlobly.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                  -- the broker's business name (shown in their UI/portal)
  slug TEXT UNIQUE,                    -- url-safe handle
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT,
  data_json TEXT NOT NULL DEFAULT '{}' -- per-business config: markup, ils_rate, whatsapp, logo…
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,                 -- admin | sales | fulfillment
  name TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_code TEXT UNIQUE,
  match_key TEXT UNIQUE,
  name TEXT, whatsapp TEXT, city TEXT, vip INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_code TEXT UNIQUE,
  customer_id INTEGER,
  customer_phone TEXT,
  status TEXT,
  amount_to_collect_usd REAL,
  batch TEXT,
  profile_box TEXT,
  amazon_order_number TEXT,
  tracking_number TEXT,
  signature TEXT,
  created_at TEXT, updated_at TEXT, created_by INTEGER,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_code TEXT, asin TEXT, clean_url TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, user_id INTEGER, username TEXT, action TEXT,
  entity TEXT, entity_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS meta_leads (
  lead_id TEXT PRIMARY KEY,
  source TEXT,                         -- messenger | instagram | leadform
  name TEXT, phone TEXT, email TEXT,
  form_id TEXT, form_name TEXT, ad_name TEXT,
  last_message TEXT, last_activity TEXT, created_time TEXT, response_min REAL,
  status TEXT NOT NULL DEFAULT 'new',  -- new | contacted | converted | lost
  assigned_to INTEGER, note TEXT, order_id TEXT,
  synced_at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,                              -- when recorded (system)
  paid_at TEXT,                         -- date the customer actually paid (editable)
  order_code TEXT,                      -- OTL-#### (nullable = customer-level credit)
  customer_phone TEXT,                  -- phone_core, for per-customer rollup
  customer_name TEXT,
  kind TEXT NOT NULL DEFAULT 'deposit', -- deposit | refund | collect
  currency TEXT NOT NULL DEFAULT 'ILS', -- ILS | USD (what staff typed)
  amount_entered REAL,                  -- e.g. 50 (₪)
  fx_rate REAL,                         -- ils_per_usd frozen at record time
  amount_usd REAL,                      -- computed USD (frozen)
  note TEXT,
  created_by INTEGER, created_by_name TEXT
);
CREATE TABLE IF NOT EXISTS catalog_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_id INTEGER NOT NULL DEFAULT 1,
  asin TEXT, amazon_url TEXT,               -- staff-internal, never sent to customers
  title TEXT, image TEXT,
  base_price_usd REAL,                      -- fetched Amazon price (internal)
  price_usd REAL,                           -- display price customers see
  category TEXT, note TEXT,
  active INTEGER NOT NULL DEFAULT 1,        -- 0 = hidden from the public catalog
  sort INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT, created_by INTEGER
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS ix_leads_status ON meta_leads(status);
CREATE INDEX IF NOT EXISTS ix_leads_created ON meta_leads(created_time);
CREATE INDEX IF NOT EXISTS ix_pay_order ON payments(order_code);
CREATE INDEX IF NOT EXISTS ix_pay_customer ON payments(customer_phone);
CREATE INDEX IF NOT EXISTS ix_catalog_biz ON catalog_items(business_id, active);
CREATE TABLE IF NOT EXISTS usage_counters (
  business_id INTEGER NOT NULL,
  resource TEXT NOT NULL,              -- searches | …  (metered per-action usage)
  period TEXT NOT NULL,                -- 'YYYY-MM' (monthly) or 'all'
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (business_id, resource, period)
);
CREATE TABLE IF NOT EXISTS leluxe_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clickup_task_id TEXT UNIQUE,          -- NULL until the first successful push
  parent_task_id TEXT,                  -- ClickUp parent task id (items only)
  parent_local_id INTEGER,              -- local FK, works before the parent is pushed
  kind TEXT NOT NULL DEFAULT 'parent',  -- parent | item
  name TEXT,
  status TEXT,                          -- one of the ClickUp list's status strings
  due_date TEXT,                        -- ms-epoch string, ClickUp-native
  date_created TEXT,
  updated_at TEXT,
  sync_state TEXT NOT NULL DEFAULT 'synced',  -- synced | dirty | pushing | error
  sync_error TEXT,
  sync_claimed_at TEXT,                 -- pushing-claim stamp (stale-claim recovery)
  sync_attempts INTEGER NOT NULL DEFAULT 0,
  img_scanned INTEGER NOT NULL DEFAULT 0, -- 1 = attachments already localized (import cursor)
  deleted INTEGER NOT NULL DEFAULT 0,
  data_json TEXT NOT NULL DEFAULT '{}'  -- description, tags, fields, images, pushed snapshot
);
CREATE INDEX IF NOT EXISTS ix_leluxe_parent ON leluxe_orders(parent_task_id);
CREATE INDEX IF NOT EXISTS ix_leluxe_plocal ON leluxe_orders(parent_local_id);
CREATE INDEX IF NOT EXISTS ix_leluxe_sync ON leluxe_orders(sync_state);
CREATE TABLE IF NOT EXISTS gerizim_registrations (
  tracking      TEXT PRIMARY KEY,            -- GWD number registered on postgerizim
  registered_at TEXT,                        -- when the local tool submitted it
  ok            INTEGER NOT NULL DEFAULT 1,  -- 0 = submitted but Gerizim flagged an issue
  business_id   INTEGER NOT NULL DEFAULT 1,  -- single-tenant owner-ops; kept for consistency
  updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_gerizim_reg_biz ON gerizim_registrations(business_id);
CREATE TABLE IF NOT EXISTS leluxe_cu_deletes (
  task_id    TEXT PRIMARY KEY,             -- ClickUp task to delete (working list only)
  row_id     INTEGER,                      -- local leluxe_orders row it belonged to
  label      TEXT,                         -- human name for status/error display
  state      TEXT NOT NULL DEFAULT 'pending', -- pending | doing | done | skipped
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_leluxe_cudel_state ON leluxe_cu_deletes(state);
CREATE TABLE IF NOT EXISTS az2_pushes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  row_id INTEGER,                       -- local leluxe_orders.id (may outlive it)
  task_id TEXT NOT NULL,                -- the AZ (2) task written to
  field TEXT NOT NULL,                  -- 'status' (v1 allowlist)
  old_value TEXT,                       -- AZ (2) value at push time (CAS-verified)
  new_value TEXT,                       -- what we wrote
  snapshot_json TEXT,                   -- full AZ (2) task JSON BEFORE the write
  ts TEXT NOT NULL,
  user TEXT,
  state TEXT NOT NULL DEFAULT 'pushed', -- pushed | undone | undo
  undo_of INTEGER                       -- for state='undo': the push it reverts
);
CREATE INDEX IF NOT EXISTS ix_az2p_task ON az2_pushes(task_id);
CREATE INDEX IF NOT EXISTS ix_az2p_ts ON az2_pushes(ts);
CREATE TABLE IF NOT EXISTS leluxe_status_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  row_id INTEGER NOT NULL,              -- leluxe_orders.id
  old_status TEXT,
  new_status TEXT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'app'    -- app | pull | sync
);
CREATE INDEX IF NOT EXISTS ix_lxlog_row ON leluxe_status_log(row_id, ts);
CREATE INDEX IF NOT EXISTS ix_lxlog_new ON leluxe_status_log(new_status);
-- 📦 GAASH status changes — what a "Check tracking" run actually moved. The
-- carriers' own answer is a moving target, so a run that changes 13 parcels
-- and reports only "13 checked" is unreviewable: this is the receipt. One
-- gash_runs row per press, one gash_status_log row per parcel that moved.
CREATE TABLE IF NOT EXISTS gash_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT,
  checked INTEGER NOT NULL DEFAULT 0,   -- parcels looked at
  changed INTEGER NOT NULL DEFAULT 0,   -- parcels that moved
  source TEXT NOT NULL DEFAULT 'check'  -- check | sequencer
);
CREATE TABLE IF NOT EXISTS gash_status_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,                       -- gash_runs.id
  at TEXT NOT NULL,
  gwd TEXT NOT NULL,
  name TEXT,                            -- the product, as shown on the board
  code TEXT,                            -- the NAME box (B28 / E-B31)
  order_status TEXT,                    -- rd | ordered … the board's own pill
  old_status TEXT,
  new_status TEXT,
  store TEXT NOT NULL DEFAULT 'leluxe'  -- leluxe | purchases
);
CREATE INDEX IF NOT EXISTS ix_gashlog_at ON gash_status_log(at);
CREATE INDEX IF NOT EXISTS ix_gashlog_run ON gash_status_log(run_id);
CREATE INDEX IF NOT EXISTS ix_gashlog_gwd ON gash_status_log(gwd);
CREATE TABLE IF NOT EXISTS leluxe_pkg_mail (
  gwd TEXT PRIMARY KEY,                 -- one clearance-email thread per GWD package
  to_email TEXT,
  subject TEXT,
  sent_at TEXT,                         -- last send (a resend restarts the cycle)
  sent_count INTEGER NOT NULL DEFAULT 0,
  replied_at TEXT                       -- owner marks the reply by hand (no inbox hookup)
);

-- 📧 GAASH Mail (gaash_mail.py): automated clearance-email sequences.
-- Owner-scoped like the rest of the leluxe_* family — no business_id.
CREATE TABLE IF NOT EXISTS gaash_accounts (
  id TEXT PRIMARY KEY,                  -- acct_<ms>
  email TEXT NOT NULL,
  label TEXT,
  app_password TEXT,                    -- Gmail app password (data disk only, never in git)
  added_at TEXT,
  last_check TEXT,
  last_error TEXT,
  imap_uidvalidity INTEGER,
  imap_last_uid INTEGER,                -- IMAP cursor: only mail AFTER this is read
  seen_ids_json TEXT                    -- Message-ID dedupe ring
);
CREATE TABLE IF NOT EXISTS gaash_ids (
  id TEXT PRIMARY KEY,                  -- id_<ms> — the reusable ID-document library
  name TEXT NOT NULL,
  filename TEXT NOT NULL,               -- under data/gaash_ids/
  uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS gaash_threads (
  gwd TEXT PRIMARY KEY,                 -- one email conversation per GWD package
  account_id TEXT,
  state TEXT NOT NULL DEFAULT 'active', -- active|waiting_reply|missing_docs|paused|cleared|exhausted|done
  step INTEGER NOT NULL DEFAULT 0,      -- sequence emails already sent (0..4)
  next_send_at TEXT,
  id_doc_id TEXT,                       -- gaash_ids.id attached to email 1
  subject TEXT,
  unread INTEGER NOT NULL DEFAULT 0,
  missing_docs INTEGER NOT NULL DEFAULT 0,
  missing_note TEXT,
  pending_files_json TEXT,              -- files queued for the NEXT send (e.g. a KMT)
  resend_json TEXT,                     -- office-closed retry marker {due_at,...}
  last_error TEXT,
  created_at TEXT,
  last_activity TEXT
);
CREATE TABLE IF NOT EXISTS gaash_msgs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gwd TEXT NOT NULL,
  dir TEXT NOT NULL,                    -- out | in
  kind TEXT NOT NULL,                   -- sent|resent|reply|auto_ack|closed
  step INTEGER,
  at TEXT NOT NULL,
  from_addr TEXT,
  to_addr TEXT,
  subject TEXT,
  message_id TEXT,
  in_reply_to TEXT,
  body TEXT,
  attachments_json TEXT,
  imap_uid INTEGER,
  notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_gaashmsgs_gwd ON gaash_msgs(gwd, at);
CREATE INDEX IF NOT EXISTS ix_gaashmsgs_mid ON gaash_msgs(message_id);

-- 📧 GAASH Mail v2: HubSpot-style sequences-as-data (builder / templates /
-- triggers / tracking). Same owner-scoped convention.
CREATE TABLE IF NOT EXISTS gaash_templates (
  id TEXT PRIMARY KEY,                  -- tpl_<ms> — the reusable email library
  name TEXT NOT NULL,
  subject_tpl TEXT,
  body_tpl TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS gaash_sequences (
  id TEXT PRIMARY KEY,                  -- seq_<ms>
  name TEXT NOT NULL,
  to_address TEXT,                      -- per-sequence recipient (any platform)
  goal TEXT NOT NULL DEFAULT 'cleared', -- cleared | reply | manual
  send_window_json TEXT,                -- {tz,days:[weekday ints],start,end}
  created_at TEXT,
  updated_at TEXT,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gaash_steps (
  id TEXT PRIMARY KEY,                  -- stp_<ms><n>
  seq_id TEXT NOT NULL,
  pos INTEGER NOT NULL,                 -- 0-based order in the sequence
  kind TEXT NOT NULL DEFAULT 'auto_email',  -- auto_email | task
  template_id TEXT,
  task_note TEXT,
  delay_days REAL NOT NULL DEFAULT 0    -- BUSINESS days before this step fires
);
CREATE INDEX IF NOT EXISTS ix_gaashsteps_seq ON gaash_steps(seq_id, pos);
CREATE TABLE IF NOT EXISTS gaash_rules (
  id TEXT PRIMARY KEY,                  -- rul_<ms> — auto-enroll triggers
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  cond_json TEXT,                       -- {gash_status, min_age_days}
  seq_id TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'queue',   -- queue (approval) | auto
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS gaash_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, -- raw open/click tracking hits
  msg_id INTEGER NOT NULL,              -- gaash_msgs.id
  kind TEXT NOT NULL,                   -- open | click
  at TEXT NOT NULL,
  url TEXT,
  ua TEXT
);
CREATE INDEX IF NOT EXISTS ix_gaashevents_msg ON gaash_events(msg_id);
CREATE TABLE IF NOT EXISTS gaash_picks (
  gwd TEXT PRIMARY KEY,                 -- name pinned BEFORE any thread exists —
  pname TEXT,                           -- the enroll picker saves on change now
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS gaash_autoclear (
  gwd TEXT PRIMARY KEY,                 -- in-app ✅ AUTO CLEAR tag (📦 Purchases
  at TEXT                               -- parcels — Leluxe tags live in ClickUp)
);
-- 🚩 Flag machine (flag_machine.py): watch Gmail inboxes read-only; an
-- "action required" subject raises a flag that nags the owner on Telegram
-- every minute until they reply done. Owner-scoped like gaash_* — no
-- business_id. Runs ONLY on the Mac's launchd app (env FLAG_MACHINE=1);
-- app passwords live in this DB on the data disk, never the repo.
CREATE TABLE IF NOT EXISTS flag_inboxes (
  id TEXT PRIMARY KEY,                  -- fin_<ms>
  email TEXT NOT NULL,
  label TEXT,
  app_password TEXT,                    -- Gmail app password (data disk only)
  active INTEGER NOT NULL DEFAULT 1,    -- paused inboxes keep creds + cursor
  added_at TEXT,
  last_check TEXT,
  last_error TEXT,                      -- the ⚠ chip (auth-help text)
  imap_uidvalidity INTEGER,
  imap_last_uid INTEGER,                -- cursor: only mail AFTER this is read
  seen_ids_json TEXT                    -- Message-ID dedupe ring (cap 2000)
);
CREATE TABLE IF NOT EXISTS flag_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inbox_id TEXT,
  email TEXT,                           -- denormalized: survives inbox removal
  msg_id TEXT,                          -- Message-ID
  uid INTEGER,
  subject TEXT,                         -- RFC2047-decoded
  sender TEXT,
  matched_phrase TEXT,
  created_at TEXT,
  state TEXT NOT NULL DEFAULT 'open',   -- open | done
  done_at TEXT,
  last_sent_at TEXT,                    -- stamped only AFTER an ok Telegram send
  sent_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_flagalerts_msg ON flag_alerts(email, msg_id);
CREATE INDEX IF NOT EXISTS ix_flagalerts_state ON flag_alerts(state);
"""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_CORRUPT_MARKERS = ("file is not a database", "not a database",
                    "database disk image is malformed", "malformed")


def _quarantine_corrupt_db(exc):
    """A corrupt otlobly.db crashes the app at boot (init_db opens it). Move the
    bad file aside so a fresh schema can be created and the app can START — then
    POST /api/restore (worker token) swaps in a good backup DB. Only fires on
    real corruption, never on a lock/busy. Returns True if it quarantined."""
    msg = str(exc).lower()
    if not any(m in msg for m in _CORRUPT_MARKERS) or not DB_FILE.exists():
        return False
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        DB_FILE.rename(DB_FILE.with_name(DB_FILE.name + f".corrupt-{ts}"))
    except OSError:
        return False
    for ext in ("-wal", "-shm"):
        p = DB_FILE.with_name(DB_FILE.name + ext)
        try:
            p.unlink()
        except OSError:
            pass
    print(f"[db] CORRUPT {DB_FILE.name} quarantined (.corrupt-{ts}); starting "
          f"with an empty schema — restore a backup via POST /api/restore",
          flush=True)
    return True


def init_db():
    # gunicorn boots 2 workers that BOTH run this at import. Concurrent DDL
    # (executescript / migrate's ALTERs) can collide as "database is locked" —
    # SQLite's deadlock detection returns it instantly, ignoring the connect
    # timeout — which kills the worker and fails the whole deploy. The loser
    # simply retries after the winner commits; every statement is guarded
    # (IF NOT EXISTS / _columns), so a re-run is a no-op.
    last = None
    for attempt in range(6):
        if attempt:
            time.sleep(0.5 * attempt)
        try:
            # executescript AND migrate both run under the corruption guard: a
            # malformed table (e.g. leluxe_orders) raised by migrate's ALTER/PRAGMA
            # is quarantined + rebuilt too, instead of crashing every worker at boot.
            try:
                with connect() as c:
                    c.executescript(SCHEMA)
                migrate()
            except sqlite3.DatabaseError as e:
                if not _quarantine_corrupt_db(e):
                    raise
                with connect() as c:
                    c.executescript(SCHEMA)
                migrate()
            return
        except sqlite3.OperationalError as e:
            if not any(m in str(e).lower() for m in ("locked", "busy", "duplicate column")):
                raise
            last = e
    raise last


# --------------------------------------------------------------------------- #
# Multi-tenant migration — every business-owned row carries a business_id.
# --------------------------------------------------------------------------- #
# Tables whose rows belong to exactly one business. settings is handled by
# key-prefixing (b<id>:key), so it's not ALTER-ed here.
TENANT_TABLES = ("users", "customers", "orders", "order_items",
                 "audit_log", "meta_leads", "payments")


def _columns(c, table):
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def migrate():
    """Idempotent schema upgrades that CREATE-IF-NOT-EXISTS can't express (adding
    columns to existing tables). Runs on every boot, right after the base schema.

    Adds business_id to every tenant table and backfills pre-tenancy rows to
    business #1 (this deployment's own business). While a single business exists,
    unscoped reads and business=1 reads return the same rows — so this is
    non-breaking. The backfill only runs while sole-tenant; once a 2nd business is
    added, every insert sets business_id explicitly and there are no NULLs left."""
    with connect() as c:
        for table in TENANT_TABLES:
            if "business_id" not in _columns(c, table):
                c.execute(f"ALTER TABLE {table} ADD COLUMN business_id INTEGER")
            c.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_biz ON {table}(business_id)")
        # Customer email login (portal second method): the column is the single
        # source of truth — NOT mirrored into data_json, which upsert_customer
        # rewrites wholesale and would clobber. Unique per business (case-blind),
        # partial so empty/NULL rows never collide.
        if "email" not in _columns(c, "customers"):
            c.execute("ALTER TABLE customers ADD COLUMN email TEXT")
            c.execute("ALTER TABLE customers ADD COLUMN email_verified_at TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_email "
                  "ON customers(business_id, lower(email)) "
                  "WHERE email IS NOT NULL AND email <> ''")
        # Portal auth v2: customer password login (manual "create an account").
        # Column-only like email — never mirrored into data_json (upsert_customer
        # rewrites that wholesale). Hash via auth.hash_pw / check_password_hash.
        if "password_hash" not in _columns(c, "customers"):
            c.execute("ALTER TABLE customers ADD COLUMN password_hash TEXT")
        # True order date for Leluxe goal math (ms-epoch string). Set once —
        # backfilled from the AZ (2) source list or stamped at insert; the
        # ClickUp pull/push sync never writes this column, so it survives every
        # import/merge (date_created was reset to migration day on 2026-07-14).
        if "ordered_at" not in _columns(c, "leluxe_orders"):
            c.execute("ALTER TABLE leluxe_orders ADD COLUMN ordered_at TEXT")
        # 📧 GAASH Mail v2: threads point at a sequence; messages carry the
        # step + open/click tracking counters (bumped by /api/gaash/px|r).
        if "seq_id" not in _columns(c, "gaash_threads"):
            c.execute("ALTER TABLE gaash_threads ADD COLUMN seq_id TEXT")
        # the name this parcel ships under, PICKED by the owner — overrides what
        # the boards say (a package can carry a wrong/blank NAME ON PACKAGEE)
        if "pname" not in _columns(c, "gaash_threads"):
            c.execute("ALTER TABLE gaash_threads ADD COLUMN pname TEXT")
        if "seq_id" not in _columns(c, "gaash_msgs"):
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN seq_id TEXT")
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN step_id TEXT")
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN opens INTEGER NOT NULL DEFAULT 0")
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0")
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN first_open_at TEXT")
            c.execute("ALTER TABLE gaash_msgs ADD COLUMN first_click_at TEXT")
        # 🪪 A package can carry SEVERAL documents — a generated declaration and
        # a shared ID scan and a dealer certificate — and they ride every email
        # of the sequence, not only the first. id_doc_id stays as the first of
        # them so {id_name} and the chat pill keep resolving.
        if "docs_json" not in _columns(c, "gaash_threads"):
            c.execute("ALTER TABLE gaash_threads ADD COLUMN docs_json TEXT")
        # 📧 Owner replies written in Gmail itself land in [Gmail]/Sent Mail,
        # never INBOX — a second per-account UID cursor tracks that folder.
        if "sent_last_uid" not in _columns(c, "gaash_accounts"):
            c.execute("ALTER TABLE gaash_accounts ADD COLUMN sent_uidvalidity INTEGER")
            c.execute("ALTER TABLE gaash_accounts ADD COLUMN sent_last_uid INTEGER")
        # 🪪 The document library splits into folders: reusable IDs vs the
        # per-package declaration papers. Existing rows are sorted once, by name —
        # the only signal there is — and anything ambiguous lands in 'id', which
        # is the safe side: a declaration filed as an ID is visible and movable,
        # a passport hidden among declarations is not.
        if "folder" not in _columns(c, "gaash_ids"):
            c.execute("ALTER TABLE gaash_ids ADD COLUMN folder TEXT")
            c.execute("UPDATE gaash_ids SET folder = CASE "
                      "WHEN lower(name) LIKE '%declaration%' "
                      "  OR name LIKE '%تصريح%' OR name LIKE '%بيان%' "
                      "THEN 'declaration' ELSE 'id' END")
        # 📝 Template picker columns: who wrote it, and when it was last actually
        # used. Both are NULL on rows that predate this — unknowable after the
        # fact, so the picker shows "—" rather than inventing a name or a date.
        if "created_by" not in _columns(c, "gaash_templates"):
            c.execute("ALTER TABLE gaash_templates ADD COLUMN created_by TEXT")
            c.execute("ALTER TABLE gaash_templates ADD COLUMN last_used_at TEXT")
        # ⚙️ Workflows page: HubSpot-style On/Off (paused stops sending AND
        # trigger enrollment) + a free-text description column.
        if "paused" not in _columns(c, "gaash_sequences"):
            c.execute("ALTER TABLE gaash_sequences ADD COLUMN paused INTEGER NOT NULL DEFAULT 0")
            c.execute("ALTER TABLE gaash_sequences ADD COLUMN description TEXT")
        # Seed business #1 (owner of all pre-tenancy data) exactly once.
        if c.execute("SELECT COUNT(*) n FROM businesses").fetchone()["n"] == 0:
            c.execute("INSERT INTO businesses (id, name, slug, active, created_at) "
                      "VALUES (1, ?, ?, 1, ?)", ("Otlobly", "otlobly", now_iso()))
        # Backfill untagged rows — ONLY while sole-tenant (guards against silently
        # mis-assigning a stray row to business 1 after real tenants exist).
        if c.execute("SELECT COUNT(*) n FROM businesses").fetchone()["n"] == 1:
            for table in TENANT_TABLES:
                c.execute(f"UPDATE {table} SET business_id=1 WHERE business_id IS NULL")


# --------------------------------------------------------------------------- #
# Businesses (tenants)
# --------------------------------------------------------------------------- #
def list_businesses():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name, slug, active, created_at FROM businesses ORDER BY id")]


def get_business(business_id):
    with connect() as c:
        r = c.execute("SELECT * FROM businesses WHERE id=?", (business_id,)).fetchone()
        return dict(r) if r else None


def create_business(name, slug=None):
    with connect() as c:
        cur = c.execute("INSERT INTO businesses (name, slug, active, created_at, data_json) "
                        "VALUES (?,?,1,?,'{}')", (name, slug, now_iso()))
        return cur.lastrowid


def get_business_config(business_id, key=None, default=None):
    """Per-business config (markup, ils_rate, whatsapp, name, logo…) from data_json.
    key=None returns the whole dict. Powers roadmap #2 (white-label)."""
    b = get_business(business_id)
    conf = {}
    if b and b.get("data_json"):
        try:
            conf = json.loads(b["data_json"])
        except (ValueError, TypeError):
            conf = {}
    return conf if key is None else conf.get(key, default)


def set_business_config(business_id, key, value):
    conf = get_business_config(business_id)
    conf[key] = value
    with connect() as c:
        c.execute("UPDATE businesses SET data_json=? WHERE id=?",
                  (json.dumps(conf, ensure_ascii=False), business_id))


# --------------------------------------------------------------------------- #
# Quota metering (Tatabu tiers): per-action usage counters + live row counts.
# --------------------------------------------------------------------------- #
def bump_usage(business_id, resource, period, n=1):
    """Increment a metered counter (e.g. SerpAPI 'searches' for the current month)."""
    with connect() as c:
        c.execute("""INSERT INTO usage_counters (business_id, resource, period, count)
                     VALUES (?,?,?,?)
                     ON CONFLICT(business_id, resource, period)
                       DO UPDATE SET count = count + excluded.count""",
                  (business_id, resource, period, n))


def get_usage(business_id, resource, period):
    with connect() as c:
        r = c.execute("SELECT count FROM usage_counters WHERE business_id=? AND resource=? "
                      "AND period=?", (business_id, resource, period)).fetchone()
        return r["count"] if r else 0


def count_rows(table, business_id):
    """COUNT(*) of a tenant table's rows for one business (orders / customers / …)."""
    with connect() as c:
        return c.execute(f"SELECT COUNT(*) n FROM {table} WHERE business_id=?",
                         (business_id,)).fetchone()["n"]


def count_active_users(business_id):
    with connect() as c:
        return c.execute("SELECT COUNT(*) n FROM users WHERE business_id=? AND active=1",
                         (business_id,)).fetchone()["n"]


# --------------------------------------------------------------------------- #
# Code generators (OTL-#### / CUS-####)
# --------------------------------------------------------------------------- #
def _next_code(c, table, col, prefix):
    rows = c.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ?",
                     (prefix + "%",)).fetchall()
    n = 0
    for r in rows:
        try:
            n = max(n, int((r[0] or "").split("-")[-1]))
        except (ValueError, IndexError):
            pass
    return f"{prefix}-{n + 1:04d}"


# --------------------------------------------------------------------------- #
# Orders — store/return the full order dict (shape matches store.py)
# --------------------------------------------------------------------------- #
def list_orders():
    with connect() as c:
        return [json.loads(r["data_json"])
                for r in c.execute("SELECT data_json FROM orders WHERE business_id=? "
                                   "ORDER BY order_code", (current_business(),))]


def get_order(order_code):
    with connect() as c:
        r = c.execute("SELECT data_json FROM orders WHERE order_code=? AND business_id=?",
                      (order_code, current_business())).fetchone()
        return json.loads(r["data_json"]) if r else None


def orders_business_index():
    """[(business_id, order_dict)] across ALL businesses — UNSCOPED. Used ONLY by the
    public customer portal to resolve which broker a phone/OTL belongs to (there's no
    staff login there to pin the tenant). Never use this for staff-side reads."""
    with connect() as c:
        return [(r["business_id"], json.loads(r["data_json"]))
                for r in c.execute("SELECT business_id, data_json FROM orders")]


def upsert_order(order, created_by=None):
    """Insert or replace by order_code. `order` is the full store-shape dict."""
    ph = (order.get("customer", {}).get("phones") or [{}])
    phone = ph[0].get("e164") if ph and ph[0] else None
    with connect() as c:
        # business_id is set ONLY on insert; ON CONFLICT preserves the row's owner
        # (never re-homes an existing order to a different tenant).
        c.execute("""INSERT INTO orders
          (order_code, customer_phone, status, amount_to_collect_usd, batch,
           profile_box, amazon_order_number, tracking_number, signature,
           created_at, updated_at, created_by, data_json, business_id)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(order_code) DO UPDATE SET
            customer_phone=excluded.customer_phone, status=excluded.status,
            amount_to_collect_usd=excluded.amount_to_collect_usd, batch=excluded.batch,
            profile_box=excluded.profile_box,
            amazon_order_number=excluded.amazon_order_number,
            tracking_number=excluded.tracking_number, updated_at=excluded.updated_at,
            data_json=excluded.data_json""",
                  (order["order_id"], phone, order["status"],
                   order.get("amount_to_collect_usd"), order.get("batch"),
                   order.get("profile_box"), order.get("amazon_order_number"),
                   order.get("tracking_number"), order.get("signature"),
                   order.get("created_at", now_iso()), now_iso(), created_by,
                   json.dumps(order, ensure_ascii=False), current_business()))
        # refresh item rows for portal/search
        c.execute("DELETE FROM order_items WHERE order_code=?", (order["order_id"],))
        for it in order.get("items", []):
            c.execute("INSERT INTO order_items (order_code, asin, clean_url) VALUES (?,?,?)",
                      (order["order_id"], it.get("asin"), it.get("clean_url")))
    return order


def update_order(order_code, changes, actor=None):
    """Apply field changes to an order's data_json + columns. Returns the order."""
    o = get_order(order_code)
    if not o:
        return None
    o.update(changes)
    o["updated_at"] = now_iso()
    upsert_order(o)
    if actor:
        audit(actor, "update_order", "order", order_code,
              ", ".join(f"{k}={v}" for k, v in changes.items()))
    return o


def delete_order(order_code):
    """Hard-delete an order row (+ its item rows). Callers copy the order into
    Trash first, so it stays recoverable there."""
    with connect() as c:
        c.execute("DELETE FROM order_items WHERE order_code=?", (order_code,))
        c.execute("DELETE FROM orders WHERE order_code=? AND business_id=?",
                  (order_code, current_business()))


def next_order_code():
    with connect() as c:
        return _next_code(c, "orders", "order_code", "OTL")


def insert_new_order(order, created_by=None):
    """Persist a BRAND-NEW order, allocating its OTL-#### code atomically.

    Unlike upsert_order (which does INSERT ... ON CONFLICT DO UPDATE and is for
    edits to an existing order), this allocates the next code and INSERTs in ONE
    write transaction (BEGIN IMMEDIATE serialises concurrent creators), using a
    PLAIN insert. So if two public submissions race for the same code, the loser
    raises IntegrityError and retries with a freshly-computed code instead of
    silently overwriting the winner's order. Mutates order['order_id'] to the code
    actually used and returns the order."""
    ph = (order.get("customer", {}).get("phones") or [{}])
    phone = ph[0].get("e164") if ph and ph[0] else None
    conn = connect()
    try:
        conn.isolation_level = None          # take manual control of the transaction
        for _ in range(50):
            try:
                conn.execute("BEGIN IMMEDIATE")   # one writer at a time: alloc + insert atomic
                code = _next_code(conn, "orders", "order_code", "OTL")
                order["order_id"] = code
                conn.execute(
                    """INSERT INTO orders
                      (order_code, customer_phone, status, amount_to_collect_usd, batch,
                       profile_box, amazon_order_number, tracking_number, signature,
                       created_at, updated_at, created_by, data_json, business_id)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, phone, order["status"], order.get("amount_to_collect_usd"),
                     order.get("batch"), order.get("profile_box"),
                     order.get("amazon_order_number"), order.get("tracking_number"),
                     order.get("signature"), order.get("created_at", now_iso()),
                     now_iso(), created_by, json.dumps(order, ensure_ascii=False),
                     current_business()))
                conn.execute("DELETE FROM order_items WHERE order_code=?", (code,))
                for it in order.get("items", []):
                    conn.execute("INSERT INTO order_items (order_code, asin, clean_url) "
                                 "VALUES (?,?,?)", (code, it.get("asin"), it.get("clean_url")))
                conn.execute("COMMIT")
                return order
            except sqlite3.IntegrityError:       # code taken between alloc and insert — retry
                conn.execute("ROLLBACK")
        raise RuntimeError("could not allocate a unique order code after 50 tries")
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
def list_customers():
    with connect() as c:
        return [json.loads(r["data_json"])
                for r in c.execute("SELECT data_json FROM customers WHERE business_id=? "
                                   "ORDER BY customer_code", (current_business(),))]


def get_customer(customer_code):
    with connect() as c:
        r = c.execute("SELECT data_json FROM customers WHERE customer_code=? AND business_id=?",
                      (customer_code, current_business())).fetchone()
        return json.loads(r["data_json"]) if r else None


def upsert_customer(cust):
    # business_id tagged on insert (preserved on conflict). NOTE: match_key is still
    # globally UNIQUE, so two businesses can't yet share a customer phone — a
    # per-business unique index is a hardening to add before onboarding a 2nd tenant.
    with connect() as c:
        c.execute("""INSERT INTO customers
          (customer_code, match_key, name, whatsapp, city, vip,
           created_at, updated_at, data_json, business_id)
          VALUES (?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(match_key) DO UPDATE SET
            name=excluded.name, whatsapp=excluded.whatsapp, city=excluded.city,
            vip=excluded.vip, updated_at=excluded.updated_at,
            data_json=excluded.data_json""",
                  (cust["customer_id"], cust.get("match_key"), cust.get("name"),
                   cust.get("whatsapp"), cust.get("city"), 1 if cust.get("vip") else 0,
                   cust.get("created_at", now_iso()), now_iso(),
                   json.dumps(cust, ensure_ascii=False), current_business()))
    return cust


def next_customer_code():
    with connect() as c:
        return _next_code(c, "customers", "customer_code", "CUS")


# ---- customer email login (portal) ---------------------------------------- #
def get_customer_by_email(email, business_id=None):
    """The customer row owning this email (case-blind) within the CURRENT business —
    id/whatsapp/name/email columns only, or None."""
    if business_id is None:
        business_id = current_business()
    with connect() as c:
        r = c.execute("SELECT id, customer_code, name, whatsapp, email, email_verified_at "
                      "FROM customers WHERE lower(email)=lower(?) AND business_id=?",
                      ((email or "").strip(), business_id)).fetchone()
        return dict(r) if r else None


def find_business_for_email(email):
    """Which business a verified customer email belongs to — searched across ALL
    tenants (public portal has no staff login). Returns a business_id or None."""
    e = (email or "").strip()
    if not e:
        return None
    with connect() as c:
        r = c.execute("SELECT business_id FROM customers "
                      "WHERE lower(email)=lower(?) AND email IS NOT NULL AND email<>'' "
                      "ORDER BY id DESC LIMIT 1", (e,)).fetchone()
        return r["business_id"] if r else None


def set_customer_email(row_id, email, verified_at):
    """Attach a verified email to a customer row. False when another customer
    already owns it (unique-index race)."""
    try:
        with connect() as c:
            c.execute("UPDATE customers SET email=?, email_verified_at=?, updated_at=? "
                      "WHERE id=?", ((email or "").strip(), verified_at, now_iso(), row_id))
        return True
    except sqlite3.IntegrityError:
        return False


def set_customer_whatsapp(row_id, e164):
    """Repair/normalise the phone column only (never touches data_json). Used when a
    verified login proves the proper E.164 for a row stored in a looser format."""
    with connect() as c:
        c.execute("UPDATE customers SET whatsapp=?, updated_at=? WHERE id=?",
                  (e164, now_iso(), row_id))


def set_customer_password(row_id, pw_hash):
    """Set the portal password hash (manual-account login). Applied only AFTER the
    phone was verified by SMS — never from an unverified signup claim."""
    with connect() as c:
        c.execute("UPDATE customers SET password_hash=?, updated_at=? WHERE id=?",
                  (pw_hash, now_iso(), row_id))


def get_customer_password_hash(row_id):
    with connect() as c:
        r = c.execute("SELECT password_hash FROM customers WHERE id=?", (row_id,)).fetchone()
        return (r["password_hash"] or "") if r else ""


def list_customer_login_rows(business_id=None):
    """Slim rows for phone→customer matching in the portal (no data_json parse),
    within the CURRENT business."""
    if business_id is None:
        business_id = current_business()
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, customer_code, name, whatsapp, email, email_verified_at "
            "FROM customers WHERE business_id=?", (business_id,))]


# --------------------------------------------------------------------------- #
# Catalog (staff-curated products the public /catalog page sells)
# --------------------------------------------------------------------------- #
def list_catalog(business_id=1, active_only=False):
    q = "SELECT * FROM catalog_items WHERE business_id=?"
    if active_only:
        q += " AND active=1"
    q += " ORDER BY sort, id DESC"
    with connect() as c:
        return [dict(r) for r in c.execute(q, (business_id,))]


def get_catalog_item(item_id, business_id=1):
    with connect() as c:
        r = c.execute("SELECT * FROM catalog_items WHERE id=? AND business_id=?",
                      (item_id, business_id)).fetchone()
        return dict(r) if r else None


def add_catalog_item(d, business_id=1):
    with connect() as c:
        cur = c.execute("""INSERT INTO catalog_items
          (business_id, asin, amazon_url, title, image, base_price_usd, price_usd,
           category, note, active, sort, created_at, updated_at, created_by)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (business_id, d.get("asin"), d.get("amazon_url"), d.get("title"),
           d.get("image"), d.get("base_price_usd"), d.get("price_usd"),
           d.get("category"), d.get("note"), 1 if d.get("active", True) else 0,
           d.get("sort") or 0, now_iso(), now_iso(), d.get("created_by")))
        return cur.lastrowid


CATALOG_EDITABLE = {"title", "image", "price_usd", "base_price_usd",
                    "category", "note", "active", "sort"}


def update_catalog_item(item_id, changes, business_id=1):
    sets = {k: v for k, v in (changes or {}).items() if k in CATALOG_EDITABLE}
    if not sets:
        return False
    cols = ", ".join(f"{k}=?" for k in sets)
    with connect() as c:
        c.execute(f"UPDATE catalog_items SET {cols}, updated_at=? "
                  "WHERE id=? AND business_id=?",
                  (*sets.values(), now_iso(), item_id, business_id))
    return True


def delete_catalog_item(item_id, business_id=1):
    with connect() as c:
        c.execute("DELETE FROM catalog_items WHERE id=? AND business_id=?",
                  (item_id, business_id))


# --------------------------------------------------------------------------- #
# Users + audit + settings
# --------------------------------------------------------------------------- #
def create_user(username, password_hash, role, name="", business_id=None):
    with connect() as c:
        c.execute("""INSERT INTO users (username, password_hash, role, name, created_at, business_id)
                     VALUES (?,?,?,?,?,?)""",
                  (username, password_hash, role, name, now_iso(), business_id))


def get_user(username):
    # Login is case-insensitive on the username — people forget capitalisation
    # (e.g. account created as "Sara" but they type "sara"). Only used by auth.verify().
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1",
                      ((username or "").strip(),)).fetchone()
        return dict(r) if r else None


def get_user_by_id(uid):
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None


def list_users(business_id=None):
    """All users, or one tenant's (COALESCE: pre-tenancy rows have NULL = business 1)."""
    with connect() as c:
        if business_id is None:
            return [dict(r) for r in c.execute(
                "SELECT id, username, role, name, active, created_at FROM users ORDER BY id")]
        return [dict(r) for r in c.execute(
            "SELECT id, username, role, name, active, created_at FROM users "
            "WHERE COALESCE(business_id, 1)=? ORDER BY id", (business_id,))]


def count_users():
    with connect() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def set_user_active(uid, active):
    with connect() as c:
        c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))


def set_user_password(uid, password_hash):
    with connect() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, uid))


def get_setting(key, default=None):
    with connect() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except (ValueError, TypeError):
            return r["value"]


def set_setting(key, value):
    with connect() as c:
        c.execute("""INSERT INTO settings (key, value) VALUES (?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                  (key, json.dumps(value, ensure_ascii=False)))


def claim_once(key):
    """Atomically claim a one-time task. Returns True for EXACTLY ONE caller — even
    across the two gunicorn workers both importing app.py at boot — and False for
    everyone after. The settings PK makes the first INSERT win and the rest raise
    IntegrityError. Used to run the legacy backfills once per deploy, not per worker."""
    with connect() as c:
        try:
            c.execute("INSERT INTO settings (key, value) VALUES (?,?)",
                      (key, json.dumps(now_iso())))
            return True
        except sqlite3.IntegrityError:
            return False


def log_leluxe_status(row_id, old, new, source="app", c=None):
    """Record a Leluxe status TRANSITION (never initial statuses — an import of an
    already-'sent rd' item must not stamp import day as its done date). The goal
    dashboard dates RD completions by the first transition into an rd-done status."""
    if (old or "") == (new or ""):
        return
    row = (row_id, old, new, now_iso(), source)
    sql = ("INSERT INTO leluxe_status_log (row_id, old_status, new_status, ts, source) "
           "VALUES (?,?,?,?,?)")
    if c is not None:
        c.execute(sql, row)
        return
    with connect() as conn:
        conn.execute(sql, row)


def gash_run_start(actor="", source="check"):
    """Open a run and return its id — the header the popup groups changes under."""
    with connect() as c:
        cur = c.execute("INSERT INTO gash_runs (at, actor, source) VALUES (?,?,?)",
                        (now_iso(), actor or "", source))
        return cur.lastrowid


def gash_run_close(run_id, checked, changed):
    with connect() as c:
        c.execute("UPDATE gash_runs SET checked=?, changed=? WHERE id=?",
                  (int(checked or 0), int(changed or 0), run_id))


def log_gash_change(run_id, gwd, old, new, *, name="", code="",
                    order_status="", store="leluxe"):
    """Record ONE parcel moving from `old` to `new`. Transitions only — a first
    sighting (no old value) still counts, but old == new never does, or a
    re-check with nothing new would fill the log with noise."""
    if (old or "") == (new or "") or not (new or "").strip():
        return False
    with connect() as c:
        c.execute("""INSERT INTO gash_status_log
                     (run_id, at, gwd, name, code, order_status,
                      old_status, new_status, store)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (run_id, now_iso(), gwd, name or "", code or "",
                   order_status or "", old or "", new or "", store))
    return True


def gash_changes(days=14):
    """Recent runs + the changes they made, newest first."""
    cut = (datetime.now(timezone.utc) - timedelta(days=int(days or 14))) \
        .astimezone().isoformat(timespec="seconds")
    with connect() as c:
        runs = [dict(r) for r in c.execute(
            "SELECT * FROM gash_runs WHERE at>=? ORDER BY at DESC", (cut,))]
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM gash_status_log WHERE at>=? ORDER BY at DESC", (cut,))]
    return {"runs": runs, "changes": rows}


def audit(actor, action, entity, entity_id, detail=""):
    with connect() as c:
        c.execute("""INSERT INTO audit_log
          (ts, user_id, username, action, entity, entity_id, detail)
          VALUES (?,?,?,?,?,?,?)""",
                  (now_iso(), (actor or {}).get("id"), (actor or {}).get("username"),
                   action, entity, str(entity_id), detail))


# --------------------------------------------------------------------------- #
# Meta leads (Messenger/IG DMs + lead-ad forms)
# --------------------------------------------------------------------------- #
def upsert_lead(lead):
    """Insert or refresh a lead from a Meta sync. NEVER overwrites the human-set
    status / assigned_to / note / order_id (those are the team's work)."""
    with connect() as c:
        c.execute("""
          INSERT INTO meta_leads (lead_id, source, name, phone, email, form_id, form_name,
            ad_name, last_message, last_activity, created_time, response_min, synced_at, business_id)
          VALUES (:lead_id,:source,:name,:phone,:email,:form_id,:form_name,
            :ad_name,:last_message,:last_activity,:created_time,:response_min,:synced_at,:business_id)
          ON CONFLICT(lead_id) DO UPDATE SET
            source=excluded.source, name=excluded.name,
            phone=COALESCE(excluded.phone, meta_leads.phone),
            email=COALESCE(excluded.email, meta_leads.email),
            form_name=excluded.form_name, ad_name=excluded.ad_name,
            last_message=excluded.last_message, last_activity=excluded.last_activity,
            response_min=COALESCE(excluded.response_min, meta_leads.response_min),
            synced_at=excluded.synced_at
        """, {**{k: lead.get(k) for k in ("lead_id", "source", "name", "phone", "email",
                                          "form_id", "form_name", "ad_name", "last_message",
                                          "last_activity", "created_time", "response_min")},
              "synced_at": now_iso(), "business_id": current_business()})


def list_leads(status=None, source=None):
    q = ("SELECT l.*, u.name AS assignee_name FROM meta_leads l "
         "LEFT JOIN users u ON u.id=l.assigned_to")
    conds, args = ["l.business_id=?"], [current_business()]
    if status:
        conds.append("l.status=?"); args.append(status)
    if source:
        conds.append("l.source=?"); args.append(source)
    q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY l.created_time DESC LIMIT 500"
    with connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def update_lead(lead_id, changes):
    allowed = {"status", "assigned_to", "note", "order_id"}
    fields = {k: v for k, v in (changes or {}).items() if k in allowed}
    if not fields:
        return None
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE meta_leads SET {sets} WHERE lead_id=? AND business_id=?",
                  (*fields.values(), lead_id, current_business()))
        r = c.execute("SELECT * FROM meta_leads WHERE lead_id=? AND business_id=?",
                      (lead_id, current_business())).fetchone()
        return dict(r) if r else None


def get_lead(lead_id):
    with connect() as c:
        r = c.execute("SELECT * FROM meta_leads WHERE lead_id=? AND business_id=?",
                      (lead_id, current_business())).fetchone()
        return dict(r) if r else None


def lead_stats():
    biz = current_business()
    with connect() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM meta_leads WHERE business_id=? "
                         "GROUP BY status", (biz,)).fetchall()
        by = {r["status"]: r["n"] for r in rows}
        total = sum(by.values())
        conv, lost = by.get("converted", 0), by.get("lost", 0)
        avg = c.execute("SELECT AVG(response_min) a FROM meta_leads WHERE response_min IS NOT NULL "
                        "AND business_id=?", (biz,)).fetchone()["a"]
        team = c.execute("""SELECT u.name nm,
                              SUM(CASE WHEN l.status='converted' THEN 1 ELSE 0 END) conv,
                              COUNT(*) tot
                            FROM meta_leads l JOIN users u ON u.id=l.assigned_to
                            WHERE l.business_id=?
                            GROUP BY l.assigned_to ORDER BY conv DESC""", (biz,)).fetchall()
    return {
        "total": total, "new": by.get("new", 0), "contacted": by.get("contacted", 0),
        "converted": conv, "lost": lost,
        "close_rate": round(conv / (conv + lost) * 100) if (conv + lost) else 0,
        "avg_response_min": round(avg, 1) if avg is not None else None,
        "by_assignee": [{"name": r["nm"], "converted": r["conv"], "total": r["tot"]} for r in team],
    }


# --------------------------------------------------------------------------- #
# Payments / deposits ledger (عربون). amount_usd + fx_rate are FROZEN per row.
# --------------------------------------------------------------------------- #
def add_payment(p):
    """Insert one ledger row (deposit/refund/collect). Returns its id."""
    with connect() as c:
        cur = c.execute("""INSERT INTO payments
          (ts, paid_at, order_code, customer_phone, customer_name, kind, currency,
           amount_entered, fx_rate, amount_usd, note, created_by, created_by_name, business_id)
          VALUES (:ts,:paid_at,:order_code,:customer_phone,:customer_name,:kind,:currency,
           :amount_entered,:fx_rate,:amount_usd,:note,:created_by,:created_by_name,:business_id)""",
          {"ts": now_iso(), "paid_at": p.get("paid_at") or now_iso()[:10],
           "order_code": p.get("order_code"), "customer_phone": p.get("customer_phone"),
           "customer_name": p.get("customer_name"), "kind": p.get("kind") or "deposit",
           "currency": p.get("currency") or "ILS", "amount_entered": p.get("amount_entered"),
           "fx_rate": p.get("fx_rate"), "amount_usd": p.get("amount_usd"),
           "note": p.get("note"), "created_by": p.get("created_by"),
           "created_by_name": p.get("created_by_name"), "business_id": current_business()})
        return cur.lastrowid


def list_payments(order_code=None, customer_phone=None):
    q = "SELECT * FROM payments"
    conds, args = ["business_id=?"], [current_business()]
    if order_code:
        conds.append("order_code=?"); args.append(order_code)
    if customer_phone:
        conds.append("customer_phone=?"); args.append(customer_phone)
    q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY paid_at DESC, id DESC LIMIT 1000"
    with connect() as c:
        return [dict(r) for r in c.execute(q, args)]


def get_payment(pid):
    with connect() as c:
        r = c.execute("SELECT * FROM payments WHERE id=? AND business_id=?",
                      (pid, current_business())).fetchone()
        return dict(r) if r else None


def delete_payment(pid):
    with connect() as c:
        c.execute("DELETE FROM payments WHERE id=? AND business_id=?",
                  (pid, current_business()))


# --------------------------------------------------------------------------- #
# Gerizim registrations — "this GWD parcel was registered on postgerizim".
# The truth lives in the owner's local GAASH tool; his browser mirrors that
# tool's done[] set here so the sign shows everywhere (Faisal / live / phone),
# not just on the Mac. Keyed by GWD; ok=0 means Gerizim flagged the submission.
# --------------------------------------------------------------------------- #
def list_gerizim_registered():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT tracking, registered_at, ok FROM gerizim_registrations "
            "WHERE business_id=? ORDER BY registered_at DESC", (current_business(),))]


def sync_gerizim_registered(rows):
    """Authoritative full sync of the registered-GWD set for this business:
    delete rows no longer present, upsert the posted ones. `rows` is a list of
    {tracking, registered_at, ok}. Callers guard against empty/partial pushes so
    this never wipes the store from a failed load."""
    biz = current_business()
    keep = [str(r.get("tracking") or "").strip() for r in rows]
    keep = [t for t in keep if t]
    with connect() as c:
        if keep:
            ph = ",".join("?" * len(keep))
            c.execute(f"DELETE FROM gerizim_registrations WHERE business_id=? "
                      f"AND tracking NOT IN ({ph})", (biz, *keep))
        else:
            c.execute("DELETE FROM gerizim_registrations WHERE business_id=?", (biz,))
        for r in rows:
            t = str(r.get("tracking") or "").strip()
            if not t:
                continue
            c.execute("""INSERT INTO gerizim_registrations
                 (tracking, registered_at, ok, business_id, updated_at)
                 VALUES (?,?,?,?,?)
                 ON CONFLICT(tracking) DO UPDATE SET
                   registered_at=excluded.registered_at, ok=excluded.ok,
                   business_id=excluded.business_id, updated_at=excluded.updated_at""",
                (t, r.get("registered_at"), 1 if r.get("ok", 1) else 0, biz, now_iso()))


# --------------------------------------------------------------------------- #
# Leluxe per-package clearance mail — "did GAASH answer about this GWD yet?"
# One row per GWD: last send + a hand-set replied stamp (mailto: flow, so the
# app never sees the inbox). A resend restarts the cycle (clears replied_at).
# Owner-only like the rest of the leluxe_* tables — no business_id.
# --------------------------------------------------------------------------- #
def pkg_mail_all():
    with connect() as c:
        return {r["gwd"]: dict(r) for r in c.execute(
            "SELECT gwd, to_email, subject, sent_at, sent_count, replied_at "
            "FROM leluxe_pkg_mail")}


def pkg_mail_sent(gwd, to_email=None, subject=None):
    """Stamp one send for this GWD (insert or restart the cycle)."""
    gwd = str(gwd or "").strip()
    if not gwd:
        return None
    with connect() as c:
        c.execute("""INSERT INTO leluxe_pkg_mail
             (gwd, to_email, subject, sent_at, sent_count, replied_at)
             VALUES (?,?,?,?,1,NULL)
             ON CONFLICT(gwd) DO UPDATE SET
               to_email=excluded.to_email, subject=excluded.subject,
               sent_at=excluded.sent_at, sent_count=leluxe_pkg_mail.sent_count+1,
               replied_at=NULL""", (gwd, to_email, subject, now_iso()))
        return dict(c.execute("SELECT gwd, to_email, subject, sent_at, sent_count, "
                              "replied_at FROM leluxe_pkg_mail WHERE gwd=?",
                              (gwd,)).fetchone())


def pkg_mail_reply(gwd, replied=True):
    """Owner marks (or unmarks) the GAASH reply for this GWD by hand."""
    gwd = str(gwd or "").strip()
    if not gwd:
        return None
    with connect() as c:
        cur = c.execute("UPDATE leluxe_pkg_mail SET replied_at=? WHERE gwd=?",
                        (now_iso() if replied else None, gwd))
        if not cur.rowcount:
            return None
        return dict(c.execute("SELECT gwd, to_email, subject, sent_at, sent_count, "
                              "replied_at FROM leluxe_pkg_mail WHERE gwd=?",
                              (gwd,)).fetchone())


def set_payment_order(pid, order_code):
    """Attach a customer-level deposit to an order after the fact (self-heal)."""
    with connect() as c:
        c.execute("UPDATE payments SET order_code=? WHERE id=? AND business_id=?",
                  (order_code, pid, current_business()))


def deposit_total_for_order(order_code):
    """Net deposit held against one order = deposits + collects − refunds (USD)."""
    with connect() as c:
        rows = c.execute("SELECT kind, amount_usd FROM payments WHERE order_code=? "
                         "AND business_id=?", (order_code, current_business())).fetchall()
    return money.usd_sum(-(r["amount_usd"] or 0) if r["kind"] == "refund"
                         else (r["amount_usd"] or 0) for r in rows)


if __name__ == "__main__":
    if "--init" in sys.argv:
        init_db()
        print(f"Initialized {DB_FILE.name}")
    elif "--stats" in sys.argv:
        with connect() as c:
            for t in ("businesses", "users", "customers", "orders", "order_items",
                      "audit_log", "meta_leads", "payments", "gerizim_registrations"):
                n = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                print(f"  {t:12} {n}")
    else:
        print(__doc__)
