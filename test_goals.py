#!/usr/bin/env python3
"""
Self-checks for the 🏆 Goals engine (goals.py) + its routes.

Proves: the value-unit rule on raw ClickUp tasks (descendants win over the
parent amount, Order # parents never double-count, exclusion at both levels,
orphan promotion, nesting); dropdown Brand decoding by orderindex and uuid
with top-inheritance and case-blind claims; the missing-brand-option warning;
Asia/Amman window edges; the Otlobly purchases category (total_usd, AED
fallback, inclusive dates); settings validation + roundtrip; the stamp-keyed
cache (hit, bump-invalidation, refresh, TTL expiry, stale-on-error fallback);
webhook signature verification; and that every /api/goals* route is
admin-only while /webhook/clickup never bumps the stamp without a valid
signature.

    ./.venv/bin/python test_goals.py
"""

import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-goals-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)      # keep config.json/purchases.json off the repo
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["CLICKUP_API_TOKEN"] = "test-token"  # never used: fetch is injected
os.environ["LELUXE_PACE"] = "0"
os.environ["LELUXE_PUSH_DISABLED"] = "1"
for k in ("LELUXE_DIGEST", "LELUXE_TG_BOT", "GAASH_MAILER",
          "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)                     # every daemon stays off

import db      # noqa: E402
import goals   # noqa: E402

db.init_db()

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


AMT = goals.DEFAULTS["amount_field_id"]
BRD = goals.DEFAULTS["brand_field_id"]
LID_IT = "901524960550"
LID_W = "901520351506"
BRAND_OPTS = [{"id": "opt-gc", "name": "Graphic card", "orderindex": 0},
              {"id": "opt-pc", "name": "Other IT", "orderindex": 1},
              {"id": "opt-au", "name": "GOLD", "orderindex": 2}]


def ms_amman(y, m, d, hh=12):
    return int(datetime(y, m, d, hh, tzinfo=goals.AMMAN).timestamp() * 1000)


def ms_utc(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000)


def task(tid, name, status, created, parent=None, amount=None, brand=None, opts=True):
    amt = {"id": AMT, "name": "Total Amount", "type": "currency", "type_config": {}}
    if amount is not None:
        amt["value"] = str(amount)
    brd = {"id": BRD, "name": "Brand", "type": "drop_down",
           "type_config": {"options": BRAND_OPTS if opts else BRAND_OPTS[:1]}}
    if brand is not None:
        brd["value"] = brand
    return {"id": tid, "name": name, "status": {"status": status}, "parent": parent,
            "date_created": str(created), "url": f"https://app.clickup.com/t/{tid}",
            "custom_fields": [amt, brd]}


AUG10 = ms_amman(2026, 8, 10)
IT_TASKS = [
    # priced child wins; unpriced sibling stays as a $0 warning unit
    task("t1", "Order # 111", "order number", AUG10),
    task("t1a", "RTX 5070", "ordered", AUG10, parent="t1", amount=900, brand=0),
    task("t1b", "SSD", "ordered", AUG10, parent="t1"),
    # no priced children → the Order # parent's own amount counts (flagged)
    task("t2", "Order # 222", "order number", AUG10, amount=500),
    task("t2a", "GPU", "ordered", AUG10, parent="t2"),
    # standalone product, PC brand by uuid
    task("t3", "Mini PC", "ordered", AUG10, amount=300, brand="opt-pc"),
    # cancelled top drops the whole order
    task("t4", "Order # 444", "cancelled", AUG10),
    task("t4a", "RTX 5090", "ordered", AUG10, parent="t4", amount=999),
    # orphan (parent never fetched) is promoted to a top
    task("t5", "Orphan SSD", "ordered", AUG10, parent="zzz", amount=250),
    # item-level cancellation drops just that item
    task("t6", "Order # 666", "order number", AUG10),
    task("t6a", "Bad card", "cancelled", AUG10, parent="t6", amount=400),
    task("t6b", "Good card", "ordered", AUG10, parent="t6", amount=200),
    # Amman window edges: 21:30 UTC = 00:30 next day in Amman
    task("t7", "Edge in", "ordered", ms_utc(2026, 8, 4, 21, 30), amount=111),
    task("t8", "Edge out", "ordered", ms_utc(2026, 9, 5, 21, 30), amount=77),
    # child inherits the top's PC brand
    task("t9", "Order # 999", "order number", AUG10, brand="opt-pc"),
    task("t9a", "Custom build", "ordered", AUG10, parent="t9", amount=600),
]
W_TASKS = [
    task("w1", "Rolex", "oredered", ms_amman(2026, 8, 6), amount=1000),
    task("w2", "Fake", "cancelled", ms_amman(2026, 8, 7), amount=500),
    task("w3", "Omega", "sent rd", ms_amman(2026, 8, 20), amount=250),
]

CALLS = {"n": 0}


def fake_fetch(lid):
    CALLS["n"] += 1
    return (IT_TASKS if lid == LID_IT else W_TASKS), None


NOW = datetime(2026, 8, 20, 10, 0, tzinfo=goals.AMMAN)

# ── 1 · the value-unit rule on raw tasks ──────────────────────────────────── #
print("unit rule")
units, opts = goals._units_from_tasks(IT_TASKS, ["cancelled"], AMT, BRD)
by_id = {u["task_id"]: u for u in units}
check("priced child wins over Order # parent", by_id["t1a"]["usd"] == 900 and "t1" not in by_id)
check("unpriced sibling is a $0 warning unit",
      by_id["t1b"]["usd"] == 0 and by_id["t1b"]["warn"] == "no_amount")
check("parent fallback counts + flags",
      by_id["t2"]["usd"] == 500 and by_id["t2"]["warn"] == "parent_fallback"
      and "t2a" not in by_id)
check("cancelled top drops the whole order", "t4" not in by_id and "t4a" not in by_id)
check("orphan promoted", by_id["t5"]["usd"] == 250)
check("item-level cancel drops just the item",
      "t6a" not in by_id and by_id["t6b"]["usd"] == 200 and "t6" not in by_id)
check("no double count anywhere",
      round(sum(u["usd"] for u in units), 2) == 900 + 500 + 300 + 250 + 200 + 111 + 77 + 600)
check("brand options collected", opts == ["GOLD", "Graphic card", "Other IT"])

# ── 2 · brand decode + classification ─────────────────────────────────────── #
print("brands")
check("brand by orderindex", by_id["t1a"]["brand"] == "Graphic card")
check("brand by uuid", by_id["t3"]["brand"] == "Other IT")
check("child inherits top brand", by_id["t9a"]["brand"] == "Other IT")
cats = [c for c in goals.DEFAULTS["categories"] if c.get("list_id") == LID_IT]
split = goals._classify(units, cats)
check("pc claims case-blind", {u["task_id"] for u in split["pc"]} == {"t3", "t9a"})
check("rest takes the remainder",
      {u["task_id"] for u in split["it"]} == {"t1a", "t1b", "t2", "t5", "t6b", "t7", "t8"})

# ── 3 · compute: windows, totals, purchases ───────────────────────────────── #
print("compute")
(_TMP / "purchases.json").write_text(json.dumps({"purchase_orders": [
    {"po_id": "PO-0001", "order_placed": "2026-08-10", "total_usd": 400,
     "created_at": "2026-08-10T09:00:00+03:00", "status": "PLACED", "packages": []},
    {"po_id": "PO-0002", "order_placed": "", "total_aed": 367.25,
     "created_at": "2026-08-12T10:00:00+03:00", "status": "PLACED", "packages": []},
    {"po_id": "PO-0003", "order_placed": "2026-07-30", "total_usd": 5000,
     "created_at": "2026-07-30T10:00:00+03:00", "status": "PLACED", "packages": []},
    {"po_id": "PO-0004", "order_placed": "2026-09-05", "total_usd": 50,
     "created_at": "2026-09-01T10:00:00+03:00", "status": "PLACED", "packages": []},
], "seq": 4}))
snap = goals.compute(now=NOW, fetch=fake_fetch)
cat = {c["key"]: c for c in snap["categories"]}
check("it actual (edge-in counts, edge-out doesn't)", cat["it"]["actual"] == 1961.0)
check("pc actual", cat["pc"]["actual"] == 900.0)
check("watches actual (cancelled dropped)", cat["watches"]["actual"] == 1250.0)
check("otlobly actual (usd + aed fallback + inclusive end)",
      cat["otlobly"]["actual"] == 550.0)
check("totals", snap["totals"]["actual"] == 4661.0 and snap["totals"]["target"] == 45000.0)
check("window days", snap["window"]["days_total"] == 32 and snap["window"]["elapsed"] == 16
      and snap["window"]["days_left"] == 17 and snap["window"]["active"])
check("need per day", snap["totals"]["need_per_day"] == round((45000 - 4661) / 17, 2))
check("by_day edge slot", snap["by_day"][0]["d"] == "2026-08-05"
      and snap["by_day"][0]["usd"] == 111.0)
check("no clickup error", snap["clickup_error"] is None)
check("warnings surfaced", any(w["kind"] == "no_amount" for w in cat["it"]["warnings"])
      and any(w["kind"] == "parent_fallback" for w in cat["it"]["warnings"]))
check("no brand_missing when the claimed brand exists",
      not any(w["kind"] == "brand_missing" for w in cat["pc"]["warnings"]))

# ── 4 · missing brand option warns ────────────────────────────────────────── #
print("brand_missing")
st, err = goals.save_settings({"categories": [{"key": "pc", "brands": ["NoSuchBrand"]}]})
check("brands save ok", err is None)
goals.bump_stamp("test")
snap2 = goals.compute(now=NOW, fetch=fake_fetch)
cat2 = {c["key"]: c for c in snap2["categories"]}
check("brand_missing warning", any(w["kind"] == "brand_missing" and w["name"] == "NoSuchBrand"
                                   for w in cat2["pc"]["warnings"]))
check("unmatched brand units fall to rest", cat2["it"]["actual"] == 1961.0 + 900.0)
goals.save_settings({"categories": [{"key": "pc", "brands": ["other it"]}]})  # case-blind on purpose
goals.bump_stamp("test")
snap3 = goals.compute(now=NOW, fetch=fake_fetch)
check("brand match is case-blind",
      {c["key"]: c for c in snap3["categories"]}["pc"]["actual"] == 900.0)

# ── 5 · cache + stamp + refresh + TTL + stale-on-error ────────────────────── #
print("cache")
n0 = CALLS["n"]
goals.compute(now=NOW, fetch=fake_fetch)
check("cache hit → no refetch", CALLS["n"] == n0)
goals.bump_stamp("webhook")
goals.compute(now=NOW, fetch=fake_fetch)
check("stamp bump → refetch both lists", CALLS["n"] == n0 + 2)
goals.compute(now=NOW, fetch=fake_fetch, refresh=True)
check("refresh=1 → refetch", CALLS["n"] == n0 + 4)
goals._CACHE["at"] -= goals.HARD_TTL + 1
goals.compute(now=NOW, fetch=fake_fetch)
check("hard TTL expiry → refetch", CALLS["n"] == n0 + 6)


def err_fetch(lid):
    return None, f"boom {lid}"


snap4 = goals.compute(now=NOW, fetch=err_fetch, refresh=True)
cat4 = {c["key"]: c for c in snap4["categories"]}
check("fetch error → stale units kept", cat4["watches"]["actual"] == 1250.0)
check("fetch error surfaced", "boom" in (snap4["clickup_error"] or ""))
snap5 = goals.compute(now=NOW, fetch=fake_fetch, refresh=True)
check("recovers after error", snap5["clickup_error"] is None)

# ── 6 · settings validation + roundtrip ───────────────────────────────────── #
print("settings")
check("bad date rejected", goals.save_settings({"window_start": "nope"})[1] is not None)
check("start>end rejected",
      goals.save_settings({"window_start": "2026-09-06", "window_end": "2026-09-05"})[1] is not None)
check("target 0 rejected",
      goals.save_settings({"categories": [{"key": "it", "target": 0}]})[1] is not None)
check("unknown category rejected",
      goals.save_settings({"categories": [{"key": "hax", "target": 5}]})[1] is not None)
check("empty brands rejected",
      goals.save_settings({"categories": [{"key": "pc", "brands": []}]})[1] is not None)
check("non-numeric list rejected",
      goals.save_settings({"categories": [{"key": "it", "list_id": "abc"}]})[1] is not None)
st, err = goals.save_settings({"combine_it_pc": True, "window_end": "2026-09-10",
                               "categories": [{"key": "it", "target": 25000,
                                               "label": "IT + metals"}]})
check("save ok", err is None)
rt = goals.settings()
check("roundtrip", rt["combine_it_pc"] is True and rt["window_end"] == "2026-09-10"
      and rt["categories"][0]["target"] == 25000.0
      and rt["categories"][0]["label"] == "IT + metals")
check("structure stays code-owned", rt["categories"][0]["mode"] == "rest"
      and rt["categories"][3]["mode"] == "purchases")
goals.save_settings({"combine_it_pc": False, "window_end": "2026-09-05",
                     "categories": [{"key": "it", "target": 20000, "label": "IT products"}]})

# ── 7 · webhook signature + info ──────────────────────────────────────────── #
print("webhook")
check("no secrets → trust nobody", goals.verify_signature(b"x", "sig") is False)
db.set_setting(goals.WEBHOOK_KEY, {"endpoint": "https://x/webhook/clickup",
                                   "hooks": {LID_IT: {"id": "a", "secret": "s3"},
                                             LID_W: {"id": "b", "secret": "s4"}}})
good = hmac.new(b"s4", b'{"event":"taskUpdated"}', hashlib.sha256).hexdigest()
check("good signature (any hook's secret)",
      goals.verify_signature(b'{"event":"taskUpdated"}', good) is True)
check("bad signature", goals.verify_signature(b'{"event":"taskUpdated"}', "0" * 64) is False)
info = goals.webhook_info()
check("info configured", info["configured"] is True and info["lists"] == [LID_IT, LID_W])

# ── 8 · routes: admin-only APIs, signature-gated webhook ──────────────────── #
print("routes")
import app as appmod  # noqa: E402
tc = appmod.app.test_client()
for path, method in (("/api/goals", "get"), ("/api/goals/settings", "get"),
                     ("/api/goals/settings", "post"), ("/api/goals/webhook_setup", "post")):
    r = getattr(tc, method)(path)
    check(f"anon {method.upper()} {path} blocked", r.status_code in (401, 403))

payload = b'{"event":"taskUpdated","webhook_id":"a"}'
before = json.dumps(db.get_setting(goals.STAMP_KEY))
r = tc.post("/webhook/clickup", data=payload,
            headers={"X-Signature": "f" * 64, "Content-Type": "application/json"})
check("bad sig → 200, no bump", r.status_code == 200
      and json.dumps(db.get_setting(goals.STAMP_KEY)) == before)
sig = hmac.new(b"s3", payload, hashlib.sha256).hexdigest()
r = tc.post("/webhook/clickup", data=payload,
            headers={"X-Signature": sig, "Content-Type": "application/json"})
after = db.get_setting(goals.STAMP_KEY)
check("good sig → 200 + stamp bump", r.status_code == 200
      and json.dumps(after) != before and after.get("src") == "webhook")
db.set_setting(goals.WEBHOOK_KEY, {})
r = tc.post("/webhook/clickup", data=payload,
            headers={"X-Signature": sig, "Content-Type": "application/json"})
check("secrets cleared → 200, no bump", r.status_code == 200
      and json.dumps(db.get_setting(goals.STAMP_KEY)) == json.dumps(after))

print()
if fails:
    raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
print(f"ALL CHECKS PASSED ({_TMP})")
