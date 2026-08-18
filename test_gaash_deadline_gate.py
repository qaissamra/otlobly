#!/usr/bin/env python3
"""
Self-checks for the GAASH arrival gate — "never ask for a deadline before the
box has landed".

Reading GAASH's ops upload page mints their document-upload link, and the expiry
it reports is 35 days out. Measured over 30 live snapshots (992 observations): a
scrape taken BEFORE the parcel lands returns that scrape + 35 every single time
(58/58) — a rolling number, not a real deadline — while a scrape taken after
arrival pins to arrival + 35. So the board must not fetch (and show) the rolling
value, and arrival must invalidate whatever was cached before it.

The trap this suite exists to hold shut: GAASH asks for documents IN ADVANCE, so
a CD event is NOT proof of arrival — it precedes the K3 in 116 of 117 parcels.
A gate that accepted CD would still have burned every damaged parcel.

    ./.venv/bin/python test_gaash_deadline_gate.py
"""

import json
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-gate-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import db          # noqa: E402
import tracking    # noqa: E402

HERE = Path(__file__).parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def ev(code, day=None):
    return {"code": code, "text": code,
            "time": f"2026-07-{day:02d}T09:00:00" if day else ""}


# ── A. the predicate ───────────────────────────────────────────────────────
def test_predicate():
    print("— predicate: what proves the box landed —")
    A = lambda **k: tracking.arrival_signal(**k)

    check("VM only → not_arrived (never fetched)",
          A(events=[ev("VM", 1)]) == ("not_arrived", "timeline:no-arrival-code"))
    # THE trap: CD is a docs request, sent in advance — not an arrival
    check("VM+CD → not_arrived (the trap that burned 6 parcels)",
          A(events=[ev("VM", 1), ev("CD", 3)]) == ("not_arrived", "timeline:no-arrival-code"))
    check("VM+CD+SD → still not_arrived",
          A(events=[ev("VM", 1), ev("CD", 3), ev("SD", 4)])[0] == "not_arrived")
    check("adding CD never flips a verdict",
          A(events=[ev("VM", 1)]) == A(events=[ev("VM", 1), ev("CD", 5)]))

    check("K3 → arrived (fetched)", A(events=[ev("VM", 1), ev("K3", 20)]) == ("arrived", "k3"))
    check("D1 without K3 → arrived", A(events=[ev("VM", 1), ev("D1", 25)]) == ("arrived", "d1"))
    check("K2 without K3 → arrived", A(events=[ev("VM", 1), ev("K2", 22)]) == ("arrived", "k2"))
    check("empty timeline → unknown, fail closed",
          A(events=[]) == ("unknown", "no-evidence") and not tracking.parcel_arrived(events=[]))
    check("no evidence at all → unknown", A() == ("unknown", "no-evidence"))

    check("stored marker alone is enough", A(stored_arrival="2026-07-20") == ("arrived", "stored"))
    check("Gerizim has it → arrived", A(gerizim_arrived=True) == ("arrived", "gerizim"))
    check("GASH rank 1 (ARIIVED) → arrived", A(gash_rank=1) == ("arrived", "gash:1"))
    # ranks 2/3 are the CD trap in board vocabulary: "customer ID" / "documents
    # sent" / "MOC" all mirror a docs request, raised BEFORE the box lands
    check("GASH rank 2 (customer ID) is NOT arrival", A(gash_rank=2)[0] != "arrived")
    check("GASH rank 3 (MOC) is NOT arrival", A(gash_rank=3)[0] != "arrived")
    check("GASH rank 4 (CLEARED GASH) → arrived", A(gash_rank=4) == ("arrived", "gash:4"))
    check("GASH rank 6 (picked up by Gerizim) → arrived", A(gash_rank=6)[0] == "arrived")
    check("rank 2 with no other evidence stays unknown, not arrived",
          A(gash_rank=2) == ("unknown", "no-evidence"))
    check("GASH rank 0 (STILL NOT ARRIVED) → not_arrived", A(gash_rank=0) == ("not_arrived", "gash:0"))
    check("…but a live K3 beats a stale rank 0",
          A(gash_rank=0, events=[ev("K3", 20)]) == ("arrived", "k3"))
    check("IsArrived True → arrived", A(docs_state={"arrived": True}) == ("arrived", "is-arrived"))
    # docs_status writes a plain bool and its no-answer path defaults to False
    check("IsArrived False is NO EVIDENCE, not a negative",
          A(docs_state={"arrived": False}) == ("unknown", "no-evidence"))

    check("earliest arrival wins over a later one",
          tracking.arrival_from_events([ev("D1", 25), ev("K3", 20)])["at"] == "2026-07-20")
    check("K3 preferred over K2 at the same timestamp",
          tracking.arrival_from_events([ev("K2", 20), ev("K3", 20)])["code"] == "K3")
    check("a blank StatusTime is still proof",
          tracking.arrival_from_events([ev("K3")]) == {"code": "K3", "at": None})
    check("junk entries are ignored, not fatal",
          tracking.arrival_from_events([None, "x", {"code": "K3", "time": "2026-07-20"}])["code"] == "K3")

    # the bucket trap, pinned in one place
    st = tracking.staff_status_from_events(
        [{"code": "K3", "text": "Arrived", "time": "2026-07-20T09:00:00"},
         {"code": "AJ", "text": "Picked up by Gerizim courier", "time": "2026-07-22T09:00:00"}])
    check("bucket goes BACKWARD after K3 (why we never gate on it)",
          st["bucket"] == "transit")
    check("…while the predicate still says arrived",
          A(events=[ev("K3", 20), ev("AJ", 22)])[0] == "arrived")


