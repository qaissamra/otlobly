"""
Keyless parcelsapp.com tier — drives the PUBLIC tracking page in headless
Chrome when no PARCELSAPP_API_KEY is set.

Primary extraction: capture the page's own tracking XHR
(https://parcelsapp.com/api/v2/parcels) from Chrome performance logs — the
page signs its requests client-side, so we execute their real JS instead of
replaying calls from Python. Fallback: parse the rendered timeline text.
Returns EXACTLY parcelsapp.fetch_statuses()'s shape so callers are agnostic:
    {tn: {status, text, time, carrier, timeline:[{status,text,time,location}]}}
    (+ "_via": "xhr"|"dom" for observability; failures: {tn: {"_error": ...}})

Mac-only tier (needs selenium + Google Chrome); available() is False elsewhere
(e.g. Render). selenium is imported lazily so importing this module is always
safe. Env knobs: PARCELSAPP_BROWSER_DISABLE=1 kill switch,
PARCELSAPP_BROWSER_DEBUG=1 prints captured XHR bodies (adapter tuning),
PARCELSAPP_BROWSER_FORCE_DOM=1 skips XHR capture (tests the DOM parser).

Smoke test:  PARCELSAPP_BROWSER_DEBUG=1 ./.venv/bin/python parcelsapp_browser.py GWD004697561
"""

import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import parcelsapp  # reuse _timeline/_exact_status → contract parity by construction

TRACK_URL = "https://parcelsapp.com/{lang}/tracking/{tn}"
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
API_MARKERS = ("/api/v2/parcels", "/api/v3/shipments/tracking")
NAV_GAP = 2.0
# parcelsapp returns {"error":"RELOAD"} to a raw HeadlessChrome UA — it sniffs
# the UA string, nothing deeper (verified: overriding it returns real data).
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Selectors + page-error phrases live in constants so a site rename is a
# one-line patch. Phrases come from the page's window.polyglotPhrases.
_PARCEL_SELECTORS = ".tracking-info .parcel, .row.parcel"
_ERROR_SELECTORS = ".tracking-info .error, .tracking-info .alert, .parcel .error"
_PAGE_ERRORS = [
    ("no information about your package", "not found on parcelsapp"),
    ("information has not been found yet", "no data yet on parcelsapp"),
    ("forbidden automated tracking", "upstream carrier blocks automated tracking"),
    ("website is down", "upstream carrier site down"),
    ("website is busy", "upstream carrier site busy"),
    ("maintenance", "upstream carrier maintenance"),
    ("invalid tracking number", "invalid tracking number"),
    ("could not detect carrier", "carrier not detected"),
    ("error while reading tracking information", "upstream parser error"),
    ("reload the page", "parcelsapp session error"),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_JUNK_LINES = ("days in transit", "estimated delivery", "add package title",
               "tracking id", "copy", "share")
# The DOM fallback runs ONLY if XHR capture failed (rare — the endpoint is
# proven). parcelsapp's timeline is interleaved with carrier names / FAQ links,
# so an event line is trusted only when it contains a known status phrase;
# everything else is dropped and, if nothing survives, we return None so the
# chain falls through to cache rather than caching garbage.
_STATUS_KEYWORDS = (
    "delivered", "out for delivery", "available for pickup", "ready for pickup",
    "arrived", "customs", "cleared", "released", "required customer id",
    "identification required", "in transit", "departed", "on the way",
    "processed", "destination country", "moc", "palestinian authority",
    "handed", "picked up", "dispatch", "shipment", "shipped", "returned",
    "exception", "held", "collection")

# One probe per poll tick: container text, the #tracking-info "empty" flag,
# any error node text, and whether the manual country/carrier pickers opened.
_PROBE_JS = """
const q = s => document.querySelector(s);
const el = q(arguments[0]);
const err = q(arguments[1]);
const ti = document.getElementById('tracking-info');
const vis = s => { const e = q(s); return !!(e && e.offsetParent !== null); };
return {text: el ? el.innerText.trim() : '',
        empty: ti ? ti.classList.contains('empty') : null,
        error: err ? err.innerText.trim() : '',
        selectors: vis('#select-country') || vis('#select-carrier')};
"""

_DATE_A = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:,?\s*(\d{4}))?(?:,?\s+(\d{1,2}:\d{2}))?")
_DATE_B = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?(?:,?\s+(\d{1,2}:\d{2}))?")


def available():
    """True when this machine can run the keyless browser tier."""
    if os.environ.get("PARCELSAPP_BROWSER_DISABLE"):
        return False
    try:
        import selenium  # noqa: F401
    except Exception:  # noqa
        return False
    return os.path.exists(os.environ.get("GAASH_CHROME_BIN") or CHROME_MAC)


