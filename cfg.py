#!/usr/bin/env python3
"""Tiny shared config loader. Reads config.json (copy of config.example.json)."""

import json
from pathlib import Path

from paths import data_path, write_json_atomic

# config.json is WRITABLE (the Settings page saves to it), so it lives in the
# data dir — the project folder locally, or the persistent disk in a hosted
# deploy. config.default.json is a committed, secret-free seed used to populate
# a fresh disk on first boot; config.example.json is the last-resort fallback.
CONFIG_FILE = data_path("config.json")
DEFAULT_FILE = Path(__file__).with_name("config.default.json")
EXAMPLE_FILE = Path(__file__).with_name("config.example.json")


def _seed():
    """On a fresh data dir (e.g. a new server disk), create config.json from the
    committed default so the app boots with the real, non-secret configuration."""
    if CONFIG_FILE.exists():
        return
    src = DEFAULT_FILE if DEFAULT_FILE.exists() else (
        EXAMPLE_FILE if EXAMPLE_FILE.exists() else None)
    if src and CONFIG_FILE.parent.exists():
        try:
            CONFIG_FILE.write_text(src.read_text())
        except OSError:
            pass


def _merge(base, over):
    """Deep-merge `over` onto `base` (override wins; nested dicts merged)."""
    out = dict(base or {})
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _defaults():
    """Generic, secret-free base for a NEW tenant — the committed config.default.json
    (NEVER Otlobly's live config.json, so a broker never inherits Otlobly's WhatsApp
    number, ad spend, tracking map, etc.)."""
    for p in (DEFAULT_FILE, EXAMPLE_FILE):
        if p.exists():
            try:
                return json.loads(p.read_text())
            except ValueError:
                continue
    return {}


def load(required=False):
    """The config for the CURRENT business. Business #1 (Otlobly) = the live
    config.json, exactly as before. Every other business (broker) = the generic
    defaults overlaid with that business's own saved settings (in its businesses row)."""
    from db import current_business, get_business_config   # lazy: cfg is imported very early
    _seed()
    if current_business() == 1:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
        if required:
            raise SystemExit(f"Missing {CONFIG_FILE.name}. "
                             f"Copy {EXAMPLE_FILE.name} to config.json and fill it in.")
        return json.loads(EXAMPLE_FILE.read_text())
    overrides = get_business_config(current_business(), "settings", None) or {}
    return _merge(_defaults(), overrides)


def get(cfg, path, default=None):
    """Nested lookup: get(cfg, 'clickup.fields.phone')."""
    cur = cfg
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def set_path(cfg, dotted, value):
    """Set a nested key, creating intermediate dicts: set_path(c,'estimate.shipping_rule.value',15)."""
    cur = cfg
    keys = dotted.split(".")
    for key in keys[:-1]:
        if not isinstance(cur.get(key), dict):
            cur[key] = {}
        cur = cur[key]
    cur[keys[-1]] = value
    return cfg


def save(cfg):
    """Persist the config for the CURRENT business. Business #1 → config.json (the
    live file, unchanged); a broker → its own settings override in the businesses row."""
    from db import current_business, set_business_config
    if current_business() == 1:
        write_json_atomic(CONFIG_FILE, cfg)    # crash-safe: temp + atomic rename
    else:
        set_business_config(current_business(), "settings", cfg)
