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
OTLOBLY_ONLY = ("clickup", "multilogin", "catalog", "leads", "gaash")


def resolve(business_id):
    """Return {feature: bool} for a business. #1 = all on (Otlobly unchanged);
    every other business = Otlobly-only tools off unless explicitly enabled in its
    `features` config."""
    business_id = business_id or 1
    if business_id == 1:
        return {f: True for f in OTLOBLY_ONLY}
    try:
        conf = db.get_business_config(business_id, "features", None) or {}
    except Exception:
        conf = {}
    return {f: bool(conf.get(f, False)) for f in OTLOBLY_ONLY}


def has(business_id, feature):
    """Whether a business has `feature` on (unknown feature → on only for #1)."""
    return resolve(business_id).get(feature, (business_id or 1) == 1)
