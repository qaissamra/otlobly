---
name: otlobly-board
description: Use when working on ANY otlobly-orders staff-board surface — Leluxe (orders/products/packages views), Purchases, Package prep, To-order — adding views/columns/pills, package estimated value, GAASH customs clearance (upload docs / clearance email / reply tracking), or when asked to "check everything" after a board change. Encodes the board design language, the column-grid recipe, the est-value formula, and the full verify-everything workflow so none of it is re-derived.
---

# otlobly-board — Leluxe/Purchases board conventions + verify-everything

Everything lives in ONE file: `web/index.html` (CSS in the single `<style>` block,
JS in the single `<script>` block). Server: `app.py` (routes) + `db.py` (SQLite).
RESTART the server after editing `web/index.html` or `templates/*` — index.html is
token-served via `branding.render_shell` (a stale server leaks raw `__BRAND_` tokens).

## 1. Design language (owner cares about consistency)

- Bilingual labels everywhere: `عربي · English` (Arabic first, ` · ` separator).
- Pills: reuse `lxStatusPill` (ClickUp schema colors), `lxCfPill` (custom-field
  options), `hexPill`/`solidPill`/`tonePill`, `statusPill`/`STATUS_COLOR` (orders).
  Never invent new pill CSS.
- Two-tier headers: white `.po-title` row over tinted `.po-meta` strip; the orange
  `open` expand state (`.po-card.open .po-title{background:#fff4ee}`, round accent caret).
- Rows: `.poc-row` (flex, 44px `.thumb`), compact names via `lxShort3` (first 3
  words, full name in `title=`), `.po-btn` buttons, `popMenu([...])` for ⋯ menus.
- Thumbnail strips: `.po-thumbs` — ALWAYS one line (`nowrap` + `overflow:hidden`,
  min-width one thumb). Duplicate photos MERGE into one thumb with a `.xn` ×N badge
  (`lxThumbs` for Leluxe rows, `poThumbStrip` for Purchases). Never render the same
  image twice in a strip; never let thumbs wrap/stack vertically.
- Row click opens the info popup (`lxInfoOpen`/`pkgInfoOpen`) — guard with
  `if(event.target.closest('select,.pop,.caret,button,a,input,label,img'))return;`.

## 2. Column-grid system (how to add a view or a column)

Registry `LX_TABLES` in web/index.html — one entry per board table:

| id  | grid CSS var | header class            | storage key |
|-----|--------------|-------------------------|-------------|
| ""  | `--lxgrid`   | `.lx-colhead .lx-cols`  | `lx_colw`   |
| "p" | `--lxpgrid`  | `.lx-pcolhead .lx-pcols`| `lx_pcolw`  |
| "k" | `--lxkgrid`  | `.lx-kcolhead .lx-kcols`| `lx_kcolw`  | (Packages view)
| "po"| `--pogrid`   | `.po-colhead .po-cols`  | `po_colw`   |

Recipe for a NEW column on table X (or a new table):
1. Row + header markup: cells are `<span class="lxc">…</span>` inside
   `<span class="lx-Xcols">`; header labels get a resize handle
   `lxRszDown(event,i,'X')` / `lxRszReset(event,'X')`.
2. CSS: the `.lx-Xcols` class must appear in FIVE shared selector lists
   (search for `.lx-pcols` to find them): `display:contents` fallback, `.lxc` base,
   the `>span` ellipsis rule, the `:has(.pop-menu.open)` escape (else dropdowns
   clip — "the empty white box" bug), and the colhead display/lbl rules.
3. Container queries: TWO tiers on `lxboard`/`poboard` — `min-width:940px`
   (grid on, default widths) and `1060px` (roomier widths). Adding a column =
   update BOTH `--lxXgrid` default strings AND the header labels array.
4. `LX_TABLES` entry (mins: floor any cell holding a `.statussel` at 84px).
5. Saved widths only apply when the array length matches the live column count
   (`lxColwApply`) — changing the column count safely invalidates old saves.
6. Below 940px the grid falls back to flowing chips — check that width too.

## 3. Leluxe data model

- `LX.orders[]` (kind parent) each with `.items[]` (loose) and `.packages[]`
  (real 📦 subtasks with their own `.items[]`); `LX.orphans[]`. `lxItemsOf(o)` =
  all items. `lxVisualPkgs(o)` groups loose items by tracking number (display-only).
- ClickUp custom fields via `lxF(row, "<lowercase name>")`. Known fields: asin,
  az id ver, brand, card type, gash date, gash status, name (=account/profile),
  exact name, phone in shipping, quantity ordered, states, total amount,
  tracking number, visa, wallet/watch, who paid.
