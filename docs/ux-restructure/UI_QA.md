# UI QA — every page and sub-page of the staff app

Run 2026-09-07 against the seeded preview at 1600×1000, admin role, new shell on.
Method, harness and the corrections made to it are at the bottom; read those before
quoting a number.

**41 surfaces measured.** 4 pages are on the design system; 15 are not.

---

## 1. The scoreboard

`cut` = text clipped with **no tooltip and no other way to read the value** — a defect.
`trunc` = clipped but recoverable on hover — intended behaviour, not a defect.
`tiny` = `<button>`/`<a>` under 24×24px. `pills` = raw `<span class="pill" style=…>`.
`fonts` = distinct font sizes. `hdr` = has a design-system page header.

| Surface | cut | trunc | tiny | pills | fonts | hdr | elements |
|---|---:|---:|---:|---:|---:|:--:|---:|
| **leluxe › products** | 0 | 178 | **692** | **335** | 10 | – | 5930 |
| **leluxe › packages** | 0 | 111 | **439** | **295** | 10 | – | 4802 |
| **leluxe › orders** | 0 | 103 | **386** | **373** | 12 | – | 4650 |
| **orders** | **69** | 29 | **220** | 22 | 7 | – | 2433 |
| purchases › customers | 0 | 27 | 106 | 24 | 8 | ✓ | 2911 |
| purchases › packages | 0 | 1 | 68 | **130** | 11 | ✓ | 2930 |
| purchases › orders | 0 | 6 | 65 | 47 | 10 | ✓ | 1830 |
| purchases › products | 0 | 42 | 51 | 85 | 8 | ✓ | 3456 |
| settings | 0 | 0 | 42 | 0 | 10 | – | 687 |
| to order › ordered | 0 | 2 | 34 | 34 | 9 | ✓ | 1727 |
| deposits | **17** | 0 | 21 | 0 | 9 | – | 643 |
| to order › pending | 0 | 0 | 18 | 18 | 9 | ✓ | 1080 |
| gaash mail › seq | 1 | 0 | 14 | 1 | 10 | – | 114 |
| gaash mail › docs | 0 | 1 | 15 | 11 | 10 | – | 125 |
| gaash mail › fcast | 0 | 0 | 10 | 4 | 9 | – | 128 |
| in cart | 0 | 0 | 8 | 8 | 9 | ✓ | 538 |
| gaash mail › conv | 2 | 0 | 8 | 6 | 9 | – | 71 |
| gaash mail › ov / tpl / dash | 0 | 0 | 8 | 0–7 | 7–8 | – | 59–79 |
| flags | **12** | 0 | 7 | 24 | 9 | – | 295 |
| package prep › ready | 0 | 0 | 6 | 0 | 6 | ✓ | 354 |
| trash | 5 | 0 | 1 | 0 | 6 | – | 90 |
| leluxe › dashboard / goal | 0 | 0 | 5 | 1 | 8 / **13** | – | 237 / 137 |
| package prep › reviews | 0 | 0 | 4 | 0 | 6 | ✓ | 268 |
| package prep › waiting | 0 | 1 | 3 | 0 | 6 | ✓ | 232 |
| needs attention | 0 | 0 | 2 | 0 | 4 | – | 296 |
| customers › list | 0 | 0 | 1 | 0 | 6 | – | 728 |
| pnl | 0 | 0 | 0 | 1 | **12** | – | 255 |
| goals | 0 | 0 | 0 | 0 | **15** | – | 167 |
| brain / overview | 0 | 0 | 0 | 0 | 9 | – | 263 |
| leads | 0 | 0 | 0 | 0 | 10 | – | 457 |
| activity · team · tracking | 0 | 0 | 0 | 0 | 5–8 | – | 10–162 |

Not captured: **gaash mail › readiness** — its fetch outruns the harness; measure it during Batch C.

---

## 2. Cross-cutting findings

These appear on many surfaces and are each **one fix**. Doing them first improves every
page before a single page is migrated.

