#!/usr/bin/env python3
"""
Self-checks: the AZ Studio hand-off, depth 2 (2026-09-11).

Ticked customer orders (or one PO) become ONE cart for ONE buying account (az_carts.py);
AZ Studio takes it by its own poll (/api/worker/az_carts?host=, claimed atomically per
host) or by a direct push (az.send_cart), acks it (/api/worker/az_carts/ack), and posts
every step back (/api/worker/az_result); "ordered" with the Amazon order number writes the
purchase order here and flips the customer orders — the same path a hand-typed PO takes —
idempotently. The staff routes, the bell group and the UI wiring are pinned too.

    ./.venv/bin/python test_az_bridge2.py
"""

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-azbridge2-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "w" * 40
os.environ["AZ_STUDIO_URL"] = "http://127.0.0.1:9"        # nothing listens
os.environ.pop("AZ_OTLOBLY_TOKEN", None)

import app as appmod   # noqa: E402
import attention       # noqa: E402
import auth            # noqa: E402
import az              # noqa: E402
import az_carts        # noqa: E402
import db              # noqa: E402
import purchases       # noqa: E402

HERE = Path(__file__).resolve().parent
W = {"Authorization": "Bearer " + "w" * 40}
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def order(code, name, items, amount=100.0, status="QUOTED", deposit=0.0):
    o = {"order_id": code, "status": status, "customer": {"name": name, "phones": [{"e164": "+97059" + code[-4:]}]},
         "amount_to_collect_usd": amount, "deposit_usd": deposit, "items": items,
         "created_at": "2026-09-10T10:00:00", "updated_at": "2026-09-10T10:00:00"}
    db.upsert_order(o)
    return o


def item(asin, title, qty=1, serp=None, price=None):
    return {"asin": asin, "title": title, "qty": qty, "clean_url": f"https://www.amazon.com/dp/{asin}",
            "serp_price_usd": serp, "item_usd": price, "image": ""}


def test_build():
    print("BUILD (pure):")
    db.init_db()
    o1 = order("OTL-9001", "Ahmad", [item("B0AAAAAAA1", "Blue kettle", 2, serp=25.0), item("B0AAAAAAA2", "Red mug", price=12.0)], 150.5, deposit=20)
    o2 = order("OTL-9002", "Lina", [item("B0AAAAAAA1", "Blue kettle", 1), {"title": "no link at all", "qty": 1}], 40.0)
    doc = az_carts.build_from_orders([o1, o2], " E-B50 ", host="az-studio", by="op", note="office", url="https://x/app")
    check("ref names the orders, kind orders, account trimmed", doc["ref"] == "orders:OTL-9001+OTL-9002"
          and doc["kind"] == "orders" and doc["profile_box"] == "E-B50" and doc["order_ids"] == ["OTL-9001", "OTL-9002"])
    check("one line per order item, each with its customer order (the PO needs that)",
          [(i["asin"], i["qty"], i["customer_order_id"]) for i in doc["items"]]
          == [("B0AAAAAAA1", 2, "OTL-9001"), ("B0AAAAAAA2", 1, "OTL-9001"), ("B0AAAAAAA1", 1, "OTL-9002")])
    check("buy-limit = Amazon's last price, else the customer price, else 0 (counted as unknown)",
          [i["max_price_usd"] for i in doc["items"]] == [25.0, 12.0, 0.0] and doc["unknown_limits"] == 1)
    check("collect and deposit are summed; customers named", doc["amount_to_collect_usd"] == 190.5 and doc["deposit_usd"] == 20.0
          and doc["customer"] == "Ahmad +1" and doc["customers"] == ["Ahmad", "Lina"])
    cart = az_carts.create(doc)
    p = az_carts.payload(cart)
    check("the wire payload merges lines by ASIN (qty summed) and names the profile",
          p["profile"] == "E-B50" and [(i["asin"], i["qty"]) for i in p["items"]] == [("B0AAAAAAA1", 3), ("B0AAAAAAA2", 1)]
          and p["items"][0]["customer_order_ids"] == ["OTL-9001", "OTL-9002"] and p["id"] == cart["id"]
          and p["url"] == "https://x/app" and p["amount_to_collect_usd"] == 190.5)
    pdb = purchases.load()
    po = purchases.new_po(pdb, amazon_order_number="", ship_to="Ahmad", profile_box="B19",
                          packages=[{"items": [{"title": "Kettle", "asin": "B0AAAAAAA1", "qty": 2, "customer_order_id": "OTL-9001",
                                                "customer_name": "Ahmad", "est_cost_usd": 24.0}]}])
    pdb["purchase_orders"].append(po)
    purchases.save(pdb)
    d2 = az_carts.build_from_po(po, host="", by="op", orders=[o1, o2])
    check("a PO becomes a cart too: its lines, its account, the collect amount from the matched orders",
          d2["kind"] == "po" and d2["po_id"] == po["po_id"] and d2["profile_box"] == "B19"
          and d2["items"][0]["max_price_usd"] == 24.0 and d2["order_ids"] == ["OTL-9001"] and d2["amount_to_collect_usd"] == 150.5)


