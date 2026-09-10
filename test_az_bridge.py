#!/usr/bin/env python3
"""
Self-checks: the AZ Studio roster bridge, depth 1 (2026-09-10).

AZ Studio pushes its buying-account roster to /api/worker/az_roster (worker bearer);
az_roster.py keeps the freshest copy by the snapshot's own timestamp; recommend.py
decides and ranks (ready tag + Accounts-Tool READY + no ban/quarantine/burned IP/dead
proxy + not in use + card and address saved, then fewest orders in 30 days, longest since
the last order, lowest fraud) and FAILS CLOSED on a stale roster; az.py reads the bridge
first and the pushed copy second so the profile popup and the Leluxe goal's ready-profile
line work without a live link; the pickers and the shopping-list export are wired.

    ./.venv/bin/python test_az_bridge.py
"""

import datetime as dt
import os
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-azbridge-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["OTLOBLY_WORKER_TOKEN"] = "w" * 40
os.environ["AZ_STUDIO_URL"] = "http://127.0.0.1:9"        # nothing listens: the bridge is down
os.environ.pop("AZ_OTLOBLY_TOKEN", None)

import app as appmod   # noqa: E402
import auth            # noqa: E402
import az              # noqa: E402
import az_roster       # noqa: E402
import db              # noqa: E402
import recommend       # noqa: E402

HERE = Path(__file__).resolve().parent
fails = []
NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def prof(name, pid, tags=("READY TO ORDER 2",), verdict="READY", **kw):
    row = {"profile_id": pid, "folder_id": "fA", "folder": "RD", "name": name,
           "tags": list(tags), "all_tags": list(tags), "last_fraud": kw.pop("fraud", 10),
           "in_use": False, "running": False, "quarantined": False, "ip_burned": False,
           "proxy_dead": False, "last_ip": "1.2.3.4", "proxy_host": "geo.example",
           "badges": {"card_set": True, "address_set": True, "cart_items": 0, "in_cart": False,
                      "order_status": "card_ready"},
           "acctool": {"verdict": verdict, "why": "never ran an RD; 1 clean order"}}
    row.update(kw)
    return row


def payload(rows, synced_ts, host="az-studio", partial=False):
    return {"ok": True, "host": host, "synced_ts": synced_ts, "partial": partial,
            "fleet_complete": not partial, "skipped_folders": 0, "count": len(rows),
            "profiles": rows, "acctool": {"ok": True, "error": "", "board_synced_at": "2026-09-10T08:00:00",
                                          "board_age_days": 0}}


