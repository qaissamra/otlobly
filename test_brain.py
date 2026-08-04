#!/usr/bin/env python3
"""
Self-checks for the Brain — the per-tenant command-center landing.

brain.build() derives urgent / due / forgotten / money / 7-day results from the
tenant's own orders + leads + quota. Rules must fire at their thresholds, stay
inside the tenant, respect feature gates, and redact money for roles without
view_money — because the same payload will later feed the Telegram digest.

    ./.venv/bin/python test_brain.py
"""

import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-brain-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import brain           # noqa: E402
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


def iso_ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def d_ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def d_in(days):
    return (date.today() + timedelta(days=days)).isoformat()


def order(tag, status, *, created=0, amount=100.0, deposit=0.0, **extra):
    """insert_new_order REALLOCATES order_id (OTL-####) — return the real code."""
    o = {"order_id": tag, "status": status,
         "customer": {"name": f"c-{tag}", "address": "", "phones": []},
         "items": [], "created_at": iso_ago(created), "updated_at": iso_ago(created),
         "amount_to_collect_usd": amount, "deposit_usd": deposit}
    o.update(extra)
    db.insert_new_order(o)
    return o["order_id"]


def sections(payload):
    return {s["key"]: s for s in payload["sections"]}


def ids(sec):
    return [i["id"] for i in sec["items"]]


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    db.create_user("otlo-ful", auth.hash_pw("s1"), "fulfillment", "F", business_id=1)
    co = client("otlo")
    bid = co.post("/api/admin/brokers", json={"name": "ACME", "tier": "starter",
                  "admin_username": "acme-admin", "admin_password": "secret1"}).get_json()["business_id"]
    cb = client("acme-admin", "secret1")

    # ---- seed Otlobly (business 1); ids come back reallocated as OTL-#### ----
    db.set_current_business(1)
    A = order("a", "REQUESTED", created=3)                                   # urgent backlog + forgotten >2d
    B = order("b", "QUOTED", created=5, quoted_at=iso_ago(4))                # forgotten quoted >3d
    C = order("c", "QUOTED", created=1, quoted_at=iso_ago(1))                # under threshold — must NOT fire
    D = order("d", "SHIPPED", created=6, est_delivery_customer=d_ago(2))     # overdue ETA → urgent
    E = order("e", "ORDERED", created=4, est_delivery_customer=d_in(2),
              amazon_arrival=d_in(1))                                        # due: ETA in 2d + amazon in 1d
    F = order("f", "ARRIVED", created=9, amount=250.0, deposit=50.0,
              amazon_arrival=d_ago(6))                                       # urgent collect + forgotten arrived>5d
    G = order("g", "DELIVERED", created=8, est_delivery_customer=d_ago(3),
              delivered_at=iso_ago(2))                                       # delivered: past ETA must NOT be past-deadline; counts in collect_now
    J = order("j", "SHIPPED", created=5, est_delivery_customer=d_ago(0))     # due TODAY → past-deadline (boundary)
    order("h", "COLLECTED", created=2, amount=80.0)                          # results: collected in window
    order("i", "REQUESTED", created=0, amount=None)                          # unpriced counts
    OTL_IDS = {A, B, C, D, E, F, G, J}
    db.upsert_lead({"lead_id": "L1", "source": "messenger", "name": "Lead One",
                    "phone": "970599", "created_time": iso_ago(3), "status": "new"})

    d1 = brain.build(1)
    s1 = sections(d1)

    # 1) urgent rules
    u = ids(s1["urgent"])
    check("need_order aggregate fires", "rule:need_order" in u)
    check("unpriced aggregate fires", "rule:unpriced" in u)
    check("collect aggregate fires with the right amount (ARRIVED 200 + DELIVERED 100)",
          any(i["id"] == "rule:collect" and i["amount_usd"] == 300.0 for i in s1["urgent"]["items"]))
    check("overdue ETA no longer in urgent (moved to deadlines)", D not in u)
    check("leads rule fires for business 1", "rule:leads" in u)
    check("no quota rule for unlimited business 1",
          not any(i["id"] == "rule:quota" for i in s1["urgent"]["items"]))

    # 1b) deadlines section (packages past the customer-promised date)
    dl = ids(s1["deadlines"])
    check("past-deadline order is in the deadlines section", D in dl)
    check("due-today order counts as past-deadline (boundary)", J in dl)
    check("delivered order with past ETA is NOT past-deadline", G not in dl)
    check("deadlines section is urgent severity", s1["deadlines"]["severity"] == "urgent")
    check("a deadline row shows the status for context",
          any("in transit" in (i["detail"] or "") for i in s1["deadlines"]["items"]))

    # 1c) pipeline funnel counts
    p = d1["pipeline"]
    check("pipeline has all funnel keys",
          set(p) == {"to_order", "ordered", "in_transit", "to_collect", "past_deadline", "due_soon"})
    check("to_order = REQUESTED count", p["to_order"] == 2)          # A + i(unpriced)
    check("in_transit = SHIPPED count", p["in_transit"] == 2)        # D + J
    check("to_collect = ARRIVED+DELIVERED", p["to_collect"] == 2)    # F + G
    check("past_deadline counts D and J only", p["past_deadline"] == 2)
    check("due_soon counts E only", p["due_soon"] == 1)

    # 2) due rules (amazon-arrival still surfaces; customer ETA in 2d is due)
    dd = ids(s1["due"])
    check("ETA within 3d is due", E in dd)
    check("amazon arrival within 2d is due (same order, purchases link)",
          sum(1 for i in s1["due"]["items"] if i["id"] == E) == 2)

    # 3) forgotten rules + threshold boundary + dedupe
    f = ids(s1["forgotten"])
    check("REQUESTED>2d forgotten", A in f)
    check("QUOTED>3d forgotten", B in f)
    check("QUOTED 1d NOT forgotten", C not in f)
    check("ARRIVED>5d forgotten", F in f)
    check("dedupe: an order appears once in forgotten",
          all(f.count(x) == 1 for x in set(f)))
    check("stale lead in forgotten", "L1" in f)

    # 4) money section
    m = {i["id"]: i for i in s1["money"]["items"]}
    check("money section has the three tiles",
          set(m) == {"rule:net_outstanding", "rule:deposits_held", "rule:collect_now"})
    check("collect_now = Σ(amount − deposit) over arrived+delivered",
          m["rule:collect_now"]["amount_usd"] == 300.0)

    # 5) results window
    r = d1["results"]
    check("new orders in 7d counted", r["new_orders"] >= 5)
    check("collected in window", r["collected"] == 1 and r["collected_usd"] == 80.0)
    check("delivered in window", r["delivered"] == 1)

    # 6) tenant isolation: the broker's brain is empty of Otlobly ids.
    d2 = cb.get("/api/brain").get_json()
    all_ids = [i["id"] for s in d2["sections"] for i in s["items"]]
    check("broker brain has no Otlobly ids", not (set(all_ids) & OTL_IDS))
    check("all sections present for an empty tenant",
          [s["key"] for s in d2["sections"]] == ["deadlines", "urgent", "due", "forgotten", "money"])
    check("empty tenant: urgent/due/forgotten all zero",
          all(s["count"] == 0 for s in d2["sections"] if s["key"] != "money"))

    # 7) feature gating: broker leads off → no leads rule even with a seeded lead.
    db.set_current_business(bid)
    db.upsert_lead({"lead_id": "BL1", "source": "messenger", "name": "B Lead",
                    "phone": "970598", "created_time": iso_ago(4), "status": "new"})
    db.set_current_business(1)
    d2 = cb.get("/api/brain").get_json()
    check("no leads rule for broker without the feature",
          not any(i["id"] in ("rule:leads", "BL1")
                  for s in d2["sections"] for i in s["items"]))
    db.set_business_config(bid, "features", {"leads": True})
    d2 = cb.get("/api/brain").get_json()
    check("leads rule appears once the feature is on",
          any(i["id"] == "rule:leads" for s in d2["sections"] for i in s["items"]))

    # 8) quota rule for an over-quota broker (starter searches limit 250).
    db.bump_usage(bid, "searches", quotas.period(), 260)
    d2 = cb.get("/api/brain").get_json()
    check("over-quota broker gets the quota item",
          any(i["id"] == "rule:quota" for s in d2["sections"] for i in s["items"]))

    # 9) redaction: fulfillment (no view_money) sees counts, not amounts.
    df = client("otlo-ful").get("/api/brain").get_json()
    check("fulfillment: every amount is null",
          all(i["amount_usd"] is None for s in df["sections"] for i in s["items"]))
    check("fulfillment: results money nulled, counts intact",
          df["results"]["collected_usd"] is None and df["results"]["collected"] == 1)
    check("admin still sees amounts",
          co.get("/api/brain").get_json()["results"]["collected_usd"] == 80.0)

    # 10) auth: unauthenticated → 401.
    check("unauthenticated /api/brain is 401",
          appmod.app.test_client().get("/api/brain").status_code == 401)

    # 11) shell: Brain is the first nav item and the boot landing.
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("brainBtn before homeBtn", html.index('id="brainBtn"') < html.index('id="homeBtn"'))
    # 2026-08-04: boot goes through restoreView() since the view-restore feature
    # (f2d682e) — Brain is its explicit fallback for a fresh start.
    check("boot lands on brain (restoreView fallback)",
          'setView(restoreView("brain"))' in html and 'fallback=fallback||"brain"' in html)
    check("brain registered in VIEW_BTN", 'brain:"brainBtn"' in html)

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
