#!/usr/bin/env python3
"""
Activity log — *who edited what*, like ClickUp's "Latest Activity" feed.

Append-only `activity.jsonl` (one JSON object per line, same pattern as
`report.write_history`). Every edit endpoint in the dashboard calls `log(...)`;
the dashboard's Latest-Activity card + Activity page read `recent(...)`.

Event shape:
  {ts, user, action, entity, entity_id, label, field, old, new, detail}
    action  — short verb: created | set | added | removed | deleted | restored
              | uploaded | synced
    entity  — order | purchase | customer | trash
    entity_id — e.g. "PO-0001" / "OTL-0001"  (used to group the feed)
    label   — human header for that entity, e.g. "Order # 113-…" or "OTL-0001 · Ahd"
    field/old/new — the changed field (ClickUp-style label) + values
    detail  — free text (e.g. the product a change applies to)

  python3 activity.py            # print the most recent events
  python3 activity.py --selftest # offline po_diff check
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import cfg
from db import current_business
from paths import data_path

ACTIVITY_FILE = data_path("activity.jsonl")    # business #1 (Otlobly) — unchanged


def _activity_file(business_id=None):
    """Per-tenant activity log so a broker's feed shows only its own events."""
    bid = business_id or current_business()
    return ACTIVITY_FILE if bid == 1 else data_path(f"activity_b{bid}.jsonl")

# Field name → the human label shown in the feed (matches the ClickUp wording the
# user sees: box dropdown is "NAME", the item's customer is "CUSTOMER NAME", …).
FIELD_LABELS = {
    "profile_box": "NAME",
    "ship_to": "ship to",
    "amazon_order_number": "Order #",
    "order_placed": "order date",
    "total_usd": "Total Amount",
    "total_aed": "Total Amount (AED)",
    "status": "STATUS",
    "arrival": "ARRIVAL",
    "tracking_number": "TRACKING",
    "otlobly_status": "OTLOBLY STATUS",
    "tracking_status": "GAASH STATUS",
    "gerizim_status": "GERIZIM STATUS",
    "gaash_docs_at": "DOCS UPLOADED",
    "gaash_deadline": "GAASH DEADLINE",
    "title": "PRODUCT NAME",
    "asin": "ASIN",
    "customer_name": "CUSTOMER NAME",
    "qty": "QTY",
    "notes": "NOTES",
}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _user(user=None):
    if user:
        return user
    try:
        return cfg.get(cfg.load(), "local_user", "You") or "You"
    except Exception:        # noqa - never let logging break a request
        return "You"


def log(action, entity, entity_id="", label="", *, field=None,
        old=None, new=None, detail="", user=None):
    """Append one event. Best-effort: never raises into the caller."""
    ev = {
        "ts": now_iso(),
        "user": _user(user),
        "action": action,
        "entity": entity,
        "entity_id": str(entity_id or ""),
        "label": label or str(entity_id or ""),
        "field": FIELD_LABELS.get(field, field) if field else None,
        "old": _short(old),
        "new": _short(new),
        "detail": detail or "",
    }
    try:
        with _activity_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return ev


def _short(v):
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= 120 else s[:117] + "…"


def _lines_newest_first(path, chunk=256 * 1024):
    """Yield the file's lines from the END backwards, reading a chunk at a time.

    The log is append-only, so the newest events are the last lines — walking
    backwards means a caller that wants the latest 200 events touches ~64 KB
    instead of the whole file."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        tail = b""                     # bytes before the earliest complete line read
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            parts = (f.read(step) + tail).split(b"\n")
            tail = parts.pop(0)        # may be a partial line — completed next loop
            for raw in reversed(parts):
                yield raw
        if tail:
            yield tail


def recent(limit=60, entity=None, business_id=None, entity_id=None):
    """Newest-first list of events, optionally filtered to one entity type and/or
    one specific entity id (the per-PO Activity feed in the detail drawer).
    `business_id` reads another tenant's feed (Tatabu platform admin only).

    Reads BACKWARDS and stops at `limit`. It used to slurp the whole file, split
    it into every line and json-parse all of them just to keep the newest few:
    at 12 MB that cost ~36 MB per call, and /api/notifications (polled every 5s
    by the staff board) was walking the process into Render's 512 MB limit."""
    af = _activity_file(business_id)
    if not af.exists():
        return []
    out = []
    for raw in _lines_newest_first(af):
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if entity and ev.get("entity") != entity:
            continue
        if entity_id and ev.get("entity_id") != str(entity_id):
            continue
        out.append(ev)
        if limit and len(out) >= limit:
            break
    return out


def platform_recent(businesses, limit=100):
    """Cross-tenant feed for the Tatabu platform admin: every tenant's events
    merged newest-first, each stamped with the business it came from (the events
    themselves carry no business tag — the per-tenant FILE is the scope)."""
    out = []
    for biz in businesses:
        for ev in recent(limit, business_id=biz["id"]):
            ev["business_id"] = biz["id"]
            ev["business"] = biz.get("name") or f"#{biz['id']}"
            out.append(ev)
    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return out[:limit]


