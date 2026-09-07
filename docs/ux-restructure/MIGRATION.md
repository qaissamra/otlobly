# MIGRATION.md — route and component checklist (UX restructure)

Status values: `not started` · `in progress` · `migrated` · `deleted` · `removal proposed` (awaiting owner approval, brief §4 rule 2).
Update this file in **every** PR of the restructure. "Screens" = `screens/before/<name>.jpg` captured in Phase 0 by `tools/screenshots.mjs`; the "after" column is filled when a row is migrated (English only, per the 2026-09-06 owner decision).

## A. Views (today's `setView` ids) → target template

| View | Today | Template | Target home (brief §6, D-decisions pending) | Screen (before) | Status |
|---|---|---|---|---|---|
| `brain` | 🧠 Brain landing | T4 | Overview (D5, renamed in the shell Phase 2 ✓) + feeds Needs attention (✓) | `brain.jpg` | in progress |
| `purchases` (orders tree) | 📦 Purchases | T1 | Fulfillment › Purchase orders (the reference page) | `purchases-orders.jpg` → `after/purchase-orders.jpg` | **migrated** |
| `purchases` › packages | 📦 Packages sub-view | T1 | same page, saved view + its own address | `purchases-packages.jpg` → `after/purchase-packages.jpg` | **migrated** |
| `purchases` › products | ⫶ Products sub-view | T1 | same page, saved view + its own address | `purchases-products.jpg` → `after/purchase-products.jpg` | **migrated** |
| `purchases` › customers | 👤 Customers sub-view | T1 | same page, saved view + its own address | `purchases-customers.jpg` → `after/purchase-customers.jpg` | **migrated** |
| `purchases` › split | 💵 Cost split (money roles) | T1 | same page, saved view | `purchases-split.jpg` | not started |
| `needorder` | 💡 To order | T1 | Fulfillment › To order | `to-order.jpg` | not started |
| `needorder` › quote tool | Quick quote (`#quoteView`, own `QLANG`) | T3 | Fulfillment › To order › Quote (drawer or page) | `to-order-quote-tool.jpg` | not started |
| `incart` | 🛒 In cart | T1 | Fulfillment › In cart | `in-cart.jpg` | not started |
| `pkgprep` | 🎁 Package prep | T1 | Fulfillment › Package prep | `package-prep.jpg` | not started |
| `orders` | 🏠 Orders | T1 | Sales › Orders | `orders.jpg` | not started |
| `orders` › add-order panel | ＋ Add order (mounts Purchases cards) | T3 | Sales › Orders › New order (drawer) | `orders-add-order-panel.jpg` | not started |
| `customers` | 👤 Customers | T1 (+T2 profile) | Sales › Customers | `customers.jpg` | not started |
| `metaleads` | 📣 Leads | T1 | Sales › Leads | `leads.jpg` | not started |
| `bulksearch` | 🔎 Bulk search | T1 | Shipping › Tracking | `bulk-search.jpg`, `bulk-search-results.jpg` | not started |
| `gaashmail` › conv | 💬 Conversations | T1 (+T2 thread) | Shipping › GAASH mail | `gaash-mail-conversations.jpg` | not started |
| `gaashmail` › ov | 🧭 Overview | T4 | Shipping › GAASH mail › Overview | `gaash-mail-overview.jpg` | not started |
| `gaashmail` › seq | ⚙️ Workflows | T1 (+T3 builder) | Shipping › GAASH mail › Workflows | `gaash-mail-workflows.jpg` | not started |
| `gaashmail` › tpl | 📝 Templates | T1 (+T3 editor) | Shipping › GAASH mail › Templates | `gaash-mail-templates.jpg` | not started |
| `gaashmail` › ready | 🩺 Readiness | T1 | Shipping › GAASH mail › Readiness (feeds Needs attention) | `gaash-mail-readiness.jpg` | not started |
| `gaashmail` › docs | 📄 Docs | T1 | Shipping › GAASH mail › Docs (feeds Needs attention) | `gaash-mail-docs.jpg` | not started |
| `gaashmail` › fcast | 🔮 Forecast | T4 | Shipping › GAASH mail › Forecast | `gaash-mail-forecast.jpg` | not started |
| `gaashmail` › dash | 📊 Analyze | T4 | Shipping › GAASH mail › Analyze | `gaash-mail-analyze.jpg` | not started |
| `flags` | 🚩 Flags | T1 | Needs attention (open flags, Phase 2 ✓) + Settings › Integrations (inboxes; routed at `#/settings/inboxes` until Phase 7) | `flags.jpg` | in progress |
| `deposits` | 💵 Deposits | T1 (+T3 entry) | Finance › Deposits | `deposits.jpg` | not started |
| `pnl` | 📊 P&L | T4 | Finance › P&L | `pnl.jpg` | not started |
| `goals` | 🏆 Goals | T4 | Insights › Goals | `goals.jpg` | not started |
| `activity` | 🕑 Activity | T4 | Insights › Activity | `activity.jpg` | not started |
| `leluxe` › orders / packages / products | ⌚ Leluxe boards | T1 | Workspace switcher → Leluxe (Fulfillment + Shipping data) | `leluxe-orders.jpg`, `leluxe-packages.jpg`, `leluxe-products.jpg` | not started |
| `leluxe` › dashboard / goal | ⌚ Board · 🎯 Goal | T4 | Workspace switcher → Leluxe › Insights | `leluxe-dashboard.jpg`, `leluxe-goal.jpg` | not started |
| `settings` (13 panels) | ⚙️ Settings | T5 | Settings (user menu) | `settings.jpg` | not started |
| `team` | 👥 Team | T1 | Settings › Team | `team.jpg` | not started |
| `trash` | 🗑 Trash | T1 | Settings › Trash (+ "show deleted" in lists) | `trash.jpg` | not started |
| `syncClickup()` | ↗ Sync ClickUp (nav action) | — | Settings › Integrations (button + run history) | — | not started |
| `platoverview` · `brokers` · `brokerprofile` · `plans` · `usage` · `platactivity` | 🏗 Tatabu console | T4 / T1 / T2 | Workspace switcher → Tatabu (D11) | `tatabu-*.jpg` | not started |
| `catalog` (hidden) | Catalog | T1 | — | `catalog-hidden.jpg` | removal proposed (D6) |
| `picking` (hidden) | Picking list | T1 | — | `picking-hidden.jpg` | removal proposed (D6) |
| ع language toggle | `langToggle()` | shell | hidden in the new shell until Arabic returns (D2) | `language-arabic-purchases.jpg` | not started |

