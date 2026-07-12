#!/usr/bin/env python3
"""
Self-checks for multi-tenant data isolation (Tatabu Phase 3).

Proves that db reads/writes are scoped to the "current business": business #1
(Otlobly) sees only its own orders / customers / payments / leads, a broker
business sees only its own, and neither can fetch the other's rows by id — across
the db layer AND end-to-end through /api/report.

    ./.venv/bin/python test_isolation.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-iso-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import db          # noqa: E402
import normalize    # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def mk_order(name, phone):
    return {"order_id": "P", "status": "DELIVERED", "amount_to_collect_usd": 100.0,
            "deposit_usd": 0, "customer": {"name": name, "address": "",
                                           "phones": [normalize.normalize_phone(phone)]},
            "items": [], "signature": "sig-" + phone, "created_at": "2026-07-01T10:00:00"}


def seed(name, phone, lead_id):
    """Create one order + customer + payment + lead under the CURRENT business."""
    o = db.insert_new_order(mk_order(name, phone))
    db.upsert_customer({"customer_id": "CUS-" + phone[-4:], "match_key": phone, "name": name,
                        "whatsapp": phone, "city": "", "vip": False})
    db.add_payment({"order_code": o["order_id"], "kind": "deposit", "amount_usd": 50.0,
                    "currency": "USD", "amount_entered": 50, "fx_rate": 1.0})
    db.upsert_lead({"lead_id": lead_id, "source": "messenger", "name": name,
                    "phone": phone, "created_time": "2026-07-01T10:00:00"})
    return o["order_id"]


def main():
    db.init_db()   # seeds business #1

    db.set_current_business(1)
    oc1 = seed("Otlobly Cust", "+970599000001", "lead-otlo")

    bid = db.create_business("Broker Co")
    db.set_current_business(bid)
    oc2 = seed("Broker Cust", "+970599000002", "lead-brkr")

    # --- reads are scoped to the current business ---
    db.set_current_business(1)
    check("biz#1 sees only its 1 order", [o["order_id"] for o in db.list_orders()] == [oc1])
    check("biz#1 sees only its 1 customer",
          [c["name"] for c in db.list_customers()] == ["Otlobly Cust"])
    check("biz#1 sees only its 1 payment", len(db.list_payments()) == 1)
    check("biz#1 sees only its 1 lead", [l["lead_id"] for l in db.list_leads()] == ["lead-otlo"])

    db.set_current_business(bid)
    check("broker sees only its 1 order", [o["order_id"] for o in db.list_orders()] == [oc2])
    check("broker sees only its 1 customer",
          [c["name"] for c in db.list_customers()] == ["Broker Cust"])
    check("broker sees only its 1 payment", len(db.list_payments()) == 1)
    check("broker sees only its 1 lead", [l["lead_id"] for l in db.list_leads()] == ["lead-brkr"])

    # --- point-lookups can't cross tenants ---
    check("broker CANNOT get_order the Otlobly order", db.get_order(oc1) is None)
    check("broker CANNOT get_lead the Otlobly lead", db.get_lead("lead-otlo") is None)
    check("broker's deposit_total for Otlobly's order = 0 (isolated)",
          db.deposit_total_for_order(oc1) == 0.0)
    db.set_current_business(1)
    check("Otlobly CAN get its own order", db.get_order(oc1) is not None)
    check("Otlobly's own deposit total is 50", db.deposit_total_for_order(oc1) == 50.0)
    check("Otlobly CANNOT get the broker's order", db.get_order(oc2) is None)

    # --- end-to-end: /api/report returns only the caller's business ---
    import app as appmod
    import auth
    db.set_current_business(1)          # reset for the direct create_user calls below
    db.create_user("otlo", auth.hash_pw("secret1"), "admin", "O", business_id=1)
    db.create_user("brk", auth.hash_pw("secret1"), "admin", "B", business_id=bid)

    def orders_via_report(u):
        c = appmod.app.test_client()
        c.post("/login", data={"username": u, "password": "secret1"})
        rep = c.get("/api/report").get_json()
        return sorted(r["order_id"] for r in rep["orders"])

    check("/api/report for Otlobly → only its order", orders_via_report("otlo") == [oc1])
    check("/api/report for broker → only its order", orders_via_report("brk") == [oc2])

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
