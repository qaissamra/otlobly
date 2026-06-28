#!/usr/bin/env python3
"""
Create a Purchase Order from a structured JSON blob — the concrete hook for the
"paste an Amazon order screenshot" flow: you paste the image to me in chat, I
extract the structure, and feed it here. Idempotent by amazon_order_number.

Input JSON (file path arg, or stdin):
{
  "amazon_order_number": "113-6397012-7559414",
  "ship_to": "Waleed Kharmah", "profile_box": "B19",
  "order_placed": "2026-06-15", "total_aed": 3100.98, "total_usd": 827.55,
  "packages": [
    {"arrival": "2026-07-09", "tracking_number": "GWD004697561",
     "items": [{"title": "Crave for Google Pixel 6 Case … Aqua", "asin": "B09CLKPMVC", "qty": 1}]}
  ]
}

  python3 import_po.py order.json
  pbpaste | python3 import_po.py -
"""

import json
import sys

import purchases
import store


def import_blob(blob):
    db = purchases.load()
    po = purchases.new_po(
        db, amazon_order_number=blob.get("amazon_order_number", ""),
        ship_to=blob.get("ship_to", ""), profile_box=blob.get("profile_box"),
        order_placed=blob.get("order_placed", ""),
        total_aed=blob.get("total_aed"), total_usd=blob.get("total_usd"),
        packages=blob.get("packages", []))
    purchases.attach_matches(po, store.load()["orders"])
    po, how = purchases.upsert(db, po)
    purchases.save(db)
    return po, how


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 import_po.py <file.json | ->")
    src = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1]).read()
    blob = json.loads(src)
    po, how = import_blob(blob)
    s = purchases.summary(po)
    print(f"{how.upper()} {po['po_id']}  (Amazon # {po['amazon_order_number']})")
    print(f"  {s['n_packages']} package(s) · {s['n_items']} item(s) · "
          f"{s['n_matched']} matched / {s['n_unmatched']} unmatched")
    for pkg in po["packages"]:
        print(f"  📦 pkg {pkg['package_no']} — arrives {pkg.get('arrival') or '—'} "
              f"· {pkg.get('tracking_number') or 'no tracking'}")
        for it in pkg["items"]:
            who = it.get("customer_name") or "⚠ UNMATCHED"
            print(f"      • {(it.get('title') or it.get('asin') or '')[:50]:50} → {who}")


if __name__ == "__main__":
    main()
