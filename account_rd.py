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

import json
from datetime import datetime, timezone

import db
import leluxe

# The 2×4 grid the board really uses: {sent, recieved, delievered, not recieved}
# × {rd, no rd}, plus the two bare in-progress ones.
RD_STATUSES = {
    "rd", "rd request", "sent rd", "recieved rd",
    "delievered rd", "not recieved rd",
}
# A finished order with NO refund claim — the proof an account is clean.
CLEAN_STATUSES = {"sent no rd", "recieved no rd"}
# Rows that carry a NAME but are not board work (the table also stores a few
# housekeeping kinds: backup / tracking_refresh / tracking_sync).
BOARD_KINDS = ("order", "parent", "package", "item")


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
        return datetime.fromtimestamp(n / 1000, timezone.utc).date().isoformat()
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


def _rd_log_dates():
    """row_id → date of the FIRST transition into an RD status.

    The only real "when did this become an RD" we hold. It is thin on purpose —
    db.log_leluxe_status records transitions only, so a row imported already on
    `sent rd` has no entry and must fall through to a weaker anchor."""
    marks = sorted(RD_STATUSES)
    q = ",".join("?" * len(marks))
    out = {}
    with db.connect() as c:
        for r in c.execute(
                f"SELECT row_id, MIN(ts) ts FROM leluxe_status_log "
                f"WHERE lower(new_status) IN ({q}) GROUP BY row_id", marks):
            day = str(r["ts"] or "")[:10]
            if day:
                out[r["row_id"]] = day
    return out


def _rd_date(row, parent, log_dates):
    """(date, source) for one RD row, best anchor first.

    Returns ('', '') when nothing dates it — the caller must treat an undated RD
    as spent rather than guess it is fresh."""
    day = log_dates.get(row["id"])
    if day:
        return day, "status_log"
    for cand in (row, parent):
        if cand:
            day = _delivered_at(cand)
            if day:
                return day, "tracking"
    day = _ms_to_iso(_field(row, "DATE SENT"))
    if day:
        return day, "date_sent"
    day = _ms_to_iso(row.get("ordered_at") or row.get("date_created"))
    if day:
        return day, "ordered"
    return "", ""


def _name_options():
    """Every account code the board knows — the `NAME` dropdown, which is the
    account universe. Wider than the rows: an option nobody has ordered on yet
    still belongs in the answer, as an account with no proven clean order."""
    fdef = leluxe._sch_field_def((leluxe.schema() or {}).get("fields") or {}, "NAME")
    return [str(o.get("name") or "").strip()
            for o in (fdef.get("options") or []) if str(o.get("name") or "").strip()]


def rollup():
    """Every account code → its RD history, for the studio to pass judgement on.

    {"synced_at": iso, "accounts": [{code, orders, rd, clean, rd_undated,
                                     states, banned, newest_rd_at,
                                     newest_rd_src, statuses}]}
    `statuses` is the raw per-status tally so the studio (or a human) can audit
    the buckets without a second round-trip.
    """
    with db.connect() as c:
        rows = [leluxe._row(r) for r in c.execute(
            "SELECT * FROM leluxe_orders WHERE deleted=0")]
    by_id = {r["id"]: r for r in rows}
    log_dates = _rd_log_dates()

    accounts = {}

    def slot(code):
        return accounts.setdefault(code, {
            "code": code, "orders": 0, "rd": 0, "clean": 0, "rd_undated": 0,
            "states": "", "banned": False, "newest_rd_at": "",
            "newest_rd_src": "", "statuses": {}})

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
        if st in CLEAN_STATUSES:
            a["clean"] += 1
        if st in RD_STATUSES:
            a["rd"] += 1
            day, src = _rd_date(r, by_id.get(r.get("parent_local_id")), log_dates)
            if not day:
                # An RD nothing can date: reported, never guessed. The studio
                # fails it closed rather than calling an unknown window "fresh".
                a["rd_undated"] += 1
            elif day > a["newest_rd_at"]:
                # newest RD wins — it owns the only 30-day window still open
                a["newest_rd_at"], a["newest_rd_src"] = day, src

    for code in _name_options():
        slot(code)

    return {
        "synced_at": db.now_iso(),
        "accounts": sorted(accounts.values(), key=lambda a: a["code"]),
    }
