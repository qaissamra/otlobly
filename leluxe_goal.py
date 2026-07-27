#!/usr/bin/env python3
"""
Leluxe Goal engine — the monthly order-goal math behind the 🎯 Goal view, the
daily 21:00 Telegram digest, and the owner's Telegram commands.

Pure module: no Flask. The only thread is the digest ticker (start(), gated by
env LELUXE_DIGEST so Render's gunicorn workers never run it). All money = USD
(the ClickUp "Total Amount" field).

Order value rule (verified against live data, where parent amounts are
inconsistent): a top row's value = Σ of its non-excluded items' Total Amount
when any item carries one, else the top row's own Total Amount. Items are one
Amazon-account purchase each (~$400–1000), and may hang off the order directly
or via a package. Orphan items count individually.

Dating: effective date = ordered_at (true AZ (2) creation date, backfilled —
date_created was reset to 2026-07-14 by the migration) falling back to
date_created. RD completions are dated by the first leluxe_status_log
transition into an rd-done status, then the DATE SENT field, then the row's
effective date.
"""

import json
import os
import threading
import time
from calendar import monthrange
from datetime import date, datetime, timedelta

import az
import cfg
import memlog
import db
import telegram

DEFAULTS = {
    "goal_usd": 30000,
    "ready_tag": "READY TO ORDER 2",
    "acct_min_usd": 400,
    "acct_max_usd": 1000,
    "rd_current": ["rd", "recieved rd"],
    "rd_done": ["sent rd"],
    "excluded": ["cancelled"],
    "digest_enabled": True,
    "digest_hour": 21,
}

SETTINGS_KEY = "leluxe:goal"
TOPS = ("order", "parent")

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]

