#!/usr/bin/env python3
"""
Self-checks: the employee money boundary + custom fields v1.

A fulfillment hire packs and tracks but must never see COGS: /api/purchases and
/api/purchase strip the paid Amazon totals + per-item cost estimates
(money:false), /api/incart strips cost/profit AND sale totals, /api/po_image
(checkout screenshots = prices) is 403, and the Settings snapshot loses
markup/fx/clearance. Admin payloads are untouched. Custom fields (Settings →
custom_fields.po) round-trip definitions and PO values.

    ./.venv/bin/python test_role_money.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-rolemoney-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402
import purchases       # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def main():
    db.init_db()
    db.create_user("rm-adm", auth.hash_pw("s1"), "admin", "Owner", business_id=1)
    db.create_user("rm-ful", auth.hash_pw("s1"), "fulfillment", "Packer", business_id=1)
    adm, ful = client("rm-adm"), client("rm-ful")

    # a PO with money everywhere: paid total + per-item cost estimate
    r = adm.post("/api/purchase", json={
        "amazon_order_number": "113-9", "ship_to": "Buyer", "profile_box": "B19",
        "total_usd": 250.5, "total_aed": 920.0,
        "packages": [{"package_no": 1, "tracking_number": "GWDROLE1",
                      "items": [{"title": "Watch", "asin": "B0ROLETEST", "qty": 2,
                                 "customer_name": "Buyer"}]}]}).get_json()
    check("PO created", r.get("ok") is True)
    po_id = r["po_id"]
    pdb = purchases.load()
    purchases.find(pdb, po_id)["packages"][0]["items"][0]["est_cost_usd"] = 42.5
    purchases.save(pdb)

    # admin sees everything
    d = adm.get("/api/purchases").get_json()
    pa = next(p for p in d["purchase_orders"] if p["po_id"] == po_id)
    check("admin sees paid total + est cost", d.get("money") is True
          and pa["total_usd"] == 250.5
          and pa["packages"][0]["items"][0]["est_cost_usd"] == 42.5)

    # fulfillment: all COGS stripped
    d = ful.get("/api/purchases").get_json()
    pf = next(p for p in d["purchase_orders"] if p["po_id"] == po_id)
    check("fulfillment list is money-free", d.get("money") is False
          and pf["total_usd"] is None and pf["total_aed"] is None
          and pf.get("money") is False
          and pf["packages"][0]["items"][0]["est_cost_usd"] is None)
    one = ful.get(f"/api/purchase?id={po_id}").get_json()
    check("single-PO GET is money-free too", one["total_usd"] is None
          and one["packages"][0]["items"][0]["est_cost_usd"] is None)
    check("checkout screenshots are 403 for fulfillment",
          ful.get("/api/po_image?file=x.png").status_code == 403)

    # in-cart: no cost/profit, no sale totals for fulfillment
    d = ful.get("/api/incart").get_json()
    check("incart money-free for fulfillment", d.get("cost_usd") is None
          and d.get("profit_usd") is None and d.get("total_usd") is None
          and d.get("money") is False)
    d = adm.get("/api/incart").get_json()
    check("incart intact for admin", d.get("cost_usd") is not None
          and d.get("profit_usd") is not None)

    # settings snapshot trimmed for fulfillment
    s = ful.get("/api/settings").get_json()
    check("settings GET hides pricing config from fulfillment",
          "markup_pct" not in s and "clearance" not in s and "alerts" not in s
          and "employee_status_map" in s and "custom_fields" in s)
    s = adm.get("/api/settings").get_json()
    check("settings GET full for admin", "markup_pct" in s and "clearance" in s)
    check("employee status map has defaults with picks",
          any(r.get("pick") for r in s["employee_status_map"])
          and any(r["status"] == "recieved no rd" for r in s["employee_status_map"]))

    # custom fields: define → snapshot → value round-trip on the PO
    r = adm.post("/api/settings", json={"custom_fields": {"po": [
        {"label": "Supplier", "type": "select", "options": ["Amazon", "eBay"]},
        {"label": "Batch no", "type": "number"}]}}).get_json()
    cf = r["settings"]["custom_fields"]["po"]
    check("custom fields saved with slug keys", len(cf) == 2
          and cf[0]["key"] == "supplier" and cf[0]["options"] == ["Amazon", "eBay"]
          and cf[1]["key"] == "batch_no" and cf[1]["type"] == "number")
    full_po = adm.get(f"/api/purchase?id={po_id}").get_json()
    full_po["custom"] = {"supplier": "Amazon", "batch_no": 7}
    r = adm.post("/api/purchase", json=full_po).get_json()
    check("PO save keeps custom values", r.get("ok") is True)
    back = adm.get(f"/api/purchase?id={po_id}").get_json()
    check("custom values round-trip",
          back.get("custom", {}).get("supplier") == "Amazon"
          and back.get("custom", {}).get("batch_no") == 7)
    # a POST without the custom dict must not wipe stored values
    full_po2 = {k: v for k, v in back.items() if k != "custom"}
    adm.post("/api/purchase", json=full_po2)
    back2 = adm.get(f"/api/purchase?id={po_id}").get_json()
    check("custom values survive a custom-less save",
          back2.get("custom", {}).get("supplier") == "Amazon")

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
