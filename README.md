# Otlobly — Order Management

Front-half of the Otlobly concierge-buying pipeline: turn messy customer
requests into clean, priced, tracked orders that flow into ClickUp + a Google
Sheet, place them on Amazon via the existing Multilogin anti-detection
automation, and drive everything from one local dashboard.

Built the same way as `gaash-clickup-sync`: **stdlib Python + vanilla HTML, REST
only, JSON state, zero AI cost per run.** Money is in **USD** throughout.

### Two ways to run
- **Local single-user** (the original): `python3 dashboard.py` → http://localhost:8788.
  No login, JSON files. Fine for solo use.
- **Hosted multi-user** (`app.py`, Flask + SQLite): logins, roles (Admin / Sales /
  Fulfillment), and a customer portal — deploy to a managed platform (**see `DEPLOY.md`**).
  Run locally with `./.venv/bin/python app.py` → http://localhost:8789 (first visit →
  `/setup` to create the admin). **This is the source of truth going forward**; the local
  dashboard reads the legacy JSON and is kept only for quick solo use.

```
 Intake form / CSV  ──▶  normalize  ──▶  orders.json  ──┬─▶ ClickUp tasks
 (dashboard)            (phones,           (source        ├─▶ Google Sheet / CSV
                         links, ASIN)       of truth)     ├─▶ WhatsApp templates
                                                          ├─▶ picking list
                            order_placer (Multilogin) ────┘   money report
                            reads Amazon CHECKOUT total → +10% markup → write-back
```

## The pieces

