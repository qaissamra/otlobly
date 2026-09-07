#!/usr/bin/env python3
"""
Phase 4 — To order, In cart and Package prep on the design system.

These three finish the Fulfillment pipeline that Purchase orders started. What is
pinned here is what a later phase must not quietly undo:

  * each page owns its header and its saved views, and the shell knows not to draw
    a second header over it;
  * every saved view has an address, so a link opens the view it points at;
  * the three hand-built list engines are GONE - the raw <table> with its own
    drag-resize, the LXT `ic` table, and the wall of cards - not left running;
  * the To-order queue keeps its selection and its one bulk action;
  * the cart still lets you type what Amazon charges and answers with the profit;
  * Package prep keeps every action it had, and its Arabic-only headings became
    English labels (owner decision, 2026-09-06) while customer-facing message
    text stays in Arabic;
  * every app function these pages call still exists.

    ./.venv/bin/python test_ds_fulfillment.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FUL = HERE / "static" / "ds" / "fulfillment.js"
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿]")


def main():
    ful = FUL.read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    shell = (HERE / "static" / "ds" / "shell.js").read_text(encoding="utf-8")
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")

    # ---- 1. shipped and wired ----------------------------------------------
    check("static/ds/fulfillment.js exists", FUL.exists())
    check("index.html loads it", '<script src="/static/ds/fulfillment.js"></script>' in idx)
    check("the service worker precaches it", "/static/ds/fulfillment.js" in sw)
    check("no emoji in the page module", not EMOJI.findall(ful))
    for ns in ["toOrder", "inCart", "pkgPrep"]:
        check(f"DS.{ns} is exported", f"D.{ns} = {{}}" in ful)

    # ---- 2. each page owns its header, and the shell yields -----------------
    for v in ["needorder", "incart", "pkgprep"]:
        check(f'"{v}" draws its own header (shell OWN_HEADER)', f'"{v}"' in re.search(r"OWN_HEADER = new Set\(\[([^\]]*)\]", shell).group(1))
    for title in ["To order", "In cart", "Package prep"]:
        check(f'the header says "{title}"', f'title: "{title}"' in ful)
    check("each carries the Fulfillment breadcrumb", ful.count('crumbs: [{ label: "Fulfillment" }') == 3)
    check("the old toolbars are gone",
          "Need to order</h2>" not in idx and 'id="cartCards"' not in idx and 'id="ppReady"' not in idx)

    # ---- 3. every saved view has an address --------------------------------
    tabs = re.search(r"const TABS = \{(.*?)\n  \};", shell, re.S).group(1)
    check("To-order buckets are routable", 'needorder: { keys: ["pending", "incart", "ordered", "deleted"]' in tabs)
    check("Package-prep buckets are routable", 'pkgprep: { keys: ["ready", "waiting", "reviews"]' in tabs)
    check("switching a To-order view writes the address", "neSetFilter(f){ NE_FILTER=f" in idx and "DS.shell2.syncTab()" in idx)
    check("so does switching a Package-prep view", "function ppSetView(v){" in idx and idx.count("DS.shell2.syncTab()") >= 3)
    check("the bridge tells the shell which view is open",
          "get neView(){ return NE_FILTER; }" in idx and "get ppView(){ return PP_VIEW; }" in idx)

    # ---- 4. no parallel systems --------------------------------------------
    for fn in ["neTable", "neRowHtml", "neItemRow", "neSection", "neChip", "neSecToggle",
               "neColDown", "neColReset", "neColStyle", "neRz", "neRowToggle", "neCartBar",
               "neCartToggle", "cartTable", "icSortVal", "ppCard", "ppReviewCard", "ppSection",
               "ppToggle", "ppFlds"]:
        check(f"{fn}() deleted", f"function {fn}(" not in idx)
    check("the hand-built To-order table engine is gone",
          "NE_COLS" not in idx and "NE_COLW" not in idx and "ne-tbl" not in idx.split("</style>")[1])
    flat = idx.replace(" ", "")
    check('LXT table "ic" is gone', '"ic":{key:"' not in flat and '"ic":[' not in flat)
    check("nothing still asks the LXT engine for it", not re.search(r'lxt\w+\("ic"', idx))

    # ---- 5. the capabilities that had to survive ---------------------------
    check("the queue still selects rows", 'selectable: kind === "pending"' in ful)
    check("and still has exactly one bulk action",
          ful.count("bulk: kind ===") == 1 and 'label: "Move to cart"' in ful)
    check("moving to the cart still posts the same ids",
          '"/api/incart/add",{ids}' in idx and "function neMoveSelected" in idx or "async function neMoveSelected" in idx)
    check("the cart cost is still typed, and still saved",
          'id: "cartCostIn"' in ful and '"/api/incart/cost"' in idx)
    check("the profit still answers live", "ds-fl-profit-out" in ful and "ds-fl-profit-out" in idx)
    for act in ["neQuotePrice", "neQuoteLink", "openOrderEdit", "neRequestId", "neGetPricesAll",
                "neImgsAll", "neDelete", "neUncart", "neRestore", "nePurge",
                "cartSetStatus", "cartRemove", "ppReviewDone", "ppCopyReview", "ppReviewCopy"]:
        check(f"{act} still reachable from a row", act in ful)
    check("both WhatsApp country codes survive", '"+972"' in ful and '"+970"' in ful)
    check("a package's own body is still what a prep row opens", "W.ppBody(c)" in ful)
    check("and its status is still editable there", "function ppStatusPicker(" in idx and "function ppPkgRow(" in idx)

    # ---- 6. one attention vocabulary ---------------------------------------
    reg = (HERE / "static" / "ds" / "status.js").read_text(encoding="utf-8")
    vocab = set(re.findall(r"(\w+): T\(", re.search(r"const attention = \{(.*?)\};", reg, re.S).group(1)))
    kinds = set(re.findall(r'kind: "(\w+)"', ful))
    check(f"every attention kind is in the registry ({sorted(kinds)})", kinds <= vocab)
    check("the Arabic section headings became English labels",
          'label: "Ready to pack"' in ful and 'label: "Waiting for pieces"' in ful and "ناقصهم" not in ful)

    # ---- 7. the bridge -----------------------------------------------------
    deps = sorted(set(re.findall(r"\bW\.([A-Za-z_]\w*)", ful)))
    missing = [d for d in deps if not re.search(r"^(async function|function|const|let|var)\s+%s\b" % d, idx, re.M)]
    check(f"all {len(deps)} app functions the pages call exist (missing: {missing})", not missing)
    calls = sorted(set(re.findall(r"onclick: `?([a-zA-Z_]\w*)\(", ful)))
    gone = [c for c in calls if not re.search(r"^(async function|function)\s+%s\b" % c, idx, re.M) and c not in ("event", "window", "setView")]
    check(f"every onclick target exists (missing: {gone})", not gone)

    # ---- 8. it runs --------------------------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed check")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}}, querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        for (const f of ["status.js","format.js","ds.js","table.js","fulfillment.js"]) require({str(HERE / "static" / "ds")!r} + "/" + f);
        const D = window.DS;
        console.log(JSON.stringify({{
          to: (D.toOrder.VIEWS || []).map(v => v.key),
          pp: (D.pkgPrep.VIEWS || []).map(v => v.key),
          hasBoards: ["toOrder","inCart","pkgPrep"].every(k => typeof D[k].board === "function" && typeof D[k].chrome === "function"),
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("fulfillment.js runs under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:3])
        else:
            import json as _json
            o = _json.loads(r.stdout.strip().splitlines()[-1])
            check("the To-order buckets are registered in pipeline order",
                  o["to"] == ["pending", "incart", "ordered", "deleted"])
            check("the Package-prep buckets are registered in work order",
                  o["pp"] == ["ready", "waiting", "reviews"])
            check("every page exports a chrome and a board", o["hasBoards"])

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
