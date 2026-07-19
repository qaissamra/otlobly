#!/usr/bin/env python3
"""
Owner Telegram command bot — two-way: the owner texts the bot ("today",
"last year rd", "profiles", …) and gets the Leluxe goal numbers back.

Runs a getUpdates long-poll daemon thread, gated by env LELUXE_TG_BOT (set
ONLY in com.otlobly.app.plist — Render's workers and manual dev runs stay
inert). getUpdates allows a single consumer per bot token, so:
  * a DB lease (settings leluxe:tg_lease) keeps a stray second local process
    from double-polling;
  * HTTP 409 (someone else polling, e.g. the Settings "Detect chat id"
    button) → back off 60s;
  * the update offset persists in settings leluxe:tg_offset, and the very
    first run drains the backlog WITHOUT executing stale commands.

Only messages from the owner chat (TELEGRAM_CHAT_ID) are answered; everything
else is ignored silently. handle_command() is pure — tests never touch the
network.
"""

import os
import re
import threading
import time
import uuid
from datetime import datetime

import db
import leluxe_goal
import telegram

HELP = "\n".join([
    "🤖 أوامر ليلوكس:",
    "today · اليوم — طلبات اليوم",
    "month · الشهر — الشهر مقابل الهدف",
    "last month · الشهر الماضي",
    "rd — حالة الـ RD الحالية",
    "last year — طلبات السنة الماضية",
    "last year rd — RD السنة الماضية",
    "2025 / 2025 rd — سنة معينة",
    "profiles · بروفايلات — جاهز للطلب",
])

_YEAR = re.compile(r"^(?:rd\s+)?(\d{4})(?:\s+rd)?$")


def _money(v):
    return f"${v:,.0f}"


def _norm(s):
    return " ".join(str(s or "").casefold().split())


