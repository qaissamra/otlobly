#!/usr/bin/env python3
"""
Amazon Purchase Orders — the SUPPLY side. One Amazon checkout (Order # 113-…)
bundles items for several customers, split into PACKAGES by arrival date (each
package = one GAASH shipment with one GWD tracking #). Each item is auto-matched
to the customer order that requested it, by ASIN.

  python3 purchases.py                 # list POs
  python3 purchases.py --rematch       # re-run ASIN→customer matching on all POs
"""

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import normalize
import store
from db import current_business
from paths import data_path, write_json_atomic

STORE_FILE = data_path("purchases.json")       # business #1 (Otlobly) — unchanged
IMAGE_DIR = data_path("po_images")
# An item lives inside a package, so it INHERITS the package's status; a per-item
# status exists only for exceptions (a product can't be somewhere its box isn't).
# "" / missing = inherit. ITEM_STATUSES keeps its name for the /api/purchases
# contract — it's the list the UI offers in the status picker.
ITEM_EXCEPTIONS = ["CANCELLED", "REFUNDED", "OUT_OF_STOCK", "RETURNED"]
ITEM_STATUSES = ITEM_EXCEPTIONS


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _store_file():
    """Per-tenant PO store: Otlobly keeps purchases.json; each broker gets its own
    purchases_b<id>.json, so a broker's Purchases page never sees another tenant's."""
    bid = current_business()
    return STORE_FILE if bid == 1 else data_path(f"purchases_b{bid}.json")


def load():
    f = _store_file()
    if f.exists():
        return json.loads(f.read_text())
    return {"purchase_orders": [], "seq": 0}


def save(db):
    write_json_atomic(_store_file(), db)       # crash-safe: temp + atomic rename


# --------------------------------------------------------------------------- #
# ASIN → customer matching (against the customer-order store)
# --------------------------------------------------------------------------- #
def asin_index(orders):
    """Map each ASIN a customer requested → [{order_id, customer_name}].
    A list because two customers can want the same product."""
    idx = {}
    for o in orders:
        for it in o.get("items", []):
            a = (it.get("asin") or "").upper()
            if a:
                idx.setdefault(a, []).append(
                    {"customer_order_id": o["order_id"],
                     "customer_name": o["customer"]["name"]})
    return idx


def match_item(asin, idx):
    """Return (customer_order_id, customer_name, ambiguous) for an ASIN."""
    hits = idx.get((asin or "").upper(), [])
    if not hits:
        return None, None, False
    return hits[0]["customer_order_id"], hits[0]["customer_name"], len(hits) > 1


def _arrival_plus(arrival, buffer_days):
    """Parse a PO package arrival date (several formats) and add buffer_days → ISO date."""
    from datetime import datetime, timedelta
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return (datetime.strptime(arrival, fmt) + timedelta(days=buffer_days)).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def apply_to_orders(po, orders, buffer_days=10):
    """The supply→demand connection: when a PO is saved, every PO item already matched to
    a customer order (by ASIN) flips that order to **ORDERED** and inherits the Amazon
    order #, box, the package arrival date, and the customer ETA (arrival + buffer).
    Mutates `orders` in place; returns [(order_id, changes), …] for logging. Idempotent."""
    by_id = {o["order_id"]: o for o in orders}
    ano = (po.get("amazon_order_number") or "").strip()
    box = po.get("profile_box")
    changed = []
    for pkg in po.get("packages", []):
        arrival = (pkg.get("arrival") or "").strip()
        eta = _arrival_plus(arrival, buffer_days) if arrival else None
        for it in pkg.get("items", []):
            o = by_id.get(it.get("customer_order_id"))
            if not o:
                continue
            ch = {}
            if o.get("status") in ("REQUESTED", "QUOTED", "PAID", "IN_CART"):
                ch["status"] = "ORDERED"
                if o.get("cart_prev_status"):
                    ch["cart_prev_status"] = None
            if ano and o.get("amazon_order_number") != ano:
                ch["amazon_order_number"] = ano
            if ano and not (o.get("batch") or "").strip():
                ch["batch"] = ano
            if box and o.get("profile_box") != box:
                ch["profile_box"] = box
            if arrival and o.get("amazon_arrival") != arrival:
                ch["amazon_arrival"] = arrival
            if eta and o.get("est_delivery_customer") != eta:
                ch["est_delivery_customer"] = eta
            if ch:
                o.update(ch)
                o["updated_at"] = now_iso()
                changed.append((o["order_id"], ch))
    return changed