def _from_xhr_json(obj, tn):
    """Adapt ONE captured JSON body → the contract dict, or None if unrecognized.
    THE function to tune if parcelsapp changes their internal response shape."""
    if isinstance(obj, list):
        for el in obj:
            r = _from_xhr_json(el, tn)
            if r:
                return r
        return None
    if not isinstance(obj, dict) or obj.get("error"):
        return None
    tnu = (str(tn) or "").strip().upper()
    if isinstance(obj.get("shipments"), list):          # v3-style body
        cands = obj["shipments"]
        sh = next((s for s in cands
                   if str(s.get("trackingId") or s.get("trackingNumber") or
                          "").upper() == tnu),
                  cands[0] if len(cands) == 1 else None)
        if not sh:
            return None
        last = sh.get("lastState") or {}
        text = parcelsapp._exact_status(sh)
        return {"status": sh.get("status"),
                "text": (str(text).strip() if text else None),
                "time": last.get("date") or (sh.get("states") or [{}])[0].get("date"),
                "carrier": sh.get("detected_carrier") or sh.get("carrier"),
                "timeline": parcelsapp._timeline(sh)}
    if isinstance(obj.get("states"), list):             # v2-style body (the live page)
        states = []
        for s in obj["states"]:
            if not isinstance(s, dict):
                continue
            s = dict(s)
            for k in ("date", "time"):
                v = s.get(k)
                if isinstance(v, (int, float)):         # epoch → ISO (str-only sorts)
                    if v > 1e11:                        # ms epoch
                        v = v / 1000.0
                    s[k] = datetime.utcfromtimestamp(v).isoformat()
            states.append(s)
        rows = parcelsapp._timeline({"states": states})
        if not rows:
            return None
        return {"status": obj.get("status"),
                "text": rows[-1].get("text"),
                "time": rows[-1].get("time"),
                "carrier": obj.get("carrier") or obj.get("detected_carrier"),
                "timeline": rows}
    return None


def _harvest_xhr(driver, tn, debug=False):
    """Scan the (auto-clearing) performance log for the page's tracking XHR and
    adapt the newest parsable body. None when nothing usable was captured."""
    hits = []
    try:
        entries = driver.get_log("performance")
    except Exception:  # noqa
        return None
    for entry in entries:
        try:
            msg = json.loads(entry["message"])["message"]
        except Exception:  # noqa
            continue
        if msg.get("method") != "Network.responseReceived":
            continue
        resp = (msg.get("params") or {}).get("response") or {}
        url = resp.get("url") or ""
        mime = (resp.get("mimeType") or "").lower()
        if any(m in url for m in API_MARKERS) and (
                not mime or "json" in mime or "javascript" in mime):
            hits.append((msg["params"].get("requestId"), url))
    for rid, url in reversed(hits):                     # newest first: last retry wins
        try:
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
            raw = body.get("body") or ""
            if body.get("base64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8", "replace")
            raw = raw.strip()
            if raw and raw[0] not in "[{":              # tolerate a JSONP wrapper
                m = re.match(r"^[^(]{0,80}\((.*)\)\s*;?\s*$", raw, re.S)
                if m:
                    raw = m.group(1)
            obj = json.loads(raw)
            if debug:
                print(f"[parcelsapp_browser] XHR {url}\n{raw[:500]}", file=sys.stderr)
            r = _from_xhr_json(obj, tn)
            if r:
                return r
        except Exception:  # noqa - evicted body, bad JSON, … → try older hit
            continue
    return None


def _parse_date(line, now):
    """Leading date on a rendered line → (iso, remainder) or (None, line).
    Missing year = current year, unless that lands >35 days in the future
    (then it was last year)."""
    m = _DATE_A.match(line)
    if m:
        day, mon, year, hm = int(m.group(1)), m.group(2), m.group(3), m.group(4)
    else:
        m = _DATE_B.match(line)
        if not m:
            return None, line
        mon, day, year, hm = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    mo = _MONTHS.get(mon[:3].lower())
    if not mo:
        return None, line
    y = int(year) if year else now.year
    try:
        dt = datetime(y, mo, day)
    except ValueError:
        return None, line
    if not year and dt > now + timedelta(days=35):
        dt = dt.replace(year=y - 1)
    iso = dt.strftime("%Y-%m-%d") + ((" " + hm) if hm else "")
    return iso, line[m.end():].strip(" ·-–—:\t")


