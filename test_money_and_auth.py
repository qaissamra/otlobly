#!/usr/bin/env python3
"""
Self-checks for the money math and the role/permission matrix (audit D-2).

Covers the paths that move money or gate access — the ones a silent regression
would hurt most:
  * pricing.apply_markup / price_from_checkout   (what we charge the customer)
  * db deposit ledger netting                    (deposits + collects − refunds)
  * auth role → permission matrix                (who can see money / edit / admin)

    ./.venv/bin/python test_money_and_auth.py     # prints per-check OK/FAIL, exits non-zero on any FAIL

Uses a throwaway temp DB; never touches real data.
"""

import os
import tempfile
from pathlib import Path

# Point db.py at a temp file BEFORE importing it (DB_FILE is read at import).
_TMP = Path(tempfile.mkdtemp(prefix="otlobly-mtest-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import auth      # noqa: E402
import db        # noqa: E402
import pricing   # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'XX '} {name:44} got={got!r} want={want!r}")
    if not ok:
        fails.append(name)


def test_markup():
    print("MARKUP (explicit 10% config, independent of the live config.json):")
    cfg10 = {"pricing": {"markup_pct": 0.10}}
    check("apply_markup(100) = +10%", pricing.apply_markup(100, cfg10), 110.0)
    check("apply_markup(49.99) rounds to cent", pricing.apply_markup(49.99, cfg10), 54.99)
    check("apply_markup(None) passes through", pricing.apply_markup(None, cfg10), None)
    check("checkout order_total wins",
          pricing.price_from_checkout({"order_total_usd": 100.0}, cfg10), 110.0)
    check("checkout falls back to summing parts",
          pricing.price_from_checkout(
              {"subtotal_usd": 80, "shipping_usd": 15, "import_fees_usd": 5}, cfg10), 110.0)
    check("checkout None = None", pricing.price_from_checkout(None, cfg10), None)


def test_deposit_netting():
    print("DEPOSIT LEDGER (net = deposits + collects − refunds, USD):")
    db.init_db()
    oc = "OTL-9001"
    for kind, usd in (("deposit", 50.0), ("collect", 20.0), ("refund", 10.0)):
        db.add_payment({"order_code": oc, "kind": kind, "amount_usd": usd,
                        "currency": "USD", "amount_entered": usd, "fx_rate": 1.0})
    check("50 deposit + 20 collect − 10 refund = 60", db.deposit_total_for_order(oc), 60.0)
    check("unknown order nets to 0", db.deposit_total_for_order("OTL-0000"), 0.0)
    # a lone refund must go negative (a credit owed back to the customer)
    db.add_payment({"order_code": "OTL-9002", "kind": "refund", "amount_usd": 15.0,
                    "currency": "USD", "amount_entered": 15.0, "fx_rate": 1.0})
    check("lone refund is negative", db.deposit_total_for_order("OTL-9002"), -15.0)
    # drift proof: 100 one-cent deposits net to EXACTLY 1.00 (float summation gives
    # 0.9999999999999999 — this is the whole point of the Decimal ledger).
    for _ in range(100):
        db.add_payment({"order_code": "OTL-9003", "kind": "deposit", "amount_usd": 0.01,
                        "currency": "USD", "amount_entered": 0.01, "fx_rate": 1.0})
    check("100 × $0.01 nets to exactly 1.00", db.deposit_total_for_order("OTL-9003"), 1.00)


def _user(role):
    return auth.User({"id": 1, "username": "u", "role": role, "name": "U"})


def test_permissions():
    print("ROLE → PERMISSION MATRIX (access control):")
    admin, sales, ful = _user("admin"), _user("sales"), _user("fulfillment")
    # admin can do everything sensitive
    check("admin has view_money", admin.has("view_money"), True)
    check("admin has manage_users", admin.has("manage_users"), True)
    check("admin has admin_actions", admin.has("admin_actions"), True)
    # sales: prices + leads, but NOT editing, P&L, cost, or user admin
    check("sales has view_money", sales.has("view_money"), True)
    check("sales CANNOT edit_order", sales.has("edit_order"), False)
    check("sales CANNOT view_pnl", sales.has("view_pnl"), False)
    check("sales CANNOT manage_users", sales.has("manage_users"), False)
    check("sales CANNOT view_cost", sales.has("view_cost"), False)
    # fulfillment: ships, but never sees money
    check("fulfillment has edit_fulfillment", ful.has("edit_fulfillment"), True)
    check("fulfillment CANNOT view_money", ful.has("view_money"), False)
    check("fulfillment CANNOT edit_order", ful.has("edit_order"), False)
    # an unknown role is powerless (fail-closed)
    check("unknown role has no perms", _user("ghost").has("view_orders"), False)


def main():
    test_markup()
    test_deposit_netting()
    test_permissions()
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
