<!-- Provenance: delivered by the owner on 2026-09-06 as ~/Downloads/otlobly-ux-restructure-brief.md and saved
     here verbatim (nothing below this block was edited). Decisions the owner took the same day, before Phase 0:
     1. IA baseline = section 6 of this brief, the 5 groups as written (not the 2026-09-04 "7 areas" proposal).
     2. Arabic mode: "only English for now" — RTL/Arabic layout work is deferred; §5.7 of AUDIT.md is a backlog.
     3. Status typos (recieved / oredered / delievered): leave the labels as they are (they mirror ClickUp).
     4. Most-used pages: Purchases, GAASH mail, To order, Package prep.
     Phase 0 output: docs/ux-restructure/AUDIT.md · MIGRATION.md · tools/inventory.py · screens/before/. -->

# Otlobly — UI/UX restructuring brief

*Owner's note: paste this whole file to the coding agent, or save it in the repo as `docs/ux-restructure/BRIEF.md` and tell the agent: "Read docs/ux-restructure/BRIEF.md and start Phase 0." Answer the questions in section 15 if you can; otherwise the agent must ask them before Phase 1.*

---

## Your role

You are the lead product engineer and design-system owner for Otlobly. The app has grown feature by feature and now reads as a collection of screens rather than one product. Your job is to give it a single information architecture and a single design system, then migrate every page onto them — without losing a single capability. You audit first, get approval, then work in phases.

## 1. What Otlobly is (hypotheses — verify against the code and the owner, then correct in AUDIT.md)

Otlobly (`otlobly.co/app`) is an internal operations platform for a purchasing and order-fulfillment business. From the UI, the working flow looks like this:

1. Leads become customers; customers place orders for products.
2. Requested items sit in **To order**, are added to retailer carts (**In cart**), and are consolidated into **purchase orders** (`PO-0001`, `PO-0005` …). One PO can cover many customers, items and packages (e.g. "17 customers · 35 items · 12 packages").
3. Packages ship through a forwarder called **GAASH** (tracking numbers like `GWD004745574`; there is a **GAASH mail** page and a **Bulk search** page for tracking).
4. Packages are prepared (**Package prep**) and handed to customers.
5. Money is tracked through **Deposits** and **P&L**; targets in **Goals**; a log in **Activity**.
6. **Flags** marks items needing attention. **Brain** is unclear. **Leluxe** appears to be a second brand or business run from the same tool.

The UI is bilingual English/Arabic (a `ع` toggle in the top bar); most customer names are Arabic. Users are a small ops team, on desktop, all day, working in dense lists. Optimize for that.

## 2. The problem

Reported by the owner:

- Too many sidebar items and no navigation hierarchy.
- Related features scattered instead of grouped.
- Too many buttons and actions exposed everywhere.
- Multiple, inconsistent upload/import experiences.
- Different pages use different layouts for similar workflows.
- Inconsistent buttons, tables, forms, modals, filters and page headers.
- Duplicate UI patterns and components.

Observed on the Purchases page alone (full list in section 14): a 14-item flat sidebar with emoji icons; a page called Purchases whose subtitle counts "41 orders"; a table whose ORDER cell contains a summary sentence that repeats the ORDER NAME and CUSTOMER columns; the TOTAL concatenated into the customer cell so headers don't line up with data; the STATUS column pushed off-screen; a status labeled "recieved"; four different treatments for "something is wrong" (red `—`, orange "no GAASH #", an uppercase "40 DAYS LATE" pill, lowercase status pills); nested package rows in a completely different visual language from their parent rows; Arabic names truncated at the wrong end.

Root cause: there is no shared structure. Fixing screens one at a time will not work. Build the structure once, then migrate everything onto it.

## 3. Outcome and principles

**Outcome:** every screen in Otlobly is recognizably the same product — same shell, same page anatomy, same table, same form, same modal, same import flow, same vocabulary — and a new ops hire can predict where things are and how they behave.

Principles, in priority order:

