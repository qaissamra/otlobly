#!/usr/bin/env python3
"""
Self-checks for the Leluxe page + ClickUp mirror (leluxe.py).

Proves: the field codecs round-trip every ClickUp type; pull upserts by task id
with local-dirty rows winning; the push claim is single-winner; a parent is
created before its items and the returned task id is persisted BEFORE any field
call (the duplicate-safety invariant); unchanged rows push zero HTTP calls; and
the endpoints are admin-only + Otlobly-only.

    ./.venv/bin/python test_leluxe.py
"""

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-leluxe-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)      # keep config.json/images off the repo
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["CLICKUP_API_TOKEN"] = "test-token"
os.environ["LELUXE_PACE"] = "0"
os.environ["LELUXE_PUSH_DISABLED"] = "1"        # flipped to "0" around push tests

import cfg      # noqa: E402
import db       # noqa: E402
import leluxe   # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


SCHEMA = {
    "statuses": [{"status": "order number", "color": "#f5cf78", "orderindex": 0, "type": "open"},
                 {"status": "in clearance", "color": "#0f9d9f", "orderindex": 5, "type": "unstarted"},
                 {"status": "picked up by ger", "color": "#f76808", "orderindex": 18, "type": "unstarted"},
                 {"status": "sent rd", "color": "#3dcc4e", "orderindex": 33, "type": "done"}],
    "fields": {
        "NAME": {"id": "f-name", "type": "drop_down",
                 "options": [{"id": "opt-a", "name": "A", "orderindex": 0, "color": "#f00"},
                             {"id": "opt-b5", "name": "B5", "orderindex": 37, "color": "#0f0"}]},
        "Quantity ordered ": {"id": "f-qty", "type": "number", "options": []},
        "Total Amount": {"id": "f-amt", "type": "currency", "options": []},
        "Tracking Number": {"id": "f-trk", "type": "short_text", "options": []},
        "ASIN": {"id": "f-asin", "type": "short_text", "options": []},
        "gash date": {"id": "f-gd", "type": "date", "options": []},
        # like live: GERZIM DELIVERED / GAASH-leg options exist; تم التسليم, SMS
        # and STILL NOT ARRIVED do NOT (unmapped stages must be skipped)
        "GASH STATUS": {"id": "f-gs", "type": "drop_down",
                        "options": [{"id": "opt-gzd", "name": "GERZIM DELIVERED", "orderindex": 6, "color": "#b6b6ff"},
                                    {"id": "opt-pug", "name": "Picked up by Gerizim", "orderindex": 8, "color": "#edadc8"},
                                    {"id": "opt-arr", "name": "ARIIVED Destination", "orderindex": 1, "color": "#04A9F4"},
                                    {"id": "opt-cid", "name": " customer ID", "orderindex": 7, "color": "#f76808"},
                                    {"id": "opt-clr", "name": "CLEARED GASH", "orderindex": 3, "color": "#1bbc9c"}]},
        "ADDRESS ISSUE": {"id": "f-ai", "type": "checkbox", "options": []},
        "Quantity": {"id": "f-ql", "type": "labels",
                     "options": [{"id": "lab-1", "name": "Correct amount", "orderindex": 1, "color": None}]},
    },
}


def setup_config():
    config = cfg.load()
    cfg.set_path(config, "leluxe.list_id", "L1")
    cfg.set_path(config, "leluxe.schema", SCHEMA)
    cfg.save(config)


def codecs():
    f = SCHEMA["fields"]
    # decode: ClickUp sends dropdowns as orderindex ints, sometimes option uuids
    check("decode dropdown by orderindex", leluxe.decode_value(f["NAME"], 37) == "B5")
    check("decode dropdown by uuid", leluxe.decode_value(f["NAME"], "opt-a") == "A")
    check("decode labels uuid list", leluxe.decode_value(f["Quantity"], ["lab-1"]) == ["Correct amount"])
    check("decode checkbox 'true'", leluxe.decode_value(f["ADDRESS ISSUE"], "true") is True)
    check("decode currency string", leluxe.decode_value(f["Total Amount"], "76.99") == 76.99)
    check("decode date stays ms string", leluxe.decode_value(f["gash date"], "1778979600000") == "1778979600000")
    # encode: names → uuids, dates → ms, checkbox → bool
    check("encode dropdown name→uuid", leluxe.encode_value(f["NAME"], "B5") == (True, "opt-b5"))
    check("encode dropdown case-blind", leluxe.encode_value(f["NAME"], "b5") == (True, "opt-b5"))
    check("encode unknown option fails", leluxe.encode_value(f["NAME"], "ZZZ")[0] is False)
    check("encode labels names→uuids", leluxe.encode_value(f["Quantity"], ["Correct amount"]) == (True, ["lab-1"]))
    ok, ms = leluxe.encode_value(f["gash date"], "2026-07-01")
    check("encode ISO date → ms", ok and isinstance(ms, int) and ms > 1_700_000_000_000)
    check("date ms round-trips", leluxe.to_ms(str(ms)) == ms)
    check("encode number from string", leluxe.encode_value(f["Quantity ordered "], "10") == (True, 10.0))


def _task(tid, name, status, parent=None, updated="100", fields=()):
    return {"id": tid, "parent": parent, "name": name,
            "status": {"status": status}, "due_date": None,
            "date_created": "1", "date_updated": updated,
            "tags": [{"name": "urgent"}] if tid == "P1" else [],
            "markdown_description": "hello" if not parent else "",
            "custom_fields": [{"id": SCHEMA["fields"][k]["id"], "name": k,
                               "type": SCHEMA["fields"][k]["type"], "value": v}
                              for k, v in fields]}


def pull_and_dirty_wins():
    r = leluxe.upsert_from_clickup(_task("P1", "Order # 111", "order number",
                                         fields=[("NAME", 37), ("Total Amount", "76.99")]), SCHEMA)
    check("first pull creates", r == "created")
    r = leluxe.upsert_from_clickup(_task("I1", "10 Watch", "sent rd", parent="P1",
                                         fields=[("Quantity ordered ", "10")]), SCHEMA)
    check("subtask creates as item", r == "created")
    leluxe._relink()
    orders, orphans = leluxe.list_tree()
    check("tree links leaf item under order", len(orders) == 1 and len(orders[0]["items"]) == 1
          and not orphans)
    p = orders[0]
    check("pulled fields decoded (NAME=B5)", p["data"]["fields"].get("NAME") == "B5")
    check("pulled tags kept", p["data"]["tags"] == ["urgent"])
    check("pulled row lands synced", p["sync_state"] == "synced")

    check("re-pull unchanged is a no-op",
          leluxe.upsert_from_clickup(_task("P1", "Order # 111", "order number",
                                           fields=[("NAME", 37)]), SCHEMA) == "unchanged")
    r = leluxe.upsert_from_clickup(_task("P1", "Order # 111 renamed", "sent rd",
                                         updated="200", fields=[("NAME", 0)]), SCHEMA)
    check("re-pull with newer cu_updated updates", r == "updated"
          and leluxe.get_by_task("P1")["name"] == "Order # 111 renamed")

    # local edit → dirty → a ClickUp-side change must NOT clobber it
    row, err = leluxe.save_row({"id": p["id"], "kind": "parent", "name": "LOCAL EDIT",
                                "status": "order number",
                                "fields": {"NAME": "A"}})
    check("save_row marks dirty", not err and row["sync_state"] == "dirty")
    r = leluxe.upsert_from_clickup(_task("P1", "CLICKUP EDIT", "sent rd",
                                         updated="300"), SCHEMA)
    check("pull skips dirty rows (local wins)", r == "skipped_dirty"
          and leluxe.get_by_task("P1")["name"] == "LOCAL EDIT")


def relink_reclaims_ghost_packages():
    """A package that lost its last product (in ClickUp or here) has no children
    to prove it's a package by depth, so a pull's blind kind= overwrite
    (upsert_from_clickup) permanently demotes it to a fake 'item' — invisible to
    regroup_order/_sweep_order_packages. _relink() must reclaim it by name
    ("📦 …" is a naming convention only OUR OWN code ever uses), and a real
    product must NEVER be reclassified just because a name loosely resembles
    one — only the exact "📦 " prefix qualifies. Once reclaimed, the ghost
    flows through the SAME regroup/sweep policy as any other package: swept if
    untracked, kept if it still carries a tracking number (a real parcel)."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    leluxe.upsert_from_clickup(_task("GO", "Order # GHOST", "order number",
                                     fields=[("NAME", 37)]), SCHEMA)
    leluxe.upsert_from_clickup(_task("GG1", "📦 no tracking", "sent rd",
                                     parent="GO"), SCHEMA)
    leluxe.upsert_from_clickup(_task("GG2", "📦 GWD-GHOST", "sent rd", parent="GO",
                                     fields=[("Tracking Number", "GWD-GHOST")]), SCHEMA)
    leluxe.upsert_from_clickup(_task("GP", "5 Real Watch, not a package", "sent rd",
                                     parent="GO"), SCHEMA)
    leluxe.upsert_from_clickup(_task("GPK", "📦 GWD-REAL", "sent rd", parent="GO",
                                     fields=[("Tracking Number", "GWD-REAL")]), SCHEMA)
    leluxe.upsert_from_clickup(_task("GC", "2 Real product under a real package",
                                     "sent rd", parent="GPK",
                                     fields=[("Quantity ordered ", "2")]), SCHEMA)
    leluxe._relink()
    check("childless untracked ghost reclaimed to package",
          leluxe.get_by_task("GG1")["kind"] == "package")
    check("childless TRACKED ghost also reclaimed to package",
          leluxe.get_by_task("GG2")["kind"] == "package")
    check("a real product is NEVER reclassified by name",
          leluxe.get_by_task("GP")["kind"] == "item")
    check("a real package with a live child still promotes by depth (unaffected)",
          leluxe.get_by_task("GPK")["kind"] == "package"
          and leluxe.get_by_task("GC")["kind"] == "item")

    order_id = leluxe.get_by_task("GO")["id"]
    out = leluxe.regroup_order(order_id)
    check("reclaimed untracked ghost is swept like any dead package",
          leluxe.get_by_task("GG1")["deleted"] == 1)
    check("reclaimed TRACKED ghost is kept — a real parcel awaiting products",
          leluxe.get_by_task("GG2")["deleted"] == 0)
    check("real package + its product untouched",
          leluxe.get_by_task("GPK")["deleted"] == 0
          and leluxe.get_by_task("GC")["deleted"] == 0)
    check("real loose product untouched", leluxe.get_by_task("GP")["deleted"] == 0)


def save_row_validation():
    row, err = leluxe.save_row({"kind": "parent", "name": "X", "status": "nope"})
    check("unknown status rejected", row is None and "status" in (err or ""))
    row, err = leluxe.save_row({"kind": "parent", "name": "X", "fields": {"BAD FIELD": 1}})
    check("unknown field rejected", row is None and "BAD FIELD" in (err or ""))
    row, err = leluxe.save_row({"kind": "parent", "name": "X",
                                "fields": {"NAME": "ZZZ"}})
    check("unknown dropdown option rejected at save", row is None and "ZZZ" in (err or ""))
    row, err = leluxe.save_row({"kind": "item", "name": "orphan item"})
    check("item without parent rejected", row is None)


def claim_race():
    row, _ = leluxe.save_row({"kind": "parent", "name": "Order # claim-test"})
    with db.connect() as c:
        first = c.execute("UPDATE leluxe_orders SET sync_state='pushing', "
                          "sync_claimed_at=datetime('now') WHERE id=? AND sync_state='dirty'",
                          (row["id"],)).rowcount
        second = c.execute("UPDATE leluxe_orders SET sync_state='pushing', "
                           "sync_claimed_at=datetime('now') WHERE id=? AND sync_state='dirty'",
                           (row["id"],)).rowcount
    check("claim wins exactly once", first == 1 and second == 0)
    with db.connect() as c:                       # put it back for the push test
        c.execute("UPDATE leluxe_orders SET sync_state='dirty' WHERE id=?", (row["id"],))
    leluxe.soft_delete(row["id"])


CALLS = []


def fake_http(url, method="GET", body=None, _retried=False):
    CALLS.append((method, url, body))
    if method == "POST" and url.endswith("/list/L1/task"):
        return 200, {"id": f"T{sum(1 for m, u, _ in CALLS if m == 'POST' and u.endswith('/list/L1/task'))}"}
    return 200, {}


def push_ordering():
    # fresh pair: parent + item, both dirty
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")   # isolate this scenario
    parent, _ = leluxe.save_row({"kind": "parent", "name": "Order # push-1",
                                 "status": "order number", "tags": ["urgent"],
                                 "fields": {"NAME": "B5", "Total Amount": "76.99"}})
    item, _ = leluxe.save_row({"kind": "item", "name": "10 Watch",
                               "parent_local_id": parent["id"],
                               "fields": {"Quantity ordered ": "10",
                                          "gash date": "2026-07-01"}})
    real_http = leluxe._http
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        stats = leluxe.run_push_pass()
    finally:
        leluxe._http = real_http
        os.environ["LELUXE_PUSH_DISABLED"] = "1"
    check("both rows pushed in one pass", stats.get("pushed") == 2 and not stats.get("failed"))

    creates = [(i, b) for i, (m, u, b) in enumerate(CALLS)
               if m == "POST" and u.endswith("/list/L1/task")]
    check("two creates happened", len(creates) == 2)
    check("parent created before item", creates[0][1]["name"] == "Order # push-1"
          and creates[1][1]["name"] == "10 Watch")
    check("item create carries parent task id", creates[1][1].get("parent") == "T1")
    check("create carries status/tags/description",
          creates[0][1].get("status") == "order number"
          and creates[0][1].get("tags") == ["urgent"]
          and "markdown_description" in creates[0][1])
    first_field_ix = next(i for i, (m, u, b) in enumerate(CALLS) if "/field/" in u)
    check("task id persisted before field calls",
          creates[0][0] < first_field_ix
          and leluxe.get_row(parent["id"])["clickup_task_id"] == "T1")
    fname_calls = [(u.rsplit("/", 1)[-1], b["value"]) for m, u, b in CALLS if "/field/" in u]
    check("dropdown pushed as option uuid", ("f-name", "opt-b5") in fname_calls)
    check("currency pushed as float", ("f-amt", 76.99) in fname_calls)
    check("date pushed as int ms", any(f == "f-gd" and isinstance(v, int) for f, v in fname_calls))
    p2 = leluxe.get_row(parent["id"])
    check("row lands synced with pushed snapshot", p2["sync_state"] == "synced"
          and p2["data"]["pushed"]["fields"].get("NAME") == "B5")

    # unchanged re-save → dirty again, but the push makes ZERO http calls
    leluxe.save_row({"id": parent["id"], "kind": "parent", "name": "Order # push-1",
                     "status": "order number", "tags": ["urgent"],
                     "description": p2["data"].get("description", ""),
                     "fields": {"NAME": "B5", "Total Amount": "76.99"}})
    n_before = len(CALLS)
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        stats = leluxe.run_push_pass()
    finally:
        leluxe._http = real_http
        os.environ["LELUXE_PUSH_DISABLED"] = "1"
    check("unchanged row pushes zero HTTP calls",
          stats.get("pushed") == 1 and len(CALLS) == n_before)
    check("unchanged row back to synced",
          leluxe.get_row(parent["id"])["sync_state"] == "synced")


def push_3tier():
    """order → package → product: shallow pushes before deep, and each child's
    create carries its immediate parent's task id (nested subtasks)."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    CALLS.clear()
    order, _ = leluxe.save_row({"kind": "order", "name": "Order # 3T",
                                "status": "order number", "fields": {"NAME": "B5"}})
    pkg, _ = leluxe.save_row({"kind": "package", "name": "📦 GWD1",
                              "parent_local_id": order["id"],
                              "fields": {"Tracking Number": "GWD1"}})
    item, _ = leluxe.save_row({"kind": "item", "name": "10 Watch",
                               "parent_local_id": pkg["id"],
                               "fields": {"Quantity ordered ": "10"}})
    check("package stores tracking_number", leluxe.get_row(pkg["id"])["data"].get("tracking_number") == "GWD1")
    real = leluxe._http
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        # three passes: order lands (T1), then package (T2), then item (T3)
        for _ in range(3):
            leluxe.run_push_pass()
    finally:
        leluxe._http = real
        os.environ["LELUXE_PUSH_DISABLED"] = "1"
    o, p, it = (leluxe.get_row(order["id"]), leluxe.get_row(pkg["id"]),
                leluxe.get_row(item["id"]))
    check("all three tiers synced", o["sync_state"] == p["sync_state"] == it["sync_state"] == "synced")
    creates = [b for m, u, b in CALLS if m == "POST" and u.endswith("/list/L1/task")]
    check("three creates, shallow→deep",
          [c["name"] for c in creates] == ["Order # 3T", "📦 GWD1", "10 Watch"])
    check("package create parents the order", creates[1].get("parent") == o["clickup_task_id"])
    check("item create parents the package", creates[2].get("parent") == p["clickup_task_id"])
    check("nested ids distinct", len({o["clickup_task_id"], p["clickup_task_id"],
                                      it["clickup_task_id"]}) == 3)
    orders, _ = leluxe.list_tree()
    o3 = next(x for x in orders if x["id"] == order["id"])
    check("tree nests item under package under order",
          len(o3["packages"]) == 1 and len(o3["packages"][0]["items"]) == 1)


