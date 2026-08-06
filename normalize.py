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


# --------------------------------------------------------------------------- #
# Multi-retailer support (Tatabu): brokers buy from Amazon, AliExpress, SHEIN, iHerb.
# Each product URL → (retailer, product_id, clean_url). Amazon keeps its exact ASIN
# handling (Otlobly is unchanged); the others get a path-based id + tracking stripped.
# --------------------------------------------------------------------------- #
# host fragments (each contains a dot, so they match a real domain, not a random
# substring). The Amazon short domain a.co is matched exactly, separately.
_RETAILER_HOSTS = (
    ("amazon", ("amazon.", "amzn.")),
    ("aliexpress", ("aliexpress.",)),
    ("shein", ("shein.", "romwe.")),
    ("iherb", ("iherb.",)),
)
_ALI_RE = re.compile(r"/item/(\d+)\.html", re.I)
_SHEIN_RE = re.compile(r"-p-(\d+)", re.I)
_IHERB_RE = re.compile(r"/pr/[^/?#]+/(\d+)", re.I)


def detect_retailer(url):
    """'amazon' | 'aliexpress' | 'shein' | 'iherb' | 'other' (any http link) | ''."""
    u = ascii_digits(str(url or "")).strip().lower()
    if not u:
        return ""
    probe = u if "://" in u else "https://" + u          # bare host → parse as a URL
    host = urlsplit(probe).netloc
    if host == "a.co" or host.endswith(".a.co"):          # Amazon short link (exact host)
        return "amazon"
    for name, needles in _RETAILER_HOSTS:
        if any(n in host for n in needles):
            return name
    return "other" if u.startswith("http") else ""


def product_ref(url):
    """(retailer, product_id) for a product URL. Amazon → ASIN; the others → their
    path id; unknown → (retailer, None)."""
    r = detect_retailer(url)
    if r == "amazon":
        return "amazon", extract_asin(url)
    pat = {"aliexpress": _ALI_RE, "shein": _SHEIN_RE, "iherb": _IHERB_RE}.get(r)
    m = pat.search(str(url or "")) if pat else None
    return r, (m.group(1) if m else None)


def clean_product_url(url, expand=False):
    """Canonical clean URL for ANY retailer. Amazon → clean_amazon_url (dp/ASIN form);
    others → scheme+host+path with the tracking query stripped."""
    if not url:
        return None
    if detect_retailer(url) == "amazon":
        return clean_amazon_url(url, expand=expand)
    parts = urlsplit(ascii_digits(str(url)).strip())
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, "", "")) or str(url).strip()


def parse_items(cells, expand=False):
    """Turn raw product-link cells (from ANY supported retailer) into deduped item
    dicts. Returns [{'raw_url', 'clean_url', 'asin', 'retailer', 'product_id',
    'needs_expand'}]. Amazon links behave EXACTLY as before (asin populated); other
    retailers get retailer + product_id (asin stays None)."""
    items, seen = [], set()
    for raw in cells:
        if not raw or not str(raw).strip():
            continue
        raw = str(raw).strip()
        retailer = detect_retailer(raw)
        clean = clean_product_url(raw, expand=expand)
        _, pid = product_ref(clean or raw)
        # Amazon: product_id IS the ASIN — keep `asin` set for all the existing ASIN code.
        asin = pid if retailer == "amazon" else None
        if retailer == "amazon" and not asin:
            asin = extract_asin(clean or "") or extract_asin(raw)
            pid = asin
        needs_expand = (retailer == "amazon" and asin is None
                        and bool(re.search(r"//(a\.co|amzn\.to)/", raw)))
        key = pid or asin or clean or raw
        if key in seen:
            continue
        seen.add(key)
        items.append({"raw_url": raw, "clean_url": clean, "asin": asin,
                      "retailer": retailer or None, "product_id": pid,
                      "needs_expand": needs_expand})
    return items


# --------------------------------------------------------------------------- #
# Customer identity
# --------------------------------------------------------------------------- #
_NAME_MARKS = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F,          # zero-widths + LRM/RLM
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E,          # bidi embeddings
     0x2066, 0x2067, 0x2068, 0x2069, 0x061C, 0xFEFF], None)
_NAME_MARKS[0x00A0] = " "                              # NBSP → real space (never delete)


def person_key(name):
    """Canonical key for a customer NAME: strip invisible bidi/zero-width marks
    (WhatsApp copy-paste junk), NBSP→space, collapse whitespace, lowercase."""
    s = (name or "").translate(_NAME_MARKS)
    return re.sub(r"\s+", " ", s).strip().lower()


def customer_key(phones, name):
    """Stable key to dedupe a customer across rows: first phone, else name."""
    if phones:
        return phones[0]["e164"]
    return "name:" + person_key(name)


# --------------------------------------------------------------------------- #
# Self-test (offline) — run: python3 normalize.py --selftest
# --------------------------------------------------------------------------- #
def _selftest():
    phones = [
        ("569000001",          "+970569000001"),
        ("059-900-0002",       "+970599000002"),
        ("٠٥٦٩٠٠٠٠٠٣",          "+970569000003"),
        ("⁦0 569 000 004⁩", "+970569000004"),
        ("599000005",          "+970599000005"),
        ("593000006",          "+970593000006"),
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
    names = [                                    # visually identical spellings must collapse
        ("\u062e\u0627\u0644\u062f \u062c\u0645\u0627\u0644", "\u062e\u0627\u0644\u062f \u062c\u0645\u0627\u0644"),
        ("\u062e\u0627\u0644\u062f\u200f \u062c\u0645\u0627\u0644", "\u062e\u0627\u0644\u062f \u062c\u0645\u0627\u0644"),  # RLM pasted from WhatsApp
        ("\u062e\u0627\u0644\u062f\u00a0\u062c\u0645\u0627\u0644", "\u062e\u0627\u0644\u062f \u062c\u0645\u0627\u0644"),   # NBSP instead of space
        (" \u062e\u0627\u0644\u062f  \u062c\u0645\u0627\u0644 ", "\u062e\u0627\u0644\u062f \u062c\u0645\u0627\u0644"),
    ]
    print("\nNAMES  raw -> person_key")
    nf = 0
    for raw, want in names:
        got = person_key(raw)
        mark = "OK " if got == want else "XX "
        nf += got != want
        print(f"  {mark} {raw!r:28} -> {got!r}")

    print(f"\n{'ALL PASS' if pf==0 and lf==0 and nf==0 else f'FAILURES: {pf} phone, {lf} link, {nf} name'}")
    return pf + lf + nf


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print(__doc__)
