#!/usr/bin/env python3
"""
Self-checks for the Tatabu platform console endpoints (Overview / Plans / Usage /
Activity). Only Otlobly's super-admin (business #1) can reach them; the aggregates
exclude Otlobly itself; tier prices round-trip and drive MRR; the activity feed is
merged across tenants and stamped with each event's business.

    ./.venv/bin/python test_platform.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-plat-"))
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
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Qais", business_id=1)
    db.create_user("otlo-sales", auth.hash_pw("s1"), "sales", "S", business_id=1)
    co = client("otlo")
    a = co.post("/api/admin/brokers", json={"name": "ACME", "tier": "starter",
                "admin_username": "acme-admin", "admin_password": "secret1"}).get_json()["business_id"]
    b = co.post("/api/admin/brokers", json={"name": "Beta", "tier": "growth",
                "admin_username": "beta-admin", "admin_password": "secret1"}).get_json()["business_id"]
    cb = client("acme-admin", "secret1")

    # 1) Guards — every platform endpoint is business-1-admin only.
    for path, meth in [("/api/admin/platform", "get"),
                       ("/api/admin/platform/activity", "get"),
                       ("/api/admin/platform/prices", "post")]:
        call = getattr(cb, meth)
        r = call(path, json={}) if meth == "post" else call(path)
        check(f"broker admin 403 on {path}", r.status_code == 403)
        call2 = getattr(client("otlo-sales"), meth)
        r2 = call2(path, json={}) if meth == "post" else call2(path)
        check(f"biz-1 sales 403 on {path}", r2.status_code == 403)

    # 2) Overview aggregates exclude Otlobly and sum the brokers.
    d = co.get("/api/admin/platform").get_json()
    check("two brokers, Otlobly excluded",
          d["totals"]["brokers"] == 2 and all(t["id"] != 1 for t in d["tenants"]))
    check("seats counted (each broker has its 1 admin)", d["totals"]["seats"] == 2)
    check("tier matrix carries limits + counts",
          d["tiers"]["starter"]["limits"]["seats"] == 3
          and d["tiers"]["growth"]["count"] == 1)

    # 3) Prices round-trip and drive MRR; bad input rejected.
    check("negative price rejected",
          co.post("/api/admin/platform/prices",
                  json={"prices": {"starter": -5}}).status_code == 400)
    check("unknown tier rejected",
          co.post("/api/admin/platform/prices",
                  json={"prices": {"platinum": 5}}).status_code == 400)
    check("non-numeric rejected",
          co.post("/api/admin/platform/prices",
                  json={"prices": {"starter": "free"}}).status_code == 400)
    check("prices save",
          co.post("/api/admin/platform/prices",
                  json={"prices": {"starter": 29, "growth": 79, "pro": 199}}).get_json().get("ok"))
    d = co.get("/api/admin/platform").get_json()
    check("MRR = 29 (ACME) + 79 (Beta) = 108", d["mrr"] == 108)
    check("tenant carries its price", next(t for t in d["tenants"] if t["id"] == a)["price"] == 29)

    # 4) Over-quota flagged: push ACME (starter, 3 seats) to its seat cap.
    db.create_user("acme-2", auth.hash_pw("s1"), "sales", "", business_id=a)
    db.create_user("acme-3", auth.hash_pw("s1"), "sales", "", business_id=a)
    d = co.get("/api/admin/platform").get_json()
    check("ACME now flagged over quota (3/3 seats)",
          d["totals"]["over_quota"] >= 1
          and next(t for t in d["tenants"] if t["id"] == a)["status"]["over_any"] is True)

    # 5) Merged activity carries business stamps + includes provisioning/tier events.
    co.post("/api/admin/broker/tier", json={"business_id": b, "tier": "pro"})
    feed = co.get("/api/admin/platform/activity").get_json()["activity"]
    check("every event is stamped with a business",
          all("business_id" in e and "business" in e for e in feed))
    check("provisioning + tier-change events are in the feed",
          any(e.get("entity") == "business" and e.get("action") == "created" for e in feed)
          and any(e.get("field") == "plan" and e.get("new") == "pro" for e in feed))

    # 6) /api/me: only the owner gets the platform brand block.
    sb = (co.get("/api/me").get_json().get("platform") or {}).get("sidebar_html") or ""
    check("owner /api/me has the platform sidebar",
          "platform console" in sb and "Tata" in sb)
    check("broker /api/me has no platform block",
          "platform" not in cb.get("/api/me").get_json())

    # 7) Otlobly shell unchanged: platform nav ships hidden.
    shell = co.get("/app").get_data(as_text=True)
    check("shell renders Otlobly", "Otlobly — Orders" in shell)
    check("platNav ships hidden by default",
          'id="platNav" style="display:none"' in shell)

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