## B. Modals (22 `.az-modal` roots, L1803–2023) → target

| Modal id | Owner view | Target | Status |
|---|---|---|---|
| `poDetailModal` | Purchases | T2 DetailDrawer (purchase order) | not started |
| `pkgInfoModal` | Purchases | T2 DetailDrawer (package) | not started |
| `newOrderModal` | Purchases | T3 FormLayout (create PO) | not started |
| `poEditModal` · `pkgEditModal` · `itemEditModal` | Purchases | T3 FormLayout | not started |
| `orderEditModal` | To order / Orders | T3 FormLayout | not started |
| `priceImgModal` | Quote tool | Modal (canvas editor) | not started |
| `notifyModal` | Purchases (notify customers) | T3 FormLayout | not started |
| `gzBulkModal` | Purchases / Leluxe (Gerizim register, Mac only) | Modal + BulkActionBar | not started |
| `azModal` | Purchases (AZ / Multilogin, Mac only) | Modal | not started |
| `bulkModal` (generic, 6 body-class reskins) | GAASH mail enroll picker, templates, changes, custom-field creator… | split into Modal / DetailDrawer / ImportWizard | not started |
| `pnlDrillModal` | P&L | DetailDrawer | not started |
| `gmStatModal` | GAASH mail | Modal | not started |
| `lxInfoModal` · `lxEditModal` · `lxMoveModal` · `lxGoalSetModal` · `lxSyncReportModal` · `lxActivityModal` · `lxConflictModal` · `lxAz2HistModal` | Leluxe | T2 / T3 / Modal | not started |
| native `confirm()` ×47 · `prompt()` ×15 · `alert()` ×2 | everywhere | ConfirmDialog / FormLayout / Toast | not started |

## C. Component families → canonical component

