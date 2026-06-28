#!/usr/bin/env python3
"""
Quick-quote price estimator. Given an Amazon product (ASIN/link) and a destination,
ask SerpApi's *amazon_product* engine for the listing WITH the delivery location set
(shipping_location + delivery_zip), then assemble an estimated landed cost:

    landed = item_price*qty + shipping + import        (best-effort)
    total  = pricing.apply_markup(landed)              (markup baked in, hidden)

SerpApi reliably returns the item price; the AmazonGlobal shipping line may come back
as TEXT and import charges are not guaranteed — so we (a) parse them from the response
when present and (b) fall back to a configurable rule otherwise, flagging `confidence`.
Results are cached by (asin, dest, qty) so re-quoting is free.

  python3 estimate.py --selftest                 # offline parser checks
  python3 estimate.py --probe B0XXXX --dest Israel  # ONE live lookup, dumps raw JSON
  python3 estimate.py B0XXXX --qty 1             # estimate one item
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import request, parse, error

import cfg
import normalize
import pricing

CACHE = Path(__file__).with_name("reports") / "estimate_cache.json"
PROBE_DUMP = Path(__file__).with_name("reports") / "estimate_probe.json"

# Regexes that pull a shipping amount / import amount out of free Amazon text such as
# "No Import Charges & $18.28 Shipping to Israel" or "AmazonGlobal Shipping $18.28".
_SHIP_RE = re.compile(r"\$\s*([\d,]+\.?\d*)\s*(?:amazon\s*global\s*)?shipping", re.I)
_SHIP_RE2 = re.compile(r"shipping[^$]{0,30}\$\s*([\d,]+\.?\d*)", re.I)
_IMPORT_RE = re.compile(r"import\s*(?:charges?|fees?)[^$]{0,30}\$\s*([\d,]+\.?\d*)", re.I)
_NO_IMPORT_RE = re.compile(r"no\s+import\s+charges?", re.I)


def _keys():
    # Auto-discover SERPAPI_KEY, SERPAPI_KEY_2, _3, _4, … (add more in .env, no code change).
    out, i = [os.environ.get("SERPAPI_KEY")], 2
    while os.environ.get(f"SERPAPI_KEY_{i}"):
        out.append(os.environ[f"SERPAPI_KEY_{i}"])
        i += 1
    return [k for k in out if k]


def _load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except ValueError:
            return {}
    return {}


def _save_cache(c):
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=2))


def _num(s):
    try:
        return round(float(str(s).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def destination(config=None):
    config = config or cfg.load()
    return {
        "shipping_location": cfg.get(config, "estimate.destination.shipping_location", "Israel"),
        "delivery_zip": cfg.get(config, "estimate.destination.delivery_zip", ""),
        "label": cfg.get(config, "estimate.destination.label", "Palestine (via Israel)"),
    }


# --------------------------------------------------------------------------- #
# Parsing — tolerant of SerpApi's (under-documented) amazon_product shape
# --------------------------------------------------------------------------- #
def _walk_strings(obj):
    """Yield every string value anywhere in the response (for text scraping)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def _first(d, *paths):
    """Return the first present, non-empty value among dotted paths."""
    for p in paths:
        cur = d
        ok = True
        for key in p.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def parse_product(data):
    """Pull {title, image, item_usd, shipping_usd, import_usd, confidence} out of an
    amazon_product response. Item price from known fields; shipping/import scraped
    from any text + explicit keys if SerpApi provides them."""
    title = _first(data, "product_results.title", "product_result.title",
                   "title", "product.title")
    image = _first(data, "product_results.images.0", "product_results.thumbnail",
                   "product_result.main_image.link", "thumbnail", "product.image")
    if isinstance(image, dict):
        image = image.get("link") or image.get("url")
    price = _first(data, "product_results.buybox.price.value",
                   "product_results.price.value", "buybox.price.value",
                   "product_results.extracted_price", "extracted_price",
                   "product_results.price", "price")
    item_usd = _num(price) if not isinstance(price, str) else _num(re.sub(r"[^\d.,]", "", price))

    # Explicit shipping/import keys, if they ever exist.
    ship = _first(data, "product_results.buybox.shipping.value",
                  "product_results.shipping.value", "shipping.value",
                  "amazon_global.shipping")
    imp = _first(data, "product_results.buybox.import_charges.value",
                 "product_results.import_charges.value", "import_charges.value")
    shipping_usd = _num(ship)
    import_usd = _num(imp)

    # Scrape ONLY the delivery/offer area for a shipping/import line — never the whole
    # response (reviews say "fast shipping" etc. and would false-positive). NOTE: a live
    # probe (B096L3TSH7, shipping_location=Israel) confirmed SerpApi returns the US
    # delivery view, not AmazonGlobal shipping/import — so this almost always finds
    # nothing and we fall back to the configured rule. Kept in case SerpApi adds it.
    offer_area = [
        _first(data, "product_results.delivery"),
        _first(data, "purchase_options.single_offer.delivery"),
        _first(data, "purchase_options.single_offer.features"),
        _first(data, "product_results.buybox"),
    ]
    blob = " ".join(_walk_strings([x for x in offer_area if x]))
    if shipping_usd is None:
        m = _SHIP_RE.search(blob) or _SHIP_RE2.search(blob)
        if m:
            shipping_usd = _num(m.group(1))
    if import_usd is None:
        if _NO_IMPORT_RE.search(blob):
            import_usd = 0.0
        else:
            m = _IMPORT_RE.search(blob)
            if m:
                import_usd = _num(m.group(1))

    # Ships-to-destination signal: only items shipped/sold by Amazon.com are
    # AmazonGlobal-eligible (reach Palestine). Third-party merchant-fulfilled items
    # (e.g. "UnbeatableSale, Inc") are domestic-only — confirmed by comparing two
    # live probes. seller unknown → assume shippable (don't false-flag).
    seller = _first(data, "purchase_options.single_offer.features.shipper_seller.text",
                    "purchase_options.single_offer.features.shipper_seller.details.content",
                    "product_results.seller", "buybox.seller")
    ships = True if not seller else ("amazon" in str(seller).lower())

    confidence = "exact" if (shipping_usd is not None) else "estimate"
    return {"title": title, "image": image, "item_usd": item_usd,
            "shipping_usd": shipping_usd, "import_usd": import_usd,
            "seller": seller, "ships": ships, "confidence": confidence}


