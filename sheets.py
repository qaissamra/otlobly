#!/usr/bin/env python3
"""
Mirror orders.json to a spreadsheet. Money in USD.

Two modes (config.sheets.mode):
  "csv"  (default, zero-dependency) -> writes a clean orders_export.csv you can
          import/paste into Google Sheets.
  "api"  -> writes straight into a Google Sheet via a service account. Needs
          `pip install google-api-python-client google-auth` and
          GOOGLE_APPLICATION_CREDENTIALS pointing at the service-account JSON,
          shared (editor) with the target sheet. Falls back to CSV if the
          libraries/credentials are missing.

  python3 sheets.py --dry-run     # print the rows that would be written
  python3 sheets.py               # write (csv or api per config)
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import cfg
import store

EXPORT_CSV = Path(__file__).with_name("orders_export.csv")

HEADER = ["Order ID", "Name", "Phone", "Phone 2", "Address",
          "Link 1", "Link 2", "Link 3", "Amount USD", "Deposit note",
          "Batch", "Status", "Box", "Tracking", "ClickUp Task"]


def order_to_row(o):
    phones = o["customer"].get("phones") or []
    links = [it.get("clean_url") or it.get("raw_url") for it in o["items"]]
    links += [""] * (3 - len(links))
    amt = o.get("amount_to_collect_usd")
    return [
        o["order_id"],
        o["customer"]["name"],
        phones[0]["e164"] if phones else "",
        phones[1]["e164"] if len(phones) > 1 else "",
        o["customer"]["address"],
        links[0], links[1], links[2],
        f"{amt:.2f}" if amt is not None else "",
        o["customer"].get("notes", ""),
        o.get("batch") or "",
        o["status"],
        o.get("profile_box") or "",
        o.get("tracking_number") or "",
        o.get("clickup_task_id") or "",
    ]


def rows(db):
    return [order_to_row(o) for o in db["orders"]]


def write_csv(db):
    with EXPORT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows(db))
    return EXPORT_CSV


def write_api(db, config):
    """Write to a Google Sheet via a service account. Returns True on success."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        print("API mode needs: pip install google-api-python-client google-auth",
              file=sys.stderr)
        return False
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    sheet_id = cfg.get(config, "sheets.spreadsheet_id")
    worksheet = cfg.get(config, "sheets.worksheet", "Orders")
    if not creds_path or not sheet_id:
        print("API mode needs GOOGLE_APPLICATION_CREDENTIALS and "
              "config.sheets.spreadsheet_id.", file=sys.stderr)
        return False
    creds = Credentials.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    values = [HEADER] + rows(db)
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"{worksheet}!A1",
        valueInputOption="RAW", body={"values": values}).execute()
    print(f"Wrote {len(values)-1} rows to Google Sheet {sheet_id} ({worksheet}).")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config = cfg.load()
    mode = cfg.get(config, "sheets.mode", "csv")
    db = store.load()

    if args.dry_run:
        print(f"DRY RUN — mode={mode}, {len(db['orders'])} rows:\n")
        print(" | ".join(HEADER))
        for r in rows(db):
            print(" | ".join(str(c)[:18] for c in r))
        print(f"\n{len(db['orders'])} rows. (DRY RUN — nothing written)")
        return

    if mode == "api" and write_api(db, config):
        return
    if mode == "api":
        print("Falling back to CSV export.", file=sys.stderr)
    path = write_csv(db)
    print(f"Wrote {len(db['orders'])} rows to {path.name}.")


if __name__ == "__main__":
    main()
