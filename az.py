#!/usr/bin/env python3
"""
Bridge to the AZ tool (multilogin-claude-code) so the Purchases page can act on
the Multilogin profile behind a box (B19, B22…):

  * profile_info(box) — last IP, fraud score, proxy country, running?, run budget
  * check_ip(box)     — live exit-IP check (proxied through the AZ app, no browser)
  * launch(box)       — start the profile's browser and leave it open (manual use)

Reads from the AZ Flask app on :8765 (which manages Multilogin auth) and, for the
launch, talks straight to the local Multilogin agent on :45000 with the AZ token —
so nothing in the AZ tool needs changing or restarting. Boxes map to profiles by
exact NAME (the Multilogin profile is literally named "B19", etc.).

This only works on the local machine where the AZ tool + Multilogin agent run.
"""

import json
import time
from pathlib import Path
from urllib import request, error

AZ_APP = "http://127.0.0.1:8765"          # the AZ tool's Flask app
AGENT = "http://127.0.0.1:45000"          # local Multilogin agent
AZ_DIR = Path(__file__).resolve().parent.parent / "multilogin-claude-code"
TOKEN_FILE = AZ_DIR / ".token"

_cache = {"profiles": None, "ts": 0}


def _get(url, timeout=20):
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, body, timeout=40, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = request.Request(url, data=data, headers=h, method="POST")
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def healthy(timeout=2):
    """Quick liveness probe of the AZ tool — cheap gate before the heavy
    /api/all_profiles fetch (which enriches 250+ profiles)."""
    try:
        return bool(_get(f"{AZ_APP}/api/health", timeout=timeout).get("ok"))
    except Exception:  # noqa: BLE001 - down/refused/timeout → just not healthy
        return False


def all_profiles(force=False):
    """All AZ profiles (cached 60s). {} on failure."""
    if not force and _cache["profiles"] is not None and time.time() - _cache["ts"] < 60:
        return _cache["profiles"]
    rows = _get(f"{AZ_APP}/api/all_profiles", timeout=25)
    _cache["profiles"] = rows
    _cache["ts"] = time.time()
    return rows


def bust_cache():
    """Drop the cached profile list so the next read re-fetches from the AZ tool."""
    _cache["profiles"] = None
    _cache["ts"] = 0


def find_box(box, force=False):
    """Match a box code (B19) to its Multilogin profile by exact name."""
    box = (box or "").strip()
    for p in all_profiles(force=force):
        if (p.get("name") or "").strip() == box:
            return p
    return None


def profile_info(box, force=False):
    try:
        p = find_box(box, force=force)
    except (error.URLError, ValueError, OSError) as e:
        return {"error": f"AZ tool not reachable on :8765 — is it running? ({e})"}
    if not p:
        return {"error": f"No Multilogin profile named “{box}”."}
    return {
        "box": box, "profile_id": p.get("profile_id"), "folder_id": p.get("folder_id"),
        "folder": p.get("folder"), "last_ip": p.get("last_ip"),
        "fraud": p.get("last_fraud"), "country": p.get("proxy_country"),
        "running": p.get("running"), "last_seen": p.get("last_seen"),
        "runs_24h": p.get("runs_24h"), "max_runs": p.get("max_runs"),
        "cooldown": p.get("cooldown"),
        "proxy": f"{p.get('proxy_host','')}:{p.get('proxy_port','')}".strip(":"),
    }


def check_ip(box):
    """Live exit-IP check through the profile's proxy (AZ app does the work)."""
    try:
        p = find_box(box)
        if not p:
            return {"ok": False, "error": f"No profile named “{box}”."}
        return _post(f"{AZ_APP}/api/ip_list/check_proxy",
                     {"profile_id": p["profile_id"], "folder_id": p["folder_id"]})
    except (error.URLError, ValueError, OSError) as e:
        return {"ok": False, "error": f"AZ tool not reachable ({e})"}


