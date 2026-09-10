#!/usr/bin/env python3
"""
Self-checks (shell-string) for the ClickUp-style activity monitor + the ⚠ NAME
vs FIELD quantity warning on the Leluxe board.

Pins the structure the owner asked for after a package read 50 units when it
held 30: a per-row Activity rail beside the fields (image: ClickUp's task
panel), old → new wording in every feed, and one canonical unit count that makes
the NAME/FIELD fork visible instead of silent.

    ./.venv/bin/python test_activity_monitor_ui.py
"""

from pathlib import Path

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    html = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    nows = html.replace(" ", "")

    # 1) The feed renders BOTH sides. Every event has carried `old` since day
    #    one; actText only ever showed `new`.
    print("old → new in the activity feed:")
    actwas = html.split("function actWas(", 1)[-1].split("\n", 1)[0]
    check("actWas() renders the old value struck through",
          "function actWas(" in html and "<s " in actwas and "poEsc(v)" in actwas)
    check("'changed FIELD from OLD to NEW' wording exists",
          "changed <b>${poEsc(e.field)}</b> from" in html)
    check("  …still falls back to 'set … to …' with no old value",
          "set <b>${poEsc(e.field)}</b> to" in html)
    check("the new 'moved' action renders", 'e.action==="moved"' in html)
    check("the new 'removed' action renders", 'e.action==="removed"' in html)
    # A "set" with no field renders its detail AS the main line (every event
    # logged before per-field diffs existed) — a sub-line would say it twice.
    check("only a FIELDED set (or a create) gets a detail sub-line",
          'e.detail&&((e.action==="set"&&e.field)||e.action==="created")' in nows)

    # 2) The detail panel: fields left, Activity right.
    print("ClickUp-style detail panel:")
    check("two-column grid class defined", ".lx-det{display:grid" in nows)
    check("  …with a dedicated activity rail", ".lx-det-act" in html)
    check("  …that scrolls on its own", ".lx-det-act.actfeed{max-height" in nows)
    check("  …and collapses to one column on narrow screens",
          "@media(max-width:900px)" in nows and ".lx-det{grid-template-columns:minmax(0,1fr)}" in nows)
    check("the popup body opens the grid", 'class="lx-det"' in html)
    check("the activity mount point exists", 'id="lxDetAct"' in html)
    check("lxDetActivity() loads it", "async function lxDetActivity(" in html)
    check("  …through the bulk ids= endpoint",
          '/api/activity?entity=leluxe&limit=120&ids=' in html)
    check("  …reuses the shared actRow renderer", "evs.map(actRow).join" in html)
    check("  …surfaces a truncated read", "d.truncated" in nows.replace("&&", "&&"))
    check("  …and survives the popup closing mid-fetch",
          'if(!$("lxDetAct")) return;' in html)
    check("an order rolls its packages + products into one request",
          "const actIds=" in html and "lxDetActivity(actIds)" in html)
    check("the modal is wide enough for two columns",
          'class="az-box" style="width:1020px' in html)

    # 3) One canonical quantity, and the fork made visible.
    print("quantity: one accessor, mismatch surfaced:")
    check("lxNameQty reads the NAME's leading number", "function lxNameQty(" in html)
    check("lxQtyOf returns field + name + mismatch",
          "function lxQtyOf(" in html and "mismatch:(field!=null&&name!=null&&field!==name)" in nows)
    check("lxUnits sums through it", "function lxUnits(" in html
          and "lxQtyOf(it).units" in html)
    check("lxCountLbl uses lxUnits, not its own reduce",
          "const any=items.some(it=>lxQtyOf(it).field>0), s=lxUnits(items);" in html)
    check("  …and shows a ⚠ count when rows disagree",
          'tonePill("red",`⚠ ${bad}`' in html)
    check("lxQtyWarn is a tonePill, never hand-rolled",
          "function lxQtyWarn(" in html and 'tonePill("red","⚠"' in html)
    check("the ⚠ rides beside ×N on the product row",
          html.count("${lxQtyWarn(it)}") >= 3)
    check("the Products Σ footer reads the canonical field",
          "a+(lxQtyOf(it).field||0)" in nows)
    check("  …and flags mismatched rows in the total",
          "const totBad=shown.filter(([it])=>lxQtyOf(it).mismatch).length;" in html)
    check("the qty column sort reads it too",
          'if(key==="qty") return lxQtyOf(it).field||0;' in html)
    check("the detail panel explains the fork in both languages",
          "quantity mismatch" in html and "الكمية غير متطابقة" in html)
    check("  …naming AZ (2) as the other counter",
          "the AZ (2) push counts" in html)

    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}): {fails}")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