| Family today | Definition | Canonical target | Decision | Status |
|---|---|---|---|---|
| bare `button{}` + `.primary` + `.accent` | CSS ≈L54–58 | `DS.button` | merge | **built** (0 of 55 call sites migrated) |
| `.po-btn` (+`.accent`, `.danger`) | CSS ≈L241 | `DS.button` (secondary / primary / danger) | keep as base | **built** (0 of 129 migrated) |
| `.minibtn` (+`.danger`) | CSS ≈L287 | `DS.button {size:'sm'}` | merge | **built** (0 of 213 migrated) |
| `.iconbtn` · `.qchip` · `.chip` | CSS ≈L235 / 267 / 330 | `Button icon` · `Tag` · `Tabs` | merge | not started |
| LXT table engine (`LX_TABLES` L3594, `LXT_COLS` L4045, `lxtHead` L4452, `lxtCells` L4497) — 15 tables | JS L4045–4955 | `DS.tableRender` (`table.js`) | superseded — the new engine adds selection + bulk bar, end-pinned status/actions, keyboard, skeleton/empty/error, typed cells | **built** (0 of 15 tables migrated) |
| `neTable` / `NE_COLS` (To order) | JS L7385–7400 | `DataTable` | delete after migration | not started |
| 20 raw `<table>` (P&L ×7, Settings ×3, platform ×3, Team, Activity, Picking, Deposits-by-customer, To order, GM templates, GM analyze) | markup + JS | `DataTable` | delete | not started |
| `.pill` rule pair (L287 vs L963) + `statusPill` L2443 · `tonePill` L9578 · `hexPill` L9581 · `solidPill` L9588 · `gaashBucketPill` · `lxStatusPill` · `lxCfPill` + 12 domain builders + 48 raw literals | JS/CSS | `DS.badge` + `DS.attention` behind `status.js` | merge (one rule, one helper) | **built** — registry covers all 35 live ClickUp statuses + every order status; 0 call sites migrated |
| `fld()` L2449 (two CSS homes: `.po-meta .field`, `.ne-meta .field`) | JS/CSS | `Stat` / meta strip | keep, one CSS home | not started |
| `editCell()` L2462 | JS | `DataTable` inline edit | keep | not started |
| `openStore()` L2487 | JS | `RowExpansion` state | keep | not started |
| `.po-card` two-tier header · `.ne-metarow` · `.poc-meta` | CSS | `PageHeader` / row header + `Stat` | merge (one meta strip) | not started |
| `.az-modal` ×22, each with its own open/close pair | markup L1803–2023 | `DS.modal` / `DS.drawer` / `DS.confirm` (native `<dialog>`: focus trap, Esc, backdrop, unsaved guard) | merge | **built** (0 of 22 migrated) |
| `.pop` / `.pop-menu` + `popMenu()` L10186 / `popToggle` | JS/CSS | `DropdownMenu` | keep, add keyboard | not started |
| `toast()` L2065 (single slot, no variants, 379 calls) | JS | `DS.toast` (+ `.success/.error/.warn/.info`, queue of 3, `role=status`, optional action) | keep, extend | **built** (0 of 379 migrated) |
| `.empty` (21) + 475 ad-hoc `muted2` empty states | CSS | `EmptyState` | merge | not started |
| `#pageTitle` + global `#sub` (L1035, written at L2362) + 44 `<h2>` + 21 `.toolbar` | markup | `PageHeader` (breadcrumb, stats, one primary) | merge | not started |
| tabs ×3 (GAASH mail 8 tabs, Purchases 5-segment, quote chips) + `.chips` rows | markup | `Tabs` | merge | not started |
| search boxes ×5 (2 `.search`, 3 inline) + 14 `.cu-search` + Purchases filter builder | markup/JS | `FilterBar` (+ `Combobox`) | merge | not started |
| emoji icons (2,168 glyphs, 191 distinct) | everywhere | `DS.icon` + `icons.svg` — 135 Heroicons v2 outline, MIT | replace | **built** (0 of 2,168 replaced) |
| formatters `money` ×3 (L2053, 4765, 10612) · `money0` · `fmt` · `cfNum` · `lxGm` · `relTime` L7199 · `agoTxt` L10055 · `gmAgo` L11572 · `fmtDue` L9563 · `lxDate` L3252 · `cfFmtDate` L9373 | JS | `DS.fmt.money / number / date / relative` (Intl, `en-US`) | merge | **built** (0 of 13 migrated) |
| 13 colour registries (`STATUS_COLOR` L2042 … `GM_STATE` L11542) | JS | `DS.status` (`status.js`) | merge | **built** — one palette per entity, one `pkgDone` set (fixes F-003/F-008); 0 call sites migrated |
| `T()` bilingual split + `data-en/ar` + `QSTR` + `ML_STATUS` | JS | keep `T()` for legacy views; new components English (D2) | keep (deferred) | not started |