# Original motivational one-liners (Tony-Robbins energy, our own words — no
# reproduced quotes), keyed by milestone tier; rotated by day so the page and
# the digest agree on the day's line.
LINES = {
    0:   ["أول خطوة هي الأصعب — ابدأ اليوم وخلّي الزخم يشتغل 🚀",
          "القرارات بتغيّر النتائج — قرّر اليوم واطلب 💪",
          "Massive action beats perfect plans — يلا نبدأ!"],
    25:  ["ربع الطريق! الزخم صار معك — لا توقّفه 🔥",
          "25% ✔ ارفع سقفك — الباقي أسهل مما تتخيل",
          "الموجة بلشت — اركبها للآخر 🌊"],
    50:  ["نص الجبل! اللي بيوصل هون ما بيرجع ⛰️",
          "50% ✔ التقدم هو السعادة — كمّل بنفس النَفَس",
          "النص الثاني دايمًا أسرع — اضغط ⚡"],
    75:  ["75%! النتيجة صارت قريبة — هجوم أخير 🏁",
          "الباقي تفاصيل — خلّصها بقوة 💥",
          "شدّ حيلك — القمة عبالها تنلمس ⛰️🔥"],
    100: ["🏆 الهدف تحقق! ارفع السقف — التحدي الجديد بلّش",
          "GOAL SMASHED 🎉 عرفت الطريق — عيدها أكبر الشهر الجاي",
          "🥇 هذا مستواك الجديد — لا تنزل عنه"],
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
    """Validate + persist the ⋮ Goal-settings form. Returns (settings, error)."""
    st = settings()
    b = body or {}
    try:
        if "goal_usd" in b:
            st["goal_usd"] = float(b["goal_usd"])
        if "acct_min_usd" in b:
            st["acct_min_usd"] = float(b["acct_min_usd"])
        if "acct_max_usd" in b:
            st["acct_max_usd"] = float(b["acct_max_usd"])
        if "digest_hour" in b:
            st["digest_hour"] = int(b["digest_hour"])
    except (TypeError, ValueError):
        return None, "goal, account min/max and hour must be numbers"
    if "ready_tag" in b:
        st["ready_tag"] = str(b["ready_tag"] or "").strip()
    if "digest_enabled" in b:
        st["digest_enabled"] = bool(b["digest_enabled"])
    for key in ("rd_current", "rd_done", "excluded"):
        if key in b:
            if not isinstance(b[key], list):
                return None, f"{key} must be a list of statuses"
            st[key] = [str(s).strip() for s in b[key] if str(s).strip()]
    if st["goal_usd"] <= 0:
        return None, "goal must be > 0"
    if not (0 < st["acct_min_usd"] <= st["acct_max_usd"]):
        return None, "account range must be 0 < min ≤ max"
    if not (0 <= st["digest_hour"] <= 23):
        return None, "digest hour must be 0–23"
    if not st["ready_tag"]:
        return None, "ready tag is required"
    db.set_setting(SETTINGS_KEY, st)
    return st, None


# --------------------------------------------------------------------------- #
# Row loading + the value-unit model
# --------------------------------------------------------------------------- #
def _norm(s):
    return " ".join(str(s or "").casefold().split())


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ms(v):
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def _mday(ms):
    """ms-epoch → local date (None-safe)."""
    return datetime.fromtimestamp(ms / 1000).date() if ms else None


def _fget(fields, name):
    want = _norm(name)
    for k, v in (fields or {}).items():
        if _norm(k) == want:
            return v
    return None


def _load():
    """One pass over leluxe_orders → light dicts the math runs on."""
    rows = []
    with db.connect() as c:
        for r in c.execute("SELECT id, kind, status, name, parent_local_id, "
                           "ordered_at, date_created, data_json FROM leluxe_orders "
                           "WHERE deleted=0"):
            try:
                data = json.loads(r["data_json"] or "{}")
            except ValueError:
                data = {}
            fields = data.get("fields") or {}
            rows.append({
                "id": r["id"], "kind": r["kind"], "name": r["name"] or "",
                "status": _norm(r["status"]),
                "parent_local_id": r["parent_local_id"],
                "eff_ms": _ms(r["ordered_at"]) or _ms(r["date_created"]),
                "amount": _f(_fget(fields, "total amount")),
                "date_sent_ms": _ms(_fget(fields, "date sent")),
            })
    return rows


def _units(rows, st):
    """The value units the goal math sums — per top order: its non-excluded
    items when any item carries an amount, else the top itself. Orphan items
    are their own units. Each unit: {usd, day, status, row, top} where `top`
    is the order-level row the unit belongs to (itself for orphans/tops).

    Exclusion works at BOTH levels: an excluded ORDER-level status (e.g.
    'order number', 'cancelled') drops the whole order — its value is summed
    from the items, so filtering only the parent row would miss it; and an
    excluded ITEM status still drops just that item (a partial cancellation
    inside a live order)."""
    excluded = {_norm(s) for s in st["excluded"]}
    kids = {}
    for r in rows:
        if r["parent_local_id"]:
            kids.setdefault(r["parent_local_id"], []).append(r)
    tops = [r for r in rows if r["kind"] in TOPS]
    top_ids = {r["id"] for r in tops}
    pkg_ids = set()
    units = []
    for t in tops:
        if t["status"] in excluded:     # order-level status excluded → whole order out
            for k in kids.get(t["id"], []):
                if k["kind"] == "package":
                    pkg_ids.add(k["id"])
            continue
        items = []
        for k in kids.get(t["id"], []):
            if k["kind"] == "item":
                items.append(k)
            elif k["kind"] == "package":
                pkg_ids.add(k["id"])
                items.extend(x for x in kids.get(k["id"], []) if x["kind"] == "item")
        live = [i for i in items if i["status"] not in excluded]
        if any(i["amount"] > 0 for i in live):
            for i in live:
                units.append({"usd": i["amount"], "day": _mday(i["eff_ms"]),
                              "status": i["status"], "row": i, "top": t})
        elif t["status"] not in excluded:
            units.append({"usd": t["amount"], "day": _mday(t["eff_ms"]),
                          "status": t["status"], "row": t, "top": t})
    known = top_ids | pkg_ids
    for r in rows:                      # orphans: parent chain missing
        if r["kind"] == "item" and r["parent_local_id"] not in known \
                and r["status"] not in excluded:
            units.append({"usd": r["amount"], "day": _mday(r["eff_ms"]),
                          "status": r["status"], "row": r, "top": r})
    return units


def _rd_done_days(rd_done):
    """row_id → date of the FIRST transition into an rd-done status."""
    if not rd_done:
        return {}
    marks = sorted({_norm(s) for s in rd_done})
    q = ",".join("?" * len(marks))
    out = {}
    with db.connect() as c:
        for r in c.execute(f"SELECT row_id, MIN(ts) ts FROM leluxe_status_log "
                           f"WHERE lower(new_status) IN ({q}) GROUP BY row_id",
                           marks):
            try:
                out[r["row_id"]] = datetime.fromisoformat(r["ts"]).date()
            except (TypeError, ValueError):
                pass
    return out


def _rd_units(rows, st):
    """RD pipeline on the same value-unit model. Returns (current, done) where
    done units carry .day = log-transition → DATE SENT → effective date."""
    rd_cur = {_norm(s) for s in st["rd_current"]}
    rd_done = {_norm(s) for s in st["rd_done"]}
    done_days = _rd_done_days(st["rd_done"])
    current, done = [], []
    for u in _units(rows, st):
        if u["status"] in rd_cur:
            current.append(u)
        elif u["status"] in rd_done:
            r = u["row"]
            day = done_days.get(r["id"]) or _mday(r["date_sent_ms"]) or u["day"]
            done.append({**u, "day": day})
    return current, done


# --------------------------------------------------------------------------- #
# The numbers
# --------------------------------------------------------------------------- #
def compute(now=None, month=None):
    """Everything the Goal view + digest + bot need for one month. `month` is
    'YYYY-MM' to view a past month (stats freeze at that month's last day)."""
    now = now or datetime.now()
    if month:
        y, m = int(month[:4]), int(month[5:7])
        if (y, m) != (now.year, now.month):
            now = datetime(y, m, monthrange(y, m)[1], 23, 59)
    st = settings()
    rows = _load()
    units = _units(rows, st)
    today = now.date()
    first = today.replace(day=1)
    ndays = monthrange(today.year, today.month)[1]

    by_day = [{"d": first.replace(day=d + 1).isoformat(), "usd": 0.0, "orders": 0}
              for d in range(ndays)]
    month_total = today_total = today_orders = 0.0
    day_tops = [set() for _ in range(ndays)]
    for u in units:
        d = u["day"]
        if not d or d.year != today.year or d.month != today.month:
            continue
        month_total += u["usd"]
        slot = by_day[d.day - 1]
        slot["usd"] += u["usd"]
        if u["usd"] > 0:
            day_tops[d.day - 1].add(u["top"]["id"])
        if d == today:
            today_total += u["usd"]
    for i, tops_set in enumerate(day_tops):
        by_day[i]["orders"] = len(tops_set)
    today_orders = by_day[today.day - 1]["orders"]

    # daily totals across ALL months (for the streak walking past month starts)
    all_days = {}
    for u in units:
        if u["day"] and u["usd"] > 0:
            all_days[u["day"]] = all_days.get(u["day"], 0) + u["usd"]
    streak, d = 0, today
    if not all_days.get(d):
        d -= timedelta(days=1)          # today still empty ≠ broken streak
    while all_days.get(d):
        streak += 1
        d -= timedelta(days=1)

    goal = float(st["goal_usd"])
    pct = round(month_total / goal * 100, 1) if goal else 0.0
    remaining = max(0.0, goal - month_total)
    days_left = ndays - today.day + 1
    milestone = max((m for m in (25, 50, 75, 100) if pct >= m), default=0)

    lm_last = first - timedelta(days=1)
    lm_total = sum(u["usd"] for u in units
                   if u["day"] and u["day"].year == lm_last.year
                   and u["day"].month == lm_last.month)

    rd_current, rd_done = _rd_units(rows, st)
    rd = {
        "current_count": len(rd_current),
        "current_usd": round(sum(u["usd"] for u in rd_current), 2),
        "done_month_usd": round(sum(u["usd"] for u in rd_done
                                    if u["day"] and u["day"].year == today.year
                                    and u["day"].month == today.month), 2),
        "done_month_count": sum(1 for u in rd_done
                                if u["day"] and u["day"].year == today.year
                                and u["day"].month == today.month),
        "done_last_month_usd": round(sum(u["usd"] for u in rd_done
                                         if u["day"] and u["day"].year == lm_last.year
                                         and u["day"].month == lm_last.month), 2),
    }

    best = max(by_day, key=lambda s: s["usd"])
    return {
        "month": f"{today.year:04d}-{today.month:02d}",
        "month_label": f"{AR_MONTHS[today.month - 1]} {today.year}",
        "goal_usd": goal,
        "month_total": round(month_total, 2),
        "today_total": round(today_total, 2),
        "today_orders": today_orders,
        "pct": pct,
        "remaining": round(remaining, 2),
        "days_left": days_left,
        "need_per_day": round(remaining / days_left, 2) if days_left else 0.0,
        "avg_per_day": round(month_total / today.day, 2),
        "best_day": {"d": best["d"], "usd": round(best["usd"], 2)} if best["usd"] else None,
        "by_day": [{"d": s["d"], "usd": round(s["usd"], 2), "orders": s["orders"]}
                   for s in by_day],
        "streak_days": streak,
        "milestone": milestone,
        "last_month_total": round(lm_total, 2),
        "rd": rd,
        "line": motivation(milestone, today),
    }


def motivation(milestone, day=None):
    opts = LINES.get(milestone) or LINES[0]
    day = day or date.today()
    return opts[day.toordinal() % len(opts)]


def window_totals(year, month=None, now=None):
    """Ordered + RD-done totals for one month or a whole year (the bot's
    'last year total rd' style questions)."""
    st = settings()
    rows = _load()

    def _in(d):
        return bool(d) and d.year == year and (month is None or d.month == month)

    units = _units(rows, st)
    _cur, rd_done = _rd_units(rows, st)
    o = [u for u in units if _in(u["day"])]
    r = [u for u in rd_done if _in(u["day"])]
    return {"year": year, "month": month,
            "orders_usd": round(sum(u["usd"] for u in o), 2),
            "orders_n": len({u["top"]["id"] for u in o if u["usd"] > 0}),
            "rd_usd": round(sum(u["usd"] for u in r), 2),
            "rd_n": len(r)}


# --------------------------------------------------------------------------- #
# az-tool (Multilogin) — ready-profile capacity
# --------------------------------------------------------------------------- #
_az_down = {"ts": 0.0}


def profiles_summary(st=None, refresh=False):
    """Ready-tag profile count + capacity from the az-tool on :8765. Never
    raises: {"offline": True} when the tool isn't reachable (60s negative
    cache so a down az-tool doesn't slow every Goal load)."""
    st = st or settings()
    if not refresh and time.time() - _az_down["ts"] < 60:
        return {"offline": True}
    if not az.healthy():
        _az_down["ts"] = time.time()
        return {"offline": True}
    if refresh:
        az.bust_cache()
    try:
        profiles = az.all_profiles() or []
    except Exception:  # noqa: BLE001 - URLError/timeout/bad JSON → offline
        _az_down["ts"] = time.time()
        return {"offline": True}
    want = _norm(st["ready_tag"])
    count = 0
    tags = {}
    for p in profiles:
        ptags = p.get("all_tags") or p.get("tags") or []
        for t in ptags:
            k = _norm(t)
            if k and k not in tags:
                tags[k] = str(t).strip()
        if any(_norm(t) == want for t in ptags):
            count += 1
    return {
        "offline": False,
        "count": count,
        "total_profiles": len(profiles),
        "ready_tag": st["ready_tag"],
        "capacity_min": round(count * float(st["acct_min_usd"])),
        "capacity_max": round(count * float(st["acct_max_usd"])),
        "all_tags": sorted(tags.values(), key=_norm),
    }


# --------------------------------------------------------------------------- #
# Daily Telegram digest
# --------------------------------------------------------------------------- #
def _money(v):
    return f"${v:,.0f}"


def _bar(pct, width=10):
    full = min(width, int(round(pct / 100 * width)))
    return "▓" * full + "░" * (width - full)


def digest_text(now=None):
    g = compute(now=now)
    lines = [
        f"🎯 هدف ليلوكس — {g['month_label']}",
        f"📦 اليوم: {_money(g['today_total'])}"
        + (f" ({g['today_orders']} طلب)" if g["today_orders"] else ""),
        f"📈 الشهر: {_money(g['month_total'])} / {_money(g['goal_usd'])} — {g['pct']:g}%",
        _bar(g["pct"]),
    ]
    if g["remaining"] > 0:
        lines.append(f"⏳ باقي {_money(g['remaining'])} على {g['days_left']} يوم "
                     f"→ ≈{_money(g['need_per_day'])}/يوم")
    lines.append(f"🔁 RD الشهر الماضي: {_money(g['rd']['done_last_month_usd'])}"
                 f" · هذا الشهر: {_money(g['rd']['done_month_usd'])}")
    p = profiles_summary()
    if not p.get("offline"):
        lines.append(f"👤 جاهز للطلب: {p['count']} بروفايل "
                     f"(سعة {_money(p['capacity_min'])}–{_money(p['capacity_max'])})")
    if g["streak_days"] >= 2:
        lines.append(f"🔥 {g['streak_days']} أيام متواصلة — كمّل!")
    lines.append(g["line"])
    return "\n".join(lines)


def maybe_send_digest(now=None, send=None):
    """Once per day at/after digest_hour. claim_once is taken BEFORE building
    the text (an az-tool stall must not widen the dedup window); a failed send
    releases the claim so the next 5-min tick retries."""
    send = send or telegram.send
    now = now or datetime.now()
    st = settings()
    if not st["digest_enabled"] or now.hour < int(st["digest_hour"]):
        return None
    if not telegram.configured():
        return None
    key = f"lxdigest:{now.date().isoformat()}"
    if not db.claim_once(key):
        return None
    try:
        txt = digest_text(now=now)
        out = send(txt)
    except Exception:  # noqa: BLE001
        out = {"ok": False}
    if not out.get("ok"):
        with db.connect() as c:                     # release → retry next tick
            c.execute("DELETE FROM settings WHERE key=?", (key,))
        return None
    return txt


_started = False


def _loop():
    time.sleep(60)                    # let the app finish booting first
    while True:
        try:
            with memlog.watch("leluxe_goal.digest"):
                out = maybe_send_digest()
            if out:
                print("leluxe_goal: daily digest sent")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"leluxe_goal: digest pass failed ({e})")
        time.sleep(300)


