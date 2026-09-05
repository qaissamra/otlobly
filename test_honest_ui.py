#!/usr/bin/env python3
"""
Self-checks: the UI can tell WHY a pane failed to load.

Three failures used to look identical on screen — a corrupt database, an expired
session, a network blip — one orange "couldn't load — retry". Pinned here: /api/*
answers JSON with a reason for 401/403/404/500 (pages keep their redirects), the
bell carries the database's health, health.json survives writers that don't know
each other's keys, and the shell carries the banner + reason plumbing.

    ./.venv/bin/python test_honest_ui.py
"""
import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-honest-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "otlobly.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["DB_SENTINEL_OFF"] = "1"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402

fails = []
REPO = Path(__file__).resolve().parent


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def api_failures_are_json_with_a_reason():
    cl = appmod.app.test_client()
    r = cl.get("/api/notifications")
    body = r.get_json() or {}
    check("anonymous /api/* → JSON 401 with a reason", r.status_code == 401 and body.get("ok") is False
          and "session" in body.get("error", "") and body.get("auth") is True)
    r = cl.get("/api/definitely-not-a-route")
    check("unknown /api/* → JSON 404", r.status_code == 404 and (r.get_json() or {}).get("ok") is False)
    r = cl.get("/app", follow_redirects=False)
    check("pages keep their redirect/HTML (no JSON leak)", r.status_code in (302, 401, 200)
          and not (r.content_type or "").startswith("application/json"))
    db.create_user("sales1", auth.hash_pw("s1"), "sales", "S", business_id=1)
    cl.post("/login", data={"username": "sales1", "password": "s1"})
    r = cl.get("/api/users")
    if r.status_code == 403:
        check("forbidden /api/* → JSON 403", (r.get_json() or {}).get("auth") is True)
    else:
        print(f"  --  (/api/users answered {r.status_code} for a sales user — 403 shape not exercised)")


def the_bell_carries_db_health_and_health_json_merges():
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    cl = appmod.app.test_client()
    cl.post("/login", data={"username": "otlo", "password": "s1"})
    db.write_health({"ok": True, "error": "", "repairing": False, "maintenance": False,
                     "at": db.now_iso(), "last_repair": "20260905-101048"})
    db.write_health({"ok": True, "error": "", "repairing": False, "maintenance": False, "at": db.now_iso()})
    h = json.loads(db.health_path().read_text())
    check("a later writer keeps the master's last_repair stamp", h.get("last_repair") == "20260905-101048")
    d = cl.get("/api/notifications").get_json() or {}
    check("bell says db ok", d.get("db", {}).get("ok") is True)
    db.write_health({"ok": False, "repairing": True, "error": "Tree 24 page 726", "at": db.now_iso()})
    d = cl.get("/api/notifications").get_json() or {}
    check("bell says repairing", d.get("db", {}).get("repairing") is True and "726" in d["db"].get("error", ""))
    db.write_health({"ok": True, "repairing": False, "error": ""})


def the_shell_has_the_plumbing():
    html = (REPO / "web" / "index.html").read_text()
    check("dbBanner element", 'id="dbBanner"' in html)
    check("setDbState + apiFailReason helpers", "function setDbState(" in html and "function apiFailReason(" in html)
    check("the bell poll drives the banner", "if(d.db) setDbState(d.db);" in html)
    check("the GAASH panes show the reason", html.count("apiFailReason()") >= 6)
    check("a repairing database retries in 30 s, not 60", "LAST_API_FAIL.db)?30000:60000" in html)


def main():
    db.init_db()
    print("API failures:");        api_failures_are_json_with_a_reason()
    print("the bell + health.json:"); the_bell_carries_db_health_and_health_json_merges()
    print("the shell:");           the_shell_has_the_plumbing()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all honest-ui checks passed ✓")


if __name__ == "__main__":
    main()
