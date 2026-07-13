#!/usr/bin/env python3
"""
Self-checks (shell-string) for the Purchases page redesign:
collapsed PO rows, filter tabs, one status line per package, ⋯ overflow menus,
GAASH-only tracking inline, edit drawers, and status-only colour.

These pin the structural markers so a future refactor can't silently regress the
information hierarchy the owner asked for.

    ./.venv/bin/python test_po_purchases_redesign.py
"""

from pathlib import Path

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")

    # 1) Filter tabs + state.
    check("filter definitions exist", "const PO_FILTERS=" in html and "PO_FILTER" in html)
    check("tab labels present",
          all(s in html for s in ('lab:"Late"', 'lab:"Arriving this week"', 'lab:"No tracking"')))
    check("filters container in the toolbar", 'id="poFilters"' in html and "po-tab" in html)

    # 2) Collapse-by-default tri-state (late POs auto-open, manual toggles stick).
    check("PO view tri-state helpers",
          all(s in html for s in ("function poIsOpen(", "function poToggleCard(",
                                  "function poNeedsAction(", "PO_VIEW", '"po_view"')))
    check("auto-expand keys on late packages", "poNeedsAction=" in html.replace(" ", "")
          or "function poNeedsAction(p){ return (p.packages||[]).some(pkgLate)" in html)

    # 3) Status + one-date helpers.
    check("status/date helpers exist",
          all(s in html for s in ("function poSummaryPill(", "function pkgStatusPill(",
                                  "function fmtDue(", "function tonePill(")))
    check("late = red, arriving = green tones",
          "days late" in html and "Arrives" in html and "TONE=" in html)

    # 4) One status line per package; GAASH labeled; OTL not inline.
    check("package status line class", "pkg-statusline" in html)
    check("GAASH label inline", "GAASH" in html)
    check("no editable Arrives date input in the package row",
          "poPkgSet('${p.po_id}',${pi},'arrival',this.value)" not in html)
    check("no GWD tracking input in the package row",
          "class=\"mono trk-input\"" not in html)

    # 5) One colour: no accent (orange) button left on a package row.
    check("Notify is no longer an accent button",
          'accent" title="WhatsApp each customer' not in html)
    check("Add product is no longer an accent button",
          'accent" title="add a product to this package' not in html)
    check("Send-to-ClickUp no longer an inline accent button",
          'accent" title="create this order in ClickUp' not in html)

    # 6) ⋯ overflow menus, with Notify inside.
    check("popover menu component", "function popMenu(" in html and ".pop-menu" in html
          and "function popToggle(" in html)
    check("Notify lives inside a menu", "Notify customers" in html and "pkgNotifyOpen(" in html)
    check("package menu also holds check-shipping + delete",
          "Check shipping" in html and "Delete package" in html)

    # 7) Edit drawers hold what left the rows.
    check("package edit drawer",
          'id="pkgEditModal"' in html and "function pkgEditOpen(" in html
          and "Customer tracking" in html)   # OTL now lives in the drawer
    check("order edit drawer",
          'id="poEditModal"' in html and "function poEditOpen(" in html
          and "Amazon order #" in html)

    # 7b) The profile/box name shows on the PO row (needed at a glance).
    check("PO row shows the profile chip",
          "p.profile_box||''" in html and "🖥 ${poEsc(p.profile_box)}" in html)

    # 8) #28 read-view still intact (grouped rows, item editor, 3-word clip).
    check("customer-grouped item rows survive", 'class="poc-cust' in html and "itemEditOpen(" in html)
    check("3-word name clip survives", "const short3=" in html)

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
