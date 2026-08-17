#!/usr/bin/env python3
"""
GAASH Worldwide (GWD) tracking — same method as gaash-clickup-sync/gaash.py:
a free, no-browser call to GAASH's public WordPress REST endpoint. Scrapes a
fresh public nonce once, then queries each parcel.

Tiered fallback (timelines_with_fallback / track_with_fallback):
  1. GAASH direct (above)
  2. parcelsapp.com — dormant until PARCELSAPP_API_KEY is set in env
  3. last-known cached result, flagged stale:True + fetched_at ("as of" date)

  python3 tracking.py GWD004697561
  python3 tracking.py --fallback GWD004697561
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib import request, error
from urllib.parse import quote

import gaash_browser      # tier-1.5: same GAASH data via headless Chrome (Mac only)
import parcelsapp         # tier-2 fallback tracker (copy of gaash-clickup-sync's client)
import parcelsapp_browser  # tier-2 keyless variant: the public site via Chrome
from paths import data_path

TRACK_PAGE = "https://gaashwd.com/track-parcel/"
DEFAULT_API = "https://gaashwd.com/wp-json/gaash-parcel-status-tracker/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
REQUEST_GAP = 0.7

# Fallback plumbing: last-good results cached on the data disk; the per-GWD
# cooldown keeps customer refreshes during a GAASH outage from hammering
# parcelsapp quota (cache is served in between attempts).
CACHE_FILE = str(data_path("tracking_cache.json"))
PARCELSAPP_COOLDOWN_SEC = 1800
PARCELSAPP_COUNTRY = "Israel"
PARCELSAPP_BROWSER_MAX = 4   # keyless browser lookups per request (~5-15s each)

# GAASH MappedStatusCode → (human label, colour bucket) for the INTERNAL staff UI.
CODE_LABEL = {
    "VM": ("On the way to the country", "transit"),
    "K3": ("Arrived in the country", "arrived"),
    "CD": ("Customs — needs your ID/docs", "customs"),
    "K2": ("Cleared customs", "cleared"),
    "AJ": ("Out for last-mile / pickup", "transit"),
    "D1": ("Delivered", "delivered"),
}

# Pipeline order of the five customer buckets. The parcel pipeline is monotonic
# (a box never un-clears customs), so "furthest stage reached" beats "last
# chronological event" — GAASH StatusTime values are often blank or out of order
# (the same reason staff_status_from_events guards its delivered pick).
BUCKET_RANK = {"transit": 1, "customs": 2, "cleared": 3, "arrived": 4, "delivered": 5}

# CUSTOMER-FACING default map (Arabic): match by GAASH status TEXT first (reliable),
# then code. The customs/authority steps all read "in clearance" so the customer is
# never shown the internal detail (e.g. "Required customer ID"). GAASH's codes are
# reused/ambiguous (AJ was wrongly "out for delivery"), so TEXT wins. Editable from
# the admin Settings table; anything unmatched falls back to DEFAULT_CUSTOMER_LABEL.
DEFAULT_STATUS_MAP = [
    # the real statuses seen on this account (exact text)
    {"match": "Parcel is on the way to destination country", "label": "في الطريق إلى بلدك", "bucket": "transit"},
    {"match": "Required customer ID", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    {"match": "MOC - Palestinian authority", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    {"match": "Ministry of Transportation", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    {"match": "Ministry of Communications", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    # K3 "Arrived at destination country" fires BEFORE clearance in every real
    # GAASH sequence (VM → docs → K3 → K2 Cleared → delivery), so its bucket is
    # customs — bucket arrived would jump the progress bar past clearance.
    {"match": "Arrived at destination country", "label": "وصلت إلى بلدك", "bucket": "customs"},
    {"match": "Cleared customs", "label": "تم التخليص الجمركي", "bucket": "cleared"},
    # GAASH "Delivered" / handed to Gerizim ≠ the customer has it — the box is on
    # its way to OTLOBLY, which re-ships it; only the owner-set Otlobly stage
    # (DEFAULT_OTLOBLY_MAP below) may ever tell the customer "تم التسليم".
    {"match": "Delivered", "label": "في الطريق إلى اطلبلي", "bucket": "arrived"},
    {"match": "Picked up by Gerizim courier", "label": "في الطريق إلى اطلبلي", "bucket": "arrived"},
    # reliable code fallbacks for stages this account hasn't reached yet
    {"match": "K3", "label": "وصلت إلى بلدك", "bucket": "customs"},
    {"match": "K2", "label": "تم التخليص الجمركي", "bucket": "cleared"},
    # CD/SD = the customs-authority codes. Matching the CODE (not just the handful of
    # texts above) means a customs wording we've never seen — e.g. "Parcel held by
    # customs - customs check", live on this account — still reads as clearance
    # instead of falling through to the generic "قيد الشحن" (which told a held
    # customer their parcel was simply in transit).
    {"match": "CD", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    {"match": "SD", "label": "قيد التخليص الجمركي", "bucket": "customs"},
    {"match": "D1", "label": "في الطريق إلى اطلبلي", "bucket": "arrived"},
    # parcelsapp machine statuses (tier-2 source; stamped as the last event's code)
    {"match": "DELIVERED", "label": "تم التسليم", "bucket": "delivered"},
    {"match": "ARRIVED", "label": "وصلت إلى بلدك", "bucket": "arrived"},
    {"match": "TRANSIT", "label": "قيد الشحن", "bucket": "transit"},
    {"match": "PICKUP", "label": "قيد الشحن", "bucket": "transit"},
]
DEFAULT_CUSTOMER_LABEL = "قيد الشحن"

# Owner-set Otlobly stage (the ClickUp-vocabulary dropdown on each package) →
# what the CUSTOMER sees. Statuses match CU_STATUSES spellings verbatim (typos
# included). "{name}" in a label is replaced with the customer's name. Buckets
# reuse the five carrier buckets so the portal progress bars need no changes
# (arrived = step 4, delivered completes). Editable from the admin Settings
# table (customer_tracking.otlobly_map).
DEFAULT_OTLOBLY_MAP = [
    # GAASH posts "Cleared customs" as its own event when it clears a parcel, but it
    # can be days late (or never) while the owner already knows from the clearance
    # thread. Setting a package to "cleared" says so to the customer immediately —
    # same green «تم التخليص الجمركي» the carrier event produces.
    {"status": "cleared", "label": "تم التخليص الجمركي", "bucket": "cleared"},
    {"status": "recieved rd", "label": "استلمتها اطلبلي", "bucket": "arrived"},
    {"status": "recieved no rd", "label": "استلمتها اطلبلي", "bucket": "arrived"},
    {"status": "sent rd", "label": "في الطريق إلى {name}", "bucket": "arrived"},
    {"status": "sent no rd", "label": "في الطريق إلى {name}", "bucket": "arrived"},
    {"status": "complete", "label": "تم التسليم", "bucket": "delivered"},
]

# parcelsapp machine statuses → staff label + colour bucket (the CODE stamped on
# a tier-2 timeline's final event; full words can't collide with GAASH's
# 2-letter codes). Vocabulary is unverified until a PARCELSAPP_API_KEY exists —
# unknown values safely fall back to the event text / generic label.
PARCELSAPP_CODE_LABEL = {
    "DELIVERED": ("Delivered", "delivered"),
    "ARRIVED": ("Arrived in the country", "arrived"),
    "TRANSIT": ("In transit", "transit"),
    "PICKUP": ("In transit", "transit"),
}

# Customer-facing built-in for the same codes: consulted by _match_row AFTER the
# admin status_map, because live deployments have an admin-SAVED map (predating
# the fallback) that would otherwise hide e.g. DELIVERED behind the generic label.
PARCELSAPP_CODE_FALLBACK = {
    "DELIVERED": {"label": "تم التسليم", "bucket": "delivered"},
    "ARRIVED": {"label": "وصلت إلى بلدك", "bucket": "arrived"},
    "TRANSIT": {"label": "قيد الشحن", "bucket": "transit"},
    "PICKUP": {"label": "قيد الشحن", "bucket": "transit"},
}


_SESSION_CACHE = {"at": 0.0, "val": None}   # (api, nonce) reused ~10 min — the
_SESSION_TTL = 600                          # popup checks GWDs one-by-one and
                                            # must not re-scrape the page each call

def get_session(retries=4, timeout=12):
    """Scrape a fresh apiUrl + nonce from the live tracking page, with the same
    retry/backoff as gaash-clickup-sync/gaash.py. timeout=12 (not 30) on purpose:
    worst case ~55s keeps a customer request inside gunicorn's 120s window.
    The scraped session is cached for _SESSION_TTL seconds."""
    if _SESSION_CACHE["val"] and time.time() - _SESSION_CACHE["at"] < _SESSION_TTL:
        return _SESSION_CACHE["val"]
    html, last = None, None
    for attempt in range(retries):
        try:
            req = request.Request(TRACK_PAGE, headers={"User-Agent": UA})
            with request.urlopen(req, timeout=timeout) as r:
                html = r.read().decode("utf-8", "replace")
            break
        except Exception as e:  # noqa
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    if html is None:
        raise RuntimeError(f"Could not reach GAASH page after {retries} tries: {last}")
    non = re.search(r'"nonce":"([a-f0-9]+)"', html)
    if not non:
        raise RuntimeError("Could not find GAASH nonce (site layout may have changed).")
    api = re.search(r'"apiUrl":"([^"]+)"', html)
    val = (api.group(1).replace("\\/", "/") if api else DEFAULT_API), non.group(1)
    _SESSION_CACHE.update(at=time.time(), val=val)
    return val


def clean_tracking(tn):
    return "".join(c for c in str(tn) if c.isprintable()).strip()


OPS_UPLOAD = "https://ops.gaashwd.com/fileUpload"


def ops_deadline(tn, timeout=8):
    """GAASH's hard deadline for a parcel, scraped from their doc-upload page
    ("תאריך פקיעת תוקף הקישור : DD/MM/YYYY" — link expiry). A parcel still
    uncleared past this date is lost for good, so it drives the owner's
    Telegram countdown alerts. Returns "YYYY-MM-DD" or None."""
    tn = clean_tracking(tn)
    if not tn:
        return None
    try:
        # `type` only picks which upload slots the page renders; the expiry we
        # scrape below belongs to the packageId, so any type does. Deliberately
        # NOT gaash_mail.docs_asked_types() — that reads the DB, and this runs
        # inside the tracking sweep for parcels that mostly have no docs_state.
        req = request.Request(f"{OPS_UPLOAD}?packageId={quote(tn)}&type=6",
                              headers={"User-Agent": UA})
        with request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - ops page down ≠ tracking broken
        return None
    m = re.search(r"תאריך פקיעת תוקף הקישור\s*:\s*(\d{2})/(\d{2})/(\d{4})", html)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


# ── GAASH docs state (the parcel-status page's yellow/blue banner) ──────────
# The public status page fires a SECOND call, /parcel-status-data, and renders:
# any AdditionalStatuses entry WITH Links → the yellow "Missing Documents" card
# (GAASH is asking us to act); entries with Links:null → the teal "Customs
# Status" info card (docs are in, parcel just processing). Same nonce pattern
# as get_session, but this is a WP REST nonce scraped from the status page.

STATUS_PAGE = "https://gaashwd.com/he/parcel-status/?TrackingNumber=x"
_STATUS_SESSION_CACHE = {"at": 0.0, "val": None}
_DOCS_LINK_LABEL = {0: "Upload Document", 1: "Upload Permit",
                    2: "Pay now", 3: "Update Info"}


def status_session(retries=3, timeout=12):
    """apiUrl + X-WP-Nonce for the parcel-status REST API, scraped from the
    embedded parcelStatusTrackerData JSON and cached like get_session."""
    if _STATUS_SESSION_CACHE["val"] and time.time() - _STATUS_SESSION_CACHE["at"] < _SESSION_TTL:
        return _STATUS_SESSION_CACHE["val"]
    html, last = None, None
    for attempt in range(retries):
        try:
            req = request.Request(STATUS_PAGE, headers={"User-Agent": UA})
            with request.urlopen(req, timeout=timeout) as r:
                html = r.read().decode("utf-8", "replace")
            break
        except Exception as e:  # noqa
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    if html is None:
        raise RuntimeError(f"Could not reach GAASH status page after {retries} tries: {last}")
    m = re.search(r"var parcelStatusTrackerData = (\{.*?\});?\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("Could not find parcelStatusTrackerData (site layout may have changed).")
    cfg = json.loads(m.group(1))
    val = cfg["apiUrl"], cfg["nonce"]
    _STATUS_SESSION_CACHE.update(at=time.time(), val=val)
    return val


def _docs_result(state, **extra):
    out = {"state": state, "codes": [], "links": [], "arrived": False,
           "checked": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    out.update(extra)
    return out


def docs_status(tn, timeout=25):
    """The status page's docs banner for one parcel, classified:
    action   — a status row carries Links (yellow card: upload/update/pay asked)
    info     — status rows without links (teal card: docs in, customs working)
    cleared / stopped / plain — IsClearance / FinalStatuses / nothing shown
    noanswer — GAASH has NO RECORD of the number (their 404 + code=no_data);
               almost always a wrong/typo'd tracking number, and a real answer
               the UI must show rather than leaving the row 'unchecked'.
    Returns None only when the lookup itself failed (network / their site down),
    so a caller can tell 'no such parcel' from 'ask again later'."""
    tn = clean_tracking(tn)
    if not tn:
        return None
    try:
        api, nonce = status_session()
        url = f"{api}/parcel-status-data?parcel_id={quote(tn)}&lang=he"
        req = request.Request(url, headers={"User-Agent": UA, "X-WP-Nonce": nonce})
        with request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode() or "null")
    except error.HTTPError as e:
        # 404 + {"code":"no_data"} is GAASH ANSWERING "we don't have this
        # number" — not a failure. Any other HTTP status is a real failure.
        try:
            body = json.loads(e.read().decode() or "null")
        except Exception:  # noqa: BLE001
            body = None
        if e.code == 404 and isinstance(body, dict) and body.get("code") == "no_data":
            return _docs_result("noanswer")
        return None
    except Exception:  # noqa: BLE001 - status page down ≠ tracking broken
        return None
    if not isinstance(d, dict):
        return None
    if d.get("code"):
        return _docs_result("noanswer") if d.get("code") == "no_data" else None
    extra = [s for s in (d.get("AdditionalStatuses") or []) if isinstance(s, dict)]
    linked = [s for s in extra if s.get("Links")]
    if d.get("IsClearance"):
        state = "cleared"
    elif d.get("FinalStatuses"):
        state = "stopped"
    elif linked:
        state = "action"
    elif extra:
        state = "info"
    else:
        state = "plain"
    links = [{"url": lk.get("LinkUrl") or "", "type": lk.get("Type"),
              "label": _DOCS_LINK_LABEL.get(lk.get("Type"), "Upload Document")}
             for s in linked for lk in (s.get("Links") or []) if isinstance(lk, dict)]
    return {"state": state,
            "codes": [s.get("StatusCode") for s in extra],
            "links": links,
            "arrived": bool(d.get("IsArrived")),
            "checked": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}


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


def staff_status_from_events(events):
    """Staff label + bucket from a normalized timeline [{code,text,time}, …].
    Delivery is terminal: ANY delivered event wins (matched by code D1/DELIVERED
    or the status text), because GAASH timelines can carry blank or out-of-order
    StatusTime values that break a plain "latest event" pick — a parcel once
    showed "In transit" on the staff page while GAASH said Delivered."""
    events = [e for e in (events or []) if e]
    if not events:
        return None
    norm = lambda e: ((e.get("code") or "").strip().upper(),
                      (e.get("text") or "").strip())
    delivered = None
    for e in events:
        code, text = norm(e)
        if code in ("D1", "DELIVERED") or text.lower() == "delivered":
            delivered = e
    if delivered is not None:
        code, text = norm(delivered)
        return {"code": code or None, "text": text or "Delivered",
                "label": "Delivered", "bucket": "delivered",
                "time": delivered.get("time")}
    last = max(events, key=lambda e: e.get("time") or "")
    code, text = norm(last)
    label, bucket = CODE_LABEL.get(code, PARCELSAPP_CODE_LABEL.get(code, (text, "transit")))
    return {"code": code or None, "text": (text or label or "").strip(),
            "label": label, "bucket": bucket, "time": last.get("time")}


def latest_status(data):
    statuses = (data or {}).get("Statuses")
    if not statuses:
        return None
    return staff_status_from_events([
        {"code": s.get("MappedStatusCode"), "text": s.get("StatusDescription"),
         "time": s.get("StatusTime")} for s in statuses])


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


def events_from_raw(data):
    """GAASH raw response → sorted normalized events [{code, text, time}]."""
    return sorted(
        ({"code": (s.get("MappedStatusCode") or "").strip(),
          "text": (s.get("StatusDescription") or "").strip(),
          "time": s.get("StatusTime")} for s in (data or {}).get("Statuses") or []),
        key=lambda e: e.get("time") or "")


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
    return {"ok": True, "events": events_from_raw(data)}


def timelines(gwds, lang="en"):
    """Full timeline for several GWDs reusing ONE GAASH session (one nonce scrape).
    Returns {gwd: {ok, events} | {ok:False, error}}."""
    out = {}
    try:
        api_url, nonce = get_session()
    except Exception as e:  # noqa
        return {g: {"ok": False, "error": f"GAASH session failed: {e}"} for g in gwds}
    for i, g in enumerate(gwds):
        if i:
            time.sleep(REQUEST_GAP)
        data = fetch_one(g, api_url, nonce, lang)
        if "_error" in data:
            out[g] = {"ok": False, "error": data["_error"]}
            continue
        out[g] = {"ok": True, "events": events_from_raw(data)}
    return out


def _match_row(ev, status_map):
    """Best map row for an event. EXACT TEXT match wins (GAASH's codes are reused
    and unreliable); the code is only a fallback."""
    code = (ev.get("code") or "").strip().upper()
    text = (ev.get("text") or "").strip().lower()
    if text:
        for row in status_map:
            m = (row.get("match") or "").strip()
            if m and m.lower() == text:
                return row
    if code:
        for row in status_map:
            m = (row.get("match") or "").strip()
            if m and m.upper() == code:
                return row
    # Built-in last resort for parcelsapp machine codes (admin-saved maps predate
    # the fallback tier and would otherwise mask e.g. a real DELIVERED).
    return PARCELSAPP_CODE_FALLBACK.get(code) if code else None


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
    # Current = the NEWEST event. Customs can pull a parcel BACK after it arrived or
    # cleared (real examples on this account: arrived 11 Jul → "Parcel held by customs"
    # 23 Jul; arrived 7 Jul → MOC asking for the ID again on 12/20/23 Jul), so
    # "furthest stage reached" would keep claiming cleared while the box is stuck.
    # GAASH's StatusTime is reliable here (0 blank / 0 out-of-order across every live
    # parcel), and events_from_raw sorts by it. Only `delivered` is terminal.
    cur = None
    if out:
        cur = next((e for e in reversed(out) if e["bucket"] == "delivered"), out[-1])
    return {"events": out, "current": cur}


def track_many(tns, lang="en"):
    api_url, nonce = get_session()
    out = {}
    for i, tn in enumerate(tns):
        if i:
            time.sleep(REQUEST_GAP)
        data = fetch_one(tn, api_url, nonce, lang)
        out[tn] = {"error": data["_error"]} if "_error" in data else (latest_status(data) or {})
    return out


# --------------------------------------------------------------------------- #
# Tiered fallback: GAASH → parcelsapp → last-known cache.
# Cache shape per cleaned GWD: {events(raw, pre-remap so later admin status_map
# edits still apply), source, fetched_at(iso), pa_attempt_at(epoch, cooldown)}.
# --------------------------------------------------------------------------- #
def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa - missing/corrupt cache is never fatal
        return {}


def _save_cache(cache):
    # tmp + os.replace = atomic (meta.py pattern). A lost race between the two
    # gunicorn workers only costs one redundant refetch later.
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)


def cache_put_events(tn, events, source="gaash"):
    """Persist a fresh raw-event timeline into the shared last-known cache — the
    customer cache-first path (timelines_cache_first) reads it, so every staff
    refresh doubles as a customer-page refresh. Preserves the parcelsapp
    cooldown stamp. No-op on empty events (never overwrite good data with
    nothing)."""
    tn = clean_tracking(tn)
    if not tn or not events:
        return
    cache = _load_cache()
    prev = cache.get(tn) or {}
    cache[tn] = {"events": events, "source": source, "fetched_at": _now_iso(),
                 "pa_attempt_at": prev.get("pa_attempt_at")}
    _save_cache(cache)


def _parcelsapp_events(shipment):
    """One parcelsapp result → GAASH-shaped events [{code,text,time}] or None.
    The machine status is stamped as the LAST event's code only."""
    if not shipment or "_error" in shipment:
        return None
    events = [{"code": "", "text": (t.get("text") or "").strip(), "time": t.get("time")}
              for t in (shipment.get("timeline") or [])]
    if not events and (shipment.get("text") or shipment.get("status")):
        events = [{"code": "", "time": shipment.get("time"),
                   "text": (shipment.get("text") or shipment.get("status") or "").strip()}]
    if not events:
        return None
    code = (shipment.get("status") or "").strip().upper()
    if code:
        events[-1]["code"] = code
    return events


