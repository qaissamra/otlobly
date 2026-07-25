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

## 2. ClickUp-table system (ALL boards — the `.bt-*` chrome + LXT registry)

Every board is a ClickUp-style table at EVERY width: fixed-width column grids
that H-SCROLL inside `.bt-wrap` (they never squeeze or collapse), a PINNED left
column (`.bt-pin`, sticky-left, shadow via `.btscrolled`), a sticky header in a
clipped strip (`.bt-clip`) whose scrollLeft `lxtSync(wrap)` mirrors (clip =
`wrap.previousElementSibling`, no ids), per-column sort, an ⊕ show/hide-columns
picker, and Σ totals footers (`.bt-total`). There is NO responsive fallback and
NO container queries anymore.

Registries (web/index.html): `LX_TABLES` (grid var + header selector + width
storage key + statussel mins) and `LXT_COLS` (per table: `{key,label,w,
sortable|sort,locked}`) for ids `""` Leluxe orders · `"p"` products ·
`"k"` packages · `"po"` Purchases. Per-table localStorage: widths
(`lx_colw`/`lx_pcolw`/`lx_kcolw`/`po_colw`), hidden set `lxt_hidden_<id>`,
generic sort `lxt_sort_<id>`. The ORDERS table keeps its richer `LX_SORT`
(`lxSortClick`/`lxSortVal`; a col's `sort` field carries that key + optional
`sortKey`), others use `lxtSortClick`/`lxtSortApply`.

View assembly: `lxtHead(tbl, pinLabel[, pinSortKey])` + `lxtWrap(tbl, rowsHtml
+ totals)`; after `mount.innerHTML=` call `lxColwApply()` THEN
`lxtGridApply(tbl[, mount])` (writes saved widths when the array length matches
the VISIBLE column count, else per-col defaults).

Row recipe (any nesting level): `<div class="bt-pin">…left content…</div>` then
`lxtCells(tbl, {colKey: html, …})` — missing keys render empty. Nested tree
levels indent INSIDE the pin (CSS `.bt-wrap .po-body .pkg-title-row>.bt-pin`
24px / `.poc-row>.bt-pin` 34px) so every grid stays column-aligned. Pin widths:
`--btpin` via wrap/clip classes `pin-lg` (orders 380) / `pin-md` (po 360) /
default 320; capped 200px under 560px viewport.

Pitfalls (all hit us once):
- ancestor `overflow:hidden` (`.po-card`, `.pkg`) hijacks the pin's sticky
  scroll box — `.bt-wrap .po-card,.bt-wrap .pkg{overflow:visible}` undoes it;
- the pin floats over scrolled content → its BACKGROUND must mirror every row
  state (open `#fff4ee`, pkg strip `bg2`, hovers, `.lx-tied` stripe) — see the
  `.bt-pin` background rules;
- header vs rows width can differ 1-2px per nested border level — the header's
  `border-inline:1px solid transparent` + `lxtSync`'s end-snap absorb it;
- `.lxc:has(.pop-menu.open)` + `.bt-wrap:has(.pop-menu.open){overflow:visible}`
  keep dropdowns unclipped;
- hiding a column resets that table's saved widths (visible-set keyed).

Plain wide `<table>`s elsewhere (staff Orders `#tbl`, Customers, Deposits,
Catalog, Team, Trash, In cart, Activity, Picking) sit in `.tbl-scroll`
wrappers (`overflow-x:auto`) — scroll, no pin.

Later additions to the system:
- **UI language toggle (staff console only)**: `LANG` (`otl_lang`, default `en`)
  + a 🌐 `#langToggle` in the top bar (`langToggle`/`langSet`). Labels are written
  bilingually `"عربي · English"`; `T(str)` splits on the first ` · ` and returns
  ONE side by LANG (leaves composed strings whose two sides are the same language,
  e.g. "3 pkgs · 4 items", untouched). EVERY column label — `LXT_COLS`, the pin
  labels passed to `lxtHead`, the ⊕ Fields picker, sort tooltips — is bilingual and
  rendered via `T()`, so a column never mixes languages. Static chrome (sidebar
  nav, `nav-sec` labels, Purchases view tabs, top-bar buttons) carries
  `data-en`/`data-ar`; `localizeStatic()` swaps their text (preserving a leading
  `.ic` icon span) at boot + on every toggle, which then re-renders the live
  boards. Customer-facing `templates/*` are NOT touched — English default keeps
  the whole staff UI one language out of the box; the toggle re-Arabizes it.
