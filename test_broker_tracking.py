#!/usr/bin/env python3
"""
Self-checks for per-tenant tracking (Tatabu Phase 9).

Otlobly (business #1) tracks via GAASH (unchanged). A broker tenant tracks via
parcelsapp (multi-carrier) instead — no GAASH. Network is monkeypatched so the test
is offline + deterministic.

    ./.venv/bin/python test_broker_tracking.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-track-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["PARCELSAPP_API_KEY"] = "test-key"          # so the broker path attempts parcelsapp

import db          # noqa: E402
import parcelsapp  # noqa: E402
import tracking    # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    db.init_db()

    # --- stub the two networks ---
    # GAASH direct (Otlobly): every number resolves ok.
    tracking.timelines = lambda gwds, lang="en": {
        g: {"ok": True, "events": [{"code": "D1", "text": "Delivered", "time": "2026-07-10T09:00:00"}]}
        for g in gwds}
    tracking.gaash_browser.available = lambda: False
    # parcelsapp (broker): every number resolves ok.
    parcelsapp.fetch_statuses = lambda tns, key, **kw: {
        tracking.clean_tracking(t): {"status": "DELIVERED",
                                     "timeline": [{"text": "Delivered", "time": "2026-07-10T09:00:00"}]}
        for t in tns}

    # --- Otlobly (business #1) → GAASH ---
    db.set_current_business(1)
    r1 = tracking.timelines_with_fallback(["GWD004697561"])
    check("Otlobly tracks via GAASH", (r1.get("GWD004697561") or {}).get("source") == "gaash")

    # --- broker (business #2) → parcelsapp, never GAASH ---
    bid = db.create_business("Broker Co")
    db.set_current_business(bid)
    r2 = tracking.timelines_with_fallback(["1Z9999W99999999999"])   # a non-GAASH carrier number
    entry = r2.get("1Z9999W99999999999") or {}
    check("broker tracks via parcelsapp (not GAASH)", entry.get("source") == "parcelsapp")
    check("broker got events back", entry.get("ok") and len(entry.get("events", [])) >= 1)

    # --- broker with NO parcelsapp key → serves cache / empty, still no GAASH ---
    old = os.environ.pop("PARCELSAPP_API_KEY", None)
    gaash_called = {"n": 0}
    _orig = tracking.timelines
    tracking.timelines = lambda gwds, lang="en": (gaash_called.__setitem__("n", gaash_called["n"] + 1) or _orig(gwds, lang))
    r3 = tracking.timelines_with_fallback(["NEWNUM123"])
    check("broker without key does NOT call GAASH", gaash_called["n"] == 0)
    if old:
        os.environ["PARCELSAPP_API_KEY"] = old

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
