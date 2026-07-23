"""GAASH Mail — automated customs-clearance email sequences.

The 📧 GAASH mail page: one email conversation (thread) per GWD package,
driven by a 4-step templated sequence — email 1 carries the tracking number
plus a chosen ID document from the reusable ID library; emails 2–4 are
escalating follow-ups sent on a cadence (default 2·2·2 days) while there is
no REAL reply.

The engine is a port of the owner's battle-tested local tool
(~/gaash-clickup-sync/support.py): multi-account Gmail via app passwords
(SMTP 587+STARTTLS / IMAP 993, both verified when an account is added),
threaded sends (In-Reply-To/References), IMAP polling with a UID cursor +
Message-ID dedupe, the owner's auto-ack rule (an incoming message landing
within `ack_window_min` minutes of our last send is `auto_ack` — never a real
reply), and office-closed detection ("closed now" ⇒ the mail was NOT received
⇒ auto-resend at `resend_hour` Israel time). State lives in SQLite
(gaash_accounts / gaash_ids / gaash_threads / gaash_msgs — owner-scoped like
the leluxe_* family) and attachment bytes under data/gaash_mail/<gwd>/.

Sequencer guards, in order, before every send:
  1. package already cleared/delivered (leluxe mirror bucket, else a live
     tracking lookup)  → state=cleared, stop;
  2. an unhandled REAL reply             → state=waiting_reply, pause;
  3. missing-docs flag (e.g. GAASH asked for a KMT) → paused until resolved;
  4. Settings dry_run                    → nothing is ever sent while ON;
  5. db.claim_once("gaashmail:{gwd}:step{n}") → exactly-once across workers.

RUNTIME RULE (critical): the daemon runs ONLY where env GAASH_MAILER=1.
Set that flag ONLY on Render — the live DB is the single source of truth.
NEVER set it in the Mac plist or a local .env: the Mac's always-on local app
holds a stale DB copy and would double-send. (This is the INVERSE of the
LELUXE_DIGEST convention, on purpose.)
"""

import imaplib
import json
import os
import re
import smtplib
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import db
import settings as settings_mod
from paths import data_path

IDS_DIR = data_path("gaash_ids")        # the reusable ID-document library
FILES_DIR = data_path("gaash_mail")     # per-thread attachment bytes

# port 587 + STARTTLS, not 465 — the owner's network blocks outbound SMTPS
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
IL_TZ = "Asia/Jerusalem"
MAX_SEEN_IDS = 2000                     # per-account Message-ID dedupe backstop

_AUTH_HELP = ("Gmail rejected this login. Use an App Password (not the normal "
              "password): turn on 2-Step Verification, then create one at "
              "myaccount.google.com/apppasswords and paste its 16 letters here.")


def _setts():
    return settings_mod.read().get("gaash_mail") or {}


def now_iso():
    return db.now_iso()


def _parse_iso(s):
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa
        return None


# --------------------------------------------------------------------------- #
# Accounts (Gmail + app passwords) — verified on add, stored in gaash_accounts
# --------------------------------------------------------------------------- #
def _smtp_connect(timeout=30):
    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout)
    s.ehlo()
    s.starttls()
    s.ehlo()
    return s


def accounts(redact=True):
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM gaash_accounts ORDER BY added_at")]
    for a in rows:
        a.pop("seen_ids_json", None)
        if redact:
            a["has_password"] = bool(a.pop("app_password", None))
    return rows


def _account(account_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM gaash_accounts WHERE id=?",
                      (account_id,)).fetchone()
    return dict(r) if r else None


def _mailbox_status(M):
    """(uidvalidity, uidnext) of INBOX, or (None, None)."""
    try:
        typ, data = M.status("INBOX", "(UIDVALIDITY UIDNEXT)")
        s = b" ".join(x for x in (data or []) if isinstance(x, bytes)).decode(errors="replace")
        uv = re.search(r"UIDVALIDITY (\d+)", s)
        un = re.search(r"UIDNEXT (\d+)", s)
        return (int(uv.group(1)) if uv else None,
                int(un.group(1)) if un else None)
    except Exception:  # noqa
        return None, None