### Q-001 · major · the expand chevron is 22×22px
`static/ds/ds.css:379` — `.ds-exp-btn { width: 22px; height: 22px }`. Two pixels under the
24px desktop minimum in the design checklist. It is the control that opens every row on
**all 11 migrated surfaces**, so it is also the most-clicked control in the app.
**Fix:** 24×24 (or 28×28 with the icon still 14px). One line.

### Q-002 · major · the legacy column-picker is 21×20px
The `⊕` emitted by `lxtHead()` (`web/index.html:4324`). Present on every legacy board —
orders, customers, deposits, trash. It is also an emoji glyph doing the job of an icon.
**Fix:** dies with each board's migration; until then give `.iconbtn` a 24px min-size.

### Q-003 · major · 1,300+ raw inline-styled pills
`<span class="pill" style=…>` — the design skill's first "instant finding". Worst:
Leluxe orders 373, products 335, packages 295; Purchases packages 130, products 85.
Purchases is *already migrated* and still carries 130, because its cells call the app's
own legacy builders. **Fix:** route every one through `statusPill`/`tonePill`/`DS.badge`.

### Q-004 · major · font-size sprawl
Goals renders **15 distinct font sizes**, Leluxe goal 13, P&L 12, Leluxe orders 12,
Purchases packages 11. The design system defines a scale; these pages predate it.
**Fix:** map onto the `--ds-fs-*` tokens during each page's migration.

### Q-005 · major · 15 pages have no page header
Only the 4 migrated pages render `.ds-pagehead`. The other 15 have no breadcrumb, no
title row, no stats strip — so the shell's breadcrumb is the only thing naming the page.
**Fix:** part of each migration; it is what `DS.pageHeader` exists for.

### Q-006 · minor · heading hierarchy
No `<h1>` anywhere. Needs attention emits 5 `<h2>`s, Deposits 4, P&L 3, Team 2.
**Fix:** one `<h1>` per page (the DS page title), `<h2>` for real sections.

### Q-007 · major · the GAASH mail tab strip is 23px tall
All 8 tab buttons (`💬 Conversations` 111×23, `🧭 Overview` 85×23 …) sit one pixel under
the minimum, on every one of the 8 tabs. **Fix:** the strip becomes `DS.tabs` in Batch C.

---

## 3. Page-specific findings

### Q-008 · blocker · Orders: 69 values are cut with no way to read them
`web/index.html` `od` table. The worst are **ASINs**: `B083LDV4YF` needs 69px and is given
42 — it renders as `B083LD…`. An ASIN is an identifier; a partial one cannot be searched,
pasted or matched, and there is no tooltip carrying the full value. Arabic addresses are
cut the same way (`عقربا مستوصف زكاه عق` at 130 of 158px).
**Fix:** Batch B. Give identifier columns their natural width, and put the full value in
`title` on every truncating cell — `purchases.js`'s `text()` helper already does exactly
this and is the pattern to copy.

### Q-009 · blocker · Orders: 220 controls under 24px
Including a **10×16px** edit pencil and 61×17px ASIN links. This is the single densest
concentration of undersized targets outside Leluxe. **Fix:** Batch B.

### Q-010 · blocker · Leluxe: 386–692 undersized controls per board view
Products 692, packages 439, orders 386 — the worst surfaces in the application, on the
page with the most rows. **Fix:** Batch F.

### Q-011 · major · Deposits: 17 cut values
The ledger's note column truncates without a tooltip (`⟦sample⟧ COD colle` at 160 of 219px).
**Fix:** Batch D.

### Q-012 · major · Flags: 12 cut values + 24 raw pills
Email subjects are the content of that page and they are the thing being cut.
**Fix:** Batch E.

### Q-013 · major · Purchases (already migrated) still carries legacy debt
106 undersized targets on the Customers board, 130 raw pills on Packages, 11 font sizes.
Migrating the *shell* of a page does not migrate the *cells* it delegates to.
**Fix:** fold into Batch A — it is shared-builder work, not page work.

---

## 4. What is NOT wrong (corrected during the audit)

