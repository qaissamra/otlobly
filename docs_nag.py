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

And a fifth, the owner's own: «done» (or «تم») in the 🚩 flags bot's chat stops
today's reminders for every parcel already nagged today (ack_today).

WHO SPEAKS — the 2026-09-24 silence. That day two IT parcels (GWD004803687 +
GWD004803612, Jihad) sat in candidates() from the 04:20 sweep to midnight, and
not one message went out: Render had the owner's chat id but NOT the alerts
bot's token, so run_once returned [] on its first line — no send, no stamp, no
log line — while the 🚩 flags bot, configured on Render and delivering
that same week, was never asked. By 03:30 the next morning both GAASH pages
read «פג תוקף הקישור». (The link IS open on its last day: GWD004803241's
papers went in at 16:34 on its deadline day, 2026-09-22.) So now
every bot that can reach the owner is a channel (channels(): the flags bot
first, because its reply loop is what makes «done» work, then the alerts bot),
tried in order until one delivers; and an alarm that cannot speak SAYS so —
a loud log line every tick, docs:nag_health, status()["mute"], and a standing
🔕 item on the bell (bell_items). A quiet alarm must never look like a quiet day.

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
HEALTH_KEY = "docs:nag_health"   # the last delivery attempt: {ok, why, channel, at}
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
# Who can reach the owner — every bot is a channel, and silence is reported
# --------------------------------------------------------------------------- #
def _flags_ready():
    try:
        import flag_machine
        return flag_machine.flags_configured()
    except Exception:  # noqa: BLE001 — a broken flags module must not mute the alerts bot
        return False


def done_loop_on():
    """«done» is only a promise where the flags bot's reply loop runs (the
    flag machine's daemon, env FLAG_MACHINE — Render)."""
    return (os.environ.get("FLAG_MACHINE") or "").strip() not in ("", "0")


def channels():
    """[(name, send)] — every bot that can reach the owner right now, best first.

    The 🚩 flags bot leads: it is his action-required bot, the one that already
    nags until «done», and its reply loop is what lets «done» stop this nag
    too. The alerts bot follows as the fallback. Each send is looked up at call
    time, so a test's stand-in for telegram.send/send_to is honoured."""
    out = []
    if _flags_ready():
        import flag_machine
        tok, chat = flag_machine._flags_token(), flag_machine._flags_chat()
        out.append(("flags", lambda txt: telegram.send_to(chat, txt, token=tok)))
    if telegram.configured():
        out.append(("alerts", lambda txt: telegram.send(txt)))
    return out


def mute_reason(chans=None):
    """'' while at least one bot can reach the owner; otherwise WHAT is missing,
    by name — "not configured" once cost the flag machine an afternoon."""
    if channels() if chans is None else chans:
        return ""
    miss = []
    tok, chat = telegram._creds()
    if not tok:
        miss.append("TELEGRAM_BOT_TOKEN")
    if not chat:
        miss.append("TELEGRAM_CHAT_ID")
    try:
        import flag_machine
        m = flag_machine.flags_missing()
        if m:
            miss.append(m)
    except Exception:  # noqa: BLE001
        miss.append("the flags bot")
    return "no Telegram bot can reach the owner — missing " + ", ".join(dict.fromkeys(miss))


def _shout(line):
    """A log line nobody can miss, flushed — a buffered warning is a silent one."""
    print(f"docs_nag: 🚨 {line}", flush=True)


def _note_health(ok, why="", channel=""):
    """Remember how the last delivery attempt went — only when it CHANGES, so a
    loud day does not rewrite the row every tick. The bell reads it: a token
    that dies after deploy is invisible to channels(), but not to this."""
    cur = db.get_setting(HEALTH_KEY)
    cur = cur if isinstance(cur, dict) else {}
    if cur.get("ok") is ok and cur.get("why", "") == why and cur.get("channel", "") == channel:
        return
    db.set_setting(HEALTH_KEY, {"ok": ok, "why": why, "channel": channel,
                                "at": db.now_iso()})


def _deliver(text_for, chans):
    """Try each channel until one delivers → (channel, text it took, errors of
    the bots that failed first) — or (None, "", errors) when nobody took it."""
    errs = []
    for name, fn in chans:
        txt = text_for(name)
        try:
            r = fn(txt) or {}
        except Exception as e:  # noqa: BLE001 — one bot failing must not stop the next
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            return name, txt, errs
        errs.append(f"{name}: {str(r.get('error') or 'not ok')[:160]}")
    return None, "", errs


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
        if cur.get("date") == today_iso and cur.get("done"):
            return False                     # he said «done» — inside the same lock
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
        if cur.get("date") != today_iso:
            cur = {}
        # dict(cur, …) keeps a «done» that landed between the claim and the send
        cur = dict(cur, date=today_iso, at=cur.get("at") or db.now_iso(),
                   n=int(cur.get("n") or 0) + 1)
        m[gwd] = cur
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (SENT_KEY, json.dumps(m, ensure_ascii=False)))
        return cur["n"]


def acked(gwd, today_iso):
    cur = _stamps().get(gwd)
    return isinstance(cur, dict) and cur.get("date") == today_iso and bool(cur.get("done"))


def ack_today(today=None):
    """«done»/«تم» in the flags bot's chat → stop today's reminders for every
    parcel that was actually nagged today. Returns those GWDs. A parcel whose
    first reminder has not gone out yet keeps its alarm: «done» answers what he
    saw, not what he never heard about."""
    import json
    iso = (today or _today()).isoformat()
    got = []
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM settings WHERE key=?", (SENT_KEY,)).fetchone()
        try:
            m = json.loads(r["value"]) if r else {}
        except (ValueError, TypeError):
            m = {}
        if not isinstance(m, dict):
            return []
        for gwd, v in m.items():
            if isinstance(v, dict) and v.get("date") == iso and int(v.get("n") or 0) > 0 \
                    and not v.get("done"):
                v["done"] = db.now_iso()
                got.append(gwd)
        if got:
            c.execute("UPDATE settings SET value=? WHERE key=?",
                      (json.dumps(m, ensure_ascii=False), SENT_KEY))
    return sorted(got)


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


