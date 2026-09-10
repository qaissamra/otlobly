#!/usr/bin/env python3
"""
az_roster.py — the buying-account roster AZ Studio pushes to us (2026-09-10).

AZ Studio (az-tool, on the droplet and on the owner's Mac) POSTs its whole profile
roster to /api/worker/az_roster every 15 minutes with the worker token it already holds
for the Accounts Tool. We keep ONE copy in the settings table — the freshest by the
snapshot's own timestamp (synced_ts), never by arrival: the Mac's copy can be hours old
(its background sync is off) and must not overwrite the droplet's. Every row is
re-whitelisted here too, so a future field on the AZ side cannot leak into our store.

Consumers: recommend.py (the picker), az.py (the profile popup when AZ Studio is not
reachable), leluxe_goal.profiles_summary (ready-profile capacity).
"""

import time

import db

KEY = "az:roster"
MAX_ROWS = 2000
STALE_MIN = 120                      # matches recommend.STALE_HOURS

FIELDS = ("profile_id", "folder_id", "folder", "name", "tags", "all_tags", "locked",
          "last_fraud", "last_ip", "proxy_host", "proxy_port", "proxy_type",
          "proxy_country", "proxy_dead", "provider", "runs_24h", "max_runs",
          "cooldown", "last_launched", "last_seen", "last_activity", "login_state",
          "quarantined", "ip_burned", "ip_changed", "is_local", "created", "updated",
          "in_use", "running")
BADGE_FIELDS = ("card_set", "address_set", "cart_items", "in_cart", "order_status")
VERDICT_FIELDS = ("verdict", "why", "orders", "rd", "clean", "clean_settled", "clean_fresh",
                  "newest_rd_at", "days_since_rd")


def _clean_row(p):
    if not isinstance(p, dict) or not str(p.get("name") or "").strip():
        return None
    row = {k: p.get(k) for k in FIELDS if k in p}
    row["name"] = str(p.get("name")).strip()
    row["tags"] = [str(t) for t in (p.get("tags") or []) if str(t).strip()][:60]
    row["all_tags"] = [str(t) for t in (p.get("all_tags") or row["tags"]) if str(t).strip()][:60]
    b = p.get("badges") if isinstance(p.get("badges"), dict) else {}
    row["badges"] = {k: b.get(k) for k in BADGE_FIELDS}
    a = p.get("acctool") if isinstance(p.get("acctool"), dict) else {}
    row["acctool"] = {k: a.get(k) for k in VERDICT_FIELDS}
    return row


def store(payload):
    """Validate + keep the payload if it is newer than what we hold.
    Returns {ok, stored, reason, count, synced_ts}."""
    if not isinstance(payload, dict):
        return {"ok": False, "stored": False, "reason": "not an object"}
    rows = [r for r in (_clean_row(p) for p in (payload.get("profiles") or [])[:MAX_ROWS]) if r]
    try:
        synced_ts = float(payload.get("synced_ts") or 0)
    except (TypeError, ValueError):
        synced_ts = 0.0
    if not rows:
        return {"ok": False, "stored": False, "reason": "no profiles"}
    if synced_ts <= 0:
        return {"ok": False, "stored": False, "reason": "no synced_ts"}
    cur = load()
    if cur and float(cur.get("synced_ts") or 0) > synced_ts:
        return {"ok": True, "stored": False, "reason": "older than the copy we hold",
                "count": len(rows), "synced_ts": synced_ts,
                "held_synced_ts": cur.get("synced_ts"), "held_host": cur.get("host")}
    acct = payload.get("acctool") if isinstance(payload.get("acctool"), dict) else {}
    doc = {"host": str(payload.get("host") or "")[:80], "synced_ts": synced_ts,
           "partial": bool(payload.get("partial")), "fleet_complete": bool(payload.get("fleet_complete")),
           "skipped_folders": int(payload.get("skipped_folders") or 0),
           "acctool": {"ok": bool(acct.get("ok")), "error": str(acct.get("error") or "")[:200],
                       "board_synced_at": str(acct.get("board_synced_at") or "")[:40],
                       "board_age_days": acct.get("board_age_days")},
           "received_at": time.time(), "count": len(rows), "profiles": rows}
    db.set_setting(KEY, doc)
    return {"ok": True, "stored": True, "count": len(rows), "synced_ts": synced_ts}


def load():
    doc = db.get_setting(KEY)
    return doc if isinstance(doc, dict) and doc.get("profiles") else None


def meta(now=None):
    """Freshness only — what the worker GET and the UI header show."""
    doc = load()
    if not doc:
        return {"ok": False, "have": False, "reason": "no roster received yet"}
    now = now or time.time()
    age = max(0.0, (now - float(doc.get("synced_ts") or 0)) / 60.0)
    return {"ok": True, "have": True, "host": doc.get("host"), "count": doc.get("count"),
            "synced_ts": doc.get("synced_ts"), "received_at": doc.get("received_at"),
            "age_min": round(age, 1), "stale": age > STALE_MIN, "partial": doc.get("partial"),
            "fleet_complete": doc.get("fleet_complete"), "acctool": doc.get("acctool")}


def profiles():
    """The rows, or [] — for az.py's fallback and the goal capacity line."""
    doc = load()
    return list(doc.get("profiles") or []) if doc else []
