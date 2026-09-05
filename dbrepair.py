#!/usr/bin/env python3
"""
dbrepair.py — the live SQLite file heals itself, with nobody watching.

WHY THIS EXISTS. otlobly.db on Render corrupted nine times between 2026-07-18 and
2026-09-05. No write path in this app was ever found to cause the first damage (the
disk is the suspect), but every event became a multi-hour outage because the app
kept writing into the broken file, repairs were done by hand on the LIVE, OPEN file
(ALTER/DROP/REINDEX on a malformed table, or swapping the file under running
workers), and nothing told anyone. This module is the whole answer to "fix it for
good": detect within minutes, rebuild a clean file from the damaged one with
near-zero loss, keep the damaged file as evidence, tell the owner once.

    python dbrepair.py preflight            # what gunicorn runs (master, no workers alive)
    python dbrepair.py --check  FILE        # quick_check + integrity_check verdicts, exit 1 if bad
    python dbrepair.py --rebuild FILE --out NEW   # rebuild FILE into NEW, no swap, print report

🛑 THE THREE RULES
1. It runs with ZERO connections open anywhere — gunicorn.conf.py calls it from the
   master, before sockets exist (boot) or after every worker has been reaped
   (runtime). It refuses to touch a database another process holds locked.
2. Build → verify → swap. The live name is touched exactly once, by os.replace, and
   only after the new file passed a full integrity_check. There is never a moment
   with no otlobly.db, and it NEVER starts an empty schema: _next_code() would
   restart order numbering at OTL-0001 and duplicate every customer's code.
3. The damaged file is evidence, not garbage: it is hard-linked to
   otlobly.db.corrupt-<ts> (WAL moved alongside), a .report.json is written next to
   it, and both are downloadable via /api/quarantined. Never `sqlite3 .recover` —
   it resurrects orphaned pages (2247 fake mailbox rows against 3 real, 2026-09-04).
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import db          # noqa: E402
import paths       # noqa: E402

CORE_TABLES = ("orders", "customers", "payments", "users", "businesses",
               "settings", "gaash_accounts", "leluxe_orders")
# Money and identity. A rebuild that loses more than a sliver of these is NOT
# applied: _next_code() would hand out order/customer codes that already exist on
# receipts and customs papers. leluxe_orders is a ClickUp mirror (a Pull refills it),
# settings/gaash_accounts are recoverable by hand — those may shrink, reported.
STRICT_TABLES = ("orders", "customers", "payments", "users", "businesses")
STRICT_TOLERANCE = 0.95
MAX_REBUILDS, WINDOW_S = 2, 3600           # the budget: 2 rebuilds per hour, then a human
KEEP_CORRUPT, CORRUPT_DAYS = 3, 14
KEEP_SNAPSHOTS = 3
PROBE_MISS_LIMIT, PROBE_SPAN = 2000, 10000


def log(msg):
    print(f"[dbrepair] {msg}", flush=True)


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def sidecars(p):
    p = Path(p)
    return p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")


# ─── verdicts ──────────────────────────────────────────────────────────────────
def _open(path, mode="rw", immutable=False, timeout=5):
    uri = f"file:{Path(path)}?mode={mode}" + ("&immutable=1" if immutable else "")
    c = sqlite3.connect(uri, uri=True, timeout=timeout)
    c.row_factory = None
    return c


def _how_to_open(path, prefer_rw=False):
    """A WAL database with no -shm cannot be opened mode=ro (SQLite must create the
    -shm → 'unable to open database file'); immutable=1 avoids that but IGNORES a
    -wal, i.e. the newest commits. So: a -wal present → read-write (recovers it);
    otherwise read-only immutable — nothing to recover, nothing gets written."""
    wal, _ = sidecars(path)
    if prefer_rw or wal.exists():
        return "rw", False
    return "ro", True


def check(path, full=False, prefer_rw=False):
    """'ok' or the first line(s) of what SQLite thinks is wrong. Never raises."""
    pragma = "integrity_check" if full else "quick_check(1)"
    mode, immutable = _how_to_open(path, prefer_rw)
    try:
        c = _open(path, mode=mode, immutable=immutable)
        try:
            rows = c.execute(f"PRAGMA {pragma}").fetchall()
        finally:
            c.close()
        out = "\n".join(str(r[0]) for r in rows) or "unknown"
        return "ok" if out.strip().lower() == "ok" else out[:600]
    except sqlite3.DatabaseError as e:
        return f"{type(e).__name__}: {e}"[:600]


def is_locked(verdict):
    v = (verdict or "").lower()
    return "locked" in v or "busy" in v


# ─── files on the data disk ───────────────────────────────────────────────────
def live_path():
    return Path(db.DB_FILE)


def marker_path():
    return db.repair_marker_path()


def maintenance_path():
    return db.maintenance_marker_path()


def repairs_log_path():
    lp = live_path()
    return lp.with_name(lp.name + ".repairs.json")


def snapshots(ok_only=True):
    """Hourly snapshots the sentinel (PR 2) writes: otlobly.snapshot-<ts>.db + .ok"""
    lp = live_path()
    stem = lp.stem                                  # "otlobly"
    out = []
    for p in sorted(lp.parent.glob(f"{stem}.snapshot-*.db"), reverse=True):
        if not ok_only or p.with_name(p.name + ".ok").exists():
            out.append(p)
    return out


def history_files():
    lp = live_path()
    return [p for p in lp.parent.glob(lp.name + ".*")
            if any(t in p.name for t in (".corrupt-", ".pre-restore-"))] + snapshots(False)


# ─── notifications (best effort, last step, never fatal) ─────────────────────
def notify(text):
    try:
        import telegram
        r = telegram.send(text)
        if not r.get("ok"):
            log(f"telegram skipped: {r.get('error')}")
    except Exception as e:                       # noqa: BLE001
        log(f"telegram skipped: {e}")


def write_health(ok, error="", **extra):
    d = {"ok": bool(ok), "error": (error or "")[:300], "at": db.now_iso(),
         "repairing": False, "maintenance": maintenance_path().exists()}
    d.update(extra)
    try:
        db.write_health(d)
    except Exception as e:                       # noqa: BLE001
        log(f"health.json not written: {e}")


# ─── budget ───────────────────────────────────────────────────────────────────
def _repairs():
    try:
        return json.loads(repairs_log_path().read_text())
    except (OSError, ValueError):
        return {"rebuilds": []}


def budget_left():
    now = time.time()
    recent = [t for t in _repairs().get("rebuilds", []) if now - float(t) < WINDOW_S]
    return MAX_REBUILDS - len(recent), len(recent)


def record_rebuild():
    d = _repairs()
    d.setdefault("rebuilds", []).append(time.time())
    d["rebuilds"] = d["rebuilds"][-20:]
    paths.write_json_atomic(repairs_log_path(), d)


# ─── the rebuild ──────────────────────────────────────────────────────────────
class Report(dict):
    def __init__(self, live, reason):
        super().__init__()
        self.update({"started": db.now_iso(), "live": str(live), "reason": reason,
                     "tables": {}, "sqlite_version": sqlite3.sqlite_version,
                     "python": sys.version.split()[0], "strategy_notes": []})

    def note(self, s):
        log(s)
        self["strategy_notes"].append(s)


def _table_meta(conn, schema, t):
    info = conn.execute(f"PRAGMA {schema}.table_info({q(t)})").fetchall()
    cols = [r[1] for r in info]
    pks = [r for r in info if r[5]]
    # rowid alias = exactly one INTEGER PRIMARY KEY column (rowid and id are the same column)
    alias = len(pks) == 1 and (pks[0][2] or "").upper() == "INTEGER"
    notnull = {r[1] for r in info if r[3] and r[4] is None}
    types = {r[1]: (r[2] or "").upper() for r in info}
    return cols, alias, notnull, types, {r[1] for r in pks}


def _shape_ok(row, cols, notnull, types, pks):
    """Reject a cell pattern that can only be a cross-linked page: a TEXT column
    holding a number, a NULL primary key, a NULL in a NOT NULL column."""
    for v, c in zip(row, cols):
        if c in pks and v is None:
            return False
        if c in notnull and v is None:
            return False
        if "TEXT" in types.get(c, "") and isinstance(v, (int, float)):
            return False
    return True


def _copy_table(conn, t, rep, snap_attached):
    r = rep["tables"][t] = {"src": None, "dst": 0, "dup": 0, "junk": 0, "mangled": 0,
                            "strategy": "A", "error": ""}
    try:
        cols_dst, alias, notnull, types, pks = _table_meta(conn, "main", t)
    except sqlite3.DatabaseError as e:
        r["error"] = f"dst meta: {e}"
        return
    try:
        cols_src = _table_meta(conn, "src", t)[0]
    except sqlite3.DatabaseError as e:
        r["error"] = f"unreadable table_info: {e}"
        r["strategy"] = "LOST"
        return
    cols = [c for c in cols_dst if c in cols_src]
    if not cols:
        r["error"] = "no common columns"
        return
    r["dropped_columns"] = [c for c in cols_src if c not in cols_dst]
    conn.execute(f"DELETE FROM main.{q(t)}")      # init_db seeds businesses row 1; live has 3
    try:
        r["src"] = conn.execute(f"SELECT COUNT(*) FROM src.{q(t)}").fetchone()[0]
    except sqlite3.DatabaseError:
        r["src"] = None
    sel_cols = ", ".join(q(c) for c in cols)
    ins_cols = sel_cols if alias else "rowid, " + sel_cols
    sel = sel_cols if alias else "rowid, " + sel_cols
    # ── Strategy A: one streaming INSERT…SELECT
    try:
        conn.execute("BEGIN")
        conn.execute(f"INSERT INTO main.{q(t)} ({ins_cols}) SELECT {sel} FROM src.{q(t)}")
        conn.execute("COMMIT")
        r["dst"] = conn.execute(f"SELECT COUNT(*) FROM main.{q(t)}").fetchone()[0]
        return
    except sqlite3.DatabaseError as e:
        conn.execute("ROLLBACK")
        conn.execute(f"DELETE FROM main.{q(t)}")
        r["strategy"] = "B"
        rep.note(f"{t}: bulk copy failed ({str(e)[:80]}) — row by row")
    # ── Strategy B: row by row, resuming past decode errors
    ins = f"INSERT INTO main.{q(t)} ({ins_cols}) VALUES ({', '.join('?' * (len(cols) + (0 if alias else 1)))})"
    seen = set()
    last = -1 << 62
    dead = False

    def insert_row(row):
        rid, vals = row[0], list(row[1:])
        if rid in seen:
            return
        if not _shape_ok(vals, cols, notnull, types, pks):
            r["junk"] += 1
            seen.add(rid)
            return
        try:
            conn.execute(ins, vals if alias else [rid] + vals)
            r["dst"] += 1
        except sqlite3.IntegrityError:
            r["dup"] += 1
        seen.add(rid)

    while not dead:
        try:
            cur = conn.execute(f"SELECT rowid, {sel_cols} FROM src.{q(t)} WHERE rowid > ? ORDER BY rowid", (last,))
            for row in cur:
                insert_row(row)
                last = row[0]
            break
        except sqlite3.OperationalError as e:
            if "decode" in str(e).lower():           # one poisoned text cell; fetch it raw
                try:
                    conn.text_factory = bytes
                    row = conn.execute(f"SELECT rowid, {sel_cols} FROM src.{q(t)} WHERE rowid > ? ORDER BY rowid LIMIT 1", (last,)).fetchone()
                finally:
                    conn.text_factory = str
                if row is None:
                    break
                row = tuple(v.decode("utf-8", "replace") if isinstance(v, bytes) else v for v in row)
                insert_row(row)
                r["mangled"] += 1
                last = row[0]
                continue
            rep.note(f"{t}: scan stopped after rowid {last}: {str(e)[:80]}")
            dead = True
        except sqlite3.DatabaseError as e:
            rep.note(f"{t}: scan stopped after rowid {last}: {str(e)[:80]}")
            dead = True
    if dead:
        # ── Strategy C: reach rows beyond the damaged page by other paths
        r["strategy"] = "C"
        # (0) walk the b-tree from the OTHER end: a descending scan reaches every
        #     row after the damaged cell that the ascending one never got to
        try:
            for row in conn.execute(f"SELECT rowid, {sel_cols} FROM src.{q(t)} WHERE rowid > ? ORDER BY rowid DESC", (last,)):
                insert_row(row)
        except sqlite3.DatabaseError as e:
            rep.note(f"{t}: reverse scan stopped: {str(e)[:80]}")
        upper = None
        try:
            row = conn.execute("SELECT seq FROM src.sqlite_sequence WHERE name=?", (t,)).fetchone()
            upper = int(row[0]) if row else None
        except sqlite3.DatabaseError:
            pass
        if upper is None:
            upper = max(last, 0) + PROBE_SPAN
        misses, rid = 0, max(last, 0)
        while rid < upper and misses < PROBE_MISS_LIMIT:
            rid += 1
            if rid in seen:
                continue
            try:
                row = conn.execute(f"SELECT rowid, {sel_cols} FROM src.{q(t)} WHERE rowid=?", (rid,)).fetchone()
            except sqlite3.DatabaseError:
                row = None
            if row is None:
                misses += 1
                continue
            misses = 0
            insert_row(row)
        # every intact index enumerates rowids the table b-tree can no longer walk
        try:
            idx = [x[0] for x in conn.execute(
                "SELECT name FROM src.sqlite_master WHERE type='index' AND tbl_name=?", (t,))]
        except sqlite3.DatabaseError:
            idx = []
        for name in idx:
            try:
                rids = [x[0] for x in conn.execute(f"SELECT rowid FROM src.{q(t)} INDEXED BY {q(name)}")]
            except sqlite3.DatabaseError:
                continue
            for rid in rids:
                if rid in seen:
                    continue
                try:
                    row = conn.execute(f"SELECT rowid, {sel_cols} FROM src.{q(t)} WHERE rowid=?", (rid,)).fetchone()
                except sqlite3.DatabaseError:
                    continue
                if row is not None:
                    insert_row(row)
    r["dst"] = conn.execute(f"SELECT COUNT(*) FROM main.{q(t)}").fetchone()[0]
    # a table that lost rows (or whose source count is unknowable) → the rows it is
    # MISSING, by primary key, from the newest verified snapshot (≤1 h old). EVERY
    # table, not just the core ones: on 2026-09-05 a destroyed gaash_threads root
    # (the clearance email threads) came back empty while the snapshot had all of
    # them. Never the whole table: today's rows stay, only holes are filled. A row
    # deleted since the snapshot can come back for an hour — reported, so the owner
    # knows (queues re-process idempotently; settings/audit rows are harmless).
    if snap_attached and (r["src"] is None or r["dst"] < r["src"]):
        try:
            snap_cols = _table_meta(conn, "snap", t)[0]
            cc = ", ".join(q(c) for c in cols_dst if c in snap_cols)
            before = r["dst"]
            conn.execute(f"INSERT OR IGNORE INTO main.{q(t)} ({cc}) SELECT {cc} FROM snap.{q(t)}")
            r["dst"] = conn.execute(f"SELECT COUNT(*) FROM main.{q(t)}").fetchone()[0]
            r["from_snapshot"] = r["dst"] - before
            if r["from_snapshot"]:
                r["strategy"] += "+snapshot"
                rep.note(f"{t}: {r['from_snapshot']} missing row(s) filled from the newest verified snapshot")
        except sqlite3.DatabaseError as e:
            rep.note(f"{t}: snapshot fill failed: {str(e)[:80]}")


def build(live, dst, reason):
    """Build a clean copy of `live` at `dst`. Returns (ok, report). Touches nothing else."""
    live, dst = Path(live), Path(dst)
    rep = Report(live, reason)
    for p in (dst, *sidecars(dst)):
        try:
            p.unlink()
        except OSError:
            pass
    # 1) the app's OWN schema, built fresh (every index rebuilt clean)
    saved = db.DB_FILE
    db.DB_FILE = dst
    try:
        db.init_db()
    finally:
        db.DB_FILE = saved
    conn = sqlite3.connect(f"file:{dst}", uri=True, isolation_level=None, timeout=30)
    conn.row_factory = None
    ok = False
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        # 2) the damaged file, read-only so the evidence is never modified; un-checkpointed
        #    WAL frames ARE read (immutable=1 would skip them — that is why it is not used)
        attached = False
        mode, immutable = _how_to_open(live)
        for uri in (f"file:{live}?mode={mode}" + ("&immutable=1" if immutable else ""),
                    f"file:{live}?mode=rw"):
            try:
                conn.execute("ATTACH DATABASE ? AS src", (uri,))
                conn.execute("SELECT name FROM src.sqlite_master LIMIT 1").fetchall()
                attached = True
                rep["source_open"] = uri.split("?", 1)[1]
                break
            except sqlite3.DatabaseError as e:
                rep.note(f"attach ({uri.split('?', 1)[1]}) failed: {str(e)[:100]}")
                try:
                    conn.execute("DETACH DATABASE src")
                except sqlite3.DatabaseError:
                    pass
        if not attached:
            rep["fatal"] = "header-level: the damaged file cannot be opened at all"
            return False, rep
        snap_attached = False
        snaps = snapshots()
        if snaps:
            try:
                conn.execute("ATTACH DATABASE ? AS snap", (f"file:{snaps[0]}?mode=ro&immutable=1",))
                snap_attached = True
                rep["snapshot"] = str(snaps[0])
            except sqlite3.DatabaseError as e:
                rep.note(f"snapshot attach failed: {str(e)[:80]}")
        # 3) tables: everything the source has ∪ everything the schema has
        try:
            src_tables = dict(conn.execute(
                "SELECT name, sql FROM src.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())
        except sqlite3.DatabaseError as e:
            rep["fatal"] = f"sqlite_master unreadable: {e}"
            return False, rep
        dst_tables = {x[0] for x in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        for t, sql in src_tables.items():
            if t not in dst_tables and sql:
                try:
                    conn.execute(sql)
                    for (isql,) in conn.execute(
                            "SELECT sql FROM src.sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (t,)):
                        try:
                            conn.execute(isql)
                        except sqlite3.DatabaseError:
                            pass
                    dst_tables.add(t)
                    rep.note(f"{t}: not in the app schema — recreated from the file's own DDL")
                except sqlite3.DatabaseError as e:
                    rep.note(f"{t}: could not recreate: {str(e)[:80]}")
        for t in sorted(dst_tables):
            if t not in src_tables:
                rep["tables"][t] = {"src": 0, "dst": 0, "strategy": "absent in source"}
                continue
            _copy_table(conn, t, rep, snap_attached)
        # 4) AUTOINCREMENT counters: never hand out an id a deleted row once had
        try:
            for name, seq in conn.execute("SELECT name, seq FROM src.sqlite_sequence").fetchall():
                if name not in dst_tables:
                    continue
                cur = conn.execute("UPDATE main.sqlite_sequence SET seq=max(seq, ?) WHERE name=?", (int(seq or 0), name))
                if cur.rowcount == 0:
                    conn.execute("INSERT INTO main.sqlite_sequence (name, seq) VALUES (?,?)", (name, int(seq or 0)))
        except sqlite3.DatabaseError as e:
            rep.note(f"sqlite_sequence: {str(e)[:80]}")
        for s in ("src", "snap"):
            try:
                conn.execute(f"DETACH DATABASE {s}")
            except sqlite3.DatabaseError:
                pass
        # 5) the new file must be perfect, or it is not used
        verdict = "\n".join(str(x[0]) for x in conn.execute("PRAGMA integrity_check").fetchall())
        rep["integrity_after"] = verdict[:600]
        if verdict.strip().lower() != "ok":
            rep["fatal"] = "rebuilt file failed integrity_check"
            return False, rep
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ok = True
    finally:
        conn.close()
        if not ok:
            for p in (dst, *sidecars(dst)):
                try:
                    p.unlink()
                except OSError:
                    pass
    for p in sidecars(dst):
        try:
            p.unlink()
        except OSError:
            pass
    rep["finished"] = db.now_iso()
    return True, rep


def swap_in(live, new, evidence_tag):
    """The ONLY step that touches the live name. Evidence first, then one os.replace."""
    live, new = Path(live), Path(new)
    ts = stamp()
    ev = live.with_name(f"{live.name}.{evidence_tag}-{ts}")
    if live.exists():
        try:
            os.link(live, ev)                     # instant, no window with a half copy
        except OSError:
            shutil.copy2(live, ev)
        wal, shm = sidecars(live)
        if wal.exists():
            os.replace(wal, ev.with_name(ev.name + "-wal"))
        try:
            shm.unlink()
        except OSError:
            pass
    wal, _ = sidecars(live)
    if wal.exists():                              # a stale WAL beside a new file IS corruption
        raise RuntimeError(f"refusing to swap: {wal.name} still present")
    os.replace(new, live)
    try:
        fd = os.open(str(live.parent), os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
    except OSError:
        pass
    return ev, ts


def summary_line(rep):
    core = []
    for t in CORE_TABLES:
        r = rep["tables"].get(t) or {}
        core.append(f"{t} {r.get('dst', '?')}" + (f"/{r['src']}" if r.get("src") is not None else "")
                    + (f" (+{r['from_snapshot']} from snapshot)" if r.get("from_snapshot") else ""))
    lost = [t for t, r in rep["tables"].items() if r.get("strategy") == "LOST"]
    filled = [f"{t} +{r['from_snapshot']}" for t, r in rep["tables"].items()
              if r.get("from_snapshot") and t not in CORE_TABLES]
    return (" · ".join(core) + (f" · LOST: {', '.join(lost)}" if lost else "")
            + (f" · from snapshot: {', '.join(filled)}" if filled else ""))


def unacceptable_losses(rep):
    """Which STRICT tables the rebuilt file is missing too much of (after any
    snapshot fill). Empty list = safe to swap in."""
    out = []
    for t in STRICT_TABLES:
        r = rep["tables"].get(t) or {}
        src, dst = r.get("src"), r.get("dst", 0)
        if src is None and dst == 0 and r.get("strategy") != "absent in source":
            out.append(f"all of {t} (source count unknown, nothing recovered)")
        elif src and dst < STRICT_TOLERANCE * src:
            out.append(f"{src - dst} of {src} {t}")
    return out


def rebuild_live(reason):
    """preflight's repair path. Returns True when the live file was replaced."""
    live = live_path()
    size = live.stat().st_size + sum(p.stat().st_size for p in sidecars(live) if p.exists())
    if shutil.disk_usage(live.parent).free < 3 * size:
        prune(aggressive=True)
        if shutil.disk_usage(live.parent).free < 3 * size:
            msg = f"🚨 Otlobly database is corrupt AND the disk is too full to rebuild ({size // 1048576} MB needed ×3)"
            log(msg)
            write_health(False, "corrupt; disk full", repairing=False)
            notify(msg + f"\n{reason[:200]}")
            return False
    left, used = budget_left()
    if left <= 0:
        maintenance_path().write_text(json.dumps({"at": db.now_iso(), "reason": reason[:300],
                                                  "why": f"{used} rebuilds in the last hour"}))
        msg = (f"🚨 Otlobly database corrupt AGAIN — {used} automatic rebuilds in the last hour, "
               f"so I stopped repairing and left the file alone. Something is wrong with the DISK. "
               f"The app answers 'database being repaired' until a human looks.\n{reason[:200]}")
        log(msg)
        write_health(False, reason, repairing=False, maintenance=True)
        notify(msg)
        return False
    dst = live.with_name(f"{live.name}.rebuild-{stamp()}")
    ok, rep = build(live, dst, reason)
    if ok:
        losses = unacceptable_losses(rep)
        if losses:
            for x in (dst, *sidecars(dst)):
                try:
                    x.unlink()
                except OSError:
                    pass
            rep["fatal"] = "rebuild refused: it would lose " + ", ".join(losses)
            ok = False
    if not ok:
        snaps = snapshots()
        if rep.get("fatal", "").startswith("header-level") and snaps:
            rep.note(f"restoring newest verified snapshot {snaps[0].name} (≤1 h old)")
            ev, ts = swap_in(live, _copy_snapshot(snaps[0]), "corrupt")
            _finish(rep, ev, ts, restored_from=str(snaps[0]))
            return True
        maintenance_path().write_text(json.dumps({"at": db.now_iso(), "reason": rep.get("fatal"),
                                                  "why": "rebuild failed"}))
        msg = (f"🚨 Otlobly database corrupt and the automatic rebuild was NOT applied: {rep.get('fatal')}\n"
               f"The damaged file is untouched; a human must restore a backup or salvage it "
               f"(/api/quarantined). The app stays up and answers 'database unavailable' for the "
               f"broken table.")
        log(msg)
        write_health(False, rep.get("fatal") or reason, maintenance=True)
        notify(msg)
        return False
    ev, ts = swap_in(live, dst, "corrupt")
    _finish(rep, ev, ts)
    return True


