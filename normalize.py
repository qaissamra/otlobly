#!/usr/bin/env python3
"""
Cleanup library for messy Otlobly order rows — the single biggest time-saver.

Handles the three recurring messes in the Google Sheet:
  1. Phones written with Arabic-Indic digits (٠٥٦…), invisible bidi marks
     (U+2066/2069), spaces and dashes, or a missing leading zero.
  2. Amazon links buried in tracking junk (fbclid, ref, psc, dib, …) or hidden
     behind a.co/d/… shorteners.
  3. The same customer repeated across rows.

Pure stdlib. Network is only touched when you ask to expand a.co shorteners
(expand=True); everything else works offline. Run a built-in check with:

    python3 normalize.py --selftest
"""

import re
import sys
from urllib import request, error
from urllib.parse import urlsplit, urlunsplit

# --------------------------------------------------------------------------- #
# Digits & invisible marks
# --------------------------------------------------------------------------- #
# Arabic-Indic (U+0660-0669) and Eastern/Persian (U+06F0-06F9) -> ASCII.
_DIGIT_MAP = {0x0660 + i: str(i) for i in range(10)}
_DIGIT_MAP.update({0x06F0 + i: str(i) for i in range(10)})
# Bidi isolates/embeddings/marks that wrap phone numbers in the sheet.
_BIDI = dict.fromkeys(
    [0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2066, 0x2067, 0x2068, 0x2069, 0x00A0, 0xFEFF], None)


def ascii_digits(s):
    """Map Arabic/Persian digits to ASCII and drop invisible bidi marks."""
    return (s or "").translate(_DIGIT_MAP).translate(_BIDI)


# --------------------------------------------------------------------------- #
# Phones — normalize to a canonical Palestinian +970 number
# --------------------------------------------------------------------------- #
def normalize_phone(raw):
    """Return {'e164': '+9705…', 'wa': '9705…', 'display': '+970 5…',
    'ok': bool, 'raw': raw} or None for an empty cell.

    Palestinian mobiles are 05XXXXXXXX locally (+9705XXXXXXXX international),
    prefixes 056/057/059. We also repair 9-digit numbers missing the leading 0.
    """
    if not raw:
        return None
    s = ascii_digits(str(raw)).strip()
    plus = s.lstrip().startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    ok = True
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(("970", "972")):
        e164 = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:        # 05XXXXXXXX
        e164 = "+970" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):         # 5XXXXXXXX (missing 0)
        e164 = "+970" + digits
    elif plus:                                                # already intl, other country
        e164 = "+" + digits
    else:
        e164 = "+970" + digits.lstrip("0")                    # best effort
        ok = len(digits.lstrip("0")) == 9

    wa = e164.lstrip("+")
    # Pretty: +970 56 961 3116
    body = e164[4:] if e164.startswith("+970") else wa
    display = ("+970 " + re.sub(r"(\d{2})(\d{3})(\d+)", r"\1 \2 \3", body)
               if e164.startswith("+970") else e164)
    return {"e164": e164, "wa": wa, "display": display, "ok": ok, "raw": str(raw)}


def collect_phones(*cells):
    """Normalize 1..N candidate phone cells, drop empties, dedupe by e164."""
    out, seen = [], set()
    for c in cells:
        p = normalize_phone(c)
        if p and p["e164"] not in seen:
            seen.add(p["e164"])
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Amazon links — clean, expand a.co, extract ASIN, dedupe
# --------------------------------------------------------------------------- #
_ASIN_RE = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|gp/offer-listing|product)/([A-Z0-9]{10})",
    re.IGNORECASE)
_BARE_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)
# Query params worth keeping; everything else is tracking noise.
_KEEP_PARAMS = {"th", "psc"}


def extract_asin(url):
    """Pull a 10-char ASIN out of a full Amazon product URL, or None.
    NOTE: a.co/d/<code> codes are NOT ASINs — expand them first."""
    if not url:
        return None
    m = _ASIN_RE.search(url)
    if m:
        return m.group(1).upper()
    return None


