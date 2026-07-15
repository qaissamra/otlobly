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
        # like live: GERZIM DELIVERED exists, تم التسليم / SMS options do NOT (yet)
        "GASH STATUS": {"id": "f-gs", "type": "drop_down",
                        "options": [{"id": "opt-gzd", "name": "GERZIM DELIVERED", "orderindex": 6, "color": "#b6b6ff"},
                                    {"id": "opt-pug", "name": "Picked up by Gerizim", "orderindex": 8, "color": "#edadc8"}]},
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
        n = leluxe.apply_gash_status()
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
        check("idempotent: same stage queues nothing", leluxe.apply_gash_status() == 0)
        # once تم التسليم exists (added in ClickUp UI → Discover), it's preferred
        config = cfg.load()
        config["leluxe"]["schema"]["fields"]["GASH STATUS"]["options"].append(
            {"id": "opt-tam", "name": "تم التسليم", "orderindex": 10, "color": "#2ecd6f"})
        cfg.save(config)
        n = leluxe.apply_gash_status()
        r4 = leluxe.get_row(row["id"])
        check("تم التسليم preferred once its option exists",
              n == 1 and r4["data"]["fields"].get("GASH STATUS") == "تم التسليم")
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
              leluxe.apply_gash_status() == 0)
    finally:
        leluxe._http = real
        os.environ["LELUXE_PUSH_DISABLED"] = "1"


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


def main():
    db.init_db()
    setup_config()
    print("codecs:");            codecs()
    print("pull / dirty-wins:"); pull_and_dirty_wins()
    print("save validation:");   save_row_validation()
    print("claim race:");        claim_race()
    print("push ordering:");     push_ordering()
    print("3-tier push:");       push_3tier()
    print("status-only change:"); status_only_change()
    print("flat tracking:");     flat_tracking_enrichment()
    print("gash status sync:");  gash_status_sync()
    print("migrate grouping:");  migrate_grouping()
    print("image cache:");       image_cache()
    print("endpoint gates:");    endpoints_gated()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all leluxe checks passed ✓")


if __name__ == "__main__":
    main()
