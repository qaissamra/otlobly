#!/usr/bin/env python3
"""
Self-checks: a backup is only "OK" when the database inside it is actually intact.

WHY. /api/backup uses SQLite's backup API, which copies pages VERBATIM — corruption
included. backup_pull.py used to check only the zip CRC and the manifest's row
counts (frozen at orders=41/customers=49 for weeks), so on 2026-09-05 a corrupt live
file would have been banked at 03:30 as "✅ backup OK". Pinned here: the server
writes its integrity verdict into manifest.json, and the puller runs its own
integrity_check on the extracted database and files a bad one as .zip.corrupt.

    ./.venv/bin/python test_backup_truth.py
"""
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-backup-truth-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "otlobly.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "sekret"
os.environ["DB_SENTINEL_OFF"] = "1"

import app as appmod       # noqa: E402
import backup_pull         # noqa: E402
import db                  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def smash_root(path, table):
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    psize = raw.execute("PRAGMA page_size").fetchone()[0]
    root = raw.execute("SELECT rootpage FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
    raw.close()
    p = Path(path)
    buf = bytearray(p.read_bytes())
    buf[(root - 1) * psize:root * psize] = b"\xde\xad\xbe\xef" * (psize // 4)
    p.write_bytes(bytes(buf))
    for ext in ("-wal", "-shm"):
        Path(str(path) + ext).unlink(missing_ok=True)


def make_zip(dbfile, manifest):
    z = _TMP / f"{Path(dbfile).stem}-{len(os.listdir(_TMP))}.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(dbfile, "otlobly.db")
        zf.writestr("manifest.json", json.dumps(manifest))
    return z


def the_server_says_what_its_snapshot_is():
    cl = appmod.app.test_client()
    r = cl.get("/api/backup", headers={"Authorization": "Bearer sekret"})
    check("backup downloads", r.status_code == 200)
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        man = json.loads(z.read("manifest.json"))
    check("manifest carries integrity=ok", man.get("integrity") == "ok")
    check("…and the SQLite version", bool(man.get("sqlite_version")))
    check("row counts still there", man.get("counts", {}).get("orders") == 1)
    # now a damaged live file → the snapshot copies the damage → the manifest SAYS so
    smash_root(db.DB_FILE, "orders")
    r = cl.get("/api/backup", headers={"Authorization": "Bearer sekret"})
    if r.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(r.data)) as z:
            man = json.loads(z.read("manifest.json"))
        check("a damaged snapshot is labelled, not blessed", man.get("integrity") not in (None, "ok"))
    else:
        check("a damaged live file cannot be banked silently (backup refused)", r.status_code >= 500)


def the_puller_refuses_a_rotten_database():
    good = _TMP / "good.db"
    saved = db.DB_FILE
    db.DB_FILE = good
    try:
        db.init_db()
        with db.connect() as c:
            c.execute("INSERT INTO orders (order_code, customer_phone, status, business_id, created_at, data_json) "
                      "VALUES ('OTL-0001','0','requested',1,'now','{}')")
        raw = sqlite3.connect(good)
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw.close()
    finally:
        db.DB_FILE = saved
    for ext in ("-wal", "-shm"):
        Path(str(good) + ext).unlink(missing_ok=True)
    ok_zip = make_zip(good, {"counts": {"orders": 1}, "integrity": "ok"})
    counts = backup_pull.verify_zip(ok_zip)
    check("an intact zip verifies and returns its counts", counts.get("orders") == 1)
    legacy = make_zip(good, {"counts": {"orders": 1}})
    check("an older server without the verdict still verifies (the puller checks itself)",
          backup_pull.verify_zip(legacy).get("orders") == 1)
    bad = _TMP / "bad.db"
    bad.write_bytes(good.read_bytes())
    smash_root(bad, "orders")
    rotten = make_zip(bad, {"counts": {"orders": 1}, "integrity": "ok"})   # server lied / older build
    raised = ""
    try:
        backup_pull.verify_zip(rotten)
    except ValueError as e:
        raised = str(e)
    check("a corrupt database inside a valid zip is REFUSED", "integrity_check" in raised)
    flagged = make_zip(good, {"counts": {"orders": 1}, "integrity": "Tree 10 page 10: btreeInitPage() returns error code 11"})
    raised = ""
    try:
        backup_pull.verify_zip(flagged)
    except ValueError as e:
        raised = str(e)
    check("the server's own bad verdict is enough to refuse", "server integrity_check" in raised)
    empty = make_zip(good, {"counts": {"orders": 0}, "integrity": "ok"})
    raised = ""
    try:
        backup_pull.verify_zip(empty)
    except ValueError as e:
        raised = str(e)
    check("no orders still means no backup", "no orders" in raised)


def the_watchdog_no_longer_says_restore():
    src = Path(backup_pull.__file__).with_name("db_watch.py").read_text()
    check("db_watch never tells the owner to POST /api/restore over today's data",
          "/api/restore" not in src and "repairs itself" in src)


def main():
    db.init_db()
    with db.connect() as c:
        c.execute("INSERT INTO orders (order_code, customer_phone, status, business_id, created_at, data_json) "
                  "VALUES ('OTL-0001','0','requested',1,'now','{}')")
    print("the server's verdict:");    the_server_says_what_its_snapshot_is()
    print("the puller:");              the_puller_refuses_a_rotten_database()
    print("the watchdog text:");       the_watchdog_no_longer_says_restore()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all backup-truth checks passed ✓")


if __name__ == "__main__":
    main()