def attach_matches(po, orders):
    """Fill each item's customer_order_id / customer_name / matched by ASIN.
    Manual customer picks are kept — but they STILL get a customer_order_id
    (name → their pending order, else their newest unshipped ORDERED one),
    otherwise apply_to_orders can never flip a manually-assigned item's order
    to ORDERED / sync its batch, box and dates."""
    idx = asin_index(orders)
    name_idx, ordered_idx = {}, {}             # person_key → newest pending / ORDERED order
    for o in orders:
        st = o.get("status")
        bucket = (name_idx if st in ("REQUESTED", "QUOTED", "PAID", "IN_CART")
                  else ordered_idx if st == "ORDERED" else None)
        if bucket is None:
            continue
        nm = normalize.person_key(o.get("customer", {}).get("name") or "")
        if nm:
            prev = bucket.get(nm)
            if not prev or (o.get("created_at") or "") > (prev.get("created_at") or ""):
                bucket[nm] = o
    for pkg in po.get("packages", []):
        for it in pkg.get("items", []):
            if it.get("customer_name") and it.get("matched"):
                if not it.get("customer_order_id"):           # manual pick → resolve the id
                    nm = normalize.person_key(it.get("customer_name") or "")
                    # pending wins; an ORDERED order is only a fallback rescue
                    m = name_idx.get(nm) or ordered_idx.get(nm)
                    if m:
                        it["customer_order_id"] = m["order_id"]
                    else:                                     # same product AND same name
                        cid, cname, _amb = match_item(it.get("asin"), idx)
                        if cid and normalize.person_key(cname or "") == nm:
                            it["customer_order_id"] = cid
                continue                       # keep the manual assignment itself
            cid, cname, amb = match_item(it.get("asin"), idx)
            it["customer_order_id"] = cid
            it["customer_name"] = cname
            it["matched"] = bool(cid)
            it["ambiguous"] = amb
    return po


# --------------------------------------------------------------------------- #
# Build / persist
# --------------------------------------------------------------------------- #
def _norm_item(raw):
    """Accept {title, asin|link, qty, image, status, notes, customer_name?} → item."""
    asin = raw.get("asin") or normalize.extract_asin(raw.get("link", "") or "")
    asin = (asin or "").upper() or None
    return {
        "item_id": raw.get("item_id") or uuid.uuid4().hex[:8],
        "title": (raw.get("title") or "").strip(),
        "asin": asin,
        "clean_url": normalize.clean_amazon_url(raw.get("link") or
                                                (f"https://www.amazon.com/dp/{asin}" if asin else "")),
        "image": raw.get("image") or None,
        "qty": int(raw.get("qty") or 1),
        # exception-only: legacy stage values (ORDERED/SHIPPED/…) normalize to
        # "" = inherit the package status; nothing downstream ever read them.
        "status": raw.get("status") if raw.get("status") in ITEM_EXCEPTIONS else "",
        "tracking_number": (raw.get("tracking_number") or "").strip() or None,
        "tracking_carrier": raw.get("tracking_carrier") or None,
        "notes": (raw.get("notes") or "").strip(),
        "customer_order_id": raw.get("customer_order_id"),
        "customer_name": raw.get("customer_name"),
        "matched": bool(raw.get("customer_name")),
        "ambiguous": bool(raw.get("ambiguous")),
        # Estimated Amazon item COST (raw price, not marked up). Filled on demand by
        # /api/purchase/estimate_cost; src = past (a prior order/lookup) | cache | serp.
        "est_cost_usd": raw.get("est_cost_usd"),
        "est_cost_src": raw.get("est_cost_src"),
        "est_cost_at": raw.get("est_cost_at"),
    }


