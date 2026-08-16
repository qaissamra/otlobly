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

import normalize
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
# The review-outreach cohort: goods received AND COD paid — the happiest, most
# complete moment to ask for a Facebook review.
COLLECTED_STATUS = "COLLECTED"
DEFAULT_COUPON_USD = 5


def _tally(pdb):
    """Per (order_id, ASIN): received / dispatched unit pools + exception units.

    Pools, not flags — one order line's qty can arrive split across packages,
    so readiness must compare unit sums, never mere presence.
    """
    recv = defaultdict(int)
    sent = defaultdict(int)
    exc = defaultdict(lambda: defaultdict(int))
    meta = {}                         # (order,asin) -> {image, title} from the PO item
    pkgmeta = {}                      # (po_id,package_no) -> package + the orders it holds
    for po in (pdb or {}).get("purchase_orders", []):
        po_id = po.get("po_id")
        for pk in po.get("packages", []):
            pstat = (pk.get("otlobly_status") or "").strip().lower()
            pkey = (po_id, pk.get("package_no"))
            for it in pk.get("items", []):
                oid = it.get("customer_order_id")
                if not oid:
                    continue          # unmatched supply can't vouch for anyone
                # remember the package so the card can offer a status dropdown
                pm = pkgmeta.setdefault(pkey, {
                    "po_id": po_id, "package_no": pk.get("package_no"),
                    "otlobly_status": pk.get("otlobly_status"),
                    "tracking_number": pk.get("tracking_number"),
                    "customer_tracking": pk.get("customer_tracking"),
                    "orders": set(), "n_items": 0})
                pm["orders"].add(oid)
                pm["n_items"] += int(it.get("qty") or 1)
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
    return recv, sent, exc, meta, pkgmeta


def _person_key(order):
    """Same person key the CRM uses: primary phone e164, else the name."""
    ph = store.primary_phone(order)
    if ph and ph.get("e164"):
        return ph["e164"]
    return "name:" + (order.get("customer", {}).get("name") or "").strip().lower()


def _person_location(group):
    """Delivery location for the card: latest non-empty city/address across the
    person's orders (the /order intake collects both on order.customer)."""
    city = addr = ""
    for o in sorted(group, key=lambda x: x.get("order_id") or "", reverse=True):
        cu = o.get("customer") or {}
        if not city and (cu.get("city") or "").strip():
            city = cu["city"].strip()
        if not addr and (cu.get("address") or "").strip():
            addr = cu["address"].strip()
        if city and addr:
            break
    return city, addr


def _pkgs_for(order_ids, pkgmeta):
    """Package cards (owner status + GWD + OTL number) holding any of these
    orders' pieces — the same shape everywhere a prep card lists parcels."""
    return sorted(
        ({"po_id": pm["po_id"], "package_no": pm["package_no"],
          "otlobly_status": pm["otlobly_status"],
          "tracking_number": pm.get("tracking_number"),
          "customer_tracking": pm.get("customer_tracking"),
          "n_items": pm["n_items"],
          "shared": bool(pm["orders"] - order_ids)}   # also holds other customers?
         for pm in pkgmeta.values() if pm["orders"] & order_ids),
        key=lambda p: (p["po_id"] or "", p["package_no"] or 0))


