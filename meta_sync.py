#!/usr/bin/env python3
"""Keep Meta ad spend fresh 24/7.

A tiny daemon thread that pulls Meta Marketing-API spend on a schedule and writes
the cache the P&L reads (meta.CACHE, on the persistent disk). Started from app.py
at boot — only when META_AD_ACCOUNT_ID + META_ACCESS_TOKEN are set, so it's a no-op
without credentials. Meta spend is PULLED (there's no ad-spend webhook), so a
scheduled pull is the right mechanism.

Interval: META_SYNC_HOURS env (default 4). Safe under gunicorn's 2 workers — the
cache write is atomic (meta.sync_once), so at worst two cheap API calls per cycle.
"""
import os
import threading
import time

import memlog
import meta


def _interval_seconds():
    try:
        return max(1, int(os.environ.get("META_SYNC_HOURS", "4"))) * 3600
    except (TypeError, ValueError):
        return 4 * 3600


def _loop():
    time.sleep(30)  # let the app finish booting before the first pull
    while True:
        try:
            with memlog.watch("meta_sync"):
                out = meta.sync_once()
            if out.get("last_error"):
                print(f"[meta_sync] error: {out['last_error']}", flush=True)
            else:
                print(f"[meta_sync] synced ${out.get('total_usd', 0):.2f}", flush=True)
        except Exception as e:  # never let the thread die
            print(f"[meta_sync] unexpected: {e}", flush=True)
        time.sleep(_interval_seconds())


_started = False


def start():
    """Start the background sync once (idempotent). No-op without API creds."""
    global _started
    if _started or not meta.has_api_creds():
        return
    _started = True
    threading.Thread(target=_loop, name="meta-sync", daemon=True).start()
    print(f"[meta_sync] started · every {_interval_seconds() // 3600}h", flush=True)
