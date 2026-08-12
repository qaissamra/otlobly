#!/usr/bin/env python3
"""
Otlobly hosted app — multi-user, role-based, customer portal.

Reuses the existing business modules (normalize, pricing, messages, report, pnl,
amazon_import, customers, clickup_cost/revenue/meta) but persists through db.py
(SQLite) and serves over Flask with logins + roles instead of the old localhost
stdlib server.

  Local dev:   ./.venv/bin/python app.py            # http://localhost:8789
  Production:  gunicorn app:app   (managed platform, HTTPS, env vars set)

First run with no users redirects to /setup to create the first admin.
"""

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote as _urlquote

from flask import (Flask, request, jsonify, redirect, url_for, render_template,
                   send_file, abort, session)
from flask_login import login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

import activity
import amazon_import
import auth
import az
import cfg
import customers as cust_mod
import db
import estimate
import settings as settings_mod
import goals
import google_login
import mailer
import memlog
import messages
import money
import meta_leads as meta_leads_mod
import normalize
import notify
import pnl as pnl_mod
import pricing
import product_import
import purchases
import quotas
import report as report_mod
import store
import tracking
import trash


def _user():
    """Display name of the logged-in user for the activity log / trash."""
    return getattr(current_user, "name", None) or getattr(current_user, "username", None) or "User"


def _olabel(o):
    name = (o.get("customer") or {}).get("name") or ""
    return f"{o.get('order_id')}" + (f" · {name}" if name else "")


def _polabel(po):
    return ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number") else po.get("po_id", "")

HERE = Path(__file__).parent


def load_env():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


load_env()
db.init_db()

import meta_sync
meta_sync.start()          # background 24/7 Meta ad-spend pull (no-op without API creds)
import alerts as alerts_mod
alerts_mod.start()         # owner Telegram alerts (no-op without bot creds)
import leluxe as leluxe_mod
leluxe_mod.start()         # Leluxe → ClickUp mirror pusher (no-op until configured)
import leluxe_goal
leluxe_goal.start()        # daily goal digest — no-op unless env LELUXE_DIGEST=1
leluxe_goal.start_autoheal()  # keep ordered_at populated (ungated — heals Render's all-NULL copy)
import telegram_bot
telegram_bot.start()       # owner command bot — no-op unless env LELUXE_TG_BOT=1
import gaash_mail
gaash_mail.migrate_v2()    # one-time: legacy 4-step settings chain → sequences-as-data
gaash_mail.migrate_decl_auto()   # filed declarations → written fresh on every email
gaash_mail.seed_followup_template()   # the hand-sent nudge, added once, then yours
gaash_mail.seed_wrong_decl_template()  # "Wrong declaration" correction note, same deal
gaash_mail.repair_name_ids()     # one-time: on-package names filed against a wrong ID
goals.repair_categories()        # one-time: 🏆 card names + the brand that card claims
gaash_mail.start()         # 📧 clearance-email sequencer — no-op unless env GAASH_MAILER=1
                           # (set ONLY on Render: the live DB is the single truth; the Mac's
                           #  local app has a stale copy and must never send)

app = Flask(__name__, template_folder="templates")
# Behind Render's single proxy hop: trust ONE X-Forwarded-For entry so the rate
# limiter (and logs) key on the real client IP, not the proxy's. x_for=1 reads the
# rightmost forwarded IP, which the proxy sets — a spoofed header can't win.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
# Session-cookie signing key. In production (OTLOBLY_SECURE set, i.e. the HTTPS
# deploy) refuse to boot on the built-in dev key — running with a public key would
# let anyone forge a logged-in session. Locally the dev default is fine.
_secret = os.environ.get("OTLOBLY_SECRET")
if not _secret:
    if os.environ.get("OTLOBLY_SECURE"):
        raise SystemExit("OTLOBLY_SECRET is not set — refusing to start in production "
                         "with a known key. Set it in the host's environment.")
    _secret = "dev-secret-change-me"
app.secret_key = _secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("OTLOBLY_SECURE")),
    # "Trust this device": only CUSTOMER logins set session.permanent, so this 180-day
    # window applies to the portal only — staff Flask-Login sessions stay browser-scoped.
    PERMANENT_SESSION_LIFETIME=timedelta(days=180),
)
auth.login_manager.init_app(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[])


@app.before_request
def _scope_current_business():
    """Tenant isolation: pin every DB read/write in this request to the right business.
    Staff → their business_id. A logged-in customer → the business we resolved from
    their phone at login (session['cust_business']). Everything else (worker, the
    initial public track/login lookups) → business 1 until the handler resolves and
    overrides it (see _business_for_phone)."""
    try:
        if current_user.is_authenticated:
            bid = current_user.business_id
            # Tatabu support view: business-1 admins may temporarily act AS a broker
            # tenant (set by /api/admin/broker/impersonate). Inert for everyone else —
            # a broker user's business_id is never 1, so a forged session key is dead.
            sv = session.get("support_view_bid")
            if sv and bid == 1 and current_user.has("admin_actions") and int(sv) != 1:
                bid = int(sv)
        elif session.get("cust_business"):
            bid = int(session["cust_business"])
        else:
            bid = 1
    except Exception:  # noqa: BLE001 — never let scoping break a request
        bid = 1
    db.set_current_business(bid)


# --------------------------------------------------------------------------- #
# Helpers: build orders/customers reusing existing logic, persist via db
# --------------------------------------------------------------------------- #
def make_order(**kw):
    code = db.next_order_code()
    return store.new_order({"seq": int(code.split("-")[1]) - 1}, **kw)


def make_customer(**kw):
    code = db.next_customer_code()
    return cust_mod.new_customer({"seq": int(code.split("-")[1]) - 1}, **kw)


def _cust_from_order(o):
    ph = store.primary_phone(o)
    cu = o.get("customer") or {}
    return make_customer(name=cu.get("name") or "",
                         whatsapp=ph["e164"] if ph else "",
                         address=cu.get("address", ""))


def _ensure_customer(o, existing=None):
    """Add an order's customer to the CRM if it's not already there — so it shows on
    the Customers / ID page. NEVER overwrites an existing profile (that would wipe a
    stored ID). Pass `existing` (a set of match_keys) when looping to avoid re-scanning."""
    c = _cust_from_order(o)
    if not c.get("match_key"):
        return
    if existing is None:
        existing = {x.get("match_key") for x in db.list_customers()}
    if c["match_key"] not in existing:
        db.upsert_customer(c)
        existing.add(c["match_key"])


def _backfill_customers_from_orders():
    """One-time: every order's customer becomes a CRM profile (intake/lead/bridge
    paths didn't create one), so To-order customers appear on the ID page."""
    existing = {x.get("match_key") for x in db.list_customers()}
    for o in db.list_orders():
        _ensure_customer(o, existing)


def _crm_customer_for_order(o):
    """The CRM customer record for an order (created if missing), matched by the
    order's match_key. Used to hang the ID number/image on the right profile."""
    _ensure_customer(o)
    key = _cust_from_order(o).get("match_key")
    if not key:
        return None
    return next((x for x in db.list_customers() if x.get("match_key") == key), None)


def _attach_customer_ids(rows):
    """Set id_number / has_id_number / has_id on order rows from the CRM, matched
    by phone. The ID lives on the customer (submitted once, reused), so it shows on
    every order + package for that person. Used by /api/report + /api/needorder."""
    idmap = {}
    for c in db.list_customers():
        core = normalize.phone_core(c.get("whatsapp") or "")
        if core:
            idmap[core] = (c.get("id_number") or "", bool(c.get("id_image")))
    for r in rows:
        core = normalize.phone_core(r.get("phone") or "")
        num, has_img = idmap.get(core, ("", False))
        r["id_number"] = num
        r["has_id_number"] = bool(num)
        r["has_id"] = has_img              # existing meaning: has an ID image on file
    return rows


def orders_db():
    return {"orders": db.list_orders()}


# Hosted app: Trash restores must land in the SQLite DB (the JSON store the
# default restorers write to is the retired local dashboard's).
def _db_restore_order(data, origin):
    db.upsert_order(data)
    return {"ok": True, "where": "Orders"}


def _db_restore_customer(data, origin):
    db.upsert_customer(data)
    return {"ok": True, "where": "Customers"}


trash._RESTORERS["order"] = _db_restore_order
trash._RESTORERS["customer"] = _db_restore_customer


def redact_report(rep):
    if current_user.has("view_money"):
        return rep
    for r in rep["orders"]:
        r["amount_to_collect_usd"] = None
        r["deposit_usd"] = None
        r["remaining_usd"] = None
    rep["summary"]["money"] = {k: None for k in rep["summary"]["money"]}
    return rep


def _redact_brain(payload):
    """Same idiom as redact_report: keep counts/structure, null the money."""
    if current_user.has("view_money"):
        return payload
    for sec in payload.get("sections", []):
        for it in sec.get("items", []):
            it["amount_usd"] = None
    for k in ("new_orders_value_usd", "collected_usd"):
        payload["results"][k] = None
    return payload


def redact_customers(rows):
    if not current_user.has("view_money"):
        for c in rows:
            c["total_spent_usd"] = None
            c["collected_usd"] = None
    return rows


def run_script(name, *args):
    cmd = [sys.executable, str(HERE / name), *args]
    try:
        p = subprocess.run(cmd, cwd=HERE, env=os.environ.copy(),
                           capture_output=True, text=True, timeout=600)
        tail = "\n".join((p.stdout or p.stderr).strip().splitlines()[-10:])
        return {"ok": p.returncode == 0, "output": tail}
    except Exception as e:  # noqa
        return {"ok": False, "output": str(e)}


# --------------------------------------------------------------------------- #
# Auth pages
# --------------------------------------------------------------------------- #
@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.count_users() > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        if u and len(p) >= 6:
            db.create_user(u, auth.hash_pw(p), "admin", request.form.get("name", u))
            return redirect(url_for("login"))
        return render_template("login.html", setup=True, error="Username + 6+ char password required.")
    return render_template("login.html", setup=True)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if db.count_users() == 0:
        return redirect(url_for("setup"))
    if request.method == "GET" and current_user.is_authenticated:
        return redirect(url_for("staff_app"))   # already in → straight to the dashboard
    if request.method == "POST":
        user = auth.verify(request.form.get("username", "").strip(),
                           request.form.get("password", ""))
        if user:
            login_user(user)
            session.pop("support_view_bid", None)   # a fresh login never inherits a support view
            db.audit(auth.actor(), "login", "user", user.username, "")
            return redirect(url_for("staff_app"))
        return render_template("login.html", error="Wrong username or password.")
    return render_template("login.html")


@app.after_request
def _revalidate_html(resp):
    """HTML pages (the dashboard + inline JS, /track, /account, /order) must revalidate on
    every load so a deploy shows up immediately — no more stale cached page after an update."""
    if (resp.headers.get("Content-Type") or "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# Which ENDPOINT is growing the process? Render kills this service for exceeding
# its 512 MB limit — memory climbs ~150 MB → ~470 MB within half an hour of every
# boot. The daemon probes (memlog.watch) cleared the background jobs, so the growth
# is request-driven; these two hooks attribute it. Two /proc reads per request,
# microseconds each, and memlog swallows its own failures.
@app.before_request
def _mem_before():
    request._mem_rss = memlog.rss_mb()


@app.after_request
def _mem_after(resp):
    memlog.note_request(request.endpoint or request.path,
                        getattr(request, "_mem_rss", None), memlog.rss_mb())
    return resp


@app.route("/healthz")
def healthz():
    """Always-200 health check for the host (independent of login/setup state) so
    the platform never restart-loops while the app is mid-boot or has no admin yet.
    Also reports this worker's memory, so the live baseline is one curl away
    without dashboard access (Render's 512 MB instance restarts when exceeded)."""
    mb = memlog.rss_mb()
    return (f"ok rss={mb:.0f}MB" if mb is not None else "ok"), 200


# The Otlobly mark, served as the browser-tab icon (favicon) for every page.
LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<rect width="100" height="100" rx="26" fill="#111"/>'
    '<circle cx="46" cy="52" r="24" fill="#fff"/>'
    '<circle cx="46" cy="52" r="8.5" fill="#111"/>'
    '<circle cx="72" cy="30" r="9" fill="#ff5a1f"/></svg>'
)


@app.route("/favicon.svg")
@app.route("/favicon.ico")
def favicon():
    resp = app.response_class(LOGO_SVG, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/logout")
@login_required
def logout():
    session.pop("support_view_bid", None)
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
def index():
    """The public landing page — for EVERYONE, staff sessions included, so the
    owner can always see what customers see. The staff dashboard lives at /app."""
    return render_template("landing.html", wa_number=_wa_business_number(),
                           sections=settings_mod.public_sections())


# web/index.html is a static, deploy-time file (~587 KB); the process restarts on
# every deploy, so read + decode it ONCE and reuse — not on every /app request,
# where the per-request disk read + decode was a needless memory/latency tax.
_INDEX_HTML = None


def _index_html():
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _INDEX_HTML = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    return _INDEX_HTML


@app.route("/app")
@login_required
def staff_app():
    """The staff dashboard (admin/sales/fulfillment). Logged-out visitors are
    bounced to /login by login_manager.login_view. The per-tenant brand (title,
    favicon, sidebar wordmark) is stitched in SERVER-SIDE so a broker never sees
    the Otlobly mark — not even a first-paint flash."""
    import branding
    html_text = _index_html()
    # db.current_business() (not the user's own id) so a Tatabu support view renders
    # the impersonated broker's shell — the owner sees exactly what the broker sees.
    brand = branding.resolve(db.current_business())
    return app.response_class(branding.render_shell(html_text, brand),
                              mimetype="text/html")


_SW_JS = None


@app.route("/sw.js")
def sw_js():
    """Offline-shell service worker (web/sw.js). Deliberately PUBLIC — the browser
    refetches this script on its own schedule, session or not, and a 302→login HTML
    answer would break worker updates; the file holds no secrets. no-cache so a
    deploy's new worker is picked up immediately (the text/html after_request hook
    doesn't cover application/javascript)."""
    global _SW_JS
    if _SW_JS is None:
        _SW_JS = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    resp = app.response_class(_SW_JS, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.route("/api/me")
@login_required
def me():
    import branding
    import features
    d = current_user.as_dict()
    bid = db.current_business()          # the EFFECTIVE tenant (support view aware)
    d["business"] = {"id": bid, "brand": branding.resolve(bid),
                     "features": features.resolve(bid), "tier": quotas.tier(bid)}
    if bid != getattr(current_user, "business_id", 1):
        biz = db.get_business(bid) or {}
        d["impersonating"] = {"business_id": bid, "name": biz.get("name") or f"#{bid}"}
    elif bid == 1 and _platform_admin():
        # The owner (and only the owner, not while impersonating) gets the Tatabu
        # platform-console brand block for the sidebar's second world.
        d["platform"] = {"sidebar_html": branding.platform_sidebar()}
    return jsonify(d)


@app.route("/api/quota")
@auth.require("view_orders")
def api_quota():
    """This tenant's plan + usage vs limits (soft — the dashboard shows an 'upgrade'
    nudge when over, but nothing is blocked). Otlobly (#1) is unlimited."""
    return jsonify(quotas.status(db.current_business()))


# --------------------------------------------------------------------------- #
# Orders / report
# --------------------------------------------------------------------------- #
@app.route("/api/report")
@auth.require("view_orders")
def api_report():
    rep = redact_report(report_mod.build(orders_db()))
    _attach_customer_ids(rep.get("orders") or [])   # id_number → Purchases order-map
    return jsonify(rep)


@app.route("/api/brain")
@auth.require("view_orders")
def api_brain():
    """The command-center landing payload: urgent / due / forgotten / money /
    7-day results for the CURRENT tenant (support-view aware via the contextvar).
    Feature and tier gating happen inside brain.build so a future Telegram digest
    gets identical output."""
    import brain
    return jsonify(_redact_brain(brain.build(db.current_business())))


def _pnl_range():
    # ?days=7 → last week; ?since=&?until=YYYY-MM-DD → explicit range. Default: all time.
    since = (request.args.get("since") or "").strip() or None
    until = (request.args.get("until") or "").strip() or None
    days = request.args.get("days", type=int)
    if days and not since:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return since, until


@app.route("/api/pnl")
@auth.require("view_pnl")
def api_pnl():
    since, until = _pnl_range()
    d = pnl_mod.build(since=since, until=until)
    import features
    if not features.has(db.current_business(), "meta_ads"):
        # Meta ads is a plan-linked feature (Pro): lower tiers get a P&L with the
        # whole Meta slot hidden — net profit is simply revenue − cost.
        t = d.get("totals") or {}
        t["meta_usd"] = 0
        t["net_profit_usd"] = t.get("gross_profit_usd", t.get("net_profit_usd"))
        t["net_margin_pct"] = t.get("gross_margin_pct")
        t["cost_per_customer_usd"] = None
        t["ad_days"] = 0
        d["sources"]["meta"] = {"connected": False, "hidden": True, "detail": "pro plan"}
    return jsonify(d)


@app.route("/api/pnl/drill")
@auth.require("view_pnl")
def api_pnl_drill():
    # The rows behind one P&L card (metric=revenue|to_order|cogs|customers|meta),
    # same window params as /api/pnl so the drill always matches the cards.
    since, until = _pnl_range()
    metric = request.args.get("metric", "")
    import features
    if metric == "meta" and not features.has(db.current_business(), "meta_ads"):
        return jsonify({"error": "Meta ads is a Pro-plan feature."}), 403
    try:
        return jsonify(pnl_mod.drill(metric, since=since, until=until))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/pnl/refresh", methods=["POST"])
@auth.require("admin_actions")
def api_pnl_refresh():
    # COGS is live from the Purchases page; force an immediate Meta pull (in-process).
    import meta
    out = meta.sync_once()
    return jsonify({"ok": True, "meta": {"total_usd": out.get("total_usd"),
                                         "error": out.get("last_error")}})


@app.route("/api/order", methods=["POST"])
@auth.require("edit_order")
def api_add_order():
    b = request.get_json(force=True, silent=True) or {}
    links = b.get("links") or []
    if isinstance(links, str):
        links = [l for l in links.splitlines() if l.strip()]
    phones = normalize.collect_phones(b.get("phone"), b.get("phone2"))
    items = normalize.parse_items(links, expand=True)
    prods = {(p.get("asin") or "").upper(): p for p in (b.get("products") or [])}
    for it in items:
        p = prods.get((it.get("asin") or "").upper())
        if p:
            it["image"] = p.get("image") or it.get("image")
            it["title"] = p.get("title") or it.get("title")
            it["item_usd"] = p.get("item_usd")
            it["serp_price_usd"] = p.get("serp_price_usd")      # internal SERP estimate (staff-only)
            it["serp_price_at"] = p.get("serp_price_at")
            it["qty"] = int(p.get("qty") or 1)
    notes = b.get("notes", "")
    if b.get("deposit_note"):
        notes = (notes + "  " if notes else "") + f"عربون/Deposit: {b['deposit_note']}"
    o = make_order(name=b.get("name", ""), phones=phones, address=b.get("address", ""),
                   city=b.get("city", ""), items=items, batch=b.get("batch"),
                   profile_box=b.get("profile_box") or None,
                   status=b.get("status", "REQUESTED"),
                   amount_to_collect_usd=(float(b["amount"]) if b.get("amount") else None),
                   notes=notes)
    db.insert_new_order(o, created_by=current_user.id)
    db.upsert_customer(make_customer(name=b.get("name", ""),
                                     whatsapp=phones[0]["e164"] if phones else "",
                                     address=b.get("address", "")))
    db.audit(auth.actor(), "create_order", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o), detail="new order", user=_user())
    _maybe_record_deposit(b, o)          # numeric deposit (₪) → ledger entry
    return jsonify({"ok": True, "order_id": o["order_id"]})


def _stamp(o):
    m = {"QUOTED": "quoted_at", "PAID": "paid_at", "DELIVERED": "delivered_at"}.get(o["status"])
    return {m: db.now_iso()} if m and not o.get(m) else {}


@app.route("/api/status", methods=["POST"])
@login_required
def api_status():
    if not (current_user.has("edit_order") or current_user.has("edit_fulfillment")):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order(b.get("id"))
    if not o or b.get("status") not in store.STATUSES:
        return jsonify({"ok": False})
    old = o.get("status")
    o["status"] = b["status"]
    o = db.update_order(o["order_id"], {"status": b["status"], **_stamp(o)}, auth.actor())
    if o:
        activity.log("set", "order", o["order_id"], _olabel(o), field="status",
                     old=old, new=o.get("status"), user=_user())
    return jsonify({"ok": bool(o)})


@app.route("/api/pricing")
@login_required
def api_pricing():
    return jsonify({"markup_pct": pricing.markup_pct()})


@app.route("/api/needorder")
@auth.require("view_orders")
def api_needorder():
    orders = db.list_orders()
    data = store.need_order(orders)
    # the extra groups so orders never just vanish from this page:
    placed = store.order_rows(orders, store.PLACED_STATUSES)
    placed.reverse()                                     # newest first
    data["ordered"] = placed
    cart = store.order_rows(orders, store.CART_STATUSES)
    cart.reverse()                                       # newest first
    data["in_cart"] = cart
    deleted = []
    for rec in reversed(trash.load().get("trash", [])):  # newest first
        if rec.get("kind") != "order":
            continue
        o = rec.get("data") or {}
        cust = o.get("customer", {}) or {}
        phs = cust.get("phones") or []
        deleted.append({
            "trash_id": rec.get("trash_id"), "order_id": o.get("order_id"),
            "customer": cust.get("name"), "phone": phs[0].get("e164") if phs else None,
            "amount_to_collect_usd": o.get("amount_to_collect_usd"),
            "deleted_at": (rec.get("deleted_at") or "")[:10], "deleted_by": rec.get("deleted_by"),
            "items": [{"title": it.get("title") or it.get("asin"), "image": it.get("image"),
                       "qty": it.get("qty") or 1} for it in (o.get("items") or [])][:6],
        })
    data["deleted"] = deleted
    # attach the customer's ID (number + image flag) to every order row, by phone
    _attach_customer_ids(data["orders"] + placed + cart)
    return jsonify(data)


def _incart_cost():
    return round(float(cfg.get(cfg.load(), "pnl.in_cart.cost_usd", 0) or 0), 2)


@app.route("/api/incart")
@auth.require("view_orders")
def api_incart():
    # The Amazon-cart staging list + the lump cart cost → live profit preview.
    data = store.in_cart(db.list_orders())
    cost = _incart_cost()
    data["cost_usd"] = cost
    data["profit_usd"] = round((data.get("total_usd") or 0) - cost, 2)
    # COGS/profit are admin-only; sale amounts need view_money (fulfillment: none)
    if not current_user.has("view_cost"):
        data["cost_usd"] = data["profit_usd"] = None
    if not current_user.has("view_money"):
        data["total_usd"] = data["deposits_usd"] = None
        data["money"] = False
        for o in data.get("orders", []):
            o["amount_to_collect_usd"] = o["deposit_usd"] = None
    return jsonify(data)


def _incart_set(ids, to_status, prev_field=True):
    """Move each order id to `to_status`. When entering the cart we stash the order's
    previous status so 'back to queue' can restore it; when leaving we consume it."""
    n = 0
    for oid in ids:
        o = db.get_order(oid)
        if not o:
            continue
        old = o.get("status")
        changes = {"status": to_status, **_stamp({**o, "status": to_status})}
        if to_status == "IN_CART":
            changes["cart_prev_status"] = old if old in store.STATUSES else "QUOTED"
        o2 = db.update_order(oid, changes, auth.actor())
        if o2:
            n += 1
            activity.log("set", "order", oid, _olabel(o2), field="status",
                         old=old, new=to_status, user=_user())
    return n


@app.route("/api/incart/add", methods=["POST"])
@auth.require("edit_order")
def api_incart_add():
    ids = (request.get_json(force=True, silent=True) or {}).get("ids") or []
    return jsonify({"ok": True, "moved": _incart_set(ids, "IN_CART")})


@app.route("/api/incart/remove", methods=["POST"])
@auth.require("edit_order")
def api_incart_remove():
    # Back to the To-order queue at the status the order held before the cart.
    ids = (request.get_json(force=True, silent=True) or {}).get("ids") or []
    n = 0
    for oid in ids:
        o = db.get_order(oid)
        if not o or o.get("status") != "IN_CART":
            continue
        old = o.get("status")
        back = o.get("cart_prev_status") or "QUOTED"
        if back not in store.PREORDER_STATUSES:
            back = "QUOTED"
        o2 = db.update_order(oid, {"status": back, "cart_prev_status": None}, auth.actor())
        if o2:
            n += 1
            activity.log("set", "order", oid, _olabel(o2), field="status",
                         old=old, new=back, user=_user())
    return jsonify({"ok": True, "moved": n})


@app.route("/api/incart/cost", methods=["POST"])
@auth.require("edit_order")
def api_incart_cost():
    b = request.get_json(force=True, silent=True) or {}
    try:
        cost = round(max(0.0, float(b.get("cost_usd") or 0)), 2)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad number"}), 400
    config = cfg.load()
    cfg.set_path(config, "pnl.in_cart.cost_usd", cost)
    cfg.save(config)
    return jsonify({"ok": True, "cost_usd": cost})


@app.route("/api/estimate", methods=["POST"])
@auth.require("view_orders")
def api_estimate():
    items = (request.get_json(force=True, silent=True) or {}).get("items") or []
    if not items:
        return jsonify({"error": "no items"}), 400
    return jsonify(estimate.estimate_cart(items))


@app.route("/api/alerts/run", methods=["POST"])
@auth.require("admin_actions")
def api_alerts_run():
    """Manual Telegram-alert pass (the Settings 'Run now' button + testing)."""
    import telegram
    if not telegram.configured():
        return jsonify({"ok": False, "error": "Telegram not configured — set the bot "
                                              "token & chat id in Settings first"})
    return jsonify({"ok": True, "sent": alerts_mod.run_once()})


@app.route("/api/alerts/telegram_test", methods=["POST"])
@auth.require("admin_actions")
def api_alerts_telegram_test():
    """{detect:true, token?} → find the owner's chat id (they must press START
    on the bot first); otherwise send a test message with the saved creds."""
    import telegram
    b = request.get_json(force=True, silent=True) or {}
    if b.get("detect"):
        cid = telegram.detect_chat_id(b.get("token"))
        return jsonify({"ok": bool(cid), "chat_id": cid,
                        "error": None if cid else "no messages yet — open the bot in "
                                                  "Telegram and press START, then retry"})
    return jsonify(telegram.send(b.get("text") or "✅ تنبيهات اطلبلي تعمل · Otlobly alerts connected"))


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    if request.method == "GET":
        if not current_user.has("view_orders"):
            abort(403)
        full = settings_mod.read()
        if not current_user.has("admin_actions"):
            # employees never see markup/fx/clearance/alerts config; sales
            # (view_money) keeps the pricing keys its quote tool computes with
            keep = {"card_flags", "card_labels", "customer_mode", "custom_fields",
                    "tracking_status_map", "tracking_default_label",
                    "tracking_otlobly_map", "employee_status_map", "public_sections"}
            if current_user.has("view_money"):
                keep |= {"markup_pct", "ils_per_usd", "pkg_ils_per_usd",
                         "destination", "shipping_rule", "import_rule"}
            full = {k: v for k, v in full.items() if k in keep}
        return jsonify(full)
    if not current_user.has("admin_actions"):
        abort(403)
    body = request.get_json(force=True, silent=True) or {}
    out = settings_mod.apply(body)
    db.audit(auth.actor(), "update_settings", "settings", "*", "")
    return jsonify({"ok": True, "settings": out})


@app.route("/api/quote", methods=["POST"])
@auth.require("edit_order")
def api_quote():
    b = request.get_json(force=True, silent=True) or {}
    try:
        total = float(b.get("total"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "enter the Amazon checkout total"})
    o = db.get_order(b.get("id"))
    if not o:
        return jsonify({"ok": False, "error": "not found"})
    old = o.get("amount_to_collect_usd")
    amount = pricing.apply_markup(total)
    changes = {"amount_to_collect_usd": amount,
               "checkout": {**(o.get("checkout") or {}), "order_total_usd": total}}
    if o.get("status") == "REQUESTED":
        changes["status"] = "QUOTED"
        changes["quoted_at"] = o.get("quoted_at") or db.now_iso()
    o2 = db.update_order(o["order_id"], changes, auth.actor())
    activity.log("set", "order", o["order_id"], _olabel(o2),
                 field="amount_to_collect_usd", old=old, new=amount,
                 detail=f"quoted from Amazon checkout ${total:.2f}", user=_user())
    return jsonify({"ok": True, "amount": amount, "markup_pct": pricing.markup_pct()})


@app.route("/api/assign", methods=["POST"])
@auth.require("edit_fulfillment")
def api_assign():
    b = request.get_json(force=True, silent=True) or {}
    old = (db.get_order(b.get("id")) or {}).get("profile_box")
    o = db.update_order(b.get("id"), {"profile_box": (b.get("box") or "").strip() or None},
                        auth.actor())
    if o:
        activity.log("set", "order", o["order_id"], _olabel(o), field="profile_box",
                     old=old, new=o.get("profile_box"), user=_user())
    return jsonify({"ok": bool(o)})


@app.route("/api/amazon_number", methods=["POST"])
@auth.require("edit_fulfillment")
def api_amazon_number():
    b = request.get_json(force=True, silent=True) or {}
    old = (db.get_order(b.get("id")) or {}).get("amazon_order_number")
    o = db.update_order(b.get("id"),
                        {"amazon_order_number": (b.get("amazon_order_number") or "").strip() or None},
                        auth.actor())
    if o:
        activity.log("set", "order", o["order_id"], _olabel(o), field="amazon_order_number",
                     old=old, new=o.get("amazon_order_number"), user=_user())
    return jsonify({"ok": bool(o)})


@app.route("/api/import")
@auth.require("edit_order")
def api_import():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "no url"})
    # Any retailer: Amazon via SerpAPI, AliExpress/SHEIN/iHerb/other via a free OG scrape.
    d = product_import.import_product(url)
    if not d.get("error") and not d.get("cached"):     # a real lookup → count vs the quota
        quotas.bump_search(db.current_business())
    return jsonify(d)


@app.route("/api/item_price")
@auth.require("view_orders")
def api_item_price():
    """INTERNAL-only current SERP item price for a link/ASIN, for the To-order page's
    private price tracking. Never shown to the customer. Returns {price_usd, asin, title}
    or {error}. Uses a fresh SerpAPI pull (refresh=True) so 'latest' is genuinely current
    rather than a stale cache hit — fetches are deliberate, staff-initiated clicks."""
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "no url"}), 400
    d = amazon_import.import_product(url, refresh=True)
    if d.get("error"):
        return jsonify({"error": d["error"], "asin": d.get("asin")})
    return jsonify({"price_usd": d.get("price_usd"), "asin": d.get("asin"),
                    "title": d.get("title")})


@app.route("/api/extract_asin")   # link -> ASIN, NO SerpApi credit (string parse + a.co expand)
@auth.require("edit_order")
def api_extract_asin():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "no url"}), 400
    clean = normalize.clean_amazon_url(url, expand=True)
    return jsonify({"asin": normalize.extract_asin(clean), "clean_url": clean})


# --------------------------------------------------------------------------- #
# Catalog: staff-curated products → the public /catalog page + cart checkout.
# SerpAPI is only ever hit on staff clicks: add is cache-first (a known ASIN
# costs nothing), refresh_price is an explicit one-credit pull. The public
# endpoints serve stored rows and never fetch.
# --------------------------------------------------------------------------- #
@app.route("/api/catalog")
@auth.require("view_orders")
def api_catalog_list():
    return jsonify({"items": db.list_catalog()})


