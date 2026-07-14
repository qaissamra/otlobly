#!/usr/bin/env python3
"""
Telegram sender for OWNER alerts (deadline countdowns, overdue arrivals).
Stdlib only (urllib), mirroring notify.py / tracking.py — zero new dependencies.

Credentials: env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID win, with a config
fallback (alerts.telegram_token / alerts.telegram_chat) so the owner can paste
them straight into the Settings page without touching Render. The token is a
secret — env vars or the data-disk config only, NEVER the repo.
"""

import json
import os
from urllib import request, error, parse

import cfg

API = "https://api.telegram.org"


def _creds():
    try:
        c = cfg.load()
    except Exception:  # noqa: BLE001 - config unreadable ≠ crash the caller
        c = {}
    tok = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get(c, "alerts.telegram_token", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get(c, "alerts.telegram_chat", "")
    return str(tok or "").strip(), str(chat or "").strip()


def configured():
    tok, chat = _creds()
    return bool(tok and chat)


def send(text):
    """One plain-text message to the owner's chat. {ok: bool, error?: str}."""
    tok, chat = _creds()
    if not (tok and chat):
        return {"ok": False, "error": "Telegram not configured"}
    data = parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with request.urlopen(request.Request(f"{API}/bot{tok}/sendMessage", data=data),
                             timeout=20) as r:
            out = json.loads(r.read().decode() or "{}")
        return {"ok": True} if out.get("ok") else {"ok": False, "error": str(out)[:200]}
    except error.HTTPError as e:
        return {"ok": False, "error": f"Telegram {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def detect_chat_id(token=None):
    """The newest chat that messaged the bot (the owner pressing START) → id or
    None. Used by the Settings 'Detect chat id' button."""
    tok = str(token or "").strip() or _creds()[0]
    if not tok:
        return None
    try:
        with request.urlopen(f"{API}/bot{tok}/getUpdates", timeout=15) as r:
            out = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001
        return None
    for u in reversed(out.get("result") or []):
        chat = ((u.get("message") or u.get("edited_message") or {}).get("chat") or {})
        if chat.get("id"):
            return chat["id"]
    return None
