#!/usr/bin/env python3
"""
Unified product auto-fill across retailers (Tatabu Phase 8).

One entry point, `import_product(url)`, that routes by retailer:
  * Amazon  → SerpAPI via amazon_import (metered) — unchanged for Otlobly.
  * AliExpress / SHEIN / iHerb / any other link → a FREE, generic OpenGraph scrape
    (og:title / og:image / og:price on the product page). No SerpAPI credits.

Best-effort: returns title/image/price when the page exposes them, else an `error`
so the UI falls back to manual entry ("try auto, manual fallback").

The HTML parsing (`parse_og`) is pure and unit-tested; the network fetch is separate.
Stdlib only (urllib), mirroring amazon_import / notify / tracking.
"""

import re
from urllib import request

import amazon_import
import normalize
import pricing

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_SITE_SUFFIX = re.compile(r"\s*[|\-–—:]\s*(Amazon|AliExpress|SHEIN|iHerb|ROMWE)[^|\-–—]*$", re.I)


def _meta(html, *props):
    """First matching <meta property|name=prop content=...> value (either attr order)."""
    for p in props:
        pat = re.escape(p)
        m = (re.search(r'<meta[^>]+(?:property|name)=["\']' + pat + r'["\'][^>]+content=["\']([^"\']+)', html, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']' + pat + r'["\']', html, re.I))
        if m:
            return m.group(1).strip()
    return None


def parse_og(html):
    """Extract {title, image, price_usd} from a product page's OpenGraph / meta tags.
    Pure — no network. Missing fields are simply omitted."""
    if not html:
        return {}
    title = _meta(html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        title = m.group(1).strip() if m else None
    if title:
        title = _SITE_SUFFIX.sub("", title).strip() or title
    image = _meta(html, "og:image", "og:image:secure_url", "og:image:url", "twitter:image")
    price_raw = _meta(html, "og:price:amount", "product:price:amount", "product:price")
    price_usd = None
    if price_raw:
        pm = re.search(r"[\d,]+\.?\d*", price_raw)
        if pm:
            try:
                price_usd = round(float(pm.group().replace(",", "")), 2)
            except ValueError:
                price_usd = None
    return {k: v for k, v in (("title", title), ("image", image), ("price_usd", price_usd))
            if v is not None}


def _fetch(url, timeout=15, cap=900000):
    """Fetch a product page: direct first, then the r.jina.ai reader proxy (which
    fetches from a non-blocked IP) as a fallback. Returns HTML text or ''."""
    ua = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    for u, hdrs, t in ((url, ua, timeout),
                       ("https://r.jina.ai/" + url, {**ua, "X-Return-Format": "html"}, timeout * 2)):
        try:
            with request.urlopen(request.Request(u, headers=hdrs), timeout=t) as r:
                html = r.read(cap).decode("utf-8", "replace")
            if html and ("og:" in html or "<title" in html.lower()):
                return html
        except Exception:  # noqa: BLE001 — any fetch failure → try the next, then manual
            continue
    return ""


def scrape_product(url):
    """FREE generic auto-fill for a non-Amazon product URL → {title, image, price_usd}."""
    return parse_og(_fetch(url))


def import_product(url, config=None, refresh=False):
    """Auto-fill a product from ANY retailer link. Amazon → SerpAPI (metered) exactly
    as before; the other sites → a free OpenGraph scrape. Returns a unified detail
    dict with retailer/product_id, or an `error` for the manual fallback."""
    retailer, pid = normalize.product_ref(url)
    if retailer == "amazon":
        d = amazon_import.import_product(url, config=config, refresh=refresh)
        if isinstance(d, dict) and not d.get("error"):
            d.setdefault("retailer", "amazon")
            d.setdefault("product_id", d.get("asin"))
        return d

    clean = normalize.clean_product_url(url, expand=True) or url
    og = scrape_product(clean)
    ok = bool(og.get("title") or og.get("image"))
    return {
        "retailer": retailer or "other", "product_id": pid, "asin": None,
        "title": og.get("title"), "image": og.get("image"),
        "price_usd": og.get("price_usd"),
        "suggested_quote_usd": (pricing.apply_markup(og["price_usd"], config)
                                if og.get("price_usd") is not None else None),
        "clean_url": clean, "link": clean, "cached": False, "source": "og",
        "error": None if ok else ("لم نتمكن من جلب المنتج تلقائياً — أدخل الاسم والصورة والسعر يدوياً · "
                                  "Couldn't auto-fetch this product — enter the title, image and price manually."),
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python3 product_import.py <product-url>")
    print(json.dumps(import_product(sys.argv[1]), ensure_ascii=False, indent=2))
