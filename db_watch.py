#!/usr/bin/env python3
"""
db_watch.py — tell the owner when the live database rots, in minutes not hours.

WHY THIS EXISTS. On 2026-09-04 the live SQLite file corrupted at 12:17 UTC. The app
kept running and failing its queries quietly; `/healthz` is deliberately always-200,
so it stayed green the whole time. Nobody knew until 15:02, when a deploy restarted
the process and it could no longer boot at all. Three hours of a broken shop, and the
only trace was a line in the Render log nobody was reading.

🛑 IT MUST NOT RUN ON RENDER. Everything inside the app dies with the app — that is
precisely the failure being watched for. alerts.py is in-process and therefore the
wrong host for this. This is a standalone poller, shaped like backup_pull.py, run
from launchd on the Mac (and better still from the droplet, which never sleeps).

    python3 db_watch.py                # one check, exits 0 healthy / 1 not
    python3 db_watch.py --quiet-ok     # same, but stay silent when healthy
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, request

HERE = Path(__file__).resolve().parent
STATE = HERE / "db_watch.state"
# One alert, then silence for this long, so an outage that lasts all afternoon does
# not become 30 identical Telegram messages the owner learns to swipe away.
REPEAT_AFTER_S = 3 * 3600


def load_env():
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def notify(text):
    """Best effort: a Telegram problem must never make the watchdog itself fail."""
    try:
        sys.path.insert(0, str(HERE))
        import telegram
        r = telegram.send(text)
        if not r.get("ok"):
            print(f"(telegram skipped: {r.get('error')})")
    except Exception as e:                       # noqa: BLE001
        print(f"(telegram skipped: {e})")


def _get(url, timeout=25):
    try:
        with request.urlopen(request.Request(url), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                       # noqa: BLE001
        return 0, str(e)


def check(base):
    """(healthy, one-line reason). Distinguishes 'database is rotten' from 'the whole
    site is down' — they need different reactions, so they must not read alike."""
    code, body = _get(base.rstrip("/") + "/api/health/db")
    if code == 0:
        return False, f"cannot reach {base} — {body[:120]}"
    if code == 404:
        # An older build without the probe. Say so rather than reporting health.
        return True, "probe not deployed on this version"
    try:
        d = json.loads(body or "{}")
    except ValueError:
        return False, f"unreadable answer from /api/health/db (HTTP {code})"
    if d.get("ok"):
        return True, "database reads clean"
    return False, f"DATABASE CORRUPT — {d.get('error') or 'quick_check failed'}"


def _last_alert():
    try:
        return float(json.loads(STATE.read_text()).get("last_alert") or 0)
    except (OSError, ValueError, AttributeError):
        return 0.0


def _stamp(ts):
    try:
        STATE.write_text(json.dumps({"last_alert": ts}))
    except OSError:
        pass


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Alert when the live database is corrupt.")
    ap.add_argument("--base", default=os.environ.get("OTLOBLY_BASE_URL",
                                                     "https://otlobly.co"))
    ap.add_argument("--quiet-ok", action="store_true",
                    help="print nothing when healthy (for a chatty timer)")
    args = ap.parse_args()

    healthy, why = check(args.base)
    if healthy:
        if not args.quiet_ok:
            print(f"ok · {why}")
        if _last_alert():
            notify(f"✅ Otlobly database is healthy again · قاعدة البيانات رجعت سليمة\n{args.base}")
            _stamp(0)
        return 0

    print(f"UNHEALTHY · {why}", file=sys.stderr)
    now = time.time()
    if now - _last_alert() > REPEAT_AFTER_S:
        notify("🚨 Otlobly DATABASE PROBLEM · مشكلة في قاعدة البيانات\n"
               f"{why}\n{args.base}\n\n"
               "The site may still look up. Restore the newest backup:\n"
               "  curl -X POST -H \"Authorization: Bearer $OTLOBLY_WORKER_TOKEN\" \\\n"
               "    --data-binary @<newest ~/OtloblyBackups zip> \\\n"
               f"    {args.base.rstrip('/')}/api/restore")
        _stamp(now)
    return 1


if __name__ == "__main__":
    sys.exit(main())
