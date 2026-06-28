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

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from flask import (Flask, request, jsonify, redirect, url_for, render_template,
                   send_file, abort, session)
from flask_login import login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import activity
import amazon_import
import auth
import az
import cfg
import customers as cust_mod
import db
import estimate
import settings as settings_mod
import messages
import normalize
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

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("OTLOBLY_SECRET", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("OTLOBLY_SECURE")),
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


def orders_db():
    return {"orders": db.list_orders()}


def redact_report(rep):
    if current_user.has("view_money"):
        return rep
    for r in rep["orders"]:
        r["amount_to_collect_usd"] = None
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
    if request.method == "POST":
        user = auth.verify(request.form.get("username", "").strip(),
                           request.form.get("password", ""))
        if user:
            login_user(user)
            db.audit(auth.actor(), "login", "user", user.username, "")
            return redirect(url_for("index"))
        return render_template("login.html", error="Wrong username or password.")
    return render_template("login.html")


@app.route("/healthz")
def healthz():
    """Always-200 health check for the host (independent of login/setup state) so
    the platform never restart-loops while the app is mid-boot or has no admin yet."""
    return "ok", 200


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
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


@app.route("/api/pnl")
@auth.require("view_pnl")
def api_pnl():
    return jsonify(pnl_mod.build())


@app.route("/api/pnl/refresh", methods=["POST"])
@auth.require("admin_actions")
def api_pnl_refresh():
    return jsonify({"ok": True, "ran": {n: run_script(n + ".py")
                    for n in ("clickup_cost", "revenue", "meta")}})


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
    db.upsert_order(o, created_by=current_user.id)
    db.upsert_customer(make_customer(name=b.get("name", ""),
                                     whatsapp=phones[0]["e164"] if phones else "",
                                     address=b.get("address", "")))
    db.audit(auth.actor(), "create_order", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o), detail="new order", user=_user())
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
    return jsonify(store.need_order(db.list_orders()))


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


@app.route("/api/extract_asin")   # link -> ASIN, NO SerpApi credit (string parse + a.co expand)
@auth.require("edit_order")
def api_extract_asin():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "no url"}), 400
    clean = normalize.clean_amazon_url(url, expand=True)
    return jsonify({"asin": normalize.extract_asin(clean), "clean_url": clean})


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


@app.route("/api/sync_sheet", methods=["POST"])
@auth.require("edit_order")
def api_sync_sheet():
    import sheet_sync
    orders = db.list_orders()
    seq = max([int(o["order_id"].split("-")[1]) for o in orders] or [0])
    sdb = {"orders": orders, "seq": seq}
    try:
        res = sheet_sync.sync(sdb, save=False)
    except Exception as e:  # noqa
        return jsonify({"ok": False, "error": str(e)})
    for o in sdb["orders"]:
        db.upsert_order(o)
    # sheet_sync already copied pruned orders into Trash + dropped them from sdb;
    # remove them from SQLite too so they don't linger in both places.
    for oid, _name in res.get("removed", []):
        db.delete_order(oid)
    db.audit(auth.actor(), "sync_sheet", "orders", "*",
             f"{res['updated']} updated / {res['created']} created / {len(res.get('removed', []))} removed")
    return jsonify({"ok": True, "updated": res["updated"], "created": res["created"],
                    "removed": len(res.get("removed", []))})


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
    return jsonify({"ok": True, "file": fn})


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
    return jsonify(tracking.track(tn) if tn else {"error": "no tracking number"})


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


@app.route("/api/export", methods=["POST"])
@auth.require("admin_actions")
def api_export():
    return jsonify(run_script("sheets.py"))


@app.route("/api/clickup", methods=["POST"])
@auth.require("admin_actions")
def api_clickup():
    return jsonify(run_script("clickup.py"))


# --------------------------------------------------------------------------- #
# Admin: user management
# --------------------------------------------------------------------------- #
@app.route("/api/users", methods=["GET", "POST"])
@auth.require("manage_users")
def api_users():
    if request.method == "POST":
        b = request.get_json(force=True, silent=True) or {}
        if (b.get("role") not in auth.ROLES or not b.get("username")
                or len(b.get("password", "")) < 6):
            return jsonify({"ok": False, "error": "username, role, 6+ char password required"}), 400
        try:
            db.create_user(b["username"].strip(), auth.hash_pw(b["password"]),
                           b["role"], b.get("name", ""))
        except Exception as e:  # noqa (unique violation)
            return jsonify({"ok": False, "error": f"{e}"}), 400
        db.audit(auth.actor(), "create_user", "user", b["username"], b["role"])
        return jsonify({"ok": True})
    return jsonify({"users": db.list_users(), "roles": auth.ROLES})


# --------------------------------------------------------------------------- #
# Worker API (the local Mac worker that places Amazon orders) — bearer token
# --------------------------------------------------------------------------- #
def _worker_ok():
    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    auth_h = request.headers.get("Authorization", "")
    return tok and auth_h == f"Bearer {tok}"


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
                    "url": request.host_url.rstrip("/") + f"/order/{did}"})


@app.route("/order/<draft_id>", methods=["GET"])
def order_intake_page(draft_id):
    return render_template("order.html")


@app.route("/api/draft/<draft_id>", methods=["GET"])
@limiter.limit("20 per minute")
def api_draft_get(draft_id):
    d = db.get_setting(f"draft:{draft_id}")
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify({"products": d.get("products") or [], "amount": d.get("amount"),
                    "used": d.get("used", False)})


