#!/usr/bin/env python3
"""
The handful of business-logic settings editable from the Admin ⚙ page (instead of
hand-editing config.json): the markup, the estimator destination + shipping/import
rules, customer-mode, and the business WhatsApp number. Whitelisted on purpose —
no arbitrary config keys are writable from the UI.

`read()` returns the current values; `apply(body)` validates + persists to config.json.
Shared by dashboard.py (local) and app.py (hosted).
"""

import os
import re

import alerts as _alerts
import cfg
import tracking


def _f(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


# Public-site section visibility (True = shown). The whitelist doubles as the
# code default when config.json has no public.sections.* key yet — so prod needs
# no migration. Membership ships hidden (owner call, 2026-07-10).
PUBLIC_SECTIONS = {
    "order_batch_urgency": False,  # /order — batch-campaign urgency banner (counts live in the template)
    "pricing_vip":        False,   # /pricing — VIP / custom-pricing plan card
    "pricing_membership": False,   # /pricing — Otlobly Membership band
    "pricing_compare":    True,    # /pricing — plan comparison table
    "landing_track":      True,    # landing — شحنتك الحالية tracking teaser
    "landing_how":        True,    # landing — كيف تعمل steps
    "landing_why":        True,    # landing — ليش اطلبلي
}


def public_sections(config=None):
    """Merged section-visibility flags: stored value wins, else the code default."""
    config = config or cfg.load()
    return {k: bool(cfg.get(config, f"public.sections.{k}", d))
            for k, d in PUBLIC_SECTIONS.items()}


# Employee-facing status labels: the OWNER keeps the raw ClickUp vocabulary
# (recieved rd / sent no rd …); staff without admin_actions see these simplified
# labels instead — same idea as the customer remap, but for employees. `pick`
# marks the statuses an employee may SET from the dropdown (curated so the
# RD/financial distinctions stay the owner's job). Display-level only — the
# underlying status values never change.
DEFAULT_EMPLOYEE_STATUS_MAP = [
    {"status": "oredered",           "label": "مطلوبة من أمازون · ordered",      "pick": False},
    {"status": "shipped",            "label": "شُحنت من أمازون · shipped",        "pick": False},
    {"status": "doc sent to gash",   "label": "أُرسلت المستندات لغاش · docs sent", "pick": True},
    {"status": "in clearance",       "label": "في التخليص · in clearance",       "pick": True},
    {"status": "waiting verification", "label": "بانتظار التحقق · verification", "pick": False},
    {"status": "recieved rd",        "label": "استلمناها بالمكتب · received",     "pick": False},
    {"status": "recieved no rd",     "label": "استلمناها بالمكتب · received ✓",   "pick": True},
    {"status": "sent rd",            "label": "أُرسلت للعميل · sent",             "pick": False},
    {"status": "sent no rd",         "label": "أُرسلت للعميل · sent ✓",           "pick": True},
    {"status": "delievered rd",      "label": "سُلّمت للعميل · delivered",        "pick": False},
    {"status": "delievered no rd",   "label": "سُلّمت للعميل · delivered ✓",      "pick": True},
    {"status": "not recieved rd",    "label": "لم تصل بعد · not arrived",        "pick": False},
    {"status": "not recieved no rd", "label": "لم تصل بعد · not arrived ",       "pick": False},
    {"status": "picked up by ger",   "label": "استلمها جيرزيم · with Gerizim",    "pick": True},
    {"status": "ariived at destnation", "label": "وصلت الوجهة · arrived",        "pick": False},
    {"status": "delivered",          "label": "سُلّمت · delivered",              "pick": True},
    {"status": "rd",                 "label": "قيد المتابعة · in progress",      "pick": False},
    {"status": "refund request",     "label": "قيد المتابعة · in progress ",     "pick": False},
    {"status": "cancelled",          "label": "أُلغيت · cancelled",              "pick": False},
    {"status": "complete",           "label": "مكتملة · complete",               "pick": True},
]

# Leluxe clearance-email compose defaults (Settings-editable). One GWD per email
# on purpose — GAASH replies then map 1:1 to packages on the board.
DEFAULT_CLEARANCE_SUBJECT = "تخليص جمركي · Customs clearance — {gwd}"
DEFAULT_CLEARANCE_BODY = (
    "مرحباً،\n\n"
    "بخصوص التخليص الجمركي للطرد رقم:\n{gwd}\n\n"
    "مستندات العميل ({customer}) مرفوعة عبر رابط غاش:\n{upload_link}\n\n"
    "نرجو إعلامنا إذا لزم أي مستند إضافي.\n"
    "Please advise on customs clearance for the package above — the requested "
    "documents were uploaded via the GAASH link.\n\nشكراً جزيلاً")

# 📧 GAASH Mail sequences (gaash_mail.py): the 4 step templates, escalating in
# firmness. All Settings-editable — the owner replaces these with his real
# wording. Placeholders: {gwd} {customer} {upload_link} {id_name}
# {days_waiting} {step}.
DEFAULT_GAASH_STEPS = [
    {"subject_tpl": "Customs clearance request — {gwd}",
     "body_tpl": ("Hello GAASH team,\n\n"
                  "Please proceed with the customs clearance of package {gwd}.\n"
                  "The ID document ({id_name}) is attached to this email, and the "
                  "documents were also uploaded via your portal:\n{upload_link}\n\n"
                  "Please confirm receipt and let us know if anything else is "
                  "needed.\n\nThank you,\nOtlobly")},
    {"subject_tpl": "Follow-up — customs clearance {gwd}",
     "body_tpl": ("Hello,\n\n"
                  "Following up on package {gwd} — we sent the clearance request "
                  "with the ID attached {days_waiting} days ago and have not "
                  "received an update yet.\n\n"
                  "Could you please check its status and confirm?\n\nThank you")},
    {"subject_tpl": "2nd follow-up — {gwd} still not cleared",
     "body_tpl": ("Hello,\n\n"
                  "This is our second follow-up on package {gwd} "
                  "({days_waiting} days now). The required documents were "
                  "provided; the package is still not cleared.\n\n"
                  "Please treat this as urgent and reply with the current "
                  "status today.\n\nThank you")},
    {"subject_tpl": "URGENT — package {gwd} stuck {days_waiting} days, please escalate",
     "body_tpl": ("Hello,\n\n"
                  "Package {gwd} has been stuck for {days_waiting} days despite "
                  "complete documents and three previous emails with no "
                  "resolution.\n\n"
                  "Please escalate this to a supervisor and reply TODAY with a "
                  "concrete clearance date. If we do not hear back we will have "
                  "to raise a formal complaint.\n\nOtlobly")},
]

DEFAULT_GAASH_MAIL = {
    "to_address": "GaashWW@glassix.support",
    "dry_run": True,                 # nothing is ever emailed until the owner flips this
    "ack_window_min": 3,             # incoming within N min of our send = auto-ack
    "poll_interval_min": 5,
    "cadence_days": [2, 2, 2],       # gaps after emails 1, 2, 3
    "auto_resend": True,             # office-closed bounce → resend at resend_hour
    "resend_phrases": ["closed now"],
    "resend_hour": 9,                # Israel time
    "missing_doc_keywords": ["kmt", "missing document", "additional document"],
    "daily_send_cap": 40,
    # on-package name (ClickUp "NAME ON PACKAGEE": FAISAL / QAIS / Nuray…) → that
    # person's ID number; templates insert it via {name_id}
    "name_ids": {},
    # the account MOST parcels ship under — used only when neither board names a
    # parcel (most Leluxe rows leave NAME ON PACKAGEE blank). Always overridable
    # per package, and every surface marks a name that came from here.
    "default_name": "",
}


def read(config=None):
    config = config or cfg.load()
    return {
        "markup_pct": cfg.get(config, "pricing.markup_pct", 0.10),
        "ils_per_usd": cfg.get(config, "fx.ils_per_usd", 3.7),
        # Customer-message rate for the Package prep page ($ → ₪ display only;
        # deliberately separate from ils_per_usd so deposit bookkeeping is safe).
        "pkg_ils_per_usd": cfg.get(config, "fx.pkg_ils_per_usd", 3.1),
        "destination": {
            "shipping_location": cfg.get(config, "estimate.destination.shipping_location", "Israel"),
            "delivery_zip": cfg.get(config, "estimate.destination.delivery_zip", ""),
            "label": cfg.get(config, "estimate.destination.label", "Palestine (via Israel)"),
        },
        "shipping_rule": cfg.get(config, "estimate.shipping_rule", {"type": "flat", "value": 15}),
        "import_rule": cfg.get(config, "estimate.import_rule", {"type": "pct", "value": 0}),
        "customer_mode": bool(cfg.get(config, "estimate.customer_mode", False)),
        "business_whatsapp": cfg.get(config, "business.whatsapp", ""),
        # Facebook page link + $ coupon promised in the Package-prep review request.
        "business_facebook_reviews": cfg.get(config, "business.facebook_reviews_url", ""),
        "reviews_coupon_usd": cfg.get(config, "reviews.coupon_usd", 5),
        "tracking_status_map": cfg.get(config, "customer_tracking.status_map",
                                       tracking.DEFAULT_STATUS_MAP),
        "tracking_default_label": cfg.get(config, "customer_tracking.default_label",
                                          tracking.DEFAULT_CUSTOMER_LABEL),
        # Owner-set Otlobly stage → customer label ("{name}" = the customer's name).
        "tracking_otlobly_map": cfg.get(config, "customer_tracking.otlobly_map",
                                        tracking.DEFAULT_OTLOBLY_MAP),
        # Telegram owner alerts (env vars win over these; token is a secret —
        # it lives in env/config on the data disk, never the repo).
        "alerts": {
            "telegram_token": cfg.get(config, "alerts.telegram_token", ""),
            "telegram_chat": cfg.get(config, "alerts.telegram_chat", ""),
            "env_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            "stop_statuses": cfg.get(config, "alerts.stop_statuses", _alerts.STOP_DEFAULT),
            "gaash_days": cfg.get(config, "alerts.gaash_days", _alerts.GAASH_DAYS_DEFAULT),
            "arrival_days": cfg.get(config, "alerts.arrival_days", _alerts.ARRIVAL_DAYS_DEFAULT),
        },
        # Employee status labels (display remap for non-admin staff) + the
        # statuses an employee may set (pick).
        "employee_status_map": cfg.get(config, "staff.employee_status_map",
                                       DEFAULT_EMPLOYEE_STATUS_MAP),
        # ClickUp-style custom fields, per business: today `po` (Purchases board
        # columns) — [{key,label,type:text|number|select|date,options[]}].
        "custom_fields": {"po": cfg.get(config, "custom_fields.po", [])},
        # Leluxe per-package clearance email (mailto: compose defaults). The
        # placeholders {gwd}, {upload_link}, {customer} are filled per package —
        # each email carries exactly ONE GWD so GAASH replies map to packages.
        "clearance": {
            "email_to": cfg.get(config, "clearance.email_to", ""),
            "email_cc": cfg.get(config, "clearance.email_cc", ""),
            "subject_tpl": cfg.get(config, "clearance.subject_tpl",
                                   DEFAULT_CLEARANCE_SUBJECT),
            "body_tpl": cfg.get(config, "clearance.body_tpl",
                                DEFAULT_CLEARANCE_BODY),
        },
        # 📧 GAASH Mail sequences (gaash_mail.py) — templates + cadence + guards.
        "gaash_mail": {
            **DEFAULT_GAASH_MAIL,
            **{k: cfg.get(config, f"gaash_mail.{k}", DEFAULT_GAASH_MAIL[k])
               for k in DEFAULT_GAASH_MAIL},
            "steps": cfg.get(config, "gaash_mail.steps", DEFAULT_GAASH_STEPS),
        },
        # White-label the order card per business. Internal/region-specific features
        # default OFF so a fresh tenant gets a clean, generic card. (Roadmap #2.)
        "card_flags": {
            "multilogin": bool(cfg.get(config, "card.flags.multilogin", False)),
            "clickup": bool(cfg.get(config, "card.flags.clickup", False)),
            "screenshot": bool(cfg.get(config, "card.flags.screenshot", False)),
            "second_currency": bool(cfg.get(config, "card.flags.second_currency", False)),
        },
        "card_labels": {
            "courier": cfg.get(config, "card.labels.courier", "Tracking"),
            "currency": cfg.get(config, "card.labels.currency", "USD"),
            "currency2": cfg.get(config, "card.labels.currency2", "AED"),
            "box_term": cfg.get(config, "card.labels.box_term", "Profile"),
        },
        # Public-website section visibility (Shopify-style hide/show from Settings).
        "public_sections": public_sections(config),
        # Monthly Meta ad spend typed by the admin (USD) — feeds the P&L meta line.
        "meta_manual": cfg.get(config, "pnl.meta.manual", {}) or {},
        # Campaigns excluded from the P&L Meta total (drill-down checkboxes).
        # None = never saved → the P&L seeds defaults from the legacy campaign_filter.
        "meta_excluded_campaign_ids": cfg.get(config, "pnl.meta.excluded_campaign_ids", None),
    }


def _rule(d):
    t = d.get("type", "flat")
    return {"type": "pct" if t == "pct" else "flat", "value": round(_f(d.get("value"), 0), 2)}


# ClickUp-style custom-field types (v2). Config values users may NOT set are
# dropped; unknown types fall back to a plain text column so a bad payload can
# never break the board.
CF_TYPES = ("text", "longtext", "number", "money", "date", "checkbox",
            "phone", "url", "email", "select", "labels", "rating", "progress")
CF_CURRENCIES = ("USD", "ILS", "EUR", "AED", "JOD", "GBP", "SAR", "EGP", "TRY")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _cf_options(raw):
    """[{name,color}] for dropdown/labels — name required, color hex-validated."""
    out = []
    for o in (raw or []):
        if isinstance(o, dict):
            name, color = str(o.get("name", "")).strip(), str(o.get("color", "")).strip()
        else:
            name, color = str(o).strip(), ""
        if not name:
            continue
        out.append({"name": name, "color": color if _HEX.match(color) else "#8d8d8d"})
        if len(out) >= 30:
            break
    return out


def _clean_custom_fields(raw):
    """Validate the per-business custom-field DEFINITIONS (Purchases board).
    One def per field: key (immutable slug) + label + type + type-specific config."""
    fields, seen = [], set()
    for f in (raw or []):
        if not isinstance(f, dict):
            continue
        label = str(f.get("label", "")).strip()
        key = re.sub(r"[^a-z0-9_]+", "_", str(f.get("key") or label).strip().lower()).strip("_")
        typ = str(f.get("type", "text")).strip().lower()
        if typ == "dropdown":
            typ = "select"
        if not key or not label or key in seen:
            continue
        seen.add(key)
        d = {"key": key, "label": label, "type": typ if typ in CF_TYPES else "text"}
        if d["type"] in ("select", "labels"):
            d["options"] = _cf_options(f.get("options"))
        elif d["type"] == "money":
            cur = str(f.get("currency", "USD")).strip().upper()
            d["currency"] = cur if cur in CF_CURRENCIES else "USD"
            d["precision"] = min(2, max(0, int(_f(f.get("precision"), 2))))
        elif d["type"] == "number":
            d["precision"] = min(4, max(0, int(_f(f.get("precision"), 0))))
        elif d["type"] == "date":
            d["include_time"] = bool(f.get("include_time"))
        elif d["type"] == "rating":
            d["icon"] = "heart" if str(f.get("icon", "")).strip().lower() == "heart" else "star"
            d["count"] = min(10, max(3, int(_f(f.get("count"), 5))))
        elif d["type"] == "progress":
            lo = int(_f(f.get("min"), 0))
            hi = int(_f(f.get("max"), 100))
            d["min"], d["max"] = lo, (hi if hi > lo else lo + 100)
        fields.append(d)
        if len(fields) >= 20:
            break
    return fields


def apply(body, config=None, persist=True):
    config = config or cfg.load()
    if "markup_pct" in body:
        cfg.set_path(config, "pricing.markup_pct", max(0.0, _f(body["markup_pct"], 0.10)))
    if "ils_per_usd" in body:
        cfg.set_path(config, "fx.ils_per_usd", max(0.01, _f(body["ils_per_usd"], 3.7)))
    if "pkg_ils_per_usd" in body:
        cfg.set_path(config, "fx.pkg_ils_per_usd", max(0.01, _f(body["pkg_ils_per_usd"], 3.1)))
    dest = body.get("destination") or {}
    for k in ("shipping_location", "delivery_zip", "label"):
        if k in dest:
            cfg.set_path(config, f"estimate.destination.{k}", str(dest[k]).strip())
    for r in ("shipping_rule", "import_rule"):
        if isinstance(body.get(r), dict):
            cfg.set_path(config, f"estimate.{r}", _rule(body[r]))
    if "customer_mode" in body:
        cfg.set_path(config, "estimate.customer_mode", bool(body["customer_mode"]))
    if "business_whatsapp" in body:
        cfg.set_path(config, "business.whatsapp",
                     re.sub(r"[^\d+]", "", str(body["business_whatsapp"])))
    if "business_facebook_reviews" in body:            # a URL — keep it verbatim
        cfg.set_path(config, "business.facebook_reviews_url",
                     str(body["business_facebook_reviews"] or "").strip())
    if "reviews_coupon_usd" in body:
        cfg.set_path(config, "reviews.coupon_usd", max(0, int(_f(body["reviews_coupon_usd"], 5))))
    if isinstance(body.get("tracking_status_map"), list):
        rows = []
        for r in body["tracking_status_map"]:
            if not isinstance(r, dict):
                continue
            match = str(r.get("match", "")).strip()
            if not match:
                continue
            rows.append({"match": match, "label": str(r.get("label", "")).strip(),
                         "bucket": (str(r.get("bucket", "transit")).strip() or "transit"),
                         "hidden": bool(r.get("hidden"))})
        cfg.set_path(config, "customer_tracking.status_map", rows)
    if "tracking_default_label" in body:
        cfg.set_path(config, "customer_tracking.default_label",
                     str(body["tracking_default_label"]).strip() or "In transit")
    if isinstance(body.get("tracking_otlobly_map"), list):
        rows = []
        for r in body["tracking_otlobly_map"]:
            if not isinstance(r, dict):
                continue
            status = str(r.get("status", "")).strip()
            label = str(r.get("label", "")).strip()
            if not status or not label:
                continue
            rows.append({"status": status, "label": label,
                         "bucket": (str(r.get("bucket", "arrived")).strip() or "arrived")})
        cfg.set_path(config, "customer_tracking.otlobly_map", rows)
    al = body.get("alerts")
    if isinstance(al, dict):
        for k in ("telegram_token", "telegram_chat"):
            if k in al:
                cfg.set_path(config, f"alerts.{k}", str(al[k] or "").strip())
        if isinstance(al.get("stop_statuses"), list):
            cfg.set_path(config, "alerts.stop_statuses",
                         [str(s).strip().lower() for s in al["stop_statuses"] if str(s).strip()])
        for k in ("gaash_days", "arrival_days"):
            if isinstance(al.get(k), list):
                vals = sorted({int(x) for x in al[k]
                               if str(x).strip().lstrip("-").isdigit() and int(x) > 0})
                if vals:
                    cfg.set_path(config, f"alerts.{k}", vals)
    if isinstance(body.get("employee_status_map"), list):
        rows = []
        for r in body["employee_status_map"]:
            if not isinstance(r, dict):
                continue
            status = str(r.get("status", "")).strip().lower()
            label = str(r.get("label", "")).strip()
            if not status or not label:
                continue
            rows.append({"status": status, "label": label, "pick": bool(r.get("pick"))})
        cfg.set_path(config, "staff.employee_status_map", rows)
    cf = body.get("custom_fields")
    if isinstance(cf, dict) and isinstance(cf.get("po"), list):
        cfg.set_path(config, "custom_fields.po", _clean_custom_fields(cf["po"]))
    cl = body.get("clearance")
    if isinstance(cl, dict):
        for k in ("email_to", "email_cc"):
            if k in cl:
                cfg.set_path(config, f"clearance.{k}", str(cl[k] or "").strip())
        for k in ("subject_tpl", "body_tpl"):
            # a cleared template falls back to the code default, never to ""
            if str(cl.get(k) or "").strip():
                cfg.set_path(config, f"clearance.{k}", str(cl[k]).strip())
    gm = body.get("gaash_mail")
    if isinstance(gm, dict):
        if "to_address" in gm:
            cfg.set_path(config, "gaash_mail.to_address",
                         str(gm["to_address"] or "").strip())
        for k in ("dry_run", "auto_resend"):
            if k in gm:
                cfg.set_path(config, f"gaash_mail.{k}", bool(gm[k]))
        for k, lo, hi in (("ack_window_min", 0, 60), ("poll_interval_min", 1, 120),
                          ("resend_hour", 0, 23), ("daily_send_cap", 1, 500)):
            if k in gm:
                try:
                    cfg.set_path(config, f"gaash_mail.{k}",
                                 min(hi, max(lo, int(gm[k]))))
                except (TypeError, ValueError):
                    pass
        if isinstance(gm.get("cadence_days"), list):
            days = []
            for v in gm["cadence_days"][:3]:
                try:
                    days.append(min(30, max(0.5, float(v))))
                except (TypeError, ValueError):
                    pass
            if len(days) == 3:
                cfg.set_path(config, "gaash_mail.cadence_days", days)
        for k in ("resend_phrases", "missing_doc_keywords"):
            if isinstance(gm.get(k), list):
                vals = [str(x).strip().lower() for x in gm[k]
                        if str(x or "").strip()][:15]
                if vals:
                    cfg.set_path(config, f"gaash_mail.{k}", vals)
        if "default_name" in gm:
            cfg.set_path(config, "gaash_mail.default_name",
                         re.sub(r"\s+", " ", str(gm["default_name"] or "")).strip()[:60])
        if isinstance(gm.get("name_ids"), dict):
            # on-package name → ID number; the whole dict is replaced each save, so
            # a cleared input in the modal deletes that name's mapping
            m = {}
            for k, v in list(gm["name_ids"].items())[:80]:
                k2 = re.sub(r"\s+", " ", str(k or "")).strip()
                v2 = str(v or "").strip()
                if k2 and v2 and len(k2) <= 60 and len(v2) <= 40:
                    m[k2] = v2
            cfg.set_path(config, "gaash_mail.name_ids", m)
        if isinstance(gm.get("steps"), list):
            steps = []
            for i, s in enumerate(gm["steps"][:4]):
                if not isinstance(s, dict):
                    s = {}
                base = (DEFAULT_GAASH_STEPS[i] if i < len(DEFAULT_GAASH_STEPS)
                        else DEFAULT_GAASH_STEPS[-1])
                steps.append({
                    "subject_tpl": str(s.get("subject_tpl") or "").strip()
                    or base["subject_tpl"],
                    "body_tpl": str(s.get("body_tpl") or "").strip()
                    or base["body_tpl"],
                })
            if len(steps) == 4:
                cfg.set_path(config, "gaash_mail.steps", steps)
    flags = body.get("card_flags")
    if isinstance(flags, dict):
        for k in ("multilogin", "clickup", "screenshot", "second_currency"):
            if k in flags:
                cfg.set_path(config, f"card.flags.{k}", bool(flags[k]))
    labels = body.get("card_labels")
    if isinstance(labels, dict):
        for k in ("courier", "currency", "currency2", "box_term"):
            if k in labels:
                cfg.set_path(config, f"card.labels.{k}", str(labels[k]).strip())
    secs = body.get("public_sections")
    if isinstance(secs, dict):
        for k in PUBLIC_SECTIONS:
            if k in secs:
                cfg.set_path(config, f"public.sections.{k}", bool(secs[k]))
    if isinstance(body.get("meta_manual"), dict):
        clean = {}
        for k, v in body["meta_manual"].items():
            k = str(k).strip()
            if re.match(r"^\d{4}-\d{2}$", k):
                clean[k] = round(_f(v, 0), 2)
        cfg.set_path(config, "pnl.meta.manual", clean)
    if isinstance(body.get("meta_excluded_campaign_ids"), list):
        clean = sorted({str(x).strip() for x in body["meta_excluded_campaign_ids"]
                        if str(x).strip()})
        cfg.set_path(config, "pnl.meta.excluded_campaign_ids", clean)
    if persist:
        cfg.save(config)
    return read(config)


if __name__ == "__main__":
    import json
    print(json.dumps(read(), ensure_ascii=False, indent=2))