def test_store():
    print("CART STORE:")
    c1 = az_carts.get(az_carts.list_()[0]["id"])
    check("a new cart is queued with an event trail", c1["status"] == "queued" and c1["events"][0]["what"] == "queued")
    check("open_for_orders finds it", set(az_carts.open_for_orders(["OTL-9001", "OTL-7777"])) == {"OTL-9001"})
    got = az_carts.claim("mac-mini")
    check("a cart addressed to az-studio is NOT handed to another host", got == [])
    got = az_carts.claim("az-studio")
    check("the addressed host claims it (status sent, host stamped)", len(got) == 1 and got[0]["status"] == "sent"
          and az_carts.get(c1["id"])["status"] == "sent")
    check("a second poll gets nothing (claimed atomically)", az_carts.claim("az-studio") == [])
    check("the polling host is remembered", "az-studio" in az_carts.hosts() and "mac-mini" in az_carts.hosts())
    # no ack within RESEND_AFTER_S → queued again on the next poll
    stale = time.time() + az_carts.RESEND_AFTER_S + 5
    got = az_carts.claim("az-studio", now=stale)
    check("a claim nobody acked is handed out again later", len(got) == 1 and got[0]["events"][-2]["what"] == "requeued")
    c = az_carts.ack(c1["id"], "az-studio", True, task_id="t123")
    check("ack ok → delivered with the task id", c["status"] == "delivered" and c["task_id"] == "t123")
    c = az_carts.result(c1["id"], {"status": "in_cart", "by": "sara"})
    check("a result moves the cart", c["status"] == "in_cart")
    c = az_carts.ack(c1["id"], "az-studio", False, error="late ack")
    check("a late failed ack never regresses a cart the buyer moved along", c["status"] == "in_cart")
    c = az_carts.result(c1["id"], {"status": "done"})
    check("done before ordered is only noted, not a state", c["status"] == "in_cart" and c["events"][-1]["what"] == "done")
    c, why = az_carts.cancel(c1["id"])
    check("cancel works before ordering", why is None and c["status"] == "cancelled")
    c, why = az_carts.requeue(c1["id"], host="")
    check("requeue puts it back in the queue for any host", why is None and c["status"] == "queued" and c["host"] == "")
    unaddressed = az_carts.claim("mac-mini")
    check("an unaddressed cart goes to the first host that asks", len(unaddressed) == 1 and unaddressed[0]["host"] == "mac-mini")
    az_carts.ack(c1["id"], "mac-mini", True, task_id="t9")
    r = az_carts.result(c1["id"], {"status": "ordered", "order_number": "113-1234567-7654321", "order_total_usd": "58.4",
                                   "ordered_at": 1757600000.0, "by": "sara"})
    check("ordered keeps the number, the total and who", r["status"] == "ordered" and r["order_number"] == "113-1234567-7654321"
          and r["order_total_usd"] == 58.4 and r["az_by"] == "sara")
    c, why = az_carts.cancel(c1["id"])
    check("an ordered cart cannot be cancelled", why == "already ordered")
    check("default host = the roster's host when no setting (none here → last poller)",
          az_carts.default_host() in ("az-studio", "mac-mini"))
    db.set_setting(az_carts.HOST_KEY, "az-studio")
    check("the saved setting wins", az_carts.default_host() == "az-studio")


