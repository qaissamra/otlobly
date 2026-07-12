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
from datetime import datetime, timezone
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
"""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
    migrate()


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
def get_customer_by_email(email, business_id=1):
    """The customer row owning this email (case-blind) — id/whatsapp/name/email
    columns only, or None."""
    with connect() as c:
        r = c.execute("SELECT id, customer_code, name, whatsapp, email, email_verified_at "
                      "FROM customers WHERE lower(email)=lower(?) AND business_id=?",
                      ((email or "").strip(), business_id)).fetchone()
        return dict(r) if r else None


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


def list_customer_login_rows(business_id=1):
    """Slim rows for phone→customer matching in the portal (no data_json parse)."""
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


def list_users():
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, username, role, name, active, created_at FROM users ORDER BY id")]


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
                      "audit_log", "meta_leads", "payments"):
                n = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                print(f"  {t:12} {n}")
    else:
        print(__doc__)
