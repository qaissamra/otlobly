#!/usr/bin/env python3
"""
Self-checks for multi-retailer link handling (Tatabu Phase 7).

Brokers buy from Amazon, AliExpress, SHEIN, and iHerb. This proves each retailer is
detected, its product id is extracted, tracking is stripped — and that Amazon links
behave EXACTLY as before (so Otlobly's Amazon flow is unchanged).

    ./.venv/bin/python test_multiretailer.py
"""

import normalize as n

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'XX '} {name:46} got={got!r}")
    if not ok:
        print(f"       want={want!r}")
        fails.append(name)


AMZ = "https://www.amazon.com/gp/product/B09CLKPMVC/ref=ox_sc?smid=A22&psc=1"
ACO = "https://a.co/d/084hXpF5"
ALI = "https://www.aliexpress.com/item/1005006123456789.html?spm=a2g0o.detail.0.0"
SHEIN = "https://www.shein.com/Summer-Floral-Dress-p-12345678-cat-1727.html?src=home"
IHERB = "https://www.iherb.com/pr/now-foods-vitamin-c-1000-mg/522?rcode=ABC"
EBAY = "https://www.ebay.com/itm/99887766"


def main():
    print("DETECT RETAILER:")
    check("amazon.com", n.detect_retailer(AMZ), "amazon")
    check("a.co short link", n.detect_retailer(ACO), "amazon")
    check("aliexpress.com", n.detect_retailer(ALI), "aliexpress")
    check("shein.com", n.detect_retailer(SHEIN), "shein")
    check("iherb.com", n.detect_retailer(IHERB), "iherb")
    check("ebay.com → other", n.detect_retailer(EBAY), "other")
    check("not-a-url → ''", n.detect_retailer("hello world"), "")
    check("a.com does NOT false-match amazon", n.detect_retailer("https://xyza.com/p/1"), "other")

    print("PRODUCT REF (retailer, id):")
    check("amazon ASIN", n.product_ref(AMZ), ("amazon", "B09CLKPMVC"))
    check("aliexpress item id", n.product_ref(ALI), ("aliexpress", "1005006123456789"))
    check("shein -p- id", n.product_ref(SHEIN), ("shein", "12345678"))
    check("iherb /pr/.../id", n.product_ref(IHERB), ("iherb", "522"))

    print("CLEAN PRODUCT URL (tracking stripped):")
    check("amazon → dp/ASIN form", n.clean_product_url(AMZ), "https://www.amazon.com/dp/B09CLKPMVC")
    check("aliexpress query stripped", n.clean_product_url(ALI),
          "https://www.aliexpress.com/item/1005006123456789.html")
    check("iherb query stripped", n.clean_product_url(IHERB),
          "https://www.iherb.com/pr/now-foods-vitamin-c-1000-mg/522")

    print("PARSE ITEMS — Amazon BACKWARD-COMPAT (unchanged for Otlobly):")
    a = n.parse_items([AMZ])[0]
    check("amazon asin still set", a["asin"], "B09CLKPMVC")
    check("amazon clean_url unchanged", a["clean_url"], "https://www.amazon.com/dp/B09CLKPMVC")
    check("amazon retailer tagged", a["retailer"], "amazon")
    check("amazon product_id == asin", a["product_id"], "B09CLKPMVC")

    print("PARSE ITEMS — other retailers (first-class):")
    items = n.parse_items([ALI, SHEIN, IHERB])
    by_r = {it["retailer"]: it for it in items}
    check("3 non-amazon items parsed", len(items), 3)
    check("aliexpress product_id", by_r["aliexpress"]["product_id"], "1005006123456789")
    check("shein product_id", by_r["shein"]["product_id"], "12345678")
    check("iherb product_id", by_r["iherb"]["product_id"], "522")
    check("non-amazon asin is None", by_r["aliexpress"]["asin"], None)

    print("PARSE ITEMS — mixed list dedups + keeps all retailers:")
    mixed = n.parse_items([AMZ, ALI, AMZ, SHEIN])   # AMZ twice → deduped
    check("mixed dedups to 3", len(mixed), 3)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
