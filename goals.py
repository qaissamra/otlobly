#!/usr/bin/env python3
"""
Goals engine — the 🏆 Goals page: one campaign window (2026-08-05 → 2026-09-05
by default) summing order values from three kinds of sources into editable
per-category targets:

  clickup lists  — "IT Products" 901524960550 (mode rest + a PC brand split)
                   and "Le Luxe Products" 901520351506 (mode all), value =
                   the "Total Amount" currency field (USD despite its ILS
                   label — owner-confirmed), dated by task date_created.
  purchases      — Otlobly POs from purchases.json via pnl._cogs_po_rows
                   (total_usd, AED fallback; order_placed → created_at date).

Value-unit rule (same rule as leluxe_goal, ported to raw ClickUp tasks): a
top task's value = its non-excluded descendants' amounts when any descendant
carries one, else the top's own amount — so "Order # …" parents and their
product subtasks never double-count. Excluded statuses drop at both levels.

Freshness: ClickUp results are reduced to units and cached per worker, keyed
by the shared `goals:stamp` row in the settings table. The /webhook/clickup
receiver (and every settings save) bumps the stamp, so a value typed into
ClickUp invalidates every worker's cache within one poll. Hard TTL 120s
covers dropped webhooks. All day math is Asia/Amman — Render runs UTC and a
naive fromtimestamp would shift day boundaries by 3h.

Pure module: no Flask, no threads.
"""

import copy
import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import db

try:
    from zoneinfo import ZoneInfo
    AMMAN = ZoneInfo("Asia/Amman")
except Exception:                                   # pragma: no cover
    AMMAN = timezone(timedelta(hours=3))            # Jordan is fixed UTC+3

SETTINGS_KEY = "goals:multi"
STAMP_KEY = "goals:stamp"
WEBHOOK_KEY = "goals:webhook"
HARD_TTL = 120          # seconds a cached ClickUp reduction may serve without a stamp match
BREAKDOWN_CAP = 60      # units listed per category (totals always cover everything)
EVENTS = ["taskCreated", "taskUpdated", "taskDeleted", "taskStatusUpdated"]

ORDER_NAME_RE = re.compile(r"^\s*order\s*#", re.I)

DEFAULTS = {
    "window_start": "2026-08-05",
    "window_end": "2026-09-05",
    "combine_it_pc": False,
    "excluded": ["cancelled"],
    "team_id": "90151514222",
    "amount_field_id": "c7243d42-56b6-4a85-8634-e2f948f72be5",   # Total Amount
    "brand_field_id": "839ed479-bea9-4447-87d6-963dfe23843a",    # Brand
    "categories": [
        {"key": "it", "label": "IT products", "target": 20000.0,
         "mode": "rest", "list_id": "901524960550"},
        {"key": "pc", "label": "Mini & normal PC", "target": 5000.0,
         "mode": "brand", "list_id": "901524960550", "brands": ["PC"]},
        {"key": "watches", "label": "Watches (Le Luxe)", "target": 10000.0,
         "mode": "all", "list_id": "901520351506"},
        {"key": "otlobly", "label": "Otlobly", "target": 10000.0,
         "mode": "purchases"},
    ],
}

# What the inline editor may change; structure (key/mode) stays code-owned so a
# stale saved blob can never break the engine.
_EDITABLE_CAT = ("label", "target", "brands", "list_id")
CLICKUP_MODES = ("brand", "rest", "all")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def settings():
    st = copy.deepcopy(DEFAULTS)
    saved = db.get_setting(SETTINGS_KEY)
    if isinstance(saved, dict):
        for k in ("window_start", "window_end", "combine_it_pc", "excluded",
                  "team_id", "amount_field_id", "brand_field_id"):
            if k in saved:
                st[k] = saved[k]
        by_key = {c.get("key"): c for c in saved.get("categories") or []
                  if isinstance(c, dict)}
        for cat in st["categories"]:
            s = by_key.get(cat["key"])
            if s:
                for f in _EDITABLE_CAT:
                    if f in s:
                        cat[f] = s[f]
    return st


def _valid_day(s):
    try:
        return date.fromisoformat(str(s or "").strip())
    except ValueError:
        return None