1. **One way to do each thing.** One `DataTable`, one `PageHeader`, one `FilterBar`, one `FormLayout`, one `Modal`/`DetailDrawer`, one `ImportWizard`, one status system, one number/date/currency formatter. A second implementation of any of these is a bug.
2. **Hierarchy over exposure.** Every page has one primary action. Everything else is secondary, in an overflow menu, or attached to the row/selection it applies to. A control is visible only because it is used constantly.
3. **Dense and calm.** This is an operations tool: tight rows, aligned columns, quiet surfaces, color only for meaning. No decorative cards, shadows or gradients.
4. **Semantics live in one place.** Status labels/colors, attention states, empty values, currency and date formats are defined once and referenced everywhere.
5. **Arabic is first-class.** RTL layout, mixed-direction text and Arabic typography are part of the system, not a patch.
6. **Behavior parity.** No feature, field, shortcut or workflow is lost. No business logic, API or data-model change is made for the UI's sake unless documented and approved.
7. **This is a structure project, not a rebrand.** Keep the Otlobly logo and its orange.

## 4. Working rules (non-negotiable)

1. **Do not write or change product code until the owner approves the Phase 0 report.** Phase 0 is read-only.
2. Never delete a capability to achieve consistency. If something looks redundant, propose its removal in AUDIT.md with evidence, and remove only after explicit approval.
3. Keep the existing stack (framework, styling approach, state management, i18n library). Do not introduce a new framework. If a component library is already in use, standardize on it and remove competitors; if none is, propose one accessible option that fits the stack in the Phase 0 report and do not install it before approval.
4. When you create a canonical component, migrate every usage and delete the old implementations in the same phase. No "new" and "old" versions living side by side beyond a phase boundary.
5. Work in small, reviewable increments: one PR (or commit series) per component or per page. The app must build, pass tests and remain usable after every merge.
6. Keep URLs stable. If a route moves, add a redirect.
7. Maintain `docs/ux-restructure/MIGRATION.md`: a checklist of every route and every component with its status (not started / in progress / migrated / deleted). Update it in every PR.
8. Build a `/design-system` demo route (or use Storybook if it already exists) that renders every primitive and composite in every state. It is the visual contract; a component that isn't on it isn't done.
9. Once the foundations exist, enforce the system with lint rules: no emoji characters in UI code; no raw `<table>`, `<button>`, `<select>` or `<input>` outside the design-system folder; no physical CSS properties (`margin-left`, `padding-right`, `left`, `right`, `text-align: left/right`) — logical properties only; no color values outside tokens.
10. Take before/after screenshots of every migrated page in both English (LTR) and Arabic (RTL) and attach them to the PR.
11. Do not change copy casually. Terminology changes come from the glossary agreed in Phase 0.

## 5. Phase 0 — Audit and plan (no product code changes)

Produce `docs/ux-restructure/AUDIT.md` with these sections. Be exhaustive; sample nothing.

**5.1 Route map.** Every route/page: path, title, purpose, main entity, primary action, which template it should use (section 8), estimated usage (ask the owner if unknown), notes.

**5.2 Component inventory.** Every implementation of: buttons, icon buttons, tables/lists, forms and inputs, modals/dialogs/drawers, filters/search, page headers, tabs, badges/pills, cards, empty states, toasts. For each: file path, where used, what it does differently from the others. Then one decision per component: keep as canonical / merge into canonical / delete.

**5.3 Upload and import inventory.** Every place a user can upload a file, paste data, import from mail, or bulk-add anything: entry point, input formats, where the parsing logic lives, validation, error feedback, success feedback. Identify shared parsing logic that must survive behind a common interface.

**5.4 Action inventory.** For every page, every visible button, link and icon action. Classify each as primary / secondary / overflow / row action / bulk action / remove-candidate, and count them. Flag pages with more than one primary or more than three visible secondaries.

**5.5 Status and enum inventory.** Every status value in code and data for orders, items, purchases, packages, deposits, leads, etc., with the label and color used in each place it appears. Note inconsistencies and typos (e.g. "recieved").