def _agent_get(path, timeout=120):
    """GET the local Multilogin agent with the AZ bearer token."""
    if not TOKEN_FILE.exists():
        raise RuntimeError("AZ token not found — open the AZ tool first.")
    token = TOKEN_FILE.read_text().strip()
    req = request.Request(f"{AGENT}{path}", headers={"Authorization": f"Bearer {token}"})
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def launch(box):
    """Start the profile's browser via the local Multilogin agent and leave it open."""
    try:
        p = find_box(box)
    except (error.URLError, ValueError, OSError) as e:
        return {"ok": False, "error": f"AZ tool not reachable ({e})"}
    if not p:
        return {"ok": False, "error": f"No profile named “{box}”."}
    try:
        data = _agent_get(f"/api/v2/profile/f/{p['folder_id']}/p/{p['profile_id']}"
                          f"/start?automation_type=selenium")
    except error.HTTPError as e:
        return {"ok": False, "error": f"agent {e.code} — is the Multilogin agent running?"}
    except (error.URLError, ValueError, OSError, RuntimeError) as e:
        return {"ok": False, "error": f"Multilogin agent not reachable on :45000 ({e})"}
    if data.get("status", {}).get("http_code") != 200:
        return {"ok": False, "error": f"start failed: {data.get('status')}"}
    return {"ok": True, "port": data.get("data", {}).get("port"), "box": box}


def stop(box):
    """Stop (close) the profile's running browser via the local Multilogin agent."""
    try:
        p = find_box(box)
    except (error.URLError, ValueError, OSError) as e:
        return {"ok": False, "error": f"AZ tool not reachable ({e})"}
    if not p:
        return {"ok": False, "error": f"No profile named “{box}”."}
    try:
        _agent_get(f"/api/v1/profile/stop/p/{p['profile_id']}", timeout=60)
    except (error.URLError, error.HTTPError, ValueError, OSError, RuntimeError) as e:
        return {"ok": False, "error": f"agent not reachable ({e})"}
    return {"ok": True, "box": box}


def rotate_start(box, target_risk=25, max_tries=8):
    """Ask the AZ tool to rotate the profile's IP — try fresh proxy sessions until
    the fraud score is ≤ target_risk (or max_tries), then commit the best. Async:
    returns {job_id}; poll rotate_status. Same engine the AZ tool uses."""
    try:
        p = find_box(box)
        if not p:
            return {"error": f"No profile named “{box}”."}
        return _post(f"{AZ_APP}/api/rotate/start", {
            "profiles": [{"id": p["profile_id"], "folder_id": p["folder_id"], "name": box}],
            "target_risk": int(target_risk), "max_tries": int(max_tries)})
    except (error.URLError, ValueError, OSError) as e:
        return {"error": f"AZ tool not reachable ({e})"}


def rotate_status(job_id):
    try:
        job = _get(f"{AZ_APP}/api/rotate/status?job_id={job_id}")
        # A finished rotation has written a new IP+fraud into the AZ tool's
        # history; drop our 60s cache so the next profile_info reflects it.
        if job.get("status") == "done":
            bust_cache()
        return job
    except (error.URLError, ValueError, OSError) as e:
        return {"status": "unknown", "error": str(e)}


def track_fetch_start(box, items, max_items=12):
    """Ask the AZ tool to open the box's profile and scrape Amazon order tracking
    numbers for `items` ([{item_id, asin, title}]). Async — returns {job_id};
    poll track_fetch_status. The AZ tool gates on IP + automation-leak (fail closed)
    and never signs in/checks out. Same engine the AZ tool's tester uses."""
    try:
        p = find_box(box)
        if not p:
            return {"error": f"No Multilogin profile named “{box}”."}
        clean = [{"item_id": it.get("item_id"), "asin": (it.get("asin") or "").strip(),
                  "title": it.get("title") or ""}
                 for it in (items or []) if it.get("asin") or it.get("title")]
        if not clean:
            return {"error": "No products with an ASIN or title to look up."}
        return _post(f"{AZ_APP}/api/track_fetch/start", {
            "folder_id": p["folder_id"], "profile_id": p["profile_id"],
            "name": box, "items": clean, "max_items": int(max_items)})
    except (error.URLError, ValueError, OSError) as e:
        return {"error": f"AZ tool not reachable on :8765 ({e})"}


def track_fetch_status(job_id):
    try:
        return _get(f"{AZ_APP}/api/track_fetch/status?job_id={job_id}")
    except (error.URLError, ValueError, OSError) as e:
        return {"status": "unknown", "error": str(e)}


if __name__ == "__main__":
    import sys
    box = sys.argv[1] if len(sys.argv) > 1 else "B19"
    print("info:", json.dumps(profile_info(box), ensure_ascii=False, indent=2))