def _copy_snapshot(snap):
    tmp = live_path().with_name(f"{live_path().name}.rebuild-{stamp()}")
    shutil.copy2(snap, tmp)
    return tmp


def _finish(rep, evidence, ts, restored_from=None):
    live = live_path()
    record_rebuild()
    try:
        maintenance_path().unlink()
    except OSError:
        pass
    rep["evidence"] = evidence.name
    rep["restored_from"] = restored_from
    rep["verdict_after_swap"] = check(live)
    try:
        du = shutil.disk_usage(live.parent)
        rep["disk_free_mb"] = du.free // 1048576
    except OSError:
        pass
    report_path = evidence.with_name(evidence.name + ".report.json")
    paths.write_json_atomic(report_path, rep)
    line = summary_line(rep)
    log(f"REBUILT → {live.name} · evidence {evidence.name} · {line}")
    try:
        db.audit({"username": "dbrepair"}, "db_rebuild", "db", ts,
                 (f"from snapshot {Path(restored_from).name}; " if restored_from else "") + line)
    except Exception as e:                       # noqa: BLE001
        log(f"audit row skipped: {e}")
    write_health(rep["verdict_after_swap"] == "ok", "" if rep["verdict_after_swap"] == "ok" else rep["verdict_after_swap"],
                 last_repair=ts)
    suspicious = []
    for t in CORE_TABLES:
        r = rep["tables"].get(t) or {}
        if r.get("src") is not None and r.get("dst", 0) < r["src"]:
            suspicious.append(f"{t} {r['dst']}/{r['src']}")
    notify("🩹 Otlobly database was corrupt and REPAIRED itself · قاعدة البيانات تصلّحت تلقائياً\n"
           f"Why: {rep['reason'][:160]}\n"
           + (f"Restored the {Path(restored_from).name} snapshot (≤1 h old).\n" if restored_from else "")
           + f"Rows: {line}\n"
           + (f"⚠ fewer rows than the damaged file reported: {', '.join(suspicious)} — check /api/quarantined\n" if suspicious else "")
           + f"Evidence kept: {evidence.name} (+ .report.json)")


