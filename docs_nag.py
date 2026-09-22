#!/usr/bin/env python3
"""
⏰ The last-day nag — Telegram every 10 minutes while a GAASH upload link DIES TODAY.

Why this exists, in one parcel: GWD004802571 (Le Luxe, FAISAL) arrived 2026-08-16,
GAASH asked for a customer ID from 2026-08-10 and re-asked five times, and its
upload link expired 2026-09-20. Nobody was told on the day. On 2026-09-22 their
page answered «פג תוקף הקישור» — the link has expired — and takes no file for
that parcel from anyone, ever. The watches inside are now GAASH's problem to
abandon, not ours to clear.

alerts.py already counts down 7/3/1 days, but it walks purchases.json ONLY, so
no Le Luxe or IT parcel has ever produced a single deadline alert. This module
covers ALL THREE boards, and it is deliberately loud on exactly one day: the day
the link closes, every `every_min` minutes, until the parcel stops asking or the
day is over. A nag you can ignore for a week gets ignored; a nag that only ever
fires on the last day is an alarm.

It stops by itself — four ways, checked in this order:

  1. we have a hand-over stamp (gaash_mail.clearance_get → sent_at): the owner
     uploaded or emailed the papers, so his part is done. A GAASH status that
     lags behind that is not his to be nagged about.
  2. GAASH, asked LIVE this minute, no longer wants papers (state != "action").
  3. the link already closed (tracking's "closed" state) — nagging someone
     toward a sealed door is what this whole feature exists to stop.
  4. the Amman day rolled past the deadline.

A live re-check per tick is what makes 1-3 honest. It costs one GAASH call per
qualifying parcel, and on almost every day of the year there are ZERO.

Gated by env DOCS_NAG=1 so only ONE host nags (Render), like FLAG_MACHINE —
the Mac's launchd copy reads a stale DB and would double every message.
Settings: docs_nag.enabled (kill switch) and docs_nag.every_min.
"""

import os
import threading
import time
from datetime import datetime, timedelta

import cfg
import db
import memlog
import telegram

SENT_KEY = "docs:nag_sent"
EVERY_MIN_DEFAULT = 10
MIN_EVERY_MIN = 2          # a floor: this bot can be loud, not a flood
STATE_ACTION = "action"


def _setts(c=None):
    c = c if isinstance(c, dict) else cfg.load()
    s = cfg.get(c, "docs_nag", {}) or {}
    return s if isinstance(s, dict) else {}


def enabled(c=None):
    """Settings kill switch (default ON). The env gate is separate and decides
    WHICH HOST nags; this decides whether anyone does."""
    s = _setts(c)
    return bool(s.get("enabled", True))


def every_min(c=None):
    try:
        return max(MIN_EVERY_MIN, int(_setts(c).get("every_min") or EVERY_MIN_DEFAULT))
    except (TypeError, ValueError):
        return EVERY_MIN_DEFAULT


def env_on():
    return (os.environ.get("DOCS_NAG") or "").strip() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Who is dying today — across ALL THREE boards
# --------------------------------------------------------------------------- #
def _state_of(d):
    st = d.get("docs_state")
    return (st.get("state") or "") if isinstance(st, dict) else ""


def _roster_rows():
    """Le Luxe + IT, from the local roster table (the same read the bell polls)."""
    try:
        import docs_roster
        return docs_roster.rows() or {}
    except Exception:  # noqa: BLE001 — one board failing must not mute the others
        return {}


def _purchase_rows():
    """Otlobly's own packages, keyed like the roster so one loop covers both."""
    out = {}
    try:
        import purchases
        pos = (purchases.load() or {}).get("purchase_orders") or []
    except Exception:  # noqa: BLE001
        return out
    for po in pos:
        for pk in po.get("packages") or []:
            gwd = (pk.get("tracking_number") or "").strip().upper()
            if not gwd:
                continue
            who = next(((it.get("customer_name") or "").strip()
                        for it in pk.get("items") or []
                        if (it.get("customer_name") or "").strip()), "")
            out[gwd] = {"open": True, "source": "purchases", "data": pk,
                        "label": f"{po.get('po_id') or ''} 📦{pk.get('package_no') or ''}"
                                 + (f" · {who}" if who else "")}
    return out