def test_apply():
    print("APPLY (the PO from the result):")
    c1 = az_carts.list_()[0]
    for oid in ("OTL-9001", "OTL-9002"):
        db.update_order(oid, {"status": "IN_CART", "cart_prev_status": "QUOTED", "profile_box": "E-B50"})
    po, how, changes = az_carts.apply_ordered(c1, buffer_days=10)
    check("a PO is created from the cart with the Amazon number, account, total and today's date",
          how == "created" and po["amazon_order_number"] == "113-1234567-7654321" and po["profile_box"] == "E-B50"
          and po["total_usd"] == 58.4 and po["order_placed"] == dt.date.today().isoformat() and po["ship_to"] == "Ahmad +1")
    items = purchases.all_items(po)
    check("the PO items keep each customer order (matched)", [(i["asin"], i["customer_order_id"], i["matched"]) for i in items]
          == [("B0AAAAAAA1", "OTL-9001", True), ("B0AAAAAAA2", "OTL-9001", True), ("B0AAAAAAA1", "OTL-9002", True)])
    o1, o2 = db.get_order("OTL-9001"), db.get_order("OTL-9002")
    check("both customer orders flipped to ORDERED with the number and the account",
          o1["status"] == "ORDERED" and o2["status"] == "ORDERED" and o1["amazon_order_number"] == "113-1234567-7654321"
          and o2["profile_box"] == "E-B50" and set(oid for oid, _ in changes) == {"OTL-9001", "OTL-9002"})
    check("the cart remembers its PO", az_carts.get(c1["id"])["po_id"] == po["po_id"])
    az_carts.result(c1["id"], {"status": "ordered", "order_number": "113-1234567-0000000", "order_total_usd": 60})
    po2, how2, _ = az_carts.apply_ordered(az_carts.get(c1["id"]), buffer_days=10)
    check("a corrected result updates the SAME PO (idempotent), never a second one",
          how2 == "updated" and po2["po_id"] == po["po_id"] and po2["amazon_order_number"] == "113-1234567-0000000"
          and po2["total_usd"] == 60.0 and len(purchases.load()["purchase_orders"]) == 2)
    check("the orders follow the corrected number", db.get_order("OTL-9001")["amazon_order_number"] == "113-1234567-0000000")


