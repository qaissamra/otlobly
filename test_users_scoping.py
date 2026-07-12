#!/usr/bin/env python3
"""
Self-checks: /api/users is tenant-scoped.

Before this fix a broker admin's Team page listed EVERY tenant's staff, users a
broker created landed in business #1 (NULL business_id coerces to Otlobly), and
PATCH could deactivate or reset the password of ANY user by id — including
Otlobly's own admin. Each of those must now be fenced to the caller's business.

    ./.venv/bin/python test_users_scoping.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-usc-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402

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
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "O", business_id=1)
    co = client("otlo")
    j = co.post("/api/admin/brokers", json={
        "name": "ACME Cargo", "admin_username": "acme-admin",
        "admin_password": "secret1"}).get_json()
    bid = j["business_id"]
    cb = client("acme-admin", "secret1")

    # 1) GET is fenced: each tenant sees only its own staff.
    ol = co.get("/api/users").get_json()["users"]
    bl = cb.get("/api/users").get_json()["users"]
    check("Otlobly list has only Otlobly staff",
          {u["username"] for u in ol} == {"otlo"})
    check("broker list has only broker staff",
          {u["username"] for u in bl} == {"acme-admin"})

    # 2) POST is fenced: a user the broker creates belongs to the BROKER's business.
    r = cb.post("/api/users", json={"username": "acme-sales", "password": "secret1",
                                    "role": "sales", "name": "S"})
    check("broker can create staff", r.get_json().get("ok") is True)
    check("new staff row belongs to the broker",
          (db.get_user("acme-sales") or {}).get("business_id") == bid)
    check("new staff does NOT appear in Otlobly's team",
          "acme-sales" not in {u["username"] for u in co.get("/api/users").get_json()["users"]})
    me = client("acme-sales", "secret1").get("/api/me").get_json()
    check("new staff logs into the broker tenant", me.get("business_id") == bid)

    # 3) PATCH is fenced: broker admin cannot touch an Otlobly user by id.
    otlo_id = db.get_user("otlo")["id"]
    check("broker PATCH on an Otlobly user id → 404",
          cb.patch("/api/users", json={"id": otlo_id, "password": "hacked1"}).status_code == 404)
    check("Otlobly admin password NOT changed", auth.verify("otlo", "s1") is not None)
    check("broker PATCH on an Otlobly user id (deactivate) → 404",
          cb.patch("/api/users", json={"id": otlo_id, "active": False}).status_code == 404)
    check("Otlobly admin still active", (db.get_user("otlo") or {}).get("active") == 1)

    # 4) Same-tenant management still works on both sides.
    sales_id = db.get_user("acme-sales")["id"]
    check("broker can reset its own staff's password",
          cb.patch("/api/users", json={"id": sales_id, "password": "newpw11"}).get_json().get("ok") is True
          and auth.verify("acme-sales", "newpw11") is not None)
    db.create_user("otlo-sales", auth.hash_pw("s1"), "sales", "S", business_id=1)
    os_id = db.get_user("otlo-sales")["id"]
    check("Otlobly can manage its own staff",
          co.patch("/api/users", json={"id": os_id, "active": False}).get_json().get("ok") is True
          and (db.get_user_by_id(os_id) or {}).get("active") == 0)  # get_user() hides inactive rows

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
