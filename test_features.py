#!/usr/bin/env python3
"""
Self-checks for per-tenant feature gating (Tatabu Phase 2).

Proves: business #1 (Otlobly) has every Otlobly-only tool on; a broker tenant has
them off by default (overridable); /api/me carries the flags; and the backend
feature-guard 403s a broker on an Otlobly-only endpoint while Otlobly still gets in.

    ./.venv/bin/python test_features.py
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-feat-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import features   # noqa: E402
import db          # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    db.init_db()

    # 1) Business #1 (Otlobly) = every Otlobly-only tool ON.
    o = features.resolve(1)
    check("biz#1 has all Otlobly tools on", all(o[f] for f in features.OTLOBLY_ONLY))
    check("biz#1 has(clickup) is True", features.has(1, "clickup"))

    # 2) A broker tenant = Otlobly-only tools OFF by default.
    bid = db.create_business("Broker Co")
    b = features.resolve(bid)
    check("broker has all Otlobly tools off", not any(b[f] for f in features.OTLOBLY_ONLY))
    check("broker has(multilogin) is False", not features.has(bid, "multilogin"))

    # 3) Override: a broker can be granted a specific tool.
    db.set_business_config(bid, "features", {"catalog": True})
    check("broker with catalog override → on", features.has(bid, "catalog"))
    check("broker still has clickup off", not features.has(bid, "clickup"))

    # 4) End-to-end: /api/me carries flags; the backend guard 403s a broker.
    import app as appmod
    import auth
    db.create_user("otlo", auth.hash_pw("secret1"), "admin", "Otlobly Admin", business_id=1)
    db.create_user("brk", auth.hash_pw("secret1"), "admin", "Broker Admin", business_id=bid)

    def client(u):
        c = appmod.app.test_client()
        c.post("/login", data={"username": u, "password": "secret1"})
        return c

    co, cb = client("otlo"), client("brk")

    fo = co.get("/api/me").get_json()["business"]["features"]
    fb = cb.get("/api/me").get_json()["business"]["features"]
    check("/api/me: Otlobly clickup=True", fo.get("clickup") is True)
    check("/api/me: broker clickup=False", fb.get("clickup") is False)

    # /api/clickup is Otlobly-only (require_feature('clickup')).
    check("Otlobly can reach /api/clickup (not 403)", co.post("/api/clickup").status_code != 403)
    check("Broker is 403'd on /api/clickup", cb.post("/api/clickup").status_code == 403)
    # /api/az/ip is Multilogin-only.
    check("Broker is 403'd on /api/az/ip", cb.post("/api/az/ip", json={"box": "B19"}).status_code == 403)

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
