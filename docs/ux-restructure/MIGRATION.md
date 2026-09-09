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
| `needorder` | 💡 To order | T1 | Fulfillment › To order (4 saved views, each addressable) | `to-order.jpg` → `after/to-order.jpg` | **migrated** |
| `needorder` › quote tool | Quick quote (`#quoteView`, own `QLANG`) | T3 | Fulfillment › To order › Quote (drawer or page) — moved below the queue and folded by default in Phase 4a; rebuilt in Phase 5 | `to-order-quote-tool.jpg` | in progress |
| `incart` | 🛒 In cart | T1 | Fulfillment › In cart | `in-cart.jpg` → `after/in-cart.jpg` | **migrated** |
| `pkgprep` | 🎁 Package prep | T1 | Fulfillment › Package prep (3 saved views, each addressable) | `package-prep.jpg` → `after/package-prep.jpg` | **migrated** |
| `orders` | 🏠 Orders | T1 | Sales › Orders | `orders.jpg` | **migrated** (Batch B) |
| `orders` › add-order panel | ＋ Add order (mounts Purchases cards) | T3 | Sales › Orders › New order (drawer) | `orders-add-order-panel.jpg` | not started |
| `customers` | 👤 Customers | T1 (+T2 profile) | Sales › Customers | `customers.jpg` | **migrated** (Batch B) |
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

## E5. Phase 4a evidence — the rest of the Fulfillment pipeline

To order, In cart and Package prep now run on the same recipe Purchase orders set:
`static/ds/fulfillment.js` renders each page's header and saved views, then one DataTable
per view, and expands a row into an aligned sub-grid. With this, **the whole Fulfillment
nav item is on the design system**.

| Page | Was | Now |
|---|---|---|
| To order | a raw `<table>` with its own drag-to-resize code, four collapsible sections, ticks wired by hand | one DataTable per saved view (To order · In cart · Ordered · Deleted), with the table's own selection and one bulk action, "Move to cart" |
| | badges for website / plan / confirmed / deposit / ID, all different shapes | a **Tags** column with one shape, and a **Needs attention** column with one vocabulary: no price · no plan · no ID · late |
| | the Arabic-first quote tool sat above the queue and opened by default | the queue comes first; the quote tool waits folded away below it |
| In cart | an LXT table plus three KPI cards | one DataTable, and one decision strip: revenue, the cost you type, and the profit, which still answers as you type |
| Package prep | three walls of cards with Arabic-only headings | one board with three saved views — Ready to pack · Waiting for pieces · Ask for a review — and a real count for each |
| | the review card was a second, near-identical card type | every row is the same row, and every row opens the same shared body (`ppBody`) |

**Deleted, not parked:** `neTable`, `neRowHtml`, `neItemRow`, `neSection`, `neChip`,
`neSecToggle`, `neRowToggle`, `neCartBar`, `neCartToggle`, `neColDown`, `neColReset`,
`neColStyle`, `neRz`, `NE_COLS`, `NE_COLW`, `cartTable`, `icSortVal`, `ppCard`,
`ppReviewCard`, `ppSection`, `ppToggle`, `ppFlds` (and the already-unreachable `ppCopy`),
plus the LXT table `ic` from all three registries.

**Also landed:** every saved view has an address — `#/fulfillment/to-order/ordered`,
`#/fulfillment/package-prep/waiting` — so a link opens the view it points at; and
`DS.tag` learned a tone, so a fact about a row can be neutral, good or cautionary
without becoming a status badge.

Verified headless on a copy of the live data: the queue shows its four buckets with counts
that match the stage tabs; ticking rows raises the bulk bar and Move to cart still posts the
same ids; the cart's cost input still saves and the profit still answers live; Package prep
shows both WhatsApp country codes, keeps every action, and its rows open the same package
body with its editable status; the classic shell renders all three; **no console errors**.
Screens in `screens/after/to-order*.jpg`, `in-cart.jpg`, `package-prep*.jpg`.

## F. Phase checklist