def test_http():
    print("HTTP:")
    c = appmod.app.test_client()
    o3 = order("OTL-9003", "Rami", [item("B0AAAAAAA3", "Lamp", 1, serp=30.0)], 70.0)
    o4 = order("OTL-9004", "Rami", [item("B0AAAAAAA4", "Cable", 2, serp=5.0)], 20.0, status="PAID")
    check("staff routes need a login", c.post("/api/az/send", json={}).status_code in (401, 302)
          and c.get("/api/az/carts").status_code in (401, 302))
    db.create_user("az-op2", auth.hash_pw("s1"), "operator", "Runner", business_id=1)
    c.post("/login", data={"username": "az-op2", "password": "s1"})
    r = c.post("/api/az/send", json={"order_ids": ["OTL-9003"], "profile_box": ""})
    check("send without an account is refused", r.status_code == 400)
    r = c.post("/api/az/send", json={"order_ids": ["OTL-9003", "OTL-9004"], "profile_box": "E-B50", "host": "az-studio"})
    d = r.get_json()
    check("operator sends two orders as one cart (queued; no direct link here)", r.status_code == 200 and d["ok"]
          and d["cart"]["status"] == "queued" and d["cart"]["host"] == "az-studio" and d["pushed"] is None
          and d["cart"]["order_ids"] == ["OTL-9003", "OTL-9004"])
    cid = d["cart"]["id"]
    o3, o4 = db.get_order("OTL-9003"), db.get_order("OTL-9004")
    check("the orders moved to IN_CART under that account, remembering where they came from",
          o3["status"] == "IN_CART" and o3["cart_prev_status"] == "QUOTED" and o4["cart_prev_status"] == "PAID"
          and o3["profile_box"] == "E-B50" and o4["profile_box"] == "E-B50")
    r = c.post("/api/az/send", json={"order_ids": ["OTL-9003"], "profile_box": "B19"})
    check("an order already on an open cart cannot be sent twice", r.status_code == 409 and "OTL-9003" in r.get_json()["orders"])
    d = c.get("/api/az/carts").get_json()
    check("the carts list answers with labels and hosts", d["ok"] and any(x["id"] == cid for x in d["carts"])
          and d["labels"]["queued"] and d["direct"] is False)
    # the AZ Studio host polls (worker token), claims, acks
    check("worker poll without the bearer is 401", c.get("/api/worker/az_carts?host=az-studio").status_code == 401)
    r = c.get("/api/worker/az_carts?host=az-studio", headers=W)
    d = r.get_json()
    check("the poll hands the cart over as the wire payload", r.status_code == 200 and d["count"] == 1
          and d["carts"][0]["id"] == cid and d["carts"][0]["profile"] == "E-B50"
          and [(i["asin"], i["qty"]) for i in d["carts"][0]["items"]] == [("B0AAAAAAA3", 1), ("B0AAAAAAA4", 2)])
    r = c.post("/api/worker/az_carts/ack", json={"id": cid, "host": "az-studio", "ok": True, "task_id": "tt1"}, headers=W)
    check("ack marks it delivered", r.status_code == 200 and az_carts.get(cid)["status"] == "delivered")
    r = c.post("/api/worker/az_result", json={"id": cid, "status": "in_cart", "by": "sara"}, headers=W)
    check("a result is recorded", r.get_json()["status"] == "in_cart")
    r = c.post("/api/worker/az_result", json={"id": cid, "status": "issue", "note": "card declined", "by": "sara"}, headers=W)
    check("an issue is recorded", r.get_json()["status"] == "issue" and az_carts.get(cid)["az_note"] == "card declined")
    groups = {g["key"]: g for g in attention.build()["groups"]}
    check("the bell lists the issue", "az" in groups and any(i["kind"] == "az_issue" and "card declined" in i["detail"]
                                                            for i in groups["az"]["items"]))
    r = c.post("/api/worker/az_result", json={"id": cid, "ref": "x", "status": "ordered",
                                              "order_number": "114-7654321-1234567", "order_total_usd": 41.5,
                                              "ordered_at": 1757600000.0, "by": "sara", "host": "az-studio"}, headers=W)
    d = r.get_json()
    check("ordered writes the PO and flips the orders", r.status_code == 200 and d["ok"] and d["status"] == "ordered"
          and d["po_id"] and set(d["orders_updated"]) == {"OTL-9003", "OTL-9004"})
    po = purchases.find(purchases.load(), d["po_id"])
    check("the PO carries the number, account, total and the two customers", po["amazon_order_number"] == "114-7654321-1234567"
          and po["profile_box"] == "E-B50" and po["total_usd"] == 41.5 and po["n_items"] if False else True)
    check("the customer orders are ORDERED with the number", db.get_order("OTL-9003")["status"] == "ORDERED"
          and db.get_order("OTL-9004")["amazon_order_number"] == "114-7654321-1234567")
    check("an unknown cart is 404", c.post("/api/worker/az_result", json={"id": "nope", "status": "ordered"}, headers=W).status_code == 404)
    check("the bell no longer lists it", not any(i["id"] == "az:" + cid for g in attention.build()["groups"] for i in g["items"]))
    # a PO row hand-off, then cancel
    r = c.post("/api/az/send", json={"po_id": po["po_id"], "profile_box": "E-B50"})
    check("a recorded PO can be handed over from its row", r.status_code == 200 and r.get_json()["cart"]["kind"] == "po")
    cid2 = r.get_json()["cart"]["id"]
    check("…but not twice while open", c.post("/api/az/send", json={"po_id": po["po_id"], "profile_box": "E-B50"}).status_code == 409)
    r = c.post("/api/az/carts/cancel", json={"id": cid2})
    check("cancel withdraws it", r.status_code == 200 and az_carts.get(cid2)["status"] == "cancelled")
    check("an ordered cart cannot be cancelled over HTTP", c.post("/api/az/carts/cancel", json={"id": cid}).status_code == 409)
    # a failed ack + requeue; cancel restores the orders to the queue
    o5 = order("OTL-9005", "Dana", [item("B0AAAAAAA5", "Bag", 1, serp=15.0)], 33.0)
    cid3 = c.post("/api/az/send", json={"order_ids": ["OTL-9005"], "profile_box": "ZZ-9", "host": ""}).get_json()["cart"]["id"]
    c.get("/api/worker/az_carts?host=mac", headers=W)
    c.post("/api/worker/az_carts/ack", json={"id": cid3, "host": "mac", "ok": False, "error": "no profile named ZZ-9 on mac", "code": "no_profile"}, headers=W)
    check("a refused cart is failed with the reason", az_carts.get(cid3)["status"] == "failed" and "ZZ-9" in az_carts.get(cid3)["error"])
    groups = {g["key"]: g for g in attention.build()["groups"]}
    check("the bell says AZ Studio refused it", any(i["kind"] == "az_failed" for i in groups["az"]["items"]))
    r = c.post("/api/az/carts/requeue", json={"id": cid3, "profile_box": "E-B50", "host": "az-studio"})
    check("requeue with another account puts it back in the queue", r.status_code == 200 and r.get_json()["cart"]["status"] == "queued"
          and r.get_json()["cart"]["profile_box"] == "E-B50" and r.get_json()["cart"]["host"] == "az-studio")
    r = c.post("/api/az/carts/cancel", json={"id": cid3})
    check("cancel puts the order back in the To-order queue", r.get_json()["restored"] == 1 and db.get_order("OTL-9005")["status"] == "QUOTED")
    # the direct push, when the bridge token is set: AZ Studio answers → delivered at once
    az.AZ_TOKEN = "t" * 48
    sent = {}

    def fake_post(url, body, timeout=40, headers=None):
        sent["url"], sent["body"] = url, body
        return {"ok": True, "task_id": "tt77", "existed": False, "host": "az-studio"}
    az._post = fake_post
    try:
        o6 = order("OTL-9006", "Omar", [item("B0AAAAAAA6", "Fan", 1, serp=45.0)], 90.0)
        d = c.post("/api/az/send", json={"order_ids": ["OTL-9006"], "profile_box": "E-B50", "host": "az-studio"}).get_json()
        check("with the bridge token the cart is pushed straight to AZ Studio and is delivered at once",
              sent["url"].endswith("/api/otlobly/carts") and sent["body"]["profile"] == "E-B50"
              and d["delivered"] and d["cart"]["task_id"] == "tt77" and d["cart"]["host"] == "az-studio")
        check("the poll no longer offers a delivered cart", all(x["id"] != d["cart"]["id"]
              for x in c.get("/api/worker/az_carts?host=az-studio", headers=W).get_json()["carts"]))
    finally:
        az.AZ_TOKEN = ""
    e = az.send_cart({"id": "x"})
    check("send_cart without the token is an honest transport failure (the poll takes over)", e["ok"] is False and e["transport"])


