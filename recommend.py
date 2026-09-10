#!/usr/bin/env python3
"""
recommend.py — which buying account should this purchase order go on? (2026-09-10)

PURE. The roster comes from AZ Studio (pushed to /api/worker/az_roster, see az_roster.py);
the order history comes from Otlobly's own purchase orders plus the Leluxe board
(account_rd.rollup). The owner's rule, in his words: "the most available profile
depending on the order history and depending on if the profile is ready or not to
order depending on the tags".

  eligible = carries the ready tag (Settings → Leluxe goal → ready_tag, default
             "READY TO ORDER 2") AND the Accounts Tool does not mark it spent — its RD
             history is a BLOCKER (BAN, DEAD, IN_PLAY, AMBIGUOUS, GONE, UNKNOWN), never a
             requirement: on the real fleet (2026-09-10) it says READY for nobody and
             UNUSED for 175 of 251, because READY means "a clean completed big order",
             which a fresh account cannot have — AND no ban tag / quarantine / burned IP /
             dead proxy AND not running or in use.
  noted    = a saved card + address (AZ Studio's own "card ready" stage) is a plus and a
             tie-break, not a gate: the operator adds the payment in AZ Studio.
  ranked   = fewest orders in the last 30 days (Otlobly POs + the Leluxe board), then
             card + address saved, then READY before UNUSED before UNPROVEN, then the
             longest time since its last order, then the lowest fraud score, then name

FAIL CLOSED: a roster older than STALE_HOURS is UNKNOWN — nothing is eligible and the UI
says how old it is — the same rule acctool applies to an old board; an UNKNOWN verdict
(stale board, partial fleet) blocks too. "We do not know" never reads as "ready".
"""

import datetime as _dt

STALE_HOURS = 2
WINDOW_DAYS = 30
BAN_TAGS = ("amazon ban", "gmail ban", "ebay ban")
DEFAULT_READY_TAG = "READY TO ORDER 2"
# acctool verdicts that mean "do not use": spent (ran an RD), banned, a claim still running,
# ambiguous, gone from the fleet, or simply unknown (stale board / partial sweep → fail closed)
BLOCK_VERDICTS = ("BAN", "DEAD", "IN_PLAY", "AMBIGUOUS", "GONE", "UNKNOWN")
VERDICT_RANK = {"READY": 0, "UNUSED": 1, "UNPROVEN": 2}


def _norm(s):
    return " ".join(str(s or "").split()).lower()


