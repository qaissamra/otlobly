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

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import data_path

# OTLOBLY_DB pins an exact file; otherwise it lives in the data dir (the project
# folder locally, or the persistent disk in a hosted deploy via OTLOBLY_DATA_DIR).
DB_FILE = Path(os.environ.get("OTLOBLY_DB") or data_path("otlobly.db"))

SCHEMA = """
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
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders(customer_id);
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
                for r in c.execute("SELECT data_json FROM orders ORDER BY order_code")]


def get_order(order_code):
    with connect() as c:
        r = c.execute("SELECT data_json FROM orders WHERE order_code=?",
                      (order_code,)).fetchone()
        return json.loads(r["data_json"]) if r else None


def upsert_order(order, created_by=None):
    """Insert or replace by order_code. `order` is the full store-shape dict."""
    ph = (order.get("customer", {}).get("phones") or [{}])
    phone = ph[0].get("e164") if ph and ph[0] else None
    with connect() as c:
        c.execute("""INSERT INTO orders
          (order_code, customer_phone, status, amount_to_collect_usd, batch,
           profile_box, amazon_order_number, tracking_number, signature,
           created_at, updated_at, created_by, data_json)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                   json.dumps(order, ensure_ascii=False)))
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
    """Hard-delete an order row (+ its item rows). Used after the sheet-sync prune
    has already copied the order into Trash, so it stays recoverable there."""
    with connect() as c:
        c.execute("DELETE FROM order_items WHERE order_code=?", (order_code,))
        c.execute("DELETE FROM orders WHERE order_code=?", (order_code,))


def next_order_code():
    with connect() as c:
        return _next_code(c, "orders", "order_code", "OTL")


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
def list_customers():
    with connect() as c:
        return [json.loads(r["data_json"])
                for r in c.execute("SELECT data_json FROM customers ORDER BY customer_code")]


def get_customer(customer_code):
    with connect() as c:
        r = c.execute("SELECT data_json FROM customers WHERE customer_code=?",
                      (customer_code,)).fetchone()
        return json.loads(r["data_json"]) if r else None


def upsert_customer(cust):
    with connect() as c:
        c.execute("""INSERT INTO customers
          (customer_code, match_key, name, whatsapp, city, vip,
           created_at, updated_at, data_json)
          VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(match_key) DO UPDATE SET
            name=excluded.name, whatsapp=excluded.whatsapp, city=excluded.city,
            vip=excluded.vip, updated_at=excluded.updated_at,
            data_json=excluded.data_json""",
                  (cust["customer_id"], cust.get("match_key"), cust.get("name"),
                   cust.get("whatsapp"), cust.get("city"), 1 if cust.get("vip") else 0,
                   cust.get("created_at", now_iso()), now_iso(),
                   json.dumps(cust, ensure_ascii=False)))
    return cust


def next_customer_code():
    with connect() as c:
        return _next_code(c, "customers", "customer_code", "CUS")


# --------------------------------------------------------------------------- #
# Users + audit + settings
# --------------------------------------------------------------------------- #
def create_user(username, password_hash, role, name=""):
    with connect() as c:
        c.execute("""INSERT INTO users (username, password_hash, role, name, created_at)
                     VALUES (?,?,?,?,?)""",
                  (username, password_hash, role, name, now_iso()))


def get_user(username):
    with connect() as c:
        r = c.execute("SELECT * FROM users WHERE username=? AND active=1",
                      (username,)).fetchone()
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


def audit(actor, action, entity, entity_id, detail=""):
    with connect() as c:
        c.execute("""INSERT INTO audit_log
          (ts, user_id, username, action, entity, entity_id, detail)
          VALUES (?,?,?,?,?,?,?)""",
                  (now_iso(), (actor or {}).get("id"), (actor or {}).get("username"),
                   action, entity, str(entity_id), detail))


if __name__ == "__main__":
    if "--init" in sys.argv:
        init_db()
        print(f"Initialized {DB_FILE.name}")
    elif "--stats" in sys.argv:
        with connect() as c:
            for t in ("users", "customers", "orders", "order_items", "audit_log"):
                n = c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                print(f"  {t:12} {n}")
    else:
        print(__doc__)
