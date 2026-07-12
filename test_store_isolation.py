#!/usr/bin/env python3
"""
Self-checks for per-tenant JSON-store isolation (Tatabu Phase 4).

The purchases, trash, and activity stores are files. This proves each business
reads/writes its OWN file: Otlobly (business #1) keeps purchases.json / trash.json /
activity.jsonl, a broker gets purchases_b<id>.json etc., and neither ever sees the
other's rows.

    ./.venv/bin/python test_store_isolation.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-store-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)          # all stores live under here
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import activity    # noqa: E402
import db          # noqa: E402
import purchases   # noqa: E402
import trash       # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def add_po(ref):
    p = purchases.load(); p["purchase_orders"].append({"po_id": "PO-0001", "amazon_order_number": ref}); purchases.save(p)


def pos():
    return [po["amazon_order_number"] for po in purchases.load()["purchase_orders"]]


def add_trash(label):
    t = trash.load(); trash.add(t, "order", label, {"order_id": "OTL-x"}); trash.save(t)


def labels_trash():
    return [r["label"] for r in trash.load().get("trash", [])]


def act_labels():
    return [e["label"] for e in activity.recent()]


def main():
    db.init_db()

    # --- Otlobly (business #1) ---
    db.set_current_business(1)
    add_po("OTLO-1"); add_trash("Otlobly order"); activity.log("created", "order", "OTL-1", "Otlobly order")

    # --- broker: starts EMPTY (own files), can't see Otlobly's ---
    bid = db.create_business("Broker Co")
    db.set_current_business(bid)
    check("broker purchases start empty", pos() == [])
    check("broker trash starts empty", labels_trash() == [])
    check("broker activity starts empty", act_labels() == [])
    add_po("BRK-1"); add_trash("Broker order"); activity.log("created", "order", "OTL-9", "Broker order")
    check("broker sees only its PO", pos() == ["BRK-1"])
    check("broker sees only its trash", labels_trash() == ["Broker order"])
    check("broker sees only its activity", act_labels() == ["Broker order"])

    # --- Otlobly unchanged: still only its own rows ---
    db.set_current_business(1)
    check("Otlobly still sees only its PO", pos() == ["OTLO-1"])
    check("Otlobly still sees only its trash", labels_trash() == ["Otlobly order"])
    check("Otlobly still sees only its activity", act_labels() == ["Otlobly order"])

    # --- files are physically separate ---
    check("Otlobly file is the base purchases.json", (_TMP / "purchases.json").exists())
    check("broker file is purchases_b<id>.json", (_TMP / f"purchases_b{bid}.json").exists())
    check("broker trash + activity files exist",
          (_TMP / f"trash_b{bid}.json").exists() and (_TMP / f"activity_b{bid}.jsonl").exists())

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