def _date(v):
    """iso date/datetime string → date, else None."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def history(purchase_orders, board_orders, now=None):
    """{normalised account name: {"orders": n, "orders_30d": n, "last_order_at": "YYYY-MM-DD"}}
    from Otlobly's POs (profile_box + order_placed/created_at) and the board's orders
    (code + ordered_at/dated_at). Undated orders still count as orders, just not as recent."""
    today = now or _dt.date.today()
    if isinstance(today, _dt.datetime):
        today = today.date()
    cutoff = today - _dt.timedelta(days=WINDOW_DAYS)
    out = {}

    def bump(name, d):
        k = _norm(name)
        if not k:
            return
        h = out.setdefault(k, {"name": str(name).strip(), "orders": 0, "orders_30d": 0, "last_order_at": ""})
        h["orders"] += 1
        if d:
            if d >= cutoff:
                h["orders_30d"] += 1
            if d.isoformat() > h["last_order_at"]:
                h["last_order_at"] = d.isoformat()

    for po in purchase_orders or []:
        bump(po.get("profile_box"), _date(po.get("order_placed")) or _date(po.get("created_at")))
    for o in board_orders or []:
        bump(o.get("code"), _date(o.get("ordered_at")) or _date(o.get("dated_at")))
    return out


def evaluate(row, hist, *, ready_tag=DEFAULT_READY_TAG, now=None, stale=False):
    """One roster row → the decision for it: eligible?, why (the facts in its favour),
    blockers (what rules it out). Never raises on a half-shaped row."""
    today = now or _dt.date.today()
    if isinstance(today, _dt.datetime):
        today = today.date()
    name = str(row.get("name") or "").strip()
    tags = [str(t) for t in (row.get("all_tags") or row.get("tags") or [])]
    ntags = {_norm(t) for t in tags}
    badges = row.get("badges") or {}
    acct = row.get("acctool") or {}
    verdict = str(acct.get("verdict") or "UNKNOWN").upper()
    h = hist.get(_norm(name)) or {"orders": 0, "orders_30d": 0, "last_order_at": ""}
    last = _date(h.get("last_order_at"))
    days_since = (today - last).days if last else None
    fraud = row.get("last_fraud")
    try:
        fraud = float(fraud) if fraud not in (None, "") else None
    except (TypeError, ValueError):
        fraud = None

    blockers, why = [], []
    if stale:
        blockers.append("roster is stale")
    if _norm(ready_tag) and _norm(ready_tag) in ntags:
        why.append(f"tagged {ready_tag}")
    else:
        blockers.append(f"no {ready_tag} tag")
    vwhy = f" ({acct.get('why')})" if acct.get("why") else ""
    if verdict in BLOCK_VERDICTS:
        blockers.append(f"Accounts Tool: {verdict.lower().replace('_', ' ')}{vwhy}")
    elif verdict == "READY":
        why.append("Accounts Tool: ready" + vwhy)
    else:                                    # UNUSED / UNPROVEN: no RD on record, not proven either
        why.append(f"Accounts Tool: {verdict.lower()}{vwhy}")
    banned = sorted(t for t in ntags if t in BAN_TAGS)
    if banned:
        blockers.append("tagged " + ", ".join(banned))
    if row.get("quarantined"):
        blockers.append("quarantined")
    if row.get("ip_burned"):
        blockers.append("burned IP")
    if row.get("proxy_dead"):
        blockers.append("dead proxy")
    if row.get("running") or row.get("in_use"):
        blockers.append("in use right now")
    card_ok = bool(badges.get("card_set") and badges.get("address_set"))
    if card_ok:
        why.append("card and address saved")
    else:
        missing = [k for k, ok in (("card", badges.get("card_set")), ("address", badges.get("address_set"))) if not ok]
        why.append("no " + " or ".join(missing) + " saved yet — add it in AZ Studio")
    if h["orders_30d"]:
        why.append(f"{h['orders_30d']} order{'s' if h['orders_30d'] != 1 else ''} in the last {WINDOW_DAYS} days")
    else:
        why.append(f"no orders in the last {WINDOW_DAYS} days")
    if days_since is not None:
        why.append(f"last order {days_since} day{'s' if days_since != 1 else ''} ago")
    elif h["orders"]:
        why.append("orders undated")
    else:
        why.append("never used on our boards")
    if fraud is not None:
        why.append(f"fraud score {int(fraud) if float(fraud).is_integer() else fraud}")

    return {
        "name": name, "profile_id": row.get("profile_id"), "folder": row.get("folder") or "",
        "tags": tags, "verdict": verdict if not stale else "UNKNOWN",
        "eligible": not blockers, "why": why, "blockers": blockers,
        "orders": h["orders"], "orders_30d": h["orders_30d"],
        "last_order_at": h.get("last_order_at") or "", "days_since_order": days_since,
        "fraud": fraud, "in_use": bool(row.get("running") or row.get("in_use")),
        "card_set": bool(badges.get("card_set")), "address_set": bool(badges.get("address_set")),
        "card_ok": card_ok, "verdict_rank": VERDICT_RANK.get(verdict, 3),
        "cart_items": int(badges.get("cart_items") or 0),
        "order_status": badges.get("order_status") or "none",
    }


def _sort_key(r):
    # eligible first; fewest recent orders; card + address saved first; READY < UNUSED <
    # UNPROVEN; longest since last order (never = infinity); lowest fraud (unknown last); name
    days = r["days_since_order"]
    return (0 if r["eligible"] else 1, r["orders_30d"], 0 if r["card_ok"] else 1,
            r["verdict_rank"], -(days if days is not None else 10 ** 6),
            r["fraud"] if r["fraud"] is not None else 10 ** 6,
            r["name"].upper())


def rank(profiles, hist, *, ready_tag=DEFAULT_READY_TAG, synced_ts=None, now=None, exclude=()):
    """Every roster row, decided and ordered. `synced_ts` (epoch) drives the stale rule;
    `now` is injected so the boundaries are testable."""
    nowdt = now or _dt.datetime.now(_dt.timezone.utc)
    if isinstance(nowdt, _dt.date) and not isinstance(nowdt, _dt.datetime):
        nowdt = _dt.datetime.combine(nowdt, _dt.time(12, 0), tzinfo=_dt.timezone.utc)
    age_min = None
    stale = True
    if synced_ts:
        age_min = max(0.0, (nowdt.timestamp() - float(synced_ts)) / 60.0)
        stale = age_min > STALE_HOURS * 60
    skip = {_norm(x) for x in (exclude or ())}
    rows = [evaluate(p, hist, ready_tag=ready_tag, now=nowdt.date(), stale=stale)
            for p in (profiles or []) if _norm(p.get("name")) and _norm(p.get("name")) not in skip]
    rows.sort(key=_sort_key)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    eligible = [r for r in rows if r["eligible"]]
    return {"stale": stale, "age_min": None if age_min is None else round(age_min, 1),
            "stale_hours": STALE_HOURS, "ready_tag": ready_tag,
            "eligible": len(eligible), "total": len(rows),
            "recommended": eligible[0]["name"] if eligible else None, "rows": rows}
