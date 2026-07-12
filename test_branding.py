#!/usr/bin/env python3
"""
Self-checks for per-tenant branding (Tatabu Phase 1).

Proves: business #1 renders EXACTLY the Otlobly chrome (unchanged); every other
business defaults to Tatabu; a broker's custom name/logo overrides it; custom names
are HTML-escaped (no injection); and the dashboard shell fills all brand tokens
(none left over) with never an "Otlobly" leak for a broker.

    ./.venv/bin/python test_branding.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-brand-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import branding   # noqa: E402
import db          # noqa: E402

fails = []
HERE = Path(__file__).resolve().parent


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    db.init_db()   # seeds business #1 (Otlobly)

    # 1) Business #1 = Otlobly, unchanged.
    o = branding.resolve(1)
    check("biz#1 name is Otlobly", o["name"] == "Otlobly")
    check("biz#1 title 'Otlobly — Orders'", o["title"] == "Otlobly — Orders")
    check("biz#1 sidebar has Otlobly wordmark", 'Otlob<span class="ly">ly</span>' in o["sidebar_html"])
    check("biz#1 favicon is the exact Otlobly mark", o["favicon_href"] == branding._OTLOBLY_FAVICON)

    # 2) Any other (unconfigured) business defaults to Tatabu (no Otlobly anywhere).
    t = branding.resolve(777)
    check("biz#2 defaults to Tatabu", t["name"] == "Tatabu")
    check("biz#2 sidebar has Tatabu wordmark", 'Tata<span class="ly">bu</span>' in t["sidebar_html"])
    check("biz#2 tagline is Arabic تتبع", "تتبع" in t["tagline"])
    check("biz#2 has NO 'Otlobly'", "Otlob" not in (t["title"] + t["sidebar_html"] + t["favicon_href"]))

    # 3) A broker's custom name overrides the default.
    bid = db.create_business("ACME Cargo")
    db.set_business_config(bid, "brand", {"name": "ACME Cargo", "tagline": "طلبات من أمريكا"})
    a = branding.resolve(bid)
    check("custom name in title", a["title"] == "ACME Cargo — Orders")
    check("custom name in sidebar", "ACME Cargo" in a["sidebar_html"])
    check("custom tagline used", "طلبات من أمريكا" in a["sidebar_html"])
    check("custom brand shows no Otlobly/Tatabu wordmark", "Tata<span" not in a["sidebar_html"] and "Otlob<span" not in a["sidebar_html"])

    # 4) Custom name is HTML-escaped (no script injection into the shell).
    db.set_business_config(bid, "brand", {"name": "<script>alert(1)</script>"})
    x = branding.resolve(bid)
    check("XSS name escaped in title", "<script>" not in x["title"] and "&lt;script&gt;" in x["title"])
    check("XSS name escaped in sidebar", "<script>" not in x["sidebar_html"])

    # 5) The dashboard shell fills all tokens — Otlobly-exact for #1, no leak for a broker.
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    check("shell carries all brand tokens", all(tok in html for tok in branding.TOKENS))

    r1 = branding.render_shell(html, branding.resolve(1))
    check("render #1: no tokens left", not any(tok in r1 for tok in branding.TOKENS))
    check("render #1: title is Otlobly", "<title>Otlobly — Orders</title>" in r1)
    check("render #1: Otlobly wordmark present", 'Otlob<span class="ly">ly</span>' in r1)
    check("render #1: Otlobly favicon present", branding._OTLOBLY_FAVICON in r1)

    r2 = branding.render_shell(html, branding.resolve(777))   # unconfigured → Tatabu default
    check("render #2: no tokens left", not any(tok in r2 for tok in branding.TOKENS))
    check("render #2: Tatabu present", "Tatabu" in r2 and "<title>Tatabu — Orders</title>" in r2)
    # the ONLY 'Otlob' allowed anywhere is inside JS comments/strings, not the brand —
    # assert the brand slots specifically are Otlobly-free for a broker.
    check("render #2: brand slots have no Otlobly",
          "Otlob<span" not in r2 and "Otlobly — Orders" not in r2 and branding._OTLOBLY_FAVICON not in r2)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
