"""
Per-account RD history — the board half of AZ Studio's Accounts Tool.

Big Amazon orders are placed from buying accounts named B27 / E-B50 / S-B32 —
the ClickUp `NAME` dropdown on the Le Luxe board. An account that has run an RD
(refund/dispute) is spent and must never carry another big order, so the studio
needs to know, per account, whether it has ever run one and when.

This module answers only the BOARD half of that question. It deliberately does
NOT decide whether an account is usable: that verdict also needs the Multilogin
fleet (does the profile still exist?), which lives in az-tool. Otlobly owns the
ClickUp token and this mirror; az-tool owns the fleet; the two are joined there.

The status spellings below are the board's own — `recieved`, `delievered` are
literally what ClickUp stores. Never "correct" them.

Why a fifth status vocabulary is not being invented here: the four that already
disagree (APP_AUDIT F-003) all answer "is this parcel DONE". This one answers
"did this account run an RD", which none of them can — `sent no rd` is done for
every one of them, yet it is the single strongest signal that an account is
CLEAN. Sharing their sets would be wrong, not tidy.
"""

import datetime
import json

import db
import leluxe

# The 2×4 grid the board really uses: {sent, recieved, delievered, not recieved}
# × {rd, no rd}, plus the two bare in-progress ones.
RD_STATUSES = {
    "rd", "rd request", "sent rd", "recieved rd",
    "delievered rd", "not recieved rd",
}
# A finished order with NO refund claim — the proof an account is clean.
# `delievered no rd` joined on 2026-09-04 (owner's call): a delivered order with no
# refund claim is the same proof as a sent one, and leaving it out was discarding
# 7 orders' worth of evidence.
# 🛑 `not recieved no rd` is deliberately NOT here. "No RD" is true of it, but the
# goods never arrived, so it is not evidence of a clean completed order — it is a
# dead end. It proves nothing about how Amazon sees the account.
CLEAN_STATUSES = {"sent no rd", "recieved no rd", "delievered no rd"}

# Statuses that carry no RD/clean verdict. Split so the page can say WHY a row has
# no verdict, instead of lumping 287 rows into one silent "other".
SCAFFOLDING_STATUSES = {"order number", "package"}
DEAD_END_STATUSES = {"cancelled", "request cancel", "undeliverable",
                     "not correct address", "not recieved no rd"}
# Everything else the board actually defines — still moving, or finished without
# the RD question ever being answered on the row.
IN_FLIGHT_STATUSES = {
    "oredered", "shipped", "parcelto destination", "doc sent to gash",
    "in clearance", "waiting verification", "required customer id", "mixed",
    "parcel check", "tracking api", "picked up by ger", "arrived at destination",
    "cleared customs", "az id", "az id sub", "documents sent", "delivered",
    "complete", "refund request",
}

# The scenario ladder — what a reader is looking at, in the order they care.
SCENARIOS = ("clean_settled", "clean_fresh", "rd_open", "rd_closed", "rd_undated",
             "in_flight", "dead_end", "scaffolding", "unknown")

RD_WINDOW_DAYS = 30

# Rows that carry a NAME but are not board work (the table also stores a few
# housekeeping kinds: backup / tracking_refresh / tracking_sync).
BOARD_KINDS = ("order", "parent", "package", "item")


def bucket_of(status, age_days=None, dated=True):
    """One status (+ how old it is) → one scenario. THE single classifier.

    🛑 An unrecognised status returns "unknown" and is COUNTED, never dropped.
    ClickUp statuses are added by hand; a silently unbucketed one is exactly how
    an RD would stop being counted and a spent account would read as clean."""
    st = _norm(status)
    if st in RD_STATUSES:
        if not dated or age_days is None:
            return "rd_undated"
        return "rd_open" if age_days <= RD_WINDOW_DAYS else "rd_closed"
    if st in CLEAN_STATUSES:
        # A "no rd" outcome is only FINAL once the refund window has passed;
        # inside 30 days a claim can still land and turn it into an RD.
        if dated and age_days is not None and age_days <= RD_WINDOW_DAYS:
            return "clean_fresh"
        return "clean_settled"
    if st in SCAFFOLDING_STATUSES:
        return "scaffolding"
    if st in DEAD_END_STATUSES:
        return "dead_end"
    if st in IN_FLIGHT_STATUSES:
        return "in_flight"
    return "unknown"


def _norm(s):
    return " ".join(str(s or "").strip().lower().split())


