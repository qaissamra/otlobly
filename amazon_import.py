#!/usr/bin/env python3
"""
Amazon link → product details, automatically. Paste a link (or ASIN), get back
image, title, price, seller, Prime, delivery, ASIN, options — so a quote takes
seconds instead of hand-copying.

Data source: SerpAPI's Amazon engine (engine=amazon, k=<ASIN>). Results are
CACHED by ASIN (reports/import_cache.json) so re-quoting the same product is
free, and keys are rotated (SERPAPI_KEY / _2 / _3) to spread the ~30-credit cost.
The authoritative LANDED price still comes from the checkout scrape at order time.

  python3 amazon_import.py <amazon-url-or-asin>
  python3 amazon_import.py <asin> --refresh     # ignore cache
  python3 amazon_import.py --selftest           # offline parser test
"""

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

CACHE = Path(__file__).with_name("reports") / "import_cache.json"


def _keys():
    # Auto-discover SERPAPI_KEY, SERPAPI_KEY_2, _3, _4, … (add more in .env, no code change).
    out, i = [os.environ.get("SERPAPI_KEY")], 2
    while os.environ.get(f"SERPAPI_KEY_{i}"):
        out.append(os.environ[f"SERPAPI_KEY_{i}"])
        i += 1
    return [k for k in out if k]


_CREDITS_CACHE = {"at": 0.0, "val": None}   # SerpApi account lookups, cached 5 min


def credits_left(max_age=300):
    """Remaining SerpApi searches summed across every rotated key (best-effort:
    a key whose account call fails contributes None, not an exception). Shown on
    the staff Estimate-costs popup so the owner can see the credit balance."""
    if _CREDITS_CACHE["val"] is not None and time.time() - _CREDITS_CACHE["at"] < max_age:
        return _CREDITS_CACHE["val"]
    keys, total, per = _keys(), 0, []
    any_ok = False
    for k in keys:
        left = None
        try:
            with request.urlopen("https://serpapi.com/account.json?"
                                 + parse.urlencode({"api_key": k}), timeout=8) as r:
                d = json.loads(r.read().decode() or "{}")
            left = d.get("total_searches_left")
            if left is None:               # older field names, just in case
                left = d.get("plan_searches_left")
        except Exception:  # noqa: BLE001 - account API down ≠ estimating broken
            pass
        per.append({"tail": ("…" + k[-4:]) if len(k) > 4 else k, "left": left})
        if left is not None:
            total += int(left)
            any_ok = True
    val = {"left": total if any_ok else None, "keys": per}
    _CREDITS_CACHE.update(at=time.time(), val=val)
    return val


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


def parse_result(res, asin, config=None):
    """Normalize one SerpAPI Amazon organic/product result into our shape."""
    price = res.get("extracted_price")
    if price is None and res.get("price"):
        m = re.search(r"[\d,]+\.?\d*", str(res["price"]))
        price = float(m.group().replace(",", "")) if m else None
    delivery = res.get("delivery")
    if isinstance(delivery, list):
        delivery = " · ".join(str(d) for d in delivery)
    opts = []
    for v in (res.get("variants") or res.get("options") or []):
        if isinstance(v, dict):
            opts.append(v.get("title") or v.get("name") or json.dumps(v, ensure_ascii=False))
        else:
            opts.append(str(v))
    return {
        "asin": asin,
        "title": res.get("title"),
        "image": res.get("thumbnail") or res.get("image"),
        "price_usd": price,
        "seller": res.get("seller") or res.get("sold_by"),
        "prime": bool(res.get("prime")) if res.get("prime") is not None else None,
        "delivery": delivery,
        "options": opts,
        "rating": res.get("rating"),
        "reviews": res.get("reviews"),
        "link": res.get("link_clean") or res.get("link") or f"https://www.amazon.com/dp/{asin}",
        "suggested_quote_usd": pricing.apply_markup(price, config) if price else None,
        "source": "serpapi",
    }


def _deep(d, *paths):
    """First present, non-empty value among dotted paths (supports list indexes)."""
    for p in paths:
        cur, ok = d, True
        for k in p.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            elif isinstance(cur, list) and k.isdigit() and int(k) < len(cur):
                cur = cur[int(k)]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


_IMG_RE = re.compile(r"https?://[^\s\"']*media-amazon\.com/images/[^\s\"']+?\.(?:jpg|jpeg|png)", re.I)


def _serpapi(extra, asin, keys, domain):
    """One rotated SerpApi search.json call. Returns (data|None, last_err)."""
    n = len(keys)
    start = hash(asin) % n
    last_err = None
    for i in range(n):
        key = keys[(start + i) % n]
        q = parse.urlencode({**extra, "amazon_domain": domain, "api_key": key})
        try:
            with request.urlopen("https://serpapi.com/search.json?" + q, timeout=30) as r:
                return json.loads(r.read().decode()), None
        except error.HTTPError as e:
            last_err = f"SerpAPI {e.code}"
            if e.code in (429, 401, 403):   # depleted/limited/invalid → try next key
                continue
            return None, last_err
        except (error.URLError, ValueError) as e:
            return None, f"SerpAPI call failed: {e}"
    return None, last_err