**5.6 Terminology glossary (draft).** Define once: Order vs Purchase vs Purchase order (PO) vs Item vs Package vs Shipment vs Delivery; Customer vs Lead; what Profile codes like `B19`, `B22`, `E-B15` mean; what Brain, Leluxe, Flags, Deposits and GAASH are. Propose the canonical English and Arabic term for each and list which UI labels must change.

**5.7 Bilingual/RTL audit.** Hard-coded left/right styles, string concatenation that breaks in Arabic, missing `dir`/bidi isolation on user text, untranslated strings, number/date formats not going through Intl, fonts without Arabic coverage.

**5.8 Proposed information architecture.** Start from section 6, validate it against 5.1 and real usage, and present the final sidebar with an old → new mapping for every current item.

**5.9 Design-system plan.** Tokens to add or consolidate, the component list (section 7) mapped to existing code, the lint rules you will add, the demo route location.

**5.10 Migration order and risks.** Page-by-page order (section 12), what could break, what needs owner decisions.

**5.11 Open questions** (section 15 plus anything you found).

Then **stop**. Post a summary of 10–20 lines, link the audit, list the decisions you need, and wait for approval.

## 6. Target information architecture (starting proposal — validate in Phase 0)

Current sidebar: Brain, Purchases, To order, Leluxe, Goals, Deposits, Orders, In cart, Leads, Customers, Package prep, Bulk search, GAASH mail, Flags; Insights: P&L, Activity. Fourteen flat items mixing entities, pipeline stages, tools and a separate business.

Proposed:

```
Overview   (optional — only if the owner wants a home page; otherwise the pipeline is the landing route)

Sales
  Leads
  Customers
  Orders

Fulfillment                   the pipeline, in order — one page with stage tabs
  To order → In cart → Purchases → Package prep   (each tab shows its count)

Shipping
  Tracking                    today's Bulk search, plus arrivals
  GAASH mail

Finance
  Deposits
  P&L

Insights
  Goals
  Activity

──────────────
Needs attention (badge)       replaces Flags: one queue of flagged, late and missing-data items
Brain                         [clarify with owner] — a top-bar utility or under Tools, not a top-level page
──────────────
Workspace: Otlobly ▾          Leluxe becomes a workspace/brand switcher, not a nav item
Settings · Language · User
```

Rules for the shell:

- At most 12 navigable items, in named, collapsible groups. Group order follows the flow of work: sell → fulfill → ship → account → learn.
- One icon set (e.g. Lucide or Phosphor), 20 px in the sidebar, 16 px inline. No emoji anywhere in the product.
- Count badges only on actionable queues (Needs attention, To order, In cart, Package prep) and only when the count is non-zero.
- Exactly one active-state treatment for navigation; never reuse it for anything else (today the same peach fill marks the active nav item and an expanded table row).
- The top bar holds global search (`/` shortcut), notifications, the language toggle and the user menu, in the same place on every page.
- The page header shows the group as a breadcrumb ("Fulfillment / Purchases") so users always know where they are.

If the owner prefers four separate pipeline pages over one page with stage tabs, keep them separate inside the Fulfillment group, sharing the exact same list template and columns.

## 7. Design system

### 7.1 Tokens (single source, e.g. `src/design-system/tokens`)

- **Color:** a neutral scale for surfaces, text and borders; the existing Otlobly orange as the one brand accent (primary actions, active nav, focus — nothing else); semantic tones `info`, `success`, `warning`, `danger`, each with background/text/border values meeting WCAG AA. No other colors in the UI.
- **Typography:** one sans-serif family with full Arabic coverage (if the current family lacks it, pair it with one Arabic family and set line-height per script); one monospace family for identifiers and tracking numbers only. Base 14 px for data, 13 px in compact tables; a five-step heading scale. Sentence case everywhere; no all-caps labels, including table headers.
- **Spacing:** 4 px base scale (4, 8, 12, 16, 24, 32, 48). Density: compact by default in lists (row height 40–44 px), comfortable in forms.
- **Radius:** two values (small for inputs/badges, medium for modals/panels). **Elevation:** two levels (raised for dropdowns/popovers, overlay for modals). Tables and page content are flat; no card-per-row shadows.
- **Motion:** 120–200 ms for open/close/expand only. Nothing animates on page load.

