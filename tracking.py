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

# GAASH MappedStatusCode → (human label, colour bucket) for the UI.
CODE_LABEL = {
    "VM": ("On the way to the country", "transit"),
    "K3": ("Arrived in the country", "arrived"),
    "CD": ("Customs — needs your ID/docs", "customs"),
    "K2": ("Cleared customs", "cleared"),
    "AJ": ("Out for last-mile / pickup", "transit"),
    "D1": ("Delivered", "delivered"),
}


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
