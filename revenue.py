#!/usr/bin/env python3
"""
Revenue — how much Otlobly collects from customers (USD).

Computed from the app's own orders (SQLite via db.py — the source of truth):
sum amount_to_collect_usd over non-cancelled orders, bucketed by month of
created_at. The old Google-Sheet modes are gone; orders are created in the
app itself now.

  python3 revenue.py               # print the aggregation
  python3 revenue.py --selftest    # offline aggregation test
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

import db


def from_orders(orders=None):
    """Revenue from the app DB (non-cancelled), by month of created_at."""
    orders = db.list_orders() if orders is None else orders
    total, n = 0.0, 0
    by_month = defaultdict(float)
    for o in orders:
        if o["status"] == "CANCELLED":
            continue
        amt = o.get("amount_to_collect_usd")
        if amt is None:
            continue
        total += amt
        n += 1
        by_month[(o.get("created_at") or "")[:7] or "unknown"] += amt
    return _pack(total, n, by_month, "orders (app db)")


def _pack(total, n, by_month, source):
    return {
        "total_usd": round(total, 2),
        "orders_counted": n,
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def _selftest():
    sample = [
        {"status": "DELIVERED", "amount_to_collect_usd": 100.0, "created_at": "2026-01-05T10:00:00"},
        {"status": "REQUESTED", "amount_to_collect_usd": 50.5, "created_at": "2026-02-01T10:00:00"},
        {"status": "CANCELLED", "amount_to_collect_usd": 999.0, "created_at": "2026-02-02T10:00:00"},
        {"status": "ORDERED", "amount_to_collect_usd": None, "created_at": "2026-02-03T10:00:00"},
    ]
    agg = from_orders(sample)
    ok = (agg["total_usd"] == 150.5 and agg["orders_counted"] == 2
          and agg["by_month"] == {"2026-01": 100.0, "2026-02": 50.5})
    print("from_orders:", "OK" if ok else f"XX {agg}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return sys.exit(_selftest())
    agg = from_orders()
    print(f"Revenue: ${agg['total_usd']:.2f} across {agg['orders_counted']} "
          f"order(s)  [source: {agg['source']}]")
    print("By month:", json.dumps(agg["by_month"]))


if __name__ == "__main__":
    main()