- Tracking enrichment on `row.data`: `tracking_status`, `gerizim_status`,
  `gaash_deadline`, `tracking_number`, `image`. GWD = GAASH tracking number.
- Money display on boards: `₪` (Total Amount), `toLocaleString()`.

## 4. Package estimated value — the owner's formula (canonical)

```
rowEst(package) = Σ its products' own Total Amount
                + (order Total Amount − Σ ALL the order's priced products)
                  / (number of package rows of that order)
```
The second term is the shipping/tax overhead split EQUALLY PER PACKAGE (it also
absorbs unpriced products), so Σ of an order's package rows always equals the
order total. Example: total 200, 4 products à 30, 2 packages, pkg 1 holds 3 →
3×30 + (200−120)/2 = **130**. Implemented in `lxOrderPkgEst` (Packages view);
tooltip must show the breakdown; `+N?` warn = row has unpriced products.

## 5. GAASH customs clearance flow

- Upload docs page (per GWD): `https://ops.gaashwd.com/fileUpload?packageId=<GWD>&type=N`
  — each `type` adds one upload slot; types in `GAASH_DOCS` (6=ID, 8=passport,
  1=invoice, 7=goods-use…). UI: `gaashUploadOpenGwd(gwd)` (GWD-first, any board).
- Clearance email: ONE GWD per email (so GAASH's reply maps 1:1 to a package).
  Compose = `lxMailCompose(gwd, orderId)` — mailto: flow, templates in Settings →
  🪪 Clearance email (`QSETTINGS.clearance`: email_to/cc, subject_tpl, body_tpl;
  placeholders `{gwd}` `{upload_link}` `{customer}`). Open-in-Mail AND Copy both
  stamp the send (`POST /api/leluxe/pkgmail/sent`).
- Reply tracking (`leluxe_pkg_mail` table, one row per GWD): ✉ pill via
  `lxMailPill(tn)` — `✉ Xd` gray <2d / amber ≥2d / red ≥5d no reply, `✉ ✓` green
  replied; click toggles (`POST /api/leluxe/pkgmail/reply`). Resend restarts the
  cycle (clears replied, bumps sent_count). No inbox integration — hand-marked.

## 6. Verify everything (run after ANY board change)

1. `node --check` the extracted `<script>` block:
   `python3 - <<'EOF'` extract `re.findall(r"<script>(.*?)</script>", src, re.S)` to a
   scratch file `EOF` then `node --check` it.
2. Python touched? `./.venv/bin/python -m pytest test_leluxe.py` (and the
   relevant test_*.py). Worktrees reuse the MAIN repo venv:
   `/Users/leluxegroup2/projects/otlobly-orders/.venv`.
3. Live check — run the app FROM THE WORKTREE with real-message channels neutered
   (env wins over `.env` because app.py uses `os.environ.setdefault`):
   - add a TEMPORARY `.claude/launch.json` config:
     `cd '<worktree>' && PORT=8793 CLICKUP_API_TOKEN= TELEGRAM_BOT_TOKEN= WHATSAPP_TOKEN= META_ACCESS_TOKEN= OTLOBLY_OTP_DEV=1 OTLOBLY_EMAIL_DEV=1 exec /Users/leluxegroup2/projects/otlobly-orders/.venv/bin/python app.py`
   - `preview_start` that config (never Bash); check the port is free first
     (`lsof -nP -iTCP:8793 -sTCP:LISTEN`) — other sessions squat ports; don't kill.
   - login previewtmp/preview1234 (temp admin — never delete it; stale cookies work).
4. In the browser pane: console errors (`read_console_messages onlyErrors`), the
   changed view at desktop width AND narrow (<940px fallback), row-click popup,
   ⋯ menu opens unclipped, column resize applies (`--lx*grid` var), search/filter
   narrows rows, and a MATH AUDIT — recompute any displayed number in the console
   from `LX`/`POS` raw data and compare with the rendered cell.
5. Cleanup before commit: `git checkout -- .claude/launch.json` (it is TRACKED),
   stop the preview server. NEVER commit `.env`, `otlobly.db*`, `config.json`,
   `purchases.json`. Worktree gotcha: Read/Edit with absolute paths — always
   `…/.claude/worktrees/<name>/…`, never the main repo path (same relative layout).

## Pitfalls that already bit us

- Editing the MAIN repo instead of the worktree (identical relative paths) —
  always check the absolute path before Edit; recover via `git -C main diff > patch`.
- `.po-thumbs` wrap → thumbs stacking vertically in nowrap grid rows.
- Dropdown menus clipped by `.lxc` overflow — the `:has(.pop-menu.open)` escapes.
- Stale saved column widths after a column-count change — length guard handles it.
- `launch.json` left modified in a commit; `alerts` firing real Telegram messages
  from a worktree run with live tokens.
