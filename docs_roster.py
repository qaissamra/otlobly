"""📄 Docs roster — the GAASH mail › Docs tab's parcels, read straight from ClickUp.

Why this exists (2026-09-21): the Docs tab listed parcels from the app's Le Luxe
mirror (leluxe_orders) and the Purchases store. The mirror had not synced since
the 2026-09-04 corruption, and IT Products was never a source at all, so two
parcels GAASH was asking documents for that very day (GWD004803012 on IT
Products, GWD004802571 on Le Luxe) were invisible. The owner's rule: the two
ClickUp lists ARE the queue.

One row per GWD in `gaash_parcels`, with two writers that never share a column:
  · refresh()             what ClickUp says  → cu_json, source, open, seen_at
  · check() / store_*()   what GAASH says    → data_json, with the SAME keys a
                          leluxe_orders row carries (docs_state, tracking_status,
                          gaash_arrival, gaash_deadline…), so gaash_mail.docs_queue
                          reads both through one code path

Freshness: the ClickUp webhooks goals.py already registers on both lists call
schedule_refresh(); the tab refreshes on load when the roster is older than
MAX_AGE_S; the nightly docs sweep refreshes before it checks. A parcel that
appears for the first time gets its first GAASH check on its own (auto_check).
"""
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

import db

# (source key, ClickUp list id, label) — the owner's two lists
LISTS = (("leluxe", "901520351506", "Le Luxe"),
         ("it", "901524960550", "IT"))
LABELS = {src: label for src, _lid, label in LISTS}

# The two lists live in one ClickUp space and share these field ids. The name is
# the fallback (case- and space-blind, prefix match) so a re-created field still
# reads — "NAME ON PACKAGEE" keeps working if the typo is ever fixed.
F_TRACKING = ("519e7736-2fd5-4c99-a606-746fb6e63a34", "tracking number")
F_NAME = ("0ae98793-33ab-4715-a11c-e54ea5204238", "name on packag")
F_GASH = ("25d6446b-9f7a-4ded-9d38-7d63cc1e1470", "gash status")
F_ASIN = ("5705feb8-5f4a-4be7-b39a-9dbdfd9fee6e", "asin")
F_QTY = ("aaf61bec-3cc3-4e4b-9d61-0adccca7ff7b", "quantity ordered")

META_KEY = "docs:roster_meta"
MAX_AGE_S = 900            # a FULL read when the last one is older (the tab's load)
MIN_GAP_S = 10             # background passes: at most one per 10 s
DEBOUNCE_S = 3             # …and only once a webhook burst has been quiet this long
DL_EVERY = timedelta(days=14)   # the boards' deadline cadence (leluxe.refresh_tracking)
ASIN_PHOTOS_PER_RUN = 6    # Amazon look-ups are metered (SerpAPI) — capped per run

GWD_CANON = re.compile(r"GWD\d{9}$")
_ORDER = re.compile(r"^\s*order\s*#", re.I)
_QTY_LEAD = re.compile(r"^\s*(\d{1,3})\s+\S")


def _fold(s):
    return " ".join(str(s or "").lower().split())


def _live():
    """DOCS_ROSTER_LIVE=0 keeps this module off the network (ClickUp and GAASH)
    unless a caller hands refresh() its own fetch — the test suites set it, so
    a stray webhook post or worker sweep in a test never reads the real lists."""
    return os.environ.get("DOCS_ROSTER_LIVE", "1").strip().lower() not in ("0", "false", "off", "no")


def _now():
    return datetime.now().astimezone()


# --------------------------------------------------------------------------- #
# Reading one ClickUp task
# --------------------------------------------------------------------------- #
def _cf(task, spec):
    fid, name = spec
    fields = [f for f in (task.get("custom_fields") or []) if isinstance(f, dict)]
    for f in fields:
        if f.get("id") == fid:
            return f
    for f in fields:
        if _fold(f.get("name")).startswith(name):
            return f
    return None


def _value(cf):
    """A custom field's value as plain text. A dropdown arrives as its
    orderindex (NAME ON PACKAGEE = 1 means "FAISAL "), so it is decoded from the
    task's own inline options — never a cached schema, which drifts."""
    if not cf:
        return ""
    v = cf.get("value")
    if v in (None, "", []):
        return ""
    if cf.get("type") == "drop_down":
        for o in (cf.get("type_config") or {}).get("options") or []:
            if v == o.get("id") or v == o.get("orderindex") \
                    or str(v) == str(o.get("orderindex")):
                return str(o.get("name") or "").strip()
    return str(v).strip()


