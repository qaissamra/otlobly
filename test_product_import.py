#!/usr/bin/env python3
"""
Self-checks for multi-retailer auto-fill (Tatabu Phase 8).

Two parts: the pure OpenGraph parser (parse_og), and the router (import_product)
that sends Amazon to SerpAPI and everything else to the free OG scrape. The network
is monkeypatched so the test is offline + deterministic.

    ./.venv/bin/python test_product_import.py
"""

import amazon_import
import product_import as pi

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    print("PARSE OG (pure HTML parsing):")
    ali = ('<meta property="og:title" content="Cool Gadget 3-Pack | AliExpress">'
           '<meta property="og:image" content="https://ae.img/x.jpg">'
           '<meta property="og:price:amount" content="12.50">')
    og = pi.parse_og(ali)
    check("title extracted + site suffix stripped", og.get("title") == "Cool Gadget 3-Pack")
    check("image extracted", og.get("image") == "https://ae.img/x.jpg")
    check("price parsed to float", og.get("price_usd") == 12.50)

    reversed_attrs = '<meta content="https://iherb.img/y.jpg" property="og:image">'
    check("meta works in either attribute order", pi.parse_og(reversed_attrs).get("image") == "https://iherb.img/y.jpg")

    title_only = "<title>Floral Dress - SHEIN</title>"
    check("falls back to <title>, suffix stripped", pi.parse_og(title_only).get("title") == "Floral Dress")

    check("no og tags → empty", pi.parse_og("<html><body>nope</body></html>") == {})

    print("IMPORT ROUTER — non-Amazon → free OG scrape:")
    pi.scrape_product = lambda url: {"title": "T", "image": "I", "price_usd": 10.0}   # stub network
    cfg10 = {"pricing": {"markup_pct": 0.10}}
    d = pi.import_product("https://www.aliexpress.com/item/1005006.html?spm=x", config=cfg10)
    check("retailer detected", d["retailer"] == "aliexpress")
    check("product_id extracted", d["product_id"] == "1005006")
    check("asin is None for non-amazon", d["asin"] is None)
    check("title/image from scrape", d["title"] == "T" and d["image"] == "I")
    check("suggested quote = price + markup", d["suggested_quote_usd"] == 11.0)
    check("no error when scrape succeeds", d["error"] is None)

    print("IMPORT ROUTER — non-Amazon scrape fails → manual-fallback error:")
    pi.scrape_product = lambda url: {}
    d2 = pi.import_product("https://www.shein.com/dress-p-99.html")
    check("error set for manual fallback", bool(d2["error"]))
    check("retailer + id still tagged", d2["retailer"] == "shein" and d2["product_id"] == "99")

    print("IMPORT ROUTER — Amazon → SerpAPI path (delegates to amazon_import):")
    amazon_import.import_product = lambda url, config=None, refresh=False: {
        "asin": "B09CLKPMVC", "title": "A", "image": "AI", "price_usd": 50.0, "error": None}
    d3 = pi.import_product("https://www.amazon.com/dp/B09CLKPMVC")
    check("routes to amazon_import (title from it)", d3["title"] == "A")
    check("retailer tagged amazon", d3["retailer"] == "amazon")
    check("product_id defaults to asin", d3["product_id"] == "B09CLKPMVC")

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