### 7.2 Primitives

`Button` (primary / secondary / ghost / danger; sm / md; loading state; icon-only variant requires `aria-label` and a tooltip), `Badge` (status, tone-driven), `AttentionBadge` (late / missing / blocked), `Tag`, `Input`, `NumberInput`, `Select`, `Combobox`, `DatePicker`, `Textarea`, `Checkbox`, `Switch`, `Tooltip`, `DropdownMenu`, `Tabs`, `Avatar`, `Skeleton`, `Toast`, `EmptyState`, `Stat`.

### 7.3 Composites

`AppShell`, `Sidebar`, `TopBar`, `PageHeader`, `FilterBar`, `DataTable` (with `RowExpansion`, `BulkActionBar`, `ColumnConfig`), `DetailDrawer`, `Modal`, `ConfirmDialog`, `FormLayout` (with `FormSection`, `FormFooter`), `ImportWizard`, `KpiRow`, `ActivityFeed`.

Each component gets a short entry in `docs/ux-restructure/DESIGN_SYSTEM.md`: what it is for, when not to use it, its props/slots, and a screenshot from the demo route.

## 8. Page templates

Every route must use exactly one of these. A custom layout requires owner approval and a written reason.

**T1 — List / queue** (Purchases, Orders, To order, In cart, Package prep, Customers, Leads, Deposits, Tracking, GAASH mail, Needs attention)

```
Breadcrumb
Title                                  [Secondary] [Secondary] [⋯]   [Primary]
Stat   Stat   Stat                                          Updated 3 min ago
[Tabs / saved views]
[Search ......] [Filter ▾] [Filter ▾] [+ Filter]       [Columns] [Density] [Export]
┌─ DataTable ─────────────────────────────────────────────────────────────────┐
│ header row (sticky)                                                          │
│ rows … (row click opens DetailDrawer or detail page; ⋯ menu for row actions) │
└──────────────────────────────────────────────────────────────────────────────┘
Pagination ("1–50 of 312")
Selection → BulkActionBar docked at the bottom
```

**T2 — Detail** (a purchase order, an order, a customer, a package): header with identifier, status badge, 3–5 key facts and the action group; tabs (Overview, Items, Packages, Payments, Activity); a side column for metadata. Nested lists inside a detail page are `DataTable` with headers, not custom rows.

**T3 — Form** (create/edit): single column, grouped sections with headings, labels above fields, inline validation, sticky footer with Cancel and the primary action. Use a `Modal` for up to 5 fields, a `DetailDrawer` for medium forms, a page for long ones.

**T4 — Dashboard / insights** (Overview, P&L, Goals, Activity): `KpiRow`, then a grid of charts and tables using the same table and stat components as everywhere else.

**T5 — Settings / utility** (Settings, and Brain if it stays a page): T3 layout with sections.

## 9. Component rules

### 9.1 PageHeader

- Title in sentence case; entity count and key metrics as separate `Stat` items, never as a middle-dot sentence. "41 orders · $9,664.11 outstanding · updated 9/6/2026, 10:35:57 AM" becomes two stats and a relative "Updated 3 min ago" with the absolute time in a tooltip — no seconds.
- Exactly one primary button. At most two visible secondaries. Everything else in `⋯`.
- Fix the count/label mismatch: the page called Purchases must count purchases (or be renamed), per the glossary.

### 9.2 Action hierarchy

- Page level: 1 primary + up to 2 secondary + overflow.
- Row level: at most three quick actions revealed on hover/focus, plus a `⋯` menu. Never a row of six icons.
- Bulk: actions appear in `BulkActionBar` only when rows are selected.
- Destructive actions use `ConfirmDialog` and are danger-toned.
- Verbs stay consistent through a flow: the button "Mark received" produces the toast "Marked as received" and the status "Received".
- Icon-only buttons always have a tooltip and `aria-label`.

### 9.3 DataTable

