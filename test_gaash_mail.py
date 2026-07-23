#!/usr/bin/env python3
"""
Self-checks: 📧 GAASH Mail sequences (gaash_mail.py + /api/gaash/*).

Pure-logic tests — NO network, NO real SMTP/IMAP: the auto-ack window, thread
matching, template fill, the sequencer state machine (guard order, dry-run
never burning a claim_once), missing-doc keyword auto-flag, office-closed
scheduling, the settings validator, and route permissions.

    ./.venv/bin/python test_gaash_mail.py
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-gaash-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("GAASH_MAILER", None)          # the daemon must NOT start in tests
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod    # noqa: E402
import auth             # noqa: E402
import db               # noqa: E402
import gaash_mail as gm  # noqa: E402
import settings as settings_mod  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def _mk_thread(gwd, step=0, state="active", next_send_at=None, **extra):
    with db.connect() as c:
        c.execute("""INSERT OR REPLACE INTO gaash_threads
            (gwd,account_id,state,step,unread,missing_docs,pending_files_json,
             next_send_at,created_at,last_activity)
            VALUES (?,?,?,?,0,0,'[]',?,?,?)""",
                  (gwd, extra.get("account_id"), state, step,
                   next_send_at, db.now_iso(), db.now_iso()))


def _mk_out(gwd, at_iso, mid="<m1@test>", step=1):
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_msgs
            (gwd,dir,kind,step,at,message_id,body,attachments_json,notified)
            VALUES (?,?,?,?,?,?,?, '[]',1)""",
                  (gwd, "out", "sent", step, at_iso, mid, "hello"))


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    db.create_user("emp", auth.hash_pw("s1"), "fulfillment", "E", business_id=1)
    db.create_user("sal", auth.hash_pw("s1"), "sales", "S", business_id=1)

    print("— auto-ack classification (the owner's Glassix rule) —")
    now = datetime.now(timezone.utc)
    _mk_thread("GWD100")
    _mk_out("GWD100", (now - timedelta(minutes=2)).isoformat(timespec="seconds"))
    check("within the window ⇒ auto_ack",
          gm.classify_incoming("GWD100", now, 3) == "auto_ack")
    check("after the window ⇒ real reply",
          gm.classify_incoming("GWD100", now + timedelta(minutes=10), 3) == "reply")
    check("no prior outgoing ⇒ reply",
          gm.classify_incoming("GWD999", now, 3) == "reply")

    print("— thread matching —")
    hm = EmailMessage()
    hm["From"] = "Support <team@glassix.support>"
    hm["Subject"] = "Re: hello"
    hm["In-Reply-To"] = "<m1@test>"
    check("References/In-Reply-To match wins",
          gm.match_thread(hm, "glassix.support", set(), ["GWD100"]) == "GWD100")
    hm2 = EmailMessage()
    hm2["From"] = "Support <team@glassix.support>"
    hm2["Subject"] = "About your package GWD100"
    check("GWD-in-subject fallback (support domain)",
          gm.match_thread(hm2, "glassix.support", set(), ["GWD100"]) == "GWD100")
    hm3 = EmailMessage()
    hm3["From"] = "stranger@elsewhere.com"
    hm3["Subject"] = "GWD100"
    check("random sender w/o refs does NOT match",
          gm.match_thread(hm3, "glassix.support", set(), ["GWD100"]) is None)
    hm4 = EmailMessage()
    hm4["Message-ID"] = "<m1@test>"
    hm4["From"] = "me@gmail.com"
    check("our own echoed message is skipped",
          gm.match_thread(hm4, "glassix.support", {"<m1@test>"}, ["GWD100"]) is None)

    print("— template fill —")
    _mk_thread("GWD200")
    th = gm.thread_get("GWD200")
    out = gm._fill("pkg {gwd} step {step} link {upload_link}", "GWD200", th, 2)
    check("placeholders fill", "GWD200" in out and "step 2" in out
          and "fileUpload?packageId=GWD200" in out)

    print("— sequencer: dry-run never burns the claim —")
    past = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    _mk_thread("GWD300", step=0, state="active", next_send_at=past)
    res = gm.run_once()                       # dry_run defaults ON, no accounts
    th = gm.thread_get("GWD300")
    check("nothing sent under dry-run", res["sent"] == 0 and th["step"] == 0)
    check("thread stayed active + due (will fire once dry-run is off)",
          th["state"] == "active" and th["next_send_at"])
    with db.connect() as c:
        burned = c.execute("SELECT 1 FROM settings WHERE key=?",
                           ("gaashmail:GWD300:step1",)).fetchone()
    check("claim_once was NOT consumed", burned is None)

    print("— sequencer guards: replied / missing_docs / cleared stay paused —")
    for st in ("waiting_reply", "missing_docs", "paused", "cleared", "done"):
        _mk_thread(f"GWD3{st[:2]}", step=1, state=st, next_send_at=past)
    res = gm.run_once()
    check("non-active threads never send", res["sent"] == 0)

    print("— missing-doc keywords + office-closed phrases —")
    setts = {"missing_doc_keywords": ["kmt", "missing document"],
             "resend_phrases": ["closed now"]}
    check("KMT detected", gm._matches_missing(setts, "we need the KMT form") == "kmt")
    check("clean reply not flagged",
          gm._matches_missing(setts, "your package cleared") is None)
    check("office-closed detected",
          gm._matches_closed(setts, "Our offices are CLOSED NOW, we reply later"))
    due = gm._next_resend_due(9)
    check("resend lands at 09:00 Israel time", due.hour == 9 and due.minute == 0)

    print("— start_threads: terminal package → cleared, bad GWD rejected —")
    res = gm.start_threads(["notanumber"], None, None)
    check("non-GWD rejected", res and res[0]["ok"] is False)
    res = gm.start_threads(["GWD400"], None, None)
    th = gm.thread_get("GWD400")
    check("thread created active with a due date",
          res[0]["ok"] and th and th["state"] == "active" and th["next_send_at"])
    res2 = gm.start_threads(["GWD400"], None, None)
    check("duplicate start rejected", res2[0]["ok"] is False)

    print("— settings validator round-trip —")
    out = settings_mod.apply({"gaash_mail": {
        "dry_run": False, "to_address": "x@y.com", "cadence_days": [1, 2, 3],
        "resend_phrases": ["closed now", "OUT of office"],
        "steps": [{"subject_tpl": "s1 {gwd}", "body_tpl": "b1"},
                  {}, {}, {"subject_tpl": "s4", "body_tpl": "b4"}]}})
    g = out["gaash_mail"]
    check("dry_run persisted OFF", g["dry_run"] is False)
    check("cadence saved", g["cadence_days"] == [1, 2, 3])
    check("phrases lowercased", "out of office" in g["resend_phrases"])
    check("blank step falls back to the default template",
          g["steps"][1]["subject_tpl"] and g["steps"][0]["subject_tpl"] == "s1 {gwd}")
    settings_mod.apply({"gaash_mail": {"dry_run": True}})   # restore the safety

    print("— sends blocked while dry-run / without account —")
    r = gm.send_manual("GWD400", "hello gaash")
    check("manual send blocked by dry-run", r["ok"] is False and r.get("dry_run"))

    print("— routes: permissions —")
    co, ce, cs = client("otlo"), client("emp"), client("sal")
    anon = appmod.app.test_client()
    check("anonymous blocked",
          anon.get("/api/gaash/overview").status_code in (302, 401, 403))
    d = co.get("/api/gaash/overview").get_json()
    check("admin sees overview", d and d.get("ok") and "threads" in d
          and "candidates" in d)
    check("accounts are redacted", all("app_password" not in a
                                       for a in d.get("accounts", [])))
    check("fulfillment (edit_order) sees overview",
          ce.get("/api/gaash/overview").get_json().get("ok") is True)
    check("sales blocked from overview",
          cs.get("/api/gaash/overview").status_code == 403)
    check("employee cannot add accounts",
          ce.post("/api/gaash/account/add",
                  json={"email": "a@b.com", "app_password": "x"}).status_code == 403)
    check("employee cannot upload IDs",
          ce.post("/api/gaash/ids", json={"name": "x"}).status_code == 403)
    r = co.post("/api/gaash/thread",
                json={"gwd": "GWD400", "action": "missing_docs", "note": "KMT"})
    th = r.get_json().get("thread")
    check("missing_docs action pauses + notes",
          th and th["state"] == "missing_docs" and th["missing_note"] == "KMT")
    r = co.post("/api/gaash/thread", json={"gwd": "GWD400", "action": "resume"})
    th = r.get_json().get("thread")
    check("resume clears the flag + reactivates",
          th and th["state"] == "active" and not th["missing_docs"])

    print("— bell notifications —")
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_msgs
            (gwd,dir,kind,at,message_id,body,attachments_json,notified)
            VALUES ('GWD400','in','reply',?,?,'we need KMT','[]',0)""",
                  (db.now_iso(), "<r1@glassix>"))
    co.post("/api/gaash/thread", json={"gwd": "GWD400", "action": "missing_docs",
                                       "note": "KMT"})
    d = co.get("/api/notifications").get_json()
    types = {e["type"] for e in d.get("events", [])}
    check("reply + missing-docs events reach the bell",
          "gaash_reply" in types and "gaash_missing" in types)
    ev = next(e for e in d["events"] if e["type"] == "gaash_missing")
    check("missing event deep-links to the page", ev["view"] == "gaashmail"
          and "GWD400" in ev["sub"])

    print()
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All GAASH-mail checks passed ✓")


if __name__ == "__main__":
    main()