@app.route("/api/order/intake", methods=["POST"])
@limiter.limit("6 per minute")
def api_order_intake():
    """Public: a customer submits their contact for a pre-filled draft → creates the
    REQUESTED order (in 'Need to order') carrying the quoted products + amount."""
    b = request.get_json(force=True, silent=True) or {}
    d = db.get_setting(f"draft:{(b.get('draft_id') or '')}")
    if not d:
        return jsonify({"error": "This link has expired."}), 404
    prods = d.get("products") or []
    phones = normalize.collect_phones(b.get("phone"))
    if not (b.get("name") or "").strip() or not phones:
        return jsonify({"error": "Name and a valid phone are required."}), 400
    items = normalize.parse_items([p.get("link") or
                                   (f"https://www.amazon.com/dp/{p.get('asin')}" if p.get("asin") else "")
                                   for p in prods], expand=False)
    by_asin = {(p.get("asin") or "").upper(): p for p in prods}
    for it in items:
        p = by_asin.get((it.get("asin") or "").upper())
        if p:
            it["image"], it["title"] = p.get("image"), p.get("title")
            it["item_usd"], it["qty"] = p.get("item_usd"), int(p.get("qty") or 1)
    o = make_order(name=b.get("name", ""), phones=phones, address=b.get("address", ""),
                   city=b.get("city", ""), items=items, status="REQUESTED",
                   amount_to_collect_usd=(float(d["amount"]) if d.get("amount") else None))
    db.upsert_order(o)
    db.set_setting(f"draft:{b['draft_id']}", {**d, "used": True})
    db.audit({"username": "customer"}, "intake_order", "order", o["order_id"], "")
    activity.log("created", "order", o["order_id"], _olabel(o),
                 detail="customer self-intake", user="customer")
    return jsonify({"ok": True, "order_id": o["order_id"]})


@app.route("/track", methods=["GET"])
def track_page():
    return render_template("track.html")


@app.route("/api/track", methods=["POST"])
@limiter.limit("20 per minute")
def api_track():
    """Public self-service tracking: customer enters their MOBILE, NAME, or an OTL
    number → we find their package(s) → friendly status timeline for each. Exposes
    NO PII (no name/ID/address) — only the masked status."""
    b = request.get_json(force=True, silent=True) or {}
    q = (b.get("query") or b.get("tracking") or b.get("phone") or "").strip()
    if not q:
        return jsonify({"found": False, "error": "Enter your mobile number or name."}), 400
    pdb = purchases.load()
    pairs = []                                    # [(po, package)]
    names, oids = set(), set()                    # the customer's identifiers (empty for a raw OTL lookup)
    if re.match(r"^OTL\d", q.upper()):            # an OTL tracking number
        po, pk = purchases.find_by_customer_tracking(pdb, q)
        if pk:
            pairs = [(po, pk)]
    else:                                         # phone or name → their order(s) → packages
        if len(re.sub(r"\D", "", q)) >= 7:        # looks like a phone
            pin = normalize.normalize_phone(q)
            for o in db.list_orders():
                ph = store.primary_phone(o)
                if pin and ph and ph["e164"] == pin["e164"]:
                    names.add((o["customer"]["name"] or "").strip().lower())
                    oids.add(o["order_id"])
        if not names and not oids:                # name lookup (or phone with no hit)
            ql = q.strip().lower()
            if len(ql) >= 3:
                for o in db.list_orders():
                    nm = (o["customer"]["name"] or "").strip().lower()
                    if nm and (ql == nm or ql in nm):
                        names.add(nm)
                        oids.add(o["order_id"])
        pairs = purchases.find_packages_for(pdb, names, oids)
    # de-dupe + cap
    seen, uniq = set(), []
    for po, pk in pairs:
        key = (pk.get("customer_tracking") or "").upper() or id(pk)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((po, pk))
    uniq = uniq[:8]
    if not uniq:
        return jsonify({"found": False, "error": "We couldn't find a package for that. "
                        "Check your mobile number or name, or contact us."}), 404
    cfgd = cfg.load()
    smap = cfg.get(cfgd, "customer_tracking.status_map", tracking.DEFAULT_STATUS_MAP)
    dlabel = cfg.get(cfgd, "customer_tracking.default_label", tracking.DEFAULT_CUSTOMER_LABEL)
    gwds = [pk.get("tracking_number") for _, pk in uniq if (pk.get("tracking_number") or "").strip()]
    tls = tracking.timelines(gwds) if gwds else {}
    shipments = []
    for po, pk in uniq:
        otl = pk.get("customer_tracking")
        gwd = (pk.get("tracking_number") or "").strip()
        # A package holds items for SEVERAL customers — show ONLY this customer's.
        if names or oids:                         # phone/name lookup → we know who they are
            pk_items = [it for it in pk.get("items", [])
                        if (it.get("customer_name") or "").strip().lower() in names
                        or (it.get("customer_order_id") or "").strip().upper() in oids]
        else:                                     # raw OTL lookup → no customer context → no item list
            pk_items = []
        items = [{"title": (it.get("title") or it.get("asin") or "").strip(),
                  "image": it.get("image") or None}
                 for it in pk_items
                 if (it.get("title") or it.get("asin") or it.get("image"))][:6]
        if not gwd:
            shipments.append({"tracking": otl, "items": items, "events": [],
                              "current": {"label": "نقوم بتجهيز طلبك", "bucket": "transit"}})
            continue
        tl = tls.get(gwd, {})
        if tl.get("ok"):
            ct = tracking.customer_timeline(tl["events"], smap, dlabel)
            shipments.append({"tracking": otl, "items": items, "current": ct["current"],
                              "events": ct["events"], "est_delivery": pk.get("arrival") or None})
        else:
            shipments.append({"tracking": otl, "items": items, "events": [],
                              "current": {"label": "قيد الشحن", "bucket": "transit"}})
    return jsonify({"found": True, "count": len(shipments), "shipments": shipments})


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8789)), debug=False)
