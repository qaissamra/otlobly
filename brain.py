#!/usr/bin/env python3
"""
The Brain — the per-tenant command center behind the dashboard's landing page.

One pass over the tenant's orders (+ new leads, + quota) derives everything the
owner needs to ACT on right now:

  🔥 urgent    — do it today (backlog to buy, unpriced, overdue ETAs, COD waiting,
                 new leads, over-quota)
  ⏰ due       — customer ETAs / Amazon arrivals in the next few days
  🧠 forgotten — items sitting in a state longer than they should (staleness)
  💰 money     — the three numbers that matter (net outstanding / deposits held /
                 collect-now)
  📈 results   — the last 7 days, computed from order date stamps
                 (reports/history.jsonl is GLOBAL, so it can't serve per-tenant)

`build()` returns pure data — no HTML — so the same payload can feed the
dashboard today and a Telegram digest later. Tenancy is implicit: every read is
scoped by db.current_business(); pass business_id only for feature/tier lookups.

    ./.venv/bin/python brain.py     # print the current tenant's brain
"""

from datetime import date, datetime, timedelta, timezone

import db
import money

# Staleness thresholds (days) — the "forgotten" rules. One place to tune.
STALE_REQUESTED_D = 2     # asked for a quote/purchase, still not moved
STALE_QUOTED_D = 3        # quoted, customer never paid/confirmed
STALE_ORDERED_D = 14      # bought on Amazon, still not shipped
STALE_SHIPPED_D = 21      # in transit too long
STALE_ARRIVED_D = 5       # arrived/delivered, COD still not collected
STALE_LEAD_D = 2          # lead still 'new'
STALE_OPEN_D = 10         # catch-all: any open order untouched this long
ETA_SOON_D = 3            # customer ETA within N days → "due"
AMZ_ARRIVAL_SOON_D = 2    # Amazon arrival within N days → "due"
RESULTS_WINDOW_D = 7
MAX_ITEMS = 8             # per-rule item cap (count still carries the true total)

OPEN_STATUSES = {"REQUESTED", "QUOTED", "ORDERED", "SHIPPED", "ARRIVED", "DELIVERED"}
_DONE = ("DELIVERED", "COLLECTED", "CANCELLED")


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_date(s):
    """ISO stamp or YYYY-MM-DD → date, else None (a None anchor skips the rule)."""
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _days_since(s, today):
    d = _to_date(s)
    return (today - d).days if d else None


def _days_until(s, today):
    d = _to_date(s)
    return (d - today).days if d else None


def _first(*vals):
    for v in vals:
        if v:
            return v
    return None


def _item(id_, icon, title, detail="", *, amount_usd=None, age_days=None,
          due=None, severity="info", view="orders", arg=None):
    return {"id": id_, "icon": icon, "title": title, "detail": detail,
            "amount_usd": amount_usd, "age_days": age_days, "due": due,
            "severity": severity, "link": {"view": view, "arg": arg}}


def _section(key, title, icon, items, count=None):
    sev = ("urgent" if any(i["severity"] == "urgent" for i in items)
           else "soon" if any(i["severity"] == "soon" for i in items) else "info")
    return {"key": key, "title": title, "icon": icon, "severity": sev,
            "count": count if count is not None else len(items), "items": items}


def _cap(items):
    """Oldest / most-overdue first, capped for the payload; caller keeps the count."""
    items.sort(key=lambda i: -(i["age_days"] or 0))
    return items[:MAX_ITEMS]


