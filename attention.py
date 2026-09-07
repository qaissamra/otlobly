#!/usr/bin/env python3
"""
Needs attention — one queue for everything that is waiting on a person.

The audit (docs/ux-restructure/AUDIT.md §5.8) found the same question answered in
five different places: the 🚩 Flags page (action-required email), the Purchases
board (a package past its due date, a package with no GWD yet), the GAASH mail
Docs tab (customs asking for papers) and the Brain's urgent section. Each one had
its own page, its own colour and its own idea of what "needs doing" means, so the
only way to know whether anything was on fire was to open all four.

`build()` merges them into ONE list, newest problem first, and returns pure data
— no HTML — so the same payload can feed the sidebar badge, the Needs attention
page and (later) a Telegram digest, exactly like brain.build() does.

Every item carries a `kind` from the fixed attention vocabulary in
static/ds/status.js (test_ds_shell.py asserts the two agree), which is what gives
the whole app one word and one colour per problem:

    action_email · late · no_tracking · missing_docs · unpriced · unpaid · stale · over_quota

Severity is `urgent` (act today) or `soon`; the count the badge shows is the
number of urgent + soon items, never a truncated page of them.

    ./.venv/bin/python attention.py     # print the current tenant's queue
"""

from datetime import date, datetime, timezone

import db

# A package with no GWD is only a problem once the money has been spent — before
# the Amazon order is placed there is nothing to track. Same idea for lateness:
# a package the owner never gave a due date to cannot be late.
MAX_ITEMS = 50            # per-group cap for the payload; `count` keeps the truth
STALE_NO_TRACKING_D = 3   # days after the Amazon order before "no GWD" is raised

# Brain rules that are already a nav badge somewhere else, so repeating them here
# would double-count the same work: "12 orders to buy" IS the To order badge.
BRAIN_SKIP = {"rule:need_order"}
# Brain rule → attention vocabulary. Anything unmapped rides in as `stale`.
BRAIN_KIND = {"rule:unpriced": "unpriced", "rule:collect": "unpaid",
              "rule:quota": "over_quota", "rule:leads": "stale",
              "rule:needs_expand": "stale"}


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _days_since(s, today):
    d = _to_date(s)
    return (today - d).days if d else None


def _item(id_, kind, title, detail="", *, severity="soon", age_days=None,
          due=None, view="orders", arg=None):
    """One row of the queue. `link` is what the shell routes to when it is clicked
    — the same {view, arg} shape brain.py already emits, so both feeds share one
    click handler."""
    return {"id": id_, "kind": kind, "title": title, "detail": detail,
            "severity": severity, "age_days": age_days, "due": due,
            "link": {"view": view, "arg": arg}}


def _group(key, label, items, count=None):
    return {"key": key, "label": label, "count": len(items) if count is None else count,
            "items": items[:MAX_ITEMS]}


def _pkg_label(po, pk):
    """How the owner names a package out loud: PO number, package number, and the
    name it ships under when there is one."""
    who = (po.get("ship_to") or "").strip()
    tail = f" · {who}" if who else ""
    return f"{po.get('po_id') or 'PO'} · package {pk.get('package_no') or '?'}{tail}"