def new_po(db, *, amazon_order_number, ship_to="", profile_box=None,
           order_placed="", total_aed=None, total_usd=None, packages=None,
           status="PLACED"):
    db["seq"] = db.get("seq", 0) + 1
    pkgs = []
    for i, p in enumerate(packages or [], 1):
        pkgs.append({
            "package_no": p.get("package_no") or i,
            "arrival": (p.get("arrival") or "").strip(),
            "tracking_number": (p.get("tracking_number") or "").strip() or None,
            "items": [_norm_item(it) for it in p.get("items", [])],
        })
    return {
        "po_id": f"PO-{db['seq']:04d}",
        "amazon_order_number": (amazon_order_number or "").strip(),
        "ship_to": (ship_to or "").strip(),
        "profile_box": profile_box,
        "order_placed": (order_placed or "").strip(),
        "total_aed": float(total_aed) if total_aed not in (None, "") else None,
        "total_usd": float(total_usd) if total_usd not in (None, "") else None,
        "status": status,
        "clickup_task_id": None,
        "packages": pkgs,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def upsert(db, po):
    """Insert, or replace an existing PO with the same amazon_order_number."""
    key = po["amazon_order_number"]
    for i, ex in enumerate(db["purchase_orders"]):
        if key and ex.get("amazon_order_number") == key:
            po["po_id"] = ex["po_id"]
            po["created_at"] = ex.get("created_at", po["created_at"])
            if ex.get("clickup_task_id") and not po.get("clickup_task_id"):
                po["clickup_task_id"] = ex["clickup_task_id"]
            po["updated_at"] = now_iso()
            db["purchase_orders"][i] = po
            return po, "updated"
    db["purchase_orders"].append(po)
    return po, "created"


def find(db, po_id):
    return next((p for p in db["purchase_orders"] if p["po_id"] == po_id), None)


def _all_customer_tracking(db):
    """Every OTL customer-tracking number currently in use (for uniqueness)."""
    s = set()
    for po in db.get("purchase_orders", []):
        for pk in po.get("packages", []):
            ct = (pk.get("customer_tracking") or "").strip().upper()
            if ct:
                s.add(ct)
    return s


def gen_customer_tracking(db):
    """Mint a fresh, unique customer-facing tracking number 'OTL' + 6 digits
    (distinct from the OTL-#### order ids — no dash)."""
    used = _all_customer_tracking(db)
    for _ in range(50):
        n = "OTL" + "".join(random.choices("0123456789", k=6))
        if n not in used:
            return n
    return "OTL" + uuid.uuid4().hex[:6].upper()


def find_by_customer_tracking(db, otl):
    """Resolve an OTL number → (po, package), or (None, None)."""
    key = (otl or "").strip().upper()
    if not key:
        return None, None
    for po in db.get("purchase_orders", []):
        for pk in po.get("packages", []):
            if (pk.get("customer_tracking") or "").strip().upper() == key:
                return po, pk
    return None, None


def find_packages_for(db, names=None, order_ids=None):
    """Every (po, package) holding an item that belongs to one of these customer
    names or order ids — so a customer can find their shipments by name/phone→order."""
    names = {(n or "").strip().lower() for n in (names or []) if (n or "").strip()}
    oids = {(o or "").strip().upper() for o in (order_ids or []) if (o or "").strip()}
    out = []
    for po in db.get("purchase_orders", []):
        for pk in po.get("packages", []):
            for it in pk.get("items", []):
                nm = (it.get("customer_name") or "").strip().lower()
                oid = (it.get("customer_order_id") or "").strip().upper()
                if (nm and nm in names) or (oid and oid in oids):
                    out.append((po, pk))
                    break
    return out


def ensure_customer_tracking(db, po):
    """Auto-mint an OTL number for every package that has a GWD but none yet.
    Returns the number of packages newly numbered."""
    n = 0
    for pk in po.get("packages", []):
        if (pk.get("tracking_number") or "").strip() and not (pk.get("customer_tracking") or "").strip():
            pk["customer_tracking"] = gen_customer_tracking(db)
            n += 1
    return n


def _norm_packages(packages):
    out = []
    for i, p in enumerate(packages or [], 1):
        out.append({
            "package_no": p.get("package_no") or i,
            "arrival": (p.get("arrival") or "").strip(),
            "tracking_number": (p.get("tracking_number") or "").strip() or None,
            "customer_tracking": (p.get("customer_tracking") or "").strip().upper() or None,
            "tracking_status": p.get("tracking_status"),
            "gerizim_status": p.get("gerizim_status"),       # last-mile (postgerizim.com) status
            "tracking_checked": p.get("tracking_checked"),   # last live GAASH lookup (refresh TTL)
            "otlobly_status": p.get("otlobly_status"),       # owner-set status (ClickUp vocabulary)
            "gaash_docs_at": p.get("gaash_docs_at"),         # owner opened the GAASH doc-upload page
            "gaash_docs_types": p.get("gaash_docs_types"),   # which doc types were picked
            "gaash_cd_at": p.get("gaash_cd_at"),             # GAASH's latest "docs required" event
            "gaash_deadline": p.get("gaash_deadline"),       # GAASH link expiry — lost past this date
            "gaash_deadline_checked": p.get("gaash_deadline_checked"),  # own cadence (~2-week re-check)
            "docs_state": p.get("docs_state"),               # GAASH docs banner (action/info/…) — see tracking.docs_status
            "docs_checked": p.get("docs_checked"),           # last docs-banner check (~daily cadence)
            "due_date": (p.get("due_date") or "").strip() or None,  # owner-set package due date
            "rd_number": (p.get("rd_number") or "").strip() or None,  # owner-typed RD (refund) number
            # 📷 photos pasted onto THIS package (filenames in po_images/) —
            # admin-only; see save_full for why a missing key is preserved
            "pkg_images": [str(f) for f in (p.get("pkg_images") or []) if f],
            "alerts_sent": p.get("alerts_sent") or {},       # Telegram threshold stamps {key: iso}
            "track_notified": p.get("track_notified") or {},   # {order_id: iso} — customer-notify stamps
            "items": [_norm_item(it) for it in p.get("items", [])],
        })
    return out


def save_full(db, po_dict, orders):
    """Upsert a FULL PO dict (from the editable table). Updates the existing PO by
    po_id, preserving protected fields (created_at / clickup_task_id / screenshot);
    auto-matches only items whose customer is still empty (manual picks stick)."""
    existing = find(db, po_dict.get("po_id")) if po_dict.get("po_id") else None
    po = {
        "po_id": po_dict.get("po_id"),
        "amazon_order_number": (po_dict.get("amazon_order_number") or "").strip(),
        "ship_to": (po_dict.get("ship_to") or "").strip(),
        "profile_box": po_dict.get("profile_box") or None,
        "order_placed": (po_dict.get("order_placed") or "").strip(),
        "total_aed": _num(po_dict.get("total_aed")),
        "total_usd": _num(po_dict.get("total_usd")),
        "status": po_dict.get("status") or "PLACED",
        "packages": _norm_packages(po_dict.get("packages")),
        # ClickUp-style custom-field values {key: value} — posted whole; an
        # older client that omits the dict must not wipe saved values
        "custom": (po_dict.get("custom") if isinstance(po_dict.get("custom"), dict)
                   else (existing or {}).get("custom")) or {},
        "clickup_task_id": (existing or {}).get("clickup_task_id"),
        "screenshot": (existing or {}).get("screenshot"),
        "attachments": ((existing or {}).get("attachments")
                        or ([existing["screenshot"]]      # legacy: seed from the single screenshot
                            if (existing or {}).get("screenshot") else [])),
        "created_at": (existing or {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    # 📷 Package photos are written by their OWN endpoint, not by the board's
    # whole-PO save. A client whose copy predates a paste would post the package
    # without the key and wipe the photos — so a MISSING key keeps what's on
    # disk (an explicit list still wins, which is how a delete goes through).
    # Same protection as `custom` above, matched by package_no not by index.
    if existing:
        old_imgs = {str(pk.get("package_no")): (pk.get("pkg_images") or [])
                    for pk in existing.get("packages") or []}
        for i, pk in enumerate(po["packages"]):
            src = (po_dict.get("packages") or [])[i] if i < len(po_dict.get("packages") or []) else {}
            if not isinstance(src, dict) or "pkg_images" not in src:
                pk["pkg_images"] = list(old_imgs.get(str(pk.get("package_no")), []))
    attach_matches(po, orders)
    ensure_customer_tracking(db, po)   # auto-mint OTL numbers for newly-tracked packages
    if existing:
        db["purchase_orders"][db["purchase_orders"].index(existing)] = po
        return po, "updated"
    db["seq"] = db.get("seq", 0) + 1
    po["po_id"] = f"PO-{db['seq']:04d}"
    db["purchase_orders"].append(po)
    return po, "created"


def delete(db, po_id):
    before = len(db["purchase_orders"])
    db["purchase_orders"] = [p for p in db["purchase_orders"] if p["po_id"] != po_id]
    return len(db["purchase_orders"]) < before


def set_screenshot(db, po_id, filename):
    """Attach an image: keeps the legacy single `screenshot` pointer AND appends
    to the `attachments` gallery (the order-detail modal shows all of them)."""
    po = find(db, po_id)
    if po:
        po["screenshot"] = filename
        atts = po.setdefault("attachments", [])
        if filename and filename not in atts:
            atts.append(filename)
        po["updated_at"] = now_iso()
    return po


def remove_attachment(db, po_id, filename):
    po = find(db, po_id)
    if po:
        po["attachments"] = [f for f in (po.get("attachments") or []) if f != filename]
        if po.get("screenshot") == filename:
            po["screenshot"] = po["attachments"][-1] if po["attachments"] else None
        po["updated_at"] = now_iso()
    return po


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def all_items(po):
    return [it for pkg in po.get("packages", []) for it in pkg.get("items", [])]


def _redact_po_money(po):
    """Cost-free copy for roles without view_cost (the employee view): the paid
    Amazon totals and the per-item cost estimates are the business's COGS —
    fulfillment can pack and track without ever seeing them. money:False tells
    the UI to skip the money cells/footers entirely."""
    po = {**po, "total_usd": None, "total_aed": None, "money": False}
    po["packages"] = [{**pk, "items": [{**it, "est_cost_usd": None}
                                       for it in pk.get("items", [])]}
                      for pk in po.get("packages", [])]
    return po


def summary(po, include_money=True):
    items = all_items(po)
    out = {
        **po,
        "n_packages": len(po.get("packages", [])),
        "n_items": len(items),
        "n_matched": sum(1 for it in items if it.get("matched")),
        "n_unmatched": sum(1 for it in items if not it.get("matched")),
    }
    return out if include_money else _redact_po_money(out)


# --------------------------------------------------------------------------- #
# Live GAASH / Gerizim refresh — the body of /api/purchases/refresh_tracking.
# It lives here rather than in the route so the GAASH-mail check can reach it:
# one tracking number can sit on a Leluxe row AND a purchase package, and a
# check that only refreshed the board it was pressed on left the other stale.
# --------------------------------------------------------------------------- #
def refresh_tracking(only=None, force=False, batch=5):
    """Top up every package's GAASH + Gerizim status from the carriers.

    The hourly Mac sync can die silently (and did — month-old buckets shown as
    truth), so opening the page tops itself up: every package with a GWD number
    that isn't delivered and wasn't checked within the TTL gets one live lookup.
    Unique numbers are fetched once (a GWD can sit on several packages); a
    package is only rewritten on a successful fetch with real statuses.
    A package whose otlobly_status says the owner RECEIVED or SENT it is
    finished and is never checked again (alerts.stop_statuses — the same list
    Leluxe and the Telegram alerts read, one editor in Settings).

    `only` refreshes just that number; `force` bypasses the TTL and the
    received/sent rule (the per-package ⋯ 🔎 must always be able to ask), never
    the carrier-terminal skips. Each GWD is checked against BOTH carriers:
    GAASH (skipped once its own bucket is delivered) and Gerizim last-mile
    (until GERIZIM says delivered — GAASH "Delivered" only means handed to the
    last-mile shelf).

    Returns {updated, remaining, changes} — `changes` carries old→new per
    package, which is what the change log and the popup are built from."""
    import time
    from datetime import timedelta

    import activity
    import alerts              # local: alerts imports purchases (circular up top)
    import db
    import gerizim
    import tracking
    ttl_min = 30
    # Owner's rule: a parcel he has RECEIVED or already SENT is finished — the
    # carriers have nothing left to say about it. Same Settings-editable
    # vocabulary the Leluxe sweep and the Telegram alerts use, read off the
    # package's own otlobly_status — the field HE sets; carriers never write it.
    # Bulk only: `force` (the per-package ⋯ 🔎) still checks anything on purpose.
    stop = alerts.stop_statuses()
    stopped = {}
    only = tracking.clean_tracking(only or "") or None
    pdb = load()
    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=ttl_min)
    dl_cutoff = now - timedelta(days=14)          # the GAASH deadline is near-fixed: fetch
    docs_cutoff = now - timedelta(days=1)         # docs banner flips fast once docs land
    old_gaash_cut = (now - timedelta(days=30)).isoformat()  # once, re-check only every ~2 weeks
    dl_cap = 10                                   # bound the first-load deadline backfill
    docs_cap = 3                                  # docs page is SLOW (~10-25s) — keep the batch inside gunicorn's 120s
    # work[gwd] = list of (po, pk, need_track, need_dl, need_docs)
    work = {}
    for po in pdb["purchase_orders"]:
        for pk in po.get("packages") or []:
            tn = tracking.clean_tracking(pk.get("tracking_number") or "")
            if not tn or (only and tn != only):
                continue
            own = str(pk.get("otlobly_status") or "").strip().lower()
            if own in stop and not force:
                stopped[tn] = own         # received / sent — the owner has it
                continue
            gz = pk.get("gerizim_status") or {}
            if isinstance(gz, dict) and gz.get("bucket") == "delivered":
                continue                  # customer has the box — truly terminal
            ts = pk.get("tracking_status") or {}
            gaash_done = isinstance(ts, dict) and ts.get("bucket") == "delivered"
            if gaash_done and not gz and (ts.get("time") or "") < old_gaash_cut:
                continue                  # ancient GAASH-delivered parcel Gerizim never saw
            need_track = force
            if not need_track:
                try:
                    need_track = datetime.fromisoformat(pk.get("tracking_checked") or "") <= cutoff
                except (ValueError, TypeError):
                    need_track = True     # never checked / unparsable → refresh
            # The deadline scrape rides its OWN cadence, not the 30-min tracking TTL
            # (the Mac sync keeps tracking_checked fresh, which used to starve it).
            # `gz` is `... or {}`, so test truthiness — an empty {} means "no Gerizim".
            eligible = (ts.get("bucket") if isinstance(ts, dict) else None) not in ("cleared", "delivered") \
                and not gz
            need_dl = need_docs = False
            if eligible:
                try:
                    need_dl = force or not pk.get("gaash_deadline") \
                        or datetime.fromisoformat(pk.get("gaash_deadline_checked") or "") <= dl_cutoff
                except (ValueError, TypeError):
                    need_dl = True        # never scraped → fetch once
                try:
                    need_docs = force or \
                        datetime.fromisoformat(pk.get("docs_checked") or "") <= docs_cutoff
                except (ValueError, TypeError):
                    need_docs = True      # never checked → fetch once
            if need_track or need_dl or need_docs:
                work.setdefault(tn, []).append((po, pk, need_track, need_dl, need_docs))
    # Each GWD costs ~10s of live scraping; doing all ~13 in one request blows past
    # gunicorn's 120s timeout, which kills the request before the final save so
    # NOTHING persists. Process a bounded batch, save after each, and report how
    # many remain so the client re-calls until done.
    all_gwds = list(work.items())
    picked = all_gwds[:max(1, int(batch or 5))]
    remaining = len(all_gwds) - len(picked)
    updated, changes, dl_done, docs_done = 0, [], 0, 0
    if picked:
        session = None                    # scraped lazily: gerizim-only rounds skip it
        for i, (tn, rows) in enumerate(picked):
            if i:
                time.sleep(tracking.REQUEST_GAP)
            want_track = any(nt for _, _, nt, _, _ in rows)   # a tracking refresh is due
            want_dl = any(nd for _, _, _, nd, _ in rows) and dl_done < dl_cap
            want_docs = any(nc for _, _, _, _, nc in rows) and docs_done < docs_cap
            st, cd_at = None, None
            if want_track:
                if session is None:
                    try:
                        session = tracking.get_session()
                    except Exception as e:  # noqa - GAASH down: keep Gerizim going
                        session = e
                if not isinstance(session, Exception):
                    data = tracking.fetch_one(tn, *session)
                    st = tracking.latest_status(data)
                    # feed the shared last-known cache — the public tracking widget
                    # serves customers straight from it (cache-first)
                    tracking.cache_put_events(tn, tracking.events_from_raw(data))
                    # latest "docs required" event — GAASH re-emits CD while docs are
                    # missing, so a CD newer than the owner's upload stamp means the
                    # upload never registered (the row shows a red warning chip).
                    cd_at = max((s.get("StatusTime") or ""
                                 for s in (data or {}).get("Statuses") or []
                                 if (s.get("MappedStatusCode") or "").strip().upper() == "CD"
                                 or (s.get("StatusDescription") or "").strip().lower() == "required customer id"),
                                default=None) or None
            gz = gerizim.track(tn) if want_track else None
            gz_new = gz if isinstance(gz, dict) else None
            # GAASH's lost-forever deadline (doc-link expiry) — own cadence, fetched
            # once then re-checked every ~2 weeks; never re-hammered per page load.
            deadline, dl_stamp = None, None
            if want_dl:
                dl_done += 1
                dl_stamp = db.now_iso()   # stamp the attempt even if it returns None
                deadline = tracking.ops_deadline(tn)
            # GAASH docs banner (upload asked vs in-customs) — daily cadence, capped
            docs, docs_stamp = None, None
            if want_docs:
                docs_done += 1
                docs_stamp = db.now_iso()
                try:
                    docs = tracking.docs_status(tn)
                except Exception:  # noqa: BLE001 — status page down ≠ refresh broken
                    docs = None
            if not st and not gz_new and not deadline and not dl_stamp and not docs_stamp:
                continue                  # every lookup failed / nothing new → no writes
            stamp = db.now_iso()
            # Write into a FRESH load, not this request's snapshot: each GWD
            # costs ~10s of live scraping, so `pdb` is seconds-to-minutes old
            # by now, and saving it verbatim CLOBBERED any PO created or
            # edited meanwhile (watched live: a just-created order vanished
            # when the in-flight sweep saved over it). Only the tracking keys
            # below belong to this writer; everything else is someone else's.
            fresh = load()
            fmap = {(fpo.get("po_id"), fpk.get("package_no")): fpk
                    for fpo in fresh["purchase_orders"]
                    for fpk in fpo.get("packages") or []
                    if fpk.get("package_no") is not None}
            for po, pk, nt, nd, nc in rows:
                old = pk.get("tracking_status") if isinstance(pk.get("tracking_status"), dict) else None
                gz_old = pk.get("gerizim_status") if isinstance(pk.get("gerizim_status"), dict) else None
                # snapshot pk too: `changes`, the activity trail and later
                # batches' TTL checks all read from it
                fpk = fmap.get((po.get("po_id"), pk.get("package_no")))
                for t in (pk, fpk) if fpk is not None else (pk,):
                    if st:
                        t["tracking_status"] = st
                    if cd_at:
                        t["gaash_cd_at"] = cd_at
                    if gz_new:
                        t["gerizim_status"] = gz_new
                    if nd and dl_stamp:
                        t["gaash_deadline_checked"] = dl_stamp
                        if deadline:
                            t["gaash_deadline"] = deadline
                    if nc and docs_stamp:
                        t["docs_checked"] = docs_stamp
                        if docs:
                            t["docs_state"] = docs
                    if nt:
                        t["tracking_checked"] = stamp
                updated += 1
                cur = st or old
                changes.append({"po_id": po.get("po_id"), "package_no": pk.get("package_no"),
                                "tracking": tn,
                                "name": _pkg_label(po, pk),
                                "old": {"bucket": (old or {}).get("bucket"),
                                        "text": activity.gaash_text(old)},
                                "new": {"bucket": (cur or {}).get("bucket"),
                                        "text": activity.gaash_text(cur)},
                                "gz_old": {"bucket": (gz_old or {}).get("bucket"),
                                           "text": activity.gaash_text(gz_old)},
                                "gz_new": {"bucket": (gz_new or gz_old or {}).get("bucket"),
                                           "text": activity.gaash_text(gz_new or gz_old)}})
                # feed the per-PO Activity trail (readable text, the carrier as actor)
                header = ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number") else po.get("po_id", "")
                ot, ntx = activity.gaash_text(old), activity.gaash_text(st)
                if st and ntx and ot != ntx:
                    activity.log("set", "purchase", po.get("po_id"), header,
                                 field="tracking_status", old=ot or None, new=ntx,
                                 detail=f"Package {pk.get('package_no')}", user="GAASH")
                got, gnt = activity.gaash_text(gz_old), activity.gaash_text(gz_new)
                if gz_new and gnt and got != gnt:
                    activity.log("set", "purchase", po.get("po_id"), header,
                                 field="gerizim_status", old=got or None, new=gnt,
                                 detail=f"Package {pk.get('package_no')}", user="Gerizim")
            # Persist after EACH GWD so a timeout mid-batch never loses finished work.
            save(fresh)
    # the route serializes `db` back as the board state — return a fresh read
    # so concurrent creates/edits survive on the client too, not just on disk
    return {"updated": updated, "remaining": remaining, "changes": changes,
            # what the owner already has/sent, so the 🔎 popup can say so
            # instead of the misleading "no update"
            "stopped": [{"tracking": t, "status": s}
                        for t, s in sorted(stopped.items())],
            "db": load() if picked else pdb}


def store_docs_state(tn, docs):
    """Persist one parcel's GAASH docs banner onto every package carrying the
    GWD (a number can sit on several packages). Fresh load-merge-save — this
    writer owns only the two docs keys (see refresh_tracking's clobber note).
    No-op 0 when no package carries the number.

    docs=None means the lookup failed: stamp the attempt, keep the last known
    state (a hiccup must not erase a real 'upload asked')."""
    import tracking
    tn = tracking.clean_tracking(tn or "")
    if not tn:
        return 0
    stamp = (docs or {}).get("checked") or datetime.now().astimezone().isoformat()
    fresh = load()
    hit = 0
    for po in fresh["purchase_orders"]:
        for pk in po.get("packages") or []:
            if tracking.clean_tracking(pk.get("tracking_number") or "") != tn:
                continue
            if isinstance(docs, dict):
                pk["docs_state"] = docs
            pk["docs_checked"] = stamp
            hit += 1
    if hit:
        save(fresh)
    return hit


def _pkg_label(po, pk):
    """A human name for one package — the first item's product, else its number.
    The change log shows a product, not 'Package 2'."""
    for it in pk.get("items") or []:
        t = (it.get("title") or it.get("name") or "").strip()
        if t:
            return t
    return f"Package {pk.get('package_no') or '?'}"


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rematch", action="store_true",
                    help="Re-run ASIN→customer matching on every PO and save.")
    args = ap.parse_args()
    db = load()
    if args.rematch:
        orders = store.load()["orders"]
        for po in db["purchase_orders"]:
            attach_matches(po, orders)
        save(db)
        print(f"Re-matched {len(db['purchase_orders'])} PO(s).")
        return
    if not db["purchase_orders"]:
        print("No purchase orders yet.")
        return
    print(f"{'PO':9} {'AMAZON #':22} {'BOX':5} {'ITEMS':>5} {'MATCH':>6} {'USD':>9}")
    for po in db["purchase_orders"]:
        s = summary(po)
        print(f"{po['po_id']:9} {(po['amazon_order_number'] or '—')[:22]:22} "
              f"{(po['profile_box'] or '—'):5} {s['n_items']:>5} "
              f"{s['n_matched']}/{s['n_items']:<4} {(po['total_usd'] or 0):>9.2f}")


if __name__ == "__main__":
    main()
