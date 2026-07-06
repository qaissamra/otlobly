"""
parcelsapp.com multi-carrier tracking client.

Async API: POST to start tracking (returns a uuid), then poll the same endpoint
by uuid until done. Auto-detects the carrier (UPS/FedEx/DHL/…) and returns the
exact status text parcelsapp shows. Mirrors gaash.py's shape:
    {tracking_number: {status, text, time, carrier, timeline:[...]}}

Smoke test:  PARCELSAPP_API_KEY=... python3 parcelsapp.py <tracking_number>
"""

import json
import os
import sys
import time
from urllib import request, error

API = "https://parcelsapp.com/api/v3/shipments/tracking"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _post(payload, retries=4, timeout=60):
    """POST JSON with retry/backoff on transient errors."""
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        req = request.Request(API, data=data, method="POST",
                              headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "null")
        except error.HTTPError as e:
            if 400 <= e.code < 500:
                return {"_error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa
            last = str(e)
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    return {"_error": f"network error after {retries} tries: {last}"}


def _timeline(shipment):
    """Normalize the event history to [{status, text, time}] (oldest -> newest)."""
    rows = []
    for s in shipment.get("states") or []:
        txt = s.get("status") or s.get("statusText") or s.get("detail") or ""
        rows.append({
            "status": s.get("status"),
            "text": str(txt).strip(),
            "time": s.get("date") or s.get("time"),
            "location": s.get("location"),
        })
    rows.sort(key=lambda r: r["time"] or "")
    return rows


def _exact_status(shipment):
    """The verbatim status parcelsapp shows (prefer the latest descriptive state)."""
    last = shipment.get("lastState") or {}
    return (last.get("status") or shipment.get("status")
            or (shipment.get("states") or [{}])[0].get("status"))


def fetch_statuses(tracking_numbers, api_key, country="Israel",
                   language="en", poll_timeout=90, retries=4, timeout=60):
    """Return {tracking_number: {status, text, time, carrier, timeline}}.
    On failure an entry gets {'_error': ...}. `retries`/`timeout`/`poll_timeout`
    exist so request-deadline callers (otlobly gunicorn) can bound total time;
    the defaults keep launchd callers unchanged."""
    if not tracking_numbers:
        return {}
    payload = {
        "apiKey": api_key, "country": country, "language": language,
        "shipments": [{"trackingId": tn, "destinationCountry": country}
                      for tn in tracking_numbers],
    }
    body = _post(payload, retries=retries, timeout=timeout)
    if "_error" in body:
        return {tn: {"_error": body["_error"]} for tn in tracking_numbers}

    uuid, waited = body.get("uuid"), 0
    while not body.get("done") and uuid and waited < poll_timeout:
        time.sleep(3)
        waited += 3
        body = _post({"apiKey": api_key, "uuid": uuid}, retries=retries, timeout=timeout)
        if "_error" in body:
            break

    out = {}
    for sh in body.get("shipments") or []:
        tn = sh.get("trackingId") or sh.get("trackingNumber")
        if not tn:
            continue
        last = sh.get("lastState") or {}
        text = _exact_status(sh)
        out[tn] = {
            "status": sh.get("status"),
            "text": (str(text).strip() if text else None),
            "time": last.get("date") or (sh.get("states") or [{}])[0].get("date"),
            "carrier": sh.get("detected_carrier") or sh.get("carrier"),
            "timeline": _timeline(sh),
        }
    # Tracking numbers parcelsapp didn't return anything for
    for tn in tracking_numbers:
        out.setdefault(tn, {"_error": "no data returned"})
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: PARCELSAPP_API_KEY=... python3 parcelsapp.py <tracking#> [country]")
    key = os.environ.get("PARCELSAPP_API_KEY")
    if not key:
        sys.exit("Missing PARCELSAPP_API_KEY in environment.")
    tn = sys.argv[1]
    country = sys.argv[2] if len(sys.argv) > 2 else "Israel"
    res = fetch_statuses([tn], key, country)
    print(json.dumps(res, indent=2, ensure_ascii=False))
