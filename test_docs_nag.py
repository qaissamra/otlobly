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

    ./.venv/bin/python test_docs_nag.py
"""

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-nag-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ.pop("DOCS_NAG", None)

import db                                    # noqa: E402
db.init_db()
import cfg                                   # noqa: E402
import docs_nag                              # noqa: E402
import telegram                              # noqa: E402

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