SRC_TASKS = []


def fake_src_http(url, method="GET", body=None, _retried=False):
    if "/list/SRC/task" in url:
        return 200, {"tasks": SRC_TASKS, "last_page": True}
    return fake_http(url, method, body)


def migrate_grouping():
    """Migration reads AZ (2) (SRC), regroups each order's items into packages
    keyed by Tracking Number, and never writes the source."""
    global SRC_TASKS
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    CALLS.clear()
    config = cfg.load()
    cfg.set_path(config, "leluxe.source_list_id", "SRC")
    cfg.set_path(config, "leluxe.list_id", "L1")
    cfg.save(config)
    SRC_TASKS = [
        _task("O1", "Order # SRC-1", "order number", fields=[("NAME", 37)]),
        _task("A1", "5 Watch A", "sent rd", parent="O1",
              fields=[("Tracking Number", "GWD-AAA"), ("ASIN", "B001")]),
        _task("A2", "3 Watch B", "sent rd", parent="O1",
              fields=[("Tracking Number", "GWD-AAA"), ("ASIN", "B002")]),
        _task("A3", "2 Wallet C", "shipped", parent="O1",
              fields=[("Tracking Number", "GWD-BBB"), ("ASIN", "B003")]),
    ]
    for t in SRC_TASKS:
        t["date_created"] = "1780000000000"
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        res = leluxe.migrate_from_source("2026-01-01", limit=10, config=config)
    finally:
        leluxe._http = real
    check("migrate reports 1 order / 2 packages / 3 items",
          res.get("orders") == 1 and res.get("packages") == 2 and res.get("items") == 3)
    check("migration only GET the source (no writes to SRC)",
          not any("/list/SRC" in u and m != "GET" for m, u, b in CALLS))
    orders, _ = leluxe.list_tree()
    o = next(x for x in orders if x["name"] == "Order # SRC-1")
    pkgs = {p["data"].get("tracking_number"): p for p in o["packages"]}
    check("grouped into GWD-AAA (2 items) + GWD-BBB (1 item)",
          set(pkgs) == {"GWD-AAA", "GWD-BBB"}
          and len(pkgs["GWD-AAA"]["items"]) == 2 and len(pkgs["GWD-BBB"]["items"]) == 1)
    check("item keeps ASIN from source", pkgs["GWD-BBB"]["items"][0]["data"]["fields"].get("ASIN") == "B003")
    check("order remembers its source task id",
          o["data"].get("source_task_id") == "O1")
    # re-run = idempotent (already-seen orders skipped)
    leluxe._http = fake_src_http
    try:
        res2 = leluxe.migrate_from_source("2026-01-01", limit=10, config=config)
    finally:
        leluxe._http = real
    check("re-migrate skips already-imported", res2.get("orders") == 0 and res2.get("skipped") == 1)


def image_cache():
    """fetch_item_image caches the Amazon URL by ASIN and doesn't refetch."""
    import amazon_import
    calls = {"n": 0}
    real = amazon_import.import_product
    amazon_import.import_product = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1)
                                                    or {"image": "http://img/x.png"})
    try:
        order, _ = leluxe.save_row({"kind": "order", "name": "Order # IMG"})
        it, _ = leluxe.save_row({"kind": "item", "name": "img item",
                                 "parent_local_id": order["id"], "fields": {"ASIN": "B0IMG"}})
        img1 = leluxe.fetch_item_image(it["id"])
        img2 = leluxe.fetch_item_image(it["id"])
        check("image fetched + cached by ASIN", img1 == "http://img/x.png"
              and leluxe.get_row(it["id"])["data"].get("image") == "http://img/x.png")
        check("second fetch uses cache (no refetch)", img2 == "http://img/x.png" and calls["n"] == 1)
        # editor's one-click flow: pass a NEW ASIN → it's written to the item
        # (marks dirty so it also syncs to ClickUp) and the image is refetched
        it2, _ = leluxe.save_row({"kind": "item", "name": "no-asin item",
                                  "parent_local_id": order["id"]})
        with db.connect() as c:                 # pretend it already synced
            c.execute("UPDATE leluxe_orders SET sync_state='synced' WHERE id=?", (it2["id"],))
        img3 = leluxe.fetch_item_image(it2["id"], asin="B0NEW")
        r3 = leluxe.get_row(it2["id"])
        check("fetch with a new ASIN writes the field + dirties for ClickUp",
              img3 == "http://img/x.png" and r3["data"]["fields"].get("ASIN") == "B0NEW"
              and r3["sync_state"] == "dirty")
    finally:
        amazon_import.import_product = real