def start():
    """Daily-digest ticker. No-op unless env LELUXE_DIGEST is truthy — the
    flag lives ONLY in com.otlobly.app.plist, so Render's workers and manual
    dev runs never double-send."""
    global _started
    if _started or os.environ.get("LELUXE_DIGEST", "") in ("", "0"):
        return
    _started = True
    threading.Thread(target=_loop, name="leluxe-goal-digest", daemon=True).start()


# --------------------------------------------------------------------------- #
# Auto-heal: keep ordered_at (true order dates) populated. UNGATED — runs on
# Render too (where LELUXE_DIGEST is off), because Render's leluxe_orders is
# the copy that gets stuck all-NULL → every order falls back to the migration
# day and the Goal reads wildly inflated. Self-no-ops once every row is dated.
# --------------------------------------------------------------------------- #
_autoheal_started = False


def _null_ordered_at_count():
    with db.connect() as c:
        r = c.execute("SELECT COUNT(*) n FROM leluxe_orders "
                      "WHERE ordered_at IS NULL AND deleted=0").fetchone()
    return r["n"] if r else 0


def run_autoheal():
    """One pass: if any row lacks ordered_at, backfill it from AZ (2) — at most
    once per day (claim_once), cross-worker-safe."""
    n = _null_ordered_at_count()
    if n == 0:
        return None
    if not db.claim_once(f"leluxe:autobackfill:{date.today().isoformat()}"):
        return None
    print(f"leluxe_goal: auto-heal — {n} rows missing ordered_at, backfilling "
          f"from source {_source_list_id()}")
    rep = backfill_ordered_at(dry_run=False)
    print(f"leluxe_goal: auto-heal ordered_at → {rep}")
    return rep


