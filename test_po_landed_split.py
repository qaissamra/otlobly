#!/usr/bin/env python3
"""
Self-checks: the 💵 PO landed-cost split (landed.py + the purchases/API wiring).

One Amazon checkout mixes Otlobly customer items, IT products and Le Luxe
watches. Its shipping/tax/import is charged on the CART, so pricing a category
alone always costs MORE than the combined order — the categories can only be
costed by sharing the real bill out pro-rata by value.

What's pinned here is the promise that makes the feature trustworthy: the split
ALWAYS re-sums to what was actually paid, to the cent, however awkward the
numbers. Plus the storage round-trip (the whitelist silently drops unknown keys)
and the COGS boundary (the split IS cost — fulfillment must never see it).

landed.py's own algorithm cases live in `python3 landed.py --selftest`; this
suite covers the parts that only exist once the module is wired into the app.

    ./.venv/bin/python test_po_landed_split.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-landed-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402
import landed          # noqa: E402
import purchases       # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def _mixed_po(total, costs=None):
    """The real shape: one checkout, three books, one shared shipment."""
    return {"amazon_order_number": "113-MIX", "ship_to": "Buyer",
            "total_usd": total, "costs": costs,
            "packages": [{"package_no": 1, "tracking_number": "GWDMIX0001", "items": [
                {"title": "Rolex", "asin": "B0WATCH001", "qty": 1,
                 "bucket": "watches", "unit_paid_usd": 900},
                {"title": "RTX 5090", "asin": "B0GPU00001", "qty": 2,
                 "bucket": "it", "unit_paid_usd": 250},
                {"title": "Headphones", "asin": "B0OTLOB001", "qty": 1,
                 "bucket": "otlobly", "unit_paid_usd": 100},
            ]}]}


def main():
    db.init_db()
    db.create_user("ls-adm", auth.hash_pw("s1"), "admin", "Owner", business_id=1)
    db.create_user("ls-ful", auth.hash_pw("s1"), "fulfillment", "Packer", business_id=1)
    adm, ful = client("ls-adm"), client("ls-ful")

    print("— storage round-trip (the whitelist drops what it doesn't name) —")
    pdb = {"purchase_orders": [], "seq": 0}
    po, _ = purchases.save_full(pdb, _mixed_po(
        1600, {"items_usd": 1500, "shipping_usd": 60, "tax_usd": 15, "import_usd": 25}), [])
    check("costs survive the save",
          po["costs"]["shipping_usd"] == 60 and po["costs"]["import_usd"] == 25)
    it0 = po["packages"][0]["items"][0]
    check("bucket + unit_paid survive the save",
          it0["bucket"] == "watches" and it0["unit_paid_usd"] == 900)
    check("an unknown bucket is refused, not stored",
          purchases._norm_item({"bucket": "sneakers"})["bucket"] is None)

    # the board posts the WHOLE PO on every edit — an older client that predates
    # the breakdown must not wipe it (same rule as `custom` / `pkg_images`)
    stale = {k: v for k, v in po.items() if k != "costs"}
    po2, _ = purchases.save_full(pdb, stale, [])
    check("a client that omits costs keeps what's on disk",
          po2["costs"]["shipping_usd"] == 60)
    po2["costs"] = None
    po3, _ = purchases.save_full(pdb, po2, [])
    check("an explicit null clears it (a delete must go through)",
          po3["costs"] is None)

    print("— the invariant: categories always re-sum to what was paid —")
    s = landed.split_po(po)
    check("Σ categories == the paid total",
          round(sum(b["landed_usd"] for b in s["by_bucket"].values()), 2) == 1600.0)
    check("Σ lines == the paid total", s["landed_usd"] == 1600.0)
    check("balanced breakdown", s["balanced"] is True)
    # $100 of extras over $1500 of goods: watches carry 900/1500, GPUs 500/1500
    check("watches carry their value share", s["by_bucket"]["watches"]["landed_usd"] == 960.0)
    check("graphics cards carry theirs", s["by_bucket"]["it"]["landed_usd"] == 533.33)
    check("otlobly carries the rounding cent", s["by_bucket"]["otlobly"]["landed_usd"] == 106.67)

    # the point of the whole feature, stated as a test: buying the watches on
    # their own would NOT have cost 900 + the full $100 of extras
    check("a category is never charged the whole shipment's extras",
          s["by_bucket"]["watches"]["extras_usd"] < 100)

    print("— awkward money that doesn't divide —")
    pdb2 = {"purchase_orders": [], "seq": 0}
    odd, _ = purchases.save_full(pdb2, {
        "amazon_order_number": "113-ODD", "ship_to": "B", "total_usd": 100.01,
        "costs": {"items_usd": 90, "shipping_usd": 10.01},
        "packages": [{"package_no": 1, "items": [
            {"title": "a", "qty": 1, "unit_paid_usd": 30, "bucket": "it"},
            {"title": "b", "qty": 1, "unit_paid_usd": 30, "bucket": "it"},
            {"title": "c", "qty": 1, "unit_paid_usd": 30, "bucket": "watches"},
        ]}]}, [])
    s = landed.split_po(odd)
    check("three equal lines, indivisible cents, still exact", s["landed_usd"] == 100.01)
    check("the odd cent goes to exactly one line",
          sorted(l["extras_usd"] for l in s["lines"]) == [3.33, 3.34, 3.34])
    check("re-splitting puts the cent in the same place",
          [l["extras_usd"] for l in landed.split_po(odd)["lines"]]
          == [l["extras_usd"] for l in s["lines"]])

    print("— lump mode: only the paid total typed (today's data) —")
    lump, _ = purchases.save_full(pdb2, {
        "amazon_order_number": "113-LUMP", "ship_to": "B", "total_usd": 827.55,
        "packages": [{"package_no": 1, "items": [
            {"title": "a", "qty": 1, "unit_paid_usd": 400, "bucket": "watches"},
            {"title": "b", "qty": 1, "unit_paid_usd": 179.24, "bucket": "otlobly"},
        ]}]}, [])
    s = landed.split_po(lump)
    check("no breakdown → the gap is what gets shared", s["basis"] == "lump"
          and s["extras_usd"] == 248.31)
    check("lump split still re-sums to the paid total", s["landed_usd"] == 827.55)

    print("— API —")
    r = adm.post("/api/purchase", json=_mixed_po(
        1600, {"items_usd": 1500, "shipping_usd": 60, "tax_usd": 15, "import_usd": 25})).get_json()
    po_id = r["po_id"]
    d = adm.post("/api/purchase/split", json={"po_id": po_id}).get_json()
    check("split by po_id", d["landed_usd"] == 1600.0 and len(d["lines"]) == 3)
    d = adm.post("/api/purchase/split", json={"po_ids": [po_id]}).get_json()
    check("batch split (one request for the whole board)",
          d["splits"][po_id]["landed_usd"] == 1600.0)
    draft = _mixed_po(1600, {"items_usd": 1500, "shipping_usd": 100})
    d = adm.post("/api/purchase/split", json={"po": draft}).get_json()
    check("an UNSAVED draft previews the same numbers",
          d["by_bucket"]["watches"]["landed_usd"] == 960.0)
    check("a missing PO is 404, not a crash",
          adm.post("/api/purchase/split", json={"po_id": "PO-9999"}).status_code == 404)

    d = adm.post("/api/purchase/parse_summary", json={"text":
        "Items (4):  $1,500.00\nShipping & handling: $68.99\n"
        "Free Shipping Promo: -$8.99\nEstimated tax to be collected: $15.00\n"
        "Estimated Import Charges: $25.00\nOrder total: $1,600.00"}).get_json()
    check("pasted summary parses", d["ok"] and d["parsed"]["items_usd"] == 1500.0
          and d["parsed"]["promo_usd"] == 8.99 and d["parsed"]["order_total_usd"] == 1600.0)
    check("a pasted summary balances against the total",
          landed.split_po({"total_usd": 1600.0,
                           "costs": landed.clean_costs(d["parsed"]),
                           "packages": []})["delta_usd"] == 0.0)

    print("— the COGS boundary (the split IS cost) —")
    check("fulfillment cannot ask for a split",
          ful.post("/api/purchase/split", json={"po_id": po_id}).status_code == 403)
    check("fulfillment cannot parse a summary either",
          ful.post("/api/purchase/parse_summary", json={"text": "Items: $5.00"}).status_code == 403)
    pf = next(p for p in ful.get("/api/purchases").get_json()["purchase_orders"]
              if p["po_id"] == po_id)
    check("the board list hides the breakdown + category totals",
          pf["costs"] is None and pf["by_bucket"] is None)
    check("and the per-line paid prices with it",
          all(i["unit_paid_usd"] is None and i["extra_override_usd"] is None
              for i in pf["packages"][0]["items"]))
    pa = next(p for p in adm.get("/api/purchases").get_json()["purchase_orders"]
              if p["po_id"] == po_id)
    check("the owner still sees the category totals",
          pa["by_bucket"]["watches"]["landed_usd"] == 960.0)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
