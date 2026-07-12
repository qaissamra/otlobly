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

import hashlib
import hmac
import json
import os
import re
import secrets
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
import mailer
import messages
import money
import meta_leads as meta_leads_mod
import normalize
import notify
import pnl as pnl_mod
import pricing
import purchases
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


@app.route("/healthz")
def healthz():
    """Always-200 health check for the host (independent of login/setup state) so
    the platform never restart-loops while the app is mid-boot or has no admin yet."""
    return "ok", 200


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
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
def index():
    """The public landing page — for EVERYONE, staff sessions included, so the
    owner can always see what customers see. The staff dashboard lives at /app."""
    return render_template("landing.html", wa_number=_wa_business_number(),
                           sections=settings_mod.public_sections())


@app.route("/app")
@login_required
def staff_app():
    """The staff dashboard (admin/sales/fulfillment). Logged-out visitors are
    bounced to /login by login_manager.login_view."""
    return send_file(HERE / "web" / "index.html")


@app.route("/api/me")
@login_required
def me():
    return jsonify(current_user.as_dict())


# --------------------------------------------------------------------------- #
# Orders / report
# --------------------------------------------------------------------------- #
@app.route("/api/report")
@auth.require("view_orders")
def api_report():
    return jsonify(redact_report(report_mod.build(orders_db())))


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
    return jsonify(pnl_mod.build(since=since, until=until))


@app.route("/api/pnl/drill")
@auth.require("view_pnl")
def api_pnl_drill():
    # The rows behind one P&L card (metric=revenue|to_order|cogs|customers|meta),
    # same window params as /api/pnl so the drill always matches the cards.
    since, until = _pnl_range()
    try:
        return jsonify(pnl_mod.drill(request.args.get("metric", ""), since=since, until=until))
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
    # read-only: flag which orders' customers already have an ID document on file
    idset = {normalize.phone_core(c.get("whatsapp") or "")
             for c in db.list_customers() if c.get("id_image")}
    idset.discard("")
    for r in data["orders"] + placed + cart:
        core = normalize.phone_core(r.get("phone") or "")
        r["has_id"] = bool(core and core in idset)
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


@app.route("/api/settings", methods=["GET", "POST"])
@login_required
def api_settings():
    if request.method == "GET":
        if not current_user.has("view_orders"):
            abort(403)
        return jsonify(settings_mod.read())
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
    return jsonify(amazon_import.import_product(url) if url else {"error": "no url"})


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
    """Paste an Amazon link → title/image/price auto-fetched → catalog row.
    Display price defaults to the markup-applied quote; staff can override."""
    b = request.get_json(force=True, silent=True) or {}
    url = (b.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400
    d = amazon_import.import_product(url)              # cache-first — no refresh
    if d.get("error"):
        return jsonify({"ok": False, "error": d["error"]}), 400
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
def api_az_profile():
    fresh = request.args.get("fresh", "0") in ("1", "true", "yes")
    return jsonify(az.profile_info(request.args.get("box", ""), force=fresh))


@app.route("/api/az/rotate_status")
@auth.require("edit_fulfillment")
def api_az_rotate_status():
    return jsonify(az.rotate_status(request.args.get("job_id", "")))


@app.route("/api/az/track_fetch_status")
@auth.require("edit_fulfillment")
def api_az_track_fetch_status():
    return jsonify(az.track_fetch_status(request.args.get("job_id", "")))


@app.route("/api/az/ip", methods=["POST"])
@auth.require("edit_fulfillment")
def api_az_ip():
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(az.check_ip(b.get("box", "")))


@app.route("/api/az/launch", methods=["POST"])
@auth.require("edit_fulfillment")
def api_az_launch():
    b = request.get_json(force=True, silent=True) or {}
    res = az.launch(b.get("box", ""))
    if res.get("ok"):
        activity.log("set", "purchase", b.get("box", ""), "Profile " + b.get("box", ""),
                     detail="started Multilogin profile", user=_az_user())
    return jsonify(res)


@app.route("/api/az/stop", methods=["POST"])
@auth.require("edit_fulfillment")
def api_az_stop():
    b = request.get_json(force=True, silent=True) or {}
    res = az.stop(b.get("box", ""))
    if res.get("ok"):
        activity.log("set", "purchase", b.get("box", ""), "Profile " + b.get("box", ""),
                     detail="closed Multilogin profile", user=_az_user())
    return jsonify(res)


@app.route("/api/az/rotate", methods=["POST"])
@auth.require("edit_fulfillment")
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
    if (b.get("name") or "").strip():
        cust = dict(o.get("customer") or {})
        cust["name"] = b["name"].strip()
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
    return jsonify({"purchase_orders": [purchases.summary(p) for p in pdb["purchase_orders"]],
                    "statuses": purchases.ITEM_STATUSES})


@app.route("/api/purchase", methods=["GET", "POST"])
@login_required
def api_purchase():
    import purchases
    if request.method == "GET":
        if not current_user.has("view_orders"):
            abort(403)
        p = purchases.find(purchases.load(), request.args.get("id", ""))
        return jsonify(purchases.summary(p) if p else {"error": "not found"})
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
    buf = cfg.get(cfg.load(), "pipeline.delivery_buffer_days", 8)
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
    lim = int(request.args.get("limit", "60"))
    return jsonify({"activity": activity.recent(lim, ent)})


@app.route("/api/po_image", methods=["GET", "POST"])
@login_required
def api_po_image():
    import base64
    import purchases
    if request.method == "GET":
        if not current_user.has("view_orders"):
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
def api_clickup():
    return jsonify(run_script("clickup.py"))


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
                           b["role"], b.get("name", ""))
        except Exception as e:  # noqa (unique violation)
            return jsonify({"ok": False, "error": f"{e}"}), 400
        db.audit(auth.actor(), "create_user", "user", b["username"], b["role"])
        return jsonify({"ok": True})
    if request.method == "PATCH":                 # deactivate/reactivate or reset password
        b = request.get_json(force=True, silent=True) or {}
        u = db.get_user_by_id(int(b["id"])) if str(b.get("id", "")).isdigit() else None
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
    return jsonify({"users": db.list_users(), "roles": auth.ROLES})


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


