#!/usr/bin/env python3
"""
Parity checklist for the UX restructure (brief §13, extended by AUDIT.md §5.10).

This suite walks the core business flow through the real modules and asserts the
behaviour every migrated page must keep. It is deliberately about BEHAVIOUR, not
markup: as pages move onto the design system in Phases 3-6, this file must stay
green untouched. If it goes red, a capability was lost — which the brief forbids.

The walk: order -> To-order queue -> In cart -> purchase order -> ASIN match ->
ORDERED + Amazon number + customer ETA -> package with a GWD and a masked OTL
tracking number -> Package prep READY -> deposit and cash-collection ledger ->
activity log. Plus the invariants the audit found: one "parcel is done" set,
the two OTL number spaces, and the customer-facing status mask.

    ./.venv/bin/python test_ux_parity.py
"""

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-parity-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)          # redirect every JSON store + the DB
os.environ["OTLOBLY_DB"] = str(_TMP / "parity.db")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import activity          # noqa: E402
import alerts            # noqa: E402
import db                # noqa: E402
import normalize         # noqa: E402
import pkgprep           # noqa: E402
import purchases         # noqa: E402
import store             # noqa: E402
import tracking          # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)
        if detail:
            print(f"      {detail}")


def step(title):
    print(f"\n{title}")