def _autoheal_loop():
    time.sleep(45)                    # let the app + DDL migration settle
    while True:
        try:
            run_autoheal()
        except Exception as e:  # noqa: BLE001 - never break boot / the thread
            print(f"leluxe_goal: auto-heal pass failed ({e})")
        time.sleep(6 * 3600)


def start_autoheal():
    global _autoheal_started
    if _autoheal_started:
        return
    _autoheal_started = True
    threading.Thread(target=_autoheal_loop, name="leluxe-goal-autoheal",
                     daemon=True).start()


# --------------------------------------------------------------------------- #
# One-time backfill: true order dates from the AZ (2) source list
# --------------------------------------------------------------------------- #
# The owner's fixed AZ (2) list id. Leluxe is a single-business feature (its
# working-list and PO-list ids are already committed in config.default.json), so
# a constant is the deploy-guaranteed fallback — it makes the date auto-heal work
# on Render without depending on config.json or a render.yaml env applying.
DEFAULT_SOURCE_LIST_ID = "901520351506"


def _source_list_id():
    """Resolve the AZ (2) source list: config → env → constant. Never None, so
    the auto-heal always has a source (Render's config.json may lack it)."""
    import leluxe
    return (leluxe.source_list_id(cfg.load())
            or os.environ.get("LELUXE_SOURCE_LIST_ID", "").strip()
            or DEFAULT_SOURCE_LIST_ID)


