#!/usr/bin/env python3
"""
Self-checks for the last-day nag (docs_nag.py).

The parcel that bought this suite: GWD004802571 (Le Luxe, FAISAL). GAASH asked
for a customer ID five times, its upload link expired 2026-09-20, and NOT ONE
Telegram message was ever sent about it — because alerts.py walks purchases.json
only, and that parcel lives on the Le Luxe board. On 2026-09-22 GAASH's page
answered «פג תוקף הקישור» and the watches inside became unreachable.

The contract under test:
  · a parcel is nagged ONLY on its deadline day, and only while GAASH asks
  · ALL THREE boards count — Le Luxe, IT and Purchases (the original gap)
  · a hand-over stamp (uploaded/emailed) stops it: his part is done
  · a LIVE re-check that says "GAASH stopped asking" stops it within one tick
  · a live check that FAILS still nags — a silent alarm is the bug, not a feature
  · two gunicorn workers produce ONE message per slot, not two
  · the slot reopens after every_min, and the reminder is numbered
  · the settings kill switch mutes everything
  · yesterday's stamps are pruned, so the row cannot grow forever

And the part that bought its second half — 2026-09-24, GWD004803687 + GWD004803612
(IT, Jihad): both sat in candidates() all day while Render had the owner's chat
id but NO alerts-bot token, and run_once returned [] on its first line. Every
check above passed that day, because main() stubs telegram.configured → True.
So the "who speaks" section below runs on the REAL credential lookup:
  · with only the 🚩 flags bot configured (Render's exact config) the nag goes out
  · a bot that fails hands the message to the next one
  · no bot at all → nothing sent, but a loud log line, a health record, a named
    reason and a 🔕 bell item — never silence that looks like a quiet day
  · «done» in the flags chat stops today's reminders (and only those he heard)

    ./.venv/bin/python test_docs_nag.py
"""

import contextlib
import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-nag-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ.pop("DOCS_NAG", None)
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FLAGS_BOT_TOKEN", "FLAGS_CHAT_ID",
           "FLAG_MACHINE"):
    os.environ.pop(_k, None)                 # the machine's own bots must not leak in

import db                                    # noqa: E402
db.init_db()
import cfg                                   # noqa: E402
import docs_nag                              # noqa: E402
import flag_machine                          # noqa: E402
import telegram                              # noqa: E402

REAL_CONFIGURED = telegram.configured

OK = FAIL = 0
TODAY = date(2026, 9, 20)
ISO = TODAY.isoformat()


def check(label, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}" + (f"\n      {extra}" if extra else ""))


def row(deadline, state="action", source="leluxe", open_=True, links=None, name="FAISAL"):
    ds = {"state": state, "arrived": True,
          "links": links if links is not None else [{"label": "Upload Document"}]}
    return {"open": open_, "source": source,
            "cu": {"name": name, "order": {"name": "Order # 112-9281272-5945009"}},
            "data": {"gaash_deadline": deadline, "docs_state": ds}}


class Sink:
    """A telegram.send stand-in that records instead of sending."""

    def __init__(self, ok=True):
        self.texts, self.ok = [], ok

    def __call__(self, text, *a, **k):
        self.texts.append(text)
        return {"ok": self.ok}


def reset():
    db.set_setting(docs_nag.SENT_KEY, {})
    cfg.save({}) if hasattr(cfg, "save") else None


def _wipe_stamps():
    db.set_setting(docs_nag.SENT_KEY, {})


def still(_tn):
    return {"state": "action", "links": [{"label": "Upload Document"}]}


def stopped(_tn):
    return {"state": "info", "links": []}


def boom(_tn):
    raise RuntimeError("gaash is down")


class Wire:
    """Stands in for BOTH Telegram entry points — the alerts bot (telegram.send)
    and the flags bot (telegram.send_to) — and records who carried what."""

    def __init__(self):
        self.got, self.ok = [], {"alerts": True, "flags": True}

    def send(self, text, token=None):
        self.got.append(("alerts", text))
        return {"ok": self.ok["alerts"], "error": "alerts bot down"}

    def send_to(self, chat, text, token=None):
        self.got.append(("flags", text, chat, token))
        return {"ok": self.ok["flags"], "error": "flags bot down"}

    def via(self):
        return [g[0] for g in self.got]


