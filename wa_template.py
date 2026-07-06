#!/usr/bin/env python3
"""
WhatsApp OTP template manager — list/create the authentication template that
notify.py sends, straight through the Graph API. Doing it by API (against an
explicit WABA id) sidesteps the Meta dashboard's account-selector, which is what
produced the "cannot create template" error when the OLD test account was picked
instead of the real Otlobly WhatsApp Business Account.

Env (nothing is committed — pass via .env / shell):
  WHATSAPP_MGMT_TOKEN  — token with the whatsapp_business_management permission
                         (falls back to WHATSAPP_TOKEN)
  WHATSAPP_WABA_ID     — the WhatsApp Business Account id (or pass as the CLI arg)
  WHATSAPP_OTP_TEMPLATE / WHATSAPP_LANG — defaults for the template name + language

Usage:
  python3 wa_template.py list  [WABA_ID]     # every template on the WABA + its status
  python3 wa_template.py create [WABA_ID]    # create otlobly_login_code (ar, copy-code)
  python3 wa_template.py create --name x --lang en

Stdlib only (urllib), mirroring notify.py — zero new dependencies.
"""

import argparse
import json
import os
import sys
from urllib import request, error

GRAPH = "https://graph.facebook.com/v21.0"


def _token():
    return os.environ.get("WHATSAPP_MGMT_TOKEN") or os.environ.get("WHATSAPP_TOKEN")


def _call(method, path, token, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = request.Request(f"{GRAPH}/{path}", data=body, method=method,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}"), None
    except error.HTTPError as e:
        return None, f"{e.code}: {e.read().decode('utf-8', 'replace')[:500]}"
    except Exception as e:  # noqa
        return None, str(e)


def list_templates(waba, token):
    return _call("GET", f"{waba}/message_templates"
                        "?fields=name,status,category,language&limit=200", token)


def create_auth_template(waba, token, name, lang, expiry=10):
    """AUTHENTICATION templates: Meta supplies BOTH the localized body text and the
    copy-code button label (you must NOT set the button text — that triggers a
    "Button Format is Incorrect" error). We only opt into the security-recommendation
    line, a code-expiry footer, and the copy-code OTP button. The 6-digit code is the
    implicit {{1}} that notify.py fills in.
    NOTE: authentication templates require a VERIFIED Meta business, else Meta returns
    "does not have permission to create message template" (subcode 2388185)."""
    payload = {
        "name": name,
        "language": lang,
        "category": "AUTHENTICATION",
        "components": [
            {"type": "BODY", "add_security_recommendation": True},
            {"type": "FOOTER", "code_expiration_minutes": expiry},
            {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE"}]},
        ],
    }
    return _call("POST", f"{waba}/message_templates", token, payload)


# The code goes in the body as {{1}}. UTILITY templates work for UNVERIFIED
# businesses (unlike AUTHENTICATION), so this is the "ship now" OTP carrier —
# notify.py sends it body-only (no copy-code button). Phrased as order-portal
# access, which is what the login actually unlocks.
UTIL_BODY = ("رمز الدخول لعرض طلباتك في اطلبلي: {{1}}\n"
             "صالح لمدة 10 دقائق. لا تشاركه مع أحد.")


def create_utility_template(waba, token, name, lang, body, example="123456"):
    payload = {
        "name": name,
        "language": lang,
        "category": "UTILITY",
        "components": [
            {"type": "BODY", "text": body, "example": {"body_text": [[example]]}},
        ],
    }
    return _call("POST", f"{waba}/message_templates", token, payload)


def main():
    ap = argparse.ArgumentParser(description="WhatsApp OTP template manager")
    ap.add_argument("action", choices=["list", "create"])
    ap.add_argument("waba", nargs="?", help="WABA id (or set WHATSAPP_WABA_ID)")
    ap.add_argument("--name", default=os.environ.get("WHATSAPP_OTP_TEMPLATE", "otlobly_login_code"))
    ap.add_argument("--lang", default=os.environ.get("WHATSAPP_LANG", "ar"))
    ap.add_argument("--kind", choices=["auth", "utility"], default="auth",
                    help="auth = AUTHENTICATION (needs verified business); utility = UTILITY (works unverified)")
    ap.add_argument("--body", default=UTIL_BODY, help="utility body text — must contain {{1}} for the code")
    ap.add_argument("--expiry", type=int, default=10, help="code expiry minutes (auth footer)")
    a = ap.parse_args()

    token = _token()
    waba = a.waba or os.environ.get("WHATSAPP_WABA_ID")
    if not token:
        sys.exit("✗ Set WHATSAPP_MGMT_TOKEN (or WHATSAPP_TOKEN) — a token with the "
                 "whatsapp_business_management permission.")
    if not waba:
        sys.exit("✗ Pass the WABA id as the argument, or set WHATSAPP_WABA_ID.")

    if a.action == "list":
        out, err = list_templates(waba, token)
        if err:
            sys.exit("✗ list failed: " + err)
        rows = out.get("data", [])
        if not rows:
            print("(no templates on this WABA yet)")
        for t in rows:
            print(f"  {(t.get('status') or '?'):10} {(t.get('category') or ''):16} "
                  f"{(t.get('language') or ''):6} {t.get('name')}")
        return

    if a.kind == "utility":
        if "{{1}}" not in a.body:
            sys.exit("✗ utility --body must contain {{1}} (the code placeholder).")
        out, err = create_utility_template(waba, token, a.name, a.lang, a.body)
    else:
        out, err = create_auth_template(waba, token, a.name, a.lang, a.expiry)
    if err:
        sys.exit("✗ create failed: " + err)
    print("✓ created:", json.dumps(out, ensure_ascii=False))
    print(f"\nTemplate '{a.name}' ({a.lang}, {a.kind}) submitted for review. It must reach status "
          f"APPROVED before notify.py can send it (usually minutes).")
    print(f"Check status with:  python3 wa_template.py list {waba}")


if __name__ == "__main__":
    main()
