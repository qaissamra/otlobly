#!/usr/bin/env python3
"""
Pull the LIVE Google Sheet ("Orders" tab) into orders.json so dashboard amounts
match what you typed in the sheet. The sheet is master for the base fields the
team edits there (amount, batch, customer); the tool keeps its own workflow
fields (status, Amazon #, profile/box, timestamps, tracking, ClickUp link).

  python3 sheet_sync.py --dry-run     # show what would change
  python3 sheet_sync.py               # apply
"""

import argparse
import csv
import io
import sys
import urllib.parse
import urllib.request

import activity
import cfg
import normalize
import store
import trash
from ingest import parse_usd, COL


def fetch_rows(config=None):
    """Read the Orders tab as RAW CSV (export endpoint). We must NOT use the gviz
    feed here: gviz type-coerces the 'number' column and silently drops phones
    that aren't pure numbers (dashes / Arabic digits), breaking customer matching."""
    config = config or cfg.load()
    sid = cfg.get(config, "pnl.revenue.spreadsheet_id")
    gid = str(cfg.get(config, "pnl.revenue.gid", "0"))      # 'Orders' tab = gid 0
    if not sid:
        raise RuntimeError("Set pnl.revenue.spreadsheet_id in config.json")
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    if "<html" in raw[:200].lower():
        raise RuntimeError("Sheet not link-readable (share: anyone with link → Viewer)")
    return list(csv.reader(io.StringIO(raw)))[1:]   # drop header


def sync(db=None, dry_run=False, save=True, prune=True):
    """Pull the sheet into orders.json.
    - adds rows that are new, updates amount/batch on matched rows;
    - marks every order it sees in the sheet `source="sheet"`;
    - if `prune`, moves orders we'd previously marked `source="sheet"` but that are
      no longer in the sheet to TRASH (recoverable). Orders never seen in the sheet
      (e.g. created via + Add order) are left untouched."""
    db = db or store.load()
    rows = fetch_rows()
    by_sig = {o["signature"]: o for o in db["orders"]}
    updated, created, changes = 0, 0, []
    sheet_sigs, seen_rows = set(), 0

    for r in rows:
        r = list(r) + [""] * (14 - len(r))
        if not (r[COL["name"]].strip() or "".join(r[i] for i in COL["links"]).strip()):
            continue
        phones = normalize.collect_phones(r[COL["phone_a"]], r[COL["phone_b"]])
        items = normalize.parse_items([r[i] for i in COL["links"]], expand=False)
        sig = store.signature(normalize.customer_key(phones, r[COL["name"]]), items)
        amount = parse_usd(r[COL["amount_usd"]])
        batch = str(r[COL["order_n"]]).strip() or None
        sheet_sigs.add(sig)
        seen_rows += 1

        ex = by_sig.get(sig)
        if ex:
            ch = {}
            if amount is not None and ex.get("amount_to_collect_usd") != amount:
                ch["amount_to_collect_usd"] = amount
            if batch and ex.get("batch") != batch:
                ch["batch"] = batch
            if not dry_run:
                ex["source"] = "sheet"          # confirmed present in the sheet
            if ch:
                changes.append((ex["order_id"], ex["customer"]["name"], dict(ch),
                                ex.get("amount_to_collect_usd")))
                if not dry_run:
                    ex.update(ch)
                    ex["updated_at"] = store.now_iso()
                updated += 1
        else:
            deposit = r[COL["deposit"]].strip()
            o = store.new_order(
                db, name=r[COL["name"]], phones=phones, address=r[COL["address"]],
                items=items, batch=r[COL["order_n"]],
                profile_box=r[COL["profile"]].strip() or None,
                status=store.map_sheet_status(r[COL["status"]]),
                amount_to_collect_usd=amount,
                notes=(f"عربون/Deposit: {deposit}" if deposit else ""))
            o["source"] = "sheet"
            changes.append((o["order_id"], o["customer"]["name"], {"NEW": amount}, None))
            if not dry_run:
                db["orders"].append(o)
                by_sig[sig] = o
            created += 1

    # Prune orders that were sheet-managed but have disappeared from the sheet.
    # Guard: never prune on an empty/failed fetch (would wrongly trash everything).
    removed = []
    if prune and seen_rows > 0:
        gone = [o for o in db["orders"]
                if o.get("source") == "sheet" and o["signature"] not in sheet_sigs]
        for o in gone:
            removed.append((o["order_id"], o["customer"]["name"]))
            if not dry_run:
                tdb = trash.load()
                trash.add(tdb, "order", f"{o['order_id']} · {o['customer']['name']}", o,
                          origin={})
                trash.save(tdb)
                activity.log("deleted", "order", o["order_id"],
                             f"{o['order_id']} · {o['customer']['name']}",
                             detail="removed (deleted from Google Sheet)")
        if not dry_run and gone:
            ids = {o["order_id"] for o in gone}
            db["orders"] = [o for o in db["orders"] if o["order_id"] not in ids]

    if not dry_run and save:
        store.save(db)
    return {"updated": updated, "created": created, "removed": removed,
            "changes": changes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = sync(dry_run=args.dry_run)
    for oid, name, ch, old in res["changes"]:
        if "NEW" in ch:
            print(f"  + {oid}  {name[:24]:24}  new (${ch['NEW']})")
        else:
            bits = ", ".join(f"{k}: {old}→{v}" for k, v in ch.items())
            print(f"  ~ {oid}  {name[:24]:24}  {bits}")
    for oid, name in res.get("removed", []):
        print(f"  - {oid}  {name[:24]:24}  gone from sheet → Trash")
    print(f"\n{res['updated']} updated · {res['created']} created · "
          f"{len(res.get('removed', []))} removed→Trash"
          + ("  (DRY RUN)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
