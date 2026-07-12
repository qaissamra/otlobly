#!/usr/bin/env python3
"""
Self-checks for the multi-tenant customer portal (Tatabu Phase 6).

The public portal has no staff login, so it resolves which broker a customer
belongs to from their phone/email (across ALL businesses), then scopes to it. This
proves: a broker's customer logs in on the shared page and sees ONLY their broker's
orders — never Otlobly's — and Otlobly's own customers are unchanged.

    ./.venv/bin/python test_customer_portal.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-portal-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_OTP_DEV"] = "1"          # dev returns the code in the response
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import db              # noqa: E402
import normalize       # noqa: E402

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


def login_and_orders(phone_local):
    """OTP-login a customer on the shared portal, return their visible order ids."""
    c = appmod.app.test_client()
    code = c.post("/api/customer/otp/request", json={"phone": phone_local}).get_json().get("dev_code")
    if not code:
        return None, None
    c.post("/api/customer/otp/verify", json={"phone": phone_local, "code": code})
    data = c.get("/api/customer/orders").get_json()
    return code, sorted(o["order_id"] for o in (data or {}).get("orders", []))


def main():
    db.init_db()

    # business #1 (Otlobly) customer, and a broker (#2) customer — distinct phones.
    db.set_current_business(1)
    ocA = db.insert_new_order(mk_order("Otlobly Cust", "+970599000001"))["order_id"]
    bid = db.create_business("Broker Co")
    db.set_current_business(bid)
    ocB = db.insert_new_order(mk_order("Broker Cust", "+970599000002"))["order_id"]
    db.set_current_business(1)

    # 1) phone → business resolution (across tenants)
    check("Otlobly phone resolves to business 1", appmod._business_for_phone("599000001") == 1)
    check("broker phone resolves to the broker", appmod._business_for_phone("599000002") == bid)
    check("unknown phone resolves to None", appmod._business_for_phone("599999999") is None)

    # 2) the broker's customer logs in and sees ONLY their order (never Otlobly's)
    codeB, oidsB = login_and_orders("0599000002")
    check("broker customer can request a code", bool(codeB))
    check("broker customer sees ONLY their own order", oidsB == [ocB])

    # 3) Otlobly's own customer is unchanged — sees only their order
    codeA, oidsA = login_and_orders("0599000001")
    check("Otlobly customer sees ONLY their own order", oidsA == [ocA])

    # 4) sanity: the two are different orders (no cross-tenant bleed)
    check("the two customers' orders are distinct", ocA != ocB and oidsA != oidsB)

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
