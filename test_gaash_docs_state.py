#!/usr/bin/env python3
"""
Self-checks for the GAASH docs-state machinery — "a dead check must never
masquerade as a fresh answer".

The bug this suite pins shut (GWD004746116, 2026-08-20): GAASH's fileUpload
page was live asking for TWO documents while the board wore a blue "in customs,
nothing asked" pill. The parser was right — the stored answer was stale, and
every failure path re-stamped docs_checked so the staleness filters (board 1d,
sweep 20h, queue 3d) all saw a freshly-confirmed answer. Five of nine nightly
sweeps did literally nothing.

The contract under test:
  · docs_checked = the last SUCCESSFUL answer, only ever written with one
  · docs_error   = the last FAILED attempt, popped on success
  · a failure keeps docs_state AND its true age
  · doc-request links outrank IsClearance/FinalStatuses (a parcel can be in
    clearance AND asked for papers — 'cleared' hid the ask and evicted it)
  · an expired WP nonce (401/403) is re-scraped once, not failed for 10 min
  · force=True always checks docs, even cleared/Gerizim rows
  · the worker sweep retries failed rows, with a 1h floor so an outage can't
    make it hammer the same parcels all night

    ./.venv/bin/python test_gaash_docs_state.py
"""

import io
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error
from urllib import request as _urlreq

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-docs-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "test-worker-token"
os.environ["LELUXE_PACE"] = "0"

import db          # noqa: E402
import tracking    # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def iso(**delta):
    return (datetime.now().astimezone() - timedelta(**delta)).isoformat()


# the real payload GAASH served for GWD004746116 while the board said blue
REAL_6116 = {"Hawb": "GWD004746116", "IsArrived": True, "IsClearance": False,
             "Is824": False, "FinalStatuses": [], "PaymentLinks": [], "Permits": [],
             "AdditionalStatuses": [{"StatusCode": 816, "Links": [
                 {"LinkUrl": "https://ops.gaashwd.com/fileUpload"
                             "?packageId=GWD004746116&type=6&type=7",
                  "Type": 0}]}]}


class _Resp:
    def __init__(self, body):
        self._body = body.encode() if isinstance(body, str) else body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _with_fake_net(urlopen, fn):
    """Run fn with tracking's urllib swapped for a fake urlopen."""
    old_req, old_cache = tracking.request, dict(tracking._STATUS_SESSION_CACHE)
    tracking.request = types.SimpleNamespace(Request=_urlreq.Request,
                                             urlopen=urlopen)
    tracking._STATUS_SESSION_CACHE.update(at=time.time(),
                                          val=("https://api", "n1"))
    try:
        return fn()
    finally:
        tracking.request = old_req
        tracking._STATUS_SESSION_CACHE.update(**old_cache)


def _serve(payload):
    def urlopen(req, timeout=None):
        return _Resp(json.dumps(payload))
    return urlopen


# ── A. classification: links outrank GAASH's own flags ─────────────────────
def test_classification():
    print("— docs_status classification —")
    S = lambda p: _with_fake_net(_serve(p), lambda: tracking.docs_status("GWD000000001"))

    check("links + IsClearance → action (the ask outranks 'cleared')",
          S({"IsClearance": True, "AdditionalStatuses": [
              {"StatusCode": 816, "Links": [{"LinkUrl": "u", "Type": 0}]}]})["state"] == "action")
    check("links + FinalStatuses → action (the ask outranks 'stopped')",
          S({"FinalStatuses": [{"x": 1}], "AdditionalStatuses": [
              {"StatusCode": 816, "Links": [{"LinkUrl": "u", "Type": 0}]}]})["state"] == "action")
    check("IsClearance alone → cleared",
          S({"IsClearance": True})["state"] == "cleared")
    check("FinalStatuses alone → stopped",
          S({"FinalStatuses": [{"x": 1}]})["state"] == "stopped")
    check("status rows without links → info",
          S({"AdditionalStatuses": [{"StatusCode": 810}]})["state"] == "info")
    check("empty payload → plain",
          S({})["state"] == "plain")

    r = S(REAL_6116)
    check("the GWD004746116 payload → action (the bug's own regression)",
          r["state"] == "action" and r["arrived"] is True)
    import re as _re
    check("  …and BOTH asked slots survive in the stored link (types 6+7)",
          _re.findall(r"[?&]type=(\d+)", r["links"][0]["url"]) == ["6", "7"])
    check("  …and codes carry 816", r["codes"] == [816])


