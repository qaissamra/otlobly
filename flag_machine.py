"""🚩 Flag machine — email "action required" watch → Telegram nag until done.

Watches any number of Gmail inboxes through IMAP app passwords, PURE
OBSERVATION ONLY: `select(readonly=True)` + `BODY.PEEK[HEADER]` — nothing is
ever marked read, no mail is ever sent, the accounts are not linked to
anything. A NEW message whose subject contains a match phrase (default
"action required") raises an open flag; while any flag is open the owner
gets ONE batched Telegram message every repeat_min minutes until they reply
«done»/«تم» to the bot (telegram_bot.py handles the reply — this module must
NEVER call getUpdates: one consumer per bot token).

Runs ONLY where env FLAG_MACHINE=1 — set in com.otlobly.app.plist (the Mac's
always-on single-process app), never on Render. IMAP mechanics cloned from
gaash_mail._check_account; send-then-stamp discipline from alerts.py.
"""

import imaplib
import json
import os
import threading
import time
from datetime import datetime, timezone
from email import message_from_bytes, policy

import db
import memlog
import telegram
from gaash_mail import _AUTH_BLOCKED, _AUTH_HELP, _clean_pw, _mailbox_status

IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
MAX_SEEN_IDS = 2000                 # per-inbox Message-ID dedupe backstop
POLL_UID_CAP = 500                  # per pass — the rest arrive next tick
LIST_CAP = 10                       # flags listed per Telegram message
TICK_SECONDS = 60
SETTINGS_KEY = "flags:settings"

DEFAULTS = {
    "enabled": True,
    "phrases": ["action required"],   # subject substrings, case-insensitive
    "poll_interval_min": 2,           # IMAP pass cadence
    "repeat_min": 1,                  # nag cadence — the every-minute ask
}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def settings():
    st = dict(DEFAULTS)
    saved = db.get_setting(SETTINGS_KEY)
    if isinstance(saved, dict):
        st.update({k: saved[k] for k in DEFAULTS if k in saved})
    return st


def save_settings(body):
    """Validate + persist the 🚩 settings form. Returns (settings, error)."""
    st = settings()
    b = body or {}
    if "phrases" in b:
        raw = b["phrases"]
        if isinstance(raw, str):                 # the UI sends comma-separated
            raw = raw.split(",")
        if not isinstance(raw, list):
            return None, "phrases must be a list or comma-separated text"
        phrases = [" ".join(str(p).split()) for p in raw]
        phrases = [p for p in phrases if p]
        if not phrases:
            return None, "at least one match phrase is required"
        st["phrases"] = phrases
    try:
        if "poll_interval_min" in b:
            st["poll_interval_min"] = int(b["poll_interval_min"])
        if "repeat_min" in b:
            st["repeat_min"] = int(b["repeat_min"])
    except (TypeError, ValueError):
        return None, "intervals must be whole minutes"
    if st["poll_interval_min"] < 1 or st["repeat_min"] < 1:
        return None, "intervals must be at least 1 minute"
    if "enabled" in b:
        st["enabled"] = bool(b["enabled"])
    db.set_setting(SETTINGS_KEY, st)
    return st, None


# --------------------------------------------------------------------------- #
# Inboxes (Gmail + app passwords) — verified on add, stored in flag_inboxes
# --------------------------------------------------------------------------- #
def inboxes(redact=True):
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM flag_inboxes ORDER BY added_at")]
    for a in rows:
        a.pop("seen_ids_json", None)
        if redact:
            a["has_password"] = bool(a.pop("app_password", None))
    return rows


def _verify_imap(email_addr, pw):
    """Prove the login works and read the mailbox position. Returns
    (uidvalidity, uidnext); raises imaplib.IMAP4.error on a refusal."""
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(email_addr, pw)
        return _mailbox_status(M)
    finally:
        try:
            M.logout()
        except Exception:  # noqa
            pass


