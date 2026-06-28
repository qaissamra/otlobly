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
from paths import data_path

STORE_FILE = data_path("purchases.json")
IMAGE_DIR = data_path("po_images")
ITEM_STATUSES = ["ORDERED", "SHIPPED", "ARRIVED", "DELIVERED", "RETURNED", "CANCELLED"]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load():
    if STORE_FILE.exists():
        return json.loads(STORE_FILE.read_text())
    return {"purchase_orders": [], "seq": 0}


def save(db):
    STORE_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


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


def apply_to_orders(po, orders, buffer_days=8):
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
            if o.get("status") in ("REQUESTED", "QUOTED", "PAID"):
                ch["status"] = "ORDERED"
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
    """Fill each item's customer_order_id / customer_name / matched by ASIN."""
    idx = asin_index(orders)
    for pkg in po.get("packages", []):
        for it in pkg.get("items", []):
            if it.get("customer_name") and it.get("matched"):
                continue                       # keep a manual assignment
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
        "status": raw.get("status") or "ORDERED",
        "tracking_number": (raw.get("tracking_number") or "").strip() or None,
        "tracking_carrier": raw.get("tracking_carrier") or None,
        "notes": (raw.get("notes") or "").strip(),
        "customer_order_id": raw.get("customer_order_id"),
        "customer_name": raw.get("customer_name"),
        "matched": bool(raw.get("customer_name")),
        "ambiguous": bool(raw.get("ambiguous")),
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
        "clickup_task_id": (existing or {}).get("clickup_task_id"),
        "screenshot": (existing or {}).get("screenshot"),
        "created_at": (existing or {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
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
    po = find(db, po_id)
    if po:
        po["screenshot"] = filename
        po["updated_at"] = now_iso()
    return po


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def all_items(po):
    return [it for pkg in po.get("packages", []) for it in pkg.get("items", [])]


def summary(po):
    items = all_items(po)
    return {
        **po,
        "n_packages": len(po.get("packages", [])),
        "n_items": len(items),
        "n_matched": sum(1 for it in items if it.get("matched")),
        "n_unmatched": sum(1 for it in items if not it.get("matched")),
    }


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