def add_account(email_addr, app_password, label=None):
    """Verify SMTP+IMAP logins, then store the account. Never raises."""
    email_addr = (email_addr or "").strip().lower()
    pw = (app_password or "").replace(" ", "").strip()   # Gmail shows it in 4s
    if not email_addr or "@" not in email_addr:
        return {"ok": False, "error": "enter a valid email address"}
    if not pw:
        return {"ok": False, "error": "enter the app password"}
    with db.connect() as c:
        if c.execute("SELECT 1 FROM gaash_accounts WHERE email=?",
                     (email_addr,)).fetchone():
            return {"ok": False, "error": f"{email_addr} is already added"}
    try:
        with _smtp_connect(timeout=25) as s:
            s.login(email_addr, pw)
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "error": _AUTH_HELP}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"SMTP connection failed: {str(e)[:120]}"}
    uv = un = None
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        try:
            M.login(email_addr, pw)
            uv, un = _mailbox_status(M)
        finally:
            try:
                M.logout()
            except Exception:  # noqa
                pass
    except imaplib.IMAP4.error:
        return {"ok": False, "error": _AUTH_HELP +
                " (Also make sure IMAP is enabled in Gmail settings.)"}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"IMAP connection failed: {str(e)[:120]}"}
    aid = f"acct_{int(time.time() * 1000)}"
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_accounts
            (id,email,label,app_password,added_at,imap_uidvalidity,imap_last_uid,seen_ids_json)
            VALUES (?,?,?,?,?,?,?,?)""",
                  (aid, email_addr, (label or "").strip() or None, pw, now_iso(),
                   uv, max(0, (un or 1) - 1),   # start at newest: never ingest old mail
                   "[]"))
    return {"ok": True, "id": aid, "email": email_addr}


def remove_account(account_id):
    with db.connect() as c:
        cur = c.execute("DELETE FROM gaash_accounts WHERE id=?", (account_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# ID library (reusable ID documents — picked per sequence, attached to email 1)
# --------------------------------------------------------------------------- #
def _safe_name(name):
    name = os.path.basename(str(name or "").replace("\\", "/").strip())
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name)[:80].strip(" .")
    return name or "attachment"


def ids_list():
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM gaash_ids ORDER BY uploaded_at")]


def ids_add(name, filename, data):
    IDS_DIR.mkdir(parents=True, exist_ok=True)
    iid = f"id_{int(time.time() * 1000)}"
    fn = f"{iid}_{_safe_name(filename)}"
    (IDS_DIR / fn).write_bytes(data)
    with db.connect() as c:
        c.execute("INSERT INTO gaash_ids (id,name,filename,uploaded_at) VALUES (?,?,?,?)",
                  (iid, (name or "").strip() or _safe_name(filename), fn, now_iso()))
    return {"ok": True, "id": iid, "filename": fn}


def ids_remove(id_doc_id):
    with db.connect() as c:
        r = c.execute("SELECT filename FROM gaash_ids WHERE id=?",
                      (id_doc_id,)).fetchone()
        if not r:
            return False
        c.execute("DELETE FROM gaash_ids WHERE id=?", (id_doc_id,))
    try:
        (IDS_DIR / r["filename"]).unlink(missing_ok=True)
    except Exception:  # noqa
        pass
    return True


def id_file_path(fn):
    """Validated path of a library file, or None (same guard as customer IDs)."""
    if not fn or "/" in fn or ".." in fn:
        return None
    p = IDS_DIR / fn
    return p if p.is_file() else None


def _id_doc(id_doc_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM gaash_ids WHERE id=?",
                      (id_doc_id,)).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------------------- #
# Threads + messages (SQLite)
# --------------------------------------------------------------------------- #
def thread_get(gwd):
    with db.connect() as c:
        r = c.execute("SELECT * FROM gaash_threads WHERE gwd=?",
                      (gwd,)).fetchone()
    return dict(r) if r else None


def threads_all():
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM gaash_threads ORDER BY last_activity DESC")]


def msgs_for(gwd):
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM gaash_msgs WHERE gwd=? ORDER BY at, id", (gwd,))]


def _thread_set(gwd, **fields):
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as c:
        c.execute(f"UPDATE gaash_threads SET {keys} WHERE gwd=?",
                  (*fields.values(), gwd))


def _msg_add(gwd, rec):
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_msgs
            (gwd,dir,kind,step,at,from_addr,to_addr,subject,message_id,
             in_reply_to,body,attachments_json,imap_uid,notified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (gwd, rec.get("dir"), rec.get("kind"), rec.get("step"),
                   rec.get("at"), rec.get("from_addr"), rec.get("to_addr"),
                   rec.get("subject"), rec.get("message_id"),
                   rec.get("in_reply_to"), rec.get("body"),
                   json.dumps(rec.get("attachments") or []),
                   rec.get("imap_uid"), 1 if rec.get("notified") else 0))
    _thread_set(gwd, last_activity=rec.get("at") or now_iso())


def _store_attachment(gwd, orig_name, data):
    dirp = FILES_DIR / gwd
    dirp.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(orig_name)
    stored = f"{len(list(dirp.iterdir())) + 1:02d}_{safe}"
    if (dirp / stored).exists():
        stored = f"{int(time.time() * 1000)}_{safe}"
    (dirp / stored).write_bytes(data)
    return stored


def attachment_path(gwd, name):
    if not gwd or not name or "/" in name or ".." in name or "/" in gwd:
        return None
    try:
        p = (FILES_DIR / gwd / name).resolve()
        if p.is_relative_to(FILES_DIR.resolve()) and p.is_file():
            return p
    except Exception:  # noqa
        pass
    return None


# --------------------------------------------------------------------------- #
# Package context: status guard + template variables
# --------------------------------------------------------------------------- #
_TERMINAL = ("cleared", "delivered")


def _bucket(v):
    """Bucket string out of a mirror status value — dict on fresh rows, but a
    plain string on older ones (matched by substring for terminal words)."""
    if isinstance(v, dict):
        return str(v.get("bucket") or "")
    s = str(v or "").lower()
    for word in ("delivered", "cleared"):
        if word in s:
            return word
    return ""


def _leluxe_row_for(gwd):
    """The leluxe-mirror row (item/package) carrying this GWD, or None."""
    g = (gwd or "").strip().upper()
    if not g:
        return None
    with db.connect() as c:
        rows = c.execute(
            "SELECT id, parent_local_id, name, data_json FROM leluxe_orders "
            "WHERE deleted=0 AND data_json LIKE ?", (f"%{g}%",)).fetchall()
    for r in rows:
        try:
            d = json.loads(r["data_json"] or "{}")
        except Exception:  # noqa
            continue
        f = d.get("fields") or {}
        tn = str(d.get("tracking_number") or "").strip().upper()
        if not tn:
            for k, v in f.items():
                if k.strip().lower() == "tracking number":
                    tn = str(v or "").strip().upper()
                    break
        if tn == g:
            return {"id": r["id"], "parent_local_id": r["parent_local_id"],
                    "name": r["name"], "data": d}
    return None


def package_terminal(gwd):
    """True when the package is already cleared/delivered (sequence must stop).
    Prefers the leluxe mirror's stored buckets; falls back to a live lookup."""
    row = _leluxe_row_for(gwd)
    if row:
        d = row["data"]
        tb = _bucket(d.get("tracking_status"))
        if tb in _TERMINAL:
            return True
        if _bucket(d.get("gerizim_status")) == "delivered":
            return True
        if tb:                          # mirror knows the status and it's live
            return False
    try:
        import tracking
        st = tracking.track_with_fallback(gwd) or {}
        return str(st.get("bucket") or "") in _TERMINAL
    except Exception:  # noqa - unknown status must never block the sequence
        return False


