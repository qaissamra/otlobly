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
from datetime import datetime, timezone
from urllib import request as urlrequest, error as urlerror
from urllib.parse import quote as urlquote

import cfg
import db
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
        conflict_rows = [{"id": r["id"], "name": r["name"]} for r in c.execute(
            "SELECT id, name FROM leluxe_orders "
            "WHERE sync_state='conflict' AND deleted=0 LIMIT 50")]
    return {"synced": counts.get("synced", 0), "dirty": counts.get("dirty", 0),
            "pushing": counts.get("pushing", 0), "error": counts.get("error", 0),
            "conflict": counts.get("conflict", 0),
            "last_errors": errors, "conflict_rows": conflict_rows}


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
        row_id = payload["id"]
    else:
        extra = {"tracking_number": tn} if tn else {}
        row_id = _insert_row(
            kind, name, status=status, due_date=due_ms, fields=fields,
            desc=desc, tags=tags, parent_local_id=parent_local,
            parent_task_id=(parent or {}).get("clickup_task_id"), extra=extra)
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
        n = c.execute("""UPDATE leluxe_orders SET status=?, updated_at=?,
                         sync_state='dirty', sync_error=NULL, sync_attempts=0
                         WHERE id=? AND deleted=0""",
                      (status, db.now_iso(), row_id)).rowcount
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


