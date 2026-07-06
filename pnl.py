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
from collections import defaultdict
from pathlib import Path

import cfg
import clickup_cost
import db
import meta
import purchases
import revenue
import store
from paths import data_path

REPORTS = Path(__file__).with_name("reports")


def _load_cache(name):
    # Caches live on the persistent data dir; the old in-repo reports/ folder is a
    # legacy fallback (it gets wiped on every deploy).
    for p in (Path(data_path(name)), REPORTS / name):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except ValueError:
                continue
    return None


def _source_revenue(config, since=None, until=None):
    # Always the app's own orders (SQLite) — the Google-Sheet modes are retired.
    return revenue.from_orders(since=since, until=until), True


def _month_filter(agg, since, until=None):
    """Monthly-granular sources (meta, legacy cogs cache): keep months within [since, until]."""
    if (not since and not until) or not agg:
        return agg
    keep = {m: v for m, v in (agg.get("by_month") or {}).items()
            if (not since or m >= since[:7]) and (not until or m <= until[:7])}
    return {**agg, "by_month": keep, "total_usd": round(sum(keep.values()), 2)}


def _source_meta(config, since=None, until=None):
    # Manual mode (the default) is always "connected" — it's a valid $0 until the
    # owner types spend in Settings. API mode is connected only once the cache pulls.
    if cfg.get(config, "pnl.meta.mode", "manual") == "manual":
        return _month_filter(meta.from_manual(config), since, until), True
    cached = _load_cache("meta_cache.json")
    return _month_filter(cached or {"total_usd": 0, "by_month": {}}, since, until), cached is not None


def _po_date(po):
    """The date a PO counts on: order_placed when it looks like a date, else created_at."""
    d = (po.get("order_placed") or "").strip()
    if not (len(d) >= 10 and d[:4].isdigit() and d[4] == "-"):
        d = po.get("created_at") or ""
    return d


def _source_cogs(config, since=None, until=None):
    """Amazon cost = the PO totals staff type on the Purchases page — the in-app
    source of truth. (The old ClickUp cache is only a fallback when no POs exist.)"""
    pos = purchases.load().get("purchase_orders", [])
    if pos:
        aed = float(cfg.get(config, "fx.aed_per_usd", 3.6725))
        total, by_month = 0.0, defaultdict(float)
        for po in pos:
            d = _po_date(po)[:10]
            if (since and d < since) or (until and d > until):
                continue
            usd = po.get("total_usd")
            if usd in (None, "", 0) and po.get("total_aed"):
                usd = float(po["total_aed"]) / aed
            usd = float(usd or 0)
            total += usd
            by_month[d[:7] or "unknown"] += usd
        return {"total_usd": round(total, 2),
                "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
                "source": "Purchases page (PO totals)"}, True
    cached = _load_cache("cost_cache.json")
    return _month_filter(cached or {"total_usd": 0, "by_month": {}}, since, until), cached is not None


def _customers(since=None, until=None):
    """Distinct customers on PLACED orders in the window (for cost-per-customer)."""
    keys = set()
    for o in db.list_orders():
        if o["status"] not in store.PLACED_STATUSES:
            continue
        d = (o.get("created_at") or "")[:10]
        if (since and d < since) or (until and d > until):
            continue
        keys.add((store.primary_phone(o) or {}).get("e164") or o["customer"]["name"])
    return len([k for k in keys if k])


def _to_order(since=None, until=None):
    """Pending pipeline — priced orders still in the To-order queue (not yet placed),
    so the amount excluded from revenue stays visible. Returns (usd, count)."""
    total, n = 0.0, 0
    for o in db.list_orders():
        if o["status"] not in store.PREORDER_STATUSES:
            continue
        d = (o.get("created_at") or "")[:10]
        if (since and d < since) or (until and d > until):
            continue
        amt = o.get("amount_to_collect_usd")
        if amt is None:
            continue
        total += amt
        n += 1
    return round(total, 2), n


def build(config=None, since=None, until=None):
    config = config or cfg.load()
    rev, rev_ok = _source_revenue(config, since, until)
    cogs, cogs_ok = _source_cogs(config, since, until)
    mta, meta_ok = _source_meta(config, since, until)

    revenue_usd = rev.get("total_usd", 0) or 0
    cogs_usd = cogs.get("total_usd", 0) or 0
    meta_usd = mta.get("total_usd", 0) or 0
    gross = round(revenue_usd - cogs_usd, 2)
    net = round(gross - meta_usd, 2)
    customers = _customers(since, until)
    to_order_usd, to_order_count = _to_order(since, until)

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
        "since": since,
        "until": until,
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
            "to_order_usd": to_order_usd,          # pipeline: priced, not yet placed
            "to_order_count": to_order_count,
        },
        "sources": {
            "revenue": {"connected": rev_ok, "detail": rev.get("source", "orders (app db)")},
            "cogs": {"connected": cogs_ok,
                     "detail": cogs.get("source", "ClickUp cache" if cogs_ok else "not pulled yet")},
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
