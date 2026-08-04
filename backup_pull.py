#!/usr/bin/env python3
"""
Pull a full OFF-SITE backup of the live Otlobly app onto this Mac.

Downloads /api/backup — one zip holding a consistent SQLite snapshot, the JSON
stores, the uploaded ID/PO images, and a manifest — verifies it, and keeps
dated copies with retention. Without this, the Render disk is the ONLY copy of
the business (orders, customers, the payments ledger, staff logins).

  python3 backup_pull.py                                # pull from the live app
  python3 backup_pull.py --base http://localhost:8789   # test against local

Scheduled nightly by com.otlobly.backup.plist (launchd) → logs to backup.log.
Auth: OTLOBLY_WORKER_TOKEN from .env (the same token the order-placer uses).
Overrides: OTLOBLY_BACKUP_DIR (default ~/OtloblyBackups),
           OTLOBLY_BACKUP_KEEP (default 30 newest zips),
           OTLOBLY_BASE_URL (default https://otlobly.onrender.com).

RESTORE: unzip the newest otlobly-backup-*.zip and copy its contents into the
data dir (/var/data on Render via a shell, or this folder locally), replacing
what's there, then restart the app. otlobly.db in the zip is a complete DB.
"""

import argparse
import json
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib import request, error

HERE = Path(__file__).resolve().parent


def load_env():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notify(text):
    """One Telegram line to the owner — the nightly run reports its own outcome
    (silence would be ambiguous: failed vs never fired). Best effort: a notify
    problem must never fail or re-run the backup itself."""
    try:
        sys.path.insert(0, str(HERE))
        import telegram
        r = telegram.send(text)
        if not r.get("ok"):
            print(f"(telegram notify skipped: {r.get('error')})")
    except Exception as e:  # noqa: BLE001
        print(f"(telegram notify skipped: {e})")


def fail(msg):
    notify(f"🚨 Otlobly backup FAILED · النسخة الاحتياطية فشلت\n{msg}")
    sys.exit(f"Backup FAILED: {msg}")


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Pull an off-site Otlobly backup.")
    ap.add_argument("--base", default=os.environ.get("OTLOBLY_BASE_URL",
                                                     "https://otlobly.onrender.com"))
    args = ap.parse_args()

    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    if not tok:
        fail("No OTLOBLY_WORKER_TOKEN in .env — copy it from Render → otlobly → Environment.")

    dest = Path(os.environ.get("OTLOBLY_BACKUP_DIR")
                or Path.home() / "OtloblyBackups").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"otlobly-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"

    print(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} pulling {args.base} -> {out}")

    # Download to a .part staging name so a failed/interrupted pull can NEVER
    # leave a truncated *.zip in the retention set (2026-08-03 did exactly that:
    # a connection reset mid-stream left a 43MB corpse the restore tools then
    # tripped over). Retry transient failures — the server zips ~80MB+ while
    # streaming and Render can stall between chunks or reset, so one attempt a
    # night was fragile (timed out 3 nights running, 2026-08-02→04). The
    # timeout is per socket operation (connect / each read), not the whole
    # download.
    tmp = out.with_suffix(".zip.part")
    attempts = 3
    for attempt in range(1, attempts + 1):
        req = request.Request(args.base.rstrip("/") + "/api/backup",
                              headers={"Authorization": f"Bearer {tok}"})
        try:
            with request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
            break                                        # downloaded — verify below
        except error.HTTPError as e:
            tmp.unlink(missing_ok=True)
            if e.code == 401:
                fail("HTTP 401 — token mismatch? Check OTLOBLY_WORKER_TOKEN vs Render.")
            if attempt == attempts:
                fail(f"HTTP {e.code} after {attempts} attempts")
            print(f"attempt {attempt}/{attempts} failed (HTTP {e.code}) — retrying in {30 * attempt}s")
            time.sleep(30 * attempt)
        except OSError as e:
            tmp.unlink(missing_ok=True)                  # never leave a truncated file
            if attempt == attempts:
                fail(f"{e} after {attempts} attempts")
            print(f"attempt {attempt}/{attempts} failed ({e}) — retrying in {30 * attempt}s")
            time.sleep(30 * attempt)

    # Verify before trusting it: intact zip, DB + manifest present, real rows.
    # Only a verified download earns the final .zip name.
    try:
        with zipfile.ZipFile(tmp) as z:
            bad = z.testzip()
            names = z.namelist()
            if bad or "otlobly.db" not in names or "manifest.json" not in names:
                raise ValueError(f"incomplete zip (first bad file: {bad})")
            man = json.loads(z.read("manifest.json"))
        counts = man.get("counts") or {}
        if not counts.get("orders"):
            raise ValueError(f"manifest shows no orders: {counts}")
    except Exception as e:  # noqa — any problem means the backup is NOT trustworthy
        tmp.rename(out.with_suffix(".zip.corrupt"))
        notify(f"🚨 Otlobly backup verification FAILED · فشل التحقق\n{e} — kept as {out.name}.corrupt")
        sys.exit(f"Backup verification FAILED ({e}) — kept as {out.name}.corrupt")
    tmp.rename(out)

    print(f"OK {out.name}  {out.stat().st_size / 1e6:.1f} MB  "
          + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    notify(f"✅ Otlobly backup OK · النسخة الاحتياطية سليمة\n{out.name} · "
           f"{out.stat().st_size / 1e6:.1f} MB · orders={counts.get('orders')} "
           f"customers={counts.get('customers')}")

    # Retention: keep the newest N good zips.
    keep = int(os.environ.get("OTLOBLY_BACKUP_KEEP", "30"))
    for p in sorted(dest.glob("otlobly-backup-*.zip"))[:-keep]:
        p.unlink()
        print(f"pruned {p.name}")


if __name__ == "__main__":
    main()
