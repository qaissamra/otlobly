"""🚩 Flag machine — email "action required" watch → Telegram nag until done.

Watches any number of Gmail inboxes through IMAP app passwords, PURE
OBSERVATION ONLY: `select(readonly=True)` + `BODY.PEEK[HEADER]` — nothing is
ever marked read, no mail is ever sent, the accounts are not linked to
anything. A NEW message whose subject contains a match phrase (default
"action required") raises an open flag; while any flag is open the owner
gets ONE batched message every repeat_min minutes from a DEDICATED flags bot
(env FLAGS_BOT_TOKEN; the chat id is the shared TELEGRAM_CHAT_ID — chat ids
are global per user) until they reply «done»/«تم» in that bot's chat. This
module IS the getUpdates consumer for that token — one consumer per bot
token, and this token has exactly one (the Mac's telegram_bot polls a
DIFFERENT token and knows nothing about flags).

Runs ONLY where env FLAG_MACHINE=1 — set in the RENDER DASHBOARD only, the
GAASH_MAILER precedent (never render.yaml, never the plist, never .env): the
live DB is the single truth, and the Mac's stale copy must never nag. Render
runs 2 gunicorn workers (recycled every ~300-500 requests), so: a DB lease
(flags:lease) elects the one polling worker, db.claim_once on a per-minute
bucket makes each nag exactly-once even through a lease race, and every
cursor (IMAP cadence, Telegram offset) lives in the DB so a worker recycle
is invisible. IMAP mechanics cloned from gaash_mail._check_account;
send-then-stamp discipline from alerts.py.
"""

import imaplib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy

from email.utils import parsedate_to_datetime

import db
import memlog
import telegram
from gaash_mail import _AUTH_BLOCKED, _AUTH_HELP, _clean_pw, _mailbox_status

IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
MAX_SEEN_IDS = 2000                 # per-inbox Message-ID dedupe backstop
POLL_UID_CAP = 500                  # per pass — the rest arrive next tick
LIST_CAP = 10                       # flags listed per Telegram message
SETTINGS_KEY = "flags:settings"
LEASE_KEY = "flags:lease"
OFFSET_KEY = "flags:tg_offset"
POLL_AT_KEY = "flags:last_poll_at"
LEASE_STALE_S = 180                 # one pass = ≤25s long-poll + 20s send +
                                    # a multi-inbox IMAP sweep; 90s is too tight
UPDATES_TIMEOUT_S = 25
DONE_WORDS = ("done", "تم", "خلص", "خلصت", "تمام")

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
# The flags bot + the single-runner lease
# --------------------------------------------------------------------------- #
def _flags_token():
    """The dedicated flags bot's token — env FLAGS_BOT_TOKEN ONLY, no config
    fallback on purpose: the daemon runs only on Render (where the dashboard
    holds the env var), and a Settings-page fallback would let a stray paste
    on the Mac become a second getUpdates consumer against a stale DB."""
    return (os.environ.get("FLAGS_BOT_TOKEN") or "").strip()


def _flags_chat():
    """Where the nag goes: env FLAGS_CHAT_ID, else the alerts bot's chat.

    The fallback is a convenience, not a dependency — 2026-08-14 the flags
    machine sat mute for hours because TELEGRAM_CHAT_ID had never been set on
    Render, and the page blamed the (perfectly good) flags token. Own the
    chat id here so this feature can never again be dark because a DIFFERENT
    feature is unconfigured."""
    return ((os.environ.get("FLAGS_CHAT_ID") or "").strip()
            or telegram._creds()[1])


def flags_missing():
    """Which piece of the flags-bot config is absent — '' when it's ready.
    Named parts, because 'not configured' cost an afternoon once."""
    if not _flags_token():
        return "FLAGS_BOT_TOKEN"
    if not _flags_chat():
        return "FLAGS_CHAT_ID (or TELEGRAM_CHAT_ID)"
    return ""


def flags_configured():
    return not flags_missing()


_me = None


def _my_id():
    """Per-PROCESS lease identity, created lazily INSIDE the daemon thread —
    a module-import uuid (telegram_bot's _ME pattern) would be shared by every
    gunicorn worker under fork-after-import, and each would think the lease
    was its own. The pid keeps it robust even under a future --preload."""
    global _me
    if _me is None:
        _me = f"{os.getpid()}-{uuid.uuid4().hex}"
    return _me