def save_settings(body):
    """Validate + persist the inline Goals settings. Returns (settings, error)."""
    st = settings()
    b = body or {}
    if "window_start" in b or "window_end" in b:
        start = _valid_day(b.get("window_start", st["window_start"]))
        end = _valid_day(b.get("window_end", st["window_end"]))
        if not start or not end:
            return None, "window dates must be YYYY-MM-DD"
        if start > end:
            return None, "window start must be on or before its end"
        st["window_start"], st["window_end"] = start.isoformat(), end.isoformat()
    if "combine_it_pc" in b:
        st["combine_it_pc"] = bool(b["combine_it_pc"])
    if "excluded" in b:
        if not isinstance(b["excluded"], list):
            return None, "excluded must be a list of statuses"
        st["excluded"] = [str(s).strip() for s in b["excluded"] if str(s).strip()]
    for k in ("team_id", "amount_field_id", "brand_field_id"):
        if k in b:
            v = str(b[k] or "").strip()
            if not v:
                return None, f"{k} is required"
            st[k] = v
    if "categories" in b:
        if not isinstance(b["categories"], list):
            return None, "categories must be a list"
        cats = {c["key"]: c for c in st["categories"]}
        for inc in b["categories"]:
            if not isinstance(inc, dict) or inc.get("key") not in cats:
                return None, f"unknown category {(inc or {}).get('key')!r}"
            cat = cats[inc["key"]]
            if "label" in inc:
                label = str(inc["label"] or "").strip()
                if not label:
                    return None, f"{cat['key']}: label is required"
                cat["label"] = label
            if "target" in inc:
                try:
                    target = float(inc["target"])
                except (TypeError, ValueError):
                    return None, f"{cat['key']}: target must be a number"
                if target <= 0:
                    return None, f"{cat['key']}: target must be > 0"
                cat["target"] = target
            if "brands" in inc and cat["mode"] == "brand":
                if not isinstance(inc["brands"], list):
                    return None, f"{cat['key']}: brands must be a list"
                brands = [str(x).strip() for x in inc["brands"] if str(x).strip()]
                if not brands:
                    return None, f"{cat['key']}: needs at least one brand name"
                cat["brands"] = brands
            if "list_id" in inc and cat["mode"] in CLICKUP_MODES:
                lid = str(inc["list_id"] or "").strip()
                if not lid.isdigit():
                    return None, f"{cat['key']}: list_id must be numeric"
                cat["list_id"] = lid
    db.set_setting(SETTINGS_KEY, st)
    return st, None


# --------------------------------------------------------------------------- #
# The freshness stamp — the cross-worker cache bus (SQLite settings row)
# --------------------------------------------------------------------------- #
def bump_stamp(src="manual"):
    db.set_setting(STAMP_KEY, {"at": datetime.now(AMMAN).isoformat(timespec="seconds"),
                               "src": src, "r": os.urandom(4).hex()})


def _stamp_token():
    return json.dumps(db.get_setting(STAMP_KEY), sort_keys=True, default=str)


# --------------------------------------------------------------------------- #
# ClickUp source → value units
# --------------------------------------------------------------------------- #
def _norm(s):
    return " ".join(str(s or "").casefold().split())


def _msint(v):
    try:
        n = int(str(v).strip())
        return n if n > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def _mday(ms):
    """ms-epoch → Amman date (None-safe)."""
    return datetime.fromtimestamp(ms / 1000, AMMAN).date() if ms else None


def _fetch_tasks(list_id):
    """Every task in a list (closed + subtasks), same paging as the AZ (2)
    fetch in leluxe_goal. Returns (tasks, error)."""
    import leluxe
    if not leluxe._token():
        return None, "CLICKUP_API_TOKEN is not set"
    if not list_id:
        return None, "list id is not set"
    tasks, page = [], 0
    while True:
        s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/list/{list_id}/task"
                                f"?include_closed=true&subtasks=true&page={page}")
        if s_ != 200:
            return None, (f"list {list_id} fetch failed ({s_}): "
                          f"{(body or {}).get('_error') or body}")
        batch = (body or {}).get("tasks") or []
        tasks.extend(batch)
        if (body or {}).get("last_page", True) or not batch:
            break
        page += 1
    return tasks, None


