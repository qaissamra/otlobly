#!/usr/bin/env python3
"""Leluxe — the Amazon-bulk side business (watches/wallets, RD workflow).

Local store + live mirror of one ClickUp list (prod: 'AZ (2)', 901520351506).
The partner reviews everything in ClickUp, so the mirror keeps HIS structure:
one parent task per Amazon order + one subtask per product, the list's own
statuses, every custom field, tags and image attachments.

Flow:
  Import / Pull  = ClickUp → SQLite upsert (local unpushed edits always win).
  Every local save marks the row dirty; a background pusher mirrors it back
  (create parent → subtasks → status/fields/tags → attachments) with retry.

Schema (statuses + custom-field ids/options) is discovered from the list and
cached in config.json under `leluxe.schema` — re-run Discover after changing
fields/options in ClickUp. Token: CLICKUP_API_TOKEN env (same as clickup.py).
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest, error as urlerror
from urllib.parse import quote as urlquote

import cfg
import db
import memlog
from paths import data_path, write_json_atomic

CLICKUP_API = "https://api.clickup.com/api/v2"
IMAGE_DIR = data_path("leluxe_images")

# Local plain-value shape kept in data_json:
#   description  markdown (ClickUp-side image embeds included)
#   tags         ["tag", ...]
#   fields       {"<field name>": plain value}   (dropdown -> option NAME,
#                labels -> [names], date -> ms-epoch string, checkbox -> bool)
#   images       [{"file": local-name | None, "url": remote | None,
#                  "uploaded": bool}]
#   pushed       last state confirmed in ClickUp (per-field change skipping)
#   cu_updated   ClickUp date_updated at last pull (image re-scan trigger)


def _token():
    return os.environ.get("CLICKUP_API_TOKEN", "")


def _pace():
    try:
        return float(os.environ.get("LELUXE_PACE", "0.7"))
    except ValueError:
        return 0.7


def push_disabled():
    return os.environ.get("LELUXE_PUSH_DISABLED", "") not in ("", "0")


# Tiers: an order task → package subtasks → product sub-subtasks (3 levels of
# ClickUp nesting). Legacy v1 rows used 'parent' for the top tier — treat it as
# an order so old data still renders. Push order = shallow before deep.
TOP_KINDS = ("order", "parent")
KIND_RANK = {"order": 0, "parent": 0, "package": 1, "item": 2}

# The ClickUp status 📦⤴ Organize stamps on every parcel container. It says what
# a task IS, not where its parcel got to — see _structural_statuses.
PKG_STATUS = "package"


# --------------------------------------------------------------------------- #
# HTTP (clickup.py style, but token read per-call and one 429 retry)
# --------------------------------------------------------------------------- #
def _http(url, method="GET", body=None, _retried=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": _token(), "Content-Type": "application/json"}
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urlerror.HTTPError as e:
        if e.code == 429 and not _retried:          # rate-limited: wait once, retry
            time.sleep(6)
            return _http(url, method, body, _retried=True)
        return e.code, {"_error": e.read().decode()[:500]}
    except urlerror.URLError as e:
        return 0, {"_error": str(e)}


def _http_multipart(url, filename, blob):
    """POST one file as multipart/form-data (stdlib only — no `requests` dep)."""
    boundary = "----otlobly" + os.urandom(8).hex()
    safe = filename.replace('"', "")
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="attachment"; filename="{safe}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    payload = head + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urlrequest.Request(url, data=payload, method="POST", headers={
        "Authorization": _token(),
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urlrequest.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urlerror.HTTPError as e:
        return e.code, {"_error": e.read().decode()[:500]}
    except urlerror.URLError as e:
        return 0, {"_error": str(e)}


def _download(url, dest):
    """Fetch a ClickUp attachment to disk. Signed URLs work bare; fall back to
    an authorized request. Returns True on success."""
    for headers in ({}, {"Authorization": _token()}):
        try:
            req = urlrequest.Request(url, headers=headers)
            with urlrequest.urlopen(req, timeout=60) as r:
                if r.status == 200:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.read())
                    return True
        except (urlerror.URLError, OSError):
            continue
    return False


# --------------------------------------------------------------------------- #
# Schema — discovered once from the list, cached in config.json (leluxe.schema)
# --------------------------------------------------------------------------- #
def discover(list_id):
    """Fetch the list's statuses (with ClickUp's colors) + custom-field defs.
    Returns (schema, error)."""
    st, body = _http(f"{CLICKUP_API}/list/{list_id}")
    if st != 200:
        return None, f"list fetch failed ({st}): {body.get('_error') or body}"
    statuses = [{"status": s.get("status"), "color": s.get("color"),
                 "orderindex": s.get("orderindex"), "type": s.get("type")}
                for s in body.get("statuses") or []]
    st, body = _http(f"{CLICKUP_API}/list/{list_id}/field")
    if st != 200:
        return None, f"field fetch failed ({st}): {body.get('_error') or body}"
    fields = {}
    for f in body.get("fields") or []:
        options = [{"id": o.get("id"),
                    "name": o.get("name") if o.get("name") is not None else o.get("label"),
                    "orderindex": o.get("orderindex"), "color": o.get("color")}
                   for o in (f.get("type_config") or {}).get("options") or []]
        fields[f["name"]] = {"id": f["id"], "type": f.get("type"), "options": options}
    return {"statuses": statuses, "fields": fields}, None


def schema(config=None):
    config = config or cfg.load()
    return cfg.get(config, "leluxe.schema", {}) or {}


def list_id(config=None):
    """The working list (read/write): push + Pull target."""
    config = config or cfg.load()
    return str(cfg.get(config, "leluxe.list_id", "") or "").strip()


def source_list_id(config=None):
    """The read-only migration source — the real AZ (2). Never written to."""
    config = config or cfg.load()
    return str(cfg.get(config, "leluxe.source_list_id", "") or "").strip()


def ready(config=None):
    """Token + list + discovered schema — the mirror can talk to ClickUp.
    (statuses, not fields: a list can legitimately have zero custom fields.)"""
    config = config or cfg.load()
    sch = schema(config)
    return bool(_token() and list_id(config)
                and (sch.get("statuses") or sch.get("fields")))


def status_names(config=None):
    return [s["status"] for s in schema(config).get("statuses") or []]


# --------------------------------------------------------------------------- #
# Field codecs — ClickUp custom-field values <-> plain local values
# --------------------------------------------------------------------------- #
def _option(fdef, key):
    """Find a dropdown/label option by uuid, orderindex or name (case-blind)."""
    for o in fdef.get("options") or []:
        if key == o.get("id") or key == o.get("orderindex"):
            return o
        if isinstance(key, str) and (o.get("name") or "").strip().lower() == key.strip().lower():
            return o
    return None


def decode_value(fdef, value):
    """ClickUp task custom-field value → plain local value (None = unset)."""
    if value in (None, "", []):
        return None
    t = fdef.get("type")
    if t == "drop_down":
        o = _option(fdef, value)
        return o["name"] if o else str(value)
    if t == "labels":
        out = []
        for v in value if isinstance(value, list) else [value]:
            o = _option(fdef, v)
            out.append(o["name"] if o else str(v))
        return out or None
    if t == "checkbox":
        return value in (True, "true", "True", 1, "1")
    if t in ("number", "currency"):
        try:
            n = float(value)
            return int(n) if n == int(n) else n
        except (TypeError, ValueError):
            return str(value)
    if t == "date":
        return str(value)                       # ms epoch, ClickUp-native
    return str(value)


def encode_value(fdef, value):
    """Plain local value → ClickUp set-field API value. Returns (ok, encoded|err)."""
    t = fdef.get("type")
    if value is None or value == "" or value == []:
        return True, None                       # None = clear the field
    if t == "drop_down":
        o = _option(fdef, value)
        return (True, o["id"]) if o else (False, f"unknown option {value!r}")
    if t == "labels":
        ids = []
        for v in value if isinstance(value, list) else [value]:
            o = _option(fdef, v)
            if not o:
                return False, f"unknown label {v!r}"
            ids.append(o["id"])
        return True, ids
    if t == "checkbox":
        return True, bool(value) if not isinstance(value, str) \
            else value.lower() in ("true", "1", "yes")
    if t in ("number", "currency"):
        try:
            return True, float(value)
        except (TypeError, ValueError):
            return False, f"not a number: {value!r}"
    if t == "date":
        ms = to_ms(value)
        return (True, ms) if ms is not None else (False, f"bad date {value!r}")
    return True, str(value)


def to_ms(value):
    """Accept ms epoch (int/str) or YYYY-MM-DD → int ms (UTC noon, round-trip
    stable with from_ms). None if unparseable."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if re.fullmatch(r"\d{11,}", s):
        return int(s)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        dt = datetime(int(m[1]), int(m[2]), int(m[3]), 12, 0, tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


# --------------------------------------------------------------------------- #
# Local store (SQLite leluxe_orders)
# --------------------------------------------------------------------------- #
def _row(r):
    d = dict(r)
    try:
        d["data"] = json.loads(d.pop("data_json") or "{}")
    except ValueError:
        d["data"] = {}
    return d


def get_row(row_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM leluxe_orders WHERE id=?", (row_id,)).fetchone()
        return _row(r) if r else None


def get_by_task(task_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM leluxe_orders WHERE clickup_task_id=?",
                      (task_id,)).fetchone()
        return _row(r) if r else None


def list_tree():
    """Orders (newest first) → packages → product items, one query, grouped by
    parent_local_id. Items may hang off a package (3-tier) or directly off the
    order (flat). Packages/items whose parent row is missing fall into orphans."""
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    orders = [r for r in rows if r["kind"] in TOP_KINDS]
    kids = {}
    for r in rows:
        if r["kind"] in ("package", "item"):
            kids.setdefault(r.get("parent_local_id"), []).append(r)
    def _created(r):
        try:
            return int(r.get("date_created") or 0)
        except (TypeError, ValueError):
            return 0
    orders.sort(key=_created, reverse=True)
    pkg_ids = set()
    for o in orders:
        pkgs = sorted([k for k in kids.get(o["id"], []) if k["kind"] == "package"],
                      key=lambda r: r["id"])
        for p in pkgs:
            p["items"] = sorted([k for k in kids.get(p["id"], []) if k["kind"] == "item"],
                                key=lambda r: r["id"])
            pkg_ids.add(p["id"])
        o["packages"] = pkgs
        # items attached straight to the order (flat/legacy) still render
        o["items"] = sorted([k for k in kids.get(o["id"], []) if k["kind"] == "item"],
                            key=lambda r: r["id"])
    have = {o["id"] for o in orders} | pkg_ids
    orphans = [r for r in rows if r["kind"] in ("package", "item")
               and r.get("parent_local_id") not in have]
    return orders, orphans


def sync_counts():
    with db.connect() as c:
        counts = {r["sync_state"]: r["n"] for r in c.execute(
            "SELECT sync_state, COUNT(*) n FROM leluxe_orders "
            "WHERE deleted=0 GROUP BY sync_state")}
        errors = [{"id": r["id"], "name": r["name"], "error": r["sync_error"]}
                  for r in c.execute(
                      "SELECT id, name, sync_error FROM leluxe_orders "
                      "WHERE sync_state='error' AND deleted=0 LIMIT 10")]
        # the ⚠ chip count must AGREE with the review modal: count only rows the
        # modal will actually list, i.e. those whose data.conflicts is non-empty.
        # A state='conflict' row with an empty list is malformed (healed by
        # heal_conflicts on the next sync / modal open) — counting it made the
        # chip say N while the modal said "no conflicts to review".
        conflict_rows, n_conf = [], 0
        for r in c.execute("SELECT id, name, data_json FROM leluxe_orders "
                           "WHERE sync_state='conflict' AND deleted=0"):
            try:
                has = bool((json.loads(r["data_json"] or "{}").get("conflicts") or []))
            except ValueError:
                has = False
            if has:
                n_conf += 1
                if len(conflict_rows) < 50:      # display list stays capped
                    conflict_rows.append({"id": r["id"], "name": r["name"]})
        counts["conflict"] = n_conf
        dels = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM leluxe_cu_deletes GROUP BY state")}
    return {"synced": counts.get("synced", 0), "dirty": counts.get("dirty", 0),
            "pushing": counts.get("pushing", 0), "error": counts.get("error", 0),
            "conflict": counts.get("conflict", 0),
            "cu_del_pending": dels.get("pending", 0) + dels.get("doing", 0),
            "cu_del_done": dels.get("done", 0),
            "cu_del_skipped": dels.get("skipped", 0),
            "last_errors": errors, "conflict_rows": conflict_rows}


def diagnose(config=None):
    """READ-ONLY sync health report — answers "why hasn't this AZ (2) change
    landed?". Writes NOTHING, so it is safe to run at any time, mid-sync
    included. Fetches AZ (2) exactly the way sync_from_source does, then
    compares every locally-linked row with its live source task and buckets
    each difference by the reason the next sync would (or would not) apply it.

    The buckets mirror the real skip paths: `pushing` returns early in
    _merge_row, `conflict` rows are parked until resolved, `error`/`dirty` are
    waiting on the pusher. Anything left is a change the next sync WILL apply
    — i.e. the pull simply has not run."""
    src, lid = source_list_id(config), list_id(config)
    out = {"list_id": lid, "source_list_id": src, "counts": sync_counts(),
           "checked": 0, "stale": [], "stale_count": 0, "stale_by_reason": {},
           "status_differs": 0, "frozen": 0}
    if not src:
        out["error"] = "leluxe.source_list_id (AZ 2) is not set"
        return out
    if not _token():
        out["error"] = "CLICKUP_API_TOKEN is not set"
        return out
    remote, page = {}, 0
    while True:                              # same URL + pagination as the sync
        st, body = _http(f"{CLICKUP_API}/list/{src}/task?include_closed=true"
                         f"&subtasks=true&page={page}")
        if st != 200:
            out["error"] = f"AZ (2) fetch failed ({st})"
            return out
        batch = body.get("tasks") or []
        for t in batch:
            remote[t["id"]] = t
        if body.get("last_page", True) or not batch:
            break
        page += 1
    out["az2_tasks"] = len(remote)
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    seen = set()
    for d in rows:
        data = d["data"] or {}
        srcid = data.get("source_task_id")
        if not srcid:
            continue
        out["checked"] += 1
        seen.add(srcid)
        t = remote.get(srcid)
        if not t:
            continue
        rstatus = (t.get("status") or {}).get("status") or ""
        rcu = str(t.get("date_updated") or "")
        if (rstatus == (d["status"] or "")
                and rcu == str(data.get("source_cu_updated") or "")):
            continue                          # in step with AZ (2)
        state = d["sync_state"]
        differs = rstatus != (d["status"] or "")
        bstatus = ((data.get("source_base") or {}).get("status") or "")
        # FROZEN needs BOTH halves, and the second is easy to get wrong: the
        # stamp must look current AND there must be an unapplied AZ (2) change,
        # i.e. our merge base no longer matches AZ (2). If base == AZ (2) the
        # row is merely "kept" — an app-side edit winning by design, which the
        # fast path is right to skip. Only base != AZ (2) means a real remote
        # change the timestamp shortcut will hide forever.
        stamp_current = rcu == str(data.get("source_cu_updated") or "")
        # parked conflicts are NOT frozen — they are visible in ⚠ Review and the
        # merge re-examines them every pass; frozen means invisible-to-everything
        frozen = (differs and stamp_current and bstatus != rstatus
                  and state != "conflict")
        # Verdict order MIRRORS the engine. Only `pushing` (early return) and
        # `conflict` (parked) stop an inbound value; `dirty`/`error` are about the
        # OUTBOUND push and never block a pull, so they must not outrank the
        # frozen verdict — otherwise the panel blames the queue for a row that is
        # actually stuck forever. Past those, the fast path wins over everything,
        # and only then does _merge_decide's base comparison apply.
        if state == "pushing":
            why = "blocked: mid-push (skipped by every pull)"
        elif state == "conflict":
            # base == local means the park came from 🛡 review mode (only AZ (2)
            # moved) — the amnesty re-applies it on the next NORMAL sync. A real
            # both-sides conflict stays until the owner picks a winner.
            why = ("parked by 🛡 review — will auto-apply on the next sync"
                   if bstatus == (d["status"] or "")
                   else "parked conflict — open ⚠ Review conflicts and pick a winner")
        elif frozen:
            why = ("FROZEN: AZ (2) stamp already current but the value differs "
                   "— no sync will re-check this row")
        elif differs and rstatus == bstatus:
            why = ("kept: the app's own edit wins (AZ (2) matches our merge base, "
                   "so the local value is deliberately preserved)")
        else:
            why = "would apply on next sync (pull has not run)"
        out["stale_by_reason"][why] = out["stale_by_reason"].get(why, 0) + 1
        if frozen:
            out["frozen"] += 1
        if differs:
            out["status_differs"] += 1
        if len(out["stale"]) < 100:
            out["stale"].append(
                {"id": d["id"], "kind": d["kind"], "name": (d["name"] or "")[:70],
                 "source_task_id": srcid, "sync_state": state, "why": why,
                 "status_differs": differs, "frozen": frozen,
                 "local_status": d["status"] or "", "az2_status": rstatus,
                 "base_status": bstatus})
    out["stale_count"] = sum(out["stale_by_reason"].values())
    # the headline number: rows whose STATUS on the board is wrong right now
    out["stale"].sort(key=lambda s: (not s["status_differs"], s["name"]))
    # AZ (2) top-level tasks with NO local row — the `skipped` bucket the sync
    # result line never shows (created before `since`, so never inserted).
    out["az2_unlinked_orders"] = [
        {"id": t["id"], "name": (t.get("name") or "")[:70],
         "date_created": t.get("date_created")}
        for tid, t in remote.items() if not t.get("parent") and tid not in seen][:50]
    out["az2_unlinked_count"] = sum(
        1 for tid, t in remote.items() if not t.get("parent") and tid not in seen)
    return out


def _insert_row(kind, name, *, status="", due_date=None, fields=None, desc="",
                tags=None, parent_local_id=None, parent_task_id=None,
                date_created=None, extra=None):
    """Low-level insert of a dirty row (shared by save_row + migrate). `extra`
    merges into data_json (e.g. tracking_number, source_task_id, image)."""
    data = {"description": desc or "", "tags": list(tags or []),
            "fields": dict(fields or {}), "images": [], "pushed": {}}
    if extra:
        data.update(extra)
    with db.connect() as c:
        cur = c.execute("""INSERT INTO leluxe_orders
            (parent_local_id, parent_task_id, kind, name, status, due_date,
             date_created, updated_at, sync_state, img_scanned, data_json)
            VALUES (?,?,?,?,?,?,?,?,'dirty',1,?)""",
            (parent_local_id, parent_task_id, kind, name, status or "",
             str(due_date) if due_date else None,
             str(date_created or int(time.time() * 1000)), db.now_iso(),
             json.dumps(data, ensure_ascii=False)))
        return cur.lastrowid


def save_row(payload, config=None):
    """Create/update one row from the editor. Overwrites name/status/due/tags/
    description/fields; keeps images + pushed snapshot. Marks dirty + kicks the
    pusher. Returns (row, error)."""
    config = config or cfg.load()
    sch = schema(config)
    kind = payload.get("kind")
    if kind not in ("order", "package", "item"):
        kind = "order"
    name = (payload.get("name") or "").strip()
    if not name:
        return None, "name is required"
    status = (payload.get("status") or "").strip()
    known = status_names(config)
    if known and status and status not in known:
        return None, f"unknown status {status!r}"
    fields_def = sch.get("fields") or {}
    fields = {}
    for fname, val in (payload.get("fields") or {}).items():
        if fname not in fields_def:
            return None, f"unknown field {fname!r}"
        if val in (None, "", []):
            continue
        if fields_def[fname].get("type") == "date" and to_ms(val) is None:
            return None, f"bad date for {fname!r}"
        ok, enc = encode_value(fields_def[fname], val)
        if not ok:
            return None, f"{fname}: {enc}"
        fields[fname] = val
    due = payload.get("due_date")
    due_ms = to_ms(due)
    if due not in (None, "") and due_ms is None:
        return None, "bad due date"
    tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
    desc = payload.get("description") or ""
    parent_local = payload.get("parent_local_id")
    parent = None
    if kind in ("package", "item"):
        if not parent_local:
            return None, f"a {kind} needs a parent"
        parent = get_row(parent_local)
        if not parent:
            return None, "parent not found"
        if kind == "package" and parent["kind"] not in TOP_KINDS:
            return None, "a package must sit under an order"
        if kind == "item" and parent["kind"] not in ("package",) + TOP_KINDS:
            return None, "an item must sit under a package or order"
    # a package's Tracking Number field also drives its live GAASH + display
    tn = (fields.get("Tracking Number") or "").strip() if kind == "package" else ""
    if payload.get("id"):
        now = db.now_iso()
        with db.connect() as c:
            r = c.execute("SELECT * FROM leluxe_orders WHERE id=? AND deleted=0",
                          (payload["id"],)).fetchone()
            if not r:
                return None, "row not found"
            d = _row(r)["data"]
            old_fields = dict(d.get("fields") or {})
            d.update({"description": desc, "tags": tags, "fields": fields})
            d.pop("pending_fields", None)   # a real edit → full push takes over
            if kind == "package":
                d["tracking_number"] = tn or None
            # always → dirty; if the pusher had this row claimed, _finish() only
            # flips pushing→synced, so a mid-push edit stays dirty and re-pushes
            c.execute("""UPDATE leluxe_orders SET name=?, status=?, due_date=?,
                         updated_at=?, data_json=?, sync_state='dirty',
                         sync_error=NULL, sync_attempts=0 WHERE id=?""",
                      (name, status, str(due_ms) if due_ms else None, now,
                       json.dumps(d, ensure_ascii=False), payload["id"]))
            db.log_leluxe_status(payload["id"], r["status"], status, "app", c=c)
        row_id = payload["id"]
        # a product's Tracking Number changed → its 📦 must follow the GWD
        if kind == "item":
            ok_ = str(old_fields.get(_field_key(old_fields, "Tracking Number",
                                                fields_def)) or "").strip()
            nk_ = str(fields.get(_field_key(fields, "Tracking Number",
                                            fields_def)) or "").strip()
            if ok_ != nk_:
                top = _top_order_of(get_row(row_id))
                if top:
                    regroup_order(top["id"], config)
    else:
        extra = {"tracking_number": tn} if tn else {}
        row_id = _insert_row(
            kind, name, status=status, due_date=due_ms, fields=fields,
            desc=desc, tags=tags, parent_local_id=parent_local,
            parent_task_id=(parent or {}).get("clickup_task_id"), extra=extra)
        # a brand-new product born WITH a GWD lands in that parcel's package
        if kind == "item" and str(fields.get(_field_key(fields, "Tracking Number",
                                                        fields_def)) or "").strip():
            top = _top_order_of(get_row(row_id))
            if top:
                regroup_order(top["id"], config)
    kick()
    return get_row(row_id), None


def set_status(row_id, status, config=None):
    """Inline status change from the board — touches ONLY the status column
    (fields/tags/description/images in data_json untouched), marks dirty and
    kicks the pusher, which mirrors it via the core PUT. Returns (row, error)."""
    config = config or cfg.load()
    status = (status or "").strip()
    known = status_names(config)
    if not status or (known and status not in known):
        return None, f"unknown status {status!r}"
    with db.connect() as c:
        old = c.execute("SELECT status FROM leluxe_orders WHERE id=? AND deleted=0",
                        (row_id,)).fetchone()
        n = c.execute("""UPDATE leluxe_orders SET status=?, updated_at=?,
                         sync_state='dirty', sync_error=NULL, sync_attempts=0
                         WHERE id=? AND deleted=0""",
                      (status, db.now_iso(), row_id)).rowcount
        if n:
            db.log_leluxe_status(row_id, old["status"], status, "app", c=c)
    if not n:
        return None, "row not found"
    _clear_pending(row_id)      # a real edit → the full push takes over
    kick()
    return get_row(row_id), None


def add_image(row_id, filename):
    """Register an uploaded local file on the row and queue it for ClickUp."""
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return False
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        d.setdefault("images", []).append(
            {"file": filename, "url": None, "uploaded": False})
        c.execute("""UPDATE leluxe_orders SET data_json=?, updated_at=?,
                     sync_state='dirty', sync_error=NULL, sync_attempts=0
                     WHERE id=?""",
                  (json.dumps(d, ensure_ascii=False), db.now_iso(), row_id))
    kick()
    return True


def soft_delete(row_id):
    """Local-only delete — NEVER touches ClickUp (the partner's board is
    authoritative; deleting there stays a deliberate manual act)."""
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET deleted=1, updated_at=? WHERE id=?",
                  (db.now_iso(), row_id))
        c.execute("""UPDATE leluxe_orders SET deleted=1, updated_at=?
                     WHERE parent_local_id=?""", (db.now_iso(), row_id))


# --------------------------------------------------------------------------- #
# Move a product between packages (Amazon split a shipment) — with an optional
# quantity split. See move_item().
# --------------------------------------------------------------------------- #
def _as_num(v):
    """'8' / 8.0 → 8 (int when whole); None when not a number."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return int(n) if n == int(n) else n


def _field_key(fields, name, schema_fields=None):
    """The EXACT stored key matching `name` (case/whitespace-insensitive) so we
    reuse 'Quantity ordered ' with its trailing space instead of forking a twin.
    Falls back to the schema's canonical key, then `name` itself."""
    want = " ".join(str(name).lower().split())
    for k in fields:
        if " ".join(str(k).lower().split()) == want:
            return k
    for k in (schema_fields or {}):
        if " ".join(str(k).lower().split()) == want:
            return k
    return name


_QTY_PREFIX = re.compile(r"^\s*(\d+)(\s+)")


def _relabel_qty(name, old_q, new_q):
    """If the name starts with the exact old quantity ("8 U.S. Polo…"), swap in
    the new count; otherwise leave the Amazon title untouched."""
    m = _QTY_PREFIX.match(str(name or ""))
    if m and _as_num(m.group(1)) == old_q:
        return f"{int(new_q)}{m.group(2)}{name[m.end():]}"
    return name


def _top_order_of(row):
    """Walk parent_local_id up to the order/parent row (or None)."""
    cur, seen, hops = row, set(), 0
    while cur and cur["kind"] not in TOP_KINDS and hops < 8:
        pid = cur.get("parent_local_id")
        if not pid or pid in seen:
            return None
        seen.add(pid)
        cur = get_row(pid)
        hops += 1
    return cur if cur and cur["kind"] in TOP_KINDS else None


def move_item(row_id, dest_parent_local_id=None, new_package=False,
              move_qty=None, config=None):
    """Move a product to another package of the SAME order, or split a partial
    quantity off into it. Returns (summary, error).

    Whole move (all units) re-parents the existing row — both parent ids are set
    so _relink() stays consistent, and the pusher re-parents the ClickUp subtask.
    A partial split leaves the source in place with a reduced quantity/amount and
    CREATES a new row in the destination, which pushes as a fresh subtask (no
    re-parent needed). Total Amount is divided proportionally, but only when the
    item actually carries one (it's often null — the amount lives on the order)."""
    config = config or cfg.load()
    row = get_row(row_id)
    if not row or row.get("deleted"):
        return None, "row not found"
    if row["kind"] != "item":
        return None, "only a product can be moved"
    if row.get("sync_state") == "conflict":     # dirty would silently un-park it
        return None, "this product is parked for sync review — resolve it first"
    order = _top_order_of(row)
    if not order:
        return None, "this product isn't inside an order"

    sch = schema(config)
    sfields = sch.get("fields") or {}
    data = dict(row["data"])
    fields = dict(data.get("fields") or {})
    qk = _field_key(fields, "Quantity ordered", sfields)
    ak = _field_key(fields, "Total Amount", sfields)
    Q = _as_num(fields.get(qk))
    mq = _as_num(move_qty)
    whole = mq is None or Q is None or mq >= Q
    if not whole and mq <= 0:       # validated BEFORE new_package creates its
        return None, "quantity to move must be at least 1"   # (empty) destination

    # ── resolve the destination package (only after validation, so a rejected
    # split can never strand a just-created empty package) ──
    if new_package:
        dest_id = _insert_row("package", "📦 no tracking",
                              parent_local_id=order["id"],
                              parent_task_id=order.get("clickup_task_id"))
        dest = get_row(dest_id)
    else:
        dest = get_row(dest_parent_local_id)
        if not dest or dest.get("deleted"):
            return None, "destination package not found"
        if dest["kind"] not in ("package",) + TOP_KINDS:
            return None, "destination must be a package or the order"
        dtop = dest if dest["kind"] in TOP_KINDS else _top_order_of(dest)
        if not dtop or dtop["id"] != order["id"]:
            return None, "can only move within the same order"
        if dest["id"] == row.get("parent_local_id"):
            return None, "already in that package"

    # ── whole move: re-parent the row (both ids → dirty → pusher re-parents;
    # _reparent_item also seeds pushed.parent so pull-born rows really move) ──
    if whole:
        if not _reparent_item(row, dest):
            return None, "row not found"
        # the move may have emptied its source package — an untracked empty is
        # pure noise (the "no tracking" leftovers the owner keeps hiding), so
        # sweep it right away; a tracked one is a real parcel and stays.
        if row.get("parent_local_id"):
            _sweep_order_packages(order["id"], sfields)
        kick()
        return {"mode": "move", "row_id": row_id, "dest_id": dest["id"],
                "qty": Q, "new_package": bool(new_package)}, None

    # ── partial split: reduce the source's QTY only, clone the moved units into
    # dest. The Total Amount is NOT divided — the full original ₪ stays on the
    # source (which keeps the original ClickUp task, so its amount is unchanged
    # on re-sync); the new row carries no amount. The owner tracks the physical
    # split without the money moving between packages. ──
    src_fields = dict(fields)
    src_fields[qk] = Q - mq                         # qty down; Total Amount untouched
    new_fields = dict(fields)
    new_fields[qk] = mq
    new_fields.pop(ak, None)                        # new product shows "—" (no amount)

    now = db.now_iso()
    with db.connect() as c:
        d = dict(data)
        d["fields"] = src_fields
        d.pop("pending_fields", None)              # a real edit → full push takes over
        c.execute("""UPDATE leluxe_orders SET name=?, data_json=?, sync_state='dirty',
                     sync_error=NULL, sync_attempts=0, updated_at=? WHERE id=?""",
                  (_relabel_qty(row["name"], Q, Q - mq),
                   json.dumps(d, ensure_ascii=False), now, row_id))
    new_id = _insert_row(
        "item", _relabel_qty(row["name"], Q, mq),
        status=row.get("status") or "", due_date=row.get("due_date"),
        fields=new_fields, desc=data.get("description") or "",
        tags=list(data.get("tags") or []), parent_local_id=dest["id"],
        parent_task_id=dest.get("clickup_task_id"),
        # carry the product's identity so the split-off row looks the same: the
        # board thumbnail (data.image + its ASIN cache key) and the editor gallery.
        # ASIN itself rides along in new_fields. image_asin matching the ASIN makes
        # the batch photo-fetch skip it (no redundant lookup).
        extra={"image": data.get("image"), "image_asin": data.get("image_asin"),
               "images": [dict(i) for i in data.get("images") or []]})
    if row.get("ordered_at"):                      # keep the true order date on the split unit
        with db.connect() as c:
            c.execute("UPDATE leluxe_orders SET ordered_at=? WHERE id=?",
                      (row["ordered_at"], new_id))
    kick()
    return {"mode": "split", "row_id": row_id, "new_id": new_id,
            "dest_id": dest["id"], "moved_qty": mq, "left_qty": Q - mq,
            "new_package": bool(new_package)}, None


# --------------------------------------------------------------------------- #
# Auto-regroup: a product's own Tracking Number decides which 📦 it lives in.
# Packages are frozen at import-time grouping (all-untracked → one shared
# "📦 no tracking"), but products receive their real GWD later — via the AZ (2)
# merge or 🚚 set-tracking — and used to stay stuck under the stale package.
# regroup_order() restores the invariant after every sync/pull/tracking edit.
# --------------------------------------------------------------------------- #
_SWEEP_GRACE_MS = 3_600_000     # a hand-made empty package survives an hour


def _row_tn(row, sfields):
    """The row's own GAASH tracking number ('' when untracked). Same precedence
    the board uses: packages read the JSON mirror key first, items their
    ClickUp custom field first."""
    d = row.get("data") or {}
    fields = d.get("fields") or {}
    fv = str(fields.get(_field_key(fields, "Tracking Number", sfields)) or "").strip()
    jv = str(d.get("tracking_number") or "").strip()
    return (jv or fv) if row.get("kind") == "package" else (fv or jv)


def _reparent_item(row, dest):
    """Whole-move re-parent shared by move_item + regroup_order: flip both
    parent ids, mark dirty, drop field-only markers. Pull-born rows get their
    OLD parent seeded into data.pushed.parent first — their push snapshot never
    recorded a parent, and push_one's legacy-adopt branch would otherwise
    swallow the move (no ClickUp PUT), the next Pull would snap the product
    back, and the boards would ping-pong forever. Conflict-parked rows are
    refused (going dirty would silently un-park the review). Returns True when
    the row was re-parented."""
    old_tid = _parent_task_id(row)
    with db.connect() as c:
        r = c.execute("SELECT sync_state, data_json FROM leluxe_orders "
                      "WHERE id=? AND deleted=0", (row["id"],)).fetchone()
        if not r or r["sync_state"] == "conflict":
            return False
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        if row.get("clickup_task_id") and old_tid and \
                (d.get("pushed") or {}).get("parent") is None:
            d.setdefault("pushed", {})["parent"] = old_tid
        c.execute("""UPDATE leluxe_orders SET parent_local_id=?, parent_task_id=?,
                     data_json=?, sync_state='dirty', sync_error=NULL,
                     sync_attempts=0, updated_at=? WHERE id=?""",
                  (dest["id"], dest.get("clickup_task_id"),
                   json.dumps(d, ensure_ascii=False), db.now_iso(), row["id"]))
    _clear_pending(row["id"])   # a field-only marker would skip the re-parent push
    return True


def _sweep_order_packages(order_id, sfields):
    """Two-phase janitor for one order's dead packages. Phase 1 soft-deletes
    (with a data.swept stamp) every package that has ZERO live children — the
    emptiness re-check lives INSIDE the UPDATE, so a package holding any
    product can never be swept — and is untracked (or a non-canonical duplicate
    of another live package's tracking; a unique tracked shell is a real parcel
    awaiting products and stays, owner's call 2026-07-21). Fresh packages get
    an hour's grace so "＋ Add package" isn't vacuumed mid-arrangement, and
    mid-push rows are left alone (their task id may not be persisted yet).

    Phase 2 queues the ClickUp working-list twin of ALREADY-swept packages —
    but only once NOTHING can cascade: ClickUp deletes subtasks with their
    parent, so the twin must survive while any row still maps a task under it
    (user-hidden children keep their "hide never touches ClickUp" promise) or
    any unpushed re-parent still references it (a moved product whose PUT
    hasn't landed). run_delete_pass re-verifies the list; AZ (2) untouchable.
    Returns the number of phase-1 sweeps."""
    swept = 0
    now_ms = int(time.time() * 1000)
    with db.connect() as c:
        pkgs = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE parent_local_id=? AND "
            "kind='package' AND deleted=0 ORDER BY id", (order_id,))]
        canon = {}                      # tn → lowest live package id
        for p in pkgs:
            t = _row_tn(p, sfields)
            if t:
                canon.setdefault(t, p["id"])
        for p in pkgs:
            t = _row_tn(p, sfields)
            if t and canon.get(t) == p["id"]:
                continue                # the parcel's canonical package stays
            try:
                born = int(p.get("date_created") or 0)
            except (TypeError, ValueError):
                born = 0
            if born and now_ms - born < _SWEEP_GRACE_MS:
                continue
            d = dict(p.get("data") or {})
            d["swept"] = db.now_iso()   # sweep-vs-hide marker (phase 2 + audit)
            n = c.execute("""UPDATE leluxe_orders SET deleted=1, data_json=?,
                             updated_at=? WHERE id=? AND deleted=0
                               AND sync_state!='pushing'
                               AND NOT EXISTS (SELECT 1 FROM leluxe_orders k
                                               WHERE k.parent_local_id=leluxe_orders.id
                                                 AND k.deleted=0)""",
                          (json.dumps(d, ensure_ascii=False), db.now_iso(),
                           p["id"])).rowcount
            swept += n
        # phase 2 — previously (or just) swept twins whose deletion is now safe
        for s in c.execute(
                """SELECT id, clickup_task_id, name, data_json FROM leluxe_orders
                   WHERE parent_local_id=? AND kind='package' AND deleted=1
                     AND clickup_task_id IS NOT NULL""", (order_id,)).fetchall():
            try:
                sd = json.loads(s["data_json"] or "{}")
            except ValueError:
                sd = {}
            if not sd.get("swept"):
                continue                # user-hidden → its ClickUp task must stay
            blocked = c.execute(
                """SELECT 1 FROM leluxe_orders
                   WHERE (parent_local_id=? AND clickup_task_id IS NOT NULL)
                      OR (deleted=0 AND sync_state!='synced'
                          AND json_extract(data_json,'$.pushed.parent')=?)
                   LIMIT 1""", (s["id"], s["clickup_task_id"])).fetchone()
            if not blocked:
                _queue_cu_delete(c, s["clickup_task_id"], s["id"], s["name"])
    return swept


