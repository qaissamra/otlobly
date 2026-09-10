#!/usr/bin/env python3
"""
Self-checks: the operator role (2026-09-10) — one login that runs the whole day.

An operator quotes, records deposits, saves purchase orders WITH their Amazon
costs, attaches package photos, edits the GAASH ID library and can freeze the
mailer — and is refused everywhere the owner works: P&L, Settings writes, Team,
Trash purge, the backup, the Tatabu console, the Leluxe board. Fulfillment keeps
its old boundary (a package photo is still 403 for it). The suite also pins the
UI wiring (CAN_OPS, the Team role option, the role tag) and the runbook.

    ./.venv/bin/python test_role_operator.py
"""

import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-roleop-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402

HERE = Path(__file__).resolve().parent
fails = []
PNG_1x1 = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
           "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def _user(role):
    return auth.User({"id": 1, "username": "u", "role": role, "name": "U"})


def test_matrix():
    print("PERMISSION MATRIX:")
    op, adm, ful, sal = _user("operator"), _user("admin"), _user("fulfillment"), _user("sales")
    for p in ("view_orders", "view_money", "view_cost", "edit_order", "edit_fulfillment",
              "view_customers", "manage_customers", "view_meta_leads", "ops_actions"):
        check(f"operator has {p}", op.has(p))
    for p in ("view_pnl", "manage_users", "admin_actions"):
        check(f"operator lacks {p}", not op.has(p))
    check("admin also holds ops_actions (the day's actions never lock the owner out)",
          adm.has("ops_actions"))
    check("fulfillment does NOT gain ops_actions", not ful.has("ops_actions"))
    check("sales does NOT gain ops_actions", not sal.has("ops_actions"))
    check("operator is a real role (Team can create it)", "operator" in auth.ROLES)


def test_http():
    print("HTTP BOUNDARY:")
    db.init_db()
    db.create_user("op-adm", auth.hash_pw("s1"), "admin", "Owner", business_id=1)
    db.create_user("op-ops", auth.hash_pw("s1"), "operator", "Runner", business_id=1)
    db.create_user("op-ful", auth.hash_pw("s1"), "fulfillment", "Packer", business_id=1)
    adm, ops, ful = client("op-adm"), client("op-ops"), client("op-ful")

    me = ops.get("/api/me").get_json() or {}
    check("/api/me reports the operator role", me.get("role") == "operator")
    check("/api/me carries ops_actions", "ops_actions" in (me.get("perms") or []))

    # the day: order → quote → deposit → purchase order (with costs) → package photo
    r = ops.post("/api/order", json={"name": "Op Customer", "phone": "0599123456",
                                     "links": ["https://www.amazon.com/dp/B0OPERATOR1"],
                                     "city": "Ramallah"}).get_json() or {}
    check("operator creates a customer order", r.get("ok") is True)
    oid = r.get("order_id")
    r = ops.post("/api/quote", json={"id": oid, "total": 80}).get_json() or {}
    check("operator quotes it (markup applied)", r.get("ok") is True and (r.get("amount") or 0) > 80)
    r = ops.post("/api/payment", json={"order_id": oid, "amount": 20, "currency": "USD",
                                       "kind": "deposit"}).get_json() or {}
    check("operator records a deposit", r.get("ok") is True)
    r = ops.post("/api/purchase", json={
        "amazon_order_number": "113-1", "ship_to": "Op Customer", "profile_box": "E-B50",
        "total_usd": 150.0,
        "packages": [{"package_no": 1, "tracking_number": "GWDOPERATOR1",
                      "items": [{"title": "Watch", "asin": "B0OPERATOR1", "qty": 1,
                                 "customer_name": "Op Customer"}]}]}).get_json() or {}
    check("operator saves a purchase order", r.get("ok") is True)
    po_id = r.get("po_id")
    d = ops.get(f"/api/purchase?id={po_id}").get_json() or {}
    check("operator SEES the Amazon cost (view_cost)", d.get("money") is not False
          and float(d.get("total_usd") or 0) == 150.0)
    d = ops.get("/api/purchases").get_json() or {}
    check("operator's board carries money", d.get("money") is not False)
    r = ops.post("/api/purchase/package/image",
                 json={"po_id": po_id, "package_no": 1, "filename": "p.png",
                       "data_base64": PNG_1x1}).get_json() or {}
    check("operator attaches a package photo (ops_actions)", r.get("ok") is True)
    rv = ful.post("/api/purchase/package/image",
                  json={"po_id": po_id, "package_no": 1, "filename": "p.png",
                        "data_base64": PNG_1x1})
    check("fulfillment is still refused a package photo", rv.status_code == 403)
    d = ops.get("/api/settings").get_json() or {}
    check("operator reads the pricing settings (view_money), read-only",
          "markup_pct" in d)

    # the owner's surfaces stay shut
    refused = {
        "P&L": ops.get("/api/pnl").status_code,
        "Settings write": ops.post("/api/settings", json={"markup_pct": 0.5}).status_code,
        "Team": ops.get("/api/users").status_code,
        "Trash purge": ops.post("/api/trash/purge", json={"id": "x"}).status_code,
        "Backup": ops.get("/api/backup").status_code,
        "Tatabu console": ops.get("/api/admin/brokers").status_code,
        "Leluxe board": ops.get("/api/leluxe/orders").status_code,
        "GAASH mail accounts": ops.post("/api/gaash/account/add", json={}).status_code,
    }
    for name, code in refused.items():
        check(f"operator is refused: {name} ({code})", code in (401, 403))

    # the owner creates the login
    r = adm.post("/api/users", json={"username": "op-new", "password": "secret1",
                                     "role": "operator", "name": "New hire"}).get_json() or {}
    check("admin creates an operator login", r.get("ok") is True)
    d = adm.get("/api/users").get_json() or {}
    check("the roles list offers operator", "operator" in (d.get("roles") or []))
    check("the new user carries the role",
          any(u.get("username") == "op-new" and u.get("role") == "operator"
              for u in d.get("users") or []))


