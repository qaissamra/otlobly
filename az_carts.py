#!/usr/bin/env python3
"""
az_carts.py — the hand-off to AZ Studio, both ways (2026-09-11, bridge depth 2).

An order the operator decided to buy leaves here as ONE cart for ONE buying account and
becomes a task on AZ Studio; when the person there picks "Ordered" and types the Amazon
order number and the total ONCE, the result comes back and the purchase order is written
here from it — the customer orders flip to ORDERED with the number, the account and the
ETA, exactly as a hand-typed PO would do (purchases.apply_to_orders).

The cart row (SQLite table az_carts, one JSON doc per cart) is the whole state:

    queued     — waiting for an AZ Studio host to take it (or for the direct push)
    sent       — a host claimed it on its poll and is making the task (no ack yet;
                 RESEND_AFTER_S later it is queued again — the intake is idempotent)
    delivered  — the task exists on `host` (`task_id`)
    failed     — the host refused it (`error`, `code`: no_profile …) — fix and requeue
    in_cart / payment_added / issue — where the profile stands, as posted back
    ordered    — the order number is in; the PO is `po_id`
    done       — ticked off on AZ Studio after ordering
    cancelled  — withdrawn here (only before it was ordered)

Two roads to AZ Studio: `az.send_cart` (Render → the droplet, when AZ_OTLOBLY_TOKEN is set
here) and the hosts' own poll (`claim`, worker token) — either way one task, because the
intake is keyed by the cart id. A cart is ADDRESSED to a host (`host`; "" = the first host
to ask) so two AZ Studio hosts can never both make it.
"""

import json
import time
import uuid
from datetime import date

import db

STATUSES = ("queued", "sent", "delivered", "failed", "in_cart", "payment_added", "ordered",
            "issue", "done", "cancelled")
OPEN = ("queued", "sent", "delivered", "failed", "in_cart", "payment_added", "issue")
FINISHED = ("ordered", "done", "cancelled")
RESEND_AFTER_S = 600
HOSTS_KEY = "az:hosts"            # {host label: last poll epoch}
HOST_KEY = "az:cart_host"         # the default host new carts are addressed to
MAX_ITEMS = 100
_EVENTS_MAX = 40

LABEL = {"queued": "Queued for AZ Studio", "sent": "Reaching AZ Studio", "delivered": "On AZ Studio",
         "failed": "AZ Studio refused it", "in_cart": "In the Amazon cart", "payment_added": "Payment added",
         "ordered": "Ordered on Amazon", "issue": "Issue on AZ Studio", "done": "Done on AZ Studio",
         "cancelled": "Cancelled"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def new_id():
    return "azc_" + uuid.uuid4().hex[:10]


def _load_row(r):
    if not r:
        return None
    try:
        doc = json.loads(r["doc"])
    except (ValueError, TypeError):
        doc = {}
    for k in ("id", "business_id", "ref", "kind", "status", "host", "profile_box", "po_id", "task_id",
              "created_at", "updated_at"):
        doc[k] = r[k]
    return doc


def _write(c, doc):
    """Insert or replace one cart row from its doc (columns mirror the doc)."""
    c.execute("""INSERT INTO az_carts (id, business_id, ref, kind, status, host, profile_box, po_id,
                                       task_id, created_at, updated_at, doc)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET ref=excluded.ref, kind=excluded.kind,
                   status=excluded.status, host=excluded.host, profile_box=excluded.profile_box,
                   po_id=excluded.po_id, task_id=excluded.task_id, updated_at=excluded.updated_at,
                   doc=excluded.doc""",
              (doc["id"], doc.get("business_id") or db.current_business(), doc.get("ref"), doc.get("kind"),
               doc.get("status"), doc.get("host") or "", doc.get("profile_box"), doc.get("po_id"),
               doc.get("task_id"), doc.get("created_at"), doc.get("updated_at"),
               json.dumps(doc, ensure_ascii=False)))


# ── reads ─────────────────────────────────────────────────────────────────────────────────
def get(cart_id):
    if not cart_id:
        return None
    with db.connect() as c:
        return _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())


def find_by_ref(ref):
    if not ref:
        return None
    with db.connect() as c:
        return _load_row(c.execute("SELECT * FROM az_carts WHERE ref=? ORDER BY created_at DESC LIMIT 1",
                                   (str(ref),)).fetchone())


