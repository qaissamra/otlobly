#!/usr/bin/env python3
"""
Self-checks: the Package prep page (تجهيز الطرود).

pkgprep.build crosses customer orders with PO packages: READY customers have
every live piece in a "recieved rd / recieved no rd" package, WAITING customers
have some pieces received and some missing. Cards carry a prefilled Arabic
WhatsApp message with the quote total in USD + ILS at fx.pkg_ils_per_usd.

    ./.venv/bin/python test_pkgprep.py
"""

from pathlib import Path

import pkgprep
import settings

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


PH = {"e164": "+970599000001", "wa": "970599000001", "display": "+970 599 000 001", "ok": True}
PH2 = {"e164": "+970599000002", "wa": "970599000002", "display": "+970 599 000 002", "ok": True}


def _order(oid, name, items, status="ORDERED", amount=None, deposit=0, phones=None):
    return {"order_id": oid, "status": status,
            "customer": {"name": name, "phones": [PH] if phones is None else phones,
                         "address": "", "city": "", "notes": ""},
            "items": items, "amount_to_collect_usd": amount, "deposit_usd": deposit}


def _pkg(status, package_no, items):
    return {"otlobly_status": status, "package_no": package_no, "items": items}


def _pitem(oid, asin, qty=1, status="", image=None, title=None):
    return {"customer_order_id": oid, "asin": asin, "qty": qty, "status": status,
            "image": image, "title": title}


