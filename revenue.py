#!/usr/bin/env python3
"""
Revenue — how much Otlobly collects from customers (USD).

Two sources (config.pnl.revenue.mode):
  "orders" (default) — sum amount_to_collect_usd from the local orders.json
                       (non-cancelled), bucketed by month of created_at.
  "sheet"            — read the master customer-orders Google Sheet live via a
                       service account (GOOGLE_APPLICATION_CREDENTIALS), summing
                       the configured amount column.

  python3 revenue.py --dry-run     # print, don't write the cache
  python3 revenue.py               # write reports/revenue_cache.json
  python3 revenue.py --selftest    # offline aggregation test
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cfg
import store

CACHE = Path(__file__).with_name("reports") / "revenue_cache.json"


def parse_amount(cell):
    if cell is None:
        return None
    s = re.sub(r"[^\d.]", "", str(cell))
    try:
        return round(float(s), 2) if s else None
    except ValueError:
        return None


def from_orders():
    """Revenue from local orders.json (non-cancelled), by month of created_at."""
    db = store.load()
    total, n = 0.0, 0
    by_month = defaultdict(float)
    for o in db["orders"]:
        if o["status"] == "CANCELLED":
            continue
        amt = o.get("amount_to_collect_usd")
        if amt is None:
            continue
        total += amt
        n += 1
        by_month[(o.get("created_at") or "")[:7] or "unknown"] += amt
    return _pack(total, n, by_month, "orders.json")


def _gviz_csv(sheet_id, worksheet):
    """Read a tab from a link-readable Google Sheet as CSV — no credentials."""
    import urllib.request, urllib.parse, io
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
           f"?tqx=out:csv&sheet=" + urllib.parse.quote(worksheet))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    if "<html" in raw[:200].lower():
        raise RuntimeError("sheet not link-readable (share: anyone with link → Viewer)")
    import csv as _csv
    return list(_csv.reader(io.StringIO(raw)))


def from_gsheet(config):
    """Revenue from a link-readable Google Sheet tab (live, no service account).
    Sums the USD amount column, skipping excluded statuses; buckets by batch."""
    sid = cfg.get(config, "pnl.revenue.spreadsheet_id")
    ws = cfg.get(config, "pnl.revenue.worksheet", "Orders")
    amount_col = cfg.get(config, "pnl.revenue.amount_column")
    status_col = cfg.get(config, "pnl.revenue.status_column")
    batch_col = cfg.get(config, "pnl.revenue.batch_column")
    exclude = [s.lower() for s in cfg.get(config, "pnl.revenue.exclude_statuses", [])]
    if not sid:
        return {"error": "set pnl.revenue.spreadsheet_id in config.json"}
    try:
        rows = _gviz_csv(sid, ws)
    except Exception as e:  # noqa
        return {"error": f"sheet read failed: {e}"}
    if not rows:
        return _pack(0, 0, {}, f"gsheet:{ws}")
    hdr = rows[0]
    ai = hdr.index(amount_col) if amount_col in hdr else None
    si = hdr.index(status_col) if status_col and status_col in hdr else None
    bi = hdr.index(batch_col) if batch_col and batch_col in hdr else None
    if ai is None:
        return {"error": f"amount column '{amount_col}' not found (header: {hdr})"}
    total, n = 0.0, 0
    by_batch = defaultdict(float)
    for r in rows[1:]:
        if ai >= len(r):
            continue
        if si is not None and si < len(r) and r[si].strip().lower() in exclude:
            continue
        amt = parse_amount(r[ai])
        if not amt:
            continue
        total += amt
        n += 1
        b = (r[bi].strip() if bi is not None and bi < len(r) and r[bi].strip() else "—")
        by_batch[b] += amt
    res = _pack(total, n, {}, f"gsheet:{ws}")
    res["by_batch"] = {k: round(v, 2) for k, v in sorted(by_batch.items())}
    return res


def from_sheet(config):
    """Revenue from the master Google Sheet via a service account."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        return {"error": "sheet mode needs: pip install google-api-python-client google-auth"}
    import os
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    sheet_id = cfg.get(config, "pnl.revenue.spreadsheet_id")
    worksheet = cfg.get(config, "pnl.revenue.worksheet", "Orders")
    amount_col = cfg.get(config, "pnl.revenue.amount_column")
    date_col = cfg.get(config, "pnl.revenue.date_column")
    if not creds_path or not sheet_id:
        return {"error": "sheet mode needs GOOGLE_APPLICATION_CREDENTIALS + pnl.revenue.spreadsheet_id."}
    creds = Credentials.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    rows = svc.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=worksheet).execute().get("values", [])
    if not rows:
        return _pack(0, 0, {}, f"sheet:{worksheet}")
    header = rows[0]
    ai = header.index(amount_col) if amount_col in header else None
    di = header.index(date_col) if date_col and date_col in header else None
    if ai is None:
        return {"error": f"amount column '{amount_col}' not found in sheet header."}
    total, n = 0.0, 0
    by_month = defaultdict(float)
    for r in rows[1:]:
        amt = parse_amount(r[ai]) if ai < len(r) else None
        if amt is None:
            continue
        total += amt
        n += 1
        month = "unknown"
        if di is not None and di < len(r):
            m = re.search(r"(\d{4})[-/](\d{2})", str(r[di]))
            if m:
                month = f"{m.group(1)}-{m.group(2)}"
        by_month[month] += amt
    return _pack(total, n, by_month, f"sheet:{worksheet}")


def _pack(total, n, by_month, source):
    return {
        "total_usd": round(total, 2),
        "orders_counted": n,
        "by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def pull(config=None):
    config = config or cfg.load()
    mode = cfg.get(config, "pnl.revenue.mode", "orders")
    if mode == "gsheet":
        return from_gsheet(config)
    if mode == "sheet":
        return from_sheet(config)
    return from_orders()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return sys.exit(_selftest())
    agg = pull()
    if agg.get("error"):
        print(agg["error"])
        return
    print(f"Revenue: ${agg['total_usd']:.2f} across {agg['orders_counted']} "
          f"order(s)  [source: {agg['source']}]")
    print("By month:", json.dumps(agg["by_month"]))
    if not args.dry_run:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(agg, ensure_ascii=False, indent=2))
        print(f"Wrote {CACHE.name}.")
    else:
        print("(DRY RUN — cache not written)")


def _selftest():
    for raw, want in [("$54.14", 54.14), ("139.79", 139.79), ("68.21$", 68.21),
                      ("", None), ("1,615", 1615.0)]:
        got = parse_amount(raw)
        if got != want:
            print(f"XX parse_amount({raw!r}) -> {got} (want {want})")
            return 1
    print("parse_amount: OK")
    return 0


if __name__ == "__main__":
    main()
