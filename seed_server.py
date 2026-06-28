#!/usr/bin/env python3
"""
One-time: push this Mac's local orders / customers / purchases UP to the hosted
Otlobly server, so the live site shows your real data.

Sends over HTTPS to the server's bearer-token-protected /api/worker/seed endpoint
(the same token the Mac worker uses) — your customer data goes straight Mac→server,
never through GitHub.

  export OTLOBLY_API_URL=https://otlobly.onrender.com
  export OTLOBLY_WORKER_TOKEN=<the token from Render → your service → Environment>
  python3 seed_server.py
"""
import json
import os
import sys
from urllib import request, error

import store
import customers as cust
import purchases as pur

URL = (os.environ.get("OTLOBLY_API_URL") or "").rstrip("/")
TOKEN = os.environ.get("OTLOBLY_WORKER_TOKEN", "")
if not URL or not TOKEN:
    sys.exit("Set OTLOBLY_API_URL and OTLOBLY_WORKER_TOKEN first (see the header).")

payload = {
    "customers": cust.load().get("customers", []),
    "orders": store.load().get("orders", []),
    "purchases": pur.load().get("purchase_orders", []),
}
print(f"sending {len(payload['orders'])} orders, {len(payload['customers'])} customers, "
      f"{len(payload['purchases'])} purchase orders → {URL}")

req = request.Request(URL + "/api/worker/seed", data=json.dumps(payload).encode(),
                      method="POST", headers={"Authorization": f"Bearer {TOKEN}",
                                              "Content-Type": "application/json"})
try:
    with request.urlopen(req, timeout=120) as r:
        print("server replied:", r.read().decode())
except error.HTTPError as e:
    print("HTTP", e.code, "-", e.read().decode()[:300])
    sys.exit(1)
except error.URLError as e:
    print("could not reach the server:", e)
    sys.exit(1)
