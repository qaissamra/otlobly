#!/usr/bin/env python3
"""
Meta (Facebook/Instagram) ad spend for the P&L page — your customer-acquisition
cost. All amounts normalised to USD.

config.pnl.meta.mode:
  "manual" (default) — read meta_spend.json: { "2026-06": 420.00, "2026-05": 310 }
  "api"              — pull monthly spend from the Meta Marketing API using
                       META_AD_ACCOUNT_ID + META_ACCESS_TOKEN (ads_read) from .env.

  python3 meta.py --selftest        # offline aggregation test
  python3 meta.py --dry-run         # pull + print, don't write the cache
  python3 meta.py                   # write reports/meta_cache.json

Connect the API: Ads Manager → account dropdown → act_##########. Token: Business
Settings → System Users → Generate token (ads_read, read_insights). Put both in .env.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, parse, error

import cfg
from paths import data_path

CACHE = Path(data_path("meta_cache.json"))   # persistent disk — survives redeploys
MANUAL = Path(__file__).with_name("meta_spend.json")
GRAPH = "https://graph.facebook.com/v21.0"


def _to_usd(amount, currency, config):
    """Convert an ad-account-currency spend to USD using config.fx."""
    cur = (currency or "USD").upper()
    if cur == "USD":
        return amount
    if cur == "AED":
        return amount / float(cfg.get(config, "fx.aed_per_usd", 3.6725))
    if cur == "ILS":
        return amount / float(cfg.get(config, "fx.ils_per_usd", 3.7))
    return amount  # unknown currency: assume already USD


def from_manual(config):
    data = json.loads(MANUAL.read_text()) if MANUAL.exists() else {}
    # Monthly spend typed in Settings (config.pnl.meta.manual) wins over the legacy file.
    data.update({str(k): v for k, v in (cfg.get(config, "pnl.meta.manual", {}) or {}).items()})
    cur = cfg.get(config, "pnl.meta.spend_currency", "USD")
    by_month = {m: round(_to_usd(float(v), cur, config), 2) for m, v in data.items()}
    return _pack(sum(by_month.values()), by_month, "manual (Settings)")


def from_api(config):
    acct = os.environ.get("META_AD_ACCOUNT_ID", "")
    token = os.environ.get("META_ACCESS_TOKEN", "")
    if not acct or not token:
        return {"error": "api mode needs META_AD_ACCOUNT_ID and META_ACCESS_TOKEN in .env."}
    if not acct.startswith("act_"):
        acct = "act_" + acct
    # Optional: count only ONE campaign (these customers came from a single campaign).
    camp_filter = cfg.get(config, "pnl.meta.campaign_filter")
    level = "campaign" if camp_filter else "account"
    fields = "spend,account_currency" + (",campaign_name" if camp_filter else "")
    q = parse.urlencode({
        "fields": fields,
        "time_increment": "monthly",
        "date_preset": "maximum",
        "level": level,
        "access_token": token,
    })
    try:
        with request.urlopen(f"{GRAPH}/{acct}/insights?{q}", timeout=40) as r:
            data = json.loads(r.read().decode())
    except error.HTTPError as e:
        return {"error": f"Meta API error {e.code}: {e.read().decode()[:200]}"}
    except (error.URLError, ValueError) as e:
        return {"error": f"Meta API call failed: {e}"}

    by_month = defaultdict(float)
    for row in data.get("data", []):
        if camp_filter and camp_filter not in (row.get("campaign_name") or ""):
            continue
        month = (row.get("date_start") or "")[:7] or "unknown"
        spend = float(row.get("spend") or 0)
        by_month[month] += _to_usd(spend, row.get("account_currency"), config)
    src = f"api:{acct}" + (f" · campaign~“{camp_filter}”" if camp_filter else "")
    return _pack(sum(by_month.values()),
                 {k: round(v, 2) for k, v in sorted(by_month.items())}, src)


def _pack(total, by_month, source):
    return {
        "total_usd": round(total, 2),
        "by_month": dict(sorted(by_month.items())),
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def pull(config=None):
    config = config or cfg.load()
    mode = cfg.get(config, "pnl.meta.mode", "manual")
    return from_api(config) if mode == "api" else from_manual(config)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return sys.exit(_selftest())
    agg = pull()
    if agg.get("error"):
        print(agg["error"])
        return
    print(f"Meta spend: ${agg['total_usd']:.2f}  [source: {agg['source']}]")
    print("By month:", json.dumps(agg["by_month"]))
    if not args.dry_run:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(agg, ensure_ascii=False, indent=2))
        print(f"Wrote {CACHE.name}.")
    else:
        print("(DRY RUN — cache not written)")


def _selftest():
    cfgd = {"fx": {"ils_per_usd": 4.0, "aed_per_usd": 3.6725},
            "pnl": {"meta": {"spend_currency": "ILS"}}}
    # 400 ILS @ 4.0 -> $100 ; 800 ILS -> $200
    global MANUAL
    import tempfile
    tf = Path(tempfile.gettempdir()) / "_meta_selftest.json"
    tf.write_text(json.dumps({"2026-05": 400, "2026-06": 800}))
    orig = MANUAL
    MANUAL = tf
    try:
        agg = from_manual(cfgd)
    finally:
        MANUAL = orig
        tf.unlink(missing_ok=True)
    ok = agg["total_usd"] == 300.0 and agg["by_month"]["2026-06"] == 200.0
    print("manual+fx:", "OK" if ok else f"XX {agg}")
    return 0 if ok else 1


if __name__ == "__main__":
    main()