# ─── pending restore (staged by /api/restore; applied here, never under live workers) ──
def apply_pending_restore():
    live = live_path()
    pend = live.with_name(live.name + ".pending-restore")
    if not pend.exists():
        return
    for p in sidecars(pend):
        try:
            p.unlink()
        except OSError:
            pass
    verdict = check(pend, full=True)
    if verdict != "ok":
        bad = live.with_name(f"{live.name}.pre-restore-rejected-{stamp()}")
        os.replace(pend, bad)
        log(f"pending restore REJECTED ({verdict[:80]}) → {bad.name}")
        notify(f"⚠ Otlobly: the uploaded restore file failed integrity_check and was NOT applied:\n{verdict[:200]}")
        return
    ev, ts = swap_in(live, pend, "pre-restore")
    log(f"pending restore applied; previous file kept as {ev.name}")
    write_health(check(live) == "ok", last_restore=ts)
    notify(f"♻️ Otlobly: the uploaded database was restored (previous file kept as {ev.name}).")


# ─── housekeeping ─────────────────────────────────────────────────────────────
def housekeeping():
    live = live_path()
    cutoff = time.time() - 3600
    for pat in (".rebuild-*", ".incoming-*", ".acctsrc-*"):
        for p in live.parent.glob(live.name + pat):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    log(f"removed stale {p.name}")
            except OSError:
                pass


