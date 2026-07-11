#!/usr/bin/env python3
"""
Concurrency self-check for the order-code race fix (audit finding B-1).

Proves that db.insert_new_order() never silently overwrites an order under
concurrent creation, and contrasts it with the OLD path (next_order_code() then
upsert_order() with ON CONFLICT DO UPDATE), which loses orders when two creators
grab the same OTL-#### code.

    ./.venv/bin/python test_order_code_race.py

Exit status is 0 only when the NEW path keeps every order (unique codes, no loss).
Uses a throwaway temp DB — never touches real data.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

# Point db.py at a temp file BEFORE importing it (DB_FILE is read at import).
_TMP = Path(tempfile.mkdtemp(prefix="otlobly-race-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "race.db")

import db  # noqa: E402

N_THREADS = 8
PER_THREAD = 15
TOTAL = N_THREADS * PER_THREAD


def _mk(marker):
    """A minimal store-shaped new order carrying a unique marker."""
    return {
        "order_id": "PENDING",
        "status": "REQUESTED",
        "customer": {"name": f"cust-{marker}", "phones": [{"e164": f"+9705{marker:07d}"}]},
        "items": [{"asin": None, "clean_url": None}],
        "signature": f"sig-{marker}",
        "created_at": db.now_iso(),
        "marker": marker,
    }


def _run_phase(dbfile, creator):
    """Reset dbfile, then have N_THREADS each create PER_THREAD orders at once."""
    for f in (dbfile, dbfile + "-wal", dbfile + "-shm"):
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass
    db.DB_FILE = Path(dbfile)
    db.init_db()
    barrier = threading.Barrier(N_THREADS)

    def worker(base):
        barrier.wait()                     # release all threads together → max contention
        for i in range(PER_THREAD):
            creator(_mk(base * PER_THREAD + i))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with db.connect() as c:
        rows = [json.loads(r["data_json"]) for r in c.execute("SELECT data_json FROM orders")]
    return rows


def _new(o):
    db.insert_new_order(o)


def _old(o):
    """The pre-fix path: allocate, then upsert — with the allocate→insert window
    widened so the race the audit describes actually triggers."""
    o["order_id"] = db.next_order_code()
    time.sleep(0.003)
    db.upsert_order(o)


def main():
    new_rows = _run_phase(str(_TMP / "new.db"), _new)
    old_rows = _run_phase(str(_TMP / "old.db"), _old)

    new_codes = {r["order_id"] for r in new_rows}
    new_markers = {r["marker"] for r in new_rows}
    old_codes = {r["order_id"] for r in old_rows}

    new_ok = (len(new_rows) == TOTAL
              and len(new_codes) == TOTAL
              and new_markers == set(range(TOTAL)))

    print(f"expected orders          : {TOTAL}")
    print(f"NEW insert_new_order     : rows={len(new_rows):3d}  distinct_codes={len(new_codes):3d}  "
          f"markers_kept={len(new_markers):3d}   -> {'OK — no overwrite' if new_ok else 'FAIL'}")
    print(f"OLD next_code+upsert     : rows={len(old_rows):3d}  distinct_codes={len(old_codes):3d}  "
          f"                lost={TOTAL - len(old_rows)}"
          + ("   (demonstrates the silent-overwrite bug the fix removes)"
             if len(old_rows) < TOTAL else "   (no loss this run — race is timing-dependent)"))

    print("\nRESULT:", "PASS" if new_ok else "FAIL")
    return 0 if new_ok else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)