def _lease_ok(me=None):
    """True when this process holds (or takes over) the single-poller lease.
    Clone of telegram_bot._lease_ok with a wider staleness window (a slow
    IMAP sweep must not lose the lease mid-pass) — and even a raced takeover
    is safe: nags are claim-guarded, flag inserts are INSERT OR IGNORE,
    ack_all is idempotent, offsets move through max()."""
    me = me or _my_id()
    lease = db.get_setting(LEASE_KEY)
    if isinstance(lease, dict) and lease.get("id") != me:
        try:
            age = (datetime.now(datetime.fromisoformat(lease["ts"]).tzinfo)
                   - datetime.fromisoformat(lease["ts"])).total_seconds()
        except (TypeError, ValueError, KeyError):
            age = 9999
        if age < LEASE_STALE_S:
            return False
    db.set_setting(LEASE_KEY, {"id": me, "ts": db.now_iso()})
    return True


def _drain_offset(get):
    """First run ever: skip the backlog so old messages never execute a stale
    «done». Only the lease holder reaches this, and a raced drain would
    execute nothing anyway — no claim needed."""
    out = get(None, 0)
    updates = out.get("result") or []
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


def poll_updates(get=None, send=None):
    """Drain the FLAGS bot's getUpdates; «done»/«تم» from the owner chat
    closes every open flag. Any other owner text gets a one-line hint — the
    bot's whole vocabulary is one word, so it teaches itself (and pressing
    Start after setup doubles as a liveness check). Returns the replies sent
    (tests inject get/send — zero network)."""
    tok = _flags_token()
    get = get or (lambda off, t: telegram.get_updates(offset=off, timeout=t,
                                                      token=tok))
    send = send or (lambda chat, txt: telegram.send_to(chat, txt, token=tok))
    offset = db.get_setting(OFFSET_KEY)
    if offset is None:
        db.set_setting(OFFSET_KEY, _drain_offset(get))
        return []
    out = get(offset or None, UPDATES_TIMEOUT_S)
    if not out.get("ok"):
        return []
    owner = _flags_chat()
    replies = []
    for u in out.get("result") or []:
        offset = max(offset or 0, u.get("update_id", 0) + 1)
        msg = u.get("message") or u.get("edited_message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if not chat_id or str(chat_id) != str(owner or ""):
            continue                  # not the owner → ignore silently
        t = " ".join(str(msg.get("text") or "").casefold().split())
        if t in DONE_WORDS:
            n = ack_all()
            txt = (f"✅ تم — سكّرت {n} تنبيه 🚩 · closed {n} flag(s)" if n
                   else "ما في تنبيهات 🚩 مفتوحة · no open flags")
        else:
            txt = ("🚩 رد «done» أو «تم» هنا لإغلاق التنبيهات · "
                   "reply done/تم here to close the flags")
        send(chat_id, txt)
        replies.append(txt)
    db.set_setting(OFFSET_KEY, offset)
    return replies


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
    """Verify the IMAP login, then store the inbox. Never raises — and never
    VANISHES: a login Gmail refuses is still saved, with the refusal in
    last_error, so what the owner typed always shows up in the list (⚠ chip
    + 🔑 re-add fix; the poll keeps retrying it). The saved row's cursor
    stays NULL until a login succeeds — the first good poll then seeds at the
    newest message, so old mail is never flagged.

    Re-adding an email that already exists UPDATES its password (and profile
    name) in place — the id and cursor survive, so open flags stay bound and
    no old mail is re-read. That's the recovery path when Google revokes an
    app password."""
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
    err = None
    try:
        uv, un = (verify or _verify_imap)(email_addr, pw)
    except imaplib.IMAP4.error as e:
        # IMAP says WEB_LOGIN_REQUIRED for the block SMTP calls 5.7.14
        said = " ".join(str(e).split())[:200]
        blocked = "web_login" in said.lower() or "webloginrequired" in said.lower()
        err = ((_AUTH_BLOCKED if blocked else _AUTH_HELP)
               + " (Also make sure IMAP is enabled in Gmail settings.)"
               + (f" — Gmail said: {said}" if said else ""))
    except Exception as e:  # noqa
        err = f"IMAP connection failed: {str(e)[:120]}"
    if existing_id:
        with db.connect() as c:
            c.execute("UPDATE flag_inboxes SET app_password=?, last_error=? "
                      "WHERE id=?", (pw, err, existing_id))
            if (label or "").strip():
                c.execute("UPDATE flag_inboxes SET label=? WHERE id=?",
                          ((label or "").strip(), existing_id))
        out = {"ok": True, "id": existing_id, "email": email_addr,
               "updated": True}
    else:
        fid = f"fin_{int(time.time() * 1000)}"
        with db.connect() as c:
            c.execute("""INSERT INTO flag_inboxes
                (id,email,label,app_password,active,added_at,last_error,
                 imap_uidvalidity,imap_last_uid,seen_ids_json)
                VALUES (?,?,?,?,1,?,?,?,?,?)""",
                      (fid, email_addr, (label or "").strip() or None, pw,
                       db.now_iso(), err, uv,
                       # start at newest: never flag old mail. Verify failed →
                       # NULL cursor; the first GOOD poll seeds it instead.
                       None if err else max(0, (un or 1) - 1),
                       "[]"))
        out = {"ok": True, "id": fid, "email": email_addr}
    if err:
        out["saved_with_error"] = err
    return out


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
def _parse_date_hdr(s):
    """The email's own Date header → ISO string, or None on garbage."""
    try:
        dt = parsedate_to_datetime(str(s or ""))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa
        return None


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
        if inbox.get("imap_last_uid") is None:
            # saved-with-error row logging in for the first time: seed at the
            # newest message — a 0 cursor would ingest the ENTIRE mailbox
            last = max(0, (un or 1) - 1)
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
                              "matched_phrase": phrase,
                              "sent_at": _parse_date_hdr(hm.get("Date"))})
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
                     sent_at,created_at,state,sent_count)
                    VALUES (?,?,?,?,?,?,?,?,?, 'open', 0)""",
                                (a["id"], a["email"], f["msg_id"], f["uid"],
                                 f["subject"], f["sender"], f["matched_phrase"],
                                 f.get("sent_at") or db.now_iso(),
                                 db.now_iso()))
                raised += cur.rowcount
    return raised


# --------------------------------------------------------------------------- #
# Alert pass — the repeating nag
# --------------------------------------------------------------------------- #
def open_flags():
    """Open flags + each one's profile name (the inbox label — NULL when the
    inbox was removed or never labeled; the flag's own email copy survives).
    One query feeds the UI, the Telegram nag and the bell alike."""
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT a.*, i.label AS profile FROM flag_alerts a "
            "LEFT JOIN flag_inboxes i ON i.id = a.inbox_id "
            "WHERE a.state='open' ORDER BY a.created_at")]


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


def _sent_label(now, iso):
    """When the email was sent: clock time if within a day, else age."""
    dt = _parse_iso(iso or "")
    if not dt:
        return None
    if (now - dt).total_seconds() < 86400:
        return dt.astimezone().strftime("%H:%M")
    return _age(now, iso)


def _build_message(flags, now):
    lines = [f"🚩 ACTION REQUIRED — {len(flags)} تنبيه مفتوح · open flag(s)", ""]
    for i, f in enumerate(flags[:LIST_CAP], 1):
        nagged = f" (sent {f['sent_count']}×)" if f.get("sent_count") else ""
        who = (f"{f['profile']} · {f['email']}" if f.get("profile")
               else f["email"])
        lines.append(f"{i}) {who}")
        lines.append(f"   «{(f.get('subject') or '(no subject)')[:120]}»")
        at = _sent_label(now, f.get("sent_at"))
        lines.append(f"   from: {(f.get('sender') or '?')[:80]}"
                     + (f" · أُرسل · sent {at}" if at else "")
                     + f" · open {_age(now, f.get('created_at'))}{nagged}")
    if len(flags) > LIST_CAP:
        lines.append(f"(+{len(flags) - LIST_CAP} أخرى · more)")
    lines += ["", "رد «done» أو «تم» هنا · "
                  "reply \"done\" here in this chat to stop these alerts"]
    return "\n".join(lines)


def alert_once(now=None, send=None):
    """One nag cycle: if flags are open and the repeat interval passed, send
    ONE batched Telegram message (from the flags bot) and stamp it — stamp
    only AFTER an ok send (alerts.py discipline: a failed send is a free
    retry next tick). A db.claim_once on the minute bucket makes the send
    exactly-once across gunicorn workers: the lease read-then-write is not
    atomic, so a lease race alone could double-nag — the claim cannot."""
    send = send or (lambda t: telegram.send_to(_flags_chat(), t,
                                               token=_flags_token()))
    now = now or datetime.now(timezone.utc)
    flags = open_flags()
    if not flags or not flags_configured():
        return []
    repeat_s = settings()["repeat_min"] * 60
    newest_sent = max((_parse_iso(f["last_sent_at"]) for f in flags
                       if f.get("last_sent_at")), default=None)
    fresh = any(not f.get("last_sent_at") for f in flags)
    if not fresh and newest_sent and (now - newest_sent).total_seconds() < repeat_s:
        return []
    bucket = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
    claim = f"flags:nag:{bucket}"
    if not db.claim_once(claim):
        return []                     # the other worker owns this minute
    txt = _build_message(flags, now)
    if not (send(txt) or {}).get("ok"):
        with db.connect() as c:       # free the minute so the next tick retries
            c.execute("DELETE FROM settings WHERE key=?", (claim,))
        return []
    # claim-key hygiene — a week-long open flag at repeat_min=1 would leak
    # ~1440 settings rows/day; the YYYYMMDDHHMM buckets sort lexicographically
    with db.connect() as c:
        c.execute("DELETE FROM settings WHERE key LIKE 'flags:nag:%' AND key<?",
                  (f"flags:nag:{(now - timedelta(hours=24)).astimezone(timezone.utc).strftime('%Y%m%d%H%M')}",))
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
# Daemon — lease-elected loop, paced by the flags bot's long-poll
# --------------------------------------------------------------------------- #
_started = False


def run_once(now=None, send=None, do_poll=None):
    """One tick: (a) IMAP poll when due, (b) the repeating nag. The «done»
    half lives in poll_updates(), not here. Tests pass do_poll=False and a
    fake send. The poll cursor is a DB setting (not a module global) so the
    cadence stays single across workers and survives worker recycling."""
    now = now or datetime.now(timezone.utc)
    if do_poll is None:
        last = _parse_iso(db.get_setting(POLL_AT_KEY) or "")
        do_poll = not last or (now - last).total_seconds() >= \
            max(1, int(settings()["poll_interval_min"])) * 60
    if do_poll:
        # set BEFORE polling — a crashing IMAP pass must not tight-loop
        db.set_setting(POLL_AT_KEY, now.isoformat(timespec="seconds"))
        poll_once()
    return alert_once(now=now, send=send)


def _loop():
    time.sleep(60)                    # let the app finish booting first
    while True:
        wait = 2                      # the ≤25s getUpdates long-poll is the
        try:                          # real pacing; nag/IMAP have DB due-checks
            with memlog.watch("flags"):
                if not flags_configured():
                    wait = 300
                elif not _lease_ok():
                    wait = 60         # the other worker holds the lease
                else:
                    poll_updates()    # «done» — returns fast on a message
                    _lease_ok()       # restamp before the slow IMAP half
                    out = run_once()
                    if out:
                        print("flags: nagged (1 message)")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"flags: pass failed ({e})")
            wait = 10
        time.sleep(wait)


def start():
    """🚩 watcher + flags-bot «done» loop. No-op unless env FLAG_MACHINE is
    truthy — set ONLY in the Render dashboard (the GAASH_MAILER precedent:
    the live DB is the single truth; never render.yaml, never the Mac plist,
    never .env). Both gunicorn workers start the thread; the flags:lease
    picks the one that actually polls."""
    global _started
    if _started or os.environ.get("FLAG_MACHINE", "") in ("", "0"):
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-flags", daemon=True).start()
