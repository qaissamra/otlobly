---
name: design
description: Use when improving, redesigning, polishing, or auditing the look and feel of any Otlobly surface — the staff app (web/index.html), customer-facing pages (templates/, static/), or the /v2 public site — or when asked to "make X look better", "حسّن التصميم", fix visual inconsistency, or review UI/UX/accessibility.
---

# Otlobly Design

## Overview

One entry point for design work on Otlobly. It routes you to the right design-language source for the surface you're touching, then runs an audit → propose → implement → verify loop. The goal is consistency: every surface should look like it was built by one hand.

**Core principle: never invent a parallel primitive.** Every visual element you need (pill, header, field row, button, editable cell, column table) already has a canonical implementation. Find it and reuse it.

## Step 1 — Route by surface

| Surface | Read FIRST |
|---|---|
| Staff **board** views (Leluxe orders/products/packages, Purchases, Package prep, To-order) | `otlobly-board` skill §1 Design language + the "standard components" table in repo-root `UI_AUDIT.md` |
| Staff app, **non-board** (Goal, Brain, CRM, Settings, Docs tab, any web/index.html view) | `UI_AUDIT.md` standard-components table + tokens in [references/otlobly-design.md](references/otlobly-design.md) |
| **Customer-facing** pages (templates/*.html, static/style.css) and the **/v2** site (templates/v2/, static/v2/) | [references/otlobly-design.md](references/otlobly-design.md) — includes the known-fragmentation backlog |

For deep generic craft (typography scales, motion, color theory), additionally load the `impeccable` skill — it's the generic companion; this skill is the Otlobly-specific truth.

## Step 2 — Audit

Walk the target surface against [references/checklist.md](references/checklist.md) (accessibility, RTL, anti-slop, interaction states). Record findings as `file:line — issue`. If the user asked for improvement rather than an audit, still do a quick pass — it surfaces the highest-leverage fixes.

If the request is **audit-only**, stop here and report the findings — Steps 3–6 apply only when implementing. Defects already listed in the known-fragmentation backlog still get reported when they affect the audited surface; tag them "(known — backlog item N)".

## Step 3 — Propose small batches

One coherent batch per session per PR (the UI_AUDIT.md discipline). List the changes before making them; for anything customer-visible or brand-level (colors, fonts, logos), show the owner a before/after screenshot and get a yes before shipping.

## Step 4 — Implement in the existing idiom

- Staff app: pill helpers (`statusPill`/`tonePill`/`hexPill`/`solidPill`), `fld()` meta rows, `editCell()`, `openStore()`, `popMenu()`, the `LX_TABLES`/`LXT_COLS` column registry. Match comment density and naming.
- Customer pages: extend `static/style.css` tokens — do not add a new inline `:root`.
- Vanilla JS + CSS only. **No React, no Tailwind, no build step, no npm UI packages.**

## Step 5 — Verify live

Use the `.claude/launch.json` preview servers (`otlobly-app` :8789 for real pages, `otlobly-static-ui` :8790 for static web/). **Restart the Flask server after editing templates/*.html or web/index.html** (index.html is token-served via `branding.render_shell`; a stale server leaks raw `__BRAND_` tokens). Check both desktop and mobile widths — customers arrive from WhatsApp on phones. Screenshot the result as proof. For customer pages, verify RTL (`dir="rtl"`) renders correctly.

## Step 6 — Record

If a convention changed or a new standard component was born, update `UI_AUDIT.md` (staff app) or the fragmentation backlog in [references/otlobly-design.md](references/otlobly-design.md) (customer pages) so the next session inherits it.

## Hard rules

These come from owner decisions and the UI_AUDIT standing review flags — do not relitigate them:

- **No raw `<span class="pill" style=…>`** — use the pill helpers.
- **No hand-rolled inline-edit inputs** — use `editCell()`.
- **No new expand/open-state `Set`s** — use `openStore()`.
- **No literal `#fff4ee`** — use `var(--tint-accent)`.
- **No hand-drawn illustration SVGs** — unDraw for card-slot illustrations, Heroicons outline for small icons (owner rejected hand-drawn shapes).
- **Bilingual labels are Arabic-first**: `عربي · English` with ` · ` separator.
- **Customers never see carrier/Gerizim statuses** — customer-facing status text comes only from the otlobly_status mapping.
- **One accent**: `#ff5a1f` (`--accent`/`--orange`) is the brand orange everywhere; don't introduce competing accent colors.