def _apply_rules(parsed, config):
    """Fill missing shipping/import from configured rules so we always have a number."""
    item = parsed.get("item_usd") or 0.0
    ship = parsed.get("shipping_usd")
    imp = parsed.get("import_usd")
    if ship is None:
        rule = cfg.get(config, "estimate.shipping_rule", {"type": "flat", "value": 15})
        ship = round(item * rule.get("value", 0) / 100, 2) if rule.get("type") == "pct" \
            else float(rule.get("value", 0))
    if imp is None:
        rule = cfg.get(config, "estimate.import_rule", {"type": "pct", "value": 0})
        imp = round(item * rule.get("value", 0) / 100, 2) if rule.get("type") == "pct" \
            else float(rule.get("value", 0))
    return ship, imp


# --------------------------------------------------------------------------- #
# Live lookup
# --------------------------------------------------------------------------- #
def _fetch(asin, dest, config, dump=False):
    """One SerpApi amazon_product call (rotates keys). Returns raw dict or {'error'}."""
    keys = _keys()
    if not keys:
        return {"error": "No SERPAPI_KEY in .env"}
    domain = cfg.get(config, "serpapi.amazon_domain", "amazon.com")
    params = {"engine": "amazon_product", "amazon_domain": domain, "asin": asin}
    if dest.get("shipping_location"):
        params["shipping_location"] = dest["shipping_location"]
    if dest.get("delivery_zip"):
        params["delivery_zip"] = dest["delivery_zip"]
    n = len(keys)
    start = hash(asin) % n
    last_err = None
    for i in range(n):
        params["api_key"] = keys[(start + i) % n]
        try:
            with request.urlopen("https://serpapi.com/search.json?" + parse.urlencode(params),
                                  timeout=40) as r:
                data = json.loads(r.read().decode())
            if dump:
                PROBE_DUMP.parent.mkdir(exist_ok=True)
                PROBE_DUMP.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return data
        except error.HTTPError as e:
            last_err = f"SerpApi {e.code}"
            if e.code in (429, 401, 403):
                continue
            return {"error": last_err}
        except (error.URLError, ValueError) as e:
            return {"error": f"SerpApi call failed: {e}"}
    return {"error": f"All SerpApi keys exhausted/limited ({last_err})."}


def _assemble(raw, qty, config, cached):
    """Turn the cached RAW Amazon data into a priced line using the CURRENT settings
    (so changing the markup / shipping rule takes effect immediately, even for cached
    products). Only the item price + parsed values are cached — never the rule output."""
    item = raw["item_usd"]
    base = {
        "asin": raw["asin"], "qty": qty,
        "title": raw.get("title"), "image": raw.get("image"),
        "item_usd": item, "seller": raw.get("seller"),
        "ships": raw.get("ships", True),
        "link": raw.get("link") or f"https://www.amazon.com/dp/{raw['asin']}",
        "cached": cached,
    }
    if not raw.get("ships", True):
        # Doesn't ship to the destination (third-party seller) — no price, no total.
        base.update({"shipping_usd": None, "import_usd": None,
                     "landed_usd": None, "total_usd": None, "confidence": "no_ship"})
        return base
    parsed = {"item_usd": item, "shipping_usd": raw.get("parsed_shipping"),
              "import_usd": raw.get("parsed_import")}
    ship, imp = _apply_rules(parsed, config)
    landed = round(item * qty + ship + imp, 2)
    base.update({"shipping_usd": ship, "import_usd": imp, "landed_usd": landed,
                 "total_usd": pricing.apply_markup(landed, config),
                 "confidence": raw.get("confidence", "estimate")})
    return base


