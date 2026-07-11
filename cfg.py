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


def load(required=False):
    """Return the parsed config. Falls back to the example file so the tool
    still runs (with placeholders) before the user has filled in real IDs."""
    _seed()
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    if required:
        raise SystemExit(f"Missing {CONFIG_FILE.name}. "
                         f"Copy {EXAMPLE_FILE.name} to config.json and fill it in.")
    return json.loads(EXAMPLE_FILE.read_text())


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
    """Persist the config back to config.json (the live, gitignored file)."""
    write_json_atomic(CONFIG_FILE, cfg)        # crash-safe: temp + atomic rename
