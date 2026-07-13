#!/usr/bin/env python3
"""
Gerizim Post (postgerizim.com) — the West Bank last-mile courier GAASH hands
parcels to. Their tracking page is a Next.js app backed by a public JSON
endpoint that takes the SAME GWD number GAASH uses:

    GET https://www.postgerizim.com/api/public/track?q=GWD004694904
    → {"parcel": {...}}   or 404 {"error": "Parcel not found"}

404 simply means the parcel hasn't reached Gerizim yet — a clean signal, not
an error. Crucially, GAASH's "Delivered" only means "handed to last-mile":
Gerizim tells the truth about whether the CUSTOMER has the box (a parcel can
sit on a pickup shelf for days — uncollected COD).

    python3 gerizim.py GWD004694904
"""

import json
import sys
from urllib import request, error, parse

API = "https://www.postgerizim.com/api/public/track"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
NOT_FOUND = "notfound"      # sentinel: parcel not in Gerizim's system (yet)

# Gerizim raw status → (staff label, bucket). Vocabulary lifted from their
# step-tracker code: new/office/sorted → sms/reviewed → awaiting_pickup/
# out_for_delivery → delivered/complete.
STATUS_LABEL = {
    "new": ("At Gerizim office", "office"),
    "office": ("At Gerizim office", "office"),
    "sorted": ("At Gerizim office", "office"),
    "sms": ("SMS sent — awaiting pickup", "sms"),
    "reviewed": ("SMS sent — awaiting pickup", "sms"),
    "awaiting_pickup": ("Awaiting pickup", "pickup"),
    "out_for_delivery": ("Out for delivery", "out"),
    "delivered": ("GERZIM DELIVERED", "delivered"),
    "complete": ("GERZIM DELIVERED", "delivered"),
}


def track(tn, timeout=15):
    """One GWD number → normalized Gerizim status dict, NOT_FOUND, or None
    on a network/parse error (callers must never overwrite a stored status
    with an error)."""
    tn = "".join(c for c in str(tn or "") if c.isprintable()).strip()
    if not tn:
        return None
    url = f"{API}?{parse.urlencode({'q': tn})}"
    try:
        req = request.Request(url, headers={"User-Agent": UA})
        with request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
    except error.HTTPError as e:
        return NOT_FOUND if e.code == 404 else None
    except Exception:  # noqa: BLE001 - Gerizim down ≠ our refresh broken
        return None
    p = (data or {}).get("parcel")
    if not p:
        return NOT_FOUND
    raw = (p.get("status") or "").strip().lower()
    label, bucket = STATUS_LABEL.get(raw, (raw or "At Gerizim office", "office"))
    day = lambda v: (v or "")[:10] or None
    return {
        "status": raw,
        "label": label,
        "bucket": bucket,
        "shelf": p.get("shelf") or None,
        "delivery": bool(p.get("delivery")),      # home delivery vs shelf pickup
        "sms_at": day(p.get("smsSentAt")),
        "eta": day(p.get("estimatedDeliveryDate")),
        "delivered_at": day(p.get("actualDeliveryDate")),
        "created_at": day(p.get("createdAt")),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 gerizim.py <GWD-number>")
    print(json.dumps(track(sys.argv[1]), indent=2, ensure_ascii=False))