def add_inbox(email_addr, app_password, label=None, verify=None):
    """Verify the IMAP login, then store the inbox. Never raises.

    Re-adding an email that already exists UPDATES its password in place —
    the id and cursor survive, so open flags stay bound and no old mail is
    re-read. That's the recovery path when Google revokes an app password."""
    email_addr = (email_addr or "").strip().lower()
    pw = _clean_pw(app_password)
    if not email_addr or "@" not in email_addr:
        return {"ok": False, "error": "enter a valid email address"}
    if not pw:
        return {"ok": False, "error": "enter the app password"}
    with db.connect() as c:
        row = c.execute("SELECT id FROM flag_inboxes WHERE email=?",
                        (email_addr,)).fetchone()
    existing_id = row["id"] if row else None
    uv = un = None
    try:
        uv, un = (verify or _verify_imap)(email_addr, pw)
    except imaplib.IMAP4.error as e:
        # IMAP says WEB_LOGIN_REQUIRED for the block SMTP calls 5.7.14
        said = " ".join(str(e).split())[:200]
        blocked = "web_login" in said.lower() or "webloginrequired" in said.lower()
        return {"ok": False,
                "error": (_AUTH_BLOCKED if blocked else _AUTH_HELP)
                + " (Also make sure IMAP is enabled in Gmail settings.)"
                + (f" — Gmail said: {said}" if said else "")}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"IMAP connection failed: {str(e)[:120]}"}
    if existing_id:
        with db.connect() as c:
            c.execute("UPDATE flag_inboxes SET app_password=?, last_error=NULL "
                      "WHERE id=?", (pw, existing_id))
            if (label or "").strip():
                c.execute("UPDATE flag_inboxes SET label=? WHERE id=?",
                          ((label or "").strip(), existing_id))
        return {"ok": True, "id": existing_id, "email": email_addr,
                "updated": True}
    fid = f"fin_{int(time.time() * 1000)}"
    with db.connect() as c:
        c.execute("""INSERT INTO flag_inboxes
            (id,email,label,app_password,active,added_at,
             imap_uidvalidity,imap_last_uid,seen_ids_json)
            VALUES (?,?,?,?,1,?,?,?,?)""",
                  (fid, email_addr, (label or "").strip() or None, pw,
                   db.now_iso(), uv,
                   max(0, (un or 1) - 1),   # start at newest: never flag old mail
                   "[]"))
    return {"ok": True, "id": fid, "email": email_addr}


def remove_inbox(inbox_id):
    """Delete an inbox. Its open flags are deliberately left open — they keep
    nagging until «done» (they carry their own copy of the email address)."""
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM flag_alerts "
                      "WHERE state='open' AND inbox_id=?",
                      (inbox_id,)).fetchone()["n"]
        cur = c.execute("DELETE FROM flag_inboxes WHERE id=?", (inbox_id,))
        return {"ok": cur.rowcount > 0, "open_flags": n}


def set_active(inbox_id, active):
    with db.connect() as c:
        cur = c.execute("UPDATE flag_inboxes SET active=? WHERE id=?",
                        (1 if active else 0, inbox_id))
        return {"ok": cur.rowcount > 0}


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def match_subject(subject, phrases):
    """The phrase that fires, or None. Case/whitespace-insensitive substring
    match against the ALREADY-DECODED subject (policy.default did RFC2047)."""
    s = " ".join(str(subject or "").casefold().split())
    for p in phrases or []:
        pn = " ".join(str(p or "").casefold().split())
        if pn and pn in s:
            return p
    return None