def regroup_order(order_id, config=None):
    """Re-home every tracked product of one order into the package whose
    tracking matches its own GWD (created on demand), then sweep untracked
    packages left with zero products. Cheapest fix first: an untracked package
    whose products ALL share one new GWD simply BECOMES that parcel (backfill +
    rename) instead of re-parenting every row. Untracked products never move,
    conflict-parked rows are skipped, and loose items (hanging off the order)
    keep their display-only visual grouping. All writes are local dirty rows —
    the pusher mirrors them to the WORKING list only; AZ (2) is never written.
    Idempotent and SQLite-only, so sync/pull/edit tails can call it freely."""
    config = config or cfg.load()
    out = {"moved": 0, "backfilled": 0, "created": 0, "swept": 0}
    order = get_row(order_id)
    if not order or order.get("deleted") or order["kind"] not in TOP_KINDS:
        return out
    sfields = (schema(config) or {}).get("fields") or {}
    known = set(sfields.keys())
    with db.connect() as c:
        pkgs = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE parent_local_id=? AND "
            "kind='package' AND deleted=0 ORDER BY id", (order_id,))]
        items = []
        if pkgs:
            ph = ",".join("?" * len(pkgs))
            items = [_row(r) for r in c.execute(
                f"SELECT * FROM leluxe_orders WHERE parent_local_id IN ({ph}) "
                f"AND kind='item' AND deleted=0", [p["id"] for p in pkgs])]
    if not pkgs:
        return out
    pkg_tn = {p["id"]: _row_tn(p, sfields) for p in pkgs}
    by_tn = {}                          # tn → canonical package (oldest wins)
    for p in pkgs:
        if pkg_tn[p["id"]]:
            by_tn.setdefault(pkg_tn[p["id"]], p)
    kids = {}
    for it in items:
        kids.setdefault(it["parent_local_id"], []).append(it)

    # 1) backfill: untracked package, every product agrees on ONE new GWD and
    #    no other package owns it → the package becomes that parcel in place.
    for p in pkgs:
        if pkg_tn[p["id"]] or not kids.get(p["id"]):
            continue
        tns = {_row_tn(it, sfields) for it in kids[p["id"]]}
        tn = next(iter(tns)) if len(tns) == 1 else ""
        if not tn or tn in by_tn:
            continue
        with db.connect() as c:
            r = c.execute("SELECT name, data_json FROM leluxe_orders "
                          "WHERE id=? AND deleted=0", (p["id"],)).fetchone()
            if not r:
                continue
            d = json.loads(r["data_json"] or "{}")
            d["tracking_number"] = tn
            if "Tracking Number" in known:
                f = d.get("fields") or {}
                f[_field_key(f, "Tracking Number", sfields)] = tn
                d["fields"] = f
            d.pop("pending_fields", None)          # a real edit → full push
            name = f"📦 {tn}" if (r["name"] or "").strip() in \
                ("📦 no tracking", "📦", "") else r["name"]
            c.execute("""UPDATE leluxe_orders SET name=?, data_json=?,
                         sync_state='dirty', sync_error=NULL, sync_attempts=0,
                         updated_at=? WHERE id=?""",
                      (name, json.dumps(d, ensure_ascii=False), db.now_iso(),
                       p["id"]))
        pkg_tn[p["id"]] = tn
        by_tn[tn] = p
        out["backfilled"] += 1

    # 2) re-home: a tracked product under a package with a DIFFERENT tracking
    #    moves to its own parcel's package (same mechanics as move_item).
    for p in pkgs:
        for it in kids.get(p["id"], []):
            tn = _row_tn(it, sfields)
            if not tn or tn == pkg_tn[p["id"]] or it["sync_state"] == "conflict":
                continue
            dest = by_tn.get(tn)
            if dest is None:
                ifields = (it["data"] or {}).get("fields") or {}
                pkg_fields = {k: ifields[k] for k in _PKG_FIELDS
                              if k in known and ifields.get(k) is not None}
                if "Tracking Number" in known:
                    pkg_fields["Tracking Number"] = tn
                did = _insert_row("package", f"📦 {tn}", fields=pkg_fields,
                                  parent_local_id=order_id,
                                  parent_task_id=order.get("clickup_task_id"),
                                  date_created=order.get("date_created"),
                                  extra={"tracking_number": tn})
                dest = get_row(did)
                by_tn[tn] = dest
                out["created"] += 1
            if _reparent_item(it, dest):
                out["moved"] += 1

    # 3) sweep dead packages (empty + untracked/duplicate-tn) and, once safe,
    #    their ClickUp twins — see _sweep_order_packages.
    out["swept"] = _sweep_order_packages(order_id, sfields)
    if any(out.values()):
        kick()
    return out


def regroup_all(config=None):
    """regroup_order over every live order — pure SQLite, so the pull/sync
    tails run it to heal historical orders (stale '📦 no tracking' groups from
    before tracking numbers existed) in the same pass."""
    config = config or cfg.load()
    out = {"moved": 0, "backfilled": 0, "created": 0, "swept": 0}
    with db.connect() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM leluxe_orders WHERE deleted=0 AND kind IN (?,?)",
            TOP_KINDS)]
    for oid in ids:
        for k, v in regroup_order(oid, config).items():
            out[k] += v
    return out


# --------------------------------------------------------------------------- #
# Pull (ClickUp → local): paged fetch + upsert. Local unpushed edits win.
# --------------------------------------------------------------------------- #
def _decode_task(task, sch):
    """ClickUp task json → (columns dict, data dict)."""
    fields_def = sch.get("fields") or {}
    fields = {}
    for cf in task.get("custom_fields") or []:
        # decode using the field's OWN inline options (ClickUp returns the full
        # option set per task), so a dropdown value maps to its real name even
        # when the cached working schema has a different/partial option set —
        # essential for migrating AZ (2)'s 140-option NAME field. Fall back to
        # the cached schema only when the task carries no inline options.
        inline = [{"id": o.get("id"),
                   "name": o.get("name") if o.get("name") is not None else o.get("label"),
                   "orderindex": o.get("orderindex")}
                  for o in (cf.get("type_config") or {}).get("options") or []]
        fdef = ({"type": cf.get("type"), "options": inline} if inline
                else fields_def.get(cf.get("name")) or {"type": cf.get("type"), "options": []})
        val = decode_value(fdef, cf.get("value"))
        if val is not None:
            fields[cf["name"]] = val
    tags = [t.get("name") for t in task.get("tags") or [] if t.get("name")]
    desc = task.get("markdown_description") or task.get("description") or ""
    status = (task.get("status") or {}).get("status") or ""
    cols = {"clickup_task_id": task["id"],
            "parent_task_id": task.get("parent"),
            "kind": "item" if task.get("parent") else "parent",
            "name": task.get("name") or "",
            "status": status,
            "due_date": str(task["due_date"]) if task.get("due_date") else None,
            "date_created": str(task.get("date_created") or "")}
    data = {"description": desc, "tags": tags, "fields": fields,
            "cu_updated": str(task.get("date_updated") or "")}
    return cols, data


def upsert_from_clickup(task, sch):
    """Insert/refresh one task. Returns 'created' | 'updated' | 'unchanged'
    | 'skipped_dirty'. ClickUp only ever overwrites rows that are fully synced —
    dirty/pushing/error rows keep the local edit until it lands."""
    cols, data = _decode_task(task, sch)
    now = db.now_iso()
    with db.connect() as c:
        r = c.execute("SELECT * FROM leluxe_orders WHERE clickup_task_id=?",
                      (cols["clickup_task_id"],)).fetchone()
        if r is None:
            data["images"] = []
            data["pushed"] = {"name": cols["name"], "status": cols["status"],
                              "due_date": cols["due_date"],
                              "description": data["description"],
                              "tags": list(data["tags"]),
                              "fields": dict(data["fields"])}
            c.execute("""INSERT INTO leluxe_orders
                (clickup_task_id, parent_task_id, kind, name, status, due_date,
                 date_created, updated_at, sync_state, img_scanned, data_json)
                VALUES (?,?,?,?,?,?,?,?,'synced',0,?)""",
                (cols["clickup_task_id"], cols["parent_task_id"], cols["kind"],
                 cols["name"], cols["status"], cols["due_date"],
                 cols["date_created"], now, json.dumps(data, ensure_ascii=False)))
            return "created"
        old = _row(r)
        if old["sync_state"] != "synced":
            return "skipped_dirty"
        if old["data"].get("cu_updated") == data["cu_updated"]:
            return "unchanged"
        d = old["data"]
        d.update(data)                     # keeps images (merged below) intact
        d["pushed"] = {"name": cols["name"], "status": cols["status"],
                       "due_date": cols["due_date"],
                       "description": data["description"],
                       "tags": list(data["tags"]),
                       "fields": dict(data["fields"])}
        c.execute("""UPDATE leluxe_orders SET parent_task_id=?, kind=?, name=?,
                     status=?, due_date=?, date_created=?, updated_at=?,
                     img_scanned=0, data_json=? WHERE id=?""",
                  (cols["parent_task_id"], cols["kind"], cols["name"],
                   cols["status"], cols["due_date"], cols["date_created"], now,
                   json.dumps(d, ensure_ascii=False), old["id"]))
        # a ClickUp-side status change (Faisal) is a real transition — log it so
        # the goal dashboard can date RD completions
        db.log_leluxe_status(old["id"], old["status"], cols["status"], "pull", c=c)
        return "updated"


def _relink():
    """After a pull: link children to their local parent rows, backfill parent
    task ids, and infer kind by depth for a 3-tier working list — a child of a
    top-level order task is a package, its own children are items."""
    with db.connect() as c:
        c.execute("""UPDATE leluxe_orders SET parent_local_id=(
                       SELECT p.id FROM leluxe_orders p
                       WHERE p.clickup_task_id=leluxe_orders.parent_task_id)
                     WHERE parent_task_id IS NOT NULL""")
        c.execute("""UPDATE leluxe_orders SET parent_task_id=(
                       SELECT p.clickup_task_id FROM leluxe_orders p
                       WHERE p.id=leluxe_orders.parent_local_id)
                     WHERE parent_local_id IS NOT NULL AND parent_task_id IS NULL""")
        # a child of a top-level order that ITSELF has children is a package;
        # a leaf child stays an item (so a plain 2-tier list still reads right).
        c.execute("""UPDATE leluxe_orders SET kind='package'
                     WHERE kind='item'
                       AND parent_local_id IN (
                         SELECT id FROM leluxe_orders WHERE parent_task_id IS NULL)
                       AND id IN (
                         SELECT parent_local_id FROM leluxe_orders
                         WHERE parent_local_id IS NOT NULL)""")
        # _decode_task labels EVERY child "item" first; a package that lost its
        # last product (moved/deleted, here or in ClickUp) has NO children to
        # prove it's a package by depth, so upsert_from_clickup's blind kind=
        # overwrite (fired whenever the task's own date_updated ever changes)
        # permanently demotes it to a fake "item" — invisible to regroup_order/
        # _sweep_order_packages (which only scan kind='package'), so it renders
        # on the board as a bogus product literally named "📦 no tracking" /
        # "📦 GWD…" forever. Reclaim it by the one signal depth can't erase:
        # only OUR OWN code ever names a row with that exact "📦 " prefix.
        ghosts = [r["id"] for r in c.execute(
            """SELECT id, name FROM leluxe_orders
               WHERE kind='item' AND deleted=0
                 AND parent_local_id IN (
                   SELECT id FROM leluxe_orders WHERE parent_task_id IS NULL)""")
            if str(r["name"] or "").startswith("📦 ")]
        if ghosts:
            ph = ",".join("?" * len(ghosts))
            c.execute(f"UPDATE leluxe_orders SET kind='package' WHERE id IN ({ph})",
                     ghosts)


def pull_tasks(config=None):
    """Fetch every task in the list (all statuses, closed included, subtasks)
    and upsert. ~1 request per 100 tasks — safe inside one web request."""
    config = config or cfg.load()
    lid = list_id(config)
    if not _token():
        return {"error": "CLICKUP_API_TOKEN is not set"}
    if not lid:
        return {"error": "leluxe.list_id is not set — run Discover first"}
    sch = schema(config)
    stats = {"tasks": 0, "created": 0, "updated": 0, "unchanged": 0,
             "skipped_dirty": 0, "pages": 0}
    page = 0
    while True:
        st, body = _http(f"{CLICKUP_API}/list/{lid}/task"
                         f"?include_closed=true&subtasks=true"
                         f"&include_markdown_description=true&page={page}")
        if st != 200:
            return {"error": f"ClickUp fetch failed ({st}): "
                             f"{(body or {}).get('_error') or body}", **stats}
        tasks = body.get("tasks") or []
        stats["pages"] += 1
        for t in tasks:
            stats[upsert_from_clickup(t, sch)] += 1
            stats["tasks"] += 1
        if body.get("last_page", True) or not tasks:
            break
        page += 1
    _relink()
    stats["regroup"] = regroup_all(config)   # tracked products follow their GWD
    return stats


_IMG_MD = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")


