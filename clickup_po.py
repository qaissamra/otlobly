#!/usr/bin/env python3
"""
Push an Amazon Purchase Order to ClickUp, mirroring the "Otloble AMAZON / Orders"
structure: a parent **Order # <amazon_order_number>** task + one **subtask per
item** (named by product title, tagged with the item's customer, box, and the
package's GWD tracking #).

  python3 clickup_po.py PO-0001 --dry-run    # preview, write nothing
  python3 clickup_po.py PO-0001              # create the tasks in ClickUp

Reuses clickup.py's HTTP layer + CLICKUP_API_TOKEN.
"""

import argparse
import sys

import cfg
import purchases
from clickup import _http, CLICKUP_API, TOKEN


def _po_cfg(config):
    c = cfg.get(config, "clickup_po", {}) or {}
    return c.get("list_id"), (c.get("fields", {}) or {}), c


def box_options(list_id, box_field_id):
    """Map box dropdown option names (B19…) → option ids."""
    st, body = _http(f"{CLICKUP_API}/list/{list_id}/field")
    if st != 200:
        return {}
    for f in body.get("fields", []):
        if f.get("id") == box_field_id:
            return {o["name"]: o["id"]
                    for o in f.get("type_config", {}).get("options", [])}
    return {}


def _set(task_id, field_id, value):
    if field_id and value not in (None, ""):
        _http(f"{CLICKUP_API}/task/{task_id}/field/{field_id}",
              method="POST", body={"value": value})


def plan(po, fields):
    """Human-readable plan of what would be created."""
    out = [f"PARENT  «Order # {po['amazon_order_number']}»  "
           f"(customer={po.get('ship_to') or '—'}, box={po.get('profile_box') or '—'}, "
           f"USD={po.get('total_usd')}, AED={po.get('total_aed')})"]
    for pkg in po.get("packages", []):
        for it in pkg.get("items", []):
            out.append(f"  SUBTASK «{(it.get('title') or it.get('asin') or '')[:48]}»  "
                       f"customer={it.get('customer_name') or '⚠ UNMATCHED'}  "
                       f"track={pkg.get('tracking_number') or '—'}")
    return out


def push(po, config=None, dry_run=False):
    config = config or cfg.load()
    list_id, fields, c = _po_cfg(config)
    if not list_id or str(list_id).startswith("PASTE"):
        return {"ok": False, "error": "Set clickup_po.list_id in config.json"}

    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan(po, fields)}
    if not TOKEN:
        return {"ok": False, "error": "Set CLICKUP_API_TOKEN to push."}

    boxes = box_options(list_id, fields.get("box")) if fields.get("box") else {}
    pstatus = c.get("parent_status")
    istatus = c.get("item_status")

    # Parent "Order #" task
    body = {"name": f"Order # {po['amazon_order_number']}"}
    if pstatus:
        body["status"] = pstatus
    st, res = _http(f"{CLICKUP_API}/list/{list_id}/task", method="POST", body=body)
    if st != 200:
        return {"ok": False, "error": f"parent create failed: {res}"}
    parent_id = res["id"]
    _set(parent_id, fields.get("customer_name"), po.get("ship_to"))
    _set(parent_id, fields.get("total_amount_usd"), po.get("total_usd"))
    _set(parent_id, fields.get("total_amount_aed"), po.get("total_aed"))
    if po.get("profile_box") in boxes:
        _set(parent_id, fields.get("box"), boxes[po["profile_box"]])

    # Item subtasks
    n = 0
    for pkg in po.get("packages", []):
        for it in pkg.get("items", []):
            body = {"name": it.get("title") or it.get("asin") or "item",
                    "parent": parent_id}
            if istatus:
                body["status"] = istatus
            st, sub = _http(f"{CLICKUP_API}/list/{list_id}/task", method="POST", body=body)
            if st != 200:
                print(f"  XX subtask failed: {sub}", file=sys.stderr)
                continue
            _set(sub["id"], fields.get("customer_name"), it.get("customer_name"))
            _set(sub["id"], fields.get("tracking_number"), pkg.get("tracking_number"))
            box = it.get("profile_box") or po.get("profile_box")
            if box in boxes:
                _set(sub["id"], fields.get("box"), boxes[box])
            n += 1

    po["clickup_task_id"] = parent_id
    return {"ok": True, "parent_id": parent_id, "subtasks": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("po_id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = purchases.load()
    po = purchases.find(db, args.po_id)
    if not po:
        sys.exit(f"No such PO: {args.po_id}")
    res = push(po, dry_run=args.dry_run)
    if res.get("plan"):
        print("\n".join(res["plan"]))
        print("\n(DRY RUN — nothing written)")
    elif res.get("ok"):
        purchases.save(db)
        print(f"Pushed {args.po_id} → ClickUp parent {res['parent_id']} "
              f"+ {res['subtasks']} subtask(s).")
    else:
        print("Error:", res.get("error"))


if __name__ == "__main__":
    main()
