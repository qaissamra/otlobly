#!/usr/bin/env python3
"""
WhatsApp OTP sender for the customer login portal (Phase 14).

Uses the WhatsApp Cloud API (Meta Graph API) **authentication-template** message. Config
comes from env so no secret is committed:
  WHATSAPP_TOKEN            — permanent access token for the WhatsApp Business number
  WHATSAPP_PHONE_NUMBER_ID  — the sender phone-number id (from the Meta app)
  WHATSAPP_OTP_TEMPLATE     — approved authentication template name (default 'otlobly_login_code')
  WHATSAPP_LANG             — template language code (default 'ar')

If the creds are absent, `send_whatsapp_otp` returns {ok:False, dev:True} so the caller can
fall back to a dev/manual code (gated by OTLOBLY_OTP_DEV) and the whole login flow stays
testable before the live WhatsApp API is connected.

Stdlib only (urllib), mirroring tracking.py / meta.py — zero new dependencies.
"""

import json
import os
from urllib import request, error

GRAPH = "https://graph.facebook.com/v21.0"


def _cfg():
    return (os.environ.get("WHATSAPP_TOKEN"),
            os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
            os.environ.get("WHATSAPP_OTP_TEMPLATE", "otlobly_login_code"),
            os.environ.get("WHATSAPP_LANG", "ar"))


def configured():
    """True when the live WhatsApp Cloud API is set up (token + phone id present)."""
    tok, pid, _, _ = _cfg()
    return bool(tok and pid)


def _send_template(e164, template, lang, components):
    """POST one template message to the Cloud API. Returns {ok:True, id} on success,
    {ok:False, dev:True} when WhatsApp isn't configured (caller uses the dev fallback),
    or {ok:False, error} on a real send failure."""
    tok, pid, _, deflang = _cfg()
    if not (tok and pid):
        return {"ok": False, "dev": True, "error": "WhatsApp not configured"}
    to = "".join(c for c in str(e164) if c.isdigit())
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang or deflang},
            "components": components,
        },
    }
    req = request.Request(f"{GRAPH}/{pid}/messages",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": f"Bearer {tok}",
                                   "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode() or "{}")
        return {"ok": True, "id": (out.get("messages") or [{}])[0].get("id")}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"ok": False, "error": f"WhatsApp {e.code}: {body}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}


def send_whatsapp_otp(e164, code, lang=None):
    """Send a one-time login code over WhatsApp (authentication template)."""
    _, _, template, _ = _cfg()
    # Meta's "authentication" template = one body variable (the code) + a copy-code URL button.
    return _send_template(e164, template, lang, [
        {"type": "body", "parameters": [{"type": "text", "text": str(code)}]},
        {"type": "button", "sub_type": "url", "index": "0",
         "parameters": [{"type": "text", "text": str(code)}]},
    ])


def send_account_verify(e164, name, token, lang=None):
    """Send the UTILITY "account verification" template whose "Verify account" button
    is a dynamic URL carrying a one-time login token (…/account?vt={{1}}) — a WhatsApp
    magic link. The library template has two body variables ({{1}} greeting name,
    {{2}} what to verify), so both are always sent; template comes from env
    WHATSAPP_VERIFY_TEMPLATE so a renamed/customised template is a config change."""
    template = os.environ.get("WHATSAPP_VERIFY_TEMPLATE", "account_creation_confirmation_3")
    return _send_template(e164, template, lang, [
        {"type": "body", "parameters": [{"type": "text", "text": (name or "").strip() or "عميلنا"},
                                        {"type": "text", "text": "حسابك · your account"}]},
        {"type": "button", "sub_type": "url", "index": "0",
         "parameters": [{"type": "text", "text": str(token)}]},
    ])


def send_track_package(e164, name, order_ref, delivery_date, otl=None, lang=None):
    """Send the UTILITY "track_package" template — "your package is on the way":
      body {{1}} name · {{2}} order/tracking ref · {{3}} estimated delivery date
      button "تتبع الطلب" → /track.
    Template name from env WHATSAPP_TRACK_TEMPLATE so a rename is a config change.
    The APPROVED template's button is a STATIC URL, so no button parameter may be
    sent (Meta rejects the param-count mismatch). If the template is later edited to
    a dynamic URL (…/track?t={{1}}), set WHATSAPP_TRACK_BUTTON=dynamic and the OTL
    deep-link param rides along — env flip, no code change."""
    template = os.environ.get("WHATSAPP_TRACK_TEMPLATE", "track_package")
    components = [
        {"type": "body", "parameters": [
            {"type": "text", "text": (name or "").strip() or "عميلنا"},
            {"type": "text", "text": str(order_ref or "")},
            {"type": "text", "text": str(delivery_date or "")},
        ]},
    ]
    if otl and os.environ.get("WHATSAPP_TRACK_BUTTON", "").lower() == "dynamic":
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [{"type": "text", "text": str(otl)}]})
    return _send_template(e164, template, lang, components)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python3 notify.py <e164> <code>   (needs WHATSAPP_* env)")
        print("configured:", configured())
        sys.exit(0)
    print(json.dumps(send_whatsapp_otp(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
