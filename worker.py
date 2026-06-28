#!/usr/bin/env python3
"""
Local automation worker — runs on the Mac where Multilogin lives.

The hosted app (cloud) can't drive the local Multilogin agent, so this worker
bridges them: it polls the hosted app for orders that are PAID and not yet placed,
runs the existing `order_placer` against the local anti-detection browser, and
posts the result (landed checkout total + status) back to the app.

  export OTLOBLY_API_URL=https://your-app.up.railway.app
  export OTLOBLY_WORKER_TOKEN=<same token set on the app>
  python3 worker.py --dry-run        # poll + print the queue, place nothing
  python3 worker.py                  # price each order at the review page (NO buy)
  python3 worker.py --place          # actually place orders
  python3 worker.py --loop 120       # keep polling every 120s

Safety mirrors order_placer: health-gated launch, and **stop-before-pay** unless
--place is given.
"""

import argparse
import json
import os
import sys
import time
from urllib import request, error

import cfg
import order_placer

URL = (os.environ.get("OTLOBLY_API_URL") or "http://localhost:8789").rstrip("/")
TOKEN = os.environ.get("OTLOBLY_WORKER_TOKEN", "")


def _api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(URL + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "null")


def fetch_queue():
    return _api("/api/worker/queue").get("orders", [])


def process(order, config, do_place):
    box = order_placer.assign_profile({"orders": []}, config,
                                      prefer=order.get("profile_box"))
    if not box:
        print(f"  {order['order_id']}: no profile capacity — skip")
        return
    driver, stop = order_placer.gated_driver(box, config)
    if driver is None:
        print(f"  {order['order_id']}: {stop}")          # stop holds the reason
        return
    try:
        wait_for_content = order_placer._mlx_imports()[5]
        checkout, placed = order_placer.place_one(driver, order, wait_for_content, do_place)
    finally:
        try:
            stop()
        except Exception:
            pass
    amt = order_placer.pricing.price_from_checkout(checkout, config)
    _api("/api/worker/result", "POST", {
        "id": order["order_id"], "checkout": checkout,
        "amount_to_collect_usd": amt, "profile_box": box,
        "status": "ORDERED" if placed else "PAID"})
    print(f"  {order['order_id']} box {box} → total {checkout.get('order_total_usd')} "
          f"=> collect ${amt}  {'PLACED' if placed else '(review only)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Poll + print the queue; launch nothing.")
    ap.add_argument("--place", action="store_true",
                    help="Actually click 'Place order' (default: stop at review).")
    ap.add_argument("--loop", type=int, default=0, help="Poll every N seconds.")
    args = ap.parse_args()
    if not TOKEN:
        sys.exit("Set OTLOBLY_WORKER_TOKEN (and OTLOBLY_API_URL) first.")
    config = cfg.load()
    while True:
        try:
            q = fetch_queue()
        except error.HTTPError as e:
            print(f"queue error {e.code}: {e.read().decode()[:120]}")
            q = []
        except Exception as e:  # noqa
            print(f"queue error: {e}")
            q = []
        print(f"{time.strftime('%H:%M:%S')} — {len(q)} order(s) ready to place "
              f"({'WILL BUY' if args.place else 'price only'}).")
        if args.dry_run:
            for o in q:
                print(f"  would place {o['order_id']} (box {o.get('profile_box') or '—'}, "
                      f"{len(o['items'])} item(s))")
        else:
            for o in q:
                try:
                    process(o, config, args.place)
                except Exception as e:  # noqa
                    print(f"  {o['order_id']} error: {e}")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
