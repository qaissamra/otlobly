#!/usr/bin/env python3
"""
Local Otlobly order dashboard — zero external packages.

    python3 dashboard.py        # opens http://localhost:8788

Serves web/index.html and a small JSON API over orders.json:
  GET  /api/report                 live report (counts, money, queue, orders)
  POST /api/order                  add an order from the intake form
  POST /api/status   {id,status}   change an order's status
  POST /api/assign   {id,box}      set the Multilogin profile/box
  GET  /api/message?id=&kind=      render a WhatsApp template (+ wa.me link)
  POST /api/export                 write orders_export.csv (sheets.py)
  POST /api/clickup                run the ClickUp sync (clickup.py)
Reads orders.json on every request, so it's always live. No model calls.
"""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import activity
import amazon_import
import az
import cfg
import customers
import estimate
import settings as settings_mod
import messages
import clickup_po
import normalize
import pnl
import pricing
import purchases
import report
import sheet_sync
import store
import tracking
import trash

HERE = Path(__file__).parent
PORT = 8788


def add_order(form):
    """Create an order from the intake form payload and persist it."""
    db = store.load()
    links = form.get("links") or []
    if isinstance(links, str):
        links = [l for l in links.splitlines() if l.strip()]
    phones = normalize.collect_phones(form.get("phone"), form.get("phone2"))
    items = normalize.parse_items(links, expand=True)   # resolve a.co on add
    # Merge product data carried from the estimator (image/title/price/qty) by ASIN,
    # so the order keeps it and nothing is re-entered downstream.
    prods = {(p.get("asin") or "").upper(): p for p in (form.get("products") or [])}
    for it in items:
        p = prods.get((it.get("asin") or "").upper())
        if p:
            it["image"] = p.get("image") or it.get("image")
            it["title"] = p.get("title") or it.get("title")
            it["item_usd"] = p.get("item_usd")
            it["qty"] = int(p.get("qty") or 1)
    notes = form.get("notes", "")
    if form.get("deposit_note"):
        notes = (notes + "  " if notes else "") + f"عربون/Deposit: {form['deposit_note']}"
    order = store.new_order(
        db, name=form.get("name", ""), phones=phones,
        address=form.get("address", ""), city=form.get("city", ""), items=items,
        batch=form.get("batch"), profile_box=form.get("profile_box") or None,
        status=form.get("status", "REQUESTED"),
        amount_to_collect_usd=(float(form["amount"]) if form.get("amount") else None),
        notes=notes)
    order, how = store.upsert(db, order)
    store.save(db)
    return order, how


def update_field(order_id, **changes):
    """Apply field changes to an order. Returns (order, olds) where `olds` maps
    each changed field → its previous value (for the activity log)."""
    db = store.load()
    o = store.find(db, order_id)
    if not o:
        return None, {}
    olds = {}
    if "status" in changes and changes["status"] in store.STATUSES:
        olds["status"] = o.get("status")
        o["status"] = changes["status"]
        # Stamp the workflow milestones as they happen.
        stamp = {"QUOTED": "quoted_at", "PAID": "paid_at",
                 "DELIVERED": "delivered_at"}.get(o["status"])
        if stamp and not o.get(stamp):
            o[stamp] = store.now_iso()
    if "box" in changes:
        olds["box"] = o.get("profile_box")
        o["profile_box"] = changes["box"] or None
    if "amazon_order_number" in changes:
        olds["amazon_order_number"] = o.get("amazon_order_number")
        o["amazon_order_number"] = (changes["amazon_order_number"] or "").strip() or None
    o["updated_at"] = store.now_iso()
    store.save(db)
    return o, olds


def order_label(o):
    """Human header for an order in the activity feed: 'OTL-0001 · Ahd'."""
    name = (o.get("customer") or {}).get("name") or ""
    return f"{o.get('order_id')}" + (f" · {name}" if name else "")


def save_customer(form):
    db = customers.load()
    cust = customers.new_customer(
        db, name=form.get("name", ""), whatsapp=form.get("whatsapp", ""),
        email=form.get("email", ""), address=form.get("address", ""),
        city=form.get("city", ""), vip=bool(form.get("vip")),
        notes=form.get("notes", ""),
        payment_method=form.get("payment_method", "Cash on delivery"),
        id_image=form.get("id_image", ""))
    cust, how = customers.upsert(db, cust)
    customers.save(db)
    return cust, how


def load_env():
    """Load .env into a copy of the environment so subprocess scripts get the
    secrets (CLICKUP_API_TOKEN, META_*, …) even if the server wasn't sourced."""
    env = os.environ.copy()
    envfile = HERE / ".env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    return env


