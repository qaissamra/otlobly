# Design audit checklist

Curated for Otlobly's stack (vanilla JS/CSS, Flask templates, no build step) from the Vercel web-interface-guidelines plus anti-slop principles. Walk the target surface and record findings as `file:line — issue`. Skip sections that don't apply to the surface (e.g. RTL for the staff app).

## Accessibility

- [ ] Visible focus state on every interactive element (`:focus-visible`); never `outline:none` without a replacement.
- [ ] Contrast meets APCA/WCAG — especially `--muted` text on tinted/pastel backgrounds.
- [ ] Hit targets ≥24px desktop / ≥44px mobile; label + checkbox/radio share one hit target, no dead zones.
- [ ] Icon-only buttons have `aria-label`; decorative icons get `aria-hidden`.
- [ ] Native semantics first: real `<button>`/`<a>`/`<label>`/`<table>` — no `<div onclick>` for navigation.
- [ ] Status is never color-only — pair pill colors with text (the `statusPill` label already does this; keep it).
- [ ] Headings are hierarchical; `<title>` matches the current context.
- [ ] Toasts / inline validation announce via polite `aria-live`.

## RTL & Arabic (customer surfaces)

- [ ] Page-level `dir` correct and `setLang()` flips `documentElement.dir` where language toggles exist.
- [ ] User-generated strings (names, product titles, addresses) get `dir="auto"`.
- [ ] Prefer logical properties (`margin-inline-start`, `padding-inline`, `inset-inline`) over left/right in new CSS; audit hard-coded left/right when a layout breaks in RTL.
- [ ] Directional icons (arrows, chevrons, "next") mirror under `html[dir=rtl]`.
- [ ] Numbers/dates render sensibly in Arabic context; tracking numbers and codes stay LTR (`dir="ltr"` or monospace span).

## Forms & input (customer pages especially)

- [ ] Mobile input `font-size ≥16px` (prevents iOS auto-zoom); never disable browser zoom.
- [ ] Correct `type`, `inputmode`, and `autocomplete` (phone fields: `type="tel" inputmode="tel"`).
- [ ] Never block paste; OTP/code fields must accept pasted codes.
- [ ] Validate after typing, not during — don't prevent input; errors inline next to the field, first error focused on submit.
- [ ] Submit buttons show a spinner while keeping their label; disable only after the request starts.
- [ ] Destructive actions get a confirm or an Undo window.

## States — every view has four

- [ ] **Empty** — designed, with a next step (no dead ends, no broken zero-state).
- [ ] **Loading** — skeleton/spinner that mirrors final layout (no layout shift).
- [ ] **Error** — human message + recovery action; API failures never leave a blank card.
- [ ] **Dense** — long Arabic names, 50-row lists, very long product titles: text truncates (`truncate`/`line-clamp`/`break-words`, flex children `min-width:0`) instead of exploding the layout.

## Anti-slop (design intent)

- [ ] Density budget: prefer whitespace over cramming; one idea per card/section.
- [ ] One accent (`#ff5a1f`) — no new competing accent colors; pastels are backgrounds, not accents.
- [ ] Headlines ≤3 lines; no nested container-in-container-in-container chrome; no decorative micro-UI that carries no information.
- [ ] Deliberate alignment to the existing grid/edges; numbers that get compared use `font-variant-numeric:tabular-nums`.
- [ ] Reuse the canonical component (pills, `fld()`, `.btn-*`, `.po-btn`) — a slightly-different bespoke copy of an existing component is a defect, not a design choice.
- [ ] Typography from the surface's existing scale (staff: 11/12.5/13.5/14/19 Inter; customer: Cairo/IBM Plex Sans Arabic 16 base) — no new ad-hoc sizes without reason.

## Motion & feedback

- [ ] Animate only `transform`/`opacity`; never `transition:all` or layout props (top/left/width/height).
- [ ] Honor `prefers-reduced-motion`.
- [ ] Tap feedback on primary buttons (the `.btn:active{scale(.98)}` pattern).
- [ ] Modals/drawers set `overscroll-behavior:contain`.

## Layout & performance

- [ ] Test at mobile width (390px), laptop, and wide; respect `env(safe-area-inset-*)` on customer pages.
- [ ] No horizontal scrollbars/overflow leaks; wide tables scroll inside their own container.
- [ ] Images have explicit dimensions (no CLS); below-fold images `loading="lazy"`.
- [ ] Long lists (>50 rows) render incrementally or paginate — the staff app is one file; don't make it slower.

## Review red flags (from UI_AUDIT.md — instant findings)

- A raw `<span class="pill" style=…>` anywhere.
- A hand-rolled inline-edit `<input>` instead of `editCell()`.
- A new open/expand-state `Set` instead of `openStore()`.
- A literal `#fff4ee` instead of `var(--tint-accent)`.
- A new inline `:root` palette in a template instead of `static/style.css` tokens.
