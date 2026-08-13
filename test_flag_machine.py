#!/usr/bin/env python3
"""
Self-checks: 🚩 Flag machine (flag_machine.py + /api/flags/*).

Pure-logic tests — NO network, NO real IMAP or Telegram: the subject matcher
(incl. RFC2047 decode), the settings validator, add/remove/pause inboxes with
an injected verifier, the alert pass (one batched nag, send-then-stamp,
claim-once exactly-once, failed send frees the claim), ack_all, the flags
bot «done» loop (poll_updates with injected get/send, first-run drain, the
single-runner lease), route permissions, and the bell.

    ./.venv/bin/python test_flag_machine.py
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-flags-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ.pop("FLAG_MACHINE", None)          # the daemon must NOT start
os.environ.pop("LELUXE_TG_BOT", None)         # nor the bot poll loop
os.environ.pop("GAASH_MAILER", None)
os.environ["OTLOBLY_SECRET"] = "x"
# dummy creds so telegram.configured() / flags_configured() are True — sends
# and getUpdates are always injected, never real
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "5551234"
os.environ["FLAGS_BOT_TOKEN"] = "test:flags"

import app as appmod          # noqa: E402
import auth                   # noqa: E402
import db                     # noqa: E402
import flag_machine as fm     # noqa: E402
import telegram               # noqa: E402
import telegram_bot           # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def _mk_flag(email, mid, subject="Action required: pay", inbox_id="fin_t",
             created_at=None, state="open"):
    with db.connect() as c:
        c.execute("""INSERT OR IGNORE INTO flag_alerts
            (inbox_id,email,msg_id,uid,subject,sender,matched_phrase,
             created_at,state,sent_count)
            VALUES (?,?,?,?,?,?,?,?,?,0)""",
                  (inbox_id, email, mid, 1, subject, "amazon@example.com",
                   "action required", created_at or db.now_iso(), state))


def _clear_flags():
    with db.connect() as c:
        c.execute("DELETE FROM flag_alerts")


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    db.create_user("emp", auth.hash_pw("s1"), "fulfillment", "E", business_id=1)
    db.create_user("sal", auth.hash_pw("s1"), "sales", "S", business_id=1)

    print("— subject matcher —")
    P = ["action required"]
    check("exact", fm.match_subject("action required", P) == "action required")
    check("case-insensitive", fm.match_subject("ACTION Required: verify", P))
    check("embedded + doubled whitespace",
          fm.match_subject("RE:  Action   Required – your account", P))
    check("non-match", fm.match_subject("your order shipped", P) is None)
    check("arabic phrase", fm.match_subject("مطلوب إجراء الآن", ["مطلوب إجراء"]))
    check("empty subject", fm.match_subject(None, P) is None)
    # RFC2047: policy.default hands the decoded subject to the matcher
    m = EmailMessage()
    m["Subject"] = "Action Required — تأكيد"
    m["From"] = "a@b.c"
    m.set_content("x")
    hm = message_from_bytes(bytes(m), policy=policy.default)
    check("rfc2047 round-trip", "=?utf-8?" in bytes(m).decode("ascii", "ignore")
          and fm.match_subject(str(hm.get("Subject")), P))
    check("date header parses",
          (fm._parse_date_hdr("Wed, 13 Aug 2026 09:15:00 +0300") or "")
          .startswith("2026-08-13"))
    check("garbage date → None", fm._parse_date_hdr("not a date") is None
          and fm._parse_date_hdr(None) is None)

    print("— settings —")
    st = fm.settings()
    check("defaults", st["enabled"] and st["phrases"] == ["action required"]
          and st["poll_interval_min"] == 2 and st["repeat_min"] == 1)
    _, err = fm.save_settings({"phrases": " , "})
    check("empty phrases rejected", err is not None)
    st, err = fm.save_settings({"phrases": "Action Required, urgent: reply"})
    check("comma string split", err is None
          and st["phrases"] == ["Action Required", "urgent: reply"])
    _, err = fm.save_settings({"poll_interval_min": 0})
    check("interval 0 rejected", err is not None)
    _, err = fm.save_settings({"repeat_min": "x"})
    check("non-numeric interval rejected", err is not None)
    fm.save_settings({"phrases": "action required"})    # back to default

    print("— inboxes —")
    r = fm.add_inbox("not-an-email", "pw")
    check("bad email rejected", not r["ok"])
    r = fm.add_inbox("watch@gmail.com", "   ")
    check("blank password rejected", not r["ok"])
    r = fm.add_inbox("watch@gmail.com", "abcd efgh ijkl mnop",
                     verify=lambda e, p: (7, 42))
    check("created, cursor seeded at newest", r["ok"] and not r.get("updated"))
    with db.connect() as c:
        row = dict(c.execute("SELECT * FROM flag_inboxes WHERE email=?",
                             ("watch@gmail.com",)).fetchone())
    check("uid cursor = uidnext-1, pw cleaned", row["imap_last_uid"] == 41
          and row["app_password"] == "abcdefghijklmnop" and row["active"] == 1)
    fid = row["id"]
    r2 = fm.add_inbox("watch@gmail.com", "qrst uvwx yzab cdef",
                      verify=lambda e, p: (7, 99))
    check("re-add updates in place", r2["ok"] and r2.get("updated")
          and r2["id"] == fid)
    with db.connect() as c:
        row2 = dict(c.execute("SELECT * FROM flag_inboxes WHERE id=?",
                              (fid,)).fetchone())
    check("password swapped, cursor kept", row2["app_password"] == "qrstuvwxyzabcdef"
          and row2["imap_last_uid"] == 41)

    def _boom(e, p):
        raise RuntimeError("connection refused")
    r3 = fm.add_inbox("other@gmail.com", "pw pw pw pw", verify=_boom)
    check("refused add is SAVED with its error", r3["ok"]
          and "IMAP" in r3.get("saved_with_error", ""))
    with db.connect() as c:
        row3 = dict(c.execute("SELECT * FROM flag_inboxes WHERE email=?",
                              ("other@gmail.com",)).fetchone())
    check("refused row: ⚠ error + NULL cursor, still active",
          row3["last_error"] and row3["imap_last_uid"] is None
          and row3["active"] == 1)
    r4 = fm.add_inbox("watch@gmail.com", "qrst uvwx yzab cdef",
                      verify=lambda e, p: (7, 99), label="AZ Profile 7")
    check("re-add sets the profile name in place", r4["ok"] and r4["updated"]
          and "saved_with_error" not in r4)
    with db.connect() as c:
        row4 = dict(c.execute("SELECT label, imap_last_uid FROM flag_inboxes "
                              "WHERE id=?", (fid,)).fetchone())
    check("label saved, cursor untouched", row4["label"] == "AZ Profile 7"
          and row4["imap_last_uid"] == 41)

    check("pause", fm.set_active(fid, False)["ok"])
    check("resume", fm.set_active(fid, True)["ok"])
    check("redact hides password", all("app_password" not in a
          and a.get("has_password") for a in fm.inboxes()))

    print("— fake-IMAP poll (NULL-cursor seed + sent_at) —")
    _hm = EmailMessage()
    _hm["Subject"] = "Action Required: verify your payment method"
    _hm["From"] = "Amazon <account-update@amazon.com>"
    _hm["Date"] = "Wed, 13 Aug 2026 09:15:00 +0300"
    _hm["Message-ID"] = "<fake-43@amazon.com>"
    _hm.set_content("x")
    _HDR = bytes(_hm)

    class _FakeIMAP:
        # mailbox: UIDVALIDITY 7, UIDNEXT 44, newest message uid 43
        def __init__(self, host=None, port=None):
            pass

        def login(self, e, p):
            return ("OK", [b"ok"])

        def status(self, folder, what):
            return ("OK", [b"INBOX (UIDVALIDITY 7 UIDNEXT 44)"])

        def select(self, folder, readonly=True):
            return ("OK", [b"1"])

        def uid(self, cmd, *args):
            if cmd == "search":
                return ("OK", [b"43"])
            if cmd == "FETCH":
                return ("OK", [(b"43 (BODY[HEADER])", _HDR)])
            return ("OK", [])

        def logout(self):
            return ("OK", [b"bye"])

    import imaplib as _il
    _real_imap = _il.IMAP4_SSL
    _il.IMAP4_SSL = _FakeIMAP
    try:
        raised = fm.poll_once()
    finally:
        _il.IMAP4_SSL = _real_imap
    # watch@ (cursor 41) sees uid 43 and flags it; other@ (NULL cursor) seeds
    # at 43 and ingests NOTHING — the whole point of the seed
    check("cursored inbox flags the new mail, seeded one doesn't", raised == 1)
    fl = fm.open_flags()
    check("flag carries sent_at + profile from the join", len(fl) == 1
          and fl[0]["email"] == "watch@gmail.com"
          and (fl[0]["sent_at"] or "").startswith("2026-08-13")
          and fl[0]["profile"] == "AZ Profile 7")
    with db.connect() as c:
        row5 = dict(c.execute("SELECT imap_last_uid, last_error FROM "
                              "flag_inboxes WHERE email=?",
                              ("other@gmail.com",)).fetchone())
    check("NULL cursor seeded at newest, error cleared by the clean poll",
          row5["imap_last_uid"] == 43 and row5["last_error"] is None)
    txt = fm._build_message(fl, datetime.now(timezone.utc))
    check("nag line: profile · email + sent time + done hint",
          "AZ Profile 7 · watch@gmail.com" in txt and "sent" in txt
          and "done" in txt)
    _clear_flags()

    print("— dedupe index —")
    _mk_flag("watch@gmail.com", "<m1@x>")
    _mk_flag("watch@gmail.com", "<m1@x>")      # INSERT OR IGNORE
    check("(email,msg_id) unique", len(fm.open_flags()) == 1)
    _clear_flags()

    print("— alert pass —")
    NOW = datetime.now(timezone.utc)
    _mk_flag("watch@gmail.com", "<a@x>", subject="Action required: card",
             inbox_id=fid, created_at=(NOW - timedelta(minutes=14)).isoformat())
    _mk_flag("watch@gmail.com", "<b@x>", subject="Action Required: address",
             inbox_id=fid, created_at=(NOW - timedelta(minutes=2)).isoformat())
    sent = []

    def ok_send(txt):
        sent.append(txt)
        return {"ok": True}

    out = fm.run_once(now=NOW, send=ok_send, do_poll=False)
    check("one batched message", len(out) == 1 and len(sent) == 1)
    check("message lists both + count + done hint",
          "card" in sent[0] and "address" in sent[0] and "2" in sent[0]
          and "done" in sent[0] and "watch@gmail.com" in sent[0])
    flags = fm.open_flags()
    check("stamped after ok send", all(f["last_sent_at"] and
          f["sent_count"] == 1 for f in flags))
    out = fm.alert_once(now=NOW, send=ok_send)
    check("not due again immediately", out == [] and len(sent) == 1)
    out = fm.alert_once(now=NOW + timedelta(minutes=2), send=ok_send)
    check("due after repeat_min", len(out) == 1 and len(sent) == 2
          and all(f["sent_count"] == 2 for f in fm.open_flags()))
    out = fm.alert_once(now=NOW + timedelta(minutes=4),
                        send=lambda t: {"ok": False})
    _b4 = f"flags:nag:{(NOW + timedelta(minutes=4)).astimezone(timezone.utc).strftime('%Y%m%d%H%M')}"
    with db.connect() as c:
        left = c.execute("SELECT COUNT(*) n FROM settings WHERE key=?",
                         (_b4,)).fetchone()["n"]
    check("failed send stamps nothing + frees its claim", out == [] and left == 0
          and all(f["sent_count"] == 2 for f in fm.open_flags()))
    out = fm.alert_once(now=NOW + timedelta(minutes=4), send=ok_send)
    check("same-minute retry after failure succeeds", len(out) == 1
          and len(sent) == 3
          and all(f["sent_count"] == 3 for f in fm.open_flags()))
    _b6 = f"flags:nag:{(NOW + timedelta(minutes=6)).astimezone(timezone.utc).strftime('%Y%m%d%H%M')}"
    db.claim_once(_b6)                # the other worker got this minute first
    out = fm.alert_once(now=NOW + timedelta(minutes=6), send=ok_send)
    check("pre-claimed minute → no double nag", out == [] and len(sent) == 3
          and all(f["sent_count"] == 3 for f in fm.open_flags()))
    check("no flags → no send", fm.ack_all() == 2
          and fm.alert_once(now=NOW, send=ok_send) == [] and len(sent) == 3)
    check("ack_all again is 0", fm.ack_all() == 0)
    with db.connect() as c:
        done = c.execute("SELECT COUNT(*) n FROM flag_alerts "
                         "WHERE state='done' AND done_at IS NOT NULL").fetchone()["n"]
    check("done rows carry done_at", done == 2)
    _clear_flags()

    print("— flags bot «done» loop (poll_updates) —")
    got = []

    def coll(chat, txt):
        got.append((chat, txt))
        return {"ok": True}

    _mk_flag("watch@gmail.com", "<c@x>", inbox_id=fid)
    stale = {"ok": True, "result": [
        {"update_id": 7, "message": {"chat": {"id": 5551234}, "text": "done"}}]}
    out = fm.poll_updates(get=lambda off, t: stale, send=coll)
    check("first-run drain executes nothing", out == [] and not got
          and len(fm.open_flags()) == 1
          and db.get_setting("flags:tg_offset") == 8)
    upd = {"ok": True, "result": [
        {"update_id": 8, "message": {"chat": {"id": 999}, "text": "done"}},
        {"update_id": 9, "message": {"chat": {"id": 5551234}, "text": "  DONE "}}]}
    out = fm.poll_updates(get=lambda off, t: upd, send=coll)
    check("owner DONE closes + replies, foreign chat ignored",
          len(out) == 1 and len(got) == 1 and got[0][0] == 5551234
          and "1" in got[0][1] and not fm.open_flags())
    check("offset persisted past the batch",
          db.get_setting("flags:tg_offset") == 10)
    upd2 = {"ok": True, "result": [
        {"update_id": 10, "message": {"chat": {"id": 5551234}, "text": "تم"}}]}
    fm.poll_updates(get=lambda off, t: upd2, send=coll)
    check("تم works, none-open reply", len(got) == 2 and "🚩" in got[1][1])
    upd3 = {"ok": True, "result": [
        {"update_id": 11, "message": {"chat": {"id": 5551234}, "text": "hello"}}]}
    fm.poll_updates(get=lambda off, t: upd3, send=coll)
    check("other owner text → self-teaching hint",
          len(got) == 3 and "done" in got[2][1])
    check("management bot: done is just an unknown command",
          "أوامر" in telegram_bot.handle_command("done"))
    check("HELP no longer mentions done", "done" not in telegram_bot.HELP)

    print("— lease + poll cursor —")
    check("lease: A takes it", fm._lease_ok(me="A"))
    check("lease: B refused while fresh", not fm._lease_ok(me="B"))
    db.set_setting(fm.LEASE_KEY, {"id": "A", "ts":
        (datetime.now(timezone.utc) - timedelta(seconds=200))
        .isoformat(timespec="seconds")})
    check("lease: B steals a 200s-stale lease", fm._lease_ok(me="B"))
    check("lease: A refused after the steal", not fm._lease_ok(me="A"))
    calls = []
    _orig_poll = fm.poll_once
    fm.poll_once = lambda: calls.append(1)
    try:
        with db.connect() as c:
            c.execute("DELETE FROM settings WHERE key=?", (fm.POLL_AT_KEY,))
        T = datetime.now(timezone.utc)
        fm.run_once(now=T, send=lambda t: {"ok": True})
        fm.run_once(now=T + timedelta(seconds=30), send=lambda t: {"ok": True})
        fm.run_once(now=T + timedelta(minutes=3), send=lambda t: {"ok": True})
    finally:
        fm.poll_once = _orig_poll
    check("DB poll cursor: due, not-due, due again (default 2 min)",
          len(calls) == 2)

    print("— configured flips —")
    os.environ.pop("FLAGS_BOT_TOKEN")
    check("flags_configured false without token", not fm.flags_configured())
    os.environ["FLAGS_BOT_TOKEN"] = "test:flags"
    check("flags_configured true again", fm.flags_configured())
    _tok = os.environ.pop("TELEGRAM_BOT_TOKEN")
    check("telegram.configured token kwarg",
          not telegram.configured() and telegram.configured(token="t2"))
    os.environ["TELEGRAM_BOT_TOKEN"] = _tok

    print("— remove keeps open flags —")
    _mk_flag("watch@gmail.com", "<d@x>", inbox_id=fid)
    r = fm.remove_inbox(fid)
    check("remove names open flags", r["ok"] and r["open_flags"] == 1
          and len(fm.open_flags()) == 1)

    print("— routes —")
    adm, ful, sal = client("otlo"), client("emp"), client("sal")
    anon = appmod.app.test_client()
    check("anonymous blocked",
          anon.get("/api/flags").status_code in (302, 401, 403))
    check("sales blocked", sal.get("/api/flags").status_code == 403
          and sal.post("/api/flags/inbox/add", json={}).status_code == 403)
    d = ful.get("/api/flags").get_json()
    check("fulfillment reads", d["ok"] and len(d["flags"]) == 1
          and "settings" in d and "telegram" in d)
    check("fulfillment cannot add",
          ful.post("/api/flags/inbox/add", json={}).status_code == 403)
    check("fulfillment cannot save settings",
          ful.post("/api/flags/settings", json={"repeat_min": 5}).status_code == 403)
    check("admin add validates",
          adm.post("/api/flags/inbox/add",
                   json={"email": "x", "app_password": "y"}).status_code == 400)
    _orig = fm._verify_imap
    fm._verify_imap = lambda e, p: (3, 10)
    try:
        d = adm.post("/api/flags/inbox/add",
                     json={"email": "route@gmail.com",
                           "app_password": "aaaa bbbb cccc dddd"}).get_json()
    finally:
        fm._verify_imap = _orig
    check("admin adds via route", d["ok"])

    def _refuse(e, p):
        raise RuntimeError("conn boom")
    fm._verify_imap = _refuse
    try:
        resp = adm.post("/api/flags/inbox/add",
                        json={"email": "routefail@gmail.com",
                              "app_password": "aaaa bbbb cccc dddd"})
    finally:
        fm._verify_imap = _orig
    check("route add keeps a refused inbox (200 + saved_with_error)",
          resp.status_code == 200 and resp.get_json().get("saved_with_error"))
    d = adm.post("/api/flags/settings", json={"repeat_min": 3}).get_json()
    check("admin saves settings", d["ok"] and d["settings"]["repeat_min"] == 3)
    d = ful.post("/api/flags/done", json={}).get_json()
    check("manual done route", d["ok"] and d["closed"] == 1
          and not fm.open_flags())
    d = adm.post("/api/flags/inbox/remove",
                 json={"id": [a for a in fm.inboxes()
                              if a["email"] == "route@gmail.com"][0]["id"]}).get_json()
    check("admin removes via route", d["ok"])

    print("— bell —")
    _mk_flag("watch@gmail.com", "<bell@x>", subject="Action required: docs")
    d = ful.get("/api/notifications").get_json()
    ev = [e for e in d["events"] if e["type"] == "flag_open"]
    check("flag_open standing item", len(ev) == 1 and ev[0]["view"] == "flags"
          and "docs" in ev[0]["sub"])
    fm.ack_all()
    d = ful.get("/api/notifications").get_json()
    check("gone after done",
          not [e for e in d["events"] if e["type"] == "flag_open"])

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("all good ✓")


if __name__ == "__main__":
    main()