def _parse_product_engine(data, asin, config):
    """Map a SerpApi amazon_product (engine) response → our detail shape. The
    amazon_product engine is a DIRECT ASIN lookup, so it returns the image even
    when the older `engine=amazon` keyword-search finds no organic hit for the
    ASIN. Returns None when there's neither a title nor an image (a real miss)."""
    title = _deep(data, "product_results.title", "product_result.title", "title", "product.title")
    image = _deep(data, "product_results.images.0", "product_results.thumbnail",
                  "product_result.main_image.link", "product.image", "thumbnail")
    if isinstance(image, dict):
        image = image.get("link") or image.get("url")
    if not image:                       # last resort: any Amazon media image in the payload
        for s in _walk_strings(data):
            m = _IMG_RE.search(s)
            if m:
                image = m.group(0)
                break
    price = _deep(data, "product_results.buybox.price.value", "product_results.price.value",
                  "buybox.price.value", "product_results.extracted_price", "extracted_price",
                  "product_results.price", "price")
    if isinstance(price, str):
        m = re.search(r"[\d,]+\.?\d*", price)
        price = float(m.group().replace(",", "")) if m else None
    delivery = _deep(data, "product_results.delivery")
    if isinstance(delivery, list):
        delivery = " · ".join(str(d) for d in delivery)
    seller = _deep(data, "product_results.seller", "buybox.seller",
                   "purchase_options.single_offer.features.shipper_seller.text")
    if not (title or image):
        return None
    return {
        "asin": asin, "title": title, "image": image, "price_usd": price,
        "seller": seller, "prime": None, "delivery": delivery, "options": [],
        "rating": _deep(data, "product_results.rating", "rating"),
        "reviews": _deep(data, "product_results.reviews", "reviews"),
        "link": _deep(data, "product_results.link", "link") or f"https://www.amazon.com/dp/{asin}",
        "suggested_quote_usd": pricing.apply_markup(price, config) if price else None,
        "source": "serpapi:amazon_product",
    }


def import_product(url_or_asin, config=None, refresh=False):
    """Resolve a link/ASIN to product details. Returns the detail dict or
    {'error': ...}. Cached by ASIN."""
    config = config or cfg.load()
    asin = (normalize.extract_asin(url_or_asin)
            or (url_or_asin.strip().upper()
                if normalize._BARE_ASIN_RE.match(url_or_asin.strip()) else None))
    if not asin and url_or_asin.strip().lower().startswith("http"):
        # a.co / junk link with no ASIN inline → expand once
        asin = normalize.extract_asin(normalize.clean_amazon_url(url_or_asin, expand=True) or "")
    if not asin:
        return {"error": "Could not find an ASIN in that link."}

    cache = _load_cache()
    if asin in cache and not refresh:
        hit = dict(cache[asin])
        hit["cached"] = True
        hit["suggested_quote_usd"] = pricing.apply_markup(hit.get("price_usd"), config)
        return hit

    keys = _keys()
    if not keys:
        return {"error": "No SERPAPI_KEY in .env — add one to enable auto-import.",
                "asin": asin, "link": f"https://www.amazon.com/dp/{asin}"}

    domain = cfg.get(config, "serpapi.amazon_domain", "amazon.com")

    # PRIMARY: the amazon_product engine is a DIRECT ASIN lookup that reliably
    # returns the product image/title. (The older engine=amazon SEARCH for the
    # ASIN often returns no organic hit for newer products → the photo never
    # loaded. That's the bug this fixes.) Keys rotate, skipping depleted ones.
    data, last_err = _serpapi({"engine": "amazon_product", "asin": asin}, asin, keys, domain)
    detail = _parse_product_engine(data, asin, config) if data else None

    # FALLBACK: only if amazon_product gave us nothing usable, try the keyword search.
    if not detail or not (detail.get("image") or detail.get("title")):
        sdata, serr = _serpapi({"engine": "amazon", "k": asin}, asin, keys, domain)
        last_err = last_err or serr
        if sdata:
            results = sdata.get("organic_results") or []
            match = next((r for r in results if (r.get("asin") or "").upper() == asin.upper()),
                         results[0] if results else None)
            if match:
                sdetail = parse_result(match, asin, config)
                if not detail or (sdetail.get("image") and not detail.get("image")):
                    detail = sdetail

    if not detail or not (detail.get("image") or detail.get("title") or detail.get("price_usd")):
        if data is None and last_err:     # never reached a working key
            return {"error": f"All SerpAPI keys exhausted/limited ({last_err}). Top up or add a key.",
                    "asin": asin, "link": f"https://www.amazon.com/dp/{asin}"}
        return {"error": "Amazon returned no image/title for this ASIN (it may be unavailable "
                         "or region-restricted). Open the link to grab the photo manually.",
                "asin": asin, "link": f"https://www.amazon.com/dp/{asin}"}

    detail["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    cache[asin] = {k: v for k, v in detail.items() if k != "suggested_quote_usd"}
    _save_cache(cache)
    detail["cached"] = False
    return detail


def _selftest():
    sample = {"asin": "B09CLKPMVC", "title": "Test Product XL",
              "thumbnail": "https://m.media-amazon.com/x.jpg",
              "extracted_price": 49.99, "prime": True,
              "delivery": ["FREE delivery Tue", "Fastest delivery Mon"],
              "seller": "ACME", "rating": 4.5, "reviews": 1200,
              "link": "https://www.amazon.com/dp/B09CLKPMVC"}
    d = parse_result(sample, "B09CLKPMVC")
    ok = (d["title"] == "Test Product XL" and d["price_usd"] == 49.99
          and d["prime"] is True and "FREE delivery Tue" in d["delivery"]
          and d["suggested_quote_usd"] == round(49.99 * 1.10, 2))
    print("parse_result:", "OK" if ok else f"XX {d}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if len(sys.argv) < 2:
        sys.exit("usage: python3 amazon_import.py <amazon-url-or-asin> [--refresh]")
    d = import_product(sys.argv[1], refresh="--refresh" in sys.argv)
    print(json.dumps(d, ensure_ascii=False, indent=2))