def localize_images(batch=8, config=None):
    """Download the attachments of `batch` not-yet-scanned tasks into
    leluxe_images/ (resumable via the img_scanned column — the client loops
    until remaining == 0). Rows mid-edit still get their files, but their
    description is left alone (dirty wins)."""
    config = config or cfg.load()
    done = 0
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE img_scanned=0 AND deleted=0 "
            "AND clickup_task_id IS NOT NULL ORDER BY id LIMIT ?", (batch,))]
    for row in rows:
        tid = row["clickup_task_id"]
        st, body = _http(f"{CLICKUP_API}/task/{tid}"
                         f"?include_markdown_description=true")
        if st != 200:
            continue                        # transient — retried next loop pass
        images = row["data"].get("images") or []
        seen = {i.get("url") for i in images if i.get("url")}
        atts = body.get("attachments") or []
        urls = [(a.get("url"), a.get("extension")) for a in atts if a.get("url")]
        desc = body.get("markdown_description") or body.get("description") or ""
        for u in _IMG_MD.findall(desc):
            if u not in {x[0] for x in urls}:
                urls.append((u, None))
        for u, ext in urls:
            if u in seen:
                continue
            tail = u.rsplit("/", 1)[-1].split("?")[0]
            ext = (ext or (tail.rsplit(".", 1)[-1] if "." in tail else "png"))
            ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:5] or "png"
            fname = f"{tid}-{hashlib.md5(u.encode()).hexdigest()[:8]}.{ext}"
            entry = {"file": None, "url": u, "uploaded": True}
            if _download(u, IMAGE_DIR / fname):
                entry["file"] = fname
            images.append(entry)
        with db.connect() as c:
            r = c.execute("SELECT sync_state, data_json FROM leluxe_orders "
                          "WHERE id=?", (row["id"],)).fetchone()
            if not r:
                continue
            try:
                d = json.loads(r["data_json"] or "{}")
            except ValueError:
                d = {}
            d["images"] = images
            if r["sync_state"] == "synced" and desc:
                d["description"] = desc
                d.setdefault("pushed", {})["description"] = desc
            c.execute("UPDATE leluxe_orders SET data_json=?, img_scanned=1 "
                      "WHERE id=?", (json.dumps(d, ensure_ascii=False), row["id"]))
        done += 1
        time.sleep(_pace())
    with db.connect() as c:
        remaining = c.execute(
            "SELECT COUNT(*) n FROM leluxe_orders WHERE img_scanned=0 "
            "AND deleted=0 AND clickup_task_id IS NOT NULL").fetchone()["n"]
    return {"done": done, "remaining": remaining}


# --------------------------------------------------------------------------- #
# Migrate — copy recent AZ (2) orders into the working list as a 3-tier tree.
# AZ (2) is READ-ONLY here (GET only); rows are built locally then the push
# worker creates them (order → package-by-tracking → product) in the working list.
# --------------------------------------------------------------------------- #
def _source_seen(src_task_id):
    with db.connect() as c:
        return bool(c.execute(
            "SELECT 1 FROM leluxe_orders "
            "WHERE json_extract(data_json,'$.source_task_id')=? LIMIT 1",
            (src_task_id,)).fetchone())


# The working list was originally seeded by DUPLICATING AZ (2) inside ClickUp —
# those rows carry no source_task_id, so _source_seen can't recognize them and
# migrate would re-copy the same orders (it did, once). Second dedup key: the
# order NAME, preferring the embedded Amazon order number (name formats vary:
# "Order # 113-…", "# 111-…", free text).
_ORDNUM = re.compile(r"\d{3}-\d{7}-\d{7}")


def _name_key(name):
    m = _ORDNUM.search(str(name or ""))
    if m:
        return "num:" + m.group(0)
    return "nm:" + " ".join(str(name or "").casefold().split())


def _existing_order_keys():
    """name-key -> local row id for every visible order/parent row."""
    out = {}
    with db.connect() as c:
        for r in c.execute("SELECT id, name FROM leluxe_orders "
                           "WHERE kind IN ('order','parent') AND deleted=0"):
            out.setdefault(_name_key(r["name"]), r["id"])
    return out


def _adopt_source(row_id, src_task_id):
    """Backfill data_json.source_task_id on a pre-existing (imported) order so
    _source_seen matches it forever after."""
    if not src_task_id:
        return
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return
        d = json.loads(r["data_json"] or "{}")
        if d.get("source_task_id"):
            return
        d["source_task_id"] = src_task_id
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))


# order-level fields stay on the order; these ride up to the package they came
# from (one shipment shares its tracking/clearance state).
_PKG_FIELDS = ("Tracking Number", "GASH STATUS", "gash date", "DATE SENT",
               "opened box", "UPS CONTACT DATE")


def migrate_from_source(since_iso, limit=20, config=None):
    """Read AZ (2) tasks created on/after `since_iso`, rebuild each as
    order → package(grouped by Tracking Number) → product, dirty for push."""
    config = config or cfg.load()
    src, lid = source_list_id(config), list_id(config)
    if not _token():
        return {"error": "CLICKUP_API_TOKEN is not set"}
    if not src:
        return {"error": "leluxe.source_list_id (AZ 2) is not set"}
    if not lid:
        return {"error": "leluxe.list_id (working list) is not set — Discover first"}
    if src == lid:
        return {"error": "the working list must differ from AZ (2)"}
    since_ms = to_ms(since_iso) or 0
    sch = schema(config)
    known = set((sch.get("fields") or {}).keys())   # only carry fields the working list has
    keep = lambda fields: {k: v for k, v in fields.items() if k in known}
    tasks, page = [], 0
    while True:
        st, body = _http(f"{CLICKUP_API}/list/{src}/task?include_closed=true"
                         f"&subtasks=true&include_markdown_description=true&page={page}")
        if st != 200:
            return {"error": f"AZ (2) fetch failed ({st}): "
                             f"{(body or {}).get('_error') or body}"}
        batch = body.get("tasks") or []
        tasks.extend(batch)
        if body.get("last_page", True) or not batch:
            break
        page += 1
    children = {}
    for t in tasks:
        if t.get("parent"):
            children.setdefault(t["parent"], []).append(t)
    orders = [t for t in tasks if not t.get("parent")
              and int(t.get("date_created") or 0) >= since_ms]
    orders.sort(key=lambda t: int(t.get("date_created") or 0), reverse=True)
    made = {"orders": 0, "packages": 0, "items": 0, "skipped": 0,
            "scanned": len(orders), "order_ids": []}
    keys = _existing_order_keys()          # 2nd dedup key: order name/number
    for src_order in orders:
        if made["orders"] >= limit:
            break
        if _source_seen(src_order["id"]):
            made["skipped"] += 1
            continue
        dup_id = keys.get(_name_key(src_order.get("name")))
        if dup_id:                          # already here via the ClickUp list duplication
            _adopt_source(dup_id, src_order["id"])   # heal the proper dedup key
            made["skipped"] += 1
            continue
        ocols, odata = _decode_task(src_order, sch)
        order_id = _insert_row(
            "order", ocols["name"], status=ocols["status"],
            due_date=ocols["due_date"], fields=keep(odata["fields"]),
            desc=odata["description"], tags=odata["tags"],
            date_created=ocols["date_created"],
            extra={"source_task_id": src_order["id"]})
        made["orders"] += 1
        made["order_ids"].append(order_id)
        keys.setdefault(_name_key(src_order.get("name")), order_id)  # same-name twice in one scan
        groups = {}
        for ch in children.get(src_order["id"], []):
            _, cdata = _decode_task(ch, sch)
            tn = str(cdata["fields"].get("Tracking Number") or "").strip()
            groups.setdefault(tn, []).append((ch, cdata))
        for tn, members in groups.items():
            first = members[0][1]["fields"]
            pkg_fields = keep({k: first[k] for k in _PKG_FIELDS
                               if first.get(k) is not None})
            if tn and "Tracking Number" in known:
                pkg_fields["Tracking Number"] = tn
            pkg_id = _insert_row(
                "package", f"📦 {tn}" if tn else "📦 no tracking",
                fields=pkg_fields, parent_local_id=order_id,
                date_created=ocols["date_created"],
                extra={"tracking_number": tn or None})
            made["packages"] += 1
            for ch, cdata in members:
                ccols, _c = _decode_task(ch, sch)
                _insert_row(
                    "item", ccols["name"], status=ccols["status"],
                    due_date=ccols["due_date"], fields=keep(cdata["fields"]),
                    desc=cdata["description"], parent_local_id=pkg_id,
                    date_created=ccols["date_created"],
                    extra={"source_task_id": ch["id"]})
                made["items"] += 1
    kick()
    return made


def _queue_cu_delete(c, tid, row_id=None, label=None):
    """Enqueue one working-list ClickUp task for background deletion. INSERT OR
    IGNORE keeps re-runs idempotent: an already-done entry stays done."""
    if not tid:
        return
    now = db.now_iso()
    c.execute("""INSERT OR IGNORE INTO leluxe_cu_deletes
                 (task_id, row_id, label, state, created_at, updated_at)
                 VALUES (?,?,?,'pending',?,?)""",
              (str(tid), row_id, label, now, now))


def dedupe_migrated(dry_run=True, config=None):
    """One-time cleanup for migrate-created duplicates. The working list was
    seeded by DUPLICATING AZ (2) in ClickUp (rows WITHOUT source_task_id);
    migrate later re-copied the same orders (rows WITH source_task_id). Where a
    name-group holds both kinds, keep the original import and remove each
    migrate copy — locally (cascade to its packages/items) plus its pushed twin
    task in the WORKING list. All ClickUp deletions are QUEUED (leluxe_cu_deletes)
    and drained by run_delete_pass in the background — this request itself is
    local-only SQLite and returns in under a second, so it can never hit the
    gunicorn/proxy timeout that killed the first live run. The double guard
    (row carries provenance AND the live task belongs to the working list —
    AZ (2) can never be touched) is re-checked per task at delete time."""
    config = config or cfg.load()
    lid = str(list_id(config) or "")
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders "
            "WHERE kind IN ('order','parent') AND deleted=0")]
    groups = {}
    for r in rows:
        groups.setdefault(_name_key(r["name"]), []).append(r)
    report, removed, cu_deleted, errors = [], 0, 0, []
    queue_rows = []                     # (task_id, row_id, label, parent_local_id)
    for key, grp in groups.items():
        if len(grp) < 2:
            continue
        keepers = [r for r in grp if not (r["data"] or {}).get("source_task_id")]
        losers = [r for r in grp if (r["data"] or {}).get("source_task_id")]
        if not keepers or not losers:
            report.append({"key": key, "ambiguous": True,
                           "names": [r["name"] for r in grp]})
            continue
        keeper = min(keepers, key=lambda r: r["id"])
        entry = {"key": key, "ambiguous": False,
                 "keeper": {"id": keeper["id"], "name": keeper["name"]},
                 "removed": [{"id": l["id"], "name": l["name"],
                              "tid": l.get("clickup_task_id")} for l in losers]}
        report.append(entry)
        if dry_run:
            continue
        for loser in losers:
            queue_rows.append((loser.get("clickup_task_id"), loser["id"],
                               loser["name"], None))
            now = db.now_iso()
            with db.connect() as c:
                kids = [r["id"] for r in c.execute(
                    "SELECT id FROM leluxe_orders WHERE parent_local_id=?",
                    (loser["id"],))]
                ids = [loser["id"]] + kids
                if kids:
                    ph = ",".join("?" * len(kids))
                    ids += [r["id"] for r in c.execute(
                        f"SELECT id FROM leluxe_orders "
                        f"WHERE parent_local_id IN ({ph})", kids)]
                ph = ",".join("?" * len(ids))
                c.execute(f"UPDATE leluxe_orders SET deleted=1, updated_at=? "
                          f"WHERE id IN ({ph})", (now, *ids))
            removed += 1
            _adopt_source(keeper["id"], (loser["data"] or {}).get("source_task_id"))

    # ── item-level pass: sync-created twin PRODUCTS. The originals came from the
    # ClickUp list-duplication (no source_task_id); the sync couldn't match them
    # and inserted copies. Pair originals with copies 1:1 by (order, name-key):
    # keep the original (the owner's edits live there), adopt the copy's AZ (2)
    # provenance onto it, remove the copy locally and queue its working-list
    # task. Everything is computed in memory first so dry-run predicts the
    # exact same numbers execute applies. ──
    with db.connect() as c:
        vis = {r["id"]: _row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")}
    children = {}
    for r in vis.values():
        if r["parent_local_id"] is not None:
            children.setdefault(r["parent_local_id"], []).append(r["id"])

    def _order_of(rid):
        cur, seen = rid, set()
        while cur in vis and cur not in seen:
            seen.add(cur)
            p = vis[cur]["parent_local_id"]
            if p is None or p not in vis:
                break
            cur = p
        return cur

    item_entries, touched_parents, gone = [], set(), set()
    adopts = []                        # (keeper_id, twin_data) applied on execute
    ambiguous_items = same_source_fixed = 0

    # repair pass: two visible items sharing one source_task_id — an interrupted
    # run adopted the twin's id onto the keeper but was killed before removing
    # the twin. Keep the oldest row (the owner's original), remove the rest.
    by_src = {}
    for r in vis.values():
        if r["kind"] == "item":
            s = (r["data"] or {}).get("source_task_id")
            if s:
                by_src.setdefault(str(s), []).append(r)
    for s, twins in by_src.items():
        if len(twins) < 2:
            continue
        twins.sort(key=lambda r: r["id"])
        for extra in twins[1:]:
            item_entries.append(
                {"order": (vis.get(_order_of(extra["id"])) or {}).get("name") or "",
                 "keep": twins[0]["name"], "keep_id": twins[0]["id"],
                 "twin_id": extra["id"], "tid": extra.get("clickup_task_id"),
                 "same_source": True, "ambiguous": False})
            same_source_fixed += 1
            gone.add(extra["id"])
            queue_rows.append((extra.get("clickup_task_id"), extra["id"],
                               extra["name"], extra["parent_local_id"]))
            if extra["parent_local_id"] is not None:
                touched_parents.add(extra["parent_local_id"])

    igroups = {}
    for r in vis.values():
        if r["kind"] != "item" or r["id"] in gone:
            continue
        g = igroups.setdefault((_order_of(r["id"]), _name_key(r["name"])),
                               {"orig": [], "sync": []})
        g["sync" if (r["data"] or {}).get("source_task_id") else "orig"].append(r)
    for (oid, key), g in igroups.items():
        if not g["orig"] or not g["sync"]:
            continue
        oname = (vis.get(oid) or {}).get("name") or ""
        if len(g["orig"]) != len(g["sync"]):
            ambiguous_items += 1
            item_entries.append({"order": oname, "key": key, "ambiguous": True})
            continue
        pairs = zip(sorted(g["orig"], key=lambda r: r["id"]),
                    sorted(g["sync"], key=lambda r: r["id"]))
        for keeper, twin in pairs:
            item_entries.append({"order": oname, "keep": keeper["name"],
                                 "keep_id": keeper["id"], "twin_id": twin["id"],
                                 "tid": twin.get("clickup_task_id"),
                                 "ambiguous": False})
            tdata = twin["data"] or {}
            kd = keeper["data"] or {}
            if any(tdata.get(k2) is not None and kd.get(k2) is None
                   for k2 in ("source_task_id", "source_base", "source_cu_updated")):
                adopts.append((keeper["id"], tdata))
            gone.add(twin["id"])
            queue_rows.append((twin.get("clickup_task_id"), twin["id"],
                               twin["name"], twin["parent_local_id"]))
            if twin["parent_local_id"] is not None:
                touched_parents.add(twin["parent_local_id"])

    # packages with no product left — emptied by this run, or already emptied
    # by an interrupted one (sync-created only; a hand-made empty package stays)
    pkg_gone = []
    for r in vis.values():
        if r["kind"] != "package" or r["id"] in gone:
            continue
        if any(k not in gone for k in children.get(r["id"], ())):
            continue
        if r["id"] in touched_parents or (r["data"] or {}).get("source_task_id"):
            pkg_gone.append(r)

    # deleting a package task takes its ClickUp subtasks with it — drop the
    # per-item deletions it makes redundant so the queue drains sooner
    pkg_ids = {p["id"] for p in pkg_gone}
    queue_rows = [q for q in queue_rows if q[3] not in pkg_ids]
    for p in pkg_gone:
        queue_rows.append((p.get("clickup_task_id"), p["id"], p["name"], None))
    queue_rows = [q for q in queue_rows if q[0]]

    if not dry_run:
        now = db.now_iso()
        with db.connect() as c:
            for keeper_id, tdata in adopts:
                kr = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                               (keeper_id,)).fetchone()
                if not kr:
                    continue
                kd = json.loads(kr["data_json"] or "{}")
                grew = False
                for k2 in ("source_task_id", "source_base", "source_cu_updated"):
                    if tdata.get(k2) is not None and kd.get(k2) is None:
                        kd[k2] = tdata[k2]
                        grew = True
                if grew:
                    c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                              (json.dumps(kd, ensure_ascii=False), keeper_id))
            ids = list(gone) + [p["id"] for p in pkg_gone]
            if ids:
                ph = ",".join("?" * len(ids))
                c.execute(f"UPDATE leluxe_orders SET deleted=1, updated_at=? "
                          f"WHERE id IN ({ph})", (now, *ids))
            for tid, rid2, label, _pp in queue_rows:
                _queue_cu_delete(c, tid, rid2, label)
        if queue_rows:
            kick()                     # start draining within seconds

    return {"groups": report, "removed": removed, "clickup_deleted": cu_deleted,
            "cu_queued": len(queue_rows),
            "items": item_entries,
            "item_pairs": sum(1 for e in item_entries if not e.get("ambiguous")),
            "items_removed": len(gone), "items_adopted": len(adopts),
            "pkgs_removed": len(pkg_gone), "ambiguous_items": ambiguous_items,
            "same_source_fixed": same_source_fixed,
            "errors": errors, "dry_run": bool(dry_run)}


# --------------------------------------------------------------------------- #
# Unified Sync — one pass over AZ (2): INSERT new orders (migrate) + 3-way MERGE
# existing rows against `data.source_base` (the AZ (2) snapshot last reconciled).
# A field changed on BOTH sides parks the row as sync_state='conflict' for manual
# review. AZ (2) is GET-only; every write here only ever reaches the working copy
# via the background pusher.
# --------------------------------------------------------------------------- #
_SCALARS = ("name", "status", "due_date", "description", "tags")
_FIELD_LABELS = {"name": "الاسم · Name", "status": "الحالة · Status",
                 "due_date": "الاستحقاق · Due date",
                 "description": "الوصف · Description", "tags": "الوسوم · Tags"}


def _strip_embeds(text):
    """Drop ClickUp image-embed markdown so a description that only differs by
    uploaded-image embeds isn't seen as an edit."""
    return _IMG_MD.sub("", text or "")


def _cmp(field, v):
    """Normalize a value for equality only (not for storage): empty forms →
    "", lists → order-insensitive, description → embeds/whitespace stripped."""
    if v is None or v == "" or v == []:
        return ""
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    if field == "description":
        return _strip_embeds(v).strip()
    return v


def _vals_equal(field, a, b):
    return _cmp(field, a) == _cmp(field, b)


def _merge_decide(field, base, local, remote):
    """3-way merge verdict for one field."""
    r_eq_b = _vals_equal(field, remote, base)
    l_eq_b = _vals_equal(field, local, base)
    if r_eq_b and l_eq_b:
        return "unchanged"
    if l_eq_b:
        return "apply"                 # AZ (2)-only change → take remote
    if r_eq_b:
        return "keep"                  # app-only change → keep local
    if _vals_equal(field, remote, local):
        return "converge"             # both reached the same value
    return "conflict"


def _base_matches(base, remote, known):
    """Is the stored merge base STILL identical to what AZ (2) holds right now?

    The sync's fast path used to trust `source_cu_updated` on its own, but a
    task's date_updated only says WHEN it last changed — never that we actually
    applied that change. Anything that advances the stamp without writing the
    value (parking a conflict does exactly that) left the row invisible to every
    later sync: permanently stale while each run honestly reported "+0 updated".
    Comparing the base to the live snapshot is the honest version of that check —
    same cost class (pure CPU, the task JSON is already in hand)."""
    for field in _SCALARS:
        if not _vals_equal(field, base.get(field), remote.get(field)):
            return False
    bf, rf = (base.get("fields") or {}), (remote.get("fields") or {})
    for fname in (set(bf) | set(rf)) & set(known or ()):
        if not _vals_equal(fname, bf.get(fname), rf.get(fname)):
            return False
    return True


def _snapshot(cols, data, keep):
    """The comparable AZ (2) shape stored as source_base / pushed."""
    return {"name": cols["name"], "status": cols["status"],
            "due_date": cols["due_date"], "description": data["description"],
            "tags": list(data["tags"]), "fields": keep(data["fields"])}


def _apply_value(data, cols, field, value):
    """Write a resolved value into the row's local representation (columns for
    name/status/due_date, data_json for the rest)."""
    if field in ("name", "status", "due_date"):
        cols[field] = value
    elif field == "description":
        data["description"] = value or ""
    elif field == "tags":
        data["tags"] = list(value or [])
    else:
        f = data.setdefault("fields", {})
        if value in (None, "", []):
            f.pop(field, None)
        else:
            f[field] = value


def _base_set(base, field, value):
    if field == "tags":
        base["tags"] = list(value or [])
    elif field in ("name", "status", "due_date", "description"):
        base[field] = value
    else:
        base.setdefault("fields", {})[field] = value


def _write_row(c, rid, cols, data, sync_state=None, reset_sync=False,
               log_source="sync"):
    if "status" in cols:            # merge/resolve applied a status transition
        old = c.execute("SELECT status FROM leluxe_orders WHERE id=?",
                        (rid,)).fetchone()
        if old is not None:
            db.log_leluxe_status(rid, old["status"], cols["status"], log_source, c=c)
    sets = ["data_json=?", "updated_at=?"]
    args = [json.dumps(data, ensure_ascii=False), db.now_iso()]
    for k in ("name", "status", "due_date"):
        if k in cols:
            sets.append(f"{k}=?")
            args.append(cols[k])
    if sync_state is not None:
        sets.append("sync_state=?")
        args.append(sync_state)
    if reset_sync:
        sets.append("sync_error=NULL")
        sets.append("sync_attempts=0")
    args.append(rid)
    c.execute(f"UPDATE leluxe_orders SET {', '.join(sets)} WHERE id=?", args)


def _row_id_by_source(src_task_id):
    with db.connect() as c:
        r = c.execute("SELECT id FROM leluxe_orders "
                      "WHERE json_extract(data_json,'$.source_task_id')=? "
                      "AND deleted=0 LIMIT 1", (src_task_id,)).fetchone()
        return r["id"] if r else None


def _merge_row(rid, src_task, sch, known, keep, review_all=False, changes=None):
    """3-way merge one existing row against its AZ (2) source task. Returns
    'unchanged' | 'updated' | 'conflict' | 'pushing'. The whole read→merge→write
    happens in one connection so a concurrent human edit can't be clobbered.
    review_all=True parks even automatic (AZ (2)-only) changes as conflicts so
    the owner approves EVERY change — nothing is written until he does.
    `changes` (mutable list) collects the applied diffs for the sync report."""
    rcols, rdata = _decode_task(src_task, sch)
    remote = _snapshot(rcols, rdata, keep)
    src_cu = str(src_task.get("date_updated") or "")
    with db.connect() as c:
        r = c.execute("SELECT * FROM leluxe_orders WHERE id=? AND deleted=0",
                      (rid,)).fetchone()
        if not r:
            return "unchanged"
        d = _row(r)
        if d["sync_state"] == "pushing":
            return "pushing"               # let the pusher finish; catch it next pass
        data = d["data"]
        base = data.get("source_base")
        if base is None:                    # pre-feature row — seed a merge base
            if d["sync_state"] == "synced" and (data.get("pushed") or {}):
                p = data["pushed"]          # trust the confirmed copy state as base
                base = {"name": p.get("name"), "status": p.get("status"),
                        "due_date": p.get("due_date"),
                        "description": p.get("description"),
                        "tags": list(p.get("tags") or []),
                        "fields": dict(p.get("fields") or {})}
            else:                           # unverifiable provenance — baseline only
                data["source_base"] = remote
                data["source_cu_updated"] = src_cu
                c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                          (json.dumps(data, ensure_ascii=False), rid))
                return "unchanged"
        elif (data.get("source_cu_updated") == src_cu
              and _base_matches(base, remote, known)):
            # AZ (2) untouched since last sync AND our base still matches it, so
            # there is genuinely nothing to merge. Without the _base_matches
            # half, a row whose stamp was advanced without its value applied
            # would be skipped here FOREVER. NOTE: parked-conflict rows are NOT
            # exempt any more — parking advances the stamp but not the base, so
            # they always fall through to a full re-merge. That is the amnesty:
            # a 🛡 review-parked "apply" (base == local, only AZ (2) moved) is
            # re-decided as "apply" on the next NORMAL sync and lands; a REAL
            # both-sides conflict re-derives the identical parked set (stable,
            # no churn) and stays waiting for the owner.
            return "unchanged"
        local = {"name": r["name"], "status": r["status"] or "",
                 "due_date": r["due_date"],
                 "description": data.get("description") or "",
                 "tags": list(data.get("tags") or []),
                 "fields": dict(data.get("fields") or {})}
        new_base = {"name": base.get("name"), "status": base.get("status"),
                    "due_date": base.get("due_date"),
                    "description": base.get("description"),
                    "tags": list(base.get("tags") or []),
                    "fields": dict(base.get("fields") or {})}
        cols_new, conflicts, changed = {}, [], [False]

        def _do(field, b, l, rv, label):
            dec = _merge_decide(field, b, l, rv)
            if dec == "apply":
                if review_all:              # 🛡 park it for approval instead
                    conflicts.append({"field": field, "label": label,
                                      "local": l, "remote": rv})
                    return
                if changes is not None:
                    changes.append({"field": field, "label": label,
                                    "old": l, "new": rv})
                _apply_value(data, cols_new, field, rv)
                _base_set(new_base, field, rv)
                changed[0] = True
            elif dec == "converge":
                _base_set(new_base, field, rv)
            elif dec == "conflict":
                conflicts.append({"field": field, "label": label,
                                  "local": l, "remote": rv})

        for field in _SCALARS:
            _do(field, new_base.get(field), local.get(field), remote.get(field),
                _FIELD_LABELS.get(field, field))
        lf, rf, bf = local["fields"], remote["fields"], new_base["fields"]
        for fname in sorted((set(lf) | set(rf) | set(bf)) & known):
            _do(fname, bf.get(fname), lf.get(fname), rf.get(fname), fname)
        data["source_base"] = new_base
        data["source_cu_updated"] = src_cu
        if conflicts:
            data["conflicts"] = conflicts
            _write_row(c, rid, cols_new, data, sync_state="conflict")
            return "conflict"
        data.pop("conflicts", None)
        if changed[0]:
            _write_row(c, rid, cols_new, data, sync_state="dirty", reset_sync=True)
            return "updated"
        _write_row(c, rid, cols_new, data)     # only base / cu advanced
        return "unchanged"


