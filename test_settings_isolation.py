#!/usr/bin/env python3
"""
Self-checks for per-tenant settings (Tatabu Phase 5).

Business #1 (Otlobly) keeps config.json exactly as before; every other business
gets generic defaults (config.default.json — never Otlobly's live config, so its
WhatsApp / fx / markup never leak) plus its OWN saved overrides in its businesses
row. A broker changing a setting never touches Otlobly's config.json.

    ./.venv/bin/python test_settings_isolation.py
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-set-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

# Seed Otlobly's live config.json with DISTINCTIVE values before importing cfg.
_CONFIG = _TMP / "config.json"
_CONFIG.write_text(json.dumps({
    "pricing": {"markup_pct": 0.15},
    "business": {"whatsapp": "970111OTLO"},
    "fx": {"ils_per_usd": 4.2},
}))

import cfg          # noqa: E402
import db           # noqa: E402
import pricing      # noqa: E402
import settings as settings_mod   # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    db.init_db()

    # 1) Otlobly (business #1) reads its live config.json.
    db.set_current_business(1)
    check("Otlobly markup = 0.15 (its config.json)", cfg.get(cfg.load(), "pricing.markup_pct") == 0.15)
    check("Otlobly whatsapp = its own", cfg.get(cfg.load(), "business.whatsapp") == "970111OTLO")
    check("pricing.markup_pct() = 0.15 for Otlobly", pricing.markup_pct() == 0.15)

    # 2) A broker gets GENERIC defaults — no Otlobly leak.
    bid = db.create_business("Broker Co")
    db.set_current_business(bid)
    check("broker markup = default 0.10 (NOT 0.15)", cfg.get(cfg.load(), "pricing.markup_pct", 0.10) == 0.10)
    check("broker does NOT inherit Otlobly whatsapp",
          cfg.get(cfg.load(), "business.whatsapp", "") != "970111OTLO")
    check("pricing.markup_pct() = 0.10 for a fresh broker", pricing.markup_pct() == 0.10)

    # 3) A broker's saved setting persists to ITS OWN record, not Otlobly's.
    settings_mod.apply({"markup_pct": 0.25})            # persists via cfg.save → business config
    check("broker markup now 0.25", pricing.markup_pct() == 0.25)
    check("broker settings.read reflects 0.25", settings_mod.read()["markup_pct"] == 0.25)

    # 4) Otlobly is completely unaffected.
    db.set_current_business(1)
    check("Otlobly markup STILL 0.15", pricing.markup_pct() == 0.15)
    check("Otlobly config.json file untouched (still 0.15)",
          json.loads(_CONFIG.read_text())["pricing"]["markup_pct"] == 0.15)

    # 5) A second broker is independent of the first.
    bid2 = db.create_business("Broker Two")
    db.set_current_business(bid2)
    check("second broker starts at default 0.10", pricing.markup_pct() == 0.10)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import shutil
    import sys
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