Recording these so nobody re-reports them:

- **Leluxe truncation is correct.** It shows 103–178 clipped strings but **zero** without a
  tooltip — every one is recoverable on hover. Its problem is hit targets and pills, not text.
- **Orders is not "unreachable".** The wide table scrolls horizontally inside `.bt-wrap`;
  nothing is permanently off-screen. An earlier reading of 575 overflowing elements counted
  content inside a scroller.
- **The sticky header does track the body.** `lxtSync` (`web/index.html:4268`) mirrors
  `clip.scrollLeft` and works when invoked. An earlier "the header does not follow" reading
  was an artifact of the Browser pane being hidden, which stops the page being painted and
  suppresses timers and scroll events entirely.
- **`.ds-sr` is a correct screen-reader class** (`ds.css:18`). The "Expand" label measuring
  1×40px is intentional, not clipped content. The first harness counted it as a defect on
  all 11 migrated surfaces.
- **Icon-only buttons all carry a label.** `noAria` is 0 on every surface measured.

---

## 5. Batch mapping

| Finding | Batch |
|---|---|
| Q-001 Q-002 Q-003 Q-004 Q-013 | **A** — cross-cutting, lands on every page |
| Q-008 Q-009 (orders) + customers | **B** |
| Q-007 + gaash mail readiness re-measure | **C** |
| Q-011 (deposits) + pnl fonts | **D** |
| Q-012 (flags) + leads, activity, goals, trash, team | **E** |
| Q-010 (leluxe) | **F** |
| settings (42 targets) + tracking | **G** |
| Q-005 Q-006 | resolved per page by the migration that touches it |

---

## 6. Method

`docs/ux-restructure/tools/uiqa.js` — paste into the console on any surface, call `__qa2()`.