def _customer_for(gwd):
    """Best-effort customer/order label for the {customer} placeholder."""
    row = _leluxe_row_for(gwd)
    if row and row.get("parent_local_id"):
        with db.connect() as c:
            p = c.execute("SELECT name FROM leluxe_orders WHERE id=?",
                          (row["parent_local_id"],)).fetchone()
        if p and p["name"]:
            return str(p["name"])
    if row and row.get("name"):
        return str(row["name"])
    return ""


def _fill(tpl, gwd, thread=None, step=None):
    id_name = ""
    if thread and thread.get("id_doc_id"):
        doc = _id_doc(thread["id_doc_id"])
        id_name = (doc or {}).get("name") or ""
    days = ""
    if thread and thread.get("created_at"):
        ts = _parse_iso(thread["created_at"])
        if ts:
            days = str(max(0, (datetime.now(timezone.utc) - ts).days))
    return (str(tpl or "")
            .replace("{gwd}", gwd)
            .replace("{customer}", _customer_for(gwd))
            .replace("{upload_link}",
                     f"https://ops.gaashwd.com/fileUpload?packageId={gwd}&type=6")
            .replace("{id_name}", id_name)
            .replace("{days_waiting}", days)
            .replace("{step}", str(step or (thread or {}).get("step") or "")))


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def _smtp_send(acct, msg):
    with _smtp_connect() as s:
        s.login(acct["email"], acct.get("app_password") or "")
        s.send_message(msg)


