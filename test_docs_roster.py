#!/usr/bin/env python3
"""📄 Docs roster — the GAASH mail › Docs tab read straight from ClickUp (2026-09-21).

The tab used to list parcels from the Le Luxe mirror (17 days stale that day) and
never read IT Products, so the two parcels GAASH was asking documents for were
not on it. These checks pin the replacement: docs_roster reads both ClickUp lists,
docs_queue merges them with the boards, a Check stores GAASH's answer + deadline
(never before arrival), the upload wizard's resolvers know an IT-only parcel, the
webhook re-reads the lists by itself, and the bell stays network-free.

The fake tasks copy the REAL list-endpoint shape (captured 2026-09-21 from
/list/901524960550/task): flat tasks with `parent`, `status` = {status, color,
type, orderindex}, dropdown values as the option's ORDERINDEX, and a field with no
value simply has no "value" key. A fake that invents a field proves nothing.

    ./.venv/bin/python test_docs_roster.py
"""
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-docsroster-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "test-worker-token"
os.environ["CLICKUP_API_TOKEN"] = ""          # nothing here may read the real lists
os.environ["DOCS_ROSTER_LIVE"] = "0"
os.environ["LELUXE_PUSH_DISABLED"] = "1"
os.environ["LELUXE_PACE"] = "0"
for k in ("GAASH_MAILER", "LELUXE_DIGEST", "LELUXE_TG_BOT", "FLAG_MACHINE",
          "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)

import app as appmod        # noqa: E402
import auth                 # noqa: E402
import db                   # noqa: E402
import docs_roster as R     # noqa: E402
import gaash_mail as gm     # noqa: E402
import goals                # noqa: E402
import settings as settings_mod  # noqa: E402
import tracking             # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


# ── the real field shapes ────────────────────────────────────────────────────
NAMES = ["QAIS", "FAISAL ", "NO NAME", "Nuray", "red shot", "yahia",
         "ESSAM EL KHATEEB", "Walled k", "qasim bsam"]
GASH = ["STILL NOT ARRIVED", "ARIIVED Destination", "DOCUMENTS SENT", "CLEARED GASH",
        "BRACHA DELIVERED", "Sent but still diidn't clear", "GERZIM DELIVERED",
        " customer ID", "Picked up by Gerizim", "MOC - Palestinian authority"]
F_PHONE = "c9c88074-87c4-47a7-a6e7-d01e86f17e57"


def _opts(names):
    return [{"id": f"opt-{i}-{n.strip()}", "name": n, "color": "#b6b6ff", "orderindex": i}
            for i, n in enumerate(names)]


def _text(fid, name, value=None, typ="short_text"):
    f = {"id": fid, "name": name, "type": typ, "type_config": {},
         "date_created": "1758872988014", "hide_from_guests": False, "required": False}
    if value is not None:
        f["value"] = value
        f["value_richtext"] = ""
    return f


def _drop(fid, name, names, idx=None):
    f = {"id": fid, "name": name, "type": "drop_down",
         "type_config": {"sorting": "manual", "new_drop_down": True, "options": _opts(names)},
         "date_created": "1762206662762", "hide_from_guests": False, "required": False}
    if idx is not None:
        f["value"] = idx                    # ClickUp sends the ORDERINDEX
        f["value_richtext"] = ""
    return f


def task(tid, name, status, stype, parent=None, tn=None, ship=None, gash=None,
         asin=None, qty=None, phone=None, color="#30a46c"):
    return {
        "id": tid, "name": name, "parent": parent, "top_level_parent": parent,
        "url": f"https://app.clickup.com/t/{tid}",
        "status": {"status": status, "id": f"sc_{status}", "color": color,
                   "type": stype, "orderindex": 1},
        "custom_fields": [
            _text(R.F_TRACKING[0], "Tracking Number", tn),
            _drop(R.F_NAME[0], "NAME ON PACKAGEE", NAMES, ship),
            _drop(R.F_GASH[0], "GASH STATUS", GASH, gash),
            _text(R.F_ASIN[0], "ASIN", asin),
            _text(R.F_QTY[0], "Quantity ordered ", qty, typ="number"),
            _text(F_PHONE, "phoneinshippping", phone),
        ],
    }


IT = [
    task("IT-O1", "Order # 114-6250957-3087447", "order number", "open"),
    task("IT-P1", "GIGABYTE GeForce RTX 5060 Ti Gaming OC 8G", "rd", "custom",
         parent="IT-O1", tn="GWD004803012", ship=1, qty="1", phone="972599088063"),
    task("IT-O2", "Order # 113-1739770-1970616", "order number", "open"),
    task("IT-P2", "GIGABYTE Radeon RX 9070 XT", "rd", "custom",
         parent="IT-O2", tn="GWD004802554", ship=8),
    task("IT-P3", "ZOTAC Gaming GeForce RTX 5060 Ti", "received rd", "done",
         tn="GWD004632439", ship=1),
]
LX = [
    task("LX-O1", "Order # 112-0000000-0000001", "order number", "open", ship=3),
    task("LX-K1", "\U0001F4E6 GWD004649674", "package", "unstarted", parent="LX-O1",
         tn="GWD004649674"),
    task("LX-P1", "6 Anne Klein Women's AK/1046CHCV", "rd", "custom", parent="LX-K1",
         asin="B008XT3PH0"),
    task("LX-P2", "2 Nine West Women's Bracelet Watch", "oredered", "unstarted",
         parent="LX-K1", qty="2", color="#9faec7"),
    task("LX-P3", "CASIO G-SHOCK MUDMASTER", "rd", "custom",
         tn="GWD004500016‎", ship=1, gash=7, asin="B01DBKURIA"),
    task("LX-P4", "UPS parcel", "rd", "custom", tn="1ZAG55190406566961"),
    task("LX-P5", "3 Nine West typo", "rd", "custom", tn="GWD0004794032"),
    task("LX-P6", "Cleared watch", "rd", "custom", tn="GWD004700001", gash=3),
    task("LX-P7", "Cancelled watch", "cancelled", "unstarted", tn="GWD004700002"),
]
FEED = {"901524960550": IT, "901520351506": LX}


def fake_fetch(feed=None, fail=()):
    feed = feed or FEED

    def f(list_id):
        if list_id in fail:
            return None, "boom"
        return json.loads(json.dumps(feed.get(list_id) or [])), None
    return f


def _lx_row(rid, tn, data=None, kind="package", parent=None, name=None):
    d = {"tracking_number": tn, "fields": {}}
    d.update(data or {})
    with db.connect() as c:
        c.execute("INSERT INTO leluxe_orders (id,kind,name,status,parent_local_id,data_json,deleted) "
                  "VALUES (?,?,?,?,?,?,0)",
                  (rid, kind, name or f"\U0001F4E6 {tn}", "", parent, json.dumps(d)))


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    db.create_user("emp", auth.hash_pw("s1"), "fulfillment", "E", business_id=1)

    print("— build(): tasks → parcels —")
    P = R.build({"it": IT, "leluxe": LX})
    it1 = P.get("GWD004803012") or {}
    check("an IT product with its own tracking number is a parcel on the IT list",
          it1.get("source") == "it" and it1.get("open") is True and it1.get("lists") == ["it"])
    check("its name on package decodes the dropdown ORDERINDEX and trims (\"FAISAL \" → FAISAL)",
          it1.get("name") == "FAISAL")
    check("its order is the parent 'Order # …' task, with the ClickUp link",
          (it1.get("order") or {}).get("name") == "Order # 114-6250957-3087447"
          and (it1.get("order") or {}).get("url", "").endswith("/IT-O1"))
    check("its product carries qty, ClickUp status and colour",
          [(p["name"], p["qty"], p["status"], p["color"]) for p in it1.get("products") or []]
          == [("GIGABYTE GeForce RTX 5060 Ti Gaming OC 8G", 1, "rd", "#30a46c")])
    check("the phone field is kept for the customer-ID lookup", it1.get("phones") == ["972599088063"])
    lx1 = P.get("GWD004649674") or {}
    check("products under a 📦 container ride the container's number; the container is not a product",
          sorted(p["id"] for p in lx1.get("products") or []) == ["LX-P1", "LX-P2"])
    check("the name on package is inherited from the ORDER when the product leaves it blank",
          lx1.get("name") == "Nuray")
    check("qty: the Quantity field, else the name's leading number",
          {p["id"]: p["qty"] for p in lx1.get("products") or []} == {"LX-P1": 6, "LX-P2": 2})
    check("a stray invisible mark (U+200E) is cleaned off a real ClickUp value",
          "GWD004500016" in P and (P["GWD004500016"].get("gash") == [" customer ID".strip()]))
    check("a UPS number is not a GAASH parcel", not any(g.startswith("1Z") for g in P))
    check("GWD + 10 digits is kept but flagged unusual",
          P.get("GWD0004794032", {}).get("unusual") is True and it1.get("unusual") is False)
    check("GASH STATUS CLEARED GASH (rank 4) = finished", P.get("GWD004700001", {}).get("open") is False)
    check("Le Luxe types 'cancelled' as unstarted - still finished", P.get("GWD004700002", {}).get("open") is False)
    check("a done status type (received rd) = finished", P.get("GWD004632439", {}).get("open") is False)

    print("— refresh(): the table, photos, seeds, failures —")
    # the board already photographed LX-P1 and checked GWD004649674 (stopped, deadline)
    _lx_row(9001, "GWD004649674", {"docs_state": {"state": "stopped", "codes": [], "links": [], "arrived": True},
                                   "docs_checked": "2026-09-20T11:28:26+00:00",
                                   "gaash_deadline": "2026-07-13", "gaash_deadline_checked": "2026-09-15T17:20:01+00:00"})
    _lx_row(9002, "", {"source_task_id": "LX-P1", "image": "https://img.example/lxp1.jpg"}, kind="item",
            name="6 Anne Klein")
    _lx_row(9003, "", {"image": "https://img.example/casio.jpg", "image_asin": "B01DBKURIA"}, kind="item",
            name="casio twin")
    res = R.refresh(force=True, fetch=fake_fetch())
    rows = R.rows()
    check("every GWD parcel of both lists is stored, open or not",
          {"GWD004803012", "GWD004802554", "GWD004632439", "GWD004649674", "GWD004500016",
           "GWD0004794032", "GWD004700001", "GWD004700002"} <= set(rows))
    check("counts are OPEN parcels per list", (res.get("counts") or {}) == {"leluxe": 3, "it": 2})
    check("a version is stamped for the tab's poll", bool(res.get("ver")) and res.get("refreshed") is True)
    photos = {p["id"]: p["image"] for p in rows["GWD004649674"]["cu"]["products"]}
    check("a photo the board already has is reused by ClickUp task id", photos.get("LX-P1") == "https://img.example/lxp1.jpg")
    check("…and by ASIN", rows["GWD004500016"]["cu"]["products"][0]["image"] == "https://img.example/casio.jpg")
    check("no ASIN, no board photo → an empty slot (nothing guessed)",
          rows["GWD004803012"]["cu"]["products"][0]["image"] == "")
    seed = rows["GWD004649674"]["data"]
    check("a new row is seeded from what the board already knew (state + deadline)",
          (seed.get("docs_state") or {}).get("state") == "stopped" and seed.get("gaash_deadline") == "2026-07-13")
    ver1 = R.meta().get("ver")
    res2 = R.refresh(force=True, fetch=fake_fetch())
    check("an unchanged ClickUp read changes nothing and keeps the version",
          res2.get("changed") == [] and R.meta().get("ver") == ver1)
    res3 = R.refresh(force=True, fetch=fake_fetch(fail=("901524960550",)))
    check("a list that fails to load keeps its parcels open (a hiccup never closes them)",
          R.get("GWD004803012")["open"] is True and "IT: boom" in (res3.get("error") or ""))
    lx_less = [t for t in LX if t["id"] != "LX-P5"]
    res4 = R.refresh(force=True, fetch=fake_fetch({"901524960550": IT, "901520351506": lx_less}))
    check("a number that left ClickUp closes, and the version moves",
          R.get("GWD0004794032")["open"] is False and "GWD0004794032" in res4.get("changed", [])
          and R.meta().get("ver") != ver1)
    R.refresh(force=True, fetch=fake_fetch())   # back to the full feed
    check("stale(): never read → stale; just read → fresh", R.stale() is False)

    print("— one task per webhook event, into a slim copy —")
    cache = db.get_setting(R.TASKS_KEY) or {}
    blob = json.dumps(cache)
    check("the saved copy is slim: no dropdown option lists (ClickUp sends ~170 per task)",
          '"type_config"' not in blob and len((cache.get("lists") or {}).get("leluxe") or []) == len(LX))
    NEW = task("IT-P9", "ASUS RTX 5070", "rd", "custom", parent="IT-O2", tn="GWD004809991", ship=6)
    NEW["list"] = {"id": "901524960550", "name": "IT Products"}
    res = R.apply_events(["IT-P9"], fetch_task=lambda tid: (json.loads(json.dumps(NEW)), None))
    check("a tracking number typed on a NEW task appears after re-reading that one task",
          R.get("GWD004809991") and R.get("GWD004809991")["open"] is True
          and R.get("GWD004809991")["cu"]["name"] == "ESSAM EL KHATEEB"
          and "GWD004809991" in res.get("changed", []))
    res = R.apply_events(["IT-P2"], fetch_task=lambda tid: (None, None))
    check("a deleted task's parcel closes", R.get("GWD004802554")["open"] is False
          and "GWD004802554" in res.get("changed", []))
    calls = {"full": 0}
    real_refresh = R.refresh
    R.refresh = lambda force=False, **k: (calls.__setitem__("full", calls["full"] + 1) or {"changed": []})
    try:
        R.apply_events(["IT-P1"], fetch_task=lambda tid: (None, "HTTP 500"))
        check("a task ClickUp cannot answer for → one full read instead (never a partial picture)",
              calls["full"] == 1 and R.get("GWD004803012")["open"] is True)
        R.apply_events([f"T{i}" for i in range(R.MAX_EVENT_TASKS + 1)], fetch_task=lambda tid: (None, None))
        check("a burst bigger than MAX_EVENT_TASKS is one full read", calls["full"] == 2)
    finally:
        R.refresh = real_refresh
    R.refresh(force=True, fetch=fake_fetch())   # back to the full feed (IT-P2 returns, IT-P9 goes)
    check("a full read replaces the copy: the deleted task is back, the event-only task is gone",
          R.get("GWD004802554")["open"] is True and R.get("GWD004809991")["open"] is False)
    ELSE = json.loads(json.dumps([x for x in IT if x["id"] == "IT-P2"][0]))
    ELSE["list"] = {"id": "999", "name": "Somewhere else"}           # home list is NOT ours…
    ELSE["locations"] = [{"id": "901524960550", "name": "IT Products"}]  # …but it is filed in IT
    R.apply_events(["IT-P2"], fetch_task=lambda tid: (ELSE, None))
    check("a task that lives elsewhere but is ALSO filed in one of our lists keeps its parcel open",
          R.get("GWD004802554")["open"] is True)
    import leluxe
    real_http, real_tok = leluxe._http, leluxe._token
    leluxe._token = lambda: "t"
    leluxe._http = lambda url, *a, **k: (200, {"tasks": [IT[1]], "last_page": False})
    try:
        tasks, err = R._fetch_slim("901524960550")
    finally:
        leluxe._http, leluxe._token = real_http, real_tok
    check("a list that never ends is reported as an error, never read as complete",
          tasks is None and "more than 60 pages" in (err or ""))

    print("— docs_queue: ClickUp first, the boards fill in —")
    # a Purchases parcel (Otlobly) and a board-only Le Luxe parcel
    import purchases
    pdb = purchases.load()
    pdb.setdefault("purchase_orders", []).append({
        "po_id": "PO-OTL", "ship_to": "Amin Nagih", "packages": [
            {"package_no": 1, "tracking_number": "GWD004752290", "items": [], "otlobly_status": "",
             "docs_state": {"state": "action", "codes": [818], "arrived": True,
                            "links": [{"url": "https://ops.gaashwd.com/fileUpload?packageId=GWD004752290&type=8", "type": 0}]},
             "docs_checked": "2026-09-20T10:00:00+00:00"}]})
    purchases.save(pdb)
    _lx_row(9004, "GWD004700777", {"gaash_deadline": "2099-01-01"})
    # a stale board twin that still thinks the CLEARED (finished in ClickUp) parcel is open
    _lx_row(9005, "GWD004700001", {})
    with db.connect() as c:
        d = json.loads(c.execute("SELECT data_json FROM gaash_parcels WHERE gwd='GWD004803012'")
                       .fetchone()["data_json"])
        d.update(docs_state={"state": "action", "codes": [818, 816], "arrived": True, "links": [
            {"url": "https://ops.gaashwd.com/fileUpload?packageId=GWD004803012&type=8", "type": 0},
            {"url": "https://ops.gaashwd.com/fileUpload?packageId=GWD004803012&type=6&type=7", "type": 0}]},
            docs_checked="2026-09-21T09:00:00+03:00", gaash_deadline="2026-10-21",
            gaash_deadline_checked="2026-09-21T09:00:00+03:00")
        c.execute("UPDATE gaash_parcels SET data_json=? WHERE gwd='GWD004803012'", (json.dumps(d),))
    q = gm.docs_queue()
    byg = {r["gwd"]: r for r in q["rows"]}
    check("the IT parcel is on the queue as source 'it', from ClickUp",
          byg.get("GWD004803012", {}).get("source") == "it" and byg["GWD004803012"].get("origin") == "clickup")
    check("…yellow, with its deadline and days left", byg["GWD004803012"]["state"] == "action"
          and byg["GWD004803012"]["gaash_deadline"] == "2026-10-21"
          and isinstance(byg["GWD004803012"]["days_left"], int))
    check("…its name on package comes from ClickUp and says so",
          byg["GWD004803012"].get("pname") == "FAISAL" and byg["GWD004803012"].get("pname_src") == "clickup")
    check("…and carries its products, order and ClickUp status for the tab",
          byg["GWD004803012"]["products"][0]["status"] == "rd"
          and byg["GWD004803012"]["order"]["name"].startswith("Order # 114"))
    check("the board's twin fills in a stored answer (the stopped Le Luxe parcel keeps its deadline)",
          byg.get("GWD004649674", {}).get("state") == "stopped"
          and byg["GWD004649674"]["gaash_deadline"] == "2026-07-13"
          and byg["GWD004649674"]["pname"] == "Nuray")
    check("ClickUp says finished → gone, even though a stale board twin says open",
          "GWD004700001" not in byg and "GWD004700002" not in byg and "GWD004632439" not in byg)
    check("a board-only parcel still lists (nothing the boards had is lost)",
          byg.get("GWD004700777", {}).get("origin") == "board")
    check("Otlobly (Purchases) parcels are still served - the tab decides to hide them",
          byg.get("GWD004752290", {}).get("source") == "purchases")
    with db.connect() as c:          # finished in ClickUp, but GAASH is still asking
        c.execute("UPDATE gaash_parcels SET data_json=? WHERE gwd='GWD004700002'",
                  (json.dumps({"docs_state": {"state": "action", "links": [], "codes": [818], "arrived": True},
                               "docs_checked": "2026-09-21T08:00:00+03:00"}),))
    check("a parcel GAASH is still asking about never hides behind a ClickUp status",
          "GWD004700002" in {r["gwd"] for r in gm.docs_queue()["rows"]})
    with db.connect() as c:
        c.execute("UPDATE gaash_parcels SET data_json='{}' WHERE gwd='GWD004700002'")
    order = [r["gwd"] for r in q["rows"]]
    check("yellow first, stopped last", q["rows"][0]["state"] == "action" and q["rows"][-1]["state"] == "stopped")
    check("inside the yellow rows, the fewest days left first",
          order.index("GWD004803012") < order.index("GWD004752290"))

    print("— photos: a dead ASIN costs one metered look-up a week, not one per pass —")
    import amazon_import
    real_ip = amazon_import.import_product
    asked = []
    amazon_import.import_product = lambda a, conf=None, refresh=False: (asked.append(a) or {"error": "no such product"})
    try:
        with db.connect() as c:     # an open parcel whose product has an ASIN and no photo
            c.execute("INSERT OR REPLACE INTO gaash_parcels (gwd, source, open, cu_json, data_json) VALUES (?,?,?,?,?)",
                      ("GWD004808888", "it", 1, json.dumps({"products": [{"id": "X1", "name": "x", "asin": "B0DEAD0000",
                                                                            "image": ""}], "lists": ["it"]}), "{}"))
        R.fill_photos(); R.fill_photos()
        check("the dead ASIN was asked for once, then left alone", asked.count("B0DEAD0000") == 1)
    finally:
        amazon_import.import_product = real_ip
        with db.connect() as c:
            c.execute("DELETE FROM gaash_parcels WHERE gwd='GWD004808888'")

    print("— the bell and the worker never touch the network —")
    real = (goals._fetch_tasks, tracking.docs_status, tracking.get_session, tracking.ops_deadline)

    def boom(*a, **k):
        raise AssertionError("network call on a no-network path")
    goals._fetch_tasks = tracking.docs_status = tracking.get_session = tracking.ops_deadline = boom
    try:
        ok = True
        try:
            gm.docs_queue(names=False)
        except AssertionError:
            ok = False
        check("docs_queue(names=False) - the 60-second bell path - makes no network call", ok)
        n = client("otlo").get("/api/notifications").get_json() or {}
        docs_items = [e for e in (n.get("events") or []) if e.get("type") == "gaash_docs"]
        check("the bell counts Needs upload on Le Luxe + IT only (not Otlobly's, not stopped)",
              len(docs_items) == 1 and docs_items[0]["title"].startswith("1 "))
    finally:
        goals._fetch_tasks, tracking.docs_status, tracking.get_session, tracking.ops_deadline = real

    print("— check(): GAASH's answer, the timeline, the deadline gate —")
    calls = {"ops": 0}
    K3 = {"Statuses": [{"MappedStatusCode": "VM", "StatusDescription": "On the way", "StatusTime": "2026-09-10T10:00:00"},
                       {"MappedStatusCode": "K3", "StatusDescription": "Arrived at destination country", "StatusTime": "2026-09-16T10:00:00"}]}
    VM = {"Statuses": [{"MappedStatusCode": "VM", "StatusDescription": "On the way", "StatusTime": "2026-09-18T10:00:00"}]}
    ANSWER = {"GWD004803012": {"state": "action", "codes": [818], "arrived": True,
                               "links": [{"url": "https://ops.gaashwd.com/fileUpload?packageId=GWD004803012&type=6&type=7&type=8", "type": 0}],
                               "checked": "2026-09-21T20:00:00+03:00"},
              "GWD004802554": {"state": "plain", "codes": [], "links": [], "arrived": False,
                               "checked": "2026-09-21T20:00:00+03:00"}}
    import gerizim
    real2 = (tracking.docs_status, tracking.get_session, tracking.fetch_one, tracking.ops_deadline,
             tracking.cache_put_events, gerizim.track)
    tracking.docs_status = lambda tn, timeout=25: ANSWER.get(tn)
    tracking.get_session = lambda *a, **k: ("https://api", "nonce")
    tracking.fetch_one = lambda tn, api, nonce, lang="en": K3 if tn == "GWD004803012" else VM
    tracking.ops_deadline = lambda tn, timeout=8: (calls.__setitem__("ops", calls["ops"] + 1) or "2026-10-21")
    tracking.cache_put_events = lambda *a, **k: None
    gerizim.track = lambda tn, timeout=15: "notfound"
    try:
        with db.connect() as c:          # forget the injected deadline: the check must find it
            d = json.loads(c.execute("SELECT data_json FROM gaash_parcels WHERE gwd='GWD004803012'")
                           .fetchone()["data_json"])
            for k in ("gaash_deadline", "gaash_deadline_checked"):
                d.pop(k, None)
            c.execute("UPDATE gaash_parcels SET data_json=? WHERE gwd='GWD004803012'", (json.dumps(d),))
        docs = R.check("GWD004803012")
        d = R.get("GWD004803012")["data"]
        check("the answer is stored on the roster row", docs["state"] == "action"
              and d["docs_state"]["state"] == "action" and d["docs_checked"] == "2026-09-21T20:00:00+03:00")
        check("…with the GAASH timeline and the arrival (K3)",
              (d.get("tracking_status") or {}).get("code") == "K3" and d.get("gaash_arrival", "").startswith("2026-09-16"))
        check("…and, having landed, GAASH's deadline", d.get("gaash_deadline") == "2026-10-21" and calls["ops"] == 1)
        check("the asked documents read back for the wizard (all three slots)",
              gm.docs_asked_types("GWD004803012") == [6, 7, 8])
        R.check("GWD004802554")
        d2 = R.get("GWD004802554")["data"]
        check("NOT arrived → the deadline is never asked for (it would start GAASH's clock)",
              calls["ops"] == 1 and not d2.get("gaash_deadline") and d2.get("deadline_skipped"))
        R.check("GWD004803012")
        check("a fresh post-arrival deadline is not re-read on every check", calls["ops"] == 1)
        tracking.docs_status = lambda tn, timeout=25: None
        R.check("GWD004803012")
        d = R.get("GWD004803012")["data"]
        check("a FAILED lookup stamps docs_error and keeps the last answer and its age",
              d["docs_state"]["state"] == "action" and d.get("docs_error")
              and d["docs_checked"] == "2026-09-21T20:00:00+03:00")
        tracking.docs_status = lambda tn, timeout=25: ANSWER.get(tn)
        before = calls["ops"]
        tracking.get_session = lambda *a, **k: (_ for _ in ()).throw(AssertionError("timeline read"))
        R.check("GWD004803012", with_tracking=False)
        tracking.get_session = lambda *a, **k: ("https://api", "nonce")
        check("the after-upload re-check reads the banner only (no timeline, no deadline)",
              calls["ops"] == before and R.get("GWD004803012")["data"]["docs_state"]["state"] == "action")
        r = client("emp").post("/api/gaash/docs_check", json={"tracking": "GWD004803012"}).get_json()
        check("the tab's Check route returns the refreshed deadline for the row",
              r.get("ok") and (r.get("row") or {}).get("gaash_deadline") == "2026-10-21"
              and (r.get("docs") or {}).get("state") == "action")
    finally:
        (tracking.docs_status, tracking.get_session, tracking.fetch_one, tracking.ops_deadline,
         tracking.cache_put_events, gerizim.track) = real2

    print("— the upload wizard's resolvers know an IT-only parcel —")
    settings_mod.apply({"gaash_mail": {"default_name": "FAISAL", "name_ids": {"FAISAL": "123456789"}}})
    check("parcel_name reads ClickUp's NAME ON PACKAGEE", gm.parcel_name("GWD004802554") == "qasim bsam")
    check("…and beats the default for a Le Luxe parcel the mirror never named",
          gm.parcel_name("GWD004649674") == "Nuray")
    check("the ID follows the name (Settings name → ID)", gm.id_number_for_email("GWD004803012") == "123456789")
    check("the declaration lists the real ClickUp items",
          gm.package_contents("GWD004649674") == [{"title": "6 Anne Klein Women's AK/1046CHCV", "qty": 6},
                                                  {"title": "2 Nine West Women's Bracelet Watch", "qty": 2}])
    check("the originality declaration names the Amazon order",
          gm.package_order_code("GWD004803012") == "114-6250957-3087447")
    check("an IT-only parcel is a known parcel (default name, name-list hint)",
          "GWD004803012" in gm._all_parcel_gwds() and gm.parcel_board_map().get("GWD004803012") == "leluxe")
    import gaash_upload
    asked_for, real_pi = [], gaash_upload.page_info
    # never the real ops page from a test: reading it is what starts GAASH's link
    gaash_upload.page_info = lambda gwd, types: (asked_for.append(list(types)) or
                                                 {"slots": [{"type": t, "label": f"slot {t}"} for t in types]})
    try:
        plan = client("emp").get("/api/gaash/upload/plan?gwd=GWD004803012").get_json() or {}
    finally:
        gaash_upload.page_info = real_pi
    check("the upload wizard opens with EVERY slot GAASH asked for (not the passport fallback)",
          plan.get("ok") and plan.get("asked_types") == [6, 7, 8] and asked_for == [[6, 7, 8]])
    check("…and its declaration reads the ClickUp name",
          (plan.get("declaration") or {}).get("name") == "FAISAL")

    print("— live: the webhook re-reads ClickUp, debounced; a new parcel checks itself —")
    check("DOCS_ROSTER_LIVE=0: nothing is scheduled", R.schedule_refresh() is False)
    seen = {"refresh": 0, "events": [], "check": []}
    real3 = (R.refresh, R.apply_events, R.check, R.DEBOUNCE_S, R.MIN_GAP_S, R.fill_photos)
    os.environ["DOCS_ROSTER_LIVE"] = "1"
    R.DEBOUNCE_S, R.MIN_GAP_S = 0.3, 0.5
    R.fill_photos = lambda *a, **k: 0

    def fake_refresh(force=False, **k):
        seen["refresh"] += 1
        return {"changed": [], "refreshed": True}

    def fake_events(ids, **k):
        seen["events"].append(list(ids))
        return {"changed": ["GWD004809999"], "refreshed": True}
    R.refresh, R.apply_events = fake_refresh, fake_events
    R.check = lambda tn: (seen["check"].append(tn) or {"state": "info", "links": [], "codes": [], "arrived": True})
    try:
        with db.connect() as c:          # the parcel ClickUp just gained
            c.execute("INSERT INTO gaash_parcels (gwd, source, open, cu_json, data_json) VALUES (?,?,?,?,?)",
                      ("GWD004809999", "it", 1, json.dumps({"products": [], "lists": ["it"]}), "{}"))
        db.set_setting(goals.WEBHOOK_KEY, {"endpoint": "https://x/webhook/clickup",
                                           "hooks": {"901524960550": {"id": "w1", "secret": "s3"}}})
        tc = appmod.app.test_client()
        payload = b'{"event":"taskUpdated","task_id":"IT-P9"}'
        sig = hmac.new(b"s3", payload, hashlib.sha256).hexdigest()
        tc.post("/webhook/clickup", data=payload, headers={"X-Signature": "0" * 64})
        time.sleep(0.8)
        check("a bad signature schedules nothing", seen["refresh"] == 0 and seen["events"] == [])
        for _ in range(3):              # a burst of deliveries (one ClickUp edit fires several)
            tc.post("/webhook/clickup", data=payload, headers={"X-Signature": sig})
        for _ in range(40):
            if seen["check"]:
                break
            time.sleep(0.1)
        time.sleep(0.6)
        check("a burst of verified deliveries = ONE pass, re-reading only the task they name",
              seen["events"] == [["IT-P9"]] and seen["refresh"] == 0)
        check("the parcel that just appeared got its first GAASH check by itself",
              seen["check"] == ["GWD004809999"])
        v1 = R.meta().get("ver")
        check("…claimed once across workers: a second pass does not re-check it",
              R.auto_check("GWD004809999") is None and seen["check"] == ["GWD004809999"])
        check("the version moved, so an open tab reloads", bool(v1))
        db.set_setting("docs:autocheck:GWD004809998", "2026-01-01T00:00:00+03:00")
        check("a claim left by a check cut off halfway expires (retried after an hour)",
              R._claim("docs:autocheck:GWD004809998") is True
              and R._claim("docs:autocheck:GWD004809998") is False)
    finally:
        R.refresh, R.apply_events, R.check, R.DEBOUNCE_S, R.MIN_GAP_S, R.fill_photos = real3
        os.environ["DOCS_ROSTER_LIVE"] = "0"

    print("— routes —")
    v = client("emp").get("/api/gaash/docs_roster/version").get_json() or {}
    check("the tab's poll route answers from local state", v.get("ok") and "ver" in v and "live" in v)
    check("the poll is gated like the tab", appmod.app.test_client().get("/api/gaash/docs_roster/version").status_code in (401, 403))
    qr = client("emp").get("/api/gaash/docs_queue").get_json() or {}
    check("the queue route ships the roster status beside the rows",
          qr.get("ok") and isinstance(qr.get("roster"), dict) and "live" in qr["roster"])

    print("――――――――――――――――――――――")
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All docs-roster checks passed ✓")


if __name__ == "__main__":
    main()
