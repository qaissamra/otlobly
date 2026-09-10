# DESIGN_SYSTEM.md — Otlobly

The catalogue is the contract: **`/design-system`** (admin only) renders every component in every
state. A component that is not on that page is not done. This file says what each one is for, when
not to use it, and what it takes.

Source of truth: `static/ds/` — `tokens.css` (values), `ds.css` (components), `ds.js` (primitives,
overlays, composites, shell), `table.js` (DataTable), `status.js` (status registry), `format.js`
(formatters), `icons.svg` (Heroicons v2 outline sprite, MIT). Loaded by `web/index.html` before its
own inline blocks, and precached by `web/sw.js`. Plain scripts: no build step, no framework, no npm.

## Rules

1. **One way to do each thing.** A second `DataTable`, `Modal`, pill or money formatter is a bug.
2. **Values come from `tokens.css`.** No hex literals, no ad-hoc font sizes, no new radii.
3. **Status labels and colours come from `status.js`.** UI code never hard-codes either.
4. **Logical CSS only** (`margin-inline-start`, `inset-inline-end`, `text-align: start`). Arabic is
   deferred by owner decision, not cancelled: physical properties would have to be found again later.
5. **Icons are SVG.** No emoji in the product.
6. **One primary action per page**, at most two visible secondaries, the rest in the overflow menu.
7. **Empty is a muted dash.** Red and orange belong to attention states, never to "no value".
8. **Icon-only controls carry `aria-label` and a tooltip.**

`test_design_system.py` enforces 1–5 and 8 mechanically; `test_ds_lint.py` tracks the retirement of
the old patterns (warn level until Phase 7).

## Tokens (`tokens.css`)

| Group | Tokens | Notes |
|---|---|---|
| Surfaces | `--ds-bg --ds-bg2 --ds-bg3 --ds-card --ds-line --ds-line-strong` | `bg2` is the tinted strip / table header, `bg3` the hover |
| Text | `--ds-ink --ds-ink2 --ds-muted --ds-muted2` | `muted2` is placeholders and the empty dash |
| Brand | `--ds-accent --ds-accent-deep --ds-accent-tint --ds-accent-ink --ds-focus` | the ONE accent: primary actions, active nav, focus ring |
| Tones | `--ds-{info,success,warning,danger,neutral}-{bg,ink,line,dot}` | every badge, banner and attention state |
| On solid | `--ds-on-solid --ds-accent-on-dark` | text and knobs on dark or accent surfaces |
| Spacing | `--ds-s1…--ds-s7` (4·8·12·16·24·32·48) | |
| Radius | `--ds-r-sm --ds-r-md --ds-r-pill` | inputs/badges · panels/modals · pills |
| Elevation | `--ds-e1 --ds-e2` | raised (menus) · overlay (dialogs). Tables and rows are flat |
| Type | `--ds-font --ds-font-mono --ds-t-xs…--ds-t-xl --ds-lh --ds-w-*` | mono is for identifiers only |
| Density | `--ds-row --ds-row-comfortable --ds-control --ds-control-sm` | 42 px rows by default |
| Layers | `--ds-z-sticky…--ds-z-banner` | never invent a z-index |
| Motion | `--ds-dur-fast --ds-dur --ds-ease` | open/close/expand only; zeroed under reduced motion |
| Shell | `--ds-sidebar-w --ds-topbar-h --ds-content-max --ds-icon --ds-icon-nav` | |

## Primitives

| Component | For | Not for | Key options |
|---|---|---|---|
| `DS.icon(name, o)` | any icon | decoration that carries no meaning | `size: "nav"\|"lg"\|"xl"\|Number`, `label` (makes it an image), directional icons auto-flip in RTL |
| `DS.button(o)` | every clickable action | navigation that is really a link (pass `href`) | `variant: primary\|secondary\|ghost\|danger`, `size: sm`, `icon/iconEnd/iconOnly`, `loading`, `disabled`, `tip`, `ariaLabel` (required when icon-only) |
| `DS.badge(o)` | a stored state | free-form emphasis | `tone`, `hex` (ClickUp colour), `solid`, `dot`, `size: sm`. Prefer `DS.status.badge(entity, value)` |
| `DS.attention(o)` | "this needs a person": late, no tracking, missing documents | a status (use a badge) | `kind`, `detail`, `action:{label,onclick}` |
| `DS.tag({label, icon, tone, title, onRemove})` | a value chip (buying account, category) | a status | `icon`, `onRemove` |
| `DS.input / numberInput / datePicker / search / select / textarea / combobox / checkbox / switch` | form controls | anything outside a `DS.field` in forms | `size: sm`, `invalid`, `mono`, `num`, `dirAuto` (user text), `icon`, `affix` |
| `DS.field(o)` | label + control + help + error | a bare control in a form | `required`, `help`, `error`, `invalid` |
| `DS.stat / DS.kpis` | a number the reader compares | a sentence with a number inside | `label`, `value`, `hint`, `tone` |
| `DS.skeleton / DS.empty / DS.errorState` | the three non-happy states every list needs | a blank container | `empty`: `icon`, `title`, `text`, `action` |
| `DS.avatar / DS.feed / DS.tipWrap / DS.kbd` | identity, activity, tooltips, keys | | |