## D. Upload and import entry points → `ImportWizard` adapter (Phase 6)

| Entry point (today) | Adapter id | Status |
|---|---|---|
| Purchases: PO screenshot (`#poImgInput`), detail multi-upload (`#podImgInput`), detail ⌘V, new-PO ⌘V/click | `po-images` | not started |
| Purchases: package popup ⌘V (`/api/purchase/package/image`) | `package-photos` | not started |
| Customers: ID upload (`#custIdInput`, jpeg/png/pdf) | `customer-id` | not started |
| Leluxe: row attachments → ClickUp | `leluxe-images` | not started |
| Quick quote: proof / product image / window paste (client-side only) | `quote-images` | not started |
| Price-image editor (`piUpload`) | `quote-images` | not started |
| GAASH mail: attach next / attach this message / document library / in-wizard library upload | `gaash-attachments`, `gaash-documents` | not started |
| Bulk search textarea (`bsTokens`) | `tracking-numbers` (paste) | not started |
| GAASH enroll wizard paste (`gmNewGwds`) | `tracking-numbers` (paste) | not started |
| Gerizim bulk register (Mac only) | `gerizim-register` (source) | not started |
| Sync buttons: Import from ClickUp, Check all shipping, Estimate all costs, Sync from orders, Sync from Meta, Leluxe tools, Check replies, Catalog fetch, item photo fetch | `clickup-po`, `tracking-refresh`, `cost-estimate`, `customers-from-orders`, `meta-leads`, `leluxe-*`, `gaash-replies`, `product-url` (source) | not started |
| Backup download `/api/backup` (+ UI-less `/api/restore`) | `backup` (export + restore) | not started |

## E. Removal candidates (need explicit approval — brief §4 rule 2)

| Item | Evidence | Status |
|---|---|---|
| Catalog staff view + `/catalog` storefront + 4 `/api/catalog*` routes + pricing-footer link | nav removed 2026-07-22; `VIEW_BTN.catalog → catalogBtn` (no such element); owner said retire on 2026-09-04; check `source="website"` orders first | removal proposed |
| Picking list (`pickingView`, `renderPicking`) | no button, `togglePicking` has no caller, runs on every refresh into a hidden div | removal proposed |
| Dead customer-auth endpoints (`wa_login/start`+`poll`, `wa_verify/start`, `email/login/start`+`verify`) | no UI caller; only `_mint_login_token()` producers | removal proposed (hide first) |
| `↗ Sync ClickUp` as a nav item | an action in a navigation list | move to Settings › Integrations |
| `RESTRICTED_NAV.sales` reference to `catalogBtn` | dead id | delete with Catalog |

## E2. Phase 1 evidence

`/design-system` (admin only) renders every component in every state; screenshots in
`screens/design-system/`. Verified headless on the Phase 1 branch: 135 icons render, the DataTable
sorts / drags / resizes / hides columns / selects with a bulk bar / expands nested tables / mirrors
the header to the body scroll, dialogs trap focus and return it, 30 icon-only buttons all carry a
label, the focus ring appears on a real Tab, no console errors, no horizontal overflow, and the RTL
smoke flip does not break the layout. The staff app itself renders identically to the Phase 0
baseline with zero design-system classes in its DOM.

## E3. Phase 2 evidence — the shell