# --------------------------------------------------------------------------- #
# Poll pass — read new mail, raise flags
# --------------------------------------------------------------------------- #
def _check_inbox(inbox, phrases):
    """Poll one Gmail INBOX header-only. Returns (new_flags, patch).
    Raises on connection errors (poll_once catches per inbox)."""
    seen = set(json.loads(inbox.get("seen_ids_json") or "[]"))
    last = int(inbox.get("imap_last_uid") or 0)
    new_flags = []

    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(inbox["email"], _clean_pw(inbox.get("app_password")))
        uv, un = _mailbox_status(M)
        if uv and inbox.get("imap_uidvalidity") and uv != inbox["imap_uidvalidity"]:
            last = max(0, (un or 1) - 1)   # mailbox renumbered — resume at the end
        M.select("INBOX", readonly=True)
        typ, data = M.uid("search", None, "UID", f"{last + 1}:*")
        uids = []
        if typ == "OK" and data and data[0]:
            # IMAP quirk: "n:*" past the end still returns the last UID
            uids = sorted(u for u in (int(x) for x in data[0].split()) if u > last)
        uids = uids[:POLL_UID_CAP]         # cursor advances only through processed
        max_uid = last
        for uid in uids:
            max_uid = max(max_uid, uid)
            typ, data = M.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
            hdr = None
            if typ == "OK":
                for item in data or []:
                    if isinstance(item, tuple) and len(item) >= 2 and \
                            isinstance(item[1], (bytes, bytearray)):
                        hdr = bytes(item[1])
                        break
            if not hdr:
                continue
            hm = message_from_bytes(hdr, policy=policy.default)
            mid = (str(hm.get("Message-ID") or "")).strip() or \
                f"<uid-{uv}-{uid}@{inbox['email']}>"
            if mid in seen:
                continue
            seen.add(mid)
            subj = str(hm.get("Subject") or "")
            phrase = match_subject(subj, phrases)
            if not phrase:
                continue
            new_flags.append({"msg_id": mid, "uid": uid, "subject": subj,
                              "sender": str(hm.get("From") or ""),
                              "matched_phrase": phrase})
    finally:
        try:
            M.logout()
        except Exception:  # noqa
            pass

    patch = {"imap_last_uid": max_uid if uids else last,
             "imap_uidvalidity": uv or inbox.get("imap_uidvalidity"),
             "last_check": db.now_iso(),
             "seen_ids_json": json.dumps(list(seen)[-MAX_SEEN_IDS:])}
    return new_flags, patch


def poll_once():
    """Poll every active inbox. Never raises; one bad inbox never stops the
    rest. Returns the number of NEW flags raised."""
    st = settings()
    if not st["enabled"]:
        return 0
    with db.connect() as c:
        boxes = [dict(r) for r in c.execute(
            "SELECT * FROM flag_inboxes WHERE active=1")]
    raised = 0
    for a in boxes:
        try:
            flags, patch = _check_inbox(a, st["phrases"])
        except Exception as e:  # noqa
            msg = str(e)[:150]
            if "AUTHENTICATIONFAILED" in msg.upper():
                said = " ".join(str(e).split())[:200]
                blocked = "web_login" in said.lower() or \
                    "webloginrequired" in said.lower()
                msg = ((_AUTH_BLOCKED if blocked else _AUTH_HELP)
                       + (f" — Gmail said: {said}" if said else ""))
            with db.connect() as c:
                c.execute("UPDATE flag_inboxes SET last_check=?, last_error=? "
                          "WHERE id=?", (db.now_iso(), msg, a["id"]))
            continue
        with db.connect() as c:
            c.execute("UPDATE flag_inboxes SET imap_last_uid=?, "
                      "imap_uidvalidity=?, last_check=?, last_error=NULL, "
                      "seen_ids_json=? WHERE id=?",
                      (patch["imap_last_uid"], patch["imap_uidvalidity"],
                       patch["last_check"], patch["seen_ids_json"], a["id"]))
            for f in flags:
                # unique (email, msg_id) absorbs any UIDVALIDITY-overlap re-read
                cur = c.execute("""INSERT OR IGNORE INTO flag_alerts
                    (inbox_id,email,msg_id,uid,subject,sender,matched_phrase,
                     created_at,state,sent_count)
                    VALUES (?,?,?,?,?,?,?,?, 'open', 0)""",
                                (a["id"], a["email"], f["msg_id"], f["uid"],
                                 f["subject"], f["sender"], f["matched_phrase"],
                                 db.now_iso()))
                raised += cur.rowcount
    return raised


# --------------------------------------------------------------------------- #
# Alert pass — the repeating nag
# --------------------------------------------------------------------------- #
def open_flags():
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM flag_alerts WHERE state='open' ORDER BY created_at")]


def _parse_iso(s):
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa
        return None


