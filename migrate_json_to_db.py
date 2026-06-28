#!/usr/bin/env python3
"""
One-time migration: orders.json + customers.json → SQLite (otlobly.db).
Idempotent (upserts), so it's safe to re-run. Leaves the JSON files untouched.

  python3 migrate_json_to_db.py
"""

import customers as cust_store
import db
import store


def _order_match_key(o):
    ph = store.primary_phone(o)
    return ph["e164"] if ph else ("name:" + o["customer"]["name"].strip().lower())


def main():
    db.init_db()

    cdb = cust_store.load()
    for c in cdb.get("customers", []):
        db.upsert_customer(c)
    print(f"customers migrated: {len(cdb.get('customers', []))}")

    odb = store.load()
    for o in odb.get("orders", []):
        db.upsert_order(o)
    print(f"orders migrated:    {len(odb.get('orders', []))}")

    # Link orders → customers by match key.
    linked = 0
    with db.connect() as conn:
        key_to_id = {r["match_key"]: r["id"]
                     for r in conn.execute("SELECT id, match_key FROM customers")}
        for o in odb.get("orders", []):
            cid = key_to_id.get(_order_match_key(o))
            if cid:
                conn.execute("UPDATE orders SET customer_id=? WHERE order_code=?",
                             (cid, o["order_id"]))
                linked += 1
    print(f"orders linked to customers: {linked}")


if __name__ == "__main__":
    main()
