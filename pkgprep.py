#!/usr/bin/env python3
"""
Package prep (تجهيز الطرود) — who can we pack & ship RIGHT NOW?

Crosses customer orders (demand) with PO packages (supply): a piece counts as
RECEIVED once it sits in a package whose owner-set Otlobly status is
"recieved rd" / "recieved no rd" (استلمتها اطلبلي — typos canonical, they're the
ClickUp vocabulary). Customers whose every live piece is in hand go in READY;
customers with some pieces received and some still on the way go in WAITING.
Each card carries a prefilled Arabic WhatsApp message with the quote total in
USD + ILS at the Settings rate fx.pkg_ils_per_usd.

  build(orders, pdb, rate, include_money) -> {"rate", "counts", "ready", "waiting"}

    python3 pkgprep.py         # print both buckets for the local store
"""

from collections import defaultdict
from urllib.parse import quote as _urlquote

import store
from purchases import ITEM_EXCEPTIONS

BRAND = "Otlobly"
DEFAULT_RATE = 3.1
# Owner-set package statuses (ClickUp vocabulary). Received = in our hands at
# the office; dispatched = already left toward the customer (covers the piece
# but doesn't put it on the prep table).
RECEIVED_STATUSES = {"recieved rd", "recieved no rd"}
DISPATCHED_STATUSES = {"sent rd", "sent no rd", "complete"}
# Orders whose pieces can still be sitting in (or heading to) the office.
# Pre-order statuses can't be PO-matched; DELIVERED+ already went out the door.
ACTIVE_STATUSES = ("ORDERED", "SHIPPED", "ARRIVED")


def _tally(pdb):
    """Per (order_id, ASIN): received / dispatched unit pools + exception units.

    Pools, not flags — one order line's qty can arrive split across packages,
    so readiness must compare unit sums, never mere presence.
    """
    recv = defaultdict(int)
    sent = defaultdict(int)
    exc = defaultdict(lambda: defaultdict(int))
    meta = {}                         # key -> {image, title} from the PO item
    for po in (pdb or {}).get("purchase_orders", []):
        for pk in po.get("packages", []):
            pstat = (pk.get("otlobly_status") or "").strip().lower()
            for it in pk.get("items", []):
                oid = it.get("customer_order_id")
                if not oid:
                    continue          # unmatched supply can't vouch for anyone
                key = (oid, (it.get("asin") or "").upper() or None)
                # PO items carry the fetched Amazon photo + real title; order
                # items usually don't. Remember the first non-empty of each so
                # the card can show a picture (fall back per field, not per item).
                m = meta.setdefault(key, {"image": None, "title": None})
                if not m["image"] and it.get("image"):
                    m["image"] = it["image"]
                if not m["title"] and it.get("title"):
                    m["title"] = it["title"]
                q = int(it.get("qty") or 1)
                istat = (it.get("status") or "").strip().upper()
                if istat in ITEM_EXCEPTIONS:
                    exc[key][istat] += q          # will never arrive
                elif pstat in RECEIVED_STATUSES:
                    recv[key] += q
                elif pstat in DISPATCHED_STATUSES:
                    sent[key] += q
    return recv, sent, exc, meta


def _person_key(order):
    """Same person key the CRM uses: primary phone e164, else the name."""
    ph = store.primary_phone(order)
    if ph and ph.get("e164"):
        return ph["e164"]
    return "name:" + (order.get("customer", {}).get("name") or "").strip().lower()


def _annotate_items(order, recv, sent, exc, meta):
    """Attach received/sent/exception/missing unit counts to each order item.

    Consumes from the tally pools so a duplicated ASIN line in one order can't
    double-count the same physical units.
    """
    out = []
    for it in order.get("items", []):
        n = int(it.get("qty") or 1)
        key = (order["order_id"], (it.get("asin") or "").upper() or None)
        m = meta.get(key) or {}       # PO-item image/title fallback
        exc_q, exceptions = 0, []
        pool = exc.get(key)
        if pool:
            for st in list(pool):
                take = min(pool[st], n - exc_q)
                if take > 0:
                    pool[st] -= take
                    exc_q += take
                    exceptions.append(st)
                if exc_q >= n:
                    break
        live = n - exc_q                      # units that can still show up
        r = min(recv.get(key, 0), live)
        if r:
            recv[key] -= r
        s = min(sent.get(key, 0), live - r)
        if s:
            sent[key] -= s
        missing = live - r - s
        state = ("missing" if missing else
                 "received" if r else
                 "sent" if s else "exception")
        out.append({
            "asin": it.get("asin"),
            "title": it.get("title") or m.get("title"),
            "image": it.get("image") or m.get("image"),
            "url": it.get("clean_url") or it.get("raw_url"),
            "qty": n,
            "received_qty": r,
            "sent_qty": s,
            "exception_qty": exc_q,
            "exceptions": exceptions,
            "missing_qty": missing,
            "state": state,
        })
    return out