# --------------------------------------------------------------------------- #
# Purchase-order diff → granular events ("set NAME to B27", "set CUSTOMER NAME…")
# --------------------------------------------------------------------------- #
_PO_FIELDS = ("amazon_order_number", "ship_to", "profile_box", "order_placed",
              "total_usd", "total_aed", "status")
_PKG_FIELDS = ("arrival", "tracking_number", "otlobly_status", "gaash_docs_at",
               "gaash_deadline")
_ITEM_FIELDS = ("title", "asin", "customer_name", "qty", "status", "notes")


def _norm(v):
    return "" if v in (None, "") else str(v)


def gaash_text(ts):
    """Readable one-liner for a tracking_status dict (never the raw JSON)."""
    if not isinstance(ts, dict) or ts.get("error"):
        return ""
    return (ts.get("text") or ts.get("label") or ts.get("bucket") or "").strip()


def po_diff(old, new):
    """Compare two PO dicts → list of {field, old, new, detail} change events.
    Matches packages by package_no and items by item_id, so only real edits show.
    `detail` names the product/package a change applies to."""
    old = old or {}
    events = []

    for f in _PO_FIELDS:
        if _norm(old.get(f)) != _norm(new.get(f)):
            events.append({"field": f, "old": old.get(f), "new": new.get(f),
                           "detail": ""})

    old_pkgs = {p.get("package_no"): p for p in old.get("packages", [])}
    for pkg in new.get("packages", []):
        pno = pkg.get("package_no")
        op = old_pkgs.get(pno)
        pkg_label = f"Package {pno}"
        if op is None:
            events.append({"field": None, "action": "added", "old": None,
                           "new": None, "detail": pkg_label})
            # brand-new package: log its items as additions too
            for it in pkg.get("items", []):
                events.append({"field": None, "action": "added", "old": None,
                               "new": None, "detail": _item_label(it)})
            continue
        for f in _PKG_FIELDS:
            if _norm(op.get(f)) != _norm(pkg.get(f)):
                events.append({"field": f, "old": op.get(f), "new": pkg.get(f),
                               "detail": pkg_label})
        # carrier statuses are dicts — diff the readable text, never the raw JSON
        for f in ("tracking_status", "gerizim_status"):
            ot, nt = gaash_text(op.get(f)), gaash_text(pkg.get(f))
            if nt and ot != nt:
                events.append({"field": f, "old": ot or None, "new": nt,
                               "detail": pkg_label})
        old_items = {it.get("item_id"): it for it in op.get("items", [])}
        for it in pkg.get("items", []):
            oit = old_items.get(it.get("item_id"))
            label = _item_label(it)
            if oit is None:
                events.append({"field": None, "action": "added", "old": None,
                               "new": None, "detail": label})
                continue
            for f in _ITEM_FIELDS:
                if _norm(oit.get(f)) != _norm(it.get(f)):
                    events.append({"field": f, "old": oit.get(f),
                                   "new": it.get(f), "detail": label})
    return events


def _item_label(it):
    return (it.get("title") or it.get("asin") or "product").strip()


def log_po_diff(old, new, *, user=None):
    """Run po_diff and emit one activity event per change."""
    po_id = new.get("po_id") or (old or {}).get("po_id") or ""
    header = ("Order # " + new.get("amazon_order_number")) if new.get("amazon_order_number") \
        else po_id
    n = 0
    for ev in po_diff(old, new):
        log(ev.get("action", "set"), "purchase", po_id, header,
            field=ev.get("field"), old=ev.get("old"), new=ev.get("new"),
            detail=ev.get("detail"), user=user)
        n += 1
    return n


# --------------------------------------------------------------------------- #
def _selftest():
    old = {"po_id": "PO-0001", "amazon_order_number": "113-1", "profile_box": "B19",
           "packages": [{"package_no": 1, "tracking_number": "GWD1", "items": [
               {"item_id": "a1", "title": "Cricut Maker 4", "customer_name": None,
                "status": "ORDERED"}]}]}
    new = {"po_id": "PO-0001", "amazon_order_number": "113-1", "profile_box": "B27",
           "packages": [{"package_no": 1, "tracking_number": "GWD1", "items": [
               {"item_id": "a1", "title": "Cricut Maker 4", "customer_name": "yasmeen saad",
                "status": "SHIPPED"},
               {"item_id": "a2", "title": "New Item", "customer_name": None}]}]}
    evs = po_diff(old, new)
    fields = {(e.get("field"), e.get("action")) for e in evs}
    ok = (("profile_box", None) in {(e.get("field"), e.get("action")) for e in evs}
          and any(e["field"] == "customer_name" and e["new"] == "yasmeen saad" for e in evs)
          and any(e["field"] == "status" and e["new"] == "SHIPPED" for e in evs)
          and any(e.get("action") == "added" and e["detail"] == "New Item" for e in evs))
    print("po_diff:", "OK" if ok else f"XX {evs}")
    print(f"  {len(evs)} events, fields={sorted(str(f) for f in fields)}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    for ev in recent(40):
        bit = f" {ev['field']}→{ev['new']}" if ev.get("field") else ""
        print(f"{ev['ts'][:19]}  {ev['user']:6} {ev['action']:8} "
              f"{ev['label'][:30]:30}{bit}  {ev.get('detail','')}")