# ── B. nonce lifecycle: 401/403 re-scrapes once ─────────────────────────────
STATUS_HTML = ('<html><script>var parcelStatusTrackerData = '
               '{"apiUrl":"https://api","nonce":"n2"};</script></html>')


def _http_err(code, body=b""):
    return error.HTTPError("https://api/x", code, "err", None, io.BytesIO(body))


def test_nonce_retry():
    print("— expired-nonce retry —")
    calls = []

    def urlopen(req, timeout=None):
        url = req.full_url
        if url.startswith(tracking.STATUS_PAGE[:30]):
            calls.append("scrape")
            return _Resp(STATUS_HTML)
        calls.append("api:" + (req.headers.get("X-wp-nonce") or "?"))
        if req.headers.get("X-wp-nonce") == "n1":
            raise _http_err(403)
        return _Resp(json.dumps(REAL_6116))

    r = _with_fake_net(urlopen, lambda: tracking.docs_status("GWD004746116"))
    check("403 on the cached nonce → fresh scrape → answer",
          isinstance(r, dict) and r["state"] == "action")
    check("  exact sequence: api(n1) → scrape → api(n2)",
          calls == ["api:n1", "scrape", "api:n2"])

    def always403(req, timeout=None):
        if req.full_url.startswith(tracking.STATUS_PAGE[:30]):
            return _Resp(STATUS_HTML)
        raise _http_err(403)
    check("403 even after a fresh nonce → None (a real failure)",
          _with_fake_net(always403, lambda: tracking.docs_status("GWD1")) is None)

    def neterr(req, timeout=None):
        raise error.URLError("boom")
    check("network error → None (never a verdict)",
          _with_fake_net(neterr, lambda: tracking.docs_status("GWD1")) is None)

    def nodata(req, timeout=None):
        raise _http_err(404, b'{"code":"no_data"}')
    check("404 + no_data stays a REAL 'noanswer' verdict",
          _with_fake_net(nodata, lambda: tracking.docs_status("GWD1"))["state"] == "noanswer")


# ── C. store semantics: failure keeps the answer AND its age ───────────────
LX_TN = "GWD000000301"


def _seed_lx(data, rid=1):
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders WHERE id=?", (rid,))
        c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                  "VALUES (?,?,?,?,?,0)",
                  (rid, "package", f"pkg {data.get('tracking_number')}", "",
                   json.dumps(data)))


def _lx_data(rid=1):
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?", (rid,)).fetchone()
    return json.loads(r["data_json"])


def test_store_semantics():
    print("— store_docs_state: leluxe + purchases —")
    import leluxe
    import purchases

    t0 = iso(days=2)
    _seed_lx({"tracking_number": LX_TN, "fields": {},
              "docs_state": {"state": "action", "links": [], "codes": [],
                             "arrived": True, "checked": t0},
              "docs_checked": t0})
    check("leluxe: failed lookup hits the row", leluxe.store_docs_state(LX_TN, None) == 1)
    d = _lx_data()
    check("  keeps the last answer", d["docs_state"]["state"] == "action")
    check("  keeps its true age (docs_checked untouched)", d["docs_checked"] == t0)
    check("  stamps docs_error", bool(d.get("docs_error")))

    good = {"state": "info", "links": [], "codes": [], "arrived": True,
            "checked": iso()}
    leluxe.store_docs_state(LX_TN, good)
    d = _lx_data()
    check("  success stores the answer + bumps docs_checked",
          d["docs_state"]["state"] == "info" and d["docs_checked"] == good["checked"])
    check("  success pops docs_error", "docs_error" not in d)

    pk_tn = "GWD000000302"
    pdb = purchases.load()
    pdb["purchase_orders"] = [{"po_id": "po1", "packages": [
        {"package_no": 1, "tracking_number": pk_tn,
         "docs_state": {"state": "info", "checked": t0}, "docs_checked": t0}]}]
    purchases.save(pdb)
    check("purchases: failed lookup hits the package",
          purchases.store_docs_state(pk_tn, None) == 1)
    pk = purchases.load()["purchase_orders"][0]["packages"][0]
    check("  keeps answer + age", pk["docs_state"]["state"] == "info"
          and pk["docs_checked"] == t0 and bool(pk.get("docs_error")))
    purchases.store_docs_state(pk_tn, good)
    pk = purchases.load()["purchase_orders"][0]["packages"][0]
    check("  success bumps + pops", pk["docs_checked"] == good["checked"]
          and "docs_error" not in pk)

    out = purchases._norm_packages([{"package_no": 1, "docs_error": "T1"}])
    check("_norm_packages whitelists docs_error (a PO save must not strip it)",
          out[0].get("docs_error") == "T1")