The new shell is **additive and opt-in**. `static/ds/shell.js` renders a design-system sidebar and
top bar beside the legacy ones and hides those with CSS; every page it opens is the same legacy view
container as before, shown by the same `setView()`. Only 49 lines of the app itself changed: the
`window.APP` read-only bridge (index.html's top-level `let` bindings are not `window` properties),
one `<script>` tag, one empty `#attentionView` container, and its four lines inside `setView`.

| What | How it works |
|---|---|
| Flag (D13) | `localStorage.otl_shell` = `"ds"`. `?shell=new` / `?shell=old` set it from a link; the classic top bar grows one "New layout" button, and the new user menu has "Switch to the classic layout". Off for everyone until the owner turns it on. |
| Role and feature gates | Not restated. A nav item is visible exactly when its legacy nav button is (`applyRole()` still owns every gate); `test_ds_shell.py` asserts each named button exists. |
| Router (D4) | `#/group/page[/tab]`. `hashchange` opens the page and its tab; every `setView()` call — from anywhere — writes the address back. Pre-Phase-2 links (`#purchases`) land and are rewritten to the canonical path with `replaceState`. Deep links win over the app's own "restore last page" once, at boot. |
| Tabs with addresses | GAASH mail (8), Purchases boards (4), Leluxe segments (5) — the route calls the page's own `gmTab` / `poSetView` / `lxSetView`. |
| Fulfillment (D8) | One nav item, four stage tabs, four addresses. The nav badge is the work waiting in the pipeline; the tabs carry the per-stage counts. `/fulfillment` alone reopens the stage you were last on. |
| Needs attention | New `attention.py` + `GET /api/attention`: action-required email (Flags), packages past their due date or GAASH deadline, customs asking for documents, packages with no GWD, and the Brain's urgent rules — minus anything that is already a nav badge. Carries no money, so it needs no redaction. |
| Workspaces (D11) | Otlobly · Leluxe · Tatabu in the sidebar foot. Tatabu is its own shell mode: the five platform pages replace the groups, and "Otlobly" calls the app's own `exitPlatform()`. |
| Global search | `/` focuses it. Pages, orders (from the report already loaded), purchase orders, GWD and OTL numbers; the PO store is fetched once, lazily, the first time someone searches. Arrow keys and Enter, Esc clears. |

Verified headless against a live copy of the app: 12 navigable items in 5 groups, all 21 legacy nav
buttons still carry their gates, 10 routes open the right page and tab, the back button works, a
legacy `#purchases` link is rewritten, a deep link into `#/shipping/gaash-mail/ready` lands there
through the app's own boot, switching the flag off restores the classic shell exactly, and there are
no console errors. Screens in `screens/after/`.

**Known and deliberate:** a migrated page will lose its own `<h2>` when Phase 3/4 rebuilds it — until
then the page header and the legacy toolbar title both show, which is why Purchases reads
"Purchase orders" twice. Package prep has no count badge yet (no cheap source; it gets one when the
page is migrated). The `#sub` subtitle now appears only on Orders, where it is actually true.

## E4. Phase 3 evidence — Purchase orders, the reference page

The page moved onto the design system: `static/ds/purchases.js` renders the header, the
filter bar and all four boards on `DS.tableRender`. The old implementation is **gone** — not
running beside it (brief §4 rule 4): `poCardHtml`, `poPkgHtml`, `poPkFlatRow`, `poItFlatRow`,
`poRenderPkgsFlat`, `poRenderProdsFlat`, `poRenderCustomers`, `poSortVal` and `poCfCells` are
deleted, the LXT tables `po` / `pok` / `pop` are out of `LX_TABLES`, `LXT_COLS` and `LXT_CLS`,
and their orphaned CSS is stripped. `renderPurchases()` is now a 20-line bridge that applies
the filters and hands the page a `ctx`; every cell still calls the app's own builders, so how
a value is **saved** did not change in this phase.

**Brief §14, item by item:**

| # | Was | Now |
|---|---|---|
| 2 | a middle-dot sentence with a seconds clock, no primary action | `DS.pageHeader`: breadcrumb, title, six numbers, one primary ("New purchase order"), one secondary, the rest in an overflow menu |
| 3 | "Purchases" titled a page counting "41 orders" | "Purchase orders", counting purchase orders, in the nav and the footer too (D7) |
| 4 | ORDER cell = id + hash + a summary sentence repeating two other columns | the identity cell carries the PO number and the Amazon tail; Order name, Customers, Items and Packages are their own columns |
| 5 | TOTAL concatenated into the CUSTOMER cell | **Paid** is its own right-aligned, tabular column |
| 6 | two "$" amounts in one cell, one of them a conversion | **Est. cost** is a separate column, in the muted tone, with the unpriced count as `+3?` |
| 7 | truncation with no tooltips, Arabic clipped at the wrong end | every text cell is `dir="auto"` + `ds-truncate` with the whole value in the tooltip; the three-word product-name clip is gone |
| 8 | STATUS off-screen, pills cut, no pinned column | identity pinned to the start, **Status** and actions pinned to the end, edge shadow while there is more to scroll |
| 9 | "40 DAYS LATE" beside lowercase pills | sentence case; lateness reads "47 d"; a legacy `.pill` stops shouting inside a DS table |
| 10 | four problem treatments (red dash, orange text, red caps pill, tone pills) | one **Needs attention** column: `late` · `no_tracking` · `missing_name` from the attention vocabulary. Status pills stay status |
| 11 | cards with shadows and gaps, a phantom row | flat rows, one border, no gap |
| 12 | nested packages with emoji counters and no header row | packages and products each get an aligned sub-grid **with a header row** (`.ds-pu-sub`) |
| 13 | `B19`, `E-B15` unexplained | the Buying account column explains itself in its header tooltip |
| 14 | the nav's peach fill also marked the open row | the open row has its own surface and a start bar; the accent fill belongs to the nav alone |
| 15 | an unlabelled "+" at the end of the header row | a labelled **Columns** button in the table bar, which also says how many are hidden |

**Also landed:** every board has an address (`#/fulfillment/purchase-orders/packages`), so a
board can be linked; `defaultHidden` columns (new in `table.js`) let a board ship with its
secondary column folded away; and `DS.shell2.syncTab()` keeps the address honest when a page
switches its own tab.

Verified headless on a copy of the live data: 10 orders, 18 packages, 30 products, 19
customers render; the identity column stays put while the grid scrolls and the Status column
is fully on screen; 18/18 text cells carry tooltips; the open row's fill differs from the
nav's; every inline editor still works (18 due-date editors, 18 RD inputs, the package status
picker); the classic shell renders the same page; **no console errors**.
Screens in `screens/after/purchase-*.jpg`.

