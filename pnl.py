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
    """Monthly-granular sources (legacy cogs/meta cache): keep months within [since, until]."""
    if (not since and not until) or not agg:
        return agg
    keep = {m: v for m, v in (agg.get("by_month") or {}).items()
            if (not since or m >= since[:7]) and (not until or m <= until[:7])}
    return {**agg, "by_month": keep, "total_usd": round(sum(keep.values()), 2)}


def _day_window(by_day, since=None, until=None):
    """Keep {YYYY-MM-DD: usd} entries within [since, until] — day-accurate, like
    revenue & COGS. This is what lets a "last 7 days" filter move the Meta number."""
    return {d: v for d, v in (by_day or {}).items()
            if (not since or d >= since) and (not until or d <= until)}


def _roll_months(by_day):
    """Roll a daily dict up to {YYYY-MM: usd} for the by-month table."""
    out = defaultdict(float)
    for d, v in (by_day or {}).items():
        out[(d or "")[:7] or "unknown"] += v
    return {k: round(v, 2) for k, v in sorted(out.items())}


def _source_meta(config, since=None, until=None):
    # Manual mode (no API creds) is always "connected" — a valid $0 until the owner
    # types spend in Settings. API mode reads the cache the background sync keeps fresh;
    # connected = we have a cache with no last_error (a bad token surfaces as an error).
    if meta.effective_mode(config) == "manual":
        m = meta.from_manual(config)
        return _month_filter({**m, "source": "manual (Settings)"}, since, until), True
    cached = meta.read_cache() or {}
    err = cached.get("last_error")
    synced = cached.get("pulled_at") or cached.get("last_sync_at")
    detail = f"api error: {err}" if err else (f"api · synced {synced[:16].replace('T', ' ')}" if synced else "api · not pulled yet")
    # Campaign exclusions (P&L drill-down) are applied at read time, so a
    # checkbox toggle changes the totals instantly without a re-pull.
    by_month, by_day, _ = meta.apply_exclusions(cached, config)
    if by_day is not None:
        # Daily cache → filter to the exact day window, like revenue & COGS.
        kept = _day_window(by_day, since, until)
        out = {**cached, "by_month": _roll_months(kept),
               "total_usd": round(sum(kept.values()), 2), "source": detail}
    else:
        # Legacy monthly-only cache → month-granular filter (best available until
        # the next daily sync overwrites the cache).
        out = _month_filter({**cached, "by_month": by_month,
                             "total_usd": round(sum(by_month.values()), 2),
                             "source": detail}, since, until)
    return out, bool(cached) and not err


def _po_date(po):
    """The date a PO counts on: order_placed when it looks like a date, else created_at."""
    d = (po.get("order_placed") or "").strip()
    if not (len(d) >= 10 and d[:4].isdigit() and d[4] == "-"):
        d = po.get("created_at") or ""
    return d


def _cogs_po_rows(config, since=None, until=None):
    """Yield (po, usd) for every PO inside the window — the exact Amazon-cost
    selection (incl. the AED→USD conversion). Shared by the total and the drill."""
    aed = float(cfg.get(config, "fx.aed_per_usd", 3.6725))
    for po in purchases.load().get("purchase_orders", []):
        d = _po_date(po)[:10]
        if (since and d < since) or (until and d > until):
            continue
        usd = po.get("total_usd")
        if usd in (None, "", 0) and po.get("total_aed"):
            usd = float(po["total_aed"]) / aed
        yield po, float(usd or 0)


def _source_cogs(config, since=None, until=None):
    """Amazon cost = the PO totals staff type on the Purchases page — the in-app
    source of truth. (The old ClickUp cache is only a fallback when no POs exist.)"""
    if purchases.load().get("purchase_orders"):
        total, by_month = 0.0, defaultdict(float)
        for po, usd in _cogs_po_rows(config, since, until):
            total += usd
            by_month[_po_date(po)[:7] or "unknown"] += usd
        return {"total_usd": round(total, 2),
                "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
                "source": "Purchases page (PO totals)"}, True
    cached = _load_cache("cost_cache.json")
    return _month_filter(cached or {"total_usd": 0, "by_month": {}}, since, until), cached is not None


def _customer_rows(since=None, until=None):
    """Yield (key, order) for PLACED orders in the window — key is the customer's
    E.164 phone (or name). The exact cost-per-customer selection (note: unlike
    revenue it does NOT require a price). Shared by the count and the drill."""
    for o in db.list_orders():
        if o["status"] not in store.PLACED_STATUSES:
            continue
        d = (o.get("created_at") or "")[:10]
        if (since and d < since) or (until and d > until):
            continue
        yield (store.primary_phone(o) or {}).get("e164") or o["customer"]["name"], o


def _customers(since=None, until=None):
    """Distinct customers on PLACED orders in the window (for cost-per-customer)."""
    return len({k for k, _ in _customer_rows(since, until) if k})


