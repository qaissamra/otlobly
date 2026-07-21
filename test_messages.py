#!/usr/bin/env python3
"""
Self-checks for messages.py — the WhatsApp template module.

Guards the quote-name collision: quote(order) is the price-quote template
(public, keyed in TEMPLATES), while wa_link() must URL-encode with
urllib.parse.quote (imported as _urlquote). A plain `from urllib.parse
import quote` gets shadowed by the template and wa_link blows up on any
order with a phone. Offline + deterministic — no store data needed.

    ./.venv/bin/python test_messages.py
"""

from urllib.parse import unquote

import messages

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


ORDER = {
    "order_id": "OTL-TEST",
    "status": "ARRIVED",
    "amount_to_collect_usd": 42.5,
    "items": [{"clean_url": "https://amazon.com/dp/TESTASIN"}],
    "customer": {"name": "تجربة", "notes": "", "phones": [{"wa": "970599000000"}]},
}


def main():
    print("TEMPLATES:")
    q = messages.quote(ORDER)
    check("quote(order) is the price-quote template", "عرض السعر" in q and "42.50$" in q)
    check("quote stays registered in TEMPLATES", messages.TEMPLATES["quote"] is messages.quote)
    for kind in messages.TEMPLATES:
        check(f"{kind} builds text for a dict order", isinstance(messages.TEMPLATES[kind](ORDER), str))

    print("WA LINK (the shadowed-import regression):")
    text = "مرحباً — عرض السعر 42.50$ ✅"
    link = messages.wa_link(ORDER, text)
    check("wa_link builds without raising", link is not None)
    check("targets the customer wa number", link.startswith("https://wa.me/970599000000?text="))
    encoded = link.split("text=", 1)[1]
    check("text is URL-encoded (no raw spaces)", " " not in encoded and "%" in encoded)
    check("encoding round-trips", unquote(encoded) == text)
    check("no phone → None", messages.wa_link({"customer": {"phones": []}}, "x") is None)

    print("RENDER:")
    r = messages.render(ORDER, "quote")
    check("render returns kind/text/wa_link", r["kind"] == "quote" and r["text"] == q and r["wa_link"])

    print()
    if fails:
        raise SystemExit(f"FAILED: {len(fails)} check(s): {fails}")
    print("All messages.py checks passed.")


if __name__ == "__main__":
    main()
