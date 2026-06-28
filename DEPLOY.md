# Putting Otlobly online (employee logins)

This hosts the **staff app** (`app.py`) so your team can log in from anywhere at a link.
It has three roles — **Admin / Sales / Fulfillment** — with the money/cost figures hidden
from Sales & Fulfillment.

We use **Render**. It's beginner-friendly, gives automatic HTTPS, and `render.yaml`
(already in this folder) sets everything up for you. Cost: about **$7/month** for an
always-on server with a **saved disk** so your data never resets.

> **What stays on your Mac:** the Amazon automation (Multilogin — the 🤖 tracking-fetch and
> order-placing) can't run in the cloud. The website handles orders, customers, quotes,
> purchases, P&L, and the customer portal; your Mac keeps doing the Amazon work and syncs up
> (see "The local worker" at the bottom).

---

## What I (Claude) already did
- Made all data save to one folder (`OTLOBLY_DATA_DIR`) so it can live on Render's saved disk.
- Wrote `render.yaml` (the deploy recipe) and a safe `.gitignore` (your passwords and customer
  data are **never** uploaded).
- Made the first commit locally. Your code is ready to push.

## What you need to do (≈15 minutes)

### 1. Create a GitHub account + repo (free)
GitHub stores the **code** (never your data or passwords — those are blocked).
1. Sign up at **github.com** (if you don't have an account).
2. Click **New repository** → name it `otlobly` → set it **Private** → **Create repository**.
   *(Don't add a README/.gitignore — we already have them.)*

### 2. Push the code
Easiest non-technical way: install **GitHub Desktop** (desktop.github.com) → *File → Add Local
Repository* → choose this `otlobly-orders` folder → *Publish*. Or, in the terminal, tell me the
repo URL and I'll run the push for you once you've signed in with `gh auth login` (or GitHub
Desktop).

### 3. Deploy on Render
1. Sign up at **render.com** (you can sign in with your GitHub account) and add a card.
2. **New ➜ Blueprint** → connect your `otlobly` repo. Render reads `render.yaml` and shows the
   plan (a Web Service + a 1 GB disk).
3. It will ask you to fill the secrets marked "sync: false" — type in whichever you use:
   - `CLICKUP_API_TOKEN` (for ClickUp sync + the Amazon-cost P&L)
   - `SERPAPI_KEY` (+ `_2`, `_3`) — optional, price estimates
   - `META_AD_ACCOUNT_ID` / `META_ACCESS_TOKEN` — optional, P&L ad spend
   *(Leave any you don't use blank.)*
4. Click **Apply / Create**. First build takes a few minutes.

### 4. First login = create the admin
Open your new link (e.g. `https://otlobly.onrender.com`). It opens a **"create admin"** screen —
pick the owner username + a strong password. That's your top account.

### 5. Add your employees
Log in as admin → **Users** → add each staff member with a role:
- **Sales** — create/quote orders, customers, WhatsApp. No cost/profit.
- **Fulfillment** — order queue, set status/tracking/Amazon #. No money totals.
- **Admin** — everything, including P&L and user management.

They each log in at the same link with their own username/password.

---

## Bringing your current data over (optional)
The server starts empty (fresh, clean). To import the orders/customers you already have, we run
`migrate_json_to_db.py` once against the server's disk — tell me when you're ready and I'll walk
you through it. Or just start fresh and add orders going forward.

## The local worker (placing Amazon orders) — later
On your Mac, once the site is up:
```bash
export OTLOBLY_API_URL=https://<your-app>.onrender.com
export OTLOBLY_WORKER_TOKEN=<the value Render generated for OTLOBLY_WORKER_TOKEN>
python3 worker.py --loop 120          # price PAID orders at checkout (no buy)
python3 worker.py --loop 120 --place  # actually place them
```
(Find the worker token in Render → your service → **Environment**.)

## Customer portal
Public, read-only, at `https://<your-app>.onrender.com/track` — customers enter their **order # +
WhatsApp number** to see status. Rate-limited; exposes no staff data.

## A custom domain later (e.g. app.otlobly.com)
Render → your service → **Settings → Custom Domains** → add it, then add the DNS record Render
gives you at your domain registrar. Tell me and I'll guide you.