It scopes to the single visible view container (an early version fell back to `.wrap` and
reported app-wide totals as one page's), skips `.ds-sr` subtrees, separates truncation that
is recoverable from truncation that is not, and walks up four ancestors looking for a
`title`/`aria-label` before calling a value unreadable.

**Running it needs the Browser pane open.** While hidden the page is not painted, timers do
not fire and `await` never settles; switch views synchronously and force a paint with a
screenshot between the switch and the measurement, or async panes measure as empty.

Counts are one viewport at one role with one dataset. They rank surfaces against each other;
they are not absolute defect totals.

---

## 7. Batch A — done 2026-09-07

Seven root causes, all in shared CSS or shared cell builders. No page was rebuilt; every
legacy page improved anyway.

| Surface | undersized targets | unreadable values |
|---|---|---|
| leluxe › products | 692 → **0** | 0 → 0 |
| leluxe › packages | 439 → **0** | 0 → 0 |
| leluxe › orders | 386 → **0** | 0 → 0 |
| orders | 220 → **0** | **69 → 0** |
| purchases › customers | 106 → **1** | 0 → 0 |
| settings | 42 → **0** | 0 → 0 |
| deposits | 21 → **0** | 17 → 0 |
| to order › pending | 18 → **0** | 0 → 0 |
| flags | 7 → **0** | 12 → 0 |
| trash | 1 → **0** | 5 → 0 |

**~1,900 undersized controls and ~100 unreadable values fixed by 9 edits.**

What changed:

- `static/ds/ds.css:379` — `.ds-exp-btn` 22px → 24px. The row-expand chevron on all 11
  migrated surfaces (**Q-001**).
- `.iconbtn` / `.minibtn` — a 24px floor, glyph size untouched, so the `⊕` column picker and
  the `✎` editors grow their hit box without growing visually (**Q-002**).
- `.lx-copy` — was 16×17. Used across Leluxe, flags and the info modals (**Q-002b**).
- `a.pill` / `button.pill` — a pill that is a *control* gets the floor; display-only pills
  keep their compact size, which is why the rule is not on `.pill` (**Q-002c**).
- `.lk` — the ASIN links were 17px tall; `padding-block:4px` (**Q-009**).
- **`.cellact`** — new, named once. The `✎ re-quote` and `＋₪ record a deposit` links were
  the same inline style written twice at 10×16 (**Q-009b**).
- ASIN cells now carry the full value (plus the product name) in `title` — an ASIN cut to
  `B083LD…` cannot be searched, pasted or matched (**Q-008**).
- **Three cells had a tooltip that said `اضغط للتعديل · click to edit` and nothing else** —
  Orders' address, Package prep's location, Customers' city. A tooltip that repeats the
  affordance instead of the value is worse than none: it looks handled. Now
  `<value> · click to edit`, and `dir="auto"` so Arabic renders correctly (**Q-008b**).
- Deposits' note, Trash's label and kind — truncating with nothing to recover them.

Not fixed here, by design: the raw inline pills (**Q-003**, ~1,300) and the font sprawl
(**Q-004**) need each page's own builders, so they belong to that page's migration batch.

58 suites green.

---

## 8. Batch B2 — one type scale (2026-09-07). Q-004 closed.

**Q-004 said Goals renders 15 font sizes, P&L 12, Leluxe 12, Orders 8.** The cause was not
those pages: `web/index.html` declared **22 distinct sizes in its stylesheet and 20 more in
inline styles — 903 declarations in total**, against a design system that defines 5.

Every size is now a token. `--ds-t-2xl: 28px` was added for hero numerals, because the
P&L headline (34px), the Leluxe goal (34px), its percentage (26px) and the KPI cards (25px)
were four sizes for one idea and squashing them into 22px would have shrunk the numbers the
owner reads first.

Measured after, at 1600px — `steps` counts distinct rendered font sizes on the page:

| surface | steps before | steps after | unreadable | targets < 24px |
|---|---|---|---|---|
| goals | 15 | **5** | 0 | 0 |
| pnl | 12 | **5** | 0 | 0 |
| leluxe › orders | 12 | **4** | 0 | 0 |
| leluxe › products | 10 | **4** | 0 | 0 |
| settings | 10 | **4** | 0 | 0 |
| orders | 8 | **5** | 0 | 0 |
| purchases › packages | 11 | **6** | 0 | 1 |
| activity | 6 | **4** | 0 | 0 |
| gaash mail › docs | 10 | **4** | 1 | 0 |

`13.02px` appears on boards carrying GWD numbers and is **correct**: `.ds-mono` is
`font-size:.93em`, an optical correction because monospace renders larger than Inter at the
same size. It scales with whatever step it sits in, so it is not a fixed size and not sprawl.

### It cannot come back

`inventory.py` already computed a font-size count but nothing enforced it, and counting only
literals would now read 0 and mean nothing. The metric is now **`font_size_steps`** — every
hard-coded px value **plus** every distinct `--ds-t-*` token referenced — baselined at **6**,
alongside `font_size_literals` at **0**. Adding a stray `13px` moves it to 7 and the lint warns.

### Three bugs this turned up

- **Three tokens Batch B shipped that do not exist.** `ds.css` referenced `--ds-fs-xs`,
  `--ds-fs-sm` and `--ds-mono` with no fallback — the real names are `--ds-t-xs`, `--ds-t-sm`,
  `--ds-font-mono`. The new Orders/Customers identity cell was silently inheriting the wrong
  size in the wrong family. A sweep for undefined tokens found exactly these three
  (`--ds-pu-cols` is also undefined in CSS but is set inline at runtime, which is correct).
- **`.statussel` fell under the hit-target floor.** Dropping it from 11.5px to 11px took ~0.5px
  off its height and put **207 buttons on the Leluxe products board** back under 24px — caught
  only because the browser pass re-measured rather than trusting the CSS diff. It now carries
  `min-height:24px`, as `.iconbtn` and friends got in Batch A.
- **The literal-only remap missed four call sites.** `poTn4(t, fs)` takes its size as an
  *argument* (`poTn4(tn,'10px')`), so the value never appeared next to `font-size:` in the
  source. 267 GWD numbers were still rendering at 10px until the callers were fixed.

### One methodology note, for whoever measures next

Twice this session a measurement was wrong because the page under the harness was stale. The
Flask app caches `web/index.html`, **and** `sw.js` caches `/app`. Restarting the server is not
enough: unregister the service worker and clear its caches, or you will confidently measure
code you replaced ten minutes ago.

## 9. Batch B3 — the information Batch B cost the Orders board (2026-09-07)

Reported by the owner, looking at the migrated board: *"a lot of the information I had for
column is gone we need them back."* He was right, and the audit above had not caught it —
every check in §1–§3 asked whether what was ON the page was readable, and none asked whether
what used to be on the page still was. **Q-014: a migration must diff its column set against
the one it replaces.**

### What had gone

| Old column (`LXT_COLS.od`) | After Batch B |
|---|---|
| منتجات · items — the ASIN links | a bare count, "2 products" |
| أمازون # · amazon # | `defaultHidden` |
| العنوان · address | `defaultHidden` |
| التتبع · tracking | `defaultHidden` |
| واتساب · whatsapp | folded into the ⋯ menu (kept — owner) |

### Q-015 · the escape hatch was unreachable — fixed, design-system wide

The table bar carrying the **Columns** control rendered *after* the rows. Measured on the
running preview with 61 orders: **y = 2,953px**. The control that unhides a column sat ~2,800px
below the top of the page, so "hidden by default" meant "gone". The bar now renders before the
header on **every** DataTable — Purchases, To order, In cart, Package prep, Orders, Customers,
all verified. The bulk bar stays last: it is `position: sticky` to the bottom of the viewport
and was never out of reach.

### Q-016 · one serializer was starving the board — fixed

Thumbnails looked like a schema change. `order_items` is `id · order_code · asin · clean_url ·
business_id` and stores no title and no image. But **that table is a search index, not the
record**: the order's truth is `orders.data_json`, where 68/101 items carry a title and 64/101
carry an image. `store.py:_order_row` reads both — which is why To order and Package prep were
built expecting photos. `report.py:39` dropped them. Two keys.

That same omission produced the expander printing `B09CLKPMVC   B09CLKPMVC`: with no title, it
rendered the ASIN in the title column and again in the ASIN column. The ASIN now sits *next to*
a title, or stands in for one, never both.

### Q-017 · three near-copies of the same markup — fixed

`thumb`, `thumbs` and the sub-table `grid` lived in `fulfillment.js`; `sales.js` hand-rolled a
fourth that emitted `.ds-pu-subrow` (the real class is `.ds-pu-sub-row`) with no `.ds-pu-td` at
all, so the Orders expander drew with no padding, no borders and no overflow control. All three
are now `DS.thumb` / `DS.thumbs` / `DS.subTable` in `ds.js`, and the suite fails if any other
page module builds that markup itself.

### Measured after

| | before | after |
|---|---|---|
| board width | 1,622px, 13 columns | **1,614px, 14 columns** (Tracking added) |
| Columns control | y ≈ 2,953px | y ≈ 1,177px, above the header |
| orders showing product photos | 0 | **35 / 61** (26 fall back to the plain count) |
| grey placeholder walls | — | 0 |
| cells with unreadable text | 0 | 0 |
| expander rows printing the ASIN twice | every unnamed product | 0 |

### Still open, and pre-existing

**Every selectable board's row checkbox is 15px**, under the 24px hit-target floor — 62 of them
on Orders, 19 on To order. It is the browser's native checkbox in `.ds-td-check`, it predates
this batch, and the honest fix is making the whole cell toggle the row rather than growing the
box. It belongs in a design-system pass, not here; §1's "0 undersized controls" for Orders was
measuring click-handler controls only and never counted them.

### The lint was lying about one metric

`native_confirm` counted `\bconfirm\(`, which matches the `confirm(` inside **`DS.confirm(`** —
the design system's own replacement scored as one of the native calls it exists to retire. Now
`(?<![.\w])confirm\(`, and the same for `prompt` and `alert`.