def _place_unkeyed(order, out, recv, sent, exc, meta):
    """Credit linked supply that the ASIN could not place on any line.

    A PO item carries customer_order_id — THAT link is what proves the piece
    belongs to this order; its ASIN only says which LINE. An order placed from
    an a.co short link has no ASIN on the line (expanding one is a network call
    Amazon blocks from our host, so intake stores the bare link), which made the
    two sides key differently — (order, None) against (order, B0DSZPZGJZ). The
    received unit landed nowhere, the order looked like "nothing arrived", and
    build() dropped THE WHOLE CUSTOMER off the board: their parcel sat received
    in the office with no card telling anyone to pack it.

    Only an ASIN-less line may absorb a stray piece. A line naming a DIFFERENT
    product stays missing on purpose — that is a real mismatch (the wrong thing
    was bought), not a lookup failure, and hiding it would be worse.
    """
    oid = order["order_id"]
    slots = [row for row, it in zip(out, order.get("items") or [])
             if not (it.get("asin") or "").strip()]
    if not slots:
        return
    for row in slots:
        if row["missing_qty"] <= 0:
            continue
        # same order as the keyed pass: exceptions first (never coming), then
        # what is in hand, then what already went out
        for key in [k for k in list(exc) if k[0] == oid]:
            for st in list(exc[key]):
                take = min(exc[key][st], row["missing_qty"])
                if take <= 0:
                    continue
                exc[key][st] -= take
                row["exception_qty"] += take
                row["missing_qty"] -= take
                if st not in row["exceptions"]:
                    row["exceptions"].append(st)
        for pool, field in ((recv, "received_qty"), (sent, "sent_qty")):
            for key in [k for k in list(pool) if k[0] == oid and pool[k] > 0]:
                take = min(pool[key], row["missing_qty"])
                if take <= 0:
                    continue
                pool[key] -= take
                row[field] += take
                row["missing_qty"] -= take
                # the line knew no ASIN; the piece that filled it does — borrow
                # its identity so the card can show a photo and a real title
                if not row["asin"]:
                    m = meta.get(key) or {}
                    row["asin"] = key[1]
                    row["title"] = row["title"] or m.get("title")
                    row["image"] = row["image"] or m.get("image")
        row["state"] = ("missing" if row["missing_qty"] else
                        "received" if row["received_qty"] else
                        "sent" if row["sent_qty"] else "exception")


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
    _place_unkeyed(order, out, recv, sent, exc, meta)
    return out