def _bots(alerts=False, flags=True):
    """Set the host's Telegram config the way Render sets it — env vars."""
    os.environ["TELEGRAM_CHAT_ID"] = "5551234"
    os.environ["FLAG_MACHINE"] = "1"
    for key, on, val in (("TELEGRAM_BOT_TOKEN", alerts, "test:alerts"),
                         ("FLAGS_BOT_TOKEN", flags, "test:flags")):
        if on:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


def _bell():
    """The bell as the nagging host (DOCS_NAG=1) builds it."""
    os.environ["DOCS_NAG"] = "1"
    try:
        return getattr(docs_nag, "bell_items", lambda: [])()
    finally:
        os.environ.pop("DOCS_NAG", None)


def who_speaks():
    health_key = getattr(docs_nag, "HEALTH_KEY", "docs:nag_health")
    telegram.configured = REAL_CONFIGURED           # the credentials ARE the subject now
    real = (telegram.send, telegram.send_to)
    w = Wire()
    telegram.send, telegram.send_to = w.send, w.send_to
    jihad = {"GWD004803687": row(ISO, source="it", name="Jihad ahmad adealy"),
             "GWD004803612": row(ISO, source="it", name="Jihad ahmad adealy")}
    try:
        print("\nwho speaks — Render on 2026-09-24: chat id, flags bot, NO alerts-bot token")
        _bots(alerts=False, flags=True)
        _wipe_stamps()
        db.set_setting(health_key, None)
        out = docs_nag.run_once(check=still, today=TODAY, rows=jihad)
        check("both parcels dying today reach the owner", len(out) == 2, f"sent {out}")
        check("through the 🚩 flags bot, to his chat",
              w.via() == ["flags", "flags"]
              and all(g[2] == "5551234" and g[3] == "test:flags" for g in w.got), w.got)
        check("and the text says how to stop it («تم» / done)",
              bool(w.got) and all("done" in g[1] and "تم" in g[1] for g in w.got), w.got[:1])

        print("\na bot that fails hands the message to the next")
        _bots(alerts=True, flags=True)
        _wipe_stamps()
        w.got, w.ok = [], {"alerts": True, "flags": False}
        out = docs_nag.run_once(check=still, today=TODAY, rows=jihad)
        check("the alerts bot carries it when the flags bot fails",
              len(out) == 2 and w.via() == ["flags", "alerts", "flags", "alerts"], w.via())
        h = db.get_setting(health_key) or {}
        check("health: delivered, and it remembers who failed first",
              h.get("ok") is True and h.get("channel") == "alerts" and "flags" in h.get("why", ""), h)

        print("\nno bot at all — the old bare `return []`")
        _bots(alerts=False, flags=False)
        _wipe_stamps()
        w.got, w.ok = [], {"alerts": True, "flags": True}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = docs_nag.run_once(check=still, today=TODAY, rows=jihad)
        log = buf.getvalue()
        check("nothing can be sent", out == [] and w.got == [], w.got)
        check("the log SHOUTS, naming both parcels",
              "MUTE" in log and "GWD004803687" in log and "GWD004803612" in log, log)
        mute = getattr(docs_nag, "mute_reason", lambda: "")()
        check("the reason names what is missing",
              "TELEGRAM_BOT_TOKEN" in mute and "FLAGS_BOT_TOKEN" in mute, mute)
        check("health records the failure", (db.get_setting(health_key) or {}).get("ok") is False)
        bell = _bell()
        check("the 🔔 bell carries a 🔕 'alarm can't reach Telegram' item",
              any(b["type"] == "alarm_mute" for b in bell), bell)

        print("\nevery bot fails at send time (a revoked token)")
        _bots(alerts=True, flags=True)
        _wipe_stamps()
        w.got, w.ok = [], {"alerts": False, "flags": False}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = docs_nag.run_once(check=still, today=TODAY, rows=jihad)
        check("nothing counts as sent", out == [])
        check("the log says the reminder reached NOBODY", "NOBODY" in buf.getvalue(), buf.getvalue())
        bell = _bell()
        check("the bell says the last message never arrived",
              any(b["type"] == "alarm_mute" and "never arrived" in b["title"] for b in bell), bell)

        print("\n«done» in the flags bot's chat")
        _bots(alerts=False, flags=True)
        _wipe_stamps()
        w.got, w.ok = [], {"alerts": True, "flags": True}
        rt = docs_nag._today()                     # «done» acts on the real Amman day
        heard = {g: row(rt.isoformat(), source="it", name="Jihad ahmad adealy") for g in jihad}
        docs_nag.run_once(check=still, today=rt, rows=heard)          # both nagged once
        late = {"GWD000000099": row(rt.isoformat(), source="leluxe")}  # starts later today
        db.set_setting(flag_machine.OFFSET_KEY, 1)
        replies = []
        upd = {"ok": True, "result": [
            {"update_id": 5, "message": {"chat": {"id": 5551234}, "text": "تم"}}]}
        flag_machine.poll_updates(get=lambda off, t: upd,
                                  send=lambda chat, txt: replies.append(txt) or {"ok": True})
        check("the reply names both parcels it silenced",
              len(replies) == 1 and "GWD004803687" in replies[0] and "GWD004803612" in replies[0],
              replies)
        st = db.get_setting(docs_nag.SENT_KEY) or {}
        for g in st.values():
            g["at"] = (datetime.now().astimezone() - timedelta(minutes=30)).isoformat()
        db.set_setting(docs_nag.SENT_KEY, st)                         # the window passed
        w.got = []
        out = docs_nag.run_once(check=still, today=rt, rows=dict(heard, **late))
        check("they stay quiet for the rest of the day",
              not any("GWD00480" in t for t in out), out)
        check("a parcel he never heard about still gets its alarm",
              len(out) == 1 and "GWD000000099" in out[0], out)
        check("tomorrow «done» is forgotten (a fresh day, a fresh alarm)",
              not getattr(docs_nag, "acked", lambda *a: True)(
                  "GWD004803687", (rt + timedelta(days=1)).isoformat()))

        print("\nthe bell on a host with the flags bot but no alerts bot")
        _bots(alerts=False, flags=True)
        db.set_setting(health_key, None)
        bell = _bell()
        check("no 'mute' item — the alarm CAN speak",
              not any(b["type"] == "alarm_mute" for b in bell), bell)
        check("but it says the alerts bot itself is not connected",
              any(b["type"] == "alerts_bot_off" for b in bell), bell)
        check("and a host without DOCS_NAG (the Mac) rings nothing",
              getattr(docs_nag, "bell_items", lambda: [])() == [])
    finally:
        telegram.send, telegram.send_to = real
        telegram.configured = lambda *a, **k: True
        for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FLAGS_BOT_TOKEN", "FLAG_MACHINE"):
            os.environ.pop(k, None)
        _wipe_stamps()