def expand_short_url(url, max_hops=4, timeout=10):
    """Follow a.co/amzn.to redirects to the real Amazon URL WITHOUT downloading
    the page (reads only Location headers). Returns the final URL, or the input
    unchanged on any failure (best-effort, never raises)."""
    class _NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = request.build_opener(_NoRedirect)
    cur = url
    try:
        for _ in range(max_hops):
            req = request.Request(cur, method="HEAD", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            try:
                resp = opener.open(req, timeout=timeout)
                return cur  # 2xx, no redirect -> we've arrived
            except error.HTTPError as e:
                loc = e.headers.get("Location")
                if e.code in (301, 302, 303, 307, 308) and loc:
                    cur = loc if loc.startswith("http") else _join(cur, loc)
                    continue
                return cur
    except Exception:
        return url
    return cur


def _join(base, loc):
    b = urlsplit(base)
    return urlunsplit((b.scheme, b.netloc, loc, "", ""))


def phone_core(raw):
    """Reduce a phone to its national subscriber digits for country-code-agnostic
    matching: drop the +970/+972 (Palestine/Israel) prefix and any leading zero,
    so 0599…, 970599…, 972599…, +9720599… all collapse to the same 9-digit core."""
    d = re.sub(r"\D", "", ascii_digits(str(raw or "")))
    if d.startswith("00"):
        d = d[2:]
    for cc in ("970", "972"):
        if d.startswith(cc):
            d = d[len(cc):]
            break
    return d.lstrip("0")


def clean_amazon_url(url, expand=False):
    """Return a canonical https://www.amazon.com/dp/<ASIN> URL when an ASIN can
    be found, else a junk-stripped version of the original.

    Set expand=True to resolve a.co/amzn.to shorteners over the network."""
    if not url:
        return None
    url = ascii_digits(str(url)).strip()
    is_short = bool(re.search(r"//(a\.co|amzn\.to)/", url))
    if is_short and expand:
        url = expand_short_url(url)

    asin = extract_asin(url)
    if asin:
        return f"https://www.amazon.com/dp/{asin}"

    # No ASIN (e.g. unexpanded a.co): keep host+path, drop the tracking query.
    parts = urlsplit(url)
    kept = [kv for kv in parts.query.split("&")
            if kv.split("=", 1)[0] in _KEEP_PARAMS]
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path,
                       "&".join(kept), ""))


def parse_items(cells, expand=False):
    """Turn raw product-link cells into deduped item dicts.
    Returns [{'raw_url', 'clean_url', 'asin', 'needs_expand'}]."""
    items, seen = [], set()
    for raw in cells:
        if not raw or not str(raw).strip():
            continue
        raw = str(raw).strip()
        clean = clean_amazon_url(raw, expand=expand)
        asin = extract_asin(clean or "") or extract_asin(raw)
        needs_expand = (asin is None and bool(re.search(r"//(a\.co|amzn\.to)/", raw)))
        key = asin or clean or raw
        if key in seen:
            continue
        seen.add(key)
        items.append({"raw_url": raw, "clean_url": clean, "asin": asin,
                      "needs_expand": needs_expand})
    return items


# --------------------------------------------------------------------------- #
# Customer identity
# --------------------------------------------------------------------------- #
def customer_key(phones, name):
    """Stable key to dedupe a customer across rows: first phone, else name."""
    if phones:
        return phones[0]["e164"]
    return "name:" + re.sub(r"\s+", " ", (name or "").strip().lower())


# --------------------------------------------------------------------------- #
# Self-test (offline) — run: python3 normalize.py --selftest
# --------------------------------------------------------------------------- #
def _selftest():
    phones = [
        ("569888059",          "+970569888059"),
        ("059-438-2951",       "+970594382951"),
        ("٠٥٦٩٦١٣١١٦",          "+970569613116"),
        ("⁦0 569 284 374⁩", "+970569284374"),
        ("599476468",          "+970599476468"),
        ("593936694",          "+970593936694"),
    ]
    print("PHONES  raw -> e164")
    pf = 0
    for raw, want in phones:
        got = normalize_phone(raw)["e164"]
        mark = "OK " if got == want else "XX "
        pf += got != want
        print(f"  {mark} {raw!r:28} -> {got:16} (want {want})")

    links = [
        "https://www.amazon.com/gp/product/B09CLKPMVC/ref=ox_sc_act_image_1?smid=A22FR1QUP6EAFM&psc=1",
        "https://www.amazon.com/dp/B07XHGQZX8/ref=mp_s_a_1_1?crid=3QU4&fbclid=IwY2x",
        "https://www.amazon.com/TUCAREST-K100046/dp/B0C1Z5V4HG/ref=sr_1_3?crid=3CDDRF1E64UFY",
        "https://a.co/d/084hXpF5",
    ]
    print("\nLINKS  raw -> clean (asin)")
    want_asins = ["B09CLKPMVC", "B07XHGQZX8", "B0C1Z5V4HG", None]
    lf = 0
    for url, want in zip(links, want_asins):
        clean = clean_amazon_url(url)            # offline: no expansion
        asin = extract_asin(clean or "")
        mark = "OK " if asin == want else "XX "
        lf += asin != want
        print(f"  {mark} {url[:54]:54} -> {asin}")
    print(f"\n{'ALL PASS' if pf==0 and lf==0 else f'FAILURES: {pf} phone, {lf} link'}")
    return pf + lf


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print(__doc__)
