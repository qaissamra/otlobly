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
import store


def placed_rows(orders=None, since=None, until=None):
    """Yield the PLACED (ORDERED..COLLECTED), priced orders inside the window —
    the exact revenue selection. Shared by the total and the P&L drill-down so
    the breakdown always sums to the headline number."""
    orders = db.list_orders() if orders is None else orders
    for o in orders:
        if o["status"] not in store.PLACED_STATUSES:
            continue
        d = (o.get("created_at") or "")[:10]
        if (since and d < since) or (until and d > until):
            continue
        if o.get("amount_to_collect_usd") is None:
            continue
        yield o


def from_orders(orders=None, since=None, until=None):
    """Revenue = only orders that were actually PLACED (ORDERED..COLLECTED) — a
    To-order quote (REQUESTED/QUOTED/PAID) is not revenue until it's ordered.
    By month of created_at + by batch. since/until = 'YYYY-MM-DD' inclusive bounds."""
    total, n = 0.0, 0
    by_month = defaultdict(float)
    by_batch = defaultdict(float)
    for o in placed_rows(orders, since, until):
        amt = o["amount_to_collect_usd"]
        total += amt
        n += 1
        by_month[(o.get("created_at") or "")[:7] or "unknown"] += amt
        by_batch[str(o.get("batch") or "—")] += amt
    return _pack(total, n, by_month, "orders (app db)", by_batch)


def _pack(total, n, by_month, source, by_batch=None):
    return {
        "total_usd": round(total, 2),
        "orders_counted": n,
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "by_batch": {k: round(v, 2) for k, v in sorted((by_batch or {}).items())},
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def _selftest():
    sample = [
        {"status": "DELIVERED", "amount_to_collect_usd": 100.0, "created_at": "2026-01-05T10:00:00"},
        {"status": "ORDERED", "amount_to_collect_usd": 50.5, "created_at": "2026-02-01T10:00:00"},
        {"status": "REQUESTED", "amount_to_collect_usd": 999.0, "created_at": "2026-02-02T10:00:00"},  # To-order: NOT revenue
        {"status": "CANCELLED", "amount_to_collect_usd": 999.0, "created_at": "2026-02-02T10:00:00"},
        {"status": "SHIPPED", "amount_to_collect_usd": None, "created_at": "2026-02-03T10:00:00"},      # no price: skipped
    ]
    agg = from_orders(sample)
    win = from_orders(sample, since="2026-02-01", until="2026-02-28")
    ok = (agg["total_usd"] == 150.5 and agg["orders_counted"] == 2
          and agg["by_month"] == {"2026-01": 100.0, "2026-02": 50.5}
          and win["total_usd"] == 50.5)   # since/until window drops the January placed order
    print("from_orders:", "OK" if ok else f"XX {agg} / {win}")
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
