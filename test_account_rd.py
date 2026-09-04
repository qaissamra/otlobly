#!/usr/bin/env python3
"""
Self-checks for the per-account RD rollup (account_rd.py) — the board half of
AZ Studio's Accounts Tool.

Proves: one RD condemns an account however many clean orders sit beside it; BAN
sticks and no later row can talk it back up; the newest RD wins the 30-day
window; the date anchors fall through in priority order and only a DELIVERED
parcel dates an RD; an RD nothing can date is REPORTED, never guessed; every
NAME option reaches the answer even with no orders; and housekeeping rows that
are not board work are ignored.

    ./.venv/bin/python test_account_rd.py
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-acctrd-"))
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import cfg          # noqa: E402
import db           # noqa: E402
import account_rd   # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


SCHEMA = {
    "statuses": [],
    "fields": {
        "NAME": {"id": "f-name", "type": "drop_down",
                 "options": [{"id": "o1", "name": "B22", "orderindex": 0},
                             {"id": "o2", "name": "E-B50", "orderindex": 1},
                             {"id": "o3", "name": "S-B32", "orderindex": 2}]},
        "States": {"id": "f-states", "type": "drop_down",
                   "options": [{"id": "s1", "name": "GOOD", "orderindex": 0},
                               {"id": "s2", "name": "BAN", "orderindex": 1}]},
        "DATE SENT": {"id": "f-ds", "type": "date"},
    },
}


def _setup_schema():
    c = cfg.load()
    c.setdefault("leluxe", {})["schema"] = SCHEMA
    cfg.save(c)


def row(name, status, code, kind="item", states="", parent=None,
        tracking=None, date_sent="", ordered_at=""):
    """One mirror row, straight into the table the rollup reads."""
    data = {"fields": {}}
    if code:
        data["fields"]["NAME"] = code
    if states:
        data["fields"]["States"] = states
    if date_sent:
        data["fields"]["DATE SENT"] = date_sent
    if tracking:
        data["tracking_status"] = tracking
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO leluxe_orders (kind, name, status, parent_local_id, "
            "date_created, ordered_at, data_json) VALUES (?,?,?,?,?,?,?)",
            (kind, name, status, parent, "1780000000000", ordered_at or None,
             json.dumps(data)))
        return cur.lastrowid


def wipe():
    with db.connect() as c:
        c.execute("DELETE FROM leluxe_orders")
        c.execute("DELETE FROM leluxe_status_log")


def by_code(res):
    return {a["code"]: a for a in res["accounts"]}


def one_rd_condemns():
    """Clean orders never cancel out an RD — the whole point of the tool."""
    wipe()
    row("clean 1", "sent no rd", "B22")
    row("clean 2", "recieved no rd", "B22")
    row("the rd", "sent rd", "B22")
    row("all clean", "sent no rd", "E-B50")
    a = by_code(account_rd.rollup())
    check("account with an RD counts it despite 2 clean orders",
          a["B22"]["rd"] == 1 and a["B22"]["clean"] == 2)
    check("clean-only account has rd=0", a["E-B50"]["rd"] == 0 and a["E-B50"]["clean"] == 1)
    check("raw status tally is reported for audit",
          a["B22"]["statuses"] == {"sent no rd": 1, "recieved no rd": 1, "sent rd": 1})


def rd_vocabulary_is_the_boards_own():
    """The misspellings are ClickUp's; matching must not 'correct' them."""
    wipe()
    for st in ("rd", "rd request", "sent rd", "recieved rd",
               "delievered rd", "not recieved rd"):
        row(st, st, "B22")
    for st in ("sent no rd", "recieved no rd"):
        row(st, st, "E-B50")
    row("done, not an rd", "delievered no rd", "S-B32")
    a = by_code(account_rd.rollup())
    check("all six RD spellings counted", a["B22"]["rd"] == 6)
    check("both clean spellings counted", a["E-B50"]["clean"] == 2)
    check("'delievered no rd' is neither RD nor clean",
          a["S-B32"]["rd"] == 0 and a["S-B32"]["clean"] == 0)