# --------------------------------------------------------------------------- #
# Backup — one zip of ALL live data, pulled nightly by backup_pull.py (Mac)
# --------------------------------------------------------------------------- #
# Every writable artifact on the data disk. New data_path() files/dirs added to
# the app MUST be added here too, or they won't be in backups.
BACKUP_FILES = ("customers.json", "purchases.json", "trash.json", "orders.json",
                "activity.jsonl", "config.json", "tracking_cache.json")
BACKUP_DIRS = ("customer_ids", "po_images")


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
    return render_template("order.html")


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
    gwds = [pk.get("tracking_number") for _, pk in uniq if (pk.get("tracking_number") or "").strip()]
    tls = tracking.timelines_with_fallback(gwds) if gwds else {}
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
            shipments.append({"tracking": otl, "items": items, "events": [],
                              "current": {"label": label, "bucket": "transit"}, **est})
            continue
        tl = tls.get(gwd, {})
        if tl.get("ok"):
            ct = tracking.customer_timeline(tl["events"], smap, dlabel)
            ship = {"tracking": otl, "items": items, "current": ct["current"],
                    "events": ct["events"], **est}
            if tl.get("stale"):                   # served from the last-known cache
                ship.update(stale=True, as_of=tl.get("fetched_at"))
            shipments.append(ship)
        else:
            # every tier failed AND nothing cached → honest generic state
            shipments.append({"tracking": otl, "items": items, "events": [],
                              "current": {"label": "قيد الشحن", "bucket": "transit"}, **est})
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
    pdb = purchases.load()
    names, oids = set(), set()
    if re.match(r"^OTL\d", q.upper()):            # an OTL tracking number
        po, pk = purchases.find_by_customer_tracking(pdb, q)
        pairs = [(po, pk)] if pk else []
    else:                                         # the customer's FULL mobile number
        core = normalize.phone_core(q)
        if len(core) < 9:
            return jsonify({"found": False,
                            "error": "Please enter your full mobile number (e.g. 0599xxxxxx)."}), 400
        pairs, names, oids = _phone_pairs(core, pdb)
    shipments = _shipments_for(pairs, names, oids)
    if not shipments:
        return jsonify({"found": False, "error": "No package found for that number. "
                        "Make sure you entered your full mobile number, or contact us."}), 404
    return jsonify({"found": True, "count": len(shipments), "shipments": shipments})


# ---- Customer account portal: phone + WhatsApp OTP login -------------------- #
OTP_TTL = 600                  # code valid for 10 minutes
OTP_MAX_ATTEMPTS = 5


