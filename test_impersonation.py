#!/usr/bin/env python3
"""
Self-checks for the Tatabu support view (impersonation) + broker profile.

The platform admin (business #1 admin) can open a broker's dashboard AS that
broker: the whole request pipeline re-scopes to the broker's tenant (shell brand,
/api/me, quota, data writes), while the REAL identity stays the owner's — brokers
can never do this, a forged session key is inert, and exiting restores Otlobly.

    ./.venv/bin/python test_impersonation.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-imp-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import activity        # noqa: E402
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
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Qais", business_id=1)
    db.create_user("otlo-sales", auth.hash_pw("s1"), "sales", "S", business_id=1)
    co = client("otlo")
    bid = co.post("/api/admin/brokers", json={
        "name": "ACME Cargo", "brand_name": "ACME",
        "admin_username": "acme-admin", "admin_password": "secret1"}).get_json()["business_id"]
    bid2 = co.post("/api/admin/brokers", json={
        "name": "Beta Cargo", "admin_username": "beta-admin",
        "admin_password": "secret1"}).get_json()["business_id"]
    cb = client("acme-admin", "secret1")

    # 1) Guards: only the real business-1 admin may impersonate, and never business 1.
    check("broker admin can't impersonate",
          cb.post("/api/admin/broker/impersonate", json={"business_id": bid2}).status_code == 403)
    check("biz-1 sales can't impersonate",
          client("otlo-sales").post("/api/admin/broker/impersonate",
                                    json={"business_id": bid}).status_code == 403)
    check("business 1 can't be impersonated",
          co.post("/api/admin/broker/impersonate", json={"business_id": 1}).status_code == 400)
    check("unknown business → 404",
          co.post("/api/admin/broker/impersonate", json={"business_id": 999}).status_code == 404)

    # 2) The support view really re-scopes the whole pipeline.
    check("impersonate starts", co.post("/api/admin/broker/impersonate",
                                        json={"business_id": bid}).get_json().get("ok") is True)
    me = co.get("/api/me").get_json()
    check("/api/me flips to the broker business", me["business"]["id"] == bid)
    check("/api/me carries the impersonating flag",
          (me.get("impersonating") or {}).get("business_id") == bid)
    check("real identity stays business 1", me.get("business_id") == 1)
    check("broker tier shown, not unlimited", me["business"]["tier"] == "starter")
    shell = co.get("/app").get_data(as_text=True)
    check("shell renders the BROKER's brand", "ACME — Orders" in shell)
    check("shell has no Otlobly brand slots", "Otlobly — Orders" not in shell)
    q = co.get("/api/quota").get_json()
    check("quota is the broker's (metered)", q.get("unlimited") is False and q.get("tier") == "starter")

    # 3) Writes while impersonating land in the BROKER's tenant (real writes).
    co.post("/api/users", json={"username": "debug-helper", "password": "secret1",
                                "role": "sales", "name": "D"})
    check("a user created while impersonating belongs to the broker",
          (db.get_user("debug-helper") or {}).get("business_id") == bid)

    # 4) Exit restores Otlobly; start/stop are in the platform's own activity feed.
    check("stop works",
          co.post("/api/admin/broker/impersonate/stop", json={}).get_json().get("ok") is True)
    me = co.get("/api/me").get_json()
    check("/api/me back to business 1", me["business"]["id"] == 1 and "impersonating" not in me)
    check("shell back to Otlobly", "Otlobly — Orders" in co.get("/app").get_data(as_text=True))
    feed = " ".join(e.get("detail", "") for e in activity.recent(50, business_id=1))
    check("start+stop logged in the platform feed",
          "opened their dashboard" in feed and "exited" in feed)

    # 5) A forged session key in a BROKER's session is inert.
    with cb.session_transaction() as s:
        s["support_view_bid"] = bid2
    check("forged key does nothing for a broker",
          cb.get("/api/me").get_json()["business"]["id"] == bid)

    # 6) Re-login never inherits a support view.
    co.post("/api/admin/broker/impersonate", json={"business_id": bid})
    co.post("/login", data={"username": "otlo", "password": "s1"})
    check("fresh login clears the support view",
          co.get("/api/me").get_json()["business"]["id"] == 1)

    # 7) Broker profile endpoint.
    check("profile 403 for broker admin",
          cb.get(f"/api/admin/broker/{bid2}").status_code == 403)
    check("profile 404 unknown", co.get("/api/admin/broker/999").status_code == 404)
    d = co.get(f"/api/admin/broker/{bid}").get_json()
    check("profile carries identity + tier + usage + staff",
          d["business"]["name"] == "ACME Cargo" and d["tier"] == "starter"
          and "seats" in (d["status"].get("resources") or {})
          and {u["username"] for u in d["users"]} >= {"acme-admin", "debug-helper"})
    check("broker profile is not internal", d.get("internal") is False)

    # 7b) Otlobly's own row → a READ-ONLY internal profile (no plan/impersonation).
    ot = co.get("/api/admin/broker/1")
    check("Otlobly profile is reachable (not 400)", ot.status_code == 200)
    otj = ot.get_json()
    check("Otlobly profile is flagged internal + unlimited",
          otj.get("internal") is True and otj["tier"] == "unlimited")
    check("Otlobly profile carries plain counts",
          {"orders", "customers", "seats"} <= set((otj.get("counts") or {}).keys()))
    check("Otlobly can still never be impersonated",
          co.post("/api/admin/broker/impersonate", json={"business_id": 1}).status_code == 400)

    # 8) Platform password reset — fenced to the right tenant.
    uid = db.get_user("acme-admin")["id"]
    check("reset broker admin password works",
          co.post("/api/admin/broker/user",
                  json={"business_id": bid, "user_id": uid,
                        "password": "newpw77"}).get_json().get("ok") is True
          and auth.verify("acme-admin", "newpw77") is not None)
    otlo_id = db.get_user("otlo")["id"]
    check("reset can't cross tenants",
          co.post("/api/admin/broker/user",
                  json={"business_id": bid, "user_id": otlo_id,
                        "password": "hacked7"}).status_code == 404)
    check("reset 403 for broker admin",
          cb.post("/api/admin/broker/user",
                  json={"business_id": bid2, "user_id": uid,
                        "password": "hacked7"}).status_code == 403)

    # 9) Tier changes now join the platform activity feed.
    co.post("/api/admin/broker/tier", json={"business_id": bid, "tier": "growth"})
    check("tier change logged with the plan field",
          any(e.get("field") == "plan" and e.get("new") == "growth"
              for e in activity.recent(20, business_id=1)))

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