def _insert_order_tree(src_order, kids, sch, known, keep, made):
    """INSERT one AZ (2) order as order → package(by Tracking Number) → item,
    seeding source_task_id + source_base on the order and each item (mirrors
    migrate_from_source, plus the merge-base seeds)."""
    ocols, odata = _decode_task(src_order, sch)
    order_id = _insert_row(
        "order", ocols["name"], status=ocols["status"],
        due_date=ocols["due_date"], fields=keep(odata["fields"]),
        desc=odata["description"], tags=odata["tags"],
        date_created=ocols["date_created"],
        extra={"source_task_id": src_order["id"],
               "source_cu_updated": str(src_order.get("date_updated") or ""),
               "source_base": _snapshot(ocols, odata, keep)})
    groups = {}
    for ch in kids:
        _, cdata = _decode_task(ch, sch)
        tn = str(cdata["fields"].get("Tracking Number") or "").strip()
        groups.setdefault(tn, []).append((ch, cdata))
    for tn, members in groups.items():
        first = members[0][1]["fields"]
        pkg_fields = keep({k: first[k] for k in _PKG_FIELDS if first.get(k) is not None})
        if tn and "Tracking Number" in known:
            pkg_fields["Tracking Number"] = tn
        pkg_id = _insert_row("package", f"📦 {tn}" if tn else "📦 no tracking",
                             fields=pkg_fields, parent_local_id=order_id,
                             date_created=ocols["date_created"],
                             extra={"tracking_number": tn or None})
        made["packages"] += 1
        for ch, cdata in members:
            ccols, _c = _decode_task(ch, sch)
            _insert_row("item", ccols["name"], status=ccols["status"],
                        due_date=ccols["due_date"], fields=keep(cdata["fields"]),
                        desc=cdata["description"], parent_local_id=pkg_id,
                        date_created=ccols["date_created"],
                        extra={"source_task_id": ch["id"],
                               "source_cu_updated": str(ch.get("date_updated") or ""),
                               "source_base": _snapshot(ccols, cdata, keep)})
            made["items"] += 1
    return order_id


def _insert_new_item(order_id, ch, sch, known, keep, made):
    """A product added in AZ (2) after its order was migrated → INSERT it under
    the order's package whose tracking matches (create the package if none)."""
    ccols, cdata = _decode_task(ch, sch)
    tn = str(cdata["fields"].get("Tracking Number") or "").strip()
    with db.connect() as c:
        pkgs = [_row(x) for x in c.execute(
            "SELECT * FROM leluxe_orders WHERE parent_local_id=? AND kind='package' "
            "AND deleted=0", (order_id,))]
    pkg_id = next((p["id"] for p in pkgs
                   if str(p["data"].get("tracking_number") or "").strip() == tn), None)
    if pkg_id is None:
        pkg_fields = keep({k: cdata["fields"][k] for k in _PKG_FIELDS
                           if cdata["fields"].get(k) is not None})
        if tn and "Tracking Number" in known:
            pkg_fields["Tracking Number"] = tn
        pkg_id = _insert_row("package", f"📦 {tn}" if tn else "📦 no tracking",
                             fields=pkg_fields, parent_local_id=order_id,
                             date_created=ccols["date_created"],
                             extra={"tracking_number": tn or None})
        made["packages"] += 1
    iid = _insert_row("item", ccols["name"], status=ccols["status"],
                      due_date=ccols["due_date"], fields=keep(cdata["fields"]),
                      desc=cdata["description"], parent_local_id=pkg_id,
                      date_created=ccols["date_created"],
                      extra={"source_task_id": ch["id"],
                             "source_cu_updated": str(ch.get("date_updated") or ""),
                             "source_base": _snapshot(ccols, cdata, keep)})
    made["items"] += 1
    made["new_items"] += 1
    return iid


def _adopt_child_source(row_id, src_task, sch, keep):
    """Seed AZ (2) provenance on a pre-existing ITEM matched by name (originals
    from the ClickUp list-duplication carry no source_task_id) so future syncs
    match it directly instead of inserting a twin product."""
    ccols, cdata = _decode_task(src_task, sch)
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return
        d = json.loads(r["data_json"] or "{}")
        if d.get("source_task_id"):
            return
        d["source_task_id"] = src_task["id"]
        d.setdefault("source_base", _snapshot(ccols, cdata, keep))
        d.setdefault("source_cu_updated", str(src_task.get("date_updated") or ""))
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))


def _trim_val(field, v):
    """Report-friendly value: long text (descriptions) clipped so the sync
    report stays readable/small."""
    if isinstance(v, str) and len(v) > 120:
        return v[:120] + "…"
    return v


def _kept_diffs(row_id, src_task, sch, keep, report, oname, order_id):
    """Owner-visible comparison: current LOCAL row vs AZ (2) RIGHT NOW — run for
    every matched row regardless of the merge's skips (source_cu_updated
    unchanged, dirty-wins, base-equal 'keep'). Any field where the app kept a
    value different from AZ (2) lands in report['kept'], so 'Sync from AZ (2)'
    always SHOWS the comparison even when it changes nothing. Conflict-parked
    rows are skipped (they're surfaced in the review panel already)."""
    if report is None or len(report.get("kept") or []) >= 400:
        return
    row = get_row(row_id)
    if not row or row.get("deleted") or row["sync_state"] == "conflict":
        return
    rcols, rdata = _decode_task(src_task, sch)
    remote = _snapshot(rcols, rdata, keep)
    data = row["data"]
    local = {"name": row["name"], "status": row["status"] or "",
             "due_date": row["due_date"],
             "description": data.get("description") or "",
             "tags": list(data.get("tags") or []),
             "fields": dict(data.get("fields") or {})}
    diffs = []
    for field in _SCALARS:
        if field == "fields":
            continue
        if not _vals_equal(field, remote.get(field), local.get(field)):
            diffs.append({"field": field, "label": _FIELD_LABELS.get(field, field),
                          "local": _trim_val(field, local.get(field)),
                          "remote": _trim_val(field, remote.get(field))})
    rf, lf = remote.get("fields") or {}, local.get("fields") or {}
    for fname in sorted(set(rf) | set(lf)):
        if not _vals_equal(fname, rf.get(fname), lf.get(fname)):
            diffs.append({"field": fname, "label": fname,
                          "local": _trim_val(fname, lf.get(fname)),
                          "remote": _trim_val(fname, rf.get(fname))})
    if diffs:
        report.setdefault("kept", []).append({
            "id": row_id, "kind": row["kind"], "name": row["name"] or "",
            "order": oname, "order_id": order_id,
            "task_id": data.get("source_task_id"),   # → enables ⤴ Push to AZ (2)
            "syncing": row["sync_state"] in ("dirty", "pushing"),
            "diffs": diffs})


def _merge_order(rid, src_order, kids, sch, known, keep, made,
                 review_all=False, report=None):
    """Merge an existing order + its AZ (2) children (merge matched items, insert
    genuinely new ones). Collects per-field applied changes + parked conflicts
    into `report` for the sync-review UI."""
    oname = src_order.get("name") or ""

    def _record(row_id, kind, name, res, chs):
        if report is None:
            return
        if res == "updated" and chs:
            report["applied"].append({"id": row_id, "kind": kind, "name": name,
                                      "order": oname, "order_id": rid,
                                      "changes": chs})
        elif res == "conflict":
            report["conflict_rows"].append({"id": row_id, "kind": kind,
                                            "name": name, "order": oname,
                                            "order_id": rid})

    chs = []
    res = _merge_row(rid, src_order, sch, known, keep, review_all, chs)
    if res == "conflict":
        made["conflicts"] += 1
    elif res == "updated":
        made["updated"] += 1
    _record(rid, "order", oname, res, chs)
    _kept_diffs(rid, src_order, sch, keep, report, oname, rid)
    # name-key adoption for CHILDREN too: originals from the list-duplication
    # carry no source_task_id — match by name so we never twin a product.
    with db.connect() as c:
        kid_rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0 AND kind='item' AND "
            "(parent_local_id=? OR parent_local_id IN (SELECT id FROM "
            "leluxe_orders WHERE parent_local_id=? AND deleted=0))", (rid, rid))]
    ikeys = {}
    for r2 in kid_rows:
        if not (r2["data"] or {}).get("source_task_id"):
            ikeys.setdefault(_name_key(r2["name"]), r2["id"])
    for ch in kids:
        crid = _row_id_by_source(ch["id"])
        if not crid:
            aid = ikeys.pop(_name_key(ch.get("name")), None)
            if aid:
                _adopt_child_source(aid, ch, sch, keep)
                crid = aid
        if crid:
            chs2 = []
            r2 = _merge_row(crid, ch, sch, known, keep, review_all, chs2)
            if r2 == "conflict":
                made["conflicts"] += 1
            elif r2 == "updated":
                made["updated"] += 1
            _record(crid, "item", ch.get("name") or "", r2, chs2)
            _kept_diffs(crid, ch, sch, keep, report, oname, rid)
        else:
            iid = _insert_new_item(rid, ch, sch, known, keep, made)
            if report is not None:
                report["new_items"].append({"id": iid, "order_id": rid,
                                            "name": ch.get("name") or "",
                                            "order": oname})


def _presync_snapshot():
    """Safety net: dump EVERY local Leluxe row (columns + data_json) to the data
    dir before a sync writes anything; keep the 5 newest. Restore any of them
    with:  python3 leluxe.py --restore-presync <file>"""
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, clickup_task_id, parent_task_id, parent_local_id, kind, "
            "name, status, due_date, date_created, sync_state, deleted, data_json "
            "FROM leluxe_orders")]
    if not rows:
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    p = data_path(f"leluxe_presync_{ts}.json")
    write_json_atomic(p, {"ts": db.now_iso(), "rows": rows})
    old = sorted(p.parent.glob("leluxe_presync_*.json"))
    for f in old[:-5]:
        try:
            f.unlink()
        except OSError:
            pass
    return p.name


def restore_presync(path):
    """Write a pre-sync snapshot's rows back over the current ones (matched by
    id; rows created after the snapshot are left alone). Restored rows go
    'dirty' so the pusher mirrors the restored values to the WORKING list
    (never AZ (2)). CLI-only — see _presync_snapshot's docstring."""
    p = data_path(path) if not os.path.isabs(str(path)) else path
    obj = json.loads(open(p, encoding="utf-8").read())
    n = 0
    with db.connect() as c:
        for r in obj.get("rows") or []:
            cur = c.execute("SELECT 1 FROM leluxe_orders WHERE id=?",
                            (r["id"],)).fetchone()
            if not cur:
                continue
            c.execute("""UPDATE leluxe_orders SET clickup_task_id=?,
                         parent_task_id=?, parent_local_id=?, kind=?, name=?,
                         status=?, due_date=?, date_created=?, deleted=?,
                         data_json=?, sync_state='dirty', sync_error=NULL,
                         sync_attempts=0, updated_at=? WHERE id=?""",
                      (r["clickup_task_id"], r["parent_task_id"],
                       r["parent_local_id"], r["kind"], r["name"], r["status"],
                       r["due_date"], r["date_created"], r["deleted"],
                       r["data_json"], db.now_iso(), r["id"]))
            n += 1
    kick()
    return f"restored {n} rows from {p} (now dirty → re-pushing to the working list)"


def sync_from_source(since_iso, limit=25, config=None, review_all=False):
    """One unified pass over the read-only AZ (2) list: NEW orders (created on/
    after `since_iso`, capped at `limit`) are inserted; every EXISTING local row
    is 3-way merged against its AZ (2) snapshot. Conflicting rows park as
    sync_state='conflict'. AZ (2) is GET-only — nothing is written back to it.
    review_all=True parks EVERY would-be change for approval (nothing is
    overwritten); either way a pre-sync snapshot + a per-change report are
    written to the data dir."""
    config = config or cfg.load()
    heal_conflicts()               # malformed parked rows repair before merging
    src, lid = source_list_id(config), list_id(config)
    if not _token():
        return {"error": "CLICKUP_API_TOKEN is not set"}
    if not src:
        return {"error": "leluxe.source_list_id (AZ 2) is not set"}
    if not lid:
        return {"error": "leluxe.list_id (working list) is not set — Discover first"}
    if src == lid:
        return {"error": "the working list must differ from AZ (2)"}
    since_ms = to_ms(since_iso) or 0
    sch = schema(config)
    known = set((sch.get("fields") or {}).keys())
    keep = lambda fields: {k: v for k, v in fields.items() if k in known}
    tasks, page = [], 0
    while True:
        st, body = _http(f"{CLICKUP_API}/list/{src}/task?include_closed=true"
                         f"&subtasks=true&include_markdown_description=true&page={page}")
        if st != 200:
            return {"error": f"AZ (2) fetch failed ({st}): "
                             f"{(body or {}).get('_error') or body}"}
        batch = body.get("tasks") or []
        tasks.extend(batch)
        if body.get("last_page", True) or not batch:
            break
        page += 1
    children = {}
    for t in tasks:
        if t.get("parent"):
            children.setdefault(t["parent"], []).append(t)
    tops = [t for t in tasks if not t.get("parent")]
    tops.sort(key=lambda t: int(t.get("date_created") or 0), reverse=True)
    made = {"orders": 0, "updated": 0, "conflicts": 0, "packages": 0,
            "items": 0, "new_items": 0, "skipped": 0, "scanned": len(tops),
            "order_ids": [],
            "regroup": {"moved": 0, "backfilled": 0, "created": 0, "swept": 0}}
    snap = _presync_snapshot()              # safety net BEFORE any write
    report = {"ts": db.now_iso(), "review_all": bool(review_all), "applied": [],
              "new_orders": [], "new_items": [], "conflict_rows": [], "kept": []}
    okeys = _existing_order_keys()          # name-key fallback for pre-feature rows
    inserted = 0
    for src_order in tops:
        rid = _row_id_by_source(src_order["id"])
        if not rid:
            dup = okeys.get(_name_key(src_order.get("name")))
            if dup:
                _adopt_source(dup, src_order["id"])
                rid = dup
        kids = children.get(src_order["id"], [])
        if rid:
            _merge_order(rid, src_order, kids, sch, known, keep, made,
                         review_all, report)
            if not review_all:            # review mode: nothing moves un-approved
                for k, v in regroup_order(rid, config).items():
                    made["regroup"][k] += v   # merged GWDs → products follow them
        elif int(src_order.get("date_created") or 0) >= since_ms and inserted < limit:
            oid = _insert_order_tree(src_order, kids, sch, known, keep, made)
            inserted += 1
            made["orders"] += 1
            made["order_ids"].append(oid)
            report["new_orders"].append({"id": oid,
                                         "name": src_order.get("name") or ""})
            okeys.setdefault(_name_key(src_order.get("name")), oid)
        else:
            made["skipped"] += 1
    write_json_atomic(data_path("leluxe_sync_report.json"), report)
    made["report_changes"] = len(report["applied"])
    made["kept"] = len(report["kept"])
    made["presync"] = snap
    kick()
    return made


# --------------------------------------------------------------------------- #
# Selective push INTO AZ (2) — the one deliberate exception to "AZ (2) is
# read-only". Safety layers: manual per-row action only (the automatic pusher
# still can never touch AZ (2)); STATUS-only allowlist; compare-and-set against
# the value the owner reviewed; a before-image journal (az2_pushes) enabling
# undo; and a ClickUp-visible trail (tag + comment) so pushed tasks are
# filterable inside ClickUp.
# --------------------------------------------------------------------------- #
def _az2_settings():
    st = {"enabled": True, "tag": "otl-push"}
    saved = db.get_setting("leluxe:az2")
    if isinstance(saved, dict):
        st.update({k: saved[k] for k in st if k in saved})
    return st


def _az2_comment(task_id, text):
    """Soft-fail comment on an AZ (2) task — the trail matters, but a failed
    comment must never fail a push that already landed."""
    try:
        _http(f"{CLICKUP_API}/task/{task_id}/comment", "POST",
              {"comment_text": text})
    except Exception:  # noqa: BLE001
        pass


def az2_push_status(row_id, expected_remote=None, user=""):
    """Write ONE row's status to its AZ (2) source task. Returns (entry, error)."""
    st = _az2_settings()
    if not st["enabled"]:
        return None, "AZ (2) push is disabled (leluxe:az2 setting)"
    row = get_row(row_id)
    if not row or row.get("deleted"):
        return None, "row not found"
    src_tid = (row["data"] or {}).get("source_task_id")
    if not src_tid:
        return None, "this row has no AZ (2) link (no source task)"
    new_status = (row.get("status") or "").strip()
    if not new_status:
        return None, "the row has no status to push"
    code, task = _http(f"{CLICKUP_API}/task/{src_tid}")
    if code != 200 or not isinstance(task, dict):
        return None, f"couldn't read the AZ (2) task ({code})"
    cur = ((task.get("status") or {}).get("status") or "").strip()
    if expected_remote is not None and cur != (expected_remote or "").strip():
        return None, (f"AZ (2) changed since you reviewed — it now says "
                      f"{cur!r}. Run Sync from AZ (2) and review again.")
    if cur == new_status:
        return {"noop": True, "task_id": src_tid, "status": cur}, None
    time.sleep(_pace())
    code, resp = _http(f"{CLICKUP_API}/task/{src_tid}", "PUT",
                       {"status": new_status})
    if code != 200:
        return None, (f"AZ (2) refused the status ({code}): "
                      f"{(resp or {}).get('_error') or resp}")
    now = db.now_iso()
    with db.connect() as c:
        cur_id = c.execute(
            """INSERT INTO az2_pushes (row_id, task_id, field, old_value,
               new_value, snapshot_json, ts, user, state)
               VALUES (?,?,?,?,?,?,?,?,'pushed')""",
            (row_id, src_tid, "status", cur, new_status,
             json.dumps(task, ensure_ascii=False), now, user)).lastrowid
    time.sleep(_pace())
    stcode, _r = _http(f"{CLICKUP_API}/task/{src_tid}/tag/{urlquote(st['tag'])}",
                       "POST", {})
    _az2_comment(src_tid, f"🔁 Otlobly push: Status '{cur}' → '{new_status}'"
                          f" · by {user or 'admin'} · {now[:16].replace('T', ' ')}"
                          f" (undo available in Otlobly)")
    return {"id": cur_id, "task_id": src_tid, "old": cur, "new": new_status,
            "tagged": stcode in (200, 201)}, None


def _az2_link_source(row_id, src_task_id, expect=None):
    """Link (or, with None, unlink) a local row to an AZ (2) task. `expect`
    guards the unlink: a row that meanwhile ADOPTED a different task (the
    hand-organized original) must not be blanked by the undo of the duplicate
    organize once created."""
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        if src_task_id:
            d["source_task_id"] = src_task_id
        else:
            if expect and d.get("source_task_id") != expect:
                return
            d.pop("source_task_id", None)
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))


def _az2_journal(row_id, task_id, field, old, new, snapshot, user):
    now = db.now_iso()
    with db.connect() as c:
        return c.execute(
            """INSERT INTO az2_pushes (row_id, task_id, field, old_value,
               new_value, snapshot_json, ts, user, state)
               VALUES (?,?,?,?,?,?,?,?,'pushed')""",
            (row_id, task_id, field, old, new,
             json.dumps(snapshot or {}, ensure_ascii=False), now, user)).lastrowid


def _sch_field_def(sfields, name):
    """A schema field's definition by case/whitespace-insensitive name (the
    live 'Quantity ordered ' key carries a trailing space)."""
    want = " ".join(str(name).casefold().split())
    for k, v in (sfields or {}).items():
        if " ".join(str(k).casefold().split()) == want:
            return v or {}
    return {}


def _sch_field_id(sfields, name):
    return _sch_field_def(sfields, name).get("id")


def _cf_display(fdef, raw):
    """A remote custom-field value as its display string — dropdowns come back
    as an option uuid or orderindex; resolve to the option NAME."""
    if raw in ("", None):
        return ""
    v = decode_value(fdef, raw)
    if v == raw and str(raw).isdigit():
        v = decode_value(fdef, int(raw))
    return "" if v is None else str(v)


def _inline_fdef(task, fid, fallback):
    """A dropdown's definition from the task's OWN inline option set — ClickUp
    ships the full, CURRENT options with every task, while the cached schema
    goes stale as new batch letters are added (the _decode_task lesson; the
    live cache was 140 options vs ClickUp's 154, so S-B6/E-B47 profiles were
    silently unencodable until the next ⟳ Discover schema)."""
    for f in (task or {}).get("custom_fields") or []:
        if f.get("id") == fid:
            opts = [{"id": o.get("id"),
                     "name": o.get("name") if o.get("name") is not None else o.get("label"),
                     "orderindex": o.get("orderindex")}
                    for o in (f.get("type_config") or {}).get("options") or []]
            if opts:
                return {"id": fid,
                        "type": f.get("type") or (fallback or {}).get("type"),
                        "options": opts}
    return fallback


def _task_cf(task, fid):
    """A ClickUp task's current custom-field value as a string ('' = unset)."""
    for f in (task or {}).get("custom_fields") or []:
        if f.get("id") == fid:
            v = f.get("value")
            return "" if v is None else str(v)
    return ""


def _num_eq(a, b):
    sa, sb = str(a if a is not None else "").strip(), str(b if b is not None else "").strip()
    try:
        return float(sa) == float(sb)
    except ValueError:
        return sa == sb