def test_recommend():
    print("RECOMMEND (pure):")
    hist = recommend.history(
        [{"profile_box": "E-B50", "order_placed": "2026-09-01"},
         {"profile_box": "e-b50 ", "created_at": "2026-06-01T10:00:00"},
         {"profile_box": "B70", "order_placed": ""}],
        [{"code": "S-B32", "ordered_at": "2026-08-28"}, {"code": "S-B32", "dated_at": "2026-05-01"}],
        now=NOW.date())
    check("history counts POs + board orders per account (case/space-insensitive)",
          hist["e-b50"]["orders"] == 2 and hist["s-b32"]["orders"] == 2)
    check("history: only the last 30 days count as recent", hist["e-b50"]["orders_30d"] == 1
          and hist["s-b32"]["orders_30d"] == 1 and hist["b70"]["orders_30d"] == 0)
    check("history keeps the newest order date", hist["e-b50"]["last_order_at"] == "2026-09-01")

    rows = [prof("E-B50", "p1", fraud=12), prof("S-B32", "p2", fraud=5),
            prof("B70", "p3", fraud=1), prof("B71", "p4", fraud=1),
            prof("B72", "p5", fraud=1, in_use=True),
            prof("B73", "p6", tags=("READY TO ORDER 2", "Amazon ban")),
            prof("B74", "p7", verdict="DEAD"),
            prof("B74b", "p7b", verdict="UNKNOWN"), prof("B74c", "p7c", verdict="IN_PLAY"),
            prof("B74d", "p7d", verdict="UNUSED"), prof("B74e", "p7e", verdict="UNPROVEN"),
            prof("B75", "p8", tags=("az-good",)),
            prof("B76", "p9", quarantined=True),
            prof("B77", "p10", badges={"card_set": True, "address_set": False}),
            prof("B78", "p11", proxy_dead=True), prof("B79", "p12", ip_burned=True)]
    hist = recommend.history([{"profile_box": "E-B50", "order_placed": "2026-09-01"},
                              {"profile_box": "B70", "order_placed": "2026-08-01"},
                              {"profile_box": "B71", "order_placed": "2026-01-01"}],
                             [{"code": "S-B32", "ordered_at": "2026-08-28"}], now=NOW.date())
    fresh = (NOW - dt.timedelta(minutes=30)).timestamp()
    r = recommend.rank(rows, hist, synced_ts=fresh, now=NOW)
    by = {x["name"]: x for x in r["rows"]}
    check("a fresh roster is not stale", r["stale"] is False and abs(r["age_min"] - 30) < 0.1)
    check("eligible = ready tag + not spent/banned/in play/unknown + clean flags + free",
          {x["name"] for x in r["rows"] if x["eligible"]} == {"E-B50", "S-B32", "B70", "B71", "B74d", "B74e", "B77"})
    blk = {n: by[n]["blockers"] for n in by}
    check("in use is a blocker", any("in use" in b for b in blk["B72"]))
    check("a ban tag is a blocker", any("amazon ban" in b for b in blk["B73"]))
    check("Accounts Tool spent / unknown / in play are blockers (with the why)",
          any("dead" in b for b in blk["B74"]) and any("unknown" in b for b in blk["B74b"])
          and any("in play" in b for b in blk["B74c"]))
    check("UNUSED and UNPROVEN are allowed (a fresh account is not spent)", by["B74d"]["eligible"] and by["B74e"]["eligible"]
          and any("unused" in w for w in by["B74d"]["why"]))
    check("missing ready tag is a blocker", any("READY TO ORDER 2" in b for b in blk["B75"]))
    check("quarantine / dead proxy / burned IP block",
          any("quarantined" in b for b in blk["B76"]) and any("dead proxy" in b for b in blk["B78"])
          and any("burned IP" in b for b in blk["B79"]))
    check("no address saved is a NOTE, not a blocker", by["B77"]["eligible"] and any("address" in w for w in by["B77"]["why"]))
    order = [x["name"] for x in r["rows"] if x["eligible"]]
    # 0 recent orders first. Among those: card+address saved before not (B77 last of them),
    # READY before UNUSED before UNPROVEN, then longest rest: B71 (January) → B70 (August)
    # → B74d (unused, never used) → B74e (unproven) → B77 (no address). Then the two with one
    # recent order each: S-B32 (13 days ago) before E-B50 (9 days ago).
    check("ranked: fewest recent orders, card saved, READY<UNUSED<UNPROVEN, longest rest",
          order == ["B71", "B70", "B74d", "B74e", "B77", "S-B32", "E-B50"])
    check("the top row is the recommendation", r["recommended"] == order[0] and by[order[0]]["rank"] == 1)
    check("why explains the pick", any("order" in w for w in by["B71"]["why"]) and any("tagged" in w for w in by["B71"]["why"]))
    stale = recommend.rank(rows, hist, synced_ts=(NOW - dt.timedelta(hours=3)).timestamp(), now=NOW)
    check("a roster older than 2 h recommends NOTHING (fail closed)",
          stale["stale"] and stale["recommended"] is None and stale["eligible"] == 0
          and all("stale" in b for x in stale["rows"] for b in x["blockers"][:1]))
    check("no synced_ts also fails closed", recommend.rank(rows, hist, synced_ts=None, now=NOW)["stale"])
    ex = recommend.rank(rows, hist, synced_ts=fresh, now=NOW, exclude=["b71", "B70", "B74d", "B74e", "B77"])
    check("exclude drops names already used on the order", ex["recommended"] == "S-B32"
          and not any(x["name"] in ("B71", "B70") for x in ex["rows"]))
    half = recommend.evaluate({"name": "X1"}, {}, now=NOW.date())
    check("a half-shaped row never raises and is not eligible", half["eligible"] is False)