def _packages(today):
    """Late · no tracking number · customs asking for documents — one pass over the
    PO store, skipping every package whose journey is already over (alerts owns
    that vocabulary and Settings can edit it, so all the sweeps stop together)."""
    import alerts
    import purchases
    stop = alerts.stop_statuses()
    late, no_trk, docs = [], [], []
    for po in (purchases.load().get("purchase_orders") or []):
        placed_age = _days_since(po.get("order_placed") or po.get("created_at"), today)
        for pk in (po.get("packages") or []):
            if (pk.get("otlobly_status") or "").strip().lower() in stop:
                continue
            name, pid = _pkg_label(po, pk), f"{po.get('po_id')}#{pk.get('package_no')}"
            trk = (pk.get("tracking_number") or "").strip()
            over = _days_since(pk.get("due_date"), today)
            if over is not None and over > 0:
                late.append(_item(pid, "late", name,
                                  f"due {pk['due_date']} — {over} day{'s' if over != 1 else ''} ago",
                                  severity="urgent", age_days=over, due=pk.get("due_date"),
                                  view="purchases", arg=po.get("po_id")))
            dl = _days_since(pk.get("gaash_deadline"), today)
            if trk and dl is not None and dl > 0:
                late.append(_item(pid + ":dl", "late", name,
                                  f"GAASH link expired {pk['gaash_deadline']} — the parcel is lost after this",
                                  severity="urgent", age_days=dl, due=pk.get("gaash_deadline"),
                                  view="purchases", arg=po.get("po_id")))
            if not trk and placed_age is not None and placed_age >= STALE_NO_TRACKING_D:
                no_trk.append(_item(pid, "no_tracking", name,
                                    f"ordered {placed_age} days ago, still no GWD",
                                    age_days=placed_age, view="purchases", arg=po.get("po_id")))
            if (pk.get("docs_state") or "") == "action":
                docs.append(_item(pid, "missing_docs", name,
                                  f"GAASH is asking for documents{' · ' + trk if trk else ''}",
                                  severity="urgent",
                                  age_days=_days_since(pk.get("docs_checked"), today),
                                  view="gaashmail", arg=trk or None))
    return late, no_trk, docs


def _flags():
    """Open "action required" emails from the watched inboxes. Feature-gated the
    same way the 🚩 page is — a tenant without it simply has no such group."""
    try:
        import features
        if not features.has(db.current_business(), "leluxe"):
            return []
    except Exception:
        pass
    try:
        import flag_machine as fm
        rows = fm.open_flags()
    except Exception:
        return []
    today = date.today()
    out = []
    for f in rows:
        who = (f.get("profile") or f.get("mailbox") or "").strip()
        out.append(_item(f"flag:{f.get('id')}", "action_email",
                         (f.get("subject") or "(no subject)").strip(),
                         f"{who} · reply \"done\" on Telegram to clear it" if who
                         else "reply \"done\" on Telegram to clear it",
                         severity="urgent",
                         age_days=_days_since(f.get("created_at"), today),
                         view="flags"))
    return out


def _brain():
    """The Brain's own urgent rules and past-deadline orders, minus the ones that
    are already a nav badge (see BRAIN_SKIP)."""
    try:
        import brain
        payload = brain.build(db.current_business())
    except Exception:
        return [], []
    secs = {s["key"]: s for s in payload.get("sections") or []}

    def rows(key, default_kind, severity):
        out = []
        for it in (secs.get(key) or {}).get("items") or []:
            if it.get("id") in BRAIN_SKIP:
                continue
            out.append(_item(f"brain:{it.get('id')}", BRAIN_KIND.get(it.get("id"), default_kind),
                             it.get("title") or "", it.get("detail") or "",
                             severity=it.get("severity") or severity,
                             age_days=it.get("age_days"), due=it.get("due"),
                             view=(it.get("link") or {}).get("view") or "orders",
                             arg=(it.get("link") or {}).get("arg")))
        return out
    return rows("deadlines", "late", "urgent"), rows("urgent", "stale", "urgent")


def build(business_id=None):
    """The tenant's Needs attention queue. Reads are scoped by the tenancy
    contextvar, exactly like brain.build()."""
    today = date.today()
    late_pkg, no_trk, docs = _packages(today)
    past_deadline, urgent = _brain()
    groups = [
        _group("flags", "Action-required email", _flags()),
        _group("deadline", "Past deadline", past_deadline + late_pkg),
        _group("docs", "Customs documents requested", docs),
        _group("no_tracking", "No tracking number yet", no_trk),
        _group("urgent", "Urgent", urgent),
    ]
    groups = [g for g in groups if g["count"]]
    return {"generated_at": _now_iso(),
            "count": sum(g["count"] for g in groups),
            "urgent": sum(1 for g in groups for i in g["items"] if i["severity"] == "urgent"),
            "groups": groups}


if __name__ == "__main__":
    import json as _json
    db.init_db()
    print(_json.dumps(build(), ensure_ascii=False, indent=2))