# ── D. refresh_tracking gates: backoff + force ──────────────────────────────
def _run_refresh(rows, docs_returns, force=False):
    """rows: list of data dicts (tracking_checked pre-stamped fresh so only the
    docs machinery runs). docs_returns: tn → value docs_status hands back.
    Returns (docs_calls, {tn: stored data})."""
    import leluxe
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        for i, data in enumerate(rows, start=1):
            c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                      "VALUES (?,?,?,?,?,0)",
                      (i, "package", f"pkg {i}", "", json.dumps(data)))
    calls = []

    def fake_docs(tn, **k):
        calls.append(tn)
        return docs_returns.get(tn)

    stub = types.SimpleNamespace(
        clean_tracking=tracking.clean_tracking, REQUEST_GAP=0,
        get_session=lambda **k: ("api", "nonce"),
        fetch_one=lambda tn, *a, **k: {"Statuses": []},
        latest_status=lambda d: {"bucket": "customs", "text": "x"},
        cache_put_events=lambda *a, **k: None,
        events_from_raw=tracking.events_from_raw,
        arrival_from_events=tracking.arrival_from_events,
        arrival_signal=tracking.arrival_signal,
        parcel_arrived=tracking.parcel_arrived,
        _load_cache=tracking._load_cache,
        docs_status=fake_docs,
        ops_deadline=lambda tn, **k: (_ for _ in ()).throw(
            AssertionError("ops page touched")))
    gstub = types.SimpleNamespace(track=lambda tn, **k: None)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = stub, gstub
    try:
        leluxe.refresh_tracking(batch=50, force=force)
    finally:
        for n, o in (("tracking", old_t), ("gerizim", old_g)):
            if o is not None:
                sys.modules[n] = o
            else:
                sys.modules.pop(n, None)
    with db.connect() as c:
        rs = c.execute("SELECT data_json FROM leluxe_orders WHERE deleted=0").fetchall()
    out = {}
    for r in rs:
        d = json.loads(r["data_json"])
        out[d.get("tracking_number")] = d
    return calls, out


def _row(tn, **extra):
    d = {"tracking_number": tn, "fields": {},
         "tracking_checked": iso(), "gaash_deadline": "2026-09-01"}
    d.update(extra)
    return d


