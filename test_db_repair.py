#!/usr/bin/env python3
"""
Self-checks for dbrepair.py — the database heals itself, and the healing can never
make things worse.

WHY THIS EXISTS. otlobly.db corrupted nine times in seven weeks (2026-07-18 →
2026-09-05). Every recovery was a human, hours later, working on the live open file.
What is pinned here is the replacement: a worker that meets corruption files a
request and steps aside; the gunicorn master rebuilds a clean file from the damaged
one with nobody holding it; the live name is touched once, by os.replace, only after
the new file passed integrity_check; the damaged file survives as evidence; and
after two rebuilds in an hour it STOPS and asks for a human instead of looping.

    ./.venv/bin/python test_db_repair.py
    OTLOBLY_CORRUPT_FIXTURE=/path/to/real-corrupt.db ./.venv/bin/python test_db_repair.py
"""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-repair-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "boot.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("OTLOBLY_GUNICORN_MASTER", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")      # never message anyone from a test
os.environ.setdefault("TELEGRAM_CHAT_ID", "")

import db          # noqa: E402
import dbrepair    # noqa: E402  (sets OTLOBLY_DBREPAIR=1 — tests that need the ACTIVE
                   #               reporter pop it explicitly)

REPO = Path(__file__).resolve().parent
PY = sys.executable
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def fresh_dir(name):
    d = _TMP / name
    d.mkdir(exist_ok=True)
    return d


def use(path):
    """Point db at `path` (dbrepair reads db.DB_FILE at call time)."""
    db.DB_FILE = Path(path)
    db._reported.update(done=False, at=0.0, n=0)
    db._quiet.clear()                            # a throttled log line reads as silence
    db._last_verdict.update(t=0.0, v="ok")
    return Path(path)


def make_db(path, orders=5, customers=300, settings=400):
    """A real-schema database with enough rows to span several pages."""
    use(path)
    db.init_db()
    with db.connect() as c:
        for i in range(customers):
            c.execute("INSERT INTO customers (customer_code, match_key, name, whatsapp, city, business_id, created_at, data_json) "
                      "VALUES (?,?,?,?,?,1,?,?)",
                      (f"CUS-{i:04d}", f"k{i}", f"Customer {i}", f"05{i:08d}", "Ramallah", db.now_iso(),
                       json.dumps({"name": f"Customer {i}", "pad": "x" * 200})))
        for i in range(orders):
            c.execute("INSERT INTO orders (order_code, customer_phone, status, business_id, created_at, data_json) "
                      "VALUES (?,?,?,1,?,?)", (f"OTL-{i:04d}", "0500000000", "requested", db.now_iso(), "{}"))
        for i in range(settings):
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                      (f"pad:{i}", json.dumps("x" * 150)))
        c.execute("INSERT INTO gaash_accounts (id, email, app_password, added_at) VALUES ('acct_1','a@x.com','pw',?)",
                  (db.now_iso(),))
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    for ext in ("-wal", "-shm"):
        Path(str(path) + ext).unlink(missing_ok=True)
    return Path(path)


