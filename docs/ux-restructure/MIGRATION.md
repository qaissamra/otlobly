# MIGRATION.md — route and component checklist (UX restructure)

Status values: `not started` · `in progress` · `migrated` · `deleted` · `removal proposed` (awaiting owner approval, brief §4 rule 2).
Update this file in **every** PR of the restructure. "Screens" = `screens/before/<name>.jpg` captured in Phase 0 by `tools/screenshots.mjs`; the "after" column is filled when a row is migrated (English only, per the 2026-09-06 owner decision).

## A. Views (today's `setView` ids) → target template

| View | Today | Template | Target home (brief §6, D-decisions pending) | Screen (before) | Status |
|---|---|---|---|---|---|
| `brain` | 🧠 Brain landing | T4 | Overview (D5) + feeds Needs attention | `brain.jpg` | not started |
| `purchases` (orders tree) | 📦 Purchases | T1 | Fulfillment › Purchases (reference page, Phase 3) | `purchases-orders.jpg` | not started |
| `purchases` › packages | 📦 Packages sub-view | T1 | same page, saved view | `purchases-packages.jpg` | not started |
| `purchases` › products | ⫶ Products sub-view | T1 | same page, saved view | `purchases-products.jpg` | not started |
| `purchases` › customers | 👤 Customers sub-view | T1 | same page, saved view | `purchases-customers.jpg` | not started |
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
| `flags` | 🚩 Flags | T1 | Needs attention (open flags) + Settings › Integrations (inboxes) | `flags.jpg` | not started |
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
| bare `button{}` + `.primary` + `.accent` | CSS ≈L54–58 | `Button` | merge | not started |
| `.po-btn` (+`.accent`, `.danger`) | CSS ≈L241 | `Button` (secondary / primary / danger) | keep as base | not started |
| `.minibtn` (+`.danger`) | CSS ≈L287 | `Button sm` | merge | not started |
| `.iconbtn` · `.qchip` · `.chip` | CSS ≈L235 / 267 / 330 | `Button icon` · `Tag` · `Tabs` | merge | not started |
| LXT table engine (`LX_TABLES` L3594, `LXT_COLS` L4045, `lxtHead` L4452, `lxtCells` L4497) — 15 tables | JS L4045–4955 | `DataTable` (+ `ColumnConfig`, `RowExpansion`) | keep as canonical, extend (bulk bar, sticky end columns, keyboard) | not started |
| `neTable` / `NE_COLS` (To order) | JS L7385–7400 | `DataTable` | delete after migration | not started |
| 20 raw `<table>` (P&L ×7, Settings ×3, platform ×3, Team, Activity, Picking, Deposits-by-customer, To order, GM templates, GM analyze) | markup + JS | `DataTable` | delete | not started |
| `.pill` rule pair (L287 vs L963) + `statusPill` L2443 · `tonePill` L9578 · `hexPill` L9581 · `solidPill` L9588 · `gaashBucketPill` · `lxStatusPill` · `lxCfPill` + 12 domain builders + 48 raw literals | JS/CSS | `Badge` + `AttentionBadge` behind `status.js` | merge (one rule, one helper) | not started |
| `fld()` L2449 (two CSS homes: `.po-meta .field`, `.ne-meta .field`) | JS/CSS | `Stat` / meta strip | keep, one CSS home | not started |
| `editCell()` L2462 | JS | `DataTable` inline edit | keep | not started |
| `openStore()` L2487 | JS | `RowExpansion` state | keep | not started |
| `.po-card` two-tier header · `.ne-metarow` · `.poc-meta` | CSS | `PageHeader` / row header + `Stat` | merge (one meta strip) | not started |
| `.az-modal` ×22, each with its own open/close pair | markup L1803–2023 | `Modal` / `DetailDrawer` / `ConfirmDialog` (one controller: focus trap, Esc, aria) | merge | not started |
| `.pop` / `.pop-menu` + `popMenu()` L10186 / `popToggle` | JS/CSS | `DropdownMenu` | keep, add keyboard | not started |
| `toast()` L2065 (single slot, no variants, 379 calls) | JS | `Toast` (variants, queue, `role=status`) | keep, extend | not started |
| `.empty` (21) + 475 ad-hoc `muted2` empty states | CSS | `EmptyState` | merge | not started |
| `#pageTitle` + global `#sub` (L1035, written at L2362) + 44 `<h2>` + 21 `.toolbar` | markup | `PageHeader` (breadcrumb, stats, one primary) | merge | not started |
| tabs ×3 (GAASH mail 8 tabs, Purchases 5-segment, quote chips) + `.chips` rows | markup | `Tabs` | merge | not started |
| search boxes ×5 (2 `.search`, 3 inline) + 14 `.cu-search` + Purchases filter builder | markup/JS | `FilterBar` (+ `Combobox`) | merge | not started |
| emoji icons (2,168 glyphs, 191 distinct) | everywhere | inline SVG sprite (D3) | replace | not started |
| formatters `money` ×3 (L2053, 4765, 10612) · `money0` · `fmt` · `cfNum` · `lxGm` · `relTime` L7199 · `agoTxt` L10055 · `gmAgo` L11572 · `fmtDue` L9563 · `lxDate` L3252 · `cfFmtDate` L9373 | JS | `formatMoney / formatNumber / formatDate / formatRelative` (Intl, `en-US`) | merge | not started |
| 13 colour registries (`STATUS_COLOR` L2042 … `GM_STATE` L11542) | JS | `status.js` registry | merge | not started |
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

## F. Phase checklist

- [x] **Phase 0** — audit and plan: `BRIEF.md`, `AUDIT.md`, this file, `tools/inventory.py`, `tools/screenshots.mjs`, `screens/before/`, test baseline 51/51. *Waiting for owner approval.*
- [ ] **Phase 1** — foundations (tokens, icons, primitives, `AppShell`/`PageHeader`/`FilterBar`/`DataTable`/`Modal`/`DetailDrawer`/`FormLayout`/`ImportWizard` shell, `status.js`, formatters, `/design-system`, `DESIGN_SYSTEM.md`, lint at warn).
- [ ] **Phase 2** — shell and navigation (grouped sidebar, top bar, hash router, Needs attention, workspace switcher).
- [ ] **Phase 3** — Purchases on T1 (reference; brief §14 list).
- [ ] **Phase 4** — GAASH mail, To order, Package prep, then Orders, In cart, Customers, Leads, Deposits, Tracking.
- [ ] **Phase 5** — details, forms, modals.
- [ ] **Phase 6** — imports onto `ImportWizard`; delete old importers.
- [ ] **Phase 7** — insights, cleanup, lint to error, keyboard pass, before/after gallery.