def _broker_timelines(gwds, lang="en"):
    """Broker tracking (Tatabu): parcelsapp (multi-carrier) is the PRIMARY tracker —
    no GAASH (that's Otlobly's courier). Needs PARCELSAPP_API_KEY (brokers run on the
    hosted app, which has no local Chrome). Serves the last-known cache in between,
    with the same per-number cooldown so refreshes don't burn the parcelsapp quota."""
    res, cache, dirty = {}, _load_cache(), False
    now = time.time()
    key = os.environ.get("PARCELSAPP_API_KEY")
    if key and gwds:
        eligible = []
        for g in gwds:
            cg = clean_tracking(g)
            at = (cache.get(cg) or {}).get("pa_attempt_at")
            if cg and (not at or now - at >= PARCELSAPP_COOLDOWN_SEC):
                eligible.append(cg)
        if eligible:
            try:
                pa = parcelsapp.fetch_statuses(eligible, key, country=PARCELSAPP_COUNTRY,
                                               poll_timeout=30, retries=2, timeout=20)
            except Exception as e:  # noqa: BLE001 — parcelsapp down → serve cache
                print(f"tracking: broker parcelsapp failed ({e})")
                pa = {}
            fetched = _now_iso()
            for g in gwds:
                cg = clean_tracking(g)
                if cg not in eligible:
                    continue
                entry = cache.get(cg) or {}
                entry["pa_attempt_at"] = now
                events = _parcelsapp_events(pa.get(cg))
                if events:
                    entry.update(events=events, source="parcelsapp", fetched_at=fetched)
                    res[g] = {"ok": True, "events": events,
                              "source": "parcelsapp", "fetched_at": fetched}
                cache[cg] = entry
                dirty = True
    for g in gwds:                          # last-known cache for anything not just resolved
        if not (res.get(g) or {}).get("ok"):
            cached = cache.get(clean_tracking(g)) or {}
            if cached.get("events"):
                res[g] = {"ok": True, "events": cached["events"], "stale": True,
                          "source": "cache", "fetched_at": cached.get("fetched_at")}
    if dirty:
        _save_cache(cache)
    return res