def smash_page(path, pageno):
    raw = sqlite3.connect(path)
    psize = raw.execute("PRAGMA page_size").fetchone()[0]
    raw.close()
    p = Path(path)
    buf = bytearray(p.read_bytes())
    off = (pageno - 1) * psize
    buf[off:off + psize] = b"\xde\xad\xbe\xef" * (psize // 4)
    p.write_bytes(bytes(buf))
    for ext in ("-wal", "-shm"):
        Path(str(path) + ext).unlink(missing_ok=True)


def root_of(path, table):
    raw = sqlite3.connect(path)
    r = raw.execute("SELECT rootpage FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
    raw.close()
    return r


def leaf_of(path, table):
    """A non-root leaf page of `table` via dbstat, or None when the build lacks it."""
    raw = sqlite3.connect(path)
    try:
        rows = raw.execute("SELECT pageno FROM dbstat WHERE name=? AND pagetype='leaf' ORDER BY pageno",
                           (table,)).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        raw.close()
    root = root_of(path, table)
    leaves = [r[0] for r in rows if r[0] != root]
    return leaves[len(leaves) // 2] if leaves else None


def count(path, table):
    raw = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        return raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        raw.close()


# ─── the rebuild ──────────────────────────────────────────────────────────────
def a_destroyed_root_costs_that_table_and_nothing_else():
    d = fresh_dir("root")
    live = make_db(d / "otlobly.db")
    smash_page(live, root_of(live, "settings"))
    check("fixture: quick_check names the damage", dbrepair.check(live) != "ok")
    ok, rep = dbrepair.build(live, d / "otlobly.db.rebuild-t", "test")
    check("rebuild succeeds", ok)
    check("rebuilt file passes integrity_check", dbrepair.check(d / "otlobly.db.rebuild-t", full=True) == "ok")
    check("every customer survived", count(d / "otlobly.db.rebuild-t", "customers") == 300)
    check("every order survived", count(d / "otlobly.db.rebuild-t", "orders") == 5)
    check("the mailbox survived", count(d / "otlobly.db.rebuild-t", "gaash_accounts") == 1)
    st = rep["tables"]["settings"]
    check("settings is reported honestly as unrecoverable, not silently", st["strategy"].startswith("C") and st["dst"] == 0)
    check("the damaged file itself was not modified", dbrepair.check(live) != "ok")


def a_smashed_leaf_loses_only_its_rows():
    d = fresh_dir("leaf")
    live = make_db(d / "otlobly.db")
    leaf = leaf_of(live, "customers")
    if leaf is None:
        print("  --  (dbstat unavailable in this SQLite build — leaf test skipped)")
        return
    snap = d / "otlobly.snapshot-20260905-120000.db"         # what the sentinel writes hourly
    shutil.copy2(live, snap)
    smash_page(live, leaf)
    ok, rep = dbrepair.build(live, d / "otlobly.db.rebuild-t", "test")
    check("rebuild succeeds past a dead leaf page", ok)
    n = count(d / "otlobly.db.rebuild-t", "customers")
    check(f"most customers recovered around the dead page ({n}/300) — snapshot not yet trusted (no .ok)", 200 < n < 300)
    check("row-by-row / index paths were used", rep["tables"]["customers"]["strategy"] in ("B", "C"))
    check("integrity ok", dbrepair.check(d / "otlobly.db.rebuild-t", full=True) == "ok")
    snap.with_name(snap.name + ".ok").write_text("ok")          # now it is a VERIFIED snapshot
    ok, rep = dbrepair.build(live, d / "otlobly.db.rebuild-u", "test")
    n2 = count(d / "otlobly.db.rebuild-u", "customers")
    filled = rep["tables"]["customers"].get("from_snapshot", 0)
    check(f"the holes are filled from the verified snapshot ({n2}/300, +{filled})", ok and n2 == 300 and filled == 300 - n)
    check("today's rows were kept, not replaced (orders untouched by the fill)",
          rep["tables"]["orders"].get("from_snapshot", 0) == 0)


def uncheckpointed_wal_frames_are_kept():
    """immutable=1 would silently drop the newest commits. Prove they are read."""
    d = fresh_dir("wal")
    live = make_db(d / "otlobly.db", orders=1)
    code = (f"import sqlite3, os; c = sqlite3.connect({str(live)!r}); c.execute('PRAGMA journal_mode=WAL');"
            "c.execute('PRAGMA wal_autocheckpoint=0');"
            "c.execute(\"INSERT INTO orders (order_code, customer_phone, status, business_id, created_at, data_json) "
            "VALUES ('OTL-9999','0','requested',1,'now','{}')\"); c.commit(); os._exit(0)")
    subprocess.run([PY, "-c", code], check=True)
    check("fixture: a -wal is left behind", (d / "otlobly.db-wal").exists())
    ok, rep = dbrepair.build(live, d / "otlobly.db.rebuild-t", "test")
    check("rebuild ok", ok)
    raw = sqlite3.connect(d / "otlobly.db.rebuild-t")
    got = raw.execute("SELECT COUNT(*) FROM orders WHERE order_code='OTL-9999'").fetchone()[0]
    raw.close()
    check("the un-checkpointed commit is in the rebuilt file", got == 1)


def preflight_repairs_then_does_nothing_twice():
    d = fresh_dir("pre")
    live = make_db(d / "otlobly.db")
    smash_page(live, root_of(live, "settings"))
    db.repair_marker_path().write_text('{"verdict":"test"}')
    before = live.stat().st_ino
    rc = dbrepair.preflight()
    check("preflight exits 0", rc == 0)
    check("live file now passes integrity_check", dbrepair.check(live, full=True) == "ok")
    check("live file is a NEW inode (os.replace), never edited in place", live.stat().st_ino != before)
    ev = sorted(d.glob("otlobly.db.corrupt-*"))
    check("damaged file kept as evidence", any(not p.name.endswith(".report.json") for p in ev))
    check("a report sits next to it", any(p.name.endswith(".report.json") for p in ev))
    check("the request marker was consumed", not db.repair_marker_path().exists())
    h = json.loads(db.health_path().read_text())
    check("health.json says ok", h.get("ok") is True and not h.get("repairing"))
    check("the old order codes are intact (never an empty schema)", count(live, "orders") == 5)
    snapshot = sorted(p.name for p in d.iterdir())
    rc2 = dbrepair.preflight()
    check("second preflight exits 0", rc2 == 0)
    check("and creates nothing", sorted(p.name for p in d.iterdir()) == snapshot)


def the_budget_stops_the_third_rebuild():
    d = fresh_dir("budget")
    live = make_db(d / "otlobly.db")
    smash_page(live, root_of(live, "settings"))
    dbrepair.repairs_log_path().write_text(json.dumps({"rebuilds": [time.time() - 100, time.time() - 50]}))
    before = live.read_bytes()
    rc = dbrepair.preflight()
    check("preflight reports failure", rc == 1)
    check("the live file was left exactly as it was", live.read_bytes() == before)
    check("maintenance marker set — humans only from here", db.maintenance_marker_path().exists())
    h = json.loads(db.health_path().read_text())
    check("health.json says not ok + maintenance", h.get("ok") is False and h.get("maintenance") is True)
    # and a worker meeting the corruption now does NOT file another request (no restart loop)
    os.environ.pop("OTLOBLY_DBREPAIR", None)
    try:
        db._reported["done"] = False
        db._last_verdict.update(t=0.0, v="ok")
        db.report_corruption(sqlite3.DatabaseError("database disk image is malformed"))
        check("no repair request while in maintenance", not db.repair_marker_path().exists())
    finally:
        os.environ["OTLOBLY_DBREPAIR"] = "1"


def a_rebuild_that_would_lose_orders_is_refused():
    """A destroyed `orders` root with no snapshot → nothing can bring the rows back →
    the rebuild is NOT applied (an empty orders table restarts numbering at OTL-0001)."""
    d = fresh_dir("refuse")
    live = make_db(d / "otlobly.db")
    snap = d / "otlobly.snapshot-20260905-120000.db"
    shutil.copy2(live, snap)                                  # not yet verified (no .ok)
    smash_page(live, root_of(live, "orders"))
    before = live.read_bytes()
    rc = dbrepair.preflight()
    check("preflight refuses", rc == 1)
    check("the live file is untouched", live.read_bytes() == before)
    check("no rebuild file left behind", not list(d.glob("otlobly.db.rebuild-*")))
    check("maintenance marker set", db.maintenance_marker_path().exists())
    h = json.loads(db.health_path().read_text())
    check("health says so", h.get("ok") is False and h.get("maintenance") is True)
    # with a VERIFIED snapshot the holes are filled and the same rebuild is accepted
    snap.with_name(snap.name + ".ok").write_text("ok")
    dbrepair.repairs_log_path().unlink(missing_ok=True)
    rc = dbrepair.preflight()
    check("with a verified snapshot the rebuild is accepted", rc == 0)
    check("orders are back in full", count(live, "orders") == 5)
    check("integrity ok", dbrepair.check(live, full=True) == "ok")
    check("maintenance marker cleared", not db.maintenance_marker_path().exists())


def a_non_core_table_is_filled_from_the_snapshot_too():
    """gaash_threads / gaash_ids are not money, but losing them silently while an
    hour-old verified snapshot holds every row is not acceptable either."""
    d = fresh_dir("noncore")
    live = make_db(d / "otlobly.db", orders=2, customers=5, settings=5)
    with db.connect() as c:
        for i in range(120):
            c.execute("INSERT INTO gaash_ids (id, name, filename, uploaded_at) VALUES (?,?,?,?)",
                      (f"id_{i:04d}", f"Passport {i} " + "x" * 120, f"p{i}.pdf", db.now_iso()))
    raw = sqlite3.connect(live)
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    for ext in ("-wal", "-shm"):
        Path(str(live) + ext).unlink(missing_ok=True)
    snap = d / "otlobly.snapshot-20260905-120000.db"
    shutil.copy2(live, snap)
    snap.with_name(snap.name + ".ok").write_text("ok")
    smash_page(live, root_of(live, "gaash_ids"))
    use(live)
    rc = dbrepair.preflight()
    check("preflight repairs", rc == 0)
    check("all 120 ID documents are back from the verified snapshot", count(live, "gaash_ids") == 120)
    rep = json.loads(sorted(d.glob("otlobly.db.corrupt-*.report.json"))[-1].read_text())
    check("the report says where they came from",
          rep["tables"]["gaash_ids"].get("from_snapshot") == 120 and "+snapshot" in rep["tables"]["gaash_ids"]["strategy"])


def a_staged_restore_is_applied_by_preflight_only():
    """/api/restore stages otlobly.db.pending-restore; the swap happens here, with no
    worker alive. A pending file that fails integrity is rejected, live untouched."""
    d = fresh_dir("pending")
    live = make_db(d / "otlobly.db", orders=3)
    good = make_db(d / "good.db", orders=9)
    use(live)
    pend = d / "otlobly.db.pending-restore"
    shutil.copy2(good, pend)
    rc = dbrepair.preflight()
    check("preflight applies the staged file", rc == 0 and count(live, "orders") == 9)
    check("the previous live file is kept as .pre-restore-<ts>",
          any(p.name.startswith("otlobly.db.pre-restore-") and not p.name.endswith("-wal")
              for p in d.iterdir()))
    check("the pending file is consumed", not pend.exists())
    check("integrity ok", dbrepair.check(live, full=True) == "ok")
    # a damaged upload
    bad = make_db(d / "bad.db", orders=2)
    smash_page(bad, root_of(bad, "orders"))
    shutil.copy2(bad, pend)
    use(live)                                   # make_db re-pointed db.DB_FILE at bad.db
    before = live.read_bytes()
    rc = dbrepair.preflight()
    check("a corrupt pending file is rejected, live untouched", rc == 0 and live.read_bytes() == before)
    check("…and kept as .pre-restore-rejected-<ts>",
          any(p.name.startswith("otlobly.db.pre-restore-rejected-") for p in d.iterdir()))
    check("the pending slot is free again", not pend.exists())


def a_build_that_never_swapped_is_harmless():
    d = fresh_dir("crash")
    live = make_db(d / "otlobly.db")
    before = live.read_bytes()
    ok, _ = dbrepair.build(live, d / "otlobly.db.rebuild-20000101-000000", "test")
    check("build ok", ok)
    check("live untouched until the swap", live.read_bytes() == before)
    old = time.time() - 7200
    for p in d.glob("otlobly.db.rebuild-*"):
        os.utime(p, (old, old))
    dbrepair.housekeeping()
    check("a stale leftover is cleaned on the next preflight", not list(d.glob("otlobly.db.rebuild-*")))


# ─── the worker side ──────────────────────────────────────────────────────────
def report_corruption_files_one_marker_and_ignores_locks():
    d = fresh_dir("report")
    live = make_db(d / "otlobly.db")
    os.environ.pop("OTLOBLY_DBREPAIR", None)
    try:
        use(live)
        db.report_corruption(sqlite3.OperationalError("database is locked"))
        check("a lock files nothing", not db.repair_marker_path().exists())
        db.report_corruption(sqlite3.DatabaseError("database disk image is malformed"))
        check("a 'malformed' on a HEALTHY file files nothing (quick_check is the judge)",
              not db.repair_marker_path().exists())
        smash_page(live, root_of(live, "settings"))
        use(live)
        db.report_corruption(sqlite3.OperationalError("disk I/O error"))
        check("a disk I/O error is not a repair trigger", not db.repair_marker_path().exists())
        db.report_corruption(sqlite3.DatabaseError("database disk image is malformed"))
        check("real corruption files the request", db.repair_marker_path().exists())
        body = json.loads(db.repair_marker_path().read_text())
        check("the marker carries the verdict", "settings" in body.get("verdict", "").lower()
              or body.get("verdict"))
        h = json.loads(db.health_path().read_text())
        check("health.json flips to repairing", h.get("ok") is False and h.get("repairing") is True)
        m = db.repair_marker_path().stat().st_mtime
        db._reported["done"] = False
        db.report_corruption(sqlite3.DatabaseError("database disk image is malformed"))
        check("a second report (other worker) is a no-op", db.repair_marker_path().stat().st_mtime == m)
        check("the live file was never renamed or replaced",
              live.exists() and not list(d.glob("otlobly.db.corrupt-*")))
    finally:
        os.environ["OTLOBLY_DBREPAIR"] = "1"


def corruption_that_keeps_being_detected_always_reaches_a_repair_request():
    """2026-09-12, 09:42 → 19:26 UTC. The live database was corrupt for nine and a
    half hours. The sentinel noticed every single time — ~57 quick_check failures,
    all logged — and not one repair was ever requested, with not one line saying
    why: `_reported["done"]`, set at 01:21 UTC, is per-process and was permanent,
    and all three early returns in request_repair were a bare `return False`.

    What is pinned here: no pass is ever mute, an unconsumed request is re-filed
    instead of dead-ending, and a repair the master DOES apply re-arms the latch so
    the next fault is treated as a new event."""
    d = fresh_dir("wedge")
    live = make_db(d / "otlobly.db")
    smash_page(live, root_of(live, "settings"))
    os.environ.pop("OTLOBLY_DBREPAIR", None)
    import db_sentinel
    retry_was, cap_was = db.REPAIR_RETRY_S, db.REPAIR_MAX_REQUESTS
    try:
        use(live)
        db_sentinel._fail["n"] = 0
        db.REPAIR_RETRY_S = 10_000               # no retry window is open yet

        def a_pass():
            """One sentinel 10-minute pass; returns everything it printed."""
            db._quiet.clear()                    # simulate the throttle window elapsing
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                db_sentinel.check_once()
            return buf.getvalue()

        out = a_pass()
        check("the first pass files the request", db.repair_marker_path().exists()
              and "REPAIR requested" in out)
        h = json.loads(db.health_path().read_text())
        check("health.json flips to repairing, and stamps since when",
              h.get("ok") is False and h.get("repairing") is True and h.get("unwell_since"))

        out = a_pass()                           # the master never forked: marker still there
        check("a refused pass NAMES the guard that refused it",
              "repair NOT requested" in out and "already requested" in out)

        # nine and a half hours, ten minutes at a time
        mute, filed = 0, 0
        for i in range(57):
            out = a_pass()
            if not out.strip():
                mute += 1
            if "REPAIR requested" in out or "REPAIR RE-requested" in out:
                filed += 1
            if i == 9:
                db.REPAIR_RETRY_S = 0            # the retry window opens
        check(f"not one of 57 passes is mute ({mute} were)", mute == 0)
        check(f"an unconsumed request gets re-filed, not dead-ended ({filed} re-filings)",
              filed >= 1)
        check("…and it stops re-filing instead of churning workers for ever",
              filed <= db.REPAIR_MAX_REQUESTS)
        body = json.loads(db.repair_marker_path().read_text())
        check("the marker counts the unapplied attempts (evidence for a human)",
              int(body.get("attempts") or 1) > 1)
        h = json.loads(db.health_path().read_text())
        check("once it gives up, health.json says broken with NOBODY repairing "
              "(the banner's third state)",
              h.get("ok") is False and h.get("repairing") is False
              and h.get("maintenance") is False)

        # the master repairs (04:52) — then a NEW corruption hours later (09:42)
        db.repair_marker_path().unlink()
        db.REPAIR_RETRY_S = 10_000
        out = a_pass()
        check("after a repair is applied, a NEW fault files a NEW request",
              "latch re-armed" in out and "REPAIR requested" in out
              and db.repair_marker_path().exists())

        # the other two guards are allowed to refuse — never in silence
        db.repair_marker_path().unlink()
        use(live)
        os.environ["OTLOBLY_DBREPAIR"] = "1"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = db.request_repair("malformed", "in the repair subprocess")
        check("the repair-subprocess guard refuses out loud",
              r is False and "NOT requested" in buf.getvalue())
        os.environ.pop("OTLOBLY_DBREPAIR", None)
        db._quiet.clear()
        db.maintenance_marker_path().write_text(json.dumps({"at": db.now_iso(),
                                                            "reason": "test"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = db.request_repair("malformed", "humans only")
        check("the maintenance guard refuses out loud",
              r is False and "maintenance marker present" in buf.getvalue())
        db.maintenance_marker_path().unlink()
        since = json.loads(db.health_path().read_text()).get("unwell_since")
        db.write_health({"ok": False, "error": "still broken", "at": db.now_iso(),
                         "repairing": False})
        check("the unwell clock starts on the ok→broken edge and never restarts",
              json.loads(db.health_path().read_text()).get("unwell_since") == since)
        check("through all of it the live file was never renamed or replaced",
              live.exists() and not list(d.glob("otlobly.db.corrupt-*"))
              and not list(d.glob("otlobly.db.pre-restore-*")))
    finally:
        db.REPAIR_RETRY_S, db.REPAIR_MAX_REQUESTS = retry_was, cap_was
        db_sentinel._fail["n"] = 0
        os.environ["OTLOBLY_DBREPAIR"] = "1"


def the_guarded_connection_reports_by_itself():
    d = fresh_dir("guard")
    live = make_db(d / "otlobly.db")
    smash_page(live, root_of(live, "settings"))
    os.environ.pop("OTLOBLY_DBREPAIR", None)
    try:
        use(live)
        raised = False
        try:
            with db.connect() as c:
                c.execute("SELECT * FROM settings").fetchall()
        except sqlite3.DatabaseError:
            raised = True
        check("the error still propagates to the caller", raised)
        check("…and the repair request was filed on the way out", db.repair_marker_path().exists())
        # iteration path too (for row in cursor)
        db.repair_marker_path().unlink()
        db._reported["done"] = False
        db._last_verdict.update(t=0.0, v="ok")
        raised = False
        try:
            with db.connect() as c:
                for _ in c.execute("SELECT * FROM settings"):
                    pass
        except sqlite3.DatabaseError:
            raised = True
        check("iterating a cursor reports as well", raised and db.repair_marker_path().exists())
        check("claim_once answers False, never raises", db.claim_once("boot:x") is False)
        check("init_db() does not raise on the corrupt file", (db.init_db() or True))
    finally:
        os.environ["OTLOBLY_DBREPAIR"] = "1"


def the_api_answers_json_503():
    d = fresh_dir("api")
    live = make_db(d / "otlobly.db")
    os.environ["OTLOBLY_DB"] = str(live)
    use(live)
    import app as app_mod

    def boom():
        raise sqlite3.DatabaseError("database disk image is malformed")
    app_mod.app.add_url_rule("/api/_boom", "boom", boom)       # before the first request
    app_mod.app.add_url_rule("/_boom", "boom_html", boom)
    cl = app_mod.app.test_client()
    r = cl.get("/api/health/db")
    body = r.get_json() or {}
    check("healthy probe: 200 + sqlite_version", r.status_code == 200 and body.get("sqlite_version"))
    r = cl.get("/api/_boom")
    body = r.get_json() or {}
    check("an API route hitting corruption answers JSON 503 with db_error",
          r.status_code == 503 and body.get("db_error") is True and body.get("retry_in"))
    r = cl.get("/_boom")
    check("a page route answers a plain 503, not JSON", r.status_code == 503 and b"repaired" in r.data)


def the_real_fixture_if_present():
    src = os.environ.get("OTLOBLY_CORRUPT_FIXTURE")
    if not src or not Path(src).exists():
        print("  --  (OTLOBLY_CORRUPT_FIXTURE not set — real-file rebuild skipped)")
        return
    d = fresh_dir("real")
    live = d / "otlobly.db"
    shutil.copy2(src, live)
    use(live)
    v = dbrepair.check(live)
    check(f"fixture is corrupt ({v[:60]!r})", v != "ok")
    t0 = time.time()
    ok, rep = dbrepair.build(live, d / "otlobly.db.rebuild-t", "fixture")
    dt = time.time() - t0
    check(f"rebuild ok in {dt:.1f}s", ok)
    out = d / "otlobly.db.rebuild-t"
    check("integrity ok", dbrepair.check(out, full=True) == "ok")
    for t, n in (("orders", 41), ("customers", 49), ("payments", 5), ("users", 4), ("gaash_accounts", 3)):
        check(f"{t} == {n}", count(out, t) == n)
    # 714 at 03:30 − 24 rows on the destroyed page range (ids 1981–2004, all in the
    # 03:30 backup, i.e. what the hourly snapshot fill recovers) + 11 created today
    lx = count(out, "leluxe_orders")
    check(f"leluxe_orders: every reachable row kept ({lx} = 714 − 24 unreachable + 11 new today)", lx == 701)
    print("      " + dbrepair.summary_line(rep))
    for t, r in rep["tables"].items():
        if r.get("strategy") not in ("A", "absent in source"):
            print(f"      {t}: {r}")


def main():
    print("a destroyed root:");        a_destroyed_root_costs_that_table_and_nothing_else()
    print("a smashed leaf:");          a_smashed_leaf_loses_only_its_rows()
    print("WAL frames:");              uncheckpointed_wal_frames_are_kept()
    print("preflight:");               preflight_repairs_then_does_nothing_twice()
    print("the budget:");              the_budget_stops_the_third_rebuild()
    print("refusing a lossy rebuild:"); a_rebuild_that_would_lose_orders_is_refused()
    print("non-core fill:");           a_non_core_table_is_filled_from_the_snapshot_too()
    print("staged restore:");          a_staged_restore_is_applied_by_preflight_only()
    print("build without swap:");      a_build_that_never_swapped_is_harmless()
    print("report_corruption:");       report_corruption_files_one_marker_and_ignores_locks()
    print("the 9½-hour wedge:");       corruption_that_keeps_being_detected_always_reaches_a_repair_request()
    print("the guarded connection:");  the_guarded_connection_reports_by_itself()
    print("the API answer:");          the_api_answers_json_503()
    print("the real fixture:");        the_real_fixture_if_present()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all db-repair checks passed ✓")


if __name__ == "__main__":
    main()
