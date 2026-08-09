#!/usr/bin/env python3
"""
Purchases shipping check: a parcel the owner already RECEIVED or SENT is done.

The Purchases sweep only ever skipped on CARRIER signals (Gerizim delivered, an
ancient GAASH-delivered parcel). Nothing looked at otlobly_status — the field
the owner himself sets — so packages he had already received or handed on kept
costing two live carrier lookups on every page open, forever. On live data that
was 8 of the 13 packages still being polled.

The rule reuses alerts.stop_statuses: ONE vocabulary, edited in one place in
Settings, shared by the Telegram alerts, the Leluxe sweep and this one.
`force` (the per-package ⋯ 🔎) must still be able to check anything.

    ./.venv/bin/python test_po_shipping_skips.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-poskip-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_SECRET"] = "x"

import alerts     # noqa: E402
import cfg        # noqa: E402
import db         # noqa: E402
import purchases  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


PKGS = [
    # (tracking, otlobly_status, gerizim bucket)
    ("GWDRECV0001", "recieved rd", None),      # owner has it + docs
    ("GWDRECV0002", "recieved no rd", None),   # owner has it
    ("GWDSENT0003", "sent no rd", None),       # already handed on
    ("GWDRD000004", "rd", None),               # docs received
    ("GWDLIVE0005", "oredered", None),         # still moving → check
    ("GWDLIVE0006", None, None),               # no status yet → check
    ("GWDGZDN0007", "oredered", "delivered"),  # carrier-terminal → check
]


def seed():
    pdb = purchases.load()
    pdb["purchase_orders"] = [{
        "po_id": "PO-SKIP", "amazon_order_number": "111-9", "ship_to": "T",
        "packages": [
            {"package_no": i + 1, "tracking_number": tn,
             "otlobly_status": st, "items": [],
             **({"gerizim_status": {"bucket": gz}} if gz else {})}
            for i, (tn, st, gz) in enumerate(PKGS)],
    }]
    pdb["seq"] = 1
    purchases.save(pdb)


def run(calls, **kw):
    """One sweep with the carriers stubbed; returns (result, fetched GWDs)."""
    calls.clear()

    def fetch(tn, *a, **k):
        calls.append(tn)
        return {"Statuses": [{"StatusDescription": "In transit",
                              "StatusTime": "2026-08-06"}]}
    tstub = types.SimpleNamespace(
        clean_tracking=lambda s: (s or "").strip(), REQUEST_GAP=0,
        get_session=lambda **k: ("api", "nonce"), fetch_one=fetch,
        latest_status=lambda d: {"bucket": "transit", "text": "In transit"},
        cache_put_events=lambda *a, **k: None, events_from_raw=lambda d: [],
        ops_deadline=lambda tn, **k: None)
    gstub = types.SimpleNamespace(track=lambda tn, **k: None)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        return purchases.refresh_tracking(batch=50, **kw), list(calls)
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def main():
    db.init_db()
    calls = []

    seed()
    res, got = run(calls)
    check("received / sent parcels are never checked again",
          not {"GWDRECV0001", "GWDRECV0002", "GWDSENT0003",
               "GWDRD000004"} & set(got))
    check("parcels still on their way ARE checked",
          {"GWDLIVE0005", "GWDLIVE0006"} <= set(got))
    check("the carrier-terminal skip still stands (Gerizim delivered)",
          "GWDGZDN0007" not in got)
    check("what was stopped is reported, with the status that stopped it",
          sorted((x["tracking"], x["status"]) for x in res["stopped"])
          == [("GWDRD000004", "rd"), ("GWDRECV0001", "recieved rd"),
              ("GWDRECV0002", "recieved no rd"), ("GWDSENT0003", "sent no rd")])

    seed()
    _res, got = run(calls, only="GWDRECV0001", force=True)
    check("force (the per-package ⋯ 🔎) still checks a received parcel",
          got == ["GWDRECV0001"])

    seed()
    _res, got = run(calls, only="GWDRECV0001")
    check("only= alone does NOT resurrect it — the ⋯ action sends force",
          got == [])

    # one vocabulary: editing the list in Settings moves this sweep too
    seed()
    config = cfg.load()
    cfg.set_path(config, "alerts.stop_statuses", ["oredered"])
    cfg.save(config)
    try:
        _res, got = run(calls)
        check("Settings alerts.stop_statuses drives the rule (one editor)",
              "GWDLIVE0005" not in got and "GWDRECV0001" in got)
    finally:
        cfg.set_path(config, "alerts.stop_statuses", list(alerts.STOP_DEFAULT))
        cfg.save(config)

    check("alerts.stop_statuses() is the shared definition",
          alerts.stop_statuses() == {s.lower() for s in alerts.STOP_DEFAULT})

    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("po shipping skip checks passed ✓")


if __name__ == "__main__":
    main()