def ban_is_final():
    wipe()
    row("banned row", "sent no rd", "B22", states="BAN")
    row("cheerier row", "sent no rd", "B22", states="GOOD")
    row("good only", "sent no rd", "E-B50", states="GOOD")
    a = by_code(account_rd.rollup())
    check("BAN survives a later GOOD row", a["B22"]["banned"] is True)
    check("BAN is what the state reads", a["B22"]["states"] == "BAN")
    check("an unbanned account is not flagged", a["E-B50"]["banned"] is False)


def order_of_ban_rows_does_not_matter():
    """GOOD first, BAN second must land exactly where BAN-first lands."""
    wipe()
    row("good first", "sent no rd", "B22", states="GOOD")
    row("ban second", "sent no rd", "B22", states="BAN")
    a = by_code(account_rd.rollup())
    check("BAN wins regardless of row order",
          a["B22"]["banned"] is True and a["B22"]["states"] == "BAN")


def newest_rd_wins():
    wipe()
    r1 = row("old rd", "sent rd", "B22")
    r2 = row("new rd", "sent rd", "B22")
    with db.connect() as c:
        c.execute("INSERT INTO leluxe_status_log (row_id, old_status, new_status, ts, source) "
                  "VALUES (?,?,?,?,?)", (r1, "oredered", "sent rd", "2026-01-05T10:00:00", "pull"))
        c.execute("INSERT INTO leluxe_status_log (row_id, old_status, new_status, ts, source) "
                  "VALUES (?,?,?,?,?)", (r2, "oredered", "sent rd", "2026-08-20T10:00:00", "pull"))
    a = by_code(account_rd.rollup())
    check("newest RD date is the one reported", a["B22"]["newest_rd_at"] == "2026-08-20")
    check("its source is the status log", a["B22"]["newest_rd_src"] == "status_log")
    check("both RDs still counted", a["B22"]["rd"] == 2)


def date_anchors_fall_through_in_order():
    wipe()
    r = row("logged", "sent rd", "B22", tracking={"bucket": "delivered", "time": "2026-03-03T09:00:00"},
            date_sent="1780000000000")
    with db.connect() as c:
        c.execute("INSERT INTO leluxe_status_log (row_id, old_status, new_status, ts, source) "
                  "VALUES (?,?,?,?,?)", (r, "oredered", "sent rd", "2026-07-07T10:00:00", "pull"))
    a = by_code(account_rd.rollup())
    check("status log beats tracking and DATE SENT",
          a["B22"]["newest_rd_at"] == "2026-07-07" and a["B22"]["newest_rd_src"] == "status_log")

    wipe()
    row("tracked", "sent rd", "E-B50",
        tracking={"bucket": "delivered", "time": "2026-03-03T09:00:00"},
        date_sent="1780000000000")
    a = by_code(account_rd.rollup())
    check("tracking beats DATE SENT when no log entry",
          a["E-B50"]["newest_rd_at"] == "2026-03-03" and a["E-B50"]["newest_rd_src"] == "tracking")

    wipe()
    row("date-sent only", "sent rd", "S-B32", date_sent="1780000000000")
    a = by_code(account_rd.rollup())
    check("DATE SENT is used when nothing better exists",
          a["S-B32"]["newest_rd_src"] == "date_sent")

    wipe()
    row("bare", "sent rd", "B22", ordered_at="1780000000000")
    a = by_code(account_rd.rollup())
    check("order date is the last resort", a["B22"]["newest_rd_src"] == "ordered")


def only_delivered_dates_an_rd():
    """A parcel merely 'arrived' or in 'customs' has not been delivered — using
    it would date an RD weeks early and retire an account that is still live."""
    wipe()
    row("mid journey", "sent rd", "B22",
        tracking={"bucket": "customs", "time": "2026-03-03T09:00:00"})
    a = by_code(account_rd.rollup())
    check("a customs parcel does not date an RD", a["B22"]["newest_rd_src"] != "tracking")


def tracking_falls_back_to_the_parent_package():
    """The RD sits on the product; the tracking sits on its package."""
    wipe()
    pkg = row("📦 GWD1", "", "B22", kind="package",
              tracking={"bucket": "delivered", "time": "2026-04-04T09:00:00"})
    row("the item", "sent rd", "B22", parent=pkg)
    a = by_code(account_rd.rollup())
    check("an item borrows its package's delivery date",
          a["B22"]["newest_rd_at"] == "2026-04-04" and a["B22"]["newest_rd_src"] == "tracking")