def timelines_with_fallback(gwds, lang="en"):
    """Tiered timelines(): same contract plus source/fetched_at on every ok
    entry, and stale:True when the answer is the last-known cache.

    Otlobly (business 1) uses GAASH first (below). A broker tenant uses parcelsapp
    as the primary tracker instead — see _broker_timelines."""
    try:
        from db import current_business
        if current_business() != 1:
            return _broker_timelines(gwds, lang)
    except Exception:  # noqa: BLE001 — scoping unavailable (CLI) → Otlobly/GAASH path
        pass
    res = timelines(gwds, lang)
    cache = _load_cache()
    now = time.time()
    dirty = False

    failed = []
    for g in gwds:
        r = res.get(g) or {}
        if r.get("ok"):
            r["source"], r["fetched_at"] = "gaash", _now_iso()
            prev = cache.get(clean_tracking(g)) or {}
            cache[clean_tracking(g)] = {"events": r["events"], "source": "gaash",
                                        "fetched_at": r["fetched_at"],
                                        "pa_attempt_at": prev.get("pa_attempt_at")}
            dirty = True
        else:
            failed.append(g)

    # Tier 1.5: retry through a REAL browser on this machine (headless Chrome
    # executes the page and calls the API in-page — survives layout changes and
    # bot walls). Render has no Chrome → available() is False → skipped there.
    if failed and gaash_browser.available():
        try:
            braw = gaash_browser.fetch_raw([clean_tracking(g) for g in failed], lang=lang)
        except Exception as e:  # noqa - browser couldn't start/load the page
            braw = {}
            print(f"tracking: browser fallback failed ({e})")
        fetched = _now_iso()
        still = []
        for g in failed:
            data = braw.get(clean_tracking(g))
            if not data or "_error" in data:
                still.append(g)
                continue
            events = events_from_raw(data)
            # empty events = GAASH answered "no record yet" → success, like tier 1
            res[g] = {"ok": True, "events": events,
                      "source": "gaash_browser", "fetched_at": fetched}
            if events:
                prev = cache.get(clean_tracking(g)) or {}
                cache[clean_tracking(g)] = {"events": events, "source": "gaash_browser",
                                            "fetched_at": fetched,
                                            "pa_attempt_at": prev.get("pa_attempt_at")}
                dirty = True
        failed = still

    key = os.environ.get("PARCELSAPP_API_KEY")
    use_browser = (not key) and parcelsapp_browser.available()
    if failed and (key or use_browser):
        eligible = []
        for g in failed:
            at = (cache.get(clean_tracking(g)) or {}).get("pa_attempt_at")
            if not at or now - at >= PARCELSAPP_COOLDOWN_SEC:
                eligible.append(clean_tracking(g))
        if use_browser and len(eligible) > PARCELSAPP_BROWSER_MAX:
            # over-cap GWDs get no attempt stamp → naturally retried next request
            eligible = eligible[:PARCELSAPP_BROWSER_MAX]
        if eligible:
            if key:
                pa = parcelsapp.fetch_statuses(eligible, key, country=PARCELSAPP_COUNTRY,
                                               poll_timeout=30, retries=2, timeout=20)
            else:
                print(f"tracking: parcelsapp (browser, keyless) for {len(eligible)} parcel(s)")
                try:
                    pa = parcelsapp_browser.fetch_statuses_browser(
                        eligible, page_timeout=15, render_timeout=10, gap=1.5)
                except Exception as e:  # noqa - Chrome died: stamp attempts, keep cooldown
                    print(f"tracking: parcelsapp browser tier failed ({e})")
                    pa = {}
            fetched = _now_iso()
            for g in failed:
                cg = clean_tracking(g)
                if cg not in eligible:
                    continue
                entry = cache.get(cg) or {}
                entry["pa_attempt_at"] = now       # attempts count even on failure
                events = _parcelsapp_events(pa.get(cg))
                if events:
                    entry.update(events=events, source="parcelsapp", fetched_at=fetched)
                    res[g] = {"ok": True, "events": events,
                              "source": "parcelsapp", "fetched_at": fetched}
                cache[cg] = entry
                dirty = True
    elif failed:
        print(f"tracking: GAASH failed for {len(failed)} parcel(s); no PARCELSAPP_API_KEY "
              "and no local Chrome -- serving last-known cache")

    for g in gwds:
        r = res.get(g) or {}
        if not r.get("ok"):
            cached = cache.get(clean_tracking(g)) or {}
            if cached.get("events"):
                res[g] = {"ok": True, "events": cached["events"], "stale": True,
                          "source": "cache", "fetched_at": cached.get("fetched_at")}

    if dirty:
        _save_cache(cache)
    return res


