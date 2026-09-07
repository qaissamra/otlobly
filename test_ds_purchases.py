#!/usr/bin/env python3
"""
Phase 3 — the Purchase orders page, rebuilt on the design system.

The brief's section 14 is a list of fifteen things wrong with this page. This file
pins each one that can be checked without a browser, so a later phase cannot
quietly undo them, plus the contract between the page module and the app:

  * the page is called "Purchase orders" and counts purchase orders (D7);
  * it renders one header with one primary action, and the four boards are saved
    views on a filter bar - not four unlabelled segmented buttons;
  * one fact per column: no cell concatenates a name, a count and two amounts;
  * Paid and Est. cost are SEPARATE columns, so two dollar signs cannot mean two
    different things in the same cell;
  * Status and the row actions are pinned to the end of the flat boards;
  * a problem is stated once, in the attention vocabulary, never as a fourth colour;
  * every board's text cells are dir="auto" with the full value in the tooltip;
  * the old LXT tables for this page are GONE, not left running beside the new ones;
  * every app function the module calls still exists (it is a separate file, so a
    rename in index.html would otherwise break the page silently at runtime).

    ./.venv/bin/python test_ds_purchases.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUR = HERE / "static" / "ds" / "purchases.js"
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿]")


def main():
    pur = PUR.read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    css = (HERE / "static" / "ds" / "ds.css").read_text(encoding="utf-8")
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")

    # ---- 1. shipped and wired -----------------------------------------------
    check("static/ds/purchases.js exists", PUR.exists())
    check("index.html loads it", '<script src="/static/ds/purchases.js"></script>' in idx)
    check("it loads after ds.js and table.js",
          idx.index("/static/ds/purchases.js") > idx.index("/static/ds/table.js"))
    check("the service worker precaches it", "/static/ds/purchases.js" in sw)
    check("no emoji in the page module", not EMOJI.findall(pur))

    # ---- 2. item 3 — the page is Purchase orders ----------------------------
    check("the page header says Purchase orders", 'title: "Purchase orders"' in pur)
    check("the count says purchase orders, not orders", "purchase orders</b>" in pur)
    check("the nav title agrees", 'purchases:"Purchase orders"' in idx)
    check("the primary action is New purchase order", 'label: "New purchase order"' in pur)

    # ---- 3. item 2 — one header, one primary, the rest in overflow ----------
    check("the header carries a breadcrumb", 'crumbs: [{ label: "Fulfillment" }' in pur)
    check("it carries the numbers that matter", "stats.push({ label: \"Late\"" in pur)
    prim = pur.count("primary: {")
    check(f"exactly one primary action on the page ({prim})", prim == 1)
    check("at most two visible secondaries", pur.count("secondary: [") == 1 and pur.count("{ label: \"Register at Gerizim\"") == 1)
    check("sync and maintenance moved into the overflow menu",
          'head: "Sync and maintenance"' in pur and "poCheckAllShipping" in pur)
    check("the old toolbar is gone from the markup",
          "Amazon purchase orders</h2>" not in idx and 'id="poSegOrders"' not in idx)

    # ---- 4. items 5 + 6 — money has its own columns -------------------------
    check("Paid is its own column", 'key: "paid", label: "Paid"' in pur)
    check("Est. cost is a SEPARATE column", 'label: "Est. cost"' in pur)
    check("neither is concatenated into another cell",
          "· ≈" not in pur and "est`" not in pur)
    check("both are right-aligned, tabular", pur.count('align: "end"') >= 6)
    check("money is formatted once, through the design system",
          'const money = (v) => D.fmt.money(v, "USD");' in pur)

    # ---- 5. item 8 — status and actions can never scroll away ---------------
    check("Status is pinned on the packages board",
          'key: "status", label: "Status", w: 164, pin: "end"' in pur)
    check("Exception is pinned on the products board",
          'key: "exception", label: "Exception", w: 136, pin: "end"' in pur)
    check("the actions column is pinned and locked",
          'type: "actions", pin: "end", locked: true' in pur)
    check("every board pins its identity column to the start", pur.count('pin: "start"') == 4)

    # ---- 6. item 10 — one attention vocabulary ------------------------------
    kinds = set(re.findall(r'kind: "(\w+)"', pur))
    check(f"attention kinds come from the registry ({sorted(kinds)})",
          kinds <= {"late", "no_tracking", "missing_name"})
    reg = (HERE / "static" / "ds" / "status.js").read_text(encoding="utf-8")
    vocab = set(re.findall(r"(\w+): T\(", re.search(r"const attention = \{(.*?)\};", reg, re.S).group(1)))
    check("and every one of them exists there", kinds <= vocab)
    code = re.sub(r"/\*.*?\*/", "", pur, flags=re.S)   # the header comment quotes the old label
    check("lateness is stated in days, not shouted",
          'detail: `${worst} d`' in code and "DAYS LATE" not in code)
    check("and a legacy pill stops shouting inside a design-system table",
          ".ds-table .pill, .ds-pu-sub .pill { text-transform: none;" in css)
    check("a missing order name is an attention badge, not a red dash",
          'kind: "missing_name"' in pur)

    # ---- 7. item 7 — truncation with the whole value in the tooltip ---------
    check("text cells isolate direction and keep the full value",
          'dir="auto" title="${esc(v)}"' in pur)
    check("they truncate with CSS, at the text's own end", 'class="ds-truncate"' in pur)

    # ---- 8. items 11 + 12 — flat rows, nested content with headers ----------
    check("nested content is an aligned grid", ".ds-pu-sub" in css and 'class="ds-pu-sub"' in pur)
    check("and it has a header row", 'class="ds-pu-sub-head"' in pur and ".ds-pu-th" in css)
    check("packages and products each get one",
          "function packageGrid(" in pur and "function productGrid(" in pur)
    check("no card-per-row shadow on this page", "po-card" not in pur)

    # ---- 9. item 14 — the open row does not wear the nav's fill -------------
    m = re.search(r"\.ds-tr\.is-open \{([^}]*)\}", css)
    check("the open row has its own treatment", m and "--ds-accent-tint" not in m.group(1))
    m2 = re.search(r'\.ds-nav-item\[aria-current="page"\] \{([^}]*)\}', css)
    check("the nav keeps the accent fill to itself", m2 and "--ds-accent-tint" in m2.group(1))

    # ---- 10. item 13 — the profile code is explained ------------------------
    check("the buying-account column explains itself",
          "PROFILE_HELP" in pur and "Multilogin browser profile" in pur)
    check("and the explanation is on the column header", "title: PROFILE_HELP" in pur)

    # ---- 11. item 15 — a labelled column control ----------------------------
    tbl = (HERE / "static" / "ds" / "table.js").read_text(encoding="utf-8")
    check("the column control is a labelled button", 'label: hiddenN ? `Columns' in tbl)
    check("a column can start hidden and be brought back", "defaultHidden" in tbl and "defaultHidden: true" in pur)

    # ---- 12. no parallel system: the old tables are gone --------------------
    for k in ["po", "pok", "pop"]:
        check(f'LXT table "{k}" is gone', f'"{k}":{{key:"' not in idx.replace(" ", ""))
        check(f'LXT columns "{k}" are gone', f'"{k}":[' not in idx.replace(" ", ""))
    for fn in ["poCardHtml", "poPkgHtml", "poPkFlatRow", "poItFlatRow",
               "poRenderPkgsFlat", "poRenderProdsFlat", "poRenderCustomers", "poSortVal", "poCfCells"]:
        check(f"{fn}() deleted", f"function {fn}(" not in idx)
    check("nothing still asks the LXT engine for these tables",
          not re.search(r'lxt\w+\("(po|pok|pop)"', idx))

    # ---- 13. the bridge: every app function the module calls still exists ---
    deps = sorted(set(re.findall(r"\bW\.([A-Za-z_]\w*)", pur)))
    missing = [d for d in deps if not re.search(r"^(async function|function|const|let|var)\s+%s\b" % d, idx, re.M)
               and not re.search(r"[,\s]%s\s*=" % d, idx)]
    check(f"all {len(deps)} app functions the page calls exist (missing: {missing})", not missing)
    calls = sorted(set(re.findall(r"onclick: `?([a-zA-Z_]\w*)\(", pur)))
    gone = [c for c in calls if not re.search(r"^(async function|function)\s+%s\b" % c, idx, re.M)
            and c not in ("event", "location")]
    check(f"every onclick target exists (missing: {gone})", not gone)
    ctx_keys = set(re.findall(r"\bctx\.(\w+)", pur))
    provided = set(re.findall(r"^\s{4}(\w+):", re.search(r"function poCtx\(inAddCard\)\{(.*?)\n\}", idx, re.S).group(1), re.M))
    provided |= set(re.findall(r"(\w+):", re.search(r"function poChromeCtx\(\)\{(.*?)\n\}", idx, re.S).group(1)))
    check(f"the ctx bridge provides everything the page reads (missing: {sorted(ctx_keys - provided)})",
          ctx_keys <= provided)

    # ---- 14. it runs -------------------------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed check")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}}, querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        require({str(HERE / "static" / "ds" / "status.js")!r});
        require({str(HERE / "static" / "ds" / "format.js")!r});
        require({str(HERE / "static" / "ds" / "ds.js")!r});
        require({str(HERE / "static" / "ds" / "table.js")!r});
        require({str(PUR)!r});
        console.log(JSON.stringify({{
          loaded: typeof window.DS.purchases === "object",
          boards: (window.DS.purchases.BOARDS || []).map(b => b.key),
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("purchases.js runs under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:3])
        else:
            import json as _json
            o = _json.loads(r.stdout.strip().splitlines()[-1])
            check("DS.purchases is exported", o["loaded"])
            check("the four boards are registered, in pipeline order",
                  o["boards"] == ["orders", "packages", "products", "customers"])

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
