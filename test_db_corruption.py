#!/usr/bin/env python3
"""
Self-checks for surviving a corrupt otlobly.db (db.py + app.py's boot blocks).

WHY THIS EXISTS — the 2026-09-04 outage, in one sentence: a page-level corruption
that `init_db()`'s quarantine guard never saw raised out of a MODULE-LEVEL
`db.claim_once(...)` in app.py, so gunicorn got no app object at all — no process,
502 on every route, and `POST /api/restore`, the endpoint whose whole job is to
repair a corrupt DB, unreachable *because* the DB was corrupt.

The corruption itself is a recurring condition (5th time since July). The app not
booting is the defect. So what is pinned here is: **a corrupt database may cost
data, but it must never cost the process.**

    ./.venv/bin/python test_db_corruption.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-corrupt-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import db          # noqa: E402

fails = []
REPO = Path(__file__).resolve().parent


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def build_corrupt_db(path):
    """A database whose `settings` b-tree is destroyed while its SCHEMA is pristine.

    That distinction is the whole point. `init_db()` only reads sqlite_master
    (CREATE TABLE IF NOT EXISTS) and PRAGMA table_info (migrate), so it sails
    through — exactly as it did on Render — and the failure lands later, on the
    first write to `settings`. Smashing pages at random reproduces a DIFFERENT
    bug (init_db catches that one) and would prove nothing.
    """
    old = db.DB_FILE
    db.DB_FILE = Path(path)
    try:
        db.init_db()
        with db.connect() as c:
            for i in range(400):
                c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                          (f"pad:{i}", json.dumps("x" * 150)))
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        psize = raw.execute("PRAGMA page_size").fetchone()[0]
        root = raw.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='settings'").fetchone()[0]
        raw.close()
    finally:
        db.DB_FILE = old
    p = Path(path)
    buf = bytearray(p.read_bytes())
    off = (root - 1) * psize                       # rootpage is 1-based
    buf[off:off + psize] = b"\xde\xad\xbe\xef" * (psize // 4)
    p.write_bytes(bytes(buf))
    for ext in ("-wal", "-shm"):
        Path(str(path) + ext).unlink(missing_ok=True)
    return path


def _fresh(name):
    d = _TMP / name
    d.mkdir(exist_ok=True)
    return build_corrupt_db(str(d / "otlobly.db"))


def the_corruption_is_the_one_that_happened():
    """Prove the fixture reproduces Render's failure, not some other corruption."""
    path = _fresh("shape")
    old = db.DB_FILE
    db.DB_FILE = Path(path)
    try:
        init_ok = True
        try:
            db.init_db()
        except Exception:
            init_ok = False
        check("init_db() passes on it, as it did on Render", init_ok)
        raised = ""
        try:
            with db.connect() as c:
                c.execute("INSERT INTO settings (key,value) VALUES (?,?)", ("probe", "1"))
        except sqlite3.DatabaseError as e:
            raised = str(e).lower()
        check("a write to settings raises 'malformed'", "malformed" in raised)
    finally:
        db.DB_FILE = old


def claim_once_never_raises():
    """🛑 THE REGRESSION. Before the fix this raised and took the whole app with it."""
    path = _fresh("claim")
    old = db.DB_FILE
    db.DB_FILE = Path(path)
    try:
        db.init_db()
        raised = None
        try:
            db.claim_once("boot:reconcile_v1")
        except Exception as e:
            raised = e
        check("claim_once() does not raise on a corrupt DB", raised is None)
        check("the bad file is preserved for salvage, not deleted",
              any(p.name.startswith("otlobly.db.corrupt-") for p in Path(path).parent.iterdir()))
        # and the app can carry on: the replacement DB is writable
        works = False
        try:
            works = db.claim_once("boot:after_quarantine") is True
        except Exception:
            works = False
        check("the app keeps working on the fresh schema", works)
    finally:
        db.DB_FILE = old


def a_missing_file_means_the_other_worker_won():
    """gunicorn imports app.py in BOTH workers. They can hit the corruption at the
    same moment; the loser finds the file already renamed. That is success, not
    failure — treating it as failure is how one worker still kills the deploy."""
    old = db.DB_FILE
    db.DB_FILE = _TMP / "definitely-not-here.db"
    try:
        err = sqlite3.DatabaseError("database disk image is malformed")
        check("a missing file reads as already-quarantined",
              db._quarantine_corrupt_db(err) is True)
    finally:
        db.DB_FILE = old


