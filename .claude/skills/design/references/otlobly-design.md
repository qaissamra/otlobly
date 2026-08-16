# DESIGN.md — Otlobly

Single source of truth for Otlobly's visual language across its two design systems. When values here and the code disagree, the code wins — fix this file.

## Brand

- **Accent:** `#ff5a1f` (brand orange) — the one color shared by every surface. Deep variant `#e6410c`. WhatsApp green `#25D366` (`--wa`) for WhatsApp CTAs only.
- **Wordmark:** "Otlob**ly**" — the `ly` is orange (`.ly` / `em` in nav). SVGs at `brand/otlobly-logo.svg`, `brand/otlobly-logo-lockup.svg`; staff shell is white-labeled via `branding.py` `__BRAND_*__` tokens (Otlobly vs Tatabu).
- **Voice:** bilingual, Arabic-first. Customer pages `lang="ar" dir="rtl"`; staff labels `عربي · English`.

## System A — Staff app (`web/index.html`)

One 15k-line file: single `<style>`, single `<script>`, served through `branding.render_shell`.

- **Font:** Inter (400/600/700/800), body `font:14px/1.45`. `ui-monospace,Menlo` for tracking numbers / ASINs / codes.
- **Tokens** (`:root`, index.html ~line 10):
  ```css
  --bg:#f6f7f9; --card:#fff; --ink:#101828; --muted:#667085; --line:#e7e9ee;
  --bg2:#f9fafb; --bg3:#f2f4f7;
  --tint-accent:#fff4ee;  /* the ONE orange highlight tint: open rows, unread, active tabs */
  --accent:#ff5a1f; --good:#16a34a; --warn:#d97706; --bad:#dc2626;
  --info:#0ea5e9; --indigo:#6366f1; --cyan:#0891b2;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 6px 16px rgba(16,24,40,.05);
  ```
- **Shell:** fixed 230px left sidebar + main column, sticky topbar, `.wrap` max 1240px. LTR (RTL only inside `#quoteView[dir="rtl"]`).
- **Recurring literals** (no tokens exist for these yet): radii 10–11px, font sizes 11 / 12.5 / 13.5 / 19px, nav padding 9px 11px.
- **Component canon** lives in `UI_AUDIT.md` "standard components" table with line numbers: `statusPill`/`STATUS_COLOR`, two-tier card header, `.field`/`.lbl` via `fld()`, `.po-thumbs`, `.po-btn` + `popMenu`, `editCell()`, `LX_TABLES`/`LXT_COLS` registry, `openStore()`. Board-specific rules: `otlobly-board` skill §1.

## System B — Customer-facing (`templates/` + `static/style.css`)

Warm, soft, mobile-first, RTL-first. `static/style.css` (439 lines) is the intended shared token file.

- **Fonts:** Cairo (`--head`, headings) + IBM Plex Sans Arabic (`--body`); `html[dir="ltr"]` swaps both to Poppins. Body 16px/1.6.
- **Tokens** (`static/style.css:1`):
  ```css
  --orange:#ff5a1f; --orange-deep:#e6410c; --wa:#25D366;
  --ink:#1a160f; --muted:#7c766c; --line:#efe9e0;
  --peach:#ffe1cf; --peach-2:#ffd3ba; --lav:#c9c6f5; --lav-2:#d9d7f8;
  --mint:#c9eede; --mint-2:#d9f2e7; --sky:#cfe0ff; --sky-2:#e0ebff;
  --card-r:28px; --shadow-soft:0 18px 44px rgba(35,24,10,.10);
  --shadow-card:0 10px 30px rgba(35,24,10,.07); --maxw:1100px; --nav-h:60px;
  ```
- **Shapes:** big radii (`--card-r:28px`, pill buttons `border-radius:999px`), soft long shadows, pastel accent pairs (peach/lav/mint/sky).
- **Buttons:** `.btn-orange` (primary), `.btn-wa` (WhatsApp), `.btn-line` (outline); `:active{transform:scale(.98)}`.
- **Illustrations:** unDraw in `.illo` slots (3D asset set still pending); Heroicons outline for small icons. Never hand-drawn SVGs.
- **Pages using this system properly:** `account.html`, `landing.html`, `order_request.html`, `pricing.html` (Google Fonts + shared feel).
- **/v2** (`templates/v2/`, `static/v2/`) is the redesign sandbox — same brand, freer hand; it holds the neutral SHEIN funnel.

## Known fragmentation — the standing backlog

Work through these in `/design` sessions (one batch per PR). Strike items through as they ship.

1. **Five templates redeclare their own inline `:root`** instead of importing `static/style.css`, with a third palette (`--bg:#f7f5ef; --ink:#15140f; --muted:#7a766c; --line:#e8e4d8`): `login.html:9`, `order.html:9`, `submit_id.html:9`, `track.html:9`, `catalog.html:10`. Unify onto System B tokens (visual check each page after — these are live customer pages).
2. **`_nav.html` hard-codes the font stack** (`_nav.html:64`) instead of `var(--head)/var(--body)`.
3. **No spacing / radius / type-scale tokens in the staff app** — radii and font sizes are literals across thousands of rules. If tokenizing, do it as its own mechanical batch, not mixed with visual changes.
4. **Three background palettes across surfaces** (staff `#f6f7f9`, portal white/`#f6f3ec`, small pages `#f7f5ef`) — staff vs customer split is intentional; the *third* (small-page) palette is not, and dies with item 1.

## Rules of engagement

- Extend `static/style.css` for customer pages; extend the index.html `:root` for staff — never a new per-page palette.
- `#ff5a1f` is the only accent. Pastels are surfaces, not accents.
- User-generated strings get `dir="auto"` (see `track.html` for the pattern).
- Server restart required after editing any template or `web/index.html` before browser verification.
