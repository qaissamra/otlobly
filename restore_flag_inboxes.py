#!/usr/bin/env python3
"""
Put the 🚩 watched inboxes back on the live app, from a backup on this Mac.

On 2026-09-12 the database damage ate the page holding flag_inboxes, and the
self-repair refilled the table with rows salvaged from OTHER tables — Le Luxe
cards, audit lines. Nothing errored: the poll selects the inboxes with
active=1, the junk rows carry text there, so it matched nothing and the flag
machine went on stamping "last poll: 2 minutes ago" against an EMPTY list for
days. Seven Amazon buying-account inboxes and their Gmail app passwords went
with the page.

Re-adding them by hand mints NEW inbox ids, which orphans every open flag, and
needs seven app passwords back out of Google. This uploads a backup zip and
restores that ONE table instead: same ids, same passwords, same IMAP cursors —
so the first poll afterwards reads the mail that arrived while the watch was
blind. Nothing else in the live database is touched. Inboxes the live app
still has are left alone; this only fills holes.

  python3 restore_flag_inboxes.py                      # newest backup → live
  python3 restore_flag_inboxes.py --dry-run            # say what it would do
  python3 restore_flag_inboxes.py --zip <path>         # a specific backup
  python3 restore_flag_inboxes.py --base http://localhost:8789

⚠ Pick the backup BEFORE the damage: --dry-run prints what a zip holds, and a
zip taken after 2026-09-12 03:44 holds nothing to restore.

Auth: OTLOBLY_WORKER_TOKEN from .env (the same token /api/backup uses).
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib import request, error

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from backup_pull import load_env          # noqa: E402 — the same .env reader

DEFAULT_DIR = Path(os.environ.get("OTLOBLY_BACKUP_DIR",
                                  Path.home() / "OtloblyBackups"))


def newest_zip(folder):
    zips = sorted(Path(folder).glob("otlobly-backup-*.zip"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        sys.exit(f"no otlobly-backup-*.zip in {folder}")
    return zips[0]


def peek(zip_path):
    """Which inboxes does this backup hold? Read BEFORE uploading, so a
    backup taken AFTER the damage cannot be sent up in place of a good one.
    Addresses only — the passwords stay inside the file."""
    with zipfile.ZipFile(zip_path) as z, z.open("otlobly.db") as src, \
            tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
        tmp = dst.name
    out = []
    try:
        # immutable: a WAL database opened read-only still wants to create
        # its -shm sidecar, and this copy has none
        c = sqlite3.connect(f"file:{tmp}?mode=ro&immutable=1", uri=True)
        c.row_factory = sqlite3.Row
        cur = c.execute("SELECT * FROM flag_inboxes")
        while True:                       # row by row: the table may be damaged
            r = cur.fetchone()
            if r is None:
                break
            r = dict(r)
            if isinstance(r.get("email"), str) and "@" in r["email"] \
                    and (r.get("app_password") or "").strip():
                out.append(f"{r['email']} ({r.get('label') or 'no profile'})")
        c.close()
    except sqlite3.DatabaseError as e:
        print(f"(that backup's inbox table stops short: {e})")
    finally:
        os.unlink(tmp)
    return out


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="backup zip (default: the newest one)")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--base", default=os.environ.get(
        "OTLOBLY_BASE_URL", "https://otlobly.onrender.com"))
    ap.add_argument("--dry-run", action="store_true",
                    help="read the backup and stop — upload nothing")
    a = ap.parse_args()

    zip_path = Path(a.zip) if a.zip else newest_zip(a.dir)
    if not zip_path.exists():
        sys.exit(f"no such file: {zip_path}")
    boxes = peek(zip_path)
    print(f"backup : {zip_path.name} ({zip_path.stat().st_size / 1e6:.0f} MB)")
    print(f"holds  : {len(boxes)} inbox(es) with a password"
          + ("\n         " + "\n         ".join(boxes) if boxes else ""))
    if not boxes:
        sys.exit("nothing to restore from this backup — try an older one "
                 "(the damage landed on 2026-09-12)")
    if a.dry_run:
        print("dry run — nothing uploaded")
        return

    token = os.environ.get("OTLOBLY_WORKER_TOKEN")
    if not token:
        sys.exit("OTLOBLY_WORKER_TOKEN missing (.env)")
    url = a.base.rstrip("/") + "/api/flags/inboxes/restore"
    print(f"upload : {url}")
    with open(zip_path, "rb") as body:
        req = request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/zip",
            "Content-Length": str(zip_path.stat().st_size)})
        try:
            with request.urlopen(req, timeout=900) as r:
                res = json.loads(r.read().decode())
        except error.HTTPError as e:
            sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    if not res.get("ok"):
        sys.exit("restore refused: " + str(res.get("error")))
    print("restored:", ", ".join(res.get("restored") or [])
          or "(none — the live app already had them)")
    if res.get("already_live"):
        print("left as is:", ", ".join(res["already_live"]))
    print("watching:", res.get("watching"), "inbox(es) now")
    print("\nNext: open 🚩 Flags on the live app. Every inbox should show ✓ "
          "within a few minutes — a ⚠ means Google revoked that app password "
          "while the watch was down, and the 🔑 button takes a fresh one.")


if __name__ == "__main__":
    main()
