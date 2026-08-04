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
| Leluxe packages (`lxRenderPackages` :4007) | 🟡 | ✅ | 🟡 | ✅ | ✅ GWD | n/a | ✅ | ✅ `k` | ✅ |
| To-order (`neRowHtml` :6399) | ✅ | 🟡 table-row variant (`.ne-meta`) | ✅ `fld()` | ✅ | 🟡 | 🟡 detail only | ✅ | 🟡 own resize only | ✅ |
| Orders (`render` :2365) | 🟡 `statusSelect` | ❌ plain table | ❌ | ❌ | ❌ | ❌ | 🟡 | ❌ | ✅ onchange inputs |
| Brain (`renderBrain` :5837) | 🟡 raw pills | ❌ bespoke tiles | ❌ | ❌ | n/a | n/a | 🟡 | n/a | n/a |
| Customers (`renderCustomers` :9671) | 🟡 | ❌ plain table | 🟡 `.kv` panel | ❌ | n/a | ✅ profile panel | 🟡 `.minibtn` | ❌ | 🟡 panel only |
| Deposits (`renderDeposits` :5727) | ✅ | ❌ plain table | ❌ | ❌ | n/a | ❌ | 🟡 | ❌ | ❌ |
| In cart (`renderIncart` :6504) | 🟡 | ❌ | ❌ | ❌ | n/a | n/a | ✅ | ❌ | 🟡 one input |
| Catalog (`renderCatalog` :2767) | 🟡 | ❌ plain table | ❌ | ✅ 38px | n/a | n/a | 🟡 `.minibtn` | ❌ | ✅ onchange |
| Meta leads (`renderMetaLeads` :5780) | 🟡 raw inline divs | ❌ fully bespoke | ❌ | ❌ | n/a | ❌ | ✅ | ❌ | ✅ onchange |
| P&L (`renderPnl` :9794) | ✅ in drills | ❌ bespoke tiles | ❌ | ❌ | n/a | n/a | 🟡 | ❌ | n/a |
| GAASH mail (gm* :10448-12770) | 🟡 ~50 raw pills | 🟡 mixed | ❌ | ✅ via bs rows | ✅ | n/a | ✅ | ✅ `wf`/`en` only | 🟡 |
| Bulk search (`bsRender` :10195) | ✅ mixed correctly | ✅ | ✅ | ✅ | ✅ both | ❌ | ✅ | ✅ `bs` | n/a read-only |
| Team/Trash/Activity/Picking | 🟡 | ❌ plain tables | ❌ | ❌ | n/a | n/a | 🟡 `.minibtn` | ❌ | 🟡 |

## Cross-cutting defects (not per-view)

1. **4 pill families + ~85 raw inline pills** — worst: GAASH mail :10168-12772, Meta leads :5795+.
2. ~~**`fld()` duplicated**~~ ✅ Batch 1: one global `fld()` next to `statusPill`
   (Package prep's `ppFlds` keeps its `<b>`-value variant deliberately — bold values).
3. ~~**Inline-edit recipe ×3**~~ ✅ Batch 1: one global `editCell()`; all four
   consumers (poCfEdit/poPkgDueEdit/gmNameIdEdit/ppLocEdit) migrated.
4. **5 expand-state stores** for one visual state: `PO_VIEW`, `PO_COLLAPSED`, `LX_OPEN`/`LX_PCOL`, `NE_OPEN`, `PP_OPEN`; 2 CSS rules for the same #fff4ee.
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
- [ ] **Batch 2 — Orders view**: plain table → LXT table (follow the "bs"
  checklist in the skill §2), `statusSelect` stays (editable) but pill-colored;
  add OTL/GWD + location columns; inline edit via shared helper.
- [ ] **Batch 3 — Customers / Deposits / Catalog / In cart / Trash / Team**:
  plain tables → LXT; editable cells via shared helper.
- [ ] **Batch 4 — GAASH mail pill cleanup**: replace raw pills with
  tonePill/hexPill/statusPill; adopt `fld()` in chat/builder headers.
- [ ] **Batch 5 — Meta leads / Brain / P&L**: adopt card language (two-tier
  headers, shared pills, `.po-btn`), keep their layouts.
- [ ] **Batch 6 — editability sweep**: audit every ❌/🟡 in the "Editable
  fields" column; wire the shared inline-edit helper + endpoints.
- [ ] **Batch 7 — expand-state + open-CSS unification** (low priority, pure
  refactor).