- **One fact per column.** No concatenated cells, no summary sentences inside cells, no name repeated across columns. On Purchases: ORDER = `PO-0017` (short hash muted, in mono); the "17 customers · 35 items · 12 packages" summary becomes three narrow numeric columns or one "Contents" column with a tooltip; TOTAL gets its own right-aligned column.
- Column types with fixed formatting: identifier (mono), text, number/currency (right-aligned, tabular figures), date (relative in lists with absolute tooltip), status (`Badge`), attention (`AttentionBadge`), actions.
- Sticky header; sticky first column; **status and actions pinned to the end so they are never scrolled out of view**; horizontal scroll with a visible scrollbar; per-column min/max widths.
- Truncate with an ellipsis and show the full value in a tooltip; truncation must respect text direction (Arabic truncates at its own end, not the visual left).
- Compact density by default, flat rows with hairline dividers, subtle hover, one selected-row treatment. Remove the per-row cards, shadows and gaps.
- Expandable rows: the expansion renders a nested `DataTable` with its own header row (e.g. Package · Items · Tracking · Customer · Arrival · Status · Actions) on a consistent grid — never ad-hoc rows with emoji counters.
- Sortable columns; `ColumnConfig` (show/hide/reorder, persisted per user); URL-synced sort/filter/page state; virtualize above roughly 200 rows.
- Loading = `Skeleton` rows; empty = `EmptyState` with the primary action; error = message with retry.
- Selection checkboxes in the first column wherever bulk actions exist.

### 9.4 Status and attention system

- One file (e.g. `design-system/status.ts`) maps every status value of every entity to `{ label, tone, icon? }`. UI code never hard-codes a status label or color. Derive the real value lists from the Phase 0 inventory; illustrative shape:

```ts
export const packageStatus = {
  ordered:   { label: 'Ordered',   tone: 'info' },
  shipped:   { label: 'Shipped',   tone: 'info' },
  received:  { label: 'Received',  tone: 'success' },
  delivered: { label: 'Delivered', tone: 'success' },
  cancelled: { label: 'Cancelled', tone: 'danger' },
} as const;
```

- Labels in sentence case, spelled correctly (fix "recieved"), translated through i18n.
- Attention states are separate from status: `AttentionBadge` with a small fixed vocabulary — `Late · 40 d`, `No tracking #`, `Missing name`, `Unpaid` — in warning/danger tones, each with a quick action where one exists. The same items feed the **Needs attention** queue.
- Empty values render as a muted `—`, never red. Red and orange are reserved for attention badges.

### 9.5 Numbers, currency, dates

- One `formatMoney(amount, currency)`, one `formatNumber`, one `formatDate` / `formatRelative`, all via `Intl` with the active locale.
- The base currency is shown once; a converted amount is secondary, muted, and carries its own currency code (e.g. `≈ 3,050 ILS` — never a second `$`).
- Decide once whether the Arabic UI uses Western (0–9) or Eastern Arabic numerals and apply it everywhere.

### 9.6 Forms and modals

- `FormLayout` only: labels above inputs, required marker, help text below, error text below in danger tone, focus moves to the first error on submit.
- Footer buttons: primary at the inline-end, Cancel beside it, destructive alone at the inline-start. Same order in every modal and page.
- `Modal` sizes sm/md/lg; header (title + close), body, footer. Focus trap, Esc closes, unsaved-changes guard on close.
- Confirm only destructive or irreversible actions; never routine saves.

### 9.7 Filters

- `FilterBar`: search input + filter chips (each opens a popover) + "Add filter" + "Clear all"; active filters visible as chips; state in the URL; saved views per page.
- The same filter types (status, date range, customer, profile, has-tracking) look and behave the same on every page.

### 9.8 ImportWizard — one flow for every upload and import

Every entry point found in 5.3 is replaced by `ImportWizard`:

1. **Source** — file drop zone (accepted formats listed), paste box, or connected source (e.g. GAASH mail). The same drop-zone component everywhere.
2. **Map** — detected columns → fields, remembered per import type.
3. **Validate** — preview with per-row errors and warnings; downloadable error report; "import valid rows only" option.
4. **Confirm** — summary of what will be created or updated.
5. **Result** — counts, a link to the affected list filtered to the imported rows, errors if any.

