#!/usr/bin/env python3
"""
Self-checks for the per-field activity diffs.

Why this exists: a package on the Leluxe board once showed 50 units when it held
30, and there was no way to find out how. /api/leluxe/order logged a single flat
"saved" event with no field/old/new, so every quantity edit on that board was
invisible. leluxe_diff is po_diff's twin, and these checks pin the parts that
actually bite:

  * the live board stores 'Quantity ordered ' WITH a trailing space, so a
    literal key compare would read every save as a rename
  * ClickUp label values arrive as lists — the feed must never show raw JSON
  * po_diff only ever walked the NEW packages, so a deleted package/item was
    silent and moving a product between parcels logged a bogus one-sided "added"

    ./.venv/bin/python test_activity_leluxe_diff.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-lxdiff-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import activity  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def row(name="10 U.S. Polo Assn. Watch", status="ordered", due=None, **fields):
    return {"id": 7, "name": name, "status": status, "due_date": due,
            "data": {"fields": dict(fields)}}


def by_field(evs):
    return {e["field"]: (e["old"], e["new"]) for e in evs}


# --------------------------------------------------------------------------- #
def test_quantity_edit():
    old = row(**{"Quantity ordered": 30})
    new = row(**{"Quantity ordered": 50})
    evs = activity.leluxe_diff(old, new)
    check("a qty edit is ONE event", len(evs) == 1)
    check("  …carrying both sides (30 → 50)",
          by_field(evs).get("Quantity ordered") == ("30", "50"))

    check("an unchanged row emits nothing", activity.leluxe_diff(old, old) == [])
    check("30 vs '30' is not a change",
          activity.leluxe_diff(row(**{"Quantity ordered": 30}),
                               row(**{"Quantity ordered": "30"})) == [])


def test_trailing_space_key():
    """The live key is 'Quantity ordered ' — same field, different string."""
    old = {"id": 1, "name": "x", "data": {"fields": {"Quantity ordered ": 30}}}
    new = {"id": 1, "name": "x", "data": {"fields": {"Quantity ordered": 30}}}
    check("trailing-space key resolves to the same field",
          activity.leluxe_diff(old, new) == [])
    new2 = {"id": 1, "name": "x", "data": {"fields": {"Quantity ordered": 50}}}
    evs = activity.leluxe_diff(old, new2)
    check("  …and a real change through it is still caught",
          len(evs) == 1 and evs[0]["old"] == "30" and evs[0]["new"] == "50")
    check("  …labelled with the new row's spelling",
          evs[0]["field"] == "Quantity ordered")


def test_columns_and_values():
    evs = by_field(activity.leluxe_diff(
        row(name="10 Polo", status="ordered"),
        row(name="8 Polo", status="package")))
    check("a rename is logged", evs.get("name") == ("10 Polo", "8 Polo"))
    check("a status change is logged", evs.get("status") == ("ordered", "package"))

    evs = activity.leluxe_diff(row(**{"GASH STATUS": []}),
                               row(**{"GASH STATUS": [{"name": "CLEARED GASH"}]}))
    check("a ClickUp label list renders readably, never raw JSON",
          len(evs) == 1 and evs[0]["new"] == "CLEARED GASH" and evs[0]["old"] is None)

    d = activity.leluxe_diff(row(due="1753900000000"), row(due="1754900000000"))
    check("a due date shows as a date, not ms since epoch",
          len(d) == 1 and d[0]["new"].count("-") == 2 and len(d[0]["new"]) == 10)

    check("adding a field logs old=None",
          by_field(activity.leluxe_diff(row(), row(Brand="annie klein")))
          .get("Brand") == (None, "annie klein"))
    check("clearing a field logs new=None",
          by_field(activity.leluxe_diff(row(Brand="annie klein"), row()))
          .get("Brand") == ("annie klein", None))


def test_log_leluxe_diff_writes_events():
    activity._activity_file = lambda business_id=None: _TMP / "feed.jsonl"
    (_TMP / "feed.jsonl").write_text("", encoding="utf-8")

    n = activity.log_leluxe_diff(row(**{"Quantity ordered": 30}),
                                 row(**{"Quantity ordered": 50}), user="Qais")
    evs = activity.recent(10)
    check("one event written", n == 1 and len(evs) == 1)
    e = evs[0]
    check("  …as a leluxe 'set' on the row", e["entity"] == "leluxe"
          and e["action"] == "set" and e["entity_id"] == "7")
    check("  …with the user, field and both values",
          e["user"] == "Qais" and e["field"] == "Quantity ordered"
          and e["old"] == "30" and e["new"] == "50")

    n = activity.log_leluxe_diff(None, {"id": 9, "name": "new one"}, user="Qais")
    check("a row with no old side is ONE 'created', not N sets",
          n == 1 and activity.recent(1)[0]["action"] == "created")


# --------------------------------------------------------------------------- #
def po(*pkgs):
    return {"po_id": "PO-0001", "packages": list(pkgs)}


def pkg(no, *items, **kw):
    return dict({"package_no": no, "items": list(items)}, **kw)


def item(iid, title="Watch", **kw):
    return dict({"item_id": iid, "title": title}, **kw)


def actions(evs):
    return [(e.get("action"), e.get("detail")) for e in evs if e.get("action")]


def test_po_removals_and_moves():
    one, two = item("a1", "Cricut"), item("a2", "Polo")

    evs = activity.po_diff(po(pkg(1, one, two)), po(pkg(1, one)))
    check("a deleted item is logged (it used to be silent)",
          ("removed", "Polo") in actions(evs))

    evs = activity.po_diff(po(pkg(1, one), pkg(2, two)), po(pkg(1, one)))
    check("a deleted package is logged", ("removed", "Package 2") in actions(evs))
    check("  …along with the item that went with it",
          ("removed", "Polo") in actions(evs))

    evs = activity.po_diff(po(pkg(1, one, two)), po(pkg(1, one), pkg(2, two)))
    acts = actions(evs)
    check("moving an item between parcels reads as ONE move",
          ("moved", "Polo") in acts)
    check("  …not as an add", ("added", "Polo") not in acts)
    check("  …and not as a delete", ("removed", "Polo") not in acts)
    mv = next(e for e in evs if e.get("action") == "moved")
    check("  …naming both parcels",
          (mv["old"], mv["new"]) == ("Package 1", "Package 2"))

    evs = activity.po_diff(po(pkg(1, one)), po(pkg(1, one, two)))
    check("a genuinely new item is still an add", ("added", "Polo") in actions(evs))

    evs = activity.po_diff(po(pkg(1, item("a1", "Cricut", qty=1))),
                           po(pkg(2, item("a1", "Cricut", qty=5))))
    check("a field edit survives a move (items match globally now)",
          by_field(evs).get("qty") == (1, 5))

    check("customer_order_id is diffed (a silent reassignment before)",
          "customer_order_id" in by_field(activity.po_diff(
              po(pkg(1, item("a1", customer_order_id="OTL-1"))),
              po(pkg(1, item("a1", customer_order_id="OTL-2"))))))


def main():
    print("quantity edit:")
    test_quantity_edit()
    print("trailing-space field key:")
    test_trailing_space_key()
    print("columns and value rendering:")
    test_columns_and_values()
    print("log_leluxe_diff writes real events:")
    test_log_leluxe_diff_writes_events()
    print("po_diff removals and moves:")
    test_po_removals_and_moves()
    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}): {fails}")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