def test_store():
    print("ROSTER STORE:")
    db.init_db()
    t1 = (NOW - dt.timedelta(minutes=20)).timestamp()
    rows = [prof("E-B50", "p1", proxy_username="secret-user", notes="private", in_use_by="a@b"),
            prof("S-B32", "p2")]
    r = az_roster.store(payload(rows, t1, host="m3"))
    check("a first roster is stored", r["stored"] and r["count"] == 2)
    doc = az_roster.load()
    p = next(x for x in doc["profiles"] if x["name"] == "E-B50")
    check("rows are re-whitelisted on our side (no proxy username / notes / email)",
          "proxy_username" not in p and "notes" not in p and "in_use_by" not in p)
    check("the popup fields travel (exit IP, proxy host)", p["last_ip"] == "1.2.3.4" and p["proxy_host"] == "geo.example")
    check("badges and the verdict are kept", p["badges"]["card_set"] is True and p["acctool"]["verdict"] == "READY")
    older = az_roster.store(payload([prof("E-B50", "p1")], t1 - 3600, host="m3-old"))
    check("an OLDER snapshot never overwrites the copy we hold", older["ok"] and not older["stored"]
          and az_roster.load()["host"] == "m3")
    newer = az_roster.store(payload([prof("E-B50", "p1"), prof("S-B32", "p2"), prof("B70", "p3")],
                                    t1 + 600, host="az-studio"))
    check("a NEWER snapshot replaces it", newer["stored"] and az_roster.load()["host"] == "az-studio"
          and az_roster.load()["count"] == 3)
    check("no profiles / no synced_ts are refused",
          not az_roster.store({"profiles": []})["ok"] and not az_roster.store({"profiles": [prof("A", "x")]})["ok"])
    m = az_roster.meta(now=NOW.timestamp())
    check("meta reports the age and freshness", m["have"] and m["host"] == "az-studio" and abs(m["age_min"] - 10) < 0.1
          and m["stale"] is False)
    check("meta flags a stale copy", az_roster.meta(now=NOW.timestamp() + 3 * 3600)["stale"] is True)


def test_http():
    print("HTTP:")
    c = appmod.app.test_client()
    t = time.time() - 60
    body = payload([prof("E-B50", "p1"), prof("S-B32", "p2", verdict="DEAD"), prof("B70", "p3", in_use=True)], t)
    check("worker POST without the bearer is 401", c.post("/api/worker/az_roster", json=body).status_code == 401)
    r = c.post("/api/worker/az_roster", json=body, headers={"Authorization": "Bearer " + "w" * 40})
    check("worker POST with the bearer stores the roster", r.status_code == 200 and r.get_json()["stored"])
    r = c.get("/api/worker/az_roster", headers={"Authorization": "Bearer " + "w" * 40})
    check("worker GET reports what we hold", r.status_code == 200 and r.get_json()["count"] == 3
          and r.get_json()["stale"] is False)
    check("a bad payload is 400", c.post("/api/worker/az_roster", json={"profiles": []},
                                         headers={"Authorization": "Bearer " + "w" * 40}).status_code == 400)
    check("staff routes need a login", c.get("/api/az/roster").status_code in (401, 302)
          and c.get("/api/az/recommend").status_code in (401, 302))
    db.create_user("az-op", auth.hash_pw("s1"), "operator", "Runner", business_id=1)
    c.post("/login", data={"username": "az-op", "password": "s1"})
    d = c.get("/api/az/roster").get_json()
    check("operator reads the roster", d.get("have") and len(d["profiles"]) == 3)
    d = c.get("/api/az/recommend").get_json()
    check("recommend answers from the stored roster", d["ok"] and d["recommended"] == "E-B50"
          and d["eligible"] == 1 and d["total"] == 3 and d["ready_tag"] == "READY TO ORDER 2")
    check("a spent account is listed with its blocker", any(x["name"] == "S-B32" and not x["eligible"]
          and any("dead" in b for b in x["blockers"]) for x in d["rows"]))
    check("recommend carries the freshness facts", d["stale"] is False and d["host"] == "az-studio")
    d = c.get("/api/az/recommend?exclude=E-B50").get_json()
    check("exclude works over HTTP", d["recommended"] is None and all(x["name"] != "E-B50" for x in d["rows"]))
    # az.py: the bridge is down (port 9) — the pushed copy answers
    az.bust_cache()
    check("az.healthy() is true on a fresh pushed roster with no live link", az.healthy(timeout=1) is True)
    info = az.profile_info("E-B50", force=True)
    check("the profile popup reads the pushed copy", info.get("box") == "E-B50" and info.get("last_ip") == "1.2.3.4"
          and info.get("proxy") == "geo.example")
    check("an unknown box is still an honest answer", "No Multilogin profile" in az.profile_info("ZZ-9").get("error", ""))
    import leluxe_goal
    s = leluxe_goal.profiles_summary(refresh=True)
    check("the Leluxe goal's ready-profile line works from the pushed copy",
          s.get("offline") is False and s.get("count") == 3 and s.get("total_profiles") == 3)
    from urllib import error
    e = error.HTTPError("http://x", 401, "UNAUTHORIZED", {}, None)
    check("a 401 is explained as a closed door, not a dead server", "login required" in az._why(e))