Existing parsers survive behind a shared adapter interface (illustrative; adapt to the stack):

```ts
interface ImportAdapter<Row> {
  id: string;                        // 'orders-csv' | 'tracking-numbers' | 'gaash-mail' …
  accepts: Array<'file' | 'paste' | 'source'>;
  parse(input: ImportInput): Promise<Row[]>;
  fields: FieldSpec[];               // drives the Map step
  validate(rows: Row[]): ValidationResult;
  commit(rows: Row[]): Promise<ImportResult>;
}
```

### 9.9 Copy

- Sentence case, plain verbs, active voice. Buttons say what happens ("Create purchase order", "Mark received"), not "Submit" or "OK".
- Errors say what went wrong and what to do; empty states say what to do first.
- Cryptic codes (Profile `B19`, `E-B15`) get a human label or a tooltip explaining them, per the glossary.

## 10. Bilingual and RTL

- `dir` and `lang` are set on `<html>` from the active locale; layout mirrors automatically via logical CSS properties (`margin-inline-start`, `inset-inline-end`, `text-align: start` …). No physical properties.
- Directional icons (chevrons, arrows, next/back) flip in RTL; non-directional icons don't.
- Any user-entered text (names, notes, addresses) rendered inside an LTR string is wrapped in `<bdi>` / `unicode-bidi: isolate`, and inputs and cells containing user text use `dir="auto"`. "1 item · مصطفى مسعود" must read correctly in both locales.
- Ellipsis truncation follows the text's own direction.
- Numbers, dates, currency and plurals go through the i18n/Intl layer; no string concatenation to build sentences.
- Arabic font metrics: check line-height, badge padding and row height with Arabic strings, not just Latin.
- The language toggle lives in the top bar or user menu on every page, in the same place.
- Every migrated page is screenshotted in Arabic before it is marked done.

## 11. Accessibility and keyboard

Visible focus rings on everything interactive; full keyboard operation of tables (arrow keys, Enter to open, Space to select), menus and dialogs; `aria-label` on all icon-only controls; AA contrast for text and badges; reduced motion respected; tooltips reachable by keyboard; landmarks (`nav`, `main`) in the shell; `/` focuses global search, `?` shows shortcuts.

## 12. Migration plan

Each phase ends with a checkpoint: summary, screenshots, updated MIGRATION.md, and a go/no-go from the owner.

**Phase 1 — Foundations.** Tokens; icon library; primitives; `AppShell`, `PageHeader`, `FilterBar`, `DataTable`, `Modal`/`DetailDrawer`, `FormLayout`, `ImportWizard` shell; status config; formatters; `/design-system` demo route; DESIGN_SYSTEM.md; lint rules at warn level (error level at the end of Phase 7).

**Phase 2 — Shell and navigation.** New grouped sidebar and top bar; redirects for moved routes; workspace switcher (if Leluxe is confirmed as a workspace); Needs attention queue (initially: existing Flags + late packages + missing tracking numbers).

**Phase 3 — Purchases as the reference implementation.** Rebuild Purchases on T1, fixing every item in section 14. This page becomes the model; get it approved before touching other lists.

**Phase 4 — Remaining list pages.** Orders, To order, In cart, Package prep, Customers, Leads, Deposits, Tracking (Bulk search), GAASH mail — each onto T1, sharing columns and filters wherever the entity is the same.

**Phase 5 — Details, forms, modals.** Every detail page onto T2, every create/edit onto T3; consolidate all dialogs onto `Modal`, `ConfirmDialog` and `DetailDrawer`.

**Phase 6 — Imports.** Every upload/import entry point onto `ImportWizard` via adapters; delete the old importers.

**Phase 7 — Insights and cleanup.** Overview (if wanted), P&L, Goals, Activity onto T4; delete every component marked "delete" in the audit; lint rules to error level; full RTL and keyboard pass; final before/after gallery.

## 13. Definition of done