def _az2_qty(it, sfields):
    """A product's per-package amount: the leading number of its (split) name —
    the board's naming convention — else its own Quantity field, else None."""
    m = re.match(r"\s*(\d+)\b", str(it.get("name") or ""))
    if m:
        return int(m.group(1))
    f = (it.get("data") or {}).get("fields") or {}
    v = f.get(_field_key(f, "Quantity ordered", sfields))
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def az2_organize(order_id, user="", dry_run=False):
    """Mirror the app's order → 📦 GWD → products tree into the REAL AZ (2)
    list — the same nesting the board shows ("just like Otlobly, in ClickUp").
    Guarded like az2_push_status: manual only, per-write journal (undoable),
    otl-push tag on created tasks, comment trail on the order, paced.

    ADOPTION FIRST, creation last: a package the owner already made by hand in
    ClickUp (any container whose products carry the GWD) IS the parcel — the
    local package links to it; a product that already exists remotely (same
    normalized name) is linked, never duplicated — its real status stays the
    truth and flows back on the next Sync. Only what truly does not exist is
    created: "📦 <GWD>" tasks (status 'package' when the list has it) and
    quantity-split remainder products. Ensures ride every run: per-product
    Tracking Number + split quantity + profile NAME + Brand + NAME ON PACKAGEE
    (dropdowns encoded from the LIVE option set) + due date, and per-package
    totals/GWD/profile/ETA plus the Brand/ASIN its products roll up to — one
    shared value, or MIX; the order task carries that same roll-up. Empty
    local packages are never created remotely (previously-created shells are
    pruned), and organize-created tasks that no local row links anymore
    (because adoption found the hand-made original) are pruned too — via the
    journal's own undo, so only our tasks ever get deleted. Products someone
    moved under an unrelated task are skipped, never fought. dry_run returns
    the same report without writing.

    Returns (report, error); report.steps lists every action taken/planned."""
    st = _az2_settings()
    if not st["enabled"]:
        return None, "AZ (2) push is disabled (leluxe:az2 setting)"
    order = get_row(order_id)
    if not order or order.get("deleted") or order.get("kind") not in TOP_KINDS:
        return None, "order not found"
    src_order = (order.get("data") or {}).get("source_task_id")
    if not src_order:
        return None, "this order has no AZ (2) link (no source task)"
    sch = schema(None)
    sfields = sch.get("fields") or {}
    statuses = set(status_names(None))
    tn_fid = _sch_field_id(sfields, "Tracking Number")
    qty_fid = _sch_field_id(sfields, "Quantity ordered")
    amt_fid = _sch_field_id(sfields, "Total Amount")
    name_fdef = _sch_field_def(sfields, "NAME")
    name_fid = name_fdef.get("id")
    brand_fdef = _sch_field_def(sfields, "Brand")
    brand_fid = brand_fdef.get("id")
    ship_fdef = _sch_field_def(sfields, "NAME ON PACKAGEE")
    ship_fid = ship_fdef.get("id")
    asin_fid = _sch_field_id(sfields, "ASIN")
    MIX = "MIX"                    # the list's own "more than one brand" option

    def _row_field(r, fname):
        f = (r.get("data") or {}).get("fields") or {}
        return str(f.get(_field_key(f, fname, sfields)) or "").strip()

    def _row_pname(r):
        return _row_field(r, "NAME")

    def _one_or_mix(vals):
        """One shared value, or MIX — how a parent summarises its children."""
        s = {str(v).strip() for v in vals if v and str(v).strip()}
        if not s:
            return ""
        return s.pop() if len(s) == 1 else MIX

    def _fam_key(nm):
        return re.sub(r"^\s*\d+\s*", "", str(nm or "")).casefold().strip()

    def _row_due(r):
        d = str(r.get("due_date") or "").strip()
        return d if d.isdigit() else ""

    def _nkey(s):
        return " ".join(str(s or "").casefold().split())

    ord_pname = _row_pname(order)
    ord_due = _row_due(order)
    with db.connect() as c:
        pkgs = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE parent_local_id=? AND "
            "kind='package' AND deleted=0 ORDER BY id", (order_id,))]
        items = {p["id"]: [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE parent_local_id=? AND "
            "kind='item' AND deleted=0 ORDER BY id", (p["id"],))]
            for p in pkgs}
    pkgs = [p for p in pkgs if _row_tn(p, sfields)]
    if not pkgs:
        return None, "no tracked packages to organize — assign GWDs first"
    code, order_task = _http(f"{CLICKUP_API}/task/{src_order}?include_subtasks=true")
    if code != 200:
        return None, f"couldn't read the AZ (2) order ({code})"
    # profile options come from the live task, not the (stale-able) cache
    name_fdef = _inline_fdef(order_task, name_fid, name_fdef)

    # ---- the remote subtree: containers (any child with children of its own,
    # or a package-looking name) and the flat/nested product tasks ----
    task_cache = {}                      # task id → full GET json

    def _full(tid):
        if tid not in task_cache:
            c_, t_ = _http(f"{CLICKUP_API}/task/{tid}")
            task_cache[tid] = t_ if c_ == 200 and isinstance(t_, dict) else None
        return task_cache[tid]

    PKGISH = re.compile(r"📦|^\s*PACKAGE\b|GWD\d+", re.I)
    containers = {}                      # id → {"task", "kids": [entries]}
    remote_products = []                 # every descendant that isn't a container

    def _walk(parent_id, entries, depth):
        """FULL-depth subtree scan (cap 5). Products can hide products (the
        July habit of nesting under a pseudo-package product) — a shallow scan
        left nested originals invisible to adoption, so every run minted a
        fresh copy of them ("it keeps adding"). Containers are only the
        order's direct children that look like parcels — a product-named task
        with children is a product holding mis-nested products, never a
        parcel candidate.

        "Has children?" is read from the entry's own NESTED `subtasks` list —
        ClickUp ships the whole tree inline and has no `subtasks_count` key at
        all (v4.4 keyed the descent on that invented field, so it was always
        False: only 📦/GWD-named tasks were ever opened and every product's
        children stayed invisible — order 114-1928954-5920239 kept its three
        products nested under "2 U.S. Polo" through run after run)."""
        for ch in entries or []:
            name_ = str(ch.get("name") or "")
            looks_pkg = bool(PKGISH.search(name_))
            inline_kids = ch.get("subtasks") or []
            has_kids = bool(inline_kids) or (ch.get("subtasks_count") or 0) > 0
            product_name = bool(re.match(r"\s*\d+\b", name_))
            kids = []
            if (has_kids or looks_pkg) and depth < 5:
                c_, full = _http(f"{CLICKUP_API}/task/{ch['id']}"
                                 f"?include_subtasks=true")
                if c_ == 200 and isinstance(full, dict):
                    task_cache[ch["id"]] = full
                    kids = full.get("subtasks") or []
                kids = kids or inline_kids
            if depth == 0 and (looks_pkg or (has_kids and not product_name)):
                containers[ch["id"]] = {"task": task_cache.get(ch["id"]) or ch,
                                        "kids": kids}
            else:
                remote_products.append({"id": ch["id"], "name": name_,
                                        "parent": parent_id})
            if kids:
                _walk(ch["id"], kids, depth + 1)

    _walk(src_order, order_task.get("subtasks") or [], 0)

    # ---- Brand / ASIN / NAME ON PACKAGEE: one consistent set of values from
    # the order down to every product. A product takes the board's Brand — or,
    # when the board is silent, the single Brand its split family already
    # agrees on; a product's own Brand is only ever FILLED IN, never argued
    # with. Parents are DERIVED: a package and the order itself take the one
    # Brand/ASIN their products share, or the list's own "MIX" option when the
    # products disagree. The ship name is owner-managed — a wrong name is a
    # wrong ID on a customs doc — so it only ever flows DOWN into a blank
    # field: the order's own value, else the one its products already agree
    # on, else the FAISAL default (products/packages only — the order's own
    # blank is the owner's decision point and is left alone). ----
    def _live(task, fid, fdef):
        return _inline_fdef(task, fid, fdef) if fid else fdef

    def _disp(task, fid, fdef):
        return _cf_display(_live(task, fid, fdef), _task_cf(task, fid)) \
            if (fid and task) else ""

    brand_fdef = _live(order_task, brand_fid, brand_fdef)
    ship_fdef = _live(order_task, ship_fid, ship_fdef)
    fam_brand, ship_seen = {}, set()
    for it_ in [x for p in pkgs for x in items.get(p["id"], [])]:
        b_ = _row_field(it_, "Brand")
        src_ = (it_.get("data") or {}).get("source_task_id")
        t0 = _full(src_) if src_ else None
        if t0:
            b_ = b_ or _disp(t0, brand_fid, brand_fdef)
            s_ = _disp(t0, ship_fid, ship_fdef)
            if s_:
                ship_seen.add(s_)
        if b_ and b_.strip().upper() != MIX:
            fam_brand.setdefault(_fam_key(it_.get("name")), set()).add(b_)
    ord_ship = _disp(order_task, ship_fid, ship_fdef) or \
        (next(iter(ship_seen)) if len(ship_seen) == 1 else "")
    ship_dflt = ord_ship or (
        str((_option(ship_fdef, "FAISAL") or {}).get("name") or "").strip()
        if ship_fid else "")
    ord_brands, ord_asins = [], []

    def _fam_one(nm):
        s = fam_brand.get(_fam_key(nm)) or set()
        return next(iter(s)) if len(s) == 1 else ""

    def _container_gwd(cid):
        full = containers[cid]["task"]
        g = (_task_cf(full, tn_fid) or "").strip().upper() if tn_fid else ""
        if not g:
            m = re.search(r"GWD\d+", str(full.get("name") or ""), re.I)
            g = m.group(0).upper() if m else ""
        if not g and containers[cid]["kids"] and tn_fid:
            k0 = _full(containers[cid]["kids"][0]["id"])
            if k0:
                g = (_task_cf(k0, tn_fid) or "").strip().upper()
        return g

    by_gwd = {}                          # GWD → [container ids]
    for cid in containers:
        g = _container_gwd(cid)
        if g:
            by_gwd.setdefault(g, []).append(cid)

    def _tagged(tid):
        t_ = containers.get(tid, {}).get("task") or {}
        return any((x or {}).get("name") == st["tag"] for x in t_.get("tags") or [])

    # every remote task known to belong to THIS order — a product parented
    # under any of these (e.g. hand-nested under a sibling PRODUCT) is
    # mis-placed and gets lifted to its GWD's parcel; only parents OUTSIDE
    # the order are a human choice organize never fights
    subtree_ids = {src_order} | set(containers) | \
        {rp["id"] for rp in remote_products}

    # every AZ (2) task some local row already points at (adoption must not
    # steal them), and every task organize itself created (prune candidates)
    with db.connect() as c:
        sub_rows = [r["id"] for r in c.execute(
            "SELECT id FROM leluxe_orders WHERE parent_local_id=? OR "
            "parent_local_id IN (SELECT id FROM leluxe_orders WHERE "
            "parent_local_id=?)", (order_id, order_id))]
        qmarks = ",".join("?" * len(sub_rows)) or "0"
        created = {r["task_id"]: r["field"] for r in c.execute(
            f"SELECT task_id, field FROM az2_pushes WHERE state='pushed' AND "
            f"field IN ('pkg_create','item_create') AND row_id IN ({qmarks})",
            sub_rows)}
    linked = set()
    for p in pkgs:
        if (p.get("data") or {}).get("source_task_id"):
            linked.add(p["data"]["source_task_id"])
        for it in items.get(p["id"], []):
            if (it.get("data") or {}).get("source_task_id"):
                linked.add(it["data"]["source_task_id"])

    steps, skipped = [], []
    skipped_empty = 0
    planned_links = set(linked)          # what will be linked AFTER this run

    def _plan_cf(row_id, task_id, task, fname, fid, want, fdef=None):
        """Ensure one custom field on an EXISTING task (dropdowns compare and
        journal by option NAME; the encoded value rides the step)."""
        if not fid or want in (None, ""):
            return
        raw = _task_cf(task, fid)
        if fdef and fdef.get("type") in ("drop_down", "labels"):
            cur = _cf_display(fdef, raw)
            if cur.strip().casefold() == str(want).strip().casefold():
                return
            ok, enc = encode_value(fdef, want)
            if not ok:
                skipped.append({"row_id": row_id,
                                "note": f"{fname} {want!r} is not a ClickUp option"})
                return
            steps.append({"op": "cf_set", "row_id": row_id, "task_id": task_id,
                          "fname": fname, "fid": fid, "old": cur,
                          "new": str(want), "post_value": enc, "snapshot": task})
        else:
            if _num_eq(raw, want) or (str(raw).strip() == str(want).strip()):
                return
            steps.append({"op": "cf_set", "row_id": row_id, "task_id": task_id,
                          "fname": fname, "fid": fid, "old": raw,
                          "new": str(want), "snapshot": task})

    # ---- one pass per GWD (duplicate local packages fold into one parcel) --
    groups = {}
    canon_map = {}                        # GWD → canonical local package row id
    for p in pkgs:
        groups.setdefault(_row_tn(p, sfields).strip().upper(), []).append(p)
    for gwd, group in groups.items():
        g_items = [it for p in group for it in items.get(p["id"], [])]
        if not g_items:
            # a GWD with nothing assigned on the board: never create an empty
            # shell — and prune the shell an earlier run already created
            for p in group:
                p_src = (p.get("data") or {}).get("source_task_id")
                if p_src:
                    steps.append({"op": "pkg_prune", "row_id": p["id"],
                                  "task_id": p_src, "gwd": gwd})
                    planned_links.discard(p_src)
                else:
                    skipped_empty += 1
            continue
        # the canonical REMOTE parcel: a hand-made container wins over one we
        # created; a linked one that still matches keeps its place
        cands = by_gwd.get(gwd, [])
        cands.sort(key=lambda cid: (_tagged(cid),
                                    -(len(containers[cid]["kids"]) or 0)))
        pkg_src = cands[0] if cands else None
        pkg_task = task_cache.get(pkg_src) if pkg_src else None
        if not pkg_src:
            for p in group:                       # fall back to a live link
                p_src = (p.get("data") or {}).get("source_task_id")
                if p_src and _full(p_src):
                    pkg_src, pkg_task = p_src, _full(p_src)
                    break
        holder = next((p for p in group
                       if (p.get("data") or {}).get("source_task_id") == pkg_src),
                      None) if pkg_src else None
        canon_row = holder or group[0]
        canon_map[gwd] = canon_row["id"]
        if pkg_src and not holder:
            old_link = (canon_row.get("data") or {}).get("source_task_id")
            steps.append({"op": "adopt", "row_id": canon_row["id"],
                          "task_id": pkg_src, "old": old_link or "",
                          "what": f"📦 {gwd}"})
            if old_link:
                planned_links.discard(old_link)
            planned_links.add(pkg_src)
        for p in group:                           # duplicates drop their links
            p_src = (p.get("data") or {}).get("source_task_id")
            if p_src and p is not canon_row and p_src != pkg_src:
                steps.append({"op": "unlink", "row_id": p["id"],
                              "task_id": p_src})
                planned_links.discard(p_src)
        # package identity comes from its contents
        it_pnames = {n for n in (_row_pname(i) for i in g_items) if n}
        pkg_pname = it_pnames.pop() if len(it_pnames) == 1 else ord_pname
        it_dues = [d for d in (_row_due(i) for i in g_items) if d]
        pkg_due = max(it_dues) if it_dues else ord_due
        pkg_create = None
        if not pkg_src:
            pkg_create = {"op": "pkg_create", "row_id": canon_row["id"],
                          "gwd": gwd, "name": f"📦 {gwd}", "due": pkg_due,
                          "pname": pkg_pname}
            steps.append(pkg_create)
        qty_total = sum(q for q in (_az2_qty(it, sfields) for it in g_items) if q)
        g_brands, g_asins = [], []       # what this parcel's products end up with
        for it in g_items:
            it_src = (it.get("data") or {}).get("source_task_id")
            want_name = (it.get("name") or "").strip()
            qty = _az2_qty(it, sfields)
            it_pname = _row_pname(it) or ord_pname
            it_due = _row_due(it) or pkg_due
            if re.fullmatch(r"\d+", want_name):
                skipped.append({"row_id": it["id"], "name": want_name,
                                "note": "the product name is just a quantity — "
                                        "give it a title on the board, then "
                                        "re-run organize"})
                continue
            if it_src and not _full(it_src):
                it_src = None                     # link points at a dead task

            def _original(key):
                return next((rp for rp in remote_products
                             if rp["id"] not in planned_links
                             and rp["id"] not in created
                             and _nkey(rp["name"]) == key), None)

            def _name_exists(key, but=None):
                return any(_nkey(rp["name"]) == key and rp["id"] != but
                           and rp["id"] not in created
                           for rp in remote_products)

            if it_src and it_src in created:
                # linked to a duplicate organize itself created — the owner's
                # hand-made product (real status!) wins; ours gets pruned
                orig = _original(_nkey(want_name))
                if orig and _full(orig["id"]):
                    steps.append({"op": "adopt", "row_id": it["id"],
                                  "task_id": orig["id"], "old": it_src,
                                  "what": want_name[:40]})
                    planned_links.discard(it_src)
                    planned_links.add(orig["id"])
                    it_src = orig["id"]
                elif _name_exists(_nkey(want_name), but=it_src):
                    # the original exists but another board row claims it —
                    # this row + our copy ARE the duplicate pair
                    steps.append({"op": "unlink", "row_id": it["id"],
                                  "task_id": it_src})
                    planned_links.discard(it_src)
                    skipped.append({"row_id": it["id"], "name": want_name,
                                    "note": "duplicate line on the board — "
                                            "another row already maps to this "
                                            "product; the extra ClickUp copy is "
                                            "removed, delete the extra row too"})
                    continue
            if not it_src:
                # ADOPT the owner's existing product before ever creating one
                cand = _original(_nkey(want_name))
                if cand and _full(cand["id"]):
                    it_src = cand["id"]
                    steps.append({"op": "adopt", "row_id": it["id"],
                                  "task_id": it_src, "old": "",
                                  "what": want_name[:40]})
                    planned_links.add(it_src)
                elif _name_exists(_nkey(want_name)):
                    # creation is the LAST resort and never repeats: the
                    # product already exists somewhere in this order's tree
                    # (claimed by another row) — never mint another copy
                    skipped.append({"row_id": it["id"], "name": want_name,
                                    "note": "another board row already maps to "
                                            "this product in ClickUp — likely a "
                                            "duplicate line on the board; delete "
                                            "one and re-run"})
                    continue
            if not it_src:
                it_brand = _row_field(it, "Brand") or _fam_one(want_name)
                it_asin = _row_field(it, "ASIN")
                steps.append({"op": "item_create", "row_id": it["id"],
                              "name": want_name, "pkg_row": canon_row["id"],
                              "status": (it.get("status") or "").strip(),
                              "gwd": gwd, "qty": qty, "due": it_due,
                              "pname": it_pname, "brand": it_brand,
                              "asin": it_asin, "ship": ship_dflt})
                g_brands.append(it_brand)
                g_asins.append(it_asin)
                continue
            task = _full(it_src)
            cur_parent = task.get("parent") or ""
            cur_name = (task.get("name") or "").strip()
            if cur_parent == (pkg_src or ""):
                pass                                 # already under its parcel
            elif cur_parent and cur_parent not in subtree_ids \
                    and cur_parent != src_order:
                skipped.append({"row_id": it["id"], "name": want_name,
                                "note": "moved under a task OUTSIDE this order "
                                        "in ClickUp — left alone"})
                continue
            else:
                # flat under the order, in the wrong parcel, or hand-nested
                # under a sibling product — the GWD decides where it belongs
                steps.append({"op": "move", "row_id": it["id"],
                              "task_id": it_src, "old_parent": cur_parent,
                              "pkg_row": canon_row["id"], "snapshot": task})
            if want_name and cur_name and want_name != cur_name:
                steps.append({"op": "rename", "row_id": it["id"],
                              "task_id": it_src, "old": cur_name,
                              "new": want_name, "snapshot": task})
            # each product carries its parcel number, split amount, profile, ETA
            if tn_fid and (_task_cf(task, tn_fid) or "").strip() != gwd:
                steps.append({"op": "cf_set", "row_id": it["id"],
                              "task_id": it_src, "fname": "Tracking Number",
                              "fid": tn_fid, "old": _task_cf(task, tn_fid),
                              "new": gwd, "snapshot": task})
            if qty_fid and qty is not None and \
                    not _num_eq(_task_cf(task, qty_fid), qty):
                steps.append({"op": "cf_set", "row_id": it["id"],
                              "task_id": it_src, "fname": "Quantity ordered",
                              "fid": qty_fid, "old": _task_cf(task, qty_fid),
                              "new": str(qty), "snapshot": task})
            _plan_cf(it["id"], it_src, task, "NAME", name_fid, it_pname,
                     fdef=name_fdef)
            # Brand + ship name are FILLED IN when blank, never overwritten
            b_cur = _disp(task, brand_fid, brand_fdef)
            b_want = _row_field(it, "Brand") or _fam_one(want_name)
            if brand_fid and b_want and not b_cur:
                _plan_cf(it["id"], it_src, task, "Brand", brand_fid, b_want,
                         fdef=_live(task, brand_fid, brand_fdef))
            if ship_fid and ship_dflt and not _disp(task, ship_fid, ship_fdef):
                _plan_cf(it["id"], it_src, task, "NAME ON PACKAGEE", ship_fid,
                         ship_dflt, fdef=_live(task, ship_fid, ship_fdef))
            g_brands.append(b_cur or b_want)
            g_asins.append(_task_cf(task, asin_fid) if asin_fid else "")
            if it_due and not _num_eq(str(task.get("due_date") or ""), it_due):
                steps.append({"op": "due_set", "row_id": it["id"],
                              "task_id": it_src,
                              "old": str(task.get("due_date") or ""),
                              "new": it_due, "snapshot": task})
        # the parcel's own identity: total, GWD, profile, ETA — and an adopted
        # hand-made container is NORMALIZED to look like every other parcel:
        # renamed to "📦 <GWD>" and given the 'package' status (the products
        # keep their own, real statuses — those are the truth)
        pkg_brand, pkg_asin = _one_or_mix(g_brands), _one_or_mix(g_asins)
        ord_brands += g_brands
        ord_asins += g_asins
        if pkg_create is not None:
            pkg_create["qty_total"] = qty_total or None
            pkg_create["brand"] = pkg_brand
            pkg_create["asin"] = pkg_asin
            pkg_create["ship"] = ship_dflt
        elif pkg_task is not None:
            cur_pname_ = (pkg_task.get("name") or "").strip()
            if cur_pname_ != f"📦 {gwd}":
                steps.append({"op": "rename", "row_id": canon_row["id"],
                              "task_id": pkg_src, "old": cur_pname_,
                              "new": f"📦 {gwd}", "snapshot": pkg_task})
            cur_pst = ((pkg_task.get("status") or {}).get("status") or "").strip()
            if PKG_STATUS in statuses and cur_pst.casefold() != PKG_STATUS:
                steps.append({"op": "status_set", "row_id": canon_row["id"],
                              "task_id": pkg_src, "old": cur_pst,
                              "new": "package", "snapshot": pkg_task})
            if qty_fid and qty_total and \
                    not _num_eq(_task_cf(pkg_task, qty_fid), qty_total):
                steps.append({"op": "cf_set", "row_id": canon_row["id"],
                              "task_id": pkg_src, "fname": "Quantity ordered",
                              "fid": qty_fid, "old": _task_cf(pkg_task, qty_fid),
                              "new": str(qty_total), "snapshot": pkg_task})
            if tn_fid and (_task_cf(pkg_task, tn_fid) or "").strip() != gwd:
                steps.append({"op": "cf_set", "row_id": canon_row["id"],
                              "task_id": pkg_src, "fname": "Tracking Number",
                              "fid": tn_fid, "old": _task_cf(pkg_task, tn_fid),
                              "new": gwd, "snapshot": pkg_task})
            _plan_cf(canon_row["id"], pkg_src, pkg_task, "NAME", name_fid,
                     pkg_pname, fdef=name_fdef)
            # a parcel SUMMARISES its products: their one Brand/ASIN, or MIX
            if brand_fid and pkg_brand:
                _plan_cf(canon_row["id"], pkg_src, pkg_task, "Brand", brand_fid,
                         pkg_brand, fdef=_live(pkg_task, brand_fid, brand_fdef))
            if asin_fid and pkg_asin:
                _plan_cf(canon_row["id"], pkg_src, pkg_task, "ASIN", asin_fid,
                         pkg_asin)
            if ship_fid and ship_dflt and \
                    not _disp(pkg_task, ship_fid, ship_fdef):
                _plan_cf(canon_row["id"], pkg_src, pkg_task, "NAME ON PACKAGEE",
                         ship_fid, ship_dflt,
                         fdef=_live(pkg_task, ship_fid, ship_fdef))
            if pkg_due and not _num_eq(str(pkg_task.get("due_date") or ""),
                                       pkg_due):
                steps.append({"op": "due_set", "row_id": canon_row["id"],
                              "task_id": pkg_src,
                              "old": str(pkg_task.get("due_date") or ""),
                              "new": pkg_due, "snapshot": pkg_task})

    # ---- the order task itself summarises the WHOLE order the same way a
    # parcel summarises its contents: one shared Brand/ASIN, or MIX. Its ship
    # name is only filled from what its own products already agree on — the
    # owner picks that value, organize never invents it at order level. ----
    ord_brand, ord_asin = _one_or_mix(ord_brands), _one_or_mix(ord_asins)
    if brand_fid and ord_brand:
        _plan_cf(order_id, src_order, order_task, "Brand", brand_fid,
                 ord_brand, fdef=brand_fdef)
    if asin_fid and ord_asin:
        _plan_cf(order_id, src_order, order_task, "ASIN", asin_fid, ord_asin)
    # NOTE: the order's own NAME ON PACKAGEE is never written. It is the SOURCE
    # of the ship name, not a derived value — the owner picks it there and it
    # only ever flows DOWN. (Writing it back up from the products it had just
    # filled also made run 2 a write instead of a no-op.)

    # ---- remote-only leftovers vs the order's own quantity. The order's
    # "Quantity ordered" is the owner's ground truth: when the BOARD's
    # products already sum exactly to it, anything else in the ClickUp order
    # is a provable extra (an old copy / abandoned duplicate) → deleted, with
    # a full snapshot journalled so undo can recreate it. When the numbers
    # do NOT agree, nothing is deleted: mis-nested remote-only products are
    # moved to the parcel their Tracking Number names, and the mismatch is
    # reported for a manual fix. ----
    order_q = 0
    if qty_fid:
        try:
            order_q = int(float(_task_cf(order_task, qty_fid) or 0))
        except ValueError:
            order_q = 0
    board_q = sum(q for q in (
        _az2_qty(it, sfields)
        for gwd, group in groups.items()
        for p in group for it in items.get(p["id"], [])) if q)
    extras_proven = order_q > 0 and board_q == order_q
    remote_only = False
    for rp in remote_products:
        if rp["id"] in planned_links or rp["id"] in created:
            continue
        t_ = _full(rp["id"])
        if not t_:
            continue
        remote_only = True
        parent = rp.get("parent") or ""
        if extras_proven:
            if (t_.get("subtasks") or []) or \
                    any(x.get("parent") == rp["id"] for x in remote_products):
                skipped.append({"row_id": 0,
                                "note": f"{rp['name'][:40]!r} is an extra but "
                                        "still holds subtasks — fix it by hand"})
                continue
            steps.append({"op": "extra_delete", "row_id": 0,
                          "task_id": rp["id"], "name": rp["name"],
                          "snapshot": t_})
            continue
        if not parent or parent == src_order or parent in containers:
            continue                      # flat or already in a parcel: leave
        g = (_task_cf(t_, tn_fid) or "").strip().upper() if tn_fid else ""
        if not g:
            pt = _full(parent)
            g = (_task_cf(pt, tn_fid) or "").strip().upper() \
                if (pt and tn_fid) else ""
        dest_row = canon_map.get(g)
        if not dest_row:
            skipped.append({"row_id": 0,
                            "note": f"{rp['name'][:40]!r} is nested under "
                                    "another product in ClickUp and no parcel "
                                    "matches it — fix it by hand"})
            continue
        steps.append({"op": "move", "row_id": 0, "task_id": rp["id"],
                      "old_parent": parent, "pkg_row": dest_row,
                      "snapshot": t_})
    if order_q > 0 and board_q != order_q and remote_only:
        skipped.append({"row_id": 0,
                        "note": f"quantities don't add up: the order says "
                                f"{order_q} but the board's products sum to "
                                f"{board_q} — extras were NOT deleted; fix the "
                                f"quantities by hand and re-run"})

    # ---- 💰 the money invariant: Total Amount(order) == Σ products, and the
    # amount never duplicated. Split families spread one priced line over its
    # quantity splits (Σ preserved); a lone product takes the order's total;
    # when one product already equals the total, stray amounts on the others
    # are cleared; anything ambiguous is reported for a manual fix. ----
    order_total = 0.0
    if amt_fid:
        try:
            order_total = float(_task_cf(order_task, amt_fid) or 0)
        except ValueError:
            order_total = 0.0
    if amt_fid and order_total > 0:
        adopted_by_row = {s["row_id"]: s["task_id"] for s in steps
                          if s["op"] == "adopt" and s.get("task_id")}
        created_by_row = {s["row_id"]: s for s in steps
                         if s["op"] == "item_create"}
        recon = []
        for gwd, group in groups.items():
            for it in [x for p in group for x in items.get(p["id"], [])]:
                cs = created_by_row.get(it["id"])
                tid = adopted_by_row.get(it["id"]) or \
                    (it.get("data") or {}).get("source_task_id")
                t_ = _full(tid) if (tid and cs is None) else None
                if t_ is None and cs is None:
                    continue                       # dead link — nothing to set
                amt_ = 0.0
                if t_:
                    try:
                        amt_ = float(_task_cf(t_, amt_fid) or 0)
                    except ValueError:
                        amt_ = 0.0
                recon.append({"row_id": it["id"], "qty": _az2_qty(it, sfields),
                              "name": (it.get("name") or "").strip(),
                              "tid": tid if t_ else None, "task": t_,
                              "cs": cs, "amt": amt_})
        # remote-only products (no board row) are still IN the order — their
        # amounts belong to the Σ, else the invariant would false-alarm
        known_tids = {e["tid"] for e in recon if e["tid"]}
        planned_extra = {s["task_id"] for s in steps if s["op"] == "extra_delete"}
        for rp in remote_products:
            if rp["id"] in known_tids or rp["id"] in created \
                    or rp["id"] in planned_extra:
                continue
            t_ = _full(rp["id"])
            if not t_:
                continue
            try:
                a_ = float(_task_cf(t_, amt_fid) or 0)
            except ValueError:
                a_ = 0.0
            m_ = re.match(r"\s*(\d+)\b", rp["name"])
            recon.append({"row_id": 0, "qty": int(m_.group(1)) if m_ else None,
                          "name": rp["name"], "tid": rp["id"], "task": t_,
                          "cs": None, "amt": a_})

        def _amount_step(e, want):
            want_s = "" if want in (None, "") else str(round(float(want), 2))
            if e["cs"] is not None:
                e["cs"]["amount"] = want_s or None
            else:
                stp = {"op": "cf_set", "row_id": e["row_id"], "task_id": e["tid"],
                       "fname": "Total Amount", "fid": amt_fid,
                       "old": str(e["amt"] if e["amt"] else ""), "new": want_s,
                       "snapshot": e["task"]}
                if want_s == "":
                    stp["post_value"] = None
                steps.append(stp)
            e["amt"] = float(want or 0)

        fams = {}
        for e in recon:
            key = re.sub(r"^\s*\d+\s*", "", e["name"]).casefold().strip()
            if key and e["qty"]:
                fams.setdefault(key, []).append(e)
        for fam in fams.values():
            priced = [e for e in fam if e["amt"] > 0]
            if len(fam) < 2 or len(priced) != 1:
                continue
            src_e = priced[0]
            m = re.match(r"\s*(\d+)\b", str((src_e["task"] or {}).get("name") or ""))
            rq = int(m.group(1)) if m else (src_e["qty"] or 0)
            if not rq or sum(e["qty"] for e in fam) != rq:
                continue
            per = src_e["amt"] / rq
            for e in fam:
                want = round(per * e["qty"], 2)
                if not _num_eq(e["amt"], want):
                    _amount_step(e, want)
        s_ = sum(e["amt"] for e in recon)
        tol = max(1.0, order_total * 0.005)
        if recon and abs(s_ - order_total) > tol:
            if len(recon) == 1:
                _amount_step(recon[0], order_total)
            else:
                eq = [e for e in recon if abs(e["amt"] - order_total) <= tol]
                if eq:
                    for e in recon:
                        if e is not eq[0] and e["amt"] > 0:
                            _amount_step(e, "")
                else:
                    skipped.append({"row_id": 0,
                                    "note": f"💰 order is {order_total:g} but its "
                                            f"products sum to {round(s_, 2):g} — "
                                            f"organize can't tell which product "
                                            f"carries the difference; fix the "
                                            f"amounts by hand"})

    # ---- organize-created tasks nothing links anymore (adoption found the
    # hand-made original): prune them, products before their packages ----
    pruned_ids = {s["task_id"] for s in steps if s["op"] == "pkg_prune"}
    for tid, fld in created.items():
        if tid in planned_links or tid in pruned_ids or not _full(tid):
            continue
        steps.append({"op": "item_prune" if fld == "item_create" else "pkg_prune",
                      "row_id": 0, "task_id": tid, "gwd": ""})

    steps.sort(key=lambda s: 1 if s["op"] in ("item_prune",) else
               (2 if s["op"] == "pkg_prune" else 0))

    report = {"order": order.get("name"), "packages": len(groups),
              "creates_pkg": sum(1 for s in steps if s["op"] == "pkg_create"),
              "moves": sum(1 for s in steps if s["op"] == "move"),
              "renames": sum(1 for s in steps if s["op"] == "rename"),
              "creates_item": sum(1 for s in steps if s["op"] == "item_create"),
              "field_sets": sum(1 for s in steps if s["op"] == "cf_set"),
              "due_sets": sum(1 for s in steps if s["op"] == "due_set"),
              "status_sets": sum(1 for s in steps if s["op"] == "status_set"),
              "extra_deletes": sum(1 for s in steps if s["op"] == "extra_delete"),
              "adopts": sum(1 for s in steps if s["op"] in ("adopt", "unlink")),
              "prunes": sum(1 for s in steps if s["op"] in ("pkg_prune",
                                                            "item_prune")),
              "pruned": 0, "skipped_empty": skipped_empty,
              "skipped": skipped, "steps": [], "dry_run": bool(dry_run)}
    if dry_run:
        report["steps"] = [{k: v for k, v in s.items() if k != "snapshot"}
                           for s in steps]
        return report, None

    src_lid = source_list_id(None)
    pkg_tid = {}                 # canon row id → task id created this run
    done = 0

    def _set_extras(tid, s):
        """Brand / ASIN / ship name on a task organize just created — the same
        ensure-set an existing task gets, so a new row is never the odd one."""
        for fid_, fdef_, val_ in ((brand_fid, brand_fdef, s.get("brand")),
                                  (ship_fid, ship_fdef, s.get("ship"))):
            if fid_ and val_:
                okv_, enc_ = encode_value(fdef_, val_)
                if okv_:
                    _http(f"{CLICKUP_API}/task/{tid}/field/{fid_}",
                          "POST", {"value": enc_})
        if asin_fid and s.get("asin"):
            _http(f"{CLICKUP_API}/task/{tid}/field/{asin_fid}",
                  "POST", {"value": s["asin"]})
    for s in steps:
        op = s["op"]
        try:
            if op == "adopt":
                _az2_link_source(s["row_id"], s["task_id"])
                _az2_journal(s["row_id"], s["task_id"], "adopt",
                             s.get("old") or "", s["task_id"], {}, user)
            elif op == "unlink":
                _az2_link_source(s["row_id"], None, expect=s["task_id"])
                _az2_journal(s["row_id"], s["task_id"], "adopt",
                             s["task_id"], "", {}, user)
            elif op == "pkg_create":
                body = {"name": s["name"], "parent": src_order}
                if PKG_STATUS in statuses:
                    body["status"] = PKG_STATUS
                if s.get("due"):
                    body["due_date"] = int(s["due"])
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/list/{src_lid}/task",
                                   "POST", body)
                tid = (resp or {}).get("id") if code == 200 else None
                if not tid:
                    return report, (f"AZ (2) refused the package ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                pkg_tid[s["row_id"]] = tid
                _az2_link_source(s["row_id"], tid)
                with db.connect() as c:      # zero-diff on the next Sync
                    c.execute("UPDATE leluxe_orders SET status=? WHERE id=? "
                              "AND (status IS NULL OR status='')",
                              (PKG_STATUS if PKG_STATUS in statuses else "",
                               s["row_id"]))
                if tn_fid:
                    _http(f"{CLICKUP_API}/task/{tid}/field/{tn_fid}",
                          "POST", {"value": s["gwd"]})
                if qty_fid and s.get("qty_total"):
                    _http(f"{CLICKUP_API}/task/{tid}/field/{qty_fid}",
                          "POST", {"value": s["qty_total"]})
                if name_fid and s.get("pname"):
                    okv, enc = encode_value(name_fdef, s["pname"])
                    if okv:
                        _http(f"{CLICKUP_API}/task/{tid}/field/{name_fid}",
                              "POST", {"value": enc})
                _set_extras(tid, s)
                time.sleep(_pace())
                _http(f"{CLICKUP_API}/task/{tid}/tag/{urlquote(st['tag'])}",
                      "POST", {})
                _az2_journal(s["row_id"], tid, "pkg_create", "", s["name"],
                             {}, user)
                s = {**s, "task_id": tid}
            elif op in ("pkg_prune", "item_prune"):
                with db.connect() as c:
                    jrow = c.execute(
                        "SELECT id FROM az2_pushes WHERE task_id=? AND "
                        "field IN ('pkg_create','item_create') AND "
                        "state='pushed' ORDER BY id DESC LIMIT 1",
                        (s["task_id"],)).fetchone()
                if not jrow:
                    skipped.append({"row_id": s["row_id"],
                                    "note": f"{s.get('gwd') or s['task_id']} was "
                                            "not created by organize — left alone"})
                    continue
                _entry, perr = az2_undo(jrow["id"], user=user)
                if perr:
                    skipped.append({"row_id": s["row_id"],
                                    "note": f"{s.get('gwd') or s['task_id']}: {perr}"})
                    continue
                report["pruned"] += 1
            elif op == "extra_delete":
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/task/{s['task_id']}", "DELETE")
                if code not in (200, 204):
                    return report, (f"AZ (2) refused the extra delete ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_journal(0, s["task_id"], "extra_delete", s["name"], "",
                             s.get("snapshot"), user)
            elif op == "move":
                dest = pkg_tid.get(s["pkg_row"])
                if not dest:
                    row_ = get_row(s["pkg_row"])
                    dest = (row_.get("data") or {}).get("source_task_id") \
                        if row_ else None
                if not dest:
                    skipped.append({"row_id": s["row_id"],
                                    "note": "its package wasn't created"})
                    continue
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/task/{s['task_id']}",
                                   "PUT", {"parent": dest})
                if code != 200:
                    return report, (f"AZ (2) refused the move ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_journal(s["row_id"], s["task_id"], "parent",
                             s["old_parent"], dest, s.get("snapshot"), user)
            elif op == "rename":
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/task/{s['task_id']}",
                                   "PUT", {"name": s["new"]})
                if code != 200:
                    return report, (f"AZ (2) refused the rename ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_journal(s["row_id"], s["task_id"], "name",
                             s["old"], s["new"], s.get("snapshot"), user)
            elif op == "cf_set":
                time.sleep(_pace())
                val = s["post_value"] if "post_value" in s else s["new"]
                code, resp = _http(
                    f"{CLICKUP_API}/task/{s['task_id']}/field/{s['fid']}",
                    "POST", {"value": val})
                if code not in (200, 201):
                    return report, (f"AZ (2) refused the {s['fname']} field "
                                    f"({code}): {(resp or {}).get('_error') or resp}")
                _az2_journal(s["row_id"], s["task_id"], f"cf:{s['fname']}",
                             s["old"], s["new"], s.get("snapshot"), user)
            elif op == "due_set":
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/task/{s['task_id']}",
                                   "PUT", {"due_date": int(s["new"])})
                if code != 200:
                    return report, (f"AZ (2) refused the due date ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_journal(s["row_id"], s["task_id"], "due_date",
                             s["old"], s["new"], s.get("snapshot"), user)
            elif op == "status_set":
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/task/{s['task_id']}",
                                   "PUT", {"status": s["new"]})
                if code != 200:
                    return report, (f"AZ (2) refused the status ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_journal(s["row_id"], s["task_id"], "status",
                             s["old"], s["new"], s.get("snapshot"), user)
            elif op == "item_create":
                dest = pkg_tid.get(s["pkg_row"])
                if not dest:
                    row_ = get_row(s["pkg_row"])
                    dest = (row_.get("data") or {}).get("source_task_id") \
                        if row_ else None
                if not dest:
                    skipped.append({"row_id": s["row_id"],
                                    "note": "its package wasn't created"})
                    continue
                body = {"name": s["name"], "parent": dest}
                if s.get("status") and s["status"] in statuses:
                    body["status"] = s["status"]
                if s.get("due"):
                    body["due_date"] = int(s["due"])
                time.sleep(_pace())
                code, resp = _http(f"{CLICKUP_API}/list/{src_lid}/task",
                                   "POST", body)
                tid = (resp or {}).get("id") if code == 200 else None
                if not tid:
                    return report, (f"AZ (2) refused the product ({code}): "
                                    f"{(resp or {}).get('_error') or resp}")
                _az2_link_source(s["row_id"], tid)
                if tn_fid and s.get("gwd"):
                    _http(f"{CLICKUP_API}/task/{tid}/field/{tn_fid}",
                          "POST", {"value": s["gwd"]})
                if qty_fid and s.get("qty") is not None:
                    _http(f"{CLICKUP_API}/task/{tid}/field/{qty_fid}",
                          "POST", {"value": s["qty"]})
                if amt_fid and s.get("amount"):
                    _http(f"{CLICKUP_API}/task/{tid}/field/{amt_fid}",
                          "POST", {"value": s["amount"]})
                if name_fid and s.get("pname"):
                    okv, enc = encode_value(name_fdef, s["pname"])
                    if okv:
                        _http(f"{CLICKUP_API}/task/{tid}/field/{name_fid}",
                              "POST", {"value": enc})
                _set_extras(tid, s)
                time.sleep(_pace())
                _http(f"{CLICKUP_API}/task/{tid}/tag/{urlquote(st['tag'])}",
                      "POST", {})
                _az2_journal(s["row_id"], tid, "item_create", "", s["name"],
                             {}, user)
                s = {**s, "task_id": tid}
            done += 1
            report["steps"].append({k: v for k, v in s.items()
                                    if k != "snapshot"})
        except Exception as e:  # noqa: BLE001 - report what landed, stop clean
            return report, f"stopped after {done} step(s): {str(e)[:150]}"
    _az2_comment(src_order,
                 f"📦 Otlobly organized this order: {report['packages']} package(s)"
                 f" · {report['moves']} moved · {report['creates_item']} added"
                 f" · {report['adopts']} adopted · {report['pruned']} duplicate(s)"
                 f" removed · by {user or 'admin'} · "
                 f"{db.now_iso()[:16].replace('T', ' ')}"
                 f" (every step undoable in Otlobly)")
    return report, None