def _plain_items(order, meta):
    """Items of a COLLECTED order for its review card: the goods are already with
    the customer, so the whole qty counts as sent (no tally-pool consumption)."""
    out = []
    for it in order.get("items", []):
        n = int(it.get("qty") or 1)
        key = (order["order_id"], (it.get("asin") or "").upper() or None)
        m = meta.get(key) or {}
        out.append({
            "asin": it.get("asin"),
            "title": it.get("title") or m.get("title"),
            "image": it.get("image") or m.get("image"),
            "url": it.get("clean_url") or it.get("raw_url"),
            "qty": n,
            "received_qty": 0,
            "sent_qty": n,
            "exception_qty": 0,
            "exceptions": [],
            "missing_qty": 0,
            "state": "sent",
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


def _fmt_items(items, qty_field):
    """Bulleted product list (title, ×qty when >1), capped at 6 + a '+N more'."""
    out = []
    for it in items[:6]:
        t = (it.get("title") or it.get("asin") or "منتج").strip()
        if len(t) > 60:
            t = t[:57] + "…"
        q = it.get(qty_field) or 0
        out.append(f"• {t}" + (f" ×{q}" if q > 1 else ""))
    extra = len(items) - 6
    if extra > 0:
        out.append(f"• و{extra} قطع أخرى…")
    return out


def _waiting_message(name, sent_items, received_items, coming_items):
    """Status update for a customer still mid-fulfilment: what already shipped to
    them, what's in our office ready, and what's still on the way. Deliberately
    NO amounts — we only quote the total once the whole order is ready
    (_ready_message), so a partial order never triggers a premature price."""
    name = name or "عميلنا العزيز"
    lines = [f"مرحباً {name} 👋"]
    if sent_items:
        lines.append("✈️ انبعتلك:")
        lines += _fmt_items(sent_items, "sent_qty")
    if received_items:
        lines.append("✅ جاهز عنّا بمكتب اطلبلي:")
        lines += _fmt_items(received_items, "received_qty")
    if coming_items:
        lines.append("⏳ لسا بالطريق إلنا:")
        lines += _fmt_items(coming_items, "missing_qty")
    lines.append("فور ما تكتمل باقي القطع بنجهّز طردك ونبلّغك 🙏")
    lines.append(f"شكراً لصبرك — {BRAND} 🙏")
    return "\n".join(lines)


def _review_message(name, facebook_url=None, coupon_usd=DEFAULT_COUPON_USD):
    """Warm ask for a review: thanks + how-was-it + $coupon-if-happy-and-review."""
    name = name or "عميلنا العزيز"
    lines = [f"مرحباً {name} 👋",
             "نتمنّى إنك استلمت طلبك من اطلبلي وكل شي وصلك تمام 🧡",
             "",
             "بنحب ناخد رأيك بصراحة:",
             "كيف كانت تجربتك معنا؟ وفي إشي نقدر نحسّنه؟",
             "",
             f"وكشكر منّا 🎁 إذا تجربتك كانت حلوة، إلك خصم {coupon_usd}$ على طلبك القادم"]
    if facebook_url:
        lines.append("لما تترك لنا تقييم على صفحتنا على فيسبوك 👇")
        lines.append(facebook_url)
    else:
        lines.append("لما تترك لنا تقييم على صفحتنا على فيسبوك 🙏")
    lines += ["", f"رأيك بيفرق كتير معنا 🙏 — فريق {BRAND}"]
    return "\n".join(lines)


def _review_links(ph, text):
    """(url_970, url_972) wa.me links for `text`, both on the same 9-digit core so
    a Palestinian number is reachable on either country code. (None, None) if no
    usable phone — the caller falls back to a copy-only button."""
    e164 = ph.get("e164") if ph else None
    core = normalize.phone_core(e164 or (ph.get("wa") if ph else "") or "")
    if not core:
        return None, None
    enc = _urlquote(text)
    return (f"https://wa.me/970{core}?text={enc}",
            f"https://wa.me/972{core}?text={enc}")


def _make_review_card(key, entry, pkgmeta, rate, include_money,
                      facebook_url=None, coupon_usd=DEFAULT_COUPON_USD):
    """One testimonial card for a person: name + phone + the +970/+972 WhatsApp
    links carrying the prefilled review message. Copy-only when no phone.

    Also carries the SAME detail the prep cards do — orders with items (images),
    the packages that shipped (GWD + OTL numbers), totals and the delivery
    location — so a DONE customer keeps their full card instead of collapsing
    to a bare name+phone row."""
    group = entry["group"]
    first = group[0]
    name = (first.get("customer", {}).get("name") or "").strip()
    ph = store.primary_phone(first)
    last_at = max((o.get("updated_at") or o.get("order_id") or "") for o in group)
    text = _review_message(name, facebook_url, coupon_usd)
    url_970, url_972 = _review_links(ph, text)
    city, address = _person_location(group)
    usd_r, dep_r = round(entry["usd"], 2), round(entry["dep"], 2)
    totals = {
        "usd": usd_r,
        "ils": round(usd_r * rate),
        "deposit_usd": dep_r,
        "remaining_usd": round(usd_r - dep_r, 2),
        "remaining_ils": round(round(usd_r - dep_r, 2) * rate),
        "unpriced": entry["unpriced"],
    }
    card_orders = entry["orders"]
    n_items = sum(it["qty"] for co in card_orders for it in (co["items"] or []))
    if not include_money:             # same blackout the prep cards apply
        totals = {k: None for k in totals}
        for co in card_orders:
            co["amount_to_collect_usd"] = None
            co["deposit_usd"] = None
    return {
        "key": key,
        "name": name,
        "phone": ph.get("e164") if ph else None,
        "city": city,
        "address": address,
        "n_orders": len(group),
        "n_items": n_items,
        "orders": card_orders,
        "packages": _pkgs_for({o["order_id"] for o in group}, pkgmeta),
        "totals": totals,
        "last_at": last_at,
        "wa_review_text": text,
        "wa_url_970": url_970,
        "wa_url_972": url_972,
    }


def build(orders, pdb, rate=DEFAULT_RATE, include_money=True,
          asked=None, facebook_url=None, coupon_usd=DEFAULT_COUPON_USD):
    """Group active orders by person and split into ready / waiting cards."""
    recv, sent, exc, meta, pkgmeta = _tally(pdb)

    groups = {}
    for o in orders or []:
        if o.get("status") not in ACTIVE_STATUSES:
            continue
        groups.setdefault(_person_key(o), []).append(o)

    ready, waiting = [], []
    dispatched = {}                       # key -> group: every live piece already sent
    for key, group in groups.items():
        first = group[0]
        card_orders = []
        n_items = n_received = n_sent = n_missing = 0
        usd = dep = 0.0
        unpriced = False
        received_items, sent_items, coming_items = [], [], []
        for o in sorted(group, key=lambda x: x.get("order_id") or ""):
            items = _annotate_items(o, recv, sent, exc, meta)
            for it in items:
                n_items += it["qty"]
                n_received += it["received_qty"]
                n_sent += it["sent_qty"]
                n_missing += it["missing_qty"]
                if it["received_qty"]:
                    received_items.append(it)
                if it["sent_qty"]:
                    sent_items.append(it)
                if it["missing_qty"]:
                    coming_items.append(it)
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
        if n_received == 0 and n_sent == 0:
            continue                  # nothing arrived and nothing sent — off-radar
        if n_received == 0 and n_missing == 0:
            # Every live piece already went out → testimonial card. Keep the
            # computed detail (annotated orders + money) so the review card can
            # show what the person got — it used to collapse to name+phone only.
            dispatched[key] = {"group": group, "orders": card_orders,
                               "usd": usd, "dep": dep, "unpriced": unpriced}
            continue
        # else: still has pieces to pack (received) OR is partially sent with more
        # on the way (n_received==0, n_sent>0, n_missing>0) — keep on the board.

        # The PO packages holding this customer's pieces — each carries the
        # owner-set otlobly_status the card lets you change (recieved/sent/…).
        order_ids = {o["order_id"] for o in group}
        card_pkgs = _pkgs_for(order_ids, pkgmeta)

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
        city, address = _person_location(group)
        is_ready = n_missing == 0
        wa_text = (_ready_message(name, totals, include_money) if is_ready
                   else _waiting_message(name, sent_items, received_items, coming_items))
        wa = ph.get("wa") if ph else None
        # Every prep card also carries the review message + 970/972 links, so the
        # owner can ask for a testimonial straight from any customer card — not
        # only from the 📸 fully-dispatched section.
        review_text = _review_message(name, facebook_url, coupon_usd)
        rev_970, rev_972 = _review_links(ph, review_text)
        card = {
            "key": key,
            "name": name,
            "phone": ph.get("e164") if ph else None,
            "wa": wa,
            "city": city,
            "address": address,
            "orders": card_orders,
            "packages": card_pkgs,
            "totals": totals,
            "n_items": n_items,
            "n_received": n_received,
            "n_sent": n_sent,
            "n_missing": n_missing,
            "wa_text": wa_text,
            "wa_url": f"https://wa.me/{wa}?text={_urlquote(wa_text)}" if wa else None,
            "wa_review_text": review_text,
            "wa_review_970": rev_970,
            "wa_review_972": rev_972,
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

    # Testimonial list: customers whose order has fully reached them — either an
    # active order with every piece dispatched (the owner's package-status
    # workflow) OR an order marked COLLECTED. One card per person, deduped, minus
    # anyone already asked (review_asked).
    asked = asked or set()
    review_entries = dict(dispatched)
    for o in orders or []:
        if o.get("status") != COLLECTED_STATUS:
            continue
        e = review_entries.setdefault(_person_key(o), {
            "group": [], "orders": [], "usd": 0.0, "dep": 0.0, "unpriced": False})
        e["group"] = e["group"] + [o]     # new list — never mutate the groups dict
        amt = o.get("amount_to_collect_usd")
        if amt is None:
            e["unpriced"] = True
        else:
            e["usd"] += float(amt)
        e["dep"] += float(o.get("deposit_usd") or 0)
        e["orders"] = e["orders"] + [{
            "order_id": o["order_id"],
            "status": o.get("status"),
            "amount_to_collect_usd": amt,
            "deposit_usd": round(float(o.get("deposit_usd") or 0), 2),
            "items": _plain_items(o, meta),
        }]
    reviews = [_make_review_card(k, e, pkgmeta, rate, include_money,
                                 facebook_url, coupon_usd)
               for k, e in review_entries.items() if k not in asked]
    reviews.sort(key=lambda c: c["last_at"], reverse=True)   # most-recent first
    return {
        "rate": rate,
        "counts": {"ready": len(ready), "waiting": len(waiting),
                   "reviews": len(reviews)},
        "ready": ready,
        "waiting": waiting,
        "reviews": reviews,
    }


if __name__ == "__main__":
    import purchases
    rep = build(store.load().get("orders", []), purchases.load(),
                facebook_url="https://facebook.com/otlobly")
    for bucket in ("ready", "waiting"):
        print(f"\n===== {bucket.upper()} ({rep['counts'][bucket]}) =====")
        for c in rep[bucket]:
            print(f"  {c['name'] or c['key']}: {c['n_received']}/{c['n_items']} received,"
                  f" {c['n_missing']} missing, total {c['totals']['usd']}$")
    print(f"\n===== REVIEWS ({rep['counts']['reviews']}) =====")
    for c in rep["reviews"]:
        print(f"  {c['name'] or c['key']} · {c['n_orders']} order(s) · {c['phone']}")
        print(f"    970: {c['wa_url_970']}")
