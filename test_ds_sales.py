#!/usr/bin/env python3
"""
Batch B3 — the information the Orders board lost in Batch B, and why it was lost.

Batch B moved Orders onto the design system and quietly cost the owner five things.
Every check here exists because one of them went missing; a later batch must not
undo any of it:

  * report.py forwards the product TITLE and IMAGE. They were always in the order
    document — store.py reads them — and this one serializer dropped them, which is
    the whole reason the Orders board could not show a product photo;
  * the Tracking column is visible again (owner, 2026-09-07);
  * the products cell shows the products, not just how many there are;
  * the row expansion never prints the same ASIN twice;
  * the table bar — which carries the ONLY control that unhides a column — renders
    ABOVE the rows. Under 61 orders it was ~2,800px down the page;
  * product photos and the sub-table grid are defined ONCE, in ds.js. They lived in
    fulfillment.js and were nearly copied a second time into sales.js.

Batch B4 added the parcel to the row. The rule the owner set for it was
"do not re do everything i did before" and "ever package should have it's own
status like GWD", so the checks below are mostly about NOT having built a second
copy of something, and about each parcel keeping its own identity.

    ./.venv/bin/python test_ds_sales.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DS = HERE / "static" / "ds"
fails = []


def code_only(src):
    """Strip comments. Several checks below ask which file BUILDS a piece of markup,
    and the comments naming the old broken class would otherwise answer for it."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in src.splitlines())


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    sales = (DS / "sales.js").read_text(encoding="utf-8")
    ds = (DS / "ds.js").read_text(encoding="utf-8")
    ful = (DS / "fulfillment.js").read_text(encoding="utf-8")
    table = (DS / "table.js").read_text(encoding="utf-8")
    shell = (DS / "shell.js").read_text(encoding="utf-8")
    tokens = (DS / "tokens.css").read_text(encoding="utf-8")
    css = (DS / "ds.css").read_text(encoding="utf-8")
    # purchases.js still carries its own copy of the sub-table builder, so the rules
    # about flexible-column sizing have to be checked against both.
    pur = (DS / "purchases.js").read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    rep = (HERE / "report.py").read_text(encoding="utf-8")

    # ---- 1. the server stopped throwing the photos away ---------------------
    items = re.search(r'"items": \[\{(.*?)\} for it in o\["items"\]\]', rep, re.S)
    check("report.py builds the item dict", bool(items))
    body = items.group(1) if items else ""
    for key in ("asin", "url", "title", "image", "qty", "needs_expand"):
        check(f'  /api/report carries item "{key}"', f'"{key}"' in body)

    # ---- 2. the columns the owner asked for --------------------------------
    orders_src, cust_src = sales.split("function custColumns")[0], "function custColumns" + sales.split("function custColumns")[1]
    cols = {m.group(1): m.group(0) for m in re.finditer(r'\{ key: "(\w+)", label: "[^"]*".*?(?=\n      [\{m]|\n    \])', orders_src, re.S)}
    # B3 made the order's own OTL the visible "Tracking". B4 reversed that on the
    # owner's instruction: the number people work from is the parcel's GWD, so THAT
    # is now the visible Tracking column and the OTL is one click away. The point of
    # the check is unchanged — a tracking number is on the board by default.
    check("a Tracking column is visible by default", "defaultHidden" not in cols.get("gwd", "x defaultHidden"))
    check("and it is the parcel's GWD", 'key: "gwd", label: "Tracking"' in orders_src)
    check("Amazon # is still reachable, just folded away", "defaultHidden" in cols.get("amazon", ""))
    check("Address is still reachable, just folded away", "defaultHidden" in cols.get("address", ""))
    for k in ("who", "items", "amount", "deposit", "remaining", "due", "attention",
              "box", "batch", "city", "status", "actions"):
        check(f"  column {k} survives", k in cols)

    # ---- 3. the products cell shows products -------------------------------
    check("the products cell renders photos, not just a count", "D.thumbs(o.items" in sales)
    check("and the dead count-only markup is gone",
          "ds-sl-items" not in sales and "ds-sl-items" not in css)
    check("Needs attention keeps its full width (three pills can land in it)",
          re.search(r'key: "attention".*?w: 190', sales, re.S) is not None)

    # ---- 4. the expansion never prints the ASIN twice ----------------------
    exp = re.search(r"expandable: \{(.*?)\n      \},", sales, re.S).group(1)
    check("the expansion uses the shared sub-table", "D.subTable(" in exp)
    check("the hand-rolled near-miss class is no longer emitted (only named in the comment that explains it)",
          "ds-pu-subrow" not in code_only(sales) and "ds-pu-subrow" not in code_only(ful))
    check("a title is only used when it is not just the ASIN",
          "it.title.trim() !== it.asin" in exp)
    check("so the ASIN column that duplicated it is gone", 'label: "ASIN"' not in exp)

    # ---- 5. the escape hatch is reachable ----------------------------------
    render = re.search(r"style=\"\$\{style\}\">(.*?)</div>`;", table, re.S).group(1)
    bar, head = render.find("this.bar()"), render.find("this.head(pins)")
    check("the table bar renders BEFORE the header and the rows", 0 <= bar < head)
    check("the bulk bar still renders last (it is sticky to the viewport)",
          render.rfind("this.bulkbar()") > render.find("ds-table-scroll"))
    check("and the bar's border moved with it",
          ".ds-table-bar {" in css and "border-bottom: 1px solid var(--ds-line); font-size: var(--ds-t-sm)" in css)

    # ---- 6. one implementation, not three ----------------------------------
    check("DS.thumb / DS.thumbs live in ds.js", "DS.thumb = " in ds and "DS.thumbs = " in ds)
    check("DS.subTable lives in ds.js", "DS.subTable = " in ds)
    # what matters is that only ONE file builds this markup — a delegating alias is fine.
    lelu = (DS / "leluxe.js").read_text(encoding="utf-8")
    EVERY = (("ds.js", ds), ("fulfillment.js", ful), ("sales.js", sales),
             ("purchases.js", pur), ("leluxe.js", lelu))
    # The sub-table classes are checked against the ORIGINAL three files, on purpose:
    # purchases.js still carries a private copy of the grid builder, and retiring it is
    # its own task. Widening these two would just record a duplicate we already know
    # about as a red test, every run, until then.
    SUBTABLE = (("ds.js", ds), ("fulfillment.js", ful), ("sales.js", sales))
    for cls, files in (("ds-pu-thumb", EVERY), ("ds-fl-thumbs", EVERY),
                       ("ds-pu-sub-row", SUBTABLE), ("ds-pu-td", SUBTABLE)):
        # purchases.js was NOT in the photo tuple, which is how a drifted private copy of
        # DS.thumb lived there for months, quietly missing the tooltip and lazy-loading.
        owners = [n for n, src in files if cls in code_only(src)]
        check(f"only ds.js builds .{cls} (found in: {owners})", owners == ["ds.js"])
    check("fulfillment.js delegates rather than re-implements",
          "const thumb = D.thumb;" in ful and "D.thumbs(items, { max })" in ful and "const grid = D.subTable;" in ful)

    # ---- 7. the photo backfill ---------------------------------------------
    check("the Orders ⋯ menu can fetch missing photos",
          'onclick: "odFetchPhotos()"' in sales and "async function odFetchPhotos(" in idx)
    run = re.search(r"async function odFetchPhotos\(\)\{(.*?)\n\}", idx, re.S).group(1)
    # `at` never raises: a crashing suite reports no failed check at all, which reads
    # like a pass in the run_all_tests table until someone opens the traceback
    at = lambda hay, needle: hay.find(needle)
    check("it asks through the design system, not window.confirm",
          "DS.confirm({" in run and not re.search(r"(?<![.\w])confirm\(", run))
    # anchored on the SPENDING dialog specifically — the first DS.confirm in the
    # function is the "already running, stop?" guard and proves nothing
    check("it counts what is missing before it spends anything",
          -1 < at(run, "odPhotoTodo()") < at(run, 'confirmLabel:"Fetch photos"') < at(run, "fetch("))
    check("it shows the number in the confirmation", "todo.length} missing product" in run)
    check("it fetches one product at a time — never Promise.all",
          "Promise.all" not in run and "for(const t of todo)" in run)
    check("a repeated product is fetched once", "seen.get(key)" in run and "seen.set(key" in run)
    check("it can be stopped", "OD_PHOTOS_STOP" in run)
    check("it reports the first real reason, not just a count", "firstErr" in run)
    check("and it refreshes the board only if something changed", "if(ok) await load();" in run)

    # ---- 8. Batch B4: the row says what the parcel says ---------------------
    check("the phone left the identity cell", "ds-sl-phone" not in code_only(orders_src))
    check("but the order number stayed", 'class="ds-sl-id"' in sales)
    check("Customers keeps its phone", "ds-sl-phone" in code_only(cust_src))
    # "remove the deposit / city / tracking" AND "do not remove anything" — hidden, not deleted
    for k in ("deposit", "city", "tracking"):
        col = cols.get(k, "")
        check(f"{k} is hidden, not deleted", bool(col) and "defaultHidden" in col)
    check("the order's own OTL is renamed so it cannot be confused with the GWD",
          'key: "tracking", label: "OTL number"' in sales)
    for k, builder in (("gwd", "tracking_number"), ("customs", "W.pkgGaashPill"),
                       ("docs", "W.pkgDocsPill"), ("deadline", "W.pkgDeadlinePill")):
        check(f"the {k} cell renders through the app's own builder ({builder})", builder in cols.get(k, ""))
    check("no second pill implementation in sales.js",
          not re.search(r"function \w*(Pill|pill)\w*\s*\(", code_only(sales)))
    check("every parcel is rendered separately — nothing merges them",
          "function perParcel(" in sales and "list.map(one)" in sales)
    check("and the expansion gives each parcel its own row",
          "Parcels carrying ${o.order_id}" in sales and 'label: "Package status"' in sales)

    # ---- 9. three views, one implementation ---------------------------------
    check("Orders has the three-lens switcher",
          re.search(r"O\.BOARDS = \[(.*?)\]", sales, re.S) is not None)
    boards = re.findall(r'\{ key: "(\w+)", label: "[^"]+" \}', re.search(r"O\.BOARDS = \[(.*?)\];", sales, re.S).group(1))
    check(f"the views are orders / packages / products ({boards})", boards == ["orders", "packages", "products"])
    check("the filter bar draws them", 'onView: "odSetView(KEY)"' in sales)
    check("Packages and Products DELEGATE to the Purchases boards",
          "DS.purchases.board(mount, list," in idx and 'board:OD_BOARD' in idx)
    check("sales.js never builds a packages or products board of its own",
          "packagesBoard" not in sales and "productsBoard" not in sales)
    check("each view has an address", '"orders", "packages", "products"' in shell and "odSetView" in shell)
    check("and switching one writes it", "DS.shell2.syncTab()" in re.search(r"function odSetView\(v\)\{(.*?)\n\}", idx, re.S).group(1))
    check("the shell can read which view is open", "get odBoard(){ return OD_BOARD; }" in idx)

    # ---- 10. the join -------------------------------------------------------
    ix = re.search(r"function odPkgIndex\(\)\{(.*?)\n\}", idx, re.S).group(1)
    check("the join is on customer_order_id, the same key pkgprep uses server-side",
          "customer_order_id" in ix)
    check("a package counts once per order, however many of its items match",
          "seen.has(oid)" in ix and "seen.add(oid)" in ix)
    check("the index is built per POS load, not per row", "OD_PKGIX_FOR===POS" in ix)
    check("POS is fetched through the existing loader, not a new one",
          "await loadPurchases()" in idx and idx.count("fetch(\"/api/purchases\")") == 1)
    check("a GWD is searchable from the Orders search box", "pk.tracking_number||\"\"" in idx)

    # ---- 11. Batch B5: the sticky layer actually sticks ---------------------
    # `overflow: hidden` made .ds-table a scroll container that never scrolls, so EVERY
    # sticky descendant scrolled away with the page. Measured on the 61-row board: headers
    # at -76px, the Columns bar at -115px, the bulk bar at 2843px in a 900px viewport.
    tbl_rule = re.search(r"\n\.ds-table \{([^}]*)\}", css).group(1)
    check("the table clips WITHOUT becoming a scroll container",
          "overflow: clip" in tbl_rule and "overflow: hidden" not in tbl_rule)
    check("the bar carrying the Columns control is sticky",
          "position: sticky" in re.search(r"\.ds-table-bar \{([^}]*)\}", css).group(1))
    check("the column headers park below it, not behind it",
          "var(--ds-table-bar-h" in re.search(r"\.ds-table-headclip \{([^}]*)\}", css).group(1))
    check("and the offsets come from tokens, not magic numbers",
          "--ds-table-top: var(--ds-topbar-h)" in tokens and "--ds-table-bar-h:" in tokens)

    # the expansion is held by CSS now, not by a transform written on every scroll event
    expr = re.search(r"\.ds-tr-exp \{([^}]*)\}", css).group(1)
    check("the row expansion is position: sticky", "position: sticky" in expr)
    check("pinned with a logical property (a physical `left` fails the DS lint)",
          "inset-inline-start: 0" in expr and "left:" not in expr)
    check("the will-change hint went with the transform", "will-change" not in expr)
    check("the body is as wide as its rows, or sticky has nothing to travel in",
          "width: max-content" in re.search(r"\.ds-table-body \{([^}]*)\}", css).group(1))
    check("an empty table still fills the viewport",
          "min-width: 100%" in re.search(r"\.ds-table-body \{([^}]*)\}", css).group(1))
    sync = re.search(r"DS\.tableSync = \(scroller\) => \{(.*?)\n  \};", table, re.S).group(1)
    check("nothing writes a transform on scroll any more",
          "transform" not in code_only(sync) and "translateX" not in code_only(sync))
    check("an error message wraps instead of making its own scrollbar",
          "max-inline-size: 640px" in re.search(r"\.ds-error-state \{([^}]*)\}", css).group(1))

    # A sub-table's flexible column stops growing before it swallows the panel — and,
    # just as important, stops SHRINKING before it disappears. This used to assert the
    # literal "minmax(0,var(--ds-pu-flex,620px))", which pinned the 0 in place: grid
    # resolves a flexible track to its minimum before it will overflow, so whenever the
    # fixed columns did not fit, the column carrying the identity was deleted outright
    # while the fixed ones kept their pixels. Both builders are checked, because
    # purchases.js still carries its own copy.
    # code_only: both builders now carry a comment quoting the old broken value, and a
    # comment must not be able to fail — or pass — a check about what the file BUILDS.
    ds_code, pur_code = code_only(ds), code_only(pur)
    check("neither sub-table builder emits a 1fr flexible column",
          "minmax(0,1fr)" not in ds_code and "minmax(0,1fr)" not in pur_code)
    check("a width-less sub-table column keeps the 620px cap",
          "var(--ds-pu-flex,620px)" in ds_code and "var(--ds-pu-flex,620px)" in pur_code)
    check("...and can never resolve to a zero-width track",
          "minmax(0px," not in ds_code and "minmax(0," not in ds_code
          and "minmax(0px," not in pur_code and "minmax(0," not in pur_code)
    check("Still owed is hidden, not deleted", "defaultHidden" in cols.get("remaining", ""))
    check("DS.empty no longer drops the `hint` its callers pass",
          "o.text || o.hint" in ds and 'hint: "Change the search' in sales)

    # ---- 12. purchases.js is text again -------------------------------------
    raw = (DS / "purchases.js").read_bytes()
    check("purchases.js carries no NUL byte — grep can read it again", b"\x00" not in raw)

    # ---- 13. it runs --------------------------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed check")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}}, querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        for (const f of ["status.js","format.js","ds.js","table.js","purchases.js","fulfillment.js","sales.js"]) require({str(DS)!r} + "/" + f);
        const D = window.DS;
        const one = [{{asin:"B01", title:"Kettle", image:"http://x/a.jpg"}}];
        const none = [{{asin:"B01"}}, {{asin:"B02"}}];
        console.log(JSON.stringify({{
          photo:   D.thumbs(one).includes("<img"),
          noPhoto: D.thumbs(none).includes("2 products") && !D.thumbs(none).includes("<img"),
          empty:   D.thumbs([]) === D.dash(),
          subrow:  D.subTable([{{label:"A", render:()=> "x"}}], [{{}}]).includes("ds-pu-sub-row"),
          subtd:   D.subTable([{{label:"A", render:()=> "x"}}], [{{}}]).includes("ds-pu-td"),
          // the emitted grid template, so the floor is asserted as BEHAVIOUR, not as a
          // substring someone can satisfy by accident
          tplDefault: (D.subTable([{{label:"A", render:()=> "x"}}], [{{}}]).match(/--ds-pu-cols:([^"]*)/)||[])[1],
          tplMin:     (D.subTable([{{label:"A", min:240, render:()=> "x"}}], [{{}}]).match(/--ds-pu-cols:([^"]*)/)||[])[1],
          tplFixed:   (D.subTable([{{label:"A", w:120, render:()=> "x"}}], [{{}}]).match(/--ds-pu-cols:([^"]*)/)||[])[1],
          boards:  (D.orders.BOARDS||[]).map(b => b.key).join(","),
          delegate: typeof D.purchases.board === "function",
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("the modules run under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:3])
        else:
            import json as _json
            o = _json.loads(r.stdout.strip().splitlines()[-1])
            check("an item with a photo renders one", o["photo"])
            check("items with no photo fall back to the plain count", o["noPhoto"])
            check("no products at all renders the em dash", o["empty"])
            check("the sub-table emits the class the CSS actually styles", o["subrow"] and o["subtd"])
            # A flexible track that can reach 0 is how the Leluxe product column and the
            # Purchases package column both vanished: grid shrinks it to its minimum before
            # it will overflow, so the escape hatch (overflow-x:auto) never gets its turn.
            m = re.match(r"minmax\((\d+)px,var\(--ds-pu-flex,620px\)\)$", (o["tplDefault"] or "").strip())
            check("a width-less sub-table column has a NON-ZERO floor", bool(m) and int(m.group(1)) > 0)
            check("and per-column `min` overrides it", (o["tplMin"] or "").strip().startswith("minmax(240px,"))
            check("a column with an explicit width is still exactly that wide",
                  (o["tplFixed"] or "").strip() == "120px")
            check("the three views are registered in order", o["boards"] == "orders,packages,products")
            check("the board they delegate to really exists", o["delegate"])

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
