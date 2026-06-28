#!/usr/bin/env python3
"""
Amazon COGS — how much Otlobly PAID Amazon — pulled from the ClickUp
"Otloble AMAZON" space, "Orders" list.

The list's `Total Amount` field is *labelled* ILS (₪) but the numbers are
actually USD (₪827.55 means $827.55), so we use them as USD directly. Values
sit on the "Order #" parent tasks; product subtasks are empty and sum to 0.

  python3 clickup_cost.py --dry-run     # pull + print, don't write the cache
  python3 clickup_cost.py               # pull + write reports/cost_cache.json
  python3 clickup_cost.py --selftest    # offline aggregation test

Reuses clickup.py's HTTP layer + CLICKUP_API_TOKEN.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cfg
from clickup import _http, CLICKUP_API, TOKEN

CACHE = Path(__file__).with_name("reports") / "cost_cache.json"


def _month(ms):
    """epoch-ms (str/int) -> 'YYYY-MM', or 'unknown'."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m")
    except (ValueError, TypeError):
        return "unknown"


def _field_value(task, field_id):
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            v = cf.get("value")
            if v in (None, ""):
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None
    return None


def _field_text(task, field_id):
    for cf in task.get("custom_fields", []):
        if cf.get("id") == field_id:
            return cf.get("value")
    return None


def aggregate(tasks, amount_field, name_field=None, date_key="date_created"):
    """Sum the (USD-valued) amount field across tasks; bucket by month + customer."""
    total = 0.0
    by_month = defaultdict(float)
    by_customer = defaultdict(float)
    n = 0
    for t in tasks:
        amt = _field_value(t, amount_field)
        if amt is None:
            continue
        total += amt
        n += 1
        by_month[_month(t.get(date_key))] += amt
        if name_field:
            cust = (_field_text(t, name_field) or "—").strip() or "—"
            by_customer[cust] += amt
    return {
        "total_usd": round(total, 2),
        "orders_counted": n,
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "by_customer": {k: round(v, 2) for k, v in
                        sorted(by_customer.items(), key=lambda kv: -kv[1])},
    }


def fetch_tasks(list_id):
    out, page = [], 0
    while True:
        url = (f"{CLICKUP_API}/list/{list_id}/task"
               f"?include_closed=true&subtasks=true&page={page}")
        status, body = _http(url)
        if status != 200:
            sys.exit(f"ClickUp fetch failed ({status}): {body}")
        tasks = body.get("tasks", [])
        out.extend(tasks)
        if body.get("last_page", True) or not tasks:
            break
        page += 1
    return out


def pull(config=None):
    config = config or cfg.load()
    list_id = cfg.get(config, "pnl.amazon_cost.list_id")
    field_id = cfg.get(config, "pnl.amazon_cost.field_id")
    name_field = cfg.get(config, "pnl.amazon_cost.customer_field_id")
    if not TOKEN:
        return {"error": "Set CLICKUP_API_TOKEN to pull Amazon cost."}
    if not list_id or str(list_id).startswith("PASTE"):
        return {"error": "Set pnl.amazon_cost.list_id/field_id in config.json."}
    tasks = fetch_tasks(list_id)
    agg = aggregate(tasks, field_id, name_field)
    agg["pulled_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    agg["tasks_seen"] = len(tasks)
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print, don't write the cache.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return sys.exit(_selftest())

    agg = pull()
    if agg.get("error"):
        print(agg["error"])
        return
    print(f"Amazon cost (COGS): ${agg['total_usd']:.2f} across "
          f"{agg['orders_counted']} priced order(s) of {agg['tasks_seen']} tasks.")
    print("By month:", json.dumps(agg["by_month"]))
    if not args.dry_run:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(agg, ensure_ascii=False, indent=2))
        print(f"Wrote {CACHE.name}.")
    else:
        print("(DRY RUN — cache not written)")


def _selftest():
    AMT = "c7243d42-56b6-4a85-8634-e2f948f72be5"
    NAME = "6f1542fd-c52f-4bda-acf4-60f9bc4f36fa"
    tasks = [
        {"date_created": "1781546196616",
         "custom_fields": [{"id": AMT, "value": "488.86"},
                           {"id": NAME, "value": "yasmeen saad"}]},
        {"date_created": "1781546196616",  # subtask, empty amount -> ignored
         "custom_fields": [{"id": AMT, "value": None},
                           {"id": NAME, "value": "yasmeen saad"}]},
        {"date_created": "1783000000000",
         "custom_fields": [{"id": AMT, "value": 827.55},
                           {"id": NAME, "value": "كامل وريدات"}]},
    ]
    agg = aggregate(tasks, AMT, NAME)
    ok = (agg["total_usd"] == 1316.41 and agg["orders_counted"] == 2
          and agg["by_customer"]["كامل وريدات"] == 827.55)
    print("aggregate:", "OK" if ok else f"XX {agg}")
    return 0 if ok else 1


if __name__ == "__main__":
    main()
