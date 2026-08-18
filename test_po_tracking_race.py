#!/usr/bin/env python3
"""
The Purchases GAASH sweep must never clobber concurrent writes.

refresh_tracking() holds its loaded snapshot across ~10s-per-GWD carrier
scrapes; it used to save that stale snapshot back verbatim, erasing any PO
created or edited meanwhile (watched live: a just-created order vanished the
moment the in-flight sweep saved over it). The fix re-loads the store and
merges ONLY the tracking keys before every save. This test fires a concurrent
create from inside the stubbed carrier call — the tightest possible race
window — and asserts the new PO survives on disk AND in the returned db.

    ./.venv/bin/python test_po_tracking_race.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-porace-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_SECRET"] = "x"

import db         # noqa: E402
import purchases  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    db.init_db()
    pdb = purchases.load()
    pdb["purchase_orders"].append({
        "po_id": "PO-A", "amazon_order_number": "111-1", "ship_to": "Seed",
        "packages": [{"package_no": 1, "tracking_number": "GWDRACE0001",
                      "items": []}]})
    pdb["seq"] = 1
    purchases.save(pdb)

    def concurrent_create():
        # what /api/purchase does under the hood: load → append → save
        d2 = purchases.load()
        d2["purchase_orders"].append({"po_id": "PO-NEW", "ship_to": "Race Winner",
                                      "packages": []})
        d2["seq"] = 2
        purchases.save(d2)

    tstub = types.SimpleNamespace(
        clean_tracking=lambda s: (s or "").strip(),
        REQUEST_GAP=0,
        get_session=lambda **k: ("api", "nonce"),
        fetch_one=lambda tn, *a, **k: {"Statuses": [
            {"StatusDescription": "In transit", "StatusTime": "2026-08-06"}]},
        latest_status=lambda d: {"bucket": "transit", "text": "In transit"},
        cache_put_events=lambda *a, **k: None,
        events_from_raw=lambda d: [],
                # These fixtures are parcels that have ALREADY LANDED — declared
        # explicitly, because the deadline fetch is arrival-gated since
        # 2026-08-18 (reading GAASH's ops page mints the 35-day upload link).
        # The gate itself is covered by test_gaash_deadline_gate.py.
        arrival_signal=lambda **k: ("arrived", "test-fixture"),
        parcel_arrived=lambda **k: True,
        arrival_from_events=lambda evs: {"code": "K3", "at": "2026-07-01"},
        _load_cache=lambda: {},
        ops_deadline=lambda tn, **k: None)

    def gz_track(tn, **k):
        concurrent_create()   # lands mid-flight, inside the carrier window
        return {"bucket": "office", "label": "At Gerizim office", "status": "office"}
    gstub = types.SimpleNamespace(track=gz_track)

    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        res = purchases.refresh_tracking(batch=5, force=True)
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)

    final = purchases.load()
    ids = [p["po_id"] for p in final["purchase_orders"]]
    pk = next((k for p in final["purchase_orders"] if p["po_id"] == "PO-A"
               for k in p["packages"]), {})
    check("tracked package got its refreshed statuses",
          (pk.get("tracking_status") or {}).get("bucket") == "transit"
          and (pk.get("gerizim_status") or {}).get("bucket") == "office"
          and pk.get("tracking_checked"))
    check("concurrently created PO survives the sweep save", "PO-NEW" in ids)
    check("seq bumped by the concurrent writer survives", final.get("seq") == 2)
    check("returned db carries the concurrent PO (client board stays whole)",
          "PO-NEW" in [p["po_id"] for p in (res["db"]["purchase_orders"])])
    check("one package refreshed", res["updated"] == 1 and res["remaining"] == 0)
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("po tracking race checks passed ✓")


if __name__ == "__main__":
    main()