def clean_gwd(v):
    """A tracking-number field → a GWD, or "". ClickUp values carry stray
    spaces and invisible marks (one real value ends in U+200E); UPS numbers,
    "-", "UNDER" and "NOFUNDS" are not GAASH parcels."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper()
    return s if s.startswith("GWD") and any(ch.isdigit() for ch in s) else ""


def _finished(task):
    """Done in ClickUp: a done/closed status type, or a cancelled one (Le Luxe
    types "cancelled" as unstarted, IT Products as done)."""
    st = task.get("status") or {}
    return st.get("type") in ("done", "closed") or "cancel" in _fold(st.get("status"))


def _is_container(task):
    st = _fold((task.get("status") or {}).get("status"))
    return str(task.get("name") or "").lstrip().startswith("\U0001F4E6") or st == "package"


def _qty(task):
    raw = _value(_cf(task, F_QTY))
    m = re.match(r"\d+", raw.replace(",", ""))
    if m and int(m.group(0)) > 0:
        return int(m.group(0))
    m = _QTY_LEAD.match(str(task.get("name") or ""))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else 1


def _phones(task):
    out = []
    for f in task.get("custom_fields") or []:
        if isinstance(f, dict) and "phone" in _fold(f.get("name")):
            d = re.sub(r"\D", "", _value(f))
            if len(d) >= 7:
                out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Tasks → parcels (pure: the tests feed it real ClickUp task JSON)
# --------------------------------------------------------------------------- #
def build(tasks_by_list):
    """{source: [raw ClickUp task, …]} → {GWD: parcel}.

    A task's GWD is its own Tracking Number, else the nearest ancestor's (a
    product under a 📦 container or an order carrying the number) — the same
    inheritance leluxe._eff_tn_map applies to the board."""
    import leluxe
    by_id = {}
    for src, tasks in (tasks_by_list or {}).items():
        for t in tasks or []:
            if isinstance(t, dict) and t.get("id"):
                by_id[t["id"]] = (src, t)

    def parent(t):
        return (by_id.get(t.get("parent")) or (None, None))[1] if t.get("parent") else None

    def eff_tn(t):
        cur, hops = t, 0
        while cur is not None and hops < 5:
            tn = clean_gwd(_value(_cf(cur, F_TRACKING)))
            if tn:
                return tn
            cur, hops = parent(cur), hops + 1
        return ""

    def up_value(t, spec):
        cur, hops = t, 0
        while cur is not None and hops < 5:
            v = _value(_cf(cur, spec))
            if v:
                return v
            cur, hops = parent(cur), hops + 1
        return ""

    def order_of(t):
        cur, hops, top = t, 0, t
        while cur is not None and hops < 5:
            if _ORDER.match(str(cur.get("name") or "")):
                return cur
            top, cur, hops = cur, parent(cur), hops + 1
        return top if top is not t else None

    groups = {}
    for tid, (src, t) in by_id.items():
        tn = eff_tn(t)
        if tn:
            groups.setdefault(tn, []).append((src, t))

    out = {}
    for tn, members in groups.items():
        members.sort(key=lambda m: str(m[1].get("id")))
        prods = [(s, t) for s, t in members
                 if not _is_container(t) and not _ORDER.match(str(t.get("name") or ""))]
        carriers = prods or members
        name = next((v for v in (_value(_cf(t, F_NAME)) for _, t in carriers) if v), "") \
            or next((v for v in (up_value(t, F_NAME) for _, t in carriers) if v), "")
        gash = sorted({g for g in (_value(_cf(t, F_GASH)) for _, t in members) if g})
        ranks = [r for r in (leluxe._gash_rank(g) for g in gash) if r is not None]
        finished = all(_finished(t) for _, t in carriers) or any(r >= 4 for r in ranks)
        srcs = Counter(s for s, _ in carriers)
        source = max(srcs, key=lambda s: (srcs[s], s == "it"))
        o = order_of(carriers[0][1])
        phones = []
        for _, t in members:
            for p in _phones(t):
                if p not in phones:
                    phones.append(p)
        out[tn] = {
            "gwd": tn, "source": source,
            "lists": sorted({s for s, _ in members}),
            "open": not finished,
            "name": name,
            "order": ({"id": o.get("id"), "name": str(o.get("name") or "").strip(),
                       "url": o.get("url") or ""} if o else None),
            "gash": gash,
            "products": [{
                "id": t.get("id"), "name": str(t.get("name") or "").strip(),
                "qty": _qty(t), "url": t.get("url") or "",
                "asin": _value(_cf(t, F_ASIN)).upper(),
                "status": str((t.get("status") or {}).get("status") or ""),
                "color": str((t.get("status") or {}).get("color") or ""),
                "image": "",
            } for _, t in prods],
            "phones": phones,
            "unusual": not GWD_CANON.match(tn),
        }
    return out


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #
def _load(r):
    def j(s):
        try:
            v = json.loads(s or "{}")
        except (ValueError, TypeError):
            v = {}
        return v if isinstance(v, dict) else {}
    return {"gwd": r["gwd"], "source": r["source"] or "", "open": bool(r["open"]),
            "cu": j(r["cu_json"]), "data": j(r["data_json"]),
            "seen_at": r["seen_at"] or "", "updated_at": r["updated_at"] or ""}


def rows():
    """{GWD: {source, open, cu, data, …}} — local only, cheap enough for the
    bell's 60-second poll. {} if the table cannot be read."""
    try:
        with db.connect() as c:
            return {r["gwd"]: _load(r) for r in c.execute("SELECT * FROM gaash_parcels")}
    except Exception:  # noqa: BLE001 — the queue must still draw from the boards
        return {}