- **Column reorder + right-click hide** (all `.bt-*` tables): headers are
  `draggable` (native HTML5 DnD → `lxtColDragStart/Over/Drop/End` → `lxtColMove`)
  and reorder MOVABLE columns only; the locked ⋯ `menu` stays last and the pinned
  first column never moves. Order persists to `lxt_order_<tbl>`, applied by
  `lxtOrderApply` inside `lxtCols` so header/cells/grid all follow one ordered list
  (unknown/new columns keep their default trailing spot). Reordering resets that
  table's positional widths (`lxColwSet(tbl,null)`), same as hide. Grabbing the
  `lx-rsz` handle resizes instead (guarded in DragStart; handle is `draggable=false`).
  Drop indicator = `.lx-drop-l/.lx-drop-r` inset shadow; `.lx-col{cursor:grab}`.
- **Right-click column menu** (`lxtColMenu(ev,tbl,key)` — ClickUp-style; replaced the
  earlier instant-hide): the header `oncontextmenu` opens a cursor-anchored,
  `position:fixed`, body-appended menu (escapes `.bt-clip` overflow:hidden; positioned
  from `ev.clientX/clientY`, viewport-clamped). Rows (all via `T()`, N/A rows omitted +
  dividers collapsed): Sort ascending / Sort descending (`lxtSortSet(tbl,key,dir)` —
  explicit dir, NOT the cycling `lxtSortClick`; the "" orders table uses
  `lxSortSet(key,dir)` on `LX_SORT`) · Edit field (cf_ + admin → `poCfFieldOpen(key.slice(3))`)
  · Move to start / Move to end (`lxtColMove` to first/last movable key; the
  "already first/last" row drops out) · Hide column (`lxtColHide`, restore from ⊕). One
  menu at a time (`LXT_MENU`); persistent doc listeners close it on outside-mousedown /
  scroll / resize. `.pop-sep` divider + `.lxt-ctx` class; rows reuse `.pop-item`.
  Left-click still sorts; drag still reorders.
- **Pin resize**: the header pin carries an `lx-rsz` handle → `lxtPinDown` /
  `lxtPinReset`, width in `lxt_pin_<tbl>` (clamp 220–560), re-applied by
  `lxtGridApply` as an inline `--btpin` on `.bt-clip`/`.bt-wrap` (skipped ≤560px
  so the 200px phone cap wins).
- **Purchases views**: `PO_BOARD_VIEW` (`po_board_view`) — orders tree ·
  packages flat (table `"pok"`) · products flat (`"pop"`) · customers
  (`poRenderCustomers`); `poSearch` free-text filter (`poSearchMatch`);
  `poJumpOrder` = back to tree + open that card.
  **👤 Customers group header = TWO TIERS** (since 2026-07-25). A group header
  row is pin + `lxtCells` like every other one — never stuff meta into the pin,
  it is `flex:0 0 var(--btpin)` + `overflow:hidden` and silently CLIPS the
  overflow (that bug hid the status + money chips here for weeks). Line 1: pin =
  👤 name only (`.pkg-title.cust` ellipsizes, full name in `title=`), cells =
  `collect` Σ quoted · `ostatus` status pill (or "N orders") · `qty` Σ. Line 2 =
  `.poc-meta` strip (📦 counts · ☎ wa.me phone · 📍 city · 🏠 address · 🪪 ID ·
  عربون · ≈ cost), rendered open AND collapsed, empty fields omitted; its `.in`
  is `position:sticky;inset-inline-start:0` so it survives horizontal scroll
  (same trick as `.poc-cust`). Contact data = `poCustInfo(cust,ords)`:
  `PO_CUSTMAP` (built in `loadPurchases` from the `/api/customers` call that was
  ALREADY being made and discarded — keyed by both `normName` and
  `phoneCoreJs`), falling back to the matched customer orders
  (`phone`/`wa`/`address`/`id_number` — but NOT `city`, which `report._row`
  omits) so a role without `view_customers` still gets contact info. No API/DB
  change. `ostatus` is also a real `pop` column (order-level data on a product
  table — same precedent as `collect`/`idnum`).
