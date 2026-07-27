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

import hashlib
import hmac
import html as html_mod
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


# reusable IDs · per-package papers · standing certificates (a dealer import
# certification is signed once and attached to whichever packages it covers)
ID_FOLDERS = ("id", "declaration", "certificate")


def _folder(v):
    v = str(v or "").strip().lower()
    return v if v in ID_FOLDERS else "id"


def ids_add(name, filename, data, folder=None):
    IDS_DIR.mkdir(parents=True, exist_ok=True)
    iid = f"id_{int(time.time() * 1000)}"
    fn = f"{iid}_{_safe_name(filename)}"
    (IDS_DIR / fn).write_bytes(data)
    with db.connect() as c:
        c.execute("INSERT INTO gaash_ids (id,name,filename,uploaded_at,folder) "
                  "VALUES (?,?,?,?,?)",
                  (iid, (name or "").strip() or _safe_name(filename), fn,
                   now_iso(), _folder(folder)))
    return {"ok": True, "id": iid, "filename": fn}


def declaration_make(gwd):
    """Generate this package's customs declaration and file it in the library's
    📄 Declarations folder, replacing any previous one for the same GWD — a
    parcel has ONE current declaration, and keeping supersededates invites
    attaching the wrong one.

    Everything on it is read here, not typed by hand: the name the parcel ships
    under, the ID that name resolves to, and the package's real contents."""
    import declaration
    g = (gwd or "").strip().upper()
    if not re.match(r"GWD\d+$", g):
        return {"ok": False, "error": "not a tracking number"}
    try:
        fn, data = declaration.build(
            gwd=g, name=parcel_name(g), id_number=id_number_for_email(g),
            contents=package_contents(g),
            purpose=(_setts().get("declaration_purpose") or "").strip() or None)
    except ValueError as e:                     # a field that cannot be printed
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"could not build it: {str(e)[:120]}"}
    name = f"{g} - declaration"
    with db.connect() as c:
        old = [dict(r) for r in c.execute(
            "SELECT id, filename FROM gaash_ids WHERE name=?", (name,))]
    for o in old:
        ids_remove(o["id"])
    res = ids_add(name, fn, data, folder="declaration")
    return {**res, "name": name, "gwd": g}


def ids_move(id_doc_id, folder):
    """Re-file a document. The one-time backfill guessed from the name, so this
    is how a wrong guess gets corrected."""
    with db.connect() as c:
        cur = c.execute("UPDATE gaash_ids SET folder=? WHERE id=?",
                        (_folder(folder), id_doc_id))
        return {"ok": cur.rowcount > 0, "folder": _folder(folder)}


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
# v2: sequences-as-data (HubSpot-style builder) — templates / sequences /
# steps / rules CRUD, send-window math, open+click tracking, stats.
# --------------------------------------------------------------------------- #
# Default window: Sun–Thu 09:00–17:00 Palestine time (GAASH's work week).
# Fully editable per sequence — days/hours/timezone — so a sequence can target
# any platform, not just GAASH. (python weekdays: Mon=0 … Sun=6)
DEFAULT_WINDOW = {"tz": "Asia/Hebron", "days": [6, 0, 1, 2, 3],
                  "start": "09:00", "end": "17:00"}


def _win(seq):
    w = {}
    try:
        w = json.loads((seq or {}).get("send_window_json") or "{}")
    except Exception:  # noqa
        pass
    out = {**DEFAULT_WINDOW, **{k: v for k, v in w.items() if v}}
    if not isinstance(out.get("days"), list) or not out["days"]:
        out["days"] = DEFAULT_WINDOW["days"]
    return out


def _hm(s, fallback):
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(s or ""))
    return (int(m.group(1)), int(m.group(2))) if m else fallback


def next_allowed(dt, win):
    """The earliest moment ≥ dt inside the window (its tz)."""
    try:
        tz = ZoneInfo(win.get("tz") or "Asia/Hebron")
    except Exception:  # noqa
        tz = ZoneInfo("Asia/Hebron")
    days = set(win.get("days") or DEFAULT_WINDOW["days"])
    sh, sm = _hm(win.get("start"), (9, 0))
    eh, em = _hm(win.get("end"), (17, 0))
    d = dt.astimezone(tz)
    for _ in range(15):                       # ≤ 2 weeks scan — always terminates
        start = d.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = d.replace(hour=eh, minute=em, second=0, microsecond=0)
        if d.weekday() in days:
            if d < start:
                return start
            if d <= end:
                return d
        d = (d + timedelta(days=1)).replace(hour=sh, minute=sm, second=0,
                                            microsecond=0)
    return d


def add_business_days(dt, n, win):
    """dt + n BUSINESS days (only the window's allowed weekdays count), then
    clamped into the window. n may be fractional (0.5 = half a day of clock)."""
    try:
        tz = ZoneInfo(win.get("tz") or "Asia/Hebron")
    except Exception:  # noqa
        tz = ZoneInfo("Asia/Hebron")
    days = set(win.get("days") or DEFAULT_WINDOW["days"])
    d = dt.astimezone(tz)
    whole = int(n or 0)
    for _ in range(whole * 7 + 14):
        if whole <= 0:
            break
        d += timedelta(days=1)
        if d.weekday() in days:
            whole -= 1
    frac = float(n or 0) - int(n or 0)
    if frac > 0:
        d += timedelta(days=frac)
    return next_allowed(d, win)


# ── CRUD: templates ──
def templates_list():
    with db.connect() as c:
        tpls = [dict(r) for r in c.execute(
            "SELECT * FROM gaash_templates ORDER BY name")]
        used = {r["template_id"]: r["n"] for r in c.execute(
            "SELECT template_id, COUNT(*) n FROM gaash_steps GROUP BY template_id")}
    for t in tpls:
        t["used_by"] = used.get(t["id"], 0)
    return tpls


def template_save(t, user=None):
    tid = (t.get("id") or "").strip() or f"tpl_{int(time.time() * 1000)}"
    name = str(t.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "template name required"}
    with db.connect() as c:
        # created_by is written on INSERT only — editing someone else's template
        # does not make it yours, and the picker's Created-by column would lie
        c.execute("""INSERT INTO gaash_templates
            (id,name,subject_tpl,body_tpl,updated_at,created_by)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,
              subject_tpl=excluded.subject_tpl, body_tpl=excluded.body_tpl,
              updated_at=excluded.updated_at""",
                  (tid, name, str(t.get("subject_tpl") or ""),
                   str(t.get("body_tpl") or ""), now_iso(),
                   (user or "").strip() or None))
    return {"ok": True, "id": tid}


def template_touch(tid):
    """Stamp 'last used'. A use is the template actually going into an email —
    the sequencer sending it, or a human picking it into the reply box. Merely
    opening the picker is not a use."""
    if not tid:
        return
    with db.connect() as c:
        c.execute("UPDATE gaash_templates SET last_used_at=? WHERE id=?",
                  (now_iso(), tid))


def template_remove(tid):
    with db.connect() as c:
        if c.execute("SELECT 1 FROM gaash_steps WHERE template_id=?",
                     (tid,)).fetchone():
            return {"ok": False, "error": "template is used by a sequence step"}
        c.execute("DELETE FROM gaash_templates WHERE id=?", (tid,))
    return {"ok": True}


def _template(tid):
    with db.connect() as c:
        r = c.execute("SELECT * FROM gaash_templates WHERE id=?",
                      (tid,)).fetchone()
    return dict(r) if r else None


# ── CRUD: sequences + steps ──
def seq_steps(seq_id):
    with db.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM gaash_steps WHERE seq_id=? ORDER BY pos", (seq_id,))]


def sequence_get(seq_id):
    if not seq_id:
        return None
    with db.connect() as c:
        r = c.execute("SELECT * FROM gaash_sequences WHERE id=?",
                      (seq_id,)).fetchone()
    if not r:
        return None
    seq = dict(r)
    seq["steps"] = seq_steps(seq_id)
    return seq


def sequences_list(include_archived=False):
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM gaash_sequences" +
            ("" if include_archived else " WHERE archived=0") +
            " ORDER BY created_at")]
        active = {r["seq_id"]: r["n"] for r in c.execute(
            "SELECT seq_id, COUNT(*) n FROM gaash_threads WHERE state IN "
            "('active','waiting_reply','missing_docs','paused','waiting_task') "
            "GROUP BY seq_id")}
    for s in rows:
        s["steps"] = seq_steps(s["id"])
        s["active_threads"] = active.get(s["id"], 0)
    return rows


def sequence_save(s):
    """Save a sequence + its full ordered steps array (replace-all)."""
    sid = (s.get("id") or "").strip() or f"seq_{int(time.time() * 1000)}"
    name = str(s.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "sequence name required"}
    goal = s.get("goal") if s.get("goal") in ("cleared", "reply", "manual") \
        else "cleared"
    win = s.get("send_window") if isinstance(s.get("send_window"), dict) else {}
    win = {**DEFAULT_WINDOW, **{k: win[k] for k in
                                ("tz", "days", "start", "end") if win.get(k)}}
    steps_in = s.get("steps") if isinstance(s.get("steps"), list) else []
    steps = []
    for i, st in enumerate(steps_in[:20]):
        if not isinstance(st, dict):
            continue
        kind = st.get("kind") if st.get("kind") in ("auto_email", "task") \
            else "auto_email"
        if kind == "auto_email" and not _template(st.get("template_id") or ""):
            return {"ok": False,
                    "error": f"step {i + 1}: pick a template from the library"}
        try:
            delay = min(60.0, max(0.0, float(st.get("delay_days") or 0)))
        except (TypeError, ValueError):
            delay = 0.0
        steps.append({"id": (st.get("id") or "").strip()
                      or f"stp_{int(time.time() * 1000)}{i}",
                      "kind": kind,
                      "template_id": st.get("template_id") if kind == "auto_email" else None,
                      "task_note": str(st.get("task_note") or "").strip() or None,
                      "delay_days": delay})
    if not steps:
        return {"ok": False, "error": "a sequence needs at least one step"}
    with db.connect() as c:
        # NOTE: paused is deliberately untouched here — the On/Off switch owns it
        c.execute("""INSERT INTO gaash_sequences
            (id,name,to_address,goal,send_window_json,description,
             created_at,updated_at,archived)
            VALUES (?,?,?,?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,
              to_address=excluded.to_address, goal=excluded.goal,
              send_window_json=excluded.send_window_json,
              description=excluded.description,
              updated_at=excluded.updated_at, archived=0""",
                  (sid, name, str(s.get("to_address") or "").strip() or None,
                   goal, json.dumps(win),
                   str(s.get("description") or "").strip()[:300] or None,
                   now_iso(), now_iso()))
        c.execute("DELETE FROM gaash_steps WHERE seq_id=?", (sid,))
        for i, st in enumerate(steps):
            c.execute("""INSERT INTO gaash_steps
                (id,seq_id,pos,kind,template_id,task_note,delay_days)
                VALUES (?,?,?,?,?,?,?)""",
                      (st["id"], sid, i, st["kind"], st["template_id"],
                       st["task_note"], st["delay_days"]))
    return {"ok": True, "id": sid}


def sequence_archive(seq_id):
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM gaash_threads WHERE seq_id=? AND "
                      "state IN ('active','waiting_reply','missing_docs',"
                      "'paused','waiting_task','proposed')",
                      (seq_id,)).fetchone()["n"]
        if n:
            return {"ok": False,
                    "error": f"{n} open conversation(s) still use this sequence"}
        c.execute("UPDATE gaash_sequences SET archived=1, updated_at=? WHERE id=?",
                  (now_iso(), seq_id))
    return {"ok": True}