def list_(limit=200, status=None, business_id=None):
    """Newest first, this tenant's carts."""
    bid = business_id or db.current_business()
    with db.connect() as c:
        if status:
            rows = c.execute("SELECT * FROM az_carts WHERE business_id=? AND status=? "
                             "ORDER BY created_at DESC LIMIT ?", (bid, status, int(limit))).fetchall()
        else:
            rows = c.execute("SELECT * FROM az_carts WHERE business_id=? ORDER BY created_at DESC LIMIT ?",
                             (bid, int(limit))).fetchall()
    return [_load_row(r) for r in rows]


def open_for_orders(order_ids):
    """{order_id: cart} for every given order already on an OPEN cart — sending an order
    twice would make two tasks for one purchase."""
    want = {str(x) for x in (order_ids or []) if x}
    out = {}
    if not want:
        return out
    for cart in list_(limit=500):
        if cart.get("status") in OPEN:
            for oid in cart.get("order_ids") or []:
                if oid in want and oid not in out:
                    out[oid] = cart
    return out


def open_for_po(po_id):
    for cart in list_(limit=500):
        if cart.get("status") in OPEN and cart.get("kind") == "po" and cart.get("po_id") == po_id:
            return cart
    return None


def hosts():
    """{host label: last poll epoch} — every AZ Studio host that has asked us for carts."""
    h = db.get_setting(HOSTS_KEY) or {}
    return h if isinstance(h, dict) else {}


def default_host():
    """Where a new cart goes unless the operator picks: the saved setting, else the host
    whose roster we hold (the droplet, normally), else the most recent poller."""
    v = db.get_setting(HOST_KEY)
    if isinstance(v, str) and v.strip():
        return v.strip()
    try:
        import az_roster
        m = az_roster.meta()
        if m.get("have") and m.get("host"):
            return str(m["host"])
    except Exception:  # noqa: BLE001
        pass
    h = hosts()
    return max(h, key=h.get) if h else ""


# ── writes ────────────────────────────────────────────────────────────────────────────────
def create(doc):
    """Store a new cart (status queued). Returns it."""
    doc = dict(doc)
    doc.setdefault("id", new_id())
    doc.setdefault("status", "queued")
    doc.setdefault("host", "")
    doc.setdefault("events", [])
    doc["business_id"] = doc.get("business_id") or db.current_business()
    doc["created_at"] = doc.get("created_at") or now_iso()
    doc["updated_at"] = now_iso()
    doc["events"] = (doc["events"] + [{"ts": now_iso(), "what": "queued",
                                       "host": doc.get("host") or "any host"}])[-_EVENTS_MAX:]
    with db.connect() as c:
        _write(c, doc)
    return doc


def update(cart_id, **changes):
    """Merge `changes` into the cart's doc. Returns the cart, or None."""
    with db.connect() as c:
        doc = _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())
        if not doc:
            return None
        doc.update(changes)
        doc["updated_at"] = now_iso()
        _write(c, doc)
        return doc


def _event(doc, what, **extra):
    ev = {"ts": now_iso(), "what": what, **{k: v for k, v in extra.items() if v not in (None, "")}}
    doc["events"] = ((doc.get("events") or []) + [ev])[-_EVENTS_MAX:]


def claim(host, limit=20, now=None):
    """The poll: hand `host` every queued cart addressed to it (or to nobody), marking
    each `sent` ATOMICALLY (UPDATE … WHERE status='queued'), so two hosts asking at once
    can never both get one. A `sent` cart nobody acked within RESEND_AFTER_S is queued
    again first — the intake on the other side is idempotent, so a repeat is safe."""
    host = str(host or "").strip()[:80]
    now = now or time.time()
    stamp = now_iso()
    out = []
    with db.connect() as c:
        stale = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - RESEND_AFTER_S))
        for r in c.execute("SELECT * FROM az_carts WHERE status='sent' AND updated_at < ?", (stale,)).fetchall():
            doc = _load_row(r)
            doc["status"] = "queued"
            doc["updated_at"] = stamp
            _event(doc, "requeued", why="no answer from %s" % (doc.get("host") or "the host"))
            _write(c, doc)
        rows = c.execute("SELECT * FROM az_carts WHERE status='queued' AND (host=? OR host='') "
                         "ORDER BY created_at LIMIT ?", (host, int(limit))).fetchall()
        for r in rows:
            doc = _load_row(r)
            doc["status"] = "sent"
            doc["host"] = host
            doc["updated_at"] = stamp
            _event(doc, "sent", host=host)
            cur = c.execute("UPDATE az_carts SET status='sent', host=?, updated_at=?, doc=? "
                            "WHERE id=? AND status='queued'",
                            (host, stamp, json.dumps(doc, ensure_ascii=False), doc["id"]))
            if cur.rowcount == 1:
                out.append(doc)
    if host:
        h = hosts()
        h[host] = now
        db.set_setting(HOSTS_KEY, h)
    return out