def get(gwd):
    g = str(gwd or "").strip().upper()
    if not g:
        return None
    try:
        with db.connect() as c:
            r = c.execute("SELECT * FROM gaash_parcels WHERE gwd=?", (g,)).fetchone()
        return _load(r) if r else None
    except Exception:  # noqa: BLE001
        return None


def _update_data(gwd, fn):
    """Read-modify-write one row's data_json inside one IMMEDIATE transaction,
    so two checks of the same parcel cannot drop each other's keys."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT data_json FROM gaash_parcels WHERE gwd=?", (gwd,)).fetchone()
        if not r:
            return False
        try:
            d = json.loads(r["data_json"] or "{}")
        except (ValueError, TypeError):
            d = {}
        fn(d)
        c.execute("UPDATE gaash_parcels SET data_json=?, updated_at=? WHERE gwd=?",
                  (json.dumps(d, ensure_ascii=False), db.now_iso(), gwd))
    return True


def meta():
    m = db.get_setting(META_KEY) or {}
    return m if isinstance(m, dict) else {}


def _bump(**extra):
    m = meta()
    m.update(extra)
    m["ver"] = os.urandom(4).hex()
    m["changed_at"] = db.now_iso()
    db.set_setting(META_KEY, m)
    return m


# --------------------------------------------------------------------------- #
# Seeding a new row from what the boards already know
# --------------------------------------------------------------------------- #
_STATE_KEYS = ("docs_state", "docs_checked", "docs_error", "tracking_status",
               "tracking_checked", "gerizim_status", "gaash_arrival",
               "gaash_arrival_code", "gaash_deadline", "gaash_deadline_checked")


def _board_twins(gwd):
    """Every board record that carries this GWD: Le Luxe rows + Purchases packages."""
    out = []
    try:
        with db.connect() as c:
            for r in c.execute("SELECT data_json FROM leluxe_orders "
                               "WHERE deleted=0 AND data_json LIKE ?", (f"%{gwd}%",)):
                try:
                    d = json.loads(r["data_json"] or "{}")
                except (ValueError, TypeError):
                    continue
                tn = str(d.get("tracking_number") or "").strip().upper()
                if not tn:
                    for k, v in (d.get("fields") or {}).items():
                        if _fold(k) == "tracking number":
                            tn = str(v or "").strip().upper()
                            break
                if clean_gwd(tn) == gwd:
                    out.append(d)
    except Exception:  # noqa: BLE001
        pass
    try:
        import purchases
        for p in (purchases.load() or {}).get("purchase_orders") or []:
            for pk in p.get("packages") or []:
                if clean_gwd(pk.get("tracking_number")) == gwd:
                    out.append(pk)
    except Exception:  # noqa: BLE001
        pass
    return out


def _seed(gwd):
    """The newest known GAASH answers for a parcel, field group by field group,
    so a parcel the boards already checked does not start from nothing."""
    twins = _board_twins(gwd)
    d = {}

    def newest(stamp_key, value_key):
        best = None
        for t in twins:
            if t.get(value_key) and (best is None
                                     or str(t.get(stamp_key) or "") > str(best.get(stamp_key) or "")):
                best = t
        return best

    t = newest("docs_checked", "docs_state")
    if t:
        for k in ("docs_state", "docs_checked"):
            d[k] = t.get(k)
    t = newest("tracking_checked", "tracking_status")
    if t:
        d["tracking_status"] = t.get("tracking_status")
        d["tracking_checked"] = t.get("tracking_checked") or ""
    t = newest("gaash_deadline_checked", "gaash_deadline")
    if t:
        d["gaash_deadline"] = t.get("gaash_deadline")
        d["gaash_deadline_checked"] = t.get("gaash_deadline_checked") or ""
    arr = sorted((t.get("gaash_arrival"), t.get("gaash_arrival_code"))
                 for t in twins if t.get("gaash_arrival"))
    if arr:
        d["gaash_arrival"], d["gaash_arrival_code"] = arr[0]
    gz = next((t.get("gerizim_status") for t in twins
               if isinstance(t.get("gerizim_status"), dict)), None)
    if gz:
        d["gerizim_status"] = gz
    return {k: v for k, v in d.items() if v not in (None, "")}


# --------------------------------------------------------------------------- #
# Photos: the board's cache first, then Amazon by ASIN (metered, capped)
# --------------------------------------------------------------------------- #
def _photo_maps():
    by_task, by_asin = {}, {}
    try:
        with db.connect() as c:
            for r in c.execute("SELECT data_json FROM leluxe_orders "
                               "WHERE deleted=0 AND data_json LIKE '%\"image\"%'"):
                try:
                    d = json.loads(r["data_json"] or "{}")
                except (ValueError, TypeError):
                    continue
                img = d.get("image")
                if not img:
                    continue
                if d.get("source_task_id"):
                    by_task.setdefault(d["source_task_id"], img)
                a = str(d.get("image_asin") or "").strip().upper()
                if a:
                    by_asin.setdefault(a, img)
    except Exception:  # noqa: BLE001
        pass
    try:
        import amazon_import
        for a, info in (amazon_import._load_cache() or {}).items():
            if isinstance(info, dict) and info.get("image"):
                by_asin.setdefault(str(a).upper(), info["image"])
    except Exception:  # noqa: BLE001
        pass
    return by_task, by_asin


def _carry_photos(parcel, old_cu, maps):
    by_task, by_asin = maps
    old = {(p.get("id"), p.get("asin")): p.get("image")
           for p in (old_cu or {}).get("products") or [] if p.get("image")}
    for p in parcel["products"]:
        p["image"] = (old.get((p["id"], p["asin"])) or by_task.get(p["id"])
                      or (by_asin.get(p["asin"]) if p["asin"] else None) or "")


def fill_photos(limit=ASIN_PHOTOS_PER_RUN):
    """Fetch the Amazon photo for products that have an ASIN but no photo yet.
    import_product caches by ASIN forever, so each ASIN costs one look-up ever.
    Products with no ASIN keep their empty slot: nothing is guessed by name."""
    todo = []
    for g, r in rows().items():
        if not r["open"]:
            continue
        for p in r["cu"].get("products") or []:
            if p.get("asin") and not p.get("image") and p["asin"] not in todo:
                todo.append(p["asin"])
    got = {}
    if todo:
        try:
            import amazon_import
            import cfg
            conf = cfg.load()
            for a in todo[:limit]:
                try:
                    img = (amazon_import.import_product(a, conf) or {}).get("image")
                except Exception:  # noqa: BLE001
                    img = None
                if img:
                    got[a] = img
        except Exception:  # noqa: BLE001
            pass
    if not got:
        return 0
    n = 0
    with db.connect() as c:
        for r in c.execute("SELECT gwd, cu_json FROM gaash_parcels").fetchall():
            try:
                cu = json.loads(r["cu_json"] or "{}")
            except (ValueError, TypeError):
                continue
            hit = False
            for p in cu.get("products") or []:
                if not p.get("image") and got.get(p.get("asin")):
                    p["image"] = got[p["asin"]]
                    hit = True
            if hit:
                c.execute("UPDATE gaash_parcels SET cu_json=? WHERE gwd=?",
                          (json.dumps(cu, ensure_ascii=False, sort_keys=True), r["gwd"]))
                n += 1
    if n:
        _bump()
    return n


# --------------------------------------------------------------------------- #
# Reading ClickUp — slim tasks, a full read rarely, one task per webhook event
# --------------------------------------------------------------------------- #
# Measured 2026-09-21: the two lists are 516 tasks and 18 MB of JSON — ClickUp
# sends every dropdown's full option list (NAME alone has 169) with EVERY task —
# and keeping them parsed costs ~80 MB. So a full read slims each page as it
# arrives (the handful of fields this module reads, decoded), the slim copy is
# kept in settings[TASKS_KEY] (~200 KB), and a webhook event re-reads ONE task
# (~40 KB) into that copy. The parcels are always rebuilt from the whole copy,
# so a product that inherits its container's number still follows it.
TASKS_KEY = "docs:roster_tasks"
MAX_EVENT_TASKS = 25       # a bigger burst than this is cheaper as one full read
_KEEP = (F_TRACKING, F_NAME, F_GASH, F_ASIN, F_QTY)
_LOCK = threading.Lock()


def _slim(task, src=None):
    """A ClickUp task reduced to what build() reads, in the same shape (so a
    slim task is also a valid task — slimming twice is a no-op)."""
    if not isinstance(task, dict) or not task.get("id"):
        return None
    fields = []
    for spec in _KEEP:
        cf = _cf(task, spec)
        v = _value(cf)
        if v:
            fields.append({"id": cf.get("id"), "name": cf.get("name"),
                           "type": "short_text", "value": v})
    for f in task.get("custom_fields") or []:
        if isinstance(f, dict) and "phone" in _fold(f.get("name")):
            v = _value(f)
            if v:
                fields.append({"id": f.get("id"), "name": f.get("name"),
                               "type": "short_text", "value": v})
    st = task.get("status") or {}
    out = {"id": task["id"], "name": str(task.get("name") or ""),
           "parent": task.get("parent"), "url": task.get("url") or "",
           "status": {"status": st.get("status"), "color": st.get("color"),
                      "type": st.get("type")},
           "custom_fields": fields}
    lid = str((task.get("list") or {}).get("id") or task.get("list_id") or "")
    if lid:
        out["list_id"] = lid
    if src:
        out["src"] = src
    return out


def _fetch_slim(list_id):
    """Every task of one list (closed + subtasks), slimmed PAGE BY PAGE so only
    one page of ClickUp's heavy JSON is alive at a time. Returns (tasks, error)."""
    import leluxe
    if not leluxe._token():
        return None, "CLICKUP_API_TOKEN is not set"
    out, page = [], 0
    while True:
        s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/list/{list_id}/task"
                                f"?include_closed=true&subtasks=true&page={page}")
        if s_ != 200:
            return None, f"list {list_id} fetch failed ({s_})"
        batch = (body or {}).get("tasks") or []
        last = (body or {}).get("last_page", True)
        out.extend(x for x in (_slim(t) for t in batch) if x)
        del body, batch
        if last or page > 60:
            break
        page += 1
    return out, None