def test_corpus():
    print("— real-corpus regression —")
    f = HERE / "forecast_history.json"      # gitignored; skip where absent
    if not f.exists():
        print("   .. forecast_history.json absent — skipped")
        return
    corpus = json.loads(f.read_text())
    verdicts = {g: tracking.arrival_signal(events=e.get("events"))[0]
                for g, e in corpus.items()}
    blocked = {g for g, v in verdicts.items() if v != "arrived"}
    check("151 of 154 parcels are arrived",
          sum(1 for v in verdicts.values() if v == "arrived") == 151)
    check("the 3 blocked are exactly the still-burning parcels",
          blocked == {"GWD004562751", "GWD004641913", "GWD004721753"})
    check("the D1-without-K3 parcel is allowed",
          verdicts.get("GWD004745574") == "arrived")


# ── B/C. the sweeps never reach the ops page for a not-arrived parcel ──────
BOOM = lambda *a, **k: (_ for _ in ()).throw(AssertionError("MINTED A LINK"))


def _tstub(dl_calls, timeline_for):
    """A tracking stub that records ops_deadline calls and serves per-GWD
    timelines. The predicate itself is the REAL one — that is the point."""
    def fetch(tn, *a, **k):
        return {"Statuses": [{"MappedStatusCode": c, "StatusDescription": c,
                              "StatusTime": t} for c, t in timeline_for(tn)]}
    return types.SimpleNamespace(
        clean_tracking=tracking.clean_tracking, REQUEST_GAP=0,
        get_session=lambda **k: ("api", "nonce"), fetch_one=fetch,
        latest_status=lambda d: {"bucket": "customs", "text": "x"},
        cache_put_events=lambda *a, **k: None,
        events_from_raw=tracking.events_from_raw,
        arrival_from_events=tracking.arrival_from_events,
        arrival_signal=tracking.arrival_signal,
        parcel_arrived=tracking.parcel_arrived,
        _load_cache=tracking._load_cache,
        docs_status=lambda tn, **k: {"state": "info", "arrived": False,
                                     "codes": [], "links": []},
        ops_deadline=lambda tn, **k: (dl_calls.append(tn), "2026-09-01")[1])