def _ms_to_iso(ms):
    """A ClickUp ms-epoch string to an ISO date, or '' when it isn't one."""
    try:
        n = int(str(ms).strip())
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            n / 1000, datetime.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _delivered_at(row):
    """The carrier's delivery date, when the parcel actually reached delivered.

    tracking_status is the shaped dict refresh_tracking writes:
    {"code": "D1", "bucket": "delivered", "time": "2026-05-15T14:47:42", ...}.
    Only the `delivered` bucket counts — `arrived`/`customs` are mid-journey and
    would date an RD far too early."""
    ts = (row.get("data") or {}).get("tracking_status") or {}
    if not isinstance(ts, dict) or _norm(ts.get("bucket")) != "delivered":
        return ""
    return str(ts.get("time") or "")[:10]


def _field(row, name):
    """One custom field off a mirror row, tolerant of ClickUp's key casing and
    the live schema's stray trailing spaces ('Quantity ordered ')."""
    fields = (row.get("data") or {}).get("fields") or {}
    return str(fields.get(leluxe._field_key(fields, name)) or "").strip()


def _log_dates():
    """(row_id, family) → (date, quality) for the first logged transition into it.

    The only real "when did this happen" we hold, and it needs two caveats told
    rather than hidden:

    ⚠️ THE LOG IS THIN. db.log_leluxe_status records TRANSITIONS only, so a row
    imported already on `sent rd` has no entry at all and must fall through to a
    weaker anchor.

    🛑 AND IT IS OFTEN LATE. If the row's `old_status` was ALREADY in the same
    family, this is a second hop (`rd` → `recieved rd`), so the family was entered
    earlier by an unknown amount. That is reported as quality "at_latest" — an
    upper bound, never a fact. Most live entries are exactly this shape, and
    presenting them as exact dates is how the page would start lying.
    """
    out = {}
    with db.connect() as c:
        for r in c.execute("SELECT row_id, old_status, new_status, ts "
                           "FROM leluxe_status_log ORDER BY ts"):
            new_st, old_st = _norm(r["new_status"]), _norm(r["old_status"])
            fam = ("rd" if new_st in RD_STATUSES else
                   "clean" if new_st in CLEAN_STATUSES else "")
            if not fam:
                continue
            day = str(r["ts"] or "")[:10]
            if not day:
                continue
            key = (r["row_id"], fam)
            if key in out:                       # ORDER BY ts — first one wins
                continue
            same = (old_st in RD_STATUSES) if fam == "rd" else (old_st in CLEAN_STATUSES)
            out[key] = (day, "at_latest" if same else "exact")
    return out


def _row_date(row, parent, log_dates, family):
    """(date, source, quality) for one row, best anchor first.

    Returns ('', '', 'none') when nothing dates it — the caller must treat an
    undated RD as spent rather than guess it is fresh.

    `quality` is what stops the page overstating its case:
      exact     a logged transition into the family from outside it
      at_latest a logged LATER hop — the true date is earlier, unknown how much
      proxy     the parcel's delivery date, or the owner-typed DATE SENT
      weak      the ORDER date — not when the status changed at all, just the
                only timestamp left. Shown greyed, and never dressed up.
    """
    hit = log_dates.get((row["id"], family))
    if hit:
        return hit[0], "status_log", hit[1]
    for cand in (row, parent):
        if cand:
            day = _delivered_at(cand)
            if day:
                return day, "tracking", "proxy"
    day = _ms_to_iso(_field(row, "DATE SENT"))
    if day:
        return day, "date_sent", "proxy"
    day = _ms_to_iso(row.get("ordered_at") or row.get("date_created"))
    if day:
        return day, "ordered", "weak"
    return "", "", "none"


def _name_options():
    """Every account code the board knows — the `NAME` dropdown, which is the
    account universe. Wider than the rows: an option nobody has ordered on yet
    still belongs in the answer, as an account with no proven clean order."""
    fdef = leluxe._sch_field_def((leluxe.schema() or {}).get("fields") or {}, "NAME")
    return [str(o.get("name") or "").strip()
            for o in (fdef.get("options") or []) if str(o.get("name") or "").strip()]