def ack(cart_id, host, ok, task_id=None, error="", code="", existed=False):
    """The host's answer after taking a cart: delivered (the task exists) or failed (with
    the reason). Never regresses a cart the person already moved along."""
    with db.connect() as c:
        doc = _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())
        if not doc:
            return None
        if doc.get("status") in ("queued", "sent", "delivered", "failed"):
            if ok:
                doc["status"] = "delivered"
                doc["task_id"] = str(task_id or "")[:40] or doc.get("task_id")
                doc["host"] = str(host or doc.get("host") or "")[:80]
                doc["delivered_at"] = doc.get("delivered_at") or now_iso()
                doc["error"] = ""
                _event(doc, "delivered", host=doc["host"], task=doc.get("task_id"),
                       note="already there" if existed else "")
            else:
                doc["status"] = "failed"
                doc["host"] = str(host or doc.get("host") or "")[:80]
                doc["error"] = str(error or "refused")[:300]
                doc["code"] = str(code or "")[:40]
                _event(doc, "failed", host=doc["host"], error=doc["error"])
        doc["updated_at"] = now_iso()
        _write(c, doc)
        return doc


_EVENT_STATUS = {"in_cart": "in_cart", "payment_added": "payment_added", "ordered": "ordered",
                 "issue": "issue", "done": "done"}


def result(cart_id, payload):
    """A status posted back by AZ Studio. Records the event and moves the cart; the order
    number and total are kept on the cart (apply_ordered writes the PO from them)."""
    with db.connect() as c:
        doc = _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())
        if not doc:
            return None
        ev = str((payload or {}).get("status") or "").strip()
        new = _EVENT_STATUS.get(ev)
        if new == "done" and doc.get("status") not in ("ordered", "done"):
            new = None                                  # ticked Done without ordering: note it, keep the state
        if new and doc.get("status") != "cancelled":
            doc["status"] = new
        for k in ("task_id", "host"):
            if payload.get(k):
                doc[k] = str(payload[k])[:80]
        num = " ".join(str(payload.get("order_number") or "").split())
        if num:
            doc["order_number"] = num[:32]
        if payload.get("order_total_usd") not in (None, ""):
            try:
                doc["order_total_usd"] = round(float(payload["order_total_usd"]), 2)
            except (TypeError, ValueError):
                pass
        if payload.get("ordered_at"):
            doc["ordered_at"] = payload["ordered_at"]
        if payload.get("note"):
            doc["az_note"] = str(payload["note"])[:600]
        if payload.get("by"):
            doc["az_by"] = str(payload["by"])[:80]
        _event(doc, ev or "update", by=payload.get("by"), number=num, note=str(payload.get("note") or "")[:120])
        doc["updated_at"] = now_iso()
        _write(c, doc)
        return doc


def cancel(cart_id):
    """Withdraw a cart here. Only before it was ordered — after that the purchase is real."""
    with db.connect() as c:
        doc = _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())
        if not doc:
            return None, "not found"
        if doc.get("status") in ("ordered", "done"):
            return doc, "already ordered"
        doc["status"] = "cancelled"
        _event(doc, "cancelled")
        doc["updated_at"] = now_iso()
        _write(c, doc)
        return doc, None


def requeue(cart_id, host=None):
    """Send a failed or cancelled cart again (after fixing the account, say)."""
    with db.connect() as c:
        doc = _load_row(c.execute("SELECT * FROM az_carts WHERE id=?", (str(cart_id),)).fetchone())
        if not doc:
            return None, "not found"
        if doc.get("status") not in ("failed", "cancelled", "queued", "sent"):
            return doc, "already on AZ Studio"
        doc["status"] = "queued"
        if host is not None:
            doc["host"] = str(host or "")[:80]
        doc["error"] = ""
        doc["task_id"] = None
        _event(doc, "queued", host=doc.get("host") or "any host")
        doc["updated_at"] = now_iso()
        _write(c, doc)
        return doc, None


