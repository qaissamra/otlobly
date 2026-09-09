#!/usr/bin/env python3
"""
🔮 Forecast: what happens next to a parcel sitting at "Cleared customs", learned
ONLY from the app's own tracking history.

Reads tracking._load_cache() — pure disk, never the network, never a board
sweep. The model is an empirical transition table over the cached GAASH
timelines: "a parcel that cleared at time T next got status S, W working days
later".

THE CLOCK IS A WORKING-DAY CLOCK (the Israel/PS weekend is Fri+Sat). Nothing in
the Gerizim lane has ever happened on a Friday or Saturday — measured over 130
post-clearance transitions, the per-weekday medians run 0.91 → 3.02 CALENDAR
days but only 0.91 → 1.26 WORKING days. The whole weekday spread is the weekend
and nothing else, so one pooled number predicts every weekday. The weekend is
removed once (at measurement) and added back once (at expansion); calendar
values are computed for display and are never added to anything. Mixing the two
units — expanding Thursday's 3.02-day CALENDAR median as if it were working
days — double-counts the weekend and pushes every Thursday ETA a week out.
"""
import json
import math
import os
from datetime import date, datetime, timedelta

import tracking
from paths import data_path

# ── the working-day clock ──────────────────────────────────────────────────
WEEKEND = (4, 5)                  # datetime.weekday(): Fri, Sat
_EPOCH = date(2000, 1, 2)         # a Sunday — origin of the working-day clock
WORK_OPEN_H, WORK_CLOSE_H, SNAP_H = 6, 18, 8   # pickups stamp 06–09 and ~13:00

# ── status identity ────────────────────────────────────────────────────────
CLEARED_CODE = "K2"
# GAASH reuses AJ for two genuinely different events: "Picked up by Gerizim
# courier" (post-clearance — the thing we predict) and "MOC - Palestinian
# authority" (a pre-clearance customs step). Only the known-ambiguous codes are
# split by text, so a thin dataset doesn't fragment on wording variants — CD's
# several texts all mean "customs wants something" and stay merged.
AMBIGUOUS_CODES = frozenset({"AJ"})
# "went backward" = customs pulled the parcel back after clearing it. This must
# NOT be derived from BUCKET_RANK: CODE_LABEL buckets AJ (the Gerizim pickup, a
# forward step) as "transit", which ranks BELOW cleared and would flag almost
# every healthy transition as backward. The customs codes are the real signal.
CUSTOMS_CODES = frozenset({"CD", "SD"})

# ── honesty thresholds ─────────────────────────────────────────────────────
# No date is printed below these. Twelve because a p90 from fewer than ten
# points IS the maximum observation; six SWEEPS because Gerizim collects in
# batches (one run stamped 11 parcels at the same hour), so a raw count alone
# cannot see that 20 rows may be one afternoon's evidence.
MIN_MODEL_N = 12
MIN_MODEL_SWEEPS = 6
STALE_HOURS = 36

# An optional read-only extra corpus (see seed_forecast_history.py). Never
# written by the app; it only ever ADDS history the live cache hasn't seen.
HISTORY_FILE = str(data_path("forecast_history.json"))


def _fold(s):
    return " ".join(str(s or "").split()).strip().lower()


def _parse_ts(v):
    """Any cached time value → naive LOCAL datetime, or None. Cached GAASH event
    times are naive local strings ("2026-08-05T12:02:00"); fetched_at is
    tz-aware ("…+03:00"). Aware inputs are converted then stripped, so every
    comparison in this module is naive-vs-naive. Blank/garbage → None."""
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v or "").strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except Exception:  # noqa - GAASH StatusTime is often blank or malformed
            try:
                dt = datetime.fromisoformat(s[:19])
            except Exception:  # noqa
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _is_workday(d):
    return d.weekday() not in WEEKEND