def _build_msg(acct, to_addr, subject, body, attachments, chain):
    msg = EmailMessage()
    msg["From"] = acct["email"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    mid = make_msgid()
    msg["Message-ID"] = mid
    if chain:
        msg["In-Reply-To"] = chain[-1]
        msg["References"] = " ".join(chain)
    msg.set_content(body or "(see attachment)")
    for name, data, ctype in (attachments or []):
        ctype = ctype if ctype and "/" in ctype else "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=_safe_name(name))
    return msg, mid


def _thread_send(gwd, body, attachments=None, kind="sent", step=None,
                 subject=None):
    """Send one message on a thread (threaded Re: after the first). Records the
    message only AFTER Gmail accepts it. Honors dry_run."""
    th = thread_get(gwd)
    if not th:
        return {"ok": False, "error": "thread not found"}
    setts = _setts()
    if setts.get("dry_run", True):
        _thread_set(gwd, last_error="dry-run is ON in Settings — nothing is sent")
        return {"ok": False, "error": "dry_run enabled — sends are disabled in Settings",
                "dry_run": True}
    acct = _account(th.get("account_id"))
    if not acct:
        return {"ok": False, "error": "sending account no longer exists — add one"}
    to_addr = (setts.get("to_address") or "").strip()
    if not to_addr:
        return {"ok": False, "error": "no GAASH to_address configured in Settings"}
    base = th.get("subject") or subject or f"Customs clearance — {gwd}"
    prior = [m for m in msgs_for(gwd) if m.get("message_id")]
    subj = base
    if prior and not re.match(r"(?i)re:", base):
        subj = f"Re: {base}"
    chain = [m["message_id"] for m in prior]
    msg, mid = _build_msg(acct, to_addr, subj, body, attachments, chain)
    try:
        _smtp_send(acct, msg)
    except smtplib.SMTPAuthenticationError:
        _thread_set(gwd, last_error=_AUTH_HELP)
        return {"ok": False, "error": _AUTH_HELP}
    except Exception as e:  # noqa
        err = f"send failed: {str(e)[:150]}"
        _thread_set(gwd, last_error=err)
        return {"ok": False, "error": err}
    atts = []
    for name, data, ctype in (attachments or []):
        stored = _store_attachment(gwd, name, data)
        atts.append({"name": _safe_name(name), "file": stored,
                     "size": len(data), "ctype": ctype})
    _msg_add(gwd, {"dir": "out", "kind": kind, "step": step, "at": now_iso(),
                   "from_addr": acct["email"], "to_addr": to_addr,
                   "subject": subj, "message_id": mid, "body": body or "",
                   "attachments": atts})
    _thread_set(gwd, subject=base, last_error=None)
    return {"ok": True, "message_id": mid}


def _step_attachments(th):
    """Attachments for the NEXT sequence email: the chosen ID doc on step 1 +
    any files the owner queued (e.g. a KMT), which are then consumed."""
    out = []
    if int(th.get("step") or 0) == 0 and th.get("id_doc_id"):
        doc = _id_doc(th["id_doc_id"])
        if doc:
            p = id_file_path(doc["filename"])
            if p:
                ext = p.suffix.lower().lstrip(".")
                ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                         "png": "image/png", "pdf": "application/pdf",
                         "webp": "image/webp"}.get(ext, "application/octet-stream")
                out.append((f"{doc['name']}{p.suffix}", p.read_bytes(), ctype))
    for a in json.loads(th.get("pending_files_json") or "[]"):
        p = attachment_path(th["gwd"], a.get("file"))
        if p:
            out.append((a.get("name") or p.name, p.read_bytes(),
                        a.get("ctype") or "application/octet-stream"))
    return out


def send_step(gwd):
    """Send the thread's next sequence email (guards already passed)."""
    th = thread_get(gwd)
    if not th:
        return {"ok": False, "error": "thread not found"}
    step = int(th.get("step") or 0)
    if step >= 4:
        return {"ok": False, "error": "all 4 emails already sent"}
    setts = _setts()
    steps = setts.get("steps") or []
    tpl = steps[step] if step < len(steps) else {}
    subject = _fill(tpl.get("subject_tpl") or f"Customs clearance — {gwd}",
                    gwd, th, step + 1)
    body = _fill(tpl.get("body_tpl") or "", gwd, th, step + 1)
    if step == 0:
        _thread_set(gwd, subject=subject)          # the base subject of the thread
        th["subject"] = subject
    res = _thread_send(gwd, body, _step_attachments(th), kind="sent",
                       step=step + 1, subject=subject)
    if not res.get("ok"):
        return res
    cadence = setts.get("cadence_days") or [2, 2, 2]
    new_step = step + 1
    fields = {"step": new_step, "pending_files_json": "[]"}
    if new_step >= 4:
        fields["state"] = "exhausted"
        fields["next_send_at"] = None
    else:
        delay = cadence[min(new_step - 1, len(cadence) - 1)]
        due = datetime.now(timezone.utc) + timedelta(days=float(delay))
        fields["next_send_at"] = due.isoformat(timespec="seconds")
    _thread_set(gwd, **fields)
    return {"ok": True, "step": new_step, **res}


def start_threads(gwds, id_doc_id, account_id):
    """Create one thread per GWD (one GWD per email — replies map 1:1) and try
    to send email 1 immediately. Returns per-GWD results."""
    out = []
    for raw in gwds or []:
        gwd = str(raw or "").strip().upper()
        if not re.match(r"GWD\d+$", gwd):
            out.append({"gwd": raw, "ok": False, "error": "not a GWD number"})
            continue
        if thread_get(gwd):
            out.append({"gwd": gwd, "ok": False, "error": "thread already exists"})
            continue
        with db.connect() as c:
            # next_send_at seeds to NOW so the sequencer retries email 1 even if
            # the immediate send below is blocked (dry-run / no account yet)
            c.execute("""INSERT INTO gaash_threads
                (gwd,account_id,state,step,id_doc_id,unread,missing_docs,
                 pending_files_json,next_send_at,created_at,last_activity)
                VALUES (?,?, 'active',0,?,0,0,'[]',?,?,?)""",
                      (gwd, account_id, id_doc_id or None,
                       datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       now_iso(), now_iso()))
        if package_terminal(gwd):
            _thread_set(gwd, state="cleared")
            out.append({"gwd": gwd, "ok": True, "state": "cleared",
                        "note": "already cleared/delivered — no email needed"})
            continue
        res = send_step(gwd)
        out.append({"gwd": gwd, **({"ok": True, "state": "active"} if res.get("ok")
                                   else {"ok": True, "state": "active",
                                         "send_error": res.get("error"),
                                         "dry_run": res.get("dry_run", False)})})
    return out