def only_corruption_is_quarantined():
    """A lock or a busy DB must never move the live database aside."""
    path = _fresh("lock")
    old = db.DB_FILE
    db.DB_FILE = Path(path)
    try:
        check("a 'database is locked' error is not corruption",
              db._quarantine_corrupt_db(sqlite3.OperationalError("database is locked")) is False)
        check("and the file is left exactly where it was", Path(path).exists())
    finally:
        db.DB_FILE = old


def the_app_still_boots():
    """The end of the story: gunicorn must get an app object and /healthz must answer.

    Run in a SUBPROCESS with a fresh interpreter — importing app.py is the thing
    that broke, and it can only be tested by actually doing it.
    """
    d = _TMP / "boot"
    d.mkdir(exist_ok=True)
    path = build_corrupt_db(str(d / "otlobly.db"))
    env = {**os.environ, "OTLOBLY_DB": path, "OTLOBLY_DATA_DIR": str(d),
           "OTLOBLY_SECRET": "x", "PYTHONPATH": str(REPO)}
    env.pop("OTLOBLY_SECURE", None)
    code = (
        "import app;"
        "c = app.app.test_client();"
        "r = c.get('/healthz');"
        "print('HEALTHZ', r.status_code)"
    )
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(REPO),
                       capture_output=True, text=True, timeout=180)
    ok = p.returncode == 0 and "HEALTHZ 200" in p.stdout
    if not ok:
        print("     ---- subprocess said ----")
        for line in (p.stdout + p.stderr).strip().splitlines()[-12:]:
            print("     " + line[:150])
    check("app.py imports and serves /healthz on a corrupt DB", ok)


def the_salvage_route_is_fail_closed():
    """A file-serving route is one bad name away from an arbitrary read, and this one
    points at the directory holding the live database and every customer ID photo."""
    os.environ["OTLOBLY_WORKER_TOKEN"] = "sekret"
    import app as app_mod
    from paths import DATA_DIR
    cl = app_mod.app.test_client()
    hdr = {"Authorization": "Bearer sekret"}

    check("listing needs the token", cl.get("/api/quarantined").status_code == 401)
    check("fetching needs the token",
          cl.get("/api/quarantined/otlobly.db.corrupt-x").status_code == 401)

    root = Path(DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    good = root / "otlobly.db.corrupt-20260904-121700"
    good.write_bytes(b"salvage me")
    (root / "otlobly.db").write_bytes(b"LIVE DATA")

    body = cl.get("/api/quarantined", headers=hdr).get_json() or {}
    names = [f["name"] for f in (body.get("files") or [])]
    check("a quarantined file is listed", good.name in names)
    check("the LIVE database is never listed", "otlobly.db" not in names)

    r = cl.get(f"/api/quarantined/{good.name}", headers=hdr)
    check("a quarantined file downloads", r.status_code == 200 and r.data == b"salvage me")

    for bad in ("otlobly.db", "config.json", "otlobly.db.corrupt-../otlobly.db",
                "../otlobly.db", "otlobly.db.corrupt-x/../../etc/passwd"):
        code = cl.get("/api/quarantined/" + bad, headers=hdr).status_code
        check(f"refuses {bad!r}", code in (404, 308))


def the_db_probe_tells_the_truth():
    """The signal that would have caught 2026-09-04 three hours earlier."""
    import app as app_mod
    cl = app_mod.app.test_client()
    r = cl.get("/api/health/db")
    check("a healthy DB answers ok", r.status_code == 200 and (r.get_json() or {}).get("ok") is True)

    d = _TMP / "probe"
    d.mkdir(exist_ok=True)
    path = build_corrupt_db(str(d / "otlobly.db"))
    old = db.DB_FILE
    db.DB_FILE = Path(path)
    try:
        r = cl.get("/api/health/db")
        body = r.get_json() or {}
        check("a corrupt DB answers not-ok", r.status_code == 503 and body.get("ok") is False)
        check("and says why", bool(body.get("error")))
    finally:
        db.DB_FILE = old


def main():
    print("the corruption shape:");   the_corruption_is_the_one_that_happened()
    print("claim_once survives:");    claim_once_never_raises()
    print("two-worker race:");        a_missing_file_means_the_other_worker_won()
    print("only corruption moves it:"); only_corruption_is_quarantined()
    print("the app still boots:");    the_app_still_boots()
    print("salvage route is safe:"); the_salvage_route_is_fail_closed()
    print("the DB probe:");          the_db_probe_tells_the_truth()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all db-corruption checks passed ✓")


if __name__ == "__main__":
    main()
