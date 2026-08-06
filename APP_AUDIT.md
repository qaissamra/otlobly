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
| 🧠 Brain (`brain`) | · | · | ✅ UI_AUDIT B5 | · | · | · | · | test_brain |
| 📦 Purchases · orders tree | · | · | ✅ B-series | · | · | · | · | test_po_purchases_redesign, test_po_item_declutter, test_role_money |
| 📦 Purchases · packages (`pok`) | · | · | ✅ | · | · | · | · | part (redesign markers) |
| 📦 Purchases · products (`pop`) | · | · | ✅ | · | · | · | · | part |
| 📦 Purchases · customers | · | · | ✅ | · | · | · | · | — |
| 💡 To order (`needorder`) + quote tool | · | · | ✅ | · | · | · | · | test_notifications part; quote math — (F-002) |
| ⌚ Leluxe · dashboard | · | · | ✅ | · | · | · | · | test_leluxe (mirror) |
| ⌚ Leluxe · orders/products | · | · | ✅ | · | · | · | · | test_leluxe |
| ⌚ Leluxe · packages | · | · | ✅ | · | · | ⚠ F-005 filter | · | — (render untested) |
| ⌚ Leluxe · goal 🎯 | · | · | ✅ | · | · | · | · | test_leluxe_goal |
| 💵 Deposits (`deposits`) | · | · | ✅ B3 | · | · | · | · | part (ledger math in test_money_and_auth; endpoint —) |
| 🏠 Orders (`orders`) | · | · | ✅ B2 | · | · | · | ⚠ F-010 GWD col deferred | part (order code race, due chips) |
| 🛒 In cart (`incart`) | · | · | ✅ B3 | · | · | · | · | test_role_money part |
| 📣 Leads (`metaleads`) | · | · | ✅ B5 | · | · | · | · | — (modules untested) |
| 👤 Customers (`customers`) | · | · | ✅ B3+B6 | · | · | · | · | test_customer_id part |
| 🎁 Package prep (`pkgprep`) | · | · | ✅ B0 | · | · | · | · | test_pkgprep |
| 🔎 Bulk search (`bulksearch`) | · | · | ✅ | · | · | · | · | — |
| 📧 GAASH mail (5 tabs) | · | · | ✅ B4 | · | · | · | · | test_gaash_mail |
| 📊 P&L (`pnl`) | · | · | ✅ B5 | · | · | · | · | — ⚠ F-001 (money math untested) |
| 🕑 Activity (`activity`) | · | · | ✅ | · | · | · | · | part (recent() only) |
| ⚙️ Settings (every panel) | · | · | · | · | · | · | · | part (isolation, one gate, GAASH validator) |
| 👥 Team (`team`) | · | · | ⚠ F-009 non-LXT | · | · | · | · | test_users_scoping (backend) |
| 🗑 Trash (`trash`) | · | · | ✅ B3 | · | · | · | · | part (file isolation; restore/purge —) |
| (hidden) Catalog / Picking / quote | · | · | · | · | · | ⚠ F-007 revive-or-delete | ⚠ F-007 | — |
| 🏗 Platform console (5 views) | · | · | · | · | n/a super-admin | · | · | test_platform, test_provisioning, test_quotas |

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

## Batch roadmap v2

- [x] **Batch A0 (this PR)** — foundations: `run_all_tests.sh`, full-suite baseline run, this document, SKILL.md test-instruction fix.
- [ ] **Batch T1** — `test_pnl.py` + `test_estimate.py` (F-001, F-002): lock the two money-math modules.
- [ ] **Sweep 2a** — staff console part 1 (Brain, Purchases ×4, To-order+quote, Leluxe ×5, Deposits, Orders): fill Matrix A cells, file findings.
- [ ] **Sweep 2b** — staff console part 2 (remaining views + Settings panels + platform + hidden-views decision).
- [ ] **Sweep 3** — public pages + sw.js offline + AR/RTL (Matrix B).
- [ ] **Sweep 4** — flows/logic walk (Matrix C), incl. F-003 vocabulary proposal + F-006 store-race audit.
- [ ] **Fix batches** — sized and ordered by the ledger after the sweeps (P0/P1 first).

## Baseline test run

**2026-08-06 · main @ 9285904 · `bash run_all_tests.sh` → 36 passed · 0 failed**
(~2.5 min wall; slowest: test_gaash_mail 70s, test_otp_login 10s, test_leluxe 7s.
test_po_tracking_race.py joins as #37 when PR #83 merges.) Every future batch
re-runs this before merging; record failures here with the F-### they map to.
