#!/usr/bin/env python3
"""
Self-checks: 🪪 "Sent to Gaash" + "Gaash Case" — the clearance record.

The bug this suite exists for: documents could reach GAASH from ELEVEN buttons,
and only four of them (the Purchases ones) recorded anything. Every Leluxe
parcel went out unrecorded, which is why "how long does GAASH take" had never
been answerable. The fix puts the record in the funnel all eleven share, so the
guarantee is structural — and `ui_wiring_guarantee` below is what keeps it
structural when someone adds a twelfth button.

The other rules under test:
  · sent_to_gaash = WHICHEVER CAME FIRST, email or documents; earliest wins and
    a later signal never moves the clock forward
  · a 'task' row in gaash_msgs is a to-do the owner wrote, NOT a send
  · a grouped member resolves through its primary (members carry no messages)
  · neither signal → ("", ""), never today
  · the AZ (2) write never overwrites a value a human typed, and the DATE is
    write-once
  · undoing a date clears it with null — "" is rejected by ClickUp
  · the report counts CALENDAR days: sent Thu, released Sun is 3, not 1

    ./.venv/bin/python test_gaash_docs_sent.py
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-clr-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("GAASH_MAILER", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["LELUXE_PACE"] = "0"
os.environ["LELUXE_PUSH_DISABLED"] = "1"

import db            # noqa: E402
import gaash_mail as gm   # noqa: E402
import leluxe        # noqa: E402
import forecast      # noqa: E402

HERE = Path(__file__).parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def _msg(gwd, at, kind="sent"):
    with db.connect() as c:
        c.execute("INSERT INTO gaash_msgs(gwd,dir,kind,step,at) VALUES(?,?,?,?,?)",
                  (gwd, "out", kind, 1, at))


# ── the resolver ─────────────────────────────────────────────────────────────
def resolver():
    db.init_db()
    check("nothing recorded is empty, NOT today",
          gm.sent_to_gaash("GWD000000001") == ("", ""))

    # documents on the 5th, email on the 10th → the 5th
    g = "GWD000000002"
    _msg(g, "2026-09-10T09:00:00+03:00")
    gm.stamp_docs_sent(g, types=[6, 7], src="upload", at="2026-09-05T08:00:00Z",
                       case="wrong id")
    iso, src = gm.sent_to_gaash(g)
    check("documents before the email win", iso.startswith("2026-09-05") and src == "upload")
    check("the case rode along", gm.clearance_get(g).get("case_name") == "wrong id")
    check("the doc types rode along",
          json.loads(gm.clearance_get(g).get("doc_types") or "[]") == [6, 7])

    # the other direction
    g = "GWD000000003"
    _msg(g, "2026-09-01T09:00:00+03:00")
    gm.stamp_docs_sent(g, types=[8], src="link", at="2026-09-06T08:00:00Z")
    check("the email before the documents wins",
          gm.sent_to_gaash(g)[0].startswith("2026-09-01"))

    # earliest wins, permanently
    gm.stamp_sent(g, "2026-09-20T08:00:00Z", "link")
    check("a later stamp never moves the clock forward",
          gm.sent_to_gaash(g)[0].startswith("2026-09-01"))
    gm.stamp_sent(g, "2026-08-02T08:00:00Z", "link")
    check("an EARLIER stamp does move it back",
          gm.sent_to_gaash(g)[0].startswith("2026-08-02"))

    # a to-do is not a send; a Gmail reply is
    g = "GWD000000004"
    _msg(g, "2026-09-01T09:00:00+03:00", kind="task")
    check("a 'task' row is not a send", gm.sent_to_gaash(g) == ("", ""))
    _msg(g, "2026-09-02T09:00:00+03:00", kind="task_done")
    check("a 'task_done' row is not a send either", gm.sent_to_gaash(g) == ("", ""))
    _msg(g, "2026-09-03T09:00:00+03:00", kind="gmail")
    check("a reply typed in Gmail IS a send",
          gm.sent_to_gaash(g)[1] == "email")

    # a grouped member carries no messages of its own
    p, m = "GWD000000010", "GWD000000011"
    with db.connect() as c:
        c.execute("INSERT INTO gaash_threads(gwd,state,step,group_gwds_json) "
                  "VALUES(?,?,?,?)", (p, "active", 1, json.dumps([p, m])))
    _msg(p, "2026-09-04T09:00:00+03:00")
    check("a grouped member resolves through its primary",
          gm.sent_to_gaash(m)[0].startswith("2026-09-04"))

    # mixed dialects: the browser writes Z, db.now_iso writes an offset
    g = "GWD000000005"
    _msg(g, "2026-09-10T09:00:00+03:00")            # 06:00 UTC
    gm.stamp_sent(g, "2026-09-10T07:00:00Z", "link")  # later in real time
    check("Z and +03:00 are compared as instants, not strings",
          gm.sent_to_gaash(g)[1] == "email")

    mp = gm.sent_to_gaash_map()
    check("the batched map agrees with the single lookup",
          mp.get("GWD000000002") == gm.sent_to_gaash("GWD000000002"))


# ── the server-side writer (app._stamp_docs_sent) ────────────────────────────
def server_writer():
    import app                                   # noqa: E402 - needs the env above
    import purchases as pm

    # a Leluxe parcel: in NO purchases store at all. Before this feature it got
    # no record anywhere, on any of the eleven buttons.
    g = "GWD000000020"
    app._stamp_docs_sent(g, [6], src="link", case="straight", user="t")
    rec = gm.clearance_get(g)
    check("a Leluxe parcel (not in purchases.json) IS stamped", bool(rec.get("sent_at")))
    check("its case is stored", rec.get("case_name") == "straight")
    check("it is queued for ClickUp", rec.get("push_state") == "pending")

    # a Purchases parcel keeps the fields the red 'docs not received' chip reads
    g2 = "GWD000000021"
    pdb = pm.load()
    pdb.setdefault("purchase_orders", []).append(
        {"po_id": "PO-T1", "packages": [{"tracking_number": g2, "items": []}]})
    pm.save(pdb)
    app._stamp_docs_sent(g2, [6, 8], src="upload", user="t")
    pk = [p for po in pm.load()["purchase_orders"] for p in po["packages"]
          if p["tracking_number"] == g2][0]
    check("a Purchases parcel keeps gaash_docs_at", bool(pk.get("gaash_docs_at")))
    check("a Purchases parcel keeps gaash_docs_types", pk.get("gaash_docs_types") == [6, 8])
    check("and it gets the clearance record too",
          bool(gm.clearance_get(g2).get("sent_at")))

    # a broken purchases store must not cost the clearance stamp — the whole
    # reason the two stamps sit in separate try blocks
    g3 = "GWD000000022"
    real = pm.load
    pm.load = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("purchases.json is corrupt"))
    try:
        app._stamp_docs_sent(g3, [6], src="link", user="t")
    finally:
        pm.load = real
    check("a corrupt purchases.json still leaves a clearance stamp",
          bool(gm.clearance_get(g3).get("sent_at")))


# ── the wiring guarantee: every button reaches the stamp ─────────────────────
def ui_wiring_guarantee():
    src = (HERE / "web" / "index.html").read_text(encoding="utf-8")

    check("the per-board stamp hook is gone (it is what the 7 Leluxe buttons lacked)",
          "GUD_STAMP" not in src and "stampCb" not in src)

    go = src[src.index("async function gaashUploadGo("):]
    go = go[:go.index("\nfunction ")]
    check("the shared Go handler posts the stamp",
          '"/api/gaash/docs_sent"' in go and 'src:"link"' in go)
    check("the stamp carries the picked case", "case:GUD_CASE" in go)

    # every call site passes ONE argument — a second one would be a private
    # per-caller behaviour, which is exactly how seven buttons drifted silently
    sites = re.findall(r"gaashUploadOpenGwd\((.*?)\)[;\"'`]", src)
    sites = [s for s in sites if "function" not in s]
    check(f"every gaashUploadOpenGwd call site takes only a GWD ({len(sites)} sites)",
          sites and all("," not in s for s in sites))
    check("all eleven upload buttons still exist",
          len(sites) + len(re.findall(r"gaashUploadOpen\('", src)) >= 11)

    # the popup's own buttons may only go through the stamping handler
    body = src[src.index("function gaashUploadOpenGwd("):]
    body = body[:body.index("async function gaashUploadGo(")]
    check("the popup's buttons only call the stamping handler",
          body.count("gaashUploadGo(") == 2 and "window.open" not in body)

    check("the wizard sends the case too", "gaash_case:GU.case" in src)
    check("the case picker uses the pill helper, not hand-rolled markup",
          "hexPill(c.color" in src and "hexPill(o.color" in src)
    # the blocked message tells the owner to press this — it has to exist
    check("the ⟳ AZ (2) columns button exists and re-reads the schema",
          "function gmCaseCols(" in src and '"/api/leluxe/discover_source"' in src)


# ── the AZ (2) write ─────────────────────────────────────────────────────────
CALLS = []
TASK = {}


def fake_http(url, method="GET", body=None, _retried=False):
    CALLS.append((method, url, body))
    if method == "GET" and "/task/T9" in url:
        return 200, TASK
    return 200, {}


def _field(fid, name, ftype, value=None, options=None):
    f = {"id": fid, "name": name, "type": ftype}
    if options is not None:
        f["type_config"] = {"options": options}
    if value is not None:
        f["value"] = value
    return f


CASE_OPTS = [{"id": "o1", "name": "straight", "color": "#2ecd6f"},
             {"id": "o2", "name": "wrong id", "color": "#04A9F4"}]


def _az2_row(gwd):
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("INSERT INTO leluxe_orders(kind,name,data_json) VALUES(?,?,?)",
                  ("package", "📦 " + gwd,
                   json.dumps({"tracking_number": gwd, "source_task_id": "T9"})))


def az2_write():
    import cfg
    global TASK
    conf = cfg.load()
    cfg.set_path(conf, "leluxe.source_list_id", "SRC1")
    cfg.save(conf)
    os.environ["CLICKUP_API_TOKEN"] = "test-token"
    real = leluxe._http
    leluxe._http = fake_http
    try:
        g = "GWD000000030"
        _az2_row(g)
        gm.stamp_docs_sent(g, types=[6], src="upload", at="2026-09-05T08:00:00Z",
                           case="wrong id")

        # 1 — an empty task takes both values
        TASK = {"id": "T9", "custom_fields": [
            _field("f1", "Gaash Case", "drop_down", None, CASE_OPTS),
            _field("f2", "Sent to Gaash", "date")]}
        CALLS.clear()
        rep, err = leluxe.az2_push_clearance(g)
        posts = [c for c in CALLS if c[0] == "POST" and "/field/" in c[1]]
        check("both columns are written", not err and len(posts) == 2)
        case_post = [c for c in posts if "/field/f1" in c[1]]
        date_post = [c for c in posts if "/field/f2" in c[1]]
        check("the case goes as its ClickUp option id",
              case_post and case_post[0][2] == {"value": "o2"})
        check("the date goes as epoch millis",
              date_post and str(date_post[0][2]["value"]).isdigit()
              and len(str(date_post[0][2]["value"])) >= 11)
        check("the write is journalled for undo",
              len(leluxe.az2_push_history(20)) >= 2)

        # 2 — a value a human typed is never overwritten
        gm.clearance_push_result(g, "done", "", case="wrong id", date="2026-09-05")
        TASK = {"id": "T9", "custom_fields": [
            _field("f1", "Gaash Case", "drop_down", "o1", CASE_OPTS),
            _field("f2", "Sent to Gaash", "date")]}
        gm.set_case(g, "wrong id")
        CALLS.clear()
        rep, err = leluxe.az2_push_clearance(g)
        check("a human's case is left alone",
              not [c for c in CALLS if c[0] == "POST" and "/field/f1" in c[1]])
        check("and it says why", any("human" in n for n in (rep or {}).get("notes") or []))

        # 3 — the date is write-once
        TASK = {"id": "T9", "custom_fields": [
            _field("f1", "Gaash Case", "drop_down", "o2", CASE_OPTS),
            _field("f2", "Sent to Gaash", "date", "1700000000000")]}
        CALLS.clear()
        leluxe.az2_push_clearance(g)
        check("an existing date is never moved",
              not [c for c in CALLS if c[0] == "POST" and "/field/f2" in c[1]])

        # 4 — the column doesn't exist yet: blocked, and nothing is written
        TASK = {"id": "T9", "custom_fields": []}
        CALLS.clear()
        rep, err = leluxe.az2_push_clearance(g)
        check("a missing column blocks instead of erroring",
              not err and len((rep or {}).get("blocked") or []) == 2)
        check("and posts nothing",
              not [c for c in CALLS if c[0] == "POST" and "/field/" in c[1]])
        gm.clearance_push_result(g, "blocked", "no column")
        check("a blocked parcel is not re-queued behind the owner's back",
              gm.enqueue_push(g) is False)
        # ...but creating the column MUST re-arm it, or the parcels that made
        # the owner create it are exactly the ones left behind
        check("creating the column re-queues what was blocked",
              gm.clearance_requeue_blocked() >= 1
              and gm.clearance_get(g).get("push_state") == "pending")

        # 5 — an option ClickUp doesn't have is refused, not invented
        gm.clearance_push_result(g, "idle", "")
        gm.set_case(g, "brand new case type")
        TASK = {"id": "T9", "custom_fields": [
            _field("f1", "Gaash Case", "drop_down", None, CASE_OPTS),
            _field("f2", "Sent to Gaash", "date", "1700000000000")]}
        CALLS.clear()
        rep, err = leluxe.az2_push_clearance(g)
        check("an unknown option is refused",
              not [c for c in CALLS if c[0] == "POST" and "/field/f1" in c[1]]
              and any("not an option" in n for n in (rep or {}).get("notes") or []))

        # 6 — a Purchases parcel has no AZ (2) task: skipped, zero HTTP
        g2 = "GWD000000031"
        gm.stamp_docs_sent(g2, types=[6], src="link", at="2026-09-05T08:00:00Z")
        CALLS.clear()
        rep, err = leluxe.az2_push_clearance(g2)
        check("a parcel with no AZ (2) task is skipped, not failed",
              not err and (rep or {}).get("skipped"))
        check("and it makes no HTTP call at all", not CALLS)
    finally:
        leluxe._http = real
        os.environ.pop("CLICKUP_API_TOKEN", None)


def az2_undo_date():
    """The latent bug this feature is the first to reach: ClickUp clears a DATE
    with null and rejects "". 'Sent to Gaash' is the first date field ever
    journalled through az2_undo, so undoing one used to 400."""
    global TASK
    real = leluxe._http
    leluxe._http = fake_http
    os.environ["CLICKUP_API_TOKEN"] = "test-token"
    try:
        TASK = {"id": "T9", "custom_fields": [
            _field("f2", "Sent to Gaash", "date", "1757030400000")]}
        with db.connect() as c:
            pid = c.execute(
                """INSERT INTO az2_pushes (row_id, task_id, field, old_value,
                   new_value, snapshot_json, ts, user, state)
                   VALUES (?,?,?,?,?,?,?,?,'pushed')""",
                (1, "T9", "cf:Sent to Gaash", "", "1757030400000", "{}",
                 db.now_iso(), "t")).lastrowid
        CALLS.clear()
        entry, err = leluxe.az2_undo(pid, "t")
        posts = [c for c in CALLS if c[0] == "POST" and "/field/f2" in c[1]]
        check("the undo finds a column the working schema never had", not err)
        check("and clears the date with null, not an empty string",
              posts and posts[0][2] == {"value": None})
    finally:
        leluxe._http = real
        os.environ.pop("CLICKUP_API_TOKEN", None)


# ── the report ───────────────────────────────────────────────────────────────
def case_report():
    import tracking
    with db.connect() as c:
        c.execute("DELETE FROM gaash_clearance")
        c.execute("DELETE FROM gaash_msgs")
        c.execute("DELETE FROM gaash_threads")

    cache = {}

    def mk(gwd, sent, released, case):
        gm.stamp_docs_sent(gwd, types=[6], src="upload", at=sent, case=case)
        if released:
            cache[gwd] = {"events": [{"code": "K2", "text": "Cleared customs",
                                      "time": released}]}

    # Thursday 2026-09-03 → Sunday 2026-09-06 is THREE calendar days. On the
    # forecast's working-day clock it would be one; this report must say three.
    mk("GWD000000040", "2026-09-03T09:00:00+03:00", "2026-09-06T09:00:00+03:00", "straight")
    for i, d in enumerate([5, 7, 9, 11, 13]):
        mk(f"GWD00000005{i}", "2026-09-01T09:00:00+03:00",
           f"2026-09-{1 + d:02d}T09:00:00+03:00", "wrong id")
    mk("GWD000000060", "2026-09-01T09:00:00+03:00", None, "straight")     # still waiting
    mk("GWD000000061", "2026-09-20T09:00:00+03:00",
       "2026-09-02T09:00:00+03:00", "straight")                          # released first

    real = tracking._load_cache
    tracking._load_cache = lambda *a, **k: cache
    forecast._MODEL = None
    try:
        d = forecast.case_report()
    finally:
        tracking._load_cache = real

    rows = {r["gwd"]: r for r in d["rows"]}
    check("Thu → Sun is 3 CALENDAR days, not 1 working day",
          rows["GWD000000040"]["days"] == 3)
    check("a parcel still with GAASH has no number, and says why",
          rows["GWD000000060"]["days"] is None
          and "not cleared" in (rows["GWD000000060"].get("reason") or ""))
    check("a parcel released before we sent is quarantined, not averaged in",
          d["weird"] == 1 and rows["GWD000000061"]["days"] is None)

    cases = {c["case"]: c for c in d["cases"]}
    check("a case with 5 parcels reports a median",
          cases["wrong id"]["ready"] and cases["wrong id"]["n"] == 5
          and cases["wrong id"]["p50"] == 9.0)   # median of 5,7,9,11,13
    check("a case with 1 parcel reports the count, never a median",
          not cases["straight"]["ready"] and cases["straight"]["p50"] is None)
    check("the report says which unit it is in", d["unit"] == "calendar days")
    check("the ready case leads the comparison", d["cases"][0]["case"] == "wrong id")


def routes_exist():
    src = (HERE / "app.py").read_text(encoding="utf-8")
    for r in ("/api/gaash/docs_sent", "/api/gaash/case_report",
              "/api/leluxe/discover_source"):
        check(f"route {r} exists", f'"{r}"' in src)
    check("the discover_source route re-queues blocked parcels",
          "clearance_requeue_blocked()" in src)
    check("the docs_sent route is fulfillment-gated",
          re.search(r'/api/gaash/docs_sent".*?\n@auth\.require\("edit_fulfillment"\)',
                    src, re.S) is not None)


def main():
    print("resolver:");            resolver()
    print("server writer:");       server_writer()
    print("ui wiring guarantee:"); ui_wiring_guarantee()
    print("az2 write:");           az2_write()
    print("az2 undo (date):");     az2_undo_date()
    print("case report:");         case_report()
    print("routes:");              routes_exist()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all clearance checks passed ✓")


if __name__ == "__main__":
    main()
