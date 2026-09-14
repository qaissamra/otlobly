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
import sqlite3
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
    print("— whole-word + sender matchers (the XM rule) —")
    W = ["xm"]
    check("whole word fires", fm.match_word("XM Global: your account", W) == "xm")
    check("whole word at the end", fm.match_word("Re: XM", W) == "xm")
    check("punctuation is a word edge", fm.match_word("[XM] statement", W))
    check("Xmas does NOT fire — the reason match_word exists",
          fm.match_word("Xmas sale 50% off", W) is None
          and fm.match_subject("Xmas sale 50% off", W) == "xm")
    check("inside a longer word does not fire",
          fm.match_word("maxmind report", W) is None)
    F = ["xm.com"]
    check("sender matches the address",
          fm.match_sender("XM <no-reply@xm.com>", F) == "xm.com")
    check("sender is case-insensitive",
          fm.match_sender("No-Reply@XM.COM", F) == "xm.com")
    check("a lookalike domain does not fire",
          fm.match_sender("billing@maxmind.com", F) is None)
    check("empty sender header", fm.match_sender(None, F) is None)
    ST = {"phrases": ["action required"], "word_phrases": W, "senders": F}
    check("match_mail: subject phrase keeps its own label",
          fm.match_mail("Action required: verify", "a@b.c", ST)
          == "action required")
    check("match_mail: a whole word says so",
          fm.match_mail("Your XM statement", "a@b.c", ST) == "word: xm")
    check("match_mail: WHO sent it, whatever the subject said",
          fm.match_mail("monthly statement", "XM <no-reply@xm.com>", ST)
          == "from: xm.com")
    check("match_mail: nothing fires on ordinary mail",
          fm.match_mail("your parcel shipped", "gaash@x.com", ST) is None)

    check("date header parses",
          (fm._parse_date_hdr("Wed, 13 Aug 2026 09:15:00 +0300") or "")
          .startswith("2026-08-13"))
    check("garbage date → None", fm._parse_date_hdr("not a date") is None
          and fm._parse_date_hdr(None) is None)

    print("— GWD extraction —")
    check("plain + case + dedupe + order",
          fm.gwd_tokens("see gwd123456789 and GWD987654321, also Gwd123456789")
          == ["GWD123456789", "GWD987654321"])
    check("embedded in prose/HTML",
          fm.gwd_tokens("<p>Parcel GWD555000111 held</p>") == ["GWD555000111"])
    check("no separator variants (the repo has none)",
          fm.gwd_tokens("GWD-123456789") == []
          and fm.gwd_tokens("GWD 123456789") == [])
    check("needs digits", fm.gwd_tokens("GWDX or GWD") == [])
    check("empty/None safe", fm.gwd_tokens("") == [] and fm.gwd_tokens(None) == [])

    print("— body text extraction (never covered before) —")
    from gaash_mail import _extract_text
    _p = EmailMessage()
    _p["Subject"] = "s"
    _p.set_content("plain GWD111111111 body")
    check("plain text", "GWD111111111" in
          _extract_text(message_from_bytes(bytes(_p), policy=policy.default)))
    _h = EmailMessage()
    _h["Subject"] = "s"
    _h.set_content("<html><body><p>html GWD222222222</p>"
                   "<style>x{}</style></body></html>", subtype="html")
    _ht = _extract_text(message_from_bytes(bytes(_h), policy=policy.default))
    check("html-only is stripped to text",
          "GWD222222222" in _ht and "<p>" not in _ht)
    _m = EmailMessage()
    _m["Subject"] = "s"
    _m.set_content("alt plain GWD333333333")
    _m.add_alternative("<p>alt html</p>", subtype="html")
    check("multipart/alternative prefers plain", "GWD333333333" in
          _extract_text(message_from_bytes(bytes(_m), policy=policy.default)))
    _a = EmailMessage()
    _a["Subject"] = "s"
    _a.set_content("with attachment GWD444444444")
    _a.add_attachment(b"\x00\x01binary", maintype="application",
                      subtype="pdf", filename="x.pdf")
    check("attachment ignored, text still read", "GWD444444444" in
          _extract_text(message_from_bytes(bytes(_a), policy=policy.default)))

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
    st, err = fm.save_settings({"senders": "xm.com, @xmglobal.",
                                "word_phrases": "xm"})
    check("sender + whole-word rules save as lists", err is None
          and st["senders"] == ["xm.com", "@xmglobal."]
          and st["word_phrases"] == ["xm"])
    st, err = fm.save_settings({"phrases": " , "})
    check("phrases may be emptied once another rule stands",
          err is None and st["phrases"] == [] and st["senders"])
    _, err = fm.save_settings({"phrases": "", "senders": "", "word_phrases": ""})
    check("but a watch with NO rule at all is refused", err is not None)
    fm.save_settings({"phrases": "action required", "senders": "",
                      "word_phrases": ""})               # back to default

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

    check("set_label names an inbox without the app password",
          fm.set_label(fid, "  E-B15  ")["ok"]
          and [a for a in fm.inboxes() if a["id"] == fid][0]["label"] == "E-B15")
    # lxOptColor matches ClickUp option names case-SENSITIVELY, and this field
    # also holds hand-typed labels — so nothing may be upper-cased
    fm.set_label(fid, "e-b15 spare")
    check("case preserved exactly (no upper-casing)",
          [a for a in fm.inboxes() if a["id"] == fid][0]["label"] == "e-b15 spare")
    fm.set_label(fid, "")
    check("blank label clears it",
          [a for a in fm.inboxes() if a["id"] == fid][0]["label"] is None)
    check("set_label on an unknown inbox is False",
          not fm.set_label("nope", "B70")["ok"])
    fm.set_label(fid, "AZ Profile 7")          # restore for later checks
    check("pause", fm.set_active(fid, False)["ok"])
    check("resume", fm.set_active(fid, True)["ok"])
    check("redact hides password", all("app_password" not in a
          and a.get("has_password") for a in fm.inboxes()))

    print("— fake-IMAP poll (NULL-cursor seed + sent_at + GWD harvest) —")

    def _mail(subject, sender, date, mid, body):
        m = EmailMessage()
        m["Subject"] = subject
        m["From"] = sender
        m["Date"] = date
        m["Message-ID"] = mid
        m.set_content(body)
        return m

    # uid 43 → flagged (keyword in subject) AND carries two parcel numbers
    _M43 = _mail("Action Required: verify your payment method",
                 "Amazon <account-update@amazon.com>",
                 "Wed, 13 Aug 2026 09:15:00 +0300", "<fake-43@amazon.com>",
                 "held: GWD123456789 and also gwd987654321 — please act")
    # uid 44 → NOT flagged (no keyword), but still donates its number
    _M44 = _mail("Your parcel is on the way", "GAASH <no-reply@gaash.com>",
                 "Wed, 13 Aug 2026 10:00:00 +0300", "<fake-44@gaash.com>",
                 "shipment GWD555000111 left the warehouse")
    _MSGS = {43: _M43, 44: _M44}

    class _FakeIMAP:
        """UIDVALIDITY 7, UIDNEXT 45, messages at uid 43 and 44. Serves the
        header-only and the partial-body fetch the poll makes."""

        def __init__(self, host=None, port=None):
            pass

        def login(self, e, p):
            return ("OK", [b"ok"])

        def status(self, folder, what):
            return ("OK", [b"INBOX (UIDVALIDITY 7 UIDNEXT 45)"])

        def select(self, folder, readonly=True):
            return ("OK", [b"1"])

        def uid(self, cmd, *args):
            if cmd == "search":
                return ("OK", [b"43 44"])
            if cmd == "FETCH":
                uid, spec = int(args[0]), args[1]
                m = _MSGS.get(uid)
                if not m:
                    return ("NO", [])
                if "HEADER" in spec:
                    payload = bytes(m).split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
                    return ("OK", [(b"%d (BODY[HEADER])" % uid, payload)])
                return ("OK", [(b"%d (BODY[]<0>)" % uid, bytes(m))])
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
          row5["imap_last_uid"] == 44 and row5["last_error"] is None)

    boxes = {b["email"]: b for b in fm.inboxes()}
    seen = boxes["watch@gmail.com"]["last_seen"]
    check("last_seen records BOTH messages, newest first, with the verdict",
          len(seen) == 2 and seen[0]["uid"] == 44 and seen[1]["uid"] == 43
          and seen[1]["hit"] is True and seen[0]["hit"] is False
          and "Action Required" in seen[1]["subject"])
    check("last_seen shows which numbers came out of which mail",
          sorted(seen[1].get("gwds") or []) == ["GWD123456789", "GWD987654321"]
          and (seen[0].get("gwds") or []) == ["GWD555000111"])
    check("an inbox that read nothing has an empty last_seen",
          boxes["other@gmail.com"]["last_seen"] == [])

    gw, gw_trunc = fm.gwds()
    got = sorted(g["gwd"] for g in gw)
    check("GWDs harvested from flagged AND unflagged mail",
          got == ["GWD123456789", "GWD555000111", "GWD987654321"]
          and not gw_trunc)
    check("GWD rows carry the inbox + its profile",
          all(g["email"] == "watch@gmail.com" and g["profile"] == "AZ Profile 7"
              for g in gw))
    check("the unflagged mail's number is attributed to its own email",
          [g["subject"] for g in gw if g["gwd"] == "GWD555000111"]
          == ["Your parcel is on the way"])
    _il.IMAP4_SSL = _FakeIMAP
    try:
        again = fm.poll_once()
    finally:
        _il.IMAP4_SSL = _real_imap
    check("re-poll adds nothing (cursor + unique index)",
          again == 0 and len(fm.gwds()[0]) == 3)

    print("— history —")
    hist, htrunc = fm.all_flags()
    check("history holds the flag, newest first, with profile",
          len(hist) == 1 and hist[0]["subject"].startswith("Action Required")
          and hist[0]["profile"] == "AZ Profile 7" and not htrunc)
    fm.ack_all()
    hist2, _ = fm.all_flags()
    check("closed flags STAY in history (the whole point)",
          len(hist2) == 1 and hist2[0]["state"] == "done"
          and hist2[0]["done_at"] and not fm.open_flags())
    check("history limit + truncated flag", fm.all_flags(limit=1)[1] is False)
    txt = fm._build_message(fl, datetime.now(timezone.utc))
    check("nag line: profile · email + sent time + done hint",
          "AZ Profile 7 · watch@gmail.com" in txt and "sent" in txt
          and "done" in txt)
    _clear_flags()

    print("— sender rule end to end (a poll that fires on WHO sent it) —")
    fm.save_settings({"phrases": "action required", "senders": "xm.com"})
    _M45 = _mail("Monthly statement", "XM Global <no-reply@xm.com>",
                 "Thu, 14 Aug 2026 08:00:00 +0300", "<fake-45@xm.com>",
                 "your statement is ready")

    class _FakeIMAP45(_FakeIMAP):
        """Same mailbox, one message further on: uid 45 from XM, with a
        subject no phrase rule would ever catch."""

        def status(self, folder, what):
            return ("OK", [b"INBOX (UIDVALIDITY 7 UIDNEXT 46)"])

        def uid(self, cmd, *args):
            if cmd == "search":
                return ("OK", [b"45"])
            if cmd == "FETCH":
                spec = args[1]
                if "HEADER" in spec:
                    payload = bytes(_M45).split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
                    return ("OK", [(b"45 (BODY[HEADER])", payload)])
                return ("OK", [(b"45 (BODY[]<0>)", bytes(_M45))])
            return ("OK", [])

    _il.IMAP4_SSL = _FakeIMAP45
    try:
        xm = fm.poll_once()
    finally:
        _il.IMAP4_SSL = _real_imap
    # every active inbox sees the same fake mailbox, so flag one by inbox
    fl45 = [f for f in fm.open_flags() if f["email"] == "watch@gmail.com"]
    check("an XM email with a plain subject is flagged by its sender",
          xm >= 1 and len(fl45) == 1
          and fl45[0]["subject"] == "Monthly statement"
          and fl45[0]["matched_phrase"] == "from: xm.com")
    check("the history says WHY it fired",
          all(h["matched_phrase"] == "from: xm.com" for h in fm.all_flags()[0]))
    fm.save_settings({"phrases": "action required", "senders": ""})
    _clear_flags()

    print("— per-inbox rules (each account its own flag) —")
    fm.save_settings({"phrases": "action required", "senders": "",
                      "word_phrases": ""})
    own = fm.add_inbox("owner000@gmail.com", "iiiijjjjkkkkllll", "OWNER",
                       verify=lambda e, pw: (7, 42))
    check("a new inbox follows the shared rules",
          [a for a in fm.inboxes() if a["id"] == own["id"]][0]["uses_shared"]
          and fm.rules_of([a for a in fm.inboxes()
                           if a["id"] == own["id"]][0])["phrases"]
          == ["action required"])
    res, err = fm.set_rules(own["id"], {"senders": "xm.com",
                                        "word_phrases": "xm", "phrases": ""})
    got = {a["id"]: a for a in fm.inboxes()}
    check("its own rules replace the shared set for THAT inbox only",
          err is None and not res["uses_shared"]
          and not got[own["id"]]["uses_shared"]
          and got[own["id"]]["rules"]["senders"] == ["xm.com"]
          and got[own["id"]]["rules"]["phrases"] == []
          and got[fid]["uses_shared"]
          and got[fid]["rules"]["phrases"] == ["action required"])
    fm.save_settings({"phrases": "urgent reply"})     # the shared field moves…
    got2 = {a["id"]: a for a in fm.inboxes()}
    check("…and editing the shared rules can never retarget it",
          got2[own["id"]]["rules"]["senders"] == ["xm.com"]
          and got2[own["id"]]["rules"]["phrases"] == []
          and got2[fid]["rules"]["phrases"] == ["urgent reply"])
    check("the XM inbox flags an XM sender and ignores action-required",
          fm.match_mail("Monthly statement", "XM <no-reply@xm.com>",
                        got2[own["id"]]["rules"]) == "from: xm.com"
          and fm.match_mail("Action required: verify", "a@amazon.com",
                            got2[own["id"]]["rules"]) is None)
    check("…while the Amazon inbox still only answers to its own phrase",
          fm.match_mail("URGENT REPLY needed", "a@amazon.com",
                        got2[fid]["rules"]) == "urgent reply"
          and fm.match_mail("Monthly statement", "XM <no-reply@xm.com>",
                            got2[fid]["rules"]) is None)
    fm.set_rules(own["id"], {"use_shared": True, "senders": "xm.com"})
    check("use_shared hands it back, even with fields still filled in",
          [a for a in fm.inboxes() if a["id"] == own["id"]][0]["uses_shared"])
    check("three empty fields mean shared too, and an unknown inbox errors",
          fm.set_rules(own["id"], {})[0]["uses_shared"]
          and fm.set_rules("nope", {"senders": "x"})[1] == "no such inbox")
    check("rules_json never leaks to the UI payload",
          all("rules_json" not in a for a in fm.inboxes()))
    fm.remove_inbox(own["id"])
    fm.save_settings({"phrases": "action required"})

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
    check("flags_configured false without token", not fm.flags_configured()
          and fm.flags_missing() == "FLAGS_BOT_TOKEN")
    os.environ["FLAGS_BOT_TOKEN"] = "test:flags"
    check("flags_configured true again", fm.flags_configured()
          and fm.flags_missing() == "")
    # the 2026-08-14 outage: a good token but no chat id anywhere. The page
    # must name the MISSING piece, not blame the token.
    _chat = os.environ.pop("TELEGRAM_CHAT_ID")
    check("no chat id → not configured, and it says which",
          not fm.flags_configured() and "CHAT_ID" in fm.flags_missing())
    os.environ["FLAGS_CHAT_ID"] = "777"
    check("FLAGS_CHAT_ID alone is enough", fm.flags_configured()
          and fm._flags_chat() == "777")
    os.environ["TELEGRAM_CHAT_ID"] = _chat
    check("FLAGS_CHAT_ID wins over TELEGRAM_CHAT_ID", fm._flags_chat() == "777")
    os.environ.pop("FLAGS_CHAT_ID")
    check("falls back to TELEGRAM_CHAT_ID", fm._flags_chat() == _chat)
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
    check("payload carries history + gwds for the table",
          isinstance(d.get("history"), list) and isinstance(d.get("gwds"), list)
          and any(h["state"] == "done" for h in d["history"])
          and "history_truncated" in d)
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
    _lid = [a for a in fm.inboxes() if a["email"] == "route@gmail.com"][0]["id"]
    check("sales cannot set a profile",
          sal.post("/api/flags/inbox/label",
                   json={"id": _lid, "label": "B70"}).status_code == 403)
    check("fulfillment cannot set a profile",
          ful.post("/api/flags/inbox/label",
                   json={"id": _lid, "label": "B70"}).status_code == 403)
    d = adm.post("/api/flags/inbox/label",
                 json={"id": _lid, "label": "B70"}).get_json()
    check("admin sets a profile via the route", d["ok"]
          and [a for a in fm.inboxes() if a["id"] == _lid][0]["label"] == "B70")
    d = ful.get("/api/leluxe/field_options?field=NAME").get_json()
    check("field options readable by fulfillment (the picker's source)",
          d["ok"] and isinstance(d["options"], list))
    check("sales blocked from field options",
          sal.get("/api/leluxe/field_options").status_code == 403)
    d = adm.post("/api/flags/settings", json={"repeat_min": 3}).get_json()
    check("admin saves settings", d["ok"] and d["settings"]["repeat_min"] == 3)
    d = ful.post("/api/flags/done", json={}).get_json()
    check("manual done route", d["ok"] and d["closed"] == 1
          and not fm.open_flags())
    d = adm.post("/api/flags/inbox/remove",
                 json={"id": [a for a in fm.inboxes()
                              if a["email"] == "route@gmail.com"][0]["id"]}).get_json()
    check("admin removes via route", d["ok"])

    print("— UI regression guards (web/index.html) —")
    from pathlib import Path
    ui = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    # 2026-08-14, reported live: picking a profile called flagDraw(), which
    # rebuilt the add form and wiped the email + app password already typed
    # above it, so the inbox was silently never added. Two guards, because the
    # fix has two independent halves.
    body = ui.split("function flagProfilePick(")[1].split("\nasync function")[0]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("//"))   # comments may NAME it
    check("picking a profile must never re-render the form",
          "flagDraw(" not in code)
    check("…it writes the field in place instead",
          '$("flagAcName")' in code and "i.value=v" in code.replace(" ", ""))
    check("a redraw preserves anything half-typed (email, password, profile)",
          "keep.e" in ui and "keep.p" in ui and "keep.n" in ui)

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

    print("— corrupt table: junk rows + restore from a backup (2026-09-12) —")
    _ok = lambda e, pw: (7, 42)             # injected verifier — no network
    fm.add_inbox("b70@gmail.com", "aaaabbbbccccdddd", "B70", verify=_ok)
    fm.add_inbox("sb10@gmail.com", "eeeeffffgggghhhh", "S-B10", verify=_ok)
    mine = {"b70@gmail.com", "sb10@gmail.com"}
    # what the rebuild actually left behind: rows salvaged from OTHER tables,
    # with TEXT where active=1 is expected — so the poll matched nothing and
    # the watch went dark without a single error line
    with db.connect() as c:
        c.execute("INSERT INTO flag_inboxes (id,email,label,app_password,"
                  "active,added_at) VALUES (?,?,?,?,?,?)",
                  ("86caqpha6", "86caqpha6", "86caqph9j", "145", "item",
                   "9 Anne Klein AK-1362RGRG Diamond Dial Watch"))
        c.commit()
    live = [a["email"] for a in fm.inboxes()]
    check("junk rows never reach the 🚩 list",
          "86caqpha6" not in live and mine <= set(live))
    with db.connect() as c:
        raw = c.execute("SELECT COUNT(*) n FROM flag_inboxes").fetchone()["n"]
    check("…they are still in the table — filtered, not deleted on a sick DB",
          raw == len(live) + 1)

    snap = _TMP / "backup-copy.db"          # stand-in for the backup zip's db
    with db.connect() as c:
        t = sqlite3.connect(snap)
        c.backup(t)
        t.close()
    src = sqlite3.connect(str(snap))
    src.row_factory = sqlite3.Row
    gone = [dict(r) for r in src.execute(
        "SELECT id,email,app_password FROM flag_inboxes WHERE email LIKE '%@%'")]
    src.close()
    with db.connect() as c:                 # now lose them, the way the page did
        c.execute("DELETE FROM flag_inboxes WHERE email LIKE '%@%'")
        c.commit()
    check("the watch is blind: no inbox at all, and the poll reads nothing",
          fm.inboxes() == [] and fm.poll_once() == 0)
    res = fm.restore_inboxes(snap)
    back = {a["email"]: a for a in fm.inboxes()}
    check("restore brings every inbox back under its OWN id — open flags stay "
          "bound", res["ok"] and sorted(res["restored"]) == sorted(
              r["email"] for r in gone)
          and all(back[r["email"]]["id"] == r["id"] for r in gone))
    check("…and with the app password, so Google is not asked for a new one",
          all(back[r["email"]]["has_password"] for r in gone))
    res2 = fm.restore_inboxes(snap)
    check("restoring twice changes nothing — an inbox live still has wins",
          res2["ok"] and res2["restored"] == []
          and sorted(res2["already_live"]) == sorted(r["email"] for r in gone)
          and len(fm.inboxes()) == len(gone))
    bad = fm.restore_inboxes(_TMP / "not-a-database.db")
    check("an unreadable backup is refused, not half-applied",
          not bad["ok"] and bad.get("error"))

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("all good ✓")


if __name__ == "__main__":
    main()
