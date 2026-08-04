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
    #    (2026-08-04: the plain <table> became the LXT "od" board — the column
    #    now lives in LXT_COLS.od and the totals in a bt-total row, not a tfoot.)
    check("Orders board has the Due column",
          '"od":[' in html and 'الموعد · due' in html)
    check("Orders row renders dueChip on est_delivery_customer",
          'dueChip(o.est_delivery_customer,["DELIVERED","COLLECTED","CANCELLED"]' in html)
    check("Orders totals row survives the board move", 'Σ ${rows.length} ${T("طلب · orders")}' in html)

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
    check("To-order summary row shows the chip next to status",
          "${statusCell}${o.est_delivery_customer?" in html)
    check("To-order ETA meta field carries a chip",
          "fld('ETA',`${poEsc(o.est_delivery_customer)} ${dueChip(o.est_delivery_customer)}`)" in html)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