def _name_of(gwd, r):
    d = r.get("data") or {}
    if r.get("label"):
        return r["label"]
    cu = r.get("cu") or {}
    who = (cu.get("name") or d.get("name") or "").strip()
    order = ((cu.get("order") or {}).get("name") or "").strip()
    bits = [b for b in (who, order) if b]
    return " · ".join(bits) if bits else gwd


def _today():
    try:
        import docs_roster
        return docs_roster.amman_today()
    except Exception:  # noqa: BLE001 — Jordan is fixed UTC+3
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=3))).date()


def candidates(today=None, rows=None):
    """[(gwd, row)] whose GAASH link expires TODAY and that GAASH still asks about.

    Stored state only — this is the cheap gate that runs every tick. The live
    confirmation happens once per survivor, in run_once."""
    today = today or _today()
    iso = today.isoformat()
    if rows is None:
        rows = dict(_roster_rows())
        for gwd, r in _purchase_rows().items():
            rows.setdefault(gwd, r)          # roster wins: it carries the ClickUp face
    out = []
    for gwd, r in sorted(rows.items()):
        if not r.get("open", True):
            continue
        d = r.get("data") or {}
        if str(d.get("gaash_deadline") or "")[:10] != iso:
            continue
        if _state_of(d) != STATE_ACTION:
            continue
        out.append((gwd, r))
    return out


def handed_over(gwd):
    """True once ANY send path stamped this parcel — upload, link or email."""
    try:
        import gaash_mail
        return bool((gaash_mail.clearance_get(gwd) or {}).get("sent_at"))
    except Exception:  # noqa: BLE001 — a missing record must not mute the alarm
        return False


# --------------------------------------------------------------------------- #
# The send slot — two gunicorn workers must not nag twice
# --------------------------------------------------------------------------- #
def _stamps():
    s = db.get_setting(SENT_KEY) or {}
    return s if isinstance(s, dict) else {}


def _claim(gwd, today_iso, gap_s):
    """Take this parcel's slot for the next `gap_s` seconds, in ONE immediate
    transaction. Both workers run the same loop; the first to BEGIN IMMEDIATE
    wins and the second sees a fresh stamp and stands down. Claiming BEFORE the
    GAASH call (not after the send) is what keeps two workers from both
    spending 25 s on the same parcel. A claimed slot that ends in no message
    simply lapses — the next tick re-decides."""
    import json
    ok = False
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM settings WHERE key=?", (SENT_KEY,)).fetchone()
        try:
            m = json.loads(r["value"]) if r else {}
        except (ValueError, TypeError):
            m = {}
        if not isinstance(m, dict):
            m = {}
        cur = m.get(gwd) if isinstance(m.get(gwd), dict) else {}
        fresh = False
        if cur.get("date") == today_iso and cur.get("at"):
            try:
                age = (datetime.now(datetime.fromisoformat(cur["at"]).tzinfo)
                       - datetime.fromisoformat(cur["at"])).total_seconds()
                fresh = age < gap_s
            except (TypeError, ValueError):
                fresh = False
        if not fresh:
            ok = True
            m[gwd] = {"date": today_iso, "at": db.now_iso(),
                      "n": int(cur.get("n") or 0) if cur.get("date") == today_iso else 0}
            # yesterday's parcels are not interesting and must not grow forever
            m = {k: v for k, v in m.items()
                 if isinstance(v, dict) and v.get("date") == today_iso}
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (SENT_KEY, json.dumps(m, ensure_ascii=False)))
    return ok


def _count_sent(gwd, today_iso):
    """Bump the visible counter after a message really went out."""
    import json
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM settings WHERE key=?", (SENT_KEY,)).fetchone()
        try:
            m = json.loads(r["value"]) if r else {}
        except (ValueError, TypeError):
            m = {}
        if not isinstance(m, dict):
            m = {}
        cur = m.get(gwd) if isinstance(m.get(gwd), dict) else {}
        cur = {"date": today_iso, "at": cur.get("at") or db.now_iso(),
               "n": int(cur.get("n") or 0) + 1}
        m[gwd] = cur
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (SENT_KEY, json.dumps(m, ensure_ascii=False)))
        return cur["n"]