TIMELINES = {
    "GWD000000001": [("VM", "2026-07-01T09:00:00")],                       # never arrived
    "GWD000000002": [("VM", "2026-07-01T09:00:00"), ("CD", "2026-07-03T09:00:00")],
    "GWD000000003": [("VM", "2026-07-01T09:00:00"), ("K3", "2026-07-20T09:00:00")],
    "GWD000000004": [("VM", "2026-07-01T09:00:00"), ("D1", "2026-07-25T09:00:00")],
}


def _run_leluxe(force=False, seed_cache=True):
    import leluxe
    if seed_cache:
        tracking._save_cache({g: {"events": [{"code": c, "text": c, "time": t}
                                             for c, t in tl]}
                              for g, tl in TIMELINES.items()})
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        for i, g in enumerate(TIMELINES, start=1):
            c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                      "VALUES (?,?,?,?,?,0)",
                      (i, "package", f"pkg {g}", "",
                       json.dumps({"tracking_number": g, "fields": {}})))
    dl = []
    stub = _tstub(dl, lambda tn: TIMELINES.get(tn, []))
    gstub = types.SimpleNamespace(track=lambda tn, **k: None)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = stub, gstub
    try:
        res = leluxe.refresh_tracking(batch=50, force=force)
    finally:
        for n, o in (("tracking", old_t), ("gerizim", old_g)):
            if o is not None:
                sys.modules[n] = o
            else:
                sys.modules.pop(n, None)
    return res, dl


def test_leluxe_gate():
    print("— leluxe.refresh_tracking —")
    res, dl = _run_leluxe()
    check("only the ARRIVED parcels reached the ops page",
          set(dl) == {"GWD000000003", "GWD000000004"})
    check("  VM-only was never fetched", "GWD000000001" not in dl)
    check("  VM+CD was never fetched", "GWD000000002" not in dl)

    with db.connect() as c:
        rows = {json.loads(r["data_json"])["tracking_number"]: json.loads(r["data_json"])
                for r in c.execute("SELECT data_json FROM leluxe_orders")}
    check("no gaash_deadline_checked stamped on a skipped parcel",
          not rows["GWD000000001"].get("gaash_deadline_checked")
          and not rows["GWD000000002"].get("gaash_deadline_checked"))
    check("…and it IS stamped on the fetched ones",
          bool(rows["GWD000000003"].get("gaash_deadline_checked")))
    check("gaash_arrival persisted from the timeline",
          rows["GWD000000003"].get("gaash_arrival") == "2026-07-20"
          and rows["GWD000000003"].get("gaash_arrival_code") == "K3")
    check("D1-without-K3 records D1 as the proof",
          rows["GWD000000004"].get("gaash_arrival_code") == "D1")
    check("a skipped parcel gets NO arrival marker",
          not rows["GWD000000001"].get("gaash_arrival"))
    skips = {r["tracking"]: r.get("deadline_skipped") for r in res["results"]}
    check("the skip reason is reported to the UI",
          skips.get("GWD000000001") == "timeline:no-arrival-code")
    check("docs are still checked for a not-arrived parcel",
          bool(rows["GWD000000001"].get("docs_checked")))

    # THE most important assertion in the suite
    _, dl_f = _run_leluxe(force=True)
    check("force=True STILL does not fetch a not-arrived parcel",
          "GWD000000001" not in dl_f and "GWD000000002" not in dl_f)
    check("…while force does fetch the arrived ones", "GWD000000003" in dl_f)

    # cache empty → gate must fall back to the fresh fetch, not fail open
    tracking._save_cache({})
    _, dl_c = _run_leluxe(seed_cache=False)
    check("cold cache: same-round K3 still unlocks the fetch (promotion)",
          "GWD000000003" in dl_c)
    check("cold cache: VM-only is still blocked", "GWD000000001" not in dl_c)


