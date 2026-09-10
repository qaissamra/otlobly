#!/usr/bin/env python3
"""Batch D — Tracking (the Bulk search page) on the design system (docs/ux-restructure).

The page draws through static/ds/tracking.js; web/index.html keeps the data, the
finders and the bridges. These checks pin what a later edit could quietly lose:
the module's surface, column parity with the LXT `bs` table it replaced, the
sort rule (misses last in BOTH directions), the bridge wiring, the two deep links
into the page, and the legacy pieces that are supposed to be gone. Run it on the
pre-Batch-D tree and most of it fails — that is the point.
"""
import re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent
FAILS = []
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿]")


def check(name, ok):
    print(("  OK  " if ok else "  XX  ") + name)
    if not ok:
        FAILS.append(name)


def code_only(src):
    """Strip /* */ and // comments so a comment naming a thing does not count as the thing."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def main():
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    trp = HERE / "static" / "ds" / "tracking.js"
    tr = trp.read_text(encoding="utf-8") if trp.exists() else ""     # pre-Batch-D tree: fail, don't crash
    gm = (HERE / "static" / "ds" / "gaash.js").read_text(encoding="utf-8")
    css = (HERE / "static" / "ds" / "ds.css").read_text(encoding="utf-8")
    shell = (HERE / "static" / "ds" / "shell.js").read_text(encoding="utf-8")
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")

    def between(src, a, b):
        """The slice between two markers, or "" when either is missing - a check on a
        missing region must fail, never raise and silence everything after it."""
        if a not in src: return ""
        rest = src.split(a, 1)[1]
        return rest.split(b, 1)[0] if b in rest else rest

    def fn_body(src, name):
        return between(src, f"function {name}(", "\n}")

    print("— the module and how it is loaded —")
    check("static/ds/tracking.js is loaded by the app", '<script src="/static/ds/tracking.js"></script>' in idx)
    check("…before gaash.js, which draws its parcel cells through it",
          "/static/ds/tracking.js" in idx and "/static/ds/gaash.js" in idx and idx.index("/static/ds/tracking.js") < idx.index("/static/ds/gaash.js"))
    check("the service worker precaches it", '"/static/ds/tracking.js"' in sw)
    check("the offline cache was bumped for it", 'const CACHE = "otl-off-v2' in sw and 'otl-off-v21"' not in sw)
    check("it exports DS.tracking", "D.tracking = {}" in tr)
    for fn in ["modelCells", "PARCEL_COLS", "sortVal", "sortRows", "VIEWS", "inView", "rowKey", "sums", "moneyText", "chrome", "results"]:
        check(f"DS.tracking.{fn} exists", re.search(rf"\bG\.{fn} = ", tr) is not None)
    check("the module carries no emoji (English labels, Heroicons)", bool(tr) and not EMOJI.search(code_only(tr)))
    check("the parcel cells live in ONE place: gaash.js draws them through DS.tracking",
          "function modelCells" not in gm and "D.tracking.modelCells" in gm and "D.tracking.PARCEL_COLS" in gm)

    print("— the page chrome —")
    check("the page draws its own header (the shell only routes to it)", '"bulksearch"' in between(shell, "const OWN_HEADER", "\n"))
    chrome = between(tr, "G.chrome = ", "G.results = ")
    check("crumbs Shipping › Tracking", 'label: "Shipping"' in chrome and 'label: "Tracking"' in chrome and 'title: "Tracking"' in chrome)
    check("one primary (Search) and two secondaries (Clear, Reload data)",
          'primary: { label: "Search"' in chrome and 'label: "Clear"' in chrome and 'label: "Reload data"' in chrome)
    check("the overflow carries Enroll all found + Copy missing numbers", "bsEnrollFound()" in chrome and "bsCopyMissing()" in chrome)
    check("stats: the two boards, then numbers / found / missing / value after a search",
          all(s in chrome for s in ['label: "Purchase orders"', 'label: "Leluxe orders"', "not loaded for your role", 'label: "Numbers"', 'label: "Found"', 'label: "Missing"', 'label: "Value"']))
    check("the header repaints without stealing the caret", "D.paintHost(el, D.pageHeader(" in chrome)

    print("— the paste box —")
    ta = between(idx, '<textarea id="bsInput"', "</textarea>")
    check("the paste box is static markup with the legacy ids kept",
          'id="trkChrome"' in idx and 'class="ds-textarea ds-trk-input"' in ta and 'id="bsResults"' in idx)
    check("Cmd/Ctrl+Enter searches", "event.key==='Enter'" in ta and "bsRun()" in ta)
    check("the legacy panel is gone (raw buttons, bilingual labels, the info line)",
          'id="bsGo"' not in idx and 'data-en="Bulk package search"' not in idx and 'id="bsInfo"' not in idx)
    check("the legacy nav button keeps its id (its visibility is the shell's gate)", 'id="bulkSearchBtn"' in idx)
    check("the classic top bar calls the page what the header calls it", 'bulksearch:"Tracking",' in idx)

    print("— the results board —")
    cols = re.findall(r'key: "(\w+)"', between(tr, "G.PARCEL_COLS = [", "];"))
    check("column parity with the LXT bs table (nine columns, same order)",
          cols == ["found", "oname", "profile", "imgs", "cust", "status", "gash", "gashdate", "value"])
    res = between(tr, "G.results = ", "})();")
    check("the tracking number is pinned, mono, with the copy button", 'key: "tn"' in res and 'pin: "start"' in res and "lxCopyRawBtn" in res)
    check("an actions menu column pinned at the end", 'type: "actions"' in res and 'pin: "end"' in res and "ctx.menu(m)" in res)
    check("the page sorts (misses always last) and tells the table so", "onSort:" in res and "G.sortRows(" in res)
    check("selection + bulk Enroll (only when GAASH mail is available)", "selectable: !!ctx.canEnroll" in res and 'onclick: "bsEnrollSel(keys,rows)"' in res)
    check("the footer carries the Σ line and the money", "footer: { tn:" in res and "missing" in res and "G.moneyText(" in res)
    check("saved views All · Found · Missing · Purchases · Leluxe", all(f'key: "{k}"' in between(tr, "G.VIEWS = [", "];") for k in ["all", "found", "missing", "po", "lx"]) and 'onView: "bsView(KEY)"' in res)
    check("the value column is dropped for a role that can see neither money nor Leluxe", 'c.key !== "value" || ctx.money || ctx.lx' in res)
    check("a miss row is tinted and badged, never a bare string", 'kind: "no_tracking"' in tr and "ds-trk-miss" in res and ".ds-tr.ds-trk-miss" in css)
    check("loading and failure states are real", "D.skeleton(" in res and "D.errorState(" in res)
    check("the table id is trk with a seed version", 'id: "trk"' in res and "seedVersion: 1" in res)

    print("— the bridges in index.html —")
    for fn in ["bsEnsureData", "bsTokens", "bsFindPo", "bsLxRows", "bsFindLx", "bsModels", "bsOpenPo", "gmAvail", "bsCtx", "bsRenderChrome",
               "bsInit", "bsRun", "bsRender", "bsView", "bsClear", "bsReload", "bsLookup", "bsRowMenu", "bsEnrollSel", "bsEnrollFound", "bsCopyMissing"]:
        check(f"function {fn} is declared", re.search(rf"^(?:async )?function {fn}\(", idx, re.M) is not None)
    for fn in ["bsModelRow", "bsSortVal", "bsEnrollBtn"]:
        check(f"{fn} is gone", f"function {fn}(" not in idx)
    check("bsRun searches through the shared model builder", "bsModels(toks)" in fn_body(idx, "bsRun"))
    check("GAASH mail's workflow expansion uses the same builder", "bsModels(" in fn_body(idx, "gmWfExpRender") and "const models=gwds=>" not in idx)
    mv = fn_body(idx, "gmMatchesView")
    check("the ⚡ match chip opens Tracking through bsLookup, no timer", "bsLookup(gwds)" in mv and "setTimeout" not in mv)
    lk = fn_body(idx, "bsLookup")
    check("bsLookup sets the view, pastes, and lets bsRun await the data", 'setView("bulksearch")' in lk and "return bsRun()" in lk and "setTimeout" not in lk)
    check("the global search prefills Tracking instead of landing on an empty page",
          "after: () => { if (window.bsLookup)" in shell and "if (r.after) r.after();" in between(shell, "S.pick = (i) =>", "};"))
    check("Clear empties the search, the view and the stats", "BS_LAST=null" in fn_body(idx, "bsClear") and 'BS_VIEW="all"' in fn_body(idx, "bsClear"))
    check("Reload drops both caches and re-runs the last search", "POS=null" in fn_body(idx, "bsReload") and "LX=null" in fn_body(idx, "bsReload") and "bsRun()" in fn_body(idx, "bsReload"))
    check("a new search starts with a clean selection", "t.selected.clear()" in fn_body(idx, "bsRun"))
    menu = fn_body(idx, "bsRowMenu")
    check("row menu: copy, open purchase order, open in Leluxe, enroll",
          all(s in menu for s in ['"Copy number"', '"Open purchase order"', '"Open in Leluxe"', '"Enroll in GAASH mail"', "lxJumpOrder(", "gmEnrollFrom("]))
    fns = set(re.findall(r"^(?:async )?function\s+(\w+)", idx, re.M))
    emitted = set(re.findall(r'onclick: "(bs\w+)\(', tr)) | set(re.findall(r"W\.(bs\w+)", tr)) | set(re.findall(r"bsOpenPo", tr))
    check("every bs* handler the module emits resolves to a declaration", bool(emitted) and all(f in fns for f in emitted))

    print("— the LXT registries —")
    check("bs is out of LX_TABLES", '"bs": {key:"bs_colw"' not in idx)
    check("…out of LXT_COLS", '"bs":[' not in idx)
    check("…out of LXT_CLS", '"bs":["bs-colhead"' not in idx)
    check("…out of LXT_PINCLS", '"bs":""' not in idx)
    check("…out of the localStorage seed array", '"pop","bs","en"' not in idx)
    check("…out of lxtRender's dispatch", "bs:bsRender" not in idx)
    check("the .bs-cols grid rule and every .bs- fragment are gone", ".bs-cols" not in idx and ".bs-colhead" not in idx and "--bsgrid" not in idx)
    check("the LXT engine stays for en, dp, ct, tr, fh",
          all(f'"{k}": {{key:"{k}_colw"' in idx for k in ["en", "dp", "ct", "tr", "fh"]) and "function lxtHead(" in idx and "function lxtCells(" in idx)

    print("— design-system rules —")
    marker = "Tracking (docs/ux-restructure Batch D)"
    trk_css = css[css.index(marker):] if marker in css else ""
    sels = [s.strip() for s in re.findall(r"^([^{\n]+)\{", trk_css, re.M)]
    check("every Tracking selector is scoped under .ds-", bool(sels) and all(".ds-" in s for s in sels))
    check("no raw hex, no emoji in the Tracking CSS", bool(trk_css) and not re.search(r"#[0-9a-fA-F]{3,8}\b", trk_css) and not EMOJI.search(trk_css))
    check("logical properties only", bool(trk_css) and not re.search(r"\b(margin|padding|border)-(left|right|top|bottom)\s*:", trk_css) and not re.search(r"(?<![-a-z])(min-|max-)?(width|height)\s*:", trk_css))
    check("no var() with a hex fallback", not re.search(r"var\(--ds-[\w-]+,\s*#", trk_css))

    print("— the module runs under node —")
    stub = r'''
      global.window = { DS: { esc: (s) => String(s == null ? "" : s), fmt: { number: (v) => String(v), money: (v) => "$" + v }, dash: () => "-" } };
      window.pkgEstTotal = (pk) => ({ sum: pk.sum || 0, priced: pk.priced || 0 });
      window.lxF = () => null;
      require("./static/ds/tracking.js");
      const G = window.DS.tracking;
      const po = (tn, id, sum, priced, pi) => ({ kind: "po", tn, p: { po_id: id, ship_to: id }, pk: { sum, priced, items: [] }, pi: pi || 0 });
      const models = [po("GWD1", "PO-1", 10, 1), po("GWD1", "PO-1", 0, 0, 1), { kind: "lx", tn: "GWD2", lx: { tot: 5, items: [], sts: [] } }, { kind: "miss", tn: "GWD3" }];
      const asc = G.sortRows(models, { key: "value", dir: "asc" }).map(G.rowKey).join();
      const desc = G.sortRows(models, { key: "value", dir: "desc" }).map(G.rowKey).join();
      if (asc !== "lx:GWD2,po:GWD1:PO-1:0,po:GWD1:PO-1:1,miss:GWD3") throw new Error("asc " + asc);
      if (desc !== "po:GWD1:PO-1:0,lx:GWD2,po:GWD1:PO-1:1,miss:GWD3") throw new Error("desc " + desc);
      const none = G.sortRows(models, null).map(G.rowKey).join();
      if (none !== "po:GWD1:PO-1:0,po:GWD1:PO-1:1,lx:GWD2,miss:GWD3") throw new Error("unsorted " + none);
      if (new Set(models.map(G.rowKey)).size !== 4) throw new Error("rowKey not unique");
      const s = G.sums(models, { money: true }); if (s.usd !== 10 || s.ils !== 5) throw new Error("sums " + JSON.stringify(s));
      if (G.sums(models, { money: false }).usd !== 0) throw new Error("money gate");
      const counts = {}; G.VIEWS.forEach((v) => (counts[v.key] = models.filter((m) => G.inView(m, v.key)).length));
      if (JSON.stringify(counts) !== JSON.stringify({ all: 4, found: 3, missing: 1, po: 2, lx: 1 })) throw new Error("views " + JSON.stringify(counts));
      console.log("node ok");
    '''
    try:
        out = subprocess.run(["node", "-e", stub], cwd=HERE, capture_output=True, text=True, timeout=30)
        check("tracking.js loads; misses and blanks sort last both ways; sums, views and keys agree", out.returncode == 0 and "node ok" in out.stdout)
        if out.returncode != 0:
            print("      " + (out.stderr or out.stdout).strip().splitlines()[-1][:200])
    except FileNotFoundError:
        print("  --  node not installed; skipped the runtime check")

    print("――――――――――――――――――――――")
    if FAILS:
        print(f"FAILED: {len(FAILS)} — {FAILS}")
        sys.exit(1)
    print("All Tracking design-system checks passed ✓")


if __name__ == "__main__":
    main()