def prune(aggressive=False):
    live = live_path()
    keep_c = 1 if aggressive else KEEP_CORRUPT
    keep_s = 1 if aggressive else KEEP_SNAPSHOTS
    old = time.time() - CORRUPT_DAYS * 86400
    for tag in (".corrupt-", ".pre-restore-"):
        mains = sorted([p for p in live.parent.glob(f"{live.name}{tag}*")
                        if not p.name.endswith(("-wal", "-shm", ".report.json"))],
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in mains[keep_c:]:
            if aggressive or p.stat().st_mtime < old:
                for x in (p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm"),
                          p.with_name(p.name + ".report.json")):
                    try:
                        x.unlink()
                    except OSError:
                        pass
                log(f"pruned {p.name}")
    snaps = snapshots(ok_only=False)
    for p in snaps[keep_s:]:
        for x in (p, p.with_name(p.name + ".ok"), p.with_name(p.name + ".part")):
            try:
                x.unlink()
            except OSError:
                pass
        log(f"pruned {p.name}")


def maintenance(reason):
    maintenance_path().write_text(json.dumps({"at": db.now_iso(), "reason": reason[:300]}))
    write_health(False, reason, maintenance=True)
    msg = f"🚨 Otlobly database needs a human: {reason[:200]}\nThe app is up but answers 'database unavailable'."
    log(msg)
    notify(msg)


# ─── preflight — what gunicorn's master runs with no workers alive ───────────
def preflight():
    live = live_path()
    live.parent.mkdir(parents=True, exist_ok=True)
    # consume the request first, so a failing repair can never loop the master
    requested = None
    try:
        requested = marker_path().read_text()[:300]
        marker_path().unlink()
    except OSError:
        pass
    housekeeping()
    apply_pending_restore()
    if not live.exists():
        snaps = snapshots()
        if snaps:
            log(f"live file missing — restoring newest verified snapshot {snaps[0].name}")
            swap_in(live, _copy_snapshot(snaps[0]), "corrupt")
            record_rebuild()
            notify(f"♻️ Otlobly: otlobly.db was MISSING — restored the {snaps[0].name} snapshot (≤1 h old).")
        elif not history_files():
            log("first boot — creating the schema")
            db.init_db()
        else:
            maintenance("otlobly.db is missing and there is no verified snapshot to restore")
            return 2
    verdict = check(live, prefer_rw=True)
    if is_locked(verdict):
        log(f"another process holds the database ({verdict[:60]}) — leaving it alone")
        return 0
    if verdict == "ok":
        if requested:
            log(f"repair was requested ({requested[:80]}) but the file now reads clean — nothing to do")
        try:
            maintenance_path().unlink()
        except OSError:
            pass
        write_health(True)
        prune()
        return 0
    reason = verdict if not requested else f"{verdict} · requested: {requested}"
    log(f"CORRUPT: {verdict[:120]}")
    write_health(False, verdict, repairing=True)
    ok = rebuild_live(reason)
    prune()
    return 0 if ok else 1


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    # Tell db.report_corruption() to stay passive in THIS process: reading a corrupt
    # source raises on purpose here, and the repair must never request itself.
    # (Set here, not at import: app.py imports this module for check().)
    os.environ["OTLOBLY_DBREPAIR"] = "1"
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("cmd", nargs="?", default="preflight", choices=["preflight"])
    ap.add_argument("--check", metavar="FILE")
    ap.add_argument("--rebuild", metavar="FILE")
    ap.add_argument("--out", metavar="NEW")
    a = ap.parse_args()
    if a.check:
        qc, ic = check(a.check), check(a.check, full=True)
        print(f"quick_check:     {qc}\nintegrity_check: {ic}")
        return 0 if ic == "ok" else 1
    if a.rebuild:
        if not a.out:
            ap.error("--rebuild needs --out NEW")
        ok, rep = build(a.rebuild, a.out, reason=f"cli: {check(a.rebuild)[:100]}")
        print(json.dumps(rep, indent=1, ensure_ascii=False)[:6000])
        print(("OK → " if ok else "FAILED ") + a.out + " · " + summary_line(rep))
        return 0 if ok else 1
    return preflight()


if __name__ == "__main__":
    sys.exit(main())