@app.route("/api/catalog", methods=["POST"])
@auth.require("edit_order")
def api_catalog_add():
    """Paste a product link (any retailer) → title/image/price auto-fetched → catalog
    row. Display price defaults to the markup-applied quote; staff can override."""
    b = request.get_json(force=True, silent=True) or {}
    url = (b.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    d = product_import.import_product(url)             # Amazon: SerpAPI · others: OG scrape
    if d.get("error"):
        return jsonify({"ok": False, "error": d["error"]}), 400
    if not d.get("cached"):                            # a real lookup → count vs the quota
        quotas.bump_search(db.current_business())
    try:
        price = round(float(b.get("price_usd")), 2) if b.get("price_usd") not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    item_id = db.add_catalog_item({
        "asin": d.get("asin"), "amazon_url": d.get("link") or url,
        "title": (b.get("title") or d.get("title") or "").strip(),
        "image": d.get("image"),
        "base_price_usd": d.get("price_usd"),
        "price_usd": price if price is not None else d.get("suggested_quote_usd"),
        "category": (b.get("category") or "").strip() or None,
        "note": (b.get("note") or "").strip() or None,
        "created_by": getattr(current_user, "id", None),
    })
    it = db.get_catalog_item(item_id)
    db.audit(auth.actor(), "catalog_add", "catalog", str(item_id), d.get("asin") or url)
    activity.log("added", "catalog", str(item_id), (it.get("title") or url)[:80], user=_user())
    return jsonify({"ok": True, "item": it})


@app.route("/api/catalog/update", methods=["POST"])
@auth.require("edit_order")
def api_catalog_update():
    b = request.get_json(force=True, silent=True) or {}
    it = db.get_catalog_item(b.get("id"))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    changes = {k: b[k] for k in ("title", "image", "price_usd", "category", "note",
                                 "active", "sort") if k in b}
    if "price_usd" in changes:
        try:
            changes["price_usd"] = round(float(changes["price_usd"]), 2)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad price"}), 400
    if "active" in changes:
        changes["active"] = 1 if changes["active"] else 0
    db.update_catalog_item(it["id"], changes)
    db.audit(auth.actor(), "catalog_update", "catalog", str(it["id"]),
             ", ".join(changes.keys()))
    return jsonify({"ok": True, "item": db.get_catalog_item(it["id"])})


@app.route("/api/catalog/delete", methods=["POST"])
@auth.require("edit_order")
def api_catalog_delete():
    b = request.get_json(force=True, silent=True) or {}
    it = db.get_catalog_item(b.get("id"))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.delete_catalog_item(it["id"])
    db.audit(auth.actor(), "catalog_delete", "catalog", str(it["id"]),
             (it.get("title") or "")[:80])
    activity.log("removed", "catalog", str(it["id"]), (it.get("title") or "")[:80], user=_user())
    return jsonify({"ok": True})


@app.route("/api/catalog/refresh_price", methods=["POST"])
@auth.require("edit_order")
def api_catalog_refresh_price():
    """Deliberate one-credit SerpAPI pull (same philosophy as /api/item_price) —
    updates the internal base price; staff decide whether to change the display price."""
    b = request.get_json(force=True, silent=True) or {}
    it = db.get_catalog_item(b.get("id"))
    if not it:
        return jsonify({"ok": False, "error": "not found"}), 404
    d = amazon_import.import_product(it.get("amazon_url") or it.get("asin") or "", refresh=True)
    if d.get("error"):
        return jsonify({"ok": False, "error": d["error"]}), 502
    db.update_catalog_item(it["id"], {"base_price_usd": d.get("price_usd")})
    return jsonify({"ok": True, "base_price_usd": d.get("price_usd"),
                    "suggested_price_usd": d.get("suggested_quote_usd"),
                    "item": db.get_catalog_item(it["id"])})


@app.route("/catalog")
def catalog_page():
    return render_template("catalog.html", wa_number=_wa_business_number())


@app.route("/pricing")
def pricing_page():
    """Public: service fees / plans (imported Replit design)."""
    return render_template("pricing.html", wa_number=_wa_business_number(),
                           sections=settings_mod.public_sections())


@app.route("/order")
def order_request_page():
    """Public: the 'اطلب الآن' funnel — plan + Amazon links + WhatsApp number.
    (Coexists with /order/<draft_id>, the quotation-confirm page.)"""
    return render_template("order_request.html", wa_number=_wa_business_number(),
                           sections=settings_mod.public_sections())


@app.route("/api/quote/request", methods=["POST"])
@limiter.limit("6 per minute")
def api_quote_request():
    """Public: a website quote request → an unpriced REQUESTED order in the
    To-order queue. Staff price it (💲) and send the quotation link back on
    WhatsApp (🔗). The chosen payment plan rides on the order like `source`."""
    b = request.get_json(force=True, silent=True) or {}
    plan = b.get("plan") if b.get("plan") in ("prepaid", "zero_risk") else "zero_risk"
    phones = normalize.collect_phones(b.get("phone"))
    if not (b.get("name") or "").strip() or not phones:
        return jsonify({"error": "الاسم ورقم واتساب صالح مطلوبان · "
                                 "Name and a valid WhatsApp number are required."}), 400
    raw = b.get("links") if isinstance(b.get("links"), list) else []
    links = [str(l).strip() for l in raw if str(l).strip().lower().startswith("http")][:15]
    if not links:
        return jsonify({"error": "أضف رابط منتج واحداً على الأقل · "
                                 "Add at least one product link."}), 400
    items = normalize.parse_items(links, expand=False)   # a.co → needs_expand for the queue
    if not items:
        return jsonify({"error": "لم نتعرف على الروابط — تأكد أنها من أمازون · "
                                 "Links not recognized — make sure they're Amazon links."}), 400
    o = make_order(name=b.get("name", ""), phones=phones, address="", city="",
                   items=items, status="REQUESTED", amount_to_collect_usd=None,
                   notes="🌐 طلب تسعير من الموقع · website quote request")
    o["source"] = "website"
    o["payment_plan"] = plan
    db.insert_new_order(o)
    _ensure_customer(o)
    db.audit({"username": "customer"}, "quote_request", "order", o["order_id"], plan)
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail=f"website quote request · {plan}", user="customer")
    return jsonify({"ok": True, "order_id": o["order_id"]})


# ── /order wizard v2: the lead is captured BEFORE the plan is chosen ───────── #
LEAD_NOTE = "🌐 طلب تسعير من الموقع · لم يختر الباقة بعد · website quote lead"
QUOTE_NOTE = "🌐 طلب تسعير من الموقع · website quote request"


def _valid_lead_order(o, token):
    """A wizard endpoint may only touch its own order: website-sourced, still
    REQUESTED, and holding the lead token issued at create time."""
    return (o and o.get("source") == "website" and o.get("status") == "REQUESTED"
            and o.get("lead_token")
            and hmac.compare_digest(str(o["lead_token"]), str(token or "")))


@app.route("/api/quote/lead", methods=["POST"])
@limiter.limit("6 per minute")
def api_quote_lead():
    """Public: screen 1 of the /order wizard (links + name + phone) → saved as a
    REQUESTED order IMMEDIATELY, before the plan screen — so the lead survives even
    if the customer abandons there. Re-POSTing with order_id+token (back + متابعة,
    or a refreshed session) updates the SAME order instead of duplicating."""
    b = request.get_json(force=True, silent=True) or {}
    phones = normalize.collect_phones(b.get("phone"))
    name = (b.get("name") or "").strip()
    if not name or not phones:
        return jsonify({"error": "الاسم ورقم واتساب صالح مطلوبان · "
                                 "Name and a valid WhatsApp number are required."}), 400
    raw = b.get("links") if isinstance(b.get("links"), list) else []
    links = [str(l).strip() for l in raw if str(l).strip().lower().startswith("http")][:15]
    if not links:
        return jsonify({"error": "أضف رابط منتج واحداً على الأقل · "
                                 "Add at least one product link."}), 400
    items = normalize.parse_items(links, expand=False)   # a.co → needs_expand for the queue
    if not items:
        return jsonify({"error": "لم نتعرف على الروابط — تأكد أنها من أمازون · "
                                 "Links not recognized — make sure they're Amazon links."}), 400
    if b.get("order_id"):
        o = db.get_order(str(b["order_id"]))
        if _valid_lead_order(o, b.get("token")):
            cust = dict(o.get("customer") or {})
            cust["name"], cust["phones"] = name, phones
            db.update_order(o["order_id"], {
                "items": items, "customer": cust,
                "signature": store.signature(normalize.customer_key(phones, name), items)})
            _ensure_customer(o)
            db.audit({"username": "customer"}, "quote_lead_update", "order", o["order_id"], "")
            activity.log("set", "order", o["order_id"], _olabel(o),
                         detail="website lead edited (back + continue)", user="customer")
            return jsonify({"ok": True, "order_id": o["order_id"], "token": o["lead_token"]})
        # stale token / order already advanced → fall through to a fresh create
    o = make_order(name=name, phones=phones, address="", city="",
                   items=items, status="REQUESTED", amount_to_collect_usd=None,
                   notes=LEAD_NOTE)
    o["source"] = "website"
    o["payment_plan"] = ""                     # set by /api/quote/plan; empty → no staff badge
    o["lead_token"] = secrets.token_urlsafe(16)
    db.insert_new_order(o)
    _ensure_customer(o)
    db.audit({"username": "customer"}, "quote_lead", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail="website quote lead · no plan yet", user="customer")
    return jsonify({"ok": True, "order_id": o["order_id"], "token": o["lead_token"]})


@app.route("/api/quote/plan", methods=["POST"])
@limiter.limit("12 per minute")
def api_quote_plan():
    """Public: screen 2 of the /order wizard — record the chosen plan on the lead
    order. Token-gated; idempotent while the order is still REQUESTED."""
    b = request.get_json(force=True, silent=True) or {}
    plan = b.get("plan")
    if plan not in ("prepaid", "zero_risk"):
        return jsonify({"error": "bad plan"}), 400
    o = db.get_order(str(b.get("order_id") or ""))
    if not _valid_lead_order(o, b.get("token")):
        return jsonify({"error": "not found"}), 404     # generic — no token oracle
    cust = dict(o.get("customer") or {})
    if (cust.get("notes") or "") == LEAD_NOTE:
        cust["notes"] = QUOTE_NOTE                      # lead → full request; custom notes kept
    db.update_order(o["order_id"], {"payment_plan": plan, "customer": cust})
    db.audit({"username": "customer"}, "quote_plan", "order", o["order_id"], plan)
    activity.log("set", "order", o["order_id"], _olabel(o), field="payment_plan",
                 new=plan, detail="website plan chosen", user="customer")
    return jsonify({"ok": True})


@app.route("/api/catalog/public")
@limiter.limit("30 per minute")
def api_catalog_public():
    """Customer-safe fields ONLY — no amazon_url/asin (don't route buyers around
    us) and no base_price_usd (the margin is nobody's business)."""
    return jsonify({"items": [{"id": it["id"], "title": it["title"], "image": it["image"],
                               "price_usd": it["price_usd"], "category": it["category"],
                               "note": it["note"]}
                              for it in db.list_catalog(active_only=True)]})


@app.route("/api/catalog/checkout", methods=["POST"])
@limiter.limit("6 per minute")
def api_catalog_checkout():
    """Public: the website cart → a REQUESTED order tagged source='website', so
    staff can always tell it apart from the manually-sent draft-link orders.
    Prices and the total are taken from the DB — never from the client."""
    b = request.get_json(force=True, silent=True) or {}
    phones = normalize.collect_phones(b.get("phone"))
    if not (b.get("name") or "").strip() or not phones:
        return jsonify({"error": "الاسم ورقم جوال صالح مطلوبان · Name and a valid phone are required."}), 400
    wanted = b.get("items") if isinstance(b.get("items"), list) else []
    rows, amount = [], 0.0
    for w in wanted[:30]:
        it = db.get_catalog_item((w or {}).get("id"))
        if not it or not it.get("active"):
            continue                                    # hidden/deleted → drop
        try:
            qty = max(1, min(20, int((w or {}).get("qty") or 1)))
        except (TypeError, ValueError):
            qty = 1
        rows.append((it, qty))
        amount += (it.get("price_usd") or 0) * qty
    if not rows:
        return jsonify({"error": "السلة فارغة · Cart is empty."}), 400
    items = []
    for it, qty in rows:
        src = it.get("amazon_url") or (f"https://www.amazon.com/dp/{it['asin']}" if it.get("asin") else "")
        parsed = normalize.parse_items([src], expand=False) if src else []
        item = parsed[0] if parsed else {"asin": it.get("asin"), "clean_url": src or None}
        item.update({"title": it.get("title"), "image": it.get("image"), "qty": qty,
                     "item_usd": it.get("price_usd")})
        if it.get("base_price_usd") is not None:
            # keep the staff To-order internal-price column meaningful
            item["serp_price_usd"] = it["base_price_usd"]
            item["serp_price_at"] = it.get("updated_at") or db.now_iso()
        items.append(item)
    o = make_order(name=b.get("name", ""), phones=phones, address=b.get("address", ""),
                   city=b.get("city", ""), items=items, status="REQUESTED",
                   amount_to_collect_usd=round(amount, 2) if amount else None,
                   notes="🌐 طلب من متجر الموقع · website order")
    o["source"] = "website"
    db.insert_new_order(o)
    _ensure_customer(o)                    # website customers join the CRM / ID page too
    db.audit({"username": "customer"}, "website_order", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail="website catalog order", user="customer")
    return jsonify({"ok": True, "order_id": o["order_id"]})


# ── Multilogin "AZ tool" bridge (the 🖥 profile popup + 🤖 auto-get-tracking) ──
# These reach the LOCAL AZ tool on 127.0.0.1:8765, so they only do real work when
# this app runs on the Mac next to Multilogin. On the server they return a clean
# "not reachable" message (the frontend just shows it). Fulfillment-level action.
def _az_user():
    try:
        return current_user.username
    except Exception:
        return None


@app.route("/api/az/profile")
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_profile():
    fresh = request.args.get("fresh", "0") in ("1", "true", "yes")
    return jsonify(az.profile_info(request.args.get("box", ""), force=fresh))


@app.route("/api/az/rotate_status")
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_rotate_status():
    return jsonify(az.rotate_status(request.args.get("job_id", "")))


@app.route("/api/az/track_fetch_status")
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_track_fetch_status():
    return jsonify(az.track_fetch_status(request.args.get("job_id", "")))