def main():
    print("\ndocs_nag — the last-day nag\n" + "=" * 58)
    telegram.configured = lambda *a, **k: True          # creds are not the subject

    # ── who qualifies ────────────────────────────────────────────────────────
    print("\ncandidates — only today, only yellow")
    rows = {
        "GWD004802571": row(ISO),                                   # ← the one
        "GWD000000001": row((TODAY + timedelta(days=1)).isoformat()),   # tomorrow
        "GWD000000002": row((TODAY - timedelta(days=1)).isoformat()),   # yesterday
        "GWD000000003": row(ISO, state="info"),                     # blue: nothing asked
        "GWD000000004": row(ISO, open_=False),                      # closed row
        "GWD000000005": row("", state="action"),                    # no deadline known
    }
    got = [g for g, _ in docs_nag.candidates(today=TODAY, rows=rows)]
    check("the parcel whose link dies TODAY is picked", got == ["GWD004802571"], f"got {got}")

    print("\nall three boards — the gap that killed GWD004802571")
    mixed = {"GWD000000010": row(ISO, source="leluxe"),
             "GWD000000011": row(ISO, source="it"),
             "GWD000000012": row(ISO, source="purchases")}
    got = [g for g, _ in docs_nag.candidates(today=TODAY, rows=mixed)]
    check("Le Luxe, IT and Purchases all nag", len(got) == 3, f"got {got}")

    # ── the happy path ───────────────────────────────────────────────────────
    print("\none pass")
    _wipe_stamps()
    docs_nag.handed_over = lambda g: False
    s = Sink()
    out = docs_nag.run_once(send=s, check=still, today=TODAY, rows=rows)
    check("exactly one message goes out", len(out) == 1, f"sent {len(out)}")
    txt = s.texts[0] if s.texts else ""
    check("it names the parcel", "GWD004802571" in txt)
    check("it names the deadline", ISO in txt)
    check("it says which paper GAASH wants", "Upload Document" in txt)
    check("it is numbered (تذكير رقم 1)", "1" in txt and "تذكير" in txt)

    # ── two workers, one message ─────────────────────────────────────────────
    print("\ntwo gunicorn workers")
    s2 = Sink()
    out2 = docs_nag.run_once(send=s2, check=still, today=TODAY, rows=rows)
    check("the second worker stands down inside the window", out2 == [], f"sent {out2}")

    # ── the slot reopens ─────────────────────────────────────────────────────
    print("\nthe slot reopens after every_min")
    st = db.get_setting(docs_nag.SENT_KEY)
    st["GWD004802571"]["at"] = (datetime.now().astimezone()
                                - timedelta(minutes=30)).isoformat()
    db.set_setting(docs_nag.SENT_KEY, st)
    s3 = Sink()
    out3 = docs_nag.run_once(send=s3, check=still, today=TODAY, rows=rows)
    check("it nags again once the window passed", len(out3) == 1)
    check("the reminder number climbed to 2", "2" in (s3.texts[0] if s3.texts else ""),
          s3.texts[0] if s3.texts else "")

    # ── the four ways it stops ───────────────────────────────────────────────
    print("\nstop 1 — the papers were handed over")
    _wipe_stamps()
    docs_nag.handed_over = lambda g: True
    s4 = Sink()
    check("a stamped hand-over mutes it",
          docs_nag.run_once(send=s4, check=still, today=TODAY, rows=rows) == [])
    docs_nag.handed_over = lambda g: False

    print("\nstop 2 — GAASH stopped asking (live)")
    _wipe_stamps()
    s5 = Sink()
    check("a live 'info' answer sends nothing",
          docs_nag.run_once(send=s5, check=stopped, today=TODAY, rows=rows) == [])

    print("\nstop 3 — the day rolled past")
    _wipe_stamps()
    s6 = Sink()
    # The nag follows the calendar, it does not follow a parcel: on the 21st
    # GWD004802571's link is already shut (nothing we send helps), while
    # GWD000000001 — whose own deadline IS the 21st — becomes the day's alarm.
    out6 = docs_nag.run_once(send=s6, check=still,
                             today=TODAY + timedelta(days=1), rows=rows)
    check("yesterday's parcel is dropped the moment its day ends",
          not any("GWD004802571" in t for t in s6.texts), s6.texts)
    check("and the parcel dying THAT day takes over the alarm",
          len(out6) == 1 and "GWD000000001" in s6.texts[0], s6.texts)

    print("\nstop 4 — the kill switch")
    _wipe_stamps()
    real_enabled = docs_nag.enabled
    docs_nag.enabled = lambda *a, **k: False
    s7 = Sink()
    check("settings can mute it entirely",
          docs_nag.run_once(send=s7, check=still, today=TODAY, rows=rows) == [])
    docs_nag.enabled = real_enabled

    # ── a silent alarm is the bug ────────────────────────────────────────────
    print("\nGAASH being down must NOT create silence")
    _wipe_stamps()
    s8 = Sink()
    out8 = docs_nag.run_once(send=s8, check=boom, today=TODAY, rows=rows)
    check("a failed live check still nags on the last day", len(out8) == 1, f"sent {out8}")

    who_speaks()

    # ── housekeeping ─────────────────────────────────────────────────────────
    print("\nthe stamp row cannot grow forever")
    db.set_setting(docs_nag.SENT_KEY,
                   {"GWD-OLD": {"date": "2020-01-01", "at": "2020-01-01T00:00:00+03:00", "n": 9}})
    docs_nag._claim("GWD004802571", ISO, 600)
    keys = set(db.get_setting(docs_nag.SENT_KEY) or {})
    check("yesterday's stamps are pruned", "GWD-OLD" not in keys, f"keys {keys}")

    print("\nthe env gate")
    os.environ.pop("DOCS_NAG", None)
    check("no DOCS_NAG → start() is a no-op", docs_nag.start() is False)
    check("env_on() reads the flag", docs_nag.env_on() is False)
    os.environ["DOCS_NAG"] = "1"
    check("DOCS_NAG=1 arms it", docs_nag.env_on() is True)
    os.environ.pop("DOCS_NAG", None)

    print("\n" + "=" * 58)
    print(f"{OK} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
