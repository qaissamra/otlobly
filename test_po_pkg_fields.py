#!/usr/bin/env python3
"""
Self-checks: the two per-package fields the board writes — 🟢 rd_number (the
owner-typed refund number) and 📷 pkg_images (photos pasted onto ONE package).

Both live in the _norm_packages WHITELIST, which silently drops anything it
doesn't name, so the round-trip is the thing worth pinning. pkg_images has a
second rule: the board posts the WHOLE PO on every edit, so a client whose copy
predates a paste must not wipe the photos — a MISSING key keeps what's on disk,
an explicit list still wins (that's how a delete goes through).

    ./.venv/bin/python test_po_pkg_fields.py
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="otlobly-pkgfields-")
os.environ["OTLOBLY_DATA_DIR"] = _TMP
os.environ["OTLOBLY_DB"] = os.path.join(_TMP, "t.db")

import purchases  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def _po(packages):
    return {"po_id": None, "amazon_order_number": "111-TEST", "ship_to": "Tester",
            "packages": packages}


def main():
    db = {"purchase_orders": [], "seq": 0}

    print("— 🟢 RD number —")
    po, _ = purchases.save_full(db, _po([
        {"package_no": 1, "tracking_number": "GWD100000001", "rd_number": " RD-77 ", "items": []},
        {"package_no": 2, "tracking_number": "GWD100000002", "items": []},
    ]), [])
    check("typed RD survives the save, trimmed",
          po["packages"][0]["rd_number"] == "RD-77")
    check("a package with no RD stores None, never ''",
          po["packages"][1]["rd_number"] is None)

    po["packages"][0]["rd_number"] = ""
    po2, _ = purchases.save_full(db, po, [])
    check("clearing the field clears the value",
          po2["packages"][0]["rd_number"] is None)

    print("— 📷 package photos —")
    po2["packages"][0]["pkg_images"] = ["pkg-PO-0001-1-aaaa1111.png"]
    po3, _ = purchases.save_full(db, po2, [])
    check("photos survive the whole-PO save",
          po3["packages"][0]["pkg_images"] == ["pkg-PO-0001-1-aaaa1111.png"])

    # the race that matters: a stale client re-posts the package WITHOUT the key
    stale = {"po_id": po3["po_id"], "amazon_order_number": "111-TEST", "ship_to": "Tester",
             "packages": [
                 {"package_no": 1, "tracking_number": "GWD100000001", "items": []},
                 {"package_no": 2, "tracking_number": "GWD100000002", "items": []}]}
    po4, _ = purchases.save_full(db, stale, [])
    check("a stale client that omits the key does NOT wipe the photos",
          po4["packages"][0]["pkg_images"] == ["pkg-PO-0001-1-aaaa1111.png"])
    check("...and a package that never had photos stays empty",
          po4["packages"][1]["pkg_images"] == [])

    # an EXPLICIT list still wins — otherwise a delete could never go through
    po4["packages"][0]["pkg_images"] = []
    po5, _ = purchases.save_full(db, po4, [])
    check("an explicit empty list deletes (a delete must not be ignored)",
          po5["packages"][0]["pkg_images"] == [])

    # photos follow package_no, not array position: dropping package 1 must not
    # hand its photos to the package that slides into index 0
    po5["packages"][0]["pkg_images"] = ["a.png"]
    po5["packages"][1]["pkg_images"] = ["b.png"]
    po6, _ = purchases.save_full(db, po5, [])
    shifted = {"po_id": po6["po_id"], "amazon_order_number": "111-TEST", "ship_to": "Tester",
               "packages": [{"package_no": 2, "tracking_number": "GWD100000002", "items": []}]}
    po7, _ = purchases.save_full(db, shifted, [])
    check("photos are matched by package_no, not by index",
          len(po7["packages"]) == 1 and po7["packages"][0]["pkg_images"] == ["b.png"])

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
