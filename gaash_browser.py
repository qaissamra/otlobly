"""
Browser-tier GAASH lookup — used ONLY when the direct urllib call fails.

Opens the real tracking page in headless Chrome and calls GAASH's WP REST API
from INSIDE the page (their live nonce + cookies + a real browser fingerprint),
so it keeps working through site-layout changes or a bot-wall that would block
plain Python. Returns the same raw JSON per parcel that gaash.fetch_one /
tracking.fetch_one return, so callers reuse their existing parsers unchanged.

Mac-only tier: needs `pip install selenium` + Google Chrome. On machines with
neither (e.g. the Render server), available() is False and callers skip it.
selenium is imported lazily so this module is always safe to import.

Smoke test:  ./.venv/bin/python gaash_browser.py GWD004697561
"""

import os
import time

TRACK_PAGE = "https://gaashwd.com/track-parcel/"
REQUEST_GAP = 0.7
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Runs inside the page: read the live nonce/apiUrl the site gave THIS browser
# session, then call the API with the page's own credentials via fetch().
_FETCH_JS = """
const tn = arguments[0], lang = arguments[1], done = arguments[arguments.length - 1];
const d = window.parcelStatusTrackerData || {};
const api = (d.apiUrl || 'https://gaashwd.com/wp-json/gaash-parcel-status-tracker/v1');
fetch(api + '/parcel-tracking-data?parcel_id=' + encodeURIComponent(tn) +
      '&lang=' + encodeURIComponent(lang), {headers: {'X-WP-Nonce': d.nonce || ''}})
  .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
  .then(j => done({ok: true, data: j}))
  .catch(e => done({ok: false, error: String(e)}));
"""


def available():
    """True when this machine can run the browser tier (selenium + Chrome)."""
    try:
        import selenium  # noqa: F401
    except Exception:  # noqa
        return False
    return os.path.exists(os.environ.get("GAASH_CHROME_BIN") or CHROME_MAC)


def fetch_raw(tracking_numbers, lang="en", page_timeout=30):
    """{tn: raw GAASH JSON | {'_error': ...}} using ONE headless-Chrome session.
    Raises when the browser itself can't start/load the page — callers treat
    that as this whole tier failing and move on to the next one."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,900")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    binary = os.environ.get("GAASH_CHROME_BIN") or CHROME_MAC
    if os.path.exists(binary):
        opts.binary_location = binary
    driver = webdriver.Chrome(options=opts)
    out = {}
    try:
        driver.set_page_load_timeout(page_timeout)
        driver.set_script_timeout(page_timeout)
        driver.get(TRACK_PAGE)
        for _ in range(20):  # the nonce object is inlined; poll briefly for slow loads
            if driver.execute_script(
                    "return !!(window.parcelStatusTrackerData||{}).nonce"):
                break
            time.sleep(0.25)
        for i, tn in enumerate(tracking_numbers):
            if i:
                time.sleep(REQUEST_GAP)
            try:
                r = driver.execute_async_script(_FETCH_JS, str(tn).strip(), lang)
            except Exception as e:  # noqa - per-parcel timeout: keep going
                out[tn] = {"_error": f"browser: {e}"}
                continue
            out[tn] = (r["data"] if (r or {}).get("ok")
                       else {"_error": f"browser: {(r or {}).get('error')}"})
    finally:
        driver.quit()
    return out


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python3 gaash_browser.py <GWD-number> [more…]")
    print("available:", available())
    res = fetch_raw(sys.argv[1:])
    print(json.dumps(res, indent=2, ensure_ascii=False)[:2500])