def _cf(task, field_id, name_fallback):
    """A task's custom-field entry by id, falling back to a case-blind name."""
    want = _norm(name_fallback)
    by_name = None
    for cf in task.get("custom_fields") or []:
        if cf.get("id") == field_id:
            return cf
        if by_name is None and _norm(cf.get("name")) == want:
            by_name = cf
    return by_name


def _amount_of(task, field_id):
    cf = _cf(task, field_id, "total amount")
    if not cf:
        return 0.0
    try:
        return float(cf.get("value"))
    except (TypeError, ValueError):
        return 0.0


def _brand_of(task, field_id):
    """Brand dropdown → option NAME, resolved from the task's own inline
    option set (the leluxe._decode_task recipe)."""
    import leluxe
    cf = _cf(task, field_id, "brand")
    if not cf or cf.get("value") in (None, "", []):
        return None
    inline = [{"id": o.get("id"),
               "name": o.get("name") if o.get("name") is not None else o.get("label"),
               "orderindex": o.get("orderindex")}
              for o in (cf.get("type_config") or {}).get("options") or []]
    val = leluxe.decode_value({"type": "drop_down", "options": inline}, cf.get("value"))
    return str(val) if val is not None else None


def _status_of(task):
    return _norm((task.get("status") or {}).get("status"))


def _units_from_tasks(tasks, excluded, amount_fid, brand_fid):
    """Raw ClickUp tasks → value units + the list's known Brand option names.
    Same rule as leluxe_goal._units: descendants win when any carries an
    amount, else the top counts; exclusion applies at both levels; orphans
    (parent not in the fetch) are promoted to tops. Units carry an optional
    `warn` — the page's trust surface."""
    excluded = {_norm(s) for s in excluded or []}
    by_id = {t["id"]: t for t in tasks or [] if t.get("id")}
    kids = {}
    for t in tasks or []:
        p = t.get("parent")
        if p and p in by_id:
            kids.setdefault(p, []).append(t)
    tops = [t for t in tasks or [] if not t.get("parent") or t.get("parent") not in by_id]

    option_names = set()
    for t in tasks or []:
        cf = _cf(t, brand_fid, "brand")
        for o in ((cf or {}).get("type_config") or {}).get("options") or []:
            n = o.get("name") if o.get("name") is not None else o.get("label")
            if n:
                option_names.add(str(n))

    def descend(t, depth=0):
        out = []
        for k in kids.get(t["id"], []):
            out.append(k)
            if depth < 6:                       # nesting cap, like the backfill walk
                out.extend(descend(k, depth + 1))
        return out

    def unit(task, top, usd, warn=None):
        return {"usd": round(float(usd or 0.0), 2),
                "day": _mday(_msint(task.get("date_created"))) or _mday(_msint(top.get("date_created"))),
                "name": task.get("name") or "",
                "task_id": task.get("id"),
                "brand": _brand_of(task, brand_fid) or _brand_of(top, brand_fid),
                "status": (task.get("status") or {}).get("status") or "",
                "top_name": top.get("name") or "",
                "url": task.get("url"),
                "warn": warn}

    units = []
    for top in tops:
        if _status_of(top) in excluded:         # order-level excluded → whole order out
            continue
        live = [d for d in descend(top) if _status_of(d) not in excluded]
        if any(_amount_of(d, amount_fid) > 0 for d in live):
            for d in live:
                usd = _amount_of(d, amount_fid)
                units.append(unit(d, top, usd, warn=None if usd > 0 else "no_amount"))
        else:
            usd = _amount_of(top, amount_fid)
            warn = None
            if usd <= 0:
                warn = "no_amount"
            elif live and ORDER_NAME_RE.match(top.get("name") or ""):
                warn = "parent_fallback"        # products unpriced → the Order # total counted
            units.append(unit(top, top, usd, warn=warn))
    return units, sorted(option_names)


