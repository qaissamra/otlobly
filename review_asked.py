#!/usr/bin/env python3
"""
Review-outreach memory: which customers have we already asked for a review?

The Package prep view's "📸 اطلب تقييم" section lists collected (delivered +
paid) customers so staff can WhatsApp them for a Facebook review. Once a
customer has been contacted, staff hit "✓ تم" and they drop off the list. That
"already asked" flag lives here — a tiny JSON store on the persistent disk,
keyed by the same person-key pkgprep uses (primary phone e164, else "name:<n>").

    {"<person_key>": {"asked_at": "2026-07-24T14:00:00+03:00"}}
"""

from datetime import datetime, timezone

from paths import data_path, write_json_atomic

STORE_FILE = data_path("review_asked.json")


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load():
    if STORE_FILE.exists():
        try:
            import json
            return json.loads(STORE_FILE.read_text() or "{}") or {}
        except (ValueError, OSError):
            return {}
    return {}


def save(d):
    write_json_atomic(STORE_FILE, d)


def keys():
    """Set of person-keys already asked — pkgprep uses this to hide them."""
    return set(load().keys())


def mark(key):
    """Record that `key` has been asked. Idempotent."""
    if not key:
        return
    d = load()
    if key not in d:
        d[key] = {"asked_at": _now_iso()}
        save(d)


def unmark(key):
    """Undo — put a customer back on the list."""
    if not key:
        return
    d = load()
    if d.pop(key, None) is not None:
        save(d)
