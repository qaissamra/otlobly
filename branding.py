#!/usr/bin/env python3
"""
Per-tenant branding for the staff dashboard (Tatabu white-label — Phase 1).

Every business (tenant) gets its own brand — tab title, favicon, and the sidebar
brand block (logo mark + wordmark + tagline) — resolved from its `brand` config in
the `businesses` table (db.py). Two built-in defaults:
  * business #1  → Otlobly (the B2C brand): byte-for-byte the current dashboard.
  * every other  → Tatabu / تتبع (the B2B default): until a broker uploads their own logo.

`resolve(business_id)` returns the ready-to-insert values; `render_shell(html, brand)`
stitches them into web/index.html SERVER-SIDE, so a broker never sees "Otlobly" — not
even a first-paint flash.

Brokers are provisioned by Otlobly, so brand config is trusted; names/logos are still
HTML-escaped defensively before they reach the page.
"""

import html as _html
from urllib.parse import quote

import db


def _esc(s):
    return _html.escape((s or "").strip(), quote=True)


# --- Otlobly (business #1): the EXACT current dashboard chrome — do not change ----
# Favicon + sidebar mark copied verbatim from web/index.html so business #1 is unchanged.
_OTLOBLY_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
    "%3E%3Crect width='100' height='100' rx='26' fill='%23111'/%3E%3Ccircle cx='46' cy='52'"
    " r='20' fill='none' stroke='%23fff' stroke-width='9'/%3E%3Ccircle cx='46' cy='52' r='6'"
    " fill='%23fff'/%3E%3Ccircle cx='72' cy='30' r='9' fill='%23ff5a1f'/%3E%3C/svg%3E")
_OTLOBLY_MARK = (
    '<svg width="38" height="38" viewBox="0 0 100 100" aria-hidden="true">'
    '<rect width="100" height="100" rx="26" fill="#111"/>'
    '<circle cx="46" cy="52" r="20" fill="none" stroke="#fff" stroke-width="9"/>'
    '<circle cx="46" cy="52" r="6" fill="#fff"/>'
    '<circle cx="72" cy="30" r="9" fill="#ff5a1f"/></svg>')

OTLOBLY_BRAND = {
    "name": "Otlobly",
    "mark_svg": _OTLOBLY_MARK,
    "wordmark_html": 'Otlob<span class="ly">ly</span>',
    "tagline": "Order management",
    "favicon_href": _OTLOBLY_FAVICON,
}

# --- Tatabu / تتبع (default for every broker tenant) ------------------------------
# A tracking motif in the same dark + orange style: a start dot, a dashed route, and
# the orange destination dot (kept where Otlobly's orange dot sits, for continuity).
_TATABU_MARK = (
    '<svg width="38" height="38" viewBox="0 0 100 100" aria-hidden="true">'
    '<rect width="100" height="100" rx="26" fill="#111"/>'
    '<path d="M28 72 50 50" stroke="#fff" stroke-width="7" stroke-linecap="round"'
    ' stroke-dasharray="2 12"/>'
    '<circle cx="28" cy="72" r="7" fill="#fff"/>'
    '<circle cx="72" cy="30" r="10" fill="#ff5a1f"/></svg>')
_TATABU_FAVICON = "data:image/svg+xml," + quote(
    _TATABU_MARK.replace(' width="38" height="38"', "").replace(' aria-hidden="true"', ""))

TATABU_BRAND = {
    "name": "Tatabu",
    "mark_svg": _TATABU_MARK,
    "wordmark_html": 'Tata<span class="ly">bu</span>',
    "tagline": "تتبع · order tracking",
    "favicon_href": _TATABU_FAVICON,
}

# The three placeholders web/index.html carries; render_shell fills them per tenant.
TOKENS = ("__BRAND_TITLE__", "__BRAND_FAVICON__", "__BRAND_SIDEBAR__")


def resolve(business_id):
    """Resolve a business's brand → dict with id/name/title/tagline/favicon_href and
    the sidebar_html block. Business #1 = Otlobly defaults; all others = Tatabu
    defaults, overridden by the business's stored `brand` config (name/tagline/logo)."""
    business_id = business_id or 1
    base = OTLOBLY_BRAND if business_id == 1 else TATABU_BRAND
    try:
        conf = db.get_business_config(business_id, "brand", None) or {}
    except Exception:                       # missing business / bad config → defaults
        conf = {}

    custom_name = (conf.get("name") or "").strip()
    logo = (conf.get("logo") or "").strip()          # a data: URI (Pro-tier upload)
    name = custom_name or base["name"]
    tagline = (conf.get("tagline") or base["tagline"]).strip()

    if logo:
        # An uploaded logo usually carries the name already → show it alone.
        sidebar = (f'<img src="{_esc(logo)}" alt="{_esc(name)}" '
                   'style="height:34px;max-width:180px;object-fit:contain">')
        favicon = logo
        wordmark = _esc(name)
    else:
        wordmark = base["wordmark_html"] if not custom_name else f'<span style="font-weight:800">{_esc(custom_name)}</span>'
        sidebar = (f'{base["mark_svg"]}<span><h1>{wordmark}</h1>'
                   f'<div class="bsub">{_esc(tagline)}</div></span>')
        favicon = base["favicon_href"]

    return {
        "id": business_id,
        "name": name,
        "title": _esc(f"{name} — Orders"),
        "tagline": _esc(tagline),
        "wordmark_html": wordmark,
        "favicon_href": favicon,
        "sidebar_html": sidebar,
    }


def render_shell(html_text, brand):
    """Fill the brand tokens in the dashboard HTML with a resolved brand."""
    return (html_text
            .replace("__BRAND_TITLE__", brand["title"])
            .replace("__BRAND_FAVICON__", brand["favicon_href"])
            .replace("__BRAND_SIDEBAR__", brand["sidebar_html"]))
