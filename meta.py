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
    # Always campaign-level so the P&L drill-down can show every campaign and the
    # owner can exclude old ones — the filter is applied at READ time
    # (apply_exclusions), so toggling never needs a re-pull.
    q = parse.urlencode({
        "fields": "campaign_id,campaign_name,spend,account_currency",
        "time_increment": "monthly",
        "date_preset": "maximum",
        "level": "campaign",
        "limit": 500,
        "access_token": token,
    })
    url, rows = f"{GRAPH}/{acct}/insights?{q}", []
    try:
        while url:   # level=campaign paginates — follow paging.next to the end
            with request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read().decode())
            rows += data.get("data", [])
            url = (data.get("paging") or {}).get("next")
    except error.HTTPError as e:
        return {"error": f"Meta API error {e.code}: {e.read().decode()[:200]}"}
    except (error.URLError, ValueError) as e:
        return {"error": f"Meta API call failed: {e}"}

    by_month, camps = defaultdict(float), {}
    for row in rows:
        month = (row.get("date_start") or "")[:7] or "unknown"
        usd = _to_usd(float(row.get("spend") or 0), row.get("account_currency"), config)
        cid = str(row.get("campaign_id") or "")
        c = camps.setdefault(cid, {"id": cid, "name": row.get("campaign_name") or cid,
                                   "by_month": defaultdict(float)})
        c["by_month"][month] += usd
        by_month[month] += usd   # RAW all-campaign totals; exclusions happen at read time
    campaigns = []
    for c in camps.values():
        bm = {k: round(v, 2) for k, v in sorted(c["by_month"].items())}
        campaigns.append({"id": c["id"], "name": c["name"], "by_month": bm,
                          "total_usd": round(sum(bm.values()), 2)})
    campaigns.sort(key=lambda c: -c["total_usd"])
    return _pack(sum(by_month.values()),
                 {k: round(v, 2) for k, v in sorted(by_month.items())},
                 f"api:{acct} · {len(campaigns)} campaign(s)", campaigns=campaigns)


def _pack(total, by_month, source, campaigns=None):
    out = {
        "total_usd": round(total, 2),
        "by_month": dict(sorted(by_month.items())),
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    if campaigns is not None:
        out["campaigns"] = campaigns
    return out


def apply_exclusions(cached, config=None):
    """Read-time campaign filter over the cached API spend. Returns
    (by_month_included, campaigns_annotated) where each campaign gains
    'excluded': bool. Defaults, in priority order:
      1. config pnl.meta.excluded_campaign_ids saved (even []) → authoritative;
      2. legacy pnl.meta.campaign_filter substring → only matching names count
         (identical numbers to the old pull-time filter — seeds the first UI open);
      3. neither → every campaign counts.
    A cache that predates per-campaign data → (its raw by_month, None)."""
    config = cfg.load() if config is None else config
    campaigns = (cached or {}).get("campaigns")
    if not campaigns:
        return dict((cached or {}).get("by_month") or {}), None
    excl = cfg.get(config, "pnl.meta.excluded_campaign_ids", None)
    excl_set = {str(x) for x in excl} if isinstance(excl, list) else None
    legacy = cfg.get(config, "pnl.meta.campaign_filter")
    annotated, by_month = [], defaultdict(float)
    for c in campaigns:
        if excl_set is not None:
            out = str(c.get("id")) in excl_set
        elif legacy:
            out = legacy not in (c.get("name") or "")
        else:
            out = False
        annotated.append({**c, "excluded": out})
        if not out:
            for m, v in (c.get("by_month") or {}).items():
                by_month[m] += v
    return {k: round(v, 2) for k, v in sorted(by_month.items())}, annotated


def has_api_creds():
    return bool(os.environ.get("META_AD_ACCOUNT_ID") and os.environ.get("META_ACCESS_TOKEN"))


def effective_mode(config=None):
    """API as soon as the two env creds exist (no hidden config flip needed).
    Without creds the API is impossible, so fall back to manual entry."""
    return "api" if has_api_creds() else "manual"


def pull(config=None):
    config = config or cfg.load()
    return from_api(config) if effective_mode(config) == "api" else from_manual(config)


def read_cache():
    try:
        return json.loads(CACHE.read_text()) if CACHE.exists() else None
    except (ValueError, OSError):
        return None


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sync_once(config=None):
    """Pull once and write the cache ATOMICALLY (temp + os.replace, safe across the
    two gunicorn workers). On error keep the last-good totals but record last_error."""
    config = config or cfg.load()
    agg = pull(config)
    prev = read_cache() or {}
    if agg.get("error"):
        out = {**prev, "last_error": agg["error"], "last_sync_at": _now()}
    else:
        out = {**agg, "last_error": None, "last_sync_at": _now()}
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        os.replace(tmp, CACHE)          # atomic
    except OSError as e:
        return {**out, "write_error": str(e)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return sys.exit(_selftest())
    if args.dry_run:
        agg = pull()
        print(agg.get("error") or f"Meta spend: ${agg['total_usd']:.2f}  [{agg['source']}] (DRY RUN)")
        return
    out = sync_once()
    if out.get("last_error"):
        print(f"⚠ sync error: {out['last_error']} (kept last-good cache)")
    else:
        print(f"Meta spend: ${out.get('total_usd',0):.2f}  [{out.get('source')}] · wrote {CACHE.name}")


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

    cached = {"by_month": {"2026-05": 999.0},   # raw — ignored when campaigns exist
              "campaigns": [
                  {"id": "1", "name": "اطلبلي — June", "by_month": {"2026-05": 100.0}, "total_usd": 100.0},
                  {"id": "2", "name": "Old promo", "by_month": {"2026-05": 50.0}, "total_usd": 50.0}]}
    # legacy filter seeds the defaults: only the matching campaign counts
    bm1, an1 = apply_exclusions(cached, {"pnl": {"meta": {"campaign_filter": "اطلبلي"}}})
    ok1 = bm1 == {"2026-05": 100.0} and [a["excluded"] for a in an1] == [False, True]
    # a saved exclusion list is authoritative (legacy filter ignored)
    bm2, an2 = apply_exclusions(cached, {"pnl": {"meta": {"excluded_campaign_ids": ["1"],
                                                          "campaign_filter": "اطلبلي"}}})
    ok2 = bm2 == {"2026-05": 50.0} and [a["excluded"] for a in an2] == [True, False]
    # no config at all → everything counts; stale cache → raw by_month passthrough
    bm3, _ = apply_exclusions(cached, {})
    bm4, an4 = apply_exclusions({"by_month": {"2026-05": 77.0}}, {})
    ok3 = bm3 == {"2026-05": 150.0} and bm4 == {"2026-05": 77.0} and an4 is None
    ok_x = ok1 and ok2 and ok3
    print("exclusions:", "OK" if ok_x else f"XX {bm1}/{bm2}/{bm3}/{bm4}")
    return 0 if (ok and ok_x) else 1


if __name__ == "__main__":
    main()