- Every route uses one of T1–T5 and is marked migrated in MIGRATION.md.
- Zero implementations of table, button, input, modal, filter, header or import outside the design-system folder; lint enforces it.
- The sidebar has at most 12 items in named groups; no emoji anywhere in the product.
- Every status label and color comes from the status config; no typos; attention states use `AttentionBadge`.
- No table cell contains more than one fact; totals are right-aligned in their own column; status and actions are never cut off at any viewport of 1280 px or wider.
- One `ImportWizard`; all previous importers deleted; every import still works end to end.
- All pages work in Arabic RTL with no layout breaks, correct truncation and correct mixed-direction text.
- The parity checklist passes: create lead → convert to customer → create order → move to To order → add to cart → create PO → import tracking numbers → mark received → prep package → record deposit → see it in P&L and Activity (extend with whatever the audit finds).
- AUDIT.md, DESIGN_SYSTEM.md and MIGRATION.md are complete and current.

## 14. Purchases page — specific issues to fix in Phase 3

Observed on `/app` → Purchases:

1. Sidebar: 14 flat items, emoji icons, Goals in the main menu while P&L and Activity are under Insights, Leluxe (a brand) beside pipeline stages.
2. Header subtitle is a middle-dot sentence with a seconds-precision timestamp; no primary action visible in the header.
3. Page title "Purchases" but the count says "41 orders" — terminology conflict; Orders is also a separate page.
4. ORDER cell = ID + hash + a summary sentence; ORDER NAME and CUSTOMER repeat the same name (three copies per row).
5. TOTAL is concatenated into the CUSTOMER cell ("Waleed Kharma… · $827.55 · ≈ $6…"), so the TOTAL header does not sit over the numbers.
6. Two amounts both prefixed with "$" where the second is a conversion — ambiguous currency.
7. Truncation everywhere without tooltips; Arabic names truncated at the wrong end ("…ى مسعود نايف القدح").
8. STATUS column off-screen; pills cut to "sent no…", "cancel…", "recieve…"; no pinned column, no visible scrollbar.
9. "recieve…" is misspelled; casing is inconsistent between "40 DAYS LATE" and the lowercase status pills.
10. Four different "problem" treatments: red `—` for a missing order name, orange text "no GAASH #", an uppercase red pill for lateness, tone-colored status pills.
11. Rows rendered as separate cards with shadows and gaps — low density; an unexplained empty row/gap between the header and the first row.
12. The expanded PO shows nested package rows with emoji counters (📦1 …), thumbnails, mono tracking numbers and no header row; nested content does not align with the parent columns.
13. Profile codes (`B19`, `E-B15`) with no explanation.
14. The same peach fill marks the active nav item and the expanded row.
15. An icon-only "+" control at the end of the header row with no label.

## 15. Open questions for the owner (ask before Phase 1; do not guess)

1. What is **Brain**? A dashboard, an AI assistant, notes, a knowledge base?
2. What is **Leluxe** — a second brand run on the same data, or a separate workspace?
3. What are **Profile** codes (`B19`, `B22`, `E-B15`)? Buyer accounts? Retailer profiles?
4. Confirm the pipeline stages and their order: To order → In cart → Purchases → Package prep. Anything missing (e.g. "Arrived", "Delivered")?
5. Which three pages are used most, by whom, and on what screen sizes? Any mobile or tablet use?
6. Base currency and secondary currency? Western or Eastern Arabic numerals in the Arabic UI?
7. Is there an existing component library, Storybook, or design-token file? Any preferred library if not?
8. Should Flags become the "Needs attention" queue as proposed, or stay a manual flag list?
9. Are any pages or features planned for removal that should not be migrated?

## 16. How to report

- **After Phase 0:** a summary of 10–20 lines, a link to AUDIT.md, the proposed IA, the decisions needed. Then wait.
- **After each phase:** what changed (routes, components added and deleted), screenshots in LTR and RTL, MIGRATION.md progress, risks, and the plan for the next phase.
- **Never** report a page as migrated without a screenshot in both locales and a green parity check for its flows.