| File | What it does |
|------|--------------|
| `normalize.py` | Clean phones (Arabic digits → `+970…`), Amazon links (strip junk, expand `a.co`, extract ASIN), dedupe. `--selftest`. |
| `ingest.py` | Import the legacy Google-Sheet CSV → `orders.json` (idempotent). |
| `store.py` | The `orders.json` model + load/save/upsert. |
| `pricing.py` | `apply_markup` / `price_from_checkout`; optional SerpAPI estimate. `--selftest`. |
| `report.py` | Live money/queue/status summary for the dashboard. |
| `messages.py` | Arabic WhatsApp templates + `wa.me` links (no sending, no email). |
| `clickup.py` | One ClickUp task per order. `--discover`, `--dry-run`. |
| `sheets.py` | Mirror to Google Sheet (`api`) or `orders_export.csv` (`csv`, default). |
| `dashboard.py` + `web/index.html` | Local dashboard: intake form, KPIs, money report, action queue, per-box load, orders table, picking list, **Profit / P&L page**. |
| `order_placer.py` | **Phase 2** — auto-place on Amazon, scrape the landed total, write the price back. Health-gated, capacity-aware, **stop-before-pay by default**. |
| `clickup_cost.py` | **P&L** — Amazon cost (COGS) from the *Otloble AMAZON → Orders* list (`Total Amount`, USD). `--dry-run`, `--selftest`. |
| `revenue.py` | **P&L** — customer revenue from the live Google Sheet or `orders.json`. `--dry-run`, `--selftest`. |
| `meta.py` | **P&L** — Meta ad spend via the Marketing API or manual `meta_spend.json`. `--selftest`. |
| `pnl.py` | **P&L** — combines revenue − Amazon cost − Meta = net profit, margin, cost/customer, by month. |
| `customers.py` | **CRM** — one profile per customer (WhatsApp, email, address, city, ID, VIP, notes, payment); orders + total-spent auto-derived. `--sync`. |
| `amazon_import.py` | **Auto-import** — Amazon link → image, title, price, seller, Prime, delivery, ASIN, options via SerpAPI (cached by ASIN). |
| `app.py` | **Hosted multi-user app** (Flask) — logins, roles (Admin/Sales/Fulfillment), all the above behind auth, + customer portal. See `DEPLOY.md`. |
| `db.py` + `migrate_json_to_db.py` | **SQLite** persistence for the hosted app; migrates the JSON stores in. |
| `auth.py` | Login + role permissions + field redaction (Sales/Fulfillment never see profit; Fulfillment never sees money). |
| `worker.py` | **Local worker** — polls the hosted app for PAID orders and places them via Multilogin (anti-detection stays on your Mac). |
| `purchases.py` | **Purchase orders** (supply side) — Amazon Order → packages → items, each item auto-matched to a customer by ASIN. |
| `clickup_po.py` | Push a PO to ClickUp *Otloble AMAZON / Orders* (parent `Order #` task + item subtasks). `--dry-run`. |
| `import_po.py` | Create a PO from extracted JSON — the hook for the "paste an Amazon screenshot" flow. |
| `clickup_import.py` | **Reverse of `clickup_po.py`** — pull existing Amazon orders FROM the ClickUp *Otloble AMAZON / Orders* list INTO the Purchases page (parent `Order #` task + item subtasks → a PO). Rebuilds packages by grouping items that share a GWD tracking #, keeps each item's ClickUp customer; skips order #s already in the tool. Wired to the **↙ Import from ClickUp** button. `--dry-run`. |
| `tracking.py` | GAASH (GWD) shipping status — same public-endpoint method as `gaash-clickup-sync`; powers the **🔎 Check shipping** button per package. |
| `trash.py` | **Recycle Bin** — soft-delete so nothing is lost. Deletes (PO / package / item) move to `trash.json` with a restore hint; **restore** puts them back, **purge/empty** removes for good. Kept forever until emptied. |
| `activity.py` | **Activity log** — append-only `activity.jsonl` of *who changed what*; `po_diff` turns a whole-PO save into granular events ("set NAME to B27", "set CUSTOMER NAME to …"). Powers the **🕕 Latest Activity** card + Activity page. |
| `estimate.py` | **Quick-quote estimator** — Amazon link → item price (SerpApi `amazon_product`) + a configurable shipping/import estimate + markup. Powers the bilingual **💰 Quote** page. (SerpApi can't read Amazon's real destination shipping/import — confirmed by a live probe — so those come from a rule you set.) |
| `settings.py` | Whitelisted business settings (markup %, estimator destination + shipping/import rules, customer-mode, business WhatsApp #) editable from the **⚙ Settings** page; persists to `config.json`. |
| `templates/order.html` | **Customer intake** (hosted, bilingual AR/EN) — a pre-filled `/order/<draft_id>` link from a quote; the customer adds name/phone/city/address → creates a REQUESTED order in **💡 Need to order**. |
| `az.py` | **AZ-tool bridge** — connects a box (B19…) to its Multilogin profile via the AZ app (`multilogin-claude-code`, :8765). Powers the **🖥** popup on each Purchases row: shows last IP / fraud score / proxy / run-budget, does a **live IP check** (`check_ip`), and **starts the profile** browser (`launch`, via the local agent on :45000) so you can fetch GAASH tracking manually. Local-machine only. |

### The connected order pipeline (no double data entry)
`① link → ② Quote (estimator) → ③ 🔗 intake link → ④ customer fills contact → 💡 Need to order →
⑤ record the Amazon purchase (Purchases PO) → orders auto-flip to ORDERED + inherit batch/box/arrival →
⑥ customer tracks delivery (order # + WhatsApp).` The product (image/price/ASIN) carries through, **Box &
Batch are assigned at PO time** (not on the order form), the **Amazon cost lives only on the PO**, and the
**delivery date = PO package arrival + `pipeline.delivery_buffer_days` (8)**. The supply→demand link is
`purchases.apply_to_orders` (matched-by-ASIN PO items flip their customer order to ORDERED).

## Setup

```bash
cp config.example.json config.json     # fill in IDs (see below)
cp .env.example .env                    # CLICKUP_API_TOKEN (+ optional keys)
```

1. **Import your existing sheet:**
   ```bash
   python3 ingest.py "~/Downloads/Otlilbly - Orders.csv"
   ```
2. **Open the dashboard:**
   ```bash
   python3 dashboard.py        # http://localhost:8788 (auto-opens)
   ```
   Add new orders from the **+ Add order** form (paste `a.co` links — they're
   expanded automatically). Change status / box inline. Generate a WhatsApp
   message from the per-row menu (opens the chat prefilled, copies the text).
3. **ClickUp:** create the custom fields on your orders list (recommended: the
   existing *AZ Orders Batch (2)* list), then:
   ```bash
   set -a; source .env; set +a
   python3 clickup.py --discover     # prints field + option IDs → paste into config.json
   python3 clickup.py --dry-run      # preview every write (works offline)
   python3 clickup.py                # apply
   ```

### Pricing — why it comes from checkout
The amount to collect is the **Amazon checkout total** (item + shipping + Import
Fees Deposit shown when shipping to Palestine) **+ markup (default 10%)**.
SerpAPI can't see destination shipping/import fees, so the authoritative number
is scraped by `order_placer.py` at the order-review step. Set the markup in
`config.json → pricing.markup_pct`.

## Amazon purchase orders (📦 Purchases)

The **supply side**: when you place a bulk Amazon order that covers several customers, record
it as a **purchase order** — `Order # → packages (by arrival, each = one GWD shipment) → items`.
Each item is **auto-matched to the customer who ordered it, by ASIN**. Then **Push to ClickUp**
recreates it in *Otloble AMAZON / Orders* (parent `Order #` task + a subtask per item, with
customer, box, and the package's tracking #).

- **Add manually:** 📦 Purchases → **+ Add purchase order** → order #, ship-to, box, totals →
  add packages (arrival + GWD) → paste each item's Amazon link/ASIN → it shows the matched customer.
- **From a screenshot:** paste your Amazon order-details image to me in chat → I extract the
  order #, packages, and items → `python3 import_po.py` creates the PO (auto-matched). No in-app AI cost.

```bash
python3 import_po.py order.json     # create a PO from extracted JSON
python3 clickup_po.py PO-0001 --dry-run   # preview the ClickUp structure
python3 clickup_po.py PO-0001             # push it
```

## Order-to-cash workflow (under a minute)

The dashboard drives the whole flow: **REQUESTED → QUOTED → PAID → ORDERED → SHIPPED
→ ARRIVED → DELIVERED**.

1. **Customer sends an Amazon link** → paste it in **+ Add order**, click **⤓ Fetch from
   Amazon** → title, image, price, Prime, delivery, ASIN auto-fill and the suggested
   quote (price + markup) lands in the amount field. *(Needs a `SERPAPI_KEY` in `.env`.)*
2. **Send the quote** → pick **Quote** from the row's WhatsApp menu (one-click `wa.me`).
   Status → QUOTED (timestamped).
3. **Payment confirmed** → set status **PAID** (timestamped).
4. **Place the Amazon order** → type the **Amazon #** in the order row; status → ORDERED.
5. **Shipping** → the existing GAASH sync / tracking updates move it SHIPPED → ARRIVED.
6. **Delivered** → status **DELIVERED**; the realized profit shows on the **P&L** page.

**👤 Customers** is the CRM: every customer's profile, order history, total spent, VIP
flag, and preferred payment — auto-built from orders (**Sync from orders**) and editable.

## Profit / P&L page

Open the dashboard and click **📊 P&L**. It shows, all in USD:
**Revenue (customers) − Amazon cost (COGS) − Meta ad spend = Net profit**, plus
gross/net margin, cost-per-customer, and a by-month breakdown.

Sources (each refreshed by its own script, or the page's **Refresh sources** button):
- **Revenue** — `config.pnl.revenue.mode`: `orders` (local `orders.json`) or `sheet`
  (your master Google Sheet; needs `GOOGLE_APPLICATION_CREDENTIALS` + `pnl.revenue.spreadsheet_id`
  and the `amount_column` name).
- **Amazon cost** — `clickup_cost.py` reads the *Otloble AMAZON → Orders* list's `Total Amount`
  field (labelled ILS but the values are USD). Needs `CLICKUP_API_TOKEN`.
- **Meta ad spend** — `config.pnl.meta.mode`: `manual` (`meta_spend.json`: `{"2026-06": 420}`)
  or `api` (Meta Marketing API).

```bash
python3 clickup_cost.py --dry-run    # Amazon cost from ClickUp (read-only)
python3 revenue.py                   # revenue → reports/revenue_cache.json
python3 meta.py                      # Meta spend → reports/meta_cache.json
python3 pnl.py                       # full P&L statement in the terminal
```

### Connect Meta Ads (for `meta.mode: "api"`)
1. **Ad account id** — Meta Ads Manager → the account dropdown (top-left) → `act_##########`.
2. **Token with `ads_read`** — *durable:* business.facebook.com → Business Settings → System
   Users → add/select a system user → **Generate token** → pick your app → check `ads_read`
   + `read_insights` → copy. *Quick test:* a Graph API Explorer token (~1–2h).
3. Put both in `.env`: `META_AD_ACCOUNT_ID=act_…` and `META_ACCESS_TOKEN=…`, then set
   `config.pnl.meta.mode` to `"api"`. Spend in a non-USD ad account is converted via `config.fx`.

## Phase 2 — auto-place (handle with care)

Needs the `multilogin-claude-code` environment (its `.venv` + a running
Multilogin agent) and each box's `folder_id`/`profile_id` in
`config.json → profiles`.

```bash
python3 order_placer.py --selftest        # offline: checkout parser
python3 order_placer.py --dry-run         # which box places which order (capacity)
python3 order_placer.py --limit 1         # price ONE order at the review page — NO buy
python3 order_placer.py --limit 1 --place # actually place ONE order
```

Every run: **ip_check → launch → leak_test** (fail-closed), respects each box's
`daily_cap`, and **stops at the review page** unless you pass `--place`.

## Scheduling (optional)
Keep ClickUp + the CSV in sync automatically:
```bash
chmod +x run.sh
cp com.otlobly.sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.otlobly.sync.plist   # every 3h
```

## Logo
The header shows a built-in Otlobly mark. Drop a `logo.png` (or `.svg`) into this
folder to use the real logo — the dashboard serves it automatically.

## Notes
- `orders.json`, `config.json`, `.env` are git-ignored (customer data + secrets).
- No email, anywhere — customer contact is WhatsApp only.
- Re-running `ingest.py` updates matching orders instead of duplicating them.