def undated_rd_is_reported_never_guessed():
    wipe()
    with db.connect() as c:
        c.execute("INSERT INTO leluxe_orders (kind, name, status, data_json) VALUES (?,?,?,?)",
                  ("item", "no dates at all", "sent rd",
                   json.dumps({"fields": {"NAME": "B22"}})))
    a = by_code(account_rd.rollup())
    check("an undatable RD is counted as such", a["B22"]["rd_undated"] == 1)
    check("and no date is invented", a["B22"]["newest_rd_at"] == "")


def every_name_option_reaches_the_answer():
    wipe()
    row("only one used", "sent no rd", "B22")
    a = by_code(account_rd.rollup())
    check("an option with no orders still appears", "S-B32" in a)
    check("and appears with zeros, not omitted",
          a["S-B32"]["orders"] == 0 and a["S-B32"]["rd"] == 0 and a["S-B32"]["clean"] == 0)


def code_missing_from_a_stale_dropdown_still_counts():
    """The cached schema drifts behind ClickUp (140 options vs 168 live). A code
    seen on a row must never vanish because the cache has not caught up."""
    wipe()
    row("unknown to the cache", "sent rd", "E-B99")
    a = by_code(account_rd.rollup())
    check("a row's code survives a stale dropdown cache",
          "E-B99" in a and a["E-B99"]["rd"] == 1)


def housekeeping_rows_are_not_board_work():
    wipe()
    row("real", "sent no rd", "B22")
    row("bookkeeping", "sent rd", "B22", kind="tracking_sync")
    row("bookkeeping 2", "sent rd", "B22", kind="backup")
    a = by_code(account_rd.rollup())
    check("non-board kinds are ignored", a["B22"]["rd"] == 0 and a["B22"]["orders"] == 1)


def deleted_rows_are_ignored():
    wipe()
    row("live", "sent no rd", "B22")
    r = row("removed", "sent rd", "B22")
    with db.connect() as c:
        c.execute("UPDATE leluxe_orders SET deleted=1 WHERE id=?", (r,))
    a = by_code(account_rd.rollup())
    check("a deleted row does not condemn an account", a["B22"]["rd"] == 0)


def endpoint_needs_the_worker_token():
    """_worker_ok or nothing: the route carries buyer-account history and is
    reachable from another host, so an unauthenticated 200 would be a leak."""
    os.environ["OTLOBLY_WORKER_TOKEN"] = "sekret"
    import app as app_mod
    cl = app_mod.app.test_client()
    check("no token → 401", cl.get("/api/worker/account_rd").status_code == 401)
    check("wrong token → 401", cl.get("/api/worker/account_rd",
                                      headers={"Authorization": "Bearer nope"}).status_code == 401)
    r = cl.get("/api/worker/account_rd", headers={"Authorization": "Bearer sekret"})
    check("right token → 200", r.status_code == 200)
    body = r.get_json() or {}
    check("payload carries the accounts", body.get("ok") is True
          and isinstance(body.get("accounts"), list))
    check("and says which list it came from", "source_list_id" in body)


def main():
    db.init_db()
    _setup_schema()
    print("one RD condemns:");        one_rd_condemns()
    print("board's own spellings:");  rd_vocabulary_is_the_boards_own()
    print("BAN is final:");           ban_is_final()
    print("BAN row order:");          order_of_ban_rows_does_not_matter()
    print("newest RD wins:");         newest_rd_wins()
    print("date anchor order:");      date_anchors_fall_through_in_order()
    print("only delivered dates:");   only_delivered_dates_an_rd()
    print("parent package date:");    tracking_falls_back_to_the_parent_package()
    print("undated RD reported:");    undated_rd_is_reported_never_guessed()
    print("every NAME option:");      every_name_option_reaches_the_answer()
    print("stale dropdown cache:");   code_missing_from_a_stale_dropdown_still_counts()
    print("housekeeping ignored:");   housekeeping_rows_are_not_board_work()
    print("deleted ignored:");        deleted_rows_are_ignored()
    print("endpoint gated:");         endpoint_needs_the_worker_token()
    print()
    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("all account_rd checks passed ✓")


if __name__ == "__main__":
    main()
