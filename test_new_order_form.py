#!/usr/bin/env python3
"""
Self-checks: the easy "New order" form + a visible Add-product button.

The Purchases redesign made data entry slow (empty collapsed card + drawers,
Add-product buried in the ⋯ menu). The form collects an order's fields + products
and creates it in one POST; a visible ＋ Add product is back on packages.

    ./.venv/bin/python test_new_order_form.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-neworder-"))
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
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    co = client("otlo")
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    # 1) The form exists and the entry points open it (not the empty-collapsed card).
    check("newOrderModal + newOrderOpen exist",
          'id="newOrderModal"' in html and "function newOrderOpen(" in html)
    check("＋ New order opens the form", "function addPO(){ newOrderOpen(); }" in html)
    check("＋ Add order (topbar) opens the form", "function aoOpenForm(){ newOrderOpen(); }" in html)
    check("quote → Create order opens the form pre-filled", "newOrderOpen(items);" in html)

    # 2) Required-field validation (profile + customer + amount) before the POST.
    check("validation requires profile/customer/amount",
          'if(!(NO.profile||' in html and 'if(!(NO.customer||' in html and 'if(!(amount>0))' in html)
    check("save is blocked when required fields miss (returns before post)",
          "if(miss.length){ $(\"no_err\").textContent" in html)

    # 3) Product rows: repeatable, paste-link → ASIN, add/remove.
    check("product rows + add/remove", "function noAddRow(" in html and "function noDelRow(" in html)
    check("paste a link auto-extracts the ASIN",
          "const a=extractAsinClient(v); if(a) NO.rows[i].asin=a" in html)

    # 4) Visible ＋ Add product on packages (not only the ⋯ menu).
    check("package body has a visible Add-product button",
          "＋ إضافة منتج · Add product</button></div></div>" in html
          and "onclick=\"poAddItem('${p.po_id}',${pi})\">＋ إضافة منتج" in html)

    # 5) Backend already creates a full nested PO in one POST (the form's write path).
    r = co.post("/api/purchase", json={
        "amazon_order_number": "113-7", "ship_to": "Buyer", "profile_box": "B19",
        "total_usd": 125.5, "packages": [{"package_no": 1, "arrival": "", "tracking_number": "GWD1",
            "items": [
                {"title": "A", "asin": "B0AAAAAAA1", "qty": 2, "customer_name": "Buyer"},
                {"title": "B", "asin": "B0BBBBBBB2", "qty": 1, "customer_name": "Buyer"}]}]}).get_json()
    check("one POST creates the PO", r.get("ok") is True and r.get("how") == "created")
    po = purchases.find(purchases.load(), r["po_id"])
    check("fields saved", po["profile_box"] == "B19" and po["total_usd"] == 125.5
          and po["ship_to"] == "Buyer")
    pk = po["packages"][0]
    check("package + products saved",
          pk["tracking_number"] == "GWD1" and len(pk["items"]) == 2
          and pk["items"][0]["qty"] == 2 and pk["items"][0]["customer_name"] == "Buyer")

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