def send_manual(gwd, body, files=None):
    """A hand-written message on the thread (files=[(name,bytes,ctype)])."""
    if not (body or "").strip() and not files:
        return {"ok": False, "error": "write a message (or attach a file)"}
    res = _thread_send(gwd, (body or "").strip(), files or [], kind="sent")
    if res.get("ok"):
        _thread_set(gwd, state="active")   # a manual send resumes the thread
    return res


# --------------------------------------------------------------------------- #
# Reading replies (IMAP) — ported classification + matching
# --------------------------------------------------------------------------- #
class _HTMLText(HTMLParser):
    _SKIP = {"style", "script", "head", "title"}
    _BREAK = {"p", "br", "div", "tr", "li", "table"}

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _strip_html(html_text):
    p = _HTMLText()
    try:
        p.feed(html_text)
    except Exception:  # noqa
        pass
    return "".join(p.parts)


def _clean_ws(text):
    text = re.sub(r"[ \t]+\n", "\n", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_text(msg):
    """Plain-text body; Glassix mail is often HTML-only → strip tags."""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            return _clean_ws(part.get_content())
    except Exception:  # noqa
        pass
    try:
        part = msg.get_body(preferencelist=("html",))
        if part is not None:
            return _clean_ws(_strip_html(part.get_content()))
    except Exception:  # noqa
        pass
    return ""


def _msg_datetime(msg):
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa
        return datetime.now(timezone.utc)


def classify_incoming(gwd, received_dt, ack_window_min):
    """auto_ack when the reply landed within the window of our latest earlier
    outgoing message; otherwise a real reply. (The owner's Glassix rule.)"""
    last_out = None
    for m in msgs_for(gwd):
        if m.get("dir") != "out":
            continue
        ts = _parse_iso(m.get("at") or "")
        if ts and ts <= received_dt and (last_out is None or ts > last_out):
            last_out = ts
    if last_out is None:
        return "reply"
    return ("auto_ack"
            if received_dt - last_out <= timedelta(minutes=ack_window_min)
            else "reply")


def match_thread(hdr_msg, to_domain, our_mids, thread_gwds):
    """GWD of the thread an incoming message belongs to, or None."""
    mid = (str(hdr_msg.get("Message-ID") or "")).strip()
    if mid and mid in our_mids:                 # our own message echoed back
        return None
    refs = set()
    for h in ("In-Reply-To", "References"):
        refs.update(re.findall(r"<[^<>]+>", str(hdr_msg.get(h) or "")))
    if refs:
        with db.connect() as c:
            for ref in refs:
                r = c.execute("SELECT gwd FROM gaash_msgs WHERE message_id=?",
                              (ref,)).fetchone()
                if r:
                    return r["gwd"]
    # fallback: sender is the support domain + a GWD token in the subject
    frm = parseaddr(str(hdr_msg.get("From") or ""))[1].lower()
    if to_domain and to_domain in frm:
        gwds = {g.upper() for g in re.findall(
            r"GWD\d+", str(hdr_msg.get("Subject") or ""), re.I)}
        hit = gwds & set(thread_gwds)
        if hit:
            return sorted(hit)[0]
    return None


def _matches_closed(setts, *texts):
    phrases = [str(p).lower() for p in (setts.get("resend_phrases") or []) if p]
    blob = " ".join(t or "" for t in texts).lower()
    return any(p in blob for p in phrases)


def _matches_missing(setts, *texts):
    kws = [str(k).lower() for k in (setts.get("missing_doc_keywords") or []) if k]
    blob = " ".join(t or "" for t in texts).lower()
    return next((k for k in kws if k in blob), None)


def _next_resend_due(hour, now=None):
    """Next occurrence of hour:00 in Israel local time (today if still ahead)."""
    tz = ZoneInfo(IL_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    due = now.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if now >= due:
        due += timedelta(days=1)
    return due


def _save_incoming_attachments(gwd, msg):
    out = []
    try:
        for part in msg.iter_attachments():
            try:
                data = part.get_payload(decode=True)
                if not data:
                    continue
                name = _safe_name(part.get_filename() or "attachment")
                stored = _store_attachment(gwd, name, data)
                out.append({"name": name, "file": stored, "size": len(data),
                            "ctype": part.get_content_type()})
            except Exception:  # noqa - one bad part shouldn't kill the message
                continue
    except Exception:  # noqa
        pass
    return out


def _fetch_bytes(M, uid, spec):
    typ, data = M.uid("FETCH", str(uid), spec)
    if typ != "OK":
        return None
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and \
                isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _check_account(acct, setts, our_mids, thread_gwds):
    """Poll one Gmail INBOX. Returns (new_records, patch). Raises on conn errors."""
    to_domain = (setts.get("to_address") or "").split("@")[-1].lower()
    seen = set(json.loads(acct.get("seen_ids_json") or "[]"))
    last = int(acct.get("imap_last_uid") or 0)
    new_records = []

    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(acct["email"], acct.get("app_password") or "")
        uv, un = _mailbox_status(M)
        if uv and acct.get("imap_uidvalidity") and uv != acct["imap_uidvalidity"]:
            last = max(0, (un or 1) - 1)    # mailbox renumbered — resume at the end
        M.select("INBOX", readonly=True)
        typ, data = M.uid("search", None, "UID", f"{last + 1}:*")
        uids = []
        if typ == "OK" and data and data[0]:
            # IMAP quirk: "n:*" past the end still returns the last UID
            uids = sorted(u for u in (int(x) for x in data[0].split()) if u > last)
        max_uid = last
        for uid in uids:
            max_uid = max(max_uid, uid)
            hdr = _fetch_bytes(M, uid, "(BODY.PEEK[HEADER])")
            if not hdr:
                continue
            hm = message_from_bytes(hdr, policy=policy.default)
            mid = (str(hm.get("Message-ID") or "")).strip() or \
                f"<uid-{uv}-{uid}@{acct['email']}>"
            if mid in seen:
                continue
            seen.add(mid)
            gwd = match_thread(hm, to_domain, our_mids, thread_gwds)
            if not gwd:
                continue
            raw = _fetch_bytes(M, uid, "(BODY.PEEK[])")
            if not raw:
                continue
            msg = message_from_bytes(raw, policy=policy.default)
            received = _msg_datetime(msg)
            body_text = _extract_text(msg)
            subj = str(msg.get("Subject") or "")
            kind = classify_incoming(gwd, received,
                                     int(setts.get("ack_window_min") or 0))
            if _matches_closed(setts, body_text, subj):
                kind = "closed"     # office-closed bounce: mail was NOT received
            new_records.append((gwd, {
                "dir": "in", "kind": kind,
                "at": received.astimezone().isoformat(timespec="seconds"),
                "from_addr": str(msg.get("From") or ""),
                "subject": subj, "message_id": mid,
                "in_reply_to": (str(msg.get("In-Reply-To") or "")).strip() or None,
                "body": body_text,
                "attachments": _save_incoming_attachments(gwd, msg),
                "imap_uid": uid,
                "notified": kind != "reply",   # only real replies get notified
            }))
    finally:
        try:
            M.logout()
        except Exception:  # noqa
            pass

    patch = {"imap_last_uid": max_uid if uids else last,
             "imap_uidvalidity": uv or acct.get("imap_uidvalidity"),
             "last_check": now_iso(), "last_error": None,
             "seen_ids_json": json.dumps(list(seen)[-MAX_SEEN_IDS:])}
    return new_records, patch


def check_replies():
    """Poll every account's inbox. Never raises."""
    with db.connect() as c:
        accts = [dict(r) for r in c.execute("SELECT * FROM gaash_accounts")]
        thread_gwds = [r["gwd"] for r in c.execute("SELECT gwd FROM gaash_threads")]
        our_mids = {r["message_id"] for r in c.execute(
            "SELECT message_id FROM gaash_msgs WHERE dir='out' "
            "AND message_id IS NOT NULL")}
    setts = _setts()
    n_reply = n_ack = 0
    errors = {}
    for a in accts:
        try:
            recs, patch = _check_account(a, setts, our_mids, thread_gwds)
        except Exception as e:  # noqa - one bad account must not stop the rest
            msg = str(e)[:150]
            if "AUTHENTICATIONFAILED" in msg.upper():
                msg = _AUTH_HELP
            errors[a.get("email") or a["id"]] = msg
            with db.connect() as c:
                c.execute("UPDATE gaash_accounts SET last_check=?, last_error=? "
                          "WHERE id=?", (now_iso(), msg, a["id"]))
            continue
        with db.connect() as c:
            c.execute("UPDATE gaash_accounts SET imap_last_uid=?, "
                      "imap_uidvalidity=?, last_check=?, last_error=NULL, "
                      "seen_ids_json=? WHERE id=?",
                      (patch["imap_last_uid"], patch["imap_uidvalidity"],
                       patch["last_check"], patch["seen_ids_json"], a["id"]))
        for gwd, rec in recs:
            with db.connect() as c:
                dup = c.execute("SELECT 1 FROM gaash_msgs WHERE gwd=? AND "
                                "message_id=?", (gwd, rec["message_id"])).fetchone()
            if dup:
                continue
            _msg_add(gwd, rec)
            th = thread_get(gwd) or {}
            upd = {"unread": int(th.get("unread") or 0) + 1}
            if rec["kind"] == "reply":
                n_reply += 1
                upd["state"] = "waiting_reply"
                kw = _matches_missing(setts, rec.get("body"), rec.get("subject"))
                if kw:
                    upd["missing_docs"] = 1
                    upd["missing_note"] = f"GAASH mentioned: {kw}"
                    upd["state"] = "missing_docs"
            elif rec["kind"] == "closed":
                n_ack += 1
                # office closed ⇒ the email was NOT received; resend at resend_hour
                if (setts.get("auto_resend", True)
                        and not (th.get("resend_json") or "").strip()):
                    due = _next_resend_due(setts.get("resend_hour") or 9)
                    upd["resend_json"] = json.dumps(
                        {"due_at": due.isoformat(timespec="seconds"),
                         "scheduled_at": now_iso(), "reason": "office closed"})
            else:
                n_ack += 1
            _thread_set(gwd, **upd)
    _notify_pending()
    resent = process_resends()
    return {"ok": True, "new": n_reply, "acks": n_ack, "resent": resent,
            "errors": errors}


# --------------------------------------------------------------------------- #
# Office-closed resend
# --------------------------------------------------------------------------- #
def process_resends():
    """Send due office-closed retries: repeat the last outgoing message."""
    now = datetime.now(timezone.utc)
    due = []
    for th in threads_all():
        r = json.loads(th.get("resend_json") or "null")
        if not r:
            continue
        dt = _parse_iso(r.get("due_at") or "")
        if dt and dt <= now:
            due.append((th["gwd"], r))
            _thread_set(th["gwd"], resend_json=None)     # claim
    sent = 0
    for gwd, marker in due:
        last_out = None
        for m in msgs_for(gwd):
            if m.get("dir") == "out":
                last_out = m
        if not last_out:
            continue
        atts = []
        for a in json.loads(last_out.get("attachments_json") or "[]"):
            p = attachment_path(gwd, a.get("file"))
            if p:
                atts.append((a.get("name") or p.name, p.read_bytes(),
                             a.get("ctype") or "application/octet-stream"))
        res = _thread_send(gwd, last_out.get("body") or "", atts, kind="resent",
                           step=last_out.get("step"))
        if res.get("ok"):
            sent += 1
            _send_telegram(f"⏰ <b>Resent to GAASH</b> (office-closed retry) — "
                           f"<code>{_tg_esc(gwd)}</code>")
        else:
            _thread_set(gwd, resend_json=json.dumps(marker))   # retry next cycle
    return sent


# --------------------------------------------------------------------------- #
# Telegram (real replies only — same creds as the alerts daemon)
# --------------------------------------------------------------------------- #
def _tg_esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML",
                             "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:  # noqa
        return False


def _notify_pending():
    """Telegram-ping every un-notified real reply; failed sends retry next poll."""
    with db.connect() as c:
        pend = [dict(r) for r in c.execute(
            "SELECT id, gwd, subject, body FROM gaash_msgs "
            "WHERE dir='in' AND kind='reply' AND notified=0")]
    sent = 0
    for m in pend:
        excerpt = _tg_esc((m.get("body") or "")[:300]).strip()
        lines = [f"📬 <b>GAASH replied</b> — <code>{_tg_esc(m['gwd'])}</code>",
                 f"✉️ {_tg_esc(m.get('subject') or '')}"]
        if excerpt:
            lines.append(f"“{excerpt}”")
        if _send_telegram("\n".join(lines)):
            with db.connect() as c:
                c.execute("UPDATE gaash_msgs SET notified=1 WHERE id=?", (m["id"],))
            sent += 1
    return sent


# --------------------------------------------------------------------------- #
# The sequencer tick
# --------------------------------------------------------------------------- #
def _sent_today():
    day = datetime.now(timezone.utc).astimezone().date().isoformat()
    with db.connect() as c:
        r = c.execute("SELECT COUNT(*) n FROM gaash_msgs WHERE dir='out' AND "
                      "at LIKE ?", (f"{day}%",)).fetchone()
    return int(r["n"] or 0)


def run_once():
    """One sequencer pass: advance due threads (guards in order), then poll
    inboxes. Safe to call from any worker — sends dedupe via claim_once."""
    setts = _setts()
    sent = 0
    cap = int(setts.get("daily_send_cap") or 40)
    now = datetime.now(timezone.utc)
    for th in threads_all():
        if th.get("state") != "active" or int(th.get("step") or 0) >= 4:
            continue
        due = _parse_iso(th.get("next_send_at") or "")
        if due is None or due > now:
            continue
        gwd = th["gwd"]
        # guard 1: already cleared/delivered → stop the sequence
        if package_terminal(gwd):
            _thread_set(gwd, state="cleared")
            continue
        # guard 2: an unhandled real reply → pause (belt & braces; the poll
        # already flips the state, but a manual DB edit must not slip through)
        if any(m.get("dir") == "in" and m.get("kind") == "reply"
               for m in msgs_for(gwd)) and th.get("state") == "active" \
                and int(th.get("unread") or 0) > 0:
            _thread_set(gwd, state="waiting_reply")
            continue
        # guard 3: missing docs is a separate paused state (never "active")
        # guard 4: dry-run — don't even claim; the step must fire once it's off
        if setts.get("dry_run", True):
            _thread_set(gwd, last_error="dry-run is ON in Settings — nothing is sent")
            continue
        # guard 5: daily cap
        if _sent_today() + 1 > cap or sent + 1 > cap:
            break
        # guard 6: exactly-once across gunicorn workers; a FAILED send releases
        # the claim so the next pass retries (otherwise the step is lost forever)
        key = f"gaashmail:{gwd}:step{int(th.get('step') or 0) + 1}"
        if not db.claim_once(key):
            continue
        res = send_step(gwd)
        if res.get("ok"):
            sent += 1
        else:
            with db.connect() as c:
                c.execute("DELETE FROM settings WHERE key=?", (key,))
    poll = check_replies() if accounts(redact=True) else {}
    return {"sent": sent, **({k: poll[k] for k in ("new", "acks", "resent")
                              if k in poll} if poll else {})}


# --------------------------------------------------------------------------- #
# Page data
# --------------------------------------------------------------------------- #
def overview():
    threads = threads_all()
    by_gwd = {}
    with db.connect() as c:
        for r in c.execute("SELECT gwd, MAX(id) mid FROM gaash_msgs GROUP BY gwd"):
            by_gwd[r["gwd"]] = r["mid"]
        last = {}
        if by_gwd:
            q = ",".join("?" * len(by_gwd))
            for r in c.execute(
                    f"SELECT id, gwd, dir, kind, at, body FROM gaash_msgs "
                    f"WHERE id IN ({q})", list(by_gwd.values())):
                last[r["gwd"]] = {"dir": r["dir"], "kind": r["kind"],
                                  "at": r["at"], "body": (r["body"] or "")[:140]}
    for th in threads:
        th["last_msg"] = last.get(th["gwd"])
        th.pop("pending_files_json", None)
    return {"threads": threads, "accounts": accounts(redact=True),
            "ids": ids_list(), "settings": _setts(),
            "mailer_env": bool(os.environ.get("GAASH_MAILER"))}


def candidates():
    """Non-terminal GWDs from the leluxe mirror without a thread yet."""
    have = {t["gwd"] for t in threads_all()}
    out, seen = [], set()
    with db.connect() as c:
        rows = c.execute("SELECT id, parent_local_id, name, kind, status, "
                         "data_json FROM leluxe_orders WHERE deleted=0").fetchall()
    names = {r["id"]: r["name"] for r in rows}
    for r in rows:
        try:
            d = json.loads(r["data_json"] or "{}")
        except Exception:  # noqa
            continue
        f = d.get("fields") or {}
        tn = str(d.get("tracking_number") or "").strip().upper()
        if not tn:
            for k, v in f.items():
                if k.strip().lower() == "tracking number":
                    tn = str(v or "").strip().upper()
                    break
        if not re.match(r"GWD\d+$", tn) or tn in seen or tn in have:
            continue
        seen.add(tn)
        ts = d.get("tracking_status")
        if _bucket(ts) in _TERMINAL or \
                _bucket(d.get("gerizim_status")) == "delivered":
            continue
        gash = next((v for k, v in f.items()
                     if k.strip().lower() == "gash status"), None)
        # the owner's ClickUp GASH STATUS field is authoritative on old rows
        # (support.py rule): anything DELIVERED needs no clearance chasing
        if re.search(r"DELIVERED", str(gash or ""), re.I):
            continue
        out.append({"gwd": tn,
                    "name": (names.get(r["parent_local_id"]) or r["name"] or "")[:70],
                    "status": r["status"], "gash_status": gash,
                    "bucket": _bucket(ts) or None,
                    "label": (ts.get("label") if isinstance(ts, dict)
                              else (str(ts)[:40] if ts else None))})
    out.sort(key=lambda x: x["gwd"])
    return out


def thread_detail(gwd):
    th = thread_get(gwd)
    if not th:
        return None
    msgs = msgs_for(gwd)
    for m in msgs:
        m["attachments"] = json.loads(m.pop("attachments_json") or "[]")
    return {"thread": th, "messages": msgs}


# --------------------------------------------------------------------------- #
# Daemon
# --------------------------------------------------------------------------- #
_started = False


def _interval_seconds():
    try:
        return max(60, int(_setts().get("poll_interval_min") or 5) * 60)
    except Exception:  # noqa
        return 300


def _loop():
    time.sleep(60)                        # let the app finish booting first
    while True:
        try:
            out = run_once()
            if out.get("sent") or out.get("new"):
                print(f"gaash_mail: sent {out.get('sent', 0)}, "
                      f"replies {out.get('new', 0)}")
        except Exception as e:  # noqa - never let the thread die
            print(f"gaash_mail: pass failed ({e})")
        time.sleep(_interval_seconds())


def start():
    """Start the sequencer+poller ONLY where env GAASH_MAILER=1 (Render — the
    live DB). The Mac's always-on local app must never run this: its stale DB
    copy would double-send. NEVER put GAASH_MAILER in the plist or local .env."""
    global _started
    if _started or not os.environ.get("GAASH_MAILER"):
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-gaash-mail", daemon=True).start()
