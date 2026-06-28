#!/usr/bin/env python3
"""
Trash / Recycle Bin — soft-delete so nothing is ever lost, with Restore (back to
its original location) and Delete-permanently. Modeled on ClickUp Trash / HubSpot
recycle bin. Items are kept FOREVER until the user purges them (no auto-expiry).

A trashed record keeps the FULL removed dict (`data`) so restore is lossless, plus
an `origin` hint that says where it came from (e.g. which PO a package belonged to).

  kind ∈ purchase_order | po_package | po_item | order | customer

  python3 trash.py             # list the bin
  python3 trash.py --selftest   # offline round-trip check (temp files)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import data_path

STORE_FILE = data_path("trash.json")

KIND_LABELS = {
    "purchase_order": "Purchase order",
    "po_package": "Package",
    "po_item": "Product",
    "order": "Customer order",
    "customer": "Customer",
}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load():
    if STORE_FILE.exists():
        return json.loads(STORE_FILE.read_text())
    return {"trash": [], "seq": 0}


def save(db):
    STORE_FILE.write_text(json.dumps(db, indent=2, ensure_ascii=False))


def add(db, kind, label, data, origin=None, actor=None):
    """Move something into the bin. Returns the trash record."""
    db["seq"] = db.get("seq", 0) + 1
    rec = {
        "trash_id": f"T-{db['seq']:04d}",
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "label": (label or "").strip() or kind,
        "data": data,
        "origin": origin or {},
        "deleted_at": now_iso(),
        "deleted_by": (actor or {}).get("username") if isinstance(actor, dict) else (actor or "You"),
    }
    db["trash"].append(rec)
    return rec


def items(db):
    """Newest-first list of trashed records (without the heavy data blob)."""
    out = []
    for r in reversed(db.get("trash", [])):
        out.append({k: v for k, v in r.items() if k != "data"})
    return out


def find(db, trash_id):
    return next((r for r in db.get("trash", []) if r["trash_id"] == trash_id), None)


def purge(db, trash_id):
    before = len(db.get("trash", []))
    db["trash"] = [r for r in db.get("trash", []) if r["trash_id"] != trash_id]
    return len(db["trash"]) < before


def empty(db):
    n = len(db.get("trash", []))
    db["trash"] = []
    return n


# --------------------------------------------------------------------------- #
# Restore — re-insert into the source store, then drop from the bin.
# Each branch loads + saves its own store so callers stay simple.
# --------------------------------------------------------------------------- #
def restore(db, trash_id):
    rec = find(db, trash_id)
    if not rec:
        return {"ok": False, "error": "not found"}
    kind, data, origin = rec["kind"], rec["data"], rec.get("origin") or {}
    try:
        where = _RESTORERS[kind](data, origin)
    except KeyError:
        return {"ok": False, "error": f"don't know how to restore '{kind}'"}
    except Exception as e:  # noqa - surface a friendly message
        return {"ok": False, "error": str(e)}
    if not where.get("ok"):
        return {"ok": False, "error": where.get("error", "restore failed")}
    purge(db, trash_id)
    return {"ok": True, "kind": kind, "label": rec["label"], "where": where.get("where", "")}


def _restore_po(data, origin):
    import purchases
    pdb = purchases.load()
    # avoid a po_id collision (shouldn't happen — it was deleted — but be safe)
    if purchases.find(pdb, data.get("po_id")):
        pdb["seq"] = pdb.get("seq", 0) + 1
        data["po_id"] = f"PO-{pdb['seq']:04d}"
    pdb["purchase_orders"].append(data)
    purchases.save(pdb)
    return {"ok": True, "where": "Purchases"}


def _restore_package(data, origin):
    import purchases
    pdb = purchases.load()
    po = purchases.find(pdb, origin.get("po_id"))
    if not po:
        return {"ok": False, "error": "its purchase order no longer exists"}
    po.setdefault("packages", [])
    pos = max(0, min(len(po["packages"]), int(data.get("package_no", len(po["packages"]) + 1)) - 1))
    po["packages"].insert(pos, data)
    for i, pk in enumerate(po["packages"], 1):
        pk["package_no"] = i
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    return {"ok": True, "where": f"{po['po_id']}"}


def _restore_item(data, origin):
    import purchases
    pdb = purchases.load()
    po = purchases.find(pdb, origin.get("po_id"))
    if not po:
        return {"ok": False, "error": "its purchase order no longer exists"}
    pkg = next((p for p in po.get("packages", [])
                if p.get("package_no") == origin.get("package_no")), None)
    if not pkg:
        pkg = po.get("packages", [None])[0]
    if not pkg:
        return {"ok": False, "error": "its package no longer exists"}
    pkg.setdefault("items", []).append(data)
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    return {"ok": True, "where": f"{po['po_id']} · Package {pkg.get('package_no')}"}


def _restore_order(data, origin):
    import store
    odb = store.load()
    store.upsert(odb, data)
    store.save(odb)
    return {"ok": True, "where": "Orders"}


def _restore_customer(data, origin):
    import customers
    cdb = customers.load()
    customers.upsert(cdb, data)
    customers.save(cdb)
    return {"ok": True, "where": "Customers"}


_RESTORERS = {
    "purchase_order": _restore_po,
    "po_package": _restore_package,
    "po_item": _restore_item,
    "order": _restore_order,
    "customer": _restore_customer,
}


# --------------------------------------------------------------------------- #
def _selftest():
    import tempfile
    global STORE_FILE
    with tempfile.TemporaryDirectory() as d:
        STORE_FILE = Path(d) / "trash.json"
        db = load()
        rec = add(db, "po_item", "Cricut Maker 4",
                  {"item_id": "x1", "title": "Cricut Maker 4"},
                  origin={"po_id": "PO-0001", "package_no": 1}, actor="You")
        save(db)
        db = load()
        ok = (len(items(db)) == 1 and find(db, rec["trash_id"]) is not None
              and items(db)[0]["label"] == "Cricut Maker 4"
              and "data" not in items(db)[0])
        purge(db, rec["trash_id"])
        ok = ok and len(items(db)) == 0
        print("trash:", "OK" if ok else "XX")
        return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    db = load()
    rows = items(db)
    if not rows:
        print("Trash is empty.")
    for r in rows:
        print(f"{r['trash_id']:7} {r['kind_label']:14} {r['label'][:36]:36} "
              f"deleted {r['deleted_at'][:19]} by {r.get('deleted_by')}")