def customer_required(fn):
    """Guard customer-portal API: 401 unless a customer session is established."""
    @wraps(fn)
    def wrapper(*a, **k):
        if not session.get("cust_phone"):
            abort(401)
        return fn(*a, **k)
    return wrapper


def _match_customer(core):
    """(matched?, name) for a phone core — only KNOWN customers (with an order) can log in."""
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
    return render_template("account.html", wa_number=_wa_business_number())


@app.route("/api/customer/me")
def api_customer_me():
    if session.get("cust_phone"):
        core = session["cust_phone"]
        row = _customer_row_for_core(core) or {}
        email = (row.get("email") or "").strip()
        return jsonify({"logged_in": True, "name": session.get("cust_name") or "",
                        "phone": core,
                        "whatsapp": row.get("whatsapp") or None,   # e164 for form prefill
                        "email_masked": _mask_email(email) if email else None,
                        "email_verified": bool(email and row.get("email_verified_at"))})
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
    matched, name = _match_customer(core)
    if not matched:
        return jsonify({"ok": False,
                        "error": "لا توجد طلبات لهذا الرقم · No orders found for this number."}), 404
    code = f"{secrets.randbelow(1000000):06d}"
    db.set_setting(f"otp:{core}", {"hash": auth.hash_pw(code),
                                   "expires": time.time() + OTP_TTL, "attempts": 0, "name": name})
    pin = normalize.normalize_phone(b.get("phone") or "")
    e164 = (pin or {}).get("e164") or ("+" + core)
    res = notify.send_whatsapp_otp(e164, code)
    if res.get("ok"):
        return jsonify({"ok": True, "sent": "whatsapp"})
    app.logger.warning("OTP for %s = %s (whatsapp not sent: %s)", core, code, res.get("error"))
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
    session["cust_phone"] = core
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
    session["cust_phone"] = rec["core"]
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
    session["cust_phone"] = rec["core"]
    session["cust_name"] = rec.get("name") or _match_customer(rec["core"])[1]
    session.permanent = True
    return jsonify({"ok": True, "name": session["cust_name"]})


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


@app.route("/api/customer/logout", methods=["POST"])
def api_customer_logout():
    session.pop("cust_phone", None)
    session.pop("cust_name", None)
    return jsonify({"ok": True})


@app.route("/api/customer/orders")
@customer_required
def api_customer_orders():
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
        buf = cfg.get(cfg.load(), "pipeline.delivery_buffer_days", 8)
        eta_iso = purchases._arrival_plus(pk["arrival"].strip(), buf)
    eta = _fmt_delivery(eta_iso)
    if not eta:
        return jsonify({"ok": False, "error": "Set the package's Arrives date first."}), 400
    if not (pk.get("customer_tracking") or "").strip():
        pk["customer_tracking"] = purchases.gen_customer_tracking(pdb)
        purchases.save(pdb)
    otl = pk["customer_tracking"]
    seen = pk.get("track_notified") or {}          # {order_id: iso} — one send per order
    if seen.get(oid) and not b.get("force"):
        return jsonify({"ok": False, "already": True, "notified_at": seen[oid],
                        "error": "Already notified — resend?"}), 409
    res = notify.send_track_package(e164, name, otl, eta, otl=otl)
    dev = (not res.get("ok")) and bool(os.environ.get("OTLOBLY_OTP_DEV"))  # local preview, no WhatsApp creds
    if res.get("ok") or dev:
        seen[oid] = db.now_iso()                   # stamp so the guard works (dev branch never runs in prod)
        pk["track_notified"] = seen
        purchases.save(pdb)
        if res.get("ok"):
            activity.log("notify", "order", oid, oid,
                         detail=f"track_package → {e164} · ETA {eta} · {otl}", user=_user())
            return jsonify({"ok": True, "sent_to": e164, "otl": otl, "eta": eta})
        return jsonify({"ok": True, "dev": True,
                        "preview": {"to": e164, "name": name, "order_ref": otl,
                                    "delivery_date": eta, "otl": otl}})
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
    buf = _cfg.get(_cfg.load(), "pipeline.delivery_buffer_days", 8)
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


if __name__ == "__main__":
    # threaded=True: the dev server must handle concurrent requests, or a single
    # slow external call (GAASH tracking, SerpAPI) blocks every other request —
    # pages/assets/preview appear to freeze. Prod uses gunicorn (unaffected).
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8789)),
            debug=False, threaded=True)