def dedupe_migrated(dry_run=True, config=None):
    """One-time cleanup for migrate-created duplicates. The working list was
    seeded by DUPLICATING AZ (2) in ClickUp (rows WITHOUT source_task_id);
    migrate later re-copied the same orders (rows WITH source_task_id). Where a
    name-group holds both kinds, keep the original import and remove each
    migrate copy — locally (cascade to its packages/items) plus its pushed twin
    task in the WORKING list. Double guard before any ClickUp delete: the row
    must carry migrate provenance (source_task_id) AND the live task must
    belong to the working list — AZ (2) can never be touched."""
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
            tid = loser.get("clickup_task_id")
            if tid:
                st, body = _http(f"{CLICKUP_API}/task/{tid}")
                if st == 200 and str(((body or {}).get("list") or {}).get("id")) == lid:
                    dst, _b = _http(f"{CLICKUP_API}/task/{tid}", "DELETE")
                    if dst in (200, 204):
                        cu_deleted += 1
                    else:
                        errors.append(f"{loser['name']}: ClickUp delete failed ({dst})")
                elif st == 404:
                    pass                             # already gone
                elif st == 200:
                    errors.append(f"{loser['name']}: task {tid} is not in the "
                                  f"working list — ClickUp left untouched")
                else:
                    errors.append(f"{loser['name']}: task lookup failed ({st})")
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
    # provenance onto it, remove the copy locally + its pushed working-list task
    # (same double guard), then drop any package the removal left empty. ──
    with db.connect() as c:
        vis = {r["id"]: _row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")}

    def _order_of(rid):
        cur, seen = rid, set()
        while cur in vis and cur not in seen:
            seen.add(cur)
            p = vis[cur]["parent_local_id"]
            if p is None or p not in vis:
                break
            cur = p
        return cur

    igroups = {}
    for r in vis.values():
        if r["kind"] != "item":
            continue
        g = igroups.setdefault((_order_of(r["id"]), _name_key(r["name"])),
                               {"orig": [], "sync": []})
        g["sync" if (r["data"] or {}).get("source_task_id") else "orig"].append(r)
    item_entries, touched_parents = [], set()
    items_removed = items_adopted = pkgs_removed = ambiguous_items = 0
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
            if dry_run:
                continue
            tdata = twin["data"] or {}
            with db.connect() as c:
                kr = c.execute("SELECT data_json FROM leluxe_orders WHERE id=?",
                               (keeper["id"],)).fetchone()
                if kr:
                    kd = json.loads(kr["data_json"] or "{}")
                    grew = False
                    for k2 in ("source_task_id", "source_base", "source_cu_updated"):
                        if tdata.get(k2) is not None and kd.get(k2) is None:
                            kd[k2] = tdata[k2]
                            grew = True
                    if grew:
                        c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                                  (json.dumps(kd, ensure_ascii=False), keeper["id"]))
                        items_adopted += 1
            tid = twin.get("clickup_task_id")
            if tid:
                st, body = _http(f"{CLICKUP_API}/task/{tid}")
                if st == 200 and str(((body or {}).get("list") or {}).get("id")) == lid:
                    dst, _b = _http(f"{CLICKUP_API}/task/{tid}", "DELETE")
                    if dst in (200, 204):
                        cu_deleted += 1
                    else:
                        errors.append(f"{twin['name']}: ClickUp delete failed ({dst})")
                elif st == 404:
                    pass                             # already gone
                elif st == 200:
                    errors.append(f"{twin['name']}: task {tid} is not in the "
                                  f"working list — ClickUp left untouched")
                else:
                    errors.append(f"{twin['name']}: task lookup failed ({st})")
            with db.connect() as c:
                c.execute("UPDATE leluxe_orders SET deleted=1, updated_at=? "
                          "WHERE id=?", (db.now_iso(), twin["id"]))
            items_removed += 1
            if twin["parent_local_id"] is not None:
                touched_parents.add(twin["parent_local_id"])
    if not dry_run and touched_parents:
        empties = []
        with db.connect() as c:
            for pid in touched_parents:
                pr = c.execute("SELECT id, name, clickup_task_id FROM leluxe_orders "
                               "WHERE id=? AND deleted=0 AND kind='package'",
                               (pid,)).fetchone()
                if not pr:
                    continue
                left = c.execute("SELECT COUNT(*) n FROM leluxe_orders "
                                 "WHERE parent_local_id=? AND deleted=0",
                                 (pid,)).fetchone()["n"]
                if left == 0:
                    empties.append(dict(pr))
        for pr in empties:
            ptid = pr["clickup_task_id"]
            if ptid:
                st, body = _http(f"{CLICKUP_API}/task/{ptid}")
                if st == 200 and str(((body or {}).get("list") or {}).get("id")) == lid:
                    dst, _b = _http(f"{CLICKUP_API}/task/{ptid}", "DELETE")
                    if dst in (200, 204):
                        cu_deleted += 1
                    else:
                        errors.append(f"{pr['name']}: ClickUp delete failed ({dst})")
            with db.connect() as c:
                c.execute("UPDATE leluxe_orders SET deleted=1, updated_at=? "
                          "WHERE id=?", (db.now_iso(), pr["id"]))
            pkgs_removed += 1

    return {"groups": report, "removed": removed, "clickup_deleted": cu_deleted,
            "items": item_entries,
            "item_pairs": sum(1 for e in item_entries if not e.get("ambiguous")),
            "items_removed": items_removed, "items_adopted": items_adopted,
            "pkgs_removed": pkgs_removed, "ambiguous_items": ambiguous_items,
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


def _write_row(c, rid, cols, data, sync_state=None, reset_sync=False):
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
        elif data.get("source_cu_updated") == src_cu:
            return "unchanged"              # AZ (2) task untouched since last sync
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
    _insert_row("item", ccols["name"], status=ccols["status"],
                due_date=ccols["due_date"], fields=keep(cdata["fields"]),
                desc=cdata["description"], parent_local_id=pkg_id,
                date_created=ccols["date_created"],
                extra={"source_task_id": ch["id"],
                       "source_cu_updated": str(ch.get("date_updated") or ""),
                       "source_base": _snapshot(ccols, cdata, keep)})
    made["items"] += 1
    made["new_items"] += 1


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
                                      "order": oname, "changes": chs})
        elif res == "conflict":
            report["conflict_rows"].append({"id": row_id, "kind": kind,
                                            "name": name, "order": oname})

    chs = []
    res = _merge_row(rid, src_order, sch, known, keep, review_all, chs)
    if res == "conflict":
        made["conflicts"] += 1
    elif res == "updated":
        made["updated"] += 1
    _record(rid, "order", oname, res, chs)
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
        else:
            _insert_new_item(rid, ch, sch, known, keep, made)
            if report is not None:
                report["new_items"].append({"name": ch.get("name") or "",
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
            "order_ids": []}
    snap = _presync_snapshot()              # safety net BEFORE any write
    report = {"ts": db.now_iso(), "review_all": bool(review_all), "applied": [],
              "new_orders": [], "new_items": [], "conflict_rows": []}
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
    made["presync"] = snap
    kick()
    return made


def list_conflicts():
    """Every row parked in a merge conflict, with its per-field diffs and the
    parent order's name (for grouping in the review panel)."""
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
            _write_row(c, row_id, cols_new, data, sync_state="conflict")
        else:
            data.pop("conflicts", None)
            _write_row(c, row_id, cols_new, data, sync_state="dirty", reset_sync=True)
    if not remaining:
        kick()
    return get_row(row_id), None


# --------------------------------------------------------------------------- #
# Live GAASH + Gerizim tracking — same carriers/logic as the Purchases page.
# Display-only enrichment (never pushed to ClickUp), keyed on tracking_number.
# Works on ANY row that carries a tracking number: packages (3-tier), or the
# order/item rows themselves on a flat mirror.
# --------------------------------------------------------------------------- #
def _row_tracking(row):
    """A row's tracking number: data.tracking_number (packages) or the
    'Tracking Number' custom field (flat orders/items), case-blind."""
    tn = row["data"].get("tracking_number")
    if not tn:
        for k, v in (row["data"].get("fields") or {}).items():
            if k.strip().lower() == "tracking number":
                tn = v
                break
    return str(tn or "").strip()


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
    """Set fields[GASH STATUS]=target and queue a FIELD-ONLY push. Returns True
    when a change was queued (False = already up to date / row gone)."""
    with db.connect() as c:
        r = c.execute("SELECT sync_state, data_json FROM leluxe_orders "
                      "WHERE id=? AND deleted=0", (row["id"],)).fetchone()
        if not r:
            return False
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        fields = d.setdefault("fields", {})
        key = next((k for k in fields if k.strip().lower() == GASH_FIELD), fkey)
        if str(fields.get(key) or "").strip() == target:
            return False
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
    return True


def apply_gash_status(only=None, config=None):
    """Mirror each tracked row's live stage (Gerizim, else GAASH) into the
    GASH STATUS dropdown. Idempotent (writes only differences), FORWARD-ONLY
    (never downgrades a manual entry that's ahead of a lagging feed); `only`
    scopes to one GWD. Runs over ALL stored enrichment, so already-delivered
    parcels (which the network loop skips) still catch up. Returns #queued."""
    config = config or cfg.load()
    fkey, fdef = _gash_field_def(config)
    if not fdef:
        return 0
    import tracking
    only = tracking.clean_tracking(only or "") or None
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    queued = 0
    for row in rows:
        if only and tracking.clean_tracking(_row_tracking(row)) != only:
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
        if _queue_gash_status(row, fkey, target):
            queued += 1
    if queued:
        kick()
    return queued


def refresh_tracking(batch=5, only=None, force=False, config=None):
    import tracking
    import gerizim
    from datetime import timedelta
    config = config or cfg.load()
    only = tracking.clean_tracking(only or "") or None
    now = datetime.now().astimezone()
    cutoff = now - timedelta(minutes=30)
    dl_cutoff = now - timedelta(days=14)
    with db.connect() as c:
        rows = [_row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    work = {}
    for row in rows:
        tn = tracking.clean_tracking(_row_tracking(row))
        if not tn or (only and tn != only):
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
        eligible = (ts.get("bucket") if isinstance(ts, dict) else None) \
            not in ("cleared", "delivered") and not gz
        need_dl = False
        if eligible:
            try:
                need_dl = force or not d.get("gaash_deadline") or \
                    datetime.fromisoformat(d.get("gaash_deadline_checked") or "") <= dl_cutoff
            except (ValueError, TypeError):
                need_dl = True
        if need or need_dl:
            work.setdefault(tn, []).append((row["id"], need, need_dl))
    todo = list(work.items())[:batch]
    session = None
    for tn, targets in todo:
        want_track = any(t[1] for t in targets)
        want_dl = any(t[2] for t in targets)
        st = gz_new = deadline = None
        if want_track:
            try:
                session = session or tracking.get_session()
                st = tracking.latest_status(tracking.fetch_one(tn, *session))
            except Exception:  # noqa: BLE001 — one bad number must not stall the batch
                st = None
            try:
                gz_new = gerizim.track(tn)
            except Exception:  # noqa: BLE001
                gz_new = None
        if want_dl:
            try:
                deadline = tracking.ops_deadline(tn)
            except Exception:  # noqa: BLE001
                deadline = None
        stamp = now.isoformat()
        for row_id, _n, _d in targets:
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
                    d["tracking_checked"] = stamp
                if gz_new:
                    d["gerizim_status"] = gz_new
                if want_dl:
                    d["gaash_deadline_checked"] = stamp
                    if deadline:
                        d["gaash_deadline"] = deadline
                c.execute("UPDATE leluxe_orders SET data_json=? WHERE id=?",
                          (json.dumps(d, ensure_ascii=False), row_id))
        time.sleep(_pace())
    # after storing fresh Gerizim stages, mirror them into the GASH STATUS field
    applied = apply_gash_status(only=only, config=config)
    return {"checked": len(todo), "remaining": max(0, len(work) - len(todo)),
            "gash_applied": applied}


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
                       "tags": list(tags)})
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


def _interval_seconds():
    try:
        return max(5, int(os.environ.get("LELUXE_PUSH_INTERVAL_SEC", "60")))
    except ValueError:
        return 60


def _loop():
    time.sleep(30)                     # let the app finish booting first
    while True:
        try:
            out = run_push_pass()
            if out.get("pushed") or out.get("failed"):
                print(f"leluxe: pushed {out.get('pushed', 0)} "
                      f"(failed {out.get('failed', 0)})")
        except Exception as e:  # noqa: BLE001 - never let the thread die
            print(f"leluxe: push pass failed ({e})")
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