def _from_dom_text(txt, now=None):
    """Rendered timeline text → contract dict (last-resort fallback; runs ONLY
    when XHR capture failed). STRICT by design: an event is a single line that
    BOTH starts with a date AND carries a known status phrase in its remainder.
    parcelsapp's page interleaves carrier names, bare times, and FAQ links
    ('…stuck in customs?'), so anything looser caches garbage — better to return
    None and let the cache tier serve the last-known status."""
    now = now or datetime.now()
    lines = [l.strip() for l in (txt or "").splitlines()]
    lines = [l for l in lines if l and not any(j in l.lower() for j in _JUNK_LINES)]
    rows = []
    for line in lines:
        if "?" in line:                                 # FAQ/help lines
            continue
        iso, rest = _parse_date(line, now)
        if not iso or not rest:
            continue
        if any(k in rest.lower() for k in _STATUS_KEYWORDS):
            rows.append({"status": None, "text": rest, "time": iso, "location": None})
    if not rows:
        return None
    dates = [r["time"] for r in rows if r["time"]]
    if len(dates) >= 2 and dates[0] > dates[-1]:        # site renders newest-first
        rows.reverse()
    low_all = " ".join(r["text"].lower() for r in rows)
    newest = rows[-1]["text"].lower()
    if "delivered" in low_all:                          # terminal state, any position
        status = "delivered"
    elif any(k in newest for k in ("out for delivery", "available for pickup",
                                   "ready for pickup")):
        status = "pickup"
    elif any(k in newest for k in ("arrived at", "arrived in", "cleared customs",
                                   "customs cleared", "released by customs",
                                   "required customer id", "identification required")):
        status = "arrived"                              # conservative, never overstates
    else:
        status = "transit"
    return {"status": status, "text": rows[-1]["text"], "time": rows[-1]["time"],
            "carrier": None, "timeline": rows, "_via": "dom"}


def _match_page_error(probe):
    """{'_error': label} when the probe's error/parcel text matches a known
    page-error phrase, else None. Checked BEFORE the DOM parser so an error
    banner is never mistaken for a timeline."""
    for hay in ((probe.get("error") or "").lower(), (probe.get("text") or "").lower()):
        if not hay:
            continue
        for needle, label in _PAGE_ERRORS:
            if needle in hay:
                return {"_error": label}
    return None


def _classify_error(probe):
    err = _match_page_error(probe)
    if err:
        return err
    if probe.get("selectors"):
        return {"_error": "parcelsapp needs manual country/carrier selection"}
    return {"_error": "render timeout (no timeline, no XHR)"}


def fetch_statuses_browser(tracking_numbers, lang="en", page_timeout=25,
                           render_timeout=15, gap=NAV_GAP, session_budget=None):
    """{tn: contract dict | {'_error': ...}} using ONE headless-Chrome session.
    Raises only when Chrome itself can't start — callers treat that as the
    whole tier failing (same semantics as gaash_browser.fetch_raw)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    debug = bool(os.environ.get("PARCELSAPP_BROWSER_DEBUG"))
    force_dom = bool(os.environ.get("PARCELSAPP_BROWSER_FORCE_DOM"))
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_argument("--lang=en-US")                   # keyword parsing expects English
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={UA}")             # required — see UA note above
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    binary = os.environ.get("GAASH_CHROME_BIN") or CHROME_MAC
    if os.path.exists(binary):
        opts.binary_location = binary
    driver = webdriver.Chrome(options=opts)
    out, started = {}, time.time()
    try:
        driver.set_page_load_timeout(page_timeout)
        driver.set_script_timeout(page_timeout)
        try:
            driver.execute_cdp_cmd("Network.enable", {})   # defensive; usually implicit
        except Exception:  # noqa
            pass
        for i, tn in enumerate(tracking_numbers):
            tn = str(tn).strip()
            if i:
                time.sleep(gap)
            if session_budget and time.time() - started > session_budget:
                out[tn] = {"_error": "session budget exhausted"}
                continue
            r, probe = None, {}
            for attempt in range(3):
                # A fresh headless profile can get {"error":"RELOAD"} until the
                # page's localStorage signature exists — do as told and reload.
                if attempt:
                    time.sleep(1.0)
                try:
                    driver.get_log("performance")       # drain the previous attempt's log
                except Exception:  # noqa
                    pass
                try:
                    driver.get(TRACK_URL.format(lang=lang, tn=tn))
                except Exception:  # noqa - page-load timeout ≠ failure; JS may still fire
                    pass
                probe, deadline = {}, time.time() + render_timeout
                while time.time() < deadline:
                    try:
                        probe = driver.execute_script(
                            _PROBE_JS, _PARCEL_SELECTORS, _ERROR_SELECTORS) or {}
                    except Exception:  # noqa
                        probe = {}
                    if probe.get("text") or probe.get("error") or probe.get("selectors"):
                        break
                    time.sleep(0.5)
                r = None if force_dom else _harvest_xhr(driver, tn, debug=debug)
                if r:
                    r["_via"] = "xhr"
                    break
                combined = ((probe.get("error") or "") + " " +
                            (probe.get("text") or "")).lower()
                if "reload the page" not in combined:
                    break                               # real answer or real error
            if r is None and probe.get("text") and not _match_page_error(probe):
                r = _from_dom_text(probe["text"])       # error banners never parsed
            out[tn] = r or _classify_error(probe)
    finally:
        driver.quit()
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 parcelsapp_browser.py <tracking#> [more…]")
    print("available:", available())
    res = fetch_statuses_browser(sys.argv[1:])
    print(json.dumps(res, indent=2, ensure_ascii=False)[:4000])
