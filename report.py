#!/usr/bin/env python3
"""
Build the order/money report that powers the dashboard. Reads orders.json
(always current) and produces a summary + per-order rows. Zero AI cost.

  report.build(db)            -> the live report dict (used by dashboard.py)
  report.write_history(rep)   -> append one compact line to reports/history.jsonl

    python3 report.py          # print the live summary to the terminal
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import store

REPORTS_DIR = Path(__file__).with_name("reports")

OPEN_STATUSES = {"REQUESTED", "QUOTED", "ORDERED", "SHIPPED", "ARRIVED", "DELIVERED"}


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _row(o):
    """Flatten an order into a dashboard-friendly row."""
    ph = store.primary_phone(o)
    return {
        "order_id": o["order_id"],
        "customer": o["customer"]["name"],
        "phone": ph["e164"] if ph else None,
        "wa": ph["wa"] if ph else None,
        "address": o["customer"]["address"],
        "items": [{"asin": it.get("asin"), "url": it.get("clean_url"),
                   "needs_expand": it.get("needs_expand")} for it in o["items"]],
        "n_items": len(o["items"]),
        "batch": o.get("batch"),
        "profile_box": o.get("profile_box"),
        "amount_to_collect_usd": o.get("amount_to_collect_usd"),
        "deposit_usd": round(o.get("deposit_usd") or 0, 2),
        "remaining_usd": (round((o.get("amount_to_collect_usd") or 0)
                                - (o.get("deposit_usd") or 0), 2)
                          if o.get("amount_to_collect_usd") is not None else None),
        "status": o["status"],
        "source": o.get("source"),
        "payment_plan": o.get("payment_plan"),
        "est_delivery_customer": o.get("est_delivery_customer"),   # gates the 📦 notify button
        "created_at": o.get("created_at"),
        "amazon_order_number": o.get("amazon_order_number"),
        "tracking_number": o.get("tracking_number"),
        "clickup_task_id": o.get("clickup_task_id"),
        "notes": o["customer"].get("notes", ""),
        "needs_expand": any(it.get("needs_expand") for it in o["items"]),
        "unpriced": o.get("amount_to_collect_usd") is None
                    and o["status"] != "CANCELLED",
    }


def build(db=None):
    db = db or store.load()
    orders = db.get("orders", [])
    rows = [_row(o) for o in orders]

    by_status = defaultdict(int)
    by_batch = defaultdict(lambda: {"count": 0, "usd": 0.0})
    by_profile = defaultdict(lambda: {"count": 0, "usd": 0.0})
    outstanding = collected = total_value = open_deposits = 0.0

    for r in rows:
        amt = r["amount_to_collect_usd"] or 0
        by_status[r["status"]] += 1
        b = r["batch"] or "—"
        by_batch[b]["count"] += 1
        by_batch[b]["usd"] += amt
        p = r["profile_box"] or "—"
        by_profile[p]["count"] += 1
        by_profile[p]["usd"] += amt
        if r["status"] not in ("CANCELLED",):
            total_value += amt
        if r["status"] in OPEN_STATUSES:
            outstanding += amt
            open_deposits += r["deposit_usd"]
        if r["status"] == "COLLECTED":
            collected += amt

    summary = {
        "total": len(rows),
        "by_status": dict(by_status),
        "money": {
            "outstanding_usd": round(outstanding, 2),
            "collected_usd": round(collected, 2),
            "total_value_usd": round(total_value, 2),
            "deposits_usd": round(sum(r["deposit_usd"] for r in rows), 2),
            "net_outstanding_usd": round(outstanding - open_deposits, 2),
        },
        "by_batch": {k: {"count": v["count"], "usd": round(v["usd"], 2)}
                     for k, v in sorted(by_batch.items())},
        "by_profile": {k: {"count": v["count"], "usd": round(v["usd"], 2)}
                       for k, v in sorted(by_profile.items())},
        "action_queue": {
            "need_order": sum(r["status"] == "REQUESTED" for r in rows),
            "ordered_not_shipped": sum(r["status"] == "ORDERED" for r in rows),
            "in_transit": sum(r["status"] == "SHIPPED" for r in rows),
            "arrived_not_collected": sum(
                r["status"] in ("ARRIVED", "DELIVERED") for r in rows),
            "needs_expand": sum(r["needs_expand"] for r in rows),
            "unpriced": sum(r["unpriced"] for r in rows),
        },
    }
    return {
        "generated_at": _now_iso(),
        "statuses": store.STATUSES,
        "summary": summary,
        "orders": rows,
    }


def write_history(rep):
    REPORTS_DIR.mkdir(exist_ok=True)
    s = rep["summary"]
    line = {"run_at": rep["generated_at"], "total": s["total"],
            "by_status": s["by_status"], "money": s["money"]}
    with (REPORTS_DIR / "history.jsonl").open("a") as f:
        f.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    rep = build()
    s = rep["summary"]
    print(f"Orders: {s['total']}")
    print("By status:", s["by_status"])
    print("Money:", json.dumps(s["money"], indent=2))
    print("Action queue:", json.dumps(s["action_queue"], indent=2))
    print("By batch:", json.dumps(s["by_batch"], ensure_ascii=False))
    print("By profile:", json.dumps(s["by_profile"], ensure_ascii=False))