def status_only_change():
    """set_status: unknown status rejected; a valid one flips ONLY the status
    (fields intact) to dirty, and pushes as a status-only PUT."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    CALLS.clear()
    row, _ = leluxe.save_row({"kind": "order", "name": "Order # st-1",
                              "status": "order number",
                              "fields": {"NAME": "B5", "Total Amount": "10"}})
    real = leluxe._http
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        leluxe.run_push_pass()                      # initial create → synced
        r, err = leluxe.set_status(row["id"], "nope")
        check("set_status rejects unknown status", r is None and "nope" in (err or ""))
        r, err = leluxe.set_status(row["id"], "sent rd")
        check("set_status flips row to dirty", not err and r["sync_state"] == "dirty"
              and r["status"] == "sent rd")
        check("set_status keeps fields intact", r["data"]["fields"].get("NAME") == "B5")
        n_before = len(CALLS)
        leluxe.run_push_pass()
        puts = [(m, u, b) for m, u, b in CALLS[n_before:]]
        check("status change pushes exactly one status-only PUT",
              len(puts) == 1 and puts[0][0] == "PUT"
              and puts[0][2].get("status") == "sent rd")
        check("row back to synced after status push",
              leluxe.get_row(row["id"])["sync_state"] == "synced")
    finally:
        leluxe._http = real
        os.environ["LELUXE_PUSH_DISABLED"] = "1"


def flat_tracking_enrichment():
    """refresh_tracking enriches ANY row carrying a tracking number (flat mirror:
    the order/item rows themselves), shares one lookup per GWD, and never
    touches sync_state (display-only)."""
    import sys, types
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    o, _ = leluxe.save_row({"kind": "order", "name": "Order # trk-1",
                            "fields": {"Tracking Number": "GWD777"}})
    it, _ = leluxe.save_row({"kind": "item", "name": "1 Watch",
                             "parent_local_id": o["id"],
                             "fields": {"Tracking Number": "GWD777"}})
    with db.connect() as c:                      # pretend both already synced
        c.execute("UPDATE leluxe_orders SET sync_state='synced'")
    tstub = types.SimpleNamespace(
        clean_tracking=lambda s: (s or "").strip(),
        get_session=lambda **k: ("api", "nonce"),
        fetch_one=lambda tn, *a, **k: {},
        latest_status=lambda d: {"bucket": "transit", "text": "In transit"},
        ops_deadline=lambda tn, **k: "2026-08-01")
    gstub = types.SimpleNamespace(
        track=lambda tn, **k: {"label": "Ready for pickup", "status": "r",
                               "bucket": "pickup"})
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        r = leluxe.refresh_tracking(batch=5)
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)
    o2, it2 = leluxe.get_row(o["id"]), leluxe.get_row(it["id"])
    check("flat rows sharing a GWD both enriched",
          (o2["data"].get("tracking_status") or {}).get("bucket") == "transit"
          and (it2["data"].get("gerizim_status") or {}).get("bucket") == "pickup")
    check("GAASH deadline stored", o2["data"].get("gaash_deadline") == "2026-08-01")
    check("one GWD → one lookup", r.get("checked") == 1)
    check("enrichment never dirties rows (display-only)",
          o2["sync_state"] == "synced" and it2["sync_state"] == "synced")


def _trk_stubs(calls, fetch_raises=False, deadline="2026-08-01",
               fetch_map=None, gz_track=None):
    """sys.modules stubs for tracking/gerizim recording fetched GWDs.
    fetch_map={tn: data} makes fetch_one/latest_status data-sensitive (real
    semantics: a status only when data carries Statuses); gz_track overrides
    gerizim.track (dict | "notfound" | None)."""
    import types

    def fetch(tn, *a, **k):
        calls.append(tn)
        if fetch_raises:
            raise RuntimeError("GAASH down")
        return (fetch_map or {}).get(tn, {}) if fetch_map is not None else {}
    latest = ((lambda d: {"bucket": "transit", "text": "In transit"}
               if (d or {}).get("Statuses") else None)
              if fetch_map is not None
              else (lambda d: {"bucket": "transit", "text": "In transit"}))
    tstub = types.SimpleNamespace(
        clean_tracking=lambda s: (s or "").strip(),
        get_session=lambda **k: ("api", "nonce"),
        fetch_one=fetch,
        latest_status=latest,
        ops_deadline=lambda tn, **k: deadline)
    gstub = types.SimpleNamespace(track=gz_track or (lambda tn, **k: None))
    return tstub, gstub


def tracking_skip_rules():
    """The bulk sweep skips parcels whose journey is over — all-done ClickUp
    statuses (Settings-reused alerts stop list), a stored Gerizim bucket at/past
    the office, or a GASH STATUS field saying DELIVERED — while only= / force
    bypass the skips (gaash_mail threads + the per-row 🔎 must still check)."""
    import sys
    import alerts  # noqa: F401 — bind the REAL tracking into alerts/purchases before stubbing
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    mk = lambda name, st, gwd: leluxe.save_row(     # flat-mirror rows (like live)
        {"kind": "order", "name": name, "status": st,
         "fields": {"Tracking Number": gwd}})[0]
    mk("1 A done", "sent rd", "GWD-A")                 # all real statuses done → skip
    mk("1 B live", "in clearance", "GWD-B")            # live status → check
    mk("1 C done", "sent rd", "GWD-C")                 # mixed with next row → check
    mk("1 C live", "in clearance", "GWD-C")
    mk("1 D unset", "", "GWD-D")                       # ""/entry only → check
    mk("1 D entry", "order number", "GWD-D")
    e1 = mk("1 E office", "", "GWD-E1")                # gz office → skip
    e2 = mk("1 E notfound", "", "GWD-E2")              # gz "notfound" STRING → check
    mk("1 F field", "", "GWD-F1")                      # GASH STATUS DELIVERED → skip
    f2 = mk("1 F planted", "", "GWD-F2")
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET sync_state='synced'")

    def plant(row_id, patch):                          # enrichment save_row can't set
        with db.connect() as c:
            r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                          (row_id,)).fetchone()
            d = json.loads(r["data_json"]); d.update(patch)
            c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), row_id))
    plant(e1["id"], {"gerizim_status": {"bucket": "office", "label": "At Gerizim office"}})
    plant(e2["id"], {"gerizim_status": "notfound"})
    with db.connect() as c:                            # F1 via fields, F2 planted raw
        r = c.execute("SELECT id,data_json FROM leluxe_orders").fetchall()
        for row in r:
            d = json.loads(row["data_json"])
            if (d.get("fields") or {}).get("Tracking Number") == "GWD-F1":
                d["fields"]["GASH STATUS"] = "GERZIM DELIVERED"
            if (d.get("fields") or {}).get("Tracking Number") == "GWD-F2":
                d["fields"]["GASH STATUS"] = "BRACHA DELIVERED"
            c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), row["id"]))
    calls = []
    tstub, gstub = _trk_stubs(calls)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        r1 = leluxe.refresh_tracking(batch=20)
        check("skips done/office/field-delivered, checks the rest",
              set(calls) == {"GWD-B", "GWD-C", "GWD-D", "GWD-E2"})
        check("counts: checked=live, remaining=0, skipped=4",
              r1["checked"] == 4 and r1["remaining"] == 0 and r1["skipped"] == 4)
        calls.clear()
        r2 = leluxe.refresh_tracking(only="GWD-A")
        check("only= bypasses the skip (gaash_mail thread freshness)",
              calls == ["GWD-A"] and r2["checked"] == 1)
        calls.clear()
        leluxe.refresh_tracking(batch=50, force=True)
        check("force bypasses skip AND ttl (per-row 🔎)",
              {"GWD-A", "GWD-E1", "GWD-F1", "GWD-F2"} <= set(calls))
        # the stop list is the Settings-editable alerts list — one vocabulary
        config = cfg.load()
        cfg.set_path(config, "alerts.stop_statuses", ["in clearance"])
        cfg.save(config)
        with db.connect() as c:
            rows = [leluxe._row(x) for x in c.execute(
                "SELECT * FROM leluxe_orders WHERE deleted=0")]
        sk = leluxe._skip_gwds(rows, cfg.load())
        check("settings alerts.stop_statuses drives the status skip",
              "GWD-B" in sk and "GWD-A" not in sk)
        import alerts as _al
        cfg.set_path(config, "alerts.stop_statuses", list(_al.STOP_DEFAULT))
        cfg.save(config)
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def tracking_failure_stamps():
    """A failed GAASH fetch still stamps tracking_checked (never
    tracking_status), so the 30-min TTL rotates the failure out of the batch
    head instead of re-attempting it on every POST forever."""
    import sys
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    row, _ = leluxe.save_row({"kind": "order", "name": "1 Fail",
                              "fields": {"Tracking Number": "GWD-X"}})
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET sync_state='synced'")
    calls = []
    tstub, gstub = _trk_stubs(calls, fetch_raises=True)
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        r1 = leluxe.refresh_tracking(batch=5)
        d = leluxe.get_row(row["id"])["data"]
        check("failed fetch stamps the attempt, not a status",
              r1["checked"] == 1 and d.get("tracking_checked")
              and "tracking_status" not in d)
        r2 = leluxe.refresh_tracking(batch=5)
        check("ttl rotates the failure out (no head-pinning)",
              r2["checked"] == 0 and r2["remaining"] == 0)
        calls.clear()
        r3 = leluxe.refresh_tracking(batch=5, force=True)
        check("force retries a stamped failure now",
              r3["checked"] == 1 and calls == ["GWD-X"])
        check("display-only: failure stamping never dirties the row",
              leluxe.get_row(row["id"])["sync_state"] == "synced")
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def tracking_results_payload():
    """refresh_tracking returns one honest per-GWD verdict — found / carrier-
    has-no-record (found False, error None) / error text — plus an advisory
    warn on non-canonical GWD shapes. The 🔎 UI renders these instead of
    toasting '✓ refreshed' at a blank row."""
    import sys
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    mk = lambda name, gwd: leluxe.save_row(
        {"kind": "order", "name": name, "fields": {"Tracking Number": gwd}})[0]
    mk("1 ok", "GWD004794031")
    mk("1 norec", "GWD004794032")
    mk("1 err", "GWD004794033")
    mk("1 typo", "GWD0004794032")          # 10 digits — the real-life typo shape
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET sync_state='synced'")
    calls = []
    tstub, gstub = _trk_stubs(calls, fetch_map={
        "GWD004794031": {"Statuses": [{}]},
        "GWD004794032": {},
        "GWD004794033": {"_error": "HTTP 500"},
        "GWD0004794032": {},
    }, gz_track=lambda tn, **k: "notfound")
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        r = leluxe.refresh_tracking(batch=10, force=True)
        res = {e["tracking"]: e for e in r.get("results") or []}
        ok, norec = res.get("GWD004794031"), res.get("GWD004794032")
        err, typo = res.get("GWD004794033"), res.get("GWD0004794032")
        check("results: found parcel carries its stage",
              bool(ok) and ok["found"] and ok["bucket"] == "transit"
              and not ok["error"])
        check("results: no-record = found False, error None, gz notfound",
              bool(norec) and not norec["found"] and norec["error"] is None
              and norec["gz"] == "notfound" and norec["warn"] is None)
        check("results: network error carried as text",
              bool(err) and not err["found"] and "HTTP 500" in (err["error"] or ""))
        check("results: 10-digit GWD gets the advisory warn",
              bool(typo) and bool(typo["warn"]) and "9 digits" in typo["warn"])
        r2 = leluxe.refresh_tracking(only="GWD-NOPE")
        check("results: only= with no matching row -> empty list",
              r2.get("results") == [] and r2["checked"] == 0)
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def tracking_notfound_still_checks_deadline():
    """BUG regression: a stored gerizim_status 'notfound' STRING used to pin
    eligible False forever, so gaash_deadline could never be fetched — not
    even with force. Only a real dict may block the deadline leg."""
    import sys
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    row, _ = leluxe.save_row({"kind": "order", "name": "1 NF",
                              "fields": {"Tracking Number": "GWD-N"}})
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row["id"],)).fetchone()
        d = json.loads(r["data_json"]); d["gerizim_status"] = "notfound"
        c.execute("UPDATE leluxe_orders SET data_json=?, sync_state='synced' WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row["id"]))
    calls = []
    tstub, gstub = _trk_stubs(calls, gz_track=lambda tn, **k: "notfound")
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        leluxe.refresh_tracking(batch=5, force=True)
        d2 = leluxe.get_row(row["id"])["data"]
        check("'notfound' string no longer blocks the deadline fetch",
              d2.get("gaash_deadline") == "2026-08-01")
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def tracking_gz_dict_survives_notfound():
    """BUG regression: a transient Gerizim 404 ('notfound') must never erase a
    real stored stage dict — and a real dict must replace a stored string."""
    import sys
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    keep, _ = leluxe.save_row({"kind": "order", "name": "1 keep",
                               "fields": {"Tracking Number": "GWD-S"}})
    gain, _ = leluxe.save_row({"kind": "order", "name": "1 gain",
                               "fields": {"Tracking Number": "GWD-T"}})
    with db.connect() as c:
        for rid, gz in ((keep["id"], {"bucket": "office", "label": "At Gerizim office",
                                      "status": "office"}),
                        (gain["id"], "notfound")):
            r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                          (rid,)).fetchone()
            d = json.loads(r["data_json"]); d["gerizim_status"] = gz
            c.execute("UPDATE leluxe_orders SET data_json=?, sync_state='synced' WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), rid))
    calls = []
    tstub, gstub = _trk_stubs(calls, gz_track=lambda tn, **k: (
        "notfound" if tn == "GWD-S"
        else {"bucket": "office", "label": "At Gerizim office", "status": "office"}))
    old_t, old_g = sys.modules.get("tracking"), sys.modules.get("gerizim")
    sys.modules["tracking"], sys.modules["gerizim"] = tstub, gstub
    try:
        leluxe.refresh_tracking(batch=5, force=True)
        kd = leluxe.get_row(keep["id"])["data"].get("gerizim_status")
        gd = leluxe.get_row(gain["id"])["data"].get("gerizim_status")
        check("stored stage dict survives a transient 'notfound'",
              isinstance(kd, dict) and kd.get("bucket") == "office")
        check("real dict replaces a stored 'notfound' string",
              isinstance(gd, dict) and gd.get("bucket") == "office")
    finally:
        for name, old in (("tracking", old_t), ("gerizim", old_g)):
            if old is not None:
                sys.modules[name] = old
            else:
                sys.modules.pop(name, None)


def gash_status_sync():
    """apply_gash_status mirrors the Gerizim bucket into the GASH STATUS field
    via a FIELD-ONLY push: the option must exist on the list, equal values are
    no-ops, the push sends NO core PUT (a status set manually in ClickUp can
    never be reverted), and a real edit clears the marker (full push resumes)."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    CALLS.clear()
    row, _ = leluxe.save_row({"kind": "order", "name": "Order # gz-1",
                              "status": "order number",
                              "fields": {"Tracking Number": "GWD9"}})
    real = leluxe._http
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        leluxe.run_push_pass()                     # initial create → synced
        def plant(gz):                             # what refresh_tracking stores
            with db.connect() as c:
                r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                              (row["id"],)).fetchone()
                d = json.loads(r["data_json"]); d["gerizim_status"] = gz
                c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                          (json.dumps(d, ensure_ascii=False), row["id"]))
        plant({"bucket": "delivered", "label": "GERZIM DELIVERED", "status": "delivered"})
        n = len(leluxe.apply_gash_status())
        r2 = leluxe.get_row(row["id"])
        check("delivered bucket queues the existing option", n == 1
              and r2["data"]["fields"].get("GASH STATUS") == "GERZIM DELIVERED"
              and r2["data"].get("pending_fields") == ["GASH STATUS"]
              and r2["sync_state"] == "dirty")
        check("task status column untouched", r2["status"] == "order number")
        n_before = len(CALLS)
        leluxe.run_push_pass()
        sent = CALLS[n_before:]
        check("field-only push: one field call, NO core PUT",
              len(sent) == 1 and sent[0][0] == "POST" and "/field/f-gs" in sent[0][1]
              and sent[0][2] == {"value": "opt-gzd"})
        r3 = leluxe.get_row(row["id"])
        check("marker cleared + synced after field-only push",
              r3["sync_state"] == "synced" and "pending_fields" not in r3["data"])
        check("idempotent: same stage queues nothing", leluxe.apply_gash_status() == [])
        # once تم التسليم exists (added in ClickUp UI → Discover), it's preferred
        config = cfg.load()
        config["leluxe"]["schema"]["fields"]["GASH STATUS"]["options"].append(
            {"id": "opt-tam", "name": "تم التسليم", "orderindex": 10, "color": "#2ecd6f"})
        cfg.save(config)
        n = len(leluxe.apply_gash_status())
        r4 = leluxe.get_row(row["id"])
        check("تم التسليم preferred once its option exists",
              n == 1 and r4["data"]["fields"].get("GASH STATUS") == "تم التسليم")
        # round 5: Arabic-first candidate ordering — office falls back to English
        # until "في مكتب جرزيم" is added, then prefers the Arabic
        _, fdefA = leluxe._gash_field_def(cfg.load())
        check("office falls back to English before its Arabic option exists",
              leluxe._gash_option_for(leluxe.GERIZIM_BUCKET_OPTIONS["office"], fdefA) == "Picked up by Gerizim")
        cA = cfg.load()
        cA["leluxe"]["schema"]["fields"]["GASH STATUS"]["options"].append(
            {"id": "opt-mkt", "name": "في مكتب جرزيم", "orderindex": 11, "color": "#8d8d8d"})
        cfg.save(cA)
        _, fdefB = leluxe._gash_field_def(cfg.load())
        check("office prefers the Arabic option once added",
              leluxe._gash_option_for(leluxe.GERIZIM_BUCKET_OPTIONS["office"], fdefB) == "في مكتب جرزيم")
        # a REAL edit clears the marker → the normal full push takes over
        row2, _ = leluxe.save_row({"id": row["id"], "kind": "order",
                                   "name": "Order # gz-1", "status": "sent rd",
                                   "fields": {"Tracking Number": "GWD9",
                                              "GASH STATUS": "تم التسليم"}})
        check("real edit clears the pending marker",
              "pending_fields" not in row2["data"])
        n_before = len(CALLS)
        leluxe.run_push_pass()
        puts = [x for x in CALLS[n_before:] if x[0] == "PUT"]
        check("full push resumes after a real edit (core PUT sent)",
              len(puts) == 1 and puts[0][2].get("status") == "sent rd")
        # set_status also clears the marker
        with db.connect() as c:
            r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                          (row["id"],)).fetchone()
            d = json.loads(r["data_json"]); d["pending_fields"] = ["GASH STATUS"]
            c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), row["id"]))
        r5, err = leluxe.set_status(row["id"], "order number")
        check("set_status clears the pending marker",
              not err and "pending_fields" not in r5["data"])
        leluxe.run_push_pass()                     # drain → synced for the next check
        # a stage whose option is missing on the list is skipped, never guessed
        plant({"bucket": "sms", "label": "SMS sent — awaiting pickup", "status": "sms"})
        check("bucket without an existing option is skipped",
              leluxe.apply_gash_status() == [])

        # ── GAASH leg (no Gerizim data yet) + the forward-only guard ──
        row2, _ = leluxe.save_row({"kind": "order", "name": "Order # gz-2",
                                   "status": "order number",
                                   "fields": {"Tracking Number": "GWD10"}})
        leluxe.run_push_pass()
        def plant2(d2):
            with db.connect() as c:
                r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                              (row2["id"],)).fetchone()
                d = json.loads(r["data_json"]); d.update(d2)
                c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                          (json.dumps(d, ensure_ascii=False), row2["id"]))
        # live GAASH "Required customer ID" → the " customer ID" option
        # (leading space — written in ClickUp's exact spelling)
        plant2({"tracking_status": {"bucket": "customs", "text": "Required customer ID"}})
        n = len(leluxe.apply_gash_status())
        r6 = leluxe.get_row(row2["id"])
        check("GAASH customs/customer-ID maps to the exact option",
              n == 1 and r6["data"]["fields"].get("GASH STATUS") == " customer ID")
        leluxe.run_push_pass()
        # live cleared (rank above customer ID) → moves FORWARD
        plant2({"tracking_status": {"bucket": "cleared", "text": "Cleared customs"}})
        n = len(leluxe.apply_gash_status())
        r7 = leluxe.get_row(row2["id"])
        check("stale field catches up when the parcel clears (his MOC bug)",
              n == 1 and r7["data"]["fields"].get("GASH STATUS") == "CLEARED GASH")
        leluxe.run_push_pass()
        # a LAGGING feed (arrived < cleared) must never move the field backward
        plant2({"tracking_status": {"bucket": "arrived",
                                    "text": "Arrived at destination country"}})
        check("forward-only: lagging GAASH stage never downgrades the field",
              leluxe.apply_gash_status() == [])
        # Gerizim data outranks the GAASH leg once the parcel reaches last-mile
        plant2({"gerizim_status": {"bucket": "office", "label": "At Gerizim office",
                                   "status": "office"}})
        n = len(leluxe.apply_gash_status())
        r8 = leluxe.get_row(row2["id"])
        check("Gerizim stage wins over the GAASH leg (Arabic office option)",
              n == 1 and r8["data"]["fields"].get("GASH STATUS") == "في مكتب جرزيم")
    finally:
        leluxe._http = real
        os.environ["LELUXE_PUSH_DISABLED"] = "1"


