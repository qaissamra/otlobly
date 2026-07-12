#!/usr/bin/env python3
"""
Self-checks for broker provisioning (Tatabu Phase 11).

Otlobly's super-admin (business #1) creates a broker — a new business + its admin
login + a plan tier + optional brand — and only that super-admin can. A broker's
own admin can't provision others, and a non-admin can't either.

    ./.venv/bin/python test_provisioning.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-prov-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import branding        # noqa: E402
import db              # noqa: E402
import quotas          # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "O", business_id=1)   # Otlobly super-admin
    co = client("otlo")

    # 1) Super-admin provisions a broker.
    r = co.post("/api/admin/brokers", json={
        "name": "ACME Cargo", "tier": "growth", "brand_name": "ACME",
        "admin_username": "acme-admin", "admin_password": "secret1"})
    j = r.get_json()
    check("create returns ok + credentials", j.get("ok") and j.get("admin_username") == "acme-admin")
    bid = j.get("business_id")
    check("business created with the name", (db.get_business(bid) or {}).get("name") == "ACME Cargo")
    check("tier set to growth", quotas.tier(bid) == "growth")
    check("brand name applied", branding.resolve(bid)["name"] == "ACME")
    check("admin user belongs to the broker", (db.get_user("acme-admin") or {}).get("business_id") == bid)

    # 2) The broker's admin logs in → their own tenant (Tatabu-branded, scoped).
    cb = client("acme-admin", "secret1")
    me = cb.get("/api/me").get_json()
    check("broker admin sees its own business", me["business"]["id"] == bid)
    check("broker admin sees its tier", me["business"]["tier"] == "growth")
    check("broker admin sees its brand", me["business"]["brand"]["name"] == "ACME")

    # 3) A broker's admin CANNOT provision other brokers.
    check("broker admin is 403 on provisioning",
          cb.post("/api/admin/brokers", json={"name": "X", "admin_username": "x",
                                              "admin_password": "secret1"}).status_code == 403)

    # 4) A non-admin (sales) on Otlobly cannot provision.
    db.create_user("sales1", auth.hash_pw("s1"), "sales", "S", business_id=1)
    check("sales is 403 on provisioning", client("sales1").get("/api/admin/brokers").status_code == 403)

    # 5) The list shows the broker; the tier can be changed.
    lst = co.get("/api/admin/brokers").get_json()
    check("broker appears in the list", len(lst["brokers"]) == 1 and lst["brokers"][0]["tier"] == "growth")
    co.post("/api/admin/broker/tier", json={"business_id": bid, "tier": "pro"})
    check("tier change persists", quotas.tier(bid) == "pro")

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