def sequence_toggle(seq_id, paused):
    """HubSpot-style On/Off. OFF (paused=1) = the sequencer skips this workflow's
    threads (they stay due, nothing is lost) AND its triggers stop enrolling."""
    if not sequence_get(seq_id):
        return {"ok": False, "error": "workflow not found"}
    with db.connect() as c:
        c.execute("UPDATE gaash_sequences SET paused=?, updated_at=? WHERE id=?",
                  (1 if paused else 0, now_iso(), seq_id))
    return {"ok": True, "paused": 1 if paused else 0}


def freeze_all(on):
    """⏸ the one big switch for a panicking owner: ALL workflows Off — the
    sequencer skips every thread and every trigger stops enrolling. Remembers
    which workflows were On (settings key gaash_freeze_prev) so ▶ resume
    restores exactly that set, not everything."""
    with db.connect() as c:
        if on:
            prev = [r["id"] for r in c.execute(
                "SELECT id FROM gaash_sequences WHERE archived=0 AND paused=0")]
            c.execute("INSERT INTO settings(key,value) VALUES('gaash_freeze_prev',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (json.dumps(prev),))
            c.execute("UPDATE gaash_sequences SET paused=1, updated_at=? "
                      "WHERE archived=0", (now_iso(),))
        else:
            r = c.execute("SELECT value FROM settings "
                          "WHERE key='gaash_freeze_prev'").fetchone()
            try:
                prev = json.loads(r["value"]) if r and r["value"] else []
            except Exception:  # noqa
                prev = []
            if prev:
                q = ",".join("?" * len(prev))
                c.execute(f"UPDATE gaash_sequences SET paused=0, updated_at=? "
                          f"WHERE id IN ({q})", (now_iso(), *prev))
            else:
                # no snapshot (workflows were paused one-by-one, not via freeze)
                # — the owner pressed ▶ resume ALL, so that's what happens
                c.execute("UPDATE gaash_sequences SET paused=0, updated_at=? "
                          "WHERE archived=0", (now_iso(),))
            c.execute("DELETE FROM settings WHERE key='gaash_freeze_prev'")
    return {"ok": True, "frozen": bool(on)}


def sequence_clone(seq_id):
    """Duplicate a workflow + its steps. The copy starts PAUSED (HubSpot clones
    start Off) so it can be tweaked before anything sends."""
    src = sequence_get(seq_id)
    if not src:
        return {"ok": False, "error": "workflow not found"}
    nid = f"seq_{int(time.time() * 1000)}"
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_sequences
            (id,name,to_address,goal,send_window_json,description,
             created_at,updated_at,archived,paused)
            VALUES (?,?,?,?,?,?,?,?,0,1)""",
                  (nid, f"نسخة من · Copy of {src['name']}"[:120],
                   src.get("to_address"), src.get("goal") or "cleared",
                   src.get("send_window_json"), src.get("description"),
                   now_iso(), now_iso()))
        for i, st in enumerate(src.get("steps") or []):
            c.execute("""INSERT INTO gaash_steps
                (id,seq_id,pos,kind,template_id,task_note,delay_days)
                VALUES (?,?,?,?,?,?,?)""",
                      (f"stp_{int(time.time() * 1000)}{i}", nid, i,
                       st["kind"], st.get("template_id"), st.get("task_note"),
                       st.get("delay_days") or 0))
    return {"ok": True, "id": nid}


def _paused_seq_ids():
    with db.connect() as c:
        return {r["id"] for r in c.execute(
            "SELECT id FROM gaash_sequences WHERE paused=1")}


def default_sequence_id():
    with db.connect() as c:
        r = c.execute("SELECT id FROM gaash_sequences WHERE archived=0 "
                      "ORDER BY created_at LIMIT 1").fetchone()
    return r["id"] if r else None


# ── CRUD: auto-enroll rules ──
# HubSpot-style criteria (owner request, from the workflow-triggers screens):
# cond = {"groups": [{"crits": [{field, op, value}, …]}, …]} — crits inside a
# group AND together, groups OR together ("Group 1 / or / Group 2"). The
# legacy fixed shape {gash_status, min_age_days} is still accepted everywhere
# and auto-converted, so pre-existing rules keep working untouched.
RULE_FIELDS = {                          # field → type; the criteria universe
    "gash_status": "text",               # Leluxe ClickUp GASH STATUS field
    "status": "text",                    # board status / otlobly_status
    "bucket": "text",                    # tracking bucket (see _bucket)
    "label": "text",                     # tracking status label
    "name": "text",                      # profile / ship-to name
    "customers": "text",                 # Purchases customer names
    "source": "enum",                    # leluxe | purchases
    "age_days": "number",                # days since the record was created
    "autoclear": "enum",                 # "1" when the in-app ✅ AUTO CLEAR tag is set
}
_RULE_OPS = {"text": ("is", "is_not", "contains", "not_contains",
                      "empty", "not_empty"),
             "number": ("gte", "lte"),
             "enum": ("is", "is_not")}
_SOURCES = ("leluxe", "purchases")


def _field_kind(f):
    """The op family for a criterion field. `cf:<label>` = any board custom
    column (all treated as text: is/contains/empty…). None ⇒ unknown, drop."""
    if f in RULE_FIELDS:
        return RULE_FIELDS[f]
    if f.startswith("cf:") and len(f) > 3:
        return "text"
    return None


def _cf_stringify(v):
    """A custom-field value (any board, any type) → a string we can match on."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return ", ".join(_cf_stringify(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):                # unsupported ClickUp shape → skip
        return str(v.get("value") or v.get("name") or "")
    return str(v)


def _cf_defs():
    """Purchases custom-field DEFINITIONS (settings.custom_fields.po)."""
    try:
        return (settings_mod.read().get("custom_fields") or {}).get("po") or []
    except Exception:  # noqa
        return []


def _cond_norm(cond):
    """Sanitized v2 criteria out of any stored/posted cond (incl. legacy)."""
    cond = cond if isinstance(cond, dict) else {}
    if "groups" not in cond:             # legacy {gash_status, min_age_days}
        crits = []
        if str(cond.get("gash_status") or "").strip():
            crits.append({"field": "gash_status", "op": "is",
                          "value": str(cond["gash_status"]).strip()})
        try:
            age = int(cond.get("min_age_days") or 0)
        except (TypeError, ValueError):
            age = 0
        if age > 0:
            crits.append({"field": "age_days", "op": "gte", "value": age})
        return {"groups": [{"crits": crits}] if crits else []}
    groups = []
    for g in (cond.get("groups") or [])[:5]:
        crits = []
        for cr in ((g if isinstance(g, dict) else {}).get("crits") or [])[:8]:
            cr = cr if isinstance(cr, dict) else {}
            f = str(cr.get("field") or "").strip()
            op = str(cr.get("op") or "").strip()
            kind = _field_kind(f)
            if not kind or op not in _RULE_OPS[kind]:
                continue                 # unknown field / op → dropped
            v = cr.get("value")
            if kind == "number":
                try:
                    v = min(365, max(0, int(v or 0)))
                except (TypeError, ValueError):
                    v = 0
            elif f == "source":
                v = str(v or "").strip().lower()
                if v not in _SOURCES:
                    continue
            else:
                v = str(v or "").strip()
                if not v and op not in ("empty", "not_empty"):
                    continue             # a text match needs a value
            crits.append({"field": f, "op": op, "value": v})
        if crits:
            groups.append({"crits": crits})
    return {"groups": groups}


def _fold(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _cand_cf_val(cd, label):
    """A candidate's value for a custom column, matched by folded label."""
    lab = _fold(label)
    for k, v in (cd.get("cf") or {}).items():
        if _fold(k) == lab:
            return v
    return ""


def _crit_match(cr, cd, age_fn):
    f, op, want = cr["field"], cr["op"], cr.get("value")
    if f == "age_days":
        age = age_fn(cd)
        if age is None:
            return False
        return age >= want if op == "gte" else age <= want
    have = _fold(_cand_cf_val(cd, f[3:]) if f.startswith("cf:") else cd.get(f))
    if op == "empty":
        return not have
    if op == "not_empty":
        return bool(have)
    w = _fold(want)
    return {"is": have == w, "is_not": have != w,
            "contains": w in have,
            "not_contains": w not in have}.get(op, False)


def _cond_match(cond, cd, age_fn):
    groups = (cond or {}).get("groups") or []
    if not groups:                       # no criteria = match every candidate
        return True                      # (same as the old blank-status rule)
    return any(all(_crit_match(cr, cd, age_fn) for cr in g["crits"])
               for g in groups)


def _cand_age_days(cd):
    """Days since this candidate's record was created, or None if unknown."""
    try:
        if cd.get("source") == "purchases":
            import purchases
            for p in (purchases.load() or {}).get("purchase_orders") or []:
                if p.get("po_id") == cd.get("po_id"):
                    created = str(p.get("created_at") or "").strip()
                    if not created:
                        return None
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return (time.time() - dt.timestamp()) / 86400
            return None
        row = _leluxe_row_for(cd["gwd"])
        if not row:
            return None
        with db.connect() as c:
            rr = c.execute("SELECT date_created FROM leluxe_orders WHERE id=?",
                           (row["id"],)).fetchone()
        created = float(rr["date_created"] or 0) if rr else 0   # ClickUp ms
        return (time.time() * 1000 - created) / 864e5 if created else None
    except Exception:  # noqa
        return None


def rules_list():
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM gaash_rules ORDER BY created_at")]
    for r in rows:
        try:
            r["cond"] = _cond_norm(json.loads(r.pop("cond_json") or "{}"))
        except Exception:  # noqa
            r["cond"] = {"groups": []}
    return rows


def rule_save(r):
    rid = (r.get("id") or "").strip() or f"rul_{int(time.time() * 1000)}"
    name = str(r.get("name") or "").strip()
    seq_id = (r.get("seq_id") or "").strip()
    if not name or not sequence_get(seq_id):
        return {"ok": False, "error": "rule needs a name and a valid sequence"}
    cond = _cond_norm(r.get("cond"))
    mode = r.get("mode") if r.get("mode") in ("queue", "auto") else "queue"
    with db.connect() as c:
        c.execute("""INSERT INTO gaash_rules
            (id,name,enabled,cond_json,seq_id,mode,created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,
              enabled=excluded.enabled, cond_json=excluded.cond_json,
              seq_id=excluded.seq_id, mode=excluded.mode""",
                  (rid, name, 1 if r.get("enabled", True) else 0,
                   json.dumps(cond), seq_id, mode, now_iso()))
    return {"ok": True, "id": rid}


def rule_remove(rid):
    with db.connect() as c:
        c.execute("DELETE FROM gaash_rules WHERE id=?", (rid,))
    return {"ok": True}


def run_rules():
    """Evaluate enabled auto-enroll rules against the candidates. mode=queue →
    a 'proposed' thread (approval chip + bell); mode=auto → real enrollment."""
    paused = _paused_seq_ids()           # OFF workflows don't enroll anything
    rules = [r for r in rules_list()
             if r.get("enabled") and r.get("seq_id") not in paused]
    if not rules:
        return 0
    cands = candidates()
    made = 0
    ages = {}                            # gwd → age; computed only when asked

    def age_fn(cd):
        if cd["gwd"] not in ages:
            ages[cd["gwd"]] = _cand_age_days(cd)
        return ages[cd["gwd"]]

    for r in rules:
        for cd in cands:
            if thread_get(cd["gwd"]):
                continue
            if not _cond_match(r["cond"], cd, age_fn):
                continue
            # readiness gate: an auto rule may only ENROLL a parcel whose ID
            # actually resolves (pick → CRM → name map → default). Anything
            # else — auto or queue — lands as a 'proposed' chip for review, so
            # a tag can never push an ID-less email out the door.
            if r["mode"] == "auto" and cd.get("pname_id"):
                start_threads([cd["gwd"]], None, None, seq_id=r["seq_id"])
            else:
                note = (None if r["mode"] != "auto" else
                        "مؤجل تلقائياً: بلا هوية · auto-enroll held: no ID yet")
                with db.connect() as c:
                    c.execute("""INSERT INTO gaash_threads
                        (gwd,seq_id,state,step,unread,missing_docs,
                         pending_files_json,created_at,last_activity,missing_note)
                        VALUES (?,?, 'proposed',0,0,0,'[]',?,?,?)""",
                              (cd["gwd"], r["seq_id"], now_iso(), now_iso(),
                               note))
            made += 1
    return made


def rule_matches(cond, cands=None):
    """How many enrollable packages a criteria set matches RIGHT NOW (+ their
    GWDs, capped) — powers the ⚡ match chips and the trigger modal's live count."""
    cands = candidates() if cands is None else cands
    ages = {}

    def age_fn(cd):
        if cd["gwd"] not in ages:
            ages[cd["gwd"]] = _cand_age_days(cd)
        return ages[cd["gwd"]]

    cond = _cond_norm(cond)
    gwds = [cd["gwd"] for cd in cands if _cond_match(cond, cd, age_fn)]
    return {"count": len(gwds), "gwds": gwds[:500]}


def rules_match_map():
    """{rule_id: {count, gwds}} for every ENABLED rule, in one candidates scan."""
    rules = [r for r in rules_list() if r.get("enabled")]
    if not rules:
        return {}
    cands = candidates()
    return {r["id"]: rule_matches(r["cond"], cands) for r in rules}


# ── open + click tracking (self-hosted pixel + redirect) ──
def _track_base():
    return (os.environ.get("PORTAL_BASE_URL") or "").rstrip("/")


def _track_sig(msg_id, idx, url=""):
    key = (os.environ.get("OTLOBLY_SECRET") or "dev").encode()
    return hmac.new(key, f"{msg_id}|{idx}|{url}".encode(),
                    hashlib.sha256).hexdigest()[:20]


def track_token(msg_id, idx, url=""):
    return f"{msg_id}.{idx}.{_track_sig(msg_id, idx, url)}"


def verify_token(token, url=""):
    """(msg_id, idx) when the signature checks out, else None."""
    m = re.match(r"^(\d+)\.(\d+)\.([0-9a-f]{20})$", str(token or ""))
    if not m:
        return None
    msg_id, idx, sig = int(m.group(1)), int(m.group(2)), m.group(3)
    if not hmac.compare_digest(sig, _track_sig(msg_id, idx, url)):
        return None
    return msg_id, idx


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


# **bold** and ##big## in a template: <strong> / large+bold in the HTML part,
# markers stripped from the plain-text part — a template stays readable and no
# reader ever sees a marker. ##big## is for the one line that must not be
# missed at a glance: the tracking number.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_BIG_RE = re.compile(r"##(.+?)##", re.S)


def strip_bold(text):
    return _BIG_RE.sub(r"\1", _BOLD_RE.sub(r"\1", text or ""))


def _html_body(text, msg_db_id):
    """HTML alternative: escaped text with tracked links + an open pixel.
    Falls back to None (plain-text only) when no public base URL is set."""
    base = _track_base()
    if not base or not msg_db_id:
        return None
    urls = []

    def _sub(m):
        url = m.group(0)
        idx = len(urls)
        urls.append(url)
        t = track_token(msg_db_id, idx, url)
        return (f'<a href="{base}/api/gaash/r/{t}?u={_uq(url)}">'
                f"{html_mod.escape(url)}</a>")
    body = _URL_RE.sub(_sub, html_mod.escape(text or ""))
    # escape ran first, so these can only emphasise — never inject markup
    body = _BIG_RE.sub(r'<span style="font-size:20px;font-weight:800;'
                       r'letter-spacing:.3px">\1</span>', body)
    body = _BOLD_RE.sub(r"<strong>\1</strong>", body)
    body = body.replace("\n", "<br>\n")
    px = track_token(msg_db_id, 0, "")
    return (f'<div style="font-family:Arial,sans-serif;font-size:14px;'
            f'line-height:1.5">{body}</div>'
            f'<img src="{base}/api/gaash/px/{px}.gif" width="1" height="1" '
            f'alt="" style="display:none">')


def _uq(s):
    from urllib.parse import quote
    return quote(str(s or ""), safe="")


def record_event(msg_id, kind, url=None, ua=None):
    now = now_iso()
    with db.connect() as c:
        c.execute("INSERT INTO gaash_events (msg_id,kind,at,url,ua) "
                  "VALUES (?,?,?,?,?)", (msg_id, kind, now, url, (ua or "")[:200]))
        col, first = ("opens", "first_open_at") if kind == "open" else \
                     ("clicks", "first_click_at")
        c.execute(f"UPDATE gaash_msgs SET {col}={col}+1, "
                  f"{first}=COALESCE({first},?) WHERE id=?", (now, msg_id))


# 1×1 transparent GIF (the tracking pixel body)
PIXEL_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
             b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
             b"\x00\x02\x02D\x01\x00;")


# ── stats (the 📊 dashboard) ──
def stats():
    with db.connect() as c:
        seqs = {s["id"]: {"seq_id": s["id"], "name": s["name"],
                          "enrolled": 0, "enrolled_7d": 0, "active": 0,
                          "goal_met": 0, "exhausted": 0, "replied": 0,
                          "sent": 0, "opened": 0, "clicked": 0, "bounces": 0,
                          "steps": []}
                for s in sequences_list(include_archived=True)}
        overall = {"enrolled": 0, "enrolled_7d": 0, "active": 0, "goal_met": 0,
                   "exhausted": 0, "replied": 0, "sent": 0, "opened": 0,
                   "clicked": 0, "bounces": 0}
        LIVE = ("active", "waiting_reply", "missing_docs", "paused",
                "waiting_task")
        wk_ago = datetime.now(timezone.utc) - timedelta(days=7)
        for t in c.execute("SELECT seq_id, state, created_at FROM gaash_threads "
                           "WHERE state!='proposed'"):
            tgt = seqs.get(t["seq_id"])
            made = _parse_iso(t["created_at"] or "")
            for d in ([overall, tgt] if tgt else [overall]):
                d["enrolled"] += 1
                if made and made >= wk_ago:
                    d["enrolled_7d"] += 1
                if t["state"] in LIVE:
                    d["active"] += 1
                if t["state"] in ("goal_met", "cleared"):
                    d["goal_met"] += 1
                if t["state"] == "exhausted":
                    d["exhausted"] += 1
        for m in c.execute("SELECT seq_id, dir, kind, step, opens, clicks "
                           "FROM gaash_msgs"):
            tgt = seqs.get(m["seq_id"])
            tgts = [overall, tgt] if tgt else [overall]
            if m["dir"] == "out" and m["kind"] in ("sent", "resent"):
                for d in tgts:
                    d["sent"] += 1
                    if (m["opens"] or 0) > 0:
                        d["opened"] += 1
                    if (m["clicks"] or 0) > 0:
                        d["clicked"] += 1
                if tgt is not None and m["step"]:
                    steps = tgt["steps"]
                    while len(steps) < m["step"]:
                        steps.append(0)
                    steps[m["step"] - 1] += 1
            elif m["dir"] == "in" and m["kind"] == "bounce":
                for d in tgts:
                    d["bounces"] += 1
        for r in c.execute("SELECT DISTINCT gwd FROM gaash_msgs WHERE dir='in' "
                           "AND kind='reply'"):
            overall["replied"] += 1
            t = c.execute("SELECT seq_id FROM gaash_threads WHERE gwd=?",
                          (r["gwd"],)).fetchone()
            if t and t["seq_id"] in seqs:
                seqs[t["seq_id"]]["replied"] += 1
    return {"overall": overall, "sequences": list(seqs.values())}


# ── Overview drill-down: which exact email is behind a stat tile ──
def stat_detail(kind, limit=300):
    """The actual EMAILS behind an 🧭 Overview tile ('sent'|'opened'|'clicked'|
    'replied') — one row per message, carrying enough to render a real mail row
    (who/whom/when, opens+clicks, and the body) instead of a bare count.

    'replied' lists the individual inbound replies rather than one row per
    parcel: the interesting thing about a reply is what GAASH actually wrote."""
    pmap = parcel_name_map()
    cols = ("id, gwd, dir, kind, step, at, from_addr, to_addr, subject, body, "
            "opens, clicks, first_open_at, first_click_at")
    with db.connect() as c:
        labels = {r["email"]: (r["label"] or "").strip()
                  for r in c.execute("SELECT email, label FROM gaash_accounts")}
        if kind in ("sent", "opened", "clicked"):
            where = {"sent": "1=1", "opened": "opens>0",
                     "clicked": "clicks>0"}[kind]
            order = {"sent": "at", "opened": "first_open_at",
                     "clicked": "first_click_at"}[kind]
            rows = [dict(r) for r in c.execute(
                f"SELECT {cols} FROM gaash_msgs "
                f"WHERE dir='out' AND kind IN ('sent','resent') AND {where} "
                f"ORDER BY {order} DESC LIMIT ?", (limit,))]
        elif kind == "replied":
            rows = [dict(r) for r in c.execute(
                f"SELECT {cols} FROM gaash_msgs WHERE dir='in' "
                f"ORDER BY at DESC LIMIT ?", (limit,))]
        else:
            return []
    for r in rows:
        r["pname"] = pmap.get(r["gwd"], "")
        frm = (r.get("from_addr") or "").strip()
        # the sending mailbox has a human label ("Qais abusamra"); GAASH's own
        # address has none, so fall back to the part before the @
        r["from_name"] = labels.get(frm) or frm.split("@")[0]
        r["body"] = (r.get("body") or "")[:4000]
    return rows


def seed_followup_template():
    """A hand-sent nudge for a parcel whose earlier email was opened but never
    answered. Seeded by id with INSERT OR IGNORE, so it appears once and any
    edit the owner makes to it survives every restart.

    It never mentions the read receipt. Knowing a message was opened is how the
    owner CHOOSES who to chase; saying so to a customs clerk reads as
    surveillance and costs more goodwill than the nudge is worth."""
    body = (
        "Hello,\n\n"
        "I hope you are well.\n\n"
        "I am following up on parcel {gwd}, which has now been waiting "
        "{days_waiting} days.\n\n"
        "Tracking number: {gwd}\n"
        "ID number: {id_number}\n\n"
        "Our dealer import certification is attached for your reference. If "
        "anything further is needed from our side, please tell me exactly which "
        "document and I will send it the same day.\n\n"
        "Could you let me know the current status and the expected clearance "
        "date?\n\n"
        "Thank you for your help,\n"
        "Otlobly")
    try:
        with db.connect() as c:
            c.execute("""INSERT OR IGNORE INTO gaash_templates
                (id,name,subject_tpl,body_tpl,updated_at) VALUES (?,?,?,?,?)""",
                      ("tpl_followup", "Follow-up — polite nudge",
                       "Follow-up — parcel {gwd}", body, now_iso()))
    except Exception:  # noqa - a missing template must never block boot
        pass


# ── one-time v2 seed: the legacy settings 4-step chain → real data ──
def migrate_v2():
    """Create the default 'GAASH clearance' sequence + its 4 templates from the
    legacy settings, and point old threads at it. claim_once ⇒ runs once per DB;
    never raises (boot must not break)."""
    try:
        with db.connect() as c:
            have = c.execute("SELECT COUNT(*) n FROM gaash_sequences").fetchone()["n"]
        if have or not db.claim_once("gaash_v2_seed"):
            return
        setts = _setts()
        tpl_ids = []
        for i, st in enumerate(setts.get("steps") or []):
            tid = f"tpl_seed{i + 1}"
            with db.connect() as c:
                c.execute("""INSERT OR IGNORE INTO gaash_templates
                    (id,name,subject_tpl,body_tpl,updated_at) VALUES (?,?,?,?,?)""",
                          (tid, f"GAASH clearance #{i + 1}",
                           st.get("subject_tpl") or "", st.get("body_tpl") or "",
                           now_iso()))
            tpl_ids.append(tid)
        cadence = setts.get("cadence_days") or [2, 2, 2]
        steps = []
        for i, tid in enumerate(tpl_ids):
            delay = 0 if i == 0 else cadence[min(i - 1, len(cadence) - 1)]
            steps.append({"kind": "auto_email", "template_id": tid,
                          "delay_days": delay})
        res = sequence_save({"id": "seq_default", "name": "GAASH clearance",
                             "to_address": setts.get("to_address") or "",
                             "goal": "cleared", "send_window": DEFAULT_WINDOW,
                             "steps": steps})
        if res.get("ok"):
            with db.connect() as c:
                c.execute("UPDATE gaash_threads SET seq_id='seq_default' "
                          "WHERE seq_id IS NULL")
    except Exception as e:  # noqa - the seed must never block boot
        print(f"gaash_mail: v2 seed skipped ({e})")


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


def thread_delete(gwd):
    """Erase an accidental enrollment COMPLETELY — thread, messages, queued
    files. Refused once the thread has real CORRESPONDENCE — mark it ✓ done
    instead. Two independent signals say 'real', and either one is enough:

      • somebody wrote back (any inbound message), or
      • an email actually went to the configured recipient — the live GAASH
        address on the sequence or in Settings.

    An email that only ever went to the owner's own test inbox is NOT
    correspondence: nobody at GAASH has it, so there is no history to protect
    and erasing it is the honest way to keep the 🧭 Overview counting real work
    only. Dry-run-blocked attempts leave no outbound message at all, so a
    purely-accidental thread always qualifies."""
    g = (gwd or "").strip().upper()
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM gaash_threads WHERE gwd=?", (g,)).fetchone():
            return {"ok": False, "error": "thread not found"}
        inbound = c.execute("SELECT COUNT(*) n FROM gaash_msgs WHERE gwd=? AND "
                            "dir='in'", (g,)).fetchone()["n"]
        real_to = {a for a in (
            [r["to_address"] for r in c.execute(
                "SELECT to_address FROM gaash_sequences WHERE to_address IS NOT NULL")]
            + [_setts().get("to_address")]) if (a or "").strip()}
        real_to = {a.strip().lower() for a in real_to}
        sent_real = [r["to_addr"] for r in c.execute(
            "SELECT DISTINCT to_addr FROM gaash_msgs WHERE gwd=? AND dir='out' "
            "AND kind IN ('sent','resent')", (g,))
            if (r["to_addr"] or "").strip().lower() in real_to]
        if inbound or sent_real:
            who = sent_real[0] if sent_real else "GAASH"
            return {"ok": False, "error":
                    f"مراسلة حقيقية مع {who} — علّمها ✓ منجزة بدلاً من الحذف · "
                    f"real correspondence with {who} — mark it ✓ done instead"}
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM gaash_msgs WHERE gwd=?", (g,))]
        if ids:
            q = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM gaash_events WHERE msg_id IN ({q})", ids)
        c.execute("DELETE FROM gaash_msgs WHERE gwd=?", (g,))
        c.execute("DELETE FROM gaash_threads WHERE gwd=?", (g,))
        # the pick (gaash_picks) survives on purpose — the NAME is truth about
        # the parcel, the thread was the mistake
    try:                                   # queued attachment bytes, if any
        import shutil
        shutil.rmtree(FILES_DIR / g, ignore_errors=True)
    except Exception:  # noqa
        pass
    return {"ok": True, "gwd": g}


def thread_restart(gwd, fresh=False, at=None):
    """🔁 back to email #1. For threads whose 'sent' history went to the wrong
    place (the test-inbox era) or where GAASH went silent — the sequence
    position resets and send_step re-renders step 1 with the CURRENT
    recipient/template/ID resolution.

    fresh=True also ERASES the message history, so the next email opens a clean
    conversation: no Re: prefix, no In-Reply-To pointing at mail the recipient
    never received. That is the honest shape when the history is with someone
    else (a test inbox), not with the real recipient.
    at=<iso> schedules step 1 for later (the daemon sends it) instead of now —
    used to space a batch out so it doesn't land as one blast."""
    g = (gwd or "").strip().upper()
    th = thread_get(g)
    if not th:
        return {"ok": False, "error": "thread not found"}
    if th.get("state") == "proposed":
        return {"ok": False, "error":
                "اقتراح لم يبدأ — وافق عليه بدلاً من الإعادة · "
                "still a suggestion — approve it instead of restarting"}
    if fresh:
        with db.connect() as c:
            c.execute("DELETE FROM gaash_msgs WHERE gwd=?", (g,))
    # subject too: _thread_send prefers the thread's stored base subject over
    # the template's, so leaving it would resend email #1 under the OLD subject
    _thread_set(g, step=0, state="active", missing_docs=0, missing_note=None,
                resend_json=None, last_error=None, subject=None,
                unread=0, next_send_at=(at or datetime.now(timezone.utc)
                                        .isoformat(timespec="seconds")))
    if at:                                 # the daemon sends it when it's due
        return {"ok": True, "gwd": g, "state": "active", "scheduled_at": at}
    res = send_step(g)                     # same immediate-send as start_threads
    return {"ok": True, "gwd": g, "state": "active",
            **({} if res.get("ok") else
               {"send_error": res.get("error"),
                "dry_run": res.get("dry_run", False)})}


def thread_switch_seq(gwd, seq_id, at=None):
    """Move a package to a DIFFERENT workflow.

    `seq_id` was only ever written at enrollment (start_threads), so a package
    was stuck in whatever workflow it started in — the sole way out being delete
    + re-enroll, which is refused once real correspondence exists.

    The move RESTARTS at email #1 of the new workflow: step numbers are
    per-sequence, so carrying step 2 into a 3-step workflow would silently skip
    its first two emails. The message history is kept — it is real
    correspondence with the same recipient, and the next email threads onto it.
    """
    g = (gwd or "").strip().upper()
    th = thread_get(g)
    if not th:
        return {"ok": False, "error": "thread not found"}
    if th.get("state") == "proposed":
        return {"ok": False, "error":
                "اقتراح لم يبدأ — وافق عليه أولاً · "
                "still a suggestion — approve it first"}
    seq = sequence_get(seq_id)
    if not seq:
        return {"ok": False, "error": "workflow not found"}
    if seq.get("paused"):
        return {"ok": False, "error":
                "هذا السير موقوف — شغّله أولاً · "
                "that workflow is off — switch it on first"}
    if (th.get("seq_id") or "") == seq["id"]:
        return {"ok": False, "error": "already in that workflow"}
    # subject too: _thread_send prefers the thread's stored base subject over the
    # template's, so leaving it would send the NEW sequence under the OLD subject
    _thread_set(g, seq_id=seq["id"], step=0, state="active", subject=None,
                missing_docs=0, missing_note=None, resend_json=None,
                last_error=None,
                next_send_at=(at or datetime.now(timezone.utc)
                              .isoformat(timespec="seconds")))
    out = {"ok": True, "gwd": g, "seq_id": seq["id"], "seq_name": seq.get("name")}
    if at:                                 # the daemon sends it when it's due
        return {**out, "scheduled_at": at}
    res = send_step(g)                     # same immediate send as start_threads
    return {**out, **({} if res.get("ok") else
                      {"send_error": res.get("error"),
                       "dry_run": res.get("dry_run", False)})}


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


def _id_number_for(gwd):
    """The package customer's CRM ID number (submitted via the Request-ID link),
    or "". Resolves the customer by phone — the Purchases order link first, then a
    'phone' field on the Leluxe mirror row — and falls back to a name match."""
    g = (gwd or "").strip().upper()
    try:
        import normalize
        by_phone, by_name = {}, {}
        for c in db.list_customers():
            num = str(c.get("id_number") or "").strip()
            if not num:
                continue
            core = normalize.phone_core(c.get("whatsapp") or "")
            if core:
                by_phone.setdefault(core, num)
            nm = _fold(c.get("name"))
            if nm:
                by_name.setdefault(nm, num)
        if not by_phone and not by_name:
            return ""
        try:                                  # 1) Purchases: gwd → order → phone
            import purchases
            for p in (purchases.load() or {}).get("purchase_orders") or []:
                for pk in (p.get("packages") or []):
                    if str(pk.get("tracking_number") or "").strip().upper() != g:
                        continue
                    for it in (pk.get("items") or []):
                        oid = it.get("customer_order_id")
                        o = db.get_order(oid) if oid else None
                        for ph in ((o or {}).get("customer") or {}).get("phones") or []:
                            core = normalize.phone_core(ph.get("e164") or "")
                            if core in by_phone:
                                return by_phone[core]
        except Exception:  # noqa
            pass
        row = _leluxe_row_for(g)              # 2) Leluxe: a phone field on the row
        if row:
            for k, v in ((row.get("data") or {}).get("fields") or {}).items():
                if "phone" in str(k).lower():
                    core = normalize.phone_core(str(v or ""))
                    if core and core in by_phone:
                        return by_phone[core]
        nm = _fold(_customer_for(g))          # 3) name fallback
        if nm in by_name:
            return by_name[nm]
    except Exception:  # noqa
        pass
    return ""


def _name_on_pkg_for(gwd):
    """The 'NAME ON PACKAGEE' board value for this GWD — the account-holder name
    GAASH sees on the parcel (FAISAL / QAIS / Nuray…). The field lives on item and
    parent rows (never package rows), so every row carrying the GWD is checked
    first, then their parent orders. Label match tolerates the typo being fixed."""
    g = (gwd or "").strip().upper()
    if not g:
        return ""

    def pick(fields):
        for k, v in (fields or {}).items():
            if _fold(k).startswith("name on packag"):
                s = _cf_stringify(v).strip()
                if s:
                    return s
        return ""

    parents = []
    try:
        with db.connect() as c:
            rows = c.execute(
                "SELECT parent_local_id, data_json FROM leluxe_orders "
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
            if tn != g:
                continue
            nm = pick(f)
            if nm:
                return nm
            if r["parent_local_id"]:
                parents.append(r["parent_local_id"])
        if parents:
            marks = ",".join("?" * len(set(parents)))
            with db.connect() as c:
                prows = c.execute(
                    f"SELECT data_json FROM leluxe_orders WHERE id IN ({marks})",
                    tuple(set(parents))).fetchall()
            for pr in prows:
                try:
                    nm = pick((json.loads(pr["data_json"] or "{}") or {}).get("fields"))
                except Exception:  # noqa
                    nm = ""
                if nm:
                    return nm
    except Exception:  # noqa
        pass
    return ""


def set_name_id(name, id_number):
    """Map ONE on-package name → ID number, merging into gaash_mail.name_ids.

    settings.apply replaces that dict wholesale, so a caller who only knows one
    pair (the enroll picker's 🪪 column) must not POST /api/settings directly —
    it would wipe every other mapping. Read-modify-write here instead. An empty
    id_number DELETES the mapping, which is how the picker clears one."""
    nm = re.sub(r"\s+", " ", str(name or "")).strip()
    if not nm:
        return {"error": "name is required"}
    val = str(id_number or "").strip()
    ids = dict(_setts().get("name_ids") or {})
    for k in [k for k in ids if _fold(k) == _fold(nm)]:
        ids.pop(k)                      # replace any case/spacing variant
    if val:
        ids[nm] = val
    settings_mod.apply({"gaash_mail": {"name_ids": ids}})
    return {"ok": True, "name": nm, "id_number": val, "count": len(ids)}


def parcel_name(gwd):
    """The name this ONE parcel ships under — what GAASH reads off the label.
    The owner's pick (gaash_threads.pname) wins; otherwise Leluxe's ClickUp
    "NAME ON PACKAGEE" column, then a Purchases column of that name, then the
    PO's Main name (ship_to), and finally the Settings default_name."""
    # neither board names it (most Leluxe rows leave NAME ON PACKAGEE blank) →
    # the Settings default, which every surface marks as assumed
    return _picked_name(gwd) or _board_name(gwd) or _default_name()


def _board_name(gwd):
    """What the BOARDS say this parcel ships under, ignoring pick and default."""
    nm = _name_on_pkg_for(gwd)
    if nm:
        return nm
    for k, v in _cf_for_gwd(gwd).items():       # a Purchases column of that name
        if _fold(k).startswith("name on packag") and str(v or "").strip():
            return str(v).strip()
    g = (gwd or "").strip().upper()
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            for pk in (p.get("packages") or []):
                if str(pk.get("tracking_number") or "").strip().upper() == g:
                    ship = str(p.get("ship_to") or "").strip()
                    if ship:
                        return ship
    except Exception:  # noqa
        pass
    return ""


def _default_name():
    return str(_setts().get("default_name") or "").strip()


def parcel_name_src(gwd):
    """Where parcel_name's answer came from: pick | board | default | "" — so the
    UI can flag an ASSUMED identity rather than presenting it as fact."""
    if _picked_name(gwd):
        return "pick"
    if _board_name(gwd):
        return "board"
    return "default" if _default_name() else ""


def _picked_name(gwd):
    """The name the owner explicitly picked for this package, or "".
    A thread's pname wins; gaash_picks covers parcels picked BEFORE enrolling."""
    g = (gwd or "").strip().upper()
    try:
        with db.connect() as c:
            r = c.execute("SELECT pname FROM gaash_threads WHERE gwd=?",
                          (g,)).fetchone()
            if r and str(r["pname"] or "").strip():
                return str(r["pname"]).strip()
            r = c.execute("SELECT pname FROM gaash_picks WHERE gwd=?",
                          (g,)).fetchone()
        return str((r["pname"] if r else "") or "").strip()
    except Exception:  # noqa
        return ""


def picked_name_map():
    """{GWD: owner-picked name} — pre-enroll picks first, thread pins on top
    (same precedence as _picked_name), batched."""
    out = {}
    try:
        with db.connect() as c:
            for r in c.execute("SELECT gwd, pname FROM gaash_picks "
                               "WHERE pname IS NOT NULL AND pname<>''"):
                out[r["gwd"]] = str(r["pname"]).strip()
            for r in c.execute("SELECT gwd, pname FROM gaash_threads "
                               "WHERE pname IS NOT NULL AND pname<>''"):
                out[r["gwd"]] = str(r["pname"]).strip()
    except Exception:  # noqa
        pass
    return out


def set_parcel_name(gwd, name):
    """Pin the name a package ships under (""/None clears it → back to the board).
    Persists in gaash_picks too, so a pick made in the enroll picker sticks even
    if the parcel is never enrolled — that was the "no save button" hole."""
    g = (gwd or "").strip().upper()
    nm = re.sub(r"\s+", " ", str(name or "")).strip()[:60]
    with db.connect() as c:
        c.execute("UPDATE gaash_threads SET pname=? WHERE gwd=?", (nm or None, g))
        if nm:
            c.execute("INSERT INTO gaash_picks(gwd,pname,updated_at) VALUES(?,?,?) "
                      "ON CONFLICT(gwd) DO UPDATE SET pname=excluded.pname, "
                      "updated_at=excluded.updated_at", (g, nm, now_iso()))
        else:
            c.execute("DELETE FROM gaash_picks WHERE gwd=?", (g,))
    return {"ok": True, "gwd": g, "pname": parcel_name(g),   # cleared → back to the board
            "pname_id": id_number_for_email(g)}


def name_id_of(name):
    """The Settings-mapped ID number for a parcel name — folded compare, so
    FAISAL / faisal / Faisal all hit. "" when the name isn't mapped yet."""
    ids = _setts().get("name_ids") or {}
    nm = _fold(name)
    if not nm or not isinstance(ids, dict):
        return ""
    for k, v in ids.items():
        if _fold(k) == nm:
            return str(v or "").strip()
    return ""


def _board_name_map():
    """{GWD: the name the BOARDS give it} in one scan — no picks, no default."""
    out = {}
    try:
        with db.connect() as c:
            rows = c.execute("SELECT id, parent_local_id, data_json "
                             "FROM leluxe_orders WHERE deleted=0").fetchall()
    except Exception:  # noqa
        rows = []
    parsed, by_id = [], {}
    for r in rows:
        try:
            d = json.loads(r["data_json"] or "{}")
        except Exception:  # noqa
            continue
        f = d.get("fields") or {}
        nm = ""
        for k, v in f.items():
            if _fold(k).startswith("name on packag"):
                nm = _cf_stringify(v).strip()
                break
        by_id[r["id"]] = nm
        tn = str(d.get("tracking_number") or "").strip().upper()
        if not tn:
            for k, v in f.items():
                if k.strip().lower() == "tracking number":
                    tn = str(v or "").strip().upper()
                    break
        if tn:
            parsed.append((tn, nm, r["parent_local_id"]))
    for tn, nm, pid in parsed:                  # own field, else the parent order's
        v = nm or by_id.get(pid) or ""
        if v and not out.get(tn):
            out[tn] = v
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            ship = str(p.get("ship_to") or "").strip()
            if not ship:
                continue
            for pk in (p.get("packages") or []):
                tn = str(pk.get("tracking_number") or "").strip().upper()
                if tn and not out.get(tn):
                    out[tn] = ship
    except Exception:  # noqa
        pass
    return out


def parcel_name_map():
    """{GWD: the name we'll actually use} — board scan, then the owner's picks on
    top, then the Settings default for whatever neither board named."""
    out = _board_name_map()
    out.update(picked_name_map())          # a pick beats both boards
    dflt = _default_name()
    if dflt:                               # only fills gaps; never masks a board name
        for gwd in set(_all_parcel_gwds()) - set(out):
            out[gwd] = dflt
    return out


def _all_parcel_gwds():
    """Every GWD either board knows about — the set the default may fill."""
    out = set()
    try:
        with db.connect() as c:
            for r in c.execute("SELECT data_json FROM leluxe_orders "
                               "WHERE deleted=0 AND data_json LIKE '%GWD%'"):
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
                if re.match(r"GWD\d+$", tn or ""):
                    out.add(tn)
    except Exception:  # noqa
        pass
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            for pk in (p.get("packages") or []):
                tn = str(pk.get("tracking_number") or "").strip().upper()
                if re.match(r"GWD\d+$", tn or ""):
                    out.add(tn)
    except Exception:  # noqa
        pass
    return out


def parcel_board_map():
    """{GWD: "purchases"|"leluxe"} — which board owns the parcel. Drives the
    "where do I add the missing ID?" hint (customer CRM vs the ⚙ name list)."""
    out = {}
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            for pk in (p.get("packages") or []):
                tn = str(pk.get("tracking_number") or "").strip().upper()
                if tn:
                    out[tn] = "purchases"
    except Exception:  # noqa
        pass
    for gwd in _all_parcel_gwds():
        out.setdefault(gwd, "leluxe")
    return out


def tracking_map():
    """{GWD: {gash_status, bucket, label}} — where customs has got to, for every
    parcel, in ONE leluxe scan + ONE purchases load.

    Batched on purpose: the per-GWD twin (_leluxe_row_for) is a full-table LIKE
    scan each time, and the conversation list draws every thread at once."""
    out = {}
    try:
        with db.connect() as c:
            rows = c.execute("SELECT data_json FROM leluxe_orders "
                             "WHERE deleted=0").fetchall()
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
            if not tn:
                continue
            gash = ""
            for k, v in f.items():
                if k.strip().lower() == "gash status":
                    # ClickUp stores several of these with a leading space
                    # (" customer ID"); untrimmed it misses the colour lookup
                    gash = _cf_stringify(v).strip()
                    break
            ts = d.get("tracking_status")
            cur = out.get(tn) or {}
            # a package row carries the truth; an item row may be blank, so keep
            # whichever value actually says something
            out[tn] = {"gash_status": gash or cur.get("gash_status") or "",
                       "bucket": _bucket(ts) or cur.get("bucket") or "",
                       "label": ((ts.get("label") if isinstance(ts, dict)
                                  else (str(ts)[:40] if ts else ""))
                                 or cur.get("label") or "")}
    except Exception:  # noqa
        pass
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            for pk in (p.get("packages") or []):
                tn = str(pk.get("tracking_number") or "").strip().upper()
                if not tn or tn in out:
                    continue
                ts = pk.get("tracking_status")
                out[tn] = {"gash_status": "",       # Purchases has no such field
                           "bucket": _bucket(ts) or "",
                           "label": (ts.get("label") if isinstance(ts, dict)
                                     else (str(ts)[:40] if ts else "")) or ""}
    except Exception:  # noqa
        pass
    return out


def parcel_src_map(pmap=None):
    """{GWD: pick|board|default} for the rows the UI is about to draw — batched
    twin of parcel_name_src (which costs a query per GWD)."""
    pmap = parcel_name_map() if pmap is None else pmap   # else default-only GWDs are missed
    boards = _board_name_map()
    picks = picked_name_map()
    dflt = _default_name()
    out = {}
    for gwd in set(list(pmap.keys()) + list(boards) + list(picks)):
        out[gwd] = ("pick" if picks.get(gwd)
                    else "board" if boards.get(gwd)
                    else ("default" if dflt else ""))
    return out


def _name_id_for(gwd):
    """The ID number mapped (Settings → gaash_mail.name_ids) to this parcel's name."""
    return name_id_of(parcel_name(gwd))


def id_number_for_email(gwd):
    """What {id_number} resolves to, in one place so the UI can't disagree:
    an explicit pick is authoritative (that's the whole point of picking one),
    else the customer's own submitted ID, else the auto-detected name's ID."""
    if _picked_name(gwd):
        return _name_id_for(gwd)
    return _id_number_for(gwd) or _name_id_for(gwd)


def effective_id_map(pmap=None):
    """{GWD: the ID its email will actually carry} — the same customer-first,
    name-map-second rule `_fill` applies to {id_number}, but batched: per-GWD
    resolution reloads the CRM and the Purchases board every call, which the
    picker (dozens of rows at once) can't afford."""
    pmap = parcel_name_map() if pmap is None else pmap
    out = {}
    # an explicit pick short-circuits everything — including a blank result, so a
    # pick whose name has no ID yet must NOT fall through to the customer's
    picks = picked_name_map()
    for gwd, nm in picks.items():
        out[gwd] = name_id_of(nm)
    try:
        import normalize
        by_phone, by_name = {}, {}
        for c in db.list_customers():
            num = str(c.get("id_number") or "").strip()
            if not num:
                continue
            core = normalize.phone_core(c.get("whatsapp") or "")
            if core:
                by_phone.setdefault(core, num)
            nm = _fold(c.get("name"))
            if nm:
                by_name.setdefault(nm, num)
        if by_phone or by_name:
            # 1) Purchases: package → its items' order → that order's phone → CRM
            import purchases
            orders = {str(o.get("order_id") or ""): o for o in db.list_orders()}
            for p in (purchases.load() or {}).get("purchase_orders") or []:
                for pk in (p.get("packages") or []):
                    tn = str(pk.get("tracking_number") or "").strip().upper()
                    if not tn or tn in picks or out.get(tn):
                        continue
                    for it in (pk.get("items") or []):
                        o = orders.get(str(it.get("customer_order_id") or ""))
                        for ph in ((o or {}).get("customer") or {}).get("phones") or []:
                            num = by_phone.get(normalize.phone_core(ph.get("e164") or ""))
                            if num:
                                out[tn] = num
                                break
                        if out.get(tn):
                            break
            # 2) Leluxe: a phone field on the row, else its order name — the same
            #    two fallbacks _id_number_for applies, so this map can't disagree
            with db.connect() as c:
                rows = c.execute("SELECT parent_local_id, data_json FROM leluxe_orders "
                                 "WHERE deleted=0").fetchall()
            pnames = {}
            with db.connect() as c:
                for r in c.execute("SELECT id, name FROM leluxe_orders WHERE deleted=0"):
                    pnames[r["id"]] = r["name"]
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
                if not tn or tn in picks or out.get(tn):
                    continue
                for k, v in f.items():
                    if "phone" in str(k).lower():
                        num = by_phone.get(normalize.phone_core(str(v or "")))
                        if num:
                            out[tn] = num
                            break
                if not out.get(tn):
                    num = by_name.get(_fold(pnames.get(r["parent_local_id"])))
                    if num:
                        out[tn] = num
    except Exception:  # noqa
        pass
    for gwd, nm in pmap.items():           # everything else falls back to the map
        if gwd not in picks and not out.get(gwd):
            v = name_id_of(nm)
            if v:
                out[gwd] = v
    return out


# the fixed personalization tokens (name → friendly label for the UI picker)
TPL_CORE_TOKENS = [
    ("gwd", "رقم التتبع · tracking number"),
    ("customer", "اسم العميل · customer name"),
    ("id_number", "رقم الهوية — هوية العميل، أو هوية الاسم على الطرد إن لم توجد "
                  "· ID number — the customer's, else the on-package name's"),
    ("name_id", "رقم هوية الاسم على الطرد فقط · ID # of the on-package name only"),
    ("upload_link", "رابط رفع المستندات · document-upload link"),
    ("id_name", "اسم مستند الهوية · attached ID name"),
    ("days_waiting", "أيام الانتظار · days waiting"),
    ("step", "رقم الخطوة · step number"),
]


def package_contents(gwd):
    """[{title, qty}] — what is actually inside ONE package, for the customs
    declaration. Deliberately does NOT go through _leluxe_row_for: that returns
    only the FIRST row matching the GWD (an item), so a three-item package would
    silently be declared as one product."""
    g = (gwd or "").strip().upper()
    if not g:
        return []
    out = []
    try:                                        # ── Leluxe: the item ROWS ──
        with db.connect() as c:
            rows = c.execute(
                "SELECT kind, name, data_json FROM leluxe_orders "
                "WHERE deleted=0 AND data_json LIKE ? ORDER BY id",
                (f"%{g}%",)).fetchall()
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
            if tn != g or r["kind"] != "item":
                continue
            qty = ""
            for k, v in f.items():              # 'Quantity ordered ' — trailing space
                if " ".join(str(k).lower().split()) == "quantity ordered":
                    qty = _cf_stringify(v)
                    break
            title = str(r["name"] or "").strip()
            if title:
                out.append({"title": title, "qty": _int_or(qty, 1)})
    except Exception:  # noqa
        pass
    if out:
        return out
    try:                                        # ── Purchases: the package items ──
        import purchases
        pos = (purchases.load() or {}).get("purchase_orders") or []
    except Exception:  # noqa
        pos = []
    for p in pos:
        for pk in (p.get("packages") or []):
            if str(pk.get("tracking_number") or "").strip().upper() != g:
                continue
            for it in (pk.get("items") or []):
                t = str(it.get("title") or "").strip()
                if t:
                    out.append({"title": t, "qty": _int_or(it.get("qty"), 1)})
            return out
    return out


def _int_or(v, dflt):
    try:
        n = int(float(str(v).strip() or 0))
        return n if n > 0 else dflt
    except Exception:  # noqa
        return dflt


def _cf_for_gwd(gwd):
    """{label: value} of this ONE package's board columns — Leluxe ClickUp
    fields + the Purchases PO's custom values (defined-but-empty defs as "")."""
    out = {}
    row = _leluxe_row_for(gwd)
    if row:
        for k, v in ((row.get("data") or {}).get("fields") or {}).items():
            lab = str(k).strip()
            if lab:
                out[lab] = _cf_stringify(v)
        return out
    g = (gwd or "").strip().upper()
    try:
        import purchases
        pos = (purchases.load() or {}).get("purchase_orders") or []
    except Exception:  # noqa
        pos = []
    for p in pos:
        if not any(str(pk.get("tracking_number") or "").strip().upper() == g
                   for pk in (p.get("packages") or [])):
            continue
        cust = p.get("custom") or {}
        for dfn in _cf_defs():                 # every defined column (blank if unset)
            lab = str(dfn.get("label") or "").strip()
            if lab:
                out[lab] = _cf_stringify(cust.get(dfn.get("key")))
        break
    return out


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
    text = (str(tpl or "")
            .replace("{gwd}", gwd)
            .replace("{customer}", _customer_for(gwd))
            .replace("{upload_link}",
                     f"https://ops.gaashwd.com/fileUpload?packageId={gwd}&type=6")
            .replace("{id_name}", id_name)
            .replace("{days_waiting}", days)
            .replace("{step}", str(step or (thread or {}).get("step") or "")))
    # {id_number}: the customer's OWN submitted ID (Request-ID link → CRM) and, when
    # the package has none, the Settings-mapped ID of the name it ships under. That
    # covers both boards with one token — Otlobly/Purchases parcels go to the
    # customer (CRM hit), Leluxe parcels ship under an AZ account holder (name map).
    if "{id_number}" in text:
        text = text.replace("{id_number}", id_number_for_email(gwd))
    if "{name_id}" in text:                     # force the on-package name's ID only
        text = text.replace("{name_id}", _name_id_for(gwd))
    if "{" not in text:                        # no board-column tokens left
        return text
    cf = {_fold(k): v for k, v in _cf_for_gwd(gwd).items()}

    def sub(m):
        v = cf.get(_fold(m.group(1)))
        return v if v is not None else m.group(0)   # unknown token → left literal
    return re.sub(r"\{([^{}]+)\}", sub, text)


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def _smtp_send(acct, msg):
    with _smtp_connect() as s:
        s.login(acct["email"], acct.get("app_password") or "")
        s.send_message(msg)


def _build_msg(acct, to_addr, subject, body, attachments, chain, html=None):
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
    if html:                          # tracked HTML alternative (pixel + links)
        msg.add_alternative(html, subtype="html")
    for name, data, ctype in (attachments or []):
        ctype = ctype if ctype and "/" in ctype else "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=_safe_name(name))
    return msg, mid


def _thread_send(gwd, body, attachments=None, kind="sent", step=None,
                 subject=None, to_override=None, step_id=None):
    """Send one message on a thread (threaded Re: after the first). The message
    row is pre-allocated so the tracking pixel/links can carry its id, then
    DELETED if Gmail rejects the send. Honors dry_run."""
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
    seq = sequence_get(th.get("seq_id"))
    to_addr = (to_override or (seq or {}).get("to_address")
               or setts.get("to_address") or "").strip()
    if not to_addr:
        return {"ok": False, "error": "no recipient address — set one on the sequence or in Settings"}
    base = th.get("subject") or subject or f"Customs clearance — {gwd}"
    prior = [m for m in msgs_for(gwd) if m.get("message_id")]
    subj = base
    if prior and not re.match(r"(?i)re:", base):
        subj = f"Re: {base}"
    chain = [m["message_id"] for m in prior]
    # pre-allocate the row: the tracking token embeds its id
    now = now_iso()
    with db.connect() as c:
        cur = c.execute("""INSERT INTO gaash_msgs
            (gwd,dir,kind,step,at,from_addr,to_addr,subject,body,
             attachments_json,notified,seq_id,step_id)
            VALUES (?,?,?,?,?,?,?,?,?, '[]',1,?,?)""",
                        (gwd, "out", kind, step, now, acct["email"], to_addr,
                         subj, strip_bold(body), th.get("seq_id"), step_id))
        mrow = cur.lastrowid
    # plain part + stored copy carry no markers; the HTML part gets the <strong>
    msg, mid = _build_msg(acct, to_addr, subj, strip_bold(body), attachments,
                          chain, html=_html_body(body or "", mrow))
    try:
        _smtp_send(acct, msg)
    except smtplib.SMTPAuthenticationError:
        with db.connect() as c:
            c.execute("DELETE FROM gaash_msgs WHERE id=?", (mrow,))
        _thread_set(gwd, last_error=_AUTH_HELP)
        return {"ok": False, "error": _AUTH_HELP}
    except Exception as e:  # noqa
        with db.connect() as c:
            c.execute("DELETE FROM gaash_msgs WHERE id=?", (mrow,))
        err = f"send failed: {str(e)[:150]}"
        _thread_set(gwd, last_error=err)
        return {"ok": False, "error": err}
    atts = []
    for name, data, ctype in (attachments or []):
        stored = _store_attachment(gwd, name, data)
        atts.append({"name": _safe_name(name), "file": stored,
                     "size": len(data), "ctype": ctype})
    with db.connect() as c:
        c.execute("UPDATE gaash_msgs SET message_id=?, attachments_json=? "
                  "WHERE id=?", (mid, json.dumps(atts), mrow))
    _thread_set(gwd, subject=base, last_error=None, last_activity=now)
    return {"ok": True, "message_id": mid, "msg_row": mrow}


_DOC_CTYPE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
              "pdf": "application/pdf", "webp": "image/webp"}


def _library_doc(id_doc_id):
    """(filename, bytes, ctype) for one library document, or None."""
    doc = _id_doc(id_doc_id)
    if not doc:
        return None
    p = id_file_path(doc["filename"])
    if not p:
        return None
    ext = p.suffix.lower().lstrip(".")
    return (f"{doc['name']}{p.suffix}", p.read_bytes(),
            _DOC_CTYPE.get(ext, "application/octet-stream"))


def _step_attachments(th):
    """Attachments for the NEXT sequence email.

    docs_json is the package's own document set — a generated declaration, a
    shared ID scan, a dealer certificate — and it rides EVERY email, because a
    different person at GAASH may pick up each follow-up and should never have
    to scroll back for the paperwork. Unlike pending_files_json it is not
    consumed by _schedule_next, so it survives to the next step.

    A thread created before docs_json existed keeps exactly its old behaviour:
    one document, on email #1 only."""
    out = []
    ids = []
    try:
        ids = [i for i in json.loads(th.get("docs_json") or "[]") if i]
    except Exception:  # noqa
        ids = []
    if not ids and int(th.get("step") or 0) == 0 and th.get("id_doc_id"):
        ids = [th["id_doc_id"]]
    for doc_id in ids:
        got = _library_doc(doc_id)
        if got:
            out.append(got)
    for a in json.loads(th.get("pending_files_json") or "[]"):
        p = attachment_path(th["gwd"], a.get("file"))
        if p:
            out.append((a.get("name") or p.name, p.read_bytes(),
                        a.get("ctype") or "application/octet-stream"))
    return out


def _schedule_next(gwd, seq, new_step):
    """After completing step new_step-1: schedule the next step (business-day
    delay inside the sequence's window) or mark the sequence exhausted."""
    steps = (seq or {}).get("steps") or []
    fields = {"step": new_step, "pending_files_json": "[]"}
    if new_step >= len(steps):
        fields["state"] = "exhausted"
        fields["next_send_at"] = None
    else:
        win = _win(seq)
        due = add_business_days(datetime.now(timezone.utc),
                                steps[new_step].get("delay_days") or 0, win)
        fields["next_send_at"] = due.isoformat(timespec="seconds")
        if thread_get(gwd).get("state") == "waiting_task":
            fields["state"] = "active"
    _thread_set(gwd, **fields)
    return fields


def send_step(gwd):
    """Run the thread's next sequence STEP (guards already passed): an
    auto_email sends the referenced template; a task pauses the thread as a
    to-do until action=task_done."""
    th = thread_get(gwd)
    if not th:
        return {"ok": False, "error": "thread not found"}
    seq = sequence_get(th.get("seq_id")) or sequence_get(default_sequence_id())
    if not seq:
        return {"ok": False, "error": "no sequence — create one in the builder"}
    steps = seq["steps"]
    step = int(th.get("step") or 0)
    if step >= len(steps):
        return {"ok": False, "error": "all steps already completed"}
    st = steps[step]
    if st["kind"] == "task":
        # a to-do, not an email: pause here until the human ticks it
        _msg_add(gwd, {"dir": "out", "kind": "task", "step": step + 1,
                       "at": now_iso(), "body": st.get("task_note") or "task",
                       "notified": True})
        _thread_set(gwd, state="waiting_task", next_send_at=None)
        return {"ok": True, "task": True, "step": step + 1}
    tpl = _template(st.get("template_id") or "") or {}
    subject = _fill(tpl.get("subject_tpl") or f"Customs clearance — {gwd}",
                    gwd, th, step + 1)
    body = _fill(tpl.get("body_tpl") or "", gwd, th, step + 1)
    if not th.get("subject"):
        _thread_set(gwd, subject=subject)          # the thread's base subject
        th["subject"] = subject
    res = _thread_send(gwd, body, _step_attachments(th), kind="sent",
                       step=step + 1, subject=subject, step_id=st.get("id"))
    if not res.get("ok"):
        return res
    template_touch(st.get("template_id"))          # it really went out → "last used"
    _schedule_next(gwd, seq, step + 1)
    return {"ok": True, "step": step + 1, **res}


def task_done(gwd):
    """Tick the current task step → the sequence continues."""
    th = thread_get(gwd)
    if not th or th.get("state") != "waiting_task":
        return {"ok": False, "error": "no task is waiting on this thread"}
    seq = sequence_get(th.get("seq_id")) or {}
    _msg_add(gwd, {"dir": "out", "kind": "task_done",
                   "step": int(th.get("step") or 0) + 1, "at": now_iso(),
                   "body": "✓", "notified": True})
    _schedule_next(gwd, seq, int(th.get("step") or 0) + 1)
    return {"ok": True, "thread": thread_get(gwd)}


def start_threads(gwds, id_doc_id, account_id, seq_id=None, names=None,
                  schedule=None, docs=None):
    """Create one thread per GWD (one GWD per email — replies map 1:1) and try
    to run step 1 immediately. `names` = {GWD: picked parcel name} pins the name
    (and therefore the ID) before email 1 goes out.

    `schedule` = {GWD: iso} holds step 1 back to that moment instead of sending
    now — the daemon picks it up when due. Spacing a batch out matters when the
    recipient is a human queue: fourteen identical emails arriving in the same
    minute read as automation and get triaged as one. Returns per-GWD results."""
    seq_id = seq_id or default_sequence_id()
    names = {str(k or "").strip().upper(): v for k, v in (names or {}).items()}
    schedule = {str(k or "").strip().upper(): v
                for k, v in (schedule or {}).items()}
    # A package's documents = its OWN (a declaration is written for one parcel
    # and must never ride another's email) + the batch-wide ones (a shared ID
    # scan, a dealer certificate). id_doc_id accepts a list or a single value.
    docs = {str(k or "").strip().upper(): (v if isinstance(v, list) else [v])
            for k, v in (docs or {}).items()}
    shared = ([d for d in id_doc_id if d] if isinstance(id_doc_id, list)
              else ([id_doc_id] if id_doc_id else []))
    out = []
    for raw in gwds or []:
        gwd = str(raw or "").strip().upper()
        if not re.match(r"GWD\d+$", gwd):
            out.append({"gwd": raw, "ok": False, "error": "not a GWD number"})
            continue
        existing = thread_get(gwd)
        if existing and existing.get("state") == "proposed":
            with db.connect() as c:                # a trigger suggestion → real
                c.execute("DELETE FROM gaash_threads WHERE gwd=?", (gwd,))
            existing = None
        if existing:
            out.append({"gwd": gwd, "ok": False, "error": "thread already exists"})
            continue
        mine = [d for d in (docs.get(gwd) or []) if d]
        pack = mine + [d for d in shared if d not in mine]     # own first, deduped
        with db.connect() as c:
            # next_send_at seeds to NOW so the sequencer retries step 1 even if
            # the immediate send below is blocked (dry-run / no account yet)
            c.execute("""INSERT INTO gaash_threads
                (gwd,account_id,state,step,id_doc_id,docs_json,unread,missing_docs,
                 pending_files_json,next_send_at,created_at,last_activity,seq_id)
                VALUES (?,?, 'active',0,?,?,0,0,'[]',?,?,?,?)""",
                      (gwd, account_id, (pack[0] if pack else None),
                       json.dumps(pack),
                       schedule.get(gwd) or datetime.now(timezone.utc)
                       .isoformat(timespec="seconds"),
                       now_iso(), now_iso(), seq_id))
        if names.get(gwd):                 # pin it BEFORE step 1 renders
            set_parcel_name(gwd, names[gwd])
        if package_terminal(gwd):
            _thread_set(gwd, state="goal_met")
            out.append({"gwd": gwd, "ok": True, "state": "goal_met",
                        "note": "already cleared/delivered — no email needed"})
            continue
        if schedule.get(gwd):              # queued — the daemon sends it
            out.append({"gwd": gwd, "ok": True, "state": "active",
                        "scheduled_at": schedule[gwd]})
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
            frm2 = parseaddr(str(msg.get("From") or ""))[1].lower()
            if frm2.startswith(("mailer-daemon@", "postmaster@")) or \
                    re.match(r"(?i)(delivery status notification|undeliverable|"
                             r"mail delivery failed)", subj):
                kind = "bounce"     # counted on the dashboard, never a reply
            else:
                kind = classify_incoming(gwd, received,
                                         int(setts.get("ack_window_min") or 0))
                if _matches_closed(setts, body_text, subj):
                    kind = "closed"  # office-closed bounce: mail was NOT received
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
                # goal 'reply' = a human answer IS the win; otherwise pause
                seq = sequence_get(th.get("seq_id"))
                upd["state"] = ("goal_met" if (seq or {}).get("goal") == "reply"
                                else "waiting_reply")
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
    """One sequencer pass: auto-enroll rules, advance due threads (guards in
    order), then poll inboxes. Safe from any worker — sends dedupe via claim_once."""
    setts = _setts()
    sent = 0
    cap = int(setts.get("daily_send_cap") or 40)
    now = datetime.now(timezone.utc)
    try:
        proposed = run_rules()
    except Exception:  # noqa - a bad rule must never stop the sends
        proposed = 0
    for th in threads_all():
        if th.get("state") != "active":
            continue
        due = _parse_iso(th.get("next_send_at") or "")
        if due is None or due > now:
            continue
        gwd = th["gwd"]
        seq = sequence_get(th.get("seq_id")) or sequence_get(default_sequence_id())
        if not seq or int(th.get("step") or 0) >= len(seq["steps"]):
            _thread_set(gwd, state="exhausted", next_send_at=None)
            continue
        # guard 0: workflow switched OFF — skip entirely (thread stays due;
        # everything resumes untouched when it's switched back ON)
        if seq.get("paused"):
            continue
        # guard 1: already cleared/delivered → the goal is met, stop
        if package_terminal(gwd):
            _thread_set(gwd, state="goal_met")
            continue
        # guard 2: an unhandled real reply → pause (belt & braces; the poll
        # already flips the state, but a manual DB edit must not slip through)
        if any(m.get("dir") == "in" and m.get("kind") == "reply"
               for m in msgs_for(gwd)) and th.get("state") == "active" \
                and int(th.get("unread") or 0) > 0:
            _thread_set(gwd, state="waiting_reply")
            continue
        # guard 3: missing docs is a separate paused state (never "active")
        # guard 4: outside the sequence's send window → defer to its next start
        # (task steps aren't emails — they may fire any time)
        st = seq["steps"][int(th.get("step") or 0)]
        if st["kind"] == "auto_email":
            win = _win(seq)
            allowed = next_allowed(now, win)
            if allowed > now.astimezone(allowed.tzinfo):
                _thread_set(gwd, next_send_at=allowed.astimezone(timezone.utc)
                            .isoformat(timespec="seconds"))
                continue
            # guard 5: dry-run — don't even claim; fires once it's off
            if setts.get("dry_run", True):
                _thread_set(gwd, last_error="dry-run is ON in Settings — nothing is sent")
                continue
            # guard 6: daily cap
            if _sent_today() + 1 > cap or sent + 1 > cap:
                break
        # guard 7: exactly-once across gunicorn workers; a FAILED send releases
        # the claim so the next pass retries (otherwise the step is lost forever)
        key = f"gaashmail:{gwd}:step{int(th.get('step') or 0) + 1}"
        if not db.claim_once(key):
            continue
        res = send_step(gwd)
        if res.get("ok"):
            if not res.get("task"):
                sent += 1
        else:
            with db.connect() as c:
                c.execute("DELETE FROM settings WHERE key=?", (key,))
    poll = check_replies() if accounts(redact=True) else {}
    return {"sent": sent, "proposed": proposed,
            **({k: poll[k] for k in ("new", "acks", "resent")
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
        # did anyone READ what we sent? the same tracking the 🧭 tiles count,
        # rolled up per parcel so the list can show it without opening a thread
        reads = {r["gwd"]: {"sent": r["n"], "opens": r["opens"] or 0,
                            "clicks": r["clicks"] or 0, "first_open_at": r["fo"]}
                 for r in c.execute(
                     "SELECT gwd, COUNT(*) n, SUM(opens) opens, SUM(clicks) clicks, "
                     "MIN(first_open_at) fo FROM gaash_msgs "
                     "WHERE dir='out' AND kind IN ('sent','resent') GROUP BY gwd")}
    pmap = parcel_name_map()            # one scan feeds every thread's name tag
    emap, smap = effective_id_map(pmap), parcel_src_map(pmap)
    bmap = parcel_board_map()
    tmap = tracking_map()
    for th in threads:
        th["last_msg"] = last.get(th["gwd"])
        th["reads"] = reads.get(th["gwd"]) or {"sent": 0, "opens": 0,
                                               "clicks": 0, "first_open_at": None}
        th.pop("pending_files_json", None)
        th["pname"] = pmap.get(th["gwd"], "")
        th["pname_id"] = emap.get(th["gwd"], "")
        th["pname_src"] = smap.get(th["gwd"], "")
        th["source"] = bmap.get(th["gwd"], "")
        # where customs has got to — the one thing the list could not tell you
        th["gash"] = tmap.get(th["gwd"]) or {}
    proposed = [t for t in threads if t.get("state") == "proposed"]
    return {"threads": [t for t in threads if t.get("state") != "proposed"],
            "proposed": proposed,
            "accounts": accounts(redact=True),
            "ids": ids_list(), "settings": _setts(),
            "sequences": sequences_list(), "templates": templates_list(),
            "rules": rules_list(), **_field_meta_out(),
            "mailer_env": bool(os.environ.get("GAASH_MAILER"))}


def _field_meta_out():
    m = rule_field_meta()
    tokens = [{"token": "{" + n + "}", "label": lab, "source": "core"}
              for n, lab in TPL_CORE_TOKENS]
    tokens += [{"token": "{" + f["label"] + "}", "label": f["label"],
                "source": f["source"]} for f in m["fields"]]
    return {"cf_fields": m["fields"], "field_values": m["values"],
            "tpl_tokens": tokens}


def autoclear_set():
    """GWDs carrying the in-app ✅ AUTO CLEAR tag (Purchases parcels — Leluxe
    parcels get tagged in ClickUp's AUTO CLEAR column instead)."""
    try:
        with db.connect() as c:
            return {r["gwd"] for r in c.execute("SELECT gwd FROM gaash_autoclear")}
    except Exception:  # noqa
        return set()


def autoclear_toggle(gwd, on):
    g = (gwd or "").strip().upper()
    if not re.match(r"GWD\d+$", g):
        return {"ok": False, "error": "not a GWD number"}
    with db.connect() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO gaash_autoclear(gwd,at) "
                      "VALUES(?,?)", (g, now_iso()))
        else:
            c.execute("DELETE FROM gaash_autoclear WHERE gwd=?", (g,))
    return {"ok": True, "gwd": g, "on": bool(on)}


def candidates(include_enrolled=False):
    """Every enrollable GWD — from the Leluxe mirror AND the Purchases board —
    that isn't already delivered. Each row is tagged with its `source` so the
    picker can show ⌚ Leluxe vs 📦 Purchases.

    By default only parcels with NO thread yet — that is what the auto-rules
    and their ⚡ match counters must see. The 📧 enroll picker passes
    include_enrolled=True: parcels already in a workflow stay VISIBLE there,
    tagged with their thread state, so the owner can tell at a glance which
    tracking numbers are covered and which still need sending."""
    thmap = {t["gwd"]: t for t in threads_all()}
    have = set() if include_enrolled else set(thmap)
    out, seen = [], set()
    # ── Leluxe mirror (carries the GASH STATUS field) ──
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
        # every ClickUp field becomes a filterable custom column (cf:<name>)
        cf = {str(k).strip(): _cf_stringify(v) for k, v in f.items()
              if str(k).strip() and _cf_stringify(v)}
        out.append({"gwd": tn, "source": "leluxe",
                    "name": (names.get(r["parent_local_id"]) or r["name"] or "")[:70],
                    "status": r["status"], "gash_status": gash,
                    "bucket": _bucket(ts) or None,
                    "label": (ts.get("label") if isinstance(ts, dict)
                              else (str(ts)[:40] if ts else None)),
                    "cf": cf})
    # ── Purchases board packages (Otlobly customer POs — no ClickUp mirror) ──
    try:
        import purchases
        pos = (purchases.load() or {}).get("purchase_orders") or []
    except Exception:  # noqa
        pos = []
    defs = _cf_defs()
    for p in pos:
        # the PO's custom-field values become filterable columns (labelled)
        pcf = {}
        for dfn in defs:
            val = _cf_stringify((p.get("custom") or {}).get(dfn.get("key")))
            lab = str(dfn.get("label") or "").strip()
            if lab and val:
                pcf[lab] = val
        for pk in (p.get("packages") or []):
            tn = str(pk.get("tracking_number") or "").strip().upper()
            if not re.match(r"GWD\d+$", tn) or tn in seen or tn in have:
                continue
            if _bucket(pk.get("tracking_status")) in _TERMINAL or \
                    _bucket(pk.get("gerizim_status")) == "delivered":
                continue
            st = str(pk.get("otlobly_status") or "")
            if re.search(r"deliver|complete|collect", st, re.I):
                continue
            seen.add(tn)
            custs = sorted({(it.get("customer_name") or "").strip()
                            for it in (pk.get("items") or [])
                            if (it.get("customer_name") or "").strip()})
            # Purchases has no ClickUp GASH STATUS field, so its customs position
            # comes from the STORED tracking status — the same value the filter
            # above already read. Keeping it costs nothing and asks no carrier:
            # it is whatever the last normal refresh wrote.
            pts = pk.get("tracking_status")
            out.append({"gwd": tn, "source": "purchases",
                        "name": (p.get("ship_to") or "").strip()[:70],
                        "status": pk.get("otlobly_status"), "gash_status": None,
                        "customers": "، ".join(custs)[:70],
                        "bucket": _bucket(pts) or None,
                        "label": (pts.get("label") if isinstance(pts, dict)
                                  else str(pts)[:40] if pts else None),
                        "po_id": p.get("po_id"), "cf": dict(pcf)})
    # the name each parcel ships under + the ID mapped to it — the picker shows
    # both so the owner can see, before sending, which ID each email will carry
    pmap = parcel_name_map()
    emap, smap = effective_id_map(pmap), parcel_src_map(pmap)
    ac = autoclear_set()                 # the in-app ✅ tag, filterable in rules
    for cd in out:
        cd["pname"] = pmap.get(cd["gwd"], "")
        cd["pname_id"] = emap.get(cd["gwd"], "")
        cd["pname_src"] = smap.get(cd["gwd"], "")
        cd["autoclear"] = "1" if cd["gwd"] in ac else ""
        th = thmap.get(cd["gwd"])
        cd["thread"] = (None if not th else
                        {"state": th.get("state"), "step": th.get("step") or 0,
                         "seq_id": th.get("seq_id")})

    # what still NEEDS enrolling first, then suggestions, then already-running;
    # inside each group Leluxe first (has clearance status), then by GWD
    def _grp(cd):
        t = cd.get("thread")
        return 0 if not t else (1 if t["state"] == "proposed" else 2)
    out.sort(key=lambda x: (_grp(x), x["source"] != "leluxe", x["gwd"]))
    return out


def readiness():
    """🩺 one table that answers, per parcel, BEFORE anything sends: what name
    it ships under (and who says so), which ID the email would carry, whether
    it's tagged for auto-clear (ClickUp column or in-app ✅), and whether a
    conversation already exists. Rows = enrollable candidates + every thread."""
    ths = threads_all()
    pmap = parcel_name_map()
    emap, smap = effective_id_map(pmap), parcel_src_map(pmap)
    bmap = parcel_board_map()
    ac = autoclear_set()
    acf = _fold("AUTO CLEAR")
    rows = []
    for cd in candidates():
        cf = cd.get("cf") or {}
        cu = next((str(v) for k, v in cf.items() if _fold(k) == acf), "")
        rows.append({"gwd": cd["gwd"], "source": cd.get("source") or "",
                     "status": cd.get("gash_status") or cd.get("status") or "",
                     "pname": cd.get("pname") or "",
                     "pname_src": cd.get("pname_src") or "",
                     "pname_id": cd.get("pname_id") or "",
                     "app_tag": cd["gwd"] in ac, "cu_tag": cu,
                     "state": "", "who": cd.get("name") or cd.get("customers") or ""})
    for th in ths:
        g = th["gwd"]
        rows.append({"gwd": g, "source": bmap.get(g, ""),
                     "status": "", "pname": pmap.get(g, ""),
                     "pname_src": smap.get(g, ""),
                     "pname_id": emap.get(g, ""),
                     "app_tag": g in ac, "cu_tag": "",
                     "state": th.get("state") or "", "who": ""})
    # blocked-and-untouched first — that's the pile the owner came to fix
    rows.sort(key=lambda r: (bool(r["state"]), bool(r["pname_id"]), r["gwd"]))
    return {"ok": True, "rows": rows}


_GASH_FK = None


def rule_field_meta():
    """The trigger builder's field catalog AND value suggestions, in ONE scan.

    fields: [{key,label,source}] every board custom column — Purchases defs (📦)
      + distinct Leluxe ClickUp field names (⌚), deduped by folded label.
    values: {field_key: [distinct values ≤50]} pulled from ALL records (not just
      the enrollable candidates) PLUS Purchases select/labels defined options —
      so the value box suggests what we already have instead of blank typing.
    """
    global _GASH_FK
    if _GASH_FK is None:
        _GASH_FK = _fold("GASH STATUS")
    fields, seen = [], {}             # seen: folded label -> catalog entry
    vals = {}                         # canonical field key -> set() of values

    def add_val(key, v):
        v = _cf_stringify(v).strip()
        if not v:
            return
        s = vals.setdefault(key, set())
        if len(s) < 50:
            s.add(v)

    # ── Purchases custom-field DEFINITIONS (📦) + their option names ──
    defs = _cf_defs()
    for dfn in defs:
        lab = str(dfn.get("label") or "").strip()
        if not lab:
            continue
        fk = _fold(lab)
        if fk not in seen:
            e = {"key": "cf:" + lab, "label": lab, "source": "purchases"}
            seen[fk] = e
            fields.append(e)
        for opt in (dfn.get("options") or []):
            add_val(seen[fk]["key"], opt.get("name") if isinstance(opt, dict) else opt)

    # ── Leluxe mirror: field names (⌚) + every observed value ──
    try:
        with db.connect() as c:
            rows = c.execute("SELECT status, data_json FROM leluxe_orders "
                             "WHERE deleted=0").fetchall()
    except Exception:  # noqa
        rows = []
    for r in rows:
        add_val("status", r["status"])
        try:
            fdict = json.loads(r["data_json"] or "{}").get("fields") or {}
        except Exception:  # noqa
            continue
        for nm, v in fdict.items():
            lab = str(nm).strip()
            if not lab:
                continue
            fk = _fold(lab)
            if fk == _GASH_FK:
                add_val("gash_status", v)
            if fk not in seen:
                if len(fields) >= 80:        # catalog ceiling for the dropdown
                    continue
                e = {"key": "cf:" + lab, "label": lab, "source": "leluxe"}
                seen[fk] = e
                fields.append(e)
            elif seen[fk]["source"] == "purchases":
                seen[fk]["source"] = "both"
            add_val(seen[fk]["key"], v)

    # ── Purchases packages: custom values (📦) + statuses ──
    try:
        import purchases
        pos = (purchases.load() or {}).get("purchase_orders") or []
    except Exception:  # noqa
        pos = []
    defs_by_key = {d.get("key"): d for d in defs}
    for p in pos:
        for k, v in (p.get("custom") or {}).items():
            lab = str((defs_by_key.get(k) or {}).get("label") or k or "").strip()
            fk = _fold(lab)
            if fk in seen:
                add_val(seen[fk]["key"], v)
        for pk in (p.get("packages") or []):
            add_val("status", pk.get("otlobly_status"))

    vals.setdefault("autoclear", set()).add("1")   # the in-app ✅ tag's only value
    return {"fields": fields,
            "values": {k: sorted(s) for k, s in vals.items() if s}}


def rule_cf_fields():
    return rule_field_meta()["fields"]


def thread_detail(gwd):
    th = thread_get(gwd)
    if not th:
        return None
    msgs = msgs_for(gwd)
    for m in msgs:
        m["attachments"] = json.loads(m.pop("attachments_json") or "[]")
    # the effective name/ID (pick → board), so the chat header shows what the
    # next email will actually carry
    th["pname"] = parcel_name(gwd)
    th["pname_id"] = id_number_for_email(gwd)
    th["pname_src"] = parcel_name_src(gwd)
    th["source"] = parcel_board_map().get(gwd, "")
    th["gash"] = tracking_map().get(gwd) or {}
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
