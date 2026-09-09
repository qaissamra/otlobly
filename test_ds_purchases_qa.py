#!/usr/bin/env python3
"""
The Purchases QA sweep (2026-09-09) — nine defects, and the checks that keep them fixed.

Walking all four Purchases boards as a staff user turned up three controls that did
nothing when you pressed them, two that quietly changed what you were looking at, and
four about what was on screen. Every check below exists because of one of them:

  * the search box lost focus after EVERY keystroke — the chrome is re-rendered on
    each `input` (its counts change) and `innerHTML` destroyed the very field being
    typed into, so only the first letter ever landed. It also rewrote what you typed
    with the page's normalised copy, lower-casing capitals under the cursor;
  * "Expand / Collapse every order" only reached the Orders board. Packages and
    Customers have expandable rows kept in other stores and did not move; Products has
    nothing to expand and was offered the item anyway;
  * jumping to an order from another board left the URL naming the OLD board, so a
    refresh threw you back to it with the order closed;
  * blanks sorted to the TOP. The pages mark "no value" with a "~" sentinel expecting
    ASCII order, but the table compares with localeCompare, where "~" collates FIRST;
  * the row-IDENTITY column could be switched off, leaving rows with nothing naming
    them. Sales already locked its identity column; nowhere else did;
  * three boards shipped every column visible, so most of each board sat past the
    right edge at the window the owner actually uses;
  * every panel these boards open is a legacy `.az-modal` div: no Escape, focus never
    entered it, and nothing announced it as a dialog;
  * the Columns menu ended with a blank row and a dead switch (the "⋯" column);
  * the board said "Buying account" and the form it opened said "Profile".

    ./.venv/bin/python test_ds_purchases_qa.py
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DS = HERE / "static" / "ds"
fails = []


def code_only(src):
    """Strip comments — every fix below is described in a comment that names the old
    broken value, and a comment must not be able to pass (or fail) a check about what
    the file actually does."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in src.splitlines())


def between(src, a, b):
    """The text between two markers, or "" if either is missing — a check about code
    that does not exist yet must FAIL, not kill the run with a traceback."""
    i = src.find(a)
    if i < 0:
        return ""
    j = src.find(b, i)
    return src[i:j if j > 0 else len(src)]


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def col_blocks(src, fn):
    """The column literals of one board function, brace-matched (they span lines)."""
    i = src.index("function %s(" % fn)
    j = src.index("\n  function ", i + 1) if "\n  function " in src[i + 1:] else len(src)
    body = src[i:j]
    out = {}
    for m in re.finditer(r'\{\s*key:\s*"([^"]+)"', body):
        d, k = 0, m.start()
        while k < len(body):
            c = body[k]
            if c == "{":
                d += 1
            elif c == "}":
                d -= 1
                if d == 0:
                    break
            elif c in "\"'`":
                q = c
                k += 1
                while k < len(body) and body[k] != q:
                    if body[k] == "\\":
                        k += 1
                    k += 1
            k += 1
        out[m.group(1)] = body[m.start():k + 1]
    return out