# A cached answer older than this gets stale:True + as_of on the customer card.
# The staff board (30-min TTL) and the Leluxe daemon both write through
# cache_put_events, so in practice entries stay well under an hour old.
CUSTOMER_CACHE_FRESH_SEC = 6 * 3600


def timelines_cache_first(gwds, lang="en"):
    """Customer-path timelines: serve the last-known cached events INSTANTLY for
    every GWD that has any (owner decision — the staff side refreshes them every
    ~30 min, so 99% of lookups never wait on GAASH). Live-fetch (full tiered
    fallback) ONLY the GWDs with nothing cached; those persist to the cache so
    the next lookup is instant too. Same contract as timelines_with_fallback."""
    cache = _load_cache()
    res, missing = {}, []
    for g in gwds:
        entry = cache.get(clean_tracking(g)) or {}
        if entry.get("events"):
            r = {"ok": True, "events": entry["events"], "source": "cache",
                 "fetched_at": entry.get("fetched_at")}
            try:
                age = time.time() - datetime.fromisoformat(entry["fetched_at"]).timestamp()
                if age > CUSTOMER_CACHE_FRESH_SEC:
                    r["stale"] = True
            except (KeyError, ValueError, TypeError):
                r["stale"] = True
            res[g] = r
        else:
            missing.append(g)
    if missing:
        res.update(timelines_with_fallback(missing, lang))  # persists to cache itself
    return res


def track_with_fallback(tn, lang="en"):
    """Staff wrapper: latest status via the tiered chain. Same shape as track()
    plus source/fetched_at (+ stale:True when served from cache)."""
    if not clean_tracking(tn):
        return {"error": "no tracking number"}
    r = timelines_with_fallback([tn], lang).get(tn) or {}
    if not r.get("ok"):
        return {"error": r.get("error") or "lookup failed"}
    st = staff_status_from_events(r.get("events"))
    if not st:
        return {"error": "no status yet for this parcel"}
    out = dict(st, source=r.get("source"), fetched_at=r.get("fetched_at"))
    if r.get("stale"):
        out["stale"] = True
    return out


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--fallback"]
    if not argv:
        sys.exit("usage: python3 tracking.py [--fallback] <GWD-number>")
    fn = track_with_fallback if "--fallback" in sys.argv else track
    print(json.dumps(fn(argv[0]), indent=2, ensure_ascii=False))