- **🔎 Bulk search view** (`bulksearch` / `#bulkSearchView`, nav `bulkSearchBtn`):
  paste many GWDs → a full LXT ClickUp table (**table id `"bs"`**, pin = tracking
  #) locating each in BOTH data sets — Purchases (`bsFindPo`: exact
  `pk.tracking_number`, PO chip → `bsOpenPo` = setView+poJumpOrder, pkg status via
  `cuLabel`/`hexPill`, `pkgGaashPill`, `pkgEstTotal` $ gated by PO_MONEY,
  `poThumbStrip` images, `poProfileCell`) and Leluxe (`bsFindLx` over
  `bsLxRows()`: dedupes package+item matches, Σ item "total amount" ₪,
  `lxStatusPill` + `lxCfPill("gash status",…)`, `lxThumbs` images, profile =
  NAME field, gash date = `lxDueChip(v,true)||lxMs(v)`). Columns: found/oname/
  profile/imgs/cust/status/gash/gashdate/value — the shared machinery (⊕ picker,
  drag-reorder, right-click menu, resize, sort) applies because "bs" is
  registered in LX_TABLES/LXT_COLS/LXT_CLS/LXT_PINCLS/the init array + every
  `.bs-*` CSS selector group; `lxtRender("bs")` → `bsRender()` re-renders the
  CACHED `BS_LAST` models (no re-search). Sort = `bsSortVal` (numeric keys
  stringified zero-padded so mixed rows compare; missing rows always last).
  `bsEnsureData` lazily loads POS via `loadPurchases()` and LX via a bare
  `/api/leluxe/orders` fetch (NOT `loadLeluxe()` — view-coupled); non-Leluxe
  roles just get no LX matches. Not-found rows amber (pin bg inlined too);
  footer Σ found/missing + $ + ₪. All labels via T()/data-en|ar.
  **Adding a new LXT table checklist** (what "bs" needed): LX_TABLES entry
  (key/varn/head/rows) · LXT_COLS · LXT_CLS · LXT_PINCLS · the
  `["","p","k","po","pok","pop","bs"]` init array · `lxtRender` branch · CSS:
  `.X-cols{display:grid;…var(--Xgrid,defaults)}` + append `.X-colhead`/`.X-cols`
  to the 7 shared selector groups (lines ~201-202, 428-429, 432-435, 438).
- **Name / profile / due columns** (po/pok/pop): `oname` = the order's Main name
  (`p.ship_to`), `pcust` = a package's item customers (`pkgWho(pk)` / `poWho(p)`
  at PO level) — plain via `poNameCell`; `profile` = the AZ/Multilogin box the PO
  was placed under (`p.profile_box`) — a compact 🖥 chip via `poProfileCell`;
  `pdue` = per-package due date via `poPkgDueCell`/`poPkgDueEdit` (a `dueChip` that
  becomes a date input; NOT `lxDueChip` — that wraps `lxMs` for ms-epoch and returns
  "" on a YYYY-MM-DD string). `pdue` is `pk.due_date` if set (preserved in
  purchases.py `_norm_packages`), ELSE INHERITED from the linked customer order's
  promised delivery `est_delivery_customer` (earliest across the pkg's items'
  `customer_order_id` → `poOrderMap()`) — `poPkgOrderDue(pk)` / effective
  `poPkgDue(pk)`; inherited shows muted (opacity .7), editing overrides. Typed sort
  in each view's valFn + `poSortVal` all use `poPkgDue`.
