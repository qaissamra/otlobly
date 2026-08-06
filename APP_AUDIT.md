# APP_AUDIT — full-app testing, design & logic audit (master matrix)

Started 2026-08-06. Successor program to `UI_AUDIT.md` (its 8 design batches all
shipped 2026-08-04, PRs #66–#73). This doc adds three dimensions the UI program
didn't cover: **does it work** (tests + browser), **is the logic complete**
(missing steps/features per flow), and **is anything duplicated**.

Working convention (same as UI_AUDIT): audit fills cells → findings go to the
ledger with a rank → fixes ship in batches, ONE batch per session per PR →
after each merge update the cells here. Before any batch read
`.claude/skills/otlobly-board/SKILL.md`; before any merge `bash run_all_tests.sh`.

Legend: `✅ checked, good · ⚠ finding filed (F-###) · ❌ broken · ·` not yet
audited · `n/a` doesn't apply. Tests column: suite name = dedicated coverage,
`part` = partial (see §1 notes), `—` = no coverage.

## Matrix A — staff console (worktree preview, previewtmp login)

| View (setView) | Works | Errors | Design | Mobile | Roles | Logic | Dup | Tests |
|---|---|---|---|---|---|---|---|---|
| 🧠 Brain (`brain`) | ✅ 2a | ✅ | ✅ UI_AUDIT B5 | ✅ | · | ✅ | · | test_brain |
| 📦 Purchases · orders tree | ✅ 2a | ✅ | ✅ B-series | ✅ | ✅ 2b money:false verified live | · | · | test_po_purchases_redesign, test_po_item_declutter, test_role_money |
| 📦 Purchases · packages (`pok`) | ✅ 2a | ✅ | ✅ | ✅ pin 200 | · | ⚠ F-012 chips | · | part (redesign markers) |
| 📦 Purchases · products (`pop`) | ✅ 2a | ✅ | ✅ | · | · | · | · | part |
| 📦 Purchases · customers | ✅ 2a | ✅ | ✅ 2-tier meta ✓ | · | · | · | · | — |
| 💡 To order (`needorder`) + quote tool | ✅ 2a | ✅ | ✅ | · | · | · | · | test_notifications part; quote math — (F-002) |
| ⌚ Leluxe · Board (dashboard) | ✅ 2a | ✅ | ✅ | · | · | ⚠ F-013 $/₪ | · | test_leluxe (mirror) |
| ⌚ Leluxe · orders/products | ✅ 2a | ✅ | ✅ | · | · | · | · | test_leluxe |
| ⌚ Leluxe · packages | ✅ 2a header formula EXACT | ✅ | ✅ | · | · | ⚠ F-005 filter | · | — (render untested) |
| ⌚ Leluxe · goal 🎯 | ✅ 2a render | ✅ | ✅ | · | · | · | · | test_leluxe_goal |
| 💵 Deposits (`deposits`) | ✅ 2a Σ exact | ✅ | ✅ B3 | · | · | · | · | part (ledger math in test_money_and_auth; /api/payments endpoint —) |
| 🏠 Orders (`orders`) | ✅ 2a | ✅ | ✅ B2 | ✅ | · | ⚠ F-014 batch Σ | ⚠ F-010 GWD col deferred | part (order code race, due chips) |
| 🛒 In cart (`incart`) | ✅ 2b empty-state | ✅ | ✅ B3 | · | ✅ redacted | · | · | test_role_money part |
| 📣 Leads (`metaleads`) | ✅ 2b (unconfigured notice honest) | ✅ | ✅ B5 | · | · | · | · | — (modules untested) |
| 👤 Customers (`customers`) | ✅ 2b 23 rows | ✅ | ✅ B3+B6 | · | · | · | · | test_customer_id part |
| 🎁 Package prep (`pkgprep`) | ✅ 2b empty-state + fx | ✅ | ✅ B0 | · | · | · | · | test_pkgprep |
| 🔎 Bulk search (`bulksearch`) | ✅ 2b (2 found + 1 missing + Σ; dup GWD across POs → 2 rows, correct) | ✅ | ✅ | · | · | · | · | — |
| 📧 GAASH mail (6 tabs incl 🩺) | ✅ 2b all panes switch | ✅ | ✅ B4 | · | · | safe-mode banner honest | · | test_gaash_mail |
| 📊 P&L (`pnl`) | ✅ 2b ALL tiles = /api/pnl totals; equation exact; cross-view ✓ (To-order, batches) | ✅ | ✅ B5 | · | ✅ 403 for fulfillment | ⚠ F-015 undated Meta vs daily chart | · | — ⚠ F-001 (money math untested) |
| 🕑 Activity (`activity`) | ✅ 2b feed + filters | ✅ | ✅ | · | · | · | · | part (recent() only) |
| ⚙️ Settings (every panel) | ✅ 2b all panels render (read-only pass) | ✅ | · | · | · | · | · | part (isolation, one gate, GAASH validator) |
| 👥 Team (`team`) | ✅ 2b renders | ✅ | ⚠ F-009 non-LXT | · | · | ⚠ F-016 no remove/deactivate | · | test_users_scoping (backend) |
| 🗑 Trash (`trash`) | ✅ 2b 10 items | ✅ | ✅ B3 | · | · | · | · | part (file isolation; restore/purge —) |
| (hidden) Catalog / Picking / quote | ✅ 2b both render fully when forced | ✅ | · | · | · | ⚠ F-007 revive-or-delete | ⚠ F-007 | — |
| 🏗 Platform console (5 views) | ✅ 2b all render (MRR $29 = Brain strip) | ✅ | · | · | n/a super-admin | · | · | test_platform, test_provisioning, test_quotas |

## Matrix B — public / customer pages

| Page (route) | Works | Errors | Design | Mobile | AR/RTL | Offline | Logic | Tests |
|---|---|---|---|---|---|---|---|---|
| Landing `/` | · | · | · | · | · | n/a | · | — |
| Login `/login` `/setup` | · | · | · | · | · | n/a | · | part (otp, portal) |
| Order wizard `/order` | · | · | · | · | · | n/a | · | test_order_wizard; quote math — (F-002) |
| Draft `/order/<draft_id>` | · | · | · | · | · | n/a | · | — |
| Track `/track` | · | · | · | · | · | n/a | · | test_track_widget (logic; render —) |
| Portal `/account` (69KB) | · | · | · | · | · | n/a | · | test_customer_portal, test_otp_login (backend) |
| Pricing `/pricing` | · | · | · | · | · | n/a | · | — |
| Catalog `/catalog` | · | · | · | · | · | n/a | ⚠ F-007 | — |
| Request-ID `/id/<token>` | · | · | · | · | · | n/a | · | test_customer_id |
| Offline shell `sw.js` | · | · | n/a | n/a | n/a | · | · | — |

## Matrix C — business flows (the logic/process audit)

| Flow | Steps complete? | Manual→automate? | Missing feature? | Duplication? |
|---|---|---|---|---|
| Otlobly core: lead → quote → ⚡approve → To-order → PO → ship → GAASH → clearance → Gerizim → deliver → collect → P&L | · | · | · | · |
| Leluxe: ClickUp pull → packages → tracking → GASH STATUS mirror → RD → goal 🎯 | · | · | · | ⚠ F-003 status vocab ×4 |
| Deposits (عربون) lifecycle: take → apply → net → refund | · | · | · | · |
| Customer ID: request link → submit → attach → {id_number} mail token | · | · | · | · |
| GAASH-mail sequences: enroll → steps → reply/goal → cleared | · | · | · | · |
| Backup / restore: /api/backup → backup_pull.py → restore drill | · | · | · | · |
| Tenant/broker: provision → features → quotas → white-label | · | · | · | · |

## Findings ledger

Rank: **P0** broken/data-loss · **P1** wrong money math / security / logic gap ·
**P2** UX & design-language violation · **P3** polish / idea / owner decision.

| # | Rank | Finding | Status |
|---|---|---|---|
| F-001 | P1 | `pnl.py` (20KB, the P&L money math) has ZERO direct tests — only `/api/pnl` touched once for the Meta slot | open — Batch T1 |
| F-002 | P1 | `estimate.py` (16KB, the instant-quote money math incl. flat-8% + $8 floor) has ZERO direct tests | open — Batch T1 |
| F-003 | P1 | FOUR disagreeing "parcel is done" status sets: `alerts.STOP_DEFAULT`, `pkgprep.RECEIVED/DISPATCHED_STATUSES`, `gaash_mail._TERMINAL`+`deliver|complete|collect` regex, `lxIsDone` regex — no shared module; they already caused the sweep-scope bug (PR #80) | open |
| F-004 | P2 | `CU_STATUSES` (web/index.html ~:9420) hand-copied from ClickUp 2026-07-13, drifted from the live schema (missing `parcelto destination`/`az id`/`cleared customs`; carries variants the list lost) | open |
| F-005 | P2 | Packages-view "Where Status is" filter matches the ORDER's products, not the package's effective status (`lxFilteredOrders` group-match) — owner-visible surprise | open |
| F-006 | P1 | JSON-store lost-update pattern: purchases fixed (PR #83 re-load-merge); audit the SAME pattern in the orders store (`store.py`), `customers.py`, `trash.py`, `meta_leads.py` — any long writer that holds a snapshot across slow work | open |
| F-007 | P3 | Hidden-but-alive views: Catalog + Picking (+`quote`) — nav removed 2026-07-22, code/routes/loaders retained. Owner decides: revive, or delete the dead weight | owner call |
| F-008 | P2 | `lxIsDone` regex (`/deliver\|complete\|reci?eved/i`) false-positives on `not recieved rd` / `not recieved no rd` | open |
| F-009 | P3 | Team view never got the LXT treatment (UI_AUDIT left it "open by choice" — password-reset rows need the wf-exp pattern) | owner call |
| F-010 | P3 | Per-order GWD column on the Orders board deferred in UI_AUDIT Batch 2 (needs server-side purchases scan) | owner call |
| F-011 | P3 | No CI: tests run only when a session runs them. `run_all_tests.sh` added (this PR); consider a GitHub Action on push later | partial |
| F-012 | P2 | Purchases packages/products quick chips (`Late · N` …) count late **POs** while the view lists **packages** — Late·7 renders ~15 rows (all packages of late POs incl. non-late siblings; 8 packages are actually late). Same PO-level-vs-row-level family as F-005 — fix them together | open |
| F-013 | P2 | Leluxe **Board** tiles label ₪ sums with **$**: "Total value $72,113" = Σ ClickUp `Total Amount` (₪ everywhere else — packages header shows the same money as ₪74.8k), also "Avg order", "$ at risk". Fix must check what the 🎯 goal engine converts (it has fx) before relabeling | open |
| F-014 | P3 | Orders overview "By batch" rows include cancelled orders' amounts, so they sum to $4,472.43 under a "Total value $4,399.98" line — one panel, two definitions | open |
| F-015 | P3 | P&L: undated Meta spend (manual $250, ad_days 0) is deducted from the headline but absent from the by_day series — the daily chart's profit sums to gross $443.63 vs the $193.63 headline. Equation itself is honest; chart can't reconcile | open |
| F-016 | P3 | Team: staff accounts can be created but the UI offers NO remove/deactivate control (and `DELETE /api/users/<id>` 404s) — parallels the known no-delete-broker gap | open |

## Batch roadmap v2

- [x] **Batch A0 (this PR)** — foundations: `run_all_tests.sh`, full-suite baseline run, this document, SKILL.md test-instruction fix.
- [ ] **Batch T1** — `test_pnl.py` + `test_estimate.py` (F-001, F-002): lock the two money-math modules.
- [x] **Sweep 2a** — done 2026-08-06 (12 sub-views). Math audits all EXACT: header outstanding = Σ collect over non-collected; To-order total; PO card total; pkgEstTotal row; Leluxe packages header replayed via the view's own recipe (157 · 207 · ₪74,856.93); Deposits Σ; Brain tiles = /api/brain pipeline. Interactions: ⋯ menus position:fixed unclipped, filters narrow, two-tier customer meta intact. Phone 390px: no page H-overflow, bt-pin caps at exactly 200px and stays sticky. Console: zero errors across all views. Filed F-012/F-013/F-014. NOT covered: Roles column (needs a non-admin login — do in 2b), per-view inline-edit deep pass.
- [x] **Sweep 2b** — done 2026-08-06. In cart/Pkgprep honest empty states; Leads unconfigured notice; Customers 23 rows; Bulk search functional (Σ footer, amber missing row, duplicate GWD across two POs correctly yields two rows); GAASH mail 6 panes switch cleanly, safe-mode banner honest; P&L: every tile = /api/pnl totals, equation to the cent, cross-view consistency with To-order + Orders batches; Activity/Settings/Team/Trash render; platform console 5/5; hidden Catalog+Picking fully alive when forced (F-007 data point). ROLES verified live via a temp fulfillment login: /api/purchases money:false with all costs nulled, /api/po_image 403, /api/incart nulled, /api/pnl 403. Zero console errors. Filed F-015, F-016.
- [ ] **Sweep 3** — public pages + sw.js offline + AR/RTL (Matrix B).
- [ ] **Sweep 4** — flows/logic walk (Matrix C), incl. F-003 vocabulary proposal + F-006 store-race audit.
- [ ] **Fix batches** — sized and ordered by the ledger after the sweeps (P0/P1 first).

## Baseline test run

**2026-08-06 · main @ 9285904 · `bash run_all_tests.sh` → 36 passed · 0 failed**
(~2.5 min wall; slowest: test_gaash_mail 70s, test_otp_login 10s, test_leluxe 7s.
test_po_tracking_race.py joins as #37 when PR #83 merges.) Every future batch
re-runs this before merging; record failures here with the F-### they map to.
