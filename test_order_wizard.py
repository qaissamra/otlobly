#!/usr/bin/env python3
"""
Self-checks: the /order 2-screen wizard + early lead capture.

Screen 1 (links + name + phone) POSTs /api/quote/lead the moment متابعة is
pressed — the customer is saved as a REQUESTED order BEFORE choosing a plan, so
abandoning on screen 2 still leaves staff a lead in the To-order queue. Screen 2
records the plan on the same order via the token issued at capture time.

    ./.venv/bin/python test_order_wizard.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-wizard-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import db              # noqa: E402

appmod.limiter.enabled = False   # the rate limit is exercised live, not here

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


AMZ1 = "https://www.amazon.com/dp/B0ABCD1234"
AMZ2 = "https://www.amazon.com/dp/B0WXYZ9876"


def main():
    db.init_db()
    c = appmod.app.test_client()

    # ── 1) Lead capture: screen-1 POST creates a plan-less REQUESTED order ──
    r = c.post("/api/quote/lead", json={"links": [AMZ1], "name": "تجربة", "phone": "970591234567"})
    d = r.get_json() or {}
    check("lead create returns ok + order_id + token",
          r.status_code == 200 and d.get("ok") and d.get("order_id") and d.get("token"))
    oid, tok = d.get("order_id"), d.get("token")
    o = db.get_order(oid) or {}
    check("lead order is REQUESTED", o.get("status") == "REQUESTED")
    check("lead order has NO plan yet", o.get("payment_plan") == "")
    check("lead order is website-sourced", o.get("source") == "website")
    check("lead token stored on the order", o.get("lead_token") == tok)
    check("lead note marks 'no plan yet'",
          (o.get("customer") or {}).get("notes") == appmod.LEAD_NOTE)
    check("lead item parsed from the Amazon link",
          any(it.get("asin") == "B0ABCD1234" for it in o.get("items") or []))

    # ── 2) Back + متابعة again: same order updated, not duplicated ──
    before = len(db.list_orders())
    r = c.post("/api/quote/lead", json={"links": [AMZ2], "name": "تجربة معدلة",
                                        "phone": "970591234567", "order_id": oid, "token": tok})
    d = r.get_json() or {}
    check("lead re-POST returns the SAME order_id", d.get("ok") and d.get("order_id") == oid)
    check("lead re-POST did not create a duplicate", len(db.list_orders()) == before)
    o = db.get_order(oid) or {}
    check("items replaced on update",
          [it.get("asin") for it in o.get("items") or []] == ["B0WXYZ9876"])
    check("name updated on update", (o.get("customer") or {}).get("name") == "تجربة معدلة")

    # ── 3) Stale token falls through to a fresh create (never a dead-end) ──
    r = c.post("/api/quote/lead", json={"links": [AMZ1], "name": "غريب", "phone": "972541234567",
                                        "order_id": oid, "token": "wrong-token"})
    d = r.get_json() or {}
    check("stale token → fresh order instead of 500/404",
          d.get("ok") and d.get("order_id") and d.get("order_id") != oid)

    # ── 4) Plan endpoint: token-gated, strict plan, idempotent ──
    r = c.post("/api/quote/plan", json={"order_id": oid, "token": "wrong", "plan": "prepaid"})
    check("plan with wrong token → 404", r.status_code == 404)
    check("plan unchanged after bad token", (db.get_order(oid) or {}).get("payment_plan") == "")
    r = c.post("/api/quote/plan", json={"order_id": oid, "token": tok, "plan": "cash"})
    check("plan outside whitelist → 400", r.status_code == 400)
    r = c.post("/api/quote/plan", json={"order_id": oid, "token": tok, "plan": "prepaid"})
    check("valid plan accepted", r.status_code == 200 and (r.get_json() or {}).get("ok"))
    o = db.get_order(oid) or {}
    check("payment_plan recorded", o.get("payment_plan") == "prepaid")
    check("note upgraded lead → quote request",
          (o.get("customer") or {}).get("notes") == appmod.QUOTE_NOTE)
    r = c.post("/api/quote/plan", json={"order_id": oid, "token": tok, "plan": "zero_risk"})
    check("re-POST while REQUESTED just overwrites (retry-safe)",
          r.status_code == 200 and (db.get_order(oid) or {}).get("payment_plan") == "zero_risk")

    # ── 5) Staff moved the order on → the wizard can no longer touch it ──
    db.update_order(oid, {"status": "QUOTED"})
    r = c.post("/api/quote/plan", json={"order_id": oid, "token": tok, "plan": "prepaid"})
    check("plan blocked once status left REQUESTED", r.status_code == 404)

    # ── 6) Screen-1 validation (server side mirrors the client) ──
    r = c.post("/api/quote/lead", json={"links": [], "name": "بدون روابط", "phone": "970591234567"})
    check("no links → 400", r.status_code == 400)
    r = c.post("/api/quote/lead", json={"links": ["not-a-link"], "name": "روابط خربانة", "phone": "970591234567"})
    check("non-http junk links → 400", r.status_code == 400)
    r = c.post("/api/quote/lead", json={"links": ["https://example.com/x"], "name": "متجر آخر", "phone": "970591234567"})
    check("non-Amazon retailer link still accepted (multi-retailer, same as /api/quote/request)",
          r.status_code == 200 and (r.get_json() or {}).get("ok"))
    r = c.post("/api/quote/lead", json={"links": [AMZ1], "name": "بدون رقم", "phone": ""})
    check("missing phone → 400", r.status_code == 400)
    r = c.post("/api/quote/lead", json={"links": [AMZ1], "name": "", "phone": "970591234567"})
    check("missing name → 400", r.status_code == 400)

    # ── 7) Regression: the classic one-shot endpoint (wizard fallback) ──
    r = c.post("/api/quote/request", json={"plan": "prepaid", "links": [AMZ1],
                                           "name": "فولباك", "phone": "970599999999"})
    d = r.get_json() or {}
    check("/api/quote/request still works", r.status_code == 200 and d.get("ok") and d.get("order_id"))
    o = db.get_order(d.get("order_id")) or {}
    check("fallback order carries the plan", o.get("payment_plan") == "prepaid")

    # ── 8) The wizard page itself ──
    r = c.get("/order")
    html = r.get_data(as_text=True)
    check("/order renders", r.status_code == 200)
    check("two wizard screens present", 'id="screen1"' in html and 'id="screen2"' in html)
    check("متابعة captures the lead", "continueLead" in html and "/api/quote/lead" in html)
    check("screen-2 submits the plan", "submitPlan" in html and "/api/quote/plan" in html)
    check("prepaid card comes FIRST in the DOM (right in RTL)",
          html.index('id="plan-prepaid"') < html.index('id="plan-zero_risk"'))
    check("Safety card leads with الدفع عند الاستلام",
          'data-i18n="codBullet"' in html and "الدفع عند الاستلام" in html)
    check("$8 floor now shown on /order", "حد أدنى $8" in html)
    check("live calculator removed", "orderval" not in html and "paintTimeline" not in html)

    tpl = Path(__file__).parent / "templates" / "pricing.html"
    check("pricing.html: COD is the Safety card's first bullet",
          'data-i18n="plan2.fPay"' in tpl.read_text(encoding="utf-8"))
    staff = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("staff queue flags plan-less website leads", "بدون باقة" in staff)

    print()
    if fails:
        print(f"FAILED: {len(fails)} check(s): {fails}")
        raise SystemExit(1)
    print("All order-wizard checks passed.")


if __name__ == "__main__":
    main()