- **Custom fields v2 (ClickUp-style, sellable core)**: 13 types — select
  (dropdown), labels (multi), text, longtext, number, money, date, checkbox,
  phone, url, email, rating, progress. Defs per business `custom_fields.po`
  (settings.py `_clean_custom_fields` validates per-type config + slugs the
  immutable key + 20 cap): select/labels→`options:[{name,color}]`, money→
  `currency`+`precision`, number→`precision`, date→`include_time`, rating→
  `icon`+`count`, progress→`min`/`max`. Values in `po.custom{}` (save_full
  preserves; custom-less POST never wipes). Client catalog `CF_CATALOG`;
  `lxtCols(tbl)` appends `cf_<key>` cols to "po"/"pok"; render by type in
  `poCfDisp` (colored pills via hexPill, money currency sym, ★ rating, progress
  bar, url/email/phone links), edit in `poCfEdit` (select/labels→`poCfPickOpen`
  colored pop-menu, checkbox→direct `poCfSet` toggle, rating→inline stars, rest→
  typed input) → saves via debounced `poSave`. Sort typed in `poSortVal`.
  **Creation**: `poCfFieldOpen()` (admin only) = the ClickUp create card
  (bulk modal: name + `CF_CATALOG` type picker + per-type options editor with
  color swatches) → POSTs the whole `custom_fields.po` list to `/api/settings`.
  Entry points: ＋ New field row + ✎ pencils in the ⊕ Fields panel (`lxtHead`,
  gated `canCf`), and the Settings → 🧩 list (`cfRender`). Never send
  custom_fields from generic `saveSettings` — the card owns that key.

## Role-based visibility (employee-safe by design)

- **Money boundary = `view_cost`** (admin only): /api/purchases, /api/purchase,
  /api/incart (cost+profit; totals additionally need `view_money`),
  /api/po_image (checkout screenshots) all redact server-side; payloads carry
  `money:false` so the UI (PO_MONEY, renderIncart) skips money cells/footers.
  Settings GET is trimmed for non-`admin_actions` (pricing keys only return
  when the caller has `view_money` — sales quoting needs them).
- **Status labels are per-role**: admins see the raw ClickUp vocabulary; other
  staff see Settings → 👷 `employee_status_map` labels via `cuLabel(s)`, and
  their dropdowns (`pkgCuSelect`, `ppStatusPicker`) list only `pick:true`
  statuses via `cuPickRows`. Display-only — stored values never change.
- Nav for non-money roles: `cartBtn` needs `view_money`, `settingsBtn` needs
  `admin_actions` (plus the older gates: P&L, Deposits, Leads, Leluxe, Team).
- Tests: `test_role_money.py` locks all of this — run it after touching any
  money-bearing endpoint.

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

## 4b. Customer ID number + Request-ID self-service (2026-07-24)

Distinct from the ID *image* (`customer.id_image`, customer_ids/) and the GAASH
mail ID library (`gaash_ids`, the `{id_name}` token): a customer's **ID NUMBER**
(a string). Per-customer (submitted once, reused across all their orders).
- **Store:** `customer.id_number` (customers.py new_customer + upsert preserve
  guard; JSON blob, no migration). Source of truth = CRM, keyed by phone.
- **Surface:** `_attach_customer_ids(rows)` (app.py) enriches order rows
  (`id_number`/`has_id_number`, by phone) for BOTH /api/report + /api/needorder;
  Purchases 📦/⫶ "ID number" column (`LXT_COLS.pok/pop` + `idNumCell`/`pkgIdNumber`
  via `poOrderMap()[it.customer_order_id].id_number`); To-order 🪪 badge + detail;
  customer profile + ID gallery (`custIdNumberEdit` → `/api/customer/id_number`).
- **Request-ID link:** To-order ⋯ menu → 🪪 Request ID (`neRequestId`) →
  `POST /api/order/request_id_link` mints a single-use `idreq:<token>` (mirrors
  the 🔗 quote-link) → copy + wa.me. Public no-login page `GET /id/<token>`
  (`templates/submit_id.html`), hydrate `GET /api/idreq/<token>`, submit
  `POST /api/id/submit` (token-gated, single-use, number required, optional 15MB
  image → customer_ids/, stamps the order `id_submitted_at`). Auto-attaches to
  ordered products via the existing `it.customer_order_id` link — no new plumbing.
