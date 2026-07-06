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
    # Campaign-level + DAILY (time_increment=1) so the P&L can filter Meta by the
    # exact same day window as revenue/COGS (a "last 7 days" filter must move the
    # Meta number too). Campaign exclusions are applied at READ time
    # (apply_exclusions), so toggling never needs a re-pull.
    q = parse.urlencode({
        "fields": "campaign_id,campaign_name,spend,account_currency",
        "time_increment": 1,
        "date_preset": "maximum",
        "level": "campaign",
        "limit": 500,
        "access_token": token,
    })
    url, rows = f"{GRAPH}/{acct}/insights?{q}", []
    try:
        while url:   # daily campaign rows paginate — follow paging.next to the end
            with request.urlopen(url, timeout=40) as r:
                data = json.loads(r.read().decode())
            rows += data.get("data", [])
            url = (data.get("paging") or {}).get("next")
    except error.HTTPError as e:
        return {"error": f"Meta API error {e.code}: {e.read().decode()[:200]}"}
    except (error.URLError, ValueError) as e:
        return {"error": f"Meta API call failed: {e}"}

    by_day, camps = defaultdict(float), {}
    for row in rows:
        day = (row.get("date_start") or "")[:10] or "unknown"
        usd = _to_usd(float(row.get("spend") or 0), row.get("account_currency"), config)
        cid = str(row.get("campaign_id") or "")
        c = camps.setdefault(cid, {"id": cid, "name": row.get("campaign_name") or cid,
                                   "by_day": defaultdict(float)})
        c["by_day"][day] += usd
        by_day[day] += usd   # RAW all-campaign totals; exclusions happen at read time
    campaigns = []
    for c in camps.values():
        bd = {k: round(v, 2) for k, v in sorted(c["by_day"].items())}
        campaigns.append({"id": c["id"], "name": c["name"], "by_day": bd,
                          "by_month": _roll_months(bd), "total_usd": round(sum(bd.values()), 2)})
    campaigns.sort(key=lambda c: -c["total_usd"])
    by_day = {k: round(v, 2) for k, v in sorted(by_day.items())}
    return _pack(sum(by_day.values()), _roll_months(by_day),
                 f"api:{acct} · {len(campaigns)} campaign(s), daily",
                 campaigns=campaigns, by_day=by_day)


def _roll_months(by_day):
    """Roll a {YYYY-MM-DD: usd} dict up to {YYYY-MM: usd} for the by-month table."""
    out = defaultdict(float)
    for d, v in (by_day or {}).items():
        out[(d or "")[:7] or "unknown"] += v
    return {k: round(v, 2) for k, v in sorted(out.items())}


def _pack(total, by_month, source, campaigns=None, by_day=None):
    out = {
        "total_usd": round(total, 2),
        "by_month": dict(sorted(by_month.items())),
        "source": source,
        "pulled_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    if by_day is not None:
        out["by_day"] = by_day
    if campaigns is not None:
        out["campaigns"] = campaigns
    return out


def apply_exclusions(cached, config=None):
    """Read-time campaign filter over the cached API spend. Returns
    (by_month_included, by_day_included, campaigns_annotated) where each campaign
    gains 'excluded': bool. by_day_included is None for a legacy monthly-only cache
    (the caller then filters by month). Defaults, in priority order:
      1. config pnl.meta.excluded_campaign_ids saved (even []) → authoritative;
      2. legacy pnl.meta.campaign_filter substring → only matching names count
         (identical numbers to the old pull-time filter — seeds the first UI open);
      3. neither → every campaign counts.
    A cache that predates per-campaign data → (its raw by_month, its by_day|None, None)."""
    config = cfg.load() if config is None else config
    campaigns = (cached or {}).get("campaigns")
    if not campaigns:
        c = cached or {}
        return dict(c.get("by_month") or {}), (dict(c["by_day"]) if c.get("by_day") else None), None
    excl = cfg.get(config, "pnl.meta.excluded_campaign_ids", None)
    excl_set = {str(x) for x in excl} if isinstance(excl, list) else None
    legacy = cfg.get(config, "pnl.meta.campaign_filter")
    annotated, by_month, by_day, any_day = [], defaultdict(float), defaultdict(float), False
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
            if c.get("by_day"):
                any_day = True
                for d, v in c["by_day"].items():
                    by_day[d] += v
    bd = {k: round(v, 2) for k, v in sorted(by_day.items())} if any_day else None
    return {k: round(v, 2) for k, v in sorted(by_month.items())}, bd, annotated


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
                  {"id": "1", "name": "اطلبلي — June", "by_month": {"2026-05": 100.0},
                   "by_day": {"2026-05-10": 60.0, "2026-05-20": 40.0}, "total_usd": 100.0},
                  {"id": "2", "name": "Old promo", "by_month": {"2026-05": 50.0},
                   "by_day": {"2026-05-15": 50.0}, "total_usd": 50.0}]}
    # legacy filter seeds the defaults: only the matching campaign counts (day + month)
    bm1, bd1, an1 = apply_exclusions(cached, {"pnl": {"meta": {"campaign_filter": "اطلبلي"}}})
    ok1 = (bm1 == {"2026-05": 100.0} and bd1 == {"2026-05-10": 60.0, "2026-05-20": 40.0}
           and [a["excluded"] for a in an1] == [False, True])
    # a saved exclusion list is authoritative (legacy filter ignored)
    bm2, bd2, an2 = apply_exclusions(cached, {"pnl": {"meta": {"excluded_campaign_ids": ["1"],
                                                              "campaign_filter": "اطلبلي"}}})
    ok2 = bm2 == {"2026-05": 50.0} and bd2 == {"2026-05-15": 50.0} and [a["excluded"] for a in an2] == [True, False]
    # no config at all → everything counts; legacy monthly-only cache → by_day None
    bm3, bd3, _ = apply_exclusions(cached, {})
    bm4, bd4, an4 = apply_exclusions({"by_month": {"2026-05": 77.0}}, {})
    ok3 = (bm3 == {"2026-05": 150.0} and round(sum(bd3.values()), 2) == 150.0
           and bm4 == {"2026-05": 77.0} and bd4 is None and an4 is None)
    # roll-up helper
    ok4 = _roll_months({"2026-05-10": 60.0, "2026-05-20": 40.0, "2026-06-01": 10.0}) == {"2026-05": 100.0, "2026-06": 10.0}
    ok_x = ok1 and ok2 and ok3 and ok4
    print("exclusions:", "OK" if ok_x else f"XX {bm1}|{bd1}/{bm2}|{bd2}/{bm3}|{bd3}/{bm4}|{bd4}")
    return 0 if (ok and ok_x) else 1


if __name__ == "__main__":
    main()