def sync_kept_report():
    """Sync from AZ (2) must SHOW the comparison even when it changes nothing:
    app-side edits that win land in report['kept'] (with the ClickUp value),
    and newly-added products carry ids so the UI can jump to them."""
    global SRC_TASKS
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    config = cfg.load()
    cfg.set_path(config, "leluxe.source_list_id", "SRC")
    cfg.set_path(config, "leluxe.list_id", "L1")
    cfg.save(config)
    SRC_TASKS = [
        _task("SO1", "Order # SYNC-1", "order number", fields=[("NAME", 37)]),
        _task("SC1", "10 Watch", "sent rd", parent="SO1",
              fields=[("Tracking Number", "GWD-S1")]),
    ]
    for t in SRC_TASKS:
        t["date_created"] = "1780000000000"
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        r1 = leluxe.sync_from_source("2026-01-01", limit=25)
        check("initial sync inserts the order", r1.get("orders") == 1 and not r1.get("error"))
        check("nothing kept on a clean first sync", r1.get("kept") == 0)
        cid = leluxe._row_id_by_source("SC1")
        leluxe.set_status(cid, "order number")         # app-side edit, AZ (2) untouched
        r2 = leluxe.sync_from_source("2026-01-01")
        check("app edit is reported as kept", r2.get("kept", 0) >= 1 and r2.get("updated") == 0)
        rep = json.loads(open(leluxe.data_path("leluxe_sync_report.json"), encoding="utf-8").read())
        ke = next((k for k in rep.get("kept") or [] if k["id"] == cid), None)
        check("kept entry compares yours vs ClickUp",
              ke is not None and ke["syncing"] is True and ke["order_id"]
              and any(d["field"] == "status" and d["local"] == "order number"
                      and d["remote"] == "sent rd" for d in ke["diffs"]))
        SRC_TASKS.append(_task("SC2", "3 Strap", "sent rd", parent="SO1",
                               fields=[("Tracking Number", "GWD-S1")]))
        SRC_TASKS[-1]["date_created"] = "1780000000001"
        r3 = leluxe.sync_from_source("2026-01-01")
        check("added product counted", r3.get("new_items") == 1)
        rep = json.loads(open(leluxe.data_path("leluxe_sync_report.json"), encoding="utf-8").read())
        ni = (rep.get("new_items") or [{}])[0]
        row = ni.get("id") and leluxe.get_row(ni["id"])
        check("added product carries jumpable ids",
              bool(ni.get("id")) and bool(ni.get("order_id"))
              and row and row["kind"] == "item" and row["name"] == "3 Strap")
    finally:
        leluxe._http = real


def frozen_row_thaws():
    """REGRESSION: a row whose source_cu_updated already equals AZ (2)'s
    date_updated, but whose stored base no longer matches AZ (2), used to be
    skipped by the merge's timestamp fast path FOREVER — permanently stale while
    every sync reported "+0 updated". The fast path must now also verify the
    base still matches, so such a row thaws on the next sync. A row whose base
    genuinely equals AZ (2) must still take the cheap path (app edits kept)."""
    global SRC_TASKS
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    config = cfg.load()
    cfg.set_path(config, "leluxe.source_list_id", "SRC")
    cfg.set_path(config, "leluxe.list_id", "L1")
    cfg.save(config)
    SRC_TASKS = [
        _task("FO1", "Order # FROZEN-1", "order number", updated="500"),
        _task("FC1", "9 Watch", "oredered", parent="FO1", updated="500",
              fields=[("Tracking Number", "GWD-F1")]),
    ]
    for t in SRC_TASKS:
        t["date_created"] = "1780000000000"
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        leluxe.sync_from_source("2026-01-01", limit=25)
        cid = leluxe._row_id_by_source("FC1")
        check("frozen fixture: product pulled in", bool(cid))

        # Forge the exact frozen state: AZ (2) now says "rd" and its stamp has
        # moved, but the row records that stamp while its base still says the
        # OLD value — i.e. the stamp advanced without the value being applied.
        SRC_TASKS[1]["status"] = {"status": "rd"}
        SRC_TASKS[1]["date_updated"] = "900"
        with db.connect() as c:
            row = leluxe._row(c.execute(
                "SELECT * FROM leluxe_orders WHERE id=?", (cid,)).fetchone())
            data = row["data"]
            data["source_cu_updated"] = "900"          # stamp says "up to date"
            data["source_base"]["status"] = "oredered"  # base says otherwise
            c.execute("UPDATE leluxe_orders SET data_json=?, status='oredered', "
                      "sync_state='synced' WHERE id=?",
                      (json.dumps(data, ensure_ascii=False), cid))
        check("frozen fixture: row is stale with a current stamp",
              leluxe.get_row(cid)["status"] == "oredered")

        r = leluxe.sync_from_source("2026-01-01")
        check("the frozen row thaws and takes AZ (2)'s value",
              leluxe.get_row(cid)["status"] == "rd")
        check("and the sync REPORTS it instead of saying +0 updated",
              r.get("updated", 0) >= 1)

        # the cheap path must survive: base == AZ (2) → an app edit is kept
        leluxe.set_status(cid, "sent rd")
        r2 = leluxe.sync_from_source("2026-01-01")
        check("an app-side edit is still kept when the base matches AZ (2)",
              leluxe.get_row(cid)["status"] == "sent rd" and r2.get("updated") == 0)
    finally:
        leluxe._http = real


def _fresh_src(tasks):
    """Reset the local mirror + point the sync fixtures at SRC/L1."""
    global SRC_TASKS
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    config = cfg.load()
    cfg.set_path(config, "leluxe.source_list_id", "SRC")
    cfg.set_path(config, "leluxe.list_id", "L1")
    cfg.save(config)
    SRC_TASKS = tasks
    for t in SRC_TASKS:
        t["date_created"] = "1780000000000"   # _task defaults to "1" = pre-since


def conflict_review_flow():
    """A REAL both-sides conflict parks, is visible everywhere the owner looks
    (chip count == modal list), survives a re-sync without duplicating, and
    resolves to ClickUp's value on request."""
    _fresh_src([
        _task("CO1", "Order # CONF-1", "order number", updated="100"),
        _task("CC1", "7 Watch", "oredered", parent="CO1", updated="100",
              fields=[("Tracking Number", "GWD-C1")]),
    ])
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        leluxe.sync_from_source("2026-01-01", limit=25)
        cid = leluxe._row_id_by_source("CC1")
        # both sides move the SAME field to DIFFERENT values
        leluxe.set_status(cid, "sent rd")                       # app side
        SRC_TASKS[1]["status"] = {"status": "rd"}               # AZ (2) side
        SRC_TASKS[1]["date_updated"] = "200"
        r = leluxe.sync_from_source("2026-01-01")
        check("both-sides change parks a conflict", r.get("conflicts", 0) >= 1)
        row = leluxe.get_row(cid)
        check("row is in conflict state, local value untouched",
              row["sync_state"] == "conflict" and row["status"] == "sent rd")
        lst = leluxe.list_conflicts()
        sc = leluxe.sync_counts()
        check("chip count agrees with the review modal",
              sc["conflict"] == len(lst) == 1
              and lst[0]["row_id"] == cid
              and any(c["field"] == "status" and c["remote"] == "rd"
                      for c in lst[0]["conflicts"]))
        leluxe.sync_from_source("2026-01-01")                   # re-sync: stable
        lst2 = leluxe.list_conflicts()
        check("re-sync neither clears nor duplicates the parked conflict",
              leluxe.sync_counts()["conflict"] == 1 and len(lst2) == 1
              and len(lst2[0]["conflicts"]) == len(lst[0]["conflicts"]))
        leluxe.resolve_conflict(cid, choice="remote")
        row = leluxe.get_row(cid)
        check("resolve→remote applies ClickUp's value and queues the push",
              row["status"] == "rd" and row["sync_state"] == "dirty")
        check("nothing left to review", leluxe.sync_counts()["conflict"] == 0)
    finally:
        leluxe._http = real


