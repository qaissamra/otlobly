#!/usr/bin/env python3
"""
Self-checks: PO items inherit their package's status + the row declutter.

A product can't be somewhere its box isn't, so per-item status is now
EXCEPTION-ONLY (cancelled / refunded / out of stock / returned); "" = inherit,
shown as plain text. Legacy stage values (ORDERED/SHIPPED/…) self-clean to ""
on save — verified safe because no Python code ever read item status. The item
row shrinks to photo · name · ×qty · exception chip · ✏️, with all data entry
(link, ASIN, get-photo, tracking, notes, exceptions) in the item editor modal.

    ./.venv/bin/python test_po_item_declutter.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-poitem-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod     # noqa: E402
import auth              # noqa: E402
import db                # noqa: E402
import purchases         # noqa: E402

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

    # 1) Exception-only model in purchases.py.
    check("exception set is cancelled/refunded/out-of-stock/returned",
          purchases.ITEM_EXCEPTIONS == ["CANCELLED", "REFUNDED", "OUT_OF_STOCK", "RETURNED"])
    check("served statuses ARE the exceptions (API contract)",
          purchases.ITEM_STATUSES == purchases.ITEM_EXCEPTIONS)

    # 2) _norm_item semantics: legacy stage values self-clean to inherit;
    #    exceptions (incl. the two new ones) survive.
    check("legacy ORDERED normalizes to inherit",
          purchases._norm_item({"title": "x", "status": "ORDERED"})["status"] == "")
    check("legacy SHIPPED normalizes to inherit",
          purchases._norm_item({"title": "x", "status": "SHIPPED"})["status"] == "")
    check("missing status = inherit", purchases._norm_item({"title": "x"})["status"] == "")
    check("REFUNDED survives",
          purchases._norm_item({"title": "x", "status": "REFUNDED"})["status"] == "REFUNDED")
    check("OUT_OF_STOCK survives",
          purchases._norm_item({"title": "x", "status": "OUT_OF_STOCK"})["status"] == "OUT_OF_STOCK")

    # 3) Round-trip through the real save path (POST /api/purchase → save_full).
    r = co.post("/api/purchase", json={
        "amazon_order_number": "113-1", "ship_to": "", "packages": [
            {"package_no": 1, "arrival": "", "tracking_number": "", "items": [
                {"title": "A", "asin": "B0AAAAAAA1", "status": "DELIVERED"},   # legacy → inherit
                {"title": "B", "asin": "B0BBBBBBB2", "status": "REFUNDED"},    # exception → kept
            ]}]}).get_json()
    check("PO saved", r.get("ok") is True)
    pdb = purchases.load()
    its = pdb["purchase_orders"][0]["packages"][0]["items"]
    check("saved legacy status became inherit", its[0]["status"] == "")
    check("saved exception preserved", its[1]["status"] == "REFUNDED")
    check("/api/purchases serves the exception list",
          co.get("/api/purchases").get_json()["statuses"] == purchases.ITEM_EXCEPTIONS)

    # 4) Regression: apply_to_orders still flips a matched order to ORDERED
    #    regardless of item status (it never read item status — keep it that way).
    db.insert_new_order({"order_id": "x", "status": "REQUESTED",
                         "customer": {"name": "Sam", "address": "", "phones": []},
                         "items": [{"asin": "B0CCCCCCC3", "clean_url": ""}],
                         "amount_to_collect_usd": 50.0, "deposit_usd": 0.0})
    oc = db.list_orders()[0]["order_id"]
    co.post("/api/purchase", json={
        "amazon_order_number": "113-2", "ship_to": "", "packages": [
            {"package_no": 1, "arrival": "2026-08-01", "tracking_number": "", "items": [
                {"title": "C", "asin": "B0CCCCCCC3", "status": "REFUNDED"}]}]})
    check("matched order still flipped to ORDERED",
          db.get_order(oc)["status"] == "ORDERED")

    # 5) The shell: no per-row status dropdown; editor modal owns the data entry.
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("row template has NO status select",
          "poItemSet('${p.po_id}',${pi},${ii},'status'" not in html)
    check("rows are grouped by customer", 'class="poc-cust' in html and "poc-group" in html)
    check("row has the ✏️ editor button", "itemEditOpen('${p.po_id}',${pi},${ii})" in html)
    check("item editor modal exists", 'id="itemEditModal"' in html and "function ieRender" in html)
    check("editor holds link/ASIN/get-photo/notes/tracking",
          all(s in html for s in ("ieLink(this.value)", "ieFetch(this)",
                                  "ieSet('notes'", "ieTracking(this.value)")))
    check("status picker offers inherit + exceptions only",
          "inherits package" in html and "PO_STATUSES=[\"CANCELLED\",\"REFUNDED\",\"OUT_OF_STOCK\",\"RETURNED\"]" in html)
    check("exception chips have colors",
          all(s in html for s in ("REFUNDED:[", "OUT_OF_STOCK:[", "RETURNED:[")))
    check("package effective status helper exists", "function pkgStatus(" in html)
    check("rollup now computes over packages", "least-advanced PACKAGE" in html)
    check("row name clipped to first 3 words (full name stays in title)",
          "const short3=" in html and "w.slice(0,3).join(' ')" in html and "short3(it.title)" in html)

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
