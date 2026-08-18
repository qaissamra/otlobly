#!/usr/bin/env python3
"""Seed forecast.HISTORY_FILE — the optional read-only extra corpus the 🔮
Forecast model merges on top of the live tracking cache.

The live cache only holds what THIS deploy has fetched. Far more GAASH history
already exists on the owner's Mac:

  · ~/OtloblyBackups/*.zip     — nightly off-site backups, each carrying a
                                 tracking_cache.json snapshot (backup_pull.py)
  · ~/gaash-clickup-sync/reports/cache.json — the GAASH Mac tool's own per-parcel
                                 timelines (`timeline` key, same event shape)

Both are read-only inputs. This script NEVER touches tracking_cache.json — the
app's real cache is left exactly as it is; the output is a separate file that
only ever ADDS parcels/events the live cache hasn't seen.

    python3 seed_forecast_history.py            # write forecast_history.json
    python3 seed_forecast_history.py --dry-run  # just report what it would write
"""
import glob
import json
import os
import sys
import zipfile

import forecast

BACKUPS = os.path.expanduser("~/OtloblyBackups/otlobly-backup-*.zip")
GAASH_TOOL = os.path.expanduser("~/gaash-clickup-sync/reports/cache.json")


def _merge(into, gwd, events, source):
    """Keep the LONGEST timeline per GWD — snapshots only ever grow."""
    gwd = str(gwd or "").strip().upper()
    if not gwd or not events:
        return
    cur = into.get(gwd) or {}
    if len(events) > len(cur.get("events") or []):
        into[gwd] = {"events": events, "source": source,
                     "fetched_at": cur.get("fetched_at") or ""}


def collect():
    out, stats = {}, {"backups": 0, "backup_entries": 0, "tool": 0, "live": 0}

    for z in sorted(glob.glob(BACKUPS)):
        try:
            with zipfile.ZipFile(z) as zf:
                hit = [n for n in zf.namelist() if n.endswith("tracking_cache.json")]
                if not hit:
                    continue
                snap = json.loads(zf.read(hit[0]))
        except Exception as e:  # noqa - a truncated backup must not stop the sweep
            print("  skip %s (%s)" % (os.path.basename(z), e))
            continue
        stats["backups"] += 1
        for gwd, ent in (snap or {}).items():
            if isinstance(ent, dict):
                stats["backup_entries"] += 1
                _merge(out, gwd, ent.get("events"), "backup")

    try:
        with open(GAASH_TOOL) as f:
            tool = json.load(f)
        for tn, v in (tool or {}).items():
            if not isinstance(v, dict):
                continue
            # the Mac tool stores {code, sub, text, time}; drop `sub`
            evs = [{"code": e.get("code"), "text": e.get("text"), "time": e.get("time")}
                   for e in (v.get("timeline") or []) if isinstance(e, dict)]
            if evs:
                stats["tool"] += 1
                _merge(out, tn, evs, "gaash-tool")
    except FileNotFoundError:
        print("  (no GAASH Mac tool cache at %s — skipped)" % GAASH_TOOL)
    except Exception as e:  # noqa
        print("  (GAASH Mac tool cache unreadable: %s)" % e)

    # the live cache is the freshest source; it wins on length like any other
    import tracking
    for gwd, ent in (tracking._load_cache() or {}).items():
        if isinstance(ent, dict):
            stats["live"] += 1
            _merge(out, gwd, ent.get("events"), "live")

    return out, stats


def main():
    dry = "--dry-run" in sys.argv
    print("Collecting GAASH history…")
    hist, stats = collect()
    print("  %d backup zips (%d snapshot entries) · %d Mac-tool parcels · %d live"
          % (stats["backups"], stats["backup_entries"], stats["tool"], stats["live"]))
    print("  → %d distinct parcels, %d events"
          % (len(hist), sum(len(v["events"]) for v in hist.values())))

    m = forecast.build_model(hist)
    print("\nModel from this corpus: n=%d transitions · %d sweeps · ready=%s"
          % (m["n"], m["sweeps"], m["ready"]))
    if m["n"]:
        print("  working-day gap: p25 %.2f · median %.2f · p90 %.2f"
              % (m["p25"], m["p50"], m["p90"]))
        for a in m["next"][:5]:
            print("   %5.1f%%  n=%-4d %s" % (100 * a["p"], a["n"], a["label"]))

    if dry:
        print("\n--dry-run: nothing written.")
        return 0
    tmp = forecast.HISTORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(hist, f, ensure_ascii=False)
    os.replace(tmp, forecast.HISTORY_FILE)
    print("\nWrote %s" % forecast.HISTORY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