def test_booby_trap():
    print("— booby trap: a board of only not-arrived parcels —")
    import leluxe
    tracking._save_cache({g: {"events": [{"code": c, "text": c, "time": t}
                                         for c, t in tl]}
                          for g, tl in TIMELINES.items() if g in
                          ("GWD000000001", "GWD000000002")})
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        for i, g in enumerate(("GWD000000001", "GWD000000002", "GWD000000009"), start=1):
            c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                      "VALUES (?,?,?,?,?,0)",
                      (i, "package", "p", "", json.dumps({"tracking_number": g, "fields": {}})))
    stub = _tstub([], lambda tn: TIMELINES.get(tn, []))
    stub.ops_deadline = BOOM                      # any call raises
    gstub = types.SimpleNamespace(track=lambda tn, **k: None)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = stub, gstub
    ok = True
    try:
        leluxe.refresh_tracking(batch=50, force=True)
    except AssertionError:
        ok = False
    finally:
        for n, o in (("tracking", old_t), ("gerizim", old_g)):
            if o is not None:
                sys.modules[n] = o
            else:
                sys.modules.pop(n, None)
    check("force sweep over VM-only / VM+CD / unknown-GWD mints nothing", ok)


# ── D. alerts: versioned countdown stamps ─────────────────────────────────
def test_alerts_stamps():
    print("— alerts: countdown stamps are keyed by the deadline —")
    import alerts
    check("_gd_key versions the name", alerts._gd_key("2026-08-31", "gd7") == "gd7@2026-08-31")
    check("_GD_RE matches gd7 but not gd_past",
          bool(alerts._GD_RE.fullmatch("gd7")) and not alerts._GD_RE.fullmatch("gd_past"))
    check("…and does not match an already-versioned key",
          not alerts._GD_RE.fullmatch("gd7@2026-08-31"))


# ── E. arrival invalidates a pre-arrival (rolling) deadline ───────────────
def test_stale_invalidation():
    print("— arrival re-opens the 14-day cadence —")
    import leluxe
    fresh = (datetime.now().astimezone() - timedelta(days=1)).isoformat()
    tracking._save_cache({g: {"events": [{"code": c, "text": c, "time": t}
                                         for c, t in tl]}
                          for g, tl in TIMELINES.items()})

    def one(row, gwd="GWD000000003"):
        """One sweep over a single row; returns the GWDs whose deadline was fetched."""
        with db.connect() as c:
            c.execute("DELETE FROM leluxe_orders")
            c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                      "VALUES (1,'package','p','',?,0)",
                      (json.dumps(dict(row, tracking_number=gwd, fields={})),))
        dl = []
        stub = _tstub(dl, lambda tn: TIMELINES.get(tn, []))
        gstub = types.SimpleNamespace(track=lambda tn, **k: None)
        old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
        sys.modules["tracking"], sys.modules["gerizim"] = stub, gstub
        try:
            leluxe.refresh_tracking(batch=50)
        finally:
            for n, o in (("tracking", old_t), ("gerizim", old_g)):
                if o is not None:
                    sys.modules[n] = o
                else:
                    sys.modules.pop(n, None)
        return dl

    stale = one({"gaash_deadline": "2026-08-20", "gaash_deadline_checked": "2026-07-15",
                 "gaash_arrival": "2026-07-20", "gaash_arrival_code": "K3",
                 "tracking_checked": fresh})
    check("a deadline scraped BEFORE arrival is re-fetched (it was rolling)",
          "GWD000000003" in stale)

    pinned = one({"gaash_deadline": "2026-08-24", "gaash_deadline_checked": fresh,
                  "gaash_arrival": "2026-07-20", "gaash_arrival_code": "K3",
                  "tracking_checked": fresh})
    check("a deadline scraped AFTER arrival is left alone (already pinned)",
          "GWD000000003" not in pinned)

    never = one({"gaash_deadline": "2026-08-20", "gaash_deadline_checked": "2026-07-15",
                 "tracking_checked": fresh}, gwd="GWD000000001")
    check("the rule never fires for a parcel that has not arrived",
          "GWD000000001" not in never)


def main():
    db.init_db()
    test_predicate()
    test_corpus()
    test_leluxe_gate()
    test_booby_trap()
    test_alerts_stamps()
    test_stale_invalidation()
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All arrival-gate checks passed ✓")


if __name__ == "__main__":
    main()