def message(gwd, r, n=1, done_hint=False):
    """Arabic, because the owner reads these on his phone at speed. done_hint:
    the text is going out through the flags bot, whose «done» stops it."""
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
            f"{every_min()} دقائق لحد ما ترفع المستندات"
            + (" أو ترد «تم» هنا · reply done to stop." if done_hint else "."))


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_once(send=None, check=None, today=None, rows=None):
    """One nag pass → the texts actually sent. Safe to call from anywhere.
    `send` (tests, manual runs) replaces the channel list with that one sink."""
    if not enabled():
        return []
    chans = [("sink", send)] if send else channels()
    today = today or _today()
    iso = today.isoformat()
    todo = [(g, r) for g, r in candidates(today=today, rows=rows) if not acked(g, iso)]
    if not chans:
        # 2026-09-24: this used to be a bare `return []`. Two links died behind it.
        if todo:
            why = mute_reason(chans)
            _note_health(False, why)
            _shout(f"MUTE — {len(todo)} parcel(s) lose their GAASH link TODAY "
                   f"({', '.join(g for g, _ in todo)}) and {why}")
        return []
    gap = every_min() * 60 - 30      # 30 s of slack: a tick that runs a hair
    sent = []                        # early must not skip the whole round
    if check is None:
        def check(tn):
            import docs_roster
            return docs_roster.check(tn, with_tracking=False)
    for gwd, r in todo:
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
        hint = done_loop_on()
        by, txt, errs = _deliver(
            lambda ch: message(gwd, r, n, done_hint=hint and ch == "flags"), chans)
        if by:
            sent.append(txt)
            _note_health(True, "; ".join(errs), by)    # errs = a bot that failed first
        else:
            _note_health(False, "every bot failed — " + "; ".join(errs))
            _shout(f"reminder {n} for {gwd} reached NOBODY — " + "; ".join(errs))
    return sent


# --------------------------------------------------------------------------- #
# Daemon
# --------------------------------------------------------------------------- #
_started = False
_BOOT_TS = datetime.now().astimezone().isoformat(timespec="seconds")


def _loop():
    time.sleep(90)                   # let the app boot (and alerts go first)
    # one line per deploy, so the log says whether this alarm can speak BEFORE
    # the only day it matters — 2026-09-24 had no such line, and no voice
    try:
        why = mute_reason()
        if why:
            _shout(f"armed but MUTE — {why}")
        else:
            print("docs_nag: armed — speaks through "
                  + " → ".join(n for n, _ in channels()), flush=True)
    except Exception as e:  # noqa: BLE001
        _shout(f"boot check failed ({e})")
    while True:
        try:
            with memlog.watch("docs_nag"):
                out = run_once()
            if out:
                print(f"docs_nag: sent {len(out)} last-day nag(s)", flush=True)
        except Exception as e:  # noqa: BLE001 — never let the thread die
            _shout(f"pass failed ({e})")
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
    """What the owner sees in Settings: is it armed, who speaks, and who is it
    watching. `telegram` is the alerts bot alone (kept for old readers);
    `channels`/`mute` are what decide whether a message can go out at all."""
    try:
        cands = [g for g, _r in candidates()]
    except Exception:  # noqa: BLE001
        cands = []
    return {"env_on": env_on(), "enabled": enabled(), "every_min": every_min(),
            "telegram": telegram.configured(),
            "channels": [n for n, _ in channels()], "mute": mute_reason(),
            "done_reply": done_loop_on(), "health": db.get_setting(HEALTH_KEY),
            "today": _today().isoformat(), "watching": cands, "sent_today": _stamps()}


def bell_items():
    """Standing 🔕 items for the bell on the host that nags (env DOCS_NAG) —
    an alarm that cannot speak has to say so where the owner already looks."""
    if not env_on():
        return []
    out = []
    chans = channels()                   # once — every lookup re-reads config.json
    why = mute_reason(chans)
    h = db.get_setting(HEALTH_KEY)
    h = h if isinstance(h, dict) else {}
    if why:
        out.append({"ts": _BOOT_TS, "type": "alarm_mute", "icon": "🔕",
                    "title": "منبّه غاش ما بيقدر يبعت تلغرام · the GAASH deadline alarm can't reach Telegram",
                    "sub": why, "view": "gaashmail"})
    elif h.get("ok") is False:
        out.append({"ts": h.get("at") or _BOOT_TS, "type": "alarm_mute", "icon": "🔕",
                    "title": "آخر تذكير غاش ما وصلك · the last GAASH alarm message never arrived",
                    "sub": (h.get("why") or "")[:160], "view": "gaashmail"})
    if not any(n == "alerts" for n, _ in chans):
        out.append({"ts": _BOOT_TS, "type": "alerts_bot_off", "icon": "🔕",
                    "title": "بوت التنبيهات مش موصول على السيرفر · alerts bot not connected on the server",
                    "sub": "TELEGRAM_BOT_TOKEN missing — the Otlobly 7/3/1 countdown and the "
                           "database-repair notices can't send",
                    "view": "settings"})
    return out


if __name__ == "__main__":
    import json as _json
    import sys
    if "--send" in sys.argv:
        print(_json.dumps(run_once(), ensure_ascii=False, indent=1))
    else:
        print(_json.dumps(status(), ensure_ascii=False, indent=1))