def _purchases_units(start_iso, end_iso):
    """Otlobly category: the Purchases-page POs, exactly the P&L selection."""
    import cfg
    import pnl
    config = cfg.load()
    units = []
    for po, usd in pnl._cogs_po_rows(config, start_iso, end_iso):
        day = _valid_day(pnl._po_date(po)[:10])
        pid = po.get("po_id") or ""
        extra = po.get("amazon_order_number") or po.get("ship_to") or ""
        units.append({"usd": round(float(usd or 0.0), 2), "day": day,
                      "name": (pid + (" · " + extra if extra else "")) or "PO",
                      "task_id": pid, "brand": None,
                      "status": po.get("status") or "", "top_name": pid,
                      "url": None, "warn": None if usd else "no_amount"})
    return units


# --------------------------------------------------------------------------- #
# Cache (per worker) + compute
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_CACHE = {"token": None, "at": 0.0, "lists": None, "error": None}


def _clickup_list_ids(st):
    out = []
    for c in st["categories"]:
        lid = str(c.get("list_id") or "")
        if c.get("mode") in CLICKUP_MODES and lid and lid not in out:
            out.append(lid)
    return out


def _clickup_lists(st, refresh=False, fetch=None):
    """{list_id: {"units": [...], "brand_options": [...]}} — reduced units
    only, never raw tasks (each AZ task embeds full inline option sets and
    the instance has 512MB). Serves the cache while the shared stamp matches
    and it's younger than HARD_TTL; on fetch failure keeps a list's previous
    reduction so the page degrades to stale-but-present."""
    token = _stamp_token()
    if (not refresh and _CACHE["lists"] is not None and _CACHE["token"] == token
            and time.time() - _CACHE["at"] < HARD_TTL):
        return _CACHE["lists"], _CACHE["error"]
    with _LOCK:
        token = _stamp_token()                   # re-check inside the lock
        if (not refresh and _CACHE["lists"] is not None and _CACHE["token"] == token
                and time.time() - _CACHE["at"] < HARD_TTL):
            return _CACHE["lists"], _CACHE["error"]
        fetch = fetch or _fetch_tasks
        lists, err = {}, None
        for lid in _clickup_list_ids(st):
            tasks, e = fetch(lid)
            if e:
                err = err or str(e)
                prev = (_CACHE["lists"] or {}).get(lid)
                if prev:
                    lists[lid] = prev            # stale beats blank
                continue
            units, opts = _units_from_tasks(tasks, st["excluded"],
                                            st["amount_field_id"], st["brand_field_id"])
            lists[lid] = {"units": units, "brand_options": opts}
        _CACHE.update({"token": token, "at": time.time(), "lists": lists, "error": err})
        return lists, err


def _classify(units, cats):
    """Split one list's units among its categories: brand-mode categories
    claim first (unit's own brand, already top-inherited); rest/all take the
    remainder."""
    out = {c["key"]: [] for c in cats}
    brand_cats = [(c["key"], {_norm(b) for b in c.get("brands") or []})
                  for c in cats if c.get("mode") == "brand"]
    rest_keys = [c["key"] for c in cats if c.get("mode") in ("rest", "all")]
    for u in units:
        b = _norm(u.get("brand"))
        key = next((k for k, names in brand_cats if b and b in names), None)
        if key is None and rest_keys:
            key = rest_keys[0]
        if key is not None:
            out[key].append(u)
    return out


def _ser_unit(u):
    return {**u, "day": u["day"].isoformat() if u.get("day") else None}


