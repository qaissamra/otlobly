#!/usr/bin/env python3
"""
Self-checks for the public tracking widget fix (landing شحنتك الحالية):

  1. customer_timeline picks the FURTHEST pipeline stage, not the last
     chronological event (GAASH StatusTime is often blank/out-of-order).
  2. The sacred rule survives: GAASH "Delivered" → bucket arrived, never
     "تم التسليم" (only otlobly_status=complete may say that).
  3. _otlobly_stage never defaults a weak map row to "arrived".
  4. timelines_cache_first serves cached events instantly and only goes live
     for GWDs with nothing cached; cache_put_events round-trips.
  5. The 5-step frontend strings exist (landing app.js, account portal, CSS).

    ./.venv/bin/python test_track_widget.py
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-widget-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import db        # noqa: E402
import tracking  # noqa: E402

HERE = Path(__file__).parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def _iso(hours_ago=0):
    return (datetime.now(timezone.utc).astimezone()
            - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def test_rank_pick():
    # "Cleared customs" carries an OLDER timestamp than a customs event (GAASH
    # emits blank/out-of-order StatusTime) → chronological pick would say
    # customs; rank pick must say cleared.
    events = [
        {"code": "VM", "text": "Parcel is on the way to destination country", "time": "2026-07-01T09:00:00"},
        {"code": "K2", "text": "Cleared customs", "time": ""},                       # blank time sorts FIRST
        {"code": "CD", "text": "Required customer ID", "time": "2026-07-05T09:00:00"},
    ]
    ct = tracking.customer_timeline(sorted(events, key=lambda e: e.get("time") or ""))
    check("rank pick: cleared beats a later-sorted customs event",
          (ct["current"] or {}).get("bucket") == "cleared")
    check("rank pick: cleared label shown",
          (ct["current"] or {}).get("label") == "تم التخليص الجمركي")

    # normal in-order flow still picks the true latest stage
    ct2 = tracking.customer_timeline([
        {"code": "VM", "text": "Parcel is on the way to destination country", "time": "2026-07-01T09:00:00"},
        {"code": "CD", "text": "Required customer ID", "time": "2026-07-03T09:00:00"},
        {"code": "K2", "text": "Cleared customs", "time": "2026-07-05T09:00:00"},
    ])
    check("rank pick: in-order flow unchanged", (ct2["current"] or {}).get("bucket") == "cleared")

    # sacred rule: GAASH "Delivered" = box heading to Otlobly, NEVER تم التسليم
    ct3 = tracking.customer_timeline([
        {"code": "K2", "text": "Cleared customs", "time": "2026-07-05T09:00:00"},
        {"code": "D1", "text": "Delivered", "time": "2026-07-08T09:00:00"},
    ])
    check("sacred rule: GAASH Delivered → bucket arrived",
          (ct3["current"] or {}).get("bucket") == "arrived")
    check("sacred rule: GAASH Delivered never says تم التسليم",
          "تم التسليم" not in ((ct3["current"] or {}).get("label") or ""))

    # same behaviour under a saved map missing the parcelsapp rows (mirrors the
    # live config.json's admin-saved copy)
    saved_map = [r for r in tracking.DEFAULT_STATUS_MAP
                 if r["match"] not in ("DELIVERED", "ARRIVED", "TRANSIT", "PICKUP")]
    ct4 = tracking.customer_timeline([
        {"code": "K2", "text": "Cleared customs", "time": ""},
        {"code": "CD", "text": "Required customer ID", "time": "2026-07-05T09:00:00"},
    ], saved_map)
    check("rank pick works under the live saved map (no parcelsapp rows)",
          (ct4["current"] or {}).get("bucket") == "cleared")

    check("empty events → current None",
          tracking.customer_timeline([])["current"] is None)

    # Real GAASH order: K3 "Arrived at destination country" fires BEFORE
    # clearance, and parcels sit in MOC customs AFTER it (GWD004697561's real
    # sequence) — K3 must bucket as customs so the bar never jumps to step 4.
    ct5 = tracking.customer_timeline([
        {"code": "VM", "text": "Parcel is on the way to destination country", "time": "2026-06-01T09:00:00"},
        {"code": "CD", "text": "Required customer ID", "time": "2026-06-03T09:00:00"},
        {"code": "K3", "text": "Arrived at destination country", "time": "2026-06-05T09:00:00"},
        {"code": "AJ", "text": "MOC - Palestinian authority", "time": "2026-06-07T09:00:00"},
    ])
    check("K3 buckets as customs (never jumps past clearance)",
          (ct5["current"] or {}).get("bucket") == "customs")
    ct6 = tracking.customer_timeline([
        {"code": "VM", "text": "Parcel is on the way to destination country", "time": "2026-06-01T09:00:00"},
        {"code": "K3", "text": "Arrived at destination country", "time": "2026-06-05T09:00:00"},
        {"code": "K2", "text": "Cleared customs", "time": "2026-06-08T09:00:00"},
    ])
    check("K2 after K3 → cleared wins", (ct6["current"] or {}).get("bucket") == "cleared")


def test_otlobly_stage():
    import app  # noqa: F401 — imported for _otlobly_stage
    po = {"updated_at": "2026-07-20T10:00:00"}
    pk = {"otlobly_status": "weird status"}
    omap = [{"status": "weird status", "label": "وصلت مرحلة غريبة"},              # no bucket
            {"status": "junk bucket", "label": "x", "bucket": "banana"},
            {"status": "complete", "label": "تم التسليم", "bucket": "delivered"}]
    st = app._otlobly_stage(pk, [], omap, po)
    check("stage with NO bucket → bucket None (not arrived)",
          st and st["bucket"] is None)
    st2 = app._otlobly_stage({"otlobly_status": "junk bucket"}, [], omap, po)
    check("stage with unknown bucket → bucket None", st2 and st2["bucket"] is None)
    st3 = app._otlobly_stage({"otlobly_status": "complete"}, [], omap, po)
    check("complete still → delivered", st3 and st3["bucket"] == "delivered")
    st4 = app._otlobly_stage({"otlobly_status": "recieved rd"}, [],
                             tracking.DEFAULT_OTLOBLY_MAP, po)
    check("default map rows keep their explicit buckets",
          st4 and st4["bucket"] == "arrived")


def test_cache_first():
    calls = []
    real_fallback = tracking.timelines_with_fallback

    def fake_fallback(gwds, lang="en"):
        calls.append(list(gwds))
        fetched = tracking._now_iso()
        cache = tracking._load_cache()
        out = {}
        for g in gwds:
            evs = [{"code": "VM", "text": "Parcel is on the way to destination country",
                    "time": "2026-07-01T09:00:00"}]
            cache[tracking.clean_tracking(g)] = {"events": evs, "source": "gaash",
                                                 "fetched_at": fetched}
            out[g] = {"ok": True, "events": evs, "source": "gaash", "fetched_at": fetched}
        tracking._save_cache(cache)
        return out

    tracking.timelines_with_fallback = fake_fallback
    try:
        tracking._save_cache({"GWD111": {"events": [{"code": "K2", "text": "Cleared customs",
                                                     "time": "2026-07-05T09:00:00"}],
                                         "source": "gaash", "fetched_at": _iso(1)}})
        res = tracking.timelines_cache_first(["GWD111", "GWD222"])
        check("cached GWD served from cache", (res.get("GWD111") or {}).get("source") == "cache")
        check("fresh cached GWD not stale", not (res.get("GWD111") or {}).get("stale"))
        check("live called ONLY for the missing GWD", calls == [["GWD222"]])
        check("missing GWD resolved live", (res.get("GWD222") or {}).get("ok"))
        check("missing GWD persisted for next time",
              bool((tracking._load_cache().get("GWD222") or {}).get("events")))

        # 2nd lookup: everything cached → zero live calls
        calls.clear()
        res2 = tracking.timelines_cache_first(["GWD111", "GWD222"])
        check("second lookup fully cached (no live calls)",
              calls == [] and all((res2.get(g) or {}).get("source") == "cache"
                                  for g in ("GWD111", "GWD222")))

        # old fetched_at → stale flag + as_of passthrough
        tracking._save_cache({"GWD333": {"events": [{"code": "VM", "text": "x", "time": "t"}],
                                         "source": "gaash", "fetched_at": _iso(10)}})
        res3 = tracking.timelines_cache_first(["GWD333"])
        check("old cache entry flagged stale", (res3.get("GWD333") or {}).get("stale") is True)
    finally:
        tracking.timelines_with_fallback = real_fallback

    # cache_put_events round-trip + guards
    tracking._save_cache({"GWD444": {"events": [], "pa_attempt_at": 123.0}})
    tracking.cache_put_events(" GWD444 ", [{"code": "K2", "text": "Cleared customs", "time": "t"}])
    entry = tracking._load_cache().get("GWD444") or {}
    check("cache_put_events writes events + stamp",
          entry.get("events") and entry.get("fetched_at"))
    check("cache_put_events preserves pa_attempt_at", entry.get("pa_attempt_at") == 123.0)
    tracking.cache_put_events("GWD444", [])
    check("cache_put_events no-op on empty events",
          (tracking._load_cache().get("GWD444") or {}).get("events"))


def test_frontend_strings():
    appjs = (HERE / "static" / "app.js").read_text(encoding="utf-8")
    css = (HERE / "static" / "style.css").read_text(encoding="utf-8")
    account = (HERE / "templates" / "account.html").read_text(encoding="utf-8")
    apppy = (HERE / "app.py").read_text(encoding="utf-8")

    check("app.js: 5th step string", '"track.s5"' in appjs)
    check("app.js: loading string", '"track.loading"' in appjs)
    check("app.js: delivered = 6 (five-node bar)", "return 6;" in appjs)
    check("app.js: cleared advances past customs",
          'b==="cleared") return 4' in appjs and 'b==="customs") return 3' in appjs)
    check("app.js: pill carries its bucket class", 'tk-pill b-' in appjs)
    check("app.js: skeleton rendered before fetch", "track-skel" in appjs)
    check("app.js: multi-card grid toggle", '"multi", d.count > 1' in appjs)

    check("style.css: per-bucket pill colors",
          all(f".tk-pill.b-{b}" in css for b in ("transit", "customs", "cleared",
                                                 "arrived", "delivered")))
    check("style.css: current node emphasised", ".prog .node.cur" in css)
    check("style.css: current label emphasised", ".prog-labels span.cur" in css)
    check("style.css: skeleton styles", ".track-skel" in css and "skshine" in css)
    check("style.css: 2-up results grid", "#track-results.multi" in css)

    check("account.html: 5th step in both languages", account.count("s5:") == 2)
    check("account.html: stepOf matches (delivered→6, cleared→4)",
          'b==="delivered") return 6' in account and 'b==="cleared")   return 4' in account)
    check("account.html: labels carry done/cur state", '${labels}' in account)

    check("app.py: customer path is cache-first", "timelines_cache_first" in apppy)
    check("app.py: staff refresh feeds the shared cache", "cache_put_events" in apppy)
    check("leluxe.py: daemon feeds the shared cache",
          "cache_put_events" in (HERE / "leluxe.py").read_text(encoding="utf-8"))


def main():
    db.init_db()
    db.set_current_business(1)
    print("rank-based current pick:")
    test_rank_pick()
    print("otlobly stage override:")
    test_otlobly_stage()
    print("cache-first lookup:")
    test_cache_first()
    print("frontend strings:")
    test_frontend_strings()
    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}): {fails}")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