def handle_command(text, now=None):
    """One command in → one reply string out. Pure (no network)."""
    now = now or datetime.now()
    t = _norm(text)
    if not t:
        return HELP
    if t in ("help", "/help", "/start", "?", "مساعدة", "الاوامر", "الأوامر", "اوامر"):
        return HELP

    if t in ("today", "اليوم"):
        g = leluxe_goal.compute(now=now)
        return (f"📦 اليوم: {_money(g['today_total'])} ({g['today_orders']} طلب)\n"
                f"📈 الشهر: {_money(g['month_total'])} / {_money(g['goal_usd'])}"
                f" — {g['pct']:g}%")

    if t in ("month", "goal", "الشهر", "الهدف", "هذا الشهر"):
        g = leluxe_goal.compute(now=now)
        out = [f"🎯 {g['month_label']}: {_money(g['month_total'])} /"
               f" {_money(g['goal_usd'])} — {g['pct']:g}%"]
        if g["remaining"] > 0:
            out.append(f"⏳ باقي {_money(g['remaining'])} على {g['days_left']} يوم"
                       f" → ≈{_money(g['need_per_day'])}/يوم")
        if g["streak_days"] >= 2:
            out.append(f"🔥 {g['streak_days']} أيام متواصلة")
        out.append(g["line"])
        return "\n".join(out)

    if t in ("last month", "الشهر الماضي"):
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        w = leluxe_goal.window_totals(y, m, now=now)
        return (f"📅 {y}-{m:02d}: طلبات {_money(w['orders_usd'])} ({w['orders_n']})\n"
                f"🔁 RD: {_money(w['rd_usd'])} ({w['rd_n']})")

    if t in ("rd", "الار دي", "ار دي", "الأر دي"):
        g = leluxe_goal.compute(now=now)
        return (f"🔁 RD حالي: {g['rd']['current_count']} بقيمة"
                f" {_money(g['rd']['current_usd'])}\n"
                f"✅ RD خلص هذا الشهر: {_money(g['rd']['done_month_usd'])}"
                f" ({g['rd']['done_month_count']})\n"
                f"📅 الشهر الماضي: {_money(g['rd']['done_last_month_usd'])}")

    if t in ("last year", "last year orders", "last year total orders", "السنة الماضية"):
        w = leluxe_goal.window_totals(now.year - 1, now=now)
        return f"📅 {now.year - 1}: طلبات {_money(w['orders_usd'])} ({w['orders_n']})"

    if t in ("last year rd", "last year total rd", "rd last year", "rd السنة الماضية"):
        w = leluxe_goal.window_totals(now.year - 1, now=now)
        return f"📅 {now.year - 1}: RD {_money(w['rd_usd'])} ({w['rd_n']})"

    m = _YEAR.match(t)
    if m:
        y = int(m.group(1))
        w = leluxe_goal.window_totals(y, now=now)
        if "rd" in t:
            return f"📅 {y}: RD {_money(w['rd_usd'])} ({w['rd_n']})"
        return (f"📅 {y}: طلبات {_money(w['orders_usd'])} ({w['orders_n']})\n"
                f"🔁 RD: {_money(w['rd_usd'])} ({w['rd_n']})")

    if t in ("profiles", "profile", "accounts", "بروفايلات", "البروفايلات", "الحسابات"):
        p = leluxe_goal.profiles_summary(refresh=True)
        if p.get("offline"):
            return "⚠ az-tool مش شغال على :8765 — شغّله وجرب كمان مرة"
        g = leluxe_goal.compute(now=now)
        out = [f"👤 جاهز للطلب: {p['count']} بروفايل ({p['ready_tag']})",
               f"💪 سعة: {_money(p['capacity_min'])}–{_money(p['capacity_max'])}"]
        if g["remaining"] > 0:
            if p["capacity_max"] >= g["remaining"]:
                out.append(f"✅ السعة تكفي الباقي ({_money(g['remaining'])})")
            else:
                st = leluxe_goal.settings()
                need_lo = -(-int(g["remaining"]) // int(st["acct_max_usd"]))
                need_hi = -(-int(g["remaining"]) // int(st["acct_min_usd"]))
                out.append(f"⚠ الباقي {_money(g['remaining'])} — بدك"
                           f" ~{need_lo}–{need_hi} حساب جاهز")
        return "\n".join(out)

    return HELP


# --------------------------------------------------------------------------- #
# The long-poll loop
# --------------------------------------------------------------------------- #
_started = False
_LEASE_KEY = "leluxe:tg_lease"
_OFFSET_KEY = "leluxe:tg_offset"
_ME = uuid.uuid4().hex


def _lease_ok():
    """True when this process holds (or takes over) the single-poller lease."""
    lease = db.get_setting(_LEASE_KEY)
    if isinstance(lease, dict) and lease.get("id") != _ME:
        try:
            age = (datetime.now(datetime.fromisoformat(lease["ts"]).tzinfo)
                   - datetime.fromisoformat(lease["ts"])).total_seconds()
        except (TypeError, ValueError, KeyError):
            age = 9999
        if age < 90:
            return False
    db.set_setting(_LEASE_KEY, {"id": _ME, "ts": db.now_iso()})
    return True


def _drain_offset():
    """First run ever: skip the backlog so old messages don't fire commands."""
    out = telegram.get_updates(offset=None, timeout=0)
    updates = out.get("result") or []
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


def _handle_update(u, owner_chat, send=None):
    """One Telegram update → reply if (and only if) it's the owner's chat.
    Returns True when a reply was sent. Pure enough for tests (send injectable)."""
    send = send or telegram.send_to
    msg = u.get("message") or u.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id or str(chat_id) != str(owner_chat or ""):
        return False                  # not the owner → ignore silently
    try:
        reply = handle_command(msg.get("text") or "")
    except Exception as e:  # noqa: BLE001 - one bad command ≠ dead bot
        reply = f"⚠ صار خطأ: {e}"
    send(chat_id, reply)
    return True


def _loop():
    time.sleep(60)                    # let the app finish booting first
    while True:
        try:
            if not telegram.configured():
                time.sleep(300)
                continue
            if not _lease_ok():
                time.sleep(60)
                continue
            offset = db.get_setting(_OFFSET_KEY)
            if offset is None:
                offset = _drain_offset()
                db.set_setting(_OFFSET_KEY, offset)
            out = telegram.get_updates(offset=offset or None, timeout=50)
            if not out.get("ok"):
                time.sleep(60 if out.get("code") == 409 else 10)
                continue
            _, owner_chat = telegram._creds()
            for u in out.get("result") or []:
                offset = max(offset or 0, u.get("update_id", 0) + 1)
                _handle_update(u, owner_chat)
            db.set_setting(_OFFSET_KEY, offset)
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"telegram_bot: pass failed ({e})")
            time.sleep(10)


def start():
    """Owner command bot. No-op unless env LELUXE_TG_BOT is truthy — the flag
    lives ONLY in com.otlobly.app.plist (this Mac), never on Render."""
    global _started
    if _started or os.environ.get("LELUXE_TG_BOT", "") in ("", "0"):
        return
    _started = True
    threading.Thread(target=_loop, name="otlobly-tgbot", daemon=True).start()


if __name__ == "__main__":
    import sys
    print(handle_command(" ".join(sys.argv[1:]) or "help"))