def az2_undo(push_id, user=""):
    """Revert one journalled push — CAS-guarded: AZ (2) must still hold the
    value we wrote, else the undo aborts untouched. Handles every write kind
    organize journals: status/name revert, parent move-back, and deletion of
    tasks we created (a package only once its products were moved back).
    Returns (entry, error)."""
    with db.connect() as c:
        p = c.execute("SELECT * FROM az2_pushes WHERE id=?", (push_id,)).fetchone()
    if not p:
        return None, "push not found"
    p = dict(p)
    field = p["field"]
    if p["state"] != "pushed" or \
            (field not in ("status", "name", "parent", "due_date",
                           "pkg_create", "item_create", "adopt", "extra_delete")
             and not field.startswith("cf:")):
        return None, "this entry can't be undone"
    if field == "extra_delete":
        # recreate the deleted extra from its journalled snapshot (new id —
        # comments/history don't come back; the undo restores the DATA)
        code, _t = _http(f"{CLICKUP_API}/task/{p['task_id']}")
        if code == 200:
            return None, "the task still exists — nothing to restore"
        try:
            snap = json.loads(p["snapshot_json"] or "{}")
        except ValueError:
            snap = {}
        nm = (snap.get("name") or p["old_value"] or "").strip()
        if not nm:
            return None, "no snapshot to restore from"
        body = {"name": nm}
        if snap.get("parent"):
            body["parent"] = snap["parent"]
        stn = ((snap.get("status") or {}).get("status") or "").strip()
        if stn and stn in set(status_names(None)):
            body["status"] = stn
        if str(snap.get("due_date") or "").isdigit():
            body["due_date"] = int(snap["due_date"])
        time.sleep(_pace())
        code, resp = _http(f"{CLICKUP_API}/list/{source_list_id(None)}/task",
                           "POST", body)
        if code != 200 and body.pop("parent", None):
            time.sleep(_pace())              # parent gone → recreate flat
            code, resp = _http(f"{CLICKUP_API}/list/{source_list_id(None)}/task",
                               "POST", body)
        tid = (resp or {}).get("id") if code == 200 else None
        if not tid:
            return None, (f"AZ (2) refused the recreate ({code}): "
                          f"{(resp or {}).get('_error') or resp}")
        for f_ in snap.get("custom_fields") or []:
            if f_.get("value") not in (None, "", []):
                _http(f"{CLICKUP_API}/task/{tid}/field/{f_['id']}",
                      "POST", {"value": f_["value"]})
        now = db.now_iso()
        with db.connect() as c:
            c.execute("UPDATE az2_pushes SET state='undone' WHERE id=?",
                      (push_id,))
            c.execute("""INSERT INTO az2_pushes (row_id, task_id, field,
                         old_value, new_value, ts, user, state, undo_of)
                         VALUES (?,?,?,?,?,?,?,'undo',?)""",
                      (0, tid, "extra_delete", "", nm, now, user, push_id))
        return {"id": push_id, "task_id": tid, "restored": nm}, None
    if field == "adopt":
        # local-only: forget (or restore) the link — AZ (2) itself untouched
        _az2_link_source(p["row_id"], (p["old_value"] or "").strip() or None,
                         expect=(p["new_value"] or "").strip() or None)
        now = db.now_iso()
        with db.connect() as c:
            c.execute("UPDATE az2_pushes SET state='undone' WHERE id=?",
                      (push_id,))
            c.execute("""INSERT INTO az2_pushes (row_id, task_id, field,
                         old_value, new_value, ts, user, state, undo_of)
                         VALUES (?,?,?,?,?,?,?,'undo',?)""",
                      (p["row_id"], p["task_id"], "adopt", p["new_value"],
                       p["old_value"], now, user, push_id))
        return {"id": push_id, "task_id": p["task_id"],
                "restored": p["old_value"] or "(unlinked)"}, None
    sub = "?include_subtasks=true" if field == "pkg_create" else ""
    code, task = _http(f"{CLICKUP_API}/task/{p['task_id']}{sub}")
    deleted_task = False
    if field in ("pkg_create", "item_create"):
        if code == 404:
            deleted_task = True                  # already gone — just close the entry
        elif code != 200 or not isinstance(task, dict):
            return None, f"couldn't read the AZ (2) task ({code})"
        elif field == "pkg_create" and (task.get("subtasks") or []):
            return None, ("the package still holds products in AZ (2) — undo "
                          "their moves first, then undo the package")
        if not deleted_task:
            time.sleep(_pace())
            code, resp = _http(f"{CLICKUP_API}/task/{p['task_id']}", "DELETE")
            if code not in (200, 204):
                return None, (f"AZ (2) refused the delete ({code}): "
                              f"{(resp or {}).get('_error') or resp}")
        # the local row forgets the task — unless it already adopted another
        _az2_link_source(p["row_id"], None, expect=p["task_id"])
        restored = "(deleted)"
    elif field.startswith("cf:"):
        if code != 200 or not isinstance(task, dict):
            return None, f"couldn't read the AZ (2) task ({code})"
        fdef = _sch_field_def(schema(None).get("fields") or {}, field[3:])
        fid = fdef.get("id")
        if not fid:
            return None, f"the {field[3:]!r} field is gone from the schema"
        fdef = _inline_fdef(task, fid, fdef)     # live options beat the cache
        raw = _task_cf(task, fid)
        dropdown = fdef.get("type") in ("drop_down", "labels")
        cur = _cf_display(fdef, raw) if dropdown else raw
        drifted = (cur.strip().casefold() != (p["new_value"] or "").strip().casefold()
                   if dropdown else not _num_eq(cur, p["new_value"]))
        if drifted:
            return None, (f"AZ (2) changed after this push — it now says {cur!r}, "
                          f"so the undo was aborted. Review it in ClickUp.")
        if dropdown:
            okv, back = (encode_value(fdef, p["old_value"])
                         if (p["old_value"] or "").strip() else (True, None))
            if not okv:
                return None, f"the old option {p['old_value']!r} no longer exists"
        else:
            back = p["old_value"] or ""
        time.sleep(_pace())
        code, resp = _http(f"{CLICKUP_API}/task/{p['task_id']}/field/{fid}",
                           "POST", {"value": back})
        if code not in (200, 201):
            return None, (f"AZ (2) refused the revert ({code}): "
                          f"{(resp or {}).get('_error') or resp}")
        restored = p["old_value"] or "(empty)"
    elif field == "due_date":
        if code != 200 or not isinstance(task, dict):
            return None, f"couldn't read the AZ (2) task ({code})"
        cur = str(task.get("due_date") or "")
        if not _num_eq(cur, p["new_value"]):
            return None, (f"AZ (2) changed after this push — it now says {cur!r}, "
                          f"so the undo was aborted. Review it in ClickUp.")
        old = (p["old_value"] or "").strip()
        time.sleep(_pace())
        code, resp = _http(f"{CLICKUP_API}/task/{p['task_id']}", "PUT",
                           {"due_date": int(old) if old.isdigit() else None})
        if code != 200:
            return None, (f"AZ (2) refused the revert ({code}): "
                          f"{(resp or {}).get('_error') or resp}")
        restored = old or "(cleared)"
    else:
        if code != 200 or not isinstance(task, dict):
            return None, f"couldn't read the AZ (2) task ({code})"
        cur = {"status": ((task.get("status") or {}).get("status") or ""),
               "name": (task.get("name") or ""),
               "parent": (task.get("parent") or "")}[field].strip()
        if cur != (p["new_value"] or "").strip():
            return None, (f"AZ (2) changed after this push — it now says {cur!r}, "
                          f"so the undo was aborted. Review it in ClickUp.")
        if not (p["old_value"] or "").strip():
            return None, "this entry has no previous value to restore"
        time.sleep(_pace())
        code, resp = _http(f"{CLICKUP_API}/task/{p['task_id']}", "PUT",
                           {field: p["old_value"]})
        if code != 200:
            return None, (f"AZ (2) refused the revert ({code}): "
                          f"{(resp or {}).get('_error') or resp}")
        restored = p["old_value"]
    now = db.now_iso()
    with db.connect() as c:
        c.execute("UPDATE az2_pushes SET state='undone' WHERE id=?", (push_id,))
        c.execute("""INSERT INTO az2_pushes (row_id, task_id, field, old_value,
                     new_value, ts, user, state, undo_of)
                     VALUES (?,?,?,?,?,?,?,'undo',?)""",
                  (p["row_id"], p["task_id"], field, p["new_value"],
                   p["old_value"], now, user, push_id))
        others = c.execute("SELECT COUNT(*) n FROM az2_pushes WHERE task_id=? "
                           "AND state='pushed'", (p["task_id"],)).fetchone()["n"]
    if not deleted_task and field not in ("pkg_create", "item_create"):
        _az2_comment(p["task_id"], f"↩️ Otlobly undo: {field} back to "
                                   f"'{p['old_value']}' · by {user or 'admin'}"
                                   f" · {now[:16].replace('T', ' ')}")
        if others == 0:                 # last active push undone → drop the marker
            time.sleep(_pace())
            _http(f"{CLICKUP_API}/task/{p['task_id']}/tag/"
                  f"{urlquote(_az2_settings()['tag'])}", "DELETE")
    return {"id": push_id, "task_id": p["task_id"],
            "restored": restored}, None