def _work_index(dt):
    """Instant → position on the working-day clock (1.0 = one working day).

    min(n % 7, 5) maps BOTH Friday and Saturday onto the next Sunday's index,
    and the fraction-of-day is only added on a workday — so any weekend instant
    collapses to Sunday 00:00 with no branching, and a Friday clearance
    automatically forecasts from Sunday morning."""
    n = (dt.date() - _EPOCH).days
    idx = (n // 7) * 5 + min(n % 7, 5)
    if not _is_workday(dt.date()):
        return float(idx)
    frac = (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0
    return idx + frac


def _from_work_index(w):
    """Inverse of _work_index. Because rem ∈ [0,4] the result is always Sun–Thu:
    an ETA can never land on a Friday or Saturday — that is a property of the
    arithmetic, not a filter applied afterwards."""
    k = math.floor(w)
    frac = w - k
    full, rem = divmod(k, 5)
    d = _EPOCH + timedelta(days=full * 7 + rem)
    return datetime(d.year, d.month, d.day) + timedelta(days=frac)


def work_gap(t0, t1):
    """Working days between two instants (may be negative)."""
    return _work_index(t1) - _work_index(t0)


def _snap_business(dt):
    """Pull an instant into working hours — GAASH stamps pickups 06:00–09:00 or
    around 13:00 and never overnight, so an ETA of 03:40 is noise, not a claim.
    Rolling forward goes through _from_work_index, so it still can't hit Fri/Sat."""
    if dt.hour < WORK_OPEN_H:
        return dt.replace(hour=SNAP_H, minute=0, second=0, microsecond=0)
    if dt.hour >= WORK_CLOSE_H:
        nxt = _from_work_index(float(math.floor(_work_index(dt)) + 1))
        return nxt.replace(hour=SNAP_H, minute=0, second=0, microsecond=0)
    return dt.replace(microsecond=0)


def _pct(vals, q):
    """Linear-interpolated percentile over an UNSORTED list; None if empty."""
    v = sorted(vals or [])
    if not v:
        return None
    k = (len(v) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


# ── status identity ────────────────────────────────────────────────────────
def _pa_label():
    return getattr(tracking, "PARCELSAPP_CODE_LABEL", {}) or {}


def _status_key(code, text):
    """Stable identity for a timeline event — see AMBIGUOUS_CODES."""
    c = str(code or "").strip().upper()
    if c in AMBIGUOUS_CODES:
        return "%s|%s" % (c, _fold(text))
    return c or ("?|%s" % _fold(text))


def _status_label(code, text):
    """Human label. tracking.CODE_LABEL first, then the raw GAASH text — but an
    ambiguous code keeps its own text, so an AJ/MOC event is never rendered as
    CODE_LABEL's "Out for last-mile / pickup"."""
    c = str(code or "").strip().upper()
    t = str(text or "").strip()
    if c in AMBIGUOUS_CODES:
        return t or (tracking.CODE_LABEL.get(c) or (c, ""))[0]
    hit = tracking.CODE_LABEL.get(c) or _pa_label().get(c)
    if hit:
        return hit[0]
    return t or c or "?"


def _status_bucket(code, text):
    """The tracking.BUCKET_RANK bucket, for display and the customs check.
    O1/X4/OD/AV are absent from CODE_LABEL and fall through to "transit" — as
    does AJ, which is why CUSTOMS_CODES (not the rank) decides `backward`."""
    c = str(code or "").strip().upper()
    hit = tracking.CODE_LABEL.get(c) or _pa_label().get(c)
    return hit[1] if hit else "transit"


def normalize_events(events):
    """Raw cached [{code,text,time}] → clean, ordered, de-duplicated events.

    Drops anything whose time won't parse, de-dupes the exact (code,text,ts)
    triple (GAASH restamps the same event), and stable-sorts by (ts, original
    index) — the index tiebreak keeps GAASH's own ordering for events sharing a
    timestamp while the ts sort repairs a genuinely out-of-order timeline
    (tracking.py warns StatusTime is often blank or out of order)."""
    parsed = []
    for i, e in enumerate(events or []):
        if not isinstance(e, dict):
            continue
        ts = _parse_ts(e.get("time"))
        if ts is None:
            continue
        code = str(e.get("code") or "").strip().upper()
        text = str(e.get("text") or "").strip()
        parsed.append({"code": code, "text": text, "ts": ts, "_i": i,
                       "key": _status_key(code, text),
                       "label": _status_label(code, text),
                       "bucket": _status_bucket(code, text)})
    seen, out = set(), []
    for e in parsed:
        k = (e["code"], e["text"], e["ts"])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    out.sort(key=lambda e: (e["ts"], e["_i"]))
    return out


def _has_raw_clearance(events):
    """A K2 exists in the RAW list — used to tell "never cleared" apart from
    "cleared, but GAASH left the timestamp blank"."""
    for e in events or []:
        if isinstance(e, dict) and str(e.get("code") or "").strip().upper() == CLEARED_CODE:
            return True
    return False


def clearance_transition(evs):
    """One NORMALIZED timeline → the single trainable transition, or None.

    Trains on the LAST K2: a parcel that un-cleared and re-cleared is, right
    now, in the same state as one that cleared once, and counting every K2 would
    let the pathological multi-clear parcels (which have the longest gaps) vote
    twice and drag the median long. The successor is the first event STRICTLY
    after it — equal timestamps carry no ordering information."""
    idx = [i for i, e in enumerate(evs) if e["key"] == CLEARED_CODE]
    if not idx:
        return None
    i = idx[-1]
    k2 = evs[i]
    succ = None
    for e in evs[i + 1:]:
        if e["ts"] > k2["ts"]:
            succ = e
            break
    if succ is None:
        return None            # still sitting — the tab's population, not training data
    return {"cleared_at": k2["ts"], "weekday": k2["ts"].weekday(),
            "key": succ["key"], "code": succ["code"], "text": succ["text"],
            "label": succ["label"],
            "backward": (succ["code"] in CUSTOMS_CODES
                         or succ["bucket"] == "customs"),
            "gap_wd": work_gap(k2["ts"], succ["ts"]),
            "gap_days": (succ["ts"] - k2["ts"]).total_seconds() / 86400.0,
            "sweep": succ["ts"].replace(minute=0, second=0, microsecond=0)}


_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_model(cache=None):
    """PURE. {GWD: {events, …}} → the empirical model. cache=None reads disk;
    tests pass a dict and touch nothing. Never fetches, never writes."""
    if cache is None:
        cache = _corpus()
    rows, parcels, sitting, newest = [], 0, 0, ""
    for gwd, ent in (cache or {}).items():
        if not isinstance(ent, dict):
            continue
        parcels += 1
        fa = str(ent.get("fetched_at") or "")
        if fa > newest:
            newest = fa
        evs = normalize_events(ent.get("events"))
        if not evs:
            continue
        tr = clearance_transition(evs)
        if tr is None:
            if any(e["key"] == CLEARED_CODE for e in evs):
                sitting += 1
            continue
        rows.append(tr)

    n = len(rows)
    gaps = [r["gap_wd"] for r in rows]
    # Gerizim collects in BATCHES — one run stamped 11 parcels at the same hour
    # — so the raw count overstates how much independent evidence there is. The
    # sweep count is reported alongside n for exactly that reason. It is NOT
    # used to re-estimate the quantiles: deduping by sweep swings p90 between
    # 2.13 and 3.44 depending on which row you pick as the representative, and
    # an alarm threshold must not depend on an arbitrary choice. Clustering
    # widens the confidence in a quantile; it does not move the quantile.
    sweeps = {}
    for r in rows:
        sweeps.setdefault(r["sweep"], []).append(r["gap_wd"])

    dist = {}
    for r in rows:
        d = dist.setdefault(r["key"], {"code": r["code"], "text": r["text"],
                                       "label": r["label"], "n": 0})
        d["n"] += 1
    nxt = sorted(({"key": k, "code": v["code"], "text": v["text"],
                   "label": v["label"], "n": v["n"],
                   "p": (v["n"] / n) if n else 0.0} for k, v in dist.items()),
                 key=lambda d: (-d["n"], d["code"]))

    weekday = {}
    for i, name in enumerate(_WD):
        cell = [r for r in rows if r["weekday"] == i]
        if not cell:
            continue
        weekday[name] = {
            "n": len(cell),
            "sweeps": len({r["sweep"] for r in cell}),
            "p50": _pct([r["gap_wd"] for r in cell], 0.5),
            "p50_days": _pct([r["gap_days"] for r in cell], 0.5),
            # display only: the working-day clock already absorbs the whole
            # weekday effect, so a second conditioning layer would fit noise
            "used": False,
        }

    return {
        "ready": n >= MIN_MODEL_N and len(sweeps) >= MIN_MODEL_SWEEPS,
        "n": n, "parcels": parcels, "sitting": sitting, "sweeps": len(sweeps),
        "n_backward": sum(1 for r in rows if r["backward"]),
        "p25": _pct(gaps, 0.25), "p50": _pct(gaps, 0.5), "p90": _pct(gaps, 0.9),
        "median_days": _pct([r["gap_days"] for r in rows], 0.5),
        "next": nxt, "weekday": weekday, "weekend": ["Fri", "Sat"],
        "thresholds": {"model_n": MIN_MODEL_N, "model_sweeps": MIN_MODEL_SWEEPS},
        "as_of": newest,
        "built_at": datetime.now().replace(microsecond=0).isoformat(),
    }


# ── corpus + memo ──────────────────────────────────────────────────────────
def _load_history():
    """The optional extra corpus — same shape as tracking_cache.json. Read-only;
    absent on a fresh install and that is fine."""
    try:
        with open(HISTORY_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa - missing/corrupt extra history is never fatal
        return {}


def _corpus():
    """The live tracking cache, plus the optional history file for GWDs the live
    cache doesn't have (or has a SHORTER timeline for). The live cache always
    wins on length — it is the fresher source."""
    cache = dict(tracking._load_cache() or {})
    for gwd, ent in _load_history().items():
        if not isinstance(ent, dict):
            continue
        cur = cache.get(gwd) or {}
        if len(ent.get("events") or []) > len(cur.get("events") or []):
            cache[gwd] = ent
    return cache


def _stat_key(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except Exception:  # noqa
        return None


def _cache_key():
    """Fingerprint of both corpus files. Nanosecond mtime + size catches a
    same-second os.replace by the other gunicorn worker (tracking._save_cache is
    tmp+os.replace, so every write lands a fresh stamp)."""
    return (_stat_key(tracking.CACHE_FILE), _stat_key(HISTORY_FILE))


_MODEL = {"key": None, "model": None}


def get_model(force=False):
    """build_model() memoized at module level, rebuilt iff the corpus files
    changed. No TTL — nothing else can change the model. Each gunicorn worker
    keeps its own memo; a rebuild is a small JSON read, so a duplicated rebuild
    across workers costs nothing."""
    key = _cache_key()
    if force or _MODEL["model"] is None or _MODEL["key"] != key:
        _MODEL["model"] = build_model()
        _MODEL["key"] = key
    return _MODEL["model"]


# ── prediction ─────────────────────────────────────────────────────────────
def _iso_d(dt):
    return dt.strftime("%Y-%m-%d")


def predict(cleared_at, *, model=None, now=None):
    """ONE parcel: its clearance instant → the forecast half of a row.

    Thin history or a missing clearance time returns ok:False and NO eta keys at
    all — a null would still render as something client-side."""
    m = get_model() if model is None else model
    k2 = _parse_ts(cleared_at)
    if k2 is None:
        return {"ok": False, "reason": "no_clearance_time", "cleared_at": None}
    now = _parse_ts(now) or datetime.now()
    dwell_wd = max(0.0, work_gap(k2, now))
    base = {"cleared_at": k2.isoformat(timespec="seconds"),
            "cleared_weekday": _WD[k2.weekday()],
            "dwell_days": round(max(0.0, (now - k2).total_seconds() / 86400.0), 2),
            "dwell_work_days": round(dwell_wd, 2),
            "model_n": m.get("n") or 0}
    if not m.get("ready"):
        base.update({"ok": False, "reason": "thin_history", "overdue": False})
        return base

    top = (m.get("next") or [{}])[0]
    w0 = _work_index(k2)
    early, likely, late = sorted(
        _snap_business(_from_work_index(w0 + m[q]))
        for q in ("p25", "p50", "p90"))
    # Overdue fires past the p90 of how long parcels HISTORICALLY waited. Note
    # this is measured only over parcels that did move on, so it is biased a
    # little SHORT (the ones still sitting are excluded) — i.e. the alarm errs
    # toward asking early, which is the safe direction for chasing GAASH.
    late_wd = m["p90"]
    base.update({
        "ok": True, "reason": None,
        "next_code": top.get("code") or "", "next_text": top.get("text") or "",
        "next_label": top.get("label") or "", "next_p": round(top.get("p") or 0.0, 3),
        "next_n": top.get("n") or 0,
        "alternatives": [{"code": a["code"], "text": a["text"], "label": a["label"],
                          "n": a["n"], "p": round(a["p"], 3)}
                         for a in (m.get("next") or [])],
        "eta": _iso_d(likely), "eta_early": _iso_d(early), "eta_late": _iso_d(late),
        "eta_at": likely.isoformat(timespec="seconds"),
        "eta_weekday": _WD[likely.weekday()],
        "gap_work_days": round(m["p50"], 2),
        "gap_days_shown": round(m.get("median_days") or m["p50"], 2),
        "basis": "all", "basis_n": m.get("n") or 0,
        "basis_sweeps": m.get("sweeps") or 0,
        "overdue": dwell_wd > late_wd,
        "overdue_by_work_days": round(max(0.0, dwell_wd - late_wd), 2),
    })
    return base


def forecast_map(gwds=None, *, model=None, now=None, cache=None):
    """BATCHED. {GWD: forecast row} from ONE corpus read + ONE model.

    gwds=None → every cached parcel whose timeline ENDS at the clearance. Given
    an explicit list, GWDs the cache doesn't know come back as
    reason:"no_history" rather than being dropped, so the tab can say why."""
    corpus = _corpus() if cache is None else cache
    m = (get_model() if model is None else model) if cache is None else \
        (model if model is not None else build_model(corpus))
    out = {}
    keys = list(corpus) if gwds is None else [
        tracking.clean_tracking(g) or str(g or "").strip().upper() for g in gwds]
    for gwd in keys:
        ent = corpus.get(gwd)
        if not isinstance(ent, dict) or not (ent.get("events") or []):
            out[gwd] = {"ok": False, "reason": "no_history", "cleared_at": None,
                        "overdue": False}
            continue
        evs = normalize_events(ent.get("events"))
        k2s = [e for e in evs if e["key"] == CLEARED_CODE]
        if not k2s:
            # a K2 with a blank/garbage StatusTime is a DIFFERENT failure from
            # "this parcel never cleared" — never invent the moment it cleared
            reason = ("no_clearance_time" if _has_raw_clearance(ent.get("events"))
                      else "not_cleared")
            out[gwd] = {"ok": False, "reason": reason, "cleared_at": None,
                        "overdue": False,
                        "last_label": (evs[-1]["label"] if evs else "")}
            continue
        k2 = k2s[-1]
        if any(e["ts"] > k2["ts"] for e in evs):
            # the cache says it already moved on — surface the disagreement with
            # the board instead of silently forecasting a stage it left
            out[gwd] = {"ok": False, "reason": "not_cleared", "overdue": False,
                        "cleared_at": k2["ts"].isoformat(timespec="seconds"),
                        "last_label": evs[-1]["label"]}
            continue
        row = predict(k2["ts"], model=m, now=now)
        fa = _parse_ts(ent.get("fetched_at"))
        row["as_of"] = str(ent.get("fetched_at") or "")
        row["stale"] = bool(fa and (_parse_ts(now) or datetime.now()) - fa
                            > timedelta(hours=STALE_HOURS))
        out[gwd] = row
    return out


def forecast_queue(now=None):
    """🔮 the tab payload: every parcel BOTH boards say is at "Cleared customs",
    with its predicted next GAASH status, ETA and basis.

    ONE tracking_map() + ONE parcel_name_map() + ONE parcel_board_map() + ONE
    corpus read — the same batching contract docs_queue() keeps. No network."""
    import gaash_mail
    import leluxe
    tmap = gaash_mail.tracking_map()
    names = gaash_mail.parcel_name_map()
    boards = gaash_mail.parcel_board_map()

    pop = []
    for gwd, t in (tmap or {}).items():
        if (t.get("bucket") or "") != "cleared":
            continue
        # Gerizim physically having it overrules a stale GAASH timeline — the
        # same cross-check _past_customs makes
        if (t.get("gz") or "") in leluxe.GZ_ARRIVED:
            continue
        pop.append(gwd)

    corpus = _corpus()
    model = get_model()
    fc = forecast_map(pop, model=model, now=now, cache=corpus)

    rows = []
    for gwd in pop:
        r = dict(fc.get(gwd) or {"ok": False, "reason": "no_history",
                                 "overdue": False})
        t = tmap.get(gwd) or {}
        r.update({"gwd": gwd, "name": (names.get(gwd) or "")[:70],
                  "board": boards.get(gwd) or "",
                  "gash_status": t.get("gash_status") or "",
                  "label": t.get("label") or ""})
        r.setdefault("as_of", "")
        r.setdefault("stale", False)
        rows.append(r)

    # overdue first, then soonest ETA, then the rows we can't forecast
    rows.sort(key=lambda r: (0 if r.get("overdue") else 1,
                             0 if r.get("ok") else 1,
                             r.get("eta_at") or "9999", r.get("gwd") or ""))
    return {
        "ok": True, "ready": bool(model.get("ready")), "rows": rows,
        "counts": {"total": len(rows),
                   "predicted": sum(1 for r in rows if r.get("ok")),
                   "overdue": sum(1 for r in rows if r.get("overdue")),
                   "unknown": sum(1 for r in rows if not r.get("ok"))},
        "model": model,
    }


# --------------------------------------------------------------------------- #
# 📊 How long GAASH took, and which case type clears fastest
#
# CALENDAR days here, deliberately — the owner's rule: "sent today, released
# fourteen days later is fourteen". That is NOT the working-day clock the
# forecast above runs on, and the two must not be confused: work_gap() answers
# "how long until the next sweep", this answers "how long did they hold it".
# Both raw instants are kept on every row, so the holiday deduction the owner
# wants later ("four of those days were GAASH holidays") can be applied to all
# of this history at once instead of only from the day it ships.
# --------------------------------------------------------------------------- #
CASE_MIN_N = 5              # below this, report the count, never a median
NO_CASE = "(no case)"


def _released_at(ent):
    """When GAASH let this parcel go — the LAST K2 on its timeline, the same
    rule clearance_transition trains on. None when it never cleared."""
    evs = normalize_events((ent or {}).get("events"))
    k2 = [e for e in evs if e["code"] == CLEARED_CODE]
    return k2[-1]["ts"] if k2 else None


def case_report(now=None):
    """Every parcel we handed to GAASH, how long it then took, grouped by case.

    ONE sent_to_gaash_map() + ONE corpus read + ONE name/board map — the same
    batching contract forecast_queue() keeps. No network."""
    import gaash_mail
    sent = gaash_mail.sent_to_gaash_map()
    clr = gaash_mail.clearance_map()
    names = gaash_mail.parcel_name_map()
    boards = gaash_mail.parcel_board_map()
    corpus = _corpus()

    rows, weird = [], []
    for gwd, (iso, src) in sorted(sent.items()):
        if not iso:
            continue
        t0 = _parse_ts(iso)
        if t0 is None:
            continue
        case = ((clr.get(gwd) or {}).get("case_name") or "").strip() or NO_CASE
        r = {"gwd": gwd, "name": (names.get(gwd) or "")[:70],
             "board": boards.get(gwd) or "", "case": case,
             "sent_at": iso, "sent_src": src, "released_at": "", "days": None}
        t1 = _released_at(corpus.get(gwd))
        if t1 is None:
            r["reason"] = "not cleared yet"
            rows.append(r)
            continue
        r["released_at"] = t1.isoformat(timespec="seconds")
        days = (t1 - t0).days
        if days < 0:
            # cleared BEFORE we wrote to GAASH — a real thing (someone else's
            # paperwork got there first). Counted visibly, never averaged in.
            r["reason"] = "released before we sent"
            weird.append(r)
            continue
        r["days"] = days
        rows.append(r)

    by_case = {}
    for r in rows:
        if r.get("days") is None:
            continue
        by_case.setdefault(r["case"], []).append(r["days"])
    opts = {}
    try:
        import leluxe
        opts = {o["name"]: o.get("color") or "" for o in leluxe.case_options(refresh=False)}
    except Exception:  # noqa - colours are decoration; the numbers are not
        pass

    cases = []
    for name, vals in by_case.items():
        ready = len(vals) >= CASE_MIN_N
        cases.append({"case": name, "color": opts.get(name, ""), "n": len(vals),
                      "ready": ready,
                      "p50": round(_pct(vals, .5), 1) if ready else None,
                      "p90": round(_pct(vals, .9), 1) if ready else None,
                      "fastest": min(vals), "slowest": max(vals)})
    # the comparison is the point, so the fastest READY case leads; unready
    # ones sort after it by size, since they are the ones needing more parcels
    cases.sort(key=lambda c: (0 if c["ready"] else 1,
                              c["p50"] if c["ready"] else -c["n"]))
    rows.sort(key=lambda r: (0 if r.get("days") is not None else 1,
                             -(r.get("days") or 0), r["gwd"]))
    done = [r for r in rows if r.get("days") is not None]
    return {"ok": True, "unit": "calendar days", "min_n": CASE_MIN_N,
            "rows": rows + weird, "cases": cases, "weird": len(weird),
            "counts": {"sent": len(rows) + len(weird), "cleared": len(done),
                       "waiting": len(rows) - len(done)},
            "overall": {"n": len(done),
                        "p50": round(_pct([r["days"] for r in done], .5), 1) if done else None,
                        "p90": round(_pct([r["days"] for r in done], .9), 1) if done else None}}


if __name__ == "__main__":   # quick look: python3 forecast.py
    m = build_model()
    print(json.dumps({k: v for k, v in m.items() if k != "weekday"},
                     indent=2, ensure_ascii=False, default=str))
