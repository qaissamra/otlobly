#!/usr/bin/env python3
"""
Local order store — orders.json is the source of truth the dashboard, ClickUp
sync, Sheet mirror and order-placer all read/write. Plain JSON, stdlib only.

Money is in USD throughout. One order = one customer + 1..N Amazon items.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import normalize
from paths import data_path

STORE_FILE = data_path("orders.json")

# Order lifecycle: request → quote → pay → order → ship → arrive → deliver.
STATUSES = ["REQUESTED", "QUOTED", "PAID", "ORDERED", "SHIPPED",
            "ARRIVED", "DELIVERED", "COLLECTED", "CANCELLED"]

# Free-text statuses from the legacy sheet -> our logical status.
SHEET_STATUS_MAP = {
    "ordered": "ORDERED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "still need to order": "REQUESTED",
    "need to order": "REQUESTED",
    "quoted": "QUOTED",
    "paid": "PAID",
    "shipped": "SHIPPED",
    "arrived": "ARRIVED",
    "delivered": "DELIVERED",
    "collected": "COLLECTED",
}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load():
    if STORE_FILE.exists():
        return json.loads(STORE_FILE.read_text())
    return {"orders": [], "seq": 0}


def save(db):
    STORE_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def signature(customer_key, items):
    """Stable key for idempotent ingest: customer + the set of ASINs/urls."""
    keys = sorted((it.get("asin") or it.get("clean_url") or it.get("raw_url"))
                  for it in items)
    return customer_key + "|" + "|".join(k for k in keys if k)


def map_sheet_status(raw):
    return SHEET_STATUS_MAP.get((raw or "").strip().lower(), "REQUESTED")


def new_order(db, *, name, phones, address, items, batch=None, deposit_usd=0.0,
              profile_box=None, status="REQUESTED", amount_to_collect_usd=None,
              notes="", tracking_number=None, city=""):
    """Build an order dict and assign the next OTL-#### id (does not save)."""
    db["seq"] = db.get("seq", 0) + 1
    return {
        "order_id": f"OTL-{db['seq']:04d}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "customer": {
            "name": (name or "").strip(),
            "phones": phones,                       # list of normalize_phone dicts
            "address": (address or "").strip(),
            "city": (city or "").strip(),
            "notes": (notes or "").strip(),
        },
        "items": items,                             # list of parse_items dicts
        "batch": str(batch).strip() if batch not in (None, "") else None,
        "deposit_usd": float(deposit_usd or 0),
        "profile_box": profile_box,
        "placed_at": None,
        "checkout": None,                           # filled by order_placer
        "amount_to_collect_usd": amount_to_collect_usd,
        "status": status if status in STATUSES else "REQUESTED",
        "amazon_order_number": None,                # the bulk Amazon order # (set from the PO)
        "amazon_arrival": None,                     # PO package arrival date (Amazon → us)
        "est_delivery_customer": None,              # arrival + buffer days → shown to the customer
        "quoted_at": None,                          # when the quote was sent
        "paid_at": None,                            # when payment was confirmed
        "delivered_at": None,                       # realized-profit timestamp
        "clickup_task_id": None,
        "sheet_row": None,
        "tracking_number": tracking_number,
        "signature": signature(normalize.customer_key(phones, name), items),
    }


PREORDER_STATUSES = ("REQUESTED", "QUOTED", "PAID")


def need_order(orders):
    """The demand queue: orders not yet placed on Amazon, as product cards — the
    bulk-buying work-list (box/batch absent; assigned when the PO is recorded)."""
    rows = []
    for o in orders:
        if o.get("status") not in PREORDER_STATUSES:
            continue
        ph = primary_phone(o)
        cust = o.get("customer", {}) or {}
        rows.append({
            "order_id": o["order_id"], "status": o["status"],
            "customer": cust.get("name"), "phone": ph["e164"] if ph else None,
            "phones": [p.get("e164") for p in (cust.get("phones") or []) if p.get("e164")],
            "address": cust.get("address"), "city": cust.get("city"), "notes": cust.get("notes"),
            "amount_to_collect_usd": o.get("amount_to_collect_usd"),
            "deposit_usd": round(o.get("deposit_usd") or 0, 2),
            "est_delivery_customer": o.get("est_delivery_customer"),
            "created_at": o.get("created_at"),
            "items": [{"asin": it.get("asin"), "title": it.get("title"),
                       "image": it.get("image"), "qty": it.get("qty") or 1,
                       "item_usd": it.get("item_usd"),
                       "link": it.get("clean_url") or it.get("raw_url")}
                      for it in o.get("items", [])],
        })
    rows.sort(key=lambda r: r["created_at"] or "")
    return {"orders": rows, "count": len(rows),
            "products": sum(len(r["items"]) for r in rows),
            "total_usd": round(sum(r["amount_to_collect_usd"] or 0 for r in rows), 2),
            "deposits_usd": round(sum(r["deposit_usd"] or 0 for r in rows), 2)}


def upsert(db, order):
    """Insert, or merge into an existing order with the same signature.
    Returns (order, 'created'|'updated')."""
    for i, ex in enumerate(db["orders"]):
        if ex.get("signature") and ex["signature"] == order["signature"]:
            order["order_id"] = ex["order_id"]
            order["created_at"] = ex.get("created_at", order["created_at"])
            # Preserve fields set later in the pipeline.
            for keep in ("clickup_task_id", "sheet_row", "checkout",
                         "placed_at", "tracking_number", "amazon_order_number",
                         "amazon_arrival", "est_delivery_customer"):
                if ex.get(keep) and not order.get(keep):
                    order[keep] = ex[keep]
            order["updated_at"] = now_iso()
            db["orders"][i] = order
            return order, "updated"
    db["orders"].append(order)
    return order, "created"


def find(db, order_id):
    return next((o for o in db["orders"] if o["order_id"] == order_id), None)


def primary_phone(order):
    ph = order.get("customer", {}).get("phones") or []
    return ph[0] if ph else None
