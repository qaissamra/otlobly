#!/usr/bin/env python3
"""
Pricing for an Otlobly order. All amounts in USD.

The AUTHORITATIVE price is the Amazon CHECKOUT total (item + shipping + import
fees) that order_placer.py scrapes off the order-review page — SerpAPI cannot
see destination shipping/import fees. Here we:

  * apply_markup(landed_usd)  -> amount to collect from the customer
  * price_from_checkout(...)  -> turn a scraped checkout{} into the final amount
  * estimate_usd(asin)        -> OPTIONAL pre-order item-price estimate via
                                 SerpAPI (cached; ~30 credits/run; keys rotated)

    python3 pricing.py --selftest
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib import request, parse, error

import cfg
import money

CACHE_FILE = Path(__file__).with_name("reports") / "price_cache.json"


# --------------------------------------------------------------------------- #
# Markup — the only number that turns landed cost into what we collect
# --------------------------------------------------------------------------- #
def markup_pct(config=None):
    config = config or cfg.load()
    return float(cfg.get(config, "pricing.markup_pct", 0.10))


def apply_markup(landed_usd, config=None):
    """amount_to_collect = landed_cost * (1 + markup_pct), to the cent."""
    if landed_usd is None:
        return None
    return money.mul(landed_usd, 1 + markup_pct(config))


def price_from_checkout(checkout, config=None):
    """checkout = {subtotal_usd, shipping_usd, import_fees_usd, order_total_usd}.
    Prefer Amazon's own order_total; fall back to summing the parts. Returns the
    marked-up amount to collect (USD)."""
    if not checkout:
        return None
    total = checkout.get("order_total_usd")
    if total is None:
        parts = [checkout.get(k) or 0 for k in
                 ("subtotal_usd", "shipping_usd", "import_fees_usd")]
        total = money.usd_sum(parts) if any(parts) else None
    return apply_markup(total, config)


# --------------------------------------------------------------------------- #
# SerpAPI estimate (optional, cached) — item price only, NOT landed
# --------------------------------------------------------------------------- #
def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except ValueError:
            return {}
    return {}


def _save_cache(c):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(c, indent=2))


def _serpapi_keys():
    # Auto-discover SERPAPI_KEY, SERPAPI_KEY_2, _3, _4, … (add more in .env, no code change).
    out, i = [os.environ.get("SERPAPI_KEY")], 2
    while os.environ.get(f"SERPAPI_KEY_{i}"):
        out.append(os.environ[f"SERPAPI_KEY_{i}"])
        i += 1
    return [k for k in out if k]


def estimate_usd(asin, config=None, force=False):
    """Best-effort item-price estimate for an ASIN. Cached for cache_ttl_hours
    so we don't burn SerpAPI credits. Returns {'usd', 'title', 'source'} or None.

    Disabled unless config.serpapi.enabled and at least one SERPAPI_KEY is set —
    this is only a rough pre-order quote; the real price comes from checkout."""
    config = config or cfg.load()
    if not asin:
        return None
    cache = _load_cache()
    ttl = float(cfg.get(config, "serpapi.cache_ttl_hours", 168)) * 3600
    hit = cache.get(asin)
    if hit and not force and (time.time() - hit.get("ts", 0)) < ttl:
        return {k: hit.get(k) for k in ("usd", "title", "source")}

    if not cfg.get(config, "serpapi.enabled", False):
        return None
    keys = _serpapi_keys()
    if not keys:
        return None

    domain = cfg.get(config, "serpapi.amazon_domain", "amazon.com")
    key = keys[hash(asin) % len(keys)]            # rotate keys across ASINs
    q = parse.urlencode({"engine": "amazon", "amazon_domain": domain,
                         "k": asin, "api_key": key})
    try:
        with request.urlopen("https://serpapi.com/search.json?" + q, timeout=30) as r:
            data = json.loads(r.read().decode())
    except (error.URLError, ValueError, TimeoutError):
        return None

    usd = title = None
    for res in data.get("organic_results", []):
        if (res.get("asin") or "").upper() == asin.upper():
            usd = res.get("extracted_price")
            title = res.get("title")
            break
    if usd is None and data.get("organic_results"):
        first = data["organic_results"][0]
        usd, title = first.get("extracted_price"), first.get("title")
    if usd is None:
        return None

    cache[asin] = {"usd": usd, "title": title, "source": "serpapi", "ts": time.time()}
    _save_cache(cache)
    return {"usd": usd, "title": title, "source": "serpapi"}


# --------------------------------------------------------------------------- #
def _selftest():
    cases = [
        ({"order_total_usd": 100.0}, 110.0),
        ({"subtotal_usd": 80, "shipping_usd": 15, "import_fees_usd": 5}, 110.0),
        ({"order_total_usd": 49.99}, 54.99),
        (None, None),
    ]
    fails = 0
    print(f"markup = {markup_pct()*100:.0f}%")
    for checkout, want in cases:
        got = price_from_checkout(checkout)
        ok = got == want
        fails += not ok
        print(f"  {'OK ' if ok else 'XX '} {str(checkout)[:48]:48} -> {got} (want {want})")
    print("ALL PASS" if not fails else f"FAILURES: {fails}")
    return fails


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print(__doc__)