def main():
    # ---- grouping / buckets ------------------------------------------------ #
    orders = [
        # A: one order, ASIN split across two received packages + one more piece → READY
        _order("OTL-0001", "Ready Rana",
               [{"asin": "B0AAA", "qty": 2},                    # no title/image → PO fills both
                {"asin": "B0BBB", "title": "Headphones", "qty": 1}],  # title set → order wins
               status="ARRIVED", amount=150.0, deposit=50.0),
        # B: two orders, one piece received, the other order untouched → WAITING
        _order("OTL-0002", "Waiting Wael", [{"asin": "B0CCC", "title": "Shoes", "qty": 1}],
               amount=80.0, phones=[PH2]),
        _order("OTL-0003", "Waiting Wael", [{"asin": "B0DDD", "title": "Bag", "qty": 1}],
               amount=40.0, phones=[PH2]),
        # C: only a dispatched package → invisible (nothing on the prep table)
        _order("OTL-0004", "Sent Samir", [{"asin": "B0EEE", "title": "Toy", "qty": 1}],
               amount=20.0, phones=[]),
        # D: received piece + a CANCELLED exception piece → READY with a ⚠ flag
        _order("OTL-0005", "Flag Fadi",
               [{"asin": "B0FFF", "title": "Charger", "qty": 1},
                {"asin": "B0GGG", "title": "Cable", "qty": 1}],
               phones=[]),                     # unpriced + no phone on purpose
        # E: pre-shipment statuses never appear, even if a package claims them
        _order("OTL-0006", "Quoted Qusai", [{"asin": "B0HHH", "qty": 1}],
               status="QUOTED", amount=99.0),
    ]
    pdb = {"purchase_orders": [{"po_id": "PO-1", "packages": [
        _pkg("recieved rd", 1, [_pitem("OTL-0001", "B0AAA", 1,
                                       image="http://img/aaa.jpg", title="Smart Watch Pro"),
                                _pitem("OTL-0002", "B0CCC", 1),
                                _pitem("OTL-0005", "B0FFF", 1),
                                _pitem("OTL-0006", "B0HHH", 1),
                                _pitem(None, "B0ZZZ", 5)]),          # unmatched → ignored
        _pkg("Recieved NO RD", 2, [_pitem("OTL-0001", "B0AAA", 1),   # case-insensitive
                                   _pitem("OTL-0001", "B0BBB", 1, image="http://img/bbb.jpg"),
                                   _pitem("OTL-0005", "B0GGG", 1, status="CANCELLED")]),
        _pkg("sent rd", 3, [_pitem("OTL-0004", "B0EEE", 1)]),
    ]}]}

    rep = pkgprep.build(orders, pdb, rate=3.1)
    ready = {c["name"]: c for c in rep["ready"]}
    waiting = {c["name"]: c for c in rep["waiting"]}
    reviews = {c["name"]: c for c in rep["reviews"]}

    check("qty split across packages sums to READY", "Ready Rana" in ready)
    check("partial customer lands in WAITING", "Waiting Wael" in waiting)
    check("fully-dispatched customer → review list, not prep",
          "Sent Samir" in reviews and "Sent Samir" not in ready and "Sent Samir" not in waiting)
    check("QUOTED order ignored",
          "Quoted Qusai" not in ready and "Quoted Qusai" not in waiting and "Quoted Qusai" not in reviews)
    check("exception doesn't block readiness", "Flag Fadi" in ready)
    check("counts match buckets", rep["counts"] == {"ready": len(rep["ready"]),
          "waiting": len(rep["waiting"]), "reviews": len(rep["reviews"])})

    rana = ready["Ready Rana"]
    check("received units counted (3/3)", rana["n_received"] == 3 and rana["n_missing"] == 0)
    check("totals: usd + ils at 3.1", rana["totals"]["usd"] == 150.0 and rana["totals"]["ils"] == 465)
    check("totals: deposit + remaining (+ils)",
          rana["totals"]["deposit_usd"] == 50.0 and rana["totals"]["remaining_usd"] == 100.0
          and rana["totals"]["remaining_ils"] == 310)
    check("ready message has total + ILS", "150.00$" in rana["wa_text"] and "465 ₪" in rana["wa_text"])
    check("ready message has remaining line", "100.00$" in rana["wa_text"] and "310 ₪" in rana["wa_text"])
    check("wa_url points at the customer's number", (rana["wa_url"] or "").startswith("https://wa.me/970599000001?text="))
    check("wa_url is percent-encoded (no raw spaces)", " " not in (rana["wa_url"] or "x"))
    # product image/title come from the matched PO item when the order line lacks them
    ritems = {it["asin"]: it for o in rana["orders"] for it in o["items"]}
    check("image falls back to PO item photo", ritems["B0AAA"]["image"] == "http://img/aaa.jpg")
    check("title falls back to PO item title", ritems["B0AAA"]["title"] == "Smart Watch Pro")
    check("PO image fills even when order title is set", ritems["B0BBB"]["image"] == "http://img/bbb.jpg")
    check("order-supplied title wins over PO title", ritems["B0BBB"]["title"] == "Headphones")
    # the card surfaces the PO package(s) so the page can change otlobly_status
    rpk = {(p["po_id"], p["package_no"]): p for p in rana["packages"]}
    check("card exposes contributing packages", ("PO-1", 1) in rpk and ("PO-1", 2) in rpk)
    check("package carries its otlobly_status", rpk[("PO-1", 1)]["otlobly_status"] == "recieved rd")
    check("package flagged shared (holds other customers too)", rpk[("PO-1", 1)]["shared"] is True)

    wael = waiting["Waiting Wael"]
    check("waiting counts received vs missing", wael["n_received"] == 1 and wael["n_missing"] == 1)
    check("partial message lists the received item", "Shoes" in wael["wa_text"])
    check("partial message has NO amounts", "$" not in wael["wa_text"])
    check("two orders grouped by one phone", len(wael["orders"]) == 2)

    fadi = ready["Flag Fadi"]
    exc_items = [it for o in fadi["orders"] for it in o["items"] if it["exception_qty"]]
    check("exception surfaced on the item", exc_items and exc_items[0]["exceptions"] == ["CANCELLED"])
    check("no phone → wa_url None but card kept", fadi["wa_url"] is None and fadi["wa_text"])
    check("unpriced total flagged + soft message",
          fadi["totals"]["unpriced"] and "سنؤكد" in fadi["wa_text"])

    # ---- money redaction (fulfillment role) -------------------------------- #
    red = pkgprep.build(orders, pdb, rate=3.1, include_money=False)
    rred = {c["name"]: c for c in red["ready"]}["Ready Rana"]
    check("redaction nulls totals", all(v is None for v in rred["totals"].values()))
    check("redaction nulls per-order amounts",
          all(o["amount_to_collect_usd"] is None and o["deposit_usd"] is None for o in rred["orders"]))
    check("redacted message has no amounts", "150" not in rred["wa_text"] and "₪" not in rred["wa_text"])

    # ---- partial send keeps the customer; full dispatch → testimonials ------ #
    two = [_order("OTL-0101", "Partial Pete",
                  [{"asin": "P0AAA", "title": "Phone", "qty": 1},
                   {"asin": "P0BBB", "title": "Case", "qty": 1}],
                  status="SHIPPED", amount=60.0)]
    # Phone shipped to the customer; Case not in any package yet → still coming.
    pdb_partial = {"purchase_orders": [{"po_id": "PO-9", "packages": [
        _pkg("sent no rd", 1, [_pitem("OTL-0101", "P0AAA", 1)])]}]}
    rp = pkgprep.build(two, pdb_partial, rate=3.1)
    pete = {c["name"]: c for c in rp["waiting"]}.get("Partial Pete")
    check("partial send KEEPS the customer in WAITING (not dropped)", pete is not None)
    check("waiting message names the sent + coming items, no price",
          bool(pete) and "Phone" in pete["wa_text"] and "Case" in pete["wa_text"]
          and "$" not in pete["wa_text"])

    # Now dispatch the Case too → the whole order is out → moves to testimonials.
    pdb_done = {"purchase_orders": [{"po_id": "PO-9", "packages": [
        _pkg("sent no rd", 1, [_pitem("OTL-0101", "P0AAA", 1)]),
        _pkg("complete", 2, [_pitem("OTL-0101", "P0BBB", 1)])]}]}
    rd2 = pkgprep.build(two, pdb_done, rate=3.1, facebook_url="https://facebook.com/x")
    pete_rv = {c["name"]: c for c in rd2["reviews"]}.get("Partial Pete")
    check("fully-dispatched customer moves into the review list", pete_rv is not None)
    check("no longer in prep once fully dispatched",
          not any(c["name"] == "Partial Pete" for c in rd2["ready"] + rd2["waiting"]))
    check("review card has +970 and +972 links on the same core",
          bool(pete_rv) and pete_rv["wa_url_970"].startswith("https://wa.me/970599000001?text=")
          and pete_rv["wa_url_972"].startswith("https://wa.me/972599000001?text="))
    check("review message is the testimonial one (fb link + review ask)",
          bool(pete_rv) and "facebook.com/x" in pete_rv["wa_review_text"]
          and "تقييم" in pete_rv["wa_review_text"])
    rd3 = pkgprep.build(two, pdb_done, rate=3.1, asked={"+970599000001"})
    check("already-asked customer hidden from reviews",
          "Partial Pete" not in {c["name"]: c for c in rd3["reviews"]})

    # ---- settings key ------------------------------------------------------ #
    cfgd = {"_seed": True}    # truthy — settings.read/apply treat {} as "load the real file"
    check("settings read defaults pkg rate 3.1", settings.read(cfgd)["pkg_ils_per_usd"] == 3.1)
    settings.apply({"pkg_ils_per_usd": "3.25"}, cfgd, persist=False)
    check("settings apply round-trips pkg rate", settings.read(cfgd)["pkg_ils_per_usd"] == 3.25)
    check("existing deposits rate untouched", settings.read(cfgd)["ils_per_usd"] == 3.7)

    # ---- HTML anchors ------------------------------------------------------ #
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("nav button exists", 'id="pkgprepBtn"' in html and "setView('pkgprep')" in html)
    check("view container exists", 'id="pkgprepView"' in html)
    check("setView toggles the view", '$("pkgprepView").classList.toggle("hidden",v!=="pkgprep")' in html)
    check("VIEW_BTN + VIEW_TITLES registered",
          'pkgprep:"pkgprepBtn"' in html and 'pkgprep:"Package prep"' in html)
    check("loader wired on view switch", 'if(v==="pkgprep"){ loadPkgprep(); }' in html)
    check("loader + renderer defined",
          "async function loadPkgprep(" in html and "function renderPkgprep(" in html)
    check("WA button uses accent po-btn", 'class="po-btn accent"' in html and "واتساب" in html)
    check("status picker + setter defined",
          "function ppStatusPicker(" in html and "async function ppSetStatus(" in html
          and "function ppPkgRow(" in html)
    check("status setter posts to the endpoint", '"/api/pkgprep/status"' in html)
    check("status edit gated on edit_fulfillment", "edit_fulfillment" in html and "function ppCanEdit(" in html)
    check("settings input exists", 'id="s_pkg_ils"' in html)
    check("settings save sends the key", "pkg_ils_per_usd:parseFloat($(\"s_pkg_ils\").value)||3.1" in html)

    print()
    if fails:
        print(f"FAILED: {len(fails)} check(s):", *fails, sep="\n  - ")
        raise SystemExit(1)
    print("All pkgprep checks passed ✓")


if __name__ == "__main__":
    main()