def review_park_amnesty():
    """🛡 review-mode parks EVERY change — including plain AZ (2)-only updates.
    The owner hit OK on that confirm by reflex and got '0 updated · 5 conflicts'.
    A later NORMAL sync must re-decide those parked applies and land them."""
    _fresh_src([
        _task("AO1", "Order # AMN-1", "order number", updated="100"),
        _task("AC1", "4 Watch", "oredered", parent="AO1", updated="100",
              fields=[("Tracking Number", "GWD-A1")]),
    ])
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        leluxe.sync_from_source("2026-01-01", limit=25)
        cid = leluxe._row_id_by_source("AC1")
        SRC_TASKS[1]["status"] = {"status": "rd"}               # AZ (2)-only change
        SRC_TASKS[1]["date_updated"] = "300"
        r = leluxe.sync_from_source("2026-01-01", review_all=True)
        check("review mode parks the pending apply instead of writing",
              r.get("updated") == 0 and r.get("conflicts", 0) >= 1
              and leluxe.get_row(cid)["status"] == "oredered")
        r2 = leluxe.sync_from_source("2026-01-01")              # normal sync
        check("amnesty: the next normal sync applies the parked change",
              leluxe.get_row(cid)["status"] == "rd" and r2.get("updated", 0) >= 1)
        check("and the conflict evaporates", leluxe.sync_counts()["conflict"] == 0)
    finally:
        leluxe._http = real


def malformed_conflict_heals():
    """A row stuck in sync_state='conflict' with an EMPTY conflicts list made
    the ⚠ chip say N while the modal said 'no conflicts to review'. The count
    must ignore it and the heal must repair it."""
    _fresh_src([
        _task("HO1", "Order # HEAL-1", "order number", updated="100"),
        _task("HC1", "2 Watch", "oredered", parent="HO1", updated="100",
              fields=[("Tracking Number", "GWD-H1")]),
    ])
    real = leluxe._http
    leluxe._http = fake_src_http
    try:
        leluxe.sync_from_source("2026-01-01", limit=25)
        cid = leluxe._row_id_by_source("HC1")
        with db.connect() as c:                                 # forge the malform
            c.execute("UPDATE leluxe_orders SET sync_state='conflict' WHERE id=?",
                      (cid,))
        check("chip count ignores the empty-list row",
              leluxe.sync_counts()["conflict"] == 0)
        check("modal payload is empty for it", leluxe.list_conflicts() == [])
        check("and opening the review healed the row",
              leluxe.get_row(cid)["sync_state"] == "synced")
    finally:
        leluxe._http = real


def auto_pull_pass():
    """⏱ background sync: off by default, runs at most once per interval, and
    respects the same mutex as the manual Sync button."""
    _fresh_src([
        _task("PO9", "Order # PULL-1", "order number", updated="100"),
    ])
    real = leluxe._http
    leluxe._http = fake_src_http
    db.set_setting("leluxe:auto_pull", None)
    db.set_setting("leluxe:auto_pull_last", None)
    db.set_setting("leluxe:import_running", 0)
    try:
        check("disabled by default",
              leluxe.run_pull_pass(now=1e9).get("skipped") == "disabled")
        db.set_setting("leluxe:auto_pull", {"enabled": True, "minutes": 30})
        r = leluxe.run_pull_pass(now=1e9)
        check("enabled → the tick runs a real sync", r.get("ran") is True)
        last = db.get_setting("leluxe:auto_pull_last") or {}
        check("last-run is recorded for the Tools label",
              float(last.get("ts") or 0) == 1e9 and last.get("error") == "")
        check("mutex released after the run",
              (db.get_setting("leluxe:import_running") or 0) == 0)
        check("not due again within the interval",
              leluxe.run_pull_pass(now=1e9 + 60).get("skipped") == "not due")
        check("due again after the interval",
              leluxe.run_pull_pass(now=1e9 + 31 * 60).get("ran") is True)
        db.set_setting("leluxe:import_running", 1e9 + 62 * 60)
        check("a running manual sync blocks the tick",
              leluxe.run_pull_pass(now=1e9 + 63 * 60).get("skipped")
              == "sync already running")
    finally:
        leluxe._http = real
        db.set_setting("leluxe:auto_pull", None)
        db.set_setting("leluxe:auto_pull_last", None)
        db.set_setting("leluxe:import_running", 0)


AZ2_STATE = {"status": "rd"}


def fake_az2_http(url, method="GET", body=None, _retried=False):
    CALLS.append((method, url, body))
    if url.endswith("/task/AZT") and method == "GET":
        return 200, {"id": "AZT", "name": "az2 task",
                     "status": {"status": AZ2_STATE["status"]}}
    if url.endswith("/task/AZT") and method == "PUT":
        AZ2_STATE["status"] = (body or {}).get("status")
        return 200, {}
    return 200, {}


