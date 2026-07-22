#!/usr/bin/env python3
"""
Self-checks: the staff bell 🔔 (/api/notifications).

Customer-driven moments — a website order-form submission, a confirmed quote,
a new Meta lead — become bell events (newest first, deduped per order), plus a
standing needs_quote counter for REQUESTED orders still waiting on a price.
The endpoint is a pure read (the client keeps its own last-seen cursor) and is
staff-only.

    ./.venv/bin/python test_notifications.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-notif-"))
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
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    co = client("otlo")
    anon = appmod.app.test_client()

    # staff-only
    check("anonymous is blocked", anon.get("/api/notifications").status_code in (302, 401, 403))

    # customer submits the /order wizard (screen 1) → order_form event + needs_quote
    r = anon.post("/api/quote/lead", json={
        "name": "Nabil Test", "phone": "0599123456",
        "links": ["https://www.amazon.com/dp/B0TESTTEST"]}).get_json()
    check("wizard lead created", r.get("ok") is True and r.get("order_id"))
    oid = r["order_id"]
    d = co.get("/api/notifications").get_json()
    check("payload shape", d.get("ok") is True and isinstance(d.get("events"), list)
          and isinstance(d.get("needs_quote"), int))
    ev = [e for e in d["events"] if e.get("sub") and oid in e["sub"]]
    check("form submission shows as an order_form event",
          ev and ev[0]["type"] == "order_form" and ev[0]["view"] == "needorder"
          and ev[0]["ts"])
    check("needs_quote counts the unpriced REQUESTED order", d["needs_quote"] >= 1)

    # the wizard logs 2-3 activity events per order — the bell must dedupe them
    anon.post("/api/quote/plan", json={"order_id": oid, "token": r.get("token"),
                                       "plan": "cod"})
    d = co.get("/api/notifications").get_json()
    same = [e for e in d["events"] if e.get("sub") and oid in e["sub"]
            and e["type"] == "order_form"]
    check("one submission = one notification (deduped per order)", len(same) == 1)

    # customer confirms the quote → quote_ok event (same log call the intake makes)
    activity.log("confirmed", "order", oid, f"{oid} · Nabil Test",
                 detail="customer confirmed the quotation", user="customer")
    d = co.get("/api/notifications").get_json()
    conf = [e for e in d["events"] if e["type"] == "quote_ok"]
    check("quote confirmation shows as quote_ok", conf
          and "confirmed" in conf[0]["title"] and conf[0]["view"] == "needorder")

    # a new Meta lead → lead event pointing at the Leads view
    db.upsert_lead({"lead_id": "L-TEST-1", "source": "instagram", "name": "IG Person",
                    "last_message": "hi", "last_activity": "2026-07-22T10:00:00+0000",
                    "created_time": "2026-07-22T10:00:00+0000"})
    d = co.get("/api/notifications").get_json()
    lead = [e for e in d["events"] if e["type"] == "lead"]
    check("new Meta lead shows as a lead event",
          lead and lead[0]["view"] == "metaleads" and lead[0]["sub"] == "IG Person")

    # newest first
    ts = [e["ts"] for e in d["events"] if e.get("ts")]
    check("events sorted newest-first", ts == sorted(ts, reverse=True))

    # staff actions never become bell events
    activity.log("set", "order", oid, oid, detail="staff edit", user="qais")
    d = co.get("/api/notifications").get_json()
    check("staff activity is excluded",
          all("staff edit" not in (e.get("title") or "") for e in d["events"]))

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