def build(business_id=None):
    """The tenant's brain. All order/lead reads are scoped by the tenancy
    contextvar; `business_id` is only for the feature/tier lookups."""
    import features
    import quotas
    bid = business_id or db.current_business()
    today = date.today()
    orders = db.list_orders()

    urgent, due, forgotten, seen_forgotten = [], [], [], set()
    # aggregates over one pass
    n_requested = n_unpriced = n_expand = 0
    oldest_requested = None
    arrived_orders = []
    outstanding = open_deposits = collect_now = money.D(0)
    res = {"new_orders": 0, "new_orders_value_usd": money.D(0),
           "delivered": 0, "collected": 0, "collected_usd": money.D(0)}

    def forget(o, icon, title, age, detail=""):
        if o["order_id"] in seen_forgotten:      # first matching rule wins
            return
        seen_forgotten.add(o["order_id"])
        forgotten.append(_item(o["order_id"], icon, title,
                               detail or f'{o["customer"]["name"]}',
                               amount_usd=o.get("amount_to_collect_usd"),
                               age_days=age, view="orders", arg=o["order_id"]))

    for o in orders:
        st = o.get("status")
        oid = o.get("order_id")
        amt = money.D(o.get("amount_to_collect_usd") or 0)
        dep = money.D(o.get("deposit_usd") or 0)
        name = (o.get("customer") or {}).get("name") or ""

        # ---- money accumulators (same Decimal idiom as report.py) ----
        if st in OPEN_STATUSES:
            outstanding += amt
            open_deposits += dep
        if st in ("ARRIVED", "DELIVERED"):
            collect_now += amt - dep
            arrived_orders.append(o)

        # ---- urgent counters ----
        if st == "REQUESTED":
            n_requested += 1
            age = _days_since(o.get("created_at"), today)
            if age is not None and (oldest_requested is None or age > oldest_requested):
                oldest_requested = age
        if o.get("amount_to_collect_usd") is None and st != "CANCELLED":
            n_unpriced += 1
        if any(it.get("needs_expand") for it in o.get("items") or []):
            n_expand += 1

        # ---- ETAs: overdue → urgent, close → due ----
        if st not in _DONE:
            left = _days_until(o.get("est_delivery_customer"), today)
            if left is not None and left < 0:
                urgent.append(_item(oid, "⏰", f"{oid} is {-left}d past its promised ETA",
                                    f'{name} · promised {o["est_delivery_customer"]}',
                                    amount_usd=o.get("amount_to_collect_usd"),
                                    age_days=-left, due=o["est_delivery_customer"],
                                    severity="urgent", view="orders", arg=oid))
            elif left is not None and left <= ETA_SOON_D:
                due.append(_item(oid, "📅", f"{oid} promised to the customer "
                                            + ("today" if left == 0 else f"in {left}d"),
                                 name, amount_usd=o.get("amount_to_collect_usd"),
                                 due=o["est_delivery_customer"], severity="soon",
                                 view="orders", arg=oid))
        if st in ("ORDERED", "SHIPPED"):
            left = _days_until(o.get("amazon_arrival"), today)
            if left is not None and 0 <= left <= AMZ_ARRIVAL_SOON_D:
                due.append(_item(oid, "📦", f"{oid} lands from Amazon "
                                            + ("today" if left == 0 else f"in {left}d"),
                                 name, due=o["amazon_arrival"], severity="soon",
                                 view="purchases", arg=oid))

        # ---- forgotten (staleness), first matching rule wins ----
        if st == "REQUESTED":
            age = _days_since(o.get("created_at"), today)
            if age is not None and age > STALE_REQUESTED_D:
                forget(o, "💡", f"{oid} waiting {age}d to be ordered", age)
        elif st == "QUOTED" and not o.get("paid_at"):
            age = _days_since(_first(o.get("quoted_at"), o.get("created_at")), today)
            if age is not None and age > STALE_QUOTED_D:
                forget(o, "💬", f"{oid} quoted {age}d ago — customer never confirmed", age)
        elif st == "ORDERED":
            age = _days_since(_first(o.get("placed_at"), o.get("updated_at"),
                                     o.get("created_at")), today)
            if age is not None and age > STALE_ORDERED_D:
                forget(o, "📦", f"{oid} bought {age}d ago — still not shipped", age)
        elif st == "SHIPPED":
            age = _days_since(o.get("updated_at"), today)
            if age is not None and age > STALE_SHIPPED_D:
                forget(o, "🚚", f"{oid} in transit {age}d — check the parcel", age)
        elif st in ("ARRIVED", "DELIVERED"):
            age = _days_since(_first(o.get("amazon_arrival"), o.get("delivered_at"),
                                     o.get("updated_at")), today)
            if age is not None and age > STALE_ARRIVED_D:
                forget(o, "💵", f"{oid} arrived {age}d ago — money not collected", age)
        if st in OPEN_STATUSES:
            age = _days_since(o.get("updated_at"), today)
            if age is not None and age > STALE_OPEN_D:
                forget(o, "🧠", f"{oid} untouched for {age}d", age)

        # ---- 7-day results from date stamps ----
        cage = _days_since(o.get("created_at"), today)
        if cage is not None and 0 <= cage < RESULTS_WINDOW_D:
            res["new_orders"] += 1
            if st != "CANCELLED":
                res["new_orders_value_usd"] += amt
        dage = _days_since(o.get("delivered_at"), today)
        if dage is not None and 0 <= dage < RESULTS_WINDOW_D:
            res["delivered"] += 1
        # no collected_at exists; COLLECTED + fresh updated_at is the best proxy
        # (the collect flip is almost always the last edit an order gets)
        uage = _days_since(o.get("updated_at"), today)
        if st == "COLLECTED" and uage is not None and 0 <= uage < RESULTS_WINDOW_D:
            res["collected"] += 1
            res["collected_usd"] += amt

    # ---- urgent aggregates (after the pass) ----
    agg = []
    if n_requested:
        agg.append(_item("rule:need_order", "💡",
                         f"{n_requested} order{'s' if n_requested > 1 else ''} waiting to be bought",
                         f"oldest {oldest_requested}d" if oldest_requested else "",
                         age_days=oldest_requested, severity="urgent", view="needorder"))
    if n_unpriced:
        agg.append(_item("rule:unpriced", "⚠",
                         f"{n_unpriced} order{'s' if n_unpriced > 1 else ''} with no price yet",
                         "quote them so totals are right", severity="urgent", view="needorder"))
    if n_expand:
        agg.append(_item("rule:needs_expand", "🔗",
                         f"{n_expand} a.co link{'s' if n_expand > 1 else ''} to expand",
                         severity="urgent", view="needorder"))
    if arrived_orders:
        agg.append(_item("rule:collect", "💵",
                         f"{len(arrived_orders)} arrived — collect the money",
                         amount_usd=money.to_usd(collect_now),
                         severity="urgent", view="orders"))
    urgent = agg + urgent

    # new leads waiting (feature-gated: leads is Otlobly-only unless enabled)
    if features.has(bid, "leads"):
        new_leads = db.list_leads(status="new")
        if new_leads:
            oldest = max((_days_since(l.get("created_time"), today) or 0) for l in new_leads)
            urgent.append(_item("rule:leads", "📣",
                                f"{len(new_leads)} new lead{'s' if len(new_leads) > 1 else ''} waiting",
                                f"oldest {oldest}d" if oldest else "",
                                age_days=oldest or None, severity="urgent", view="metaleads"))
            stale_leads = [l for l in new_leads
                           if (_days_since(l.get("created_time"), today) or 0) > STALE_LEAD_D]
            for l in stale_leads[:MAX_ITEMS]:
                a = _days_since(l.get("created_time"), today)
                forgotten.append(_item(l["lead_id"], "📣",
                                       f"lead {l.get('name') or l['lead_id']} still new after {a}d",
                                       l.get("source") or "", age_days=a, view="metaleads"))

    # over quota (brokers only — Otlobly is unlimited)
    qs = quotas.status(bid)
    if not qs.get("unlimited") and qs.get("over_any"):
        over = [r for r, v in (qs.get("resources") or {}).items() if v.get("over")]
        urgent.append(_item("rule:quota", "🚦",
                            "plan limit reached: " + ", ".join(over),
                            "upgrade the plan or free capacity",
                            severity="urgent", view="settings"))

    money_items = [
        _item("rule:net_outstanding", "💰", "net outstanding",
              "what customers still owe (after deposits)",
              amount_usd=money.to_usd(outstanding - open_deposits), view="orders"),
        _item("rule:deposits_held", "🧾", "deposits held",
              "عربون already paid on open orders",
              amount_usd=money.to_usd(open_deposits), view="deposits"),
        _item("rule:collect_now", "💵", "to collect now",
              "arrived orders — COD waiting",
              amount_usd=money.to_usd(collect_now), view="orders"),
    ]

    n_urgent, n_due, n_forgotten = len(urgent), len(due), len(forgotten)
    return {
        "generated_at": _now_iso(),
        "sections": [
            _section("urgent", "Urgent · الآن", "🔥", _cap(urgent), n_urgent),
            _section("due", "Due soon · مواعيد", "⏰",
                     sorted(due, key=lambda i: i["due"] or "9999")[:MAX_ITEMS], n_due),
            _section("forgotten", "Forgotten · منسي", "🧠", _cap(forgotten), n_forgotten),
            _section("money", "Money · فلوس", "💰", money_items),
        ],
        "results": {
            "window_days": RESULTS_WINDOW_D,
            "new_orders": res["new_orders"],
            "new_orders_value_usd": money.to_usd(res["new_orders_value_usd"]),
            "delivered": res["delivered"],
            "collected": res["collected"],
            "collected_usd": money.to_usd(res["collected_usd"]),
        },
    }


if __name__ == "__main__":
    import json as _json
    db.init_db()
    print(_json.dumps(build(), ensure_ascii=False, indent=2))
