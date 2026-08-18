#!/usr/bin/env python3
"""
Owner Telegram alerts over the purchase packages — daemon mirrors meta_sync.py.

Two rules (thresholds + stop list editable in Settings under alerts.*):
  GD — GAASH doc-link deadline (pk.gaash_deadline): countdown at 7/3/1 days
       left + once when past ("lost forever" risk). Moot once customs cleared
       or Gerizim has the parcel.
  AL — Amazon arrival overdue (pk.arrival): 5/7/8 days late. Silenced once the
       owner's Otlobly status says the box reached us / the customer (stop
       list), or a carrier says delivered.

Each threshold fires ONCE per package via alerts_sent stamps — this also
neutralizes gunicorn's 2 workers running the loop twice (first stamp wins).
"""

import os
import re
import threading
import time
from datetime import date

import cfg
import db
import memlog
import purchases
import telegram

STOP_DEFAULT = ["rd", "delivered", "delievered rd", "delievered no rd",
                "recieved rd", "recieved no rd", "sent rd", "sent no rd", "complete"]
GAASH_DAYS_DEFAULT = [7, 3, 1]
ARRIVAL_DAYS_DEFAULT = [5, 7, 8]

_GD_RE = re.compile(r"gd\d+")


def _gd_key(dl_iso, name):
    """Countdown stamps are keyed by the DEADLINE they fired against, not by the
    threshold alone. `alerts_sent` is write-once-forever, so a bare "gd7" about a
    wrong (prematurely minted) deadline would suppress the countdown for the
    corrected one GAASH re-issues. Versioning re-arms it exactly once."""
    return f"{name}@{dl_iso}"



def stop_statuses(config=None):
    """The ONE vocabulary for "this parcel's journey is over" — the owner has
    it, sent it, or closed the job. Settings-editable at alerts.stop_statuses,
    and editing it there now moves all three consumers together: alert
    silencing, the Leluxe sweep's skips, and the Purchases sweep's skips.
    Lives here because alerts owns STOP_DEFAULT; callers import it locally
    (alerts imports purchases, so purchases cannot import alerts up top)."""
    lst = cfg.get(config or cfg.load(), "alerts.stop_statuses", STOP_DEFAULT)
    return {str(s).strip().lower() for s in lst if str(s).strip()}


def _d(s):
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _bucket(v):
    return v.get("bucket") if isinstance(v, dict) else None


def _head(po, pk):
    gwd = (pk.get("tracking_number") or "").strip()
    who = next(((it.get("customer_name") or "").strip()
                for it in pk.get("items") or []
                if (it.get("customer_name") or "").strip()), "")
    return f"{po.get('po_id')} 📦{pk.get('package_no')}" \
           + (f" · {gwd}" if gwd else "") + (f" · {who}" if who else "")


def run_once(send=telegram.send):
    """One rules pass → list of message texts actually sent (stamps persisted)."""
    if not telegram.configured():
        return []
    c = cfg.load()
    stop = stop_statuses(c)
    gd_days = sorted({int(x) for x in cfg.get(c, "alerts.gaash_days", GAASH_DAYS_DEFAULT)})
    al_days = sorted({int(x) for x in cfg.get(c, "alerts.arrival_days", ARRIVAL_DAYS_DEFAULT)})
    today = date.today()
    pdb = purchases.load()
    sent, dirty = [], False
    for po in pdb["purchase_orders"]:
        for pk in po.get("packages") or []:
            if (pk.get("otlobly_status") or "").strip().lower() in stop:
                continue
            stamps = pk.get("alerts_sent") or {}
            gaash_b = _bucket(pk.get("tracking_status"))
            gz = pk.get("gerizim_status")
            delivered = gaash_b == "delivered" or _bucket(gz) == "delivered"

            # ── GD: the lost-forever countdown ──
            dl = _d(pk.get("gaash_deadline"))
            # Legacy bare stamps ("gd7") → versioned ("gd7@2026-08-31"). Rewritten,
            # never re-fired: the key created here is immediately "already sent"
            # for the CURRENT deadline, so this migration sends nothing. Only a
            # genuinely different deadline (a re-issued link) produces a new key.
            if dl and any(k == "gd_past" or _GD_RE.fullmatch(k) for k in stamps):
                for k in [k for k in stamps if k == "gd_past" or _GD_RE.fullmatch(k)]:
                    stamps.setdefault(_gd_key(pk["gaash_deadline"], k), stamps[k])
                    del stamps[k]
                pk["alerts_sent"] = stamps
                dirty = True
            if dl and gaash_b not in ("cleared", "delivered") and not isinstance(gz, dict):
                left = (dl - today).days
                hit = [n for n in gd_days if left <= n]
                name = "gd_past" if left < 0 else (f"gd{min(hit)}" if hit else None)
                key = _gd_key(pk["gaash_deadline"], name) if name else None
                if key and key not in stamps:
                    txt = (f"🛑 موعد غاش النهائي فات ({pk['gaash_deadline']}) — الطرد بخطر الضياع!\n"
                           if left < 0 else
                           f"⏳ باقي {left} يوم على موعد غاش النهائي ({pk['gaash_deadline']}) — "
                           f"جهّز المستندات\n") + _head(po, pk)
                    if send(txt).get("ok"):
                        now = db.now_iso()
                        stamps[key] = now
                        for n in (gd_days if left < 0 else hit):
                            # crossed thresholds count as done — versioned too
                            stamps.setdefault(_gd_key(pk["gaash_deadline"], f"gd{n}"), now)
                        pk["alerts_sent"] = stamps
                        sent.append(txt)
                        dirty = True

            # ── AL: Amazon arrival overdue ──
            arr = _d(pk.get("arrival"))
            if arr and not delivered:
                late = (today - arr).days
                hit = [n for n in al_days if late >= n]
                key = f"al{max(hit)}" if hit else None
                if key and key not in stamps:
                    ts = pk.get("tracking_status")
                    st_txt = (ts.get("text") or ts.get("label") or "") if isinstance(ts, dict) \
                        else (ts or "")
                    txt = (f"⚠ تأخر الوصول {late} يوم عن موعد أمازون ({pk['arrival']})\n"
                           + _head(po, pk) + (f"\nحالة غاش: {st_txt}" if st_txt else ""))
                    if send(txt).get("ok"):
                        now = db.now_iso()
                        stamps[key] = now
                        for n in hit:
                            stamps.setdefault(f"al{n}", now)
                        pk["alerts_sent"] = stamps
                        sent.append(txt)
                        dirty = True
    if dirty:
        purchases.save(pdb)
    return sent


_started = False


def _interval_seconds():
    try:
        return max(5, int(os.environ.get("ALERTS_INTERVAL_MIN", "30"))) * 60
    except ValueError:
        return 1800


def _loop():
    time.sleep(60)                    # let the app finish booting first
    while True:
        try:
            with memlog.watch("alerts"):
                out = run_once()      # no-op unless Telegram creds exist
            if out:
                print(f"alerts: sent {len(out)} telegram alert(s)")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"alerts: pass failed ({e})")
        time.sleep(_interval_seconds())


def start():
    """Start the background alerter once (idempotent). Runs even before creds
    exist — the owner can paste them in Settings and the next tick picks up."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-alerts", daemon=True).start()