# --------------------------------------------------------------------------- #
# The message
# --------------------------------------------------------------------------- #
def _asked_for(d):
    """GAASH's own upload slots, named, so the message says WHICH paper."""
    st = d.get("docs_state")
    links = (st.get("links") or []) if isinstance(st, dict) else []
    names = []
    for lk in links:
        lbl = (lk.get("label") or "").strip()
        if lbl and lbl not in names:
            names.append(lbl)
    return " + ".join(names)


def message(gwd, r, n=1):
    """Arabic, because the owner reads these on his phone at speed."""
    d = r.get("data") or {}
    who = _name_of(gwd, r)
    what = _asked_for(d)
    src = {"leluxe": "لي لوكس", "it": "IT", "purchases": "أوتلوبلي"}.get(
        str(r.get("source") or "").lower(), "")
    return ("🚨 اليوم آخر يوم لرفع مستندات غاش!\n"
            f"{gwd}" + (f" · {who}" if who else "") + (f" · {src}" if src else "") + "\n"
            + (f"المطلوب: {what}\n" if what else "")
            + f"ينتهي الرابط اليوم ({d.get('gaash_deadline')}) — بعده غاش "
              "ما بيستقبل أي ملف لهذا الطرد نهائياً.\n"
            f"🔔 تذكير رقم {n} — بيضل يوصلك كل "
            f"{every_min()} دقائق لحد ما ترفع المستندات.")


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_once(send=telegram.send, check=None, today=None, rows=None):
    """One nag pass → the texts actually sent. Safe to call from anywhere."""
    if not enabled():
        return []
    if not telegram.configured():
        return []
    today = today or _today()
    iso = today.isoformat()
    gap = every_min() * 60 - 30      # 30 s of slack: a tick that runs a hair
    sent = []                        # early must not skip the whole round
    if check is None:
        def check(tn):
            import docs_roster
            return docs_roster.check(tn, with_tracking=False)
    for gwd, r in candidates(today=today, rows=rows):
        if handed_over(gwd):
            continue                                   # stop 1: his part is done
        if not _claim(gwd, iso, gap):
            continue                                   # another worker has this slot
        try:
            live = check(gwd)
        except Exception as e:  # noqa: BLE001 — their site is not our alarm's problem
            print(f"[docs_nag] live check for {gwd} failed ({e})", flush=True)
            live = None
        if isinstance(live, dict):
            state = (live.get("state") or "").strip()
            if state != STATE_ACTION:
                continue                               # stop 2/3: GAASH stopped asking
            r = dict(r, data=dict(r.get("data") or {}, docs_state=live))
        # live is None → the LOOKUP failed (their site down). On the last day we
        # nag anyway: a silent alarm is exactly the failure this module exists
        # to end, and the stored state still says GAASH is asking.
        n = _count_sent(gwd, iso)
        txt = message(gwd, r, n)
        if send(txt).get("ok"):
            sent.append(txt)
    return sent


# --------------------------------------------------------------------------- #
# Daemon
# --------------------------------------------------------------------------- #
_started = False


def _loop():
    time.sleep(90)                   # let the app boot (and alerts go first)
    while True:
        try:
            with memlog.watch("docs_nag"):
                out = run_once()
            if out:
                print(f"docs_nag: sent {len(out)} last-day nag(s)")
        except Exception as e:  # noqa: BLE001 — never let the thread die
            print(f"docs_nag: pass failed ({e})")
        time.sleep(max(MIN_EVERY_MIN, every_min()) * 60)


def start():
    """Start the last-day nagger once. Env-gated so exactly ONE host nags."""
    global _started
    if _started or not env_on():
        return False
    _started = True
    threading.Thread(target=_loop, name="otlobly-docs-nag", daemon=True).start()
    return True


def status():
    """What the owner sees in Settings: is it armed, and who is it watching."""
    try:
        cands = [g for g, _r in candidates()]
    except Exception:  # noqa: BLE001
        cands = []
    return {"env_on": env_on(), "enabled": enabled(), "every_min": every_min(),
            "telegram": telegram.configured(), "today": _today().isoformat(),
            "watching": cands, "sent_today": _stamps()}


if __name__ == "__main__":
    import json as _json
    import sys
    if "--send" in sys.argv:
        print(_json.dumps(run_once(), ensure_ascii=False, indent=1))
    else:
        print(_json.dumps(status(), ensure_ascii=False, indent=1))