def test_wiring():
    print("SOURCE WIRING:")
    app_src = (HERE / "app.py").read_text(encoding="utf-8")
    for route, methods in (("/api/gerizim/registered", '["POST"]'), ("/api/purchase/package/image", '["GET", "POST"]'),
                           ("/api/purchase/package/image/delete", '["POST"]'), ("/api/gaash/check_tracking", '["POST"]'),
                           ("/api/gaash/freeze", '["POST"]')):
        # match the exact (path, methods) pair — /api/gerizim/registered also has a GET route
        m = re.search(r'@app\.route\("' + re.escape(route) + r'", methods=' + re.escape(methods)
                      + r'\)\n@auth\.require\("(\w+)"\)', app_src)
        check(f"{route} is gated ops_actions", bool(m) and m.group(1) == "ops_actions")
    for fn, hint in (("api_gaash_ids", "library edits"), ("api_gaash_name_id", "Pinning a name"),
                     ("api_gaash_thread", "accidental enrollment")):
        i = app_src.find(f"def {fn}(")
        j = app_src.find("\n@app.route", i) if i >= 0 else -1      # the whole function, to the next route
        body = app_src[i:j if j > 0 else i + 6000] if i >= 0 else ""
        check(f"{fn}: the inline gate is ops_actions", 'has("ops_actions")' in body and hint in body)
    for route in ("/api/pnl/refresh", "/api/trash/purge", "/api/trash/empty", "/api/admin/brokers",
                  "/api/gaash/account/add", "/api/gaash/test_send", "/api/leluxe/orders"):
        m = re.search(r'@app\.route\("' + re.escape(route) + r'"[^\n]*\n@auth\.require\("(\w+)"\)', app_src)
        check(f"{route} stays admin_actions", bool(m) and m.group(1) == "admin_actions")
    check("POST /api/settings stays admin", 'if not current_user.has("admin_actions"):\n        abort(403)\n    body = request.get_json' in app_src
          or app_src.count('if not current_user.has("admin_actions"):') >= 2)

    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    check("CAN_OPS is derived from ops_actions", 'CAN_OPS   = p.includes("ops_actions")' in idx)
    check("CAN_OPS is declared beside CAN_ADMIN", "CAN_ADMIN=true, CAN_OPS=true" in idx)
    check("Team page offers the operator role", 'value="operator"' in idx)
    check("Team page labels the role", 'operator:"Operator"' in idx)
    for snippet in ("function cuLabel(s){ if(CAN_OPS)", "pkgImgSec=CAN_OPS?",
                    "if(CAN_OPS) out.push({divider:true},{label:\"Erase this enrollment\"",
                    "${CAN_OPS?`<button class=\"gm-folder\" onclick=\"gmIdUpload()\">",
                    "canAdmin:CAN_ADMIN, canOps:CAN_OPS"):
        check(f"daily UI gate uses CAN_OPS: {snippet[:40]}", snippet in idx)
    check("status pick-lists use CAN_OPS (both)", idx.count("  const opts=CAN_OPS\n") == 2
          and "  const opts=CAN_ADMIN\n" not in idx)
    for snippet in ("function poCfFieldOpen(key){\n  if(!CAN_ADMIN)", "canAdmin:CAN_ADMIN,\n"):
        check(f"owner UI gate stays CAN_ADMIN: {snippet[:30]!r}", snippet in idx or snippet.strip() in idx)

    st = (HERE / "static" / "ds" / "status.js").read_text(encoding="utf-8")
    check("status.js knows the role tag", 'operator: T("Operator"' in st)
    gm = (HERE / "static" / "ds" / "gaash.js").read_text(encoding="utf-8")
    check("gaash.js freeze switch honours canOps", "(ctx.canOps || ctx.canAdmin) && ctx.seqsLive" in gm)
    att = (HERE / "attention.py").read_text(encoding="utf-8")
    check("Needs attention no longer assumes the owner's Telegram",
          "Watched inboxes" in att and 'reply \\"done\\" on Telegram' in att)
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    check("service worker cache bumped (v25)", 'const CACHE = "otl-off-v25"' in sw)
    for doc, needle in (("DEPLOY.md", "**Operator**"), ("README.md", "Operator"),
                        ("CLAUDE.md", "operator")):
        check(f"{doc} mentions the role", needle in (HERE / doc).read_text(encoding="utf-8"))

    rb = HERE / "docs" / "OPERATOR_RUNBOOK.md"
    check("the operator runbook exists", rb.exists())
    text = rb.read_text(encoding="utf-8") if rb.exists() else ""
    for needle in ("Needs attention", "To order", "Package prep", "Deposits", "AZ Studio",
                   "recieved rd", "recieved no rd", "35 days", "READY TO ORDER", "COLLECTED",
                   "Owner-only", "Daily checklist"):
        check(f"runbook covers: {needle}", needle in text)


def main():
    test_matrix()
    test_http()
    test_wiring()
    print("――――――――――――――――")
    if fails:
        print(f"FAILED {len(fails)}: " + "; ".join(fails))
        raise SystemExit(1)
    print("operator role: all checks passed")


if __name__ == "__main__":
    main()
