#!/usr/bin/env python3
"""
Pull existing Amazon orders FROM ClickUp (Otloble AMAZON → Orders list) INTO the
tool's Purchases page — the reverse of clickup_po.py. Each ClickUp parent
"Order # …" task + its item subtasks becomes a purchase order.

ClickUp doesn't store ASIN or the package grouping, but each item subtask carries
a CUSTOMER NAME (kept as a manual match) and a GWD Tracking # — so we rebuild
packages by grouping items that share a tracking number. Order #s already in the
tool are skipped (your local edits win); only new ones are imported.

  python3 clickup_import.py --dry-run     # show what would import
  python3 clickup_import.py               # import new orders into purchases.json
"""

import argparse
import sys

import cfg
import clickup_cost
import purchases


def _fval(task, fid):
    """Resolve a custom-field value (handles dropdowns → option name)."""
    for cf in task.get("custom_fields", []):
        if cf.get("id") != fid:
            continue
        v = cf.get("value")
        if cf.get("type") == "drop_down" and v is not None:
            try:
                return cf.get("type_config", {}).get("options", [])[v]["name"]
            except (IndexError, KeyError, TypeError):
                return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[0].get("name")
        return v
    return None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def build_pos(tasks, fields):
    """Group parents + their item subtasks → list of PO-input dicts (for new_po)."""
    parents = [t for t in tasks if not t.get("parent")]
    kids = {}
    for t in tasks:
        if t.get("parent"):
            kids.setdefault(t["parent"], []).append(t)

    pos = []
    for p in parents:
        name = (p.get("name") or "").replace("Order #", "").replace("Order", "").strip()
        # Rebuild packages from each item's tracking #: same GWD = same shipment.
        groups = {}
        order = []
        for k in kids.get(p["id"], []):
            trk = (_fval(k, fields.get("tracking_number")) or "").strip() or "_none"
            if trk not in groups:
                groups[trk] = []
                order.append(trk)
            groups[trk].append({
                "title": (k.get("name") or "").strip(),
                "asin": None,                              # not stored in ClickUp
                "customer_name": _fval(k, fields.get("customer_name")),
                "status": "ORDERED",
            })
        packages = []
        for i, trk in enumerate(order, 1):
            packages.append({"package_no": i, "arrival": "",
                             "tracking_number": None if trk == "_none" else trk,
                             "items": groups[trk]})
        pos.append({
            "amazon_order_number": name,
            "ship_to": _fval(p, fields.get("customer_name")) or "",
            "profile_box": _fval(p, fields.get("box")),
            "total_usd": _num(_fval(p, fields.get("total_amount_usd"))),
            "total_aed": _num(_fval(p, fields.get("total_amount_aed"))),
            "clickup_task_id": p["id"],
            "packages": packages,
        })
    return pos


def sync(config=None, dry_run=False):
    config = config or cfg.load()
    fields = cfg.get(config, "clickup_po.fields", {})
    list_id = cfg.get(config, "clickup_po.list_id")
    if not list_id:
        return {"error": "Set clickup_po.list_id in config.json."}
    tasks = clickup_cost.fetch_tasks(list_id)
    incoming = build_pos(tasks, fields)

    db = purchases.load()
    have = {(p.get("amazon_order_number") or "").strip()
            for p in db["purchase_orders"]}
    imported, skipped = [], []
    for src in incoming:
        ano = (src["amazon_order_number"] or "").strip()
        if ano in have:
            skipped.append(ano)
            continue
        if not dry_run:
            po = purchases.new_po(
                db, amazon_order_number=ano, ship_to=src["ship_to"],
                profile_box=src["profile_box"], total_usd=src["total_usd"],
                total_aed=src["total_aed"], packages=src["packages"])
            po["clickup_task_id"] = src["clickup_task_id"]
            purchases.upsert(db, po)
        imported.append({"order": ano, "box": src["profile_box"],
                         "usd": src["total_usd"], "packages": len(src["packages"]),
                         "items": sum(len(pk["items"]) for pk in src["packages"])})
    if not dry_run and imported:
        purchases.save(db)
    return {"imported": imported, "skipped": skipped, "seen": len(incoming)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = sync(dry_run=args.dry_run)
    if res.get("error"):
        sys.exit(res["error"])
    for i in res["imported"]:
        print(f"  + {i['order']:24} box {i['box'] or '—':4} ${i['usd'] or 0:>8.2f}  "
              f"{i['packages']} pkg · {i['items']} items")
    print(f"\n{len(res['imported'])} imported · {len(res['skipped'])} skipped "
          f"(already in tool: {', '.join(res['skipped']) or '—'})"
          + ("   (DRY RUN)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