- [x] **Phase 0** — audit and plan: `BRIEF.md`, `AUDIT.md`, this file, `tools/inventory.py`, `tools/screenshots.mjs`, `screens/before/`, test baseline 51/51. *Waiting for owner approval.*
- [x] **Phase 1** — foundations *(this PR)*: `static/ds/` (tokens · ds.css · ds.js · table.js · status.js · format.js · icons.svg), the `/design-system` catalogue, `DESIGN_SYSTEM.md`, the warn-level lint (`test_ds_lint.py` + `lint-baseline.json`), `test_design_system.py`, and the behaviour parity suite `test_ux_parity.py`. Loaded app-wide but used by nothing yet — the staff app is byte-for-byte unchanged on screen.
- [x] **Phase 2** — shell and navigation *(this PR)*: `static/ds/shell.js` (grouped sidebar, top bar with global search, hash router, stage tabs, workspace switcher), `attention.py` + `/api/attention` + the Needs attention page, `test_ds_shell.py`, `test_attention.py`. Behind the per-user flag (D13) until Phase 4 completes.
- [x] **Phase 3** — Purchase orders on T1 *(this PR)*: `static/ds/purchases.js` (header, filter bar, four DataTable boards, aligned sub-grids), the old renderers and LXT tables deleted, `test_ds_purchases.py`, `defaultHidden` columns. All fifteen §14 items answered.
- [~] **Phase 4** — the daily pages. **4a *(this PR)*: To order, In cart, Package prep** — `static/ds/fulfillment.js`, `test_ds_fulfillment.py`; the Fulfillment group is now fully migrated. Still to do: GAASH mail (8 tabs), then Orders, Customers, Leads, Deposits, Tracking.
- [ ] **Phase 5** — details, forms, modals.
- [ ] **Phase 6** — imports onto `ImportWizard`; delete old importers.
- [ ] **Phase 7** — insights, cleanup, lint to error, keyboard pass, before/after gallery.


## Batch B — Sales (2026-09-07)

`static/ds/sales.js` holds `DS.orders` and `DS.customers`; `render()` and
`renderCustomers()` in index.html are bridges passing a `ctx`. The LXT `od` and `cu`
tables are **deleted** from all four registries, from `lxtRender`, from the persistence
bootstrap and from their orphaned CSS. Ten functions removed: `buildFilters`,
`toggleOrderSel`, `toggleSelAll`, `clearOrderSel`, `updateBulkBar`, `odSortVal`,
`waMenu`, `cuSortVal`, plus the two hand-rolled render bodies.

Measured before → after (1600px, admin):

| | unreadable | truncated | targets < 24px | raw pills | DS header |
|---|---|---|---|---|---|
| Orders | 69 → **0** | 29 → **0** | 220 → **0** | 22 → **0** | no → **yes** |
| Customers | 0 → 0 | 0 → 0 | 1 → **0** | 0 → 0 | no → **yes** |

