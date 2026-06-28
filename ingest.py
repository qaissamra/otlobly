#!/usr/bin/env python3
"""
Ingest the legacy Otlobly Google-Sheet export (CSV) into orders.json.

Cleans every row through normalize.py (phones, links, ASINs), de-dupes
customers/orders, maps the free-text status, and prints a before/after table.
Idempotent: re-running updates matching orders instead of duplicating them.

    python3 ingest.py "~/Downloads/Otlilbly - Orders.csv"
    python3 ingest.py <file> --expand     # also resolve a.co/… shorteners (network)
    python3 ingest.py <file> --limit 5 --dry-run
"""

import argparse
import csv
import os
import re
import sys

import normalize
import store

# Column layout of the sheet (0-indexed). Header row:
# Name, , number, address, link, link, link, amount USD, price ILS,
# Order N, Status, ID, عبرون(deposit), Name device
COL = {"name": 0, "phone_a": 1, "phone_b": 2, "address": 3,
       "links": (4, 5, 6), "amount_usd": 7, "order_n": 9,
       "status": 10, "deposit": 12, "profile": 13}


def parse_usd(cell):
    """'68.21$' / '139.79' / '1,234' -> float, or None."""
    if not cell:
        return None
    s = normalize.ascii_digits(str(cell))
    s = re.sub(r"[^\d.]", "", s)
    try:
        return round(float(s), 2) if s else None
    except ValueError:
        return None


def is_data_row(row):
    """Skip the blank/summary rows at the bottom of the sheet."""
    name = row[COL["name"]].strip() if len(row) > COL["name"] else ""
    phones = (row[COL["phone_a"]] + row[COL["phone_b"]]) if len(row) > COL["phone_b"] else ""
    links = "".join(row[i] for i in COL["links"] if i < len(row))
    return bool(name or phones.strip() or links.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path to the Otlobly orders CSV export")
    ap.add_argument("--expand", action="store_true",
                    help="Resolve a.co/amzn.to shorteners over the network.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and print, but don't write orders.json.")
    args = ap.parse_args()

    path = os.path.expanduser(args.csv_path)
    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")

    db = store.load()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))[1:]            # drop header

    data_rows = [r for r in rows if is_data_row(r)]
    if args.limit:
        data_rows = data_rows[:args.limit]

    created = updated = 0
    print(f"{'ORDER':9} {'CUSTOMER':22} {'PHONE':16} {'ITEMS':5} "
          f"{'USD':>8}  {'BOX':4} STATUS")
    print("-" * 86)
    for r in data_rows:
        r += [""] * (14 - len(r))                 # pad short rows
        phones = normalize.collect_phones(r[COL["phone_a"]], r[COL["phone_b"]])
        items = normalize.parse_items([r[i] for i in COL["links"]],
                                      expand=args.expand)
        deposit_raw = r[COL["deposit"]].strip()
        notes = f"عربون/Deposit: {deposit_raw}" if deposit_raw else ""

        order = store.new_order(
            db,
            name=r[COL["name"]],
            phones=phones,
            address=r[COL["address"]],
            items=items,
            batch=r[COL["order_n"]],
            profile_box=r[COL["profile"]].strip() or None,
            status=store.map_sheet_status(r[COL["status"]]),
            amount_to_collect_usd=parse_usd(r[COL["amount_usd"]]),
            notes=notes,
        )
        order, how = store.upsert(db, order)
        created += how == "created"
        updated += how == "updated"

        ph = phones[0]["display"] if phones else "—"
        name = (order["customer"]["name"] or "—")[:22]
        flag = " ⚠a.co" if any(it["needs_expand"] for it in items) else ""
        print(f"{order['order_id']:9} {name:22} {ph:16} {len(items):^5} "
              f"{(order['amount_to_collect_usd'] or 0):8.2f}  "
              f"{(order['profile_box'] or '—'):4} {order['status']}{flag}")

    print("-" * 86)
    print(f"{created} created · {updated} updated · {len(data_rows)} rows"
          + ("  (DRY RUN — nothing written)" if args.dry_run else ""))
    if not args.dry_run:
        store.save(db)
        print(f"Wrote {store.STORE_FILE.name} ({len(db['orders'])} orders total).")


if __name__ == "__main__":
    main()
