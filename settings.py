#!/usr/bin/env python3
"""
The handful of business-logic settings editable from the Admin ⚙ page (instead of
hand-editing config.json): the markup, the estimator destination + shipping/import
rules, customer-mode, and the business WhatsApp number. Whitelisted on purpose —
no arbitrary config keys are writable from the UI.

`read()` returns the current values; `apply(body)` validates + persists to config.json.
Shared by dashboard.py (local) and app.py (hosted).
"""

import re

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


def read(config=None):
    config = config or cfg.load()
    return {
        "markup_pct": cfg.get(config, "pricing.markup_pct", 0.10),
        "ils_per_usd": cfg.get(config, "fx.ils_per_usd", 3.7),
        "destination": {
            "shipping_location": cfg.get(config, "estimate.destination.shipping_location", "Israel"),
            "delivery_zip": cfg.get(config, "estimate.destination.delivery_zip", ""),
            "label": cfg.get(config, "estimate.destination.label", "Palestine (via Israel)"),
        },
        "shipping_rule": cfg.get(config, "estimate.shipping_rule", {"type": "flat", "value": 15}),
        "import_rule": cfg.get(config, "estimate.import_rule", {"type": "pct", "value": 0}),
        "customer_mode": bool(cfg.get(config, "estimate.customer_mode", False)),
        "business_whatsapp": cfg.get(config, "business.whatsapp", ""),
        "tracking_status_map": cfg.get(config, "customer_tracking.status_map",
                                       tracking.DEFAULT_STATUS_MAP),
        "tracking_default_label": cfg.get(config, "customer_tracking.default_label",
                                          tracking.DEFAULT_CUSTOMER_LABEL),
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


def apply(body, config=None, persist=True):
    config = config or cfg.load()
    if "markup_pct" in body:
        cfg.set_path(config, "pricing.markup_pct", max(0.0, _f(body["markup_pct"], 0.10)))
    if "ils_per_usd" in body:
        cfg.set_path(config, "fx.ils_per_usd", max(0.01, _f(body["ils_per_usd"], 3.7)))
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
