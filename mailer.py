#!/usr/bin/env python3
"""
Email sender for the customer-portal email login (second method next to WhatsApp).

Uses the Resend HTTP API — an HTTPS POST, not SMTP, because the host (Render)
restricts outbound SMTP ports and a blocking SMTP retry would tie up a gunicorn
worker. Config comes from env so no secret is committed:
  RESEND_API_KEY — API key from resend.com
  EMAIL_FROM     — sender, e.g. 'Otlobly <login@otlobly.com>'
                   (use 'Otlobly <onboarding@resend.dev>' until the domain is
                   verified in Resend)

If the creds are absent, `send_email` returns {ok:False, dev:True} so the caller
can fall back to a dev/manual code (gated by OTLOBLY_EMAIL_DEV) and the whole
login flow stays testable before the live email API is connected.

Stdlib only (urllib), mirroring notify.py / tracking.py — zero new dependencies.
"""

import json
import os
from urllib import request, error

RESEND_API = "https://api.resend.com/emails"


def _cfg():
    return (os.environ.get("RESEND_API_KEY"),
            os.environ.get("EMAIL_FROM", "Otlobly <onboarding@resend.dev>"))


def configured():
    """True when the live email API is set up (API key present)."""
    key, _ = _cfg()
    return bool(key)


def send_email(to, subject, html):
    """Send one email. Returns {ok:True, id} on success, {ok:False, dev:True}
    when email isn't configured (caller uses the dev fallback), or
    {ok:False, error} on a real send failure."""
    key, sender = _cfg()
    if not key:
        return {"ok": False, "dev": True, "error": "Email not configured"}
    payload = {"from": sender, "to": [str(to)], "subject": subject, "html": html}
    req = request.Request(RESEND_API,
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode() or "{}")
        return {"ok": True, "id": out.get("id")}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"ok": False, "error": f"Resend {e.code}: {body}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}


def send_login_code(to, code):
    """The one email this app sends: a 6-digit portal code (AR+EN, both logins
    and link-verification reuse it)."""
    return send_email(
        to,
        f"رمز الدخول إلى اطلبلي · Otlobly login code: {code}",
        f"""<div dir="rtl" style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;
             max-width:420px;margin:0 auto;padding:24px;color:#15140f">
          <h2 style="margin:0 0 4px">Otlob<span style="color:#ff5a1f">ly</span> · اطلبلي</h2>
          <p style="color:#7a766c;font-size:14px">رمز الدخول الخاص بك · your login code:</p>
          <div style="font-size:34px;font-weight:800;letter-spacing:8px;text-align:center;
               background:#f7f5ef;border:1px solid #e8e4d8;border-radius:12px;padding:16px">{code}</div>
          <p style="color:#7a766c;font-size:12px">صالح لمدة 10 دقائق. إذا لم تطلب هذا الرمز تجاهل الرسالة.<br>
             Valid for 10 minutes. If you didn't request it, ignore this email.</p>
        </div>""")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python3 mailer.py <to> <code>   (needs RESEND_API_KEY env)")
        print("configured:", configured())
        sys.exit(0)
    print(json.dumps(send_login_code(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