def compute(refresh=False, now=None, fetch=None):
    """The whole 🏆 Goals snapshot. Never raises for ClickUp problems — the
    page renders whatever it has plus `clickup_error`."""
    st = settings()
    now = now or datetime.now(AMMAN)
    today = now.date()
    start = _valid_day(st["window_start"]) or today
    end = _valid_day(st["window_end"]) or today
    if start > end:
        start, end = end, start
    days_total = (end - start).days + 1
    if today < start:
        elapsed, days_left = 0, days_total
    elif today > end:
        elapsed, days_left = days_total, 0
    else:
        elapsed, days_left = (today - start).days + 1, (end - today).days + 1

    lists, cerr = _clickup_lists(st, refresh=refresh, fetch=fetch)

    per_cat_units = {}
    for lid, blob in lists.items():
        cats = [c for c in st["categories"]
                if c.get("mode") in CLICKUP_MODES and str(c.get("list_id")) == lid]
        for key, us in _classify(blob["units"], cats).items():
            per_cat_units[key] = us

    day_axis = [start + timedelta(days=i) for i in range(days_total)]
    combined_by_day = {d: 0.0 for d in day_axis}
    categories = []
    for cat in st["categories"]:
        if cat["mode"] == "purchases":
            units = _purchases_units(st["window_start"], st["window_end"])
        else:
            units = [u for u in per_cat_units.get(cat["key"], [])
                     if u["day"] and start <= u["day"] <= end]
        actual = sum(u["usd"] for u in units)
        for u in units:
            if u["day"] in combined_by_day:
                combined_by_day[u["day"]] += u["usd"]
        warnings = [{"kind": u["warn"], "name": u["name"], "task_id": u["task_id"]}
                    for u in units if u.get("warn")]
        if cat["mode"] == "brand":
            options = {_norm(o) for o in (lists.get(str(cat.get("list_id"))) or {})
                       .get("brand_options") or []}
            for b in cat.get("brands") or []:
                if options and _norm(b) not in options:
                    warnings.insert(0, {"kind": "brand_missing", "name": b, "task_id": None})
        target = float(cat["target"])
        remaining = max(0.0, target - actual)
        breakdown = sorted(units, key=lambda u: (u["day"] or date.min, u["usd"]),
                           reverse=True)[:BREAKDOWN_CAP]
        categories.append({
            "key": cat["key"], "label": cat["label"], "mode": cat["mode"],
            "list_id": cat.get("list_id"), "brands": cat.get("brands"),
            "target": round(target, 2), "actual": round(actual, 2),
            "pct": round(actual / target * 100, 1) if target else 0.0,
            "n": sum(1 for u in units if u["usd"] > 0),
            "remaining": round(remaining, 2),
            "need_per_day": round(remaining / days_left, 2) if days_left else 0.0,
            "breakdown": [_ser_unit(u) for u in breakdown],
            "breakdown_more": max(0, len(units) - BREAKDOWN_CAP),
            "warnings": warnings,
        })

    target_total = sum(c["target"] for c in categories)
    actual_total = sum(c["actual"] for c in categories)
    remaining_total = max(0.0, target_total - actual_total)
    pct = round(actual_total / target_total * 100, 1) if target_total else 0.0
    milestone = max((m for m in (25, 50, 75, 100) if pct >= m), default=0)
    by_day = [{"d": d.isoformat(), "usd": round(combined_by_day[d], 2)} for d in day_axis]
    best = max(by_day, key=lambda s: s["usd"]) if by_day else None

    try:
        import leluxe_goal
        line = leluxe_goal.motivation(milestone, today)
    except Exception:                                # pragma: no cover
        line = ""

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "days_total": days_total, "days_left": days_left,
                   "elapsed": elapsed, "today": today.isoformat(),
                   "active": start <= today <= end},
        "categories": categories,
        "totals": {"target": round(target_total, 2), "actual": round(actual_total, 2),
                   "pct": pct, "remaining": round(remaining_total, 2),
                   "need_per_day": round(remaining_total / days_left, 2) if days_left else 0.0,
                   "avg_per_day": round(actual_total / elapsed, 2) if elapsed else 0.0,
                   "best_day": best if best and best["usd"] else None,
                   "milestone": milestone},
        "by_day": by_day,
        "combine_it_pc": bool(st["combine_it_pc"]),
        "clickup_error": cerr,
        "stamp": db.get_setting(STAMP_KEY),
        "line": line,
        "generated_at": datetime.now(AMMAN).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
# ClickUp webhooks — the instant-sync wiring
# --------------------------------------------------------------------------- #
def webhook_info(st=None):
    """Local-only summary (no ClickUp calls — safe for every 10s poll)."""
    st = st or settings()
    saved = db.get_setting(WEBHOOK_KEY) or {}
    hooks = saved.get("hooks") or {}
    want = _clickup_list_ids(st)
    stamp = db.get_setting(STAMP_KEY) or {}
    return {"configured": bool(hooks) and all(l in hooks for l in want),
            "lists": want, "hooked": sorted(hooks),
            "endpoint": saved.get("endpoint"),
            "last_bump": stamp.get("at"), "last_src": stamp.get("src")}


