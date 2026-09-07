#!/usr/bin/env python3
"""
Self-checks for attention.py — the Needs attention queue.

The queue is the one place that answers "is anything on fire?", so the rules that
decide what lands in it are the whole product. What matters here:

  * a package whose journey is OVER never appears again — the done vocabulary is
    alerts.stop_statuses(), the same one the sweeps and the Telegram alerts use, so
    editing it in Settings moves all of them together (APP_AUDIT F-003);
  * "no tracking number" waits until the money is actually spent, so a package that
    was ordered this morning is not nagged about;
  * the GAASH deadline is a LOST-PARCEL date, so a package past it is urgent even
    when its own due date is fine;
  * the counts the sidebar badge shows are the true totals, not the capped page;
  * nothing in the payload carries money (there is no redaction step behind it).

    ./.venv/bin/python test_attention.py
"""

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-attention-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("GAASH_MAILER", None)
os.environ["OTLOBLY_SECRET"] = "x"

import attention   # noqa: E402
import db          # noqa: E402
import purchases   # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def d(n):
    """n days ago, as YYYY-MM-DD."""
    return (date.today() - timedelta(days=n)).isoformat()


def store(*packages, placed=30):
    """A one-PO store whose packages are the ones given."""
    pdb = {"seq": 1, "purchase_orders": [{
        "po_id": "PO-0001", "ship_to": "Waleed", "order_placed": d(placed),
        "created_at": d(placed) + "T09:00:00+03:00", "amazon_order_number": "111-2",
        "packages": list(packages), "status": "PLACED", "custom": {},
    }]}
    purchases.save(pdb)


def pkg(no=1, **kw):
    p = {"package_no": no, "items": [], "arrival": ""}
    p.update(kw)
    return p


def groups(payload):
    return {g["key"]: g for g in payload["groups"]}


# ── the rules ──────────────────────────────────────────────────────────────
def test_late_and_deadline():
    store(pkg(1, due_date=d(4), tracking_number="GWD004000001", otlobly_status="oredered"),
          pkg(2, due_date=(date.today() + timedelta(days=3)).isoformat(), tracking_number="GWD004000002"),
          pkg(3, tracking_number="GWD004000003", gaash_deadline=d(2)))
    g = groups(attention.build())
    titles = [i["title"] for i in g["deadline"]["items"]]
    check("a package past its due date is raised", any("package 1" in t for t in titles))
    check("a package due in the future is not", not any("package 2" in t for t in titles))
    check("a package past its GAASH deadline is raised", any("package 3" in t for t in titles))
    kinds = {i["kind"] for i in g["deadline"]["items"]}
    check("both read as 'late' in the attention vocabulary", kinds == {"late"})
    check("they are urgent, not 'soon'", all(i["severity"] == "urgent" for i in g["deadline"]["items"]))
    dl = next(i for i in g["deadline"]["items"] if "package 3" in i["title"])
    check("the deadline row says what expiry means", "lost after this" in dl["detail"])


def test_done_packages_are_silent():
    import alerts
    stop = sorted(alerts.stop_statuses())
    store(*[pkg(n + 1, due_date=d(9), tracking_number=f"GWD00400{n:04d}", otlobly_status=s)
            for n, s in enumerate(stop)])
    p = attention.build()
    check(f"every one of the {len(stop)} 'journey over' statuses silences a late package",
          p["count"] == 0)
    # …and the moment one of them is NOT done, it comes back
    store(pkg(1, due_date=d(9), tracking_number="GWD004000001", otlobly_status="not recieved rd"))
    check("'not recieved rd' is NOT done (APP_AUDIT F-008) — it still raises",
          attention.build()["count"] == 1)


def test_no_tracking_waits_for_the_order():
    store(pkg(1), placed=0)
    check("a package ordered today is not nagged for a GWD", attention.build()["count"] == 0)
    store(pkg(1), placed=attention.STALE_NO_TRACKING_D)
    g = groups(attention.build())
    check("after the grace period it is", g["no_tracking"]["count"] == 1)
    check("it reads as 'no_tracking'", g["no_tracking"]["items"][0]["kind"] == "no_tracking")
    check("and it links to the Purchases board",
          g["no_tracking"]["items"][0]["link"] == {"view": "purchases", "arg": "PO-0001"})
    store(pkg(1, tracking_number="GWD004000001"), placed=30)
    check("a package that HAS a GWD is not raised", attention.build()["count"] == 0)


def test_documents_requested():
    # the REAL shape the sweeps store (tracking.docs_status's dict) — an
    # earlier version of this test used a bare string, and the string is what
    # the code was written against, so the group could never fire in production
    store(pkg(1, tracking_number="GWD004000009",
              docs_state={"state": "action", "codes": ["ID"], "links": []}, docs_checked=d(1)),
          pkg(2, tracking_number="GWD004000010", docs_state={"state": "info", "links": []}))
    g = groups(attention.build())
    check("docs_state 'action' is raised", g["docs"]["count"] == 1)
    check("docs_state 'info' is not", "GWD004000010" not in json.dumps(g))
    it = g["docs"]["items"][0]
    check("it reads as 'missing_docs' and is urgent",
          it["kind"] == "missing_docs" and it["severity"] == "urgent")
    check("it links to GAASH mail with the parcel number",
          it["link"] == {"view": "gaashmail", "arg": "GWD004000009"})


def test_payload_shape():
    store(pkg(1, due_date=d(3), tracking_number="GWD004000001"),
          pkg(2, docs_state="action", tracking_number="GWD004000002"))   # bare string: still honoured
    p = attention.build()
    check("the payload carries a generated_at stamp", bool(p.get("generated_at")))
    check("count is the sum of the groups", p["count"] == sum(g["count"] for g in p["groups"]))
    check("empty groups are dropped", all(g["count"] for g in p["groups"]))
    check("urgent is counted separately for the badge", p["urgent"] >= 1)
    blob = json.dumps(p)
    check("no money anywhere in the payload",
          "amount_usd" not in blob and "usd" not in blob.lower())
    for g in p["groups"]:
        check(f"group '{g['key']}' never returns more than the cap",
              len(g["items"]) <= attention.MAX_ITEMS)


def test_cap_keeps_the_true_count():
    store(*[pkg(n + 1, due_date=d(5), tracking_number=f"GWD00500{n:04d}")
            for n in range(attention.MAX_ITEMS + 7)])
    g = groups(attention.build())["deadline"]
    check("the page is capped", len(g["items"]) == attention.MAX_ITEMS)
    check("but the badge still shows the truth", g["count"] == attention.MAX_ITEMS + 7)


def test_brain_rules_do_not_double_count():
    store()
    src = (Path(__file__).parent / "attention.py").read_text(encoding="utf-8")
    check("the To-order queue is left to its own nav badge",
          "rule:need_order" in src and "BRAIN_SKIP" in src)
    check("every mapped brain rule has an attention kind",
          set(attention.BRAIN_KIND) >= {"rule:unpriced", "rule:collect", "rule:quota"})


def main():
    db.init_db()
    print("attention.py")
    test_late_and_deadline()
    test_done_packages_are_silent()
    test_no_tracking_waits_for_the_order()
    test_documents_requested()
    test_payload_shape()
    test_cap_keeps_the_true_count()
    test_brain_rules_do_not_double_count()
    print("――――――――――――――――――――――")
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All Needs-attention checks passed")


if __name__ == "__main__":
    main()