## Composites

| Component | For | Notes |
|---|---|---|
| `DS.pageHeader(o)` | the top of every page | breadcrumb, title, `stats[]`, `updated` (relative, absolute in the tooltip), one `primary`, ≤2 `secondary`, the rest in `overflow`; `title: false` renders the breadcrumb alone (the shell uses it over a legacy page that still draws its own title row) |
| `DS.filterBar(o)` | search + saved views + filter chips | chips carry their value and a remove control; `add` opens the filter builder, `clear` resets |
| `DS.callout(o)` | a state strip that carries the control which changes it (safe mode / live, an account that needs a password) | `tone: neutral·info·success·warning·danger`, `icon`, `text` or `html`, `actions[]` (button options or html); not a toast — it stays until the state does |
| `DS.tabs(o)` | switching between views of one object | not for navigation between pages | `variant: pills` for saved views; full arrow-key support |
| `DS.menu(o)` / `DS.menuOpenAt(items, x, y)` | overflow and context menus | fixed-position, focus-managed, Escape closes | items: `{label, icon, onclick, danger, disabled, kbd}`, `{divider}`, `{head}` |
| `DS.modal(o)` | a short form or a decision (≤5 fields) | anything long (use a drawer or a page) | native `<dialog>`: focus trap, Esc, backdrop click, `unsavedGuard` |
| `DS.drawer(o)` | the detail surface (T2) over a list | replacing a full page | `identifier`, `facts[]`, `tabs`, `side`, `actions[]`, `primary` |
| `DS.confirm(o)` → Promise | destructive or irreversible actions only | routine saves | replaces `window.confirm` |
| `DS.prompt(o)` → Promise | one value, when a full form is overkill | more than one field | replaces `window.prompt` |
| `DS.toast(msg, o)` | the result of an action | errors a user must fix in place (use a field error) | `tone`, `action:{label,onclick}`, `duration`; queue of 3, `role="status"` |
| `DS.form(o)` + `DS.formValidate` | every create/edit form | | validates on submit, marks each field, focuses the first error |
| `DS.wizard(o)` | Source → Map → Validate → Confirm → Result | any other multi-step flow | the shell for `ImportWizard` (Phase 6) |
| `DS.dropzone(o)` | every upload | a bare file input | drag-and-drop **and** click **and** Cmd/Ctrl+V; filters by `accept` and reports what it skipped |
| `DS.sidebar / DS.topbar / DS.shell` | the app shell | | ≤12 items in named groups; badges only on actionable queues |
| `DS.installShortcuts()` | `/` focuses search, `?` shows the sheet | | |

## Pages (`purchases.js`, and the ones that follow)

A migrated page is its own file under `static/ds/`, exporting one namespace (`DS.purchases`);
pages that share a nav item may share a file (`fulfillment.js` holds `DS.toOrder`, `DS.inCart`
and `DS.pkgPrep`).
It renders the page header and the filter bar into a host element, and each board through
`DS.tableRender`. It must not read the app's globals directly — index.html's top-level `let`
bindings are not `window` properties — so the page's render function in index.html passes a
`ctx` object with everything it needs, and the page calls the app's own cell builders through
`window` for anything that still carries inline editing. Two rules that came out of the first
one:

* **Add the view to `OWN_HEADER` in `shell.js`** so the shell stops drawing a generic header
  over the page's own; the shell then contributes only the tabs that are navigation.
* **Call `DS.shell2.syncTab()`** whenever the page switches one of its own tabs, so a link
  points at the tab that will actually open — and add the tab keys to `TABS` in `shell.js`
  plus a getter on `window.APP`, or the address will not survive a reload.

Columns may carry `defaultHidden: true`: the board ships without them and the Columns button
brings them back. It applies only the first time a user meets the table — a saved layout is
theirs from then on.

## The shell (`shell.js`)

`DS.shell2` is the running shell, not a component: it renders the sidebar and top bar over the
existing app, owns the address bar, and draws the Needs attention page. It is opt-in
(`localStorage.otl_shell === "ds"`, or `?shell=new`) and does nothing at all when the flag is off.

