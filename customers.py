#!/usr/bin/env python3
"""
Customer CRM — the heart of the system. One profile per customer, keyed by their
normalized WhatsApp number, with orders + total-spent auto-derived from the order
store so nothing is double-entered.

  python3 customers.py --sync     # create/refresh profiles from existing orders
  python3 customers.py            # list profiles with order counts + total spent
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import normalize
import store
from paths import data_path

STORE_FILE = data_path("customers.json")
ID_DIR = data_path("customer_ids")     # uploaded ID images live here

PAYMENT_METHODS = ["Cash on delivery", "Bank transfer", "PayPal", "Crypto", "Other"]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load():
    if STORE_FILE.exists():
        return json.loads(STORE_FILE.read_text())
    return {"customers": [], "seq": 0}


def save(db):
    STORE_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def _key(whatsapp, name):
    """Match key: normalized phone if present, else lowercased name."""
    p = normalize.normalize_phone(whatsapp) if whatsapp else None
    if p:
        return p["e164"]
    return "name:" + (name or "").strip().lower()


def new_customer(db, *, name, whatsapp="", email="", address="", city="",
                 vip=False, notes="", payment_method="Cash on delivery",
                 id_image=""):
    db["seq"] = db.get("seq", 0) + 1
    ph = normalize.normalize_phone(whatsapp) if whatsapp else None
    return {
        "customer_id": f"CUS-{db['seq']:04d}",
        "name": (name or "").strip(),
        "whatsapp": ph["e164"] if ph else (whatsapp or "").strip(),
        "wa": ph["wa"] if ph else "",
        "email": (email or "").strip(),
        "address": (address or "").strip(),
        "city": (city or "").strip(),
        "id_image": id_image,                 # filename under customer_ids/
        "vip": bool(vip),
        "notes": (notes or "").strip(),
        "payment_method": payment_method or "Cash on delivery",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "match_key": _key(whatsapp, name),
    }


def upsert(db, cust):
    for i, ex in enumerate(db["customers"]):
        if ex.get("match_key") and ex["match_key"] == cust["match_key"]:
            cust["customer_id"] = ex["customer_id"]
            cust["created_at"] = ex.get("created_at", cust["created_at"])
            # keep an uploaded ID if the new record doesn't carry one
            if ex.get("id_image") and not cust.get("id_image"):
                cust["id_image"] = ex["id_image"]
            cust["updated_at"] = now_iso()
            db["customers"][i] = cust
            return cust, "updated"
    db["customers"].append(cust)
    return cust, "created"


def find(db, customer_id):
    return next((c for c in db["customers"] if c["customer_id"] == customer_id), None)


# --------------------------------------------------------------------------- #
# Derive orders + money from the order store (single source of truth)
# --------------------------------------------------------------------------- #
def _orders_for(orders_db, cust):
    out = []
    for o in orders_db.get("orders", []):
        ph = store.primary_phone(o)
        ckey = ph["e164"] if ph else ("name:" + o["customer"]["name"].strip().lower())
        if ckey == cust["match_key"]:
            out.append(o)
    return out


def enrich(cust, orders_db):
    """Attach live previous_orders / totals (computed, never stored)."""
    orders = _orders_for(orders_db, cust)
    spent = sum(o.get("amount_to_collect_usd") or 0
                for o in orders if o["status"] not in ("CANCELLED",))
    collected = sum(o.get("amount_to_collect_usd") or 0
                    for o in orders if o["status"] in ("DELIVERED", "COLLECTED"))
    last = max((o.get("created_at", "") for o in orders), default="")
    return {
        **cust,
        "order_count": len(orders),
        "in_to_order": any(o["status"] in ("REQUESTED", "QUOTED", "PAID", "IN_CART") for o in orders),
        "total_spent_usd": round(spent, 2),
        "collected_usd": round(collected, 2),
        "last_order_at": last,
        "previous_orders": [
            {"order_id": o["order_id"], "status": o["status"],
             "amount_to_collect_usd": o.get("amount_to_collect_usd"),
             "batch": o.get("batch"), "created_at": o.get("created_at")}
            for o in sorted(orders, key=lambda x: x.get("created_at", ""), reverse=True)
        ],
    }


def sync_from_orders(db, orders_db):
    """Create a profile for every distinct customer seen in the orders."""
    created = 0
    for o in orders_db.get("orders", []):
        ph = store.primary_phone(o)
        cust = new_customer(
            db, name=o["customer"]["name"],
            whatsapp=ph["e164"] if ph else "",
            address=o["customer"].get("address", ""),
            notes="")
        _, how = upsert(db, cust)
        created += how == "created"
    return created


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true",
                    help="Create/refresh customer profiles from existing orders.")
    args = ap.parse_args()
    db = load()
    odb = store.load()
    if args.sync:
        n = sync_from_orders(db, odb)
        save(db)
        print(f"Synced. {n} new profile(s), {len(db['customers'])} total.")
        return
    if not db["customers"]:
        print("No customers yet. Run: python3 customers.py --sync")
        return
    rows = sorted((enrich(c, odb) for c in db["customers"]),
                  key=lambda c: -c["total_spent_usd"])
    print(f"{'ID':9} {'NAME':24} {'WHATSAPP':16} {'ORDERS':>6} {'SPENT':>9}  VIP")
    for c in rows:
        print(f"{c['customer_id']:9} {(c['name'] or '—')[:24]:24} "
              f"{(c['whatsapp'] or '—'):16} {c['order_count']:>6} "
              f"{c['total_spent_usd']:>9.2f}  {'★' if c['vip'] else ''}")


if __name__ == "__main__":
    main()