def _age_days(day, today):
    try:
        return (today - datetime.date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return None


def _board_age(rows, today):
    """How old the BOARD data is — the newest AZ (2) change we have ever seen.

    Not "when did we last run a sync": a sync that finds nothing still stamps
    itself as recent, which would report a 22-day-old board as fresh."""
    newest = 0
    for r in rows:
        for k in ("source_cu_updated", "cu_updated"):
            try:
                newest = max(newest, int((r.get("data") or {}).get(k) or 0))
            except (TypeError, ValueError):
                pass
    if not newest:
        return "", None
    day = _ms_to_iso(newest)
    return day, _age_days(day, today)


def rollup(now=None):
    """Every account code → its RD history, plus every ORDER behind it.

    {"synced_at": iso, "board_seen_at": iso, "board_age_days": int|None,
     "accounts": [{code, orders, rd, clean, rd_undated, states, banned,
                   clean_settled, clean_fresh, newest_rd_at, newest_rd_src,
                   newest_rd_quality, statuses, scenarios}],
     "orders": [{code, kind, status, scenario, label, order_label, task_id,
                 ordered_at, dated_at, anchor, anchor_quality, age_days,
                 past_window}]}

    `now` is injected so the 30-day boundary is testable rather than discovered
    in production.
    """
    today = now or datetime.datetime.now(datetime.timezone.utc).date()
    if isinstance(today, datetime.datetime):
        today = today.date()
    with db.connect() as c:
        rows = [leluxe._row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    by_id = {r["id"]: r for r in rows}
    log_dates = _log_dates()

    accounts, orders = {}, []

    def slot(code):
        return accounts.setdefault(code, {
            "code": code, "orders": 0, "rd": 0, "clean": 0,
            "clean_settled": 0, "clean_fresh": 0, "rd_undated": 0,
            "states": "", "banned": False, "newest_rd_at": "",
            "newest_rd_src": "", "newest_rd_quality": "", "statuses": {},
            "scenarios": {}})

    def top_label(r):
        """The order this row belongs to, for grouping.

        Same walk as leluxe._top_order_of, but over the rows already in memory —
        that helper re-reads the DB once per hop, which is one query per level
        per row and would turn a page render into hundreds of round-trips."""
        cur, seen, hops = r, set(), 0
        while cur and cur.get("kind") not in leluxe.TOP_KINDS and hops < 8:
            pid = cur.get("parent_local_id")
            if not pid or pid in seen:
                cur = None
                break
            seen.add(pid)
            cur = by_id.get(pid)
            hops += 1
        return str((cur or r).get("name") or "").strip()

    for r in rows:
        if r.get("kind") not in BOARD_KINDS:
            continue
        code = _field(r, "NAME")
        if not code:
            continue
        a = slot(code)
        st = _norm(r.get("status"))
        a["orders"] += 1
        if st:
            a["statuses"][st] = a["statuses"].get(st, 0) + 1
        # BAN is final: one banned row condemns the account, and no later row
        # carrying a cheerier State may talk it back up.
        state = _field(r, "States")
        if _norm(state) == "ban":
            a["banned"] = True
            a["states"] = state
        elif state and not a["banned"] and not a["states"]:
            a["states"] = state

        family = "rd" if st in RD_STATUSES else ("clean" if st in CLEAN_STATUSES else "")
        day, src, quality = ("", "", "none")
        if family:
            day, src, quality = _row_date(
                r, by_id.get(r.get("parent_local_id")), log_dates, family)
        age = _age_days(day, today) if day else None
        scenario = bucket_of(st, age, dated=bool(day))
        a["scenarios"][scenario] = a["scenarios"].get(scenario, 0) + 1

        if st in CLEAN_STATUSES:
            a["clean"] += 1
            # Settled vs fresh matters: a "no rd" outcome inside the 30-day
            # window is not proof yet — a claim can still land against it. Only
            # a settled one may clear an account.
            a["clean_settled" if scenario == "clean_settled" else "clean_fresh"] += 1
        if st in RD_STATUSES:
            a["rd"] += 1
            if not day:
                # An RD nothing can date: reported, never guessed. The studio
                # fails it closed rather than calling an unknown window "fresh".
                a["rd_undated"] += 1
            elif day > a["newest_rd_at"]:
                # newest RD wins — it owns the only 30-day window still open
                a["newest_rd_at"] = day
                a["newest_rd_src"], a["newest_rd_quality"] = src, quality

        # One emitted row per real ORDER. The item grain is the only one that
        # carries verdicts (live board: items hold 130 of 131 RD/clean statuses;
        # parent and order rows hold none), so the 129 'order number' and 37
        # 'package' scaffolding rows would only bury them.
        if r.get("kind") == "item" or family:
            orders.append({
                "code": code,
                "kind": r.get("kind") or "",
                "status": st,
                "scenario": scenario,
                "label": str(r.get("name") or "").strip(),
                "order_label": top_label(r),
                "task_id": r.get("clickup_task_id") or "",
                "ordered_at": _ms_to_iso(r.get("ordered_at") or r.get("date_created")),
                "dated_at": day,
                "anchor": src,
                "anchor_quality": quality,
                "age_days": age,
                "past_window": (age is not None and age > RD_WINDOW_DAYS),
            })

    for code in _name_options():
        slot(code)

    seen_at, age = _board_age(rows, today)
    return {
        "synced_at": db.now_iso(),
        "board_seen_at": seen_at,
        "board_age_days": age,
        "window_days": RD_WINDOW_DAYS,
        "accounts": sorted(accounts.values(), key=lambda a: a["code"]),
        "orders": sorted(orders, key=lambda o: (o["code"], o["ordered_at"])),
    }
