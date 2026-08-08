# UI_AUDIT — staff-app consistency matrix

Master checklist for making every staff view consistent, customizable, and
editable. Produced 2026-08-04 from a full scan of `web/index.html` (12,870
lines) + `app.py`. **Fix in batches — one batch per session per PR.** Before
working a batch, read `.claude/skills/otlobly-board/SKILL.md` (design language +
verify-everything workflow). After a batch, update this file: mark it ✅ and fix
any cells you learned were wrong.

Legend: ✅ uses the shared standard · 🟡 bespoke/hand-rolled equivalent ·
❌ missing entirely · `n/a` doesn't apply.

## The standard components

| Component | Canonical implementation |
|---|---|
| Status pill | `statusPill()`/`STATUS_COLOR` (index.html:2322/:1976) for order statuses; `lxStatusPill` (:2961) ClickUp; `tonePill/hexPill/solidPill` (:8471+) tones. Never new pill CSS. |
| Two-tier card header | `.po-card > .po-head > (.po-title + .po-meta)` (CSS :96-105) + orange `open` state (#fff4ee) |
| Meta strip labels | `.field/.lbl` — `fld()` helper (dup at :6429 and :8198 — Batch 1 unifies) |
| Product thumbnails | `.po-thumbs` one-line strip, dup-merge ×N: `poThumbStrip` (:9037) / `lxThumbs` (:3082); 34-44px `.thumb` |
| Buttons | `.po-btn` (+ `.accent/.danger`); ⋯ menus via `popMenu` |
| Inline edit | replace-node recipe of `poCfEdit` (:8305) — input swaps in, blur saves, Esc cancels (dup at :7511, :11636 — Batch 1 unifies) |
| Column system (LXT) | `LX_TABLES`/`LXT_COLS` registry (:3159+): resize, drag-reorder, ⊕ hide/show, sort, right-click menu, saved to localStorage |
| Numbers | GWD `tracking_number`, OTL `customer_tracking` — visible + copyable where a parcel appears |

## Matrix

| View (render fn) | Status pill | 2-tier header | .field/.lbl | Thumbnails | OTL/GWD | Cust. location | .po-btn | LXT columns | Editable fields |
|---|---|---|---|---|---|---|---|---|---|
| Purchases orders tree (`poCardHtml` :9047) | ✅ + 🟡 tone/hex | ✅ canonical | ✅ | ✅ `poThumbStrip` | 🟡 GWD ✅ / OTL popup-only | 🟡 customers sub-view only | ✅ | ✅ `po` | ✅ inline + modals |
| Purchases pkgs/products flat (:8022/:8083) | ✅ | ✅ | ✅ | ✅ | 🟡 same | ❌ | ✅ | ✅ `pok`/`pop` | ✅ |
| Purchases customers (`poRenderCustomers` :8156) | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ 📍 city+addr | ✅ | ✅ | 🟡 partial |
| Package prep (`ppCard` :2672) | ✅ + 🟡 | ✅ | 🟡 inline variant | ✅ 34px in rows | ❌→✅ **Batch 0 adds OTL** | ❌→✅ **Batch 0** | ✅ | ❌ (card view) | 🟡 status only →✅ **Batch 0 adds location** |
| Package prep done/review (`ppReviewCard` :2726) | ❌ | 🟡 title only | ❌→✅ **Batch 0** | ❌→✅ **Batch 0** | ❌→✅ **Batch 0** | ❌→✅ **Batch 0** | ✅ | ❌ | ❌→✅ **Batch 0** (backend stripped everything — pkgprep.py:300-304 `_make_review_card`) |
| Leluxe orders/products (`renderLeluxe` :3447) | 🟡 `lxStatusPill` (correct for ClickUp) | ✅ `.pkg-head` | 🟡 | ✅ `lxThumbs` | ✅ GWD / ❌ OTL | ❌ (AZ world — n/a mostly) | ✅ | ✅ `""`/`p` | ✅ modal + selects |
| Leluxe packages (`lxRenderPackages`) | ✅ editable `lxStatusSelect` on subtask/single-product rows *(2026-08-04 Purchases-parity pass; multi-product grouped rows stay read-only — never-bulk)* | ✅ | 🟡 | ✅ 32px max-4 `lxThumbs` *(parity)* | ✅ GWD | n/a | ✅ | ✅ `k` (+status sort, 84px floor) | ✅ row-level status |
| To-order (`neRowHtml`) | ✅ | 🟡 table-row variant (`.ne-meta`) | ✅ `fld()` | ✅ | 🟡 | ✅ 📍 editable in detail *(Batch 6)* | ✅ | 🟡 own resize only | ✅ + location `editCell` |
| Orders (`render`, LXT table `"od"`) | ✅ pill-colored `statusSelect` | ✅ board (pin + columns) | n/a | ❌ (asin links only) | ✅ order `tracking_number` col | ✅ 📍 city+addr editable | ✅ | ✅ `od` *(Batch 2)* | ✅ onchange + `editCell` |
| Brain (`renderBrain`) | ✅ (audited Batch 5: uses shared `qchip`, no raw pills) | n/a dashboard tiles (`.panel` idiom) | ❌ | ❌ | n/a | n/a | ✅ `minibtn` | n/a | n/a |
| Customers (LXT `"cu"`) | ✅ | ✅ board *(Batch 3)* | 🟡 `.kv` panel | ❌ | n/a | ✅ 📍 city editable on board *(Batch 6)* | ✅ | ✅ `cu` | ✅ board city `editCell` + ★ VIP toggle + panel form *(Batch 6 — /api/customer now MERGE-guards, fixing a latent bug where any profile save wiped the stored ID number/image)* |
| Deposits (LXT `"dp"`) | ✅ | ✅ board *(Batch 3)* | ❌ | ❌ | n/a | ❌ | ✅ | ✅ `dp` | n/a ledger (delete only) |
| In cart (LXT `"ic"`) | 🟡 | ✅ board *(Batch 3)* | ❌ | ✅ 30px thumbs | n/a | n/a | ✅ | ✅ `ic` | ✅ cost input |
| Catalog (LXT `"ct"`) | 🟡 | ✅ board *(Batch 3)* | ❌ | ✅ 34px in pin | n/a | n/a | 🟡 `.minibtn` | ✅ `ct` | ✅ onchange in cells |
| Meta leads (`renderMetaLeads`) | ✅ (none needed) | ✅ two-tier lead cards *(Batch 5)* | ✅ `fld()` meta strip | ❌ | n/a | ❌ | ✅ | n/a (card list) | ✅ onchange + note in strip |
| P&L (`renderPnl`) | ✅ (margin via tonePill, *Batch 5*) | n/a dashboard tiles (`.card` idiom) | ❌ | ❌ | n/a | n/a | 🟡 | n/a | n/a |
| GAASH mail (gm* :10448-12770) | 🟡 ~50 raw pills | 🟡 mixed | ❌ | ✅ via bs rows | ✅ | n/a | ✅ | ✅ `wf`/`en` only | 🟡 |
| Bulk search (`bsRender` :10195) | ✅ mixed correctly | ✅ | ✅ | ✅ | ✅ both | ❌ | ✅ | ✅ `bs` | n/a read-only |
| Goals (`renderGoals`) | ✅ qchip warns only | n/a hero + `.gl-cat` cards (lx-goal idiom) | ❌ | ❌ | n/a | n/a | ✅ `.minibtn` | n/a (card grid) | ✅ target/label `editCell` + ⚙ strip |
| Trash (LXT `"tr"`) | n/a | ✅ board *(Batch 3)* | ❌ | ❌ | n/a | n/a | ✅ | ✅ `tr` | n/a |
| Team/Activity/Picking | 🟡 | ❌ plain tables (Team deferred: interleaved pw-reset rows need the wf-exp translate pattern) | ❌ | ❌ | n/a | n/a | ✅ Team / 🟡 rest | ❌ | 🟡 |

## Cross-cutting defects (not per-view)

1. **4 pill families + raw inline pills** — GAASH mail block ✅ cleaned in
   Batch 4 (27 sites → tonePill/hexPill; helpers gained opts `{style, attrs,
   cls}` as the ONE sanctioned tweak path). ~45 raw pills remain elsewhere
   (Meta leads, scattered one-offs) — Batch 5+.
2. ~~**`fld()` duplicated**~~ ✅ Batch 1: one global `fld()` next to `statusPill`
   (Package prep's `ppFlds` keeps its `<b>`-value variant deliberately — bold values).
3. ~~**Inline-edit recipe ×3**~~ ✅ Batch 1: one global `editCell()`; all four
   consumers (poCfEdit/poPkgDueEdit/gmNameIdEdit/ppLocEdit) migrated.
4. ~~**5 expand-state stores**~~ ✅ Batch 7: one `openStore()` (next to `editCell`)
   now backs `PP_OPEN`, `NE_OPEN`, `LX_OPEN`, `LX_PCOL` (inverted) and
   `PO_COLLAPSED` (inverted + persisted). `PO_VIEW` stays as-is — it is
   tri-state ("open"/"closed"/unset→`poNeedsAction()` smart default), a feature
   not a duplicate. The `#fff4ee` accent tint is single-sourced as
   `--tint-accent` (17 sites).
5. **Backend payload gap**: pkgprep review cards stripped server-side (fixed in Batch 0); OTL `customer_tracking` absent from pkgprep payload (Batch 0).
6. **Customer location** (`customer.city`/`.address`, collected by /order intake) surfaced only on Purchases-customers + To-order detail; missing from Package prep (Batch 0), Orders, Deposits.

## Batches (fix order — one PR each)

- [x] **Batch 0 — Package prep done-cards + location** *(this PR)*: pkgprep.py
  keeps full payload for review cards (orders+items+packages+totals), adds
  `city`/`address` + OTL `customer_tracking`; ppReviewCard expandable with
  product images + package rows; 📍 location editable on all prep cards
  (extends `/api/order/edit` with city/address).
- [x] **Batch 1 — shared primitives** (no visual change) *(shipped 2026-08-04)*:
  global `fld()` (next to `statusPill`) replaced the To-order + Purchases-customers
  duplicates; global `editCell(el,opts,save,cancel)` now carries the ONE copy of
  the replace-node inline-edit recipe — `poCfEdit` (typed branch), `poPkgDueEdit`,
  `gmNameIdEdit` (fill mode) and `ppLocEdit` all migrated; pill policy comment
  sits above `STATUS_COLOR`. New code must use these three.
- [x] **Batch 2 — Orders view** *(shipped 2026-08-04)*: plain table → LXT table
  **`"od"`** (pin = checkbox + order # + customer + phone; 13 columns; resize/
  drag-reorder/⊕ hide/sort/right-click menu all live). `statusSelect` pill-colored
  via STATUS_COLOR; 📍 city (new in report.py `_row`) + address columns editable
  via `editCell`/`odLocEdit`; order `tracking_number` column added; totals as a
  `bt-total` row; bulk-select kept (select-all moved to the toolbar). Per-order
  GWD (GAASH parcel number) deferred — needs a purchases-scan attach server-side;
  fold into a later batch.
- [x] **Batch 3 — Customers / Deposits / Catalog / In cart / Trash** *(shipped
  2026-08-04)*: five plain tables → LXT boards `cu`/`dp`/`ct`/`ic`/`tr` (full
  resize/reorder/hide/sort per user; Σ totals rows on cu+dp; Catalog keeps its
  onchange inputs inside cells; Customers keeps row-click → profile).
  **Deferred from this batch:** Team (interleaved password-reset rows need the
  wf-exp full-width-block pattern — tiny admin table, low value); Customers
  board inline-edit (POST /api/customer upsert may blank omitted fields — review
  its semantics before wiring editCell).
- [x] **Batch 4 — GAASH mail pill cleanup** *(shipped 2026-08-04)*: all 27 raw
  `<span class="pill" style>` sites in the gm block (conversations list, chat
  header, wizard, accounts, enroll picker, seq pills, Readiness, workflows,
  rule cards, templates) now go through tonePill/hexPill. The helpers gained an
  optional 4th arg `{style, attrs, cls}` — the one sanctioned way to tweak
  size/handlers/extra class (documented at the definitions). Tones normalized
  to TONE/hexPill recipes (tiny shade shifts accepted — that IS the
  consistency). The audit's "adopt fld() in chat/builder headers" line had no
  real target — the gm headers use chips, not label strips; dropped.
- [x] **Batch 5 — Meta leads / Brain / P&L** *(shipped 2026-08-04)*: Meta leads
  lead cards → the two-tier `.po-card` anatomy (title row + tinted `.po-meta`
  with `fld()` fields + the note input); P&L margin chip → `tonePill`. Audit
  correction: Brain was already conformant (`panel`/`qchip`/`minibtn` are shared
  idiom, no raw pills) and both Brain + P&L are dashboards where two-tier order
  headers don't apply — matrix cells fixed rather than force-converting.
- [x] **Batch 5b — scattered one-off raw pills** *(shipped 2026-08-04)*: ~25
  one-off raw pill sites converted to tonePill/hexPill (orders 🌐, pkgprep
  "بلا رقم" ×2 + OTL copy pill + ppSection counts, lxChip's 5 sync states,
  Leluxe "mixed" ×4 + "w/o parent" ×2 + UPLOADING + lxCVal chips +
  lxProfileCell, To-order's 7-badge family + 🗑 محذوف, gaash/gz "none"
  fallbacks, bulk-search ⌚ Leluxe + trk). **Raw `<span class="pill"` census:
  45 → 20**, and every survivor is a helper implementation, a DOMAIN pill
  builder (lxStatusPill/lxCfPill/lxGashPill/lxDeadlinePill/lxMailPill/
  gzCuChip/pkgDocsPill/pkgDeadlinePill), a STATUS_COLOR-map exception pill,
  a bare default `.pill` count, or the policy comment itself. The pill layer
  is DONE — new raw pills are a review flag.
- [x] **Batch 6 — editability sweep** *(shipped 2026-08-04)*:
  **(a) Latent data-loss bug fixed**: `/api/customer` rebuilt the record from
  posted fields and `db.upsert_customer` replaces `data_json` wholesale — any
  ✎ profile save silently wiped the stored `id_number`/`id_image` and re-keyed
  `customer_id`. The endpoint now MERGE-guards against the existing record
  (locked by 4 new checks in test_customer_id.py).
  **(b) Customers board**: 📍 city inline-editable via `editCell` + ★/☆ VIP
  click-to-toggle (gated `manage_customers`; client posts the full record —
  safe with the guard).
  **(c) To-order detail**: 📍 City/Address editable via `editCell` →
  `/api/order/edit` (`neLocCell`/`neLocEdit` — same field Orders board +
  Package prep edit).
  Intentionally NOT editable (ledger/derived): Deposits rows, spent/orders
  counts, Bulk search, P&L, Brain, order `due` (computed est_delivery_customer).
- [x] **Batch 7 — expand-state + open-CSS unification** *(shipped 2026-08-04 —
  the program's final batch)*: one `openStore({inverted, persist})` helper
  replaces the five hand-rolled open-state Sets (23 call sites); `PO_VIEW`
  deliberately kept (tri-state smart default). `--tint-accent:#fff4ee` defined
  once in `:root` and used by all 17 former literal sites (open rows, unread,
  active tabs, chat bubbles). New boards should use `openStore()` + the
  `.open` classes; new tints use `var(--tint-accent)`.

**Program complete.** All roadmap batches shipped 2026-08-04 (PRs #66–#73 + 7).
Still open by choice: Team → LXT (interleaved pw-reset rows; tiny surface).
Review flags from here: a raw `<span class="pill" style>`, a hand-rolled
inline-edit input, a new open-state `Set`, or a literal `#fff4ee`.
