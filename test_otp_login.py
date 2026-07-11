#!/usr/bin/env python3
"""
Integration self-check for the customer-portal OTP login (audit D-2), driven
through the real Flask endpoints with a test client.

Proves the security-critical properties of the login:
  * only a KNOWN customer's number can request a code (enumeration guard)
  * a wrong code is rejected
  * the right code logs the session in
  * the attempt counter caps brute force

    OTLOBLY_OTP_DEV=1 is set here so the code is returned in the response instead
    of sent over WhatsApp (the same dev path the app documents). Temp DB only.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-otp-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_OTP_DEV"] = "1"          # echo the code in the response (no WhatsApp creds)
os.environ.pop("OTLOBLY_SECURE", None)       # boot with the dev secret key

import app as appmod   # noqa: E402
import db              # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    # A known customer with an order (only known numbers may log in).
    db.insert_new_order({
        "order_id": "P", "status": "REQUESTED",
        "customer": {"name": "Test Customer", "phones": [{"e164": "+970599000001"}]},
        "items": [], "signature": "sig-otp", "created_at": db.now_iso()})

    c = appmod.app.test_client()

    # 1) Unknown number cannot even request a code.
    r = c.post("/api/customer/otp/request", json={"phone": "0599123123"})
    check("unknown number → 404, no code issued", r.status_code == 404 and not r.get_json().get("ok"))

    # 2) Known number gets a code (dev path returns it).
    r = c.post("/api/customer/otp/request", json={"phone": "0599000001"})
    j = r.get_json()
    code = j.get("dev_code")
    check("known number → ok + dev code issued", bool(j.get("ok")) and bool(code))

    # 3) Wrong code is rejected.
    wrong = "000000" if code != "000000" else "111111"
    r = c.post("/api/customer/otp/verify", json={"phone": "0599000001", "code": wrong})
    check("wrong code → rejected", r.status_code == 400 and not r.get_json().get("ok"))

    # 4) Right code logs the session in.
    r = c.post("/api/customer/otp/verify", json={"phone": "0599000001", "code": code})
    check("right code → ok", bool(r.get_json().get("ok")))
    r = c.get("/api/customer/me")
    me = r.get_json()
    check("session is now logged in", bool(me.get("logged_in")) and me.get("name") == "Test Customer")

    # 5) Brute force is capped: request a fresh code, then exhaust the attempt cap.
    code2 = c.post("/api/customer/otp/request", json={"phone": "0599000001"}).get_json().get("dev_code")
    wrong2 = "000000" if code2 != "000000" else "111111"
    saw_lock = False
    for _ in range(app_max_attempts() + 2):
        rr = c.post("/api/customer/otp/verify", json={"phone": "0599000001", "code": wrong2})
        if rr.status_code == 429:                # "too many attempts"
            saw_lock = True
            break
    check("attempt cap locks brute force (429)", saw_lock)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


def app_max_attempts():
    return getattr(appmod, "OTP_MAX_ATTEMPTS", 5)


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
