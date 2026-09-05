"""
gunicorn.conf.py — the master process repairs the database, because only the
master can guarantee that NO worker has it open.

gunicorn loads this file from the working directory on its own (no flag needed;
render.yaml passes -c explicitly anyway). Two hooks matter:

  on_starting  — master, before any socket exists or any worker is forked. Runs
                 `dbrepair.py preflight` in a SUBPROCESS: quick_check the live
                 file, rebuild it if it is corrupt, apply a staged restore, prune.
                 A deploy therefore repairs today's corruption before serving.
  pre_fork     — master, right before each worker fork. If a worker left a
                 `otlobly.db.repair-requested` marker (db.report_corruption), every
                 worker is stopped and reaped FIRST — zero processes hold the file —
                 then the same preflight runs, then gunicorn forks fresh workers.
                 Total blip ≈ 5 s + a few seconds of rebuild on a 30 MB file.

🛑 The master must never import db or open SQLite itself: an SQLite file
descriptor carried across fork() is on sqlite.org's list of ways to corrupt a
database (a child closing the inherited fd drops the process's POSIX locks).
Hence the subprocess. And a hook that raises kills the master (Arbiter.run →
sys.exit(-1)), so every hook here swallows its own errors and logs them.
"""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _live_db():
    p = os.environ.get("OTLOBLY_DB")
    if p:
        return p
    return os.path.join(os.environ.get("OTLOBLY_DATA_DIR") or HERE, "otlobly.db")


def _marker():
    return _live_db() + ".repair-requested"


def _preflight(server, why):
    try:
        server.log.info("[dbrepair] preflight (%s) …", why)
        r = subprocess.run([sys.executable, os.path.join(HERE, "dbrepair.py"), "preflight"],
                           timeout=600)
        server.log.info("[dbrepair] preflight (%s) exit %s", why, r.returncode)
    except Exception as e:                       # noqa: BLE001 — a hook must never raise
        server.log.error("[dbrepair] preflight (%s) failed: %s", why, e)


def on_starting(server):
    _preflight(server, "boot")


def pre_fork(server, worker):
    try:
        if not os.path.exists(_marker()):
            return
        server.log.warning("[dbrepair] repair requested by a worker — stopping all workers first")
        server.kill_workers(signal.SIGTERM)
        deadline = time.time() + 8
        while server.WORKERS and time.time() < deadline:
            server.reap_workers()
            time.sleep(0.2)
        if server.WORKERS:
            server.kill_workers(signal.SIGKILL)
            time.sleep(0.5)
            server.reap_workers()
        _preflight(server, "runtime")
    except Exception as e:                       # noqa: BLE001 — a hook must never raise
        server.log.error("[dbrepair] pre_fork repair failed: %s", e)


def post_fork(server, worker):
    # lets db.report_corruption know a master exists that will repair after it exits
    os.environ["OTLOBLY_GUNICORN_MASTER"] = str(server.pid)
