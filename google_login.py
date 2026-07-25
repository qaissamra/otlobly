#!/usr/bin/env python3
"""
"Sign in with Google" for the customer portal (free per-login — the no-SMS-cost,
no-Meta-verification door for returning customers with a linked email).

Server-side OAuth 2.0 authorization-code flow, stdlib only (urllib), mirroring
mailer.py / notify.py — zero new dependencies. Config comes from env:
  GOOGLE_CLIENT_ID     — OAuth web client id (Google Cloud Console → Credentials)
  GOOGLE_CLIENT_SECRET — its client secret
The button on /account is gated on configured(), so the feature is inert until
both are set. The redirect URI registered on the Google client must be
  {PORTAL_BASE_URL}/api/customer/google/callback

Trust model: we fetch the id_token DIRECTLY from Google's token endpoint over
TLS in the code exchange, so its payload is trusted without local JWT signature
verification (the standard confidential-client shortcut — the token never
transits the browser).
"""

import base64
import json
import os
import secrets
from urllib import request, error, parse

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _cfg():
    return (os.environ.get("GOOGLE_CLIENT_ID"),
            os.environ.get("GOOGLE_CLIENT_SECRET"))


def configured():
    """True when the Google OAuth client is set up (id + secret present)."""
    cid, csec = _cfg()
    return bool(cid and csec)


def new_state():
    """CSRF state nonce for the auth round-trip (stored in the session)."""
    return secrets.token_urlsafe(24)


def auth_url(redirect_uri, state):
    """The accounts.google.com URL to send the customer to."""
    cid, _ = _cfg()
    return AUTH_URL + "?" + parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",   # always show the account chooser
    })


def _jwt_payload(id_token):
    """Decode a JWT's payload segment (no signature check — see trust model above)."""
    seg = id_token.split(".")[1]
    seg += "=" * (-len(seg) % 4)                      # restore base64 padding
    return json.loads(base64.urlsafe_b64decode(seg))


def exchange_code(code, redirect_uri):
    """Swap the callback ?code for tokens; return {ok, email, email_verified, name}
    or {ok:False, error}. Talks only to Google's token endpoint, server-to-server."""
    cid, csec = _cfg()
    if not (cid and csec):
        return {"ok": False, "error": "Google login not configured"}
    form = parse.urlencode({
        "code": code, "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }).encode()
    req = request.Request(TOKEN_URL, data=form,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read().decode() or "{}")
        claims = _jwt_payload(tok["id_token"])
        return {"ok": True,
                "email": (claims.get("email") or "").strip().lower(),
                "email_verified": bool(claims.get("email_verified")),
                "name": claims.get("name") or ""}
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"ok": False, "error": f"Google {e.code}: {body}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}
