#!/usr/bin/env python3
"""
Profit / P&L — combine the three money flows into one USD picture:

    Revenue (from customers)  −  Amazon cost (COGS)  −  Meta ad spend  =  Net profit

Revenue and customer counts come straight from the app DB (db.py) every call.
Sources that need network/credentials (ClickUp Amazon cost, Meta API) are read
from their reports/*_cache.json, refreshed by running clickup_cost.py / meta.py
(or the dashboard's Refresh button).

  python3 pnl.py            # print the P&L statement + by-month table
"""

import json
from pathlib import Path

import cfg
import clickup_cost
import db
import meta
import revenue
import store

REPORTS = Path(__file__).with_name("reports")


def _load_cache(name):
    p = REPORTS / name
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            return None
    return None


def _source_revenue(config):
    # Always the app's own orders (SQLite) — the Google-Sheet modes are retired.
    return revenue.from_orders(), True


def _source_meta(config):
    if cfg.get(config, "pnl.meta.mode", "manual") == "manual":
        return meta.from_manual(config), True          # free/local, always live
    cached = _load_cache("meta_cache.json")
    return (cached or {"total_usd": 0, "by_month": {}}), cached is not None


def _source_cogs():
    cached = _load_cache("cost_cache.json")
    return (cached or {"total_usd": 0, "by_month": {}}), cached is not None


def _customers():
    """Distinct paying customers in the app DB (for cost-per-customer)."""
    keys = {(store.primary_phone(o) or {}).get("e164") or o["customer"]["name"]
            for o in db.list_orders() if o["status"] != "CANCELLED"}
    return len([k for k in keys if k])


def build(config=None):
    config = config or cfg.load()
    rev, rev_ok = _source_revenue(config)
    cogs, cogs_ok = _source_cogs()
    mta, meta_ok = _source_meta(config)

    revenue_usd = rev.get("total_usd", 0) or 0
    cogs_usd = cogs.get("total_usd", 0) or 0
    meta_usd = mta.get("total_usd", 0) or 0
    gross = round(revenue_usd - cogs_usd, 2)
    net = round(gross - meta_usd, 2)
    customers = _customers()

    months = sorted(set(rev.get("by_month", {})) | set(cogs.get("by_month", {}))
                    | set(mta.get("by_month", {})))
    by_month = []
    for m in months:
        r = rev.get("by_month", {}).get(m, 0)
        c = cogs.get("by_month", {}).get(m, 0)
        x = mta.get("by_month", {}).get(m, 0)
        by_month.append({"month": m, "revenue": round(r, 2), "cogs": round(c, 2),
                         "meta": round(x, 2), "net": round(r - c - x, 2)})

    return {
        "currency": "USD",
        "totals": {
            "revenue_usd": round(revenue_usd, 2),
            "cogs_usd": round(cogs_usd, 2),
            "gross_profit_usd": gross,
            "meta_usd": round(meta_usd, 2),
            "net_profit_usd": net,
            "gross_margin_pct": round(100 * gross / revenue_usd, 1) if revenue_usd else None,
            "net_margin_pct": round(100 * net / revenue_usd, 1) if revenue_usd else None,
            "customers": customers,
            "cost_per_customer_usd": round(meta_usd / customers, 2) if customers else None,
        },
        "sources": {
            "revenue": {"connected": rev_ok, "detail": rev.get("source", "orders (app db)")},
            "cogs": {"connected": cogs_ok,
                     "detail": "ClickUp Otloble AMAZON" if cogs_ok else "not pulled yet"},
            "meta": {"connected": meta_ok, "detail": mta.get("source", "manual")},
        },
        "revenue_by_batch": rev.get("by_batch", {}),
        "by_month": by_month,
    }


if __name__ == "__main__":
    p = build()
    t = p["totals"]
    print("=== Otlobly P&L (USD) ===")
    print(f"  Revenue (customers) : ${t['revenue_usd']:>10,.2f}")
    print(f"  Amazon cost (COGS)  : ${t['cogs_usd']:>10,.2f}"
          + ("" if p["sources"]["cogs"]["connected"] else "   ⚠ not pulled (set token, run clickup_cost.py)"))
    print(f"  Gross profit        : ${t['gross_profit_usd']:>10,.2f}"
          + (f"   ({t['gross_margin_pct']}%)" if t['gross_margin_pct'] is not None else ""))
    print(f"  Meta ad spend       : ${t['meta_usd']:>10,.2f}"
          + ("" if p["sources"]["meta"]["connected"] else "   ⚠ none (manual/api)"))
    print(f"  NET PROFIT          : ${t['net_profit_usd']:>10,.2f}"
          + (f"   ({t['net_margin_pct']}%)" if t['net_margin_pct'] is not None else ""))
    print(f"  Customers           : {t['customers']}"
          + (f"  ·  ${t['cost_per_customer_usd']}/customer" if t['cost_per_customer_usd'] is not None else ""))
    if p["by_month"]:
        print("\n  Month     Revenue      COGS      Meta       Net")
        for r in p["by_month"]:
            print(f"  {r['month']:8} {r['revenue']:9.2f} {r['cogs']:9.2f} "
                  f"{r['meta']:9.2f} {r['net']:9.2f}")
