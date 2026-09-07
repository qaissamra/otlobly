#!/usr/bin/env python3
"""
Self-checks: ClickUp-style due-date urgency chips in the staff shell.

The dueChip() helper renders a colored relative chip (red "Nd late" / orange
"today"/"in Nd" / muted later) wherever a due date appears: the Orders table's
new Due column, the To-order summary row + ETA field, and the Purchases page
package "Arrives" fields (+ the PO detail modal). Pure UI — these checks pin
the helper's contract and every anchor in web/index.html so a refactor can't
silently drop the urgency treatment.

    ./.venv/bin/python test_due_chips.py
"""

import re
from pathlib import Path

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    # 1) The helper exists and parses BOTH date formats (ISO + legacy dd/mm/yyyy).
    check("dueChip helper exists", "function dueChip(" in html)
    check("dueDays parses ISO yyyy-mm-dd", re.search(r"dueDays[\s\S]{0,200}\\d\{4\}\)-\(\\d\{2\}\)-\(\\d\{2\}", html) is not None)
    check("dueDays parses legacy dd/mm/yyyy", r"(\d{1,2})\/(\d{1,2})\/(\d{4})" in html)
    check("late = red (var(--bad))", "d late" in html and "color:var(--bad)" in html)
    check("today/soon = orange (var(--warn))", '"today"' in html)

    # 2) Orders board: the Due column + the row cell, done statuses quieted.
    #    2026-08-04: the plain <table> became the LXT "od" board.
    #    2026-09-07 (Batch B): the "od" board became a design-system DataTable in
    #    static/ds/sales.js, so the same three requirements are asserted THERE.
    #    The label is English now (owner decision: English only for now); the rule
    #    it encodes - a promised date, quiet once the order is done - is unchanged.
    sales = (Path(__file__).parent / "static" / "ds" / "sales.js").read_text(encoding="utf-8")
    check("Orders board has the Due column",
          'key: "due", label: "Promised"' in sales)
    check("Orders row renders dueChip on est_delivery_customer",
          'W.dueChip(o.est_delivery_customer, ["DELIVERED", "COLLECTED", "CANCELLED"]' in sales)
    check("Orders totals row survives the board move",
          "orderFooter" in sales and 'order${rows.length === 1 ? "" : "s"}' in sales)
    check("and its Σ still counts only the open statuses",
          'const OD_OPEN=["REQUESTED","QUOTED","ORDERED","SHIPPED","ARRIVED","DELIVERED"];' in html)

    # 3) Purchases: (re-anchored 2026-08-04) the redesign-era pkgStatusPill was
    #    split into pkgDatePill (promised arrival, muted once DELIVERED) and
    #    pkgDeadlinePill (GAASH lost-forever countdown) — both driven by dueDays.
    check("package urgency pills use the due date (dueDays)",
          "function pkgDatePill(" in html and "function pkgDeadlinePill(" in html
          and "dueDays(pk&&pk.arrival)" in html)
    check("delivered parcels never show 'late' (pills mute on DELIVERED/cleared)",
          "if(pkgStatus(pk)===\"DELIVERED\") return ''" in html
          and "b==='cleared'||b==='delivered'" in html)
    check("PO detail modal package header carries a chip", "يصل ${poEsc(pk.arrival)} ${dueChip(pk.arrival)}" in html)

    # 4) To-order: the summary row (next to the status pill) + the expanded ETA field.
    # Since Phase 4 the queue is a DataTable in static/ds/fulfillment.js: the promised
    # date is its own column and still carries the chip, next to the status column.
    ful = (Path(__file__).parent / "static" / "ds" / "fulfillment.js").read_text(encoding="utf-8")
    check("To-order shows the promised date with its chip, in its own column",
          'key: "promised", label: "Promised"' in ful and "W.dueChip(o.est_delivery_customer," in ful)
    check("and the row's detail repeats it as a fact",
          '["Promised", `${esc(o.est_delivery_customer)} ${W.dueChip(o.est_delivery_customer)}`]' in ful)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
