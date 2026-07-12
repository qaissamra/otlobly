#!/usr/bin/env python3
"""
Self-checks for Tatabu plan tiers + quota metering (Phase 10).

Otlobly (#1) is unlimited; a broker gets a tier (default starter) with per-resource
limits; usage = live counts (orders/seats/packages) + a monthly searches counter;
soft enforcement surfaces an over-limit flag but blocks nothing.

    ./.venv/bin/python test_quotas.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-quota-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import db          # noqa: E402
import normalize   # noqa: E402
import purchases   # noqa: E402
import quotas      # noqa: E402
import auth        # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def mk_order(phone):
    return {"order_id": "P", "status": "REQUESTED", "amount_to_collect_usd": 10.0,
            "customer": {"name": "C", "phones": [normalize.normalize_phone(phone)]},
            "items": [], "signature": "s" + phone, "created_at": "2026-07-01T10:00:00"}


def main():
    db.init_db()

    # 1) Otlobly (#1) is unlimited.
    check("Otlobly tier = unlimited", quotas.tier(1) == "unlimited")
    check("Otlobly status unlimited", quotas.status(1)["unlimited"] is True)

    # 2) A broker defaults to starter with the starter limits.
    bid = db.create_business("Broker Co")
    check("broker default tier = starter", quotas.tier(bid) == "starter")
    s = quotas.status(bid)
    check("starter orders limit = 250", s["resources"]["orders"]["limit"] == 250)
    check("starter seats limit = 3", s["resources"]["seats"]["limit"] == 3)
    check("nothing over on a fresh broker", s["over_any"] is False)

    # 3) Tier change flows through the limits.
    db.set_business_config(bid, "tier", "growth")
    check("growth orders limit = 1000", quotas.status(bid)["resources"]["orders"]["limit"] == 1000)
    db.set_business_config(bid, "tier", "starter")

    # 4) Usage = live counts.
    db.set_current_business(bid)
    for i in range(3):
        db.insert_new_order(mk_order("+97059900000" + str(i)))
    check("orders usage counts live rows", quotas.usage(bid)["orders"] == 3)

    pdb = purchases.load()
    pdb["purchase_orders"].append({"po_id": "PO-0001", "packages": [{"package_no": 1}, {"package_no": 2}]})
    purchases.save(pdb)
    check("packages usage counts PO packages", quotas.usage(bid)["packages"] == 2)

    # 5) Searches = a metered per-action counter (Otlobly exempt).
    quotas.bump_search(bid); quotas.bump_search(bid)
    check("searches counter increments", quotas.usage(bid)["searches"] == 2)
    quotas.bump_search(1)
    check("Otlobly searches NOT metered", quotas.usage(1).get("searches", 0) == 0)

    # 6) Soft over-limit flag (seats: starter cap 3).
    for i in range(4):
        db.create_user(f"u{i}", auth.hash_pw("x"), "sales", f"U{i}", business_id=bid)
    st = quotas.status(bid)
    check("seats over the starter cap", st["resources"]["seats"]["over"] is True)
    check("over_any true when a resource is over", st["over_any"] is True)

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
