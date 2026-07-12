#!/usr/bin/env python3
"""
Tatabu plan tiers + quota metering (Phase 10).

Three tiers cap what a broker tenant can do; Otlobly (business #1) is unlimited.
Enforcement is SOFT — the dashboard surfaces usage + an "upgrade" nudge when a
resource is over its limit, but nothing is blocked mid-task (owner decision).

Metered resources:
  searches — SerpAPI/product lookups this month (a per-action counter)
  packages — packages the broker is tracking (live count across its POs)
  seats    — active staff logins (live count)
  orders   — orders held (live count)
"""

from datetime import datetime

import db

TIERS = {
    "starter": {"searches": 250,  "packages": 250,  "seats": 3,  "orders": 250},
    "growth":  {"searches": 750,  "packages": 750,  "seats": 8,  "orders": 1000},
    "pro":     {"searches": 2000, "packages": 2000, "seats": 20, "orders": 3000},
}
RESOURCES = ("searches", "packages", "seats", "orders")
DEFAULT_TIER = "starter"


def period():
    return datetime.now().strftime("%Y-%m")


def tier(business_id):
    """A business's plan. Business #1 (Otlobly) is 'unlimited'; brokers default to starter."""
    if (business_id or 1) == 1:
        return "unlimited"
    t = db.get_business_config(business_id, "tier", DEFAULT_TIER)
    return t if t in TIERS else DEFAULT_TIER


def _package_count(business_id):
    """Packages across the business's purchase orders (per-tenant JSON store)."""
    import purchases
    prev = db.current_business()
    try:
        db.set_current_business(business_id)
        pdb = purchases.load()
    finally:
        db.set_current_business(prev)
    return sum(len(po.get("packages") or []) for po in pdb.get("purchase_orders", []))


def usage(business_id):
    return {
        "searches": db.get_usage(business_id, "searches", period()),
        "packages": _package_count(business_id),
        "seats": db.count_active_users(business_id),
        "orders": db.count_rows("orders", business_id),
    }


def bump_search(business_id, n=1):
    """Count a product lookup against the monthly 'searches' quota (Otlobly exempt)."""
    if (business_id or 1) != 1:
        db.bump_usage(business_id, "searches", period(), n)


def status(business_id):
    """{tier, unlimited, over_any, resources:{r:{used,limit,over,pct}}} for the UI."""
    t = tier(business_id)
    if t == "unlimited":
        return {"tier": "unlimited", "unlimited": True, "over_any": False, "resources": {}}
    lim = TIERS[t]
    use = usage(business_id)
    resources, over_any = {}, False
    for r in RESOURCES:
        u, limit = use.get(r, 0), lim[r]
        is_over = u >= limit
        over_any = over_any or is_over
        resources[r] = {"used": u, "limit": limit, "over": is_over,
                        "pct": min(100, round(100 * u / limit)) if limit else 0}
    return {"tier": t, "unlimited": False, "over_any": over_any, "resources": resources}
