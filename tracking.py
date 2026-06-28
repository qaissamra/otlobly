#!/usr/bin/env python3
"""
GAASH Worldwide (GWD) tracking — same method as gaash-clickup-sync/gaash.py:
a free, no-browser call to GAASH's public WordPress REST endpoint. Scrapes a
fresh public nonce once, then queries each parcel.

  python3 tracking.py GWD004697561
"""

import json
import re
import sys
import time
from urllib import request, error
from urllib.parse import quote

TRACK_PAGE = "https://gaashwd.com/track-parcel/"
DEFAULT_API = "https://gaashwd.com/wp-json/gaash-parcel-status-tracker/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
REQUEST_GAP = 0.7

# GAASH MappedStatusCode → (human label, colour bucket) for the INTERNAL staff UI.
CODE_LABEL = {
    "VM": ("On the way to the country", "transit"),
    "K3": ("Arrived in the country", "arrived"),
    "CD": ("Customs — needs your ID/docs", "customs"),
    "K2": ("Cleared customs", "cleared"),
    "AJ": ("Out for last-mile / pickup", "transit"),
    "D1": ("Delivered", "delivered"),
}

# CUSTOMER-FACING default map: GAASH code OR status text → friendly label + colour.
# Deliberately vague where the internal status is operational (e.g. "needs ID" →
# "In clearance"). Editable from the admin Settings table; anything unmatched falls
# back to DEFAULT_CUSTOMER_LABEL so customers never see raw internal text.
DEFAULT_STATUS_MAP = [
    {"match": "VM", "label": "On its way to your country", "bucket": "transit"},
    {"match": "K3", "label": "Arrived in your country", "bucket": "arrived"},
    {"match": "CD", "label": "In clearance", "bucket": "customs"},
    {"match": "K2", "label": "Customs cleared", "bucket": "cleared"},
    {"match": "AJ", "label": "Out for delivery", "bucket": "transit"},
    {"match": "D1", "label": "Delivered", "bucket": "delivered"},
    {"match": "MOC - Palestinian authority", "label": "In customs", "bucket": "customs"},
    {"match": "Required customer ID", "label": "In clearance", "bucket": "customs"},
    {"match": "Cleared customs", "label": "Customs cleared", "bucket": "cleared"},
    {"match": "Delivered", "label": "Delivered", "bucket": "delivered"},
]
DEFAULT_CUSTOMER_LABEL = "In transit"


def get_session():
    req = request.Request(TRACK_PAGE, headers={"User-Agent": UA})
    with request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    non = re.search(r'"nonce":"([a-f0-9]+)"', html)
    if not non:
        raise RuntimeError("Could not find GAASH nonce (site layout may have changed).")
    api = re.search(r'"apiUrl":"([^"]+)"', html)
    return (api.group(1).replace("\\/", "/") if api else DEFAULT_API), non.group(1)


def clean_tracking(tn):
    return "".join(c for c in str(tn) if c.isprintable()).strip()


def fetch_one(tn, api_url, nonce, lang="en"):
    url = f"{api_url}/parcel-tracking-data?parcel_id={quote(clean_tracking(tn))}&lang={lang}"
    req = request.Request(url, headers={"User-Agent": UA, "X-WP-Nonce": nonce})
    try:
        with request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "null")
    except error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:  # noqa
        return {"_error": str(e)}


def latest_status(data):
    statuses = (data or {}).get("Statuses")
    if not statuses:
        return None
    last = max(statuses, key=lambda s: s.get("StatusTime") or "")
    code = last.get("MappedStatusCode")
    label, bucket = CODE_LABEL.get(code, ((last.get("StatusDescription") or "").strip(), "transit"))
    return {
        "code": code,
        "text": (last.get("StatusDescription") or label or "").strip(),
        "label": label,
        "bucket": bucket,
        "time": last.get("StatusTime"),
    }


def track(tn, lang="en"):
    """One GWD number → latest status dict, or {'error': ...}."""
    if not clean_tracking(tn):
        return {"error": "no tracking number"}
    try:
        api_url, nonce = get_session()
    except Exception as e:  # noqa
        return {"error": f"GAASH session failed: {e}"}
    data = fetch_one(tn, api_url, nonce, lang)
    if "_error" in data:
        return {"error": data["_error"]}
    s = latest_status(data)
    return s or {"error": "no status yet for this parcel"}


def timeline(tn, lang="en"):
    """One GWD number → the FULL event list (oldest→newest), raw. Returns
    {ok, events:[{code, text, time}]} or {ok:False, error}."""
    if not clean_tracking(tn):
        return {"ok": False, "error": "no tracking number"}
    try:
        api_url, nonce = get_session()
    except Exception as e:  # noqa
        return {"ok": False, "error": f"GAASH session failed: {e}"}
    data = fetch_one(tn, api_url, nonce, lang)
    if "_error" in data:
        return {"ok": False, "error": data["_error"]}
    statuses = (data or {}).get("Statuses") or []
    events = sorted(
        ({"code": (s.get("MappedStatusCode") or "").strip(),
          "text": (s.get("StatusDescription") or "").strip(),
          "time": s.get("StatusTime")} for s in statuses),
        key=lambda e: e.get("time") or "")
    return {"ok": True, "events": events}


def _match_row(ev, status_map):
    """First map row matching the event by its code OR its text (case-insensitive)."""
    code = (ev.get("code") or "").strip().upper()
    text = (ev.get("text") or "").strip().lower()
    for row in status_map:
        m = (row.get("match") or "").strip()
        if m and ((code and m.upper() == code) or (text and m.lower() == text)):
            return row
    return None


def customer_timeline(events, status_map=None, default_label=None):
    """Remap raw GAASH events → a customer-friendly timeline: rename via status_map,
    drop hidden rows, collapse consecutive duplicates (keep when the state BEGAN),
    fall back to a safe generic label. Returns {events:[{label, bucket, date}], current}."""
    status_map = status_map if status_map is not None else DEFAULT_STATUS_MAP
    default_label = default_label or DEFAULT_CUSTOMER_LABEL
    out = []
    for ev in events or []:
        row = _match_row(ev, status_map)
        if row and row.get("hidden"):
            continue
        label = (row or {}).get("label") or default_label
        bucket = (row or {}).get("bucket") or "transit"
        if out and out[-1]["label"] == label:
            continue  # same state as before → keep the first occurrence's date
        out.append({"label": label, "bucket": bucket, "date": ev.get("time")})
    return {"events": out, "current": (out[-1] if out else None)}


def track_many(tns, lang="en"):
    api_url, nonce = get_session()
    out = {}
    for i, tn in enumerate(tns):
        if i:
            time.sleep(REQUEST_GAP)
        data = fetch_one(tn, api_url, nonce, lang)
        out[tn] = {"error": data["_error"]} if "_error" in data else (latest_status(data) or {})
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 tracking.py <GWD-number>")
    print(json.dumps(track(sys.argv[1]), indent=2, ensure_ascii=False))
