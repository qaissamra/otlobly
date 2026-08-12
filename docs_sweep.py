#!/usr/bin/env python3
"""
Keep the 📄 GAASH docs queue fresh on the LIVE app (nightly).

Asks GAASH's parcel-status page, per open parcel, whether documents are being
requested — the yellow "upload asked" vs blue "in customs" state the 📄 Docs tab
(GAASH mail page) and the 🔔 bell read. Nothing else fills it on a schedule: the
board sweeps only reach 3 parcels per page refresh, and the tab's "Check all"
needs a human in a browser.

  python3 docs_sweep.py                                 # nightly job: re-check everything
  python3 docs_sweep.py --no-refresh                     # only parcels never checked
  python3 docs_sweep.py --base http://localhost:8793     # test against a local server
  python3 docs_sweep.py --gwd GWD004776509 --gwd GWD...  # just these

Scheduled daily by com.otlobly.docssweep.plist (launchd) → logs to docs_sweep.log.
Auth: OTLOBLY_WORKER_TOKEN from .env (the same token backup_pull.py uses).
Overrides: OTLOBLY_BASE_URL (default https://otlobly.co).

WHY IT LOOPS: GAASH's status page takes 10-25s per parcel, so the server checks
at most 3 per request (a bigger batch would exceed gunicorn's 120s timeout and
lose the whole request). Each reply carries `remaining`; we call again until it
is 0. A full pass over ~30 parcels is a handful of minutes.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import request, error

HERE = Path(__file__).resolve().parent
MAX_ROUNDS = 60          # backstop: 60 × 3 = 180 parcels, far above any real queue
GAP_SEC = 2.0            # breathing room between requests — GAASH is a live site


def load_env():
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def sweep(base, tok, body, timeout=180):
    req = request.Request(base.rstrip("/") + "/api/worker/docs_sweep",
                          data=json.dumps(body).encode(),
                          headers={"Authorization": f"Bearer {tok}",
                                   "Content-Type": "application/json"},
                          method="POST")
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "null")


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Refresh the GAASH docs queue.")
    ap.add_argument("--base", default=os.environ.get("OTLOBLY_BASE_URL",
                                                    "https://otlobly.co"))
    ap.add_argument("--no-refresh", action="store_true",
                    help="only parcels never checked (default re-checks stale ones)")
    ap.add_argument("--max-age-hours", type=int, default=20,
                    help="with refresh: re-check answers older than this (default 20)")
    ap.add_argument("--gwd", action="append", default=[],
                    help="check only these parcels (repeatable)")
    args = ap.parse_args()

    tok = os.environ.get("OTLOBLY_WORKER_TOKEN")
    if not tok:
        sys.exit("No OTLOBLY_WORKER_TOKEN in .env — copy it from Render → otlobly → Environment.")

    log(f"=== docs sweep → {args.base}"
        + (f" · only {len(args.gwd)} parcel(s)" if args.gwd else
           (" · never-checked only" if args.no_refresh else
            f" · re-checking answers older than {args.max_age_hours}h")))

    body = {"batch": 3}
    if args.gwd:
        body["gwds"] = args.gwd
    elif not args.no_refresh:
        body.update(refresh=True, max_age_hours=args.max_age_hours)

    checked, states, counts, fails = 0, {}, {}, 0
    for rnd in range(1, MAX_ROUNDS + 1):
        try:
            d = sweep(args.base, tok, body)
        except error.HTTPError as e:
            sys.exit(f"HTTP {e.code} from the sweep endpoint — "
                     + ("token rejected (check OTLOBLY_WORKER_TOKEN)" if e.code == 401
                        else e.read().decode()[:200]))
        except Exception as e:  # noqa: BLE001 — one bad round must not kill the night
            fails += 1
            log(f"round {rnd}: request failed ({e}) — {'giving up' if fails >= 3 else 'retrying'}")
            if fails >= 3:
                sys.exit("three consecutive failures — aborting")
            time.sleep(10)
            continue
        fails = 0
        if not d or not d.get("ok"):
            sys.exit(f"unexpected reply: {str(d)[:200]}")
        checked += d.get("checked") or 0
        counts = d.get("counts") or counts
        for r in d.get("results") or []:
            states[r.get("state") or "?"] = states.get(r.get("state") or "?", 0) + 1
        rem = d.get("remaining") or 0
        log(f"round {rnd}: checked {d.get('checked')} · {rem} left")
        if rem <= 0:
            break
        time.sleep(GAP_SEC)
    else:
        log(f"stopped at the {MAX_ROUNDS}-round backstop — queue may still have parcels left")

    # counts come from the queue as it was BEFORE this round's writes, so re-read
    # it once (a no-op call: an impossible GWD checks nothing) for a true summary
    try:
        counts = (sweep(args.base, tok, {"gwds": ["__none__"]}, timeout=60)
                  or {}).get("counts") or counts
    except Exception:  # noqa: BLE001 — the summary is nice-to-have
        pass
    log(f"done · {checked} parcel(s) checked · this run: "
        + (", ".join(f"{k} {v}" for k, v in sorted(states.items())) or "nothing")
        + f" · queue now: 🟡 {counts.get('action', '?')} action, "
        f"⛔ {counts.get('stopped', '?')} stopped, 🚫 {counts.get('noanswer', '?')} no-answer, "
        f"🔵 {counts.get('watching', '?')} in customs, ❔ {counts.get('unchecked', '?')} unchecked")


if __name__ == "__main__":
    main()