def main():
    pur = (DS / "purchases.js").read_text(encoding="utf-8")
    ds = (DS / "ds.js").read_text(encoding="utf-8")
    table = (DS / "table.js").read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    sales = (DS / "sales.js").read_text(encoding="utf-8")

    # ---- 1. the caret stays in the search box -------------------------------
    check("DS.paintHost exists — one place that repaints a chrome host",
          "DS.paintHost = " in ds)
    for name, src in (("purchases.js", pur), ("sales.js", sales)):
        c = code_only(src)
        check(f"  {name} repaints its chrome through paintHost", "D.paintHost(el, header + bar)" in c)
        check(f"  {name} no longer blows the field away with innerHTML",
              "el.innerHTML = header + bar" not in c)

    # ---- 2. expand / collapse reaches the board you are on ------------------
    check("the table can open or close every row it has", "DS.tableSetAllOpen = " in table)
    check("...and a page can ask whether it has any", "DS.tableExpandable = " in table)
    view_all = re.search(r"function poViewAll\(open\)\{(.*?)\n\}", idx, re.S)
    check("poViewAll exists", bool(view_all))
    body = view_all.group(1) if view_all else ""
    check("  it handles the Customers board", 'PO_BOARD_VIEW==="customers"' in body)
    check("  it handles the Packages board", 'DS.tableSetAllOpen("po_packages"' in body)
    check("  and still handles Orders", "PO_VIEW[p.po_id]" in body)
    check("the menu items are withheld where they cannot act",
          "ctx.canExpand === false ? [] : [" in pur and "canExpand: PO_BOARD_VIEW!==" in idx)

    # ---- 3. a jump lands on a board the URL agrees with --------------------
    jump = re.search(r"function poJumpOrder\(id\)\{(.*?)\n", idx, re.S)
    body = idx[idx.index("function poJumpOrder(id)"):idx.index("function poJumpOrder(id)") + 400]
    check("poJumpOrder syncs the route, exactly as poSetView does",
          "DS.shell2.syncTab" in body.split("function poOrderMap")[0])

    # ---- 4. blanks sort last ------------------------------------------------
    tc = code_only(table)
    check("the comparator knows the pages' blank sentinel", "const blank = (v)" in tc)
    check("  and does not lean on ASCII order for it", '/^~+$/' in tc)

    # ---- 5. the row-identity column cannot be switched off ------------------
    unlocked = []
    for f in sorted(DS.glob("*.js")):
        if f.name == "table.js":
            continue
        src = code_only(f.read_text(encoding="utf-8"))
        for m in re.finditer(r'\{\s*key:\s*"([^"]+)"[^\n]*?pin:\s*"start"[^\n]*', src):
            if "locked: true" not in m.group(0):
                unlocked.append(f"{f.name}:{m.group(1)}")
    check(f"every start-pinned identity column is locked (loose: {unlocked})", not unlocked)

    # ---- 6. the boards fit the window they are used in ---------------------
    # Folded columns REPEAT a fact from the row's parent (order name, buying account,
    # both shown one level up) or serve one sub-task (ID at customs, RD at refund
    # time, last mile on Tracking). Nothing is deleted: the Columns button shows them.
    want = {
        "packagesBoard": {"oname", "profile", "lastmile", "idnum", "rd"},
        "productsBoard": {"oname", "profile", "idnum"},
        "customersBoard": {"idnum"},
    }
    for fn, keys in want.items():
        cols = col_blocks(pur, fn)
        for k in keys:
            check(f"  {fn}: {k} is folded away, not deleted",
                  k in cols and "defaultHidden: true" in cols[k])
        # and the columns the board is SCANNED for stay put
        for k in ("pkg", "product", "customer"):
            if k in cols and fn.startswith(k[:4]):
                check(f"  {fn}: {k} is still shown", "defaultHidden" not in cols[k])
    for tid in ("po_packages", "po_products", "po_customers"):
        check(f"  {tid} bumps seedVersion so saved layouts pick the change up",
              f'id: "{tid}", seedVersion:' in pur)

    # ---- 7. the legacy panels behave like dialogs ---------------------------
    az = idx[idx.index("function azModalA11y("):] if "function azModalA11y(" in idx else ""
    check("the .az-modal panels get dialog behaviour", bool(az))
    for bit, why in (('setAttribute("role","dialog")', "announced as a dialog"),
                     ('setAttribute("aria-modal","true")', "announced as modal"),
                     ('e.key==="Escape"', "Escape closes it"),
                     ('e.key==="Tab"', "Tab stays inside it"),
                     ("OPENER.get(m)", "focus goes back to whatever opened it")):
        check(f"  {why}", bit in az)
    # Escape closes a panel by triggering the panel's OWN handler, so every one of
    # them has to carry that handler — a panel without it would be uncloseable by key.
    # Count ELEMENTS, not mentions: the comment above this fix quotes the markup, and
    # a comment must not be able to answer a question about the markup.
    decl = len(re.findall(r'<div[^>]*class="az-modal hidden"', idx))
    own = len(re.findall(r'<div[^>]*class="az-modal hidden" onclick="if\(event\.target===this\)', idx))
    check(f"every .az-modal declares its own close handler ({own}/{decl})", decl and own == decl)

    # ---- 8. the Columns menu has no blank, dead row ------------------------
    check("the Columns menu lists only columns that have a name",
          "t.columns().filter((c) => c.label)" in table)

    # ---- 9. one word for the buying account --------------------------------
    check("there is one resolver for the term", "function poBoxTerm()" in idx)
    check("  the boards read it", "boxTerm: poBoxTerm()" in idx and "label: ctx.boxTerm" in pur)
    check("  no form hard-codes a second word", "box_term||'Profile'" not in idx)
    check("  and the panel is called what the button that opens it is called",
          "New purchase order</b>" in idx)

    # ---- 10. the filter builder is design-system controls -------------------
    # It used to be legacy .pop/.po-btn/minibtn markup — with a bare <select> and a
    # rubbish-bin emoji — sitting inside the design system's own filter bar.
    fltr = between(idx, "function poFilterRow(f,i){", "function poFSetField(i,j){")
    check("the filter row builder exists", bool(fltr))
    for bad, why in (('class="pop"', "no legacy popover"), ("pop-menu", "no legacy pop menu"),
                     ("po-btn", "no legacy button"), ("minibtn", "no legacy mini-button"),
                     ("cu-item", "no legacy menu item"), ("cu-search", "no legacy search"),
                     ("cu-ring", "no legacy colour ring"),
                     ("<select", "no hand-rolled select"), ("<input", "no hand-rolled input")):
        check(f"  the filter row builds {why}", bad not in code_only(fltr))
    for good in ("DS.button(", "DS.select(", "DS.input(", "DS.numberInput(", "DS.datePicker("):
        check(f"  it builds with {good}…)", good in fltr)
    check("  remove is an icon button with a real label, not an emoji",
          'icon:"trash"' in fltr and 'ariaLabel:"Remove this filter"' in fltr)
    check("  both pickers open in the shared design-system menu",
          "DS.menuOpenAt(" in between(idx, "function poFMenuOpen(", "function poFFieldMenu("))
    check("  the row's own value label is plain text, since DS.button escapes it",
          "function poFValLabel(sel)" in idx
          and "poEsc" not in between(idx, "function poFValLabel(sel)", "function poFMenuOpen("))
    # a menu that carries a search field must not eat the keys typed into it
    check("a menu with a search field keeps its typing keys", "const typing = a &&" in ds)
    # the field list speaks one language
    flds = between(idx, "function poFields(){", "function poFdef(key)")
    labels = re.findall(r'label:\s*"([^"]*)"', flds)
    arabic = [l for l in labels if any("\u0600" <= ch <= "\u06ff" for ch in l)]
    check(f"every filter field is named in one language (Arabic-first left: {arabic})", not arabic)
    check("  and the buying-account field follows the same term as the board",
          'label:poBoxTerm()+" · B##"' in flds)
    # the DS controls are `width:100%` by design; in a flex row each must be sized
    css_txt = (DS / "ds.css").read_text(encoding="utf-8")
    for k in ("ds-fltr-op", "ds-fltr-text", "ds-fltr-num", "ds-fltr-date"):
        m = re.search(r"\.%s \{([^}]*)\}" % k, css_txt)
        check(f"  .{k} is sized so the row cannot wrap", bool(m) and "flex: none" in m.group(1))
    check("  an empty builder takes no room", ".ds-fltr:empty { display: none; }" in css_txt)

    # ---- 11. it runs, and it behaves ---------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed checks")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}},
                                 querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        for (const f of ["status.js","format.js","ds.js","table.js","purchases.js","fulfillment.js","sales.js"])
          require({str(DS)!r} + "/" + f);
        const D = window.DS;

        // --- blanks sort last, however a page spells "no value" ---
        const rows = [{{id:"amin",n:"Amin"}},{{id:"blank",n:""}},{{id:"zoe",n:"zoe"}},
                      {{id:"tilde",n:"~"}},{{id:"tilde3",n:"~~~"}}];
        const order = (dir) => [...D.table({{ id:"probe_"+dir, sort:{{key:"n",dir}}, rows,
            columns:[{{key:"n", label:"Name", sortVal:r=>(r.n||"~")}}] }})
          .matchAll(/data-key="([^"]+)"/g)].map(m=>m[1]);

        // --- paintHost carries the typed field across the swap ---
        // A tiny stand-in for the chrome host: the point is that the value the PERSON
        // typed survives, not the normalised copy the page would have re-rendered.
        const fresh = {{ id:"q", value:"normalised", focused:false,
                        focus(){{this.focused=true}}, setSelectionRange(a,b){{this.sel=[a,b]}} }};
        const typing = {{ id:"q", tagName:"INPUT", value:"Wa", selectionStart:2, selectionEnd:2 }};
        const host = {{ innerHTML:"", contains:(n)=>n===typing, querySelector:()=>fresh }};
        globalThis.document.activeElement = typing;
        // guarded: on a tree without the fix this is simply absent, and the sort
        // assertions above it must still get to run and fail on their own merits
        if (typeof D.paintHost === "function") D.paintHost(host, "<em>repainted</em>");

        console.log(JSON.stringify({{
          asc: order("asc"), desc: order("desc"),
          setAllOpen: typeof D.tableSetAllOpen, expandable: typeof D.tableExpandable,
          painted: host.innerHTML,
          keptValue: fresh.value, keptFocus: fresh.focused, keptCaret: (fresh.sel||[]).join(","),
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("the modules run under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:4])
        else:
            o = json.loads(r.stdout.strip().splitlines()[-1])
            check("the modules run under node", True)
            # ascending: every real name, THEN every spelling of blank
            check(f"  ascending puts blanks last ({o['asc']})",
                  o["asc"][:2] == ["amin", "zoe"] and set(o["asc"][2:]) == {"blank", "tilde", "tilde3"})
            check(f"  descending reverses it ({o['desc']})", o["desc"][-2:] == ["zoe", "amin"])
            check("  a table can open or close all of its rows", o["setAllOpen"] == "function")
            check("  and can say whether it has any to open", o["expandable"] == "function")
            check("  paintHost still writes the new markup", o["painted"] == "<em>repainted</em>")
            check("  ...keeps the text as it was TYPED, not as the page normalised it",
                  o["keptValue"] == "Wa")
            check("  ...hands focus back", o["keptFocus"] is True)
            check("  ...and restores the caret", o["keptCaret"] == "2,2")

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all Purchases QA checks passed")


if __name__ == "__main__":
    main()