def estimate_item(url_or_asin, qty=1, config=None, refresh=False, dest=None):
    """Estimate one product. Returns the per-item dict (or {'error'})."""
    config = config or cfg.load()
    dest = dest or destination(config)
    qty = max(1, int(qty or 1))
    asin = (normalize.extract_asin(url_or_asin)
            or (url_or_asin.strip().upper()
                if normalize._BARE_ASIN_RE.match((url_or_asin or "").strip()) else None))
    if not asin and (url_or_asin or "").strip().lower().startswith("http"):
        asin = normalize.extract_asin(normalize.clean_amazon_url(url_or_asin, expand=True) or "")
    if not asin:
        return {"error": "Could not find an ASIN in that link."}

    ck = f"{asin}|{dest.get('shipping_location')}"      # qty-independent raw cache
    cache = _load_cache()
    if ck in cache and not refresh:
        return _assemble(cache[ck], qty, config, cached=True)

    data = _fetch(asin, dest, config)
    if data.get("error"):
        return {"error": data["error"], "asin": asin,
                "link": f"https://www.amazon.com/dp/{asin}"}
    parsed = parse_product(data)
    if parsed.get("item_usd") is None:
        return {"error": "Couldn't read the item price from Amazon.", "asin": asin}
    raw = {
        "asin": asin, "title": parsed.get("title"), "image": parsed.get("image"),
        "item_usd": parsed["item_usd"],
        "parsed_shipping": parsed.get("shipping_usd"),   # what SerpApi gave (usually None)
        "parsed_import": parsed.get("import_usd"),
        "seller": parsed.get("seller"), "ships": parsed.get("ships", True),
        "confidence": parsed.get("confidence"),
        "link": f"https://www.amazon.com/dp/{asin}",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    cache[ck] = raw
    _save_cache(cache)
    return _assemble(raw, qty, config, cached=False)


def estimate_cart(items, config=None, refresh=False):
    """items = [{link, qty}]. Returns {items:[...], landed_usd, total_usd, ...}."""
    config = config or cfg.load()
    dest = destination(config)
    rows, landed = [], 0.0
    errors = []
    for it in items:
        r = estimate_item(it.get("link") or it.get("asin") or "", it.get("qty", 1),
                          config, refresh, dest)
        rows.append(r)
        if r.get("error"):
            errors.append(r["error"])
        elif r.get("ships", True) and r.get("landed_usd") is not None:
            landed += r["landed_usd"]
    landed = round(landed, 2)
    priced = [r for r in rows if not r.get("error") and r.get("ships", True)]
    return {
        "items": rows,
        "landed_usd": landed,
        "total_usd": pricing.apply_markup(landed, config) if priced else None,
        "markup_pct": pricing.markup_pct(config),
        "destination": dest,
        "any_estimate": any(r.get("confidence") == "estimate" for r in priced),
        "any_unshippable": any(r.get("ships") is False for r in rows if not r.get("error")),
        "errors": errors,
    }


# --------------------------------------------------------------------------- #
def _selftest():
    sample = {"product_results": {
        "title": "Nine West Women's Bracelet Watch",
        "thumbnail": "https://m.media-amazon.com/x.jpg",
        "buybox": {"price": {"value": 24.28}},
        "delivery": ["No Import Charges & $18.28 Shipping to Israel"]}}
    p = parse_product(sample)
    ok = (p["item_usd"] == 24.28 and p["shipping_usd"] == 18.28
          and p["import_usd"] == 0.0 and p["confidence"] == "exact")
    print("parse_product:", "OK" if ok else f"XX {p}")
    # landed + markup math
    cfg_ = {"pricing": {"markup_pct": 0.10}}
    landed = 24.28 + 18.28 + 0.0
    total = pricing.apply_markup(landed, cfg_)
    ok2 = total == round(landed * 1.10, 2)
    print(f"markup: {'OK' if ok2 else 'XX'} landed {landed} -> total {total}")
    return 0 if (ok and ok2) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("asin", nargs="?")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--dest", default=None, help="shipping_location (default: config / Israel)")
    ap.add_argument("--probe", metavar="ASIN", help="ONE live lookup; dumps raw JSON to reports/")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if args.probe:
        config = cfg.load()
        dest = destination(config)
        if args.dest:
            dest["shipping_location"] = args.dest
        print(f"Probing {args.probe} shipping_location={dest['shipping_location']} …")
        data = _fetch(args.probe, dest, config, dump=True)
        if data.get("error"):
            sys.exit(data["error"])
        print(f"raw dumped to {PROBE_DUMP}")
        print("top-level keys:", list(data.keys()))
        print("parsed:", json.dumps(parse_product(data), ensure_ascii=False, indent=2))
        sys.exit(0)
    if not args.asin:
        sys.exit("usage: estimate.py <asin|link> [--qty N] | --probe <asin> | --selftest")
    print(json.dumps(estimate_item(args.asin, args.qty), ensure_ascii=False, indent=2))
