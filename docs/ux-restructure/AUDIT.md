# AUDIT.md — Otlobly staff app (`/app`), UX restructure Phase 0

**Audited:** `origin/main` @ `a8c53fd` (2026-09-06) · `web/index.html` 15,491 lines / 1,125 KB · `app.py` 7,022 lines, 252 routes.
**Scope:** the staff single-page app served at `/app` — 28 staff views plus the Tatabu platform console in the same shell. Customer pages (`templates/`) are out of scope; §5.3 lists their uploads for completeness only.
**Reproduce every number:** `python3 docs/ux-restructure/tools/inventory.py` → `inventory.md` / `inventory.json` (same folder). Screens: `node docs/ux-restructure/tools/screenshots.mjs` → `screens/before/`.
**Companions (canon, not restated here):** `UI_AUDIT.md` (component canon + the 8 consistency batches shipped 2026-08-04), `APP_AUDIT.md` (findings F-001…F-016), `.claude/skills/design/` (tokens, checklist, fragmentation backlog), `.claude/skills/otlobly-board/SKILL.md` (LXT table recipe, verify-everything workflow), `BRIEF.md`, `MIGRATION.md`.
Line numbers are `web/index.html` unless a file is named. Nothing in this document changes product code.

---

## 0. Corrections to the brief, owner decisions, method

### 0.1 Owner decisions (2026-09-06) that bind this audit

| Question | Decision | Effect on this document |
|---|---|---|
| IA baseline | **Brief §6 as written** — Sales · Fulfillment · Shipping · Finance · Insights, plus Needs attention, Brain clarified, Leluxe as a workspace switcher | §5.8 maps every current item onto those groups. The 2026-09-04 "7 areas" proposal is prior art only. |
| Arabic mode | **"Only English for now"** | RTL/Arabic layout work is deferred; §5.7 is a recorded backlog, not a phase requirement; screenshots are English only. Arabic *content* inside the English UI (customer names, addresses) stays in scope. |
| Status typos (`recieved`, `oredered`, `delievered`) | **Leave labels as they are** | The status registry (§5.9) centralises tone per value but keeps the ClickUp spelling. The brief's "fix recieved" is withdrawn. |
| Most-used pages | **Purchases, GAASH mail, To order, Package prep** | "daily" in §5.1; they follow the reference page in the migration order (§5.10). |

### 0.2 Corrections to the brief (it was written from screenshots)

1. **Stack.** No framework, TypeScript, npm, bundler, linter or Storybook. The staff app is one file: `<style>` L9–978, markup L979–2034, one `<script>` L2035–15491. There is **no `<html>`, `<head>` or `<body>` element** (the file starts `<!doctype html><meta charset>`), so there is no static place for `lang`/`dir`. It is served by `app.py:524` `staff_app()` through `branding.render_shell()` (`branding.py:115`): three string replacements of `__BRAND_TITLE__ / __BRAND_FAVICON__ / __BRAND_SIDEBAR__` for the Tatabu white-label. A "component" here is a JS render helper plus a CSS class family; the only stack-compatible "design-system folder" is a set of plain static files loaded before the inline blocks (decision D1). Standing owner rule (design skill): vanilla JS + CSS, no React, no Tailwind, no npm UI packages.
2. **No routes.** Views are `setView(id)` (L2730) + `localStorage.otl_view`; 28 containers toggled by hand (`classList.toggle("hidden", v!=="…")` ×28), a 34-term `full` expression, `VIEW_TITLES` re-declared inside the function, and a 24-branch `if(v==="…") loadX()` tail. `pushState`/`hashchange`/`location.hash`: 0 occurrences. 29 distinct `localStorage` keys hold what a URL would. "Keep URLs stable" is vacuous today; a hash router is the Phase 2 prerequisite for URL-synced tables and deep links (D4).
3. **The i18n "library"** is `T()` (L2155): it splits a `"عربي · English"` literal on `" · "` and returns one half by `LANG` (L2153). 997 call sites, ≈1,948 bilingual literals, plus 60 `data-en`/`data-ar` attributes swapped by `localizeStatic()` (L2169). `langSet()` (L2175) re-renders only Purchases and Leluxe; every other view keeps its previous-language DOM until re-entered. A second, unrelated dictionary `QSTR` (30 keys, L7775) with its own `QLANG` (default **Arabic**, L7771) drives the quote tool; a third convention `ML_STATUS {ar,en}` (L6763) drives Meta leads. The toggle sets `data-lang` on the root (L2172) and nothing reads it. With "English only for now", new components take plain English labels (D2 covers the ع toggle).
4. **Statuses.** The typos are ClickUp list values mirrored into data (`CU_STATUSES`, L10259 — 31 hard-coded values vs 35 live in `config.json → leluxe.schema.statuses`; `pkgprep.py` says so at its header). Owner: labels stay. Still to fix through one registry: `PAID` has no colour (`STATUS_COLOR` L2042 lacks it → `#555/#eee` fallback), five palettes for the five carrier buckets, red used for empty values, `delivered` in six colours, GAASH-mail state `waiting_reply` labelled "replied".
5. **Tests.** 51 script-style suites (`test_*.py`, never pytest) run by `bash run_all_tests.sh`; "build" = `node --check` of the extracted `<script>`. Baseline on this commit: **51 passed · 0 failed · 1 min 24 s**. Several suites assert markup anchors (`test_po_purchases_redesign`, `test_po_item_declutter`, `test_honest_ui`, `test_gaash_mail`, `test_new_order_form`…), so every migration PR updates its anchors; the parity checklist (§5.10) becomes a new suite in Phase 1.
6. **Prior audits are canon.** `UI_AUDIT.md` fixed the pill layer, `fld()`, `editCell()`, `openStore()`, the `--tint-accent` token and moved seven plain tables onto the LXT engine; `APP_AUDIT.md` carries 16 open findings (F-003 four disagreeing "parcel is done" sets, F-004 `CU_STATUSES` drift, F-008 `lxIsDone` false-positive, F-012 quick-chip counts, F-013 ₪ labelled $, F-016 no user deactivation). This audit cites and extends them; it does not restate them.
7. **Scope.** Staff app only. The customer pages use a second design system (`static/style.css`, Cairo + IBM Plex Sans Arabic, RTL-first) that the `/design` skill already documents with its own backlog.
8. **Brief §14 precision.** Items 2–3: the subtitle is the global `#sub` (L1035) written once by `load()` (L2362) from the *Orders* report — `"N orders · $… outstanding · updated <date with seconds>"` — and shown stale on every view; on Purchases "41 orders" is the *Orders* board's count, while the Σ footer's "N orders" counts POs after the filter. `↻ Refresh` is CSS-hidden outside `orders/brain/picking` (`REFRESH_VIEWS` L2713), so it cannot be refreshed from Purchases. Item 15: the "+" at the end of the header row is `⊕`, built by `lxtHead()` (L4452) — the ClickUp-style Fields panel (column show/hide + custom-field creator), not an add button.

### 0.3 Method

Three read-only inventories over the code (shell/tokens/i18n; uploads/actions/profile codes; statuses/glossary/currency) plus `tools/inventory.py`, which parses the whole file — nothing is sampled. The script's counts are the numbers quoted below; the hand-classified parts (§5.2 decisions, §5.4 action classes, §5.6 terms) name the function or line they rest on. Line numbers from the inventories were re-verified on `a8c53fd`.

---

## 5.1 Route map

There are no URLs. A "route" is a `setView` id (`VIEW_BTN`, L2715, 27 keys) plus, for four views, a sub-tab switch (`poSetView`, `lxSetView`, `gmTab`, Settings panels). Usage: **daily** = owner's answer; others are estimates to confirm at the report.

| # | `setView` id | Sidebar label | Purpose today | Main entity | Primary action today | Template | Usage | Gate / notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `brain` | 🧠 Brain | Landing page: urgent · due · forgotten · money · last-7-days, every row deep-links via `setView` (`brain.py` rules engine) | work item | none — read and jump | T4 | landing, daily | default landing for non-restricted roles; D5 |
| 2 | `purchases` | 📦 Purchases | Amazon purchase orders; sub-views `orders` tree · `packages` flat · `products` flat · `customers` · `split` (cost split, money roles) | Purchase order → package → item | `+ New order` (creates a **PO**) | T1 | **daily** | title says Purchases, footer counts "orders" (= POs); D7 |
| 3 | `needorder` | 💡 To order | REQUESTED / QUOTED / PAID queue as product rows + the Quick-quote tool (`#quoteView`, own `QLANG`) | Order + items | `🛒 Move N to cart` (only bulk action) | T1 | **daily** | `quote` is an alias of this view |
| 4 | `leluxe` | ⌚ Leluxe | ClickUp "AZ (2)" mirror: `orders` · `packages` · `products` · `dashboard` · `goal` (`lxSetView`, L3816) | Leluxe order / package / item | `⚙ Tools ▾` (sync, discover, organise…) | T1 (+T4 dashboard, goal) | weekly (ask) | `admin_actions` + `FEAT.leluxe`; owner: workspace switcher |
| 5 | `goals` | 🏆 Goals | Campaign targets by category across ClickUp lists + POs | goal | edit target inline | T4 | weekly (ask) | `admin_actions` + `FEAT.leluxe` |
| 6 | `deposits` | 💵 Deposits | Payments ledger: deposit · collect · refund (LXT `dp`) | payment | record a payment (form at top) | T1 | weekly (ask) | `view_money` |
| 7 | `orders` | 🏠 Orders | Every customer order, KPI cards, bulk delete (LXT `od`) | Order | `＋ Add order` (top bar) | T1 | weekly (ask) | the only view with row checkboxes |
| 8 | `incart` | 🛒 In cart | IN_CART orders + one typed cart cost (LXT `ic`) | Order | `✅ Ordered` per row | T1 | weekly (ask) | `view_money` |
| 9 | `metaleads` | 📣 Leads | Messenger / Instagram / lead-form leads, status select, note | Lead | `➕ order` per lead | T1 | weekly (ask) | `view_meta_leads` + `FEAT.leads`; sync happens on GET |
| 10 | `customers` | 👤 Customers | CRM list (LXT `cu`), profile panel, ID gallery | Customer | row → profile; `↻ Sync from orders` | T1 (+T2 profile) | weekly (ask) | — |
| 11 | `pkgprep` | 🎁 Package prep | Ready / waiting parcels to pack, review-request cohort, WhatsApp texts | package per customer | status picker / WhatsApp | T1 | **daily** | — |
| 12 | `bulksearch` | 🔎 Bulk search | Paste GWDs → where each package lives on both boards (LXT `bs`) | Parcel (GWD) | Search | T1 (query) | weekly (ask) | client-side only → Shipping › Tracking |
| 13 | `gaashmail` | 📧 GAASH mail | Customs clearance: tabs `conv` · `ov` · `seq` (Workflows) · `tpl` · `ready` · `docs` · `fcast` · `dash` (`gmTab`, L11547) | Parcel thread | `📧 Enroll packages` | T1 (+T4 overview/forecast/analyze, T3 builders) | **daily** | `edit_fulfillment` + `FEAT.leluxe` |
| 14 | `flags` | 🚩 Flags | Gmail "action required" watcher: open flags, history, inboxes | Flag | manage inboxes; done via Telegram | T1 | weekly (ask) | `edit_fulfillment` + `FEAT.leluxe` → Needs attention |
| 15 | `pnl` | 📊 P&L | Revenue − Amazon cost − Meta spend, drill-downs | money | none (drill-down modals) | T4 | weekly (ask) | `view_pnl` |
| 16 | `activity` | 🕑 Activity | Append-only log with filter chips | event | filter | T4 | weekly (ask) | — |
| 17 | `settings` | ⚙️ Settings | 13 stacked panels (pricing, fx, tracking labels, employee statuses, custom fields, GAASH mail, Telegram, Meta spend, website sections…) | setting | Save (per panel) | T5 | rare | `admin_actions` |
| 18 | `team` | 👥 Team | Staff accounts, roles, password reset | user | add user | T1 | rare | `manage_users`; no deactivate (F-016) |
| 19 | `trash` | 🗑 Trash | Soft-deleted items (LXT `tr`) | trashed item | Restore | T1 | rare | — |
| 20 | `enterPlatform()` → `platoverview` · `brokers` · `brokerprofile` · `plans` · `usage` · `platactivity` | 🏗 Tatabu | Multi-tenant console; replaces the whole sidebar (`PLAT_VIEWS` L6906) | broker / plan | provision broker | T4 / T1 / T2 | rare | `admin_actions` + `business.id === 1`; D11 |
| 21 | `syncClickup(this)` | ↗ Sync ClickUp | An **action** in the nav (runs the ClickUp sync) | — | itself | — | rare | `admin_actions` + `FEAT.clickup` → Settings › Integrations |
| — | `catalog` (hidden) | — | Public storefront products (LXT `ct`); nav removed 2026-07-22, view + loader + 4 routes alive | catalog item | add product | T1 | none | removal candidate (D6); `VIEW_BTN` still maps to a dead `catalogBtn` |
| — | `picking` (hidden) | — | Picking list; no button, `togglePicking` has no caller, renders on every refresh into a hidden div | order | print | T1 | none | removal candidate (D6); dead `pickBtn` |