def az2_push_history(limit=200):
    with db.connect() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, row_id, task_id, field, old_value, new_value, ts, user, "
            "state, undo_of FROM az2_pushes ORDER BY id DESC LIMIT ?", (limit,))]
        names = {r["id"]: r["name"] for r in c.execute(
            "SELECT id, name FROM leluxe_orders")}
    for r in rows:
        r["name"] = names.get(r["row_id"]) or f"#{r['row_id']}"
    return rows


def heal_conflicts():
    """Repair malformed parked rows: sync_state='conflict' with an EMPTY
    data.conflicts list. Those rows were counted by the ⚠ chip but invisible in
    the review modal — the owner saw "N conflicts" with nothing to review.
    Healing flips them to 'synced'; that is safe because the merge fast path now
    verifies the base against AZ (2), so anything genuinely unapplied re-merges
    (and re-parks if it is a real conflict) on the very next sync."""
    fixed = 0
    with db.connect() as c:
        for r in c.execute("SELECT id, data_json FROM leluxe_orders "
                           "WHERE sync_state='conflict' AND deleted=0"):
            try:
                data = json.loads(r["data_json"] or "{}")
            except ValueError:
                data = {}
            if data.get("conflicts"):
                continue
            data.pop("conflicts", None)
            c.execute("UPDATE leluxe_orders SET data_json=?, sync_state='synced' "
                      "WHERE id=?", (json.dumps(data, ensure_ascii=False), r["id"]))
            fixed += 1
    return fixed


def list_conflicts():
    """Every row parked in a merge conflict, with its per-field diffs and the
    parent order's name (for grouping in the review panel)."""
    heal_conflicts()                 # opening the review self-repairs stragglers
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE sync_state='conflict' AND deleted=0")]
        names = {r["id"]: r["name"] for r in c.execute(
            "SELECT id, name FROM leluxe_orders")}
        parents = {r["id"]: r["parent_local_id"] for r in c.execute(
            "SELECT id, parent_local_id FROM leluxe_orders")}

    def _order_name(row):
        cur, seen = row["id"], set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            p = parents.get(cur)
            if p is None:
                break
            cur = p
        return names.get(cur, row["name"])

    out = []
    for r in rows:
        confs = r["data"].get("conflicts") or []
        if not confs:
            continue
        out.append({"row_id": r["id"], "name": r["name"], "kind": r["kind"],
                    "order_name": _order_name(r), "conflicts": confs,
                    "source_cu_updated": r["data"].get("source_cu_updated")})
    return out


def resolve_conflict(row_id, choices=None, choice=None):
    """Apply the owner's choice to a parked conflict row. `choice` ('local' /
    'remote') resolves EVERY field; `choices`={field: side} resolves a subset.
    'remote' takes AZ (2)'s value; 'local' keeps the Otlobly value. Either way
    the base advances to AZ (2)'s value so the field won't re-flag. When the last
    conflict clears, the row flips to 'dirty' and the pusher mirrors it to the
    working copy (never AZ 2). Returns (row, error)."""
    choices = choices or {}
    remaining = None
    with db.connect() as c:
        r = c.execute("SELECT * FROM leluxe_orders WHERE id=? AND deleted=0",
                      (row_id,)).fetchone()
        if not r:
            return None, "row not found"
        d = _row(r)
        data = d["data"]
        confs = data.get("conflicts") or []
        if not confs:
            return None, "no conflict to resolve"
        base = data.get("source_base") or {}
        cols_new, remaining = {}, []
        for cf in confs:
            field = cf["field"]
            pick = choice or choices.get(field)
            if pick not in ("local", "remote"):
                remaining.append(cf)
                continue
            if pick == "remote":
                _apply_value(data, cols_new, field, cf.get("remote"))
            _base_set(base, field, cf.get("remote"))    # advance base either way
        data["source_base"] = base
        if remaining:
            data["conflicts"] = remaining
            _write_row(c, row_id, cols_new, data, sync_state="conflict",
                       log_source="app")
        else:
            data.pop("conflicts", None)
            _write_row(c, row_id, cols_new, data, sync_state="dirty",
                       reset_sync=True, log_source="app")
    if not remaining:
        kick()
        # an approved Tracking Number lands NOW — the product follows its GWD
        # right away instead of waiting for the next full sync
        if d["kind"] == "item" and any(
                str(cf.get("field") or "").strip().lower() == "tracking number"
                for cf in confs):
            top = _top_order_of(get_row(row_id))
            if top:
                regroup_order(top["id"])
    return get_row(row_id), None


# --------------------------------------------------------------------------- #
# Live GAASH + Gerizim tracking — same carriers/logic as the Purchases page.
# Display-only enrichment (never pushed to ClickUp), keyed on tracking_number.
# Works on ANY row that carries a tracking number: packages (3-tier), or the
# order/item rows themselves on a flat mirror.
# --------------------------------------------------------------------------- #
def _row_tracking(row):
    """A row's OWN tracking number: data.tracking_number (packages) or the
    'Tracking Number' custom field (flat orders/items), case-blind."""
    tn = row["data"].get("tracking_number")
    if not tn:
        for k, v in (row["data"].get("fields") or {}).items():
            if k.strip().lower() == "tracking number":
                tn = v
                break
    return str(tn or "").strip()


def _eff_tn_map(rows):
    """row id → the PARCEL number that row belongs to. A product's GWD lives on
    its PACKAGE row in ClickUp, not on the product, so a child with no number of
    its own inherits its parcel's — the same rule the board already renders
    (muted, "from the package"). Without it a parcel's products were invisible
    to the sweep: one lookup happened, but its answer reached only the rows that
    happened to carry the number, and the rest sat blank forever.

    Derived per sweep and NEVER written back — an inherited number stored in
    data would push a phantom Tracking Number to ClickUp on the next sync."""
    by_id = {r["id"]: r for r in rows}
    out = {}
    for r in rows:
        tn = _row_tracking(r)
        if not tn:
            p = by_id.get(r.get("parent_local_id"))
            tn = _row_tracking(p) if p else ""
        out[r["id"]] = str(tn or "").strip()
    return out


# ── GASH STATUS ← live tracking stage ───────────────────────────────────────
# Owner's rule: the ClickUp task STATUS is his to change manually — automation
# may only touch the GASH STATUS dropdown, mirroring the parcel's live stage
# (Gerizim last-mile when present, else the GAASH leg). Mapping is by option
# NAME and only to options that EXIST on the working list (add a missing one
# in ClickUp UI, then Discover); first candidate that exists wins, so adding
# "تم التسليم" upgrades the delivered mapping.
GASH_FIELD = "gash status"
GERIZIM_BUCKET_OPTIONS = {
    # Arabic first (mirrors the parcel app); English kept as a fallback so the
    # sync keeps working until Qais renames/adds the Arabic options in ClickUp.
    "office":    ["في مكتب جرزيم", "Picked up by Gerizim"],
    "sms":       ["تم إرسال SMS", "تم الارسال", "SMS SENT — AWAITING PICKUP", "SMS SENT"],
    "pickup":    ["جاهز للاستلام", "READY FOR PICKUP"],
    "out":       ["خارج للتوصيل", "OUT FOR DELIVERY"],
    "delivered": ["تم التسليم", "GERZIM DELIVERED"],
}
GAASH_BUCKET_OPTIONS = {
    "transit":   ["STILL NOT ARRIVED"],
    "arrived":   ["ARIIVED Destination"],
    "cleared":   ["CLEARED GASH"],
    "delivered": ["BRACHA DELIVERED"],
    # "customs" maps only when the live text asks for the customer ID (below)
}
# Forward-only guard: a live feed that lags a manual entry (GAASH still says
# "arrived" after he already handled the ID request) must never move the
# field BACKWARD. Keys are strip/casefold; unknown current values are treated
# as rank None (replaceable — same as empty).
GASH_STAGE_RANK = {
    "still not arrived": 0,
    "ariived destination": 1,
    "customer id": 2,
    "documents sent": 2,
    "sent but still diidn't clear": 2,
    "moc - palestinian authority": 3,
    "cleared gash": 4,
    "bracha delivered": 5,
    "picked up by gerizim": 6, "في مكتب جرزيم": 6,
    "تم الارسال": 7, "تم إرسال sms": 7, "sms sent — awaiting pickup": 7, "sms sent": 7,
    "جاهز للاستلام": 7, "ready for pickup": 7, "خارج للتوصيل": 7, "out for delivery": 7,
    "gerzim delivered": 8,
    "تم التسليم": 8,
}


def _gash_field_def(config=None):
    """The working list's GASH STATUS dropdown (exact key + def), case-blind."""
    for k, v in (schema(config).get("fields") or {}).items():
        if k.strip().lower() == GASH_FIELD and v.get("type") == "drop_down":
            return k, v
    return None, None


def _gash_option_for(candidates, fdef):
    """First candidate option that exists on the list, in ClickUp's exact
    spelling — None if the stage has no matching option (skip, never guess)."""
    opts = {str(o.get("name") or "").strip().casefold(): o.get("name")
            for o in (fdef.get("options") or [])}
    for cand in candidates or []:
        hit = opts.get(cand.strip().casefold())
        if hit:
            return hit
    return None


def _gash_rank(name):
    return GASH_STAGE_RANK.get(str(name or "").strip().casefold())


def _gash_target(row, fdef):
    """The GASH STATUS option this row SHOULD carry per live tracking —
    Gerizim stage first (last mile = later leg), else the GAASH stage."""
    d = row["data"]
    gz = d.get("gerizim_status") or {}
    if isinstance(gz, dict) and gz.get("bucket"):
        hit = _gash_option_for(GERIZIM_BUCKET_OPTIONS.get(gz["bucket"], []), fdef)
        if hit:
            return hit
    ts = d.get("tracking_status") or {}
    if not isinstance(ts, dict) or not ts.get("bucket"):
        return None
    if ts["bucket"] == "customs":
        txt = str(ts.get("text") or "")
        cands = [" customer ID"] if "customer id" in txt.lower() else []
    else:
        cands = GAASH_BUCKET_OPTIONS.get(ts["bucket"], [])
    return _gash_option_for(cands, fdef)


def _queue_gash_status(row, fkey, target):
    """Set fields[GASH STATUS]=target and queue a FIELD-ONLY push.

    Returns the transition `{row_id, tracking, name, old, new}` when one was
    queued, else None (already up to date / row gone). It returns the pair
    rather than a bare True because this is the only place that still knows
    what the value WAS — the change log and the "what moved" popup are built
    from it, and a caller that only counts throws that away."""
    with db.connect() as c:
        r = c.execute("SELECT sync_state, data_json FROM leluxe_orders "
                      "WHERE id=? AND deleted=0", (row["id"],)).fetchone()
        if not r:
            return None
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        fields = d.setdefault("fields", {})
        key = next((k for k in fields if k.strip().lower() == GASH_FIELD), fkey)
        old = str(fields.get(key) or "").strip()
        if old == target:
            return None
        fields[key] = target
        # Only a clean row gets the field-only marker; a real edit in flight
        # (dirty/pushing/error) keeps its FULL push, which now carries the field.
        if r["sync_state"] == "synced" or d.get("pending_fields"):
            pf = set(d.get("pending_fields") or [])
            pf.add(key)
            d["pending_fields"] = sorted(pf)
        c.execute("""UPDATE leluxe_orders SET data_json=?, updated_at=?,
                     sync_state='dirty', sync_error=NULL, sync_attempts=0
                     WHERE id=?""",
                  (json.dumps(d, ensure_ascii=False), db.now_iso(), row["id"]))
    try:
        import activity
        activity.log("set", "leluxe", row["id"],
                     row.get("name") or f"#{row['id']}",
                     detail=f"gash status → {target} (from Gerizim) "
                            f"→ syncing to ClickUp")
    except Exception:  # noqa: BLE001 — logging must never block the sync
        pass
    code = next((str(v) for k, v in (row["data"].get("fields") or {}).items()
                 if k.strip().upper() == "NAME" and v), "")
    return {"row_id": row["id"], "tracking": _row_tracking(row),
            "name": row.get("name") or "", "code": code, "old": old,
            "new": target, "status": row.get("status") or "", "store": "leluxe"}


def apply_gash_status(only=None, config=None):
    """Mirror each tracked row's live stage (Gerizim, else GAASH) into the
    GASH STATUS dropdown. Idempotent (writes only differences), FORWARD-ONLY
    (never downgrades a manual entry that's ahead of a lagging feed); `only`
    scopes to one GWD. Runs over ALL stored enrichment, so already-delivered
    parcels (which the network loop skips) still catch up.

    Returns the LIST of transitions queued (each `{row_id, tracking, name,
    code, old, new, status, store}`) — `len()` is the old count."""
    config = config or cfg.load()
    fkey, fdef = _gash_field_def(config)
    if not fdef:
        return []
    import tracking
    only = tracking.clean_tracking(only or "") or None
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    eff = _eff_tn_map(rows)          # same parcel grouping the sweep uses
    queued = []
    for row in rows:
        if only and tracking.clean_tracking(eff.get(row["id"]) or "") != only:
            continue
        target = _gash_target(row, fdef)
        if not target:
            continue
        cur = next((v for k, v in (row["data"].get("fields") or {}).items()
                    if k.strip().lower() == GASH_FIELD), None)
        cur_rank, tgt_rank = _gash_rank(cur), _gash_rank(target)
        # >= (not >) so a same-stage upgrade (GERZIM DELIVERED → تم التسليم)
        # still applies; equal VALUES are dropped inside _queue_gash_status.
        if cur_rank is not None and (tgt_rank is None or tgt_rank < cur_rank):
            continue
        moved = _queue_gash_status(row, fkey, target)
        if moved:
            queued.append(moved)
    if queued:
        kick()
    return queued


# ── Bulk-sweep skip rules (owner's 2026-08-05 ask) ──────────────────────────
# The bulk sweep must not spend carrier calls on parcels whose journey is
# over — received at the office / sent / delivered to the customer, or already
# in Gerizim's last-mile hands. Any bucket key below means "the parcel reached
# Gerizim"; gerizim.NOT_FOUND is stored as a bare STRING (never a dict) so it
# can never match.
GZ_ARRIVED = frozenset(GERIZIM_BUCKET_OPTIONS)

# Canonical GAASH number shape — every real number on the board is GWD + 9
# digits. Only ever used for an ADVISORY warn (a typo'd digit count is the
# usual reason GAASH "has no record"); never blocks a lookup.
GWD_CANON = re.compile(r"GWD\d{9}$")

# GAASH's own namespace. The board also holds UPS numbers (1Z…) and typed-in
# notes ("under", "-", "NO FUNDS") in the tracking slot; GAASH can never answer
# for those, so they never reach a done status and were re-fetched on every
# sweep forever. Bulk skips them and REPORTS them; only=/force still check
# anything the owner points the 🔎 at on purpose. Deliberately a PREFIX test,
# not GWD_CANON's exact shape — a typo'd digit count IS ours to ask about, and
# GWD_CANON already warns about it in the results.
GWD_LOOKUP = re.compile(r"\s*GWD", re.I)

# PKG_STATUS (top of file) is the parcel-container marker — a SHAPE, not a
# stage. Counted as a live workflow status it kept 44 finished parcels in the
# sweep (54 skipped where 98 should have been), because a done parcel's own 📦
# row always disagreed with its products.


def _entry_status(config=None):
    """The list's ENTRY status (ClickUp type "open" — "order number").
    ClickUp auto-stamps it on every pushed row, so it counts as UNSET —
    server twin of the client's lxKEffStatus rule."""
    sts = schema(config).get("statuses") or []
    ent = next((s.get("status") for s in sts if s.get("type") == "open"),
               (sts[0].get("status") if sts else ""))
    return str(ent or "").strip().lower()


def _stop_statuses(config=None):
    """ClickUp statuses that END carrier checking (the box reached us or the
    customer, or the task closed). Reuses the Settings-editable alerts stop
    list — one vocabulary, one editor — plus "picked up by ger" (Gerizim has
    it), added HERE only so the alerts rules keep their own semantics."""
    import alerts
    return alerts.stop_statuses(config) | {"picked up by ger"}


def _structural_statuses(config=None):
    """Statuses that say what a row IS, not where its parcel got to. Organize
    stamps 'package' on every 📦 container, so counting it as a live stage made
    a finished parcel look mixed forever. Settings-overridable the same way the
    stop list is (leluxe.structural_statuses)."""
    lst = cfg.get(config or cfg.load(), "leluxe.structural_statuses",
                  [PKG_STATUS])
    return {str(s).strip().lower() for s in lst if str(s).strip()}


def _skip_gwds(rows, config=None):
    """GWDs the BULK sweep must not spend carrier calls on:
    (a) any row shows the parcel AT Gerizim (dict bucket in GZ_ARRIVED),
    (b) every REAL status across the GWD's rows (""/entry/'package' = unset) is
        in the stop set — mixed statuses (one product done, one live) keep
        checking,
    (c) any row's GASH STATUS field says DELIVERED (GERZIM/BRACHA DELIVERED —
        authoritative on old rows, same rule as gaash_mail's candidates).

    Rows are grouped by their EFFECTIVE parcel number, so a product inherits
    its 📦 parcel's GWD and one verdict covers every copy of the parcel."""
    import tracking
    entry, stop = _entry_status(config), _stop_statuses(config)
    unset = _structural_statuses(config) | {entry}
    eff = _eff_tn_map(rows)
    skip, real = set(), {}
    for row in rows:
        tn = tracking.clean_tracking(eff.get(row["id"]) or "")
        if not tn:
            continue
        d = row["data"]
        gz = d.get("gerizim_status")
        if isinstance(gz, dict) and gz.get("bucket") in GZ_ARRIVED:
            skip.add(tn)
        gash = next((v for k, v in (d.get("fields") or {}).items()
                     if k.strip().lower() == GASH_FIELD), None)
        if gash and re.search(r"delivered", str(gash), re.I):
            skip.add(tn)
        st = str(row.get("status") or "").strip().lower()
        if st and st not in unset:
            real.setdefault(tn, set()).add(st)
    skip.update(tn for tn, sts in real.items() if sts <= stop)
    return skip


def store_docs_state(tn, docs):
    """Persist one parcel's GAASH docs banner onto every row that rides the
    GWD (same effective-number fan-out as refresh_tracking), so a manual ⋯
    re-check survives the next board reload.

    docs=None means the LOOKUP FAILED: stamp the attempt only and keep the last
    known state — a network hiccup must never erase a real 'upload asked'. The
    stamp still matters: an unstamped attempt made a pressed button look like
    nothing happened."""
    import tracking
    tn = tracking.clean_tracking(tn or "")
    if not tn:
        return 0
    stamp = (docs or {}).get("checked") or datetime.now().astimezone().isoformat()
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    eff = _eff_tn_map(rows)
    hit = 0
    with db.connect() as c:
        for row in rows:
            if tracking.clean_tracking(eff.get(row["id"]) or "") != tn:
                continue
            d = row["data"]
            if isinstance(docs, dict):
                d["docs_state"] = docs
            d["docs_checked"] = stamp
            c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), row["id"]))
            hit += 1
    return hit


def refresh_tracking(batch=5, only=None, force=False, config=None):
    """One bounded batch of live GAASH + Gerizim checks. Skips parcels that
    need no more carrier calls (_skip_gwds) UNLESS only/force is set: `only`
    keeps gaash_mail's enrolled threads fresh, `force` is the per-row 🔎
    "check NOW". Every ATTEMPTED fetch stamps tracking_checked (success or
    not) so failing GWDs rotate out on the 30-min TTL instead of pinning the
    head of the queue forever. `results` carries one honest per-GWD verdict
    (found / carrier-has-no-record / error text) so the 🔎 UI can tell the
    truth instead of toasting "refreshed" at a blank row."""
    import tracking
    import gerizim
    from datetime import timedelta
    config = config or cfg.load()
    only = tracking.clean_tracking(only or "") or None
    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=30)
    dl_cutoff = now - timedelta(days=14)
    docs_cutoff = now - timedelta(days=1)   # yellow→blue flips fast once docs land
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    skip = set() if (only or force) else _skip_gwds(rows, config)
    eff = _eff_tn_map(rows)
    bulk = not (only or force)
    work, excluded = {}, {}
    for row in rows:
        # the row's EFFECTIVE parcel number — a product with no number of its
        # own rides its 📦 parcel's, so ONE lookup lands on every copy
        tn = tracking.clean_tracking(eff.get(row["id"]) or "")
        if not tn or (only and tn != only) or tn in skip:
            continue
        if bulk and not GWD_LOOKUP.match(tn):
            excluded[tn] = "not a GAASH number — GAASH can't answer for it"
            continue
        d = row["data"]
        gz = d.get("gerizim_status") or {}
        if isinstance(gz, dict) and gz.get("bucket") == "delivered":
            continue
        ts = d.get("tracking_status") or {}
        need = force
        if not need:
            try:
                need = datetime.fromisoformat(d.get("tracking_checked") or "") <= cutoff
            except (ValueError, TypeError):
                need = True
        # only a DICT is real Gerizim presence — the stored "notfound" STRING
        # must not pin eligible False forever (it used to block gaash_deadline
        # from ever being fetched again, even with force)
        eligible = (ts.get("bucket") if isinstance(ts, dict) else None) \
            not in ("cleared", "delivered") and not (isinstance(gz, dict) and gz)
        need_dl = need_docs = False
        if eligible:
            try:
                need_dl = force or not d.get("gaash_deadline") or \
                    datetime.fromisoformat(d.get("gaash_deadline_checked") or "") <= dl_cutoff
            except (ValueError, TypeError):
                need_dl = True
            try:
                need_docs = force or \
                    datetime.fromisoformat(d.get("docs_checked") or "") <= docs_cutoff
            except (ValueError, TypeError):
                need_docs = True
        if need or need_dl or need_docs:
            work.setdefault(tn, []).append((row["id"], need, need_dl, need_docs))
    todo = list(work.items())[:batch]
    session = None
    results = []
    docs_done, docs_cap = 0, 3   # docs page is SLOW (~10-25s) — keep the batch inside gunicorn's 120s
    for tn, targets in todo:
        want_track = any(t[1] for t in targets)
        want_dl = any(t[2] for t in targets)
        want_docs = any(t[3] for t in targets) and docs_done < docs_cap
        st = gz_new = deadline = docs = gaash_err = None
        if want_track:
            data = None
            try:
                session = session or tracking.get_session()
                data = tracking.fetch_one(tn, *session)
                st = tracking.latest_status(data)
            except Exception as e:  # noqa: BLE001 — get_session scrape failed; one bad number must not stall the batch
                st, data = None, None
                gaash_err = f"GAASH: {e}"[:160]
            if isinstance(data, dict) and data.get("_error"):
                # fetch_one never raises — network/HTTP failures come back as
                # data. Distinguish them from an honest "no record" (found
                # False + error None), so the UI can say which one happened.
                gaash_err = f"GAASH: {data['_error']}"[:160]
            if st:
                try:
                    # feed the shared last-known cache — the public tracking widget
                    # serves customers straight from it (cache-first, best-effort)
                    tracking.cache_put_events(tn, tracking.events_from_raw(data))
                except Exception:  # noqa: BLE001 — a cache hiccup must not lose the status
                    pass
            try:
                gz_new = gerizim.track(tn)
            except Exception:  # noqa: BLE001
                gz_new = None
        if want_dl:
            try:
                deadline = tracking.ops_deadline(tn)
            except Exception:  # noqa: BLE001
                deadline = None
        if want_docs:
            docs_done += 1
            try:
                docs = tracking.docs_status(tn)
            except Exception:  # noqa: BLE001
                docs = None
        results.append({
            "tracking": tn,
            "found": bool(st),
            "bucket": (st or {}).get("bucket"),
            "text": (st or {}).get("text") or (st or {}).get("label"),
            "error": gaash_err,
            "gz": gz_new.get("bucket") if isinstance(gz_new, dict) else gz_new,
            "deadline": deadline,
            "docs": (docs or {}).get("state"),
            "warn": ("unusual GWD length — usually GWD + 9 digits"
                     if tn.upper().startswith("GWD")
                     and not GWD_CANON.match(tn.upper()) else None),
        })
        stamp = now.isoformat()
        for row_id, *_flags in targets:
            with db.connect() as c:
                r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                              (row_id,)).fetchone()
                if not r:
                    continue
                try:
                    d = json.loads(r["data_json"] or "{}")
                except ValueError:
                    d = {}
                if st:
                    d["tracking_status"] = st
                if want_track:
                    # stamp the ATTEMPT, success or not — unstamped failures
                    # used to pin list(work)[:batch] and re-run every POST;
                    # the 30-min TTL now rotates them out.
                    d["tracking_checked"] = stamp
                if gz_new and (isinstance(gz_new, dict)
                               or not isinstance(d.get("gerizim_status"), dict)):
                    # a dict always wins; the "notfound" marker only lands
                    # where no real stage was ever stored (a transient Gerizim
                    # 404 must never erase a known stage)
                    d["gerizim_status"] = gz_new
                if want_dl:
                    d["gaash_deadline_checked"] = stamp
                    if deadline:
                        d["gaash_deadline"] = deadline
                if want_docs:
                    d["docs_checked"] = stamp
                    if docs:
                        d["docs_state"] = docs
                c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                          (json.dumps(d, ensure_ascii=False), row_id))
        time.sleep(_pace())
    # after storing fresh Gerizim stages, mirror them into the GASH STATUS field
    applied = apply_gash_status(only=only, config=config)
    return {"checked": len(todo), "remaining": max(0, len(work) - len(todo)),
            "skipped": len(skip), "gash_applied": len(applied), "changes": applied,
            "results": results,
            # never folded into checked/remaining — the UI's stall detector
            # keys off remaining, and these never enter the queue at all
            "excluded": [{"tracking": t, "reason": r}
                         for t, r in sorted(excluded.items())]}