| Piece | Contract |
|---|---|
| `NAV` / `STAGES` / `EXTRA` | the information architecture, in one place. Each item: `{key, label, icon, path, view, btn, badge}`. `view` is the legacy `setView` id it opens; `btn` is the legacy nav button whose visibility **is** its role/feature gate — never restate a gate here. |
| `DS.shell2.href(view, tab)` | the canonical `#/group/page/tab` address for a view. |
| `DS.shell2.go(path)` | navigate. Everything else (a nav click, a legacy `onclick`, the back button) reaches the same place through `hashchange` or the `setView` wrapper. |
| `TABS` | which sub-tabs a page can address, and the page's own function to switch them (`gmTab`, `poSetView`, `lxSetView`). |
| `window.APP` | the read-only bridge index.html exposes (`view`, `data`, `pos`, `me`, `platform`, `gmTab`, `poBoard`, `lxView`). The app's top-level `let` bindings are not `window` properties, so this is the only way in. |
| `DS.shell2.attnLoad()` | fetches `/api/attention` (60 s cache, 5-minute refresh) and repaints the badge and the page. |

Adding a page: add one entry to `NAV` (or `EXTRA` if it is not in the sidebar) with its legacy view
id and nav-button id. `test_ds_shell.py` then checks that the view exists, the button exists, and
nothing lost its address.

## DataTable (`table.js`)

`DS.tableRender(el, o)` — the one list component.

- **Columns:** `{key, label, type, w, min, sortable, pin: "start"|"end", locked, align, entity, currency, render(row), sortVal(row), quick(row), menu(row), title}`.
  Types: `id` (mono), `text` (`dir="auto"`, ellipsis + tooltip), `number`, `money`, `date` (relative, absolute in the tooltip), `status` (needs `entity`), `attention`, `bool`, `actions`.
- **One fact per column.** No summary sentences in a cell, no name repeated across columns, totals right-aligned in their own column.
- **Status and actions pin to the end** (`pin: "end"`) so they are never scrolled out of view; the identifier pins to the start.
- **Per-user layout** — resize, drag to reorder, right-click a header (sort, move, reset width, hide), the Columns menu (show/hide, reorder, reset), and density; saved under `ds_table_<id>` in `localStorage`.
- **Selection** with `selectable: true` + `bulk: [{label, icon, onclick(keys, rows)}]` → a docked `BulkActionBar`.
- **Expandable rows** with `expandable: {render(row)}` — the expansion holds a nested DataTable with its own header, never ad-hoc rows. `onToggle(key, isOpen)` tells the page when a row opens or closes, so a page-driven `open(row)` predicate can stay the single truth (`DS.tableResetOpen(id)` clears the by-hand overrides).
- **States:** `loading` → skeleton rows; `error` + `retry` → an error row; no rows → `EmptyState`.
- **Keyboard:** arrows move, Home/End jump, Enter opens, Space selects, Left/Right collapse/expand.
- **Sorting** is local by default; pass `onSort(key, dir)` to sort on the server. `page: {from, to, total, onPrev, onNext}` renders the counter and pager.

## Status registry (`status.js`)

`DS.status.badge(entity, value)` is the only way to render a status. Entities: `order`, `poItem`,
`pkg`, `bucket`, `gerizim`, `docs`, `gmThread`, `lead`, `payment`, `sync`, `flag`, `role`, `tier`,
`attention`. Also `label`, `tone`, `hex`, `values`, `extend(entity, liveOptions)` (merge ClickUp's
live schema at runtime) and `isPkgDone(value)`.

**Stored values never change.** The ClickUp spellings (`oredered`, `recieved rd`, `delievered no rd`)
are real data and stay exactly as they are — the owner decided this on 2026-09-06. The registry fixes
what the app controls: one tone per value, one palette for the five carrier stages, a tone for `PAID`
(it had none), and one definition of "this parcel is done" — mirrored from `alerts.STOP_DEFAULT`, so
`not recieved rd` no longer counts as finished (`APP_AUDIT` F-008). `test_design_system.py` fails if
the JavaScript and the Python sets ever disagree.

## Formatters (`format.js`)

`DS.fmt.money(n, currency, {approx, decimals})` · `secondary` · `number` · `percent` · `date` ·
`datetime` · `time` · `relative` · `daysUntil` · `title` · `iso` · `parse`.

All through `Intl`, pinned to `en-US` (owner decision: Western digits, no seconds). USD shows its
symbol; every other currency shows its **code** (`≈ 3,050 ILS`) so two amounts can never both read
as dollars. `parse` accepts ISO, `YYYY-MM-DD`, `D/M/YYYY`, `Date` and millisecond epochs — the four
formats the app stores today.

## Adding a component

1. Build it in `ds.js` (or `table.js`) using tokens and existing primitives.
2. Add it to `/design-system` in every state it can be in, including dense and empty.
3. Document it in the table above: what it is for, when **not** to use it, its options.
4. Add a check to `test_design_system.py` if it carries a rule (a required label, a colour source).
5. Migrate the existing copies and delete them in the same phase — `MIGRATION.md` tracks the count.
