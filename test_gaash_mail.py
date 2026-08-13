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


def _del_thread(gwd):
    with db.connect() as c:
        c.execute("DELETE FROM gaash_threads WHERE gwd=?", (gwd,))


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

    print("— v2 seed (legacy settings chain → sequences-as-data) —")
    gm.migrate_v2()
    gm.migrate_v2()                                   # idempotent
    seqs = gm.sequences_list()
    check("default sequence seeded once", len(seqs) == 1
          and seqs[0]["id"] == "seq_default" and len(seqs[0]["steps"]) == 4)
    # (2026-08-04) migrate_v2 later gained a 5th seeded template (tpl_followup) —
    # assert the four clearance seeds exist rather than pinning the total count.
    tpl_ids = {t["id"] for t in gm.templates_list()}
    check("seed templates exist",
          {"tpl_seed1", "tpl_seed2", "tpl_seed3", "tpl_seed4"} <= tpl_ids)

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

    print("— accounts: app-password upsert + protocol-scoped errors —")
    check("_clean_pw strips spaces/NBSP/zero-width/BOM",
          gm._clean_pw(" abcd efgh\u00a0ijkl\u200b\u200c\u200d\u2060\ufeffmnop ")
          == "abcdefghijklmnop")

    class _FakeSMTP:                       # gmail double: rejects any other pw
        ok_pw = "goodpassword1234"

        def login(self, email, pw):
            if pw != _FakeSMTP.ok_pw:
                raise gm.smtplib.SMTPAuthenticationError(535, b"bad creds")

        def send_message(self, msg):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeIMAP:
        def __init__(self, host, port):
            pass

        def login(self, email, pw):
            if pw != _FakeSMTP.ok_pw:
                raise gm.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

        def status(self, *a):
            return "OK", [b"INBOX (UIDVALIDITY 7 UIDNEXT 42)"]

        def logout(self):
            pass

    real_connect, real_imap = gm._smtp_connect, gm.imaplib.IMAP4_SSL
    real_send, real_chk = gm._smtp_send, gm._check_account
    gm._smtp_connect = lambda timeout=30: _FakeSMTP()
    gm.imaplib.IMAP4_SSL = _FakeIMAP
    try:
        r1 = gm.add_account("Upsert@Test.com", "good password 1234")
        with db.connect() as c:
            row = c.execute("SELECT * FROM gaash_accounts WHERE email=?",
                            ("upsert@test.com",)).fetchone()
        check("fresh add verifies + stores the cleaned password",
              r1["ok"] and not r1.get("updated")
              and row and row["app_password"] == "goodpassword1234")
        aid1 = r1["id"]
        _mk_thread("GWD700", step=1, state="active", account_id=aid1)

        _FakeSMTP.ok_pw = "newpassword5678"
        r2 = gm.add_account("  UPSERT@test.com", "new password 5678")
        with db.connect() as c:
            row = c.execute("SELECT * FROM gaash_accounts WHERE id=?",
                            (aid1,)).fetchone()
            n = c.execute("SELECT COUNT(*) n FROM gaash_accounts WHERE email=?",
                          ("upsert@test.com",)).fetchone()["n"]
        check("re-add same email updates in place (same id, no duplicate)",
              r2["ok"] and r2.get("updated") and r2["id"] == aid1 and n == 1
              and row["app_password"] == "newpassword5678"
              and row["last_error"] is None)
        check("the thread kept its sender",
              gm.thread_get("GWD700")["account_id"] == aid1)

        r3 = gm.add_account("upsert@test.com", "wrongpassword999")
        with db.connect() as c:
            row = c.execute("SELECT app_password FROM gaash_accounts "
                            "WHERE id=?", (aid1,)).fetchone()
        check("failed re-verify never clobbers the stored password",
              r3["ok"] is False and r3["error"].startswith(gm._AUTH_HELP)
              and row["app_password"] == "newpassword5678")

        from email.message import EmailMessage as _EM
        junk_acct = {"email": "upsert@test.com",
                     "app_password": "new pass word 5678"}
        try:
            gm._smtp_send(junk_acct, _EM())
            login_ok = True
        except Exception:  # noqa
            login_ok = False
        check("login sites clean stored junk too (defense in depth)", login_ok)

        settings_mod.apply({"gaash_mail": {"dry_run": False,
                                           "to_address": "gaash@test.com"}})

        def _boom(acct, msg):
            raise gm.smtplib.SMTPAuthenticationError(535, b"revoked")
        gm._smtp_send = _boom
        r4 = gm.send_manual("GWD700", "hello")
        with db.connect() as c:
            arow = c.execute("SELECT last_error FROM gaash_accounts WHERE id=?",
                             (aid1,)).fetchone()
            leftover = c.execute("SELECT 1 FROM gaash_msgs WHERE gwd='GWD700' "
                                 "AND dir='out'").fetchone()
        check("auth failure stamps thread AND account (SMTP-prefixed)",
              r4["ok"] is False
              and gm.thread_get("GWD700")["last_error"].startswith(gm._AUTH_HELP)
              and arow["last_error"].startswith("SMTP: " + gm._AUTH_HELP)
              and leftover is None)
        # the refusal Gmail actually sent rides along — without it a BLOCKED
        # sign-in is indistinguishable from a bad password
        check("...and carries Gmail's own reply",
              "Gmail said:" in arow["last_error"] and "revoked" in arow["last_error"])

        gm._check_account = lambda a, s, m, t: ([], {
            "imap_last_uid": 1, "imap_uidvalidity": 7,
            "last_check": gm.now_iso(), "seen_ids_json": "[]"})
        gm.check_replies()
        with db.connect() as c:
            arow = c.execute("SELECT last_error FROM gaash_accounts WHERE id=?",
                             (aid1,)).fetchone()
        check("a clean IMAP poll keeps the SMTP-owned error",
              arow["last_error"].startswith("SMTP: " + gm._AUTH_HELP))
        gm._account_set_error(aid1, "some imap trouble")
        gm.check_replies()
        with db.connect() as c:
            arow = c.execute("SELECT last_error FROM gaash_accounts WHERE id=?",
                             (aid1,)).fetchone()
        check("a clean IMAP poll clears its own (unprefixed) error",
              arow["last_error"] is None)

        gm._smtp_send = _boom
        rt = gm.send_test(aid1)
        with db.connect() as c:
            arow = c.execute("SELECT last_error FROM gaash_accounts WHERE id=?",
                             (aid1,)).fetchone()
        check("send_test maps auth failure to the friendly help + stamps",
              rt["ok"] is False and rt["error"].startswith(gm._AUTH_HELP)
              and arow["last_error"].startswith("SMTP: " + gm._AUTH_HELP))
        check("send_test on a ghost id says so",
              gm.send_test("acct_nope")["error"] == "account not found")

        gm._smtp_send = lambda acct, msg: None
        rt2 = gm.send_test(aid1)
        r5 = gm.send_manual("GWD700", "hello again")
        with db.connect() as c:
            arow = c.execute("SELECT last_error FROM gaash_accounts WHERE id=?",
                             (aid1,)).fetchone()
        check("working test-send + send clear the SMTP error",
              rt2["ok"] and rt2["sent_to"] == "upsert@test.com"
              and r5["ok"] and arow["last_error"] is None)
    finally:
        gm._smtp_connect, gm.imaplib.IMAP4_SSL = real_connect, real_imap
        gm._smtp_send, gm._check_account = real_send, real_chk
        settings_mod.apply({"gaash_mail": {"dry_run": True}})
        _del_thread("GWD700")

    print("— declaration contents: blank titles resolve, regrouped packages —")
    import purchases as purch
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO orders (order_code, data_json) "
                  "VALUES ('OTL-T90', ?)",
                  (json.dumps({"items": [{"asin": "B0TESTAAA1",
                                          "title": "Kindle Paperwhite 16GB"}]}),))
    pdb = purch.load()
    pdb["purchase_orders"].append({
        "po_id": "PO-T9", "ship_to": "Decl Buyer", "packages": [
            {"package_no": 1, "tracking_number": "GWD009100001",
             "otlobly_status": "", "items": [
                 {"item_id": "it_a", "title": "", "asin": "B0TESTAAA1",
                  "qty": 2, "customer_order_id": "OTL-T90"},
                 {"item_id": "it_b", "title": "", "asin": "B0TESTBBB2",
                  "qty": 1, "customer_order_id": None},
                 {"item_id": "it_c", "title": "Named Product", "asin": None,
                  "qty": 1}]}]})
    purch.save(pdb)
    got = gm.package_contents("GWD009100001")
    check("blank title resolves from the customer order (with qty)",
          {"title": "Kindle Paperwhite 16GB", "qty": 2} in got)
    check("unresolvable blank falls back to the ASIN label (count integrity)",
          {"title": "Amazon item B0TESTBBB2", "qty": 1} in got)
    check("a titled item passes through untouched",
          {"title": "Named Product", "qty": 1} in got and len(got) == 3)

    import amazon_import
    real_imp = amazon_import.import_product
    calls = []

    def _fake_import(url_or_asin, config=None, refresh=False):
        calls.append(url_or_asin)
        return {"title": "Fetched Widget Pro", "asin": url_or_asin}
    amazon_import.import_product = _fake_import
    try:
        r = gm._declaration_fill_titles("GWD009100001")
    finally:
        amazon_import.import_product = real_imp
    pdb = purch.load()
    its = {it["item_id"]: it for p in pdb["purchase_orders"]
           for k in p.get("packages") or []
           if str(k.get("tracking_number")) == "GWD009100001"
           for it in k["items"]}
    check("fill resolves order-chain + fetches ONLY the orderless asin",
          r == {"filled": 2, "fetched": 1} and calls == ["B0TESTBBB2"])
    check("resolved titles persist into the PO store",
          its["it_a"]["title"] == "Kindle Paperwhite 16GB"
          and its["it_b"]["title"] == "Fetched Widget Pro"
          and its["it_c"]["title"] == "Named Product")
    check("a second fill is a no-op (nothing blank left)",
          gm._declaration_fill_titles("GWD009100001") == {"filled": 0,
                                                          "fetched": 0})

    res = gm.declaration_make("GWD009100001")
    check("declaration builds for the package", res.get("ok") is True)
    # read the bytes the EMAIL would carry, not a filed copy — that is the
    # artefact that has to be right, and nothing is filed any more
    att = gm.declaration_attachment("GWD009100001")
    if att:
        pdf = att[1]
        check("the PDF lists the real products, not (not itemised)",
              b"Kindle Paperwhite" in pdf and b"Fetched Widget" in pdf
              and b"not itemised" not in pdf)

    now_ms2 = str(int(now.timestamp() * 1000))
    with db.connect() as c:              # regrouped mirror: GWD on the PACKAGE row
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('package','PACKAGE 1','on hold',?,?)""",
                  (now_ms2, json.dumps({"tracking_number": "GWD009100002",
                                        "fields": {"NAME": "x"}})))
        pkg_id = c.execute("SELECT id FROM leluxe_orders WHERE "
                           "kind='package' AND name='PACKAGE 1'").fetchone()["id"]
        c.execute("""INSERT INTO leluxe_orders
            (kind,name,status,date_created,parent_local_id,data_json)
            VALUES ('item','Steve Madden Wallet Brown','on hold',?,?,?)""",
                  (now_ms2, pkg_id,
                   json.dumps({"fields": {"Quantity ordered ": "3"}})))
        c.execute("""INSERT INTO leluxe_orders
            (kind,name,status,date_created,parent_local_id,data_json)
            VALUES ('item','PACKAGE 2','on hold',?,?,?)""",
                  (now_ms2, pkg_id, json.dumps({"fields": {}})))
    check("regrouped package: children found via the package row, labels skipped",
          gm.package_contents("GWD009100002")
          == [{"title": "Steve Madden Wallet Brown", "qty": 3}])
    with db.connect() as c:              # tidy the fixtures
        c.execute("DELETE FROM leluxe_orders WHERE parent_local_id=?", (pkg_id,))
        c.execute("DELETE FROM leluxe_orders WHERE id=?", (pkg_id,))
        c.execute("DELETE FROM orders WHERE order_code='OTL-T90'")
    pdb = purch.load()
    pdb["purchase_orders"] = [p for p in pdb["purchase_orders"]
                              if p.get("po_id") != "PO-T9"]
    purch.save(pdb)

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

    print("— v2: send-window / business-day math (Palestine time) —")
    win = {"tz": "Asia/Hebron", "days": [6, 0, 1, 2, 3],
           "start": "09:00", "end": "17:00"}
    from zoneinfo import ZoneInfo
    hebron = ZoneInfo("Asia/Hebron")
    fri = datetime(2026, 7, 24, 12, 0, tzinfo=hebron)         # Friday (weekend)
    na = gm.next_allowed(fri, win)
    check("Friday defers to Sunday 09:00",
          na.weekday() == 6 and na.hour == 9 and na.minute == 0)
    thu = datetime(2026, 7, 23, 10, 0, tzinfo=hebron)         # Thursday
    d2 = gm.add_business_days(thu, 2, win)
    check("+2 business days from Thu skips Fri+Sat → Monday",
          d2.weekday() == 0 and d2.day == 27)
    inw = gm.next_allowed(datetime(2026, 7, 26, 11, 0, tzinfo=hebron), win)
    check("inside the window stays put", inw.hour == 11 and inw.weekday() == 6)

    print("— v2: templates + sequence builder CRUD —")
    t1 = gm.template_save({"name": "T-Email", "subject_tpl": "s {gwd}",
                           "body_tpl": "b {gwd}"})
    check("template created", t1["ok"])
    bad = gm.sequence_save({"name": "X", "steps": [{"kind": "auto_email",
                                                    "template_id": "nope"}]})
    check("step without a real template rejected", bad["ok"] is False)
    sq = gm.sequence_save({"name": "Two-step", "to_address": "other@platform.com",
                           "goal": "reply",
                           "send_window": {"days": [0, 1, 2, 3, 4],
                                           "start": "08:00", "end": "18:00"},
                           "steps": [
                               {"kind": "auto_email", "template_id": t1["id"],
                                "delay_days": 0},
                               {"kind": "task", "task_note": "call them",
                                "delay_days": 1},
                               {"kind": "auto_email", "template_id": t1["id"],
                                "delay_days": 2}]})
    check("sequence saved with email→task→email", sq["ok"])
    seq = gm.sequence_get(sq["id"])
    check("steps ordered + window persisted",
          [s["kind"] for s in seq["steps"]] == ["auto_email", "task", "auto_email"]
          and json.loads(seq["send_window_json"])["start"] == "08:00")
    check("used template cannot be deleted",
          gm.template_remove(t1["id"])["ok"] is False)

    print("— v2: task steps pause until ticked —")
    _mk_thread("GWD500", step=1, state="active",
               next_send_at=(now - timedelta(minutes=1)).isoformat(timespec="seconds"))
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET seq_id=? WHERE gwd='GWD500'",
                  (sq["id"],))
    res = gm.send_step("GWD500")                       # step 2 = the task
    th = gm.thread_get("GWD500")
    check("task step pauses as waiting_task", res.get("task") is True
          and th["state"] == "waiting_task" and th["next_send_at"] is None)
    res = gm.task_done("GWD500")
    th = gm.thread_get("GWD500")
    check("task_done advances + reschedules", res["ok"] and th["step"] == 2
          and th["state"] == "active" and th["next_send_at"])
    check("task bubbles recorded", [m["kind"] for m in gm.msgs_for("GWD500")]
          == ["task", "task_done"])

    print("— enroll picker: candidates span Leluxe + Purchases —")
    import purchases
    pdb = purchases.load()
    pdb["purchase_orders"].append({
        "po_id": "PO-T1", "ship_to": "Test Buyer", "packages": [
            {"package_no": 1, "tracking_number": "GWD009000001",
             "otlobly_status": "", "items": [{"customer_name": "Zaid"}]},
            {"package_no": 2, "tracking_number": "GWD009000002",
             "otlobly_status": "تم التسليم delivered",       # delivered → excluded
             "items": []}]})
    purchases.save(pdb)
    cands = gm.candidates()
    pc = [c for c in cands if c["gwd"] == "GWD009000001"]
    check("Purchases package is a candidate, source-tagged",
          pc and pc[0]["source"] == "purchases" and pc[0]["name"] == "Test Buyer")
    check("delivered Purchases package excluded",
          not any(c["gwd"] == "GWD009000002" for c in cands))
    check("a Purchases-only GWD enrolls (start_threads)",
          gm.start_threads(["GWD009000001"], None, None)[0]["ok"]
          and gm.thread_get("GWD009000001"))
    check("an enrolled GWD drops out of candidates",
          not any(c["gwd"] == "GWD009000001" for c in gm.candidates()))

    print("— v2: auto-enroll rules (HubSpot-style criteria) —")
    r1 = gm.rule_save({"name": "stuck on ID",
                       "cond": {"gash_status": " customer ID", "min_age_days": 3},
                       "seq_id": sq["id"], "mode": "queue", "enabled": True})
    check("legacy-shape rule saved", r1["ok"])
    check("legacy cond auto-converts to criteria groups",
          gm.rules_list()[0]["cond"] == {"groups": [{"crits": [
              {"field": "gash_status", "op": "is", "value": "customer ID"},
              {"field": "age_days", "op": "gte", "value": 3}]}]})
    gm.rule_remove(r1["id"])
    check("rule removed", not gm.rules_list())
    nm = gm._cond_norm({"groups": [{"crits": [
        {"field": "nope", "op": "is", "value": "x"},
        {"field": "status", "op": "zap", "value": "x"},
        {"field": "age_days", "op": "gte", "value": "999"},
        {"field": "source", "op": "is", "value": "PURCHASES"}]}]})
    check("sanitizer drops unknown field/op, clamps age, folds source",
          nm == {"groups": [{"crits": [
              {"field": "age_days", "op": "gte", "value": 365},
              {"field": "source", "op": "is", "value": "purchases"}]}]})

    cdx = {"gwd": "GWDX", "source": "purchases", "status": "stuck on Customer ID",
           "gash_status": None, "name": "Ali", "customers": "Ahmad، Sara",
           "bucket": "", "label": None}

    def m(cnd, age=5.0):
        return gm._cond_match(gm._cond_norm(cnd), cdx, lambda c: age)

    def g1(*crits):
        return {"groups": [{"crits": list(crits)}]}

    check("evaluator: contains folds case + whitespace",
          m(g1({"field": "status", "op": "contains", "value": " customer  id"})))
    check("evaluator: AND inside a group",
          not m(g1({"field": "status", "op": "contains", "value": "id"},
                   {"field": "source", "op": "is", "value": "leluxe"})))
    check("evaluator: OR across groups",
          m({"groups": [
              {"crits": [{"field": "source", "op": "is", "value": "leluxe"}]},
              {"crits": [{"field": "customers", "op": "contains",
                          "value": "sara"}]}]}))
    check("evaluator: empty / not_empty / is_not",
          m(g1({"field": "gash_status", "op": "empty"}))
          and not m(g1({"field": "gash_status", "op": "not_empty"}))
          and m(g1({"field": "name", "op": "is_not", "value": "Omar"})))
    check("evaluator: age gte/lte + unknown age fails closed",
          m(g1({"field": "age_days", "op": "gte", "value": 4}))
          and not m(g1({"field": "age_days", "op": "lte", "value": 4}))
          and not m(g1({"field": "age_days", "op": "gte", "value": 1}), age=None))
    check("no criteria = every candidate", m({"groups": []}))

    print("— v2: candidate ages + end-to-end run_rules —")
    pdb = purchases.load()
    for p in pdb["purchase_orders"]:
        if p["po_id"] == "PO-T1":
            p["created_at"] = (now - timedelta(days=4)).isoformat(timespec="seconds")
            p["packages"].append({"package_no": 3,
                                  "tracking_number": "GWD009000003",
                                  "otlobly_status": "بانتظار الشحن",
                                  "items": [{"customer_name": "Omar"}]})
    purchases.save(pdb)
    with db.connect() as c:
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','Watch pkg','on hold',?,?)""",
                  (str(int((now - timedelta(days=6)).timestamp() * 1000)),
                   json.dumps({"tracking_number": "GWD009000005",
                               "fields": {"GASH STATUS": "customer ID"}})))
    age_p = gm._cand_age_days({"gwd": "GWD009000001", "source": "purchases",
                               "po_id": "PO-T1"})
    age_l = gm._cand_age_days({"gwd": "GWD009000005", "source": "leluxe"})
    check("purchases age from the PO created_at", age_p and 3.5 < age_p < 4.5)
    check("leluxe age from date_created ms", age_l and 5.5 < age_l < 6.5)
    r2 = gm.rule_save({"name": "old leluxe ID holds", "cond": {"groups": [{"crits": [
        {"field": "source", "op": "is", "value": "leluxe"},
        {"field": "gash_status", "op": "is", "value": "Customer  ID"},
        {"field": "age_days", "op": "gte", "value": 3}]}]},
        "seq_id": sq["id"], "mode": "queue", "enabled": True})
    made = gm.run_rules()
    th5 = gm.thread_get("GWD009000005")
    check("run_rules proposes only the matching candidate", made == 1
          and th5 and th5["state"] == "proposed"
          and not gm.thread_get("GWD009000003"))
    gm.rule_remove(r2["id"])
    _del_thread("GWD009000005")            # reset for the cf test below

    print("— v2: board custom fields as criteria (cf:*) —")
    _del_thread("GWD009000001")                 # enrolled earlier — free it
    # a Purchases custom-field def + a value on the PO (applies to all its pkgs)
    co0 = client("otlo")
    co0.post("/api/settings", json={"custom_fields": {"po": [
        {"key": "priority", "label": "Priority", "type": "select",
         "options": [{"name": "High"}, {"name": "Low"}]}]}})
    pdb = purchases.load()
    for p in pdb["purchase_orders"]:
        if p["po_id"] == "PO-T1":
            p["custom"] = {"priority": "High"}
    purchases.save(pdb)
    # a Leluxe ClickUp field on a fresh package
    with db.connect() as c:
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','Ring pkg','on hold',?,?)""",
                  (str(int((now - timedelta(days=2)).timestamp() * 1000)),
                   json.dumps({"tracking_number": "GWD009000007",
                               "fields": {"GASH STATUS": "customer ID",
                                          "PRODUCT": "Gold Ring 18k"}})))
    cands = {c["gwd"]: c for c in gm.candidates()}
    check("purchases candidate carries its PO custom-field value",
          cands.get("GWD009000001", {}).get("cf", {}).get("Priority") == "High")
    check("leluxe candidate carries its ClickUp field value",
          "Gold Ring 18k" in cands.get("GWD009000007", {}).get("cf", {}).get("PRODUCT", ""))
    cat = {f["key"]: f for f in gm.rule_cf_fields()}
    check("cf catalog lists the Purchases def + a Leluxe field",
          cat.get("cf:Priority", {}).get("source") == "purchases"
          and "cf:PRODUCT" in cat)

    print("— v2: field_values suggests values we already have (all records) —")
    # a leluxe pkg that is NOT a candidate (delivered) but carries a field value
    with db.connect() as c:
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','Sold pkg','delivered',?,?)""",
                  (str(int(now.timestamp() * 1000)),
                   json.dumps({"tracking_number": "GWD009000009",
                               "tracking_status": {"bucket": "delivered"},
                               "fields": {"Colour": "Midnight Blue"}})))
    meta = gm.rule_field_meta()
    fv = meta["values"]
    cand_gwds = {c["gwd"] for c in gm.candidates()}
    check("field_values pulls from records that aren't candidates",
          "GWD009000009" not in cand_gwds
          and "Midnight Blue" in (fv.get("cf:Colour") or []))
    check("field_values includes a select def's defined option (even if unused)",
          "High" in (fv.get("cf:Priority") or [])
          and "Low" in (fv.get("cf:Priority") or []))
    check("overview ships field_values + cf_fields together",
          set(gm.overview()["field_values"].get("cf:Priority") or []) >= {"High", "Low"})

    print("— templates: any board column is a fillable {token} —")
    lel = gm._fill("pkg {gwd} product {PRODUCT} unknown {foo}", "GWD009000007")
    check("Leluxe field token fills; unknown token left literal",
          "GWD009000007" in lel and "Gold Ring 18k" in lel and "{foo}" in lel)
    pur = gm._fill("prio={Priority}", "GWD009000001")   # PO-T1 custom priority=High
    check("Purchases custom-field token fills from the PO",
          pur == "prio=High")
    toks = {t["token"]: t for t in gm.overview()["tpl_tokens"]}
    check("tpl_tokens lists core + every board column",
          toks.get("{gwd}", {}).get("source") == "core" and "{PRODUCT}" in toks)

    print("— {name_id}: on-package name → its Settings-mapped ID number —")
    r = co.post("/api/settings", json={"gaash_mail": {"name_ids": {
        "FAISAL": "999888777", "  Nuray  htab ": "111222333",
        "noid": "", "": "555"}}}).get_json()
    check("name_ids saved + sanitized (trim, drop empties)",
          r.get("ok") and gm._setts().get("name_ids") ==
          {"FAISAL": "999888777", "Nuray htab": "111222333"})
    check("{name_id} listed in tpl_tokens", "{name_id}" in
          {t["token"] for t in gm.overview()["tpl_tokens"]})
    now_ms = str(int(now.timestamp() * 1000))
    with db.connect() as c:
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','NID direct','on hold',?,?)""",
                  (now_ms, json.dumps({"tracking_number": "GWD009000021",
                   "fields": {"NAME ON PACKAGEE": "faisal"}})))
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('parent','NID order','on hold',?,?)""",
                  (now_ms, json.dumps({"fields": {"NAME ON PACKAGEE": "NURAY HTAB"}})))
        pid = c.execute("SELECT id FROM leluxe_orders WHERE name='NID order'"
                        ).fetchone()["id"]
        c.execute("""INSERT INTO leluxe_orders
            (kind,name,status,date_created,parent_local_id,data_json)
            VALUES ('item','NID child','on hold',?,?,?)""",
                  (now_ms, pid, json.dumps({"tracking_number": "GWD009000022",
                   "fields": {"BRAND": "x"}})))
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','NID unmapped','on hold',?,?)""",
                  (now_ms, json.dumps({"tracking_number": "GWD009000023",
                   "fields": {"NAME ON PACKAGEE": "yahia"}})))
    check("{name_id} resolves case-blind from the row's own field",
          gm._fill("id={name_id}", "GWD009000021") == "id=999888777")
    check("{name_id} falls back to the PARENT order's field",
          gm._fill("{name_id}", "GWD009000022") == "111222333")
    check("unmapped name → empty, not literal",
          gm._fill("x{name_id}y", "GWD009000023") == "xy")
    # {id_number} = the customer's own ID, falling back to the on-package name's —
    # one token covering Purchases (CRM hit) and Leluxe (name map)
    check("{id_number} falls back to the name map when the customer has none",
          gm._id_number_for("GWD009000021") == ""
          and gm._fill("{id_number}", "GWD009000021") == "999888777")
    db.upsert_customer({"customer_id": "CUS-NIDT", "match_key": "nidtest",
                        "name": "ID Fallback Test", "whatsapp": "+970599000777",
                        "id_number": "CRM-777"})
    with db.connect() as c:
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','NID crm','on hold',?,?)""",
                  (now_ms, json.dumps({"tracking_number": "GWD009000024",
                   "fields": {"NAME ON PACKAGEE": "FAISAL",
                              "PHONE IN SHIPPING": "+970599000777"}})))
    check("the customer's OWN ID wins over the name map",
          gm._fill("{id_number}", "GWD009000024") == "CRM-777"
          and gm._fill("{name_id}", "GWD009000024") == "999888777")
    # the picker/conversation columns must show what the email will ACTUALLY send
    pm, em = gm.parcel_name_map(), gm.effective_id_map()
    check("parcel_name_map reads Leluxe's own field AND the parent's",
          pm.get("GWD009000021") == "faisal" and pm.get("GWD009000022") == "NURAY HTAB")
    check("Purchases parcels are named by the PO's Main name",
          gm.parcel_name("GWD009000001") == "Test Buyer")
    check("effective_id_map matches _fill on every GWD",
          all(em.get(g, "") == gm._fill("{id_number}", g)
              for g in ("GWD009000021", "GWD009000022", "GWD009000023",
                        "GWD009000024", "GWD009000001")))
    cands = {c["gwd"]: c for c in gm.candidates()}
    if "GWD009000021" in cands:
        check("candidates carry the parcel name + the ID its email will send",
              cands["GWD009000021"]["pname"] == "faisal"
              and cands["GWD009000021"]["pname_id"] == "999888777")

    print("— picking the name per package (overrides what the boards say) —")
    _mk_thread("GWD009000023", step=0, state="active")     # board name: 'yahia', unmapped
    r = co.post("/api/gaash/thread", json={"gwd": "GWD009000023",
                                           "action": "set_name",
                                           "pname": "  Nuray   htab "}).get_json()
    check("set_name pins the name, trimmed, and reports its ID",
          r.get("ok") and r["pname"] == "Nuray htab" and r["pname_id"] == "111222333")
    check("the pinned name drives {id_number} and {name_id}",
          gm._fill("{id_number}", "GWD009000023") == "111222333"
          and gm.parcel_name("GWD009000023") == "Nuray htab")
    # a pick must beat even a customer's own CRM ID — that's the point of picking
    _mk_thread("GWD009000024", step=0, state="active")
    co.post("/api/gaash/thread", json={"gwd": "GWD009000024",
                                       "action": "set_name", "pname": "FAISAL"})
    check("a pinned name outranks the customer's CRM ID",
          gm._id_number_for("GWD009000024") == "CRM-777"
          and gm._fill("{id_number}", "GWD009000024") == "999888777")
    check("effective_id_map still agrees with _fill once names are pinned",
          all(gm.effective_id_map().get(g, "") == gm._fill("{id_number}", g)
              for g in ("GWD009000021", "GWD009000023", "GWD009000024")))
    # pinning a name with no ID must stay blank, NOT fall back to the customer's
    co.post("/api/gaash/thread", json={"gwd": "GWD009000024",
                                       "action": "set_name", "pname": "nobody"})
    check("a pinned but unmapped name yields no ID (no silent fallback)",
          gm._fill("{id_number}", "GWD009000024") == ""
          and gm.effective_id_map().get("GWD009000024", "") == "")
    r = co.post("/api/gaash/thread", json={"gwd": "GWD009000024",
                                           "action": "set_name", "pname": ""}).get_json()
    check("clearing the pick falls back to the board again",
          r["pname"] == "FAISAL" and r["pname_id"] == "CRM-777")
    _del_thread("GWD009000023"); _del_thread("GWD009000024")

    print("— default name: only where NEITHER board names the parcel —")
    with db.connect() as c:      # a parcel no board names at all
        c.execute("""INSERT INTO leluxe_orders (kind,name,status,date_created,data_json)
            VALUES ('item','NID nameless','on hold',?,?)""",
                  (now_ms, json.dumps({"tracking_number": "GWD009000025",
                                       "fields": {"BRAND": "x"}})))
    check("no default set → a nameless parcel stays nameless",
          gm.parcel_name("GWD009000025") == ""
          and gm._fill("{id_number}", "GWD009000025") == "")
    r = co.post("/api/settings", json={"gaash_mail":
                                       {"default_name": "  FAISAL  "}}).get_json()
    check("default_name saved + trimmed", r.get("ok")
          and gm._setts().get("default_name") == "FAISAL")
    check("the nameless parcel now takes the default, flagged as such",
          gm.parcel_name("GWD009000025") == "FAISAL"
          and gm._fill("{id_number}", "GWD009000025") == "999888777"
          and gm.parcel_name_src("GWD009000025") == "default")
    check("a parcel the BOARD names ignores the default",
          gm.parcel_name("GWD009000021") == "faisal"
          and gm.parcel_name_src("GWD009000021") == "board")
    check("Purchases keeps its Main name, never the default",
          gm.parcel_name("GWD009000001") == "Test Buyer"
          and gm.parcel_name_src("GWD009000001") == "board")
    _mk_thread("GWD009000025", step=0, state="active")   # a pick lives ON the thread
    co.post("/api/gaash/thread", json={"gwd": "GWD009000025", "action": "set_name",
                                       "pname": "Nuray htab"})
    check("an owner pick outranks the default",
          gm.parcel_name("GWD009000025") == "Nuray htab"
          and gm.parcel_name_src("GWD009000025") == "pick"
          and gm._fill("{id_number}", "GWD009000025") == "111222333")
    co.post("/api/gaash/thread", json={"gwd": "GWD009000025", "action": "set_name",
                                       "pname": ""})
    # the batched maps the picker/list draw from must agree with the per-GWD truth
    pm2 = gm.parcel_name_map()
    check("batched maps agree with _fill and parcel_name_src once a default is set",
          all(gm.effective_id_map(pm2).get(g, "") == gm._fill("{id_number}", g)
              and gm.parcel_src_map(pm2).get(g, "") == gm.parcel_name_src(g)
              for g in ("GWD009000021", "GWD009000025", "GWD009000001")))
    check("the customer's own ID still beats the default",
          gm._fill("{id_number}", "GWD009000022") == "111222333")
    co.post("/api/settings", json={"gaash_mail": {"default_name": ""}})
    check("clearing the default returns those parcels to blank",
          gm.parcel_name("GWD009000025") == ""
          and gm._fill("{id_number}", "GWD009000025") == "")
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders WHERE name='NID nameless'")
    _del_thread("GWD009000025")

    print("— 🪪 the one-pair name→ID writer (enroll picker's editable column) —")
    # set_name_id must MERGE — settings.apply replaces name_ids wholesale, so a
    # naive one-pair writer would wipe every other mapping
    # (2026-08-04) the enroll-flow section above picked 'Nuray htab' for
    # GWD009000023; re-point the pick at its board name so {name_id} follows
    # the yahia mapping here (a pick outranks every other source).
    gm.set_parcel_name("GWD009000023", "yahia")
    gm.set_name_id("yahia", "444555666")
    ids = gm._setts().get("name_ids") or {}
    check("set_name_id merges — the other mappings survive",
          ids.get("yahia") == "444555666" and ids.get("FAISAL") == "999888777"
          and ids.get("Nuray htab") == "111222333")
    check("...and the new pair immediately fills {name_id}",
          gm._fill("{name_id}", "GWD009000023") == "444555666")
    gm.set_name_id("  YAHIA ", "777")
    ids = gm._setts().get("name_ids") or {}
    check("a case/space variant REPLACES instead of duplicating",
          [k for k in ids if k.strip().lower() == "yahia"] == ["YAHIA"]
          and ids["YAHIA"] == "777")
    gm.set_name_id("YAHIA", "")
    check("an empty id clears the mapping",
          not [k for k in (gm._setts().get("name_ids") or {})
               if k.strip().lower() == "yahia"]
          and gm._fill("x{name_id}y", "GWD009000023") == "xy")
    check("a blank name is rejected", bool(gm.set_name_id("   ", "1").get("error")))
    check("employee + sales cannot write the name map",
          ce.post("/api/gaash/name_id",
                  json={"name": "FAISAL", "id_number": "1"}).status_code == 403
          and cs.post("/api/gaash/name_id",
                      json={"name": "FAISAL", "id_number": "1"}).status_code == 403)
    rn = co.post("/api/gaash/name_id", json={"name": "Amin Nagih",
                                             "id_number": "401234567"})
    check("admin writes one pair via the route",
          rn.status_code == 200 and rn.get_json().get("ok")
          and (gm._setts().get("name_ids") or {}).get("Amin Nagih") == "401234567"
          and (gm._setts().get("name_ids") or {}).get("FAISAL") == "999888777")
    check("the route rejects a blank name",
          co.post("/api/gaash/name_id", json={"name": "", "id_number": "9"}
                  ).status_code == 400)

    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders WHERE name='NID crm'")
        c.execute("DELETE FROM customers WHERE customer_code='CUS-NIDT'")
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders WHERE name LIKE 'NID %'")
    rcf = gm.rule_save({"name": "gold rings", "cond": {"groups": [{"crits": [
        {"field": "cf:product", "op": "contains", "value": "ring"}]}]},
        "seq_id": sq["id"], "mode": "queue", "enabled": True})
    made = gm.run_rules()
    check("cf:* Leluxe field enrolls only the matching package",
          made == 1 and gm.thread_get("GWD009000007")
          and gm.thread_get("GWD009000007")["state"] == "proposed")
    gm.rule_remove(rcf["id"]); _del_thread("GWD009000007")
    # Priority=High is a PO-level value → both live PO-T1 packages (001 + 003)
    rcf2 = gm.rule_save({"name": "high prio", "cond": {"groups": [{"crits": [
        {"field": "cf:Priority", "op": "is", "value": "high"}]}]},
        "seq_id": sq["id"], "mode": "queue", "enabled": True})
    made = gm.run_rules()
    check("cf:* Purchases custom field enrolls the PO's packages",
          made == 2 and gm.thread_get("GWD009000001")
          and gm.thread_get("GWD009000003"))
    gm.rule_remove(rcf2["id"])
    _del_thread("GWD009000001"); _del_thread("GWD009000003")

    print("— workflows: On/Off, clone, match counts, 7d stats —")
    co3, ce3 = client("otlo"), client("emp")
    # description round-trips through sequence save
    seq0 = gm.sequence_get(sq["id"])
    r = co3.post("/api/gaash/sequences", json={
        "id": sq["id"], "name": seq0["name"], "goal": seq0["goal"],
        "description": "chase GAASH until cleared",
        "steps": [{"id": s["id"], "kind": s["kind"], "template_id": s["template_id"],
                   "task_note": s["task_note"], "delay_days": s["delay_days"]}
                  for s in seq0["steps"]]}).get_json()
    check("description saved on the workflow", r.get("ok")
          and gm.sequence_get(sq["id"])["description"] == "chase GAASH until cleared")
    # match counts: cond → count + gwds (the ⚡ chip / modal preview)
    mm = gm.rule_matches({"groups": [{"crits": [
        {"field": "cf:product", "op": "contains", "value": "ring"}]}]})
    check("rule_matches counts the matching packages",
          mm["count"] >= 1 and "GWD009000007" in mm["gwds"])
    rr = ce3.post("/api/gaash/rules/preview", json={"cond": {"groups": [{"crits": [
        {"field": "cf:product", "op": "contains", "value": "ring"}]}]}}).get_json()
    check("rules/preview route mirrors it", rr.get("ok") and rr["count"] == mm["count"])
    # On/Off: paused blocks the sequencer (no claim burned) AND trigger enrollment
    gm.sequence_toggle(sq["id"], True)
    check("toggle persists", gm.sequence_get(sq["id"])["paused"] == 1)
    _mk_thread("GWD600", step=0, state="active",
               next_send_at=(now - timedelta(minutes=3)).isoformat(timespec="seconds"))
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET seq_id=? WHERE gwd='GWD600'", (sq["id"],))
    gm.run_once()
    th6 = gm.thread_get("GWD600")
    with db.connect() as c:
        burned = c.execute("SELECT 1 FROM settings WHERE key=?",
                           ("gaashmail:GWD600:step1",)).fetchone()
    check("paused workflow: due thread untouched, claim not burned",
          th6["step"] == 0 and th6["state"] == "active" and burned is None)
    rp = gm.rule_save({"name": "paused target", "cond": {"groups": [{"crits": [
        {"field": "cf:product", "op": "contains", "value": "ring"}]}]},
        "seq_id": sq["id"], "mode": "queue", "enabled": True})
    check("triggers of a paused workflow don't enroll",
          gm.run_rules() == 0 and not gm.thread_get("GWD009000007"))
    gm.sequence_toggle(sq["id"], False)
    check("back ON: the same trigger enrolls again",
          gm.run_rules() == 1 and gm.thread_get("GWD009000007"))
    # approve / dismiss — surfaced ONLY in the Workflows table's per-row
    # expansion now (the old top banner is gone), so lock the whole loop
    ov = gm.overview()
    check("overview splits proposed (with its seq_id) out of threads",
          any(p["gwd"] == "GWD009000007" and p.get("seq_id") == sq["id"]
              for p in ov["proposed"])
          and all(t["gwd"] != "GWD009000007" for t in ov["threads"]))
    r = co3.post("/api/gaash/thread",
                 json={"gwd": "GWD009000007", "action": "dismiss"})
    check("dismiss deletes the proposed thread",
          r.get_json().get("ok") and not gm.thread_get("GWD009000007"))
    check("the trigger re-proposes it on the next rules run",
          gm.run_rules() == 1
          and gm.thread_get("GWD009000007")["state"] == "proposed")
    r = co3.post("/api/gaash/thread",
                 json={"gwd": "GWD009000007", "action": "approve"})
    th7 = gm.thread_get("GWD009000007")
    check("approve starts a real thread in the thread's own workflow",
          r.get_json().get("ok") and th7 and th7["state"] != "proposed"
          and th7["seq_id"] == sq["id"])
    check("approve rejects a non-proposed thread",
          co3.post("/api/gaash/thread",
                   json={"gwd": "GWD009000007",
                         "action": "approve"}).status_code == 400)
    gm.rule_remove(rp["id"]); _del_thread("GWD009000007"); _del_thread("GWD600")
    # clone: copies steps, starts paused
    cl = gm.sequence_clone(sq["id"])
    cseq = gm.sequence_get(cl["id"])
    check("clone copies steps and starts Off", cl["ok"] and cseq["paused"] == 1
          and len(cseq["steps"]) == len(seq0["steps"])
          and cseq["name"].endswith(seq0["name"]))
    with db.connect() as c:                       # drop the clone (test hygiene)
        c.execute("DELETE FROM gaash_steps WHERE seq_id=?", (cl["id"],))
        c.execute("DELETE FROM gaash_sequences WHERE id=?", (cl["id"],))
    # perms: toggle/clone are admin-only; matches is fulfillment-readable
    check("employee cannot toggle/clone",
          ce3.post("/api/gaash/sequence/toggle",
                   json={"id": sq["id"], "paused": True}).status_code == 403
          and ce3.post("/api/gaash/sequence/clone",
                       json={"id": sq["id"]}).status_code == 403)
    check("employee reads rules/matches",
          ce3.get("/api/gaash/rules/matches").get_json().get("ok") is True)
    check("stats carries enrolled_7d",
          gm.stats()["overall"].get("enrolled_7d", 0) >= 1)

    print("— v2: open/click tracking tokens + public endpoints —")
    with db.connect() as c:
        cur = c.execute("""INSERT INTO gaash_msgs
            (gwd,dir,kind,step,at,message_id,body,attachments_json,notified,seq_id)
            VALUES ('GWD500','out','sent',1,?,?,'x','[]',1,?)""",
                        (db.now_iso(), "<t1@test>", sq["id"]))
        mrow = cur.lastrowid
    tok = gm.track_token(mrow, 0, "")
    check("token verifies", gm.verify_token(tok, "") == (mrow, 0))
    check("forged token rejected",
          gm.verify_token(f"{mrow}.0." + "a" * 20, "") is None)
    anon = appmod.app.test_client()
    rr = anon.get(f"/api/gaash/px/{tok}.gif")
    check("pixel is PUBLIC + returns a gif", rr.status_code == 200
          and rr.data.startswith(b"GIF89a"))
    url = "https://example.com/x"
    tok2 = gm.track_token(mrow, 1, url)
    rr = anon.get(f"/api/gaash/r/{tok2}?u={url}")
    check("redirect follows the real URL", rr.status_code == 302
          and rr.headers["Location"] == url)
    check("forged redirect 404s",
          anon.get(f"/api/gaash/r/{mrow}.1.{'a' * 20}?u={url}").status_code == 404)
    with db.connect() as c:
        m = c.execute("SELECT opens, clicks FROM gaash_msgs WHERE id=?",
                      (mrow,)).fetchone()
    check("open + click counters bumped", m["opens"] == 1 and m["clicks"] == 1)
    html = gm._html_body("see https://example.com/y please", mrow) \
        if gm._track_base() else None
    check("html body tracks links + pixel (when base URL set)",
          html is None or ("/api/gaash/r/" in html and "/api/gaash/px/" in html))

    print("— v2: stats shape —")
    st = gm.stats()
    check("stats aggregates", st["overall"]["sent"] >= 1
          and any(s["seq_id"] == sq["id"] and s["sent"] >= 1
                  for s in st["sequences"]))

    print("— v2: routes + permissions —")
    co2, ce2 = client("otlo"), client("emp")
    check("employee reads sequences",
          ce2.get("/api/gaash/sequences").get_json().get("ok") is True)
    check("employee cannot save sequences",
          ce2.post("/api/gaash/sequences", json={"name": "x"}).status_code == 403)
    check("employee cannot save templates",
          ce2.post("/api/gaash/templates", json={"name": "x"}).status_code == 403)
    check("stats route works", co2.get("/api/gaash/stats").get_json().get("ok") is True)
    check("archive blocked while threads use the sequence",
          co2.post("/api/gaash/sequence/delete",
                   json={"id": sq["id"]}).status_code == 400)

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

    print("— ✍️ owner replies from Gmail itself (Sent-folder scan) —")
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(gaash_accounts)")}
    check("migration adds the Sent cursor columns",
          {"sent_uidvalidity", "sent_last_uid"} <= cols)

    class _LGood:
        def list(self):
            return "OK", [b'(\\HasChildren) "/" "[Gmail]"',
                          b'(\\Sent \\HasNoChildren) "/" [Gmail]/Localized-Sent']

    class _LBad:
        def list(self):
            return "NO", []
    check("sent folder resolved by the \\Sent flag (and quoted)",
          gm._sent_folder(_LGood()) == '"[Gmail]/Localized-Sent"')
    check("sent folder falls back to the canonical name",
          gm._sent_folder(_LBad()) == '"[Gmail]/Sent Mail"')

    _mk_thread("GWD800", 1, "waiting_reply", None)
    _mk_out("GWD800", "2026-08-01T09:00:00+00:00", mid="<out800@test>")
    OUR = {"<out800@test>"}

    def _hdr(mid, subj, frm, to, irt=None):
        m = EmailMessage()
        m["Message-ID"] = mid
        m["Subject"] = subj
        m["From"] = frm
        m["To"] = to
        if irt:
            m["In-Reply-To"] = irt
        return m
    check("sent-match: our own SMTP copy is skipped",
          gm.match_sent_thread(_hdr("<out800@test>", "Re: GWD800", "o@t", "g@glassix.support"),
                               "glassix.support", OUR, ["GWD800"]) is None)
    check("sent-match: References beat everything",
          gm.match_sent_thread(_hdr("<own1@gmail>", "whatever", "o@t", "x@else.com",
                                    irt="<out800@test>"),
                               "glassix.support", OUR, ["GWD800"]) == "GWD800")
    check("sent-match: recipient domain + GWD subject fallback",
          gm.match_sent_thread(_hdr("<own2@gmail>", "about GWD800 please",
                                    "o@t", "GaashWW@glassix.support"),
                               "glassix.support", OUR, ["GWD800"]) == "GWD800")
    check("sent-match: support domain as SENDER does not match",
          gm.match_sent_thread(_hdr("<own3@gmail>", "about GWD800",
                                    "GaashWW@glassix.support", "someone@else.com"),
                               "glassix.support", OUR, ["GWD800"]) is None)
    check("sent-match: unrelated mail is ignored",
          gm.match_sent_thread(_hdr("<own4@gmail>", "dinner?", "o@t", "friend@else.com"),
                               "glassix.support", OUR, ["GWD800"]) is None)

    def _raw(mid, subj, frm, to, body, irt=None, attach=False):
        m = EmailMessage()
        m["Message-ID"] = mid
        m["Subject"] = subj
        m["From"] = frm
        m["To"] = to
        m["Date"] = "Thu, 06 Aug 2026 09:02:00 +0300"
        if irt:
            m["In-Reply-To"] = irt
            m["References"] = irt
        m.set_content(body)
        if attach:
            m.add_attachment(b"%PDF-1.4 test", maintype="application",
                             subtype="pdf", filename="import-certificate.pdf")
        return m.as_bytes()

    SENT_MSGS = {
        9: _raw("<out800@test>", "Re: GWD800", "app@t", "GaashWW@glassix.support",
                "step 2 body"),                                  # our own SMTP copy
        10: _raw("<own10@gmail>", "Re: Urgent Request GWD800", "owner@test.com",
                 "GaashWW@glassix.support", "Sure I will attach it in this email.",
                 irt="<out800@test>", attach=True),              # the owner's reply
        11: _raw("<own11@gmail>", "dinner?", "owner@test.com", "friend@else.com",
                 "unrelated"),                                   # personal mail
    }

    class _SentIMAP:
        def __init__(self):
            self.searches = []

        def list(self):
            return "OK", [b'(\\Sent \\HasNoChildren) "/" "[Gmail]/Sent Mail"']

        def status(self, folder, spec):
            return "OK", [b'"[Gmail]/Sent Mail" (UIDVALIDITY 3 UIDNEXT 12)']

        def select(self, folder, readonly=False):
            return "OK", [b"11"]

        def uid(self, cmd, *args):
            if cmd == "search":
                self.searches.append(args)
                return "OK", [b"9 10 11"]
            raw = SENT_MSGS.get(int(args[0]))
            return ("OK", [(b"h", raw)]) if raw else ("NO", [])

    setts8 = {"to_address": "GaashWW@glassix.support"}
    acct8 = {"id": "acct_s8", "email": "owner@test.com",
             "sent_last_uid": 8, "sent_uidvalidity": 3}
    M8 = _SentIMAP()
    recs, spatch = gm._scan_sent(M8, acct8, setts8, OUR, ["GWD800"], set())
    check("incremental scan uses the UID cursor",
          M8.searches and M8.searches[0][1] == "UID" and M8.searches[0][2] == "9:*")
    check("exactly the owner's message ingested (echo + unrelated skipped)",
          len(recs) == 1 and recs[0][0] == "GWD800")
    rec8 = recs[0][1]
    check("record shape: out/gmail, notified, dated by the Date header",
          rec8["dir"] == "out" and rec8["kind"] == "gmail"
          and rec8["notified"] is True and rec8["message_id"] == "<own10@gmail>"
          and "Sure I will attach" in rec8["body"] and rec8["at"].startswith("2026-08-06"))
    check("attachment stored on disk",
          len(rec8["attachments"]) == 1 and rec8["attachments"][0]["size"] > 0
          and gm.attachment_path("GWD800", rec8["attachments"][0]["file"]).exists())
    check("sent cursor advances to the newest UID",
          spatch == {"sent_uidvalidity": 3, "sent_last_uid": 11})

    M9 = _SentIMAP()
    recs2, spatch2 = gm._scan_sent(M9, {"id": "acct_s9", "email": "o2@test.com",
                                        "sent_last_uid": 0, "sent_uidvalidity": None},
                                   setts8, OUR, ["GWD800"], set())
    check("first scan backfills a bounded SINCE window",
          M9.searches and M9.searches[0][1] == "SINCE"
          and len(recs2) == 1 and spatch2["sent_last_uid"] == 11)
    M10 = _SentIMAP()
    recs3, spatch3 = gm._scan_sent(M10, {"id": "acct_sA", "email": "o3@test.com",
                                         "sent_last_uid": 99, "sent_uidvalidity": 2},
                                   setts8, OUR, ["GWD800"], set())
    check("UIDVALIDITY renumber falls back to the SINCE window",
          M10.searches and M10.searches[0][1] == "SINCE" and spatch3["sent_uidvalidity"] == 3)

    grec = ("GWD800", dict(rec8))
    real_chk2 = gm._check_account
    gm._check_account = lambda a, s, m, t: ([grec], {
        "imap_last_uid": 1, "imap_uidvalidity": 7, "last_check": gm.now_iso(),
        "seen_ids_json": "[]", "sent_uidvalidity": 3, "sent_last_uid": 11})
    try:
        rchk = gm.check_replies()
    finally:
        gm._check_account = real_chk2
    with db.connect() as c:
        grow = c.execute("SELECT * FROM gaash_msgs WHERE gwd='GWD800' AND "
                         "kind='gmail'").fetchall()
        acur = c.execute("SELECT sent_last_uid, sent_uidvalidity FROM "
                         "gaash_accounts LIMIT 1").fetchone()
    th8 = gm.thread_get("GWD800")
    check("check_replies stores the Gmail message once (dup-proof across accounts)",
          len(grow) == 1 and grow[0]["dir"] == "out" and grow[0]["notified"] == 1
          and rchk["gmail"] == 1)
    check("owner's message touches neither unread nor the state machine",
          int(th8["unread"] or 0) == 0 and th8["state"] == "waiting_reply")
    check("sent cursor persisted on the account",
          acur["sent_last_uid"] == 11 and acur["sent_uidvalidity"] == 3)
    _del_thread("GWD800")

    print("— filing several documents at once —")
    # The id is a millisecond timestamp. Picking several files at once uploads
    # them in a tight loop, so two land in the same millisecond and the second
    # used to fail the PRIMARY KEY and 500 the request. (This bit a test run
    # before it ever bit the owner.)
    burst = [gm.ids_add(f"burst {i}", f"b{i}.pdf", b"%PDF-1.4 z", folder="id")
             for i in range(6)]
    check("six files filed in one burst all land, with distinct ids",
          all(b.get("ok") for b in burst)
          and len({b["id"] for b in burst}) == 6
          and all(gm._id_doc(b["id"]) for b in burst))
    for b in burst:
        gm.ids_remove(b["id"])

    print("— re-arm: attach the paperwork, THEN email #1 again —")
    # Owner: "i can not reselect a running number … i do not want [it to] send
    # immediately because i want to attach maybe ids and declaration — if you
    # can re open the steps so i can attach them it could work." Re-enrolling
    # would have to DELETE the conversation; re-arming keeps every word of it.
    _mk_thread("GWD770770770", step=2, state="waiting_reply")
    _mk_out("GWD770770770", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with db.connect() as c:
        msgs_before = c.execute("SELECT COUNT(*) n FROM gaash_msgs WHERE gwd=?",
                                ("GWD770770770",)).fetchone()["n"]
    doc = gm.ids_add("AMIN id", "a.jpg", b"\xff\xd8x", folder="id")
    rr = gm.thread_rearm("GWD770770770", doc_ids=[doc["id"], gm.DECL_AUTO])
    th_r = gm.thread_get("GWD770770770")
    with db.connect() as c:
        msgs_after = c.execute("SELECT COUNT(*) n FROM gaash_msgs WHERE gwd=?",
                               ("GWD770770770",)).fetchone()["n"]
    check("re-arm sets the documents the thread was missing",
          rr.get("ok") and json.loads(th_r["docs_json"]) == [doc["id"], gm.DECL_AUTO]
          and th_r["id_doc_id"] == doc["id"])
    check("...and the ID scan rides the next email",
          "AMIN id.jpg" in [a[0] for a in gm._step_attachments(th_r)])
    check("...and it is back at email #1", int(th_r["step"] or 0) == 0)
    check("the GAASH conversation is KEPT — a re-enrollment would erase it",
          msgs_before > 0 and msgs_after >= msgs_before)
    # ONE press must put email #1 in front of GAASH ONCE. thread_switch_seq
    # already resets to step 0 and sends, so restarting on top of it would
    # double-send — the kind of bug the recipient sees and we never would.
    sends = []
    real_send = gm.send_step
    gm.send_step = lambda g, **k: (sends.append(g) or {"ok": True})
    try:
        other = [s for s in gm.sequences_list() if not s.get("paused")]
        _mk_thread("GWD772772772", step=2, state="waiting_reply")
        gm.thread_rearm("GWD772772772", doc_ids=[doc["id"]])
        n_same = len(sends)
        if len(other) > 1:
            _mk_thread("GWD773773773", step=2, state="waiting_reply",
                       seq_id=other[0]["id"])
            sends.clear()
            gm.thread_rearm("GWD773773773", doc_ids=[doc["id"]],
                            seq_id=other[1]["id"])
    finally:
        gm.send_step = real_send
    check("one press sends email #1 exactly once",
          n_same == 1 and (len(other) < 2 or len(sends) == 1))
    _del_thread("GWD772772772")
    _del_thread("GWD773773773")
    _mk_thread("GWD771771771", state="proposed")
    check("a suggestion is approved, never re-armed",
          not gm.thread_rearm("GWD771771771").get("ok"))
    check("an unknown parcel refuses cleanly",
          not gm.thread_rearm("GWD000000000").get("ok"))
    _del_thread("GWD770770770")
    _del_thread("GWD771771771")

    print("— declarations are written on the fly, never filed —")
    # Owner: "i do not need [them kept] — they just make me able to generate
    # them on the fly not keep the document i generate." A declaration is a
    # pure function of the boards, so a stored copy is only one that can go
    # stale, plus a folder that fills up.
    import purchases
    pdb = purchases.load()
    pdb["purchase_orders"].append({
        "po_id": "PO-DECL", "amazon_order_number": "111-D", "ship_to": "Decl",
        "profile_box": "A", "packages": [{
            "package_no": 1, "tracking_number": "GWD900900900",
            "main_name": "SAMPLE NAME",
            "items": [{"item_id": "i1", "title": "Watch", "qty": 2}]}]})
    purchases.save(pdb)

    def _lib_rows():
        with db.connect() as c:
            return c.execute("SELECT COUNT(*) n FROM gaash_ids").fetchone()["n"]

    before = _lib_rows()
    res = gm.declaration_make("GWD900900900")
    check("the wizard's check says yes and files NOTHING",
          res.get("ok") and res.get("id") == gm.DECL_AUTO
          and _lib_rows() == before)
    att = gm.declaration_attachment("GWD900900900")
    check("send time builds a real PDF from the boards",
          att and att[0] == "GWD900900900 - declaration.pdf"
          and att[1][:5] == b"%PDF-" and att[2] == "application/pdf")
    _mk_thread("GWD900900900")
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET docs_json=? WHERE gwd=?",
                  ('["decl:auto"]', "GWD900900900"))
    th = gm.thread_get("GWD900900900")
    atts = gm._step_attachments(th)
    check("the marker resolves to fresh bytes on EVERY email of the sequence",
          len(atts) == 1 and atts[0][1][:5] == b"%PDF-"
          and _lib_rows() == before)
    check("a parcel that cannot be papered refuses by name, never crashes",
          gm.declaration_make("NOT-A-GWD").get("error") == "not a tracking number"
          and gm.declaration_attachment("NOT-A-GWD") is None)
    check("...and an unpaperable parcel never blocks the email itself",
          gm._step_attachments(dict(th, gwd="NOT-A-GWD")) == [])
    filed = gm.declaration_make("GWD900900900", save=True)
    check("save=True still files one, for the hand-kept copy",
          filed.get("ok") and _lib_rows() == before + 1)

    # a thread enrolled BEFORE this change still points at the filed copy.
    # Removing those files without moving the pointer would strip the
    # paperwork off its follow-ups — the email goes out looking normal and
    # arrives at GAASH with nothing attached.
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET docs_json=?, id_doc_id=? WHERE gwd=?",
                  (json.dumps([filed["id"], filed["id"]]), filed["id"],
                   "GWD900900900"))
    # a filed declaration NOTHING references — the clutter to be rid of — and
    # a filed NON-declaration, which must survive untouched
    orphan = gm.ids_add("GWD009109999 - declaration", "d.pdf", b"%PDF-1.4 x",
                        folder="declaration")
    keep = gm.ids_add("AMIN passport", "id.jpg", b"\xff\xd8jpeg", folder="id")
    # the owner's OWN papers live in that folder too ("GWD… - sultan
    # declaration", a scan he dropped in). Only what the app generated —
    # named exactly "<GWD> - declaration" — is ours to remove.
    mine = gm.ids_add("GWD009109999 - sultan declaration", "s.pdf",
                      b"%PDF-1.4 y", folder="declaration")
    rows_before = _lib_rows()
    moved = gm.migrate_decl_auto()
    th2 = gm.thread_get("GWD900900900")
    check("an old thread's filed declaration is repointed at the marker",
          json.loads(th2["docs_json"]) == [gm.DECL_AUTO]
          and th2["id_doc_id"] == gm.DECL_AUTO)
    check("...and it still gets a real PDF on its next email",
          gm._step_attachments(th2)[0][1][:5] == b"%PDF-")
    check("the filed copies nothing points at are cleared out",
          moved == 3 and _lib_rows() == rows_before - 2
          and gm._id_doc(orphan["id"]) is None
          and gm._id_doc(filed["id"]) is None)
    check("the owner's own papers are NEVER deleted — an ID scan, and a "
          "hand-named declaration sitting in the same folder",
          gm._id_doc(keep["id"]) is not None
          and gm._id_doc(mine["id"]) is not None)
    check("the migration is idempotent", gm.migrate_decl_auto() == 0)
    _del_thread("GWD900900900")

    print("— grouped conversations (one email, several parcels of one order) —")
    _mk_thread("GWD700700701")
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET group_gwds_json=? WHERE gwd=?",
                  (json.dumps(["GWD700700701", "GWD700700702", "gwd700700703",
                               "GWD700700702", "junk"]), "GWD700700701"))
    gth = gm.thread_get("GWD700700701")
    check("thread_members: primary first, uppercased, deduped, junk dropped",
          gm.thread_members(gth) == ["GWD700700701", "GWD700700702",
                                     "GWD700700703"])
    _mk_thread("GWD700700709")
    check("a solo thread is its own single member",
          gm.thread_members(gm.thread_get("GWD700700709")) == ["GWD700700709"])
    mpm = gm.member_primary_map()
    check("member_primary_map: members → primary, solos → themselves",
          mpm.get("GWD700700702") == "GWD700700701"
          and mpm.get("GWD700700703") == "GWD700700701"
          and mpm.get("GWD700700709") == "GWD700700709")
    check("_fill group tokens on a SOLO thread collapse to the one parcel",
          gm._fill("{package_count}|{gwd_list}|{gwd_lines}", "GWD700700709",
                   gm.thread_get("GWD700700709"))
          == "1|GWD700700709|GWD700700709")
    check("_fill group tokens list every member of a group",
          gm._fill("{package_count}: {gwd_list}", "GWD700700701", gth)
          == "3: GWD700700701, GWD700700702, GWD700700703")
    check("...{gwd_lines} one per line, {gwd} stays the primary",
          gm._fill("{gwd}\n{gwd_lines}", "GWD700700701", gth)
          == "GWD700700701\nGWD700700701\nGWD700700702\nGWD700700703")
    check("members= narrows the list (partial clearance at send time)",
          gm._fill("{gwd_list}", "GWD700700701", gth,
                   members=["GWD700700702"]) == "GWD700700702")
    hmm = EmailMessage()
    hmm["From"] = "Support <team@glassix.support>"
    hmm["Subject"] = "About your package GWD700700703"
    check("a member GWD in the subject lands on the GROUP's thread",
          gm.match_thread(hmm, "glassix.support", set(), mpm) == "GWD700700701")
    hs = EmailMessage()
    hs["To"] = "GaashWW@glassix.support"
    hs["Subject"] = "please release GWD700700702"
    check("...and a Gmail-written email naming a member files there too",
          gm.match_sent_thread(hs, "glassix.support", set(), mpm)
          == "GWD700700701")
    real_pt = gm.package_terminal
    try:
        gm.package_terminal = lambda g: g == "GWD700700702"
        check("group_terminal: one cleared member is NOT enough",
              gm.group_terminal(gth) is False)
        gm.package_terminal = lambda g: True
        check("group_terminal: every member cleared → goal met",
              gm.group_terminal(gth) is True)
    finally:
        gm.package_terminal = real_pt
    _del_thread("GWD700700701")
    _del_thread("GWD700700709")

    # a real group enrollment over the Purchases parcels from the section above
    pdb = purchases.load()
    for p in pdb["purchase_orders"]:
        if p.get("po_id") == "PO-DECL":
            p["packages"].append({
                "package_no": 2, "tracking_number": "GWD900900901",
                "main_name": "SAMPLE NAME",
                "items": [{"item_id": "i2", "title": "Strap", "qty": 1}]})
    purchases.save(pdb)
    real_pt = gm.package_terminal
    gm.package_terminal = lambda g: False       # stale boards must not decide
    try:
        res = gm.start_threads([], None, None,
                               groups=[["GWD900900900", "GWD900900901"]])
        th9 = gm.thread_get("GWD900900900")
        check("a group enrolls as ONE thread keyed by its first member",
              len(res) == 1 and res[0]["gwd"] == "GWD900900900"
              and res[0].get("members") == ["GWD900900900", "GWD900900901"]
              and gm.thread_get("GWD900900901") is None and th9
              and json.loads(th9["group_gwds_json"])
              == ["GWD900900900", "GWD900900901"])
        with db.connect() as c:
            c.execute("UPDATE gaash_threads SET docs_json=? WHERE gwd=?",
                      ('["decl:auto"]', "GWD900900900"))
        atts = gm._step_attachments(gm.thread_get("GWD900900900"))
        check("decl:auto papers EVERY member — one PDF per parcel",
              [a[0] for a in atts] == ["GWD900900900 - declaration.pdf",
                                       "GWD900900901 - declaration.pdf"]
              and all(a[1][:5] == b"%PDF-" for a in atts))
        rows = {r["gwd"]: r for r in gm.docs_queue()["rows"]}
        check("docs queue: a member parcel wears the group's thread state",
              rows.get("GWD900900901", {}).get("thread_state") == "active")
        r2 = gm.start_threads(["GWD900900901"], None, None)
        check("a member can't be enrolled again solo",
              r2 and not r2[0]["ok"]
              and "grouped" in (r2[0].get("error") or ""))
        r3 = gm.start_threads([], None, None,
                              groups=[["GWD900900901", "GWD900900900"]])
        check("...nor folded into a different group",
              r3 and not r3[0]["ok"])
        rr = client("emp").post(
            "/api/gaash/template_render",
            json={"gwd": "GWD900900900", "template_id": "tpl_group",
                  "gwds": ["GWD900900900", "GWD900900901"],
                  "preview": True}).get_json()
        check("template_render previews a PLANNED group's full member list",
              rr.get("ok")
              and "GWD900900900, GWD900900901" in (rr.get("subject") or "")
              and "GWD900900900\nGWD900900901" in (rr.get("body") or ""))
        res1 = gm.start_threads([], None, None, groups=[["GWD900900902"]])
        th1 = gm.thread_get("GWD900900902")
        check("a 1-member group is a plain solo thread — today's exact path",
              res1 and res1[0]["ok"] and th1
              and th1.get("group_gwds_json") is None
              and "members" not in res1[0])
    finally:
        gm.package_terminal = real_pt
        _del_thread("GWD900900900")
        _del_thread("GWD900900902")

    print()
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All GAASH-mail checks passed ✓")


if __name__ == "__main__":
    main()
