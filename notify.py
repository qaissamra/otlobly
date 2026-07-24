#!/usr/bin/env python3
"""
WhatsApp OTP sender for the customer login portal (Phase 14).

Uses the WhatsApp Cloud API (Meta Graph API) template messages. Config
comes from env so no secret is committed:
  WHATSAPP_TOKEN            — permanent access token for the WhatsApp Business number
  WHATSAPP_PHONE_NUMBER_ID  — the sender phone-number id (from the Meta app)
  WHATSAPP_OTP_TEMPLATE     — approved login-code template name (default 'otlobly_login_code')
  WHATSAPP_OTP_KIND         — 'auth' (default: AUTHENTICATION template, body code + copy-code
                              button) or 'utility' (body-only UTILITY template — the ship-now
                              carrier while the Meta business is unverified, since Meta only
                              grants AUTHENTICATION templates to verified businesses)
  WHATSAPP_LANG             — template language code (default 'ar')

If the creds are absent, `send_whatsapp_otp` returns {ok:False, dev:True} so the caller can
fall back to a dev/manual code (gated by OTLOBLY_OTP_DEV) and the whole login flow stays
testable before the live WhatsApp API is connected.

Stdlib only (urllib), mirroring tracking.py / meta.py — zero new dependencies.
"""

import base64
import json
import os
from urllib import request, error, parse

GRAPH = "https://graph.facebook.com/v21.0"
TWILIO = "https://api.twilio.com/2010-04-01"


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
    """Send a one-time login code over WhatsApp. The payload shape MUST match the
    approved template's kind (WHATSAPP_OTP_KIND):
      auth (default) — AUTHENTICATION template: one body variable (the code) + a
                       copy-code URL button (param at button index 0).
      utility        — body-only UTILITY template ({{1}} = the code, NO button) —
                       Meta rejects a button component the template doesn't have."""
    _, _, template, _ = _cfg()
    components = [{"type": "body", "parameters": [{"type": "text", "text": str(code)}]}]
    if os.environ.get("WHATSAPP_OTP_KIND", "auth").strip().lower() != "utility":
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [{"type": "text", "text": str(code)}]})
    return _send_template(e164, template, lang, components)


# --------------------------------------------------------------------------- #
# SMS login codes (Twilio) — the "no Meta verification" channel. Alphanumeric
# sender ids (e.g. "Otlobly") need NO pre-registration for Palestine, so this
# sidesteps the WhatsApp AUTHENTICATION-template / business-verification wall.
# --------------------------------------------------------------------------- #
def sms_configured():
    """True when Twilio SMS is set up: account sid + auth token + a sender
    (TWILIO_SMS_FROM number/alphanumeric id, or a Messaging Service SID)."""
    return bool(os.environ.get("TWILIO_ACCOUNT_SID")
                and os.environ.get("TWILIO_AUTH_TOKEN")
                and (os.environ.get("TWILIO_SMS_FROM")
                     or os.environ.get("TWILIO_MESSAGING_SERVICE_SID")))


def send_sms_otp(e164, code):
    """Send a one-time login code by SMS via the Twilio REST API (stdlib only).
    Returns the same {ok|dev|error} shape as the WhatsApp sender, so the caller's
    OTLOBLY_OTP_DEV fallback works unchanged. Sender is an alphanumeric id
    (TWILIO_SMS_FROM=Otlobly — no pre-registration for +970), a Twilio number,
    or a Messaging Service SID (TWILIO_MESSAGING_SERVICE_SID)."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    frm = os.environ.get("TWILIO_SMS_FROM")
    msid = os.environ.get("TWILIO_MESSAGING_SERVICE_SID")
    if not (sid and tok and (frm or msid)):
        return {"ok": False, "dev": True, "error": "SMS not configured"}
    to = str(e164).strip()
    if not to.startswith("+"):
        to = "+" + "".join(c for c in to if c.isdigit())
    text = os.environ.get(
        "SMS_OTP_TEXT",
        "رمز الدخول إلى اطلبلي: {code}\nصالح لمدة 10 دقائق. لا تشاركه مع أحد.")
    form = {"To": to, "Body": text.replace("{code}", str(code))}
    if msid:
        form["MessagingServiceSid"] = msid
    else:
        form["From"] = frm
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    req = request.Request(f"{TWILIO}/Accounts/{sid}/Messages.json",
                          data=parse.urlencode(form).encode(),
                          headers={"Authorization": f"Basic {auth}",
                                   "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode() or "{}")
        return {"ok": True, "id": out.get("sid"), "status": out.get("status")}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"ok": False, "error": f"Twilio {e.code}: {body}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}


def otp_channel():
    """Which channel delivers the typed login code: 'sms' or 'whatsapp' (default)."""
    return os.environ.get("OTP_CHANNEL", "whatsapp").strip().lower()


def send_login_code(e164, code, lang=None):
    """Deliver a one-time login code over the configured channel (OTP_CHANNEL)."""
    if otp_channel() == "sms":
        return send_sms_otp(e164, code)
    return send_whatsapp_otp(e164, code, lang)


def otp_available():
    """True when the typed-code login can actually deliver on the selected channel —
    gates the /account UI. Each channel has its OWN explicit enable flag so nothing
    appears before the owner activates it (WhatsApp: template approval; SMS: Twilio
    creds + a Jawwal deliverability test)."""
    if otp_channel() == "sms":
        return bool(os.environ.get("SMS_OTP_ENABLED", "").strip()) and sms_configured()
    return bool(os.environ.get("WHATSAPP_OTP_ENABLED", "").strip()) and configured()


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


def send_track_package(e164, name, order_ref, delivery_date, otl=None,
                       total=None, due=None, lang=None):
    """Send the UTILITY "track_package" template — "your package is on the way":
      body {{1}} name · {{2}} order/tracking ref · {{3}} estimated delivery date
      (+ {{4}} order total · {{5}} due on delivery when WHATSAPP_TRACK_AMOUNTS is on)
      button "تتبع الطلب" → /track.
    Template name from env WHATSAPP_TRACK_TEMPLATE so a rename is a config change.

    The body-variable COUNT must match the currently-approved template exactly, so the
    two amount vars are gated behind env WHATSAPP_TRACK_AMOUNTS and the URL-button param
    behind WHATSAPP_TRACK_BUTTON=dynamic. With both unset this sends the original 3-var
    static template; flip both envs together the moment the 5-var dynamic template is
    approved — atomic switch, no code change."""
    template = os.environ.get("WHATSAPP_TRACK_TEMPLATE", "track_package")
    body = [
        {"type": "text", "text": (name or "").strip() or "عميلنا"},
        {"type": "text", "text": str(order_ref or "")},
        {"type": "text", "text": str(delivery_date or "")},
    ]
    if os.environ.get("WHATSAPP_TRACK_AMOUNTS", "").strip():   # 5-var template
        body.append({"type": "text", "text": str(total if total is not None else "—")})
        body.append({"type": "text", "text": str(due if due is not None else "—")})
    components = [{"type": "body", "parameters": body}]
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