# ── building a cart ───────────────────────────────────────────────────────────────────────
def _asin_of(it):
    import normalize
    a = (it.get("asin") or normalize.extract_asin(it.get("link") or it.get("clean_url") or it.get("raw_url") or "")
         or "")
    return str(a).upper()


def _item_url(it, asin):
    return (it.get("link") or it.get("clean_url") or it.get("raw_url")
            or (f"https://www.amazon.com/dp/{asin}" if asin else "") or "")


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def build_from_orders(orders, profile_box, *, host="", by="", note="", url=""):
    """One cart from the customer orders the operator ticked. Keeps one line PER ORDER ITEM
    (customer_order_id on each — that is what the purchase order needs later); the wire
    payload merges them by ASIN for AZ Studio's catalogue. The buy-limit is Amazon's price
    when we last looked (serp_price_usd), else the customer price as a ceiling, else 0
    (AZ Studio treats 0 as "no limit known", never as "over")."""
    items, names, ids, collect, deposit, unknown = [], [], [], 0.0, 0.0, 0
    for o in orders:
        if not o:
            continue
        oid = o.get("order_id")
        ids.append(oid)
        nm = ((o.get("customer") or {}).get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
        collect += float(o.get("amount_to_collect_usd") or 0)
        deposit += float(o.get("deposit_usd") or 0)
        for it in (o.get("items") or [])[:MAX_ITEMS]:
            asin = _asin_of(it)
            u = _item_url(it, asin)
            if not asin and not u:
                continue
            limit = _num(it.get("serp_price_usd")) or _num(it.get("item_usd")) or 0.0
            if not limit:
                unknown += 1
            items.append({"asin": asin, "url": u, "name": (it.get("title") or "").strip()[:300],
                          "qty": int(it.get("qty") or 1), "max_price_usd": round(limit, 2),
                          "customer_order_id": oid, "customer_name": nm, "image": it.get("image") or ""})
    customer = (names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")) if names else ""
    return {"kind": "orders", "ref": "orders:" + "+".join(ids), "host": host or "",
            "profile_box": (profile_box or "").strip(), "po_id": None, "order_ids": ids,
            "customer": customer, "customers": names, "items": items[:MAX_ITEMS],
            "amount_to_collect_usd": round(collect, 2), "deposit_usd": round(deposit, 2),
            "note": (note or "").strip()[:600], "url": url, "by": by, "unknown_limits": unknown}


def build_from_po(po, *, host="", by="", note="", url="", orders=None):
    """One cart from a purchase order already recorded here (its items → lines; the
    customers and the collect amount from the orders its items are matched to)."""
    by_id = {o["order_id"]: o for o in (orders or [])}
    items, names, ids, collect = [], [], [], 0.0
    for pk in po.get("packages") or []:
        for it in pk.get("items") or []:
            asin = (it.get("asin") or "").upper()
            u = it.get("clean_url") or (f"https://www.amazon.com/dp/{asin}" if asin else "")
            if not asin and not u:
                continue
            oid = it.get("customer_order_id")
            nm = (it.get("customer_name") or "").strip()
            if oid and oid not in ids:
                ids.append(oid)
                o = by_id.get(oid) or {}
                collect += float(o.get("amount_to_collect_usd") or 0)
            if nm and nm not in names:
                names.append(nm)
            items.append({"asin": asin, "url": u, "name": (it.get("title") or "").strip()[:300],
                          "qty": int(it.get("qty") or 1), "max_price_usd": round(_num(it.get("est_cost_usd")) or 0.0, 2),
                          "customer_order_id": oid, "customer_name": nm, "image": it.get("image") or ""})
    customer = (names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")) if names else (po.get("ship_to") or "")
    return {"kind": "po", "ref": "po:" + str(po.get("po_id")), "host": host or "",
            "profile_box": (po.get("profile_box") or "").strip(), "po_id": po.get("po_id"), "order_ids": ids,
            "customer": customer, "customers": names, "items": items[:MAX_ITEMS],
            "amount_to_collect_usd": round(collect, 2) if ids else None, "deposit_usd": None,
            "note": (note or "").strip()[:600], "url": url, "by": by}


def payload(cart):
    """The wire object AZ Studio's intake takes (one line per product, quantities summed)."""
    merged = {}
    for it in cart.get("items") or []:
        key = ("asin:" + it["asin"]) if it.get("asin") else ("url:" + (it.get("url") or "").lower().rstrip("/"))
        m = merged.get(key)
        if m:
            m["qty"] += int(it.get("qty") or 1)
            if not m.get("max_price_usd") and it.get("max_price_usd"):
                m["max_price_usd"] = it["max_price_usd"]
            if it.get("customer_order_id") and it["customer_order_id"] not in m["customer_order_ids"]:
                m["customer_order_ids"].append(it["customer_order_id"])
        else:
            merged[key] = {"asin": it.get("asin") or "", "url": it.get("url") or "", "name": it.get("name") or "",
                           "qty": int(it.get("qty") or 1), "max_price_usd": it.get("max_price_usd") or 0.0,
                           "customer_order_id": it.get("customer_order_id") or "",
                           "customer_order_ids": [it["customer_order_id"]] if it.get("customer_order_id") else [],
                           "image": it.get("image") or ""}
    return {"id": cart["id"], "ref": cart.get("ref"), "kind": cart.get("kind"), "host": cart.get("host") or "",
            "profile": cart.get("profile_box"), "customer": cart.get("customer"), "customers": cart.get("customers") or [],
            "order_ids": cart.get("order_ids") or [], "po_id": cart.get("po_id"),
            "amount_to_collect_usd": cart.get("amount_to_collect_usd"), "deposit_usd": cart.get("deposit_usd"),
            "items": list(merged.values()), "note": cart.get("note") or "", "url": cart.get("url") or "",
            "created_at": cart.get("created_at"), "by": cart.get("by") or ""}


# ── the result → the purchase order ───────────────────────────────────────────────────────
def apply_ordered(cart, *, buffer_days=10, actor=None, user="AZ Studio"):
    """The order number is in: write the purchase order and let purchases.apply_to_orders
    flip the customer orders — the same path a hand-typed PO takes. IDEMPOTENT: the cart
    remembers its PO (`po_id`), so a repeated or corrected result updates one PO.
    Returns (po, how, changes)."""
    import activity
    import purchases
    num = (cart.get("order_number") or "").strip()
    if not num:
        raise ValueError("no order number on this cart")
    pdb = purchases.load()
    orders = db.list_orders()
    existing = purchases.find(pdb, cart.get("po_id")) if cart.get("po_id") else None
    old_snapshot = json.loads(json.dumps(existing)) if existing else None
    if existing:
        po_dict = dict(existing)
        po_dict["amazon_order_number"] = num
        if cart.get("order_total_usd") is not None:
            po_dict["total_usd"] = cart["order_total_usd"]
        if not (po_dict.get("order_placed") or "").strip():
            po_dict["order_placed"] = date.today().isoformat()
        if not po_dict.get("profile_box") and cart.get("profile_box"):
            po_dict["profile_box"] = cart["profile_box"]
    else:
        po_dict = {"amazon_order_number": num, "ship_to": cart.get("customer") or "",
                   "profile_box": cart.get("profile_box") or None, "order_placed": date.today().isoformat(),
                   "total_usd": cart.get("order_total_usd"), "status": "PLACED", "custom": {},
                   "packages": [{"package_no": 1, "arrival": "", "tracking_number": None,
                                 "items": [{"title": it.get("name") or "", "link": it.get("url") or "",
                                            "asin": it.get("asin") or None, "qty": it.get("qty") or 1,
                                            "image": it.get("image") or None,
                                            "customer_order_id": it.get("customer_order_id"),
                                            "customer_name": it.get("customer_name")}
                                           for it in (cart.get("items") or [])]}]}
    po, how = purchases.save_full(pdb, po_dict, orders)
    purchases.save(pdb)
    src = "AZ Studio (%s) · Amazon %s" % (cart.get("host") or "?", num)
    changes = purchases.apply_to_orders(po, orders, buffer_days)
    for oid, ch in changes:
        db.update_order(oid, ch, actor or {"username": "az-studio"})
        activity.log("set", "order", oid, oid, detail=f"auto → {ch.get('status', 'updated')} from {src}", user=user)
    if how == "created":
        activity.log("created", "purchase", po["po_id"], "Order # " + num,
                     detail=f"written from {src} — {cart.get('customer') or cart.get('ref')}", user=user)
    else:
        activity.log_po_diff(old_snapshot, po, user=user)
    update(cart["id"], po_id=po["po_id"])
    return po, how, changes