def test_refresh_gates():
    print("— leluxe.refresh_tracking: failure honesty + backoff + force —")
    T1 = "GWD000000401"

    # (i) a failed lookup: docs_error stamped, state + age preserved
    seeded = {"state": "info", "checked": iso(days=2)}
    calls, out = _run_refresh(
        [_row(T1, docs_state=seeded, docs_checked=iso(days=2))], {T1: None})
    d = out[T1]
    check("stale row is re-asked", calls == [T1])
    check("  failure stamps docs_error only", bool(d.get("docs_error")))
    check("  docs_checked keeps its true age (still ~2 days old)",
          d["docs_checked"] < iso(hours=23))
    check("  docs_state preserved", d["docs_state"]["state"] == "info")

    # (ii) a failure < 1h old backs off — no hot loop
    calls, _ = _run_refresh(
        [_row(T1, docs_state=seeded, docs_checked=iso(days=2),
              docs_error=iso(minutes=5))], {T1: None})
    check("failure 5min ago → NOT re-asked yet", calls == [])

    # (iii) a failure 2h old retries even though docs_checked is 'stamped'
    calls, out = _run_refresh(
        [_row(T1, docs_state=seeded, docs_checked=iso(days=2),
              docs_error=iso(hours=2))],
        {T1: {"state": "action", "links": [], "codes": [816], "arrived": True,
              "checked": iso()}})
    check("failure 2h ago → re-asked", calls == [T1])
    check("  success flips the state and pops docs_error",
          out[T1]["docs_state"]["state"] == "action"
          and "docs_error" not in out[T1])

    # (iv) cleared bucket: bulk skips docs, force never does
    cleared = _row(T1, docs_state=seeded, docs_checked=iso(days=2),
                   tracking_status={"bucket": "cleared", "label": "x"})
    calls, _ = _run_refresh([cleared], {T1: None})
    check("cleared row: bulk sweep skips docs", calls == [])
    calls, _ = _run_refresh([cleared], {T1: None}, force=True)
    check("cleared row: force=True STILL checks docs (E)", calls == [T1])


# ── E. worker sweep: retries failures, 1h floor, refresh cutoff ────────────
def test_worker_sweep():
    print("— /api/worker/docs_sweep —")
    import app as appmod

    checked = []
    old = appmod._docs_check_one
    appmod._docs_check_one = lambda tn: (checked.append(tn) or
                                         {"state": "info", "links": []})
    try:
        with db.connect() as c:
            c.execute("DELETE FROM leluxe_orders")
            rows = [
                # failing: old success, failure 2h ago → picked
                ("GWD000000501", {"docs_state": {"state": "info"},
                                  "docs_checked": iso(days=2),
                                  "docs_error": iso(hours=2)}),
                # fresh failure (5min) → left alone this round
                ("GWD000000502", {"docs_state": {"state": "info"},
                                  "docs_checked": iso(days=2),
                                  "docs_error": iso(minutes=5)}),
                # healthy fresh answer → nothing to do
                ("GWD000000503", {"docs_state": {"state": "info"},
                                  "docs_checked": iso(minutes=10)}),
                # old success, no failure → only a refresh picks it
                ("GWD000000504", {"docs_state": {"state": "info"},
                                  "docs_checked": iso(days=2)}),
            ]
            for i, (tn, extra) in enumerate(rows, start=1):
                data = {"tracking_number": tn, "fields": {}}
                data.update(extra)
                c.execute("INSERT INTO leluxe_orders (id,kind,name,status,data_json,deleted) "
                          "VALUES (?,?,?,?,?,0)",
                          (i, "package", f"pkg {tn}", "", json.dumps(data)))
        cl = appmod.app.test_client()
        hdr = {"Authorization": "Bearer test-worker-token"}

        r = cl.post("/api/worker/docs_sweep", json={"batch": 3}, headers=hdr)
        j = r.get_json()
        check("plain sweep picks EXACTLY the failed-and-cooled row",
              checked == ["GWD000000501"])
        check("  nothing left over", j["remaining"] == 0)

        checked.clear()
        cl.post("/api/worker/docs_sweep",
                json={"batch": 3, "refresh": True, "max_age_hours": 20}, headers=hdr)
        check("refresh sweep adds the old-success row, still skips the "
              "5min-old failure and the fresh answer",
              sorted(checked) == ["GWD000000501", "GWD000000504"])

        check("no token → 401",
              cl.post("/api/worker/docs_sweep", json={}).status_code == 401)
    finally:
        appmod._docs_check_one = old


def main():
    print("test_gaash_docs_state")
    db.init_db()
    test_classification()
    test_nonce_retry()
    test_store_semantics()
    test_refresh_gates()
    test_worker_sweep()
    if fails:
        print(f"\nFAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