- **Email token:** `{id_number}` in GAASH templates — `_id_number_for(gwd)`
  resolves the package's customer (Purchases order link → phone, else Leluxe phone
  field, else name) → CRM `id_number`. Registered in `TPL_CORE_TOKENS`.
- Tests: test_customer_id.py (16 checks).

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
- **📧 GAASH Mail page** (`gaashmail` view, `gaash_mail.py`) — the AUTOMATED
  version: one chat per GWD, 4-email templated sequence (cadence default 2·2·2d,
  Settings `gaash_mail`), real Gmail SMTP+IMAP via app-password accounts (engine
  ported from ~/gaash-clickup-sync/support.py — auto-ack window rule, office-
  closed resend at 9:00 IL, UID-cursor inbox polling). Tables gaash_accounts/
  gaash_ids (reusable ID library, files data/gaash_ids)/gaash_threads/gaash_msgs
  (attachment bytes data/gaash_mail/<gwd>). Sequencer guards in order: package
  cleared/delivered (mirror `_bucket` — dict OR legacy string values +
  GASH-STATUS-field DELIVERED) → real reply pauses (waiting_reply) →
  missing_docs pauses (KMT keyword auto-flag from reply text) → dry_run (default
  ON, never burns the claim) → daily cap → `db.claim_once("gaashmail:{gwd}:stepN")`
  (released on failed send). Routes /api/gaash/* — view/send `edit_fulfillment`
  (NOT edit_order — fulfillment lacks that), accounts/IDs/test_send
  admin_actions, feature leluxe. Bell events (gaash_reply/missing/exhausted/
  cleared → view gaashmail) read fresh in api_notifications. ⚠ OPERATIONAL RULE:
  daemon gated env `GAASH_MAILER=1` set ONLY on Render (live DB) — NEVER in the
  Mac plist/local .env (stale DB ⇒ double-sends); this is the INVERSE of
  LELUXE_DIGEST. test_gaash_mail.py locks all of it.
  **v2 (HubSpot-style, sequences-as-DATA):** page tabs 💬 Conversations · 🧬
  Sequences · 📝 Templates · 📊 Dashboard. Tables gaash_templates (library,
  delete blocked while referenced) / gaash_sequences (per-seq to_address = ANY
  platform, goal cleared|reply|manual, send_window_json {tz days start end} —
  default Sun–Thu 09–17 **Asia/Hebron "Palestine time"**) / gaash_steps (pos,
  kind auto_email|task, template_id, delay_days = BUSINESS days) / gaash_rules
  (auto-enroll → seq, mode queue|auto; queue ⇒ state='proposed', approved
  ONLY from the Workflows table's per-row expansion) / gaash_events
  (open/click hits). **Trigger criteria (HubSpot-style,
  2026-07-24):** cond_json v2 `{"groups":[{"crits":[{field,op,value}…]}…]}` —
  crits AND inside a group, groups OR; legacy {gash_status,min_age_days}
  auto-converts via `_cond_norm` (also the sanitizer: unknown field/op
  dropped, age clamped 0-365, ≤5 groups × ≤8 crits). Field registry
  `RULE_FIELDS`: gash_status/status/bucket/label/name/customers (text: is,
  is_not, contains, not_contains, empty, not_empty — `_fold` case+space),
  source (enum leluxe|purchases), age_days (gte/lte — `_cand_age_days`:
  leluxe date_created ms, purchases PO created_at ISO; None fails closed).
  UI `gmRuleOpen` builder modal (datalists fed from GM.ov.candidates,
  value input morphs by type), ✎ `gmRuleEdit` pre-fills, `gmRuleSummary`
  renders the strip line. **⚙️ Workflows page (2026-07-25, HubSpot Automation
  layout):** tabs 💬 Conversations · 🧭 Overview · ⚙️ Workflows (was Sequences)
  · 📝 Templates · 📊 Analyze (was Dashboard). Workflows = an lxt TABLE
  (`LXT_COLS.wf`, registered across LX_TABLES/LXT_CLS/LXT_PINCLS/init/lxtRender
  + .wf-cols/.wf-colhead CSS): onoff switch · trigger + ⚡chip · steps/days/
  goal/active · enrolled/enr7d/goalmet/sent/open%/reply% · description · ⋯.
  Search + All/On/Off/has-trigger quick filters (GM_WF), hover ✎ ⧉ actions.
  On/Off = `gaash_sequences.paused` (ALTER + description): run_once guard 0
  skips paused-seq threads BEFORE claim (nothing lost), run_rules skips their
  triggers; `sequence_toggle`/`sequence_clone` (clone starts Off) via
  /api/gaash/sequence/toggle|clone (admin). ⚡ match counts: `rule_matches`/
  `rules_match_map` (one candidates scan) via GET /api/gaash/rules/matches +
  POST /rules/preview (modal's debounced live count `gmRulePreviewKick`);
  chip click → `gmMatchesView(gwds)` = Bulk search prefilled (full columns).
  `stats()` has enrolled_7d; builder shows a stats strip + description input;
  Overview tab `gmOvRender` tiles. Caches GM.stats/GM.matches cleared each
  gmLoad, lazy-fetched by `gmWfEnsureData`. **Per-row enrollments expansion
  (2026-07-25):** the top "suggested enrollments" banner (#gmProposed, shown on
  every tab) is REMOVED — its 6-vs-ENROLLED-1 mismatch confused the owner.
  Instead each wf row has a ▸ `.caret` in the pin + a clickable ENROLLED cell
  (real count + amber `⚡ N` chip = this workflow's proposed GWDs via
  `gmWfPropOf(seqId)` grouping `GM.ov.proposed` by seq_id). Toggle
  `gmWfExpToggle` (state `GM.wfOpen` Set, survives re-renders) appends a
  `.wf-exp` block after the row; `gmWfExpRender` fills it with bulk-search
  full-columns tables (`bsModelRow`) in two sections: enrolled (state chip +
  💬 opens the conversation) and suggested (✓/✕ per GWD `gmPropAct` +
  per-workflow approve-all/dismiss-all `gmPropAllSeq`). `.wf-exp` is a
  client-width block inside the h-scrolling wf wrap — sticky can't pin it
  (parent = element width), so gmRenderSeq translates it by wrap.scrollLeft
  on scroll. Open row = `.wf-openrow` (#fff4ee, pin bg mirrored). Approve =
  POST /api/gaash/thread action approve (start_threads into the thread's own
  seq_id) — test_gaash_mail.py locks the approve/dismiss/re-propose loop. **Surfaced in the sequence builder (2026-07-24):**
  `gmRenderBuilder` shows an ⚡ Enrollment-trigger card at the top (HubSpot
  "when this happens") listing rules where seq_id=this seq, ＋ New trigger
  `gmRuleNew(seqId)` pre-sets enroll-into; `gmAfterRule()` re-renders builder
  or list. `?` help chips (`.gm-q`/`gmHelp`) on every control (field ? is
  dynamic). **Board custom columns as criteria (2026-07-24):** field
  `cf:<label>` filters ANY board column — candidates() attach a `cf` map
  (Purchases PO custom values by label + Leluxe ClickUp `fields`), catalog
  `rule_cf_fields()` (📦 defs + ⌚ leluxe names) ships in overview as
  `cf_fields`; client `gmRuleFieldList()` = built-ins + cf (source badge);
  `_field_kind`/`_cand_cf_val` make cf:* generic text; safe datalist ids
  `gmDlId`. Field picker is a type-to-search datalist combobox
  (`gmRuleFieldPick`, shared `gmFieldsDL`). **Value auto-suggest (2026-07-24):**
  `rule_field_meta()` (one scan, replaces rule_cf_fields — kept as a wrapper)
  returns {fields, values}; `values` = distinct per-field values (≤50) from ALL
  records (leluxe `fields` + Purchases `custom` + statuses) + Purchases
  select/labels DEFINED options; overview ships `field_values`; client
  `gmRuleVals` seeds from it (candidates were only the enrollable subset → most
  columns were blank). **Template variables (2026-07-24):** `_fill` resolves any
  `{Column}` token from THAT package's data (`_cf_for_gwd`: Leluxe fields or the
  Purchases PO custom by label; unknown tokens left literal) on top of the 6
  fixed tokens; overview ships `tpl_tokens` (core + every column).
  **`{name_id}` (2026-07-25):** the AZ-account holder's ID number by the
  package's ClickUp "NAME ON PACKAGEE" value (FAISAL/QAIS/Nuray… — the field
  lives on item/parent rows, never package rows). Mapping = Settings
  `gaash_mail.name_ids` {name: id_number} (sanitizer trims + drops empties,
  whole-dict replace each save so a cleared input deletes); edited in the ⚙
  Accounts & templates modal — rows auto-listed from `field_values` (label
  match `/^name\s*on\s*packag/i` tolerates the typo being fixed) + saved keys
  + a free-add pair. Resolution `_name_on_pkg_for(gwd)`: every row carrying
  the GWD → their parent orders → (Purchases fallback via `_cf_for_gwd`);
  `_name_id_for` compares `_fold`ed, empty (not literal) when unmapped.
  **The two boards need DIFFERENT ID sources** (measured 2026-07-25, don't
  re-derive): an Otlobly/**Purchases** parcel is addressed to the CUSTOMER, and
  `_id_number_for` resolves it automatically (pkg → item.customer_order_id →
  order phone → CRM `id_number`) — verified end-to-end. A **Leluxe** parcel is
  bulk-bought under an AZ account, so GAASH wants the ACCOUNT HOLDER's ID:
  `_id_number_for` returns "" there (its `PHONE IN SHIPPING` values are AZ
  shipping numbers, matching no CRM customer), and `_name_id_for` supplies it.
  So `{id_number}` = `_id_number_for(gwd) or _name_id_for(gwd)` — customer ID
  first, name map as fallback — and ONE template serves both boards.
  `{name_id}` stays available to force the on-package name's ID only. Purchases
  has no "NAME ON PACKAGEE" column and its `profile_box` pool (B19/B22/B27…) is
  disjoint from Leluxe's, so profile→name can't be auto-derived across boards.
  **Parcel name is one concept across both boards (2026-07-25):**
  `parcel_name(gwd)` = Leluxe NAME ON PACKAGEE (row → parent) → a Purchases
  column of that name → the PO's Main name `ship_to`. `name_id_of(name)` does
  the folded name_ids lookup. Batched twins for list rendering:
  `parcel_name_map()` (one scan of both boards) and `effective_id_map()` —
  which MUST mirror `_fill`'s `{id_number}` chain exactly (customer CRM ID via
  Purchases order-phone AND via a Leluxe `phone` field AND the order-name
  fallback, then the name map), because the picker's ID column is a promise of
  what the email will send; a test asserts the two agree on every GWD. Both are
  attached to `candidates()` rows and `overview()` threads as `pname`/`pname_id`
  → the enroll picker's NAME + ID NUMBER columns (green pill = ready, amber
  ⚠ no ID = unmapped) and the 👤/⚠ name tag on each conversation row.
  **Per-package name PICK (2026-07-26):** boards are sometimes blank or
  self-contradicting (one GWD had two item rows disagreeing), so the NAME cell
  is a `<select>` — mapped names + the detected one, ⚠ marking unmapped
  options. Stored in `gaash_threads.pname` (ALTER); `set_parcel_name` /
  `picked_name_map` / `_picked_name`, route action `set_name`, and
  `start_threads(..., names={gwd: name})` pins it BEFORE email 1 renders. The
  precedence is centralised in `id_number_for_email()` — **a pick outranks even
  the customer's CRM ID, and a pick whose name has no ID stays BLANK rather
  than silently falling back** (effective_id_map guards picked GWDs out of its
  later passes to match). The picker only sends names the owner actually
  changed (`value !== data-auto`), so untouched rows keep following the board.
  Also editable after enrollment from the conversation header.
  **Default name (2026-07-26)** — measured: **92 of 105 Leluxe parcels carry NO
  `NAME ON PACKAGEE`**, while Purchases names 12/12 from `ship_to`. So Settings
  `gaash_mail.default_name` fills ONLY parcels neither board names; the full
  chain is `pick → customer CRM ID → board name → default`, and
  `parcel_name = _picked_name or _board_name or _default_name` (the board logic
  lives once, in `_board_name`). Batched twins: `_board_name_map` (raw scan),
  `parcel_name_map` (board + picks + default over `_all_parcel_gwds`),
  `parcel_src_map`/`parcel_name_src` → `pick|board|default` and
  `parcel_board_map` → `leluxe|purchases`; both ship on candidates and threads
  as `pname_src`/`source`. **A default-sourced name is marked `· default`
  everywhere** (picker, conversation row, chat header) — assuming an identity on
  a customs document must never read as a board fact. **Don't re-propose keying
  IDs by Amazon order number**: Leluxe averages 1.06 parcels/order, so it needs
  ~110 keys vs 12 by name, and 8 Leluxe roots have no order number at all. Editor
  (`gmRenderTpl`) has an insert-variable toolbar (core chips + searchable
  all-columns combobox `gmTokBar`/`gmTokPick`, insert-at-caret `gmTokInsert`/
  `gmTokCaret`), a big body (rows=22, min-height 48vh), and a `GM.tplEditId`
  guard so the 60s `gmLoad` poll no longer wipes an open editor. Engine:
  `next_allowed`/`add_business_days` window math; `send_step` runs gaash_steps
  (task ⇒ waiting_task until action=task_done); `migrate_v2` claim_once-seeds
  "seq_default" from the legacy settings.steps; goal reply ⇒ human reply =
  goal_met; bounce kind from mailer-daemon/DSN subjects. **Tracking:** sends are
  multipart w/ `_html_body` — links rewritten to `/api/gaash/r/<msg_id.idx.hmac>`
  + pixel `/api/gaash/px/<token>.gif` (PUBLIC routes, HMAC(OTLOBLY_SECRET) is
  the auth, base=PORTAL_BASE_URL, msg row PRE-allocated then deleted on SMTP
  failure). `stats()` → 📊 (open/click/reply/goal rates + per-step funnel;
  opens approximate — image proxies). Builder UI mirrors HubSpot: summary card
  (steps/days/automation %), step cards + delay chips + ＋ "Choose step" panel
  (email/task), per-seq window editor. A/B testing deliberately DEFERRED.
  **Enroll picker** (`gmNewOpen(preGwds)`, toolbar "📧 Enroll packages" — NOT the
  builder): `candidates()` spans BOTH the Leluxe mirror AND Purchases packages
  (`import purchases` — source-tagged leluxe/purchases, de-duped, terminal/
  delivered excluded); the modal has search + "select shown" + a live count +
  synthetic rows for pre-checked GWDs not in the list (any `GWD\d+` enrolls —
  `start_threads` validates). `gmEnrollFrom(gwds)` opens it pre-checked from
  elsewhere: a 📧 button on every Bulk-search row (`bsEnrollBtn`, gated by
  `gmAvail()` = nav button visible) + a "📧 Chase via GAASH mail" item in the
  Purchases package ⋯ menu. No new routes.

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

## "Two versions" of the app — already answered by feature flags

The owner may resell the app (Tatabu). There is NO code fork: per-business
feature flags (`ME.business.features` — `FEAT.leluxe`, `FEAT.clickup`, …) gate
the Otlobly-customized parts (ClickUp mirror, Leluxe) to business #1, while the
board/table system, columns, notifications etc. are CORE product for every
tenant. New customized features → gate behind a flag; new general features →
build for all tenants. Never fork.

## Pitfalls that already bit us

- Editing the MAIN repo instead of the worktree (identical relative paths) —
  always check the absolute path before Edit; recover via `git -C main diff > patch`.
- `.po-thumbs` wrap → thumbs stacking vertically in nowrap grid rows.
- Dropdown menus clipped by `.lxc` overflow — the `:has(.pop-menu.open)` escapes.
- Stale saved column widths after a column-count change — length guard handles it.
- `launch.json` left modified in a commit; `alerts` firing real Telegram messages
  from a worktree run with live tokens.