# --------------------------------------------------------------------------- #
# Amazon product image by ASIN — reuse the Catalog's import_product fetch.
# --------------------------------------------------------------------------- #
def _item_asin(row):
    f = row["data"].get("fields") or {}
    return str(f.get("ASIN") or f.get("ASIN DROP DOWN") or "").strip()


def set_item_asin(row_id, asin):
    """Write a just-typed ASIN into the item's 'ASIN' text field and mark the
    row dirty (so it also syncs to ClickUp — the ASIN is genuinely missing
    there). Returns the row or None. Called by the editor's fetch-by-ASIN flow."""
    asin = str(asin or "").strip()
    if not asin:
        return get_row(row_id)
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=? AND deleted=0",
                      (row_id,)).fetchone()
        if not r:
            return None
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        f = d.setdefault("fields", {})
        key = next((k for k in f if k.strip().lower() == "asin"), "ASIN")
        if str(f.get(key) or "").strip() == asin:
            return get_row(row_id)          # unchanged — no dirty
        f[key] = asin
        d.pop("pending_fields", None)       # a real edit → full push takes over
        c.execute("""UPDATE leluxe_orders SET data_json=?, updated_at=?,
                     sync_state='dirty', sync_error=NULL, sync_attempts=0
                     WHERE id=?""",
                  (json.dumps(d, ensure_ascii=False), db.now_iso(), row_id))
    kick()
    return get_row(row_id)


def fetch_item_image(row_id, config=None, force=False, asin=None):
    """Fetch + cache the Amazon photo for one item by its ASIN. Returns the
    image URL (or None). Skips if already cached for the same ASIN. When `asin`
    is given, it's saved to the item first (editor's one-click add-ASIN flow)."""
    import amazon_import
    config = config or cfg.load()
    if asin:
        set_item_asin(row_id, asin)
        force = True                        # a new/edited ASIN → always refetch
    row = get_row(row_id)
    if not row or row["kind"] != "item":
        return None
    asin = _item_asin(row)
    if not asin:
        return None
    if not force and row["data"].get("image") and row["data"].get("image_asin") == asin:
        return row["data"]["image"]
    try:
        info = amazon_import.import_product(asin, config, refresh=force) or {}
    except Exception:  # noqa: BLE001
        info = {}
    img = info.get("image")
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return None
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        d["image_asin"] = asin
        if img:
            d["image"] = img
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))
    return img


def fetch_item_images(batch=6, config=None):
    """Batched image backfill for items with an ASIN but no cached photo
    (resumable: the client loops until remaining == 0)."""
    config = config or cfg.load()
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE kind='item' AND deleted=0")]
    todo = [r for r in rows if _item_asin(r)
            and not (r["data"].get("image") and r["data"].get("image_asin") == _item_asin(r))]
    done = 0
    for r in todo[:batch]:
        fetch_item_image(r["id"], config)
        done += 1
        time.sleep(_pace())
    return {"done": done, "remaining": max(0, len(todo) - done)}


# --------------------------------------------------------------------------- #
# Push (local → ClickUp) — background worker, alerts.py pattern
# --------------------------------------------------------------------------- #
_KICK = threading.Event()
_started = False


def kick():
    _KICK.set()


def _parent_task_id(row):
    if row.get("parent_task_id"):
        return row["parent_task_id"]
    if row.get("parent_local_id"):
        p = get_row(row["parent_local_id"])
        return (p or {}).get("clickup_task_id")
    return None


def _persist_task_id(row_id, task_id, parent_task_id=None):
    """The duplicate-safety invariant: the created task id is written BEFORE any
    field/tag/attachment call, so a crash retries on the SAME task."""
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET clickup_task_id=?, "
                  "parent_task_id=COALESCE(?, parent_task_id) WHERE id=?",
                  (task_id, parent_task_id, row_id))


def _finish(row_id, pushed, images, error=None, attempts=0):
    """Persist push results without clobbering an edit made mid-push: merge only
    pushed/images into the CURRENT data_json, and flip pushing→synced only if
    nobody re-dirtied the row meanwhile."""
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        if pushed is not None:
            d["pushed"] = pushed
        if images is not None:
            local = {i.get("file"): i for i in d.get("images") or [] if i.get("file")}
            merged = []
            for img in images:
                merged.append(img)
                local.pop(img.get("file"), None)
            merged.extend(local.values())      # files added mid-push survive
            d["images"] = merged
        if error:
            c.execute("""UPDATE leluxe_orders SET data_json=?, sync_state='error',
                         sync_error=?, sync_attempts=? WHERE id=?
                         AND sync_state='pushing'""",
                      (json.dumps(d, ensure_ascii=False), error[:400],
                       attempts, row_id))
        else:
            c.execute("""UPDATE leluxe_orders SET data_json=?, sync_error=NULL,
                         sync_attempts=0,
                         sync_state=CASE WHEN sync_state='pushing' THEN 'synced'
                                         ELSE sync_state END
                         WHERE id=?""",
                      (json.dumps(d, ensure_ascii=False), row_id))


def _clear_pending(row_id, names=None):
    """Drop (some of) a row's field-only push markers, merged into the CURRENT
    data_json so a marker queued mid-push survives. names=None clears all."""
    with db.connect() as c:
        r = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r:
            return
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        if "pending_fields" not in d:
            return
        left = [] if names is None else \
            [f for f in d.get("pending_fields") or [] if f not in names]
        if left:
            d["pending_fields"] = left
        else:
            d.pop("pending_fields", None)
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))


def push_one(row, config=None):
    """Mirror one claimed row to ClickUp. Returns (ok, error)."""
    config = config or cfg.load()
    lid = list_id(config)
    sch = schema(config)
    fields_def = sch.get("fields") or {}
    d = row["data"]
    pushed = dict(d.get("pushed") or {})
    desc = d.get("description") or ""
    tags = list(d.get("tags") or [])
    tid = row.get("clickup_task_id")
    errors = []

    is_child = row["kind"] not in TOP_KINDS
    if is_child and not _parent_task_id(row):
        return False, "parent not pushed yet"

    # ── field-only sync (queued by the tracking mirror): touch ONLY those
    # custom fields. The core PUT (name/STATUS/due/description), tags and
    # attachments are all skipped, so a status Qais set manually in ClickUp
    # can never be reverted by a stale local mirror.
    pending = [f for f in (d.get("pending_fields") or [])
               if f in (d.get("fields") or {})]
    if tid and pending:
        pushed_fields = dict(pushed.get("fields") or {})
        for fname in pending:
            val = d["fields"][fname]
            fdef = fields_def.get(fname)
            if not fdef:
                continue               # field not on the working list — skip
            ok, enc = encode_value(fdef, val)
            if not ok:
                continue               # option missing here — syncs later
            st, resp = _http(f"{CLICKUP_API}/task/{tid}/field/{fdef['id']}",
                             "POST", {"value": enc})
            if st != 200:
                errors.append(f"{fname}: set failed ({st})")
                continue
            pushed_fields[fname] = val
            time.sleep(_pace())
        pushed["fields"] = pushed_fields
        if errors:                     # keep the marker → retry stays field-only
            _finish(row["id"], pushed, None, error="; ".join(errors),
                    attempts=row.get("sync_attempts", 0) + 1)
            return False, "; ".join(errors)
        _clear_pending(row["id"], pending)
        _finish(row["id"], pushed, None)
        return True, None

    # only send a status the working list actually defines — otherwise ClickUp
    # 400s the whole create. On a true AZ (2) duplicate every status exists; a
    # partial/scratch list just falls back to its default status (soft, not an error).
    known_status = set(status_names(config))
    ok_status = row.get("status") if (not known_status
                                      or row.get("status") in known_status) else None

    if not tid:                                            # ── create
        body = {"name": row["name"], "markdown_description": desc}
        if ok_status:
            body["status"] = ok_status
        if row.get("due_date"):
            body["due_date"] = int(row["due_date"])
        if tags:
            body["tags"] = tags
        parent_tid = _parent_task_id(row) if is_child else None
        if parent_tid:
            body["parent"] = parent_tid
        st, resp = _http(f"{CLICKUP_API}/list/{lid}/task", "POST", body)
        if st != 200 or not (resp or {}).get("id"):
            return False, f"create failed ({st}): {(resp or {}).get('_error') or resp}"
        tid = resp["id"]
        _persist_task_id(row["id"], tid, parent_tid)
        pushed.update({"name": row["name"], "status": row.get("status"),
                       "due_date": row.get("due_date"), "description": desc,
                       "tags": list(tags), "parent": parent_tid})
        time.sleep(_pace())
    else:                                                  # ── update core
        core_now = {"name": row["name"], "status": row.get("status"),
                    "due_date": row.get("due_date"), "description": desc}
        core_pushed = {k: pushed.get(k) for k in core_now}
        if core_now != core_pushed:
            body = {"name": row["name"], "markdown_description": desc}
            if ok_status:
                body["status"] = ok_status
            if row.get("due_date"):
                body["due_date"] = int(row["due_date"])
            st, resp = _http(f"{CLICKUP_API}/task/{tid}", "PUT", body)
            if st != 200:
                return False, f"update failed ({st}): {(resp or {}).get('_error') or resp}"
            pushed.update(core_now)
            time.sleep(_pace())
        # tag diff (create path sets tags in the POST body)
        old_tags = set(pushed.get("tags") or [])
        for t in [t for t in tags if t not in old_tags]:
            st, resp = _http(f"{CLICKUP_API}/task/{tid}/tag/{urlquote(t)}",
                             "POST", {})
            if st not in (200, 201):
                errors.append(f"tag +{t} ({st})")
            time.sleep(_pace())
        for t in [t for t in old_tags if t not in tags]:
            st, resp = _http(f"{CLICKUP_API}/task/{tid}/tag/{urlquote(t)}",
                             "DELETE")
            if st not in (200, 204):
                errors.append(f"tag -{t} ({st})")
            time.sleep(_pace())
        pushed["tags"] = list(tags)
        # ── re-parent (product moved to another package) ──
        # ClickUp's Update-Task `parent` moves a subtask. Legacy rows predate the
        # snapshot key, so adopt their current parent as the baseline (no PUT);
        # only a genuine change fires the move. A failure fails the row (retryable)
        # rather than silently diverging the boards.
        if is_child:
            cur_parent = _parent_task_id(row)
            old_parent = pushed.get("parent")
            if old_parent is None:
                pushed["parent"] = cur_parent
            elif cur_parent and cur_parent != old_parent:
                st, resp = _http(f"{CLICKUP_API}/task/{tid}", "PUT",
                                 {"parent": cur_parent})
                if st != 200:
                    return False, f"move failed ({st}): {(resp or {}).get('_error') or resp}"
                pushed["parent"] = cur_parent
                time.sleep(_pace())

    # ── custom fields (only the changed ones) ──
    # A field the working list can't represent (missing field, or a dropdown
    # value that isn't one of its options) is SOFT-skipped, not a row failure:
    # on a true AZ (2) duplicate everything matches, and on a partial list we'd
    # rather sync the task than block it. It's left un-pushed so it syncs later
    # if the option is added. Only a real HTTP error fails the row.
    pushed_fields = dict(pushed.get("fields") or {})
    for fname, val in (d.get("fields") or {}).items():
        if pushed_fields.get(fname) == val:
            continue
        fdef = fields_def.get(fname)
        if not fdef:
            continue                       # field not on the working list — skip
        ok, enc = encode_value(fdef, val)
        if not ok:
            continue                       # value not representable here — skip
        st, resp = _http(f"{CLICKUP_API}/task/{tid}/field/{fdef['id']}",
                         "POST", {"value": enc})
        if st != 200:
            errors.append(f"{fname}: set failed ({st})")
            continue
        pushed_fields[fname] = val
        time.sleep(_pace())
    pushed["fields"] = pushed_fields

    # ── attachments (new local images → upload + embed in description) ──
    images = [dict(i) for i in d.get("images") or []]
    desc_added = ""
    for img in images:
        if img.get("uploaded") or not img.get("file"):
            continue
        path = IMAGE_DIR / img["file"]
        if not path.exists():
            img["uploaded"] = True             # file vanished — drop silently
            continue
        st, resp = _http_multipart(f"{CLICKUP_API}/task/{tid}/attachment",
                                   img["file"], path.read_bytes())
        if st != 200 or not (resp or {}).get("url"):
            errors.append(f"image {img['file']}: upload failed ({st})")
            continue
        img["uploaded"] = True
        img["url"] = resp["url"]
        desc_added += f"\n![]({resp['url']})"
        time.sleep(_pace())
    if desc_added:
        new_desc = (pushed.get("description") or desc) + desc_added
        st, resp = _http(f"{CLICKUP_API}/task/{tid}", "PUT",
                         {"markdown_description": new_desc})
        if st == 200:
            pushed["description"] = new_desc
            _sync_local_description(row["id"], new_desc)
        else:
            errors.append(f"description embed failed ({st})")

    if errors:
        _finish(row["id"], pushed, images,
                error="; ".join(errors), attempts=row.get("sync_attempts", 0) + 1)
        return False, "; ".join(errors)
    _clear_pending(row["id"])          # a successful FULL push covers everything
    _finish(row["id"], pushed, images)
    return True, None


def _sync_local_description(row_id, desc):
    """After embedding uploaded images, keep the local description identical to
    ClickUp's — unless the user edited it mid-push (their text wins; the embeds
    already live in ClickUp either way)."""
    with db.connect() as c:
        r = c.execute("SELECT sync_state, data_json FROM leluxe_orders WHERE id=?",
                      (row_id,)).fetchone()
        if not r or r["sync_state"] != "pushing":
            return
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        d["description"] = desc
        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                  (json.dumps(d, ensure_ascii=False), row_id))


def run_push_pass(limit=20, config=None):
    """One sweep: recover stale claims, retry errors (with backoff), then claim
    and push up to `limit` dirty rows — parents before items, new before old.
    Duplicate-safe under two gunicorn workers: the claim UPDATE is atomic and
    only one worker's rowcount==1."""
    if push_disabled():
        return {"skipped": "LELUXE_PUSH_DISABLED"}
    config = config or cfg.load()
    if not ready(config):
        return {"skipped": "not configured"}
    if list_id(config) and list_id(config) == source_list_id(config):
        return {"skipped": "working list must differ from AZ (2)"}  # never write the source
    with db.connect() as c:
        # stale pushing claims (worker died mid-push) back to dirty after 10 min
        c.execute("""UPDATE leluxe_orders SET sync_state='dirty'
                     WHERE sync_state='pushing' AND sync_claimed_at IS NOT NULL
                     AND sync_claimed_at < datetime('now', '-10 minutes')""")
        # retry errored rows: first 3 attempts every pass, then every 30 min,
        # give up after 10 (manual Retry resets)
        c.execute("""UPDATE leluxe_orders SET sync_state='dirty'
                     WHERE sync_state='error' AND deleted=0 AND (
                       sync_attempts < 3 OR (sync_attempts < 10 AND
                       sync_claimed_at < datetime('now', '-30 minutes')))""")
        # shallow before deep: order (0) → package (1) → item (2), so a child
        # never pushes before its parent has a task id.
        candidates = [r["id"] for r in c.execute(
            """SELECT id FROM leluxe_orders WHERE sync_state='dirty' AND deleted=0
               ORDER BY (CASE kind WHEN 'package' THEN 1 WHEN 'item' THEN 2 ELSE 0 END),
                        (clickup_task_id IS NOT NULL), id
               LIMIT ?""", (limit * 2,))]
    stats = {"pushed": 0, "failed": 0, "waiting": 0}
    for row_id in candidates:
        if stats["pushed"] + stats["failed"] >= limit:
            break
        row = get_row(row_id)
        if not row or row["sync_state"] != "dirty":
            continue
        if row["kind"] not in TOP_KINDS and not _parent_task_id(row):
            stats["waiting"] += 1              # parent lands first, child next pass
            continue
        with db.connect() as c:
            claimed = c.execute(
                """UPDATE leluxe_orders SET sync_state='pushing',
                   sync_claimed_at=datetime('now') WHERE id=? AND
                   sync_state='dirty'""", (row_id,)).rowcount
        if claimed != 1:
            continue                            # the other worker owns it
        row = get_row(row_id)
        row["sync_state"] = "pushing"
        try:
            ok, err = push_one(row, config)
        except Exception as e:  # noqa: BLE001 — a bad row must not stall the queue
            _finish(row_id, None, None, error=f"push crashed: {e}",
                    attempts=row.get("sync_attempts", 0) + 1)
            ok, err = False, str(e)
        stats["pushed" if ok else "failed"] += 1
    if stats["waiting"] and not stats["failed"]:
        kick()                                  # items were waiting on a parent
    return stats


def retry_errors():
    with db.connect() as c:
        n = c.execute("""UPDATE leluxe_orders SET sync_state='dirty',
                         sync_attempts=0, sync_error=NULL
                         WHERE sync_state='error' AND deleted=0""").rowcount
    kick()
    return n


def run_delete_pass(limit=15, config=None):
    """Drain the ClickUp-twin deletion queue (filled by dedupe_migrated) at a
    rate-limit-friendly pace — ~2 API calls per task with a 1.5s gap keeps us
    under half of ClickUp's ~100 req/min cap, leaving room for the pusher.
    Same shape as run_push_pass: the atomic claim makes it duplicate-safe under
    two gunicorn workers, and the double guard re-checks at delete time that
    the task still lives in the WORKING list — AZ (2) or any other list is
    never touched (marked 'skipped'). On a rate-limit or network error the
    pass ends early and the next 60s tick retries."""
    if push_disabled():
        return {"skipped": "LELUXE_PUSH_DISABLED"}
    config = config or cfg.load()
    if not ready(config):
        return {"skipped": "not configured"}
    lid = str(list_id(config) or "")
    if not lid or lid == str(source_list_id(config) or ""):
        return {"skipped": "working list must differ from AZ (2)"}
    with db.connect() as c:
        # stale claims (worker died mid-delete) back to pending after 10 min
        c.execute("""UPDATE leluxe_cu_deletes SET state='pending'
                     WHERE state='doing' AND updated_at IS NOT NULL
                     AND updated_at < datetime('now', '-10 minutes')""")
        cand = [r["task_id"] for r in c.execute(
            "SELECT task_id FROM leluxe_cu_deletes WHERE state='pending' "
            "ORDER BY rowid LIMIT ?", (limit,))]
    stats = {"deleted": 0, "gone": 0, "skipped": 0, "deferred": 0}
    for i, tid in enumerate(cand):
        with db.connect() as c:
            claimed = c.execute(
                """UPDATE leluxe_cu_deletes SET state='doing',
                   updated_at=datetime('now') WHERE task_id=? AND
                   state='pending'""", (tid,)).rowcount
        if claimed != 1:
            continue                    # the other worker owns it
        if i:
            time.sleep(1.5)
        outcome = key = err = None
        st, body = _http(f"{CLICKUP_API}/task/{tid}")
        if st == 404:
            outcome, key = "done", "gone"          # already deleted (cascade)
        elif st == 200 and str(((body or {}).get("list") or {}).get("id")) == lid:
            dst, _b = _http(f"{CLICKUP_API}/task/{tid}", "DELETE")
            if dst in (200, 204):
                outcome, key = "done", "deleted"
            else:
                err = f"ClickUp delete failed ({dst})"
        elif st == 200:
            outcome, key = "skipped", "skipped"
            err = "task is not in the working list — left untouched"
        else:
            err = f"task lookup failed ({st})"
        with db.connect() as c:
            if outcome:
                c.execute("""UPDATE leluxe_cu_deletes SET state=?, last_error=?,
                             updated_at=datetime('now') WHERE task_id=?""",
                          (outcome, err, tid))
            else:
                row = c.execute("SELECT attempts FROM leluxe_cu_deletes "
                                "WHERE task_id=?", (tid,)).fetchone()
                att = ((row["attempts"] if row else 0) or 0) + 1
                c.execute("""UPDATE leluxe_cu_deletes SET state=?, attempts=?,
                             last_error=?, updated_at=datetime('now')
                             WHERE task_id=?""",
                          ("skipped" if att >= 10 else "pending", att, err, tid))
        if outcome:
            stats[key] += 1
        else:
            stats["deferred"] += 1
            break                       # likely rate-limited — retry next tick
    return stats


def _interval_seconds():
    try:
        return max(5, int(os.environ.get("LELUXE_PUSH_INTERVAL_SEC", "60")))
    except ValueError:
        return 60


def auto_pull_settings():
    """⏱ Auto-sync from AZ (2): {"enabled": bool, "minutes": int}. Stored in
    THIS instance's DB (like leluxe:az2), so enabling it on the live site never
    turns it on for the stale local mirror — each DB opts in for itself."""
    s = db.get_setting("leluxe:auto_pull") or {}
    return {"enabled": bool(s.get("enabled")),
            "minutes": max(5, int(s.get("minutes") or 30))}


def run_pull_pass(config=None, now=None):
    """One ⏱ auto-sync tick: at most one sync_from_source per `minutes`,
    sharing the same leluxe:import_running mutex as the manual Sync button so
    the two can never overlap. Never runs review_all. Records the outcome in
    leluxe:auto_pull_last for the Tools label + the bell."""
    ap = auto_pull_settings()
    if not ap["enabled"]:
        return {"skipped": "disabled"}
    config = config or cfg.load()
    if not (_token() and source_list_id(config) and list_id(config)
            and source_list_id(config) != list_id(config)):
        return {"skipped": "not configured"}
    now = now if now is not None else time.time()
    last = db.get_setting("leluxe:auto_pull_last") or {}
    if now - float(last.get("ts") or 0) < ap["minutes"] * 60:
        return {"skipped": "not due"}
    flag = db.get_setting("leluxe:import_running") or 0
    if isinstance(flag, (int, float)) and flag and now - flag < 120:
        return {"skipped": "sync already running"}
    db.set_setting("leluxe:import_running", now)
    try:
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        r = sync_from_source(since, limit=25, config=config, review_all=False)
    except Exception as e:  # noqa: BLE001 — the tick must never kill the loop
        r = {"error": str(e)[:200]}
    finally:
        db.set_setting("leluxe:import_running", 0)
    db.set_setting("leluxe:auto_pull_last", {
        "ts": now, "at": db.now_iso(),
        "orders": r.get("orders", 0), "updated": r.get("updated", 0),
        "conflicts": r.get("conflicts", 0), "kept": r.get("kept", 0),
        "error": r.get("error") or ""})
    return {"ran": True, **{k: r.get(k) for k in
                            ("orders", "updated", "conflicts", "kept", "error")}}


def _loop():
    time.sleep(30)                     # let the app finish booting first
    while True:
        try:
            with memlog.watch("leluxe.push"):
                out = run_push_pass()
            if out.get("pushed") or out.get("failed"):
                print(f"leluxe: pushed {out.get('pushed', 0)} "
                      f"(failed {out.get('failed', 0)})")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"leluxe: push pass failed ({e})")
        try:
            with memlog.watch("leluxe.delete"):
                d = run_delete_pass()
            if d.get("deleted") or d.get("gone") or d.get("deferred"):
                print(f"leluxe: cu-delete queue {d}")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"leluxe: delete pass failed ({e})")
        try:
            with memlog.watch("leluxe.pull"):     # pulls the whole ClickUp board
                p = run_pull_pass()
            if p.get("ran"):
                print(f"leluxe: auto-pull {p}")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"leluxe: auto-pull failed ({e})")
        _KICK.wait(timeout=_interval_seconds())
        _KICK.clear()


def start():
    """Start the background pusher once (idempotent). A no-op pass costs one
    config read when the feature isn't configured, so it's safe to always run."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="leluxe-push", daemon=True).start()


if __name__ == "__main__":
    # Disaster recovery only: put back a pre-sync snapshot written by
    # _presync_snapshot (Tools → Sync writes one before every run).
    #   python3 leluxe.py --restore-presync leluxe_presync_20260718-101500.json
    import sys as _sys
    if "--restore-presync" in _sys.argv:
        _f = _sys.argv[_sys.argv.index("--restore-presync") + 1]
        if input(f"Restore Leluxe rows from {_f}? This overwrites current local "
                 f"values (they then re-push to the working list). Type RESTORE: "
                 ).strip() == "RESTORE":
            print(restore_presync(_f))
        else:
            print("aborted")
    else:
        print(__doc__ or "leluxe module — see --restore-presync")
