#!/usr/bin/env python3
"""
Self-checks for the Leluxe Goal engine (leluxe_goal.py + telegram_bot.py).

Proves: the order-value money rule (items win over an inconsistent parent
amount); today/month/last-month windows off ordered_at with date_created
fallback; the streak; RD current/done buckets with done-dating via the status
log → DATE SENT → effective date; the status-log hooks fire on app edits,
ClickUp pulls and sync merges (transitions only, dirty rows skipped); pulls
never clobber ordered_at; the historic-date backfill matches by source id,
name and inheritance; settings validation; the digest sends exactly once per
day (claim released on a failed send); the bot answers the owner only; and
the goal endpoints are admin-only.

    ./.venv/bin/python test_leluxe_goal.py
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-lxgoal-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)      # keep config.json off the repo
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["CLICKUP_API_TOKEN"] = "test-token"
os.environ["LELUXE_PACE"] = "0"
os.environ["LELUXE_PUSH_DISABLED"] = "1"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-bot"   # digest/bot gates need creds
os.environ["TELEGRAM_CHAT_ID"] = "5551234"
os.environ.pop("LELUXE_DIGEST", None)           # threads stay off in tests
os.environ.pop("LELUXE_TG_BOT", None)

import cfg           # noqa: E402
import db            # noqa: E402
import leluxe        # noqa: E402
import leluxe_goal   # noqa: E402
import telegram_bot  # noqa: E402

db.init_db()

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


SCHEMA = {
    "statuses": [{"status": s, "color": "#888", "orderindex": i, "type": "custom"}
                 for i, s in enumerate(["order number", "oredered", "waiting verification",
                                        "cancelled", "rd", "recieved rd", "sent rd",
                                        "sent no rd", "complete"])],
    "fields": {
        "Total Amount": {"id": "f-amt", "type": "currency", "options": []},
        "DATE SENT": {"id": "f-ds", "type": "date", "options": []},
        "Tracking Number": {"id": "f-trk", "type": "short_text", "options": []},
    },
}

config = cfg.load()
cfg.set_path(config, "leluxe.list_id", "L1")
cfg.set_path(config, "leluxe.source_list_id", "SRC1")
cfg.set_path(config, "leluxe.schema", SCHEMA)
cfg.save(config)

NOW = datetime(2026, 7, 19, 12, 0)


def ms(y, mo, d):
    return str(int(datetime(y, mo, d, 12, 0).timestamp() * 1000))


def wipe():
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("DELETE FROM leluxe_status_log")


def mk(kind, name, *, status="oredered", amt=None, parent=None, created=None, **kw):
    fields = dict(kw.pop("fields", {}) or {})
    if amt is not None:
        fields["Total Amount"] = amt
    return leluxe._insert_row(kind, name, status=status, fields=fields,
                              parent_local_id=parent, date_created=created or ms(2026, 7, 19),
                              **kw)


def set_oa(row_id, val):
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET ordered_at=? WHERE id=?", (val, row_id))


def log_rows(rid=None):
    with db.connect() as c:
        q = "SELECT * FROM leluxe_status_log" + (" WHERE row_id=?" if rid else "")
        return [dict(r) for r in c.execute(q, (rid,) if rid else ())]


# ── 1 · the money rule ────────────────────────────────────────────────────── #
print("money rule")
a = mk("order", "A", amt=190.61)
for v in (300, 326.13, 200):
    mk("item", f"A-{v}", amt=v, parent=a)
b = mk("order", "B", amt=809.17)
mk("item", "B-1", parent=b)
mk("item", "B-2", parent=b)
c_ = mk("order", "C", amt=645.60)
d_ = mk("order", "D")
mk("item", "D-dead", status="cancelled", amt=200, parent=d_)
mk("item", "D-live", amt=300, parent=d_)
g = leluxe_goal.compute(now=NOW)
check("items win over parent amount (826.13)", abs(g["by_day"][18]["usd"] - 2580.90) < 0.01
      and abs(g["month_total"] - 2580.90) < 0.01)
check("today == the 4 orders' values", abs(g["today_total"] - 2580.90) < 0.01)
check("today order count", g["today_orders"] == 4)
u = {x["row"]["name"]: x for x in leluxe_goal._units(leluxe_goal._load(), leluxe_goal.settings())}
check("parent-with-amount-items → item sum", "A-300" in u and "A" not in u)
check("parent alone when items carry no amount", "B" in u and abs(u["B"]["usd"] - 809.17) < 0.01)
check("itemless parent = own amount", abs(u["C"]["usd"] - 645.60) < 0.01)
check("cancelled item excluded", "D-dead" not in u and abs(u["D-live"]["usd"] - 300) < 0.01)

# order-level exclusion: an excluded PARENT status drops the whole order (its
# value is summed from the items, so filtering only the parent row misses it —
# the "excluded 'order number' but the total didn't move" bug)
e = mk("order", "E", status="order number", amt=None)
mk("item", "E-1", status="oredered", amt=400, parent=e)
mk("item", "E-2", status="oredered", amt=500, parent=e)
u2 = {x["row"]["name"]: x for x in leluxe_goal._units(leluxe_goal._load(), leluxe_goal.settings())}
check("order-number order counts by default", "E-1" in u2 and "E-2" in u2)
st_excl = dict(leluxe_goal.settings()); st_excl["excluded"] = ["cancelled", "order number"]
u3 = {x["row"]["name"]: x for x in leluxe_goal._units(leluxe_goal._load(), st_excl)}
check("excluding parent status drops the WHOLE order",
      "E-1" not in u3 and "E-2" not in u3 and "E" not in u3)
check("other orders unaffected by the exclusion", "A-300" in u3 and "D-live" in u3)
wipe()

# ── 2 · windows, pace, streak, coalesce ───────────────────────────────────── #
print("windows / streak / coalesce")
o1 = mk("order", "o1", amt=500, created=ms(2026, 7, 19))
o2 = mk("order", "o2", amt=1000, created=ms(2026, 7, 2))
o3 = mk("order", "o3", amt=800, created=ms(2026, 6, 30))
g = leluxe_goal.compute(now=NOW)
check("today 500", g["today_total"] == 500)
check("month 1500", g["month_total"] == 1500)
check("last month 800", g["last_month_total"] == 800)
check("pct 5", g["pct"] == 5.0)
check("remaining 28500", g["remaining"] == 28500)
check("days_left 13", g["days_left"] == 13)
check("need/day 2192.31", g["need_per_day"] == 2192.31)
check("avg/day 78.95", g["avg_per_day"] == 78.95)
check("by_day day-2 has the 1000", g["by_day"][1]["usd"] == 1000)
check("streak 1 (gap on the 18th)", g["streak_days"] == 1)
check("milestone 0 at 5%", g["milestone"] == 0)
o7 = mk("order", "o7", amt=50, created=ms(2026, 6, 15))
set_oa(o7, ms(2026, 7, 5))
g = leluxe_goal.compute(now=NOW)
check("ordered_at wins over date_created", g["month_total"] == 1550
      and g["last_month_total"] == 800)
mk("order", "o4", amt=100, created=ms(2026, 7, 18))
mk("order", "o5", amt=100, created=ms(2026, 7, 17))
g = leluxe_goal.compute(now=NOW)
check("streak 3 after filling 17+18", g["streak_days"] == 3)
mk("order", "o6", amt=999, created=ms(2025, 3, 15))
w = leluxe_goal.window_totals(2025, now=NOW)
check("year 2025 total", w["orders_usd"] == 999 and w["orders_n"] == 1)

# ── 3 · the bot answers with the same numbers ─────────────────────────────── #
print("telegram bot")
check("bot today", "$500" in telegram_bot.handle_command("today", now=NOW))
check("bot اليوم (arabic)", "$500" in telegram_bot.handle_command("اليوم", now=NOW))
check("bot month has pct", "%" in telegram_bot.handle_command("month", now=NOW))
check("bot last month", "$800" in telegram_bot.handle_command("last month", now=NOW))
check("bot year 2025", "$999" in telegram_bot.handle_command("2025", now=NOW))
check("bot 2025 rd is zero", "$0" in telegram_bot.handle_command("2025 rd", now=NOW))
check("bot unknown → help", "أوامر" in telegram_bot.handle_command("xyzzy", now=NOW))
sent_to = []
telegram_bot._handle_update({"message": {"chat": {"id": 999}, "text": "today"}},
                            "5551234", send=lambda c, t: sent_to.append((c, t)))
check("foreign chat ignored", not sent_to)
# NOTE: _handle_update answers with REAL today (no frozen clock), so assert the
# auth gate + a well-formed reply, not fixture amounts (those rot at midnight).
telegram_bot._handle_update({"message": {"chat": {"id": 5551234}, "text": "today"}},
                            "5551234", send=lambda c, t: sent_to.append((c, t)))
check("owner chat answered", len(sent_to) == 1 and sent_to[0][0] == 5551234
      and "📦" in sent_to[0][1])

# ── 4 · digest: text, once-a-day claim, failed-send release ───────────────── #
print("digest")
_az_healthy, _az_all = leluxe_goal.az.healthy, leluxe_goal.az.all_profiles
leluxe_goal.az.healthy = lambda timeout=2: False           # deterministic: az offline
leluxe_goal._az_down["ts"] = 0
txt = leluxe_goal.digest_text(now=NOW)
check("digest has month total", "$1,750" in txt)
check("digest has goal", "$30,000" in txt)
check("digest has last-month rd line", "RD الشهر الماضي" in txt)
check("digest skips profiles line when az offline", "بروفايل" not in txt)
outbox = []
ok_send = lambda t: (outbox.append(t) or {"ok": True})     # noqa: E731
check("too early → no send",
      leluxe_goal.maybe_send_digest(now=NOW.replace(hour=20), send=ok_send) is None)
check("at 22:00 → sends",
      leluxe_goal.maybe_send_digest(now=NOW.replace(hour=22), send=ok_send) is not None)
check("same day again → deduped",
      leluxe_goal.maybe_send_digest(now=NOW.replace(hour=23), send=ok_send) is None
      and len(outbox) == 1)
day2 = NOW + timedelta(days=1)
check("failed send releases the claim",
      leluxe_goal.maybe_send_digest(now=day2.replace(hour=22),
                                    send=lambda t: {"ok": False}) is None
      and leluxe_goal.maybe_send_digest(now=day2.replace(hour=22), send=ok_send) is not None)
leluxe_goal.save_settings({"digest_enabled": False})
day3 = NOW + timedelta(days=2)
check("disabled → silent",
      leluxe_goal.maybe_send_digest(now=day3.replace(hour=22), send=ok_send) is None)
leluxe_goal.save_settings({"digest_enabled": True})
wipe()

# ── 5 · RD buckets + done-dating (log → DATE SENT → effective) ────────────── #
print("rd pipeline")
rnow = datetime.now()
p_ = mk("order", "P", status="order number")
i1 = mk("item", "i1", status="rd", amt=754, parent=p_, created=ms(2026, 7, 10))
g = leluxe_goal.compute(now=rnow)
check("rd current before flip", g["rd"]["current_count"] == 1
      and g["rd"]["current_usd"] == 754)
row, err = leluxe.set_status(i1, "sent rd")
lr = log_rows(i1)
check("set_status logged the transition", err is None and len(lr) == 1
      and lr[0]["old_status"] == "rd" and lr[0]["new_status"] == "sent rd"
      and lr[0]["source"] == "app")
g = leluxe_goal.compute(now=rnow)
check("flip → rd done this month (log-dated)", g["rd"]["current_count"] == 0
      and g["rd"]["done_month_usd"] == 754 and g["rd"]["done_month_count"] == 1)
lastm = (rnow.replace(day=1) - timedelta(days=1))
i2 = mk("item", "i2", status="sent rd", amt=100, parent=p_,
        fields={"DATE SENT": ms(lastm.year, lastm.month, 15)})
g = leluxe_goal.compute(now=rnow)
check("legacy sent rd dated by DATE SENT (last month)", g["rd"]["done_last_month_usd"] == 100)
old = rnow - timedelta(days=400)
i3 = mk("item", "i3", status="sent rd", amt=55, parent=p_,
        created=ms(old.year, old.month, min(old.day, 28)))
g = leluxe_goal.compute(now=rnow)
check("no log + no DATE SENT → effective date (not this/last month)",
      g["rd"]["done_month_usd"] == 754 and g["rd"]["done_last_month_usd"] == 100)
w = leluxe_goal.window_totals(old.year, now=rnow)
check("old rd counted in its own year", w["rd_usd"] >= 55)
wipe()

# ── 6 · pull hooks: transitions only, dirty skipped, ordered_at survives ──── #
print("pull hooks")
sch = leluxe.schema()


def task(status, updated, name="Pull order"):
    return {"id": "T1", "name": name, "status": {"status": status},
            "date_created": "1770000000000", "date_updated": updated,
            "custom_fields": [], "tags": []}


check("created", leluxe.upsert_from_clickup(task("oredered", "1"), sch) == "created")
rid = leluxe.get_by_task("T1")["id"]
check("no log on insert", log_rows(rid) == [])
check("updated", leluxe.upsert_from_clickup(task("rd", "2"), sch) == "updated")
lr = log_rows(rid)
check("pull transition logged", len(lr) == 1 and lr[0]["source"] == "pull"
      and lr[0]["old_status"] == "oredered" and lr[0]["new_status"] == "rd")
check("unchanged pull adds nothing",
      leluxe.upsert_from_clickup(task("rd", "2"), sch) == "unchanged" and len(log_rows(rid)) == 1)
with db.connect() as c:
    c.execute("UPDATE leluxe_orders SET sync_state='dirty' WHERE id=?", (rid,))
check("dirty pull skipped, no log",
      leluxe.upsert_from_clickup(task("sent rd", "3"), sch) == "skipped_dirty"
      and len(log_rows(rid)) == 1)
with db.connect() as c:
    c.execute("UPDATE leluxe_orders SET sync_state='synced', ordered_at='123' WHERE id=?", (rid,))
check("pull update keeps ordered_at",
      leluxe.upsert_from_clickup(task("sent rd", "4"), sch) == "updated"
      and leluxe.get_row(rid)["ordered_at"] == "123" and len(log_rows(rid)) == 2)

# ── 7 · merge hook (sync from AZ (2)) logs with source=sync ───────────────── #
print("merge hook")
base = {"name": "M", "status": "oredered", "due_date": None,
        "description": "", "tags": [], "fields": {}}
mrid = mk("order", "M", status="oredered", amt=None,
          extra={"source_task_id": "S-M", "source_base": base, "source_cu_updated": "1"})
src = {"id": "S-M", "name": "M", "status": {"status": "rd"}, "date_updated": "2",
       "custom_fields": [], "tags": []}
res = leluxe._merge_row(mrid, src, sch, set(), lambda f: {})
lr = log_rows(mrid)
check("merge applied the AZ status", res == "updated"
      and leluxe.get_row(mrid)["status"] == "rd")
check("merge transition logged as sync", len(lr) == 1 and lr[0]["source"] == "sync")
wipe()

# ── 8 · backfill: source id, name key, inherit; dry-run writes nothing ────── #
print("backfill")
r1 = mk("order", "R1 anything", created=ms(2026, 7, 14), extra={"source_task_id": "S10"})
r2 = mk("order", "Order # 113-1111111-2222222", created=ms(2026, 7, 14))
r3 = mk("item", "Widget", parent=r1, created=ms(2026, 7, 14))
r4 = mk("package", "📦 GWD1", parent=r1, created=ms(2026, 7, 14))
r5 = mk("item", "Gadget", parent=r4, created=ms(2026, 7, 14))
FAKE = [
    {"id": "S10", "name": "R1 in the source", "date_created": "1770000000000"},
    {"id": "S11", "name": "# 113-1111111-2222222 watch", "date_created": "1771000000000"},
    {"id": "C1", "name": "Widget", "parent": "S10", "date_created": "1770000000001"},
    {"id": "C2", "name": "Gadget", "parent": "S10", "date_created": "1770000000002"},
]
rep = leluxe_goal.backfill_ordered_at(dry_run=True, fetch=lambda: (FAKE, None))
check("dry-run counts", rep["filled"] == 5 and rep["matched_source"] == 1
      and rep["matched_name"] == 3 and rep["matched_inherit"] == 1)
check("dry-run writes nothing", leluxe.get_row(r1)["ordered_at"] is None)
rep = leluxe_goal.backfill_ordered_at(dry_run=False, fetch=lambda: (FAKE, None))
got = {r: (leluxe.get_row(r)["ordered_at"]) for r in (r1, r2, r3, r4, r5)}
check("real run fills true dates", got[r1] == "1770000000000"
      and got[r2] == "1771000000000" and got[r3] == "1770000000001"
      and got[r5] == "1770000000002" and got[r4] == "1770000000000")
rep = leluxe_goal.backfill_ordered_at(dry_run=False, fetch=lambda: (FAKE, None))
check("second run respects already_set", rep["filled"] == 0 and rep["already_set"] == 5)
wipe()

# ── 9 · az profiles summary (stubbed az-tool) ─────────────────────────────── #
print("profiles")
leluxe_goal.az.healthy = lambda timeout=2: True
leluxe_goal.az.all_profiles = lambda force=False: [
    {"all_tags": ["ready to order 2", "muti vpn"]},
    {"all_tags": ["READY TO ORDER 2"]},
    {"all_tags": ["az-good"]},
]
leluxe_goal._az_down["ts"] = 0
p = leluxe_goal.profiles_summary(refresh=True)
check("tag match is case-blind", p["count"] == 2 and p["total_profiles"] == 3)
check("capacity from defaults", p["capacity_min"] == 800 and p["capacity_max"] == 2000)
check("tags union deduped", len(p["all_tags"]) == 3)
leluxe_goal.az.healthy = lambda timeout=2: False
leluxe_goal._az_down["ts"] = 0
check("az offline → degrade", leluxe_goal.profiles_summary(refresh=True)["offline"] is True)
leluxe_goal.az.healthy, leluxe_goal.az.all_profiles = _az_healthy, _az_all

# ── 10 · settings validation + roundtrip ──────────────────────────────────── #
print("settings")
check("hour 25 rejected", leluxe_goal.save_settings({"digest_hour": 25})[1] is not None)
check("min>max rejected",
      leluxe_goal.save_settings({"acct_min_usd": 900, "acct_max_usd": 100})[1] is not None)
check("goal 0 rejected", leluxe_goal.save_settings({"goal_usd": 0})[1] is not None)
check("empty tag rejected", leluxe_goal.save_settings({"ready_tag": " "})[1] is not None)
st, err = leluxe_goal.save_settings({"goal_usd": 45000, "rd_done": ["sent rd", "sent no rd"]})
check("save ok", err is None)
check("roundtrip", leluxe_goal.settings()["goal_usd"] == 45000
      and leluxe_goal.settings()["rd_done"] == ["sent rd", "sent no rd"])

# ── 11 · endpoints are admin-only ─────────────────────────────────────────── #
print("routes")
import app as appmod  # noqa: E402
tc = appmod.app.test_client()
for path, method in (("/api/leluxe/goal", "get"), ("/api/leluxe/goal_settings", "get"),
                     ("/api/leluxe/goal_digest", "post"), ("/api/leluxe/goal_backfill", "post")):
    r = getattr(tc, method)(path)
    check(f"anon {path} blocked", r.status_code in (401, 403))

print()
if fails:
    raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
print(f"ALL CHECKS PASSED ({_TMP})")
