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
    css = (DS / "ds.css").read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    rep = (HERE / "report.py").read_text(encoding="utf-8")

    # ---- 1. the server stopped throwing the photos away ---------------------
    items = re.search(r'"items": \[\{(.*?)\} for it in o\["items"\]\]', rep, re.S)
    check("report.py builds the item dict", bool(items))
    body = items.group(1) if items else ""
    for key in ("asin", "url", "title", "image", "qty", "needs_expand"):
        check(f'  /api/report carries item "{key}"', f'"{key}"' in body)

    # ---- 2. the columns the owner asked for --------------------------------
    cols = {m.group(1): m.group(0) for m in re.finditer(r'\{ key: "(\w+)", label: "[^"]*".*?(?=\n      [\{m]|\n    \])', sales, re.S)}
    check("Tracking is a column", "tracking" in cols)
    check("Tracking is VISIBLE (not defaultHidden)", "defaultHidden" not in cols.get("tracking", "x defaultHidden"))
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
    # what matters is that only ONE file builds this markup — a delegating alias is fine
    for cls in ("ds-pu-thumb", "ds-fl-thumbs", "ds-pu-sub-row", "ds-pu-td"):
        owners = [n for n, src in (("ds.js", ds), ("fulfillment.js", ful), ("sales.js", sales)) if cls in code_only(src)]
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

    # ---- 8. it runs ---------------------------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed check")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}}, querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        for (const f of ["status.js","format.js","ds.js","table.js","fulfillment.js","sales.js"]) require({str(DS)!r} + "/" + f);
        const D = window.DS;
        const one = [{{asin:"B01", title:"Kettle", image:"http://x/a.jpg"}}];
        const none = [{{asin:"B01"}}, {{asin:"B02"}}];
        console.log(JSON.stringify({{
          photo:   D.thumbs(one).includes("<img"),
          noPhoto: D.thumbs(none).includes("2 products") && !D.thumbs(none).includes("<img"),
          empty:   D.thumbs([]) === D.dash(),
          subrow:  D.subTable([{{label:"A", render:()=> "x"}}], [{{}}]).includes("ds-pu-sub-row"),
          subtd:   D.subTable([{{label:"A", render:()=> "x"}}], [{{}}]).includes("ds-pu-td"),
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

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