Public routes, for completeness (out of scope): `/` landing · `/login` · `/setup` · `/order` + `/order/<draft>` · `/track` · `/account` · `/pricing` · `/catalog` · `/id/<token>`.

Shell facts the templates must respect: sidebar = 21 buttons in 3 groups (Main menu 14 · Insights 2 · System 5, L996–1024) + the platform nav (5 views + Back, hidden) + quota box + user chip; top bar (L1033) = ☰ · `#pageTitle` + `#sub` · ع · 🔔 · `＋ Add order` · `↻ Refresh` (both view-gated through `body.hide-addorder / hide-refresh`); **no global search, no user menu, no breadcrumb**. Gates live in `applyRole()` (L2189): `view_pnl`, `admin_actions` (+`FEAT.clickup`, `FEAT.leluxe`, `business.id===1`), `manage_users`, `view_meta_leads` (+`FEAT.leads`), `edit_fulfillment`, `view_money`; `RESTRICTED_NAV.sales` (L2187) keeps only `needBtn`, `metaLeadsBtn` and the dead `catalogBtn`, landing on `needorder`.

---

## 5.2 Component inventory

Counts are from `inventory.md`; "uses" = call sites or `class=` occurrences in `web/index.html`. Decision: **keep** = becomes (the base of) the canonical component · **merge** = its call sites move onto the canonical one and the implementation is deleted in the same phase · **delete** = removed once nothing references it.

### Buttons — 6 families, 5 radii, 4 padding scales

| Implementation | Defined | Uses | What differs | Decision |
|---|---|---|---|---|
| bare `button{}` + `button.primary` + `button.accent` | CSS L54, L57, L58 | `.accent` 54 · `.primary` 1 | 10 px radius, 8×13 padding; the default look of every unstyled button | merge → `Button` |
| `.po-btn` (+ `.accent` L244, `.danger`) | CSS L241 | 129 | 8 px radius, 4×10 padding; the board button; the only family with a danger variant | **keep** as `Button` base (secondary / primary / danger, sm / md) |
| `.minibtn` (+ `.danger`) | CSS L292 | 213 | 7 px radius, 3×8 padding, 11.5 px; no background of its own — inherits `button{}` | merge → `Button sm` |
| `.iconbtn` | CSS L235 | 19 | borderless, 6 px radius; top-bar ☰ / ع; no `aria-label` except ☰ | merge → `Button icon` (tooltip + `aria-label` required) |
| `.qchip` (+ `.alert`) | CSS L267 | 17 | Brain / Goals metric chips that are also clickable | merge → `Tag` / `Stat` |
| `.chip` (+ `.on`) | CSS L339 | 6 | quote-tool language switch, filter chips | merge → `Tabs` / `FilterBar` chips |
| `.statussel` | CSS L296 | status `<select>` painted like a pill (`statusSelect()` L2500) | native select, macOS ignores option colours | merge → `Select` + `Badge` |
| links styled as buttons (`<a class="minibtn">`) | Settings backup link | 1 | anchor, not button | merge |

### Tables — three paradigms

