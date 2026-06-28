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


def read(config=None):
    config = config or cfg.load()
    return {
        "markup_pct": cfg.get(config, "pricing.markup_pct", 0.10),
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
    }


def _rule(d):
    t = d.get("type", "flat")
    return {"type": "pct" if t == "pct" else "flat", "value": round(_f(d.get("value"), 0), 2)}


def apply(body, config=None, persist=True):
    config = config or cfg.load()
    if "markup_pct" in body:
        cfg.set_path(config, "pricing.markup_pct", max(0.0, _f(body["markup_pct"], 0.10)))
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
    if persist:
        cfg.save(config)
    return read(config)


if __name__ == "__main__":
    import json
    print(json.dumps(read(), ensure_ascii=False, indent=2))
