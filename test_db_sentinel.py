#!/usr/bin/env python3
"""
Self-checks: db_sentinel.py (verified hourly snapshots + 10-min quick_check), the
staged /api/restore, the mailbox-repair gate, and the health field on the bell.

    ./.venv/bin/python test_db_sentinel.py
"""
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-sentinel-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "otlobly.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("OTLOBLY_GUNICORN_MASTER", None)
os.environ.pop("OTLOBLY_DBREPAIR", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "sekret"
os.environ["DB_SENTINEL_OFF"] = "1"                  # no background thread in a test
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("TELEGRAM_CHAT_ID", "")

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402
import db_sentinel     # noqa: E402
import dbrepair        # noqa: E402
import gaash_mail      # noqa: E402

fails = []
LIVE = Path(db.DB_FILE)


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def reset_reporter():
    db._reported["done"] = False
    db._last_verdict.update(t=0.0, v="ok")
    for p in (db.repair_marker_path(), db.maintenance_marker_path()):
        p.unlink(missing_ok=True)


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


def zip_of(dbfile):
    z = _TMP / (Path(dbfile).stem + ".zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(dbfile, "otlobly.db")
    return z.read_bytes()


def the_sentinel_keeps_verified_snapshots():
    reset_reporter()
    for h in ("00", "01", "02", "03"):
        got = db_sentinel.snapshot_once(bucket=f"20260905-{h}")
        check(f"snapshot {h} written + verified", got is not None and got.with_name(got.name + ".ok").exists())
    check("only the newest 3 are kept", len(db_sentinel.snapshots()) == 3)
    check("dbrepair sees them as verified", len(dbrepair.snapshots()) == 3)
    check("the same hour is never taken twice", db_sentinel.snapshot_once(bucket="20260905-03") is None)
    check("a clean quick_check writes health ok", db_sentinel.check_once() is True
          and json.loads(db.health_path().read_text()).get("ok") is True)
    check("no repair was requested", not db.repair_marker_path().exists())


def a_rotten_live_file_is_caught_by_the_sentinel():
    reset_reporter()
    smash_root(LIVE, "settings")
    ok = db_sentinel.check_once()
    check("quick_check verdict → repair requested", ok is False and db.repair_marker_path().exists())
    h = json.loads(db.health_path().read_text())
    check("health.json says repairing", h.get("ok") is False and h.get("repairing") is True)
    reset_reporter()
    check("no snapshot is taken from a damaged file", db_sentinel.snapshot_once(bucket="20260905-09") is None)
    # and the master's preflight puts it right — filling the lost table from the snapshot
    rc = dbrepair.preflight()
    check("preflight repairs", rc == 0 and dbrepair.check(LIVE, full=True) == "ok")
    raw = sqlite3.connect(LIVE)
    n = raw.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    raw.close()
    check(f"settings came back from the verified snapshot ({n} rows)", n > 0)


def the_mailbox_repair_refuses_a_corrupt_file():
    reset_reporter()
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO gaash_accounts (id, email, app_password, added_at) "
                  "VALUES ('acct_t','t@x.com','pw',?)", (db.now_iso(),))
    res = gaash_mail.repair_accounts()
    check("on a healthy file the old repair still answers normally", "repair_requested" not in res)
    smash_root(LIVE, "settings")
    res = gaash_mail.repair_accounts()
    check("on a corrupt file it refuses and requests the whole-file repair",
          res.get("ok") is False and res.get("repair_requested") is True and db.repair_marker_path().exists())
    reset_reporter()
    check("preflight repairs afterwards", dbrepair.preflight() == 0)


def restore_stages_and_never_swaps_in_the_request():
    reset_reporter()
    cl = appmod.app.test_client()
    hdr = {"Authorization": "Bearer sekret"}
    check("needs the worker token", cl.post("/api/restore", data=b"x").status_code == 401)
    good = _TMP / "good.db"
    shutil.copy2(LIVE, good)
    before = LIVE.stat().st_ino
    r = cl.post("/api/restore", data=zip_of(good), headers=hdr)
    body = r.get_json() or {}
    check("a valid upload is STAGED", r.status_code == 200 and body.get("staged") is True)
    check("the live file was not touched by the request", LIVE.stat().st_ino == before)
    check("otlobly.db.pending-restore exists", (LIVE.parent / "otlobly.db.pending-restore").exists())
    check("a repair was requested for the master", db.repair_marker_path().exists())
    check("no staging leftovers", not list(LIVE.parent.glob("otlobly.db.incoming-*")))
    reset_reporter()
    check("preflight applies it", dbrepair.preflight() == 0 and LIVE.stat().st_ino != before)
    bad = _TMP / "bad.db"
    shutil.copy2(LIVE, bad)
    smash_root(bad, "orders")
    r = cl.post("/api/restore", data=zip_of(bad), headers=hdr)
    check("a corrupt upload is refused with 400", r.status_code == 400)
    check("and nothing was staged", not (LIVE.parent / "otlobly.db.pending-restore").exists())


def the_bell_carries_the_database_health():
    reset_reporter()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    cl = appmod.app.test_client()
    cl.post("/login", data={"username": "otlo", "password": "s1"})
    d = cl.get("/api/notifications").get_json() or {}
    check("notifications include db health", isinstance(d.get("db"), dict) and d["db"].get("ok") is True)
    db.write_health({"ok": False, "error": "x", "repairing": True, "maintenance": False, "at": db.now_iso()})
    d = cl.get("/api/notifications").get_json() or {}
    check("…and reflect a repair in progress", d.get("db", {}).get("repairing") is True)
    db.write_health({"ok": True, "error": "", "repairing": False, "maintenance": False, "at": db.now_iso()})


def main():
    db.init_db()
    with db.connect() as c:
        for i in range(50):
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (f"k{i}", json.dumps(i)))
    print("verified snapshots:");        the_sentinel_keeps_verified_snapshots()
    print("a rotten live file:");        a_rotten_live_file_is_caught_by_the_sentinel()
    print("mailbox repair gate:");       the_mailbox_repair_refuses_a_corrupt_file()
    print("staged restore:");            restore_stages_and_never_swaps_in_the_request()
    print("the bell:");                  the_bell_carries_the_database_health()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all db-sentinel checks passed ✓")


if __name__ == "__main__":
    main()