@app.route("/api/az/ip", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_ip():
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(az.check_ip(b.get("box", "")))


@app.route("/api/az/launch", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_launch():
    b = request.get_json(force=True, silent=True) or {}
    res = az.launch(b.get("box", ""))
    if res.get("ok"):
        activity.log("set", "purchase", b.get("box", ""), "Profile " + b.get("box", ""),
                     detail="started Multilogin profile", user=_az_user())
    return jsonify(res)


@app.route("/api/az/stop", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_stop():
    b = request.get_json(force=True, silent=True) or {}
    res = az.stop(b.get("box", ""))
    if res.get("ok"):
        activity.log("set", "purchase", b.get("box", ""), "Profile " + b.get("box", ""),
                     detail="closed Multilogin profile", user=_az_user())
    return jsonify(res)


@app.route("/api/az/rotate", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_rotate():
    b = request.get_json(force=True, silent=True) or {}
    box = b.get("box", "")
    res = az.rotate_start(box, b.get("target_risk", 25), b.get("max_tries", 8))
    if res.get("job_id"):
        activity.log("set", "purchase", box, "Profile " + box,
                     detail="rotating IP", user=_az_user())
    return jsonify(res)


@app.route("/api/az/track_fetch", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("multilogin")
def api_az_track_fetch():
    b = request.get_json(force=True, silent=True) or {}
    box = b.get("box", "")
    res = az.track_fetch_start(box, b.get("items", []), b.get("max_items", 12))
    if res.get("job_id"):
        activity.log("set", "purchase", box, "Profile " + box,
                     detail="auto-fetching Amazon tracking", user=_az_user())
    return jsonify(res)


@app.route("/api/message")
@auth.require("view_orders")
def api_message():
    o = db.get_order(request.args.get("id", ""))
    if not o:
        return jsonify({"error": "not found"}), 404
    return jsonify(messages.render(o, request.args.get("kind", "status_update")))


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
@app.route("/api/customers")
@auth.require("view_customers")
def api_customers():
    odb = orders_db()
    rows = sorted((cust_mod.enrich(c, odb) for c in db.list_customers()),
                  key=lambda c: -(c["total_spent_usd"] or 0))
    return jsonify({"customers": redact_customers(rows),
                    "payment_methods": cust_mod.PAYMENT_METHODS})


@app.route("/api/customer", methods=["POST"])
@auth.require("manage_customers")
def api_customer():
    b = request.get_json(force=True, silent=True) or {}
    c = make_customer(name=b.get("name", ""), whatsapp=b.get("whatsapp", ""),
                      email=b.get("email", ""), address=b.get("address", ""),
                      city=b.get("city", ""), vip=bool(b.get("vip")),
                      notes=b.get("notes", ""),
                      payment_method=b.get("payment_method", "Cash on delivery"))
    # MERGE guard: db.upsert_customer replaces data_json wholesale, so a profile
    # save that omits fields (the edit form has no ID inputs; board inline edits
    # post the record as-is) must never wipe what's already on file — keep the
    # existing customer_id/created_at and any stored ID image/number.
    existing = next((x for x in db.list_customers()
                     if x.get("match_key") and x.get("match_key") == c.get("match_key")), None)
    if existing:
        c["customer_id"] = existing.get("customer_id") or c["customer_id"]
        c["created_at"] = existing.get("created_at", c["created_at"])
        for keep in ("id_image", "id_number"):
            if existing.get(keep) and not c.get(keep):
                c[keep] = existing[keep]
    db.upsert_customer(c)
    db.audit(auth.actor(), "save_customer", "customer", c["customer_id"], "")
    activity.log("set", "customer", c["customer_id"], c.get("name") or c["customer_id"],
                 detail="saved profile", user=_user())
    return jsonify({"ok": True, "customer_id": c["customer_id"]})


@app.route("/api/customers/sync", methods=["POST"])
@auth.require("manage_customers")
def api_customers_sync():
    odb, n = orders_db(), 0
    existing = {c["match_key"] for c in db.list_customers()}
    for o in odb["orders"]:
        ph = store.primary_phone(o)
        c = make_customer(name=o["customer"]["name"],
                          whatsapp=ph["e164"] if ph else "",
                          address=o["customer"].get("address", ""))
        if c["match_key"] not in existing:
            db.upsert_customer(c)
            existing.add(c["match_key"])
            n += 1
    return jsonify({"ok": True, "created": n, "total": len(db.list_customers())})


@app.route("/api/orders/delete", methods=["POST"])
@auth.require("edit_order")
def api_orders_delete():
    """Bulk-delete orders → move each to Trash (restorable), then drop from the DB."""
    b = request.get_json(force=True, silent=True) or {}
    ids = [str(i) for i in (b.get("ids") or []) if i]
    tdb = trash.load()
    n = 0
    for oid in ids:
        o = db.get_order(oid)
        if not o:
            continue
        name = (o.get("customer") or {}).get("name", "")
        trash.add(tdb, "order", f"{o['order_id']} · {name}", o, actor=_user())
        activity.log("deleted", "order", o["order_id"], f"{o['order_id']} · {name}",
                     detail="moved to Trash", user=_user())
        db.delete_order(o["order_id"])
        n += 1
    trash.save(tdb)
    db.audit(auth.actor(), "delete_orders", "orders", ",".join(ids[:20]), f"{n} → trash")
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/order/edit", methods=["POST"])
@auth.require("edit_order")
def api_order_edit():
    """Edit an order: replace its product list (edit / add / remove items) and optionally
    the customer name + amount. Used by the To-order page's Edit button."""
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order(str(b.get("order_id") or ""))
    if not o:
        return jsonify({"ok": False, "error": "order not found"}), 404
    changes = {}
    if isinstance(b.get("items"), list):
        new_items = []
        for it in b["items"]:
            link = (it.get("link") or it.get("clean_url") or "").strip()
            asin = (it.get("asin") or "").strip().upper() or (normalize.extract_asin(link) if link else None)
            if not (link or (it.get("title") or "").strip() or it.get("image")):
                continue                                   # skip blank rows
            new_items.append({
                "raw_url": link or it.get("raw_url"),
                "clean_url": (normalize.clean_amazon_url(link) if link else it.get("clean_url")) or (link or None),
                "asin": asin,
                "title": (it.get("title") or "").strip() or None,
                "qty": max(1, int(it.get("qty") or 1)),
                "image": it.get("image") or None,
                "item_usd": it.get("item_usd"),
                "serp_price_usd": it.get("serp_price_usd"),     # internal SERP estimate (staff-only)
                "serp_price_at": it.get("serp_price_at"),
            })
        changes["items"] = new_items
    if b.get("amount") not in (None, ""):
        try:
            changes["amount_to_collect_usd"] = round(float(b["amount"]), 2)
        except (TypeError, ValueError):
            pass
    cust = dict(o.get("customer") or {})
    cust_changed = False
    if (b.get("name") or "").strip():
        cust["name"] = b["name"].strip()
        cust_changed = True
    for fld in ("city", "address"):
        # present-as-string means "set it" (empty string deliberately clears);
        # an absent key leaves the field untouched
        if isinstance(b.get(fld), str):
            cust[fld] = b[fld].strip()
            cust_changed = True
    if cust_changed:
        changes["customer"] = cust
    if not changes:
        return jsonify({"ok": False, "error": "nothing to change"}), 400
    db.update_order(o["order_id"], changes, auth.actor())
    activity.log("set", "order", o["order_id"], _olabel(o), field="products",
                 detail="edited order", user=_user())
    _maybe_record_deposit(b, o)          # optional quick deposit (₪) from the Edit modal
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Deposits / payments ledger (عربون) — enter in ₪, stored + reported in $.
# --------------------------------------------------------------------------- #
def _ils_rate():
    try:
        return float(cfg.get(cfg.load(), "fx.ils_per_usd", 3.7)) or 3.7
    except Exception:
        return 3.7


def _to_usd(amount, currency):
    """Convert an entered amount to (amount_usd, fx_rate). USD passes through."""
    if (currency or "ILS").upper() == "USD":
        return money.to_usd(amount), 1.0
    rate = _ils_rate()
    return money.div(amount, rate), rate


def _recompute_order_deposit(order_code):
    """Sync the order's cached deposit_usd to the net of its ledger rows."""
    if not order_code:
        return None
    net = db.deposit_total_for_order(order_code)
    o = db.get_order(order_code)
    if o and round(o.get("deposit_usd") or 0, 2) != net:
        db.update_order(order_code, {"deposit_usd": net})
    return net


def _order_for_phone(core, orders=None):
    """The best order to attach a customer's deposit to, matched by phone: the
    newest still-open (REQUESTED/QUOTED/PAID) order, else the newest of any."""
    if not core:
        return None
    mine = []
    for o in (orders if orders is not None else db.list_orders()):
        if any(normalize.phone_core(p.get("e164") or p.get("raw") or "") == core
               for p in (o.get("customer", {}).get("phones") or [])):
            mine.append(o)
    if not mine:
        return None
    pending = [o for o in mine if o.get("status") in ("REQUESTED", "QUOTED", "PAID", "IN_CART")]
    pool = pending or mine
    pool.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    return pool[0]["order_id"]


def _link_unlinked_deposits():
    """Self-heal: deposits recorded for a customer (phone) but with no order # get
    attached to that customer's order, so the To-order badge + remaining show up."""
    stray = [p for p in db.list_payments()
             if not p.get("order_code") and p.get("customer_phone")]
    if not stray:
        return
    orders = db.list_orders()
    touched = set()
    for p in stray:
        oc = _order_for_phone(p["customer_phone"], orders)
        if oc:
            db.set_payment_order(p["id"], oc)
            touched.add(oc)
    for oc in touched:
        _recompute_order_deposit(oc)


def _record_payment(*, amount, currency, order_code=None, name="", phone_core=None,
                    kind="deposit", paid_at=None, note=None):
    """Shared by /api/payment and the order create/edit quick-deposit fields."""
    amount_usd, rate = _to_usd(amount, currency)
    kind = kind if kind in ("deposit", "refund", "collect") else "deposit"
    pid = db.add_payment({
        "paid_at": (paid_at or "").strip() or None, "order_code": order_code,
        "customer_phone": phone_core, "customer_name": name, "kind": kind,
        "currency": (currency or "ILS").upper(), "amount_entered": round(float(amount), 2),
        "fx_rate": rate, "amount_usd": amount_usd, "note": (note or "").strip() or None,
        "created_by": getattr(current_user, "id", None), "created_by_name": _user(),
    })
    net = _recompute_order_deposit(order_code)
    label = order_code or (name or "customer")
    activity.log("recorded", "payment", str(pid),
                 f"{kind} ${amount_usd} · {label}", user=_user())
    db.audit(auth.actor(), "record_payment", "payment", str(pid),
             f"{kind} {amount} {(currency or 'ILS')} = ${amount_usd} on {label}")
    return {"id": pid, "amount_usd": amount_usd, "fx_rate": rate, "order_deposit_usd": net}


def _maybe_record_deposit(b, o):
    """Quick-deposit from the Add-order form / Edit modal: b['deposit'] (₪) → ledger."""
    try:
        amt = float(b.get("deposit"))
    except (TypeError, ValueError):
        return
    if amt <= 0:
        return
    cust = o.get("customer", {}) or {}
    phs = cust.get("phones") or []
    core = (normalize.phone_core(phs[0].get("e164") or phs[0].get("raw") or "")
            if phs else None)
    _record_payment(amount=amt, currency=b.get("deposit_currency") or "ILS",
                    order_code=o["order_id"], name=cust.get("name") or "",
                    phone_core=core, kind="deposit", note=b.get("deposit_note"))


@app.route("/api/payment", methods=["POST"])
@auth.require("edit_order")
def api_payment():
    b = request.get_json(force=True, silent=True) or {}
    try:
        amount = float(b.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "amount required"}), 400
    if amount <= 0:
        return jsonify({"ok": False, "error": "amount must be greater than 0"}), 400
    order_code = (b.get("order_id") or "").strip() or None
    name = (b.get("name") or "").strip()
    phone_core = None
    if order_code:
        o = db.get_order(order_code)
        if not o:
            return jsonify({"ok": False, "error": "order not found"}), 404
        cust = o.get("customer", {}) or {}
        name = name or cust.get("name") or ""
        phs = cust.get("phones") or []
        if phs:
            phone_core = normalize.phone_core(phs[0].get("e164") or phs[0].get("raw") or "")
    elif b.get("phone"):
        phone_core = normalize.phone_core(b["phone"])
    # No order # given? attach to the customer's pending order (matched by phone),
    # so the deposit shows on the To-order card and reduces the remaining.
    if not order_code and phone_core:
        order_code = _order_for_phone(phone_core)
        if order_code and not name:
            oo = db.get_order(order_code) or {}
            name = (oo.get("customer", {}) or {}).get("name") or name
    res = _record_payment(amount=amount, currency=b.get("currency"), order_code=order_code,
                          name=name, phone_core=phone_core, kind=b.get("kind"),
                          paid_at=b.get("paid_at"), note=b.get("note"))
    return jsonify({"ok": True, **res})


@app.route("/api/payments")
@auth.require("view_money")
def api_payments():
    order_code = (request.args.get("order") or "").strip() or None
    phone = (request.args.get("customer") or "").strip() or None
    core = normalize.phone_core(phone) if phone else None
    rows = db.list_payments(order_code=order_code, customer_phone=core)
    by_cust, net_total = {}, money.D(0)
    for r in rows:
        amt = r["amount_usd"] or 0
        signed = money.D(-amt if r["kind"] == "refund" else amt)   # exact accumulation
        net_total += signed
        key = r.get("customer_phone") or (r.get("customer_name") or "—")
        c = by_cust.setdefault(key, {"name": r.get("customer_name") or "—",
                                     "phone": r.get("customer_phone"),
                                     "deposited_usd": money.D(0), "count": 0})
        c["deposited_usd"] += signed
        c["count"] += 1
    for c in by_cust.values():
        c["deposited_usd"] = money.to_usd(c["deposited_usd"])       # back to a float for JSON
    # everyone who could receive a deposit: CRM customers + anyone with an order
    # (incl. the pending "To order" queue), so the picker searches the full list.
    seen, people = set(), []
    def _add(nm, ph):
        nm = (nm or "").strip()
        if not nm:
            return
        k = nm.lower() + "|" + (ph or "")
        if k in seen:
            return
        seen.add(k)
        people.append({"name": nm, "phone": ph or ""})
    for c in db.list_customers():
        _add(c.get("name"), c.get("whatsapp"))
    for o in db.list_orders():
        cu = o.get("customer", {}) or {}
        phs = cu.get("phones") or []
        _add(cu.get("name"), (phs[0].get("e164") if phs else ""))
    people.sort(key=lambda x: x["name"].lower())
    return jsonify({
        "ok": True, "payments": rows, "rate": _ils_rate(),
        "totals": {"deposited_usd": money.to_usd(net_total), "count": len(rows)},
        "by_customer": sorted(by_cust.values(), key=lambda x: -x["deposited_usd"]),
        "customers": people,
    })


@app.route("/api/payment/delete", methods=["POST"])
@auth.require("edit_order")
def api_payment_delete():
    b = request.get_json(force=True, silent=True) or {}
    p = db.get_payment(b.get("id"))
    if not p:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.delete_payment(p["id"])
    net = _recompute_order_deposit(p.get("order_code"))
    activity.log("deleted", "payment", str(p["id"]),
                 f"{p.get('kind')} ${p.get('amount_usd')} · {p.get('order_code') or p.get('customer_name') or ''}",
                 detail="deleted payment", user=_user())
    db.audit(auth.actor(), "delete_payment", "payment", str(p["id"]), "")
    return jsonify({"ok": True, "order_deposit_usd": net})


@app.route("/api/order/item_image", methods=["POST"])
@auth.require("edit_order")
def api_order_item_image():
    """Persist a product image onto one of an order's items (used by the To-order page's
    'get image' button so the photo sticks and carries into the PO/customer view)."""
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order(str(b.get("order_id") or ""))
    if not o:
        return jsonify({"ok": False, "error": "order not found"}), 404
    items = o.get("items") or []
    try:
        idx = int(b.get("index"))
    except (TypeError, ValueError):
        idx = -1
    img = (b.get("image") or "").strip()
    if not (0 <= idx < len(items)) or not img:
        return jsonify({"ok": False, "error": "bad item or image"}), 400
    items[idx]["image"] = img
    db.upsert_order(o)
    return jsonify({"ok": True})


@app.route("/api/order/item_price", methods=["POST"])
@auth.require("edit_order")
def api_order_item_price():
    """Capture the INTERNAL SERP item price onto one of an order's items — the To-order
    card's '💲' button. ORIGINAL-price-first: reuse the import cache (the price from when
    the product was first imported — i.e. when the customer sent the link) and stamp
    serp_price_at with that original fetched_at; only when the ASIN was never imported
    does it fall through to a live SerpAPI pull (needs SERPAPI_KEY). Stored staff-only
    (serp_price_usd/at), never shown to the customer."""
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order(str(b.get("order_id") or ""))
    if not o:
        return jsonify({"ok": False, "error": "order not found"}), 404
    items = o.get("items") or []
    try:
        idx = int(b.get("index"))
    except (TypeError, ValueError):
        idx = -1
    if not (0 <= idx < len(items)):
        return jsonify({"ok": False, "error": "bad item"}), 400
    it = items[idx]
    url = ((b.get("url") or "").strip() or it.get("clean_url") or it.get("raw_url")
           or it.get("asin") or "")
    if not url:
        return jsonify({"ok": False, "error": "no link or ASIN for this item"}), 400
    # refresh=False → cached price (original import date) when available; live pull only
    # on a cache miss (which needs SERPAPI_KEY in the env).
    d = amazon_import.import_product(url, refresh=False)
    if d.get("error") or d.get("price_usd") is None:
        err = d.get("error") or "Amazon returned no price for this item."
        if "SERPAPI_KEY" in err:
            err = ("لا يوجد سعر محفوظ لهذا المنتج — أضف SERPAPI_KEY على السيرفر لجلبه · "
                   "no stored price for this product; add SERPAPI_KEY on the server to fetch it live")
        return jsonify({"ok": False, "error": err})
    it["serp_price_usd"] = d.get("price_usd")
    # honest date: a cached hit keeps the ORIGINAL import time, not "now"
    it["serp_price_at"] = d.get("fetched_at") or db.now_iso()
    db.upsert_order(o)
    return jsonify({"ok": True, "price_usd": d.get("price_usd"),
                    "as_of": it["serp_price_at"], "cached": bool(d.get("cached"))})


# --------------------------------------------------------------------------- #
# Meta leads — Messenger/IG DMs + lead-ad forms, tracked as leads
# --------------------------------------------------------------------------- #
@app.route("/api/meta/leads")
@auth.require("view_meta_leads")
def api_meta_leads():
    d = meta_leads_mod.sync()
    d["staff"] = [{"id": u["id"], "name": u.get("name") or u["username"]}
                  for u in db.list_users() if u.get("active")]
    return jsonify(d)


@app.route("/api/meta/lead", methods=["POST"])
@auth.require("view_meta_leads")
def api_meta_lead():
    b = request.get_json(force=True, silent=True) or {}
    lead = db.get_lead(str(b.get("id") or ""))
    if not lead:
        return jsonify({"ok": False, "error": "lead not found"}), 404
    changes = {}
    if b.get("status") in ("new", "contacted", "converted", "lost"):
        changes["status"] = b["status"]
    if "assigned_to" in b:
        changes["assigned_to"] = int(b["assigned_to"]) if str(b.get("assigned_to") or "").isdigit() else None
    if "note" in b:
        changes["note"] = (b.get("note") or "").strip()
    db.update_lead(lead["lead_id"], changes)
    activity.log("set", "lead", lead["lead_id"], lead.get("name") or "lead",
                 detail=", ".join(f"{k}={v}" for k, v in changes.items()), user=_user())
    return jsonify({"ok": True})


@app.route("/api/meta/lead/convert", methods=["POST"])
@auth.require("view_meta_leads")
def api_meta_lead_convert():
    """Turn a lead into a REQUESTED order (prefilled with name + phone). The staff then adds
    products via the To-order Edit. Marks the lead converted + links the order."""
    b = request.get_json(force=True, silent=True) or {}
    lead = db.get_lead(str(b.get("id") or ""))
    if not lead:
        return jsonify({"ok": False, "error": "lead not found"}), 404
    if lead.get("order_id") and db.get_order(lead["order_id"]):
        return jsonify({"ok": True, "order_id": lead["order_id"], "existing": True})
    phones = normalize.collect_phones(lead.get("phone")) if lead.get("phone") else []
    o = make_order(name=lead.get("name") or "Lead", phones=phones, address="", city="",
                   items=[], status="REQUESTED",
                   notes=f"Meta lead · {lead.get('source')}")
    db.insert_new_order(o, created_by=current_user.id)
    _ensure_customer(o)                    # add the converted lead to the CRM / ID page
    db.update_lead(lead["lead_id"], {"status": "converted", "order_id": o["order_id"]})
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail=f"converted from Meta lead ({lead.get('source')})", user=_user())
    return jsonify({"ok": True, "order_id": o["order_id"]})


@app.route("/api/purchases")
@auth.require("view_orders")
def api_purchases():
    import purchases
    pdb = purchases.load()
    show_cost = current_user.has("view_cost")   # COGS boundary — admin only
    return jsonify({"purchase_orders": [purchases.summary(p, include_money=show_cost)
                                        for p in pdb["purchase_orders"]],
                    "money": show_cost,
                    "statuses": purchases.ITEM_STATUSES})


# Gerizim registrations — durable "this GWD was registered at Gerizim" set.
# Read by every grid so the sign shows everywhere (all staff have view_orders);
# written only by the owner's browser, which mirrors the local GAASH tool's
# done[] set here (the tool itself is unreachable off his Mac).
@app.route("/api/gerizim/registered")
@auth.require("view_orders")
def api_gerizim_registered():
    return jsonify(registered=db.list_gerizim_registered())


@app.route("/api/gerizim/registered", methods=["POST"])
@auth.require("admin_actions")
def api_gerizim_registered_post():
    rows = (request.get_json(force=True, silent=True) or {}).get("records") or []
    db.sync_gerizim_registered(rows)
    return jsonify(ok=True, count=len(rows))


@app.route("/api/purchases/refresh_tracking", methods=["POST"])
@auth.require("view_orders")
def api_purchases_refresh_tracking():
    """Self-serve GAASH refresh for the staff Purchases page. The hourly Mac
    sync can die silently (and did — month-old buckets shown as truth), so
    opening the page tops itself up: every package with a GWD number that
    isn't delivered and wasn't checked within the TTL gets one live lookup.
    Unique numbers are fetched once (a GWD can sit on several packages); a
    package is only rewritten on a successful fetch with real statuses.
    Body (optional): {"tracking": "<GWD>"} refreshes just that number (the
    live check-shipping popup calls once per GWD to stream old→new rows);
    {"force": true} bypasses the TTL (never the terminal skips).
    Each GWD is checked against BOTH carriers: GAASH (skipped once its own
    bucket is delivered) and Gerizim last-mile (until GERIZIM says delivered —
    GAASH "Delivered" only means handed to the last-mile shelf)."""
    import purchases
    import tracking
    b = request.get_json(force=True, silent=True) or {}
    res = purchases.refresh_tracking(only=tracking.clean_tracking(b.get("tracking") or "") or None,
                                     force=bool(b.get("force")))
    if res["updated"]:
        db.audit(auth.actor(), "tracking_refresh", "purchase", "-",
                 f"refreshed GAASH status on {res['updated']} package(s)")
    return jsonify({"ok": True, "updated": res["updated"], "remaining": res["remaining"],
                    "changes": res["changes"],
                    "purchase_orders": [purchases.summary(p) for p in res["db"]["purchase_orders"]]})


@app.route("/api/purchase", methods=["GET", "POST"])
@login_required
def api_purchase():
    import purchases
    if request.method == "GET":
        if not current_user.has("view_orders"):
            abort(403)
        p = purchases.find(purchases.load(), request.args.get("id", ""))
        return jsonify(purchases.summary(p, include_money=current_user.has("view_cost"))
                       if p else {"error": "not found"})
    if not current_user.has("edit_order"):
        abort(403)
    import cfg
    b = request.get_json(force=True, silent=True) or {}
    pdb = purchases.load()
    before = purchases.find(pdb, b.get("po_id")) if b.get("po_id") else None
    old_snapshot = json.loads(json.dumps(before)) if before else None
    orders = db.list_orders()
    po, how = purchases.save_full(pdb, b, orders)
    purchases.save(pdb)
    db.audit(auth.actor(), "save_po", "purchase", po["po_id"], "")
    # Connect supply→demand: matched orders auto-flip to ORDERED + inherit batch/box/ETA.
    buf = cfg.get(cfg.load(), "pipeline.delivery_buffer_days",
                  settings_mod.DELIVERY_BUFFER_DAYS)
    src = po.get("amazon_order_number") or po["po_id"]
    for oid, ch in purchases.apply_to_orders(po, orders, buf):
        db.update_order(oid, ch, auth.actor())
        activity.log("set", "order", oid, oid,
                     detail=f"auto → {ch.get('status', 'updated')} from {src}", user=_user())
    if how == "created":
        activity.log("created", "purchase", po["po_id"], _polabel(po),
                     detail="new purchase order", user=_user())
    else:
        activity.log_po_diff(old_snapshot, po, user=_user())
    return jsonify({"ok": True, "how": how, "po_id": po["po_id"],
                    "purchase_order": purchases.summary(po)})


@app.route("/api/purchase/import_clickup", methods=["POST"])
@auth.require("edit_order")
@auth.require_feature("clickup")
def api_purchase_import_clickup():
    import clickup_import
    try:
        res = clickup_import.sync()
    except Exception as e:  # noqa
        return jsonify({"ok": False, "error": str(e)})
    if res.get("error"):
        return jsonify({"ok": False, "error": res["error"]})
    for imp in res.get("imported", []):
        activity.log("created", "purchase", imp["order"], "Order # " + imp["order"],
                     detail="imported from ClickUp", user=_user())
    return jsonify({"ok": True, **res})


@app.route("/api/purchase/delete", methods=["POST"])
@auth.require("edit_order")
def api_purchase_delete():
    import purchases
    pdb = purchases.load()
    po = purchases.find(pdb, (request.get_json(force=True, silent=True) or {}).get("id"))
    if not po:
        return jsonify({"ok": False})
    tdb = trash.load()
    trash.add(tdb, "purchase_order", _polabel(po), po, actor=_user())
    trash.save(tdb)
    ok = purchases.delete(pdb, po["po_id"])
    purchases.save(pdb)
    db.audit(auth.actor(), "delete_po", "purchase", po["po_id"], "to trash")
    activity.log("deleted", "purchase", po["po_id"], _polabel(po),
                 detail="moved to Trash", user=_user())
    return jsonify({"ok": ok})


@app.route("/api/purchase/package/delete", methods=["POST"])
@auth.require("edit_order")
def api_purchase_package_delete():
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    pdb = purchases.load()
    po = purchases.find(pdb, b.get("id"))
    pi = b.get("pi")
    if not po or pi is None or pi >= len(po.get("packages", [])):
        return jsonify({"ok": False})
    pkg = po["packages"].pop(pi)
    for i, pk in enumerate(po["packages"], 1):
        pk["package_no"] = i
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    tdb = trash.load()
    trash.add(tdb, "po_package", f"Package {pkg.get('package_no')} · {_polabel(po)}", pkg,
              origin={"po_id": po["po_id"]}, actor=_user())
    trash.save(tdb)
    activity.log("deleted", "purchase", po["po_id"], _polabel(po),
                 detail=f"deleted Package {pkg.get('package_no')}", user=_user())
    return jsonify({"ok": True})


@app.route("/api/serpapi/credits")
@auth.require("view_orders")
def api_serpapi_credits():
    """Remaining SerpApi credits across the rotated keys (5-min cached) — shown
    on the Estimate-all-costs popup so the owner sees the balance. ?refresh=1
    busts the cache (used right after a run that spent credits)."""
    return jsonify(amazon_import.credits_left(0 if request.args.get("refresh") else 300))


@app.route("/api/purchase/estimate_cost", methods=["POST"])
@auth.require("edit_order")
def api_purchase_estimate_cost():
    """Estimate each product's Amazon COST (raw item price) and sum per package.
    Reuse-first: a price from a past order (free) → the ASIN import cache (free) →
    a live SerpApi lookup (one credit, metered). Fills only items with no estimate
    yet, so re-runs are free. `pi` omitted = the whole PO."""
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    pdb = purchases.load()
    po = purchases.find(pdb, b.get("id"))
    if not po:
        return jsonify({"ok": False, "error": "not found"}), 404
    pkgs = po.get("packages", [])
    pi = b.get("pi")
    targets = ([pkgs[int(pi)]] if (str(pi).isdigit() and int(pi) < len(pkgs)) else pkgs)
    # A free tenant-wide ASIN → raw-price index from past orders (SERP price first).
    past = {}
    for o in db.list_orders():
        for it in (o.get("items") or []):
            a = (it.get("asin") or "").upper()
            price = it.get("serp_price_usd")
            if price is None:
                price = it.get("item_usd")
            if a and price is not None and a not in past:
                try:
                    past[a] = float(price)
                except (TypeError, ValueError):
                    pass
    res = {"from_past": 0, "from_cache": 0, "fresh": 0, "unpriced": 0}
    for pk in targets:
        for it in (pk.get("items") or []):
            if it.get("est_cost_at"):
                continue                                   # already attempted — fill-missing only
            asin = (it.get("asin") or "").upper()
            price, src = None, None
            if asin and asin in past:
                price, src = past[asin], "past"
                res["from_past"] += 1
            elif it.get("clean_url") or asin:
                try:
                    d = amazon_import.import_product(it.get("clean_url") or asin, refresh=False)
                except Exception:  # noqa: BLE001 — a lookup failure must not 500 the batch
                    d = {}
                if d.get("price_usd") is not None:
                    price = d["price_usd"]
                    if d.get("cached"):
                        src = "cache"; res["from_cache"] += 1
                    else:
                        src = "serp"; res["fresh"] += 1
                        quotas.bump_search(db.current_business())   # only real lookups cost a credit
            if price is not None:
                it["est_cost_usd"] = round(float(price), 2)
                it["est_cost_src"] = src
            else:
                it["est_cost_src"] = "none"                 # attempted, no price found
                res["unpriced"] += 1
            it["est_cost_at"] = db.now_iso()               # stamp every attempt so re-runs skip it
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    db.audit(auth.actor(), "estimate_cost", "purchase", po["po_id"],
             f"past {res['from_past']} · cache {res['from_cache']} · serp {res['fresh']}")
    res["packages"] = [{"pi": i,
                        "est_total_usd": round(sum((it.get("est_cost_usd") or 0) * (it.get("qty") or 1)
                                                   for it in (pk.get("items") or [])
                                                   if it.get("est_cost_usd") is not None), 2)}
                       for i, pk in enumerate(pkgs)]
    return jsonify({"ok": True, "purchase_order": purchases.summary(po), "result": res})


@app.route("/api/purchase/item/delete", methods=["POST"])
@auth.require("edit_order")
def api_purchase_item_delete():
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    pdb = purchases.load()
    po = purchases.find(pdb, b.get("id"))
    pi, ii = b.get("pi"), b.get("ii")
    try:
        it = po["packages"][pi]["items"].pop(ii)
    except (TypeError, KeyError, IndexError):
        return jsonify({"ok": False})
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    tdb = trash.load()
    trash.add(tdb, "po_item", (it.get("title") or it.get("asin") or "product"), it,
              origin={"po_id": po["po_id"], "package_no": po["packages"][pi].get("package_no")},
              actor=_user())
    trash.save(tdb)
    activity.log("deleted", "purchase", po["po_id"], _polabel(po),
                 detail=f"deleted product “{(it.get('title') or it.get('asin') or '')[:40]}”",
                 user=_user())
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Trash (Recycle Bin) + Activity feed
# --------------------------------------------------------------------------- #
@app.route("/api/trash")
@auth.require("view_orders")
def api_trash():
    return jsonify({"trash": trash.items(trash.load())})


@app.route("/api/trash/restore", methods=["POST"])
@auth.require("edit_order")
def api_trash_restore():
    tdb = trash.load()
    res = trash.restore(tdb, (request.get_json(force=True, silent=True) or {}).get("id"))
    if res.get("ok"):
        trash.save(tdb)
        activity.log("restored", "trash", res.get("kind", ""), res.get("label", ""),
                     detail=f"restored to {res.get('where', '')}", user=_user())
    return jsonify(res)


@app.route("/api/trash/purge", methods=["POST"])
@auth.require("admin_actions")
def api_trash_purge():
    tdb = trash.load()
    ok = trash.purge(tdb, (request.get_json(force=True, silent=True) or {}).get("id"))
    trash.save(tdb)
    return jsonify({"ok": ok})


@app.route("/api/trash/empty", methods=["POST"])
@auth.require("admin_actions")
def api_trash_empty():
    tdb = trash.load()
    n = trash.empty(tdb)
    trash.save(tdb)
    return jsonify({"ok": True, "removed": n})


@app.route("/api/activity")
@auth.require("view_orders")
def api_activity():
    ent = request.args.get("entity") or None
    eid = request.args.get("id") or None          # per-entity feed (PO detail drawer)
    lim = int(request.args.get("limit", "60"))
    return jsonify({"activity": activity.recent(lim, ent, entity_id=eid)})


# The staff bell 🔔 — customer-driven moments only (form submissions, quote
# confirmations, new Meta leads) + the standing "waiting for YOUR quote" count.
# Built from the existing activity feed + tables; no new storage — the client
# keeps its own last-seen cursor, so this endpoint is a pure read.
def _notif_from_activity(ev):
    """Map one customer activity event → a bell notification (or None)."""
    act, detail = ev.get("action") or "", (ev.get("detail") or "").lower()
    label = ev.get("label") or ev.get("entity_id") or ""
    if act == "confirmed":
        icon, title = "✅", "أكّد العميل عرض السعر · quote confirmed"
    elif "catalog" in detail:
        icon, title = "🛒", "طلب جديد من الكتالوج · new catalog order"
    elif "plan chosen" in detail:
        plan = ev.get("new") or ""
        icon = "🛍"
        title = ("أكمل طلبه واختار الخطة · completed the order form"
                 + (f" — {plan}" if plan else ""))
    elif "quote request" in detail:
        icon, title = "🛍", "طلب تسعير من الموقع · website quote request"
    elif "no plan yet" in detail:
        icon, title = "✍️", "بدأ طلباً من الموقع · started an order"
    elif "self-intake" in detail:
        icon, title = "🛍", "عبّأ نموذج الطلب · filled the order form"
    elif "lead edited" in detail:
        icon, title = "✍️", "عدّل طلبه · updated their request"
    else:
        icon, title = "🔔", (ev.get("detail") or act or "event")
    return {"ts": ev.get("ts") or "", "type": "quote_ok" if act == "confirmed" else "order_form",
            "icon": icon, "title": title, "sub": label, "view": "needorder"}


@app.route("/api/notifications")
@auth.require("view_orders")
def api_notifications():
    events, seen_order = [], set()
    for ev in activity.recent(200):
        if ev.get("user") != "customer":
            continue
        # a wizard run logs 2-3 events for the same order (lead → plan → edit):
        # keep only the newest per order so one submission = one notification
        key = (ev.get("action") == "confirmed", ev.get("entity_id"))
        if key in seen_order:
            continue
        seen_order.add(key)
        n = _notif_from_activity(ev)
        if n:
            events.append(n)
    for lead in db.list_leads(status="new"):
        events.append({"ts": lead.get("created_time") or lead.get("synced_at") or "",
                       "type": "lead", "icon": "📣",
                       "title": "عميل محتمل جديد · new lead",
                       "sub": lead.get("name") or lead.get("source") or lead["lead_id"],
                       "view": "metaleads"})
    # 📧 GAASH-mail events: real replies, missing-docs flags, exhausted/cleared
    # sequences — read fresh from the gaash tables (client dedupes by ts cursor).
    try:
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        with db.connect() as _c:
            for r in _c.execute(
                    "SELECT gwd, at, subject FROM gaash_msgs WHERE dir='in' "
                    "AND kind='reply' AND at>=? ORDER BY at DESC LIMIT 10", (cutoff,)):
                events.append({"ts": r["at"], "type": "gaash_reply", "icon": "📬",
                               "title": "رد من غاش · GAASH replied",
                               "sub": r["gwd"], "view": "gaashmail"})
            for r in _c.execute(
                    "SELECT gwd, state, missing_docs, missing_note, last_activity "
                    "FROM gaash_threads WHERE last_activity>=?", (cutoff,)):
                if r["missing_docs"]:
                    events.append({"ts": r["last_activity"], "type": "gaash_missing",
                                   "icon": "⚠️",
                                   "title": "طرد ناقص مستندات · package missing documents",
                                   "sub": f"{r['gwd']}" + (f" — {r['missing_note']}"
                                                           if r["missing_note"] else ""),
                                   "view": "gaashmail"})
                elif r["state"] == "exhausted":
                    events.append({"ts": r["last_activity"], "type": "gaash_exhausted",
                                   "icon": "📭",
                                   "title": "٤ إيميلات بلا رد · 4 emails, no reply",
                                   "sub": r["gwd"], "view": "gaashmail"})
                elif r["state"] == "cleared":
                    events.append({"ts": r["last_activity"], "type": "gaash_cleared",
                                   "icon": "✅",
                                   "title": "تخلّص الطرد — توقف التسلسل · cleared, sequence stopped",
                                   "sub": r["gwd"], "view": "gaashmail"})
    except Exception:  # noqa - the bell must never break on a fresh DB
        pass
    # ⌚ Leluxe sync conflicts waiting for review — one bell item whose ts is the
    # newest parked row's updated_at, so new parks re-surface it and an idle
    # backlog doesn't re-ping on every poll. Auto-sync can park these while
    # nobody is looking, so the bell is how the owner finds out.
    try:
        import features
        if current_user.has("admin_actions") and features.has(db.current_business(), "leluxe"):
            with db.connect() as _c:
                row = _c.execute(
                    "SELECT COUNT(*) n, MAX(updated_at) ts FROM leluxe_orders "
                    "WHERE sync_state='conflict' AND deleted=0 AND "
                    "json_array_length(json_extract(data_json,'$.conflicts'))>0"
                ).fetchone()
            if row and row["n"]:
                events.append({"ts": row["ts"] or "", "type": "lx_conflict",
                               "icon": "⚠️",
                               "title": "تعارض مزامنة يحتاج مراجعة · sync conflicts need review",
                               "sub": f"{row['n']} صف · rows — Leluxe ⚠",
                               "view": "leluxe"})
    except Exception:  # noqa - same rule: the bell never breaks
        pass
    # 📄 parcels whose GAASH page is asking for documents (or shows clearance
    # stopped) — one standing item, ts = the newest docs check so a state flip
    # re-surfaces it without re-pinging on every poll. Data = the docs_state
    # both sweeps store; scanning the two stores costs one query + one file read.
    try:
        import features
        if current_user.has("edit_fulfillment") and features.has(db.current_business(), "leluxe"):
            n_docs, ts_docs, seen_g = 0, "", set()
            with db.connect() as _c:
                for _r in _c.execute(
                        "SELECT data_json FROM leluxe_orders WHERE deleted=0 "
                        "AND data_json LIKE '%docs_state%'"):
                    try:
                        _d = json.loads(_r["data_json"] or "{}")
                    except ValueError:
                        continue
                    _ds = _d.get("docs_state") or {}
                    _g = str(_d.get("tracking_number") or "").strip().upper()
                    if not _g:   # rows often carry the GWD only in the ClickUp field
                        _g = next((str(v or "").strip().upper()
                                   for k, v in (_d.get("fields") or {}).items()
                                   if k.strip().lower() == "tracking number"), "")
                    _b = ((_d.get("tracking_status") or {}).get("bucket")
                          if isinstance(_d.get("tracking_status"), dict) else None)
                    if _ds.get("state") in ("action", "stopped") and _g and _g not in seen_g \
                            and _b not in ("delivered", "cleared") \
                            and not isinstance(_d.get("gerizim_status"), dict):
                        seen_g.add(_g)
                        n_docs += 1
                        ts_docs = max(ts_docs, _d.get("docs_checked") or "")
            import purchases as _pmod
            for _po in (_pmod.load() or {}).get("purchase_orders") or []:
                for _pk in _po.get("packages") or []:
                    _ds = _pk.get("docs_state") or {}
                    _g = str(_pk.get("tracking_number") or "").strip().upper()
                    _b = ((_pk.get("tracking_status") or {}).get("bucket")
                          if isinstance(_pk.get("tracking_status"), dict) else None)
                    if _ds.get("state") in ("action", "stopped") and _g and _g not in seen_g \
                            and _b not in ("delivered", "cleared") \
                            and not isinstance(_pk.get("gerizim_status"), dict):
                        seen_g.add(_g)
                        n_docs += 1
                        ts_docs = max(ts_docs, _pk.get("docs_checked") or "")
            if n_docs:
                events.append({"ts": ts_docs, "type": "gaash_docs", "icon": "📄",
                               "title": f"{n_docs} طرود تحتاج مستندات · parcels need documents",
                               "sub": "غاش يطلب رفع مستندات — افتح قائمة المستندات · GAASH asks for uploads — open the Docs tab",
                               "view": "gaashmail"})
    except Exception:  # noqa - same rule: the bell never breaks
        pass
    events.sort(key=lambda e: e["ts"], reverse=True)
    out = events[:30]
    # standing alerts survive the recency cap: parked sync conflicts can be DAYS
    # old (that is the whole problem) — sorting by ts pushed them off the list
    for _typ in ("lx_conflict", "gaash_docs"):
        stand = next((e for e in events if e["type"] == _typ), None)
        if stand and stand not in out:
            out.insert(0, stand)
    needs_quote = sum(1 for o in db.list_orders()
                      if o.get("status") == "REQUESTED" and not o.get("quoted_at"))
    return jsonify({"ok": True, "needs_quote": needs_quote, "events": out})


@app.route("/api/po_image", methods=["GET", "POST"])
@login_required
def api_po_image():
    import base64
    import purchases
    if request.method == "GET":
        # checkout screenshots show the paid Amazon totals → COGS boundary
        if not current_user.has("view_cost"):
            abort(403)
        fn = request.args.get("file", "")
        p = purchases.IMAGE_DIR / fn
        if fn and "/" not in fn and p.exists():
            return send_file(p)
        abort(404)
    if not current_user.has("edit_order"):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    data = b.get("data_base64", "")
    if data.strip().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    ext = (b.get("filename", "img.png").rsplit(".", 1)[-1] or "png")[:5]
    purchases.IMAGE_DIR.mkdir(exist_ok=True)
    fn = f"{b.get('po_id', 'po')}-{uuid.uuid4().hex[:8]}.{ext}"
    try:
        (purchases.IMAGE_DIR / fn).write_bytes(base64.b64decode(data))
    except Exception as e:  # noqa
        return jsonify({"ok": False, "error": str(e)})
    pdb = purchases.load()
    po = purchases.set_screenshot(pdb, b.get("po_id"), fn)
    purchases.save(pdb)
    activity.log("uploaded", "purchase", b.get("po_id", ""),
                 _polabel(po) if po else b.get("po_id", ""), detail="uploaded a file",
                 user=_user())
    return jsonify({"ok": True, "file": fn,
                    "attachments": (po or {}).get("attachments") or []})


@app.route("/api/po_image/delete", methods=["POST"])
@auth.require("edit_order")
def api_po_image_delete():
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    fn = (b.get("file") or "").strip()
    if not fn or "/" in fn:
        return jsonify({"ok": False, "error": "bad file"}), 400
    pdb = purchases.load()
    po = purchases.remove_attachment(pdb, b.get("po_id"), fn)
    if not po:
        return jsonify({"ok": False, "error": "order not found"}), 404
    purchases.save(pdb)
    try:
        (purchases.IMAGE_DIR / fn).unlink(missing_ok=True)
    except Exception:                          # noqa: BLE001 — DB already consistent
        pass
    activity.log("deleted", "purchase", po["po_id"], _polabel(po),
                 detail="removed an attachment", user=_user())
    return jsonify({"ok": True, "attachments": po.get("attachments") or []})


@app.route("/api/customer_image", methods=["GET", "POST"])
@login_required
def api_customer_image():
    import base64
    # ID documents are sensitive — only served via this authed route, never public.
    if request.method == "GET":
        if not current_user.has("view_customers"):
            abort(403)
        fn = request.args.get("file", "")
        p = cust_mod.ID_DIR / fn
        if fn and "/" not in fn and p.exists():
            return send_file(p)
        abort(404)
    if not current_user.has("manage_customers"):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    data = b.get("data_base64", "")
    if data.strip().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    ext = (b.get("filename", "id.png").rsplit(".", 1)[-1] or "png")[:5]
    cust_mod.ID_DIR.mkdir(exist_ok=True)
    fn = f"{b.get('customer_id', 'cust')}-{uuid.uuid4().hex[:8]}.{ext}"
    try:
        (cust_mod.ID_DIR / fn).write_bytes(base64.b64decode(data))
    except Exception as e:  # noqa
        return jsonify({"ok": False, "error": str(e)})
    c = db.get_customer(b.get("customer_id"))
    if c:
        c["id_image"] = fn
        db.upsert_customer(c)
    activity.log("uploaded", "customer", b.get("customer_id", ""),
                 (c.get("name") if c else b.get("customer_id", "")),
                 detail="uploaded ID document", user=_user())
    return jsonify({"ok": True, "file": fn})


@app.route("/api/customer/id_number", methods=["POST"])
@auth.require("manage_customers")
def api_customer_id_number():
    """Staff: set/clear a customer's ID number (also settable by the customer via
    the /id/<token> self-service link)."""
    b = request.get_json(force=True, silent=True) or {}
    c = db.get_customer((b.get("customer_id") or "").strip())
    if not c:
        return jsonify({"ok": False, "error": "customer not found"}), 404
    c["id_number"] = (b.get("id_number") or "").strip()[:40]
    db.upsert_customer(c)
    activity.log("edited", "customer", c.get("customer_id", ""), c.get("name", ""),
                 detail="set ID number", user=_user())
    return jsonify({"ok": True})


@app.route("/api/automatch")
@auth.require("edit_order")
def api_automatch():
    import purchases
    idx = purchases.asin_index(db.list_orders())
    cid, cname, amb = purchases.match_item(request.args.get("asin", ""), idx)
    return jsonify({"customer_order_id": cid, "customer_name": cname, "ambiguous": amb})


@app.route("/api/track_gwd")
@auth.require("view_orders")
def api_track_gwd():
    import tracking
    tn = request.args.get("tracking", "")
    return jsonify(tracking.track_with_fallback(tn) if tn else {"error": "no tracking number"})


@app.route("/api/purchase/clickup", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("clickup")
def api_purchase_clickup():
    import clickup_po
    import purchases
    pdb = purchases.load()
    po = purchases.find(pdb, (request.get_json(force=True, silent=True) or {}).get("id"))
    if not po:
        return jsonify({"ok": False, "error": "not found"})
    res = clickup_po.push(po, dry_run=bool((request.get_json(silent=True) or {}).get("dry_run")))
    if res.get("ok") and not res.get("dry_run"):
        purchases.save(pdb)
    return jsonify(res)


@app.route("/api/clickup", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("clickup")
def api_clickup():
    return jsonify(run_script("clickup.py"))


# --------------------------------------------------------------------------- #
# Leluxe — Amazon-bulk side business, mirrored live to the ClickUp 'AZ (2)'
# list (leluxe.py). Admin-only + Otlobly-only: the page carries VISA labels,
# buyer accounts and ID info.
# --------------------------------------------------------------------------- #
@app.route("/api/leluxe/discover", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_discover():
    b = request.get_json(force=True, silent=True) or {}
    config = cfg.load()
    lid = str(b.get("list_id") or "").strip() or leluxe_mod.list_id(config)
    if not lid:
        return jsonify({"ok": False, "error": "Pass the ClickUp list id once."}), 400
    if not os.environ.get("CLICKUP_API_TOKEN"):
        return jsonify({"ok": False, "error": "CLICKUP_API_TOKEN is not set."}), 400
    sch, err = leluxe_mod.discover(lid)
    if err:
        return jsonify({"ok": False, "error": err}), 502
    cfg.set_path(config, "leluxe.list_id", lid)
    cfg.set_path(config, "leluxe.schema", sch)
    cfg.save(config)
    activity.log("set", "leluxe", lid, "Leluxe",
                 detail=f"discovered list {lid}: {len(sch['statuses'])} statuses, "
                        f"{len(sch['fields'])} fields", user=_user())
    return jsonify({"ok": True, "list_id": lid,
                    "statuses": len(sch["statuses"]), "fields": len(sch["fields"])})


@app.route("/api/leluxe/orders")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_orders():
    config = cfg.load()
    orders, orphans = leluxe_mod.list_tree()
    return jsonify({"ok": True, "orders": orders, "orphans": orphans,
                    "schema": leluxe_mod.schema(config),
                    "list_id": leluxe_mod.list_id(config),
                    "source_list_id": leluxe_mod.source_list_id(config),
                    "token": bool(os.environ.get("CLICKUP_API_TOKEN")),
                    "ready": leluxe_mod.ready(config),
                    "push_disabled": leluxe_mod.push_disabled(),
                    "sync": leluxe_mod.sync_counts(),
                    "auto_pull": {**leluxe_mod.auto_pull_settings(),
                                  "last": db.get_setting("leluxe:auto_pull_last") or {}}})


@app.route("/api/leluxe/auto_pull", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_auto_pull():
    """⏱ toggle the background sync-from-AZ (2). Stored in THIS instance's DB,
    so flipping it on live never enables the stale local mirror's copy."""
    b = request.get_json(force=True, silent=True) or {}
    cur = leluxe_mod.auto_pull_settings()
    new = {"enabled": bool(b.get("enabled")),
           "minutes": max(5, int(b.get("minutes") or cur["minutes"]))}
    db.set_setting("leluxe:auto_pull", new)
    db.audit(auth.actor(), "leluxe_auto_pull", "settings", "",
             f"enabled={new['enabled']} every {new['minutes']}m")
    return jsonify({"ok": True, "auto_pull": new})


@app.route("/api/leluxe/import", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_import():
    """Batched importer AND the Pull button. phase 'tasks' = one paged fetch +
    upsert of the whole list (~1 request / 100 tasks); phase 'images' = localize
    a few tasks' attachments per call — the client loops while remaining > 0
    (same pattern as the PO tracking refresh, to stay under the 120s timeout)."""
    b = request.get_json(force=True, silent=True) or {}
    phase = b.get("phase") or "tasks"
    if phase == "tasks":
        flag = db.get_setting("leluxe:import_running") or 0
        try:
            flag = float(flag)
        except (TypeError, ValueError):
            flag = 0
        if time.time() - flag < 120:
            return jsonify({"ok": False, "error": "an import is already running"}), 409
        db.set_setting("leluxe:import_running", time.time())
        try:
            res = leluxe_mod.pull_tasks()
        finally:
            db.set_setting("leluxe:import_running", 0)
        if res.get("error"):
            return jsonify({"ok": False, **res}), 502
        activity.log("set", "leluxe", "pull", "Leluxe",
                     detail=f"pulled {res['tasks']} ClickUp tasks "
                            f"(+{res['created']} new, {res['updated']} updated, "
                            f"{res['skipped_dirty']} kept local)", user=_user())
        return jsonify({"ok": True, **res})
    if phase == "images":
        res = leluxe_mod.localize_images(batch=min(int(b.get("batch") or 8), 25))
        return jsonify({"ok": True, **res})
    return jsonify({"ok": False, "error": "bad phase"}), 400


@app.route("/api/leluxe/migrate", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_migrate():
    """Copy recent AZ (2) orders into the working list as a 3-tier tree. AZ (2)
    is READ-ONLY (GET only). phase 'scan' builds order→package(by tracking)→item
    rows created on/after `since`; 'images'/'tracking' batched-backfill the new
    rows (client loops while remaining > 0), same shape as the PO refresh loop."""
    b = request.get_json(force=True, silent=True) or {}
    phase = b.get("phase") or "scan"
    if phase in ("scan", "sync"):
        since = (b.get("since") or "").strip()
        if not since:
            return jsonify({"ok": False, "error": "since date required"}), 400
        # one-time: persist the read-only AZ (2) source list id when the client
        # supplies it (it has no other setter — Discover only sets the working list)
        src = str(b.get("source_list_id") or "").strip()
        if src:
            _c = cfg.load()
            cfg.set_path(_c, "leluxe.source_list_id", src)
            cfg.save(_c)
        flag = db.get_setting("leluxe:import_running") or 0
        try:
            flag = float(flag)
        except (TypeError, ValueError):
            flag = 0
        if time.time() - flag < 120:
            return jsonify({"ok": False, "error": "a migration/import is already running"}), 409
        db.set_setting("leluxe:import_running", time.time())
        try:
            if phase == "sync":
                res = leluxe_mod.sync_from_source(since, limit=int(b.get("limit") or 25),
                                                  review_all=bool(b.get("review_all")))
            else:
                res = leluxe_mod.migrate_from_source(since, limit=int(b.get("limit") or 20))
        finally:
            db.set_setting("leluxe:import_running", 0)
        if res.get("error"):
            return jsonify({"ok": False, **res}), 502
        if phase == "sync":
            activity.log("set", "leluxe", "sync", "Leluxe",
                         detail=f"synced AZ (2) since {since}: +{res['orders']} new, "
                                f"{res['updated']} updated, {res['conflicts']} conflict(s), "
                                f"{res['new_items']} new product(s)"
                                + (" — 🛡 review-all (nothing auto-applied)" if b.get("review_all") else "")
                                + (f" · snapshot {res.get('presync')}" if res.get("presync") else ""),
                         user=_user())
        else:
            activity.log("set", "leluxe", "migrate", "Leluxe",
                         detail=f"migrated {res['orders']} orders / {res['packages']} packages "
                                f"/ {res['items']} items from AZ (2) since {since}", user=_user())
        return jsonify({"ok": True, **res})
    if phase == "images":
        return jsonify({"ok": True, **leluxe_mod.fetch_item_images(
            batch=min(int(b.get("batch") or 6), 20))})
    if phase == "tracking":
        return jsonify({"ok": True, **leluxe_mod.refresh_tracking(
            batch=min(int(b.get("batch") or 5), 10))})
    return jsonify({"ok": False, "error": "bad phase"}), 400


@app.route("/api/leluxe/sync_report")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_sync_report():
    """The last Sync-from-AZ(2) run's per-change report (what was applied,
    what's new, what parked as conflicts) — written by sync_from_source."""
    from paths import data_path
    try:
        rep = json.loads(data_path("leluxe_sync_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rep = {"ts": None, "applied": [], "new_orders": [], "new_items": [],
               "conflict_rows": []}
    return jsonify({"ok": True, **rep})


@app.route("/api/leluxe/diag")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_diag():
    """Read-only sync health: which rows are out of step with AZ (2) and WHY
    the next sync would skip them (mid-push / parked conflict / never pulled).
    Writes nothing — safe to open at any time, mid-sync included."""
    return jsonify({"ok": True, **leluxe_mod.diagnose()})


@app.route("/api/leluxe/conflicts")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_conflicts():
    """Rows parked in a sync merge conflict — Otlobly vs AZ (2) per field."""
    return jsonify({"ok": True, "conflicts": leluxe_mod.list_conflicts()})


@app.route("/api/leluxe/resolve_conflict", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_resolve_conflict():
    """Apply the owner's pick to a parked conflict row. Body: {row_id, choice:
    'local'|'remote'} to resolve the whole row, or {row_id, choices:{field:side}}
    per field. Resolving the last field flips the row to 'dirty' → mirrored to the
    working copy (AZ (2) is never written)."""
    b = request.get_json(force=True, silent=True) or {}
    rid = b.get("row_id")
    if not rid:
        return jsonify({"ok": False, "error": "row_id required"}), 400
    row, err = leluxe_mod.resolve_conflict(
        rid, choices=b.get("choices"), choice=b.get("choice"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    remaining = len(row["data"].get("conflicts") or []) if row else 0
    activity.log("set", "leluxe", str(rid), row.get("name") if row else "Leluxe",
                 detail=f"resolved sync conflict ({b.get('choice') or 'per-field'})",
                 user=_user())
    return jsonify({"ok": True, "row": row, "remaining_conflicts": remaining})


@app.route("/api/leluxe/dedupe", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_dedupe():
    """Remove migrate-created duplicate orders: keeps the original imported row,
    soft-deletes each migrate copy + deletes its pushed twin task in the WORKING
    list only (AZ (2) is never touched). dry_run=true reports without acting."""
    b = request.get_json(force=True, silent=True) or {}
    try:
        res = leluxe_mod.dedupe_migrated(dry_run=bool(b.get("dry_run", True)))
    except Exception as e:  # noqa: BLE001 — surface the reason, never a dead socket
        return jsonify({"ok": False, "error": str(e)[:300]})
    if not res.get("dry_run"):
        activity.log("deleted", "leluxe", "dedupe", "Leluxe",
                     detail=f"removed {res['items_removed']} duplicate product(s), "
                            f"{res['pkgs_removed']} emptied package(s), "
                            f"{res['removed']} order(s); queued "
                            f"{res['cu_queued']} ClickUp deletion(s)", user=_user())
    return jsonify({"ok": True, **res})


@app.route("/api/leluxe/refresh_tracking", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_refresh_tracking():
    b = request.get_json(force=True, silent=True) or {}
    res = leluxe_mod.refresh_tracking(batch=min(int(b.get("batch") or 5), 10),
                                      only=b.get("tracking"), force=bool(b.get("force")))
    return jsonify({"ok": True, **res})


def _docs_check_one(tn):
    """Fresh GAASH docs-banner check for one parcel, persisted to WHICHEVER
    board(s) carry the number (each store call is a no-op elsewhere)."""
    import tracking
    docs = tracking.docs_status(tn)
    if docs:
        leluxe_mod.store_docs_state(tn, docs)
        try:
            import purchases as purchases_mod
            purchases_mod.store_docs_state(tn, docs)
        except Exception:  # noqa: BLE001 — purchases store hiccup must not lose the answer
            pass
    return docs


@app.route("/api/leluxe/docs_status")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_docs_status():
    """Live GAASH docs-banner check for ONE parcel (the ⋯ menu's on-demand
    re-check) — yellow "upload asked" vs teal "docs in". The bulk sweep stores
    the same shape on data.docs_state; this bypasses it for a fresh answer."""
    import tracking
    tn = tracking.clean_tracking(request.args.get("tracking") or "")
    if not tn:
        return jsonify({"ok": False, "error": "tracking required"}), 400
    return jsonify({"ok": True, "tracking": tn, "docs": _docs_check_one(tn)})


@app.route("/api/gaash/docs_check", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_docs_check():
    """The 📄 Docs queue's per-row Re-check — same check as
    /api/leluxe/docs_status but reachable by fulfillment (the queue's gate)."""
    import tracking
    b = request.get_json(force=True, silent=True) or {}
    tn = tracking.clean_tracking(b.get("tracking") or "")
    if not tn:
        return jsonify({"ok": False, "error": "tracking required"}), 400
    return jsonify({"ok": True, "tracking": tn, "docs": _docs_check_one(tn)})


@app.route("/api/leluxe/item_image", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_item_image():
    b = request.get_json(force=True, silent=True) or {}
    img = leluxe_mod.fetch_item_image(b.get("id"), force=bool(b.get("force")),
                                      asin=(b.get("asin") or None))
    return jsonify({"ok": True, "image": img})


@app.route("/api/leluxe/order", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_order():
    b = request.get_json(force=True, silent=True) or {}
    row, err = leluxe_mod.save_row(b)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    activity.log("saved", "leluxe", row["id"], row["name"] or f"#{row['id']}",
                 detail="leluxe order saved → syncing to ClickUp", user=_user())
    return jsonify({"ok": True, "row": row})


@app.route("/api/leluxe/move", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_move():
    """Move a product to another package of the same order (Amazon split a
    shipment), optionally splitting a partial quantity into it. `qty` omitted or
    ≥ the item's count moves the whole product (re-parents the ClickUp subtask);
    a smaller `qty` splits off a new product row. `new_package:true` creates an
    empty destination package first."""
    b = request.get_json(force=True, silent=True) or {}
    summary, err = leluxe_mod.move_item(
        b.get("id"), dest_parent_local_id=b.get("parent_local_id"),
        new_package=bool(b.get("new_package")), move_qty=b.get("qty"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if summary["mode"] == "split":
        detail = (f"split {summary['moved_qty']} unit(s) off (kept "
                  f"{summary['left_qty']}) → new subtask, amount stays on the "
                  f"original → syncing to ClickUp")
    else:
        detail = "moved to another package → syncing to ClickUp"
    activity.log("moved", "leluxe", summary["row_id"], f"#{summary['row_id']}",
                 detail=detail, user=_user())
    return jsonify({"ok": True, **summary})


@app.route("/api/leluxe/az2_push", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_az2_push():
    """Manually push ONE row's status into the AZ (2) source list. The only
    write path to AZ (2) — CAS-guarded, journalled, tagged + commented there."""
    b = request.get_json(force=True, silent=True) or {}
    entry, err = leluxe_mod.az2_push_status(
        b.get("row_id"), expected_remote=b.get("expected_remote"), user=_user())
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not entry.get("noop"):
        activity.log("pushed", "leluxe", b.get("row_id"), f"#{b.get('row_id')}",
                     detail=f"AZ (2) status {entry['old']!r} → {entry['new']!r}"
                            f" (task {entry['task_id']})", user=_user())
    return jsonify({"ok": True, **entry})


@app.route("/api/leluxe/az2_undo", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_az2_undo():
    b = request.get_json(force=True, silent=True) or {}
    entry, err = leluxe_mod.az2_undo(b.get("push_id"), user=_user())
    if err:
        return jsonify({"ok": False, "error": err}), 400
    activity.log("undone", "leluxe", entry["task_id"], f"AZ (2) {entry['task_id']}",
                 detail=f"push #{entry['id']} reverted → {entry['restored']!r}",
                 user=_user())
    return jsonify({"ok": True, **entry})


@app.route("/api/leluxe/az2_pushes")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_az2_pushes():
    return jsonify({"ok": True, "pushes": leluxe_mod.az2_push_history()})


@app.route("/api/leluxe/az2_organize", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_az2_organize():
    """Mirror one order's 📦 GWD package tree into the REAL AZ (2) list —
    {order_id, dry_run:true} previews the step plan, without dry_run it writes
    (each step journalled in az2_pushes with per-entry undo)."""
    b = request.get_json(force=True, silent=True) or {}
    rep, err = leluxe_mod.az2_organize(b.get("order_id"), user=_user(),
                                       dry_run=bool(b.get("dry_run")))
    if err:
        return jsonify({"ok": False, "error": err, **(rep or {})}), 400
    if not rep.get("dry_run") and rep.get("steps"):
        activity.log("pushed", "leluxe", b.get("order_id"), rep.get("order") or "",
                     detail=f"AZ (2) organized: {rep['packages']} pkg · "
                            f"{rep['moves']} moved · {rep['creates_item']} added"
                            f" · {rep['creates_pkg']} pkg created", user=_user())
    return jsonify({"ok": True, **rep})


@app.route("/api/leluxe/status", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_status():
    """Inline status change from the board (no editor card) — status-only save."""
    b = request.get_json(force=True, silent=True) or {}
    row, err = leluxe_mod.set_status(b.get("id"), b.get("status"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    activity.log("set", "leluxe", row["id"], row["name"] or f"#{row['id']}",
                 detail=f"status → {row['status']} (board) → syncing to ClickUp",
                 user=_user())
    return jsonify({"ok": True, "row": row})


# ── Per-package clearance mail (one GWD per email; replies marked by hand) ──
@app.route("/api/leluxe/pkgmail")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_pkgmail():
    return jsonify({"ok": True, "mail": db.pkg_mail_all()})


@app.route("/api/leluxe/pkgmail/sent", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_pkgmail_sent():
    b = request.get_json(force=True, silent=True) or {}
    rec = db.pkg_mail_sent(b.get("gwd"), b.get("to"), b.get("subject"))
    if not rec:
        return jsonify({"ok": False, "error": "gwd required"}), 400
    activity.log("send", "leluxe", 0, rec["gwd"],
                 detail=f"clearance email #{rec['sent_count']} → {rec.get('to_email') or '?'}",
                 user=_user())
    return jsonify({"ok": True, "rec": rec})


@app.route("/api/leluxe/pkgmail/reply", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_pkgmail_reply():
    b = request.get_json(force=True, silent=True) or {}
    rec = db.pkg_mail_reply(b.get("gwd"), bool(b.get("replied", True)))
    if not rec:
        return jsonify({"ok": False, "error": "no clearance email logged for this GWD"}), 400
    return jsonify({"ok": True, "rec": rec})


# ── 📧 GAASH Mail: automated clearance-email sequences (gaash_mail.py) ──────
# View/send = edit_fulfillment (the fulfillment hire chases GAASH docs); account
# management + ID-library edits + test sends = admin_actions. Feature-gated
# like the Leluxe family.

def _gm_files(raw):
    """[{name,data_base64,ctype}] → [(name, bytes, ctype)] (skip bad entries)."""
    out = []
    for f in (raw or [])[:6]:
        try:
            data = base64.b64decode(str(f.get("data_base64") or ""), validate=False)
        except Exception:  # noqa
            continue
        if not data or len(data) > 15 * 1024 * 1024:
            continue
        out.append((str(f.get("name") or "attachment"), data,
                    str(f.get("ctype") or "application/octet-stream")))
    return out


@app.route("/api/gaash/overview")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_overview():
    return jsonify({"ok": True, **gaash_mail.overview(),
                    "candidates": gaash_mail.candidates(include_enrolled=True)})


@app.route("/api/gaash/check_tracking", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_check_tracking():
    """📦 one press → every store that holds the number, plus the receipt.

    Replaces the browser's old loop of one request per conversation, which
    could only ever refresh the board each conversation came from.
    Body: {"gwds": [...]}(default: every open conversation), {"force": true}."""
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.check_tracking(gwds=b.get("gwds") or None,
                                   force=bool(b.get("force")),
                                   actor=auth.actor())
    if res.get("changed"):
        db.audit(auth.actor(), "gaash_check", "leluxe", "-",
                 f"{res['changed']} parcel(s) moved of {res['checked']} checked")
    return jsonify(res)


@app.route("/api/gaash/changes")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_changes():
    """What the checks have moved lately — the popup's whole data source."""
    try:
        days = max(1, min(90, int(request.args.get("days") or 14)))
    except ValueError:
        days = 14
    return jsonify({"ok": True, **db.gash_changes(days)})


@app.route("/api/gaash/stat_detail")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_stat_detail():
    """Row list behind an 🧭 Overview tile — which exact parcel/email it counts."""
    kind = (request.args.get("kind") or "").strip().lower()
    if kind not in ("sent", "opened", "clicked", "replied"):
        return jsonify({"ok": False, "error": "bad kind"}), 400
    return jsonify({"ok": True, "kind": kind, "rows": gaash_mail.stat_detail(kind)})


@app.route("/api/gaash/thread_detail")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_thread_detail():
    d = gaash_mail.thread_detail((request.args.get("gwd") or "").strip().upper())
    if not d:
        return jsonify({"ok": False, "error": "thread not found"}), 404
    return jsonify({"ok": True, **d})


@app.route("/api/gaash/account/add", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_account_add():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.add_account(b.get("email"), b.get("app_password"),
                                 b.get("label"))
    if res.get("ok"):
        db.audit(auth.actor(),
                 "gaash_account_pw_update" if res.get("updated")
                 else "gaash_account_add", "gaash", res["email"], "")
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/account/remove", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_account_remove():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.remove_account(b.get("id"))
    return jsonify(res)


@app.route("/api/gaash/accounts/health")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_accounts_health():
    return jsonify({"ok": True, **gaash_mail.accounts_health()})


@app.route("/api/gaash/accounts/repair", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_accounts_repair():
    """Rebuild a mailbox table whose index stopped matching its rows — the
    failure that reads as 'Gmail rejected this login' on every account."""
    res = gaash_mail.repair_accounts()
    if res.get("ok"):
        db.audit(auth.actor(), "gaash_accounts_repair", "gaash",
                 ",".join(res.get("kept") or []),
                 f"dropped {res.get('dropped')} unreadable row(s)")
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/account/uses")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_account_uses():
    """How many conversations a mailbox sends for — read before offering to
    delete it, so the confirm can name the number instead of the damage
    appearing afterwards."""
    n = gaash_mail.account_thread_count((request.args.get("id") or "").strip())
    return jsonify({"ok": True, "threads": n})


@app.route("/api/gaash/resend", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_resend():
    """Send an already-sent message again, optionally from another mailbox —
    which moves the conversation there, so its follow-ups keep coming from the
    address that just wrote."""
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.resend_message(b.get("msg_id"), b.get("account_id"))
    if res.get("ok"):
        activity.log("send", "gaash", 0, res.get("gwd") or "",
                     detail="re-sent a message", user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/ids", methods=["GET", "POST", "DELETE"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_ids():
    if request.method == "GET":
        return jsonify({"ok": True, "ids": gaash_mail.ids_list()})
    if not current_user.has("admin_actions"):   # library edits are admin-only
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    if request.method == "DELETE":
        return jsonify({"ok": gaash_mail.ids_remove(b.get("id"))})
    if b.get("action") == "move":       # re-file, no upload
        return jsonify(gaash_mail.ids_move(b.get("id"), b.get("folder")))
    try:
        data = base64.b64decode(str(b.get("data_base64") or ""), validate=False)
    except Exception:  # noqa
        data = b""
    if not data or len(data) > 15 * 1024 * 1024:
        return jsonify({"ok": False, "error": "bad or oversized file"}), 400
    return jsonify(gaash_mail.ids_add(b.get("name"), b.get("filename"), data,
                                      folder=b.get("folder")))


@app.route("/api/gaash/declaration", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_declaration():
    """Check that a package can be papered — the name it ships under, that
    name's ID, its real contents. Nothing is stored: the declaration is written
    fresh onto every email it rides (gaash_mail.DECL_AUTO), so this only has to
    say yes or name the reason. Pass save=true for the rare hand-filed copy."""
    b = request.get_json(force=True, silent=True) or {}
    save = bool(b.get("save"))
    many = b.get("gwds")
    if isinstance(many, list):
        # one refusal must not sink the batch: a parcel with no name is reported
        # by name with its reason, the rest still come back ready
        results = [{**gaash_mail.declaration_make(g, save=save),
                    "gwd": str(g or "").strip().upper()} for g in many[:200]]
        done = [r["gwd"] for r in results if r.get("ok")]
        if done and save:
            activity.log("create", "gaash", 0, ",".join(done[:10]),
                         detail=f"filed {len(done)} customs declaration(s)",
                         user=_user())
        return jsonify({"ok": True, "results": results})
    res = gaash_mail.declaration_make(b.get("gwd"), save=save)
    if res.get("ok") and save:
        activity.log("create", "gaash", 0, res.get("gwd") or "",
                     detail="filed a customs declaration", user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/declaration_preview")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_declaration_preview():
    """The bytes GAASH is about to receive — built HERE, the same call the send
    makes, so what you read is what goes out.

    Every other attachment is a file on disk with a URL; the declaration is
    written fresh onto each email and never stored, so it had no URL and was the
    one document that could not be checked before sending. That is how a wrong
    ID number reached customs on four parcels: it was regenerated correctly from
    a wrong source every time, and nobody could open it to see.

    Inline, not a download — the point is to glance at it and go back."""
    gwd = (request.args.get("gwd") or "").strip().upper()
    got = gaash_mail.declaration_attachment(gwd)
    if not got:
        # declaration_make names the reason (no name on the parcel, an Arabic
        # name the document cannot print); the UI shows it instead of a dead link
        res = gaash_mail.declaration_make(gwd)
        return jsonify({"ok": False,
                        "error": res.get("error") or "no declaration"}), 400
    name, data, ctype = got
    return send_file(io.BytesIO(data), mimetype=ctype,
                     as_attachment=False, download_name=name)


@app.route("/api/gaash/idfile")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_idfile():
    # ID scans are sensitive — served only via this authed route (same rule as
    # /api/customer_image), never public.
    p = gaash_mail.id_file_path((request.args.get("file") or "").strip())
    if not p:
        abort(404)
    return send_file(p)


@app.route("/api/gaash/attachment")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_attachment():
    p = gaash_mail.attachment_path((request.args.get("gwd") or "").strip().upper(),
                                   (request.args.get("file") or "").strip())
    if not p:
        abort(404)
    return send_file(p)


@app.route("/api/gaash/name_id", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_name_id():
    """Map one on-package name → ID number from the 🪪 column in the enroll
    picker. Merges (never replaces) gaash_mail.name_ids. Writing settings is an
    admin job — same gate as the ⚙ panel that owns the rest of that map."""
    if not current_user.has("admin_actions"):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.set_name_id(b.get("name"), b.get("id_number"))
    if res.get("error"):
        return jsonify({"ok": False, **res}), 400
    db.audit(auth.actor(), "gaash_name_id", "settings", res["name"],
             res["id_number"] or "(cleared)")
    return jsonify({"ok": True, **res})


@app.route("/api/gaash/start", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_start():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.start_threads(b.get("gwds") or [], b.get("id_doc_id"),
                                   b.get("account_id"),
                                   seq_id=(b.get("seq_id") or "").strip() or None,
                                   names=b.get("names") if isinstance(b.get("names"), dict) else None,
                                   schedule=b.get("schedule") if isinstance(b.get("schedule"), dict) else None,
                                   docs=b.get("docs") if isinstance(b.get("docs"), dict) else None,
                                   replace=bool(b.get("replace")))
    started = [r["gwd"] for r in res if r.get("ok")]
    if started:
        activity.log("send", "gaash", 0, ",".join(started[:10]),
                     detail=f"started {len(started)} clearance sequence(s)",
                     user=_user())
    return jsonify({"ok": True, "results": res})


@app.route("/api/gaash/freeze", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_freeze():
    """⏸ freeze/resume EVERYTHING — all workflows Off (and back)."""
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.freeze_all(bool(b.get("on", True)))
    activity.log("update", "gaash", 0, "freeze",
                 detail=("frozen" if res.get("frozen") else "resumed"),
                 user=_user())
    return jsonify(res)


@app.route("/api/gaash/pick", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_pick():
    """Pin a parcel's ship-under name WITHOUT enrolling it (picker save-on-change)."""
    b = request.get_json(force=True, silent=True) or {}
    gwd = (b.get("gwd") or "").strip().upper()
    if not re.match(r"GWD\d+$", gwd):
        return jsonify({"ok": False, "error": "not a GWD number"}), 400
    return jsonify(gaash_mail.set_parcel_name(gwd, b.get("pname")))


@app.route("/api/gaash/autoclear", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_autoclear():
    """The in-app ✅ AUTO CLEAR tag (Purchases parcels — Leluxe tags in ClickUp)."""
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(gaash_mail.autoclear_toggle(b.get("gwd"), bool(b.get("on"))))


@app.route("/api/gaash/readiness")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_readiness():
    """🩺 the pre-flight table: name / ID / tag / conversation per parcel."""
    return jsonify(gaash_mail.readiness())


@app.route("/api/gaash/docs_queue")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_docs_queue():
    """📄 the docs-upload queue: every open parcel (both boards) with its GAASH
    docs banner — yellow upload-asked first, with GAASH's own upload links."""
    return jsonify(gaash_mail.docs_queue())


@app.route("/api/gaash/send", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_send():
    b = request.get_json(force=True, silent=True) or {}
    gwd = (b.get("gwd") or "").strip().upper()
    # doc_ids attach straight from the library — the bytes never round-trip
    # through the browser just to come back again
    files = _gm_files(b.get("files"))
    for did in (b.get("doc_ids") or [])[:6]:
        # DECL_AUTO = "write this parcel's declaration now" — never a stored file
        got = (gaash_mail.declaration_attachment(gwd)
               if did == gaash_mail.DECL_AUTO
               else gaash_mail._library_doc(did))
        if got:
            files.append(got)
    res = gaash_mail.send_manual(gwd, b.get("body"), files)
    if res.get("ok"):
        activity.log("send", "gaash", 0, gwd, detail="manual GAASH email",
                     user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/thread", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_thread():
    b = request.get_json(force=True, silent=True) or {}
    gwd = (b.get("gwd") or "").strip().upper()
    action = (b.get("action") or "").strip()
    th = gaash_mail.thread_get(gwd)
    if not th:
        return jsonify({"ok": False, "error": "thread not found"}), 404
    if action == "pause":
        gaash_mail._thread_set(gwd, state="paused")
    elif action == "resume":
        gaash_mail._thread_set(gwd, state="active", missing_docs=0,
                               missing_note=None,
                               next_send_at=db.now_iso())
    elif action == "done":
        gaash_mail._thread_set(gwd, state="done")
    elif action == "mark_read":
        gaash_mail._thread_set(gwd, unread=0)
    elif action == "missing_docs":
        gaash_mail._thread_set(gwd, missing_docs=1, state="missing_docs",
                               missing_note=str(b.get("note") or "").strip() or None)
    elif action == "clear_missing":
        gaash_mail._thread_set(gwd, missing_docs=0, missing_note=None,
                               state="active", next_send_at=db.now_iso())
    elif action == "attach":
        queued = json.loads(th.get("pending_files_json") or "[]")
        for name, data, ctype in _gm_files(b.get("files")):
            stored = gaash_mail._store_attachment(gwd, name, data)
            queued.append({"name": name, "file": stored, "ctype": ctype})
        gaash_mail._thread_set(gwd, pending_files_json=json.dumps(queued))
    elif action == "send_next_now":
        if gaash_mail.package_terminal(gwd):
            gaash_mail._thread_set(gwd, state="goal_met")
            return jsonify({"ok": False, "error": "package already cleared/delivered"})
        res = gaash_mail.send_step(gwd)
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "task_done":
        res = gaash_mail.task_done(gwd)
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "approve":       # a trigger-proposed enrollment → real
        if th.get("state") != "proposed":
            return jsonify({"ok": False, "error": "not a proposed thread"}), 400
        res = gaash_mail.start_threads([gwd], b.get("id_doc_id"),
                                       b.get("account_id"),
                                       seq_id=th.get("seq_id"))
        return jsonify({"ok": True, "results": res})
    elif action == "set_name":      # pin the name this parcel ships under ("" clears)
        return jsonify(gaash_mail.set_parcel_name(gwd, b.get("pname")))
    elif action == "delete":        # erase an accidental enrollment (admin)
        if not current_user.has("admin_actions"):
            return jsonify({"ok": False, "error": "admin only"}), 403
        res = gaash_mail.thread_delete(gwd)
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "restart":       # 🔁 back to email #1 (now, or scheduled)
        res = gaash_mail.thread_restart(gwd, fresh=bool(b.get("fresh")),
                                        at=b.get("at"))
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "rearm":         # attach the paperwork FIRST, then email #1
        res = gaash_mail.thread_rearm(gwd, doc_ids=b.get("doc_ids"),
                                      seq_id=(b.get("seq_id") or "").strip(),
                                      at=b.get("at"))
        if res.get("ok"):
            activity.log("send", "gaash", 0, gwd,
                         detail=f"re-armed with {res.get('docs') or 0} document(s)",
                         user=_user())
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "switch_seq":    # move this package to a different workflow
        res = gaash_mail.thread_switch_seq(gwd, (b.get("seq_id") or "").strip(),
                                           at=b.get("at"))
        if res.get("ok"):
            activity.log("send", "gaash", 0, gwd,
                         detail=f"moved to workflow {res.get('seq_name') or ''}".strip(),
                         user=_user())
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "switch_acct":   # send this conversation from another mailbox
        res = gaash_mail.thread_switch_acct(gwd, b.get("account_id"))
        if res.get("ok"):
            activity.log("send", "gaash", 0, gwd,
                         detail=f"now sends from {res.get('email') or ''}".strip(),
                         user=_user())
        return jsonify(res), (200 if res.get("ok") else 400)
    elif action == "dismiss":
        if th.get("state") != "proposed":
            return jsonify({"ok": False, "error": "not a proposed thread"}), 400
        with db.connect() as _c:
            _c.execute("DELETE FROM gaash_threads WHERE gwd=?", (gwd,))
        return jsonify({"ok": True})
    else:
        return jsonify({"ok": False, "error": "unknown action"}), 400
    return jsonify({"ok": True, "thread": gaash_mail.thread_get(gwd)})


@app.route("/api/gaash/check", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_check():
    return jsonify(gaash_mail.check_replies())


@app.route("/api/gaash/test_send", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_test_send():
    """Send a test email from an account TO ITSELF — proves SMTP works from
    this host (Render may block outbound 587) without ever touching GAASH."""
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.send_test(b.get("account_id"))
    if res.get("error") == "account not found":
        return jsonify(res), 404
    return jsonify(res)


# ── v2: sequences / templates / rules CRUD + dashboard + public tracking ────

@app.route("/api/gaash/sequences", methods=["GET", "POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_sequences():
    if request.method == "GET":
        return jsonify({"ok": True, "sequences": gaash_mail.sequences_list()})
    if not current_user.has("admin_actions"):   # builder edits are admin-only
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.sequence_save(b)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/sequence/delete", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_sequence_delete():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.sequence_archive((b.get("id") or "").strip())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/sequence/toggle", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_sequence_toggle():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.sequence_toggle((b.get("id") or "").strip(),
                                     bool(b.get("paused")))
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/sequence/clone", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_gaash_sequence_clone():
    b = request.get_json(force=True, silent=True) or {}
    res = gaash_mail.sequence_clone((b.get("id") or "").strip())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/templates", methods=["GET", "POST", "DELETE"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_templates():
    if request.method == "GET":
        return jsonify({"ok": True, "templates": gaash_mail.templates_list()})
    if not current_user.has("admin_actions"):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    if request.method == "DELETE":
        res = gaash_mail.template_remove((b.get("id") or "").strip())
    else:
        res = gaash_mail.template_save(b, user=_user())
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/template_render", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_template_render():
    """One template, rendered for ONE package — the same _fill() the sequencer
    sends with, so a template picked by hand carries exactly what an automated
    email would. Renders only; nothing is sent.

    Also reports which tokens did not survive: `unresolved` came back literal
    (no such board column), `blank` resolved to nothing at all. A visible
    {days_waiting} is a fixable mistake — a silently empty sentence is not.
    """
    b = request.get_json(force=True, silent=True) or {}
    gwd = (b.get("gwd") or "").strip().upper()
    tid = (b.get("template_id") or "").strip()
    tpl = next((t for t in gaash_mail.templates_list() if t["id"] == tid), None)
    if not tpl:
        return jsonify({"ok": False, "error": "template not found"}), 404
    th = gaash_mail.thread_get(gwd)
    body_tpl = tpl.get("body_tpl") or ""
    subj_tpl = tpl.get("subject_tpl") or ""
    unresolved, blank = [], []
    for tok in sorted(set(re.findall(r"\{([^{}]+)\}", body_tpl + " " + subj_tpl))):
        one = gaash_mail._fill("{" + tok + "}", gwd, th)
        if one == "{" + tok + "}":
            unresolved.append(tok)
        elif not one.strip():
            blank.append(tok)

    def render(text):
        # a token that resolves to NOTHING is left standing as itself. _fill()
        # substitutes "" — right for the sequencer, wrong here: it turns into
        # "The ID document () is attached", a blank the writer cannot see. The
        # token survives the fill behind a sentinel, so the writer meets
        # "{id_name}" and can fix it. _fill itself is untouched.
        for i, t in enumerate(blank):
            text = text.replace("{" + t + "}", f"\x00{i}\x00")
        text = gaash_mail._fill(text, gwd, th)
        for i, t in enumerate(blank):
            text = text.replace(f"\x00{i}\x00", "{" + t + "}")
        return text

    # previewing twenty packages before enrolling must not rewrite "last used"
    # twenty times — only an actual pick counts as a use
    if not b.get("preview"):
        gaash_mail.template_touch(tid)
    return jsonify({"ok": True, "subject": render(subj_tpl), "body": render(body_tpl),
                    "unresolved": unresolved, "blank": blank})


@app.route("/api/gaash/rules", methods=["GET", "POST", "DELETE"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_rules():
    if request.method == "GET":
        return jsonify({"ok": True, "rules": gaash_mail.rules_list()})
    if not current_user.has("admin_actions"):
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    if request.method == "DELETE":
        res = gaash_mail.rule_remove((b.get("id") or "").strip())
    else:
        res = gaash_mail.rule_save(b)
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/gaash/rules/run", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_rules_run():
    return jsonify({"ok": True, "proposed": gaash_mail.run_rules()})


@app.route("/api/gaash/rules/preview", methods=["POST"])
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_rules_preview():
    """Live 'matches now: N' for a criteria set being edited (nothing is saved)."""
    b = request.get_json(force=True, silent=True) or {}
    return jsonify({"ok": True, **gaash_mail.rule_matches(b.get("cond"))})


@app.route("/api/gaash/rules/matches")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_rules_matches():
    """⚡ chips: {rule_id: {count, gwds}} for every enabled trigger."""
    return jsonify({"ok": True, "matches": gaash_mail.rules_match_map()})


@app.route("/api/gaash/stats")
@auth.require("edit_fulfillment")
@auth.require_feature("leluxe")
def api_gaash_stats():
    return jsonify({"ok": True, **gaash_mail.stats()})


# PUBLIC tracking endpoints — the email recipient hits these, no session. The
# HMAC signature (OTLOBLY_SECRET) is the auth: a bad sig is a plain 404.
@app.route("/api/gaash/px/<token>.gif")
@limiter.exempt
def api_gaash_pixel(token):
    v = gaash_mail.verify_token(token, "")
    if v:
        try:
            gaash_mail.record_event(v[0], "open",
                                    ua=request.headers.get("User-Agent"))
        except Exception:  # noqa - tracking must never 500 at the recipient
            pass
    resp = app.response_class(gaash_mail.PIXEL_GIF, mimetype="image/gif")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/gaash/r/<token>")
@limiter.exempt
def api_gaash_redirect(token):
    url = request.args.get("u", "")
    v = gaash_mail.verify_token(token, url)
    if not v or not re.match(r"^https?://", url):
        abort(404)
    try:
        gaash_mail.record_event(v[0], "click", url=url[:500],
                                ua=request.headers.get("User-Agent"))
    except Exception:  # noqa
        pass
    return redirect(url, code=302)


@app.route("/api/leluxe/order/delete", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_order_delete():
    b = request.get_json(force=True, silent=True) or {}
    row = leluxe_mod.get_row(b.get("id"))
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    leluxe_mod.soft_delete(row["id"])
    activity.log("deleted", "leluxe", row["id"], row["name"] or f"#{row['id']}",
                 detail="hidden locally (the ClickUp task is untouched)", user=_user())
    return jsonify({"ok": True})


@app.route("/api/leluxe/image", methods=["GET", "POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_image():
    import base64
    if request.method == "GET":
        fn = request.args.get("file", "")
        p = leluxe_mod.IMAGE_DIR / fn
        if fn and "/" not in fn and p.exists():
            return send_file(p)
        abort(404)
    b = request.get_json(force=True, silent=True) or {}
    row = leluxe_mod.get_row(b.get("id"))
    if not row:
        return jsonify({"ok": False, "error": "row not found"}), 404
    data = b.get("data_base64", "")
    if data.strip().startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    ext = (b.get("filename", "img.png").rsplit(".", 1)[-1] or "png")[:5]
    leluxe_mod.IMAGE_DIR.mkdir(exist_ok=True)
    fn = f"lx{row['id']}-{uuid.uuid4().hex[:8]}.{ext}"
    try:
        (leluxe_mod.IMAGE_DIR / fn).write_bytes(base64.b64decode(data))
    except Exception as e:  # noqa
        return jsonify({"ok": False, "error": str(e)})
    leluxe_mod.add_image(row["id"], fn)
    activity.log("uploaded", "leluxe", row["id"], row["name"] or f"#{row['id']}",
                 detail="uploaded an image → syncing to ClickUp", user=_user())
    return jsonify({"ok": True, "file": fn,
                    "images": (leluxe_mod.get_row(row["id"])["data"].get("images") or [])})


@app.route("/api/leluxe/push_status")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_push_status():
    return jsonify({"ok": True, "push_disabled": leluxe_mod.push_disabled(),
                    **leluxe_mod.sync_counts()})


@app.route("/api/leluxe/push", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_push():
    n = leluxe_mod.retry_errors()
    return jsonify({"ok": True, "retried": n})


@app.route("/api/leluxe/goal")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_goal():
    """Everything the 🎯 Goal view renders: the month's math, the az-tool
    ready-profile capacity, and the settings. ?month=YYYY-MM views a past
    month; ?refresh=1 re-reads az-tool (busts its 60s cache)."""
    refresh = request.args.get("refresh") == "1"
    month = (request.args.get("month") or "").strip() or None
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        return jsonify({"ok": False, "error": "month must be YYYY-MM"}), 400
    st = leluxe_goal.settings()
    prof = leluxe_goal.profiles_summary(st, refresh=refresh)
    return jsonify({"ok": True, "goal": leluxe_goal.compute(month=month),
                    "profiles": prof, "settings": st,
                    "all_tags": prof.get("all_tags") or []})


@app.route("/api/leluxe/goal_settings", methods=["GET", "POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_goal_settings():
    if request.method == "GET":
        return jsonify({"ok": True, "settings": leluxe_goal.settings()})
    st, err = leluxe_goal.save_settings(request.get_json(force=True, silent=True) or {})
    if err:
        return jsonify({"ok": False, "error": err}), 400
    activity.log("set", "leluxe", "goal", "Goal settings",
                 detail=f"goal ${st['goal_usd']:,.0f}/شهر · tag {st['ready_tag']!r}"
                        f" · digest {st['digest_hour']}:00"
                        f"{'' if st['digest_enabled'] else ' (off)'}", user=_user())
    return jsonify({"ok": True, "settings": st})


@app.route("/api/leluxe/goal_digest", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_goal_digest():
    """{send:false} → preview the digest text; {send:true} → send it to the
    owner chat NOW (the ⋮ test button — deliberately outside the once-a-day
    claim, so testing never eats the real 21:00 digest)."""
    import telegram
    b = request.get_json(force=True, silent=True) or {}
    txt = leluxe_goal.digest_text()
    if not b.get("send"):
        return jsonify({"ok": True, "text": txt, "sent": False})
    out = telegram.send(txt)
    if not out.get("ok"):
        return jsonify({"ok": False, "text": txt, "error": out.get("error")}), 502
    return jsonify({"ok": True, "text": txt, "sent": True})


@app.route("/api/leluxe/goal_backfill", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_leluxe_goal_backfill():
    """Fill ordered_at (true AZ (2) order dates) — {dry_run:true} reports what
    WOULD change; a real run writes it. Guarded by the same 120s mutex as the
    importer so it can't race a pull."""
    b = request.get_json(force=True, silent=True) or {}
    dry = bool(b.get("dry_run", True))
    flag = db.get_setting("leluxe:import_running") or 0
    try:
        flag = float(flag)
    except (TypeError, ValueError):
        flag = 0
    if time.time() - flag < 120:
        return jsonify({"ok": False, "error": "an import is already running"}), 409
    db.set_setting("leluxe:import_running", time.time())
    try:
        rep = leluxe_goal.backfill_ordered_at(dry_run=dry, force=bool(b.get("force")))
    finally:
        db.set_setting("leluxe:import_running", 0)
    if rep.get("error"):
        return jsonify({"ok": False, **rep}), 502
    if not dry:
        activity.log("set", "leluxe", "goal", "Goal dates backfill",
                     detail=f"ordered_at filled on {rep['filled']} rows "
                            f"(src {rep['matched_source']} · name {rep['matched_name']}"
                            f" · inherited {rep['matched_inherit']})", user=_user())
    return jsonify({"ok": True, **rep})


# --------------------------------------------------------------------------- #
# 🏆 Goals — the multi-source campaign tracker (ClickUp lists + Purchases)
# --------------------------------------------------------------------------- #
@app.route("/api/goals")
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_goals():
    """The whole 🏆 Goals snapshot. ?refresh=1 busts the ClickUp cache; the
    page's 10s poll serves the stamp-keyed cache, so it stays cheap until a
    webhook (or a settings save) bumps the stamp."""
    snap = goals.compute(refresh=request.args.get("refresh") == "1")
    return jsonify({"ok": True, "goals": snap, "settings": goals.settings(),
                    "webhook": goals.webhook_info()})


@app.route("/api/goals/settings", methods=["GET", "POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_goals_settings():
    if request.method == "GET":
        return jsonify({"ok": True, "settings": goals.settings()})
    st, err = goals.save_settings(request.get_json(force=True, silent=True) or {})
    if err:
        return jsonify({"ok": False, "error": err}), 400
    goals.bump_stamp("settings")    # excluded/window edits must bust every worker's cache
    tgt = " + ".join(f"{c['label']} ${c['target']:,.0f}" for c in st["categories"])
    activity.log("set", "goals", "multi", "Goals settings",
                 detail=f"{st['window_start']} → {st['window_end']} · {tgt}", user=_user())
    return jsonify({"ok": True, "settings": st})


@app.route("/api/goals/webhook_setup", methods=["POST"])
@auth.require("admin_actions")
@auth.require_feature("leluxe")
def api_goals_webhook_setup():
    """{action:"status"} → live ClickUp webhook health; {action:"create"} →
    register the instant-sync webhooks (one per source list, idempotent);
    {action:"delete"} → remove them. Create refuses a localhost base —
    ClickUp can't reach it."""
    b = request.get_json(force=True, silent=True) or {}
    action = (b.get("action") or "status").strip()
    if action == "create":
        base = _portal_base()
        if "localhost" in base or "127.0.0.1" in base:
            return jsonify({"ok": False, "error": "webhooks need the public app "
                            "(PORTAL_BASE_URL) — ClickUp can't reach localhost"}), 400
        out, err = goals.webhook_create(base)
        if err:
            return jsonify({"ok": False, "error": err}), 502
        activity.log("set", "goals", "webhook", "Goals instant sync",
                     detail=f"webhooks → {out.get('endpoint')}", user=_user())
        return jsonify({"ok": True, "webhook": out})
    if action == "delete":
        out, err = goals.webhook_delete()
        if err:
            return jsonify({"ok": False, "error": err}), 502
        activity.log("set", "goals", "webhook", "Goals instant sync",
                     detail="webhooks removed", user=_user())
        return jsonify({"ok": True, "webhook": out})
    out, err = goals.webhook_status()
    if err:
        return jsonify({"ok": False, "error": err}), 502
    return jsonify({"ok": True, "webhook": out})


# --------------------------------------------------------------------------- #
# Platform admin: brokers — Otlobly's super-admin provisions Tatabu tenants.
# --------------------------------------------------------------------------- #
def _platform_admin():
    """Only Otlobly's own admin (business #1) may create/list brokers."""
    return (current_user.is_authenticated and current_user.has("admin_actions")
            and getattr(current_user, "business_id", 1) == 1)


@app.route("/api/admin/brokers", methods=["GET", "POST"])
@auth.require("admin_actions")
def api_admin_brokers():
    if not _platform_admin():
        abort(403)
    if request.method == "POST":
        b = request.get_json(force=True, silent=True) or {}
        name = (b.get("name") or "").strip()
        tier = b.get("tier") if b.get("tier") in quotas.TIERS else quotas.DEFAULT_TIER
        au = (b.get("admin_username") or "").strip()
        ap = (b.get("admin_password") or "").strip()
        if not name or not au or len(ap) < 6:
            return jsonify({"ok": False, "error": "Broker name, an admin username, and a "
                                                   "6+ character password are required."}), 400
        slug = re.sub(r"[^a-z0-9]+", "-", (b.get("slug") or name).lower()).strip("-") or None
        try:
            bid = db.create_business(name, slug)
        except Exception as e:  # noqa: BLE001 — unique slug etc.
            return jsonify({"ok": False, "error": f"Couldn't create the broker ({e})."}), 400
        db.set_business_config(bid, "tier", tier)
        if (b.get("brand_name") or "").strip():
            db.set_business_config(bid, "brand", {"name": b["brand_name"].strip()})
        try:
            db.create_user(au, auth.hash_pw(ap), "admin",
                           (b.get("admin_name") or name).strip(), business_id=bid)
        except Exception as e:  # noqa: BLE001 — username is globally unique
            return jsonify({"ok": False, "error": f"Broker created, but the admin login "
                                                  f"failed — username taken? ({e})"}), 400
        db.audit(auth.actor(), "create_broker", "business", str(bid), f"{name} · {tier}")
        activity.log("created", "business", str(bid), name,
                     detail=f"new broker · {tier} · admin {au}", user=_user())
        return jsonify({"ok": True, "business_id": bid, "slug": slug, "tier": tier,
                        "admin_username": au, "admin_password": ap})
    # GET: every tenant — Otlobly #1 first (tier "unlimited", the platform's own
    # business), then the brokers — each with tier + live counts.
    out = []
    for biz in db.list_businesses():
        out.append({**biz, "tier": quotas.tier(biz["id"]),
                    "orders": db.count_rows("orders", biz["id"]),
                    "customers": db.count_rows("customers", biz["id"]),
                    "seats": db.count_active_users(biz["id"])})
    return jsonify({"brokers": out, "tiers": list(quotas.TIERS.keys())})


@app.route("/api/admin/broker/tier", methods=["POST"])
@auth.require("admin_actions")
def api_admin_broker_tier():
    """Change a broker's plan (Otlobly super-admin only)."""
    if not _platform_admin():
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    bid = b.get("business_id")
    tier = b.get("tier")
    if not (str(bid).isdigit() and int(bid) != 1 and tier in quotas.TIERS):
        return jsonify({"ok": False, "error": "bad business or tier"}), 400
    db.set_business_config(int(bid), "tier", tier)
    db.audit(auth.actor(), "set_broker_tier", "business", str(bid), tier)
    biz = db.get_business(int(bid)) or {}
    activity.log("set", "business", str(bid), biz.get("name") or f"#{bid}",
                 field="plan", new=tier, user=_user())
    return jsonify({"ok": True, "tier": tier})


@app.route("/api/admin/broker/<int:bid>")
@auth.require("admin_actions")
def api_admin_broker_detail(bid):
    """One tenant's profile for the platform admin: identity, plan, live quota/usage
    (or plain counts for Otlobly, which is unlimited), staff, and recent activity.
    Business #1 (Otlobly itself) resolves to a read-only profile — `internal: True`,
    no tier/impersonation (it's the platform's own business, not a broker)."""
    if not _platform_admin():
        abort(403)
    biz = db.get_business(bid)
    if not biz:
        abort(404)
    import branding
    brand = branding.resolve(bid)
    return jsonify({
        "business": {k: biz.get(k) for k in ("id", "name", "slug", "active", "created_at")},
        "brand": {"name": brand["name"], "tagline": brand["tagline"]},
        "tier": quotas.tier(bid),
        "status": quotas.status(bid),
        "counts": {"orders": db.count_rows("orders", bid),
                   "customers": db.count_rows("customers", bid),
                   "seats": db.count_active_users(bid)},
        "users": db.list_users(bid),
        "activity": activity.recent(30, business_id=bid),
        "internal": bid == 1,
    })


@app.route("/api/admin/broker/impersonate", methods=["POST"])
@auth.require("admin_actions")
def api_admin_impersonate():
    """Open a broker's dashboard as them (support view). Session-scoped; every
    write while it's on is a REAL write to the broker's data, attributed to us."""
    if not _platform_admin():
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    bid = b.get("business_id")
    if not (str(bid).isdigit() and int(bid) != 1):
        return jsonify({"ok": False, "error": "bad business"}), 400
    biz = db.get_business(int(bid))
    if not biz:
        abort(404)
    if not biz.get("active", 1):
        return jsonify({"ok": False, "error": "business is inactive"}), 400
    session["support_view_bid"] = int(bid)
    db.audit(auth.actor(), "impersonate_start", "business", str(bid), biz["name"])
    activity.log("viewed", "business", str(bid), biz["name"],
                 detail="support view — opened their dashboard", user=_user())
    return jsonify({"ok": True, "business_id": int(bid)})


@app.route("/api/admin/broker/impersonate/stop", methods=["POST"])
@auth.require("admin_actions")
def api_admin_impersonate_stop():
    """Exit the support view (idempotent)."""
    if not _platform_admin():
        abort(403)
    bid = session.pop("support_view_bid", None)
    if bid:
        db.set_current_business(1)   # this request was still scoped to the broker —
        biz = db.get_business(int(bid)) or {}          # log the exit in OUR feed
        db.audit(auth.actor(), "impersonate_stop", "business", str(bid), biz.get("name", ""))
        activity.log("viewed", "business", str(bid), biz.get("name") or f"#{bid}",
                     detail="support view — exited", user=_user())
    return jsonify({"ok": True})


@app.route("/api/admin/broker/user", methods=["POST"])
@auth.require("admin_actions")
def api_admin_broker_user():
    """Reset a broker staff member's password (platform admin only). Verifies the
    user really belongs to that broker so an id can't cross tenants."""
    if not _platform_admin():
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    bid, uid = b.get("business_id"), b.get("user_id")
    pw = str(b.get("password") or "").strip()
    if not (str(bid).isdigit() and int(bid) != 1 and str(uid).isdigit()):
        return jsonify({"ok": False, "error": "bad business or user"}), 400
    if len(pw) < 6:
        return jsonify({"ok": False, "error": "password must be 6+ chars"}), 400
    u = db.get_user_by_id(int(uid))
    if not u or (u.get("business_id") or 1) != int(bid):
        return jsonify({"ok": False, "error": "user not found"}), 404
    db.set_user_password(u["id"], auth.hash_pw(pw))
    db.audit(auth.actor(), "platform_reset_pw", "user", u["username"], f"business {bid}")
    return jsonify({"ok": True})


@app.route("/api/admin/platform")
@auth.require("admin_actions")
def api_admin_platform():
    """One payload for the Tatabu console's Overview / Plans / Usage screens:
    every broker tenant with tier + price + live quota status, platform totals,
    the tier matrix, and MRR (Σ tier price over active brokers)."""
    if not _platform_admin():
        abort(403)
    prices = db.get_setting("platform:tier_prices", {}) or {}
    tenants = []
    totals = {"brokers": 0, "orders": 0, "customers": 0, "seats": 0, "over_quota": 0}
    tier_counts = {t: 0 for t in quotas.TIERS}
    mrr = 0
    for biz in db.list_businesses():
        if biz["id"] == 1:
            continue
        t = quotas.tier(biz["id"])
        st = quotas.status(biz["id"])
        res = st.get("resources", {})
        price = float(prices.get(t) or 0)
        tenants.append({**biz, "tier": t, "price": price, "status": st})
        totals["brokers"] += 1
        totals["orders"] += (res.get("orders") or {}).get("used", 0)
        totals["seats"] += (res.get("seats") or {}).get("used", 0)
        totals["customers"] += db.count_rows("customers", biz["id"])
        if st.get("over_any"):
            totals["over_quota"] += 1
        tier_counts[t] = tier_counts.get(t, 0) + 1
        if biz.get("active", 1):
            mrr += price
    tiers = {t: {"limits": quotas.TIERS[t], "price": prices.get(t),
                 "count": tier_counts.get(t, 0)} for t in quotas.TIERS}
    return jsonify({"tenants": tenants, "totals": totals, "tiers": tiers,
                    "mrr": round(mrr, 2)})


@app.route("/api/admin/platform/activity")
@auth.require("admin_actions")
def api_admin_platform_activity():
    """Cross-tenant activity feed (provisioning, tier changes, every broker's own
    events), each stamped with its business."""
    if not _platform_admin():
        abort(403)
    try:
        lim = min(int(request.args.get("limit", 100)), 300)
    except ValueError:
        lim = 100
    return jsonify({"activity": activity.platform_recent(db.list_businesses(), lim)})


@app.route("/api/admin/platform/prices", methods=["POST"])
@auth.require("admin_actions")
def api_admin_platform_prices():
    """Set the monthly USD price per tier (global — lives in the settings KV)."""
    if not _platform_admin():
        abort(403)
    b = request.get_json(force=True, silent=True) or {}
    raw = b.get("prices")
    if not isinstance(raw, dict) or not raw:
        return jsonify({"ok": False, "error": "prices required"}), 400
    prices = {}
    for t, v in raw.items():
        if t not in quotas.TIERS:
            return jsonify({"ok": False, "error": f"unknown tier '{t}'"}), 400
        try:
            v = float(v)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"bad price for {t}"}), 400
        if v < 0:
            return jsonify({"ok": False, "error": "prices can't be negative"}), 400
        prices[t] = round(v, 2)
    db.set_setting("platform:tier_prices", prices)
    db.audit(auth.actor(), "set_tier_prices", "platform", "",
             " ".join(f"{t}=${p}" for t, p in sorted(prices.items())))
    return jsonify({"ok": True, "prices": prices})


# --------------------------------------------------------------------------- #
# Admin: user management
# --------------------------------------------------------------------------- #
@app.route("/api/users", methods=["GET", "POST", "PATCH"])
@auth.require("manage_users")
def api_users():
    if request.method == "POST":
        b = request.get_json(force=True, silent=True) or {}
        pw = (b.get("password") or "").strip()   # stray spaces from copy-paste break login later
        if b.get("role") not in auth.ROLES or not b.get("username") or len(pw) < 6:
            return jsonify({"ok": False, "error": "username, role, 6+ char password required"}), 400
        try:
            db.create_user(b["username"].strip(), auth.hash_pw(pw),
                           b["role"], b.get("name", ""),
                           business_id=db.current_business())
        except Exception as e:  # noqa (unique violation)
            return jsonify({"ok": False, "error": f"{e}"}), 400
        db.audit(auth.actor(), "create_user", "user", b["username"], b["role"])
        return jsonify({"ok": True})
    if request.method == "PATCH":                 # deactivate/reactivate or reset password
        b = request.get_json(force=True, silent=True) or {}
        u = db.get_user_by_id(int(b["id"])) if str(b.get("id", "")).isdigit() else None
        # Tenant fence: staff may only touch users of their OWN business (NULL = business 1).
        if u and (u.get("business_id") or 1) != db.current_business():
            u = None
        if not u:
            return jsonify({"ok": False, "error": "user not found"}), 404
        if "active" in b:
            if not b["active"] and u["id"] == current_user.id:
                return jsonify({"ok": False, "error": "you can't deactivate yourself"}), 400
            db.set_user_active(u["id"], bool(b["active"]))
            db.audit(auth.actor(), "user_active", "user", u["username"], str(bool(b["active"])))
        if b.get("password"):
            pw = str(b["password"]).strip()
            if len(pw) < 6:
                return jsonify({"ok": False, "error": "password must be 6+ chars"}), 400
            db.set_user_password(u["id"], auth.hash_pw(pw))
            db.audit(auth.actor(), "user_reset_pw", "user", u["username"], "")
        return jsonify({"ok": True})
    return jsonify({"users": db.list_users(db.current_business()), "roles": auth.ROLES})


# --------------------------------------------------------------------------- #
# Worker API (the local Mac worker that places Amazon orders) — bearer token
# --------------------------------------------------------------------------- #
def _worker_ok():
    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    auth_h = request.headers.get("Authorization", "")
    # constant-time compare (matches _backup_ok) so the token can't be timing-probed
    return bool(tok) and hmac.compare_digest(auth_h, f"Bearer {tok}")


@app.route("/api/worker/queue")
def worker_queue():
    if not _worker_ok():
        abort(401)
    ready = [o for o in db.list_orders()
             if o["status"] == "PAID" and not o.get("amazon_order_number")]
    return jsonify({"orders": ready})


@app.route("/api/worker/result", methods=["POST"])
def worker_result():
    if not _worker_ok():
        abort(401)
    b = request.get_json(force=True, silent=True) or {}
    changes = {k: b[k] for k in ("amazon_order_number", "checkout",
                                 "amount_to_collect_usd", "profile_box", "status")
               if k in b}
    changes.setdefault("placed_at", db.now_iso())
    o = db.update_order(b.get("id"), changes, {"username": "worker"})
    return jsonify({"ok": bool(o)})


@app.route("/api/worker/packages")
def worker_packages():
    """Every PO package that has a tracking number, with the customers waiting
    on it — read hourly by the Mac's GAASH tracking sync (gaash-clickup-sync)."""
    if not _worker_ok():
        abort(401)
    import purchases
    orders = {o.get("order_id"): o for o in db.list_orders()}
    out = []
    for po in purchases.load()["purchase_orders"]:
        for pk in po.get("packages") or []:
            tn = (pk.get("tracking_number") or "").strip()
            if not tn:
                continue
            custs, seen = [], set()
            for it in pk.get("items") or []:
                oid = it.get("customer_order_id")
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                o = orders.get(oid) or {}
                custs.append({"order_id": oid,
                              "name": it.get("customer_name") or "",
                              "amount_to_collect_usd": o.get("amount_to_collect_usd"),
                              "order_status": o.get("status")})
            out.append({"po_id": po.get("po_id"),
                        "package_no": pk.get("package_no"),
                        "tracking_number": tn,
                        "customer_tracking": pk.get("customer_tracking"),
                        "tracking_status": pk.get("tracking_status"),
                        "tracking_code": pk.get("tracking_code"),
                        "tracking_time": pk.get("tracking_time"),
                        "arrival": pk.get("arrival"),
                        "customers": custs})
    return jsonify({"packages": out})


@app.route("/api/worker/tracking", methods=["POST"])
def worker_tracking():
    """The Mac's tracking sync writes live GAASH statuses back onto PO
    packages. Status fields only — never touches orders, never messages
    customers. Body: {"updates": [{po_id, package_no, tracking_status,
    tracking_code, tracking_time}, ...]}."""
    if not _worker_ok():
        abort(401)
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    updates = {(u.get("po_id"), u.get("package_no")): u
               for u in b.get("updates") or [] if u.get("po_id")}
    if not updates:
        return jsonify({"ok": True, "updated": 0})
    pdb = purchases.load()
    n = 0
    import tracking
    for po in pdb["purchase_orders"]:
        for pk in po.get("packages") or []:
            u = updates.get((po.get("po_id"), pk.get("package_no")))
            if not u:
                continue
            for k in ("tracking_status", "tracking_code", "tracking_time"):
                if k in u:
                    pk[k] = u[k]
            # The Mac's gaash-clickup-sync posts tracking_status as a BARE STRING
            # (raw GAASH text) — that has no .bucket, so every pill fell back to
            # amber "In transit" and re-clobbered the app's granular dict hourly.
            # Coerce strings into the same shape the app's own refresh writes.
            ts = pk.get("tracking_status")
            if isinstance(ts, str):
                pk["tracking_status"] = tracking.staff_status_from_events(
                    [{"code": u.get("tracking_code"), "text": ts,
                      "time": u.get("tracking_time")}]) or {"text": ts, "bucket": "transit"}
            pk["tracking_checked"] = db.now_iso()
            n += 1
    if n:
        purchases.save(pdb)
        db.audit({"username": "worker"}, "tracking_sync", "purchase", "-",
                 f"updated GAASH status on {n} package(s)")
    return jsonify({"ok": True, "updated": n})


# --------------------------------------------------------------------------- #
# Backup — one zip of ALL live data, pulled nightly by backup_pull.py (Mac)
# --------------------------------------------------------------------------- #
# Every writable artifact on the data disk. New data_path() files/dirs added to
# the app MUST be added here too, or they won't be in backups.
BACKUP_FILES = ("customers.json", "purchases.json", "trash.json", "orders.json",
                "activity.jsonl", "config.json", "tracking_cache.json",
                "leluxe_sync_report.json")   # leluxe_presync_* stay out of the zip (short-lived safety snapshots; the DB itself is in every backup)
BACKUP_DIRS = ("customer_ids", "po_images", "leluxe_images",
               "gaash_ids", "gaash_mail")


def _backup_ok():
    """Admin session (Settings download button) OR the worker bearer token
    (the nightly pull). Token compared constant-time."""
    if current_user.is_authenticated and current_user.has("admin_actions"):
        return True
    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    auth_h = request.headers.get("Authorization", "")
    return bool(tok) and hmac.compare_digest(auth_h, f"Bearer {tok}")


@app.route("/api/backup")
def api_backup():
    """Stream a consistent snapshot of the ENTIRE business state as one zip:
    the SQLite DB (via sqlite3's backup API — safe under WAL while the app is
    live), the JSON stores, and the uploaded ID/PO images, plus a manifest with
    row counts so the puller can sanity-check. Contains PII + password hashes —
    admin/worker-token only. Restore = unzip into the data dir + restart."""
    if not _backup_ok():
        abort(401)
    from paths import DATA_DIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp = tempfile.NamedTemporaryFile(prefix="otlobly-backup-", suffix=".zip",
                                      delete=False)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            # 1) DB snapshot — never zip the raw file while WAL is active.
            dbtmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            dbtmp.close()
            try:
                src, dst = db.connect(), sqlite3.connect(dbtmp.name)
                with dst:
                    src.backup(dst)
                dst.close()
                src.close()
                z.write(dbtmp.name, "otlobly.db")
            finally:
                os.unlink(dbtmp.name)
            # 2) JSON stores + uploaded images (whitelist — see BACKUP_* above).
            for name in BACKUP_FILES:
                p = DATA_DIR / name
                if p.is_file():
                    z.write(p, name)
            for dname in BACKUP_DIRS:
                d = DATA_DIR / dname
                if d.is_dir():
                    for p in sorted(d.rglob("*")):
                        if p.is_file():
                            z.write(p, str(p.relative_to(DATA_DIR)))
            # 3) Manifest — lets backup_pull.py verify the zip holds real data.
            with db.connect() as c:
                counts = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                          for t in ("users", "customers", "orders", "payments",
                                    "meta_leads")}
            z.writestr("manifest.json", json.dumps(
                {"created_at": db.now_iso(), "counts": counts}, indent=2))
        tmp.close()
        f = open(tmp.name, "rb")
        os.unlink(tmp.name)          # POSIX: lives until the handle closes
    except Exception:
        tmp.close()
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise
    db.audit(auth.actor() or {"username": "worker"}, "backup", "db", stamp, "")
    return send_file(f, mimetype="application/zip", as_attachment=True,
                     download_name=f"otlobly-backup-{stamp}.zip")


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Disaster recovery: replace the live SQLite DB with the otlobly.db inside
    an uploaded backup zip (worker-token only — same auth as /api/backup). For
    when the live DB is corrupt. ONLY the DB is swapped; JSON stores + images on
    the disk are left untouched. The uploaded DB is integrity-checked before the
    swap, and the current file is kept as otlobly.db.pre-restore-<ts>. Because
    db.connect() opens the file per-operation, the app serves the restored data
    immediately (no restart needed)."""
    if not _backup_ok():
        abort(401)
    # STREAM the upload: a backup zip is ~100 MB and grows with the business, so
    # reading the body (and the decompressed DB) into memory would spike well past
    # what the 512 MB instance can absorb and take the whole service down with it.
    # Body → temp file → zip entry → staging file, a chunk at a time.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = db.DB_FILE.with_name(db.DB_FILE.name + f".incoming-{stamp}")
    up = tempfile.NamedTemporaryFile(prefix="otlobly-restore-", suffix=".zip",
                                     delete=False)
    try:
        shutil.copyfileobj(request.stream, up, length=1024 * 1024)
        up.close()
        if not os.path.getsize(up.name):
            abort(400, "empty body — POST the backup zip as the raw body")
        try:
            with zipfile.ZipFile(up.name) as z, z.open("otlobly.db") as src, \
                    open(staging, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        except Exception as e:        # noqa: BLE001 — report any zip/entry issue
            try:
                staging.unlink()
            except OSError:
                pass
            abort(400, f"could not read otlobly.db from the zip: {e}")
    finally:
        try:
            os.unlink(up.name)
        except OSError:
            pass
    try:                              # verify it's a real, intact SQLite DB
        t = sqlite3.connect(str(staging))
        ok = t.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {tb: t.execute(f"SELECT COUNT(*) FROM {tb}").fetchone()[0]
                  for tb in ("users", "customers", "orders", "payments")}
        t.close()
        if ok != "ok":
            raise ValueError(f"integrity_check={ok}")
    except Exception as e:            # noqa: BLE001
        try:
            staging.unlink()
        except OSError:
            pass
        abort(400, f"uploaded DB failed validation: {e}")
    for ext in ("-wal", "-shm"):      # drop the corrupt/empty file's WAL sidecars
        p = db.DB_FILE.with_name(db.DB_FILE.name + ext)
        try:
            p.unlink()
        except OSError:
            pass
    if db.DB_FILE.exists():
        try:
            db.DB_FILE.rename(db.DB_FILE.with_name(
                db.DB_FILE.name + f".pre-restore-{stamp}"))
        except OSError:
            pass
    os.replace(staging, db.DB_FILE)   # atomic swap-in
    db.init_db()                       # idempotent schema/migrate on the restored DB
    return jsonify({"ok": True, "restored_at": db.now_iso(), "counts": counts})


@app.route("/api/worker/seed", methods=["POST"])
def worker_seed():
    """One-time data import from the Mac → server (orders / customers / purchases).
    Same bearer-token channel as the worker; idempotent upserts so it's safe to
    re-run. Customer data travels Mac→server over HTTPS, never through the repo."""
    if not _worker_ok():
        abort(401)
    b = request.get_json(force=True, silent=True) or {}
    out = {"customers": 0, "orders": 0, "linked": 0, "purchases": 0}
    for c in b.get("customers", []):
        try:
            db.upsert_customer(c)
            out["customers"] += 1
        except Exception:
            pass
    orders = b.get("orders", [])
    for o in orders:
        try:
            db.upsert_order(o)
            out["orders"] += 1
        except Exception:
            pass
    try:  # link orders → customers (mirrors migrate_json_to_db)
        with db.connect() as conn:
            key_to_id = {r["match_key"]: r["id"]
                         for r in conn.execute("SELECT id, match_key FROM customers")}
            for o in orders:
                ph = store.primary_phone(o)
                key = ph["e164"] if ph else ("name:" + o["customer"]["name"].strip().lower())
                cid = key_to_id.get(key)
                if cid:
                    conn.execute("UPDATE orders SET customer_id=? WHERE order_code=?",
                                 (cid, o["order_id"]))
                    out["linked"] += 1
    except Exception as e:
        out["link_error"] = str(e)[:80]
    pos = b.get("purchases")
    if pos is not None:
        try:
            import purchases as _pur
            pdb = _pur.load()
            pdb["purchase_orders"] = pos
            _pur.save(pdb)
            out["purchases"] = len(pos)
        except Exception as e:
            out["purchase_error"] = str(e)[:80]
    return jsonify({"ok": True, **out})


# --------------------------------------------------------------------------- #
# Customer portal — read-only, order # + WhatsApp lookup, rate-limited
# --------------------------------------------------------------------------- #
def _amz_image_from_html(html):
    """Pull the main product image URL out of an Amazon product page's HTML."""
    for pat in (r'data-old-hires=["\'](https://[^"\']+)',
                r'"hiRes":"(https://[^"\\]+)"',
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'id=["\']landingImage["\'][^>]+src=["\'](https://[^"\']+)'):
        m = re.search(pat, html)
        if m:
            return m.group(1).replace("\\/", "/")
    # last resort: first product image on Amazon's CDN, upgraded to a large size
    m = re.search(r'm\.media-amazon\.com/images/I/([A-Za-z0-9+_-]{6,})\._', html)
    if m:
        return f"https://m.media-amazon.com/images/I/{m.group(1)}._AC_SL1500_.jpg"
    return None


def _amz_title_from_html(html):
    tm = (re.search(r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)', html)
          or re.search(r'<title>([^<]+)</title>', html))
    title = tm.group(1).strip()[:140] if tm else None
    if title:
        title = re.sub(r"\s*[:|-]\s*Amazon\.com.*$", "", title)
        title = title.replace("Amazon.com:", "").strip()
    return title or None


@app.route("/api/product_image")
@auth.require("view_orders")
def api_product_image():
    """Product-image grab for the Quick Quote tool. FREE paths first (direct Amazon fetch,
    then a free reader proxy — both no SerpApi credits); if Amazon blocks those (cloud IPs
    like Render usually are), falls back to SerpAPI via the import cache (a previously seen
    ASIN costs nothing; a new one uses one credit AND archives its price — which becomes the
    product's original 💲 price). Staff can always paste/upload instead."""
    from urllib import request as _u
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"error": "no url"}), 400
    try:
        clean = normalize.clean_amazon_url(url, expand=True) or url
    except Exception:
        clean = url
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def _fetch(u, hdrs, timeout, cap):
        try:
            with _u.urlopen(_u.Request(u, headers=hdrs), timeout=timeout) as r:
                return r.read(cap).decode("utf-8", "replace")
        except Exception:  # noqa
            return ""

    attempts = [
        (clean, {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}, 15, 900000),
        ("https://r.jina.ai/" + clean,                       # proxy fetches from a non-blocked IP
         {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9", "X-Return-Format": "html"}, 30, 1200000),
    ]
    img = title = None
    for u, hdrs, timeout, cap in attempts:
        html = _fetch(u, hdrs, timeout, cap)
        if not html:
            continue
        img = _amz_image_from_html(html)
        if img:
            title = _amz_title_from_html(html)
            break
    if not img:
        # Last resort: SerpAPI (cache-first — a known ASIN is free; a fresh one spends a
        # credit and archives image/title/price for the To-order 💲 button).
        d = amazon_import.import_product(clean, refresh=False)
        if d.get("image"):
            return jsonify({"image": d["image"], "title": d.get("title"), "source": "serpapi"})
        return jsonify({"error": "Amazon blocked the image fetch — paste or upload the photo."})
    return jsonify({"image": img, "title": title})


@app.route("/api/draft", methods=["POST"])
@auth.require("view_orders")
def api_draft():
    """Staff: turn a quote into a pre-filled customer intake link. Stores the quoted
    products + amount under a short id; returns the shareable /order/<id> URL."""
    b = request.get_json(force=True, silent=True) or {}
    did = uuid.uuid4().hex[:10]
    db.set_setting(f"draft:{did}", {
        "products": b.get("products") or [],
        "amount": b.get("amount"),
        "created_at": db.now_iso(), "used": False,
    })
    return jsonify({"ok": True, "draft_id": did, "path": f"/order/{did}",
                    "url": _portal_base() + f"/order/{did}"})


@app.route("/api/order/quote_link", methods=["POST"])
@auth.require("edit_order")
def api_order_quote_link():
    """One click on a priced order → a customer-facing quotation link (the
    /order/<draft_id> page with pictures + final price + confirm form) plus a
    wa.me link to the CUSTOMER's WhatsApp carrying it. The draft remembers
    order_id, so the customer's confirmation updates THIS order (no duplicate)."""
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order((b.get("id") or "").strip())
    if not o:
        return jsonify({"ok": False, "error": "order not found"}), 404
    if o.get("amount_to_collect_usd") is None:
        return jsonify({"ok": False,
                        "error": "سعّر الطلب أولاً (💲) · Price the order first (💲)."}), 400
    products = [{"image": it.get("image"), "title": it.get("title"),
                 "qty": it.get("qty") or 1,
                 "link": it.get("clean_url") or it.get("raw_url"),
                 "asin": it.get("asin"),
                 "serp_price_usd": it.get("serp_price_usd"),
                 "serp_price_at": it.get("serp_price_at")}
                for it in (o.get("items") or [])]
    did = uuid.uuid4().hex[:10]
    db.set_setting(f"draft:{did}", {
        "products": products, "amount": o["amount_to_collect_usd"],
        "order_id": o["order_id"],
        "created_at": db.now_iso(), "used": False,
    })
    url = _portal_base() + f"/order/{did}"
    ph = store.primary_phone(o)
    name = (o.get("customer") or {}).get("name") or ""
    text = (f"مرحباً {name} 👋 عرض سعرك من اطلبلي جاهز:\n{url}\n"
            "افتح الرابط لتأكيد الطلب · open the link to confirm")
    wa = f"https://wa.me/{ph['wa']}?text={_urlquote(text)}" if ph and ph.get("wa") else None
    db.audit(auth.actor(), "quote_link", "order", o["order_id"], did)
    activity.log("sent", "order", o["order_id"], _olabel(o),
                 detail="quotation link generated", user=_user())
    return jsonify({"ok": True, "url": url, "wa_link": wa})


@app.route("/api/order/request_id_link", methods=["POST"])
@auth.require("edit_order")
def api_order_request_id_link():
    """One click on a To-order row → a per-customer link to the /id/<token> page
    where the customer types their ID number (+ optional photo). The token
    remembers the order + customer so the ID lands on the right CRM profile and
    auto-shows on all their orders/packages. Returns a copy URL + a wa.me link."""
    b = request.get_json(force=True, silent=True) or {}
    o = db.get_order((b.get("id") or "").strip())
    if not o:
        return jsonify({"ok": False, "error": "order not found"}), 404
    cust = _crm_customer_for_order(o)
    did = uuid.uuid4().hex[:10]
    db.set_setting(f"idreq:{did}", {
        "order_id": o["order_id"],
        "customer_id": (cust or {}).get("customer_id"),
        "match_key": (cust or {}).get("match_key"),
        "created_at": db.now_iso(), "used": False,
    })
    url = _portal_base() + f"/id/{did}"
    ph = store.primary_phone(o)
    name = (o.get("customer") or {}).get("name") or ""
    text = (f"مرحباً {name} 👋 لإتمام طلبك من اطلبلي، فضلاً أدخل رقم هويتك "
            f"(وصورتها اختياري):\n{url}\n"
            "Please enter your ID number (photo optional) to complete your order.")
    wa = f"https://wa.me/{ph['wa']}?text={_urlquote(text)}" if ph and ph.get("wa") else None
    db.audit(auth.actor(), "request_id_link", "order", o["order_id"], did)
    activity.log("sent", "order", o["order_id"], _olabel(o),
                 detail="ID-request link generated", user=_user())
    return jsonify({"ok": True, "url": url, "wa_link": wa})


@app.route("/id/<token>", methods=["GET"])
def submit_id_page(token):
    return render_template("submit_id.html")


@app.route("/api/idreq/<token>", methods=["GET"])
@limiter.limit("20 per minute")
def api_idreq_get(token):
    """Public: hydrate the /id/<token> page — prefill the customer's name/phone and
    whether they already have an ID on file. 404 on a bad/used token."""
    d = db.get_setting(f"idreq:{token}")
    if not d or d.get("used"):
        return jsonify({"error": "not found"}), 404
    name = phone = ""
    oo = db.get_order(d.get("order_id") or "")
    if oo:
        ph = store.primary_phone(oo)
        name = (oo.get("customer") or {}).get("name") or ""
        phone = (ph.get("e164") if ph else "") or ""
    cust = db.get_customer(d.get("customer_id")) if d.get("customer_id") else None
    return jsonify({"ok": True, "name": name, "phone": phone,
                    "have_number": bool((cust or {}).get("id_number")),
                    "have_image": bool((cust or {}).get("id_image"))})


@app.route("/api/id/submit", methods=["POST"])
@limiter.limit("6 per minute")
def api_id_submit():
    """Public (token-gated, single-use): the customer submits an ID number AND an
    ID photo — both required. Saved onto their CRM profile (reused across all their
    orders); the order is stamped id_submitted_at. Image serving stays authed
    (via /api/customer_image GET) — never echoed back here."""
    import base64
    b = request.get_json(force=True, silent=True) or {}
    tok = (b.get("token") or "").strip()
    d = db.get_setting(f"idreq:{tok}")
    if not d or d.get("used"):
        return jsonify({"error": "This link has expired."}), 404
    id_number = (b.get("id_number") or "").strip()
    if not id_number:
        return jsonify({"error": "رقم الهوية مطلوب · An ID number is required."}), 400
    cust = db.get_customer(d.get("customer_id")) if d.get("customer_id") else None
    if not cust:
        oo0 = db.get_order(d.get("order_id") or "")
        cust = _crm_customer_for_order(oo0) if oo0 else None
    if not cust:
        return jsonify({"error": "This link has expired."}), 404
    cust["id_number"] = id_number[:40]
    # The photo is REQUIRED. Every failure below returns early, so the token is
    # never burned and nothing is saved — the customer can just retry. (This used
    # to swallow a failed decode/write and still answer ok, which would now mean
    # telling them "Received ✓" with no image on file.)
    data = (b.get("data_base64") or "").strip()
    if not data:
        return jsonify({"error": "صورة الهوية مطلوبة · An ID photo is required."}), 400
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    raw = b""
    try:
        if len(data) <= 22 * 1024 * 1024:         # ~15 MB decoded
            raw = base64.b64decode(data)
    except Exception:  # noqa
        raw = b""
    if not raw or len(raw) > 15 * 1024 * 1024:
        return jsonify({"error": "الصورة كبيرة جداً أو غير صالحة (الحد 15 ميغابايت) · "
                                 "That photo is too large or unreadable (15 MB max)."}), 400
    ext = re.sub(r"[^a-z0-9]", "", (b.get("filename", "id.png")
                 .rsplit(".", 1)[-1] or "png").lower())[:5] or "png"
    fn = f"{cust['customer_id']}-{uuid.uuid4().hex[:8]}.{ext}"
    try:
        cust_mod.ID_DIR.mkdir(exist_ok=True)
        (cust_mod.ID_DIR / fn).write_bytes(raw)
    except OSError:
        return jsonify({"error": "تعذّر حفظ الصورة · Could not save that photo."}), 500
    cust["id_image"] = fn
    db.upsert_customer(cust)
    oo = db.get_order(d.get("order_id") or "")
    if oo:
        oo["id_submitted_at"] = db.now_iso()
        db.upsert_order(oo)
    db.set_setting(f"idreq:{tok}", {**d, "used": True})
    db.audit({"username": "customer"}, "id_submitted", "customer",
             cust.get("customer_id", ""), "")
    activity.log("uploaded", "customer", cust.get("customer_id", ""),
                 cust.get("name", ""), detail="customer submitted ID number",
                 user="customer")
    return jsonify({"ok": True})


@app.route("/api/bridge/draft", methods=["POST"])
def api_bridge_draft():
    """Server-to-server: sara-tool (deployed) creates a pre-filled draft here.
    Authed by X-Bridge-Token == SARA_BRIDGE_TOKEN (no staff session needed)."""
    tok = os.environ.get("SARA_BRIDGE_TOKEN")
    if not tok or request.headers.get("X-Bridge-Token") != tok:
        abort(401)
    b = request.get_json(force=True, silent=True) or {}
    did = uuid.uuid4().hex[:10]
    db.set_setting(f"draft:{did}", {
        "products": b.get("products") or [],
        "amount": b.get("amount"),
        "created_at": db.now_iso(), "used": False,
        "source": b.get("source") or "sara-tool",
    })
    return jsonify({"ok": True, "draft_id": did, "path": f"/order/{did}",
                    "url": _portal_base() + f"/order/{did}"})


@app.route("/order/<draft_id>", methods=["GET"])
def order_intake_page(draft_id):
    # wa_number: without it the shared nav's WhatsApp/Contact link self-hides here
    return render_template("order.html", wa_number=_wa_business_number())


# A sample "Amazon checkout" screenshot for the /order/demo preview (so the big proof
# image is visible). Real quotes use a pasted/uploaded screenshot instead.
_DEMO_PROOF = "data:image/svg+xml;charset=utf-8," + _urlquote(
    '<svg xmlns="http://www.w3.org/2000/svg" width="560" height="400" '
    'font-family="Arial,Helvetica,sans-serif">'
    '<rect width="560" height="400" fill="rgb(255,255,255)"/>'
    '<rect width="560" height="56" fill="rgb(35,47,62)"/>'
    '<text x="24" y="36" fill="rgb(255,255,255)" font-size="22" font-weight="bold">amazon</text>'
    '<text x="24" y="94" fill="rgb(17,17,17)" font-size="20" font-weight="bold">Order Summary</text>'
    '<line x1="24" y1="112" x2="536" y2="112" stroke="rgb(227,227,227)"/>'
    '<text x="24" y="152" fill="rgb(51,51,51)" font-size="17">Items (1):</text>'
    '<text x="436" y="152" fill="rgb(51,51,51)" font-size="17">$89.00</text>'
    '<text x="24" y="190" fill="rgb(51,51,51)" font-size="17">Shipping / handling:</text>'
    '<text x="436" y="190" fill="rgb(51,51,51)" font-size="17">$18.40</text>'
    '<text x="24" y="228" fill="rgb(51,51,51)" font-size="17">Import Fees Deposit:</text>'
    '<text x="436" y="228" fill="rgb(51,51,51)" font-size="17">$11.60</text>'
    '<line x1="24" y1="250" x2="536" y2="250" stroke="rgb(227,227,227)"/>'
    '<text x="24" y="292" fill="rgb(177,39,4)" font-size="22" font-weight="bold">Order total:</text>'
    '<text x="404" y="292" fill="rgb(177,39,4)" font-size="22" font-weight="bold">$119.00</text>'
    '<text x="24" y="348" fill="rgb(136,136,136)" font-size="13">'
    'Sample Amazon checkout — مثال صورة الفاتورة</text></svg>')


@app.route("/api/draft/<draft_id>", methods=["GET"])
@limiter.limit("20 per minute")
def api_draft_get(draft_id):
    if draft_id == "demo":                         # stable preview link (no real quote behind it)
        return jsonify({"products": [{
            "title": "Apple AirPods Pro (2nd Generation) — مثال / sample",
            "image": "https://m.media-amazon.com/images/I/61SUj2aKoEL._AC_SL1500_.jpg",
            "proof": _DEMO_PROOF, "qty": 1}], "amount": 119.0, "used": False, "demo": True})
    d = db.get_setting(f"draft:{draft_id}")
    if not d:
        return jsonify({"error": "not found"}), 404
    out = {"products": d.get("products") or [], "amount": d.get("amount"),
           "used": d.get("used", False)}
    if d.get("order_id"):
        # quotation drafts know their order → pre-fill the confirm form (the
        # customer already gave name+WhatsApp on the /order request page)
        oo = db.get_order(d["order_id"])
        if oo:
            ph = store.primary_phone(oo)
            out["prefill"] = {"name": (oo.get("customer") or {}).get("name") or "",
                              "phone": (ph.get("e164") if ph else "") or ""}
    return jsonify(out)


@app.route("/api/order/intake", methods=["POST"])
@limiter.limit("6 per minute")
def api_order_intake():
    """Public: a customer submits their contact for a pre-filled draft → creates the
    REQUESTED order (in 'Need to order') carrying the quoted products + amount."""
    b = request.get_json(force=True, silent=True) or {}
    if (b.get("draft_id") or "") == "demo":         # preview link — never creates a real order
        return jsonify({"error": "هذا رابط تجريبي للعرض فقط · This is a demo preview — "
                        "generate a real link from the dashboard."}), 400
    d = db.get_setting(f"draft:{(b.get('draft_id') or '')}")
    if not d:
        return jsonify({"error": "This link has expired."}), 404
    prods = d.get("products") or []
    phones = normalize.collect_phones(b.get("phone"))
    if not (b.get("name") or "").strip() or not phones:
        return jsonify({"error": "Name and a valid phone are required."}), 400
    if d.get("order_id"):
        # Quotation drafts (🔗 from the To-order queue) price an EXISTING order —
        # the customer's confirm fills their address and stamps acceptance; it must
        # never create a duplicate. Single-use, like every draft.
        if d.get("used"):
            return jsonify({"error": "This link has expired."}), 404
        o = db.get_order(d["order_id"])
        if not o:
            return jsonify({"error": "This link has expired."}), 404
        cu = o.setdefault("customer", {})
        cu["address"] = (b.get("address") or "").strip() or cu.get("address") or ""
        cu["city"] = (b.get("city") or "").strip() or cu.get("city") or ""
        if not (cu.get("name") or "").strip():
            cu["name"] = b.get("name", "").strip()
        if not cu.get("phones"):
            cu["phones"] = phones
        o["customer_confirmed_at"] = db.now_iso()
        db.upsert_order(o)
        _ensure_customer(o)
        db.set_setting(f"draft:{b['draft_id']}", {**d, "used": True})
        db.audit({"username": "customer"}, "quote_confirmed", "order", o["order_id"], "")
        activity.log("confirmed", "order", o["order_id"], _olabel(o),
                     detail="customer confirmed the quotation", user="customer")
        return jsonify({"ok": True, "order_id": o["order_id"]})
    items = normalize.parse_items([p.get("link") or
                                   (f"https://www.amazon.com/dp/{p.get('asin')}" if p.get("asin") else "")
                                   for p in prods], expand=False)
    by_asin = {(p.get("asin") or "").upper(): p for p in prods}
    for it in items:
        p = by_asin.get((it.get("asin") or "").upper())
        if p:
            it["image"], it["title"] = p.get("image"), p.get("title")
            it["item_usd"], it["qty"] = p.get("item_usd"), int(p.get("qty") or 1)
            # keep the quote-time internal price — "the first price the customer got"
            if p.get("serp_price_usd") is not None:
                it["serp_price_usd"] = p["serp_price_usd"]
                it["serp_price_at"] = p.get("serp_price_at") or db.now_iso()
    o = make_order(name=b.get("name", ""), phones=phones, address=b.get("address", ""),
                   city=b.get("city", ""), items=items, status="REQUESTED",
                   amount_to_collect_usd=(float(d["amount"]) if d.get("amount") else None))
    o["source"] = "intake"                 # staff-sent draft link (vs 'website' cart orders)
    db.insert_new_order(o)
    _ensure_customer(o)                    # add the self-intake customer to the CRM / ID page
    db.set_setting(f"draft:{b['draft_id']}", {**d, "used": True})
    db.audit({"username": "customer"}, "intake_order", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail="customer self-intake", user="customer")
    return jsonify({"ok": True, "order_id": o["order_id"]})


def _phone_pairs(core, pdb):
    """A normalized phone core → ([(po, package)], names, oids) for that customer's orders.
    Matches 970/972/leading-0 forms (normalize.phone_core)."""
    names, oids = set(), set()
    for o in db.list_orders():
        for ph in (o.get("customer", {}).get("phones") or []):
            if normalize.phone_core(ph.get("e164") or ph.get("raw") or "") == core:
                names.add((o["customer"]["name"] or "").strip().lower())
                oids.add(o["order_id"])
                break
    return purchases.find_packages_for(pdb, names, oids), names, oids


def _delivery_window(arrival):
    """Customer-facing delivery RANGE from a package arrival date: end = arrival + 1 week,
    start = 2 weeks before the end (= arrival − 1 week). e.g. 2026-07-08 → 2026-07-01 .. 2026-07-15."""
    if not arrival:
        return None, None
    try:
        d = datetime.strptime(str(arrival)[:10], "%Y-%m-%d")
    except Exception:  # noqa
        return None, None
    end = d + timedelta(days=7)
    return (end - timedelta(days=14)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _otlobly_stage(pk, pk_items, omap, po):
    """The owner-set Otlobly stage (the package's ClickUp-vocabulary dropdown) →
    the customer-facing label that OVERRIDES the carrier timeline. Carriers can
    only ever get the box to Otlobly; \"تم التسليم\" comes solely from here
    (the owner marking the package complete). Returns {label, bucket, date} or None."""
    ots = (pk.get("otlobly_status") or "").strip().lower()
    if not ots:
        return None
    for row in omap or []:
        if (row.get("status") or "").strip().lower() != ots:
            continue
        label = (row.get("label") or "").strip()
        if not label:
            return None
        if "{name}" in label:
            name = next(((it.get("customer_name") or "").strip()
                         for it in (pk_items or pk.get("items") or [])
                         if (it.get("customer_name") or "").strip()), "")
            label = label.replace("{name}", name or "إليك")
        # A weak map row (no/unknown bucket) must NOT force the progress bar to
        # "arrived" — return bucket=None and let the caller keep the carrier's.
        bucket = (row.get("bucket") or "").strip().lower()
        if bucket not in tracking.BUCKET_RANK:
            bucket = None
        return {"label": label, "bucket": bucket,
                "date": (po.get("updated_at") or "")[:10] or None}
    return None


def _shipments_for(pairs, names, oids):
    """[(po, package)] → friendly shipment cards: de-dupe, show ONLY this customer's items,
    one GAASH session for all timelines. Shared by /api/track and the customer dashboard.
    Exposes NO PII — only the masked status + the customer's own item names/images."""
    seen, uniq = set(), []
    for po, pk in pairs:
        key = (pk.get("customer_tracking") or "").upper() or id(pk)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((po, pk))
    uniq = uniq[:8]
    if not uniq:
        return []
    cfgd = cfg.load()
    smap = cfg.get(cfgd, "customer_tracking.status_map", tracking.DEFAULT_STATUS_MAP)
    dlabel = cfg.get(cfgd, "customer_tracking.default_label", tracking.DEFAULT_CUSTOMER_LABEL)
    omap = cfg.get(cfgd, "customer_tracking.otlobly_map", tracking.DEFAULT_OTLOBLY_MAP)
    gwds = [pk.get("tracking_number") for _, pk in uniq if (pk.get("tracking_number") or "").strip()]
    tls = tracking.timelines_cache_first(gwds) if gwds else {}
    shipments = []
    for po, pk in uniq:
        otl = pk.get("customer_tracking")
        gwd = (pk.get("tracking_number") or "").strip()
        # A package holds items for SEVERAL customers — show ONLY this customer's.
        if names or oids:                         # phone lookup → we know who they are
            pk_items = [it for it in pk.get("items", [])
                        if (it.get("customer_name") or "").strip().lower() in names
                        or (it.get("customer_order_id") or "").strip().upper() in oids]
        else:                                     # raw OTL lookup → no customer context → no item list
            pk_items = []
        items = [{"title": (it.get("title") or it.get("asin") or "").strip(),
                  "image": it.get("image") or None}
                 for it in pk_items
                 if (it.get("title") or it.get("asin") or it.get("image"))][:6]
        ef, et = _delivery_window(pk.get("arrival"))   # show the ETA whenever an arrival date is set,
        est = {"est_delivery": pk.get("arrival") or None, "est_from": ef, "est_to": et}  # even without a tracking #
        if not gwd:
            # an arrival date means it's been ordered/shipped → "on the way"; otherwise "preparing"
            label = "في الطريق إلى بلدك" if pk.get("arrival") else "نقوم بتجهيز طلبك"
            ship = {"tracking": otl, "items": items, "events": [],
                    "current": {"label": label, "bucket": "transit"}, **est}
        else:
            tl = tls.get(gwd, {})
            if tl.get("ok"):
                ct = tracking.customer_timeline(tl["events"], smap, dlabel)
                ship = {"tracking": otl, "items": items, "current": ct["current"],
                        "events": ct["events"], **est}
                if tl.get("stale"):               # served from the last-known cache
                    ship.update(stale=True, as_of=tl.get("fetched_at"))
            else:
                # every tier failed AND nothing cached → honest generic state
                ship = {"tracking": otl, "items": items, "events": [],
                        "current": {"label": "قيد الشحن", "bucket": "transit"}, **est}
        # The owner-set Otlobly stage outranks the carriers: hero + one synthetic
        # timeline event, so "استلمتها اطلبلي / في الطريق إلى {الاسم} / تم التسليم"
        # come from the owner, never from GAASH/Gerizim.
        stage = _otlobly_stage(pk, pk_items, omap, po)
        if stage:
            # The owner's LABEL always wins; a stage with no explicit bucket keeps
            # the carrier's bucket so the progress bar never jumps ahead of GAASH.
            b = stage["bucket"] or (ship.get("current") or {}).get("bucket") or "arrived"
            ship["current"] = {"label": stage["label"], "bucket": b}
            ship["events"] = (ship.get("events") or []) + [dict(stage, bucket=b)]
        shipments.append(ship)
    return shipments


@app.route("/track", methods=["GET"])
def track_page():
    return render_template("track.html", wa_number=_wa_business_number())


@app.route("/api/track", methods=["POST"])
@limiter.limit("20 per minute")
def api_track():
    """Public self-service tracking: customer enters their MOBILE or an OTL number → we
    find their package(s) → friendly status timeline. Exposes NO PII — only the status."""
    b = request.get_json(force=True, silent=True) or {}
    q = (b.get("query") or b.get("tracking") or b.get("phone") or "").strip()
    if not q:
        return jsonify({"found": False, "error": "Enter your full mobile number."}), 400
    names, oids = set(), set()
    if re.match(r"^OTL\d", q.upper()):            # an OTL tracking number (business 1 for now)
        pdb = purchases.load()
        po, pk = purchases.find_by_customer_tracking(pdb, q)
        pairs = [(po, pk)] if pk else []
    else:                                         # the customer's FULL mobile number
        core = normalize.phone_core(q)
        if len(core) < 9:
            return jsonify({"found": False,
                            "error": "Please enter your full mobile number (e.g. 0599xxxxxx)."}), 400
        _scope_to_phone(core)                     # route to the customer's broker
        pdb = purchases.load()                    # now the broker's own purchases file
        pairs, names, oids = _phone_pairs(core, pdb)
    shipments = _shipments_for(pairs, names, oids)
    if not shipments:
        return jsonify({"found": False, "error": "No package found for that number. "
                        "Make sure you entered your full mobile number, or contact us."}), 404
    return jsonify({"found": True, "count": len(shipments), "shipments": shipments})


# ---- Customer account portal: phone + WhatsApp OTP login -------------------- #
OTP_TTL = 600                  # code valid for 10 minutes
OTP_MAX_ATTEMPTS = 5

# --- Login-code cost-abuse guards (DB-backed → shared across workers, survive restart).
# The strongest guard is upstream: only KNOWN customers (with an order) reach the send at
# all (_match_customer). These add per-phone throttling + a global daily SMS budget so no
# one can quietly run up the Twilio bill even with a valid customer number. ---
OTP_RESEND_COOLDOWN = int(os.environ.get("OTP_RESEND_COOLDOWN", "60"))   # seconds between codes to one number
OTP_PHONE_DAILY_MAX = int(os.environ.get("OTP_PHONE_DAILY_MAX", "8"))    # max codes to one number / 24h
SMS_DAILY_CAP = int(os.environ.get("SMS_DAILY_CAP", "500"))             # global SMS/day circuit-breaker


def _otp_recent_sends(core):
    """Timestamps of codes sent to this number in the last 24h (self-pruning)."""
    now = time.time()
    rec = db.get_setting(f"otpsend:{core}") or {}
    return [t for t in (rec.get("sends") or []) if now - t < 86400]


def _otp_guard(core, channel):
    """Cost-abuse gate before sending a login code. Returns (ok, status, msg): a
    per-phone cooldown + daily cap (any channel), plus a global daily SMS budget when
    the channel actually costs money (sms)."""
    sends = _otp_recent_sends(core)
    if sends and time.time() - max(sends) < OTP_RESEND_COOLDOWN:
        return (False, 429,
                "انتظر قليلاً قبل طلب رمز جديد · Please wait a moment before requesting another code.")
    if len(sends) >= OTP_PHONE_DAILY_MAX:
        return (False, 429,
                "محاولات كثيرة اليوم · Too many codes today — try again later or use WhatsApp/email.")
    if channel == "sms" and db.get_usage(1, "otp_sms", datetime.now().strftime("%Y-%m-%d")) >= SMS_DAILY_CAP:
        app.logger.warning("SMS daily cap %s reached — refusing OTP SMS for %s", SMS_DAILY_CAP, core)
        return (False, 429,
                "تعذّر الإرسال الآن · Can't text a code right now — please use WhatsApp or email.")
    return (True, 200, None)


def _otp_record_send(core, channel):
    """Record a SUCCESSFUL code send: per-phone timestamp (self-pruning >24h) + the
    global daily SMS counter (only for the paid sms channel)."""
    db.set_setting(f"otpsend:{core}", {"sends": _otp_recent_sends(core) + [time.time()]})
    if channel == "sms":
        db.bump_usage(1, "otp_sms", datetime.now().strftime("%Y-%m-%d"), 1)


def customer_required(fn):
    """Guard customer-portal API: 401 unless a customer session is established."""
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("cust_phone"):
            abort(401)
        return fn(*a, **k)
    return wrapper


def customer_session_required(fn):
    """Lighter guard for auth-v2 onboarding: accepts a FULL session (cust_phone) or
    a not-yet-phone-linked one — Google email-only (cust_email) or a pending manual
    signup (cust_pending). Used only by /api/customer/orders (returns an empty
    shell for phone-less sessions) and the phone-link endpoints; everything
    money/data-bearing stays behind customer_required."""
    @wraps(fn)
    def wrapper(*a, **k):
        if not (session.get("cust_phone") or session.get("cust_email")
                or session.get("cust_pending")):
            abort(401)
        return fn(*a, **k)
    return wrapper


def _business_for_phone(core):
    """Which BUSINESS (broker) a customer phone belongs to — searched across ALL
    tenants, since the public portal has no staff login to tell us. Returns a
    business_id (the one with this phone's most recent order) or None. The customer
    portal calls this first, then scopes the request to that business so a broker's
    customer sees ONLY their broker's data."""
    if not core:
        return None
    best_bid, best_at = None, ""
    for bid, o in db.orders_business_index():
        for ph in (o.get("customer", {}).get("phones") or []):
            if normalize.phone_core(ph.get("e164") or ph.get("raw") or "") == core:
                at = o.get("created_at") or ""
                if best_bid is None or at > best_at:
                    best_bid, best_at = bid, at
                break
    return best_bid


def _scope_to_phone(core):
    """Resolve a phone's business and pin this request to it. Returns the business_id
    (or None if the phone belongs to no one). Used by the public track/login lookups."""
    bid = _business_for_phone(core)
    if bid:
        db.set_current_business(bid)
    return bid


def _match_customer(core):
    """(matched?, name) for a phone core — only KNOWN customers (with an order) can log in.
    Scans within the CURRENT business, so callers scope with _scope_to_phone first."""
    for o in db.list_orders():
        for ph in (o.get("customer", {}).get("phones") or []):
            if normalize.phone_core(ph.get("e164") or ph.get("raw") or "") == core:
                return True, (o.get("customer", {}).get("name") or "").strip()
    return False, ""


def _customer_orders(core):
    """The customer's own orders (newest first): status + ETA + items + THEIR money
    (price / deposit / remaining). Only the logged-in customer's own figures."""
    out = []
    for o in db.list_orders():
        if not any(normalize.phone_core(ph.get("e164") or ph.get("raw") or "") == core
                   for ph in (o.get("customer", {}).get("phones") or [])):
            continue
        amt = o.get("amount_to_collect_usd")
        dep = round(o.get("deposit_usd") or 0, 2)
        out.append({
            "order_id": o.get("order_id"),
            "status": o.get("status"),
            "est_delivery": o.get("est_delivery_customer"),
            "created_at": o.get("created_at"),
            "amount_usd": amt,
            "deposit_usd": dep,
            "remaining_usd": (round((amt or 0) - dep, 2) if amt is not None else None),
            "items": [{"title": (it.get("title") or it.get("asin") or "").strip(),
                       "image": it.get("image") or None, "qty": it.get("qty") or 1}
                      for it in (o.get("items") or [])
                      if (it.get("title") or it.get("asin") or it.get("image"))][:8],
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


@app.route("/account", methods=["GET"])
def account_page():
    # otp_enabled lights up the typed-code login (3rd method) only when the selected
    # channel (OTP_CHANNEL) can actually deliver AND its explicit enable flag is set:
    #   sms      → Twilio creds + SMS_OTP_ENABLED=1 (no Meta verification needed)
    #   whatsapp → Cloud API creds + WHATSAPP_OTP_ENABLED=1 (needs an APPROVED auth
    #              template — blocked until the Meta business is verified, tested 2026-07)
    # google_enabled shows "Sign in with Google" once the OAuth client env is set —
    # the free-per-login door for returning customers with a linked email.
    return render_template("account.html", wa_number=_wa_business_number(),
                           otp_enabled=notify.otp_available(),
                           google_enabled=google_login.configured())


@app.route("/api/customer/me")
def api_customer_me():
    if session.get("cust_phone"):
        core = session["cust_phone"]
        row = _customer_row_for_core(core) or {}
        email = (row.get("email") or "").strip()
        return jsonify({"logged_in": True, "phone_linked": True,
                        "name": session.get("cust_name") or "",
                        "phone": core,
                        "whatsapp": row.get("whatsapp") or None,   # e164 for form prefill
                        # full address, not masked: it's the visitor's OWN email,
                        # echoed back so they can see which one is on the account.
                        "email": email or None,
                        "email_verified": bool(email and row.get("email_verified_at")),
                        # drives the Settings "change password" box — only accounts
                        # that actually registered one can change it.
                        "has_password": bool(row.get("id")
                                             and db.get_customer_password_hash(row["id"]))})
    if session.get("cust_email") or session.get("cust_pending"):
        # Auth-v2 onboarding session (Google email-only, or a pending manual
        # signup) — logged in, but the phone isn't linked/verified yet.
        return jsonify({"logged_in": True, "phone_linked": False,
                        "name": session.get("cust_name") or "",
                        "pending_phone": session.get("cust_pending") or None,
                        "email": session.get("cust_email") or None})
    return jsonify({"logged_in": False})


# ---- Login WITHOUT a template: customer taps → sends US a WhatsApp message ------ #
# Meta lets you act on a message the customer SENT you (24h service window) with NO
# template and NO business verification. So the customer proves they own the phone
# by sending a one-time code from their WhatsApp; our inbound webhook logs them in.
# This is the verification-free path while business verification is pending.
WA_LOGIN_TTL = 600
WA_NONCE_RE = re.compile(r"OTL[0-9A-F]{8}")


def _wa_business_number():
    num = os.environ.get("WHATSAPP_BUSINESS_NUMBER") or cfg.get(cfg.load(), "business.whatsapp", "") or ""
    return "".join(c for c in str(num) if c.isdigit())


@app.route("/api/customer/wa_login/start", methods=["POST"])
@limiter.limit("6 per 10 minutes")
def api_wa_login_start():
    num = _wa_business_number()
    if not num:
        return jsonify({"ok": False, "error": "WhatsApp login not configured."}), 503
    nonce = "OTL" + secrets.token_hex(4).upper()          # e.g. OTL9F3A2B1C
    db.set_setting(f"walogin:{nonce}", {"status": "pending", "expires": time.time() + WA_LOGIN_TTL})
    session["walogin_nonce"] = nonce                       # only THIS browser can complete it
    session.permanent = True
    prefill = f"رمز دخولي إلى اطلبلي: {nonce}\n(أرسل هذه الرسالة كما هي · send this as-is)"
    return jsonify({"ok": True, "wa_url": f"https://wa.me/{num}?text={_urlquote(prefill)}"})


def _wa_login_consume(phone_raw, text):
    """Shared by both inbound webhooks: if `text` carries a pending login nonce,
    mark it verified/unknown for the sender's phone. Returns the resulting status
    ('verified' | 'unknown' | 'ignored') so callers can branch a reply on it."""
    m = WA_NONCE_RE.search((text or "").upper())
    if not m:
        return "ignored"
    rec = db.get_setting(f"walogin:{m.group(0)}")
    if not rec or rec.get("status") != "pending" or time.time() > (rec.get("expires") or 0):
        return "ignored"
    core = normalize.phone_core(phone_raw or "")
    _scope_to_phone(core)                                   # route to the customer's broker
    matched, name = _match_customer(core)                   # only known customers can log in
    status = "verified" if matched else "unknown"
    db.set_setting(f"walogin:{m.group(0)}",
                   {"status": status, "phone": core, "name": name,
                    "expires": time.time() + WA_LOGIN_TTL})
    return status


@app.route("/webhook/whatsapp", methods=["GET", "POST"])
def wa_webhook():
    # GET = Meta's subscription handshake; POST = inbound messages.
    if request.method == "GET":
        if (request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token")
                == os.environ.get("WHATSAPP_VERIFY_TOKEN", "otlobly_verify")):
            return request.args.get("hub.challenge", ""), 200
        return "forbidden", 403
    # A forged POST here would log an attacker in as any customer, so the payload
    # must prove it came from Meta: X-Hub-Signature-256 = HMAC of the raw body
    # with the Meta app secret. No WHATSAPP_APP_SECRET configured = trust nobody.
    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    sig_ok = False
    if app_secret:
        want = "sha256=" + hmac.new(app_secret.encode(), request.get_data(),
                                    hashlib.sha256).hexdigest()
        sig_ok = hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), want)
    if not sig_ok:
        app.logger.warning("wa webhook POST rejected (bad or missing signature)")
        return jsonify({"ok": True})                        # 200 so Meta doesn't hammer retries
    body = request.get_json(force=True, silent=True) or {}
    try:
        for entry in body.get("entry", []):
            for ch in entry.get("changes", []):
                for msg in (ch.get("value") or {}).get("messages", []):
                    _wa_login_consume(msg.get("from"), (msg.get("text") or {}).get("body"))
    except Exception:  # noqa — never 500 a webhook (Meta retries aggressively)
        app.logger.exception("wa webhook parse failed")
    return jsonify({"ok": True})                            # always 200


@app.route("/webhook/manychat", methods=["POST"])
def manychat_webhook():
    """Inbound-message relay for when the WhatsApp number lives on ManyChat (ManyChat
    owns the Cloud API connection, so Meta's webhooks go to them, not us). A ManyChat
    keyword automation ("message contains OTL") fires an External Request here with
    the sender's phone + message text. Auth = shared secret header, set the same
    value in Render env MANYCHAT_WEBHOOK_SECRET and in the ManyChat request headers."""
    secret = os.environ.get("MANYCHAT_WEBHOOK_SECRET", "")
    got = request.headers.get("X-Otlobly-Secret", "") or request.args.get("secret", "")
    if not (secret and hmac.compare_digest(got, secret)):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    b = request.get_json(force=True, silent=True) or {}
    status = _wa_login_consume(b.get("phone"), b.get("text"))
    return jsonify({"ok": True, "status": status})


@app.route("/webhook/clickup", methods=["POST"])
def clickup_webhook():
    """ClickUp → 🏆 Goals freshness ping. The payload itself is never trusted
    or parsed for data — a valid X-Signature (HMAC of the raw body with a
    stored webhook secret) just bumps the goals stamp so every worker's cache
    refetches on the next poll. Always a fast 200: ClickUp counts non-2xx
    responses and suspends the webhook."""
    try:
        if goals.verify_signature(request.get_data(),
                                  request.headers.get("X-Signature", "")):
            goals.bump_stamp("webhook")
    except Exception:  # noqa — never fail a webhook delivery
        app.logger.exception("clickup webhook")
    return jsonify({"ok": True})


@app.route("/api/customer/wa_login/poll")
def api_wa_login_poll():
    nonce = session.get("walogin_nonce")
    if not nonce:
        return jsonify({"status": "none"})
    rec = db.get_setting(f"walogin:{nonce}") or {}
    st = rec.get("status")
    if st == "verified":
        session["cust_phone"] = rec.get("phone")
        session["cust_name"] = rec.get("name") or ""
        session["cust_business"] = _business_for_phone(rec.get("phone")) or 1
        session.pop("walogin_nonce", None)
        db.set_setting(f"walogin:{nonce}", {"status": "used", "expires": 0})   # one-time
        return jsonify({"status": "ok", "name": session["cust_name"]})
    if st == "unknown":
        session.pop("walogin_nonce", None)
        return jsonify({"status": "unknown",
                        "error": "لا توجد طلبات لهذا الرقم · No orders found for this number."})
    if not rec or time.time() > (rec.get("expires") or 0):
        return jsonify({"status": "expired"})
    return jsonify({"status": "pending"})


@app.route("/api/customer/otp/request", methods=["POST"])
@limiter.limit("5 per 10 minutes")
def api_customer_otp_request():
    b = request.get_json(force=True, silent=True) or {}
    core = normalize.phone_core(b.get("phone") or "")
    if len(core) < 9:
        return jsonify({"ok": False,
                        "error": "أدخل رقم جوالك كاملاً · Enter your full mobile number."}), 400
    _scope_to_phone(core)                 # route the lookup to the customer's broker
    matched, name = _match_customer(core)
    if not matched:
        return jsonify({"ok": False,
                        "error": "لا توجد طلبات لهذا الرقم · No orders found for this number."}), 404
    channel = notify.otp_channel()
    ok, status, msg = _otp_guard(core, channel)     # per-phone throttle + global SMS budget
    if not ok:
        return jsonify({"ok": False, "error": msg}), status
    code = f"{secrets.randbelow(1000000):06d}"
    db.set_setting(f"otp:{core}", {"hash": auth.hash_pw(code),
                                   "expires": time.time() + OTP_TTL, "attempts": 0, "name": name})
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164") or ("+" + core)
    res = notify.send_login_code(e164, code)
    if res.get("ok"):
        _otp_record_send(core, channel)             # only count an actual queued send
        return jsonify({"ok": True, "sent": channel})
    app.logger.warning("OTP for %s = %s (%s not sent: %s)", core, code,
                       channel, res.get("error"))
    if os.environ.get("OTLOBLY_OTP_DEV"):         # testing before the live WhatsApp API is set up
        return jsonify({"ok": True, "sent": "dev", "dev_code": code})
    return jsonify({"ok": False,
                    "error": "تعذّر إرسال الرمز حالياً · Couldn't send the code right now."}), 502


@app.route("/api/customer/otp/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
def api_customer_otp_verify():
    b = request.get_json(force=True, silent=True) or {}
    core = normalize.phone_core(b.get("phone") or "")
    code = re.sub(r"\D", "", b.get("code") or "")
    rec = db.get_setting(f"otp:{core}")
    if not rec or not code:
        return jsonify({"ok": False, "error": "أدخل الرمز · Enter the code."}), 400
    if time.time() > (rec.get("expires") or 0):
        return jsonify({"ok": False,
                        "error": "انتهت صلاحية الرمز · Code expired — request a new one."}), 400
    if (rec.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        return jsonify({"ok": False,
                        "error": "محاولات كثيرة · Too many attempts — request a new code."}), 429
    if not check_password_hash(rec.get("hash") or "", code):
        rec["attempts"] = (rec.get("attempts") or 0) + 1
        db.set_setting(f"otp:{core}", rec)
        return jsonify({"ok": False, "error": "رمز غير صحيح · Wrong code."}), 400
    db.set_setting(f"otp:{core}", {"used": True, "expires": 0})   # one-time
    bid = _business_for_phone(core) or 1
    db.set_current_business(bid)                    # scope the name fallback to the right tenant
    session["cust_phone"] = core
    session["cust_business"] = bid
    session["cust_name"] = rec.get("name") or _match_customer(core)[1]
    session.permanent = True
    return jsonify({"ok": True, "name": session["cust_name"]})


# ---- Magic-link tokens (shared by WhatsApp verify-button + email link) -------- #
# One namespace, two delivery channels: a high-entropy single-use token rides in a
# URL (?vt=) — the WhatsApp utility template's "Verify account" dynamic-URL button,
# or the "Sign in" button in the login-code email. Same KV discipline as walogin:/
# otp:/emailotp: — hashed key (raw tokens must not show in admin settings tooling),
# TTL, single-use. The verify is a POST so mail/WA link-scanner GET prefetches
# can't burn the token.

def _token_kv_key(token):
    return "logintoken:" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _mint_login_token(core, name):
    """Create a one-time login token for a known customer; returns the raw token."""
    token = secrets.token_urlsafe(24)
    db.set_setting(_token_kv_key(token),
                   {"core": core, "name": name or "", "expires": time.time() + OTP_TTL})
    return token


def _portal_base():
    """Absolute base for customer-facing links (login magic links, /order quote
    links): env override for prod, else this request's root (keeps local-dev
    links local)."""
    return (os.environ.get("PORTAL_BASE_URL") or request.url_root).rstrip("/")


@app.route("/api/customer/magic/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
def api_customer_magic_verify():
    b = request.get_json(force=True, silent=True) or {}
    token = (b.get("token") or "").strip()
    rec = db.get_setting(_token_kv_key(token)) if token else None
    if not rec or rec.get("used") or time.time() > (rec.get("expires") or 0):
        # one generic answer for missing/used/expired — nothing to enumerate
        return jsonify({"ok": False,
                        "error": "انتهت صلاحية الرابط — اطلب رابطاً جديداً · "
                                 "Link expired — request a new one."}), 400
    db.set_setting(_token_kv_key(token), {"used": True, "expires": 0})   # one-time
    bid = _business_for_phone(rec["core"]) or 1
    db.set_current_business(bid)                    # scope to the customer's broker
    session["cust_phone"] = rec["core"]
    session["cust_business"] = bid
    session["cust_name"] = rec.get("name") or _match_customer(rec["core"])[1]
    session.permanent = True
    return jsonify({"ok": True, "name": session["cust_name"]})


@app.route("/api/customer/wa_verify/start", methods=["POST"])
@limiter.limit("5 per 10 minutes")
def api_customer_wa_verify_start():
    """Send the WhatsApp utility template whose "Verify account" button carries a
    one-time login link. Anti-enumeration: ALWAYS {"ok":true} once the number parses —
    a message is only actually sent when the phone belongs to a known customer."""
    b = request.get_json(force=True, silent=True) or {}
    core = normalize.phone_core(b.get("phone") or "")
    if len(core) < 9:
        return jsonify({"ok": False,
                        "error": "أدخل رقم جوالك كاملاً · Enter your full mobile number."}), 400
    _scope_to_phone(core)                 # route to the customer's broker
    matched, name = _match_customer(core)
    out = {"ok": True}
    if matched:
        token = _mint_login_token(core, name)
        pin = normalize.normalize_phone(b.get("phone") or "")
        e164 = (pin or {}).get("e164") or ("+" + core)
        res = notify.send_account_verify(e164, name, token)
        if not res.get("ok"):
            app.logger.warning("wa_verify for %s not sent: %s", core, res.get("error"))
            if os.environ.get("OTLOBLY_OTP_DEV"):   # local testing without WhatsApp creds
                out["dev_link"] = f"/account?vt={token}"
            else:
                return jsonify({"ok": False,
                                "error": "تعذّر الإرسال حالياً · Couldn't send right now."}), 502
    return jsonify(out)


# ---- Customer email login (second method — WhatsApp stays) ------------------- #
# Bootstrap: no emails exist, so a customer first logs in via WhatsApp, then links
# + verifies an email from the dashboard; after that email+code login works too.
# Same settings-KV nonce discipline as walogin:/otp: — hashed codes, TTL, attempt
# cap, single-use — plus a per-business unique email and anti-enumeration replies.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
EMAIL_GENERIC_FAIL = "تعذّر استخدام هذا البريد · This email can't be used."


def _customer_row_for_core(core):
    """The CRM row whose WhatsApp matches this phone core (every portal customer
    has one — _backfill_customers_from_orders runs at boot)."""
    if not core:
        return None
    for r in db.list_customer_login_rows():
        if normalize.phone_core(r.get("whatsapp") or "") == core:
            return r
    return None


def _mask_email(e):
    local, _, dom = (e or "").partition("@")
    return (local[:1] + "***@" + dom) if dom else "***"


def _email_kv_key(email):
    # keyed by hash, not raw address — settings keys show up in admin tooling
    return "emailotp:" + hashlib.sha256(email.encode()).hexdigest()[:16]


def _send_or_dev(email, code, link=None):
    """Send the code (plus a sign-in link when given); in OTLOBLY_EMAIL_DEV echo it
    instead of failing when the email API isn't configured (mirrors the OTP dev
    fallback)."""
    res = mailer.send_login_code(email, code, link)
    if res.get("ok"):
        return {"ok": True}
    app.logger.warning("email code for %s = %s (not sent: %s)", email, code, res.get("error"))
    if os.environ.get("OTLOBLY_EMAIL_DEV"):
        return {"ok": True, "dev_code": code}
    return {"ok": False}


@app.route("/api/customer/email/link/start", methods=["POST"])
@limiter.limit("5 per 10 minutes")
@customer_required
def api_customer_email_link_start():
    b = request.get_json(force=True, silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "أدخل بريداً صالحاً · Enter a valid email."}), 400
    mine = _customer_row_for_core(session["cust_phone"])
    if not mine:
        return jsonify({"ok": False, "error": EMAIL_GENERIC_FAIL}), 400
    owner = db.get_customer_by_email(email)
    if owner and owner["id"] != mine["id"]:
        # someone else's email — same wording as any other failure (no enumeration)
        return jsonify({"ok": False, "error": EMAIL_GENERIC_FAIL}), 400
    code = f"{secrets.randbelow(1000000):06d}"
    db.set_setting(f"emaillink:{session['cust_phone']}",
                   {"email": email, "hash": auth.hash_pw(code),
                    "expires": time.time() + OTP_TTL, "attempts": 0})
    sent = _send_or_dev(email, code)
    if not sent.get("ok"):
        return jsonify({"ok": False,
                        "error": "تعذّر إرسال الرمز حالياً · Couldn't send the code right now."}), 502
    out = {"ok": True}
    if sent.get("dev_code"):
        out["dev_code"] = sent["dev_code"]
    return jsonify(out)


@app.route("/api/customer/email/link/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
@customer_required
def api_customer_email_link_verify():
    b = request.get_json(force=True, silent=True) or {}
    code = re.sub(r"\D", "", b.get("code") or "")
    key = f"emaillink:{session['cust_phone']}"
    rec = db.get_setting(key)
    if not rec or not code or rec.get("used"):
        return jsonify({"ok": False, "error": "اطلب رمزاً جديداً · Request a new code."}), 400
    if time.time() > (rec.get("expires") or 0):
        return jsonify({"ok": False, "error": "انتهت صلاحية الرمز · Code expired."}), 400
    if (rec.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        return jsonify({"ok": False,
                        "error": "محاولات كثيرة · Too many attempts — request a new code."}), 429
    if not check_password_hash(rec.get("hash") or "", code):
        rec["attempts"] = (rec.get("attempts") or 0) + 1
        db.set_setting(key, rec)
        return jsonify({"ok": False, "error": "رمز غير صحيح · Wrong code."}), 400
    mine = _customer_row_for_core(session["cust_phone"])
    if not mine or not db.set_customer_email(mine["id"], rec["email"], db.now_iso()):
        return jsonify({"ok": False, "error": EMAIL_GENERIC_FAIL}), 400
    db.set_setting(key, {"used": True, "expires": 0})   # one-time
    db.audit({"username": "customer"}, "link_email", "customer",
             mine.get("customer_code") or str(mine["id"]), _mask_email(rec["email"]))
    return jsonify({"ok": True, "email_masked": _mask_email(rec["email"])})


@app.route("/api/customer/email/login/start", methods=["POST"])
@limiter.limit("5 per 10 minutes")
def api_customer_email_login_start():
    """Anti-enumeration: ALWAYS {"ok":true} — a code is only actually created and
    sent when the email belongs to a verified customer."""
    b = request.get_json(force=True, silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "أدخل بريداً صالحاً · Enter a valid email."}), 400
    ebid = db.find_business_for_email(email)          # route to the customer's broker
    if ebid:
        db.set_current_business(ebid)
    row = db.get_customer_by_email(email)
    out = {"ok": True}
    if row and row.get("email_verified_at"):
        core = normalize.phone_core(row.get("whatsapp") or "")
        if core:
            code = f"{secrets.randbelow(1000000):06d}"
            db.set_setting(_email_kv_key(email),
                           {"hash": auth.hash_pw(code), "expires": time.time() + OTP_TTL,
                            "attempts": 0, "core": core, "name": row.get("name") or ""})
            # same email carries a one-tap magic link next to the code (one email, two ways in)
            token = _mint_login_token(core, row.get("name") or "")
            sent = _send_or_dev(email, code, f"{_portal_base()}/account?vt={token}")
            if sent.get("dev_code"):
                out["dev_code"] = sent["dev_code"]
                out["dev_link"] = f"/account?vt={token}"
    return jsonify(out)


@app.route("/api/customer/email/login/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
def api_customer_email_login_verify():
    b = request.get_json(force=True, silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    code = re.sub(r"\D", "", b.get("code") or "")
    rec = db.get_setting(_email_kv_key(email)) if EMAIL_RE.match(email) else None
    if not rec or not code or rec.get("used"):
        return jsonify({"ok": False, "error": "اطلب رمزاً جديداً · Request a new code."}), 400
    if time.time() > (rec.get("expires") or 0):
        return jsonify({"ok": False, "error": "انتهت صلاحية الرمز · Code expired."}), 400
    if (rec.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        return jsonify({"ok": False,
                        "error": "محاولات كثيرة · Too many attempts — request a new code."}), 429
    if not check_password_hash(rec.get("hash") or "", code):
        rec["attempts"] = (rec.get("attempts") or 0) + 1
        db.set_setting(_email_kv_key(email), rec)
        return jsonify({"ok": False, "error": "رمز غير صحيح · Wrong code."}), 400
    db.set_setting(_email_kv_key(email), {"used": True, "expires": 0})   # one-time
    bid = _business_for_phone(rec["core"]) or 1
    db.set_current_business(bid)                    # scope to the customer's broker
    session["cust_phone"] = rec["core"]
    session["cust_business"] = bid
    session["cust_name"] = rec.get("name") or _match_customer(rec["core"])[1]
    session.permanent = True
    return jsonify({"ok": True, "name": session["cust_name"]})


# ---- Sign in with Google (OAuth) — email login where Google does the verifying --- #
# Same eligibility as the email-code login: the Google email must match a customer
# whose email is linked & verified and whose record has a phone (orders key on phone).
def _google_redirect_uri():
    return f"{_portal_base()}/api/customer/google/callback"


@app.route("/api/customer/google/start")
@limiter.limit("10 per minute")
def api_customer_google_start():
    if not google_login.configured():
        return redirect("/account")
    state = google_login.new_state()
    session["g_state"] = state
    # Remember-me travels the OAuth round-trip via the session (default: on).
    session["g_remember"] = (request.args.get("remember") or "1") != "0"
    return redirect(google_login.auth_url(_google_redirect_uri(), state))


@app.route("/api/customer/google/callback")
@limiter.limit("10 per minute")
def api_customer_google_callback():
    # CSRF check: the state must round-trip through THIS browser session.
    state = request.args.get("state") or ""
    if not state or state != session.pop("g_state", None):
        return redirect("/account?gerr=state")
    code = request.args.get("code")
    if not code:                                    # user cancelled on Google
        return redirect("/account")
    res = google_login.exchange_code(code, _google_redirect_uri())
    if not res.get("ok") or not res.get("email_verified"):
        app.logger.warning("google login failed: %s", res.get("error") or "unverified email")
        return redirect("/account?gerr=fail")
    email = res["email"]
    remember = bool(session.pop("g_remember", True))
    ebid = db.find_business_for_email(email)        # cross-tenant → the customer's broker
    if ebid:
        db.set_current_business(ebid)
    row = db.get_customer_by_email(email)
    core = normalize.phone_core((row or {}).get("whatsapp") or "")
    if not row or not row.get("email_verified_at") or not core:
        # Not the row's PRIMARY email — check the login-identity map: any address
        # that once proved itself alongside a verified phone signs in forever after.
        # (A customer can hold several Gmail accounts; `customers.email` holds one.)
        alt = db.get_setting(_email_identity_key(email)) or {}
        acore, abid = alt.get("core") or "", alt.get("bid") or 1
        if acore:
            db.set_current_business(abid)
            arow = _customer_row_for_core(acore)
            if arow:
                row, core, ebid = arow, acore, abid
    if not row or not core:
        # Auth v2 — Google is the FRONT DOOR for everyone: a proven-owned Google
        # email with no linked customer gets an email-only session and lands on
        # the "verify your phone" step (linkable, or skippable with the
        # you-won't-see-your-orders disclaimer).
        session["cust_email"] = email
        session["cust_name"] = (res.get("name") or "").strip()
        session["cust_business"] = ebid or 1
        session.permanent = remember
        # g=1 marks a SUCCESSFUL Google sign-in so /account can tag it "last used"
        return redirect("/account?link=phone&g=1")
    bid = _business_for_phone(core) or ebid or 1
    db.set_current_business(bid)
    session["cust_phone"] = core
    session["cust_email"] = email
    session["cust_business"] = bid
    session["cust_name"] = (row.get("name") or "").strip() or _match_customer(core)[1]
    session.permanent = remember
    return redirect("/account?g=1")             # g=1 → tag Google as "last used"


# ---- Auth v2: manual accounts + the shared "verify your phone" step ------------- #
# SECURITY INVARIANT: no customer row is created or modified from an UNVERIFIED
# phone claim. A manual signup parks in KV (pendsignup:) until the SMS code proves
# the phone; only then is the row attached/created and the password applied.
PW_MIN = 8
SIGNUP_TTL = 1800                     # pending signup valid for 30 minutes


def _email_identity_key(email):
    """KV key for the login-identity map: a proven email → the customer's phone core.
    Written only when BOTH were proven in one flow (Google verified the address, SMS
    verified the phone), so it is exactly as trustworthy as the login itself. Lets a
    customer keep several sign-in emails without clobbering `customers.email`."""
    return "custemail:" + hashlib.sha256((email or "").strip().lower().encode()).hexdigest()[:16]


@app.route("/api/customer/signup", methods=["POST"])
@limiter.limit("6 per minute")
def api_customer_signup():
    b = request.get_json(force=True, silent=True) or {}
    name = (b.get("name") or "").strip()
    core = normalize.phone_core(b.get("phone") or "")
    email = (b.get("email") or "").strip().lower()
    pw, pw2 = b.get("password") or "", b.get("confirm") or ""
    if not name:
        return jsonify({"ok": False, "error": "أدخل اسمك الكامل · Enter your full name."}), 400
    if len(core) < 9:
        return jsonify({"ok": False,
                        "error": "أدخل رقم جوالك كاملاً · Enter your full mobile number."}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "بريد غير صالح · Enter a valid email."}), 400
    if len(pw) < PW_MIN:
        return jsonify({"ok": False,
                        "error": f"كلمة المرور ٨ أحرف على الأقل · Password must be at least {PW_MIN} characters."}), 400
    if pw != pw2:
        return jsonify({"ok": False,
                        "error": "كلمتا المرور غير متطابقتين · Passwords don't match."}), 400
    # Park the signup — NO customer row yet (see invariant above). Generic success:
    # never reveals whether the phone/email already exists.
    db.set_setting(f"pendsignup:{core}", {"name": name, "email": email,
                                          "pw_hash": auth.hash_pw(pw),
                                          "expires": time.time() + SIGNUP_TTL})
    session["cust_pending"] = core
    session["cust_name"] = name
    session.permanent = bool(b.get("remember", True))
    return jsonify({"ok": True, "next": "verify_phone", "phone": core})


@app.route("/api/customer/pwlogin", methods=["POST"])
@limiter.limit("6 per minute")
def api_customer_pwlogin():
    """Password login for manual accounts — identifier is a phone OR an email."""
    b = request.get_json(force=True, silent=True) or {}
    ident = (b.get("identifier") or "").strip()
    pw = b.get("password") or ""
    FAIL = (jsonify({"ok": False,
                     "error": "بيانات الدخول غير صحيحة · Wrong credentials."}), 400)
    if not ident or not pw:
        return FAIL
    row, core = None, ""
    if "@" in ident:
        if EMAIL_RE.match(ident.lower()):
            ebid = db.find_business_for_email(ident.lower())
            if ebid:
                db.set_current_business(ebid)
            row = db.get_customer_by_email(ident.lower())
            core = normalize.phone_core((row or {}).get("whatsapp") or "")
    else:
        core = normalize.phone_core(ident)
        if len(core) >= 9:
            _scope_to_phone(core)
            row = _customer_row_for_core(core)
    if not row or not core:
        return FAIL
    pw_hash = db.get_customer_password_hash(row["id"])
    if not pw_hash or not check_password_hash(pw_hash, pw):
        return FAIL
    bid = _business_for_phone(core) or db.current_business() or 1
    db.set_current_business(bid)
    session["cust_phone"] = core
    session["cust_business"] = bid
    session["cust_name"] = (row.get("name") or "").strip() or _match_customer(core)[1]
    session.permanent = bool(b.get("remember", True))
    return jsonify({"ok": True, "name": session["cust_name"]})


@app.route("/api/customer/phone/link/start", methods=["POST"])
@limiter.limit("5 per 10 minutes")
@customer_session_required
def api_customer_phone_link_start():
    """Send the verify-your-phone SMS for an onboarding session (Google email-only
    or pending manual signup). Full _otp_guard cost protection; `known` in the
    response drives the "no orders linked to this number" note."""
    b = request.get_json(force=True, silent=True) or {}
    core = normalize.phone_core(b.get("phone") or "")
    if len(core) < 9:
        return jsonify({"ok": False,
                        "error": "أدخل رقم جوالك كاملاً · Enter your full mobile number."}), 400
    channel = notify.otp_channel()
    ok, status, msg = _otp_guard(core, channel)
    if not ok:
        return jsonify({"ok": False, "error": msg}), status
    _scope_to_phone(core)
    known = _match_customer(core)[0] or bool(_customer_row_for_core(core))
    code = f"{secrets.randbelow(1000000):06d}"
    db.set_setting(f"phonelink:{core}", {"hash": auth.hash_pw(code),
                                         "expires": time.time() + OTP_TTL, "attempts": 0})
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164") or ("+" + core)
    res = notify.send_login_code(e164, code)
    if res.get("ok"):
        _otp_record_send(core, channel)
        return jsonify({"ok": True, "sent": channel, "to": e164, "known": known})
    app.logger.warning("phone-link code for %s = %s (%s not sent: %s)", core, code,
                       channel, res.get("error"))
    if os.environ.get("OTLOBLY_OTP_DEV"):
        return jsonify({"ok": True, "sent": "dev", "dev_code": code,
                        "to": e164, "known": known})
    return jsonify({"ok": False,
                    "error": "تعذّر إرسال الرمز حالياً · Couldn't send the code right now."}), 502


@app.route("/api/customer/phone/link/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
@customer_session_required
def api_customer_phone_link_verify():
    b = request.get_json(force=True, silent=True) or {}
    core = normalize.phone_core(b.get("phone") or "")
    code = re.sub(r"\D", "", b.get("code") or "")
    rec = db.get_setting(f"phonelink:{core}") if core else None
    if not rec or not code or rec.get("used"):
        return jsonify({"ok": False, "error": "اطلب رمزاً جديداً · Request a new code."}), 400
    if time.time() > (rec.get("expires") or 0):
        return jsonify({"ok": False, "error": "انتهت صلاحية الرمز · Code expired."}), 400
    if (rec.get("attempts") or 0) >= OTP_MAX_ATTEMPTS:
        return jsonify({"ok": False,
                        "error": "محاولات كثيرة · Too many attempts — request a new code."}), 429
    if not check_password_hash(rec.get("hash") or "", code):
        rec["attempts"] = (rec.get("attempts") or 0) + 1
        db.set_setting(f"phonelink:{core}", rec)
        return jsonify({"ok": False, "error": "رمز غير صحيح · Wrong code."}), 400
    db.set_setting(f"phonelink:{core}", {"used": True, "expires": 0})   # one-time
    # Phone OWNERSHIP is now proven — safe to attach/create the customer row.
    bid = _business_for_phone(core) or session.get("cust_business") or 1
    db.set_current_business(bid)
    row = _customer_row_for_core(core)
    known = bool(row) or _match_customer(core)[0]
    # proper E.164 (+970…) — NOT "+"+core, which drops the country code and stores a
    # number staff/WhatsApp/GAASH can't use.
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164") or ("+" + core)
    if not row:
        db.upsert_customer({"customer_id": db.next_customer_code(), "match_key": core,
                            "whatsapp": e164,
                            "name": session.get("cust_name") or ""})
        row = _customer_row_for_core(core)
    elif row.get("whatsapp") and len(re.sub(r"\D", "", row["whatsapp"])) < len(re.sub(r"\D", "", e164)):
        # self-heal a row stored without its country code (same core, shorter digits)
        db.set_customer_whatsapp(row["id"], e164)
        row = _customer_row_for_core(core) or row
    if row and session.get("cust_email"):
        # Remember this address as a sign-in identity for the phone — so the NEXT
        # login (any device) goes straight in even if it isn't the row's primary email.
        db.set_setting(_email_identity_key(session["cust_email"]),
                       {"core": core, "bid": bid})
        # Link it as the primary email too — but NEVER clobber a different verified one.
        cur = (row.get("email") or "").strip().lower()
        if cur and row.get("email_verified_at") and cur != session["cust_email"]:
            app.logger.warning("phone-link: row %s keeps existing verified email", row["id"])
        elif not db.set_customer_email(row["id"], session["cust_email"], db.now_iso()):
            app.logger.warning("phone-link: email %s already owned elsewhere — left as-is",
                               _mask_email(session["cust_email"]))
    pend_key = session.get("cust_pending")
    if row and pend_key:
        pend = db.get_setting(f"pendsignup:{pend_key}") or {}
        if pend.get("pw_hash") and time.time() <= (pend.get("expires") or 0):
            db.set_customer_password(row["id"], pend["pw_hash"])
            if pend.get("email") and not (row.get("email") or "").strip():
                db.set_customer_email(row["id"], pend["email"], None)   # stored, unverified
        db.set_setting(f"pendsignup:{pend_key}", {"used": True, "expires": 0})
        session.pop("cust_pending", None)
    session["cust_phone"] = core
    session["cust_business"] = bid
    session["cust_name"] = ((row or {}).get("name") or "").strip() \
        or session.get("cust_name") or _match_customer(core)[1]
    return jsonify({"ok": True, "name": session["cust_name"], "known": known})


@app.route("/api/customer/password/change", methods=["POST"])
@limiter.limit("6 per 10 minutes")
@customer_required
def api_customer_password_change():
    """Change the portal password from Settings. Only for accounts that HAVE one
    (manual signups); the current password must be given, so a borrowed session
    can't silently take the account over."""
    b = request.get_json(force=True, silent=True) or {}
    row = _customer_row_for_core(session["cust_phone"])
    cur_hash = db.get_customer_password_hash(row["id"]) if row else ""
    if not row or not cur_hash:
        return jsonify({"ok": False,
                        "error": "لا توجد كلمة مرور لهذا الحساب · This account has no password."}), 400
    if not check_password_hash(cur_hash, b.get("current") or ""):
        return jsonify({"ok": False,
                        "error": "كلمة المرور الحالية غير صحيحة · Current password is wrong."}), 400
    new = b.get("new") or ""
    if len(new) < PW_MIN:
        return jsonify({"ok": False,
                        "error": f"كلمة المرور ٨ أحرف على الأقل · Password must be at least {PW_MIN} characters."}), 400
    if new != (b.get("confirm") or ""):
        return jsonify({"ok": False,
                        "error": "كلمتا المرور غير متطابقتين · Passwords don't match."}), 400
    db.set_customer_password(row["id"], auth.hash_pw(new))
    return jsonify({"ok": True})


@app.route("/api/admin/whatsapp_test", methods=["GET", "POST"])
@auth.require("admin_actions")
def api_admin_whatsapp_test():
    """Admin diagnostic for the customer-login WhatsApp OTP. GET reports whether the
    WHATSAPP_* env vars are set; POST sends a REAL test code to a phone and surfaces
    Meta's exact error — so the setup can be validated before customers rely on it.
    The test code is a delivery test only; it is NOT stored as a login OTP."""
    if request.method == "GET":
        return jsonify({"configured": notify.configured()})
    b = request.get_json(force=True, silent=True) or {}
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164")
    if not e164:
        return jsonify({"ok": False, "error": "Enter a valid phone number (e.g. 970599…)."}), 400
    if not notify.configured():
        return jsonify({"ok": False, "configured": False,
                        "error": "WhatsApp not configured yet — set WHATSAPP_TOKEN + "
                                 "WHATSAPP_PHONE_NUMBER_ID in Render, then redeploy."})
    code = f"{secrets.randbelow(1000000):06d}"
    res = notify.send_whatsapp_otp(e164, code)
    if res.get("ok"):
        return jsonify({"ok": True, "sent_to": e164, "message_id": res.get("id")})
    return jsonify({"ok": False, "configured": True, "error": res.get("error") or "send failed"})


@app.route("/api/admin/email_test", methods=["GET", "POST"])
@auth.require("admin_actions")
def api_admin_email_test():
    """Admin diagnostic for the customer-login email (Resend), mirroring the WhatsApp
    test above. GET reports whether RESEND_API_KEY is set; POST sends a REAL test code
    to an address and surfaces Resend's exact error — so the setup can be validated
    before customers rely on it. The test code is a delivery test only, never a login."""
    if request.method == "GET":
        return jsonify({"configured": mailer.configured()})
    b = request.get_json(force=True, silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Enter a valid email address."}), 400
    if not mailer.configured():
        return jsonify({"ok": False, "configured": False,
                        "error": "Email not configured yet — set RESEND_API_KEY (+ EMAIL_FROM) "
                                 "in Render, then redeploy."})
    res = mailer.send_login_code(email, f"{secrets.randbelow(1000000):06d}")
    if res.get("ok"):
        return jsonify({"ok": True, "sent_to": email, "message_id": res.get("id")})
    return jsonify({"ok": False, "configured": True, "error": res.get("error") or "send failed"})


@app.route("/api/admin/sms_test", methods=["GET", "POST"])
@auth.require("admin_actions")
def api_admin_sms_test():
    """Admin diagnostic for the customer-login SMS OTP (Twilio), mirroring the
    WhatsApp/email tests. GET reports whether the Twilio env vars are set; POST
    sends a REAL test code to a phone and surfaces Twilio's exact error — so the
    setup AND deliverability to Jawwal/+970 can be validated before customers rely
    on it. The test code is a delivery test only, never stored as a login OTP."""
    if request.method == "GET":
        return jsonify({"configured": notify.sms_configured()})
    b = request.get_json(force=True, silent=True) or {}
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164")
    if not e164:
        return jsonify({"ok": False, "error": "Enter a valid phone number (e.g. 970599…)."}), 400
    if not notify.sms_configured():
        return jsonify({"ok": False, "configured": False,
                        "error": "SMS not configured yet — set TWILIO_ACCOUNT_SID + "
                                 "TWILIO_AUTH_TOKEN + TWILIO_SMS_FROM in Render, then redeploy."})
    code = f"{secrets.randbelow(1000000):06d}"
    res = notify.send_sms_otp(e164, code)
    if res.get("ok"):
        return jsonify({"ok": True, "sent_to": e164, "message_id": res.get("id"),
                        "status": res.get("status")})
    return jsonify({"ok": False, "configured": True, "error": res.get("error") or "send failed"})


@app.route("/api/customer/logout", methods=["POST"])
def api_customer_logout():
    for k in ("cust_phone", "cust_name", "cust_business",
              "cust_email", "cust_pending", "g_remember"):
        session.pop(k, None)
    return jsonify({"ok": True})


@app.route("/api/customer/orders")
@customer_session_required
def api_customer_orders():
    if not session.get("cust_phone"):
        # Phone-less onboarding session (Google skip / pending signup): an empty
        # shell so the dashboard renders with the "phone not connected" banner.
        return jsonify({"orders": [], "shipments": [], "payments": [],
                        "deposited": 0, "remaining": 0, "credit": 0,
                        "name": session.get("cust_name") or "", "phone_linked": False})
    core = session["cust_phone"]
    pdb = purchases.load()
    pairs, names, oids = _phone_pairs(core, pdb)
    shipments = _shipments_for(pairs, names, oids)
    orders = _customer_orders(core)
    # IN_CART included: a staged order still owes its balance — without it the
    # customer's "remaining" total would dip while the order sits in the cart.
    OPEN = {"REQUESTED", "QUOTED", "IN_CART", "ORDERED", "SHIPPED", "ARRIVED", "DELIVERED"}
    deposited = money.usd_sum(o["deposit_usd"] or 0 for o in orders)
    remaining = money.usd_sum(o["remaining_usd"] or 0 for o in orders
                              if o["status"] in OPEN and o["remaining_usd"] is not None)
    # Their money trail: full ledger rows (customer-safe fields ONLY — notes and
    # staff names stay internal) + standalone credit = net of payments not attached
    # to any order (deposits/collects − refunds). Order-attached money already shows
    # per order; credit>0 means "money on file with no open order to absorb it".
    pay_rows = db.list_payments(customer_phone=core)
    credit = money.usd_sum((-(r["amount_usd"] or 0) if r["kind"] == "refund" else (r["amount_usd"] or 0))
                           for r in pay_rows if not r.get("order_code"))
    payments = [{"paid_at": r.get("paid_at") or r.get("ts"), "kind": r.get("kind"),
                 "currency": r.get("currency"), "amount_entered": r.get("amount_entered"),
                 "amount_usd": r.get("amount_usd"), "order_code": r.get("order_code")}
                for r in pay_rows[:50]]
    return jsonify({"name": session.get("cust_name") or "",
                    # the session's own phone (core digits) — lets the orders search
                    # recognise "my number" without a second /me round-trip
                    "phone": core,
                    "orders": orders, "totals": {"deposited_usd": deposited,
                                                 "remaining_usd": remaining,
                                                 "credit_usd": credit},
                    "payments": payments,
                    "shipments": shipments, "count": len(shipments)})


def _pkg_for_order(pdb, order_id):
    """(po, pk) for the package carrying this order's items, or (None, None).
    A package can hold several customers' items; we match on customer_order_id."""
    oid = (order_id or "").strip().upper()
    for po in pdb.get("purchase_orders", []):
        for pk in po.get("packages", []):
            if any((it.get("customer_order_id") or "").strip().upper() == oid
                   for it in pk.get("items", [])):
                return po, pk
    return None, None


def _fmt_delivery(iso):
    """A friendly delivery date for the customer message; passthrough if unparseable."""
    try:
        return datetime.strptime((iso or "")[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:  # noqa: BLE001
        return (iso or "").strip()


@app.route("/api/customer_notify/track", methods=["POST"])
@auth.require("edit_fulfillment")
@limiter.limit("30 per 10 minutes")
def api_customer_notify_track():
    """Staff: send the customer the 'your package is on the way' WhatsApp (track_package
    template) with their delivery date + a track link. Manual, per order — anti-spam
    'already notified' guard (override with force). Dev fallback previews without sending."""
    b = request.get_json(force=True, silent=True) or {}
    order = db.get_order((b.get("order_id") or "").strip())
    if not order:
        return jsonify({"ok": False, "error": "Order not found."}), 404
    oid = order.get("order_id")
    cust = order.get("customer") or {}
    name = (cust.get("name") or "").strip()
    e164 = None
    for ph in (cust.get("phones") or []):
        pin = normalize.normalize_phone(ph.get("e164") or ph.get("raw") or "")
        if pin and pin.get("e164"):
            e164 = pin["e164"]
            break
    if not e164:
        return jsonify({"ok": False, "error": "No valid phone on this order."}), 400
    pdb = purchases.load()
    po, pk = _pkg_for_order(pdb, oid)
    if not pk:
        return jsonify({"ok": False, "error": "This order isn't in a shipped package yet."}), 400
    # The order's stamped ETA, else straight from the package's Arrives date + buffer —
    # so Notify works the moment the date is typed, before reconcile stamps the order.
    eta_iso = order.get("est_delivery_customer")
    if not eta_iso and (pk.get("arrival") or "").strip():
        buf = cfg.get(cfg.load(), "pipeline.delivery_buffer_days",
                      settings_mod.DELIVERY_BUFFER_DAYS)
        eta_iso = purchases._arrival_plus(pk["arrival"].strip(), buf)
    eta = _fmt_delivery(eta_iso)
    if not eta:
        return jsonify({"ok": False, "error": "Set the package's Arrives date first."}), 400
    if not (pk.get("customer_tracking") or "").strip():
        pk["customer_tracking"] = purchases.gen_customer_tracking(pdb)
        purchases.save(pdb)
    otl = pk["customer_tracking"]
    # Amounts the customer sees (only rendered when the 5-var template is live, but always
    # computed so the dev preview shows them): total = quoted price, due = total − عربون.
    amt = order.get("amount_to_collect_usd")
    dep = round(order.get("deposit_usd") or 0, 2)
    total_txt = f"${amt:.2f}" if amt is not None else "—"
    due_txt = f"${round(amt - dep, 2):.2f}" if amt is not None else "—"
    seen = pk.get("track_notified") or {}          # {order_id: iso} — one send per order
    if seen.get(oid) and not b.get("force"):
        return jsonify({"ok": False, "already": True, "notified_at": seen[oid],
                        "error": "Already notified — resend?"}), 409
    res = notify.send_track_package(e164, name, otl, eta, otl=otl, total=total_txt, due=due_txt)
    dev = (not res.get("ok")) and bool(os.environ.get("OTLOBLY_OTP_DEV"))  # local preview, no WhatsApp creds
    if res.get("ok") or dev:
        seen[oid] = db.now_iso()                   # stamp so the guard works (dev branch never runs in prod)
        pk["track_notified"] = seen
        purchases.save(pdb)
        if res.get("ok"):
            activity.log("notify", "order", oid, oid,
                         detail=f"track_package → {e164} · ETA {eta} · {otl} · due {due_txt}",
                         user=_user())
            return jsonify({"ok": True, "sent_to": e164, "otl": otl, "eta": eta,
                            "total": total_txt, "due": due_txt})
        return jsonify({"ok": True, "dev": True,
                        "preview": {"to": e164, "name": name, "order_ref": otl,
                                    "delivery_date": eta, "otl": otl,
                                    "total": total_txt, "due": due_txt}})
    app.logger.warning("track_package for %s not sent: %s", oid, res.get("error"))
    return jsonify({"ok": False, "error": res.get("error") or "send failed"}), 502


@app.route("/api/customer_tracking/generate", methods=["POST"])
@auth.require("edit_fulfillment")
def api_gen_customer_tracking():
    """Staff: mint an OTL number for a package early (before its GAASH # is added)."""
    b = request.get_json(force=True, silent=True) or {}
    pdb = purchases.load()
    po = purchases.find(pdb, b.get("po_id"))
    pk = next((p for p in (po or {}).get("packages", [])
               if p.get("package_no") == b.get("package_no")), None) if po else None
    if not pk:
        return jsonify({"error": "package not found"}), 404
    if not (pk.get("customer_tracking") or "").strip():
        pk["customer_tracking"] = purchases.gen_customer_tracking(pdb)
        purchases.save(pdb)
    return jsonify({"ok": True, "customer_tracking": pk["customer_tracking"]})


@app.route("/api/pkgprep")
@auth.require("view_orders")
def api_pkgprep():
    """Package prep: customers whose pieces are received (استلمتها اطلبلي) —
    ready-to-ship vs still-missing — with prefilled WhatsApp status messages.
    Plus a 'reviews' cohort: collected (delivered+paid) customers to ask for a
    Facebook review, minus anyone already contacted (review_asked)."""
    import pkgprep
    import review_asked
    c = cfg.load()
    try:
        rate = float(cfg.get(c, "fx.pkg_ils_per_usd", 3.1)) or 3.1
    except (TypeError, ValueError):
        rate = 3.1
    try:
        coupon = int(cfg.get(c, "reviews.coupon_usd", 5) or 5)
    except (TypeError, ValueError):
        coupon = 5
    return jsonify(pkgprep.build(
        db.list_orders(), purchases.load(), rate,
        include_money=current_user.has("view_money"),
        asked=review_asked.keys(),
        facebook_url=(cfg.get(c, "business.facebook_reviews_url", "") or "").strip(),
        coupon_usd=coupon))


@app.route("/api/pkgprep/review-asked", methods=["POST"])
@auth.require("edit_fulfillment")
def api_pkgprep_review_asked():
    """Mark a collected customer as already asked for a review (drops them off the
    Package-prep review list), or undo it with {asked:false}. Keyed by the same
    person-key pkgprep emits (primary phone e164, else 'name:<name>')."""
    import review_asked
    b = request.get_json(force=True, silent=True) or {}
    key = (b.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    asked = b.get("asked", True)
    if asked:
        review_asked.mark(key)
    else:
        review_asked.unmark(key)
    db.audit(auth.actor(), "review_asked", "customer", key,
             "asked for review" if asked else "undo review-asked")
    return jsonify({"ok": True, "asked": bool(asked)})


@app.route("/api/pkgprep/status", methods=["POST"])
@auth.require("edit_fulfillment")
def api_pkgprep_status():
    """Set a PO package's owner Otlobly status from Package prep (recieved rd/no
    rd, sent rd/no rd, complete, …). It's a PACKAGE-level field, so if the same
    package also holds other customers' items their cards change too."""
    import purchases
    b = request.get_json(force=True, silent=True) or {}
    status = (b.get("otlobly_status") or "").strip()
    pdb = purchases.load()
    po = purchases.find(pdb, b.get("po_id"))
    pno = b.get("package_no")
    pk = next((p for p in (po or {}).get("packages", [])
               if str(p.get("package_no")) == str(pno)), None) if po else None
    if not pk:
        return jsonify({"ok": False, "error": "package not found"}), 404
    old = pk.get("otlobly_status")
    pk["otlobly_status"] = status or None
    po["updated_at"] = purchases.now_iso()
    purchases.save(pdb)
    db.audit(auth.actor(), "pkg_otlobly_status", "purchase", po["po_id"],
             f"pkg {pno}: {old or '—'} → {status or '—'}")
    activity.log("set", "purchase", po["po_id"], _polabel(po),
                 detail=f"Otlobly status → {status or '—'} (pkg {pno})", user=_user())
    return jsonify({"ok": True, "otlobly_status": pk["otlobly_status"]})


# One-time legacy backfills, run ONCE per deploy (not per worker, not per request):
#   * link deposits recorded without an order # to the customer's order (by phone)
#   * ensure every order's customer exists in the CRM (intake/lead orders skipped it)
#   * re-run PO→order matching for manually-assigned items missing customer_order_id
# These used to run on every worker's boot and could clash on purchases.save;
# db.claim_once() now lets EXACTLY ONE worker run them, ending that race. Runtime
# paths keep all three consistent afterwards, so once-per-deploy is enough. Bump the
# _v suffix to force a re-run on a future deploy.
def _reconcile_pos_to_orders():
    import purchases
    import cfg as _cfg
    pdb = purchases.load()
    orders = db.list_orders()
    buf = _cfg.get(_cfg.load(), "pipeline.delivery_buffer_days",
                   settings_mod.DELIVERY_BUFFER_DAYS)
    touched = False
    for po in pdb.get("purchase_orders", []):
        purchases.attach_matches(po, orders)
        for oid, ch in purchases.apply_to_orders(po, orders, buf):
            db.update_order(oid, ch)
            activity.log("set", "order", oid, oid,
                         detail=f"auto → {ch.get('status', 'updated')} from "
                                f"{po.get('amazon_order_number') or po['po_id']} (reconcile)",
                         user="system")
            touched = True
    if touched:
        purchases.save(pdb)


if db.claim_once("boot:reconcile_v1"):
    for _fn, _label in ((_link_unlinked_deposits, "deposit backfill"),
                        (_backfill_customers_from_orders, "customer backfill"),
                        (_reconcile_pos_to_orders, "PO reconcile")):
        try:
            _fn()
        except Exception as _e:                  # noqa: BLE001 — never block boot
            app.logger.warning("%s skipped: %s", _label, _e)


def _patch_customer_status_map():
    """2026-07-13: carrier 'Delivered' must read 'في الطريق إلى اطلبلي' for customers —
    GAASH/Gerizim delivering only means the box reached Otlobly's side, never the
    customer; \"تم التسليم\" now comes solely from the owner-set Otlobly stage.
    Patches the SAVED Settings map once (idempotent + re-editable in Settings)."""
    cfgd = cfg.load()
    rows = cfg.get(cfgd, "customer_tracking.status_map", None)
    if not isinstance(rows, list):
        return
    changed = False
    for r in rows:
        if (r.get("match") or "").strip() in ("Delivered", "D1") \
                and (r.get("label") or "").strip() == "تم التسليم":
            r["label"], r["bucket"] = "في الطريق إلى اطلبلي", "arrived"
            changed = True
    if not any((r.get("match") or "").strip().lower() == "picked up by gerizim courier"
               for r in rows):
        rows.append({"match": "Picked up by Gerizim courier",
                     "label": "في الطريق إلى اطلبلي", "bucket": "arrived", "hidden": False})
        changed = True
    if changed:
        cfg.set_path(cfgd, "customer_tracking.status_map", rows)
        cfg.save(cfgd)


if db.claim_once("boot:customer_map_v1"):
    try:
        _patch_customer_status_map()
    except Exception as _e:                      # noqa: BLE001 — never block boot
        app.logger.warning("customer status-map patch skipped: %s", _e)


def _patch_k3_customs():
    """2026-07-27: K3 "Arrived at destination country" fires BEFORE customs
    clearance in every real GAASH sequence, but the SAVED Settings map buckets it
    "arrived" — jumping the customer progress bar past clearance (the wrong-status
    complaint). Patch the saved rows to bucket "customs" and add the text-match
    row; only rows still holding the old default label are touched, so an
    owner-customized row survives. Idempotent + re-editable in Settings."""
    cfgd = cfg.load()
    rows = cfg.get(cfgd, "customer_tracking.status_map", None)
    if not isinstance(rows, list):
        return
    changed = False
    for r in rows:
        if (r.get("match") or "").strip() in ("K3", "Arrived at destination country") \
                and (r.get("label") or "").strip() == "وصلت إلى بلدك" \
                and (r.get("bucket") or "").strip() == "arrived":
            r["bucket"] = "customs"
            changed = True
    if not any((r.get("match") or "").strip().lower() == "arrived at destination country"
               for r in rows):
        idx = next((i for i, r in enumerate(rows)
                    if (r.get("match") or "").strip() == "Cleared customs"), len(rows))
        rows.insert(idx, {"match": "Arrived at destination country",
                          "label": "وصلت إلى بلدك", "bucket": "customs", "hidden": False})
        changed = True
    if changed:
        cfg.set_path(cfgd, "customer_tracking.status_map", rows)
        cfg.save(cfgd)


if db.claim_once("boot:k3_customs_v1"):
    try:
        _patch_k3_customs()
    except Exception as _e:                      # noqa: BLE001 — never block boot
        app.logger.warning("K3 customs-bucket patch skipped: %s", _e)


def _patch_cleared_stage():
    """2026-07-27 (owner): "if a package has been cleared, let it say so, in green".
    GAASH posts "Cleared customs" as its own event, but it can lag by weeks or never
    arrive while the owner already knows. Adds the owner-settable `cleared` stage to
    the SAVED otlobly_map so setting a package to "cleared" shows the customer the
    green «تم التخليص الجمركي» straight away. Idempotent + re-editable in Settings."""
    cfgd = cfg.load()
    rows = cfg.get(cfgd, "customer_tracking.otlobly_map", None)
    if not isinstance(rows, list):
        return
    if any((r.get("status") or "").strip().lower() == "cleared" for r in rows):
        return
    rows.insert(0, {"status": "cleared", "label": "تم التخليص الجمركي", "bucket": "cleared"})
    cfg.set_path(cfgd, "customer_tracking.otlobly_map", rows)
    cfg.save(cfgd)


if db.claim_once("boot:cleared_stage_v1"):
    try:
        _patch_cleared_stage()
    except Exception as _e:                      # noqa: BLE001 — never block boot
        app.logger.warning("cleared-stage patch skipped: %s", _e)


def _patch_customs_codes():
    """2026-07-27: the SAVED status map matches only a handful of customs TEXTS, so a
    wording it has never seen fell through to the generic "قيد الشحن" — a live parcel
    reading "Parcel held by customs - customs check" was telling its customer the box
    was simply in transit. Adds the CD/SD customs CODE rows as a catch-all."""
    cfgd = cfg.load()
    rows = cfg.get(cfgd, "customer_tracking.status_map", None)
    if not isinstance(rows, list):
        return
    have = {(r.get("match") or "").strip().upper() for r in rows}
    added = [{"match": c, "label": "قيد التخليص الجمركي", "bucket": "customs", "hidden": False}
             for c in ("CD", "SD") if c not in have]
    if not added:
        return
    # after the text rows (text always wins over a code in _match_row) but before
    # the arrival/clearance rows, so ordering stays readable in the Settings table
    idx = next((i for i, r in enumerate(rows)
                if (r.get("match") or "").strip() == "Cleared customs"), len(rows))
    cfg.set_path(cfgd, "customer_tracking.status_map", rows[:idx] + added + rows[idx:])
    cfg.save(cfgd)


if db.claim_once("boot:customs_codes_v1"):
    try:
        _patch_customs_codes()
    except Exception as _e:                      # noqa: BLE001 — never block boot
        app.logger.warning("customs-code patch skipped: %s", _e)


def _bump_delivery_buffer():
    """2026-07-26 (owner): promise the customer arrival + 10 days instead of + 8.
    The saved config.json on the live disk wins over the code default, so bump it
    once — and ONLY if it still holds the old default 8, never an owner-set value.
    Re-editable any time in ⚙ Settings → Customer ETA buffer."""
    cfgd = cfg.load()
    if cfg.get(cfgd, "pipeline.delivery_buffer_days", None) != 8:
        return
    cfg.set_path(cfgd, "pipeline.delivery_buffer_days", settings_mod.DELIVERY_BUFFER_DAYS)
    cfg.save(cfgd)


if db.claim_once("boot:delivery_buffer_10_v1"):
    try:
        _bump_delivery_buffer()
    except Exception as _e:                      # noqa: BLE001 — never block boot
        app.logger.warning("delivery-buffer bump skipped: %s", _e)


if __name__ == "__main__":
    # threaded=True: the dev server must handle concurrent requests, or a single
    # slow external call (GAASH tracking, SerpAPI) blocks every other request —
    # pages/assets/preview appear to freeze. Prod uses gunicorn (unaffected).
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8789)),
            debug=False, threaded=True)
