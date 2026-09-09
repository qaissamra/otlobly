#!/usr/bin/env python3
"""Batch C — GAASH mail on the design system (docs/ux-restructure).

The page's eight tabs draw through static/ds/gaash.js; web/index.html keeps the
fetches, the thread body and the bridges. These checks pin what a later edit
could quietly lose: the module's surface, the columns the old LXT table had, the
tab strip contract the shell relies on, the bridge wiring, and the pieces of the
legacy page that are supposed to be gone. Run the suite on the pre-Batch-C tree
and most of it fails — that is the point.
"""
import re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent
FAILS = []


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
    # the pre-Batch-C tree has no module: read "" so the checks FAIL instead of crashing
    gmp = HERE / "static" / "ds" / "gaash.js"
    gm = gmp.read_text(encoding="utf-8") if gmp.exists() else ""
    ds = (HERE / "static" / "ds" / "ds.js").read_text(encoding="utf-8")
    css = (HERE / "static" / "ds" / "ds.css").read_text(encoding="utf-8")
    shell = (HERE / "static" / "ds" / "shell.js").read_text(encoding="utf-8")
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    cat = (HERE / "web" / "design-system.html").read_text(encoding="utf-8")

    def between(src, a, b):
        """The slice between two markers, or "" when either is missing - a check on a
        missing region must fail, never raise and silence everything after it."""
        if a not in src: return ""
        rest = src.split(a, 1)[1]
        return rest.split(b, 1)[0] if b in rest else rest

    print("— the module and how it is loaded —")
    check("static/ds/gaash.js is loaded by the app", '<script src="/static/ds/gaash.js"></script>' in idx)
    check("…after leluxe.js (the page modules load in nav order)",
          '/static/ds/gaash.js' in idx and idx.index('/static/ds/leluxe.js') < idx.index('/static/ds/gaash.js'))
    check("the service worker precaches it", '"/static/ds/gaash.js"' in sw)
    check("the offline cache was bumped for it", 'const CACHE = "otl-off-v2' in sw and 'otl-off-v20"' not in sw)
    check("it exports DS.gaash", "D.gaash = {}" in gm)
    for fn in ["tabs", "chrome", "conversations", "overview", "wfExpansion", "workflows", "readiness",
               "docs", "docsBadge", "docsFilter", "readyFilter", "fcFilter", "forecast", "cases", "templates", "analyze"]:
        check(f"DS.gaash.{fn} exists", re.search(rf"\bG\.{fn} = ", gm) is not None)
    check("every board goes through the shared thumbnail builder, never its own markup",
          "ds-pu-thumb" not in code_only(gm) and "ds-fl-thumbs" not in code_only(gm) and "ds-pu-sub-row" not in code_only(gm))
    check("the module carries no emoji (English labels, Heroicons)",
          not re.search("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿]", gm))
    check("thread and documents states come from the status registry",
          'D.status.badge("gmThread"' in gm and 'D.status.badge("docs"' in gm)
    check("the module paints without stealing the caret (search box, reply box)", "D.paintHost(" in gm)

    print("— the eight tabs —")
    keys = re.findall(r'\{ key: "(\w+)", label: "[^"]+" \}', between(gm, "G.TABS = [", "];"))
    check("the page's own strip lists the eight tabs in the shell's order",
          keys == ["conv", "ov", "seq", "ready", "docs", "fcast", "tpl", "dash"])
    shell_keys = (re.search(r'gaashmail: \{ keys: \[([^\]]+)\]', shell) or re.search(r"()", "")).group(1)
    check("…the same eight the shell routes", sorted(re.findall(r'"(\w+)"', shell_keys)) == sorted(keys))
    check("the strip is DS.tabs with the legacy button ids (the tests and gmTab still name them)",
          'id: "gmTabStrip"' in gm and 'fcast: "gmTabFcast"' in gm and "TAB_ID[t.key]" in gm)
    check("DS.tabs accepts a per-item id", "id: t.id || `${id}-${t.key}`" in ds)
    check("gmTab still switches panes by the [key, button, pane] triple",
          '["fcast","gmTabFcast","gmFcastPane"]' in idx.replace(" ", "") and "function gmTab(t){" in idx)
    check("gmTab keeps the address honest", "DS.shell2.syncTab" in between(idx, "function gmTab(t){", "\n}"))
    check("the page draws its own header, so the shell contributes only the tabs",
          '"gaashmail"' in between(shell, "const OWN_HEADER", "\n"))
    check("the shell still hides the page's own strip under the new layout", "body.ds-shell-on #gmTabs" in css)
    check("the legacy hand-styled tab buttons are gone",
          'id="gmTabConv"' not in idx and 'onclick="gmTab(\'conv\')"' not in idx)

    print("— the chrome —")
    check("the header is DS.pageHeader with Shipping › GAASH mail", '{ label: "Shipping" }, { label: "GAASH mail" }' in gm)
    check("one primary: Enroll packages", 'primary: { label: "Enroll packages"' in gm)
    check("two visible secondaries, the rest in the overflow",
          'label: "Check replies"' in gm and 'label: "Check tracking"' in gm and 'label: "Accounts & templates"' in gm)
    check("the safe-mode strip is a DS.callout that carries Freeze all / Resume all",
          "D.callout(" in gm and '"Freeze all"' in gm and '"Resume all"' in gm and 'onclick: "gmFreeze(this)"' in gm)
    check("an auth error offers Update password in place", '"Update password"' in gm and "gmAcctFix(" in gm)
    check("DS.callout exists and is on the catalogue", "DS.callout = " in ds and "DS.callout(" in cat)
    check("the callout family is styled from tokens", ".ds-callout-warning" in css and ".ds-callout-success" in css)
    check("the bridge hands the chrome its state", "function gmChromeCtx(" in idx and 'DS.gaash.chrome("gmChrome"' in idx)
    check("the legacy toolbar, banner and subtitle are gone",
          'id="gmSub"' not in idx and 'id="gmBanner"' not in idx and 'id="gmFreezeBtn"' not in idx and 'id="gmCheckBtn"' not in idx)
    check("Check replies restores the button it disabled (a DS button is icon + label)",
          "btn.innerHTML=was" in between(idx, "async function gmCheck(btn){", "\n}"))

    print("— conversations: a board whose row opens into the thread —")
    check("the list is a DataTable", 'id: "gm_conv"' in gm)
    check("the thread opens under its row, one at a time",
          "expandable: { render: (t) => (ctx.thread ? ctx.thread(t) : \"\"), open: (t) => t.gwd === ctx.cur }" in gm
          and "function gmSetCur(gwd){" in idx and 'DS.tableResetOpen("gm_conv")' in idx)
    check("the thread body is built by index.html and painted by id", "function gmChatHtml(){" in idx and 'id="gmChat"' in idx)
    check("the reply box survives the 60s poll", "DS.paintHost(host, html)" in idx and "function gmChatAfterPaint(){" in idx)
    check("views: Open · Cleared · All, remembered", 'key: "open", label: "Open"' in gm and 'localStorage.setItem("gm_conv_view"' in idx)
    check("search over parcel, name, subject and last message", 'id: "gmConvSearch"' in gm and "function gmConvText(t){" in idx)
    for col in ["gwd", "products", "state", "step", "name", "gash", "last", "activity", "next", "sender", "reads", "seq", "attention"]:
        check(f"conversations column {col}", f'key: "{col}"' in between(gm, "G.conversations = ", "G.overview = "))
    check("the row menu carries the thread actions", "function gmConvMenu(t){" in idx and "'send_next_now'" in idx and "'task_done'" in idx)
    check("the old card list and its cleared fold are gone",
          "function gmClearedOpen(" not in idx and "class=\"gm-row" not in idx and ".gm-fold{" not in idx)

    print("— workflows: the LXT table is a DataTable —")
    for key in ["onoff", "trigger", "steps", "days", "goal", "active", "enrolled", "enr7d", "goalmet", "sent", "open", "reply", "desc"]:
        check(f"workflows column {key} survived the move", f'key: "{key}"' in between(gm, "G.workflows = ", "G.readiness = "))
    check("the LXT wf table is out of every registry",
          '"wf": {key:"wf_colw"' not in idx and '"wf":[' not in idx and '"wf":["wf-colhead"' not in idx and '"wf":" pin-md"' not in idx
          and '"bs","wf","en"' not in idx)
    check("…and its grid rule", ".wf-cols{display:grid" not in idx and ".wf-exp{" not in idx)
    check("the old row builder and expansion toggle are gone", "function gmWfRow(" not in idx and "function gmWfExpToggle(" not in idx)
    check("the enrolment expansion is two sub-grids: enrolled and suggested",
          "G.wfExpansion = " in gm and '"Approve all"' in gm and '"Dismiss all"' in gm and "D.subTable(" in gm)
    check("…fed by the bridge with the boards' models", "DS.gaash.wfExpansion({" in idx and "function gmWfSetOpen(" in idx)
    check("the triggers are a table too", 'id: "gm_rules"' in gm and '"New trigger"' in gm)
    check("On/Off is a real switch", "D.switch({ checked: !r.s.paused" in gm and "gmWfToggle(" in gm)

    print("— readiness · docs · forecast · templates · analyze —")
    check("readiness views carry counts", 'key: "blocked", label: "No ID"' in gm and 'id: "gm_ready"' in gm)
    check("an empty No-ID view falls back to All", 'GM_READY.mode="all"' in idx)
    check("the docs by-order / flat toggle is kept and remembered",
          'onclick: "gmDocsView(\'order\')"' in gm and 'onclick: "gmDocsView(\'flat\')"' in gm and 'localStorage.setItem("otl_gmdocs_view"' in idx)
    check("by order: parcels of one order sit together", "ds-gm-tie-first" in gm and "ds-gm-tied" in gm and "orderKey" in gm)
    check("Check all keeps its Stop", '"gmDocsCheckAll()"' in gm and "gmDocsStop()" in gm and "function gmDocsStop(){" in idx)
    check("Check all walks the rows on screen through the module's filter", "DS.gaash.docsFilter(GM.docs" in idx)
    check("the docs state pill is the registry's, and the old hand-rolled one is gone",
          "function gmDocsStatePill(" not in idx and "G.docsBadge = " in gm)
    check("forecast keeps queue + cases as one pane, two views",
          'onclick: "gmFcView(\'queue\')"' in gm and 'onclick: "gmFcView(\'cases\')"' in gm and "G.cases = " in gm)
    check("the prediction pills stay index.html's (tonePill/hexPill, never gaashBucketPill)",
          "reasonPill:gmFcReasonPill, nextPill:gmFcNextPill" in idx)
    check("templates: a table plus the editor", 'id: "gm_tpl"' in gm and "ds-gm-editor" in gm and 'id: "gmTplBody"' in gm)
    check("analyze: KPIs plus a table, the raw <table> is gone", 'id: "gm_dash"' in gm and "ds-gm-fun" in gm
          and "<table>" not in between(idx, "async function gmDashLoad(){", "\n}"))
    check("every loader keeps its honest failure path", idx.count("apiFailReason()") >= 6 and "DS.errorState({text:\"Couldn't load\"+apiFailReason()" in idx)

    print("— what left index.html —")
    check("GM_STATE (a colour registry) is gone; gmChip delegates", "const GM_STATE=" not in idx and 'DS.status.badge("gmThread", state' in idx)
    check("the tile and funnel CSS moved with their surfaces", ".gm-tile{" not in idx and ".gm-fun{" not in idx and ".ds-gm-fun" in css)
    check("the two-pane chat layout is gone", ".gm-wrap{" not in idx and ".gm-list{" not in idx)
    check("the chat body CSS stays (the thread is still legacy markup, a Phase 5 detail)", ".gm-chat{" in idx and ".gm-msg{" in idx)

    print("— the module runs under node —")
    stub = """
      global.window = global; global.document = { getElementById: () => null, querySelector: () => null };
      const DS = global.DS = { esc: (s) => String(s), attrs: () => "", cls: (...a) => a.filter(Boolean).join(" "), dash: () => "-",
        fmt: { number: (v) => String(v), money: (v) => String(v), relative: (v) => v, title: (v) => v, datetime: (v) => v },
        status: { badge: (e, v) => v }, icon: () => "", button: () => "", badge: () => "", tag: () => "", attention: () => "",
        thumbs: () => "", subTable: () => "", tabs: () => "", pageHeader: () => "", filterBar: () => "", paintHost: () => null,
        table: () => "", tableMount: () => null, tableGet: () => null, tableRender: () => null, skeleton: () => "", empty: () => "",
        stat: () => "", kpis: () => "", switch: () => "", checkbox: () => "", input: () => "", textarea: () => "", field: () => "",
        formRow: () => "", callout: () => "" };
      require("./static/ds/gaash.js");
      const G = DS.gaash;
      const rows = [{state:"a",pname_id:"1"},{state:"",pname_id:""},{app_tag:1,state:""}];
      if (G.readyFilter(rows, "blocked").length !== 2) throw new Error("readyFilter blocked");
      if (G.readyFilter(rows, "tagged").length !== 1) throw new Error("readyFilter tagged");
      if (G.docsFilter([{state:"action"},{state:"stopped"},{state:"info"}], "action").length !== 2) throw new Error("docsFilter");
      if (G.fcFilter([{ok:true,overdue:true},{ok:false}], "unknown").length !== 1) throw new Error("fcFilter");
      if (G.TABS.length !== 8) throw new Error("TABS");
      console.log("node ok");
    """
    try:
        out = subprocess.run(["node", "-e", stub], cwd=HERE, capture_output=True, text=True, timeout=30)
        check("gaash.js loads and its pure filters agree with the page", out.returncode == 0 and "node ok" in out.stdout)
        if out.returncode:
            print(out.stderr[-600:])
    except FileNotFoundError:
        print("  --  node not installed; skipped the runtime check")

    print("――――――――――――――――――――――")
    if FAILS:
        print(f"FAILED: {len(FAILS)} — {FAILS}")
        sys.exit(1)
    print("All GAASH mail design-system checks passed ✓")


if __name__ == "__main__":
    main()