**Deliberate change to call out:** inside a package, products used to sit under a customer
heading; each product row now NAMES its customer in its own column instead (one fact per
column). Grouping by customer is what the Customers board is for, and it kept its per-order
separation. Nothing was removed.

## F. Phase checklist

- [x] **Phase 0** — audit and plan: `BRIEF.md`, `AUDIT.md`, this file, `tools/inventory.py`, `tools/screenshots.mjs`, `screens/before/`, test baseline 51/51. *Waiting for owner approval.*
- [x] **Phase 1** — foundations *(this PR)*: `static/ds/` (tokens · ds.css · ds.js · table.js · status.js · format.js · icons.svg), the `/design-system` catalogue, `DESIGN_SYSTEM.md`, the warn-level lint (`test_ds_lint.py` + `lint-baseline.json`), `test_design_system.py`, and the behaviour parity suite `test_ux_parity.py`. Loaded app-wide but used by nothing yet — the staff app is byte-for-byte unchanged on screen.
- [x] **Phase 2** — shell and navigation *(this PR)*: `static/ds/shell.js` (grouped sidebar, top bar with global search, hash router, stage tabs, workspace switcher), `attention.py` + `/api/attention` + the Needs attention page, `test_ds_shell.py`, `test_attention.py`. Behind the per-user flag (D13) until Phase 4 completes.
- [x] **Phase 3** — Purchase orders on T1 *(this PR)*: `static/ds/purchases.js` (header, filter bar, four DataTable boards, aligned sub-grids), the old renderers and LXT tables deleted, `test_ds_purchases.py`, `defaultHidden` columns. All fifteen §14 items answered.
- [ ] **Phase 4** — GAASH mail, To order, Package prep, then Orders, In cart, Customers, Leads, Deposits, Tracking.
- [ ] **Phase 5** — details, forms, modals.
- [ ] **Phase 6** — imports onto `ImportWizard`; delete old importers.
- [ ] **Phase 7** — insights, cleanup, lint to error, keyboard pass, before/after gallery.
