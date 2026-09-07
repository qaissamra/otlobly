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