def az2_push_and_undo():
    """The one write path into AZ (2): CAS-guarded, journalled with a before-
    image, tagged+commented, undoable — and refusals write NOTHING."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("DELETE FROM az2_pushes")
    rid = leluxe._insert_row("item", "9 Watch", status="sent rd",
                             extra={"source_task_id": "AZT"})
    bare = leluxe._insert_row("item", "no-link item", status="rd")
    AZ2_STATE["status"] = "rd"
    real = leluxe._http
    leluxe._http = fake_az2_http
    try:
        CALLS.clear()
        _, err = leluxe.az2_push_status(rid, expected_remote="oredered")
        check("CAS mismatch aborts", err is not None and "changed since" in err)
        check("aborted push wrote NOTHING",
              not [1 for m, u, b in CALLS if m in ("PUT", "POST")])
        _, err = leluxe.az2_push_status(bare)
        check("row without AZ (2) link refused", err is not None and "no AZ (2) link" in err)
        db.set_setting("leluxe:az2", {"enabled": False})
        _, err = leluxe.az2_push_status(rid, expected_remote="rd")
        check("kill switch honored", err is not None and "disabled" in err)
        db.set_setting("leluxe:az2", {"enabled": True})

        CALLS.clear()
        entry, err = leluxe.az2_push_status(rid, expected_remote="rd", user="qais")
        check("push ok", err is None and entry["old"] == "rd" and entry["new"] == "sent rd")
        check("AZ (2) task updated", AZ2_STATE["status"] == "sent rd")
        check("tag + comment posted",
              any("/tag/otl-push" in u and m == "POST" for m, u, b in CALLS)
              and any(u.endswith("/comment") and m == "POST" for m, u, b in CALLS))
        with db.connect() as c:
            j = dict(c.execute("SELECT * FROM az2_pushes WHERE id=?",
                               (entry["id"],)).fetchone())
        check("journal keeps the before-image", j["state"] == "pushed"
              and j["old_value"] == "rd" and j["new_value"] == "sent rd"
              and j["user"] == "qais" and "az2 task" in (j["snapshot_json"] or ""))
        e2, err = leluxe.az2_push_status(rid, expected_remote="sent rd")
        with db.connect() as c:
            n = c.execute("SELECT COUNT(*) n FROM az2_pushes").fetchone()["n"]
        check("already-equal is a no-op (no journal)", err is None
              and e2.get("noop") and n == 1)

        AZ2_STATE["status"] = "delivered"          # Faisal moved it meanwhile
        _, err = leluxe.az2_undo(entry["id"])
        check("undo aborts when AZ (2) drifted", err is not None and "changed after" in err)
        AZ2_STATE["status"] = "sent rd"
        CALLS.clear()
        u1, err = leluxe.az2_undo(entry["id"], user="qais")
        check("undo restores the old value", err is None
              and u1["restored"] == "rd" and AZ2_STATE["status"] == "rd")
        with db.connect() as c:
            st1 = c.execute("SELECT state FROM az2_pushes WHERE id=?",
                            (entry["id"],)).fetchone()["state"]
            und = c.execute("SELECT * FROM az2_pushes WHERE undo_of=?",
                            (entry["id"],)).fetchone()
        check("journal marks undone + records the undo",
              st1 == "undone" and und and und["state"] == "undo")
        check("marker tag removed after last undo",
              any("/tag/otl-push" in u and m == "DELETE" for m, u, b in CALLS))
        hist = leluxe.az2_push_history()
        check("history lists newest first", hist and hist[0]["state"] == "undo"
              and hist[-1]["state"] == "undone")
    finally:
        leluxe._http = real


AZO = {"tasks": {}, "n": 0}


def fake_azo_http(url, method="GET", body=None, _retried=False):
    """In-memory AZ (2): create-in-list, GET (with ?include_subtasks), PUT
    name/parent/status, DELETE. Tag/field/comment sub-paths are accepted no-ops."""
    CALLS.append((method, url, body))
    base = url.split("?")[0]
    m = re.match(r".*/task/([^/]+)(/.*)?$", base)
    tid = m.group(1) if m else None
    sub = m.group(2) if m else None
    if "/list/" in base and base.endswith("/task") and method == "POST":
        AZO["n"] += 1
        nid = f"NEW{AZO['n']}"
        AZO["tasks"][nid] = {"id": nid, "name": (body or {}).get("name") or "",
                             "status": {"status": (body or {}).get("status") or "order number"},
                             "parent": (body or {}).get("parent"),
                             "due_date": (body or {}).get("due_date")}
        return 200, {"id": nid}
    if sub and sub.startswith("/field/") and method == "POST" \
            and tid in AZO["tasks"]:
        AZO["tasks"][tid].setdefault("cf", {})[sub.split("/")[2]] = \
            (body or {}).get("value")
        return 200, {}
    if sub:                                    # /tag /comment
        return 200, {}
    if tid:
        t = AZO["tasks"].get(tid)
        if not t:
            return 404, {"_error": "not found"}
        if method == "GET":
            out = {k: v for k, v in t.items() if k not in ("cf", "cf_defs")}
            # like real ClickUp: every field entry ships its full type_config
            namedef = {"type": "drop_down", "type_config": {"options": [
                {"id": "opt-a", "name": "A", "orderindex": 0},
                {"id": "opt-b5", "name": "B5", "orderindex": 37},
                {"id": "opt-newly", "name": "NEWLY", "orderindex": 99}]}}
            out["custom_fields"] = [
                {"id": k, "value": v, **(namedef if k == "f-name" else {})}
                for k, v in (t.get("cf") or {}).items()]
            out["custom_fields"] += t.get("cf_defs") or []
            if "include_subtasks" in url:
                out["subtasks"] = [dict(x) for x in AZO["tasks"].values()
                                   if x.get("parent") == tid]
            return 200, out
        if method == "PUT":
            for k in ("name", "parent", "due_date"):
                if k in (body or {}):
                    t[k] = body[k]
            if "status" in (body or {}):
                t["status"] = {"status": body["status"]}
            return 200, dict(t)
        if method == "DELETE":
            del AZO["tasks"][tid]
            return 204, {}
    return 200, {}


def az2_organize_flow():
    """📦⤴ Organize in ClickUp: the board's order → 📦 GWD → products tree is
    mirrored into AZ (2) — packages created, products re-parented, split
    quantities renamed, splits created, and every product/package carries its
    GWD + split quantity + profile NAME + due date; empty shells are never
    created (and previously-created ones are pruned); bare-quantity names are
    flagged, never mirrored; every step journalled + undoable; a second run is
    a no-op."""
    config = cfg.load()
    cfg.set_path(config, "leluxe.source_list_id", "SRC")
    cfg.save(config)
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("DELETE FROM az2_pushes")
    oid = leluxe._insert_row("order", "Order # 999-TEST", status="order number",
                            fields={"NAME": "B5"}, due_date="1786200000000",
                            extra={"source_task_id": "ORD"})
    p1 = leluxe._insert_row("package", "📦 GWD111", parent_local_id=oid,
                            extra={"tracking_number": "GWD111"})
    p2 = leluxe._insert_row("package", "📦 GWD222", parent_local_id=oid,
                            extra={"tracking_number": "GWD222"})
    p0 = leluxe._insert_row("package", "📦 no tracking", parent_local_id=oid)
    p3 = leluxe._insert_row("package", "📦 GWD333", parent_local_id=oid,
                            extra={"tracking_number": "GWD333"})
    itA = leluxe._insert_row("item", "2 U.S. Polo", status="sent rd",
                             parent_local_id=p1, fields={"NAME": "NEWLY"},
                             due_date="1786000000000",
                             extra={"source_task_id": "A"})
    itD = leluxe._insert_row("item", "8", status="sent rd", parent_local_id=p1)
    itB = leluxe._insert_row("item", "8 U.S. Polo", status="sent rd",
                             parent_local_id=p2, due_date="1786100000000")
    itC = leluxe._insert_row("item", "9 Accutime", status="sent rd",
                             parent_local_id=p2, extra={"source_task_id": "C"})
    AZO["tasks"] = {
        # ORD carries the LIVE dropdown definition: it includes "NEWLY", a
        # batch letter the cached SCHEMA does not know — organize must encode
        # from these inline options, not the stale cache
        "ORD": {"id": "ORD", "name": "Order # 999-TEST", "parent": None,
                "status": {"status": "order number"},
                "cf_defs": [{"id": "f-name", "type": "drop_down", "type_config": {
                    "options": [{"id": "opt-a", "name": "A", "orderindex": 0},
                                {"id": "opt-b5", "name": "B5", "orderindex": 37},
                                {"id": "opt-newly", "name": "NEWLY",
                                 "orderindex": 99}]}}]},
        "A": {"id": "A", "name": "10 U.S. Polo", "parent": "ORD",
              "status": {"status": "sent rd"}, "cf": {"f-qty": "5"}},
        "C": {"id": "C", "name": "9 Accutime", "parent": "OTHER",
              "status": {"status": "sent rd"}},
        "OTHER": {"id": "OTHER", "name": "someone else's box", "parent": None,
                  "status": {"status": "order number"}},
    }
    AZO["n"] = 0
    real = leluxe._http
    leluxe._http = fake_azo_http
    try:
        CALLS.clear()
        d, err = leluxe.az2_organize(oid, dry_run=True)
        check("dry-run plans the tree", err is None and d["creates_pkg"] == 2
              and d["moves"] == 1 and d["renames"] == 1 and d["creates_item"] == 1)
        check("dry-run plans the fields (GWD + qty + profile) and the due date",
              d["field_sets"] == 3 and d["due_sets"] == 1)
        check("untracked package is not planned", d["packages"] == 3)
        check("empty tracked package is never created as a shell",
              d["skipped_empty"] == 1 and d["prunes"] == 0)
        check("foreign move + bare-quantity name are skipped, not mirrored",
              len(d["skipped"]) == 2
              and any("different task" in s["note"] for s in d["skipped"])
              and any("just a quantity" in s["note"] for s in d["skipped"]))
        check("dry-run wrote NOTHING",
              not [1 for m_, u, b in CALLS if m_ in ("PUT", "POST", "DELETE")])

        rep, err = leluxe.az2_organize(oid, user="qais")
        check("organize ok", err is None and len(rep["steps"]) == 9)
        pkg1_tid = (leluxe.get_row(p1)["data"] or {}).get("source_task_id")
        pkg2_tid = (leluxe.get_row(p2)["data"] or {}).get("source_task_id")
        itb_tid = (leluxe.get_row(itB)["data"] or {}).get("source_task_id")
        check("packages created under the order + linked back",
              pkg1_tid and pkg2_tid
              and AZO["tasks"][pkg1_tid]["parent"] == "ORD"
              and AZO["tasks"][pkg1_tid]["name"] == "📦 GWD111"
              and AZO["tasks"][pkg2_tid]["name"] == "📦 GWD222")
        check("product moved under its package + split qty renamed",
              AZO["tasks"]["A"]["parent"] == pkg1_tid
              and AZO["tasks"]["A"]["name"] == "2 U.S. Polo")
        check("split remainder created + linked back",
              itb_tid and AZO["tasks"][itb_tid]["parent"] == pkg2_tid
              and AZO["tasks"][itb_tid]["name"] == "8 U.S. Polo")
        cf = lambda t, f: str((AZO["tasks"].get(t, {}).get("cf") or {}).get(f, ""))
        check("every product carries its parcel number",
              cf("A", "f-trk") == "GWD111" and cf(itb_tid, "f-trk") == "GWD222")
        check("split quantities written",
              cf("A", "f-qty") == "2" and cf(itb_tid, "f-qty") == "8")
        check("package totals = sum of contents (incl. skipped rows)",
              cf(pkg1_tid, "f-qty") == "10" and cf(pkg2_tid, "f-qty") == "17")
        check("profile NAME on products and packages (encoded option)",
              cf(itb_tid, "f-name") == "opt-b5"
              and cf(pkg2_tid, "f-name") == "opt-b5")
        check("a batch letter the cached schema doesn't know encodes from the "
              "task's LIVE options",
              cf("A", "f-name") == "opt-newly"
              and cf(pkg1_tid, "f-name") == "opt-newly")
        check("due dates on products and packages",
              str(AZO["tasks"]["A"].get("due_date")) == "1786000000000"
              and str(AZO["tasks"][itb_tid].get("due_date")) == "1786100000000"
              and str(AZO["tasks"][pkg1_tid].get("due_date")) == "1786000000000"
              and str(AZO["tasks"][pkg2_tid].get("due_date")) == "1786100000000")
        check("bare-quantity row was NOT mirrored",
              not any(t.get("name") == "8" for t in AZO["tasks"].values()))
        check("no 'package' status in schema → created without status",
              not any((b or {}).get("status") == "package"
                      for m_, u, b in CALLS if m_ == "POST" and "/list/" in u))
        with db.connect() as c:
            j = [dict(r) for r in c.execute(
                "SELECT * FROM az2_pushes ORDER BY id")]
        check("journal carries every step in order",
              [x["field"] for x in j] ==
              ["pkg_create", "parent", "name", "cf:Tracking Number",
               "cf:Quantity ordered", "cf:NAME", "due_date",
               "pkg_create", "item_create"]
              and all(x["state"] == "pushed" for x in j))
        d2, err = leluxe.az2_organize(oid, dry_run=True)
        check("second run is a no-op (structure, fields, dues)", err is None
              and d2["creates_pkg"] == 0 and d2["moves"] == 0
              and d2["renames"] == 0 and d2["creates_item"] == 0
              and d2["field_sets"] == 0 and d2["due_sets"] == 0)

        # an earlier run created an empty shell → this run prunes it
        AZO["tasks"]["PKG3"] = {"id": "PKG3", "name": "📦 GWD333",
                                "parent": "ORD",
                                "status": {"status": "order number"}}
        leluxe._az2_link_source(p3, "PKG3")
        with db.connect() as c:
            c.execute("""INSERT INTO az2_pushes (row_id, task_id, field,
                         old_value, new_value, ts, user, state)
                         VALUES (?,?,?,?,?,?,?,'pushed')""",
                      (p3, "PKG3", "pkg_create", "", "📦 GWD333",
                       db.now_iso(), "qais"))
        d3, err = leluxe.az2_organize(oid, dry_run=True)
        check("empty shell from an earlier run is planned for pruning",
              err is None and d3["prunes"] == 1)
        rep3, err = leluxe.az2_organize(oid, user="qais")
        check("empty shell pruned + unlinked", err is None
              and rep3["pruned"] == 1 and "PKG3" not in AZO["tasks"]
              and not (leluxe.get_row(p3)["data"] or {}).get("source_task_id"))

        AZO["tasks"]["A"]["cf"]["f-qty"] = "5"       # Faisal reset the qty
        d4, err = leluxe.az2_organize(oid, dry_run=True)
        check("a drifted field re-plans exactly one fix",
              err is None and d4["field_sets"] == 1 and d4["moves"] == 0)
        rep4, err = leluxe.az2_organize(oid, user="qais")
        check("field re-fix lands", err is None and cf("A", "f-qty") == "2")
        with db.connect() as c:
            jq = dict(c.execute("SELECT * FROM az2_pushes WHERE "
                                "field='cf:Quantity ordered' ORDER BY id DESC "
                                "LIMIT 1").fetchone())
        AZO["tasks"]["A"]["cf"]["f-qty"] = "7"
        _, err = leluxe.az2_undo(jq["id"])
        check("field undo aborts when AZ (2) drifted",
              err is not None and "changed after" in err)
        AZO["tasks"]["A"]["cf"]["f-qty"] = "2"
        _, err = leluxe.az2_undo(jq["id"], user="qais")
        check("field undo restores the old value", err is None
              and cf("A", "f-qty") == "5")

        # j: 0 pkg_create p1 · 1 parent A · 2 name A · 3 cf trk · 4 cf qty ·
        #    5 cf NAME · 6 due_date · 7 pkg_create p2 · 8 item_create B
        _, err = leluxe.az2_undo(j[5]["id"], user="qais")
        check("profile undo clears the dropdown (old was empty)", err is None
              and (AZO["tasks"]["A"].get("cf") or {}).get("f-name") is None)
        _, err = leluxe.az2_undo(j[6]["id"], user="qais")
        check("due-date undo clears it back (old was empty)", err is None
              and AZO["tasks"]["A"].get("due_date") is None)
        _, err = leluxe.az2_undo(j[0]["id"])
        check("package undo refused while it still holds products",
              err is not None and "still holds products" in err)
        _, err = leluxe.az2_undo(j[8]["id"], user="qais")
        check("created product undone (deleted + unlinked)", err is None
              and itb_tid not in AZO["tasks"]
              and not (leluxe.get_row(itB)["data"] or {}).get("source_task_id"))
        _, err = leluxe.az2_undo(j[1]["id"], user="qais")
        check("move undone — product back under the order", err is None
              and AZO["tasks"]["A"]["parent"] == "ORD")
        _, err = leluxe.az2_undo(j[0]["id"], user="qais")
        check("empty package undo deletes + unlinks", err is None
              and pkg1_tid not in AZO["tasks"]
              and not (leluxe.get_row(p1)["data"] or {}).get("source_task_id"))
        AZO["tasks"]["A"]["name"] = "changed by faisal"
        _, err = leluxe.az2_undo(j[2]["id"])
        check("rename undo aborts when AZ (2) drifted",
              err is not None and "changed after" in err)
        AZO["tasks"]["A"]["name"] = "2 U.S. Polo"
        _, err = leluxe.az2_undo(j[2]["id"], user="qais")
        check("rename undone", err is None
              and AZO["tasks"]["A"]["name"] == "10 U.S. Polo")

        # ---- the live regression: the owner had ALREADY hand-organized this
        # order in ClickUp (PACKAGE 1 with the real SENT RD product); a prior
        # organize run created duplicates with the stale local status ----
        oid2 = leluxe._insert_row("order", "Order # HAND", status="order number",
                                  extra={"source_task_id": "ORD2"})
        h1 = leluxe._insert_row("package", "📦 GWD444", parent_local_id=oid2,
                                extra={"tracking_number": "GWD444",
                                       "source_task_id": "DUPPKG"})
        hA = leluxe._insert_row("item", "25 Steve Madden", status="oredered",
                                parent_local_id=h1,
                                extra={"source_task_id": "DUPITEM"})
        AZO["tasks"].update({
            "ORD2": {"id": "ORD2", "name": "Order # HAND", "parent": None,
                     "status": {"status": "order number"}},
            "HANDPKG": {"id": "HANDPKG", "name": "PACKAGE 1", "parent": "ORD2",
                        "status": {"status": "order number"}},
            "REAL25": {"id": "REAL25", "name": "25 Steve Madden",
                       "parent": "HANDPKG", "status": {"status": "sent rd"},
                       "cf": {"f-trk": "GWD444", "f-qty": "25"}},
            "DUPPKG": {"id": "DUPPKG", "name": "📦 GWD444", "parent": "ORD2",
                       "status": {"status": "package"},
                       "tags": [{"name": "otl-push"}],
                       "cf": {"f-trk": "GWD444"}},
            "DUPITEM": {"id": "DUPITEM", "name": "25 Steve Madden",
                        "parent": "DUPPKG", "status": {"status": "oredered"},
                        "tags": [{"name": "otl-push"}]},
        })
        with db.connect() as c:
            c.execute("""INSERT INTO az2_pushes (row_id, task_id, field,
                         old_value, new_value, ts, user, state)
                         VALUES (?,?,?,?,?,?,?,'pushed')""",
                      (h1, "DUPPKG", "pkg_create", "", "📦 GWD444",
                       db.now_iso(), "qais"))
            c.execute("""INSERT INTO az2_pushes (row_id, task_id, field,
                         old_value, new_value, ts, user, state)
                         VALUES (?,?,?,?,?,?,?,'pushed')""",
                      (hA, "DUPITEM", "item_create", "", "25 Steve Madden",
                       db.now_iso(), "qais"))
        # the hand-made parcel must come out CONSISTENT with the rest:
        # renamed "📦 <GWD>" + the 'package' status — products keep their own
        config = cfg.load()
        sts = cfg.get(config, "leluxe.schema.statuses", [])
        cfg.set_path(config, "leluxe.schema.statuses",
                     sts + [{"status": "package", "color": "#8d8d8d",
                             "orderindex": 23, "type": "unstarted"}])
        cfg.save(config)
        dh, err = leluxe.az2_organize(oid2, dry_run=True)
        check("hand-organized order: adopts the owner's package + product, "
              "creates NOTHING", err is None and dh["creates_pkg"] == 0
              and dh["creates_item"] == 0 and dh["moves"] == 0
              and dh["adopts"] == 2 and dh["prunes"] == 2)
        check("...and normalizes the parcel: rename to 📦 GWD + package status",
              dh["renames"] == 1 and dh["status_sets"] == 1)
        reph, err = leluxe.az2_organize(oid2, user="qais")
        check("duplicates pruned, real product status untouched", err is None
              and reph["pruned"] == 2
              and "DUPPKG" not in AZO["tasks"] and "DUPITEM" not in AZO["tasks"]
              and AZO["tasks"]["REAL25"]["status"]["status"] == "sent rd"
              and AZO["tasks"]["REAL25"]["parent"] == "HANDPKG")
        check("hand-made parcel looks like every other one now",
              AZO["tasks"]["HANDPKG"]["name"] == "📦 GWD444"
              and AZO["tasks"]["HANDPKG"]["status"]["status"] == "package")
        check("local rows now link the hand-made originals",
              (leluxe.get_row(h1)["data"] or {}).get("source_task_id") == "HANDPKG"
              and (leluxe.get_row(hA)["data"] or {}).get("source_task_id") == "REAL25")
        dh2, err = leluxe.az2_organize(oid2, dry_run=True)
        check("hand-organized order settles to a no-op", err is None
              and dh2["adopts"] == 0 and dh2["prunes"] == 0
              and dh2["creates_pkg"] == 0 and dh2["creates_item"] == 0
              and dh2["moves"] == 0 and dh2["renames"] == 0
              and dh2["status_sets"] == 0)
    finally:
        leluxe._http = real


def endpoints_gated():
    import app as appmod
    import auth
    bid = db.create_business("Broker Co")
    db.create_user("lx-otlo", auth.hash_pw("secret1"), "admin", "Otlobly Admin", business_id=1)
    db.create_user("lx-brk", auth.hash_pw("secret1"), "admin", "Broker Admin", business_id=bid)
    db.create_user("lx-sales", auth.hash_pw("secret1"), "sales", "Sales", business_id=1)

    def client(u):
        c = appmod.app.test_client()
        c.post("/login", data={"username": u, "password": "secret1"})
        return c

    otlo, brk, sales = client("lx-otlo"), client("lx-brk"), client("lx-sales")
    check("Otlobly admin can read /api/leluxe/orders",
          otlo.get("/api/leluxe/orders").status_code == 200)
    check("broker admin is 403 (feature off)",
          brk.get("/api/leluxe/orders").status_code == 403)
    check("sales is 403 (needs admin_actions)",
          sales.get("/api/leluxe/orders").status_code == 403)
    check("image GET blocks path traversal",
          otlo.get("/api/leluxe/image?file=..%2Fconfig.json").status_code == 404)
    r = otlo.get("/api/leluxe/orders").get_json()
    check("orders payload carries schema + sync",
          "schema" in r and "sync" in r and r.get("list_id") == "L1")
    check("move route blocks a broker (feature off)",
          brk.post("/api/leluxe/move", json={"id": 1, "parent_local_id": 2}).status_code == 403)
    check("move route blocks sales (needs admin_actions)",
          sales.post("/api/leluxe/move", json={"id": 1, "parent_local_id": 2}).status_code == 403)
    check("az2 push blocked for broker + sales",
          brk.post("/api/leluxe/az2_push", json={"row_id": 1}).status_code == 403
          and sales.post("/api/leluxe/az2_push", json={"row_id": 1}).status_code == 403
          and sales.get("/api/leluxe/az2_pushes").status_code == 403)
    check("az2 organize blocked for broker + sales",
          brk.post("/api/leluxe/az2_organize", json={"order_id": 1}).status_code == 403
          and sales.post("/api/leluxe/az2_organize", json={"order_id": 1}).status_code == 403)
    check("diag route blocked for broker + sales",
          brk.get("/api/leluxe/diag").status_code == 403
          and sales.get("/api/leluxe/diag").status_code == 403)


def diagnose_readonly():
    """🩺 Diagnose sync is READ-ONLY and never throws: with no ClickUp token it
    reports the reason instead of failing, and it writes nothing (the row count
    and every sync_state are identical before and after)."""
    import app as appmod
    import auth
    import leluxe

    def snapshot():
        with db.connect() as c:
            return [tuple(r) for r in c.execute(
                "SELECT id, status, sync_state, data_json FROM leluxe_orders "
                "ORDER BY id")]

    before = snapshot()
    out = leluxe.diagnose()
    check("diagnose returns a dict even with no token", isinstance(out, dict))
    check("diagnose reports WHY it could not run",
          bool(out.get("error")) or "stale_count" in out)
    check("diagnose carries the counts block", isinstance(out.get("counts"), dict))
    check("diagnose wrote NOTHING", snapshot() == before)

    db.create_user("dg-otlo", auth.hash_pw("secret1"), "admin", "O", business_id=1)
    c = appmod.app.test_client()
    c.post("/login", data={"username": "dg-otlo", "password": "secret1"})
    r = c.get("/api/leluxe/diag")
    check("diag endpoint answers 200 for an Otlobly admin", r.status_code == 200)
    j = r.get_json()
    check("diag payload is ok-shaped",
          j.get("ok") is True and "counts" in j and "list_id" in j)
    check("diag still wrote nothing via the route", snapshot() == before)


def pkgmail_tracking():
    """Per-package clearance-mail log (one GWD per email): a send stamps the
    cycle, a resend bumps sent_count AND clears the hand-marked reply, and the
    endpoints are admin-only + Otlobly-only like the rest of Leluxe."""
    import app as appmod
    import auth
    db.create_user("pm-otlo", auth.hash_pw("secret1"), "admin", "O", business_id=1)
    c = appmod.app.test_client()
    c.post("/login", data={"username": "pm-otlo", "password": "secret1"})
    r = c.get("/api/leluxe/pkgmail").get_json()
    check("pkgmail starts empty", r["ok"] and r["mail"] == {})
    r = c.post("/api/leluxe/pkgmail/sent",
               json={"gwd": "GWD001", "to": "clear@gaash.com",
                     "subject": "clearance — GWD001"}).get_json()
    check("sent stamps count 1", r["ok"] and r["rec"]["sent_count"] == 1
          and bool(r["rec"]["sent_at"]) and r["rec"]["replied_at"] is None)
    r = c.post("/api/leluxe/pkgmail/reply",
               json={"gwd": "GWD001", "replied": True}).get_json()
    check("reply marked by hand", r["ok"] and bool(r["rec"]["replied_at"]))
    r = c.post("/api/leluxe/pkgmail/reply",
               json={"gwd": "GWD001", "replied": False}).get_json()
    check("reply unmarked", r["ok"] and r["rec"]["replied_at"] is None)
    c.post("/api/leluxe/pkgmail/reply", json={"gwd": "GWD001", "replied": True})
    r = c.post("/api/leluxe/pkgmail/sent",
               json={"gwd": "GWD001", "to": "clear@gaash.com",
                     "subject": "reminder — GWD001"}).get_json()
    check("resend bumps count + restarts the cycle (reply cleared)",
          r["ok"] and r["rec"]["sent_count"] == 2 and r["rec"]["replied_at"] is None)
    r = c.get("/api/leluxe/pkgmail").get_json()
    check("GET lists the record", "GWD001" in r["mail"]
          and r["mail"]["GWD001"]["sent_count"] == 2
          and r["mail"]["GWD001"]["to_email"] == "clear@gaash.com")
    check("sent without gwd is 400",
          c.post("/api/leluxe/pkgmail/sent", json={}).status_code == 400)
    check("reply for an unlogged gwd is 400",
          c.post("/api/leluxe/pkgmail/reply",
                 json={"gwd": "GWD-NOPE"}).status_code == 400)
    bid = db.create_business("PM Broker")
    db.create_user("pm-brk", auth.hash_pw("secret1"), "admin", "B", business_id=bid)
    b = appmod.app.test_client()
    b.post("/login", data={"username": "pm-brk", "password": "secret1"})
    check("broker blocked from pkgmail (feature off)",
          b.get("/api/leluxe/pkgmail").status_code == 403
          and b.post("/api/leluxe/pkgmail/sent",
                     json={"gwd": "GWD001"}).status_code == 403)


def move_between_packages():
    """Move a product to another package: a partial quantity splits off a new
    row (fresh subtask, no re-parent); a whole move re-parents the ClickUp
    subtask via PUT {parent}. Cross-order moves are rejected."""
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
    order, _ = leluxe.save_row({"kind": "order", "name": "Order # MOVE",
                                "status": "order number", "fields": {"NAME": "B5"}})
    pkgA, _ = leluxe.save_row({"kind": "package", "name": "📦 A",
                              "parent_local_id": order["id"],
                              "fields": {"Tracking Number": "GWD-A"}})
    pkgB, _ = leluxe.save_row({"kind": "package", "name": "📦 B",
                              "parent_local_id": order["id"],
                              "fields": {"Tracking Number": "GWD-B"}})
    item, _ = leluxe.save_row({"kind": "item", "name": "8 Watch",
                              "parent_local_id": pkgA["id"],
                              "fields": {"Quantity ordered ": 8, "Total Amount": 800,
                                         "ASIN": "B0WATCH"}})
    with db.connect() as c:                          # seed a photo + true date on the source
        c.execute("UPDATE leluxe_orders SET ordered_at='1780000000000' WHERE id=?", (item["id"],))
    d = leluxe.get_row(item["id"])["data"]; d["image"] = "http://img/watch.jpg"; d["image_asin"] = "B0WATCH"
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), item["id"]))
    qk = "Quantity ordered "

    # ── partial split into a NEW package ──
    summ, err = leluxe.move_item(item["id"], new_package=True, move_qty=1)
    check("split ok", err is None and summ["mode"] == "split" and summ["moved_qty"] == 1)
    src, new = leluxe.get_row(item["id"]), leluxe.get_row(summ["new_id"])
    check("source qty 8→7", leluxe._as_num(src["data"]["fields"][qk]) == 7)
    check("new row qty 1", leluxe._as_num(new["data"]["fields"][qk]) == 1)
    check("amount NOT divided — full ₪ stays on source",
          leluxe._as_num(src["data"]["fields"]["Total Amount"]) == 800)
    check("new row carries NO amount", "Total Amount" not in new["data"]["fields"])
    check("split-off row carries the picture + ASIN",
          new["data"].get("image") == "http://img/watch.jpg"
          and new["data"].get("image_asin") == "B0WATCH"
          and new["data"]["fields"].get("ASIN") == "B0WATCH")
    check("new row not yet in ClickUp", new["clickup_task_id"] is None and new["kind"] == "item")
    check("new row inherits ordered_at", new["ordered_at"] == "1780000000000")
    newpkg = leluxe.get_row(new["parent_local_id"])
    check("new package created under the order",
          newpkg["kind"] == "package" and newpkg["parent_local_id"] == order["id"])
    check("source + new row both dirty",
          src["sync_state"] == "dirty" and new["sync_state"] == "dirty")

    # ── split with no item-level amount just divides the count ──
    it2, _ = leluxe.save_row({"kind": "item", "name": "6 Strap",
                             "parent_local_id": pkgA["id"], "fields": {"Quantity ordered ": 6}})
    s2, _ = leluxe.move_item(it2["id"], dest_parent_local_id=pkgB["id"], move_qty=2)
    n2 = leluxe.get_row(s2["new_id"])
    check("amount-less split: qty only",
          leluxe._as_num(leluxe.get_row(it2["id"])["data"]["fields"][qk]) == 4
          and leluxe._as_num(n2["data"]["fields"][qk]) == 2
          and "Total Amount" not in n2["data"]["fields"])

    # ── cross-order move rejected ──
    order2, _ = leluxe.save_row({"kind": "order", "name": "Order # OTHER"})
    pkgC, _ = leluxe.save_row({"kind": "package", "name": "📦 C", "parent_local_id": order2["id"]})
    _, err2 = leluxe.move_item(item["id"], dest_parent_local_id=pkgC["id"])
    check("cross-order move rejected", err2 is not None)

    # ── whole move A→B re-parents the subtask in ClickUp ──
    real = leluxe._http
    leluxe._http = fake_http
    os.environ["LELUXE_PUSH_DISABLED"] = "0"
    try:
        for _ in range(8):
            leluxe.run_push_pass()
        it_tid = leluxe.get_row(item["id"])["clickup_task_id"]
        b_tid = leluxe.get_row(pkgB["id"])["clickup_task_id"]
        a_tid = leluxe.get_row(pkgA["id"])["clickup_task_id"]
        check("item + both packages pushed", bool(it_tid and a_tid and b_tid))
        check("create recorded pushed.parent = A",
              leluxe.get_row(item["id"])["data"]["pushed"].get("parent") == a_tid)
        CALLS.clear()
        summ2, err3 = leluxe.move_item(item["id"], dest_parent_local_id=pkgB["id"])
        moved = leluxe.get_row(item["id"])
        check("whole move repoints both ids to B",
              err3 is None and summ2["mode"] == "move"
              and moved["parent_local_id"] == pkgB["id"]
              and moved["parent_task_id"] == b_tid and moved["sync_state"] == "dirty")
        for _ in range(2):
            leluxe.run_push_pass()
        reparents = [b for m, u, b in CALLS if m == "PUT" and u.endswith(f"/task/{it_tid}")
                     and isinstance(b, dict) and "parent" in b]
        check("exactly one re-parent PUT to package B",
              len(reparents) == 1 and reparents[0]["parent"] == b_tid)
        check("item synced, pushed.parent now B",
              leluxe.get_row(item["id"])["sync_state"] == "synced"
              and leluxe.get_row(item["id"])["data"]["pushed"].get("parent") == b_tid)
        CALLS.clear()
        for _ in range(2):
            leluxe.run_push_pass()
        check("no spurious re-parent on an unchanged push",
              not [1 for m, u, b in CALLS if m == "PUT" and isinstance(b, dict) and "parent" in b])

        # ── legacy child (pulled, no pushed.parent) adopts its parent, no PUT ──
        with db.connect() as c:
            c.execute("DELETE FROM leluxe_orders")
        leluxe.upsert_from_clickup(_task("LP", "Order # LEG", "order number",
                                         fields=[("NAME", 37)]), SCHEMA)
        leluxe.upsert_from_clickup(_task("LC", "5 Watch", "sent rd", parent="LP",
                                         fields=[("Quantity ordered ", "5")]), SCHEMA)
        leluxe._relink()
        child = leluxe.get_by_task("LC")
        check("pulled child has no pushed.parent",
              "parent" not in (child["data"].get("pushed") or {}))
        leluxe.save_row({"id": child["id"], "kind": "item", "name": "5 Watch EDIT",
                         "parent_local_id": child["parent_local_id"],
                         "fields": {"Quantity ordered ": "5"}})
        CALLS.clear()
        for _ in range(2):
            leluxe.run_push_pass()
        check("legacy child edit fires NO re-parent PUT",
              not [1 for m, u, b in CALLS if m == "PUT" and isinstance(b, dict) and "parent" in b])
        check("legacy child adopts pushed.parent = LP",
              leluxe.get_by_task("LC")["data"]["pushed"].get("parent") == "LP")
    finally:
        leluxe._http = real
        os.environ["LELUXE_PUSH_DISABLED"] = "1"


def regroup_and_sweep():
    """A product's GWD decides its 📦: regroup re-homes tracked products into
    per-tracking packages (backfilling a whole package in place when all its
    products agree on one new GWD), sweeps dead untracked packages two-phase
    (the ClickUp twin is queued only once nothing can cascade), and the
    save_row / move_item hooks fire it. See leluxe.regroup_order()."""
    import time as _t
    OLD = int(_t.time() * 1000) - 7_200_000        # beyond the 1h sweep grace
    F, SF = "Tracking Number", SCHEMA["fields"]
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("DELETE FROM leluxe_cu_deletes")

    def order(name):
        return leluxe._insert_row("order", name)

    def pkg(oid, tn=None, tid=None, fresh=False):
        pid = leluxe._insert_row(
            "package", f"📦 {tn}" if tn else "📦 no tracking",
            parent_local_id=oid, extra={"tracking_number": tn} if tn else {},
            date_created=None if fresh else OLD)
        if tid:
            with db.connect() as c:
                c.execute("UPDATE leluxe_orders SET clickup_task_id=?, "
                          "sync_state='synced' WHERE id=?", (tid, pid))
        return pid

    def item(pid, name, tn=None):
        return leluxe._insert_row("item", name, parent_local_id=pid,
                                  fields={F: tn} if tn else {})

    def queued():
        with db.connect() as c:
            return sorted(r["task_id"] for r in
                          c.execute("SELECT task_id FROM leluxe_cu_deletes"))

    row = leluxe.get_row

    # ── the reported case: a stale "📦 no tracking" package holding products
    # that each got their own GWD later, plus an empty untracked package ──
    oA = order("#61068")
    p1 = pkg(oA)
    a, b = item(p1, "5 Watch", "GWD-1"), item(p1, "13 Watch", "GWD-2")
    c_ = item(p1, "15 Watch", "GWD-2")
    p2 = pkg(oA, tid="cu-empty")                   # empty untracked, pushed
    p3 = pkg(oA, tn="GWD-9")                       # empty TRACKED → real parcel
    out = leluxe.regroup_order(oA)
    check("regroup: moved=3 created=2 swept=2",
          out == {"moved": 3, "backfilled": 0, "created": 2, "swept": 2})
    with db.connect() as c:
        ps = {leluxe._row_tn(row(r["id"]), SF): row(r["id"]) for r in
              c.execute("SELECT id FROM leluxe_orders WHERE parent_local_id=? "
                        "AND kind='package' AND deleted=0", (oA,))}
    check("per-GWD packages exist, tracked shell kept",
          sorted(ps) == ["GWD-1", "GWD-2", "GWD-9"])
    check("products re-homed to their GWD's package",
          row(a)["parent_local_id"] == ps["GWD-1"]["id"]
          and row(b)["parent_local_id"] == ps["GWD-2"]["id"]
          == row(c_)["parent_local_id"] and row(a)["sync_state"] == "dirty")
    check("stale + empty untracked pkgs swept (with marker)",
          row(p1)["deleted"] == 1 and row(p1)["data"].get("swept")
          and row(p2)["deleted"] == 1 and row(p3)["deleted"] == 0)
    check("empty pushed pkg's ClickUp twin queued (nothing can cascade)",
          queued() == ["cu-empty"])
    check("created package carries the Tracking Number field",
          ps["GWD-1"]["data"]["fields"].get(F) == "GWD-1")

    # ── backfill: every product agrees on ONE new GWD → package becomes it ──
    oB = order("#B"); p4 = pkg(oB)
    d1, d2 = item(p4, "d1", "GWD-5"), item(p4, "d2", "GWD-5")
    out = leluxe.regroup_order(oB)
    p4r = row(p4)
    check("backfill in place (no moves, renamed, field set)",
          out == {"moved": 0, "backfilled": 1, "created": 0, "swept": 0}
          and p4r["name"] == "📦 GWD-5" and p4r["data"]["tracking_number"] == "GWD-5"
          and p4r["data"]["fields"].get(F) == "GWD-5"
          and row(d1)["parent_local_id"] == p4 and row(d2)["parent_local_id"] == p4)

    # ── an untracked product never moves; its package survives ──
    oC = order("#C"); p5 = pkg(oC)
    f_, g_ = item(p5, "f"), item(p5, "g", "GWD-7")
    out = leluxe.regroup_order(oC)
    check("tracked product splits off, untracked stays, pkg kept",
          out["moved"] == 1 and row(f_)["parent_local_id"] == p5
          and row(p5)["deleted"] == 0
          and leluxe._row_tn(row(row(g_)["parent_local_id"]), SF) == "GWD-7")
    check("regroup is idempotent",
          leluxe.regroup_all() == {"moved": 0, "backfilled": 0,
                                   "created": 0, "swept": 0})

    # ── move_item: validation precedes the new-package destination ──
    oE = order("#E"); p6 = pkg(oE)
    h = leluxe._insert_row("item", "5 h", parent_local_id=p6,
                           fields={"Quantity ordered ": 5})
    res, err = leluxe.move_item(h, new_package=True, move_qty=0)
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM leluxe_orders WHERE "
                      "parent_local_id=? AND kind='package' AND deleted=0",
                      (oE,)).fetchone()["n"]
    check("qty=0 split rejected BEFORE creating its destination",
          res is None and n == 1)
    res, err = leluxe.move_item(h, new_package=True)
    check("whole move sweeps the emptied untracked source",
          err is None and row(p6)["deleted"] == 1
          and row(res["dest_id"])["deleted"] == 0)

    # ── save_row hook: a changed Tracking Number re-homes the product ──
    oH = order("#H"); p7 = pkg(oH)
    i2, i3 = item(p7, "i2"), item(p7, "i3")
    leluxe.save_row({"id": i2, "kind": "item", "name": "i2", "status": "",
                     "fields": {F: "GWD-8"}, "parent_local_id": p7})
    check("save_row(tracking) fires regroup, untracked sibling untouched",
          leluxe._row_tn(row(row(i2)["parent_local_id"]), SF) == "GWD-8"
          and row(i2)["parent_local_id"] != p7
          and row(i3)["parent_local_id"] == p7)

    # ── conflict-parked products are never touched ──
    oI = order("#I"); p8 = pkg(oI); i4 = item(p8, "i4", "GWD-X")
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET sync_state='conflict' WHERE id=?", (i4,))
    out = leluxe.regroup_order(oI)
    res, err = leluxe.move_item(i4, new_package=True)
    check("conflict row: regroup skips, move refuses",
          out["moved"] == 0 and row(i4)["parent_local_id"] == p8
          and res is None and "review" in (err or ""))

    # ── pull-born row: pushed.parent seeded; CU delete deferred until safe ──
    oJ = order("#J"); pJ = pkg(oJ, tid="cu-p1")
    iJ = item(pJ, "iJ", "GWD-Z"); item(pJ, "iJ2", "GWD-Y")
    with db.connect() as c:                        # as upsert_from_clickup left it
        c.execute("""UPDATE leluxe_orders SET clickup_task_id='cu-i1',
                     parent_task_id='cu-p1', sync_state='synced' WHERE id=?""", (iJ,))
    leluxe.regroup_order(oJ)
    rJ = row(iJ)
    check("pull-born move seeds pushed.parent (PUT will really fire)",
          rJ["sync_state"] == "dirty"
          and (rJ["data"].get("pushed") or {}).get("parent") == "cu-p1")
    check("swept pkg's CU twin NOT queued while the re-parent is unpushed",
          row(pJ)["deleted"] == 1 and "cu-p1" not in queued())
    with db.connect() as c:                        # simulate the push landing
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?", (iJ,)).fetchone()
        dd = json.loads(r["data_json"]); dd["pushed"]["parent"] = "cu-new"
        c.execute("UPDATE leluxe_orders SET sync_state='synced', data_json=? "
                  "WHERE id=?", (json.dumps(dd), iJ))
    leluxe.regroup_order(oJ)
    check("…and queued once it landed", "cu-p1" in queued())

    # ── a just-made empty package gets an hour's grace ──
    oK = order("#K"); pK = pkg(oK, fresh=True)
    out = leluxe.regroup_order(oK)
    check("fresh hand-made empty pkg survives the sweep",
          out["swept"] == 0 and row(pK)["deleted"] == 0)

    # ── a user-hidden child keeps blocking its parent's CU delete ──
    oM = order("#M"); pM = pkg(oM, tid="cu-pm"); iM = item(pM, "iM")
    with db.connect() as c:                        # lxDelete: local hide only
        c.execute("UPDATE leluxe_orders SET clickup_task_id='cu-im', deleted=1 "
                  "WHERE id=?", (iM,))
    leluxe.regroup_order(oM)
    check("hidden child's task blocks the swept pkg's CU delete",
          row(pM)["deleted"] == 1 and "cu-pm" not in queued())


def main():
    db.init_db()
    setup_config()
    print("codecs:");            codecs()
    print("pull / dirty-wins:"); pull_and_dirty_wins()
    print("ghost reclaim:");     relink_reclaims_ghost_packages()
    print("save validation:");   save_row_validation()
    print("claim race:");        claim_race()
    print("push ordering:");     push_ordering()
    print("3-tier push:");       push_3tier()
    print("status-only change:"); status_only_change()
    print("flat tracking:");     flat_tracking_enrichment()
    print("tracking skip rules:"); tracking_skip_rules()
    print("tracking failure stamps:"); tracking_failure_stamps()
    print("tracking results payload:"); tracking_results_payload()
    print("notfound deadline unblock:"); tracking_notfound_still_checks_deadline()
    print("gz dict vs notfound:"); tracking_gz_dict_survives_notfound()
    print("gash status sync:");  gash_status_sync()
    print("migrate grouping:");  migrate_grouping()
    print("image cache:");       image_cache()
    print("move packages:");     move_between_packages()
    print("regroup + sweep:");   regroup_and_sweep()
    print("sync kept report:");  sync_kept_report()
    print("az2 push + undo:");   az2_push_and_undo()
    print("az2 organize:");      az2_organize_flow()
    print("endpoint gates:");    endpoints_gated()
    print("frozen row thaws:"); frozen_row_thaws()
    print("conflict review flow:"); conflict_review_flow()
    print("review-park amnesty:"); review_park_amnesty()
    print("malformed conflict heals:"); malformed_conflict_heals()
    print("auto-pull pass:"); auto_pull_pass()
    print("diagnose read-only:"); diagnose_readonly()
    print("pkg mail:");          pkgmail_tracking()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all leluxe checks passed ✓")


if __name__ == "__main__":
    main()
