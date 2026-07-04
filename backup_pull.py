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


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Pull an off-site Otlobly backup.")
    ap.add_argument("--base", default=os.environ.get("OTLOBLY_BASE_URL",
                                                     "https://otlobly.onrender.com"))
    args = ap.parse_args()

    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    if not tok:
        sys.exit("No OTLOBLY_WORKER_TOKEN in .env — copy it from Render → otlobly → Environment.")

    dest = Path(os.environ.get("OTLOBLY_BACKUP_DIR")
                or Path.home() / "OtloblyBackups").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"otlobly-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"

    print(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} pulling {args.base} -> {out}")
    req = request.Request(args.base.rstrip("/") + "/api/backup",
                          headers={"Authorization": f"Bearer {tok}"})
    try:
        with request.urlopen(req, timeout=300) as r, open(out, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except error.HTTPError as e:
        sys.exit(f"Backup FAILED: HTTP {e.code}"
                 + (" — token mismatch? Check OTLOBLY_WORKER_TOKEN vs Render." if e.code == 401 else ""))
    except OSError as e:
        sys.exit(f"Backup FAILED: {e}")

    # Verify before trusting it: intact zip, DB + manifest present, real rows.
    try:
        with zipfile.ZipFile(out) as z:
            bad = z.testzip()
            names = z.namelist()
            if bad or "otlobly.db" not in names or "manifest.json" not in names:
                raise ValueError(f"incomplete zip (first bad file: {bad})")
            man = json.loads(z.read("manifest.json"))
        counts = man.get("counts") or {}
        if not counts.get("orders"):
            raise ValueError(f"manifest shows no orders: {counts}")
    except Exception as e:  # noqa — any problem means the backup is NOT trustworthy
        out.rename(out.with_suffix(".zip.corrupt"))
        sys.exit(f"Backup verification FAILED ({e}) — kept as {out.name}.corrupt")

    print(f"OK {out.name}  {out.stat().st_size / 1e6:.1f} MB  "
          + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # Retention: keep the newest N good zips.
    keep = int(os.environ.get("OTLOBLY_BACKUP_KEEP", "30"))
    for p in sorted(dest.glob("otlobly-backup-*.zip"))[:-keep]:
        p.unlink()
        print(f"pruned {p.name}")


if __name__ == "__main__":
    main()
