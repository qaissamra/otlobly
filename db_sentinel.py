#!/usr/bin/env python3
"""
db_sentinel.py — notices a rotting database in minutes, and keeps an hour-old
verified copy on the disk so the repair loses nothing.

WHY. The 2026-09-04 corruption sat unnoticed for three hours behind an always-200
/healthz; the 2026-09-05 one (leluxe_orders) was found by a person opening a page.
And every rebuild so far could only recover what the damaged file still held —
24 rows on a destroyed page were simply gone, although a 12-hour-old backup on a
Mac had them. This thread fixes both, from inside the app:

  every 10 min  PRAGMA quick_check(1) on the live file. A structural verdict →
                db.request_repair → the master rebuilds (see gunicorn.conf.py).
  every hour    conn.backup() → otlobly.snapshot-<YYYYmmdd-HH>.db, then a FULL
                PRAGMA integrity_check on the SNAPSHOT (off the hot file; catches
                index↔table mismatches quick_check skips). Passes → a `.ok` sidecar,
                and dbrepair.py fills holes in a rebuilt core table from it (≤1 h
                loss instead of "gone"). Fails → the LIVE file is already bad →
                request_repair. Newest 3 verified snapshots kept (~30 MB each).

Runs in every gunicorn worker; the snapshot for a given hour is claimed with an
O_EXCL `.part` file, so two workers never write the same one. Never raises out of
its loop. Intervals are env-tunable for tests (DB_SENTINEL_CHECK_S, DB_SENTINEL_SNAPSHOT_S).
"""
import os
import random
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import db

CHECK_S = int(os.environ.get("DB_SENTINEL_CHECK_S", "600"))
SNAPSHOT_S = int(os.environ.get("DB_SENTINEL_SNAPSHOT_S", "3600"))
KEEP = 3
_started = False


def _snap_dir():
    return Path(db.DB_FILE).parent


def _snap_stem():
    return Path(db.DB_FILE).stem                 # "otlobly" → otlobly.snapshot-*.db


def snapshots(ok_only=False):
    out = sorted(_snap_dir().glob(f"{_snap_stem()}.snapshot-*.db"), reverse=True)
    if ok_only:
        out = [p for p in out if p.with_name(p.name + ".ok").exists()]
    return out


def quick_verdict():
    """quick_check(1) on a PLAIN connection (a probe must never file a repair by itself)."""
    try:
        raw = sqlite3.connect(db.DB_FILE, timeout=5)
        try:
            return str((raw.execute("PRAGMA quick_check(1)").fetchone() or ["unknown"])[0])
        finally:
            raw.close()
    except sqlite3.DatabaseError as e:
        return str(e)


def _structural(verdict):
    v = (verdict or "").lower()
    return v.strip() != "ok" and "locked" not in v and "busy" not in v


def check_once():
    v = quick_verdict()
    if _structural(v):
        print(f"[sentinel] quick_check: {v[:120]}", flush=True)
        db.request_repair(v, "sentinel quick_check")
        return False
    if not db.repair_marker_path().exists() and not db.maintenance_marker_path().exists():
        try:
            db.write_health({"ok": True, "error": "", "at": db.now_iso(),
                             "repairing": False, "maintenance": False})
        except Exception as e:                   # noqa: BLE001
            print(f"[sentinel] health.json: {e}", flush=True)
    return True


def snapshot_once(bucket=None):
    """One verified snapshot for this hour. Returns the path, or None (someone else
    took it / the live file is unwell). Never raises."""
    bucket = bucket or datetime.now().strftime("%Y%m%d-%H")
    dst = _snap_dir() / f"{_snap_stem()}.snapshot-{bucket}.db"
    part = dst.with_name(dst.name + ".part")
    if dst.exists():
        return None
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
    except FileExistsError:
        return None                              # the other worker is on it
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        if _structural(quick_verdict()):
            return None                          # check_once will file the repair
        for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
            try:
                p.unlink()
            except OSError:
                pass
        src = sqlite3.connect(db.DB_FILE, timeout=30)
        out = sqlite3.connect(tmp)
        try:
            with out:
                src.backup(out, pages=512, sleep=0.01)
        finally:
            out.close()
            src.close()
        v = sqlite3.connect(f"file:{tmp}?mode=ro&immutable=1", uri=True, timeout=5)
        try:
            rows = v.execute("PRAGMA integrity_check").fetchall()
        finally:
            v.close()
        verdict = "\n".join(str(r[0]) for r in rows) or "unknown"
        if verdict.strip().lower() != "ok":
            print(f"[sentinel] SNAPSHOT FAILED integrity_check — the live file is damaged: "
                  f"{verdict[:160]}", flush=True)
            tmp.unlink(missing_ok=True)
            db.request_repair(verdict, "sentinel integrity_check on hourly snapshot")
            return None
        os.replace(tmp, dst)
        dst.with_name(dst.name + ".ok").write_text(db.now_iso())
        prune()
        print(f"[sentinel] verified snapshot {dst.name}", flush=True)
        return dst
    except Exception as e:                       # noqa: BLE001 — never out of the loop
        print(f"[sentinel] snapshot skipped: {e}", flush=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    finally:
        try:
            part.unlink()
        except OSError:
            pass


def prune(keep=KEEP):
    for p in snapshots()[keep:]:
        for x in (p, p.with_name(p.name + ".ok"), p.with_name(p.name + ".part")):
            try:
                x.unlink()
            except OSError:
                pass


def _loop():
    time.sleep(45 + random.uniform(0, 30))       # let the app finish booting first
    last_snap = 0.0
    while True:
        try:
            if check_once() and time.time() - last_snap >= SNAPSHOT_S:
                snapshot_once()
                last_snap = time.time()
        except Exception as e:                   # noqa: BLE001 — never let the thread die
            print(f"[sentinel] pass failed: {e}", flush=True)
        time.sleep(CHECK_S + random.uniform(0, 30))


def start():
    global _started
    if _started or os.environ.get("OTLOBLY_DBREPAIR") or os.environ.get("DB_SENTINEL_OFF"):
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-db-sentinel", daemon=True).start()