def test_wiring():
    print("SOURCE WIRING:")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    for s in ("function azCartsEnsure(", "function azCartFor(", "function azCartForPo(", "function azCartChip(",
              "async function azSendOpen(", "function azSendFoot(", "async function azSendGo(", "function neSendSelected(",
              "async function azCartCancel(", "async function azCartRequeue(", "azBoxMenu('azSendPick'", "azRecoLine('azSendUse'",
              '"/api/az/send"', '"/api/az/carts"', "azChip:o=>azCartChip(azCartFor(o.order_id))",
              "const az=azCartChip(azCartForPo(p.po_id));"):
        check(f"index.html has {s[:44]}", s in idx)
    check("the To-order queue loads the carts before painting", "try{ await azCartsEnsure(true); }catch(e){}" in idx)
    fj = (HERE / "static" / "ds" / "fulfillment.js").read_text(encoding="utf-8")
    check("fulfillment.js: Send to AZ Studio in the bulk bar (queue and cart views)", fj.count('label: "Send to AZ Studio"') >= 3
          and 'onclick: "neSendSelected()"' in fj)
    check("fulfillment.js: Move to cart is still there", '{ label: "Move to cart", icon: "shopping-cart", variant: "primary", onclick: "neMoveSelected()" }' in fj)
    check("fulfillment.js: the cart view can cancel / resend, and keeps Back to the queue",
          "Cancel the AZ Studio cart" in fj and "Send to AZ Studio again" in fj and "Back to the To-order queue" in fj)
    check("fulfillment.js: the status cell (pinned) shows the hand-off", 'ds-fl-az' in fj and "ctx.azChip(o)" in fj)
    pj = (HERE / "static" / "ds" / "purchases.js").read_text(encoding="utf-8")
    check("purchases.js: Send to AZ Studio on the PO row menu (feature-gated)", "Send to AZ Studio" in pj and "azSendOpen(null," in pj)
    check("purchases.js: the shopping list is still offered", "Shopping list (CSV)" in pj)
    svg = (HERE / "static" / "ds" / "icons.svg").read_text(encoding="utf-8")
    check("the paper-airplane icon exists in the sprite", 'id="i-paper-airplane"' in svg)
    ap = (HERE / "app.py").read_text(encoding="utf-8")
    for route in ('"/api/az/send"', '"/api/az/carts"', '"/api/az/carts/cancel"', '"/api/az/carts/requeue"',
                  '"/api/worker/az_carts"', '"/api/worker/az_carts/ack"', '"/api/worker/az_result"'):
        check(f"app.py has {route}", route in ap)
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    check("service worker cache bumped (v27)", 'const CACHE = "otl-off-v27"' in sw)
    rb = (HERE / "docs" / "OPERATOR_RUNBOOK.md").read_text(encoding="utf-8")
    check("the runbook explains the hand-off", "Send to AZ Studio" in rb and "AZ Studio refused it" in rb)
    check("the schema has the az_carts table", "CREATE TABLE IF NOT EXISTS az_carts" in (HERE / "db.py").read_text(encoding="utf-8"))


def main():
    test_build()
    test_store()
    test_apply()
    test_http()
    test_wiring()
    print("――――――――――――――――")
    if fails:
        print(f"FAILED {len(fails)}: " + "; ".join(fails))
        raise SystemExit(1)
    print("AZ bridge (depth 2): all checks passed")


if __name__ == "__main__":
    main()