def _fetch_source_tasks(src=None):
    """Every task in AZ (2) (closed + subtasks), same paging as sync_from_source."""
    import leluxe
    src = src or _source_list_id()
    if not leluxe._token():
        return None, "CLICKUP_API_TOKEN is not set"
    if not src:
        return None, "leluxe.source_list_id (AZ 2) is not set"
    tasks, page = [], 0
    while True:
        st_, body = leluxe._http(f"{leluxe.CLICKUP_API}/list/{src}/task"
                                 f"?include_closed=true&subtasks=true&page={page}")
        if st_ != 200:
            return None, f"AZ (2) fetch failed ({st_}): {(body or {}).get('_error') or body}"
        batch = body.get("tasks") or []
        tasks.extend(batch)
        if body.get("last_page", True) or not batch:
            break
        page += 1
    return tasks, None


def backfill_ordered_at(dry_run=True, force=False, fetch=None):
    """Fill leluxe_orders.ordered_at from the AZ (2) tasks' true date_created.
    Match by data_json.source_task_id, then by order name/number key, then
    (items/packages) inherit the matched top's date — anything beats the
    migration-day stamp. Sync never touches ordered_at, so this sticks. NOTE:
    restore_presync doesn't restore ordered_at — re-run this after a restore."""
    import leluxe
    tasks, err = (fetch or _fetch_source_tasks)()
    if err:
        return {"error": err}
    sid2dc, topkey2dc, bytop, childkey = {}, {}, {}, {}
    for t in tasks:
        dc = _ms(t.get("date_created"))
        if not dc:
            continue
        sid2dc[t["id"]] = dc
        key = leluxe._name_key(t.get("name"))
        if t.get("parent"):
            bytop.setdefault(t["parent"], {}).setdefault(key, dc)
            childkey[key] = None if key in childkey else dc   # None = ambiguous
        else:
            topkey2dc.setdefault(key, dc)

    rows = []
    with db.connect() as c:
        for r in c.execute("SELECT id, kind, name, parent_local_id, ordered_at, "
                           "data_json FROM leluxe_orders WHERE deleted=0"):
            try:
                data = json.loads(r["data_json"] or "{}")
            except ValueError:
                data = {}
            rows.append({"id": r["id"], "kind": r["kind"], "name": r["name"] or "",
                         "parent_local_id": r["parent_local_id"],
                         "ordered_at": r["ordered_at"],
                         "sid": data.get("source_task_id")})
    byid = {r["id"]: r for r in rows}

    def top_of(r, hops=0):
        while r and r["kind"] not in TOPS and hops < 6:
            r = byid.get(r["parent_local_id"])
            hops += 1
        return r if r and r["kind"] in TOPS else None

    rep = {"scanned": len(rows), "already_set": 0, "filled": 0,
           "matched_source": 0, "matched_name": 0, "matched_inherit": 0,
           "unmatched": 0, "unmatched_names": []}
    updates = []
    top_dc = {}
    for r in rows:                                   # tops first: inherit needs them
        if r["kind"] not in TOPS:
            continue
        dc, how = None, None
        if r["sid"] in sid2dc:
            dc, how = sid2dc[r["sid"]], "matched_source"
        else:
            k = leluxe._name_key(r["name"])
            if k in topkey2dc:
                dc, how = topkey2dc[k], "matched_name"
        if dc:
            top_dc[r["id"]] = dc
            if r["ordered_at"] and not force:
                rep["already_set"] += 1
            else:
                updates.append((str(dc), r["id"]))
                rep[how] += 1
        else:
            rep["unmatched"] += 1
            if len(rep["unmatched_names"]) < 20:
                rep["unmatched_names"].append(r["name"])
    for r in rows:
        if r["kind"] in TOPS:
            continue
        t = top_of(r)
        dc, how = None, None
        if r["sid"] in sid2dc:
            dc, how = sid2dc[r["sid"]], "matched_source"
        elif r["kind"] == "item":
            k = leluxe._name_key(r["name"])
            tsid = (t or {}).get("sid")
            if tsid and k in bytop.get(tsid, {}):
                dc, how = bytop[tsid][k], "matched_name"
            elif childkey.get(k):
                dc, how = childkey[k], "matched_name"
        if not dc and t and t["id"] in top_dc:
            dc, how = top_dc[t["id"]], "matched_inherit"
        if dc:
            if r["ordered_at"] and not force:
                rep["already_set"] += 1
            else:
                updates.append((str(dc), r["id"]))
                rep[how] += 1
        else:
            rep["unmatched"] += 1
            if len(rep["unmatched_names"]) < 20:
                rep["unmatched_names"].append(r["name"])
    rep["filled"] = len(updates)
    rep["dry_run"] = bool(dry_run)
    if not dry_run and updates:
        with db.connect() as c:
            c.executemany("UPDATE leluxe_orders SET ordered_at=? WHERE id=?", updates)
    return rep


if __name__ == "__main__":
    print(json.dumps(compute(), ensure_ascii=False, indent=2))
    print(digest_text())
