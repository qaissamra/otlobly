#!/usr/bin/env python3
"""
Self-checks for 🔮 forecast.py — "what happens next after Cleared customs".

The one that matters most is the WEEKEND math. Every weekday's calendar gap in
the real data is different (Sun 0.91d … Thu 3.02d) purely because Fri+Sat are
the weekend; on a working-day clock they are all ~1.0. So the model measures and
re-expands in working days. Mixing the units — expanding Thursday's 3.02-day
CALENDAR median as if it were working days — would push every Thursday ETA a
week out, which is exactly what test_weekend_regression pins down.

    ./.venv/bin/python test_gaash_forecast.py
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-forecast-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("GAASH_MAILER", None)
os.environ["OTLOBLY_SECRET"] = "x"

import tracking   # noqa: E402  — CACHE_FILE binds at import, so the env must be set first
import forecast   # noqa: E402

HERE = Path(__file__).parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


# ── builders ───────────────────────────────────────────────────────────────
def ev(code, text, ts):
    return {"code": code, "text": text,
            "time": ts if isinstance(ts, str) else ts.isoformat(timespec="seconds")}


def cleared_then(cleared, gap_hours, code="AJ", text="Picked up by Gerizim courier"):
    """One parcel: cleared at `cleared`, next event `gap_hours` later."""
    return [ev("K3", "Arrived at destination country", cleared - timedelta(days=3)),
            ev("K2", "Cleared customs", cleared),
            ev(code, text, cleared + timedelta(hours=gap_hours))]


def corpus(n=20, gap_hours=24, start=None, offset=700000, **kw):
    """n parcels, each cleared on a distinct WORKING day so the sweep count
    equals n (the thin-history guard counts distinct successor hours)."""
    start = start or datetime(2026, 3, 1, 9, 0, 0)
    out, d, made = {}, start, 0
    while made < n:
        if forecast._is_workday(d.date()):
            out["GWD%09d" % (offset + made)] = {
                "events": cleared_then(d, gap_hours, **kw),
                "source": "gaash", "fetched_at": d.isoformat(timespec="seconds")}
            made += 1
        d += timedelta(days=1)
    return out


def _d(y, m, day, h=12):
    return datetime(y, m, day, h, 0, 0)


# ── A. the working-day clock ───────────────────────────────────────────────
def test_clock():
    print("— working-day clock —")
    ok = True
    t = _d(2026, 8, 16, 0)          # a Sunday
    for _ in range(400):
        if forecast._is_workday(t.date()):
            back = forecast._from_work_index(forecast._work_index(t))
            if abs((back - t).total_seconds()) > 1:
                ok = False
                break
        t += timedelta(hours=7)
    check("index/inverse round-trip over 400 instants", ok)

    bad = []
    w = forecast._work_index(_d(2026, 1, 4, 0))
    for i in range(15000):                 # ~2 years of working days, 0.05 steps
        got = forecast._from_work_index(w + i * 0.05)
        if got.weekday() in forecast.WEEKEND:
            bad.append(got)
            break
    check("_from_work_index can NEVER emit a Fri/Sat", not bad)

    fri = forecast._work_index(datetime(2026, 8, 21, 9, 0))    # Friday
    sat = forecast._work_index(datetime(2026, 8, 22, 23, 0))   # Saturday
    sun = forecast._work_index(datetime(2026, 8, 23, 0, 0))    # Sunday 00:00
    check("Fri 09:00 and Sat 23:00 collapse onto the same index", fri == sat)
    check("…and that index IS the coming Sunday 00:00", fri == sun)

    check("Thu 12:00 → Sun 12:00 is ONE working day",
          abs(forecast.work_gap(datetime(2026, 8, 20, 12), datetime(2026, 8, 23, 12)) - 1.0) < 1e-6)
    check("Fri 08:00 → Sat 20:00 is ZERO working days",
          abs(forecast.work_gap(datetime(2026, 8, 21, 8), datetime(2026, 8, 22, 20))) < 1e-6)
    check("Wed 12:00 → Thu 12:00 is ONE working day",
          abs(forecast.work_gap(datetime(2026, 8, 19, 12), datetime(2026, 8, 20, 12)) - 1.0) < 1e-6)

    check("_snap_business(Mon 23:30) → Tue 08:00",
          forecast._snap_business(datetime(2026, 8, 17, 23, 30))
          == datetime(2026, 8, 18, 8, 0))
    thu = forecast._snap_business(datetime(2026, 8, 20, 23, 30))
    check("_snap_business(Thu 23:30) → SUNDAY 08:00, not Friday",
          thu == datetime(2026, 8, 23, 8, 0))
    check("_snap_business(Tue 04:00) → same day 08:00",
          forecast._snap_business(datetime(2026, 8, 18, 4, 0))
          == datetime(2026, 8, 18, 8, 0))


# ── B. the weekend regression (the whole point) ────────────────────────────
def test_weekend_regression():
    print("— weekend regression —")
    m = forecast.build_model(corpus(20, 24))      # every gap = 1 working day
    check("model from 20 one-day parcels is ready", m["ready"])
    check("…and its median is 1.0 working days", abs(m["p50"] - 1.0) < 0.02)

    def eta(dt):
        return datetime.fromisoformat(
            forecast.predict(dt, model=m, now=dt + timedelta(days=30))["eta"])

    thu, e = _d(2026, 8, 20, 14), eta(_d(2026, 8, 20, 14))
    check("Thursday clearance → SUNDAY", e.weekday() == 6)
    check("…which is 3 calendar days later", (e.date() - thu.date()).days == 3)
    check("…and NOT a week out (the double-count guard)",
          (e.date() - thu.date()).days < 5)

    wed = eta(_d(2026, 8, 19, 14))
    check("Wednesday → Thursday, ONE calendar day (no weekend injected)",
          wed.date() == _d(2026, 8, 20).date())
    sun = eta(_d(2026, 8, 16, 14))
    check("Sunday → Monday", sun.date() == _d(2026, 8, 17).date())
    fri = eta(_d(2026, 8, 21, 14))
    check("a K2 stamped on a FRIDAY anchors at Sunday → Mon or later",
          fri.weekday() not in forecast.WEEKEND and fri.date() >= _d(2026, 8, 23).date())

    bad = []
    t = _d(2026, 1, 1, 0)
    for _ in range(24 * 365):
        r = forecast.predict(t, model=m, now=t + timedelta(days=60))
        for k in ("eta", "eta_early", "eta_late"):
            if datetime.fromisoformat(r[k]).weekday() in forecast.WEEKEND:
                bad.append((t, k, r[k]))
                break
        if bad:
            break
        t += timedelta(hours=1)
    check("EVERY hour of a year: eta/early/late all land Sun–Thu", not bad)


# ── C. extraction ──────────────────────────────────────────────────────────
def test_extraction():
    print("— extraction —")
    c = _d(2026, 8, 17, 9)
    dup = [ev("K2", "Cleared customs", c),
           ev("D1", "Delivered", c + timedelta(hours=20)),
           ev("D1", "Delivered", c + timedelta(hours=20))]
    check("exact duplicate (code,text,time) collapses",
          len(forecast.normalize_events(dup)) == 2)

    out_of_order = [ev("K2", "Cleared customs", c),
                    ev("AJ", "Picked up by Gerizim courier", c + timedelta(hours=20)),
                    ev("K3", "Arrived at destination country", c - timedelta(days=2))]
    evs = forecast.normalize_events(out_of_order)
    check("an out-of-order timeline sorts by time", evs[0]["code"] == "K3")
    tr = forecast.clearance_transition(evs)
    check("…and the successor is the true next event", tr and tr["code"] == "AJ")

    # Mon 17th … re-cleared Wed 19th, picked up Thu 20th (all working days, so
    # the gap is a clean 1.0 — pick the dates deliberately, the clock is real)
    recleared = [ev("K2", "Cleared customs", c),
                 ev("CD", "Required customer ID", c + timedelta(days=1)),
                 ev("K2", "Cleared customs", c + timedelta(days=2)),
                 ev("AJ", "Picked up by Gerizim courier", c + timedelta(days=3))]
    tr = forecast.clearance_transition(forecast.normalize_events(recleared))
    check("a re-cleared parcel trains on the LAST K2 only",
          tr and tr["cleared_at"] == c + timedelta(days=2))
    check("…so its gap is measured from the SECOND clearance",
          tr and abs(tr["gap_wd"] - 1.0) < 0.02)

    blank = [ev("K3", "Arrived at destination country", c),
             {"code": "K2", "text": "Cleared customs", "time": ""}]
    check("a K2 with a blank time contributes nothing and doesn't raise",
          forecast.clearance_transition(forecast.normalize_events(blank)) is None)

    sitting = {"GWD000000001": {"events": [ev("K2", "Cleared customs", c)]}}
    m = forecast.build_model(sitting)
    check("a sitting parcel trains nothing…", m["n"] == 0)
    check("…but is counted as sitting", m["sitting"] == 1)

    same = [ev("K2", "Cleared customs", c), ev("AJ", "Picked up by Gerizim courier", c)]
    check("an equal-timestamp successor is skipped (no zero-gap row)",
          forecast.clearance_transition(forecast.normalize_events(same)) is None)


# ── D. the AJ ambiguity ────────────────────────────────────────────────────
def test_aj_ambiguity():
    print("— AJ is two different events —")
    check("AJ/Gerizim and AJ/MOC get DIFFERENT keys",
          forecast._status_key("AJ", "Picked up by Gerizim courier")
          != forecast._status_key("AJ", "MOC - Palestinian authority"))
    check("an AJ/MOC event keeps its own label, not CODE_LABEL's pickup wording",
          forecast._status_label("AJ", "MOC - Palestinian authority")
          == "MOC - Palestinian authority")
    check("D1 is NOT split by text (only AMBIGUOUS_CODES are)",
          forecast._status_key("D1", "Delivered") == forecast._status_key("D1", "delivered!"))
    check("O1 is NOT split by text",
          forecast._status_key("O1", "Entered w/h") == forecast._status_key("O1", "x"))

    c = corpus(10, 24)
    c.update(corpus(10, 24, start=datetime(2026, 5, 4, 9), offset=900000,
                    code="AJ", text="MOC - Palestinian authority"))
    m = forecast.build_model(c)
    labs = {a["text"] for a in m["next"]}
    check("both AJ texts survive as separate alternatives",
          "Picked up by Gerizim courier" in labs and "MOC - Palestinian authority" in labs)

    back = forecast.clearance_transition(forecast.normalize_events(
        cleared_then(_d(2026, 8, 17, 9), 24, code="CD", text="Required customer ID")))
    check("a CD successor is flagged backward", back and back["backward"] is True)
    fwd = forecast.clearance_transition(forecast.normalize_events(
        cleared_then(_d(2026, 8, 17, 9), 24)))
    check("an AJ pickup is NOT flagged backward (CODE_LABEL buckets it 'transit')",
          fwd and fwd["backward"] is False)


# ── E. the thin-history guard ──────────────────────────────────────────────
def test_thin_history():
    print("— thin history —")
    m = forecast.build_model(corpus(5, 24))
    check("5 transitions → not ready", not m["ready"])
    r = forecast.predict(_d(2026, 8, 17, 9), model=m, now=_d(2026, 8, 18, 9))
    check("…row says thin_history", r.get("reason") == "thin_history")
    check("…and carries NO eta key at all (a null could still render)", "eta" not in r)
    check("…and never claims overdue", r.get("overdue") is False)

    # 20 parcels, every successor stamped in the SAME hour = one pickup run
    one = {}
    base, when = datetime(2026, 4, 5, 9, 0), datetime(2026, 4, 6, 8, 30)
    for i in range(20):
        one["GWD%09d" % (800000 + i)] = {"events": [
            ev("K2", "Cleared customs", base),
            ev("AJ", "Picked up by Gerizim courier", when)]}
    m1 = forecast.build_model(one)
    check("20 transitions but ONE sweep → still not ready (n alone must not pass)",
          m1["n"] == 20 and m1["sweeps"] == 1 and not m1["ready"])

    m2 = forecast.build_model(corpus(12, 24))
    check("crossing 12 transitions / 6 sweeps flips ready on",
          m2["ready"] and m2["n"] == 12 and m2["sweeps"] >= 6)
    check("…and a date appears", "eta" in forecast.predict(
        _d(2026, 8, 17, 9), model=m2, now=_d(2026, 8, 18, 9)))


# ── F. per-parcel reasons ──────────────────────────────────────────────────
def test_reasons():
    print("— per-parcel honesty —")
    m = forecast.build_model(corpus(20, 24))
    c = _d(2026, 8, 17, 9)
    cache = {
        "GWD000000010": {"events": [ev("K3", "Arrived", c - timedelta(days=2)),
                                    ev("K2", "Cleared customs", c)],
                         "fetched_at": c.isoformat()},
        "GWD000000011": {"events": [ev("K2", "Cleared customs", c),
                                    ev("AJ", "Picked up by Gerizim courier",
                                       c + timedelta(days=1))]},
        "GWD000000012": {"events": [{"code": "K2", "text": "Cleared customs", "time": ""}]},
    }
    fm = forecast.forecast_map(
        ["GWD000000010", "GWD000000011", "GWD000000012", "GWD000000099"],
        model=m, cache=cache, now=c + timedelta(hours=6))
    check("a parcel sitting at K2 gets a real forecast",
          fm["GWD000000010"].get("ok") is True and "eta" in fm["GWD000000010"])
    check("a parcel that already moved → not_cleared",
          fm["GWD000000011"].get("reason") == "not_cleared")
    check("…and says what it actually is now",
          fm["GWD000000011"].get("last_label") == "Picked up by Gerizim courier")
    check("a blank K2 timestamp → no_clearance_time (never invented)",
          fm["GWD000000012"].get("reason") == "no_clearance_time"
          and fm["GWD000000012"].get("cleared_at") is None)
    check("a GWD the cache doesn't know → no_history, still listed",
          fm["GWD000000099"].get("reason") == "no_history")

    old = {"GWD000000013": {"events": [ev("K2", "Cleared customs", c)],
                            "fetched_at": (c - timedelta(hours=48)).isoformat()}}
    fm2 = forecast.forecast_map(["GWD000000013"], model=m, cache=old, now=c)
    check("a cache entry older than 36h is flagged stale", fm2["GWD000000013"]["stale"])


# ── G. overdue ─────────────────────────────────────────────────────────────
def test_overdue():
    print("— overdue —")
    m = forecast.build_model(corpus(20, 24))
    p90 = m["p90"]
    c = _d(2026, 8, 17, 9)                       # Monday
    late = forecast.predict(c, model=m, now=c + timedelta(days=6))
    check("sitting well past p90 → overdue", late["overdue"] is True)
    check("…and reports by how much",
          late["overdue_by_work_days"] > 0
          and abs(late["overdue_by_work_days"]
                  - (late["dwell_work_days"] - p90)) < 0.05)
    fresh = forecast.predict(c, model=m, now=c + timedelta(hours=3))
    check("just cleared → not overdue", fresh["overdue"] is False)

    # A Thursday-afternoon clearance still accrues the REST of Thursday; what
    # the weekend must not do is add anything on top of it.
    thu = _d(2026, 8, 20, 14)                    # Thursday
    thu_eve = forecast.predict(thu, model=m, now=datetime(2026, 8, 20, 23, 59))
    fri = forecast.predict(thu, model=m, now=datetime(2026, 8, 21, 12))
    sat = forecast.predict(thu, model=m, now=datetime(2026, 8, 22, 23))
    check("Friday adds nothing to the wait",
          abs(fri["dwell_work_days"] - thu_eve["dwell_work_days"]) < 0.01)
    check("…and neither does Saturday",
          abs(sat["dwell_work_days"] - fri["dwell_work_days"]) < 0.01)
    check("cleared Thursday, still Saturday → under a working day waited",
          sat["dwell_work_days"] < 1.0)
    check("…so no weekend panic", sat["overdue"] is False)


# ── H. caching, and never the network ──────────────────────────────────────
def test_cache_and_no_network():
    print("— memo + no network —")
    tracking._save_cache(corpus(14, 24))
    forecast.get_model(force=True)
    a = forecast.get_model()
    b = forecast.get_model()
    check("get_model() memoizes (same object identity)", a is b)

    calls = {"n": 0}
    real = forecast.build_model

    def counted(cache=None):
        calls["n"] += 1
        return real(cache)
    forecast.build_model = counted
    try:
        forecast.get_model(); forecast.get_model(); forecast.get_model()
        check("…and does not rebuild while the corpus is unchanged", calls["n"] == 0)
        tracking._save_cache(corpus(16, 24))
        m = forecast.get_model()
        check("a cache write rebuilds it", calls["n"] == 1 and m["n"] == 16)
    finally:
        forecast.build_model = real

    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!"))
    saved = {}
    # ops_deadline is the expensive one: calling it MINTS GAASH's 35-day upload
    # link, so it must never be reachable from the forecast path
    for name in ("timelines_with_fallback", "track", "get_session", "fetch_one",
                 "timelines", "timeline", "ops_deadline", "docs_status"):
        if hasattr(tracking, name):
            saved[name] = getattr(tracking, name)
            setattr(tracking, name, boom)
    loads = {"n": 0}
    real_load = tracking._load_cache

    def counted_load():
        loads["n"] += 1
        return real_load()
    tracking._load_cache = counted_load
    try:
        m = forecast.build_model()
        check("build_model() runs with every carrier call booby-trapped", m["n"] == 16)
        check("…reading the cache once per build", loads["n"] == 1)
    finally:
        tracking._load_cache = real_load
        for k, v in saved.items():
            setattr(tracking, k, v)

    missing = forecast.HISTORY_FILE + ".nope"
    keep = forecast.HISTORY_FILE
    forecast.HISTORY_FILE = missing
    try:
        check("a missing history file is not fatal", isinstance(forecast._load_history(), dict))
    finally:
        forecast.HISTORY_FILE = keep

    os.remove(tracking.CACHE_FILE)
    m = forecast.get_model(force=True)
    check("a missing cache file → ready:false, no exception",
          m["ready"] is False and m["n"] == 0)


# ── I. the route ───────────────────────────────────────────────────────────
def test_route():
    print("— route: permissions + shape —")
    import app as appmod
    import auth as authmod
    import db as dbmod

    def client(u, pw="s1"):
        c = appmod.app.test_client()
        c.post("/login", data={"username": u, "password": pw})
        return c

    dbmod.init_db()
    for u, role in (("fcotlo", "admin"), ("fcemp", "fulfillment"), ("fcsal", "sales")):
        try:
            dbmod.create_user(u, authmod.hash_pw("s1"), role, u, business_id=1)
        except Exception:  # noqa - already there from a previous run
            pass

    anon = appmod.app.test_client()
    check("anonymous is blocked",
          anon.get("/api/gaash/forecast").status_code in (302, 401, 403))
    check("sales is blocked", client("fcsal").get("/api/gaash/forecast").status_code == 403)
    r = client("fcemp").get("/api/gaash/forecast")
    check("fulfillment gets 200", r.status_code == 200)
    d = r.get_json() or {}
    check("…with ok/ready/rows/model/counts",
          d.get("ok") is True and "ready" in d and isinstance(d.get("rows"), list)
          and isinstance(d.get("model"), dict) and isinstance(d.get("counts"), dict))
    check("the payload is JSON-clean (no datetime leaked)",
          json.dumps(d) is not None)
    bad = [k for row in d.get("rows") or [] for k in ("eta", "eta_early", "eta_late")
           if k in row and not isinstance(row[k], str)]
    check("every eta* value is a string", not bad)


# ── J. the UI is actually wired ────────────────────────────────────────────
def test_ui_wired():
    print("— UI wiring (all four points) —")
    html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    nows = html.replace(" ", "")
    check("1. the tab button exists", 'id="gmTabFcast"' in html)
    check("   …and is bilingual", 'data-ar="🔮 التوقعات"' in html)
    check("2. the pane exists", 'id="gmFcastPane"' in html)
    check("3. 'fcast' is in the reopen whitelist",
          '"docs","fcast","dash"' in nows or '"fcast"' in nows.split('gm_tab')[1][:400])
    check("4. the switch carries the triple",
          '["fcast","gmTabFcast","gmFcastPane"]' in nows)
    check("   …and calls the renderer", 'if(t==="fcast")gmFcastRender();' in nows)
    check("the renderer hits the endpoint", '"/api/gaash/forecast"' in html)
    check("fetch/draw are split like the Docs tab",
          "async function gmFcastRender(" in html and "function gmFcastDraw(" in html)
    # the board rule: never hand-roll `<span class="pill" style="…">` — every
    # pill must come from tonePill/hexPill/solidPill so one change restyles all
    block = html.split("═══ 🔮 Forecast")[1].split("function gmDocsStatePill(")[0]
    check("pills go through the sanctioned helpers",
          "tonePill(" in block and "hexPill(" in block)
    check("…and none are hand-rolled", '<span class="pill"' not in block)
    check("the predicted status is NOT rendered via gaashBucketPill",
          "gaashBucketPill(" not in block)   # it overwrites the label with SHORT[bucket]


def main():
    test_clock()
    test_weekend_regression()
    test_extraction()
    test_aj_ambiguity()
    test_thin_history()
    test_reasons()
    test_overdue()
    test_cache_and_no_network()
    test_route()
    test_ui_wired()
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All forecast checks passed ✓")


if __name__ == "__main__":
    main()
