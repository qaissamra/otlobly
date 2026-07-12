#!/usr/bin/env python3
"""
Self-check: the Settings "🌐 Website sections" panel is gated to business #1.

Those checkboxes toggle sections of Otlobly's public site (/pricing, the landing
page) — including an "Otlobly Membership" toggle — so a broker tenant seeing them
is a white-label leak. The shell HTML is shared by every tenant; the gate is the
boot-time hide() on ME.business.id, same pattern as the Brokers nav button.

    ./.venv/bin/python test_website_sections_gate.py
"""

import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-secgate-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


GATE = re.compile(r'hide\("websiteSectionsPanel",\s*!!\(ME\.business\s*&&\s*ME\.business\.id===1\)\)')


def main():
    db.init_db()

    # 1) The shell source: the panel carries the id and the boot JS gates it on business #1.
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("panel has the gate id (exactly once)", html.count('id="websiteSectionsPanel"') == 1)
    anchor = html.find('id="websiteSectionsPanel"')
    check("the id is on the Website-sections panel",
          0 <= anchor and "🌐 Website sections" in html[anchor:anchor + 200])
    check("boot JS hides the panel for business != 1", bool(GATE.search(html)))

    # 2) Live shell + tenant data: broker admin gets the gate JS and a business.id != 1,
    #    so the hide() evaluates false for them; Otlobly's admin stays business 1 (unchanged).
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "O", business_id=1)
    co = client("otlo")
    j = co.post("/api/admin/brokers", json={
        "name": "ACME Cargo", "admin_username": "acme-admin",
        "admin_password": "secret1"}).get_json()
    check("broker provisioned for the check", bool(j.get("ok")))

    cb = client("acme-admin", "secret1")
    shell = cb.get("/app").get_data(as_text=True)
    check("broker's served shell contains the gate", bool(GATE.search(shell)))
    check("broker resolves to business != 1",
          cb.get("/api/me").get_json()["business"]["id"] != 1)
    check("Otlobly admin resolves to business 1",
          co.get("/api/me").get_json()["business"]["id"] == 1)

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