def _age(now, iso):
    dt = _parse_iso(iso or "")
    if not dt:
        return "?"
    m = max(0, int((now - dt).total_seconds() // 60))
    if m < 60:
        return f"{m}m"
    if m < 60 * 24:
        return f"{m // 60}h"
    return f"{m // (60 * 24)}d"


def _build_message(flags, now):
    lines = [f"🚩 ACTION REQUIRED — {len(flags)} تنبيه مفتوح · open flag(s)", ""]
    for i, f in enumerate(flags[:LIST_CAP], 1):
        sent = f" (sent {f['sent_count']}×)" if f.get("sent_count") else ""
        lines.append(f"{i}) {f['email']}")
        lines.append(f"   «{(f.get('subject') or '(no subject)')[:120]}»")
        lines.append(f"   from: {(f.get('sender') or '?')[:80]} · "
                     f"open {_age(now, f.get('created_at'))}{sent}")
    if len(flags) > LIST_CAP:
        lines.append(f"(+{len(flags) - LIST_CAP} أخرى · more)")
    lines += ["", "رد «done» أو «تم» لإيقاف التنبيهات · "
                  "reply \"done\" to stop these alerts"]
    return "\n".join(lines)


def alert_once(now=None, send=None):
    """One nag cycle: if flags are open and the repeat interval passed, send
    ONE batched Telegram message and stamp it — stamp only AFTER an ok send
    (alerts.py discipline: a failed send is a free retry next tick)."""
    send = send or telegram.send
    now = now or datetime.now(timezone.utc)
    flags = open_flags()
    if not flags or not telegram.configured():
        return []
    repeat_s = settings()["repeat_min"] * 60
    newest_sent = max((_parse_iso(f["last_sent_at"]) for f in flags
                       if f.get("last_sent_at")), default=None)
    fresh = any(not f.get("last_sent_at") for f in flags)
    if not fresh and newest_sent and (now - newest_sent).total_seconds() < repeat_s:
        return []
    txt = _build_message(flags, now)
    if not (send(txt) or {}).get("ok"):
        return []
    ids = [f["id"] for f in flags]
    with db.connect() as c:
        c.execute(f"""UPDATE flag_alerts
            SET last_sent_at=?, sent_count=sent_count+1
            WHERE id IN ({','.join('?' * len(ids))})""",
                  (now.astimezone().isoformat(timespec="seconds"), *ids))
    return [txt]


def ack_all():
    """«done» from Telegram or the UI button → close EVERY open flag.
    Returns how many were closed."""
    with db.connect() as c:
        cur = c.execute("UPDATE flag_alerts SET state='done', done_at=? "
                        "WHERE state='open'", (db.now_iso(),))
        return cur.rowcount


# --------------------------------------------------------------------------- #
# Daemon — one thread, one tick per minute
# --------------------------------------------------------------------------- #
_started = False
_next_poll_at = 0.0          # module-global is fine: single process (the plist)


def run_once(now=None, send=None, do_poll=None):
    """One tick: (a) IMAP poll when due, (b) the repeating nag. Tests pass
    do_poll=False and a fake send."""
    global _next_poll_at
    if do_poll is None:
        do_poll = time.time() >= _next_poll_at
    if do_poll:
        _next_poll_at = time.time() + \
            max(1, int(settings()["poll_interval_min"])) * 60
        poll_once()
    return alert_once(now=now, send=send)


def _loop():
    time.sleep(60)                    # let the app finish booting first
    while True:
        try:
            with memlog.watch("flags"):
                out = run_once()
            if out:
                print(f"flags: nagged ({len(out)} message)")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"flags: pass failed ({e})")
        time.sleep(TICK_SECONDS)


def start():
    """🚩 watcher. No-op unless env FLAG_MACHINE is truthy — the flag lives
    ONLY in com.otlobly.app.plist (this Mac). The RENDER check is
    belt-and-suspenders: Render injects RENDER=true into every service, so a
    leaked FLAG_MACHINE var still can't double-poll from the cloud."""
    global _started
    if _started or os.environ.get("FLAG_MACHINE", "") in ("", "0") \
            or os.environ.get("RENDER"):
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-flags", daemon=True).start()
