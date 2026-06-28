#!/usr/bin/env python3
"""
Sync Otlobly orders -> ClickUp (one task per order). Reuses the same REST style
as gaash-clickup-sync. Money in USD.

  python3 clickup.py --discover     # list the list's fields + dropdown options
                                    # (paste the IDs into config.json)
  python3 clickup.py --dry-run      # show every create/update, write NOTHING
                                    # (works offline — no token needed)
  python3 clickup.py                # apply: create/update tasks, write back IDs

Env: CLICKUP_API_TOKEN   Config: config.json (clickup.list_id / fields / options)
"""

import argparse
import json
import os
import sys
from urllib import request, error

import cfg
import store

CLICKUP_API = "https://api.clickup.com/api/v2"
TOKEN = os.environ.get("CLICKUP_API_TOKEN", "")


# --------------------------------------------------------------------------- #
# HTTP (same tiny helper as the gaash sync)
# --------------------------------------------------------------------------- #
def _http(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": TOKEN, "Content-Type": "application/json"}
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except error.HTTPError as e:
        return e.code, {"_error": e.read().decode()}
    except error.URLError as e:
        return 0, {"_error": str(e)}


# --------------------------------------------------------------------------- #
# Discovery — print field + option IDs to fill config.json
# --------------------------------------------------------------------------- #
def discover(list_id):
    if not TOKEN:
        sys.exit("Set CLICKUP_API_TOKEN first.")
    status, body = _http(f"{CLICKUP_API}/list/{list_id}/field")
    if status != 200:
        sys.exit(f"Field fetch failed ({status}): {body}")
    print(f"Custom fields on list {list_id}:\n")
    for f in body.get("fields", []):
        print(f"  {f.get('name')!r}")
        print(f"     id:   {f.get('id')}")
        print(f"     type: {f.get('type')}")
        for o in f.get("type_config", {}).get("options", []) or []:
            print(f"       option {o.get('name')!r:30} -> {o.get('id')}")
        print()


# --------------------------------------------------------------------------- #
# Build the intended ClickUp write for one order (used by dry-run AND live)
# --------------------------------------------------------------------------- #
def _task_name(o):
    return f"{o['order_id']} — {o['customer']['name'] or 'بدون اسم'}"


def _description(o):
    ph = store.primary_phone(o)
    links = "\n".join(f"- {it.get('clean_url') or it.get('raw_url')}"
                      for it in o["items"])
    amt = o.get("amount_to_collect_usd")
    amt_line = f"{amt:.2f}$" if amt is not None else "— (unpriced)"
    notes = f"\n_{o['customer']['notes']}_" if o["customer"].get("notes") else ""
    return (
        f"**Customer:** {o['customer']['name']}\n"
        f"**Phone:** {ph['e164'] if ph else '—'}\n"
        f"**Address:** {o['customer']['address']}\n"
        f"**Amount to collect:** {amt_line}\n"
        f"**Batch:** {o.get('batch') or '—'}  ·  **Box:** {o.get('profile_box') or '—'}\n"
        f"**Status:** {o['status']}\n\n"
        f"**Items:**\n{links}\n{notes}"
    )


def _field_values(o, fields, status_options):
    """[(field_id, label, value)] for the custom fields we set."""
    ph = store.primary_phone(o)
    amt = o.get("amount_to_collect_usd")
    out = [
        (fields.get("customer"), "Customer", o["customer"]["name"]),
        (fields.get("phone"), "Phone", ph["e164"] if ph else ""),
        (fields.get("address"), "Address", o["customer"]["address"]),
        (fields.get("product_links"), "Product Links",
         "\n".join(it.get("clean_url") or it.get("raw_url") for it in o["items"])),
        (fields.get("amount_to_collect"), "Amount (USD)", amt),
        (fields.get("deposit"), "Deposit", o.get("deposit_usd") or 0),
        (fields.get("profile_box"), "Box", o.get("profile_box") or ""),
        (fields.get("order_status"), "Order Status",
         status_options.get(o["status"])),
    ]
    return [(fid, label, val) for fid, label, val in out if fid]


def _set_field(task_id, field_id, value):
    return _http(f"{CLICKUP_API}/task/{task_id}/field/{field_id}",
                 method="POST", body={"value": value})


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan the writes locally and print them; touch nothing.")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    config = cfg.load()
    list_id = cfg.get(config, "clickup.list_id")
    fields = cfg.get(config, "clickup.fields", {}) or {}
    status_options = cfg.get(config, "clickup.order_status_options", {}) or {}

    if args.discover:
        return discover(list_id)

    db = store.load()
    orders = db["orders"][:args.limit] if args.limit else db["orders"]
    placeholders = (not list_id or str(list_id).startswith("PASTE"))

    if args.dry_run:
        print(f"DRY RUN — {len(orders)} orders → ClickUp list {list_id}\n")
        for o in orders:
            action = "UPDATE" if o.get("clickup_task_id") else "CREATE"
            print(f"  [{action}] {_task_name(o)}")
            for fid, label, val in _field_values(o, fields, status_options):
                shown = "" if val in (None, "") else str(val).replace("\n", " ⏎ ")
                print(f"        {label:16}= {shown[:60]}")
        print(f"\n{len(orders)} task(s) planned. (DRY RUN — nothing written)")
        if placeholders:
            print("NOTE: config.json has placeholder IDs — run "
                  "`python3 clickup.py --discover` after creating the fields.")
        return

    if not TOKEN:
        sys.exit("Set CLICKUP_API_TOKEN to apply (or use --dry-run).")
    if placeholders:
        sys.exit("config.json still has placeholder IDs. Run --discover and fill it in.")

    created = updated = 0
    for o in orders:
        was_new = not o.get("clickup_task_id")
        if not was_new:
            tid = o["clickup_task_id"]
            _http(f"{CLICKUP_API}/task/{tid}", method="PUT",
                  body={"name": _task_name(o), "markdown_description": _description(o)})
            updated += 1
        else:
            st, body = _http(f"{CLICKUP_API}/list/{list_id}/task", method="POST",
                             body={"name": _task_name(o),
                                   "markdown_description": _description(o)})
            if st != 200:
                print(f"  XX create failed for {o['order_id']}: {body}", file=sys.stderr)
                continue
            tid = body["id"]
            o["clickup_task_id"] = tid
            created += 1
        for fid, label, val in _field_values(o, fields, status_options):
            if val not in (None, ""):
                _set_field(tid, fid, val)
        print(f"  -> {o['order_id']} {'created' if was_new else 'updated'} ({tid})")

    store.save(db)
    print(f"Done. {created} created · {updated} updated.")


if __name__ == "__main__":
    main()
