#!/usr/bin/env python3
"""
Self-checks: Otlobly's Meta ad spend never leaks into a broker's P&L, and the
Meta-ads P&L slot is a plan-linked (Pro) feature.

The env creds, the synced meta_cache.json, and the legacy meta_spend.json are all
OTLOBLY's ad account. Before this fix every tenant's P&L read them — a broker's
net profit was Otlobly's ad spend below zero, with a green "meta: connected" chip.

    ./.venv/bin/python test_meta_isolation.py
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-meta-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"
os.environ["META_AD_ACCOUNT_ID"] = "act_123"      # "Otlobly's" ad account (fake)
os.environ["META_ACCESS_TOKEN"] = "tok"

import app as appmod   # noqa: E402
import auth            # noqa: E402
import db              # noqa: E402
import features        # noqa: E402
import meta            # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def main():
    db.init_db()
    db.create_user("otlo", auth.hash_pw("s1"), "admin", "Q", business_id=1)
    co = client("otlo")
    a = co.post("/api/admin/brokers", json={"name": "ACME", "tier": "starter",
                "admin_username": "acme-admin", "admin_password": "secret1"}).get_json()["business_id"]
    b = co.post("/api/admin/brokers", json={"name": "Beta", "tier": "pro",
                "admin_username": "beta-admin", "admin_password": "secret1"}).get_json()["business_id"]
    ca = client("acme-admin", "secret1")
    cb = client("beta-admin", "secret1")

    # Otlobly's synced Meta cache (the leak source): $132.07 across two ad days.
    meta.CACHE.parent.mkdir(parents=True, exist_ok=True)
    meta.CACHE.write_text(json.dumps({
        "total_usd": 132.07, "by_month": {"2026-07": 132.07},
        "by_day": {"2026-07-10": 100.0, "2026-07-11": 32.07},
        "source": "api:act_123", "pulled_at": "2026-07-12T10:00:00+03:00",
        "last_error": None}))

    # 1) The feature flag is plan-linked (Pro), overridable, and always on for #1.
    check("starter broker: meta_ads off", features.resolve(a)["meta_ads"] is False)
    check("pro broker: meta_ads on", features.resolve(b)["meta_ads"] is True)
    db.set_business_config(a, "features", {"meta_ads": True})
    check("manual override wins over tier", features.resolve(a)["meta_ads"] is True)
    db.set_business_config(a, "features", {})
    check("business 1 always has meta_ads", features.resolve(1)["meta_ads"] is True)

    # 2) Otlobly's P&L is unchanged: api mode, spend from the cache.
    d1 = co.get("/api/pnl").get_json()
    check("Otlobly meta connected via api", d1["sources"]["meta"]["connected"] is True)
    check("Otlobly meta spend intact", d1["totals"]["meta_usd"] == 132.07)
    check("Otlobly meta not hidden", "hidden" not in d1["sources"]["meta"])
    check("Otlobly counts its ad days", d1["totals"]["ad_days"] == 2)

    # 3) Starter broker: the whole Meta slot is hidden and ZERO — no leak.
    d2 = ca.get("/api/pnl").get_json()
    check("starter broker meta hidden", d2["sources"]["meta"].get("hidden") is True)
    check("starter broker meta spend is zero", d2["totals"]["meta_usd"] == 0)
    check("starter broker net = gross (no ad deduction)",
          d2["totals"]["net_profit_usd"] == d2["totals"]["gross_profit_usd"])
    check("starter broker has no ad days", d2["totals"]["ad_days"] == 0)
    check("starter broker meta drill is 403",
          ca.get("/api/pnl/drill?metric=meta").status_code == 403)
    check("Otlobly meta drill still works",
          co.get("/api/pnl/drill?metric=meta").status_code == 200)

    # 4) Pro broker: the slot exists (manual mode, their own data = $0) — no leak.
    d3 = cb.get("/api/pnl").get_json()
    check("pro broker sees the meta slot", not d3["sources"]["meta"].get("hidden"))
    check("pro broker meta is manual (own data), not Otlobly's cache",
          d3["totals"]["meta_usd"] == 0 and "manual" in d3["sources"]["meta"]["detail"])
    check("pro broker has no ad days from Otlobly's campaigns", d3["totals"]["ad_days"] == 0)

    # 5) Source scoping directly: env creds / cache / legacy file are business-1 only.
    db.set_current_business(a)
    check("broker ctx: has_api_creds false despite env", meta.has_api_creds() is False)
    check("broker ctx: read_cache is None", meta.read_cache() is None)
    legacy = Path(tempfile.gettempdir()) / "_meta_leak_test.json"
    legacy.write_text(json.dumps({"2026-07": 500.0}))
    orig = meta.MANUAL
    meta.MANUAL = legacy
    try:
        check("broker ctx: legacy manual file ignored",
              meta.from_manual({})["total_usd"] == 0)
        db.set_current_business(1)
        check("otlobly ctx: legacy manual file read",
              meta.from_manual({})["total_usd"] == 500.0)
    finally:
        meta.MANUAL = orig
        legacy.unlink()
        db.set_current_business(1)

    # 6) A broker's "Refresh sources" can never clobber Otlobly's cache.
    before = meta.CACHE.read_text()
    db.set_current_business(a)
    out = meta.sync_once()
    db.set_current_business(1)
    check("broker sync_once is a guarded no-op", out.get("skipped") is not None)
    check("Otlobly's cache untouched", meta.CACHE.read_text() == before)
    check("broker /api/pnl/refresh doesn't clobber the cache",
          ca.post("/api/pnl/refresh", json={}).status_code == 200
          and meta.CACHE.read_text() == before)

    # 7) The Settings monthly-spend panel is gated in the shell.
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    check("settings panel carries the gate id", 'id="metaSpendPanel"' in html)
    check("boot JS gates the panel on the feature",
          'hide("metaSpendPanel", FEAT.meta_ads!==false)' in html)

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