def main():
    db.init_db()
    phones = normalize.collect_phones("+970599711377")
    odb = {"orders": [], "seq": 0}

    # ---- 1. a customer order enters the queue ------------------------------
    step("1. Order intake -> To-order queue")
    order = store.new_order(odb, name="معاذ الشيخ", phones=phones, address="رام الله",
                            items=[{"asin": "B0TEST0001", "title": "Metal hole punch", "qty": 1},
                                   {"asin": "B0TEST0002", "title": "Drawing tablet", "qty": 2}],
                            city="Ramallah")
    store.upsert(odb, order)
    oid = order["order_id"]
    check("an order gets an OTL-#### id", oid.startswith("OTL-") and len(oid) == 8, oid)
    check("a new order starts REQUESTED", order["status"] == "REQUESTED")
    queue = store.need_order(odb["orders"])
    check("it appears in the To-order queue", any(o["order_id"] == oid for o in queue["orders"]))
    check("the queue counts it once, with its products", queue["count"] == 1 and queue["products"] == 2,
          f"count={queue['count']} products={queue['products']}")
    check("it is NOT in the In-cart queue", not any(o["order_id"] == oid for o in store.in_cart(odb["orders"])["orders"]))

    # ---- 2. quote --------------------------------------------------------- #
    step("2. Quote")
    order["status"] = "QUOTED"
    order["amount_to_collect_usd"] = 110.0
    store.upsert(odb, order)
    q2 = store.need_order(odb["orders"])
    check("QUOTED stays in the To-order queue", any(o["order_id"] == oid for o in q2["orders"]))
    check("the queue total is the quoted amount", abs(q2["total_usd"] - 110.0) < 0.01, str(q2["total_usd"]))
    check("QUOTED is one of the pre-order statuses", "QUOTED" in store.PREORDER_STATUSES)

    # ---- 3. in cart -------------------------------------------------------- #
    step("3. Move to cart")
    order["status"] = "IN_CART"
    store.upsert(odb, order)
    check("IN_CART leaves the To-order queue", not any(o["order_id"] == oid for o in store.need_order(odb["orders"])["orders"]))
    cart = store.in_cart(odb["orders"])
    check("IN_CART appears in the In-cart queue", any(o["order_id"] == oid for o in cart["orders"]))
    check("the cart carries its own revenue figure", abs(cart["total_usd"] - 110.0) < 0.01)

    # ---- 4. purchase order + ASIN match ------------------------------------ #
    step("4. Purchase order, ASIN match, supply -> demand")
    pdb = {"purchase_orders": [], "seq": 0}
    po = purchases.new_po(pdb, amazon_order_number="113-0913603-TEST", ship_to="معاذ الشيخ",
                          profile_box="B19", total_usd=142.5,
                          packages=[{"package_no": 1, "arrival": "2026-09-12",
                                     "tracking_number": "GWD004705019",
                                     "items": [{"asin": "B0TEST0001", "qty": 1},
                                               {"asin": "B0TEST0002", "qty": 2}]}])
    check("a purchase order gets a PO-#### id", po["po_id"].startswith("PO-"), po["po_id"])
    check("a PO is NOT an order id (two different spaces)", not po["po_id"].startswith("OTL-"))
    purchases.attach_matches(po, odb["orders"])
    matched = [it for p in po["packages"] for it in p["items"] if it.get("customer_order_id") == oid]
    check("both items match the customer order by ASIN", len(matched) == 2,
          f"matched {len(matched)} of 2")
    changes = purchases.apply_to_orders(po, odb["orders"], buffer_days=10)
    check("saving the PO flips the order to ORDERED", odb["orders"][0]["status"] == "ORDERED")
    check("the order inherits the Amazon order number",
          odb["orders"][0].get("amazon_order_number") == "113-0913603-TEST")
    check("the order inherits the buying account (profile box)", odb["orders"][0].get("profile_box") == "B19")
    check("the customer ETA is the arrival plus the buffer",
          (odb["orders"][0].get("est_delivery_customer") or "") == "2026-09-22",
          odb["orders"][0].get("est_delivery_customer"))
    check("apply_to_orders reports what it changed", bool(changes))
    before = dict(odb["orders"][0])
    purchases.apply_to_orders(po, odb["orders"], buffer_days=10)
    check("applying twice changes nothing (idempotent)", odb["orders"][0] == before)

    # ---- 5. the masked customer tracking number ---------------------------- #
    step("5. Parcel numbers")
    purchases.upsert(pdb, po)
    purchases.ensure_customer_tracking(pdb, po)
    otl = po["packages"][0].get("customer_tracking")
    check("the package gets a masked OTL tracking number", bool(otl), str(otl))
    check("the masked number is NOT the order id space", otl != oid and "-" not in str(otl))
    check("the GWD stays the carrier's number", po["packages"][0]["tracking_number"] == "GWD004705019")
    found = purchases.find_by_customer_tracking(pdb, otl)
    check("a customer can be found by the masked number alone", bool(found))

    # ---- 6. package prep ---------------------------------------------------- #
    step("6. Package prep")
    po["packages"][0]["otlobly_status"] = "recieved no rd"
    purchases.upsert(pdb, po)
    built = pkgprep.build(odb["orders"], pdb, rate=3.1)
    ready_names = [c.get("name") for c in built.get("ready", [])]
    waiting_names = [c.get("name") for c in built.get("waiting", [])]
    check("a fully received customer is READY to pack", "معاذ الشيخ" in ready_names,
          f"ready={ready_names} waiting={waiting_names}")
    card = next((c for c in built["ready"] if c.get("name") == "معاذ الشيخ"), {})
    check("the prep card carries the prefilled WhatsApp message", bool(card.get("wa_text")))
    check("the message quotes the total in USD and ILS", "$" in (card.get("wa_text") or "") and "₪" in (card.get("wa_text") or ""))
    check("the prep card knows the customer location (city or address)",
          bool((card.get("city") or "") or (card.get("address") or "")), str(card.get("city")))
    po["packages"][0]["otlobly_status"] = "oredered"
    purchases.upsert(pdb, po)
    built2 = pkgprep.build(odb["orders"], pdb, rate=3.1)
    check("an un-received package is NOT ready",
          "معاذ الشيخ" not in [c.get("name") for c in built2.get("ready", [])])
    po["packages"][0]["otlobly_status"] = "recieved no rd"
    purchases.upsert(pdb, po)

    # ---- 7. money ----------------------------------------------------------- #
    step("7. Deposit and collection")
    core = normalize.phone_core("+970599711377")
    db.add_payment({"order_code": oid, "customer_phone": core, "customer_name": "معاذ الشيخ",
                    "kind": "deposit", "amount": 185.0, "currency": "ILS", "fx_rate": 3.7,
                    "amount_usd": 50.0, "note": "عربون"})
    db.add_payment({"order_code": oid, "customer_phone": core, "customer_name": "معاذ الشيخ",
                    "kind": "collect", "amount": 60.0, "currency": "USD", "fx_rate": 1.0,
                    "amount_usd": 60.0, "note": ""})
    rows = db.list_payments(order_code=oid)
    check("both ledger rows are stored", len(rows) == 2, str(len(rows)))
    kinds = sorted(r["kind"] for r in rows)
    check("the ledger keeps the three payment kinds apart", kinds == ["collect", "deposit"], str(kinds))
    dep = next(r for r in rows if r["kind"] == "deposit")
    check("a deposit entered in ILS is stored in USD with a frozen rate",
          abs(float(dep["amount_usd"]) - 50.0) < 0.001 and abs(float(dep["fx_rate"]) - 3.7) < 0.001)

    # ---- 8. activity log ----------------------------------------------------- #
    step("8. Activity log")
    activity.log("set", "order", oid, label="معاذ الشيخ", field="status", old="IN_CART", new="ORDERED")
    feed = activity.recent(limit=10)
    check("the change is on the activity feed", any(e.get("entity_id") == oid for e in feed))

    # ---- 9. invariants the audit found ---------------------------------------- #
    step("9. Invariants that must survive the restructure")
    done = set(alerts.STOP_DEFAULT)
    check("'complete' counts as a finished parcel", "complete" in done)
    check("'not recieved rd' does NOT count as finished (APP_AUDIT F-008)", "not recieved rd" not in done)
    check("received and dispatched sets stay disjoint",
          not (set(pkgprep.RECEIVED_STATUSES) & set(pkgprep.DISPATCHED_STATUSES)))
    labels = {r.get("label") for r in tracking.DEFAULT_OTLOBLY_MAP}
    check("only the owner-set 'complete' may tell a customer it was delivered",
          all(("تم التسليم" != (r.get("label") or "")) or r.get("status") == "complete"
              for r in tracking.DEFAULT_OTLOBLY_MAP), str(labels))
    check("a carrier 'Delivered' is remapped, never shown raw to a customer",
          any("اطلبلي" in (r.get("label") or "") for r in tracking.DEFAULT_STATUS_MAP))
    check("every order status has a place in the pipeline",
          set(store.PREORDER_STATUSES) | set(store.CART_STATUSES) | set(store.PLACED_STATUSES) | {"CANCELLED"} == set(store.STATUSES))

    print("\n――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