def _delete_matching_hooks(team_id, endpoints):
    """Idempotency sweep: drop every existing webhook pointing at one of our
    endpoints (current or previously-saved). Returns (ok, error)."""
    import leluxe
    s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/team/{team_id}/webhook")
    if s_ != 200:
        return False, f"list webhooks failed ({s_}): {(body or {}).get('_error') or body}"
    for wh in (body or {}).get("webhooks") or []:
        if (wh.get("endpoint") or "") in endpoints and wh.get("id"):
            leluxe._http(f"{leluxe.CLICKUP_API}/webhook/{wh['id']}", method="DELETE")
    return True, None


def webhook_create(base_url, st=None):
    """Register one list-scoped webhook per source list (idempotent). Returns
    (status, error)."""
    import leluxe
    st = st or settings()
    endpoint = (base_url or "").rstrip("/") + "/webhook/clickup"
    saved = db.get_setting(WEBHOOK_KEY) or {}
    old_ep = saved.get("endpoint") or ""
    ok, err = _delete_matching_hooks(st["team_id"], {e for e in (endpoint, old_ep) if e})
    if not ok:
        return None, err
    hooks = {}
    for lid in _clickup_list_ids(st):
        payload = {"endpoint": endpoint, "events": EVENTS}
        payload["list_id"] = int(lid) if lid.isdigit() else lid
        s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/team/{st['team_id']}/webhook",
                                method="POST", body=payload)
        wh = (body or {}).get("webhook") or {}
        wid = wh.get("id") or (body or {}).get("id")
        if s_ != 200 or not wid:
            return None, (f"create failed for list {lid} ({s_}): "
                          f"{(body or {}).get('err') or (body or {}).get('_error') or body}")
        hooks[lid] = {"id": wid, "secret": wh.get("secret") or ""}
    db.set_setting(WEBHOOK_KEY, {"endpoint": endpoint, "hooks": hooks,
                                 "created_at": datetime.now(AMMAN).isoformat(timespec="seconds")})
    bump_stamp("webhook_setup")
    return webhook_status(st)


def webhook_status(st=None):
    """Live health from ClickUp (only for the ⚙ setup panel, never the poll).
    Returns (status, error)."""
    import leluxe
    st = st or settings()
    saved = db.get_setting(WEBHOOK_KEY) or {}
    s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/team/{st['team_id']}/webhook")
    if s_ != 200:
        return None, f"list webhooks failed ({s_}): {(body or {}).get('_error') or body}"
    ours = [{"id": wh.get("id"), "list_id": str(wh.get("list_id") or ""),
             "endpoint": wh.get("endpoint"), "events": wh.get("events"),
             "health": wh.get("health")}
            for wh in (body or {}).get("webhooks") or []
            if (wh.get("endpoint") or "") == (saved.get("endpoint") or "")]
    return {"hooks": ours, **webhook_info(st)}, None


def webhook_delete(st=None):
    """Remove our webhooks + forget the secrets. Returns (info, error)."""
    st = st or settings()
    saved = db.get_setting(WEBHOOK_KEY) or {}
    eps = {e for e in (saved.get("endpoint"),) if e}
    if eps:
        ok, err = _delete_matching_hooks(st["team_id"], eps)
        if not ok:
            return None, err
    db.set_setting(WEBHOOK_KEY, {})
    return webhook_info(st), None


def verify_signature(raw_body, signature):
    """ClickUp delivery check: X-Signature = HMAC-SHA256 hex of the raw body
    with the webhook's secret. No secrets stored → trust nobody."""
    saved = db.get_setting(WEBHOOK_KEY) or {}
    sig = signature or ""
    ok = False
    for h in (saved.get("hooks") or {}).values():
        sec = (h or {}).get("secret") or ""
        if sec:
            want = hmac.new(sec.encode(), raw_body or b"", hashlib.sha256).hexdigest()
            ok = hmac.compare_digest(want, sig) or ok
    return ok
