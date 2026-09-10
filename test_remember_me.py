#!/usr/bin/env python3
"""
Self-checks: the staff login's "Keep me signed in for 30 days" box.

Staff sessions used to die with the browser (only CUSTOMER logins set
session.permanent). The box now opts into Flask-Login's OWN remember cookie —
30 days, independent of the portal's 180-day PERMANENT_SESSION_LIFETIME. What
must stay true:

  * ticked   → a remember_token cookie, Max-Age = 30 days
  * unticked → no remember cookie at all (session-scoped, as before)
  * the remembered login really is a login (/api/me answers)
  * logout clears the remember cookie — no zombie 30-day sign-in
  * a WRONG password never mints one, box ticked or not

    ./.venv/bin/python test_remember_me.py
"""

import os
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# Point db.py at a temp file BEFORE importing it (DB_FILE is read at import).
_TMP = Path(tempfile.mkdtemp(prefix="otlobly-rmb-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402

THIRTY_DAYS = 30 * 24 * 3600
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def remember_cookie(resp):
    """The response's remember_token Set-Cookie header, or "" if it sets none.
    Read raw rather than via the client's cookie jar so the assertions don't
    depend on a particular Werkzeug version's test-cookie API."""
    for h in resp.headers.getlist("Set-Cookie"):
        if h.startswith("remember_token="):
            return h
    return ""


def cookie_lifetime(cookie):
    """Seconds from now until the cookie's Expires — Flask-Login stamps an absolute
    date rather than Max-Age, so read the date and subtract. -1 if it has neither."""
    for part in cookie.split("; "):
        if part.startswith("Expires="):
            return (parsedate_to_datetime(part[len("Expires="):])
                    - datetime.now(timezone.utc)).total_seconds()
        if part.startswith("Max-Age="):
            return float(part[len("Max-Age="):])
    return -1


def sign_in(user="qais", pw="secret1", remember=None):
    c = appmod.app.test_client()
    data = {"username": user, "password": pw}
    if remember:
        data["remember"] = "1"          # what the ticked checkbox posts
    return c, c.post("/login", data=data)


def main():
    db.init_db()
    db.create_user("qais", auth.hash_pw("secret1"), "admin", "Qais", business_id=1)

    print("TICKED — 30-day remember cookie:")
    c, r = sign_in(remember=True)
    cookie = remember_cookie(r)
    check("login sets a remember_token cookie", cookie != "")
    check("cookie carries a value", not cookie.startswith("remember_token=;"))
    check("expires ~30 days out (not a browser-session cookie)",
          abs(cookie_lifetime(cookie) - THIRTY_DAYS) < 3600)
    check("cookie is HttpOnly", "HttpOnly" in cookie)
    check("cookie is SameSite=Lax", "SameSite=Lax" in cookie)
    check("no Secure flag off HTTPS (OTLOBLY_SECURE unset)", "Secure" not in cookie)
    check("it is a real login", c.get("/api/me").get_json().get("username") == "qais")

    print("UNTICKED — session-scoped, exactly as before:")
    c2, r2 = sign_in(remember=False)
    check("no remember_token cookie", remember_cookie(r2) == "")
    check("still signed in for this browser session",
          c2.get("/api/me").get_json().get("username") == "qais")

    print("LOGOUT — the 30-day cookie must not outlive it:")
    out = remember_cookie(c.get("/logout"))
    check("logout clears remember_token",
          out.startswith("remember_token=;") or "Max-Age=0" in out or "Expires=Thu, 01 Jan 1970" in out)
    check("and the session is gone", c.get("/api/me").status_code in (302, 401))

    print("WRONG PASSWORD — never mints a cookie:")
    c3, r3 = sign_in(pw="nope", remember=True)
    check("no remember_token on a failed login", remember_cookie(r3) == "")
    check("error page is shown", b"Wrong username or password" in r3.data)
    check("box stays ticked so the choice survives a typo",
          b'name="remember"' in r3.data and b"checked" in r3.data)
    _, r4 = sign_in(pw="nope", remember=False)
    check("and stays UNticked if that is what they chose",
          b'name="remember"' in r4.data and b"checked" not in r4.data)

    print("SETUP PAGE — the first-run admin form has no such box:")
    check("no remember checkbox on /login when it is the setup form",
          b'name="remember"' not in appmod.app.test_client().get("/setup").data)

    print()
    if fails:
        print(f"FAILED ({len(fails)}): " + ", ".join(fails))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