def _preorder_rows(since=None, until=None):
    """Yield the priced To-order-queue orders inside the window — the exact
    pipeline selection. Shared by the total and the drill."""
    for o in db.list_orders():
        if o["status"] not in store.PREORDER_STATUSES:
            continue
        d = (o.get("created_at") or "")[:10]
        if (since and d < since) or (until and d > until):
            continue
        if o.get("amount_to_collect_usd") is None:
            continue
        yield o


def _to_order(since=None, until=None):
    """Pending pipeline — priced orders still in the To-order queue (not yet placed),
    so the amount excluded from revenue stays visible. Returns (usd, count)."""
    total, n = 0.0, 0
    for o in _preorder_rows(since, until):
        total += o["amount_to_collect_usd"]
        n += 1
    return round(total, 2), n


def _in_cart_rows():
    """Yield the priced orders staged in the Amazon cart (status IN_CART). A LIVE
    snapshot — not date-windowed — since 'the cart' is a now-state, so its P&L card
    and drill always match regardless of the P&L date filter."""
    for o in db.list_orders():
        if o["status"] not in store.CART_STATUSES:
            continue
        if o.get("amount_to_collect_usd") is None:
            continue
        yield o


def _in_cart(config):
    """In-cart preview: (revenue, count, cost, profit). Revenue = Σ amount_to_collect;
    cost = the one lump cart total typed on the In-cart page; profit = revenue − cost."""
    total, n = 0.0, 0
    for o in _in_cart_rows():
        total += o["amount_to_collect_usd"]
        n += 1
    cost = round(float(cfg.get(config, "pnl.in_cart.cost_usd", 0) or 0), 2)
    return round(total, 2), n, cost, round(total - cost, 2)


def _by_day(config, since=None, until=None):
    """Per-day activity for the P&L chart: revenue (order created_at), Amazon cost
    (PO date) and Meta ad spend (daily cache — empty in manual mode / legacy cache)
    merged into sorted rows. Same generators as the totals, so the days always sum
    back to the cards."""
    rev, cogs, ads = defaultdict(float), defaultdict(float), {}
    for o in revenue.placed_rows(None, since, until):
        rev[(o.get("created_at") or "")[:10]] += o["amount_to_collect_usd"]
    for po, usd in _cogs_po_rows(config, since, until):
        cogs[_po_date(po)[:10]] += usd
    if meta.effective_mode(config) == "api":
        _, ads_by_day, _ = meta.apply_exclusions(meta.read_cache() or {}, config)
        ads = _day_window(ads_by_day or {}, since, until)
    days = sorted(set(rev) | set(cogs) | set(ads))
    return [{"date": d, "revenue": round(rev.get(d, 0), 2), "cogs": round(cogs.get(d, 0), 2),
             "meta": round(ads.get(d, 0), 2),
             "net": round(rev.get(d, 0) - cogs.get(d, 0) - ads.get(d, 0), 2)}
            for d in days if d]


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
    in_cart_usd, in_cart_count, in_cart_cost_usd, in_cart_profit_usd = _in_cart(config)
    by_day = _by_day(config, since, until)
    days_active = sum(1 for r in by_day if r["revenue"] > 0)

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
            "in_cart_usd": in_cart_usd,            # staged in Amazon cart (live snapshot)
            "in_cart_count": in_cart_count,
            "in_cart_cost_usd": in_cart_cost_usd,  # one lump cart cost typed by the owner
            "in_cart_profit_usd": in_cart_profit_usd,
            # daily-activity stats (Activity chart): active = days with revenue
            "days_active": days_active,
            "avg_revenue_day_usd": round(revenue_usd / days_active, 2) if days_active else None,
            "avg_net_day_usd": round(net / days_active, 2) if days_active else None,
            "ad_days": sum(1 for r in by_day if r["meta"] > 0),
        },
        "sources": {
            "revenue": {"connected": rev_ok, "detail": rev.get("source", "orders (app db)")},
            "cogs": {"connected": cogs_ok,
                     "detail": cogs.get("source", "ClickUp cache" if cogs_ok else "not pulled yet")},
            "meta": {"connected": meta_ok, "detail": mta.get("source", "manual")},
        },
        "revenue_by_batch": rev.get("by_batch", {}),
        "by_month": by_month,
        "by_day": by_day,
    }


def _order_row(o):
    ph = store.primary_phone(o) or {}
    return {"order_id": o.get("order_id"),
            "customer": (o.get("customer") or {}).get("name") or "",
            "phone": ph.get("display") or ph.get("e164") or "",
            "status": o.get("status"),
            "batch": str(o.get("batch") or ""),
            "date": (o.get("created_at") or "")[:10],
            "amount_usd": o.get("amount_to_collect_usd")}