def _fetch_task(task_id):
    """One task, fresh. (task, None) · (None, None) when it is gone (deleted or
    no longer ours) · (None, error) when ClickUp could not answer."""
    import leluxe
    if not leluxe._token():
        return None, "CLICKUP_API_TOKEN is not set"
    s_, body = leluxe._http(f"{leluxe.CLICKUP_API}/task/{task_id}")
    if s_ == 200 and isinstance(body, dict) and body.get("id"):
        return body, None
    if s_ == 404:
        return None, None
    return None, f"task {task_id} fetch failed ({s_})"


def _tasks_update(fn):
    """Read-modify-write the slim copy in ONE immediate transaction, so two
    workers applying webhook events cannot drop each other's task."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT value FROM settings WHERE key=?", (TASKS_KEY,)).fetchone()
        try:
            cache = json.loads(r["value"]) if r else {}
        except (ValueError, TypeError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        fn(cache)
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (TASKS_KEY, json.dumps(cache, ensure_ascii=False)))
    return cache


def _stamp():
    try:
        import goals
        return goals._stamp_token()
    except Exception:  # noqa: BLE001
        return ""


def _age_s(iso):
    try:
        return (_now() - datetime.fromisoformat(iso)).total_seconds()
    except (ValueError, TypeError):
        return None


def stale(max_age=MAX_AGE_S):
    """True when a FULL read is due: never read, or the last full read is older
    than max_age. Webhook events keep the copy current in between, one task at
    a time, so there is no reason to re-read everything because one arrived."""
    age = _age_s(meta().get("at"))
    return age is None or age > max_age


def _rebuild(cache, errors, full):
    """Parcels from the WHOLE slim copy → gaash_parcels; bumps the version when
    anything changed. Returns the meta plus `changed` and `refreshed`."""
    lists = cache.get("lists") or {}
    parcels = build({src: tasks for src, tasks in lists.items() if tasks is not None})
    changed = _upsert(parcels, {src for src, tasks in lists.items() if tasks is not None})
    counts = {src: 0 for src, _l, _b in LISTS}
    with db.connect() as c:
        for r in c.execute("SELECT source, COUNT(*) n FROM gaash_parcels "
                           "WHERE open=1 GROUP BY source"):
            counts[r["source"]] = r["n"]
    m = meta()
    m.pop("new", None)
    if full:
        m.update(at=db.now_iso(), error="; ".join(errors), lists_ok=sorted(lists))
    m.update(counts=counts, touched_at=db.now_iso(), stamp=_stamp())
    if changed:
        m.update(ver=os.urandom(4).hex(), changed_at=db.now_iso())
    m.setdefault("ver", os.urandom(4).hex())
    db.set_setting(META_KEY, m)
    return dict(m, refreshed=True, changed=changed)


def refresh(force=False, max_age=MAX_AGE_S, fetch=None):
    """A FULL read of both ClickUp lists into the slim copy, then the parcels.
    Returns the meta plus `changed` (GWDs added / changed / closed).

    A list that fails to load keeps its previous copy — a ClickUp hiccup must
    never make open parcels look finished. `fetch(list_id)` → (tasks, error) is
    the test seam; the default slims each page as it arrives."""
    if not force and not stale(max_age):
        return dict(meta(), refreshed=False, changed=[])
    if fetch is None and not _live():
        return dict(meta(), refreshed=False, changed=[])
    if not _LOCK.acquire(blocking=False):      # this process is already reading
        return dict(meta(), refreshed=False, changed=[])
    try:
        fetch = fetch or _fetch_slim
        fetched, errors = {}, []
        for src, lid, label in LISTS:
            try:
                tasks, err = fetch(lid)
            except Exception as e:  # noqa: BLE001
                tasks, err = None, str(e)
            if err or tasks is None:
                errors.append(f"{label}: {err or 'no answer'}"[:200])
                continue
            fetched[src] = [x for x in (_slim(t, src) for t in tasks) if x]
        if not fetched:
            m = meta()
            m.update(error="; ".join(errors), tried=db.now_iso())
            db.set_setting(META_KEY, m)
            return dict(m, refreshed=False, changed=[])

        def put(cache):
            cache.setdefault("lists", {}).update(fetched)
            cache["at"] = db.now_iso()
        return _rebuild(_tasks_update(put), errors, full=True)
    finally:
        _LOCK.release()


def apply_events(task_ids, fetch_task=None):
    """The webhook path: re-read only the tasks ClickUp says changed, patch them
    into the slim copy, rebuild. Falls back to a full read when there is no copy
    yet, the burst is large, or a task cannot be read (a partial picture must
    never close a parcel)."""
    ids = [t for t in dict.fromkeys(str(x) for x in (task_ids or []) if x)]
    cache = db.get_setting(TASKS_KEY) or {}
    lists = (cache.get("lists") if isinstance(cache, dict) else None) or {}
    if not ids or not lists or len(ids) > MAX_EVENT_TASKS:
        return refresh(force=True)
    fetch_task = fetch_task or _fetch_task
    src_of = {lid: src for src, lid, _ in LISTS}
    got = {}
    for tid in ids:
        try:
            task, err = fetch_task(tid)
        except Exception as e:  # noqa: BLE001
            task, err = None, str(e)
        if err:
            return refresh(force=True)
        got[tid] = task

    def patch(cache):
        ls = cache.setdefault("lists", {})
        for tid, task in got.items():
            for src in list(ls):
                ls[src] = [t for t in (ls[src] or []) if t.get("id") != tid]
            if task:
                lid = str((task.get("list") or {}).get("id") or task.get("list_id") or "")
                src = src_of.get(lid)
                if src:                          # moved to another list = not ours any more
                    ls.setdefault(src, []).append(_slim(task, src))
    return _rebuild(_tasks_update(patch), [], full=False)


def _upsert(parcels, fetched_sources):
    maps = _photo_maps()
    now = db.now_iso()
    with db.connect() as c:
        existing = {r["gwd"]: dict(r) for r in c.execute(
            "SELECT gwd, source, open, cu_json FROM gaash_parcels")}
    # read everything first (the seeds scan the boards), then write in ONE
    # transaction — never a read connection opened under a pending write
    seeds = {g: _seed(g) for g in parcels if g not in existing}
    inserts, updates, touched, closed, changed = [], [], [], [], []
    for gwd, p in sorted(parcels.items()):
        old = existing.get(gwd)
        try:
            old_cu = json.loads((old or {}).get("cu_json") or "{}")
        except (ValueError, TypeError):
            old_cu = {}
        _carry_photos(p, old_cu, maps)
        cu = json.dumps(p, ensure_ascii=False, sort_keys=True)
        if old is None:
            inserts.append((gwd, p["source"], int(p["open"]), cu,
                            json.dumps(seeds.get(gwd) or {}, ensure_ascii=False), now, now))
            changed.append(gwd)
        elif (old["cu_json"] != cu or bool(old["open"]) != p["open"]
              or old["source"] != p["source"]):
            updates.append((p["source"], int(p["open"]), cu, now, now, gwd))
            changed.append(gwd)
        else:
            touched.append((now, gwd))
    # a number that left ClickUp (deleted task, edited tracking number) closes —
    # but only when every list it came from was actually read this time
    for gwd, old in existing.items():
        if gwd in parcels or not old["open"]:
            continue
        try:
            lists = set(json.loads(old["cu_json"] or "{}").get("lists") or [])
        except (ValueError, TypeError):
            lists = set()
        if (lists or {old["source"]}) <= fetched_sources:
            closed.append((now, gwd))
            changed.append(gwd)
    with db.connect() as c:
        c.executemany("INSERT OR IGNORE INTO gaash_parcels "
                      "(gwd, source, open, cu_json, data_json, seen_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?)", inserts)
        c.executemany("UPDATE gaash_parcels SET source=?, open=?, cu_json=?, "
                      "seen_at=?, updated_at=? WHERE gwd=?", updates)
        c.executemany("UPDATE gaash_parcels SET seen_at=? WHERE gwd=?", touched)
        c.executemany("UPDATE gaash_parcels SET open=0, updated_at=? WHERE gwd=?", closed)
    return changed


# --------------------------------------------------------------------------- #
# Live: the webhook → a debounced background update
# --------------------------------------------------------------------------- #
_BG = {"due": 0.0, "last": 0.0, "thread": None, "ids": set(), "full": False}
_BG_LOCK = threading.Lock()


def schedule_refresh(reason="webhook", task_id=None):
    """Called by /webhook/clickup on every verified delivery (with the task it
    names) and by the tab when a full read is due. Returns at once; one
    background thread per worker coalesces a burst of deliveries into one pass —
    the named tasks re-read one by one, or a full read — then gives every parcel
    that just appeared its first GAASH check and fetches missing photos."""
    if not _live():
        return False
    with _BG_LOCK:
        if task_id:
            _BG["ids"].add(str(task_id))
        else:
            _BG["full"] = True
        _BG["due"] = time.time() + DEBOUNCE_S
        t = _BG["thread"]
        if t is not None and t.is_alive():
            return False
        t = threading.Thread(target=_bg_run, name="docs-roster", daemon=True)
        _BG["thread"] = t
        t.start()
        return True


def _bg_run():
    while True:
        with _BG_LOCK:
            wait = max(_BG["due"], _BG["last"] + MIN_GAP_S) - time.time()
        if wait > 0:
            time.sleep(min(wait, 5))
            continue
        with _BG_LOCK:
            ids, full = sorted(_BG["ids"]), _BG["full"]
            _BG["ids"], _BG["full"] = set(), False
        try:
            res = refresh(force=True) if (full or not ids) else apply_events(ids)
            _follow(res.get("changed") or [])
        except Exception as e:  # noqa: BLE001 — a background thread must never die loudly
            print(f"[docs_roster] background update failed: {e}", flush=True)
        with _BG_LOCK:
            _BG["last"] = time.time()
            if not _BG["ids"] and not _BG["full"]:   # nothing arrived while we worked
                _BG["thread"] = None
                return


def _follow(gwds):
    """After a refresh: the first GAASH check for parcels that just appeared,
    then any missing photos. Slow (GAASH answers in 10-25 s), so never on a
    request thread."""
    # only open parcels nobody has asked GAASH about — the very first read
    # "changes" all ~150 numbers, most of them long finished
    fresh = []
    for g in gwds or []:
        r = get(g)
        if r and r["open"] and not r["data"].get("docs_checked"):
            fresh.append(r["gwd"])
    for g in fresh[:10]:
        try:
            auto_check(g)
        except Exception as e:  # noqa: BLE001
            print(f"[docs_roster] auto-check {g} failed: {e}", flush=True)
    try:
        fill_photos()
    except Exception as e:  # noqa: BLE001
        print(f"[docs_roster] photos failed: {e}", flush=True)


def follow_up(gwds):
    """Run _follow for a refresh made on a request thread (the tab's load or
    its Refresh button) without holding that request."""
    gwds = [g for g in (gwds or []) if g]
    if not gwds or not _live():
        return False
    threading.Thread(target=_follow, args=(gwds,), name="docs-roster-follow",
                     daemon=True).start()
    return True


def auto_check(gwd):
    """A parcel that just appeared gets its first GAASH answer without anyone
    pressing Check. Once per GWD across every worker (claim_once), and never
    for a parcel the boards had already checked (its seed carries docs_checked)."""
    r = get(gwd)
    if not r or not r["open"] or r["data"].get("docs_checked"):
        return None
    if not _claim(f"docs:autocheck:{r['gwd']}"):
        return None
    docs = check(r["gwd"])
    _bump()
    return docs


def _claim(key, ttl_s=3600):
    """One caller across every worker wins the key — and it EXPIRES: a check cut
    off halfway (a deploy, a restart) must not leave its parcel unchecked for
    good. Returns True for the winner."""
    now = db.now_iso()
    cutoff = (_now() - timedelta(seconds=ttl_s)).isoformat(timespec="seconds")
    with db.connect() as c:
        cur = c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                        "WHERE json_extract(settings.value, '$') < ?",
                        (key, json.dumps(now), cutoff))
        return cur.rowcount == 1


# --------------------------------------------------------------------------- #
# Checks: what GAASH says
# --------------------------------------------------------------------------- #
def store_docs_state(tn, docs):
    """Same contract as leluxe/purchases.store_docs_state: a result sets
    docs_state + docs_checked and clears docs_error; None (the LOOKUP failed)
    only stamps docs_error, keeping the last answer and its true age."""
    g = clean_gwd(tn)
    if not g:
        return 0
    stamp = (docs or {}).get("checked") or db.now_iso()

    def apply(d):
        if isinstance(docs, dict):
            d["docs_state"] = docs
            d["docs_checked"] = stamp
            d.pop("docs_error", None)
        else:
            d["docs_error"] = stamp
    return 1 if _update_data(g, apply) else 0


def _deadline_due(d):
    if not d.get("gaash_deadline"):
        return True
    chk = str(d.get("gaash_deadline_checked") or "")
    # read before the box landed = GAASH's ROLLING number (scrape + 35), not the
    # real expiry — arrival invalidates it (PR #141)
    if d.get("gaash_arrival") and chk[:10] < str(d["gaash_arrival"])[:10]:
        return True
    try:
        return datetime.fromisoformat(chk) <= _now() - DL_EVERY
    except (ValueError, TypeError):
        return True


def refresh_tracking_one(gwd, docs=None):
    """The GAASH timeline + Gerizim + the upload deadline for one roster parcel.
    The deadline is read only once the box has provably landed: reading GAASH's
    ops upload page CREATES the 35-day link, so asking early burns days."""
    import leluxe
    import tracking
    r = get(gwd)
    if not r:
        return None
    d, cu = r["data"], r["cu"]
    st = events = None
    try:
        session = tracking.get_session()
        data = tracking.fetch_one(gwd, *session)
        if isinstance(data, dict) and not data.get("_error"):
            st = tracking.latest_status(data)
            events = tracking.events_from_raw(data)
            if events:
                try:
                    tracking.cache_put_events(gwd, events)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001 — the timeline is extra; the docs answer stands
        pass
    gz = None
    try:
        import gerizim
        gz = gerizim.track(gwd)
    except Exception:  # noqa: BLE001
        gz = None
    arr = None
    if events:
        try:
            arr = tracking.arrival_from_events(events)
        except Exception:  # noqa: BLE001
            arr = None
    ranks = [x for x in (leluxe._gash_rank(g) for g in cu.get("gash") or []) if x is not None]
    verdict, why = tracking.arrival_signal(
        stored_arrival=d.get("gaash_arrival") or (arr or {}).get("at"),
        events=events,
        docs_state=docs if isinstance(docs, dict) else d.get("docs_state"),
        gerizim_arrived=isinstance(gz, dict) and gz.get("bucket") in leluxe.GZ_ARRIVED,
        gash_rank=max(ranks) if ranks else None)
    arr_at = d.get("gaash_arrival") or (arr or {}).get("at") or ""
    want_dl = verdict == "arrived" and _deadline_due(dict(d, gaash_arrival=arr_at))
    deadline = None
    if want_dl:
        try:
            deadline = tracking.ops_deadline(gwd)
        except Exception:  # noqa: BLE001
            deadline = None
    stamp = db.now_iso()

    def apply(x):
        if st:
            x["tracking_status"] = st
        x["tracking_checked"] = stamp
        if gz and (isinstance(gz, dict) or not isinstance(x.get("gerizim_status"), dict)):
            x["gerizim_status"] = gz
        if arr and (not x.get("gaash_arrival") or (arr.get("at") or "") < x["gaash_arrival"]):
            x["gaash_arrival"] = arr.get("at")
            x["gaash_arrival_code"] = arr.get("code")
        if want_dl:
            x["gaash_deadline_checked"] = stamp
            if deadline:
                x["gaash_deadline"] = deadline
        x["deadline_skipped"] = "" if want_dl else why
    _update_data(r["gwd"], apply)
    return {"arrived": verdict == "arrived", "deadline": deadline, "skipped": None if want_dl else why}


def check(tn):
    """THE docs check — the tab's Check and Check all, the upload wizard's
    after-send re-check and the nightly worker sweep all land here. GAASH's
    banner is stored on every board that carries the number (a no-op where
    none does); a roster parcel also gets its timeline and, once it has
    landed, its deadline. Returns tracking.docs_status's answer (None = the
    lookup failed)."""
    import leluxe
    import tracking
    tn = tracking.clean_tracking(tn or "").upper()
    if not tn:
        return None
    docs = tracking.docs_status(tn)
    leluxe.store_docs_state(tn, docs)
    try:
        import purchases
        purchases.store_docs_state(tn, docs)
    except Exception:  # noqa: BLE001 — a purchases hiccup must not lose the answer
        pass
    if get(tn) is not None:
        store_docs_state(tn, docs)
        try:
            refresh_tracking_one(tn, docs)
        except Exception as e:  # noqa: BLE001
            print(f"[docs_roster] tracking for {tn} failed: {e}", flush=True)
    return docs


def summary(gwd):
    """What the tab needs to redraw one row after a Check: the deadline, the
    days left and GAASH's latest status, from whichever store has them."""
    r = get(gwd)
    if not r:
        return None
    d = r["data"]
    ts = d.get("tracking_status") if isinstance(d.get("tracking_status"), dict) else {}
    return {"gaash_deadline": d.get("gaash_deadline") or "",
            "days_left": days_left(d.get("gaash_deadline")),
            "bucket": ts.get("bucket"), "label": ts.get("label"),
            "arrived": bool((d.get("docs_state") or {}).get("arrived")
                            or d.get("gaash_arrival")),
            "deadline_skipped": d.get("deadline_skipped") or ""}


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #
def amman_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Amman")).date()
    except Exception:  # noqa: BLE001 — Jordan is fixed UTC+3
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=3))).date()


def days_left(deadline):
    """Days until GAASH's upload link expires, on the owner's (Amman) calendar."""
    from datetime import date
    try:
        return (date.fromisoformat(str(deadline)[:10]) - amman_today()).days
    except (ValueError, TypeError):
        return None


def version():
    """The tab's 15-second poll: the roster version plus whether ClickUp can
    reach us (both lists hooked). Local reads only — no ClickUp call."""
    m = meta()
    live, last = False, ""
    try:
        import goals
        info = goals.webhook_info()
        hooked = set(info.get("hooked") or [])
        live = all(lid in hooked for _s, lid, _l in LISTS)
        last = info.get("last_bump") or ""
    except Exception:  # noqa: BLE001
        pass
    return {"ver": m.get("ver") or "", "at": m.get("touched_at") or m.get("at") or "",
            "full_at": m.get("at") or "",
            "changed_at": m.get("changed_at") or "", "counts": m.get("counts") or {},
            "error": m.get("error") or "", "live": live, "last_ping": last}