def run_script(name, *args):
    cmd = [sys.executable, str(HERE / name), *args]
    try:
        p = subprocess.run(cmd, cwd=HERE, env=load_env(),
                           capture_output=True, text=True, timeout=600)
        tail = "\n".join((p.stdout or p.stderr).strip().splitlines()[-10:])
        return {"ok": p.returncode == 0, "output": tail}
    except Exception as e:  # noqa
        return {"ok": False, "output": str(e)}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except ValueError:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "web" / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path == "/api/report":
            return self._json(report.build())
        if u.path == "/api/pnl":
            return self._json(pnl.build())
        if u.path == "/api/customers":
            cdb, odb = customers.load(), store.load()
            rows = sorted((customers.enrich(c, odb) for c in cdb["customers"]),
                          key=lambda c: -c["total_spent_usd"])
            return self._json({"customers": rows,
                               "payment_methods": customers.PAYMENT_METHODS})
        if u.path == "/api/customer":
            q = parse_qs(u.query)
            cdb = customers.load()
            c = customers.find(cdb, (q.get("id") or [""])[0])
            if not c:
                return self._json({"error": "not found"}, 404)
            return self._json(customers.enrich(c, store.load()))
        if u.path == "/api/import":
            url = (parse_qs(u.query).get("url") or [""])[0]
            if not url:
                return self._json({"error": "no url"}, 400)
            return self._json(amazon_import.import_product(url))
        if u.path == "/api/extract_asin":   # link -> ASIN, NO SerpApi credit (string parse + a.co expand)
            url = (parse_qs(u.query).get("url") or [""])[0].strip()
            if not url:
                return self._json({"error": "no url"}, 400)
            clean = normalize.clean_amazon_url(url, expand=True)
            return self._json({"asin": normalize.extract_asin(clean), "clean_url": clean})
        if u.path == "/api/purchases":
            pdb = purchases.load()
            return self._json({"purchase_orders":
                               [purchases.summary(p) for p in pdb["purchase_orders"]],
                               "statuses": purchases.ITEM_STATUSES})
        if u.path.startswith("/api/po_image"):
            fn = (parse_qs(u.query).get("file") or [""])[0]
            p = purchases.IMAGE_DIR / fn
            if fn and "/" not in fn and p.exists():
                ext = fn.rsplit(".", 1)[-1].lower()
                ct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "gif": "image/gif", "webp": "image/webp",
                      "svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                return self._send(200, p.read_bytes(), ct)
            return self._send(404, b"")
        if u.path.startswith("/api/customer_image"):
            fn = (parse_qs(u.query).get("file") or [""])[0]
            p = customers.ID_DIR / fn
            if fn and "/" not in fn and p.exists():
                ext = fn.rsplit(".", 1)[-1].lower()
                ct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "gif": "image/gif", "webp": "image/webp",
                      "svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                return self._send(200, p.read_bytes(), ct)
            return self._send(404, b"")
        if u.path == "/api/purchase":
            p = purchases.find(purchases.load(), (parse_qs(u.query).get("id") or [""])[0])
            return self._json(purchases.summary(p) if p else {"error": "not found"},
                              200 if p else 404)
        if u.path == "/api/automatch":
            asin = (parse_qs(u.query).get("asin") or [""])[0]
            idx = purchases.asin_index(store.load()["orders"])
            cid, cname, amb = purchases.match_item(asin, idx)
            return self._json({"asin": asin.upper(), "customer_order_id": cid,
                               "customer_name": cname, "ambiguous": amb})
        if u.path == "/api/track_gwd":
            tn = (parse_qs(u.query).get("tracking") or [""])[0]
            return self._json(tracking.track(tn) if tn else {"error": "no tracking number"})
        if u.path == "/api/pricing":
            return self._json({"markup_pct": pricing.markup_pct()})
        if u.path == "/api/needorder":
            return self._json(store.need_order(store.load()["orders"]))
        if u.path == "/api/az/profile":
            q = parse_qs(u.query)
            box = (q.get("box") or [""])[0]
            fresh = (q.get("fresh") or ["0"])[0] in ("1", "true", "yes")
            return self._json(az.profile_info(box, force=fresh))
        if u.path == "/api/az/rotate_status":
            return self._json(az.rotate_status((parse_qs(u.query).get("job_id") or [""])[0]))
        if u.path == "/api/az/track_fetch_status":
            return self._json(az.track_fetch_status((parse_qs(u.query).get("job_id") or [""])[0]))
        if u.path == "/api/settings":
            return self._json(settings_mod.read())
        if u.path == "/api/trash":
            return self._json({"trash": trash.items(trash.load())})
        if u.path == "/api/activity":
            q = parse_qs(u.query)
            ent = (q.get("entity") or [""])[0] or None
            lim = int((q.get("limit") or ["60"])[0])
            return self._json({"activity": activity.recent(lim, ent)})
        if u.path == "/api/message":
            q = parse_qs(u.query)
            oid = (q.get("id") or [""])[0]
            kind = (q.get("kind") or ["status_update"])[0]
            o = store.find(store.load(), oid)
            if not o:
                return self._json({"error": "not found"}, 404)
            return self._json(messages.render(o, kind))
        if u.path.startswith("/logo."):
            for ext, ct in (("png", "image/png"), ("svg", "image/svg+xml"),
                            ("jpg", "image/jpeg")):
                p = HERE / f"logo.{ext}"
                if p.exists():
                    return self._send(200, p.read_bytes(), ct)
            return self._send(404, b"")
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path == "/api/order":
            order, how = add_order(body)
            if how == "created":
                activity.log("created", "order", order["order_id"],
                             order_label(order), detail="new order")
            return self._json({"ok": True, "how": how, "order_id": order["order_id"]})
        if u.path == "/api/status":
            o, olds = update_field(body.get("id"), status=body.get("status"))
            if o:
                activity.log("set", "order", o["order_id"], order_label(o),
                             field="status", old=olds.get("status"), new=o.get("status"))
            return self._json({"ok": bool(o)})
        if u.path == "/api/assign":
            o, olds = update_field(body.get("id"), box=body.get("box"))
            if o:
                activity.log("set", "order", o["order_id"], order_label(o),
                             field="profile_box", old=olds.get("box"),
                             new=o.get("profile_box"))
            return self._json({"ok": bool(o)})
        if u.path == "/api/amazon_number":
            o, olds = update_field(body.get("id"),
                                   amazon_order_number=body.get("amazon_order_number", ""))
            if o:
                activity.log("set", "order", o["order_id"], order_label(o),
                             field="amazon_order_number",
                             old=olds.get("amazon_order_number"),
                             new=o.get("amazon_order_number"))
            return self._json({"ok": bool(o)})
        if u.path == "/api/estimate":
            items = body.get("items") or []
            if not items:
                return self._json({"error": "no items"}, 400)
            return self._json(estimate.estimate_cart(items))
        if u.path == "/api/settings":
            return self._json({"ok": True, "settings": settings_mod.apply(body)})
        if u.path == "/api/az/ip":
            return self._json(az.check_ip(body.get("box", "")))
        if u.path == "/api/az/launch":
            res = az.launch(body.get("box", ""))
            if res.get("ok"):
                activity.log("set", "purchase", body.get("box", ""), "Profile " + body.get("box", ""),
                             detail="started Multilogin profile (manual)")
            return self._json(res)
        if u.path == "/api/az/stop":
            res = az.stop(body.get("box", ""))
            if res.get("ok"):
                activity.log("set", "purchase", body.get("box", ""), "Profile " + body.get("box", ""),
                             detail="closed Multilogin profile")
            return self._json(res)
        if u.path == "/api/az/rotate":
            box = body.get("box", "")
            res = az.rotate_start(box, body.get("target_risk", 25), body.get("max_tries", 8))
            if res.get("job_id"):
                activity.log("set", "purchase", box, "Profile " + box, detail="rotating IP")
            return self._json(res)
        if u.path == "/api/az/track_fetch":
            box = body.get("box", "")
            res = az.track_fetch_start(box, body.get("items", []), body.get("max_items", 12))
            if res.get("job_id"):
                activity.log("set", "purchase", box, "Profile " + box,
                             detail="auto-fetching Amazon tracking")
            return self._json(res)
        if u.path == "/api/quote":
            try:
                total = float(body.get("total"))
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": "enter the Amazon checkout total"})
            db = store.load()
            o = store.find(db, body.get("id"))
            if not o:
                return self._json({"ok": False, "error": "not found"})
            old = o.get("amount_to_collect_usd")
            amount = pricing.apply_markup(total)            # checkout total × (1+markup)
            o["amount_to_collect_usd"] = amount
            o["checkout"] = {**(o.get("checkout") or {}), "order_total_usd": total}
            if o.get("status") == "REQUESTED":
                o["status"] = "QUOTED"
                o["quoted_at"] = o.get("quoted_at") or store.now_iso()
            o["updated_at"] = store.now_iso()
            store.save(db)
            activity.log("set", "order", o["order_id"], order_label(o),
                         field="amount_to_collect_usd", old=old, new=amount,
                         detail=f"quoted from Amazon checkout ${total:.2f}")
            return self._json({"ok": True, "amount": amount,
                               "markup_pct": pricing.markup_pct()})
        if u.path == "/api/customer":
            c, how = save_customer(body)
            activity.log("created" if how == "created" else "set", "customer",
                         c["customer_id"], c.get("name") or c["customer_id"],
                         detail="saved profile")
            return self._json({"ok": True, "how": how, "customer_id": c["customer_id"]})
        if u.path == "/api/customers/sync":
            cdb, odb = customers.load(), store.load()
            n = customers.sync_from_orders(cdb, odb)
            customers.save(cdb)
            return self._json({"ok": True, "created": n, "total": len(cdb["customers"])})
        if u.path == "/api/sync_sheet":
            try:
                res = sheet_sync.sync()
                return self._json({"ok": True, "updated": res["updated"],
                                   "created": res["created"],
                                   "removed": len(res.get("removed", []))})
            except Exception as e:  # noqa
                return self._json({"ok": False, "error": str(e)})
        if u.path == "/api/purchase":
            pdb = purchases.load()
            odb = store.load()
            before = purchases.find(pdb, body.get("po_id")) if body.get("po_id") else None
            old_snapshot = json.loads(json.dumps(before)) if before else None
            po, how = purchases.save_full(pdb, body, odb["orders"])
            purchases.save(pdb)
            # Connect supply→demand: matched customer orders auto-flip to ORDERED and
            # inherit the Amazon order #, box, arrival date + customer ETA (arrival+buffer).
            buf = cfg.get(cfg.load(), "pipeline.delivery_buffer_days", 8)
            changed = purchases.apply_to_orders(po, odb["orders"], buf)
            if changed:
                store.save(odb)
                src = po.get("amazon_order_number") or po["po_id"]
                for oid, ch in changed:
                    activity.log("set", "order", oid, oid,
                                 detail=f"auto → {ch.get('status', 'updated')} from {src}")
            if how == "created":
                activity.log("created", "purchase", po["po_id"],
                             ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number")
                             else po["po_id"], detail="new purchase order")
            else:
                activity.log_po_diff(old_snapshot, po)
            return self._json({"ok": True, "how": how, "po_id": po["po_id"],
                               "purchase_order": purchases.summary(po)})
        if u.path == "/api/po_image":
            import base64
            data = body.get("data_base64", "")
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            ext = (body.get("filename", "img.png").rsplit(".", 1)[-1] or "png")[:5]
            purchases.IMAGE_DIR.mkdir(exist_ok=True)
            fn = f"{body.get('po_id', 'po')}-{int(time.time())}.{ext}"
            try:
                (purchases.IMAGE_DIR / fn).write_bytes(base64.b64decode(data))
            except Exception as e:  # noqa
                return self._json({"ok": False, "error": str(e)})
            pdb = purchases.load()
            po = purchases.set_screenshot(pdb, body.get("po_id"), fn)
            purchases.save(pdb)
            activity.log("uploaded", "purchase", body.get("po_id", ""),
                         ("Order # " + po["amazon_order_number"]) if po and po.get("amazon_order_number")
                         else body.get("po_id", ""), detail="uploaded a file")
            return self._json({"ok": True, "file": fn})
        if u.path == "/api/customer_image":
            import base64
            data = body.get("data_base64", "")
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            ext = (body.get("filename", "id.png").rsplit(".", 1)[-1] or "png")[:5]
            customers.ID_DIR.mkdir(exist_ok=True)
            fn = f"{body.get('customer_id', 'cust')}-{int(time.time())}.{ext}"
            try:
                (customers.ID_DIR / fn).write_bytes(base64.b64decode(data))
            except Exception as e:  # noqa
                return self._json({"ok": False, "error": str(e)})
            cdb = customers.load()
            c = customers.find(cdb, body.get("customer_id"))
            if c:
                c["id_image"] = fn
                c["updated_at"] = customers.now_iso()
                customers.save(cdb)
            activity.log("uploaded", "customer", body.get("customer_id", ""),
                         (c.get("name") if c else body.get("customer_id", "")),
                         detail="uploaded ID document")
            return self._json({"ok": True, "file": fn})
        if u.path == "/api/purchase/import_clickup":
            import clickup_import
            try:
                res = clickup_import.sync()
            except Exception as e:  # noqa
                return self._json({"ok": False, "error": str(e)})
            if res.get("error"):
                return self._json({"ok": False, "error": res["error"]})
            for imp in res.get("imported", []):
                activity.log("created", "purchase", imp["order"],
                             "Order # " + imp["order"], detail="imported from ClickUp")
            return self._json({"ok": True, **res})
        if u.path == "/api/purchase/clickup":
            pdb = purchases.load()
            po = purchases.find(pdb, body.get("id"))
            if not po:
                return self._json({"ok": False, "error": "not found"})
            res = clickup_po.push(po, dry_run=bool(body.get("dry_run")))
            if res.get("ok") and not res.get("dry_run"):
                purchases.save(pdb)
                activity.log("synced", "purchase", po["po_id"],
                             ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number")
                             else po["po_id"], detail="sent to ClickUp")
            return self._json(res)
        if u.path == "/api/purchase/delete":
            pdb = purchases.load()
            po = purchases.find(pdb, body.get("id"))
            if not po:
                return self._json({"ok": False})
            label = ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number") else po["po_id"]
            tdb = trash.load()
            trash.add(tdb, "purchase_order", label, po)
            trash.save(tdb)
            ok = purchases.delete(pdb, body.get("id"))
            purchases.save(pdb)
            activity.log("deleted", "purchase", po["po_id"], label, detail="moved to Trash")
            return self._json({"ok": ok})
        if u.path == "/api/purchase/package/delete":
            pdb = purchases.load()
            po = purchases.find(pdb, body.get("id"))
            pi = body.get("pi")
            if not po or pi is None or pi >= len(po.get("packages", [])):
                return self._json({"ok": False})
            pkg = po["packages"].pop(pi)
            for i, pk in enumerate(po["packages"], 1):
                pk["package_no"] = i
            po["updated_at"] = purchases.now_iso()
            purchases.save(pdb)
            poh = ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number") else po["po_id"]
            tdb = trash.load()
            trash.add(tdb, "po_package", f"Package {pkg.get('package_no')} · {poh}", pkg,
                      origin={"po_id": po["po_id"]})
            trash.save(tdb)
            activity.log("deleted", "purchase", po["po_id"], poh,
                         detail=f"deleted Package {pkg.get('package_no')}")
            return self._json({"ok": True})
        if u.path == "/api/purchase/item/delete":
            pdb = purchases.load()
            po = purchases.find(pdb, body.get("id"))
            pi, ii = body.get("pi"), body.get("ii")
            try:
                it = po["packages"][pi]["items"].pop(ii)
            except (TypeError, KeyError, IndexError):
                return self._json({"ok": False})
            po["updated_at"] = purchases.now_iso()
            purchases.save(pdb)
            poh = ("Order # " + po["amazon_order_number"]) if po.get("amazon_order_number") else po["po_id"]
            tdb = trash.load()
            trash.add(tdb, "po_item", (it.get("title") or it.get("asin") or "product"), it,
                      origin={"po_id": po["po_id"], "package_no": po["packages"][pi].get("package_no")})
            trash.save(tdb)
            activity.log("deleted", "purchase", po["po_id"], poh,
                         detail=f"deleted product “{(it.get('title') or it.get('asin') or '')[:40]}”")
            return self._json({"ok": True})
        if u.path == "/api/trash/restore":
            tdb = trash.load()
            res = trash.restore(tdb, body.get("id"))
            if res.get("ok"):
                trash.save(tdb)
                activity.log("restored", "trash", res.get("kind", ""),
                             res.get("label", ""), detail=f"restored to {res.get('where','')}")
            return self._json(res)
        if u.path == "/api/trash/purge":
            tdb = trash.load()
            ok = trash.purge(tdb, body.get("id"))
            trash.save(tdb)
            return self._json({"ok": ok})
        if u.path == "/api/trash/empty":
            tdb = trash.load()
            n = trash.empty(tdb)
            trash.save(tdb)
            return self._json({"ok": True, "removed": n})
        if u.path == "/api/export":
            return self._json(run_script("sheets.py"))
        if u.path == "/api/clickup":
            return self._json(run_script("clickup.py"))
        if u.path == "/api/pnl/refresh":
            # Refresh each P&L source cache (cogs needs the token; others are local).
            outs = {name: run_script(name + ".py")
                    for name in ("clickup_cost", "revenue", "meta")}
            return self._json({"ok": True, "ran": outs})
        return self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    # Make .env secrets (SERPAPI_KEY*, CLICKUP_API_TOKEN, …) visible to the
    # IN-PROCESS modules too (amazon_import, tracking, clickup), not just the
    # subprocess scripts — otherwise "Get photo" reports "No SERPAPI_KEY".
    for _k, _v in load_env().items():
        os.environ.setdefault(_k, _v)
    url = f"http://localhost:{PORT}"
    print(f"Otlobly dashboard -> {url}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
