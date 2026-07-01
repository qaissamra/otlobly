#!/usr/bin/env python3
"""
Meta Leads for Otlobly — one place for everyone who reaches the business through Meta:
Facebook Messenger + Instagram DMs (the page inbox) AND Lead-Ads instant-form submissions.

Pulls from the Graph API (v21.0), upserts each into the `meta_leads` table (preserving the
team's status / assignee / note / order-link on re-sync), and returns the lead list + close-rate
stats. Stdlib urllib only — same token/version as meta.py (ad spend) and notify.py (WhatsApp).

Config (env):
  META_PAGE_ID     — the Facebook Page id (e.g. 868855596307697)
  META_PAGE_TOKEN  — a Page access token with pages_messaging + instagram_manage_messages (DMs)
                     and leads_retrieval (lead forms). Falls back to META_ACCESS_TOKEN.

  python3 meta_leads.py            # sync + print a summary (needs the env vars)
"""

import json
import os
from datetime import datetime, timezone
from urllib import request, parse, error

import db

GRAPH = "https://graph.facebook.com/v21.0"


def _cfg():
    token = (os.environ.get("META_PAGE_TOKEN") or os.environ.get("META_ACCESS_TOKEN") or "").strip()
    return token, (os.environ.get("META_PAGE_ID") or "").strip()


def configured():
    t, p = _cfg()
    return bool(t and p)


def _get(url):
    with request.urlopen(url, timeout=25) as r:
        return json.load(r)


def _parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S+0000").replace(tzinfo=timezone.utc)
    except Exception:  # noqa
        return None


# ---- Messenger + Instagram DMs --------------------------------------------- #
def _fetch_conversations(token, page_id, platform, limit=100):
    q = parse.urlencode({
        "fields": "id,updated_time,unread_count,participants,messages{from,created_time,message}",
        "platform": platform, "limit": limit, "access_token": token})
    return _get(f"{GRAPH}/{page_id}/conversations?{q}").get("data", [])


def _dm_lead(c, page_id, platform):
    """One conversation → a lead dict (name, last message, first-response time, needs_reply)."""
    msgs = (c.get("messages", {}) or {}).get("data", [])
    if not msgs:
        return None
    last = msgs[0]
    needs_reply = str(last.get("from", {}).get("id")) != page_id
    names = [p.get("name") for p in (c.get("participants", {}) or {}).get("data", [])
             if str(p.get("id")) != page_id]
    rt = None
    for i, m in enumerate(msgs):                       # page reply → next customer msg = response time
        if str(m.get("from", {}).get("id")) == page_id:
            for j in range(i + 1, len(msgs)):
                if str(msgs[j].get("from", {}).get("id")) != page_id:
                    tr, tc = _parse_dt(m.get("created_time")), _parse_dt(msgs[j].get("created_time"))
                    if tr and tc:
                        d = (tr - tc).total_seconds() / 60
                        if 0 < d < 1440:
                            rt = round(d, 1)
                    break
            break
    last_cust = next((( m.get("message") or "")[:100] for m in msgs
                      if str(m.get("from", {}).get("id")) != page_id), "")
    return {
        "lead_id": c["id"], "source": platform,
        "name": names[0] if names else "Unknown", "phone": None, "email": None,
        "form_id": None, "form_name": None, "ad_name": None,
        "last_message": last_cust, "last_activity": last.get("created_time"),
        "created_time": last.get("created_time"), "response_min": rt, "needs_reply": needs_reply,
    }


# ---- Lead Ads (instant forms) ---------------------------------------------- #
def _fetch_forms(token, page_id):
    q = parse.urlencode({"fields": "id,name", "limit": 100, "access_token": token})
    return _get(f"{GRAPH}/{page_id}/leadgen_forms?{q}").get("data", [])


def _fetch_form_leads(token, form_id, limit=200):
    q = parse.urlencode({"fields": "id,created_time,ad_name,field_data",
                         "limit": limit, "access_token": token})
    return _get(f"{GRAPH}/{form_id}/leads?{q}").get("data", [])


def _field(field_data, *keys):
    for f in field_data or []:
        k = (f.get("name") or f.get("key") or "").lower()
        if any(x in k for x in keys):
            v = f.get("values") or f.get("value")
            return (v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v))
    return None


def _form_lead(lead, form):
    fd = lead.get("field_data", [])
    return {
        "lead_id": lead["id"], "source": "leadform",
        "name": _field(fd, "full_name", "name") or "Lead",
        "phone": _field(fd, "phone"), "email": _field(fd, "email"),
        "form_id": form["id"], "form_name": form.get("name"), "ad_name": lead.get("ad_name"),
        "last_message": None, "last_activity": lead.get("created_time"),
        "created_time": lead.get("created_time"), "response_min": None, "needs_reply": True,
    }


def sync():
    """Pull DMs + form leads from Meta, upsert into meta_leads, return {leads, stats, error}."""
    token, page_id = _cfg()
    if not (token and page_id):
        return {"leads": db.list_leads(), "stats": db.lead_stats(), "configured": False,
                "error": "META_PAGE_ID / META_PAGE_TOKEN not set — showing stored leads only."}
    errors, fetched = [], []
    for platform in ("messenger", "instagram"):
        try:
            for c in _fetch_conversations(token, page_id, platform):
                lead = _dm_lead(c, page_id, platform)
                if lead:
                    fetched.append(lead)
        except error.HTTPError as e:
            if e.code not in (400, 403):
                errors.append(f"{platform}: HTTP {e.code}")
        except Exception as e:  # noqa
            errors.append(f"{platform}: {e}")
    try:
        for form in _fetch_forms(token, page_id):
            for lead in _fetch_form_leads(token, form["id"]):
                fetched.append(_form_lead(lead, form))
    except error.HTTPError as e:
        errors.append("lead forms need the 'leads_retrieval' permission on the token"
                      if e.code in (400, 403) else f"forms: HTTP {e.code}")
    except Exception as e:  # noqa
        errors.append(f"forms: {e}")
    for lead in fetched:
        try:
            db.upsert_lead(lead)
        except Exception as e:  # noqa
            errors.append(f"save {lead.get('lead_id')}: {e}")
    return {"leads": db.list_leads(), "stats": db.lead_stats(), "configured": True,
            "error": "; ".join(errors) if errors else None}


if __name__ == "__main__":
    db.init_db()
    out = sync()
    print(json.dumps({"configured": out["configured"], "error": out["error"],
                      "n": len(out["leads"]), "stats": out["stats"]}, ensure_ascii=False, indent=2))