| Implementation | Defined | Where used | What differs | Decision |
|---|---|---|---|---|
| **LXT engine** — `LX_TABLES` L3594, `LXT_COLS` L4045, `lxtHead` L4452, `lxtCells` L4497, `lxtWrap`, `lxtGridApply` L4401; CSS `.bt-clip` L787 (sticky header), `.bt-wrap` L825 (h-scroll), `.bt-pin` L847 (sticky first column), `.fld-anchor` L798 (⊕ panel) | JS L4045–4955 (nested inside the Leluxe block) | 15 tables: Leluxe `""`/`p`/`k`, Purchases `po`/`pok`/`pop`, Bulk search `bs`, GAASH enroll `en`, Orders `od`, Customers `cu`, Deposits `dp`, Catalog `ct`, In cart `ic`, Trash `tr`, Workflows `wf` — 19 `lxtHead` calls, 35 `lxtCells` | CSS-grid rows with **pixel** column widths, per-user resize / reorder / hide / sort / pin persisted in `lxt_hidden_* · lxt_order_* · lxt_sort_* · lxt_pin_* · *_colw` localStorage keys; no selection checkboxes (except Orders' own), no keyboard navigation, no virtualisation, status/actions not pinned to the end | **keep** as `DataTable`; extend with `BulkActionBar`, end-pinned status/actions, keyboard, `Skeleton`, `EmptyState`, URL-synced state |
| `neTable` (To order) — `NE_COLS` L7385, `neTable` L7389; CSS `.ne-tbl` L393, `.col-resize` L398 | To order only | a real `<table>` with its own resize handles and `ne_colw` storage; no hide / reorder / sort | delete after To order migrates |
| raw `<table>` ×20 inside `.tbl-scroll` (L390) | P&L ×7 (markup L1117+ and `renderPnl` drill-downs), Settings ×3 (tracking labels, employee statuses, custom fields), platform console ×3, Team, Activity, Picking, Deposits-by-customer, To order (`neTable`), GAASH templates, GAASH analyze | plain `th,td` (L278, `text-align:left`), no sort, no sticky header, scroll without pin | delete → `DataTable` |

### Pills / badges

| Implementation | Defined | Uses | What differs | Decision |
|---|---|---|---|---|
| `.pill` rule **twice** | CSS L287 (`inline-flex; gap:5px; 11px`) and L963 (`inline-block; 10px; uppercase; letter-spacing:.4px; margin-left:6px`) | every pill | identical specificity, the later rule wins: `gap` and `align-items` are inert, every pill is 10 px uppercase with a physical left margin | merge → one `.badge` rule |
| `statusPill()` | L2443 | 8 | `STATUS_COLOR` lookup, prints the raw enum (`REQUESTED`…) | merge → `Badge` via `status.js` |
| `tonePill()` / `hexPill()` / `solidPill()` / `gaashBucketPill()` | L9578 / L9581 / L9588 / L9596 | 88 / 32 / 11 / 3 | tone map `TONE` (L9571, only `gray` uses tokens), `${hex}1a` tints, solid with luminance-picked ink | **keep** `tonePill` semantics as `Badge` (tone-driven); hex/solid become registry entries |
| `lxStatusPill()` · `lxCfPill()` | L3253 / L3304 | 12 / 12 | colours from the live ClickUp schema (`LX.schema`) | merge (registry reads the schema) |
| 15 domain builders: `lxGashPill`, `lxGerizimPill` ≡ `pkgGerizimPill` (identical bodies), `pkgGaashPill`, `pkgDatePill`, `pkgDocsPill` L10033, `gmDocsStatePill` L13767, `lxDeadlinePill`, `lxDocsPill`, `lxNoDataPill`, `lxMailPill`, `lxConfPill`, `lxMergedStatusPill`, `lxGashRollupPill`, `gzBadge`, `dueChip` L7215 | Leluxe / Purchases / GAASH mail regions | each hard-codes its own palette and wording (docs state has two glyph sets, §5.5) | merge → `Badge` + `AttentionBadge` (late / no tracking / missing docs / no reply) |
| raw `<span class="pill" style=…>` literals | 48 remaining (policy comment L2037 forbids new ones) | — | hand-picked colours | delete |

### Meta strips, cards, headers

| Implementation | Defined | Uses | What differs | Decision |
|---|---|---|---|---|
| `fld(lbl,val,style)` | L2449 | 22 | one helper, **four** CSS homes: `.po-meta .field` L103, `.pkg-meta .field` L163, `.poc-meta .field` L174, `.ne-meta .field` L959 | keep helper → `Stat` / meta row with one CSS home |
| `.po-card` two-tier header (`.po-title` + `.po-meta`) | CSS L96–105 | Purchases cards, Add-order panel (via `PO_MOUNT` L2693), Meta leads cards | asymmetric physical padding on the title (`11px 14px 11px 8px`) | keep as `RowExpansion` header pattern; logical padding |
| `.poc-meta` (Purchases › customers), `.ne-metarow` (To order) | CSS L172, L956 | 2 | near-identical strips with different paddings | merge into the one meta row |
| `.card` / `.panel` | CSS L61 / L254 | KPI cards, Brain tiles, Settings, forms | two names for one surface (16 px radius, shadow) | merge → one surface token set |
| `.kv` rows | CSS L258 | customer profile | key/value list | keep as `DetailDrawer` metadata list |
| page header: `<h1 id="pageTitle">` + `<div id="sub">` L1035; per-view `<h2>` ×44 and `.toolbar` ×21 (L272) | markup | every view | `#sub` is written once by `load()` (L2362) with the Orders report and shown on all views; the toolbar h2 carries repeated inline styles (`margin:0;font-size:17px;font-weight:800`) | merge → `PageHeader` (breadcrumb, title, `Stat`s, one primary, ⋯) |

### Inline edit, expand state, menus, toasts, empty states

| Implementation | Defined | Uses | Decision |
|---|---|---|---|
| `editCell(el,opts,save,cancel)` — the one inline-edit recipe (UI_AUDIT Batch 1) | L2462 | 11 | keep → `DataTable` inline edit; move its `background:#fff` literal to a token |
| `openStore({inverted,persist})` — expand-state sets (Batch 7) | L2487 | 6 | keep → `RowExpansion` state |
| `.pop` / `.pop-menu` L183–184 (already `inset-inline-end`), `popMenu()` L10186, `popToggle()`; column context menu `lxtColMenu`; `.notif-menu` L194 | JS/CSS | 13 / 15 | keep → `DropdownMenu`; add keyboard + `aria-haspopup` |
| `toast(msg)` L2065, `#toast` L308 (`position:fixed; right:18px; bottom:18px`) | JS/CSS | **379** calls | keep → `Toast` with variants (success / error / warning / info), a queue, `role="status"`, optional action; today one slot, one look, 3.5 s |
| `.empty` L337 | CSS | 21 | merge → `EmptyState` (icon, text, primary action) |
| ad-hoc empty/hint text via `muted2` | — | 475 | merge where it is an empty state |
| native `confirm()` / `prompt()` / `alert()` | — | 47 / 15 / 2 | delete → `ConfirmDialog` / `FormLayout` (the GAASH wizard already replaced its confirm with an in-page review step; the reason is recorded in code) |

### Modals, popovers, tabs, filters, forms

| Implementation | Defined | Uses | What differs | Decision |
|---|---|---|---|---|
| `.az-modal` overlay | CSS L966; 22 roots L1803–2023 (`poDetailModal`, `pkgInfoModal`, `lxSyncReportModal`, `gzBulkModal`, `bulkModal`, `notifyModal`, `pnlDrillModal`, `azModal`, `gmStatModal`, `priceImgModal`, `orderEditModal`, `itemEditModal`, `newOrderModal`, `pkgEditModal`, `poEditModal`, `lxActivityModal`, `lxGoalSetModal`, `lxInfoModal`, `lxConflictModal`, `lxEditModal`, `lxAz2HistModal`, `lxMoveModal`) | 22 | each root has its own `xxxOpen()/xxxClose()` pair; no focus trap, no Esc, no `aria-modal`, no scroll lock, no unsaved-changes guard; `#bulkModal` (`bulkOpen` L8482) is reskinned by 6 body classes; `<dialog>` unused | merge → one `Modal` controller (sm / md / lg), `DetailDrawer`, `ConfirmDialog` |
| tabs ×3: GAASH mail 8 tabs (`gmTab` L11547, hand-styled `.minibtn`s), Purchases 5-segment (`poSetView`), Leluxe 5-segment (`lxSetView` L3816), quote-tool language chips; `.chips` rows L338 (Activity, P&L, To order `neChip`) | markup/JS | 4 patterns | different markup, states persisted in 4 localStorage keys (`gm_tab`, `po_board_view`, `lx_view`, `otl_gmdocs_view`) | merge → `Tabs` (URL-synced) |
| search: `.search` L275 used by Orders `#search` and Customers `#custSearch`; inline-styled clones `#poSearch`, `#depFilter`, `#lxSearch`, `#flagHistFilter`; 14 `.cu-search` L216 popover searches | markup | 6 + 14 | five boxes, two looks; the Purchases and Leluxe **filter builders** (rows AND-ed, saved in `po_filters` / `lx_filters`) are the only real filter UIs; Orders has two `<select>` filters; Activity/P&L/To order use chips | merge → `FilterBar` (search + chips + Add filter + Clear all; builders become the "Add filter" popover) |
| forms: `.form-grid` L297 two-column grid (Settings, Customers, Deposits, Team, Brokers); `<label class="fld">`; 252 `<input`, 43 `<select`, 8 `<textarea` | markup | — | no shared validation, no inline errors (all feedback is a toast), no required markers, no focus-first-error | merge → `FormLayout` (`FormSection`, `FormFooter`) |
| icons | — | 2,168 emoji / symbol glyphs, 191 distinct (top: → 179 · ⚠ 141 · ✓ 133 · 📦 104 · 🪪 64 · ✅ 62 · ✕ 60); 22 in the sidebar; directional glyphs 310 (→ ← ↩ ▸ ▾ ⤴ ↗ ↙ ⇒) that never mirror; `<svg>` ×1 (brand); no icon library | everywhere | platform-dependent rendering, no size/colour/stroke control, emoji baked into `data-en`/`data-ar` pairs | replace → inline SVG sprite (D3) |

### Tokens and type (for §5.9)

One `:root` (L10) with **17** custom properties (`--bg --card --ink --muted --line --bg2 --bg3 --tint-accent --accent --ink2 --good --warn --bad --info --indigo --cyan --shadow`); `--info` and `--indigo` are unused in CSS; `var()` is used 442× in CSS against **125 hex literals (70 distinct) in CSS and 520 (136 distinct) file-wide**; 17 runtime grid variables (`--lxgrid … --wfgrid`, `--btpin`). **22 distinct `font-size` values** in CSS (9–34 px; the three most used are 11, 11.5 and 12.5 px, i.e. below the declared 14 px base). Inter only (`fonts.googleapis.com`, no Arabic subset); the mono stack is declared three different ways (`.mono` L289 is the canonical one). No spacing, radius, z-index or motion scale; `prefers-reduced-motion` / `prefers-color-scheme`: 0.

### Accessibility baseline

`aria-*` 4 · `role=` 0 · `alt=` 3 · `tabindex` 1 — against 717 `onclick=` and 458 `title=` attributes (the `title` tooltip carries the explanatory layer, invisible on touch and to screen readers). No visible focus style is defined for the board rows or pills; keyboard operation of tables and menus does not exist.

---

## 5.3 Upload and import inventory

**Architecture fact:** `app.py` contains **no `request.files`**. Every upload is a client-side `FileReader.readAsDataURL` → base64 data-URL → JSON `POST`; the two restore endpoints read a raw zip body and have no UI. The base64-image write is copy-pasted in **five** routes with five different validation postures (size cap on three of them, four filename-sanitisation styles). There is **no drag-and-drop anywhere** (`ondrop`/`dragover`: 1 occurrence — column reordering, L4480); the dashed "paste here" tiles in the package popup and the new-PO modal are click/paste targets only.

### Staff app — file and clipboard entry points

| # | Surface | Control (`web/index.html`) | Accepts / limits | Endpoint (`app.py`) | Backend | Validation | Error UX | Success UX |
|---|---|---|---|---|---|---|---|---|
| 1 | Purchases › order card ⋯ → 📷 Screenshot | `#poImgInput` L1218; `poUpload()` L10818 | image/*, single, **no size cap** | `POST /api/po_image` L2434 | `purchases.set_screenshot` → `IMAGE_DIR` | `_safe_seg` on id/ext (L2329); GET gated `view_cost` | toast "Upload failed" (server text dropped) | toast + re-render |
| 2 | Purchases › order detail → 📷 upload photos | `#podImgInput` L1219 (multiple); `podUpload()` L10886 | image/*, multi, sequential | same | same | same | per-file failure swallowed | toast count, then "attached" |
| 3 | Purchases › order detail → ⌘V | `document.addEventListener("paste")` L10906 | first clipboard image | same | same | none client-side | toast | toast |
| 4 | Purchases › ＋ New order → ⌘V or click-to-pick | paste L10923; `noPickImage()` L10725 (`inp.type="file"` L10727), `noStageImage()` L10717; upload after create in `noSave()` L10774 | image/*, multi, staged as data-URLs | `/api/po_image` after `POST /api/purchase` | same | none | toast "created but image upload failed" — order exists, image lost | toast |
| 5 | Purchases › package popup → ⌘V | paste L10421 (hint tile inside `pkgInfoOpen`) | image/*, **15 MB**, ext whitelist | `POST /api/purchase/package/image` L2357 | `purchases.IMAGE_DIR` | the best-validated route: b64 try/except, empty check, `_PKG_IMG_MAX` L2342, `_safe_seg` | toast with server error | toast |
| 6 | Customers › 🪪 View IDs → ＋ Upload ID | `#custIdInput` L1171 (`image/jpeg,image/png,.pdf`); `custIdUpload()` L11071 | jpeg / png / pdf, single | `POST /api/customer_image` L2495 | `customers.ID_DIR` | `manage_customers`; PDF gate (PR #134); filename built from `customer_id` | toast with server error | toast |
| 7 | Leluxe › row editor → attachments | `<input type="file" multiple onchange="lxImgUpload(this)">` L6009; `lxImgUpload()` L6569 | image/*, multi, no cap | `POST /api/leluxe/image` L4062 | `leluxe.add_image` → ClickUp push | `admin_actions` + `leluxe` feature | toast per file | toast "syncing to ClickUp ↗" |
| 8 | To order › quick quote → 🧾 proof | L7823 `qPickProof` → `qResizeImage` L7838 | image/*, client resize 1400 px / q0.88 | none (kept in `QROWS`, sent with the quote) | client canvas | none | silent | thumbnail |
| 9 | To order › quick quote → 🖼 product image | L7826 `qPickImage` | image/*, 760 px / q0.82 | none | client canvas | none | silent | thumbnail |
| 10 | To order › quick quote → window ⌘V | `window.addEventListener("paste", qPasteImage)` L7881 | clipboard image | none | client | gated on the view being open | none | toast |
| 11 | Price-image editor modal | `piUpload(this)` L1891 / L7897 | image/*, single | none (canvas) | client | none | silent | canvas swap |
| 12 | GAASH mail › 📎 attach to the next sequence email | `gmAttachPick()` L11986 (dynamic input, **no `accept`**) | any type, first 6 | `POST /api/gaash/thread` action `attach` L3738 | `gaash_mail` b64 helper | b64 decode, bad entries skipped | none | toast |
| 13 | GAASH mail › 📎 attach to this message | dynamic input L12012 | any type, first 4 (a different cap) | rides with send | same | none | none | chips |
| 14 | GAASH mail › document library upload | `gmIdUpload()` L13252 → `gmIdUploadForm` L13258 (`.pdf,image/jpeg,image/png`) | pdf / jpeg / png, **15 MB** | `POST /api/gaash/ids` L3494 | `gaash_mail.ids_add` (`gaash_mail.py:570`), folders id / declaration / certificate | `admin_actions`, size, `_safe_name`, PDF-only library | toast with server error | toast, auto-select |
| 15 | GAASH mail › docs wizard in-slot library upload | dynamic input L14160 (`.pdf,image/jpeg,image/png`) | pdf / jpeg / png | `/api/gaash/ids` | same | same | toast | toast |
| 16 | Settings › ⬇ Download full backup | `<a class="minibtn" href="/api/backup">` (Settings toolbar) | zip out | `GET /api/backup` L4785 | sqlite `.backup()` + files | admin or worker token | browser | download |

### Bulk paste and bulk-add surfaces

| Surface | Control | Tokeniser / rule | Endpoint | Notes |
|---|---|---|---|---|
| 🔎 Bulk search | `#bsInput` textarea; `bsRun()` L11436 | `bsTokens()` L11392: split on whitespace/`,;،`, keep ≥4 chars, **cap 300** | none — client-side over the already-loaded `POS` + `LX` | results render as LXT `bs`; also driven from the Workflows ⚡ chip |
| 📧 GAASH enroll wizard → paste extra GWDs | `#gmNewPaste`; `gmNewOpen()` L12196 | `gmNewGwds()` L13135: **strict `^GWD\d+$`, no cap** | `POST /api/gaash/start` L3624 | the same list pasted here and in Bulk search yields different sets |
| 📮 Register at Gerizim (Purchases L1219 and Leluxe toolbars) | `gzBulkOpen()` L9949, select-all, `gzRegister` | selection, not paste | local Mac tool `127.0.0.1:8787`, mirrored to `POST /api/gerizim/registered` L1881 | native `confirm()`; fails with a toast on the server |
| 🪪 Upload docs to GAASH (package ⋯) | `gaashUploadOpen` L9640/9671 → `gaashUploadGo` L9680 | checkbox doc-type picker | none — opens the GAASH upload page in a new tab | no receipt; the app stamps `gaash_docs_at` optimistically |
| /order intake (customer) | 15 single-line URL inputs (`templates/order_request.html`) | `^https?://`, server `normalize.parse_items` | `POST /api/quote/lead` L1168 | inline errors — the only inline validation in the product besides `/id` |
| /id/<token> (customer) | `templates/submit_id.html` L47 (`image/jpeg,image/png,.pdf`) | 15 MB client + server | `POST /api/id/submit` L5265 | single-use token, photo required, token kept on failure; inline error box |

*Not on `main`:* an Amazon-summary paste parser (`landed.py`, `/api/purchase/parse_summary`) exists only in the owner's uncommitted working tree and is not audited here.

### Sync / import-from-source buttons (no file)

| Trigger | Endpoint | Module |
|---|---|---|
| Purchases ⚙ Tools ▾ → 🔎 Check all shipping (`poCheckAllShipping` L8511) | `POST /api/purchases/refresh_tracking` L1889 | `tracking.py` (GAASH + Gerizim) |
| Purchases ⚙ Tools ▾ → 💰 Estimate all costs (`poEstimateAll` L8625) | `POST /api/purchase/estimate_cost` L2024 | `estimate.py` / SerpAPI (metered) |
| Purchases ⚙ Tools ▾ → ↙ Import from ClickUp (`importClickup` L8318) | `POST /api/purchase/import_clickup` L1955 | `clickup_import.sync()` — rebuilds packages by shared tracking number |
| Customers → ↻ Sync from orders (`syncCustomers` L11095) | `POST /api/customers/sync` L1441 | `customers.py` |
| Leads → ↻ Sync from Meta | `GET /api/meta/leads` L1807 (**sync happens on GET**) | `meta_leads.sync()` |
| Leluxe ⚙ Tools ▾ — 13 items: refresh tracking, fetch images, sync from AZ (2), sync with review, auto-pull, diagnose, last changes, push history, organise all, review conflicts, remove duplicates, discover schema, activity log | `/api/leluxe/*` (migrate, import, discover, auto_pull, dedupe, az2_organize…) | `leluxe.py` |
| GAASH mail → 📥 Check replies (`gmCheck` L12128) · 📦 Check tracking | `POST /api/gaash/check` L3832 | `gaash_mail.check_replies()` IMAP |
| Catalog (hidden) → 🔎 Fetch & add (`catAdd` L3072) · item editor ⤓ Get photo | `POST /api/catalog` L1013 · `GET /api/import` L961 | `product_import.import_product` (`product_import.py:86`) |
| Purchases package ⋯ → 🤖 Get tracking (auto) | AZ bridge (Mac only, Multilogin) | `az.py` |
| ↗ Sync ClickUp (sidebar) | ClickUp sync | `clickup.py` |

### Endpoints with no UI caller (32 of 252; full list in `inventory.md`)

Headless by design: `/api/worker/*` (queue, result, packages, docs_sweep, account_rd, board_pull, tracking, seed), `/webhook/*`, `/api/gaash/px/<token>.gif`, `/api/customer/google/callback`, `/healthz`, `/api/health/db`, `/api/quarantined*`. **Real gaps:** `POST /api/restore` L4916 and `POST /api/gaash/accounts/restore` L4985 (backup is one click, restore is a `curl`); `POST /api/gaash/rules/run` L3985 (rules can be previewed and matched from the UI but only run headlessly). **Dead:** `/api/automatch`, `/api/track_gwd`, `/api/estimate`, `/api/bridge/draft`, the five customer-login endpoints (`wa_login/start`+`poll`, `wa_verify/start`, `email/login/start`+`verify`; `otp/request`+`verify` are test-only).

### Shared parsing logic that must survive behind a common interface

- `normalize.parse_items(links, expand=)` (`normalize.py:239`) — the single link→item parser behind **five** intakes (`/api/order`, `/api/quote/request`, `/api/quote/lead`, `/api/catalog/checkout`, `/api/order/intake`), with `clean_amazon_url`, `detect_retailer`, `extract_asin`; `normalize.collect_phones` (`:81`) on every intake.
- `product_import.import_product` (`product_import.py:86`) — Amazon via SerpAPI, other retailers via OG scrape; behind `/api/import` and `/api/catalog`.
- `gaash_mail.ids_add` (`gaash_mail.py:570`) — the document library (PDF-only, sanitised names, id collision retry).
- `clickup_import.sync()` and `tracking.refresh` — the two "connected source" imports.
- The **base64 image write** — five copies (`app.py` L2357, L2434, L2495, L4062, L5265) to become one helper with one size cap, one sanitiser, one error shape.

Forks to close in Phase 6: two GWD tokenisers with different rules; attachment caps 6 vs 4 vs 6 (library picker); three client resize policies (1400 px, 760 px, none); size caps on 3 of 6 image routes; error strings dropped by three toasts.

---

## 5.4 Action inventory

Method: static toolbar controls come from each view container's markup (`inventory.md`, "View containers"); row, nested and bulk actions were read from the render functions. Classes: **P** primary · **S** visible secondary · **O** overflow (inside a ⋯ / ▾ menu) · **R** row quick action · **N** nested / expanded-row action · **B** bulk (needs a selection) · **X** remove-candidate. Brief rule: one P, at most three visible S per page; at most three R per row plus a ⋯.

| View | Header / toolbar (visible) | P | S | O | Row (R) | Nested (N) | B | Flags |
|---|---|---|---|---|---|---|---|---|
| Brain | none of its own; top-bar `＋ Add order`, `↻ Refresh` show here | 0 | 2 | 0 | every row deep-links (1) | — | 0 | no page action (acceptable for T4) |
| **Purchases** | `⚙ Tools ▾` (→ 🔎 Check all shipping · 💰 Estimate all costs · ↙ Import from ClickUp), `📮 Register at Gerizim`, `⊟ Collapse all`, `⊞ Expand all`, `+ New order`; second strip: 4–5 view segments, search, 4 quick-filter tabs, filter builder `＋ Add filter`, `⊕` Fields | 1 (`+ New order`, creates a PO) | **4** | 3 + Fields panel | order card: caret, id-toggle, 📋 copy Amazon #, ⋯ (✏️ Edit · ⤢ Open detail · 📷 Screenshot · 👁 Preview ClickUp · ↗ Send to ClickUp · 💰 Estimate all packages · 🗑 Delete) = **4 + 7** | package row: caret, row-click popup, 📋 copy GWD, due-date edit, RD-number edit, status select, Gerizim cell, docs pill, ⋯ (✏️ Edit · 📱 Notify · 🔎 Check shipping · 🪪 Upload docs · 🤖 Get tracking · 💰 Estimate · ＋ Add product · 🗑 Delete) = **7 + 8**; product row ↗ ✏️ = 2; package modal 7 footer buttons + ship-to edit + status picker + photo delete + ⌘V | **0** (no row checkboxes; "all" commands only) | >3 visible S; 15 controls on a nested row; the page's only P is named "order" but creates a purchase order; `Register at Gerizim` is Mac-only and also appears on Leluxe |
| **To order** | need-to-order strip: `🛒 Move N to cart` (hidden until ticked), `🔗 Open all products`, `↻ Refresh`, 5 filter chips (all · pending · incart · ordered · deleted); quote panel: collapse, `عربي`/`EN`, `＋ item`, `Build quote`, total + markup inputs | 2 (`Build quote`, `Move to cart`) | 2 | 3 (⋯: 💲 Get missing prices · 📷 Get missing photos · 🪪 Request ID) | pending row: checkbox, expand, 💲 quote, 🔗 quote link, ✏️ edit, 🗑 delete, ⋯ = **up to 9** (+ city/address inline edits); incart row ↩ = 1; deleted row ♻️ ✕ = 2 | item row: link + 🔗 Amazon = 2; quote row: 🧾 proof, ✏️ price image, 🖼 image, ↓🖼 fetch, ✕ = 5 | 1 (`Move N to cart`) | two primaries (quote tool vs queue); 9 icons on a row; a second language system on one panel |
| In cart | `↻ Refresh`; cart-cost input in the KPI card | 0 | 1 | 0 | `✅ Ordered`, `↩` remove = 2 | — | 0 | no page P; verb "Ordered" vs status `ORDERED` (consistent) |
| **Package prep** | `↻ Refresh` | 0 | 1 | 0 | card: caret, 📱 972, 📱 970, 📋 copy message, 📦 status link = 5 (+ 2 inline location edits); review card: caret, 📱 972, 📱 970, 📋, `✓ تم` = 5 | package row: OTL copy, status picker = 2; item row: link = 1 | 0 | no page P; the job (mark ready / sent) lives in a nested status picker |
| Orders | select-all, search, status filter, batch filter; top-bar `＋ Add order`, `↻ Refresh` | 1 (`＋ Add order`) | 1 | 0 | checkbox, 💲 quote / ✎ re-quote, `＋₪` deposit, profile-box input, Amazon-# input, status select, city + address inline, 💬 message select (6 templates), 📦 notify, item links ≈ **10** | — (no expansion) | 2 (`🗑 Delete selected` via native confirm, `Clear`) | ≈10 controls per row, three of them inline inputs |
| Customers | search, `🪪 View IDs`, `+ Add customer`, `↻ Sync from orders`; add-form `Cancel` / `Save customer` | 1 (`+ Add customer`) | 2 | 0 | row → profile, ★ VIP toggle, city inline edit = 3 | profile panel: ✎ edit, ID-number edit, gallery `＋ Upload ID`, delete = 4 | 0 | conformant |
| Leads | `↻ Sync from Meta`; 3 emoji-only source tabs (💬 📷 📝) + status tabs | 0 | 1 | 0 | status select, note input, `➕ order`, WhatsApp link = 4 | — | 0 | emoji-only tabs; no page P |
| Deposits | `↻ Refresh`, `💾 Record` + an 8-field entry form + filter input | 1 (`💾 Record`) | 1 | 0 | 🗑 delete = 1 | — | 0 | the form is the page header |
| Bulk search | `🔎 Search`, `Clear` + textarea | 1 | 1 | 0 | PO chip jump, `📧` enroll = 2 | — | 0 | conformant; becomes a filter mode |
| **GAASH mail** | `📥 Check replies`, `📦 Check tracking`, `🕐 Changes`, one unlabeled button, `⚙ Accounts & templates`, `📧 Enroll packages`; 8 tabs | 1 (`📧 Enroll packages`) | **5** | 0 | conversation row: open, 👤/⚠ name tag, state pill; workflow row: on/off, ✎, ⧉, ▸ expand, enrolled cell; docs row: pill link, wizard; readiness row: inline identity edits | thread: 📎 attach next, 📎 attach this, send, set name, approve / dismiss, task done, 🪪 upload; builder: ＋ step, delay chips, window editor | 1 (docs tab bulk re-check; per-workflow approve-all / dismiss-all) | >3 visible S; 116 + 64 `onclick` handlers in its JS; setup (sequences, templates, `dry_run`) shares tabs with daily work |
| Flags | add-inbox form (email, app password, profile), history filter | 1 (add inbox) | 0 | 0 | ⚠ / 🔑 re-add chip = 1 | history rows = 0 | 0 | conformant; belongs to Needs attention + Settings |
| P&L | `All time`, `7 days`, `30 days`, `Apply`, `↻ Refresh sources`, `Day`, `Month`, two date inputs, source chips | 0 | **7** | 0 | drill-down click = 1 | — | 0 | 7 visible controls, all filters |
| Activity | `↻ Refresh`, filter chips | 0 | 1 | 0 | 0 | — | 0 | conformant |
| Settings | **20 buttons + 1 link** across 13 panels: 12 Save / Add rows, `🔍 Detect chat id`, `📨 Send test message`, `▶ Run alerts now`, `Send test code/email/SMS`, `⬇ Download full backup`; 38 inputs, 3 selects | 13 (one Save per panel) | 8 | 0 | table rows: `＋ Add status/stage/row`, delete = per table | — | 0 | thirteen primaries on one page; test tools beside settings |
| Team | `＋ Create account` + 3 inputs + role select | 1 | 0 | 0 | reset password, role change = 2 | — | 0 | no remove / deactivate (F-016) |
| Trash | `Empty trash` | 0 | 1 (destructive) | 0 | Restore, purge = 2 | — | 0 | a destructive action as the only header button, native confirm |
| **Leluxe** | `⚙ Tools ▾` (**13** items: 🚚 Refresh tracking · 🖼 Fetch images · 🔄 Sync from AZ (2) · 🛡 Sync with review · ⏱ auto-pull · 🩺 Diagnose · 👁 Last changes · ⤴ Push history · 📦⤴ Organize ALL · ⚖ Review conflicts · 🧹 Remove duplicates · ⟳ Discover schema · 🕑 Activity log), `🎯 Goal settings`, `📮 Register at Gerizim`, `＋ New order`, `↻ Refresh`, 5 view segments, `⊞` / `⊟`, search, filter builder, group-by | 1 (`＋ New order`) | **5** | 13 | caret, status select, sync dot / conflict badge, ✎ edit, ⋯ (move · delete · push · organise), thumbs | package rows: status select (subtask rows), GWD copy, docs / deadline / mail pills | push-to-AZ selection (guarded `az2_*`) | >3 visible S; a 13-item menu mixing daily sync with rare schema work |
| Goals | `⚙ Edit goals`, `↻ Refresh` | 0 | 2 | 0 | inline target / label edits | — | 0 | conformant |
| Tatabu console | Overview 0; Brokers `＋ Create broker` + 4 inputs + tier select; Plans / Usage / Activity 0 | 1 | 0 | 0 | broker row → profile; profile: feature toggles, quotas, support view | — | 0 | separate shell mode (D11) |
| Catalog (hidden) | `↻ Refresh`, `🔎 Fetch & add` + 4 inputs | 1 | 1 | 0 | onchange inputs, active toggle, delete | — | 0 | X |
| Picking (hidden) | `🖨 Print` | 1 | 0 | 0 | 0 | — | 0 | X |

**Feedback channels (whole app):** `toast()` 379 calls — the near-universal feedback for saves, failures, long-running jobs and destructive actions alike; native `confirm()` 47, `prompt()` 15 (still collecting real data: a Leluxe migration date, a missing-docs note, a customer ID number), `alert()` 2. Only the customer pages (`/order`, `/id`) show inline field errors. Several toasts discard the server's error string (`toast("Upload failed")`).

**Pages breaking the hierarchy rule:** Purchases (4 visible secondaries), GAASH mail (5), Leluxe (5), P&L (7 filter controls, no primary), Settings (13 primaries), To order (2 primaries). **Rows breaking the three-quick-actions rule:** To order (9), Orders (≈10), Purchases package row (7 direct + 8 in ⋯).

**Remove-candidates (X):** Catalog and Picking (whole views), the nav-level `↗ Sync ClickUp` action, the second `Register at Gerizim` entry, the three "Send test …" buttons living next to settings (move to a Diagnostics section), the P&L `Day / Month` toggles as buttons (become a `Tabs`).

---

## 5.5 Status and enum inventory

Every status value per entity, where it is defined, and the label + colour used wherever it is displayed. Owner decision: labels (including the ClickUp spellings) stay; what the registry must fix is *colour, tone, casing, duplicated palettes and red-for-empty*.

### A. Customer order — `order.status`

Defined `store.py:19`: `REQUESTED · QUOTED · PAID · IN_CART · ORDERED · SHIPPED · ARRIVED · DELIVERED · COLLECTED · CANCELLED`; groupings `PREORDER` (`:79`), `CART` (`:85`), `PLACED` (`:88`); served as `DATA.statuses` for the editable dropdown.

| Value | Staff `STATUS_COLOR` L2042 (ink / bg) | Customer portal `account.html` `STAT` (ar / en) | Brain `STATUS_LABEL` `brain.py:47` |
|---|---|---|---|
| REQUESTED | grey `#6b7280/#f3f4f6` | قيد المراجعة / Reviewing (amber) | "not ordered yet" |
| QUOTED | cyan `#0891b2/#e0f2fe` | بانتظار التأكيد / Awaiting confirm | "quoted" |
| **PAID** | **absent → fallback `#555/#eee`** | مدفوع / Paid (indigo) | — |
| IN_CART | amber `#b45309/#fef3c7` | قيد التجهيز / Being prepared | — |
| ORDERED | indigo `#6366f1/#eef2ff` | تم الطلب / Ordered | "ordered — awaiting shipment" |
| SHIPPED | sky `#0ea5e9/#e0f2fe` | تم الشحن / Shipped | "in transit" |
| ARRIVED | amber `#d97706/#fff7ed` | وصلت بلدك / Arrived (purple) | "arrived — waiting collection" |
| DELIVERED | **brand orange** `#ff5a1f/#fff1ea` | تم التسليم / Delivered (green) | — |
| COLLECTED | green `#16a34a/#ecfdf5` | مكتمل / Completed (green) | — |
| CANCELLED | red `#dc2626/#fef2f2` | ملغى / Cancelled | — |

`statusPill()` L2443 prints the **raw uppercase enum** as the label (never through `T()`); `statusSelect()` L2500 tints a native `<select>`. `OPEN_STATUSES` is defined twice (`brain.py:42`, `report.py:22`) and **both omit PAID and IN_CART**, so a paid-but-unplaced order is invisible to "open work".

### B. Order items and PO lines

Customer-order items have no status. PO lines: `purchases.py:31` `ITEM_EXCEPTIONS = CANCELLED · REFUNDED · OUT_OF_STOCK · RETURNED`, empty = inherit the package. Labels `EXC_LABEL` L2050 (bilingual); rendered three ways — full bilingual (dropdown), `.split(' · ')[0]` → **always English** regardless of the toggle (L9220, L10842), and the exception-vs-inherit branch (L10503). Colours from `STATUS_COLOR` (REFUNDED `#b42318`, OUT_OF_STOCK `#92400e`, RETURNED `#6d28d9`).

### C. Purchase order (PO level)

A PO carries `"status"` defaulting to `PLACED` (`purchases.py:207`, `:362`) that no view displays. The board's PO status is **derived**: `PO_STAGE` L9538 (`ORDERED → SHIPPED → ARRIVED → DELIVERED → COLLECTED`) and `poRollupStatus()` L9553 = the least-advanced package. Quick-filter tabs: `all · late · soon (≤7 d) · notrk`; `poNeedsAction()` L9570 auto-expands late POs. Days-late pill `dueChip()` L7215: `yesterday` / `Nd late` (bold red on `#fef2f2`), `today` (amber), `in Nd` (amber ≤3, muted ≤14), raw date beyond — English only. A **missing tracking number is painted red as an error** (`.trk-cell:placeholder-shown` L123, comment: "MISSING tracking # lights up red"), while the same absence is muted `no GAASH tracking` text in the Leluxe/Purchases rows (L5322, L5363, L5608, L9184, L10475).

### D. Package `otlobly_status` — the ClickUp vocabulary

`CU_STATUSES` L10259: **31** hard-coded `[status, hex]` pairs copied from ClickUp; `CU_COLOR` L10271. The live list (`config.json → leluxe.schema.statuses`) has **35**. Drift (F-004): present in ClickUp but not in the picker — `parcelto destination`, `required customer id`, `arrived at destination`, `cleared customs`, `az id`, `az id sub`, `package`; in the picker but not in ClickUp — `cleared`, `id request`, `ariived at destnation`. An **unset** `otlobly_status` renders as the real status `oredered` (`CU_STATUSES[0][0]` at L10276 and L8203). Set from three pickers (`pkgCuSelect` L10275, Package prep `ppStatusPicker`, the Settings map editor) — the Settings editor lists the hard-coded 31, so seven real statuses cannot be mapped for customers and three non-existent ones can.

Notable colours: `delivered` **brown** `#a18072`; `ariived at destnation` **red** `#e5484d` (a good milestone); `cancelled` and `not correct address` share the red family with `id request`; `recieved rd` `#0ff17e` neon green vs `recieved no rd` `#1090e0` blue. Typos kept by decision: `oredered`, `ariived at destnation`, `delievered rd/no rd`, `not recieved rd/no rd`, `recieved rd/no rd`.

**Employee remap** (`settings.py:60` `DEFAULT_EMPLOYEE_STATUS_MAP`, applied by `cuLabel()` L2866 for staff without `admin_actions`): 20 statuses → bilingual friendly labels; twins are told apart only by a `✓` suffix (`received` vs `received ✓`) and, for `not recieved no rd` and `refund request`, by a **trailing space** in the label (`"not arrived "`, `"in progress "`), so two different statuses look identical.

**Customer remap** (`tracking.py:106` `DEFAULT_OTLOBLY_MAP`): only 6 of the 31 map (`cleared` → تم التخليص الجمركي; `recieved rd/no rd` → استلمتها اطلبلي; `sent rd/no rd` → في الطريق إلى {name}; `complete` → تم التسليم); everything else falls through to the carrier label.

**"Journey is over" sets — four that disagree (F-003):** `alerts.py:29` `STOP_DEFAULT` (9 values: rd · delivered · delievered rd/no rd · recieved rd/no rd · sent rd/no rd · complete); `pkgprep.py:30–31` `RECEIVED` = {recieved rd, recieved no rd}, `DISPATCHED` = {sent rd, sent no rd, complete}; `gaash_mail.py:2175` `_TERMINAL = (cleared, delivered)`; `lxIsDone()` L3334 = `/deliver|complete|reci?eved/i` — which also matches **`not recieved rd`** (F-008), so a parcel that has *not* arrived is treated as done (due chip suppressed, never counted late). `rd` is a *stop* status for alerts but an *in-progress* status for the Leluxe goal (`leluxe_goal.py:41`). `account_rd.py:32–43` adds a fifth reading of the same words (RD vs CLEAN accounts).

### E. Derived package status

`pkgStatus()` L9546 maps the live carrier bucket into the *order* enum: `delivered → DELIVERED`, `arrived | customs | cleared → ARRIVED`, `transit → SHIPPED`, else `ORDERED` — a package **in customs displays "ARRIVED"**, the same word that on an order means "in your country".

### F. GAASH parcel / tracking

- Raw code → staff label + bucket: `tracking.py:44` `CODE_LABEL` (`VM` on the way / transit · `K3` arrived · `CD` customs · `K2` cleared · `AJ` last mile / transit · `D1` delivered). `BUCKET_RANK` `:57` transit 1 → customs 2 → cleared 3 → arrived 4 → delivered 5.
- Customer map `tracking.py:64` `DEFAULT_STATUS_MAP` (identical to the saved config): `K3` row uses bucket **customs** while `CODE_LABEL["K3"]` says **arrived** — the two maps disagree on the same code. GAASH "Delivered" → في الطريق إلى اطلبلي (bucket arrived) by design. Fallback `:98` قيد الشحن.
- ClickUp "GASH STATUS" custom field mirror `GASH_STATUS` L9606 / `gashLookup()` L9620: 12 triples, labels English-only, typos kept (`ARIIVED Destination`). Live options (10): `STILL NOT ARRIVED · ARIIVED Destination · DOCUMENTS SENT · CLEARED GASH · BRACHA DELIVERED · Sent but still diidn't clear · GERZIM DELIVERED · " customer ID" (leading space) · Picked up by Gerizim · MOC - Palestinian authority`. The client invents two rows (Ministry of Transportation / Communications) that never colour-match, leaves four options unhandled, and returns `customer ID` without the leading space, so `lxCfPill()` L3304 (exact-match) falls back to grey.
- **Five palettes for the same five buckets:**

| bucket | `GAASH_HEX` L9591 | `PO_GAASH_BUCKETS` L8884 | `BUCKET_HEX` L8180 (Settings) | `lxGashPill.COL` L3341 | `track.html` |
|---|---|---|---|---|---|
| transit | `#4466ff` | `#4466ff` "IN TRANSIT" | `#3b82f6` | `#2563eb` | `#3b82f6` |
| arrived | `#e5484d` (red) | `#e5484d` "ARRIVED" | `#0284c7` | — | `#3b82f6` |
| customs | `#f76808` | `#f76808` "CUSTOMS" | `#d97706` | `#b45309` | `#d97706` |
| cleared | `#0f9d9f` | `#0f9d9f` "CLEARED GASH" | `#16a34a` | `#0891b2` | `#16a34a` |
| delivered | — (falls to green) | `#0a7d33` "DELIVERED" | `#15803d` | `#16a34a` | `#16a34a` |

plus three label sets (`gaashBucketPill.SHORT` sentence case, `PO_GAASH_BUCKETS` uppercase, `tracking.py` prose). Package pill `pkgGaashPill()` L10096: no tracking → grey "Awaiting tracking"; delivered → green "Delivered" (L8498); ClickUp match → `solidPill`; else `gaashBucketPill()` L9596.

### G. Gerizim last mile

`gerizim.py:29` `STATUS_LABEL`: new/office/sorted → "At Gerizim office" (office) · sms/reviewed → "SMS sent — awaiting pickup" · awaiting_pickup → pickup · out_for_delivery → out · delivered/complete → "GERZIM DELIVERED" (typo kept from ClickUp); sentinel `NOT_FOUND` `:24`. Arabic `GZ_AR` L3349 (spelled جرزيم; the column header elsewhere says جيرزيم); colours `GZ_HEX` L10109 — `delivered` is **lavender** `#b6b6ff`, unlike every other "delivered". The code comment near L10119 records the split on purpose: Leluxe shows Arabic, Purchases keeps English, same data.

### H. Docs state (customs documents)

Produced by `tracking.py:339` `docs_status()`: `action` (GAASH lists document links) · `info` · `cleared` · `stopped` · `plain` · `noanswer` (404 / no data) · `None` (lookup failed, PR #142). Sort order `gaash_mail.py:4680` `_DOCS_ORDER`. Rendered with **two glyph sets and two wordings**: Purchases `pkgDocsPill()` L10033 (📄 upload docs · ⛔ clearance stopped · 📄 in customs · ⚠ docs not received) vs GAASH mail Docs tab `gmDocsStatePill()` L13767 (🟡 upload asked · ⛔ stopped · 🚫 no answer · ❔ unchecked · 🚚 not arrived · 🔵 in customs). Deadline ramp: `<0 d` red · ≤3 red · ≤7 orange · else grey.

### I. Payments

`db.py:107–121`: `kind ∈ deposit · refund · collect`, `currency ∈ ILS · USD`, `fx_rate` and `amount_usd` frozen at record time. Labels differ between the entry dropdown (عربون · Deposit / تحصيل · Collected / استرجاع · Refund) and the ledger row (عربون / ✅ collect / ↩️ refund — the first has no English side). Order badges: `✅ عربون` green tone; payment plans `prepaid` 💰 green / `zero_risk` 🛡️ amber / website order without a plan ⏳.

### J. Meta leads

`db.py:97`: `source ∈ messenger · instagram · leadform`, `status ∈ new · contacted · converted · lost`. `ML_STATUS` L6763 `{ar,en}` labels, used only in the `<select>`; **no colour anywhere** — the one entity with zero visual status encoding; source tabs are emoji-only (💬 📷 📝).

### K. Customers

`vip` 0/1 (★); ID document = presence only (🪪 vs `—`, `title="ID on file"`); ID number = string. The ClickUp side has its own ID enum (`AZ ID VER`: `SUHA ISRAEL DRIVE`, `NEEDS ID`, `NO ID VER`, `qais palestaian pass`…).

### L. Leluxe (ClickUp mirror)

`leluxe.py:66–71`: kinds `order | parent` (top), `package`, `item`; the SQL comment still says `parent | item` (`db.py:153`). Status = the live ClickUp string; `lxStatusPill()` L3253 tints from `LX.schema.statuses` and prints the raw string (typos included); unknown → grey. Custom-field pills `lxCfPill()` L3304 from `LX.schema.fields[...].options` — option sets behaving as enums: GASH STATUS (10), RD STATUS (`SENT REQUEST ` with trailing space, RD DONE, NO RD, STILL NOT SENT), check in days, States (BAN / GOOD / N / NO GMAIL / READY FOR AZ), opened box, Quantity, NAME ON PACKAGEE (typo in the field name), CARD TYPE, wallet/watch, who paid. Sync state `db.py:159` `synced · dirty · pushing · error` + a UI-only `conflict` (`lxDot()` L3312). Rollups: `mixed` grey pill when children disagree; `lxGashRollup` red ⚠ مختلط.

### M. GAASH mail thread states

`GM_STATE` L11542: `active` #0091ff · `waiting_reply` #12a594 labelled **"رد وصل · replied"** (the state means *waiting for a reply*) · `missing_docs` #e5484d · `paused` #8d8d8d · `cleared` #30a46c · `goal_met` #30a46c · `waiting_task` #7c4dff · `proposed` #ffc53d · `exhausted` #f76808 · `done` #8d8d8d; `GM_DONE_STATES` L11770 = goal_met · done · cleared.

### N. Flags, Package prep, Trash, Team

Flags: `state ∈ open · done` (`flag_machine.py`), closed by `DONE_WORDS` `:62` (done · تم · خلص · خلصت · تمام); count only, no pill. Package prep buckets `ready` (green ✅ جاهزين للشحن) / `waiting` (amber ⏳ ناقصهم قطع) / `reviews`; its empty state instructs staff in raw ClickUp typo vocabulary («recieved rd / no rd»). Trash `KIND_LABELS` `trash.py:26` (purchase_order · po_package · po_item · order · customer). Roles `admin · sales · fulfillment` (`ROLE_LABEL` L6823); broker tiers `starter · growth · pro` defined **twice** (`TIER_LABEL` L2251, `BK_TIER_LABEL` L7016).

### O. Inconsistency catalogue (what the registry must resolve)

1. **Same value, different colour/label per surface:** `delivered` — brown (`CU_STATUSES`), green (`PO_GAASH_BUCKETS`, `lxGashPill`, `account.html`), lavender (`GZ_HEX`), brand orange (`STATUS_COLOR.DELIVERED`); `arrived` — red in three maps, amber in `STATUS_COLOR`, purple in the portal; five bucket palettes (table F); payment kinds worded two ways; Gerizim spelled two ways; docs state with two glyph sets.
2. **Casing:** order/PO-item statuses `SCREAMING_SNAKE` rendered raw; package statuses lowercase prose; GAASH field values shouty mixed case — all three side by side on one Purchases row. (Rendering casing is a `Badge` concern; stored values do not change.)
3. **Red for non-errors / empty values:** empty tracking input (L123); unknown exception fallback `#b42318`; the good milestone `arrived` red in three maps; `PAID` with no colour at all.
4. **Missing / dead:** `PAID` absent from `STATUS_COLOR` and from both `OPEN_STATUSES`; PO `status` "PLACED" never shown; unset `otlobly_status` shown as `oredered`; Settings map editor built from the stale 31.
5. **Logic bugs riding on labels:** `lxIsDone` regex (F-008); `K3` bucket disagreement; `waiting_reply` labelled "replied"; `Ministry …` rows that exist only client-side; the leading-space ` customer ID` option.
6. **Registries to collapse into `status.js`:** 13 `const` objects carrying hex values (`STATUS_COLOR`, `COL`, `GL_COLORS`, `BUCKET_HEX`, `PO_GAASH_BUCKETS`, `CF_PALETTE`, `TONE`, `GAASH_HEX`, `GASH_STATUS`, `GZ_HEX`, `CU_STATUSES`, `GM_STATE`, forecast `HEX`) plus the label-only maps (`EXC_LABEL`, `GZ_AR`, `ML_STATUS`, `GAASH_DOCS`, `TIER_LABEL` ×2, `ROLE_LABEL`, `ACT_ICON`, `TRASH_ICON`) and the Python-side sets above — the registry should be generated from one source (the live ClickUp schema for package statuses, `store.STATUSES` for orders) rather than hand-copied.

---

## 5.6 Terminology glossary (draft)

Canonical English term first (sentence case), then what the code actually means, with the proof. Arabic terms are deferred (owner: English only for now); the existing Arabic halves of labels stay untouched. Labels that must change are marked **→**.

| Term | Meaning in the code | Proof | Label changes |
|---|---|---|---|
| **Order** (customer order) | One customer + 1..N items, id `OTL-####`, status from `store.STATUSES` (REQUESTED → QUOTED → PAID → IN_CART → ORDERED → SHIPPED → ARRIVED → DELIVERED → COLLECTED, + CANCELLED). The demand side. | `store.py:19`, `:79–88` groupings, `db.py` orders table | Orders page keeps the word; nothing else may call a PO an "order" |
| **Purchase order (PO)** | One Amazon checkout (Amazon order `113-…`) bundling items for many customers, id `PO-####`, money as one `total_usd` lump. The supply side. | `purchases.py` header docstring, `:31` item exceptions, `new_po` | **→** Purchases page title "Purchase orders"; Σ footer "N purchase orders"; `+ New order` → "New purchase order" (D7) |
| **Item / line** | A product row. On an order: asin/title/image/qty/prices, no status of its own. On a PO: nested inside a package, auto-matched to an order by ASIN (`asin_index` / `match_item`), inherits the package status; its own status exists only as an exception (CANCELLED, REFUNDED, OUT_OF_STOCK, RETURNED). | `store.py` item shape, `purchases.py:60/74`, `:31` | "Products" (Purchases sub-view) and "items" (counts) are the same thing **→** one word: *items* |
| **Package** | A PO split by arrival: one package = one GAASH shipment = one GWD. Also a ClickUp task *kind* on the Leluxe side (`PKG_STATUS = "package"`). | `purchases.py` package norm, `leluxe.py:71` | keep "package" for the PO child; never "parcel" for it |
| **Parcel / GWD** | The physical box as GAASH Worldwide knows it: `GWD` + 9 digits (`^GWD\d{9}$`). Same object as a package once it has a tracking number; the tracking-centric views (Bulk search, GAASH mail, Package prep) call it parcel. | `tracking.py`, `gmNewGwds` L13135 | use "parcel" only for the shipping/customs stage; the identifier is "GWD" |
| **Shipment** | The customer-facing card built per (PO, package) pair, de-duplicated by the masked tracking number, exposing only that customer's items. | `app.py` `_shipments_for` | customer-facing only |
| **Delivery** | Ambiguous by design: GAASH "Delivered" = handed to the last-mile courier, *not* the customer; only the owner-set `complete` may say "delivered to the customer". | `tracking.py:106` `DEFAULT_OTLOBLY_MAP`, `gerizim.py` header | staff UI: "GAASH delivered" vs "Delivered to customer" must never share a label |
| **Collection** | Cash-on-delivery taken (`COLLECTED`; payment kind `collect`) | `store.py:19`, `db.py:114` | — |
| **OTL number** | Two different things share the prefix: `OTL-####` = the order id; `OTL` + 6 digits (no dash) = the customer-facing masked tracking number minted per package (`gen_customer_tracking`). | `store.py`, `purchases.py:264` | **→** "Order #" vs "Customer tracking #" |
| **Customer** | `CUS-####`, matched by normalised phone (`match_key`); may carry an ID image, an ID number, VIP flag, city/address. | `customers.py`, `db.py` | — |
| **Lead** | Anyone who reached the business through Meta (Messenger DM, Instagram DM, Lead-Ads form) before an order; status new → contacted → converted → lost; converted into an order with `➕ order`. | `meta_leads.py`, `db.py:97` | — |
| **Quote** | The price told to the customer (Amazon checkout total × markup); the tool at the top of To order writes `QUOTED` + `quoted_at`. | `quoteOrder`, `store.py` | — |
| **To order** | The bulk-buying work-list: orders in REQUESTED / QUOTED / PAID as product rows. | `store.py:79` `need_order()` | keep as a stage name inside Fulfillment |
| **In cart** | `IN_CART` only — deliberately its own group so staged Amazon carts leave the queue and get their own P&L line. | `store.py:85`, comment above it | keep |
| **Deposit (عربون)** vs **payment** vs **COD** | Deposit = money up front, entered in ₪, stored in $ with a frozen rate; payment = any ledger row (deposit / collect / refund); COD = the `collect` kind. | `db.py:107–121`, `app.py` `/api/payments` | ledger and dropdown must use the same three words (§5.5 A9) |
| **Profile (B19, E-B15, S-B32)** | The Amazon **buying account** the PO was placed under — 1:1 with a Multilogin browser profile of the same name (`az.py:12` "Boxes map to profiles by exact NAME"). Purchases stores it as free-text `profile_box` (datalist seeded `PO_BOXES` L8316: B19 B22 B27 B31 B85 + every value already used); Leluxe stores the ClickUp `NAME` dropdown (140 options: `B*`, `E-B*`, later `S-B*` — later account generations; no code parses the prefix). Colour = the ClickUp option hex ("the colour IS how the owner reads this column"); it degrades to plain text when Leluxe has not loaded. `account_rd.py` turns the RD statuses into a per-account "spent / clean" verdict. | `az.py:12`, `poProfileCell` L8575, `lxProfileChip` L4526, `account_rd.py:32–43` | **→** column label "Buying account" with the code as value and a tooltip "Amazon buying account / Multilogin profile" |
| **RD number** | The Amazon refund/dispute number typed per package once Amazon issues it (green pill, own column) — a *different* concept from the profile. | `pkgRdCell` / `pkgRdEdit` | keep "RD number"; tooltip "Amazon refund/dispute #" |
| **Brain** | `brain.py`: a per-tenant rules engine over orders, leads and quota → urgent · due · forgotten · money · last-7-days; pure data, built to feed a Telegram digest; rendered as the landing page. Not an AI assistant. | `brain.py` header, `renderBrain` | **→** "Overview" (D5) |
| **Flags** | `flag_machine.py`: read-only IMAP watch of Gmail inboxes; a new mail whose subject contains "action required" opens a flag; a dedicated Telegram bot nags until the owner replies done/تم. Not a manual flag list. | `flag_machine.py:62` `DONE_WORDS` | **→** part of "Needs attention" |
| **Bulk search** | Paste many GWDs → where each package is on both boards, status, GAASH stage, value; client-side. | `bsRun` L11436 | **→** "Tracking" (Shipping) |
| **GAASH mail** | `gaash_mail.py` (4.9k lines): automated customs-clearance email sequences — one thread per GWD, templated steps, reply tracking, plus Docs, Readiness, Forecast, Overview, Workflows, Templates, Analyze. | `gaash_mail.py`, `gmTab` L11547 | keep the name (owner uses it); "Customs" is the stage |
| **Package prep** | `pkgprep.py`: crosses orders with PO packages → READY (every live piece received) / WAITING (partial) + a review-request cohort; WhatsApp texts with USD + ILS at `fx.pkg_ils_per_usd`. | `pkgprep.py:30–37` | keep |
| **Docs / Readiness / Forecast** | Docs = per-GWD `docs_state` from GAASH's parcel-status data (action / info / cleared / stopped / plain / noanswer); Readiness = identity data needed to clear; Forecast = `forecast.py`, next status after "cleared customs" learned from the app's own tracking history on a working-day clock (weekend Fri + Sat). | `tracking.py:339`, `forecast.py` | — |
| **Leluxe** | The owner's second business line (Amazon bulk stock for a partner — watches, cards, IT), mirrored live from ClickUp list "AZ (2)": three tiers order → package → item, five board views, its own 🎯 monthly goal. Has no customer, quote, deposit or cash collection. | `leluxe.py:66–71`, `lxSetView` L3816 | workspace switcher (owner) |
| **Goals** vs **Leluxe goal** | 🏆 Goals = one campaign window summing two ClickUp lists + POs into per-category targets (`goals.py`); 🎯 Leluxe goal = the monthly order goal for Leluxe alone with a 21:00 Telegram digest (`leluxe_goal.py`). | `goals.py`, `leluxe_goal.py` | **→** "Goals" (campaign) and "Leluxe monthly goal" |
| **P&L** | Revenue − Amazon cost (COGS) − Meta ad spend = net, all USD. | `pnl.py` | — |
| **Activity** | Append-only `activity.jsonl` (created / set / added / removed / deleted / restored / uploaded / synced). | `activity.py` | — |
| **Trash** | Soft-delete with lossless restore, kept forever. | `trash.py:26` | — |
| **Team / roles** | `users.role ∈ admin · sales · fulfillment`; `sales` = a two-item console (To order, Leads). | `db.py`, `RESTRICTED_NAV` L2187 | — |
| **Catalog** | Public storefront products; nav removed 2026-07-22, code alive. | `db.py` catalog_items | X (D6) |
| **Settings** | The whitelisted business knobs (`settings.py`): markup, fx, tracking labels, employee status labels, GAASH mail, custom fields, Telegram, Meta spend, website sections, white-label. | `settings.py:1–8` | 13 panels → sections (T5) |
| **Tatabu / Platform** | The multi-tenant super-admin console (brokers, plans, usage, activity); each broker = a `businesses` row with its own purchases file. | `PLAT_VIEWS` L6906, `db.py` businesses | workspace switcher (D11) |
| **Status vocabularies** | Four families coexist on one Purchases row: order enum (`REQUESTED`…), PO-item exceptions, package `otlobly_status` (31 ClickUp values, lowercase prose, typos kept), GAASH field values (`STILL NOT ARRIVED`, `ARIIVED Destination`), Gerizim last-mile buckets. | §5.5 | labels stay (owner); casing is unified by the `Badge` component (sentence case rendering does not change stored values) |

---

## 5.7 Bilingual / RTL audit — recorded as a backlog (owner: English only for now)

What exists today, so the deferred Arabic work starts from facts rather than a re-audit:

- **No `lang`, no `dir`.** The document has no `<html>` element; `localizeStatic()` sets `data-lang` on the root (L2172) and nothing reads it. `dir="rtl"` appears 5 times, all inside the isolated quote tool (`#quoteView[dir="rtl"]` L365, L377; set at L7803 from `QLANG`). `direction:`/`unicode-bidi`: 0.
- **Three translation mechanisms.** `T()` split-literals (997 calls, ≈1,948 `"عربي · English"` strings; a label whose halves are the same script is left bilingual by design), `data-en/data-ar` (60 each, swapped by `localizeStatic`), `QSTR` (30 keys, `QLANG` default Arabic), `ML_STATUS {ar,en}`. Toggling re-renders Purchases and Leluxe only. Status pills, `relTime()`/`agoTxt()` ("just now", "3h ago"), `dueChip()` ("2d late"), `MON` month names (L9562) and `GM_DAY_LABELS` (L14758) are English-only.
- **Physical CSS.** 33 sites in the `<style>` block, 64 file-wide (inline styles) — the full list with line numbers is in `inventory.md` ("Physical CSS properties"). The ones that matter: `.pill{margin-left:6px}` L963 (every pill), the sidebar drawer `#sidebar{left:-250px}` L48–49, `#sidebar{border-right}` L25, `#sideNav button{text-align:left}` L33, `.po-actions/.pkg-actions{margin-left:auto}` L149/L166, `.col-resize{right:0}` L398, `#toast{right:18px}` L308, `th,td{text-align:left}` L278. Against that, logical properties are already the majority: `margin-inline` 38, `inset-inline` 33, `border-inline` 22, `padding-inline` 17, `text-align:start` 10 — the conversion is a short mechanical batch.
- **Bidi isolation.** `<bdi>`: 0. `dir="auto"`: 25 (free-text fields only). Mixed runs such as `GWD004705019 · فاروق البرادعي` or `$827.55 · ≈ …` are not isolated; three sites combine `dir="auto"` with a hard `text-align:left`. **This part stays in scope now** (Arabic content inside the English UI): user text in cells and headers gets `dir="auto"`/`<bdi>`, truncation follows the text's own direction (today Arabic names ellipsise at the visual left, brief §14 item 7), and numbers get `font-variant-numeric: tabular-nums`.
- **Numbers and dates.** `Intl.*`: 0. 22 `toLocaleString` + 5 `toLocaleDateString` + 2 `toLocaleTimeString` calls, **none with a pinned locale** — a browser set to Arabic renders Arabic-Indic digits next to hand-written Western ones. `#sub` prints seconds. Three functions named `money` (L2053 two decimals, L4765 Leluxe dashboard no decimals, L10612 `toFixed`), plus `money0`, `fmt` (₪ prefixed by callers), `cfNum`, `lxGm`; three relative-time formatters; `fmtDue` parses two input formats with hard-coded English months.
- **Fonts and widths.** Inter only (no Arabic glyphs — Arabic falls to the system font with different metrics); LXT column widths are pixel values tuned to the English half of each label, with header ellipsis at `.lbl`.
- **Directional glyphs.** → 179, ↩ 15, ▸ 16, ▾ 36, ⤴ 17, ↗ 27 — none would mirror.

## 5.8 Proposed information architecture (brief §6, validated)

Validation against §5.1 and the owner's usage answer: the four daily pages are Purchases, GAASH mail, To order and Package prep — three of them are pipeline stages and one is the customs stage, which matches the brief's Fulfillment / Shipping split. Nothing in §5.1 needs a group the brief lacks; the one thing the brief did not know is that **Leluxe only exists on the fulfillment and shipping side** (no customer, quote, deposit or cash collection), so the workspace switch affects Fulfillment and Shipping data and leaves Sales and Finance as Otlobly-only — recorded here once as prior art (2026-09-04 proposal), not as a counter-proposal.

```
Overview                      (D5: Brain's five sections become the home; no separate "Brain" item)

Sales
  Leads                       ← 📣 Leads (metaleads)
  Customers                   ← 👤 Customers
  Orders                      ← 🏠 Orders (+ the Add-order panel as a drawer)

Fulfillment                   one page with stage tabs, each with its count (D8), or four pages on one template
  To order → In cart → Purchase orders → Package prep
                              ← 💡 To order · 🛒 In cart · 📦 Purchases (D7 rename) · 🎁 Package prep

Shipping
  Tracking                    ← 🔎 Bulk search (paste-many becomes a filter mode) + arrivals
  GAASH mail                  ← 📧 GAASH mail; its 8 tabs become this page's tabs; Docs + Readiness feed Needs attention;
                                 sequences / templates / dry-run settings move under Settings › Clearance automation

Finance
  Deposits                    ← 💵 Deposits (+ "record deposit" as a drawer action from any order row)
  P&L                         ← 📊 P&L

Insights
  Goals                       ← 🏆 Goals (with the Leluxe 🎯 monthly goal as a toggle inside it)
  Activity                    ← 🕑 Activity (also a Timeline tab on detail pages later)

──────────────
Needs attention (badge)       ← 🚩 Flags (open action-required mails) + late packages + missing tracking numbers
                                 + docs state "action" + Brain "urgent"; one queue, one AttentionBadge vocabulary
──────────────
Workspace: Otlobly ▾          ← ⌚ Leluxe (its 5 segments become that workspace's Fulfillment/Shipping/Insights data)
                                 and 🏗 Tatabu (the platform console, D11)
Settings · Language · User    ← ⚙️ Settings (13 panels → sections), 👥 Team, 🗑 Trash, ↗ Sync ClickUp (→ Settings › Integrations,
                                 a button with run history), ع toggle (D2), the user chip (today at the sidebar foot)
```

**Old → new mapping (every current item):**

| Today (sidebar order) | New home | Change | Gate to preserve |
|---|---|---|---|
| 🧠 Brain | Overview (home) + Needs attention feed + nav badges | rename / split (D5) | none |
| 📦 Purchases | Fulfillment › Purchase orders | move + rename (D7) | none (money cells by `view_cost`) |
| 💡 To order | Fulfillment › To order | move | none; `sales` role home |
| ⌚ Leluxe | Workspace switcher → Leluxe | dissolve into a workspace | `admin_actions` + `FEAT.leluxe` |
| 🏆 Goals | Insights › Goals (+ Leluxe monthly goal toggle) | move + merge | `admin_actions` + `FEAT.leluxe` |
| 💵 Deposits | Finance › Deposits | move | `view_money` |
| 🏠 Orders | Sales › Orders | move | none |
| 🛒 In cart | Fulfillment › In cart | move | `view_money` |
| 📣 Leads | Sales › Leads | move | `view_meta_leads` + `FEAT.leads` |
| 👤 Customers | Sales › Customers | move | none |
| 🎁 Package prep | Fulfillment › Package prep | move | none |
| 🔎 Bulk search | Shipping › Tracking | move + becomes a filter mode | none |
| 📧 GAASH mail | Shipping › GAASH mail (+ Settings › Clearance automation for setup) | move + split by cadence | `edit_fulfillment` + `FEAT.leluxe` |
| 🚩 Flags | Needs attention (+ Settings › Integrations for inboxes) | dissolve | `edit_fulfillment` + `FEAT.leluxe` |
| 📊 P&L | Finance › P&L | move | `view_pnl` |
| 🕑 Activity | Insights › Activity | move | none |
| ⚙️ Settings | Settings (user menu) | move; 13 panels → sections | `admin_actions` |
| 👥 Team | Settings › Team | move | `manage_users` |
| 🏗 Tatabu | Workspace switcher → Tatabu | reframe (D11) | `admin_actions` + `business.id===1` |
| 🗑 Trash | Settings › Trash (+ "show deleted" toggle in lists) | move | none |
| ↗ Sync ClickUp | Settings › Integrations (button + run history) | move — not navigation | `admin_actions` + `FEAT.clickup` |
| (hidden) Catalog, Picking | — | removal proposed (D6) | — |
| Platform views ×5 | unchanged inside the Tatabu workspace | keep | super-admin |

Count: **5 groups, 12 navigable items** (Leads, Customers, Orders, Fulfillment [1 page, 4 tabs], Tracking, GAASH mail, Deposits, P&L, Goals, Activity, Needs attention, Overview) — 13 if Fulfillment becomes four pages (D8), still within the shell rule if Overview is dropped. Shell rules applied: named collapsible groups in flow order; one icon set at 20 px / 16 px (D3); count badges only on Needs attention, To order, In cart, Package prep, and only when non-zero; **one** active-state treatment (today `--tint-accent` marks both the active nav item and an open row — the open row keeps the tint, the nav gets its own); top bar = global search (`/`), notifications, language, user menu, in the same place on every page; breadcrumb "Fulfillment / Purchase orders" in the `PageHeader`. Role gates move with their items; `RESTRICTED_NAV.sales` becomes Sales + Fulfillment › To order.

## 5.9 Design-system plan

**Tokens** (`static/ds/tokens.css`, D1): keep the 17 existing custom properties as the neutral scale (`--bg --bg2 --bg3 --card --line --ink --ink2 --muted`), the one accent (`--accent #ff5a1f`, `--tint-accent`), and add what is missing: a 4-px spacing scale (`--s1…--s8` = 4 · 8 · 12 · 16 · 24 · 32 · 48), two radii (`--r-sm` 6–8 px inputs/badges, `--r-md` 12–16 px panels/modals), two elevations (`--e1` raised, `--e2` overlay — today one `--shadow`), a five-step type scale (`--t-xs` 11 · `--t-sm` 12.5 · `--t-md` 14 · `--t-lg` 17 · `--t-xl` 22) replacing 22 literal sizes, a z-index scale (`.bt-clip` 20, `.pop-menu` 60, banners 100, menus 99 today), motion (`--dur-fast` 120 ms, `--dur` 180 ms, `prefers-reduced-motion` guard), and semantic tones `info / success / warning / danger` each with bg / text / border at AA — replacing the 125 hex literals in CSS (70 distinct) and the 13 JS colour registries. Fonts: Inter stays for the English-only decision; the mono stack becomes one token (`--font-mono`).

**Primitives → existing code:** `Button` ← `.po-btn` family (merge `button{}`, `.minibtn`, `.iconbtn`, `.accent`); `Badge` ← `tonePill/hexPill/solidPill/statusPill` behind `status.js`; `AttentionBadge` ← `dueChip`, `pkgDocsPill`, "no tracking", `lxMailPill`, `lxDeadlinePill`; `Tag` ← `.qchip`/`.chip`; `Input/NumberInput/Select/Combobox/DatePicker/Textarea/Checkbox/Switch` ← today's bare elements + `.cu-search` combobox + the Workflows on/off switch; `Tooltip` ← 458 `title=` attributes; `DropdownMenu` ← `popMenu/popToggle`; `Tabs` ← `gmTab/poSetView/lxSetView/.chips`; `Avatar` (new, user menu); `Skeleton` (new; today "loading…" text); `Toast` ← `toast()`; `EmptyState` ← `.empty`; `Stat` ← `fld()`/KPI `.card`.

**Composites:** `AppShell/Sidebar/TopBar` ← `#sidebar`/`#sideNav`/`.topbar` + `applyRole()` gates + `branding.render_shell` tokens; `PageHeader` ← `#pageTitle/#sub` + per-view `.toolbar`; `FilterBar` ← search boxes + the Purchases/Leluxe filter builders + chips; `DataTable` ← the LXT engine (extended: selection column, `BulkActionBar`, end-pinned status/actions, sortable headers already there, `ColumnConfig` = the existing ⊕ panel, URL-synced state via the router, `Skeleton`/`EmptyState`/error rows, keyboard); `DetailDrawer` ← `poDetailModal`, `pkgInfoModal`, customer profile, GM thread; `Modal/ConfirmDialog` ← `.az-modal` + the 47 `confirm()`; `FormLayout` ← `.form-grid` forms; `ImportWizard` ← §5.3 adapters; `KpiRow` ← Orders/In cart cards, Brain tiles; `ActivityFeed` ← Activity + Home activity.

**Status registry** `static/ds/status.js`: one map per entity — order statuses (from `store.STATUSES`, tones: neutral / info / success / danger, `PAID` gets a tone), PO-item exceptions, package `otlobly_status` (values verbatim from the **live** ClickUp schema, replacing the hand-copied 31; label = value, tone per value, employee remap kept as a display layer), carrier buckets (one palette), Gerizim buckets, docs states (one glyph set), GAASH-mail thread states (fix the `waiting_reply` label), lead statuses (get a tone), payment kinds (one wording). The Python "done" sets (F-003) become one module the registry mirrors.

**Formatters** `static/ds/format.js`: `formatMoney(amount, currency)` (USD primary, ILS/AED secondary muted with their own code — never a second `$`), `formatNumber`, `formatDate` (no seconds), `formatRelative` (with absolute tooltip), all `Intl` pinned to `en-US` (D9) — replacing the three `money`, `money0`, `fmt`, `cfNum`, `lxGm`, `relTime`, `agoTxt`, `gmAgo`, `fmtDue`, `lxDate`, `cfFmtDate`, `gmChgLabel/Time`.

**Icons:** one inline SVG sprite (`static/ds/icons.svg`, `<use href="#icon-…">`), 20 px sidebar / 16 px inline, `currentColor`; Heroicons outline is the prior owner choice for customer pages, Lucide is the brief's example (D3). Replaces 2,168 glyph occurrences; directional icons get an `rtl-flip` class for the deferred Arabic work.

**Lint (warn in Phase 1, error at the end of Phase 7):** `tools/inventory.py` gains a `--lint` mode run by a new `test_ds_lint.py` (script-style, in `run_all_tests.sh`): emoji outside an allowlist in `web/index.html` and `static/ds`; raw `<table>`, `<button>`, `<select>`, `<input>` outside `static/ds` (with a per-file baseline that must only go down); physical CSS properties; hex colours outside `tokens.css`; a second definition of any formatter name.

**Demo route:** `GET /design-system` (admin only) serving `web/design-system.html`, which loads the same `static/ds/*` files and renders every primitive and composite in every state (default, hover, focus, disabled, loading, error, empty, dense with long Arabic names). `docs/ux-restructure/DESIGN_SYSTEM.md` documents each component (purpose, when not to use, props/slots, screenshot from the demo route). `web/sw.js` (`CACHE = "otl-off-v1"`, network-first) must precache the new static files and bump its cache name.

**Where the code lives (D1):** `static/ds/tokens.css`, `static/ds/ds.css`, `static/ds/ds.js`, `static/ds/status.js`, `static/ds/format.js`, `static/ds/icons.svg`, loaded by `web/index.html` before its inline `<style>`/`<script>`; migrated views keep living in `web/index.html` (no build step, no framework) but only call design-system helpers. Alternative if refused: the same files inlined between `/* DS START */ … /* DS END */` markers.

## 5.10 Migration order and risks

**Order.** Phase 1 foundations → Phase 2 shell (grouped sidebar, top bar, **hash router** `#/fulfillment/purchase-orders?tab=packages&status=late` (D4), Needs attention, workspace switcher; shipped behind a per-user flag until Phase 4 completes, D13) → Phase 3 Purchases as the reference (brief §14 list) → Phase 4 in usage order: **GAASH mail, To order, Package prep** (daily), then Orders, In cart, Customers, Leads, Deposits, Tracking → Phase 5 details/forms/modals (22 `.az-modal`, 47 `confirm`, 15 `prompt`) → Phase 6 imports (§5.3 adapters; delete the five b64 copies) → Phase 7 Overview / P&L / Goals / Activity, deletions, lint to error, keyboard pass, gallery.

**Risks and how each is held:**

| Risk | Where it bites | Mitigation |
|---|---|---|
| Shared `main` checkout with several parallel sessions; `web/index.html` conflicts | every PR | small PRs from worktrees, one page or component each; rebase before merge; never touch the main checkout's uncommitted files |
| Render auto-deploys every merge to production | every PR | each PR leaves the app usable; new shell behind a flag (D13); `run_all_tests.sh` + `node --check` before merge |
| Markup-anchor tests (`test_po_purchases_redesign`, `test_po_item_declutter`, `test_honest_ui`, `test_new_order_form`, `test_gaash_mail`, `test_activity_monitor_ui`…) | Phases 3–5 | update anchors in the same PR; add the parity suite |
| Per-user `localStorage` schemas (29 keys: `lxt_*`, `*_colw`, `po_filters`, `lx_filters`, `otl_view`, `po_board_view`, `gm_tab`…) | Phase 2–4 | migrate or namespace keys once; never silently reset a user's column layout |
| `branding.render_shell` tokens (`__BRAND_*__`), Tatabu white-label, `FEAT.*` flags, role gates, `RESTRICTED_NAV` | Phase 2 | `AppShell` reads the same tokens/gates; `test_branding`, `test_role_money`, `test_users_scoping` stay green |
| `web/sw.js` offline cache (network-first, `/app` fallback, PII blocklist) | Phase 1 | precache the `static/ds/*` files; bump the cache name |
| The two largest code regions — GAASH mail ≈3,950 lines (with Flags, Forecast, Analyze nested) and Leluxe ≈3,560 (with the LXT engine and Goals nested) | Phase 4 | migrate tab by tab; move the LXT engine out of the Leluxe block first (Phase 1) |
| Mac-only features (Gerizim register via `127.0.0.1:8787`, AZ tracking via Multilogin) | Phases 3–5 | keep their graceful failure; label them as local tools |
| Data-touching endpoints reached from the UI (Estimate all costs is metered; Leluxe organise writes the real ClickUp list) | Phase 3–4 | no behaviour change; every button keeps its confirm/guard semantics |
| Screenshot/parity drift | every phase | `tools/screenshots.mjs` re-run per phase; before/after pairs in the PR |
| Owner decisions pending (D1–D14) | Phase 1 start | none of Phase 1 starts before the report is approved |

**Test baseline** (`bash run_all_tests.sh` on `a8c53fd`, worktree, main venv): **51 passed · 0 failed · 1 min 24 s**.

**Parity checklist (brief §13, extended by this audit)** — becomes `test_ux_parity.py` in Phase 1 and is walked in the browser before any page is marked migrated:

1. Create a lead (Meta sync or manual) → convert to an order → the order appears in To order.
2. Quote it (quick-quote tool) → status QUOTED → move to cart → In cart → mark Ordered.
3. Create a purchase order (+ New order modal, ⌘V screenshot) → add a package with a GWD → the package appears in Purchases, Bulk search and Package prep.
4. Set the package `otlobly_status` to `recieved no rd` → Package prep shows it READY → copy the WhatsApp text.
5. Record a deposit (ILS) → order badge → Deposits ledger → P&L revenue → Activity log.
6. GAASH mail: enroll the GWD (dry-run) → thread state `active` → Docs tab state → Readiness identity edit → declaration attach.
7. Request-ID link → `/id/<token>` submission → customer ID number and image on the customer, package and email token.
8. Leluxe: pull from ClickUp → package rows → status edit (mirror write) → conflict review; organise stays guarded.
9. Customer ID gallery upload (jpeg/pdf) → visible in Customers and Purchases ID column.
10. Backup download works; Trash restore works; Team create + password reset; Settings save round-trips every panel.
11. Roles: `fulfillment` sees no money (`money:false`), `sales` lands on To order; Tatabu impersonation banner still appears.

## 5.11 Open questions and decisions

**Brief §15 — answered from the code, to be confirmed by the owner:**

1. **Brain** = `brain.py`, a per-tenant rules engine (urgent · due · forgotten · money · last 7 days) rendered as the landing page; pure data out, built to feed a Telegram digest; not an AI assistant. Proposal: it becomes **Overview** and feeds Needs attention (D5).
2. **Leluxe** = the owner's second business line (Amazon bulk stock for a partner: watches, cards, IT), mirrored live from ClickUp list "AZ (2)", admin-only; shares GWDs, GAASH, tracking and tables with Otlobly, shares no customer/quote/deposit. Owner today: workspace switcher.
3. **Profile codes** = Amazon buying accounts = Multilogin browser profiles of the same name (`az.py:12`); `E-B*`/`S-B*` are later account generations; free-text on Purchases, ClickUp dropdown on Leluxe, colour-coded by ClickUp. Proposal: label "Buying account" with a tooltip.
4. **Pipeline:** order statuses REQUESTED → QUOTED → PAID → IN_CART → ORDERED → SHIPPED → ARRIVED → DELIVERED → COLLECTED (+ CANCELLED); packages carry parallel stages (GAASH transit → customs → cleared → arrived → delivered; Gerizim last mile; 31–35 ClickUp `otlobly_status` values). The brief's four pages are stages 3–6; "Arrived / customs" lives in GAASH mail › Docs, "Delivered / collected" in Orders. Missing from the brief: **Customs** as a stage — handled by GAASH mail under Shipping.
5. **Most used:** Purchases, GAASH mail, To order, Package prep (owner). Devices: desktop; a ≤900 px drawer and a 200 px pinned-column cap exist and are kept (rule 2). **Ask:** any tablet/phone use worth designing for?
6. **Currency:** USD base; ILS for entry (`fx.ils_per_usd` 3.7 bookkeeping, `fx.pkg_ils_per_usd` 3.1 display-only), AED fallback; ClickUp `Total Amount` is USD but rendered ₪ on Leluxe boards and $ on its dashboard (F-013). Numerals: Western, `en-US` pinned (D9).
7. **Component library:** none; no Storybook; tokens = one 17-var `:root`. Proposal D14.
8. **Flags** = Gmail "action required" watcher + Telegram nag, not a manual list → the email source of Needs attention; inbox config → Settings › Integrations.
9. **Removal candidates:** Catalog (staff view, `/catalog` storefront, footer link — check `source="website"` orders first), Picking list, the five dead customer-login endpoints, Sync ClickUp as a nav item (D6).

**Decisions needed before Phase 1 (recommended option first):**

- **D1** Design-system files in `static/ds/*` loaded by `index.html` — vs marker-delimited sections inside `index.html`.
- **D2** The ع toggle: hidden in the new shell until Arabic returns (legacy views keep it) — vs kept, swapping legacy labels only.
- **D3** Icons: Heroicons outline as an inline SVG sprite (prior decision for customer pages) — vs Lucide.
- **D4** Hash-based URLs per view / tab / filter / object in Phase 2 (needed for URL-synced tables and deep links from Telegram) — vs no URLs.
- **D5** Brain → "Overview" home whose sections also feed Needs attention — vs a top-bar utility.
- **D6** Removals, each needing an explicit yes: Catalog (+ storefront), Picking list, dead login endpoints, Sync ClickUp nav item.
- **D7** Rename: page "Purchases" → "Purchase orders"; footer "N purchase orders"; "+ New order" → "New purchase order".
- **D8** Fulfillment as one page with four stage tabs (brief default) — vs four pages on one template.
- **D9** Numbers and dates pinned to `en-US`; no seconds; relative dates with absolute tooltips.
- **D10** Before/after screenshots committed as JPEG under `docs/ux-restructure/screens/` (Phase 0 set = 49 files, ≈4.3 MB) — vs a private artifact gallery.
- **D11** Tatabu stays a separate shell mode reached from the workspace switcher.
- **D12** Purchases gets row checkboxes + a `BulkActionBar` in Phase 3 (none today; Orders has one).
- **D13** New shell behind a per-user flag until Phase 4 completes — vs a hard switch at Phase 2.
- **D14** No component library: hand-built primitives on native `<dialog>`, `<details>`, `popover` — vs Shoelace web components from a CDN.

**Found during the audit, also for the owner:** the Purchases subtitle shows the Orders board's numbers (fix in Phase 3 by design); `PAID` orders are invisible to Brain's and the report's "open" sets; the Settings status-map editor cannot map seven real ClickUp statuses; `lxIsDone` treats "not received" parcels as done (F-008, a logic bug worth fixing before the restructure); `/api/restore` has no UI while backup does.