**Capabilities kept:** every inline edit (city, address, box, Amazon #, status, VIP),
bulk select + delete, the Σ totals row and its open-status rule, the quote/deposit
actions, the six WhatsApp templates, the notify button and its date gate, the ID
gallery and both its filters, the profile panel and its order history.

**Capabilities gained:** the products of an order are now readable (a row expansion with
ASIN and a link out, instead of block links overflowing a 43px row); Customers gained
`Collected` and `Last order` columns, which `customers.enrich()` has always computed and
nothing displayed; the WhatsApp templates became a labelled menu instead of a bare
`<select>`; the gallery's two "Show only …" links became real filter chips.

**Bugs found and fixed while migrating:**
- `DS.tableEv`'s bulk handler called `b.onclick(...)` as a function, but every other
  onclick in the design system is a string — so Phase 4a's "Move to cart" button on the
  To-order page threw the moment it was clicked. It now accepts either.
- The Customers `Spent` column rendered `$0.00` for a role without `view_money`, because
  the server redacts the figure to `null` and `money(null)` is `"$0.00"`. A redacted
  number that looks real is worse than no column, so the column is dropped for that role.
- The gallery's counter said "8 of 43 have an ID" while the header said 43/43: one
  counted photos, the other photos-or-typed-numbers. The chip now counts exactly what it
  filters, and is labelled "No photo yet".

**Known, not fixed here:** Orders still renders 8 distinct font sizes (was 7). The board
itself is on the DS scale; the remainder come from `statusSelect`'s inline styles and the
KPI cards above the board, which are legacy markup this batch did not touch.


## A migrated page module is a legitimate home for a legacy button (2026-09-09)

Rebasing the clearance-record branch (PR #151) onto post-Batch-B main surfaced a rule the
migration has to state out loud: **a feature test that counts buttons must scan
`static/ds/*.js` as well as `web/index.html`.**

`test_gaash_docs_sent.py` guards the structural fix behind "Sent to Gaash" — documents could
reach GAASH from **eleven** buttons and only four recorded anything, so the record moved into
`gaashUploadOpenGwd`, the funnel they all share, and the suite fails if a call site ever grows
a private opt-out. It counted those eleven in index.html alone. Phase 3 then moved the
Purchases board into `static/ds/purchases.js` and its "Upload documents to GAASH" row action
went with it — ten in index.html, one in the page module. The button never disappeared; the
count did.

The suite now reads index.html **plus every `static/ds/*.js`** for the counting checks (the
structural checks stay scoped to index.html, where the funnel itself lives) and prints the
number it found, so the next drop says *what* it dropped to. Expect the same repoint in every
later batch: as Leluxe, GAASH mail, Leads, Deposits and Tracking move, any test that greps
index.html for a control is measuring a shrinking file.

Also in that rebase: the branch's own 18 literal `font-size:` values became `--ds-t-*` tokens.
`lint-baseline.json` pins `font_size_literals: 0` after Batch B2, and a branch written before
it reintroduces literals silently — check the lint before merging anything long-lived.


## Batch F1 — the Leluxe ORDERS board (2026-09-09)

`static/ds/leluxe.js` holds `DS.lxOrders`; the orders branch of `renderLeluxe()` in
index.html is now a 16-line bridge passing a `ctx`. The LXT `""` table is **deleted** from
all three registries (`LX_TABLES`, `LXT_COLS`, `LXT_CLS`) and its `.lx-cols` grid rule is
gone. Four functions removed: `lxCardHtml`, `lxPkgHtml`, `lxVPkgHtml`, `lxOrphanRow`, plus
the tree branch of `lxItemRow` (its `{cols:true}` branch stays — the products board is F2).
213 lines out of index.html.

**Three levels, two grids.** The legacy board pushed order rows, parcel heads and product
rows through ONE column grid (`lxtCells("")`), which is why a product's tracking cell had to
be *blanked* when its parcel carried the number — it was standing in an order's column. Now
the order row lives on the DataTable's grid and each parcel's products live in their own
`DS.subTable`, so a product shows its own tracking, gash status, RD status and quantity
without pretending to be an order. The parcel head carries what belongs to the PARCEL: GWD,
count, thumbs, GAASH stage, deadline, documents, and its own ⋯ menu.

Measured on the migrated board (1400×900, admin, 157 orders / 232 products):

| | before (audit, post-Batch-A) | after |
|---|---|---|
| controls under 24px | 0 | **0** (of 1,695) |
| values truncated with no tooltip | 0 | **0** (94 truncated, every one has a tooltip) |
| distinct font sizes | 4 | **5** — 11 · 12.5 · 13.02 · 14 · 17, all DS steps (13.02 is `.ds-mono`'s .93em) |
| DS page header | no | still no — the Leluxe chrome (Tools menu, view switcher, filter builder) is F3's job |

**Capabilities kept** (diffed column by column against `LXT_COLS[""]` before the PR, the
check Batch B skipped): all nine columns at the same widths and labels, nothing
`defaultHidden`; per-column sort; the Σ totals footer (orders · products · Σ Total Amount);
the order ⋯ menu's eight actions; the parcel ⋯ menu; per-product inline status editing
(`.statussel`), quantity, tracking with the muted inherited number, gash + RD pills, due
chips; ＋ Add package / ＋ Add product / 🗑 hide; the "set tracking for all" bar on an
untracked group; the just-migrated 👁 banner; the search box and the ClickUp-style filter
builder (page chrome, untouched); LX_OPEN / LX_PCOL so open orders and open parcels survive
a re-render. The old `lx_sort` preference seeds the new table once, then the board persists
its own layout like every other DS table.

**Two bugs this migration made, both caught before the PR:**

1. **A test caught a lost capability, exactly as designed.** `test_gaash_docs_sent.py`
   counts the eleven upload buttons; my first draft of the product menu dropped
   "🪪 رفع مستندات لغاش · Upload docs" and "📄 فحص المستندات", which the legacy VIRTUAL parcel
   gave every product inside it. The count went 11 → 10 and the suite failed. A product now
   gets the customs actions on its own GWD **or the parcel's** — which is what it always
   inherited. *A wiring test that counts is worth more than a test that renders.*
2. **`lxShortName` returns escaped HTML, not text** (a muted `#` span + the last five
   digits). Escaping it again printed `<span class="lx-hash">#</span>…` on every row. Its
   sibling `lxShort3` returns PLAIN text and must stay escaped. When a legacy builder is
   reused from a page module, check whether it hands back text or markup — the two are one
   character apart in the call site and completely different on screen.

**Still on LXT:** products (`p`), packages (`k`), bulk search (`bs`) — F2 and F3. The shared
`.lx-colhead`/`.lx-cols` selector chains stay in index.html's CSS while those boards use the
same machinery; deleting the orders fragments out of eleven-way selector lists is churn with
real typo risk and no gain, so it happens when the last LXT board goes.


## Batch F2 — the Leluxe PRODUCTS board (2026-09-09)

`DS.lxProducts` joins `DS.lxOrders` in `static/ds/leluxe.js`; the products branch of
`renderLeluxe()` is a bridge. The LXT `"p"` table is **deleted** from all three registries
with its `.lx-pcols` grid rule, and `lxItemRow` — the last renderer either board used — is
gone. Its `LXT_SORT["p"]` entry stays seeded: the bridge still reads that preference.

**Two things this board has that no other DS board has, both kept:**

1. **Same-order tie runs.** Consecutive rows sharing a parent order render as one visual run
   — the order pill and ×N on the first, "└ نفس الطلب" on the rest, each still clickable
   back to the order. Adjacency is a property of the FINAL row order, so the **app sorts**
   (the same `LXT_SORT["p"]` the board always used) and hands the module rows in the order
   they will appear; `onSort` is passed precisely so the DataTable renders what it is given
   instead of sorting behind us. Sort by quantity and the runs mostly dissolve — that is
   correct, and it is what the legacy board did too (77 tie markers under the default sort,
   16 under quantity).
2. **Grouping by any field**, with collapsible sections. A DataTable has no group rows and
   two tables cannot share an `id`, so a grouped view is **one table per section**. Sections
   would drift apart on width/hide/order, so the layout lives in one canonical key
   (`ds_table_lxp`) that is copied into each section's key before it renders.

**The trap under that:** `DS.table` **reuses a registered table** and only calls `load()`
when it has no state — `TABLES[o.id] ? Object.assign(TABLES[o.id], {o}) : new Table(o)`.
Writing the localStorage key is therefore not enough for a section that has already
rendered once: hiding a column in one section reached exactly one other. Clearing
`t.state` (the component's own signal for "reload") before rendering is what makes the
shared layout real. **Remember this for any page that renders several tables of one kind.**

**Also fixed here, and it lands on every DS board:** `.ds-btn-icon` set only `width`, so a
flex parent could squeeze it — the table bar's density toggle measured **18×26**, under the
24px floor Batch A set. It now carries `min-inline-size` and `flex: 0 0 auto`. That was the
single undersized control on this board; with it, **0 of 11,069**.

Measured after (Leluxe products, on a copy of the live data — 234 products across 12 status
groups): **11,069 controls, 0 under 24px · 144 truncated values, 0 without a tooltip ·
4 font sizes (11 · 12.5 · 14 · 17), all DS steps.** The orders board re-measured 0 undersized
after the shared CSS change. The packages board is untouched and still legacy (181 rows) — F3.

**Capabilities kept:** all nine columns at their labels and widths, none hidden; the pinned
product column with thumb, clipped title and 📋 copy-full-title; the order pill / tie marker;
profile chain; inline status editing; quantity; tracking with 📦 — when absent; gaash status
and RD status pills; due chips; the ⋯ menu (edit · move to package · set tracking · check
shipping · **upload docs** · **check docs** · hide — the two customs actions are new here and
match what F1 gave a product inside a parcel); the Σ footer (products · Σ quantity) in both
the flat and grouped views; the group direction toggle and the "(none) last" section order.


## Batch F3 — the Leluxe PACKAGES board (2026-09-09)

`DS.lxPackages` joins the other two in `static/ds/leluxe.js`; `lxRenderPackages()` keeps the
row building (parcel rows, the estimated-value split, the orphan packages) and hands the
board over. The LXT `"k"` table is **deleted** from all three registries with its
`.lx-kcols` grid rule, and `lxKRow` with it. **`LX_TABLES` is down to seven entries** — on
this page only bulk search (`bs`) is still on the old engine.

**What did NOT move, on purpose:** the value formula. `lxOrderPkgEst` / `lxOrphanPkgEst`
still compute a package's estimate as Σ its priced products + an equal share of the order's
overhead, and the cell still prints the full derivation in its tooltip plus the `+N?` marker
for products with no individual price. A migration that quietly re-derived money would be
the worst kind of regression on this board.

**Row-level status editing keeps its rule** (owner's, from the packages-view work): a real
📦 subtask edits ITS OWN ClickUp task; a loose group of exactly ONE product edits that
product; a multi-product group stays read-only with a "mixed" pill, because one control must
never bulk-write N tasks.

Verified on a copy of the live data: **180 packages · 232 products · ≈ ₪78,756.16**, and the
Σ footer says the same. That is the same 180 the legacy board drew — the "181" a quick
`.poc-row` count gives includes the old totals row, which was itself a `.poc-row`. Sorting by
value re-sorted through `LXT_SORT["k"]` with all 180 rows intact; 172 inline status editors;
352 row menus. The ✉ mail column reads empty here because the snapshot carries **no**
`leluxe_pkg_mail` records at all — injecting one temporarily proved the cell still paints its
amber "waiting N days" pill.

Measured: **8,010 controls, 0 under 24px · 105 truncated values, 0 without a tooltip ·
4 font sizes**, all DS steps. Orders (157) and products (234) re-checked, unchanged.

**`test_design_system.py` caught a real slip:** the new `+N?` marker used
`var(--ds-warn-ink, #b45309)`. The suite forbids raw hex in `ds.css` — and the fallback was
hiding that **`--ds-warn-ink` does not exist**; the token is `--ds-warning-ink`. This is
Batch B2's trap #1 again (a `var()` with a fallback silently papering over a token that was
never defined). Write `var(--ds-warning-ink)` with no fallback and let the lint fail loudly.


## Batch G: the QA sweep's five defects (2026-09-09)

Not a migration — a walk of every staff page as a normal user, on a live-data snapshot,
after Batches A–F. Five defects, all of them things a migration left behind rather than
things a migration broke.

### 1. "Reset layout" showed the columns the page hides on purpose

`Table.reset()` set `hidden: []`. Every Columns dropdown has a **Reset layout** button, and
one click turned the Orders board from 12 columns into 19 and its row from 1166px into
2294px — twice the frame — with no way back but hiding seven columns by hand. It now
re-seeds `hidden` from `defaultHidden`, exactly as a first visit does in `load()`.

Verified: Orders restores its 7 defaults, Customers its 4.

### 2. The three Leluxe boards kept the pre-Batch-B2 column widths

`leluxe.js` inherited the legacy `LXT_COLS` pixel widths; `purchases.js` had been
re-measured for the type scale Batch B2 introduced. Like-for-like: `profile` 78 vs 128,
`due` 84 vs 106, `status` 100–118 vs 160. Because the widths are fixed pixels the damage was
identical at 1024, 1280 and 1440 — this was never a small-screen problem.

Widths are now set from measured content: the 90th-percentile cell and the header label,
plus the cell's padding. Sizing to the *widest* row would have made the boards absurd (one
`deadline` cell needs 287px), so genuine outliers still ellipsis — with a tooltip.

| board | truncated headers | truncated cells | unreadable (no tooltip) |
|---|---|---|---|
| Leluxe orders | 5 → **0** | 455 → **23** | 8 → **0** |
| Leluxe products | 5 → **0** | 763 → **31** | 0 → 0 |
| Leluxe packages | 4 → **0** | 594 → **63** | 0 → 0 |

The 8 unreadable ones were ClickUp statuses (`recieved no rd`) cut with nothing to hover:
`lxStatusPill` now carries its own text as a `title`. The same one-line defect on the
Purchases packages board (`62 days late`, 4 cells) is fixed by giving `pkgDatePill` a tip —
the promised date, which is more useful than repeating the truncated text.

**No default visibility was changed.** Widening pushes the Leluxe boards past the frame, and
hiding a column to buy that back is the owner's call, not a side effect of a bug fix.

### 3 + 4. Leluxe and GAASH mail: the tab strip was drawn twice, and went stale

`OWN_HEADER` lists the views that draw their own `DS.pageHeader`; `leluxe` and `gaashmail`
are not in it, so the shell drew a tab strip *and* the page drew its own. Under the new
layout the two disagreed — and on GAASH mail they were **in a different order**, so
Templates was the 4th tab in one strip and the 7th in the other.

Two causes, two fixes:

- `lxSetView` and `gmTab` never called `DS.shell2.syncTab()` (the four other tab-switchers
  do). They do now.
- `syncTab()` itself only called `syncHash()` — which sets `applying = true` so its own
  `hashchange` is ignored, so `paint()` never ran and the strip kept highlighting the tab
  you left. `syncTab()` and `S.tab()` now repaint. **This was the real bug**; the two
  missing call sites only made it visible.

The page's own strip is hidden under `body.ds-shell-on`, the same way the legacy sidebar and
top bar are — the shell owns navigation. It needs `!important`: both strips carry an inline
`display:inline-flex`, which a stylesheet rule cannot beat. The classic layout still shows
the page's strip, untouched. `TABS.gaashmail` is reordered to match `#gmTabs`.

### 5. Customers got its ★ VIP column back

Batch B kept every VIP *capability* — the tag in the name cell, the header count, the row
menu's "Mark as VIP" — but dropped the **column**, so the list could no longer be sorted or
scanned by it, and it was not in the Columns dropdown to bring back. It is a column again,
sortable, second from the left, with the old click-to-toggle star. `sortVal` scores a VIP
as `1` exactly as the old board did, so descending puts them on top like every other column.

The star is a real target (26px, `role="button"`, `aria-pressed`, Enter/Space) rather than
the old bare glyph with an `onclick`. The name-cell tag stays: it is what still says "VIP"
when someone hides the column.

**Trap for the next batch:** `test_design_system.py` requires every `ds.css` selector to
contain `.ds-` and forbids emoji anywhere in `ds.css`. `.cu-vip` and a `★` in a comment both
failed it. Name it `.ds-vip`, and keep the star out of the stylesheet.


## Batch H: the Leluxe workspace, and four controls that had stopped responding (2026-09-09)

The owner's report was two sentences: *"in the leluxe workspace we need to fix the page,
only keep the ones we use in leluxe — we do not have customers. Leluxe is a special case; if
I added another workspace it would be normal"*, and *"I tried pressing a button in Leluxe and
nothing happened"*.

### The workspace was a label, not a mode

`S.workspace()` was derived from the current view:

```js
S.workspace = () => (A().platform ? "Tatabu" : A().view === "leluxe" || A().view === "goals" ? "Leluxe" : "Otlobly");
```

So the switcher read "Leluxe" while the menu above it was the full Otlobly nav — Leads,
Customers, Orders, Fulfillment, Deposits, P&L — and it forgot itself the moment you stepped
anywhere else. Leluxe is a live mirror of one ClickUp list with **no customer, quote, deposit
or cash collection** (AUDIT.md §Leluxe), so most of that menu is noise inside it.

It is a stored setting now (`otl_ws`), and the sidebar has a third branch built the way `PLAT`
already builds Tatabu's. Chosen by the owner: **Leluxe · Tracking · GAASH mail · Goals ·
Activity** — the pages that actually touch Leluxe data (bulk search covers Purchases *and*
Leluxe; `gaash_mail._SOURCES = ("leluxe","purchases")`; the Goals campaign counts the Le Luxe
list). Overview and Needs attention sit it out, as they already do in platform mode.

**`NAV` is untouched on purpose.** `test_ds_shell.py` regex-scans the *whole file* for
`{ key: "…", label: "…", items: [` and requires exactly five groups in flow order, so a second
literal of that shape anywhere in `shell.js` fails the suite. `LX_GROUPS` stores keys and
resolves them against `ALL` at render time — same trick as `PLAT`, and it keeps every
role/feature gate (`FEAT.leluxe`, `admin_actions`) in the one place `visible()` already owns.

`syncWs(view)` hangs off `paint()`, the one hook every route change passes through, so the
sidebar can always reach the page you are looking at: `leluxe` forces the Leluxe workspace,
an Otlobly-only page forces Otlobly, and the pages the two share — plus Settings, Team, Trash,
Flags, Needs attention — leave it alone. Without that, a deep link to `#/sales/customers` left
a Leluxe sidebar with no way back to the page on screen.

`render()`'s `sig` gained the workspace. It is a JSON signature that short-circuits the
repaint, so without that line the groups changed and the sidebar never redrew.

### The four dead controls — three of them Batch F1 regressions

None of this was a missing function: all 143 handlers in the Leluxe region resolve. It was
wiring that F1 left behind when the board moved onto the DataTable.

1. **Product rows inside an expanded order did nothing.** `leluxe.js` sets
   `_click: lxInfoOpen('item',ID)` on every row, and `DS.subTable` never read it — the
   property appears nowhere else in the repo. The rows still highlighted on hover, so they
   advertised a click they did not have. `DS.subTable` now honours `_click` with the same
   `closest('select,.pop,.caret,button,a,input,label,img')` guard the pre-F1 markup used, and
   the hover highlight is scoped to `.is-clickable` so the other boards stop making the same
   promise. No `tabindex`: the row is `display:contents`, generates no box and is not reliably
   focusable — the row's own ⋯ menu carries the actions for the keyboard.

2. **⊞ / ⊟ silently failed on any row you had touched by hand.** `lxExpandAll` resets
   `LX_OPEN`, but `Table` keeps its own `open`/`closed` sets and `DS.table` **reuses the
   instance**, so `this.open.has(k)` outlived every re-render and won. Expand a row by chevron,
   press ⊟ — nothing. It worked after a page load, which is why it read as flaky. New
   `DS.tableResetOpen(id)` drops those overrides so `expandable.open(row)` is the only truth
   again; `lxExpandAll`, `lxJumpOrder` and `lxViewMigrated` all call it. `lxExpandAll` also
   read `LX.orders` with no null guard.

3. **"Jump to order" never scrolled.** Both callers did `getElementById("lxo"+id)`; F1
   replaced those `<div class="po-card" id="lxo…">` cards with DataTable rows keyed by
   `data-key`. Clicking an order pill on Products or Packages switched tab and left the page
   exactly where it was. One `lxRowEl(id)` helper queries `#dst-lxo .ds-tr[data-key="…"]`.

4. **`⬇ Migrate from AZ (2)` had lost its menu entry** while `lxMigrateAsk()`, its hidden
   `#lxMigrateSince` input and two empty states that name the button were all still there. Put
   back next to `🔄 Sync from AZ (2)` rather than deleting the strings that promise it.

### Chrome that belonged to a board, not to every tab

The panel header is one piece of static markup shared by all five tabs, so it said
**"📦 Orders"** on the Goal tab and offered ⊞/⊟, a search box and a filter builder that tab
reads nothing from. `lxChrome()` now names the tab you are on and shows only what acts: ⊞/⊟
on Orders (only that board reads `LX_OPEN`), search and filters everywhere except Goal — the
Board keeps both, because it *is* fed `lxFilteredOrders()`.

The Goal `↻` also blanked the view: it set `LX_GOAL=null` and then called `lxGoalFetch(true)`,
which returns at its busy guard if a fetch is already in flight — so the refresh was dropped
*and* nothing would clear the "Loading the goal…" it had just caused. `lxGoalRefresh()` checks
busy first.

**Left for its own task:** `CAN_EDIT` is never consulted anywhere in the Leluxe view, while
Purchases, Orders and Package prep all gate on it. A read-only user would see every editor,
every ⋯ action and every Tools item enabled. Low risk today — `leluxeBtn` is gated on
`admin_actions` — but it is a real gap.