# --------------------------------------------------------------------------- #
# Arabic WhatsApp messages (messages.py tone; amounts X.XX$, ILS whole ₪)
# --------------------------------------------------------------------------- #
def _ready_message(name, totals, include_money):
    name = name or "عميلنا العزيز"
    lines = [f"مرحباً {name} 👋",
             "🎉 وصلت جميع قطع طلبك إلى مكتب اطلبلي، وبدأنا بتجهيز طردك للتسليم."]
    if include_money and not totals["unpriced"] and totals["usd"] is not None:
        lines.append(f"💵 المبلغ الإجمالي: {totals['usd']:.2f}$ (≈ {totals['ils']} ₪)")
        if (totals["deposit_usd"] or 0) > 0:
            lines.append(f"✅ العربون المدفوع: {totals['deposit_usd']:.2f}$")
            lines.append(f"💰 المتبقي عند الاستلام: {totals['remaining_usd']:.2f}$"
                         f" (≈ {totals['remaining_ils']} ₪)")
    else:
        lines.append("سنؤكد لك المبلغ الإجمالي قريباً.")
    lines.append(f"شكراً لثقتك — {BRAND} 🧡")
    return "\n".join(lines)


def _partial_message(name, received_items):
    name = name or "عميلنا العزيز"
    lines = [f"مرحباً {name} 👋",
             "📦 وصل جزء من طلبك إلى مكتب اطلبلي:"]
    for it in received_items[:6]:
        t = (it.get("title") or it.get("asin") or "منتج").strip()
        if len(t) > 60:
            t = t[:57] + "…"
        q = it.get("received_qty") or 0
        lines.append(f"• {t}" + (f" ×{q}" if q > 1 else ""))
    extra = len(received_items) - 6
    if extra > 0:
        lines.append(f"• و{extra} قطع أخرى…")
    lines.append("⏳ باقي القطع في طريقها إلينا — فور اكتمالها نجهز طردك كاملاً ونخبرك.")
    lines.append(f"شكراً لصبرك — {BRAND} 🙏")
    return "\n".join(lines)


def build(orders, pdb, rate=DEFAULT_RATE, include_money=True):
    """Group active orders by person and split into ready / waiting cards."""
    recv, sent, exc, meta = _tally(pdb)

    groups = {}
    for o in orders or []:
        if o.get("status") not in ACTIVE_STATUSES:
            continue
        groups.setdefault(_person_key(o), []).append(o)

    ready, waiting = [], []
    for key, group in groups.items():
        first = group[0]
        card_orders = []
        n_items = n_received = n_missing = 0
        usd = dep = 0.0
        unpriced = False
        received_items = []
        for o in sorted(group, key=lambda x: x.get("order_id") or ""):
            items = _annotate_items(o, recv, sent, exc, meta)
            for it in items:
                n_items += it["qty"]
                n_received += it["received_qty"]
                n_missing += it["missing_qty"]
                if it["received_qty"]:
                    received_items.append(it)
            amt = o.get("amount_to_collect_usd")
            if amt is None:
                unpriced = True
            else:
                usd += float(amt)
            dep += float(o.get("deposit_usd") or 0)
            card_orders.append({
                "order_id": o["order_id"],
                "status": o.get("status"),
                "amount_to_collect_usd": amt,
                "deposit_usd": round(float(o.get("deposit_usd") or 0), 2),
                "items": items,
            })
        if n_received == 0:
            continue                  # nothing of theirs on the prep table yet

        usd_r = round(usd, 2)
        dep_r = round(dep, 2)
        totals = {
            "usd": usd_r,
            "ils": round(usd_r * rate),
            "deposit_usd": dep_r,
            "remaining_usd": round(usd_r - dep_r, 2),
            "remaining_ils": round(round(usd_r - dep_r, 2) * rate),
            "unpriced": unpriced,
        }
        name = (first.get("customer", {}).get("name") or "").strip()
        ph = store.primary_phone(first)
        is_ready = n_missing == 0
        wa_text = (_ready_message(name, totals, include_money) if is_ready
                   else _partial_message(name, received_items))
        wa = ph.get("wa") if ph else None
        card = {
            "key": key,
            "name": name,
            "phone": ph.get("e164") if ph else None,
            "wa": wa,
            "orders": card_orders,
            "totals": totals,
            "n_items": n_items,
            "n_received": n_received,
            "n_missing": n_missing,
            "wa_text": wa_text,
            "wa_url": f"https://wa.me/{wa}?text={_urlquote(wa_text)}" if wa else None,
        }
        if not include_money:         # fulfillment: page loads, money stays dark
            card["totals"] = {k: None for k in totals}
            for co in card["orders"]:
                co["amount_to_collect_usd"] = None
                co["deposit_usd"] = None
        (ready if is_ready else waiting).append(card)

    ready.sort(key=lambda c: (1 if (c["totals"]["usd"] is None or c["totals"]["unpriced"]) else 0,
                              -(c["totals"]["usd"] or 0)))
    waiting.sort(key=lambda c: (c["n_missing"], -c["n_received"]))
    return {
        "rate": rate,
        "counts": {"ready": len(ready), "waiting": len(waiting)},
        "ready": ready,
        "waiting": waiting,
    }


if __name__ == "__main__":
    import purchases
    rep = build(store.load().get("orders", []), purchases.load())
    for bucket in ("ready", "waiting"):
        print(f"\n===== {bucket.upper()} ({rep['counts'][bucket]}) =====")
        for c in rep[bucket]:
            print(f"  {c['name'] or c['key']}: {c['n_received']}/{c['n_items']} received,"
                  f" {c['n_missing']} missing, total {c['totals']['usd']}$")
