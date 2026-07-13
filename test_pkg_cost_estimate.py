#!/usr/bin/env python3
"""
Self-checks: estimated package cost = Σ each product's Amazon item price, filled
reuse-first (a past order's price → the ASIN import cache → a live SerpApi call,
metered), on demand only.

SerpApi is monkeypatched so the test spends no network/credits and can assert the
"reuse before fetch" priority + metering exactly.

    ./.venv/bin/python test_pkg_cost_estimate.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-cost-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import amazon_import   # noqa: E402
import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402
import purchases       # noqa: E402
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


# ---- monkeypatch SerpApi: a controlled price table + a "cached" flag ----
IMPORT_CALLS = []
CACHE_TABLE = {  # ASIN → (price, cached?)  — what import_product "knows"
    "B0CACHE001": (30.0, True),    # cached hit → free
    "B0LIVE0001": (45.0, False),   # live lookup → one credit
}


def fake_import_product(url_or_asin, config=None, refresh=False):
    IMPORT_CALLS.append(url_or_asin)
    a = (url_or_asin or "").upper()
    for asin, (price, cached) in CACHE_TABLE.items():
        if asin in a:
            return {"asin": asin, "price_usd": price, "cached": cached,
                    "fetched_at": "2026-07-01T00:00:00"}
    return {"asin": None, "price_usd": None}   # unknown product


amazon_import.import_product = fake_import_product


def order_with_priced_item(asin, price):
    db.insert_new_order({
        "order_id": "x", "status": "ORDERED",
        "customer": {"name": "C", "address": "", "phones": []},
        "items": [{"asin": asin, "serp_price_usd": price, "clean_url": ""}],
        "amount_to_collect_usd": price * 2, "deposit_usd": 0.0})


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    co = client("otlo")
    bid = co.post("/api/admin/brokers", json={"name": "ACME", "tier": "starter",
                  "admin_username": "acme-admin", "admin_password": "secret1"}).get_json()["business_id"]
    cb = client("acme-admin", "secret1")

    # 0) _norm_item persists the est fields.
    it = purchases._norm_item({"asin": "B0X", "est_cost_usd": 12.5,
                               "est_cost_src": "past", "est_cost_at": "2026-07-01"})
    check("_norm_item persists est_cost fields",
          it["est_cost_usd"] == 12.5 and it["est_cost_src"] == "past" and it["est_cost_at"] == "2026-07-01")

    # ---- seed as the BROKER (so metering hits its quota) ----
    db.set_current_business(bid)
    order_with_priced_item("B0PAST0001", 20.0)   # a price already known from a past order
    # A PO: pkg1 = past(20)×2 + cache(30)×1 ; pkg2 = live(45)×1 + unknown
    pdb = purchases.load()
    po, _ = purchases.save_full(pdb, {
        "amazon_order_number": "113-1", "ship_to": "", "packages": [
            {"package_no": 1, "arrival": "", "tracking_number": "", "items": [
                {"asin": "B0PAST0001", "qty": 2, "clean_url": "https://amazon.com/dp/B0PAST0001"},
                {"asin": "B0CACHE001", "qty": 1, "clean_url": "https://amazon.com/dp/B0CACHE001"}]},
            {"package_no": 2, "arrival": "", "tracking_number": "", "items": [
                {"asin": "B0LIVE0001", "qty": 1, "clean_url": "https://amazon.com/dp/B0LIVE0001"},
                {"asin": "B0UNKNOWN9", "qty": 1, "clean_url": "https://amazon.com/dp/B0UNKNOWN9"}]}]},
        db.list_orders())
    purchases.save(pdb)
    po_id = po["po_id"]
    before_usage = db.get_usage(bid, "searches", quotas.period())
    IMPORT_CALLS.clear()

    # 1) Estimate the whole PO.
    r = cb.post("/api/purchase/estimate_cost", json={"id": po_id}).get_json()
    check("endpoint ok", r.get("ok") is True)
    res = r["result"]
    check("one price reused from a past order (no fetch)", res["from_past"] == 1)
    check("one cached hit (free)", res["from_cache"] == 1)
    check("one live SerpApi fetch", res["fresh"] == 1)
    check("one product left unpriced", res["unpriced"] == 1)

    # 2) The past-order ASIN was NOT sent to import_product (reused, no call).
    check("past ASIN never hit SerpApi", not any("B0PAST0001" in c for c in IMPORT_CALLS))
    check("cache + live + unknown ASINs did hit import_product",
          all(any(a in c for c in IMPORT_CALLS) for a in ("B0CACHE001", "B0LIVE0001", "B0UNKNOWN9")))

    # 3) Metering: only the live lookup counts against the broker's quota.
    after_usage = db.get_usage(bid, "searches", quotas.period())
    check("broker searches +1 (only the fresh lookup)", after_usage - before_usage == 1)

    # 4) Per-package totals: pkg1 = 20*2 + 30 = 70 ; pkg2 = 45 (+ unknown skipped).
    packs = {p["pi"]: p["est_total_usd"] for p in res["packages"]}
    check("package 1 est = 70.0", packs[0] == 70.0)
    check("package 2 est = 45.0", packs[1] == 45.0)

    # 5) Persisted: items carry est_cost_usd/src after the save.
    po2 = purchases.find(purchases.load(), po_id)
    srcs = {it["asin"]: it.get("est_cost_src") for pk in po2["packages"] for it in pk["items"]}
    check("sources stamped past/cache/serp",
          srcs["B0PAST0001"] == "past" and srcs["B0CACHE001"] == "cache" and srcs["B0LIVE0001"] == "serp")

    # 6) Re-running makes ZERO new fetches (fill-missing only).
    IMPORT_CALLS.clear()
    r2 = cb.post("/api/purchase/estimate_cost", json={"id": po_id}).get_json()["result"]
    check("re-run: nothing re-fetched",
          r2["from_past"] == 0 and r2["from_cache"] == 0 and r2["fresh"] == 0 and len(IMPORT_CALLS) == 0)

    # 7) Otlobly (business 1) is exempt from search metering.
    db.set_current_business(1)
    opdb = purchases.load()
    o_po, _ = purchases.save_full(opdb, {
        "amazon_order_number": "113-9", "ship_to": "", "packages": [
            {"package_no": 1, "arrival": "", "tracking_number": "", "items": [
                {"asin": "B0LIVE0001", "qty": 1, "clean_url": "https://amazon.com/dp/B0LIVE0001"}]}]},
        db.list_orders())
    purchases.save(opdb)
    u0 = db.get_usage(1, "searches", quotas.period())
    co.post("/api/purchase/estimate_cost", json={"id": o_po["po_id"]})
    check("Otlobly search usage stays 0 (exempt)",
          db.get_usage(1, "searches", quotas.period()) == u0 == 0)

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
