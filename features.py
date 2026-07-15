#!/usr/bin/env python3
"""
Per-tenant feature flags (Tatabu white-label — Phase 2).

Which optional / Otlobly-internal tools a business sees. Business #1 (Otlobly)
always gets everything; every other business (a Tatabu broker) starts from the
generic set with the Otlobly-only tools OFF, overridable per-business via its
`features` config in the businesses table (db.py).

Anything NOT listed here is always on (orders, customers, deposits, P&L,
purchases, tracking) — those are the core every tenant gets.
"""

import db

# Otlobly-internal tools. Default for a broker tenant = OFF (hidden); business #1
# always gets them. Keys map to nav buttons + backend endpoints.
OTLOBLY_ONLY = ("clickup", "multilogin", "catalog", "leads", "gaash", "leluxe")

# Plan-linked features: default ON only for the listed tiers, still overridable
# per business via its `features` config (grant/revoke manually).
#   meta_ads — the Meta-ads slot in the P&L (spend card, ad-day dots, the Settings
#              monthly-spend panel). Brokers connect their OWN ad account in a
#              later phase; until then a Pro broker sees the slot empty, and lower
#              tiers don't see it at all.
TIERED = {"meta_ads": ("pro",)}


def resolve(business_id):
    """Return {feature: bool} for a business. #1 = all on (Otlobly unchanged);
    every other business = Otlobly-only tools off and plan-linked tools per its
    tier, unless explicitly set in its `features` config."""
    business_id = business_id or 1
    if business_id == 1:
        return {f: True for f in OTLOBLY_ONLY + tuple(TIERED)}
    try:
        conf = db.get_business_config(business_id, "features", None) or {}
    except Exception:
        conf = {}
    out = {f: bool(conf.get(f, False)) for f in OTLOBLY_ONLY}
    import quotas
    for f, tiers in TIERED.items():
        out[f] = bool(conf.get(f, quotas.tier(business_id) in tiers))
    return out


def has(business_id, feature):
    """Whether a business has `feature` on (unknown feature → on only for #1)."""
    return resolve(business_id).get(feature, (business_id or 1) == 1)