def drill(metric, config=None, since=None, until=None):
    """The rows behind one P&L card — computed with the SAME selection code as
    build(), so the breakdown always sums to the headline number.
    Raises ValueError on an unknown metric (route answers 400)."""
    config = config or cfg.load()
    base = {"metric": metric, "since": since, "until": until}

    if metric in ("revenue", "to_order"):
        src = (revenue.placed_rows(None, since, until) if metric == "revenue"
               else _preorder_rows(since, until))
        rows, total = [], 0.0
        for o in src:
            rows.append(_order_row(o))
            total += o["amount_to_collect_usd"]
        rows.sort(key=lambda r: r["date"], reverse=True)
        return {**base, "total_usd": round(total, 2), "count": len(rows), "rows": rows}

    if metric == "in_cart":
        # Live cart snapshot (ignores the date window) + the lump cost → profit preview.
        rows = [_order_row(o) for o in _in_cart_rows()]
        rows.sort(key=lambda r: r["date"], reverse=True)
        revenue_usd = round(sum(r["amount_usd"] or 0 for r in rows), 2)
        cost = round(float(cfg.get(config, "pnl.in_cart.cost_usd", 0) or 0), 2)
        return {**base, "total_usd": revenue_usd, "count": len(rows), "rows": rows,
                "cost_usd": cost, "profit_usd": round(revenue_usd - cost, 2)}

    if metric == "cogs":
        if purchases.load().get("purchase_orders"):
            rows, total = [], 0.0
            for po, usd in _cogs_po_rows(config, since, until):
                total += usd
                rows.append({"po_id": po.get("po_id"),
                             "amazon_order_number": po.get("amazon_order_number") or "",
                             "date": _po_date(po)[:10],
                             "amount_usd": round(usd, 2)})
            rows.sort(key=lambda r: r["date"], reverse=True)
            return {**base, "total_usd": round(total, 2), "count": len(rows),
                    "fallback": False, "rows": rows}
        cached = _month_filter(_load_cache("cost_cache.json") or {"total_usd": 0, "by_month": {}},
                               since, until)
        rows = [{"month": m, "amount_usd": v} for m, v in sorted((cached.get("by_month") or {}).items())]
        return {**base, "total_usd": cached.get("total_usd", 0) or 0, "count": len(rows),
                "fallback": True, "rows": rows}

    if metric == "customers":
        groups = {}
        for key, o in _customer_rows(since, until):
            if not key:
                continue
            ph = store.primary_phone(o) or {}
            g = groups.setdefault(key, {"name": (o.get("customer") or {}).get("name") or "",
                                        "phone": ph.get("display") or ph.get("e164") or "",
                                        "orders": 0, "total_usd": 0.0})
            g["orders"] += 1
            g["total_usd"] += o.get("amount_to_collect_usd") or 0
        rows = sorted(({**g, "total_usd": round(g["total_usd"], 2)} for g in groups.values()),
                      key=lambda r: -r["total_usd"])
        return {**base, "count": len(rows), "rows": rows}

    if metric == "meta":
        if meta.effective_mode(config) == "manual":
            m = _month_filter(meta.from_manual(config), since, until)
            return {**base, "mode": "manual", "total_usd": m.get("total_usd", 0),
                    "by_month": m.get("by_month", {}), "campaigns": None,
                    "excluded_campaign_ids": None, "legacy_filter": None,
                    "synced_at": None, "stale": False,
                    "note": "manual — type monthly spend in Settings"}
        cached = meta.read_cache() or {}
        by_month_incl, by_day_incl, campaigns = meta.apply_exclusions(cached, config)

        def _win(bd, bm):
            """Window total + by-month, day-accurate when daily data exists."""
            if bd is not None:
                k = _day_window(bd, since, until)
                return round(sum(k.values()), 2), _roll_months(k)
            m = _month_filter({"by_month": bm or {}}, since, until).get("by_month", {})
            return round(sum(m.values()), 2), m

        total, by_month = _win(by_day_incl, by_month_incl)
        camps_out = None
        if campaigns is not None:
            camps_out = []
            for c in campaigns:
                ct, cbm = _win(c.get("by_day"), c.get("by_month"))
                camps_out.append({"id": c["id"], "name": c["name"], "excluded": c["excluded"],
                                  "by_month": cbm, "total_usd": ct})
        excl = cfg.get(config, "pnl.meta.excluded_campaign_ids", None)
        legacy = cfg.get(config, "pnl.meta.campaign_filter")
        monthly_only = campaigns is not None and by_day_incl is None
        note = None
        if campaigns is None:
            note = "campaign breakdown appears after the next sync (a few minutes)"
        elif monthly_only and (since or until):
            note = "Meta spend is monthly for now — day-level filtering starts after the next sync (press ↻ Refresh sources)"
        elif excl is None and legacy:
            note = f"seeded from the old “{legacy}” filter — Save to make your own selection permanent"
        return {**base, "mode": "api",
                "total_usd": total, "by_month": by_month,
                "campaigns": camps_out, "excluded_campaign_ids": excl,
                "legacy_filter": legacy,
                "synced_at": cached.get("pulled_at") or cached.get("last_sync_at"),
                "stale": campaigns is None, "note": note}

    raise ValueError(f"unknown metric: {metric!r}")


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