def test_wiring():
    print("SOURCE WIRING:")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    for s in ("function azRosterEnsure(", "function azBoxMenu(", "function azRecoLine(", "function azBoxRowsHtml(",
              "let PO_BOXES=", "function poShoppingList(", "function poShoppingRows(", "poeBoxPick", "noBoxPick",
              "${azBoxMenu('poeBoxPick', p.profile_box)}", "${azBoxMenu('noBoxPick', NO.profile)}",
              "${azRecoLine('noBoxUse', NO.profile)}", '<input class="pobox mono" list="poBoxList"'):
        check(f"index.html has {s[:44]}", s in idx)
    check("the typed field is still there twice (nothing removed)", idx.count('<input class="pobox mono" list="poBoxList"') == 2)
    check("the picker never re-renders a form: rows refresh in place", ".az-box-rows" in idx and "el.dataset.pick" in idx)
    pj = (HERE / "static" / "ds" / "purchases.js").read_text(encoding="utf-8")
    check("purchases.js row menu offers the shopping list", "Shopping list (CSV)" in pj and "Copy shopping list" in pj)
    azs = (HERE / "az.py").read_text(encoding="utf-8")
    check("az.py reads the bridge with its own bearer", "AZ_STUDIO_URL" in azs and "AZ_OTLOBLY_TOKEN" in azs
          and "/api/otlobly/profiles" in azs and "/api/otlobly/health" in azs)
    check("az.py no longer calls the closed /api/all_profiles", 'f"{AZ_APP}/api/all_profiles"' not in azs)
    ap = (HERE / "app.py").read_text(encoding="utf-8")
    for route in ('"/api/worker/az_roster"', '"/api/az/roster"', '"/api/az/recommend"'):
        check(f"app.py has {route}", route in ap)
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    check("service worker cache bumped (v26)", 'const CACHE = "otl-off-v26"' in sw)
    check("recommend fails closed at 2 hours", recommend.STALE_HOURS == 2 and az_roster.STALE_MIN == 120)
    csv_rows = [["https://www.amazon.com/dp/B0TEST", "12.50", 2], ["https://x/y,z", "", 1]]
    check("the shopping list is link,price,qty (AZ Studio's positional layout)",
          idx.count('["link,price,qty"]') == 1)


def main():
    test_recommend()
    test_store()
    test_http()
    test_wiring()
    print("――――――――――――――――")
    if fails:
        print(f"FAILED {len(fails)}: " + "; ".join(fails))
        raise SystemExit(1)
    print("AZ bridge (depth 1): all checks passed")


if __name__ == "__main__":
    main()
