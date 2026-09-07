#!/usr/bin/env python3
"""
Phase 2 shell — the contract between static/ds/shell.js and the app it navigates.

The shell is deliberately parasitic: it re-uses index.html's view containers, its
role gates and its tab functions rather than re-implementing any of them. That only
stays true if the names it borrows keep existing, which is what this file checks:

  * shell.js ships, is loaded by index.html and precached by the service worker;
  * the information architecture is the one the owner approved (AUDIT 5.8): five
    groups, at most twelve navigable items, count badges only on actionable queues;
  * every route points at a view id setView() actually toggles, and every view id
    the app has can be reached by a route (nothing loses its address);
  * every nav item names a real legacy nav button — that button's visibility IS the
    role/feature gate, so a typo would silently show a page to the wrong role;
  * the window.APP bridge exposes exactly what shell.js reads (index.html's
    top-level `let` bindings are not window properties);
  * every attention kind attention.py emits exists in the status registry;
  * the new shell is off unless the flag is set, and the classic shell is untouched.

    ./.venv/bin/python test_ds_shell.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHELL = HERE / "static" / "ds" / "shell.js"
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿]")


def main():
    src = SHELL.read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")

    # ---- 1. shipped and wired ----------------------------------------------
    check("static/ds/shell.js exists", SHELL.exists())
    check("index.html loads shell.js", '<script src="/static/ds/shell.js"></script>' in idx)
    check("shell.js loads after ds.js (it calls DS.*)",
          idx.index("/static/ds/shell.js") > idx.index("/static/ds/ds.js"))
    check("service worker precaches shell.js", "/static/ds/shell.js" in sw)
    ver = re.search(r'CACHE = "otl-off-v(\d+)"', sw)
    check("service worker cache name was bumped for the shell", ver and int(ver.group(1)) >= 3)
    check("shell.js carries no emoji", not EMOJI.findall(src))

    # ---- 2. the approved information architecture (AUDIT 5.8) --------------
    groups = re.findall(r'\{ key: "(\w+)", label: "([^"]+)", items: \[', src)
    check("five groups, in flow order: Sales, Fulfillment, Shipping, Finance, Insights",
          [g[1] for g in groups] == ["Sales", "Fulfillment", "Shipping", "Finance", "Insights"])
    nav_block = re.search(r"const NAV = \[(.*?)\n  \];", src, re.S).group(1)
    grouped = len(re.findall(r'\{ key: "\w+", label: "[^"]+", icon:', nav_block))
    # grouped + Overview + Needs attention must stay inside the brief's "at most 12"
    check(f"{grouped + 2} navigable items (brief: at most 12)", grouped + 2 <= 12)
    check("Overview and Needs attention sit outside the groups",
          '"/overview"' in src and '"/attention"' in src)
    badges = set(re.findall(r'badge: "(\w+)"', src))
    check("count badges only on actionable queues", badges <= {"toorder", "incart", "pkgprep", "attention", "fulfil"})
    stage_block = re.search(r"const STAGES = \[(.*?)\n  \];", src, re.S).group(1)
    check("the pipeline keeps its four stages, in order (D8: one page, four tabs)",
          re.findall(r'label: "([^"]+)"', stage_block) ==
          ["To order", "In cart", "Purchase orders", "Package prep"])
    check("Fulfillment is one nav item, not four", 'key: "fulfil"' in nav_block and grouped == 10)
    nav_items = re.findall(r'\{ key: "([\w]+)", label: "([^"]+)", icon: "([\w-]+)", path: "([^"]+)", view: "(\w+)"', src)

    # ---- 3. every route lands on a view the app really has ------------------
    toggled = set(re.findall(r'\$\("(\w+View)"\)\.classList\.toggle\("hidden",v!=="(\w+)"\)', idx))
    app_views = {v for _, v in toggled} | {"orders", "brain"}
    routed = {m[4] for m in nav_items} | set(re.findall(r'path: "[^"]+", view: "(\w+)"', src))
    missing = sorted(routed - app_views)
    check(f"every route targets a real view (offenders: {missing})", not missing)
    # Nothing loses its address. picking/catalog are the two removal candidates (D6).
    unreachable = sorted(app_views - routed - {"picking", "catalog", "quote"})
    check(f"every view keeps an address (unreachable: {unreachable})", not unreachable)

    # ---- 4. the gates are inherited, not restated ---------------------------
    for btn in sorted(set(re.findall(r'btn: "(\w+)"', src))):
        check(f"legacy nav button #{btn} exists (its visibility is the gate)", f'id="{btn}"' in idx)

    # ---- 5. the window.APP bridge ------------------------------------------
    check("index.html defines the window.APP bridge", "window.APP = {" in idx)
    exposed = set(re.findall(r"get (\w+)\(\)\{ return ", idx))
    read = set(re.findall(r"A\(\)\.(\w+)", src))
    check(f"bridge exposes everything shell.js reads (missing: {sorted(read - exposed)})", read <= exposed)

    # ---- 6. sub-tabs address real tab functions -----------------------------
    for fn in ["gmTab", "poSetView", "lxSetView"]:
        check(f"tab function {fn}() exists in the app", f"function {fn}(" in idx)
    gm_tabs = re.search(r'gaashmail: \{ keys: \[([^\]]+)\]', src).group(1)
    live = re.search(r'if\(\["([^"]+(?:","[^"]+)*)"\]\.includes\(t\)\) GM\.tab=t', idx)
    check("GAASH mail route tabs match the app's own list",
          sorted(re.findall(r'"(\w+)"', gm_tabs)) == sorted(live.group(1).split('","')))

    # ---- 7. attention: server kinds exist in the client registry ------------
    att = (HERE / "attention.py").read_text(encoding="utf-8")
    reg = (HERE / "static" / "ds" / "status.js").read_text(encoding="utf-8")
    vocab = set(re.findall(r"(\w+): T\(", re.search(r"const attention = \{(.*?)\};", reg, re.S).group(1)))
    kinds = set(re.findall(r'_item\([^,]+, "(\w+)"', att)) | set(re.findall(r'"(\w+)"\}', ""))
    kinds |= set(re.findall(r'BRAIN_KIND = \{(.*?)\}', att, re.S)[0].count and
                 re.findall(r': "(\w+)"', re.search(r"BRAIN_KIND = \{(.*?)\}", att, re.S).group(1)))
    kinds |= set(re.findall(r'rows\("\w+", "(\w+)"', att))
    check(f"every attention kind is in the registry (extra: {sorted(kinds - vocab)})", kinds <= vocab)
    check("attention.py is served by /api/attention",
          "@app.route(\"/api/attention\")" in (HERE / "app.py").read_text(encoding="utf-8"))

    # ---- 8. off by default, classic shell intact ----------------------------
    check("the shell is opt-in (reads a stored flag)", 'store.get(FLAG, "") === "ds"' in src)
    check("it does nothing when the flag is off", "if (!S.enabled() || !window.setView" in src)
    check("the classic sidebar and top bar are only HIDDEN, never removed",
          "body.ds-shell-on #sidebar, body.ds-shell-on .main > .topbar { display: none; }"
          in (HERE / "static" / "ds" / "ds.css").read_text(encoding="utf-8"))
    check("the Needs attention container exists in index.html", 'id="attentionView"' in idx)

    # ---- 9. it parses, and the route table is self-consistent ---------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed checks")
    else:
        js = f"""
        globalThis.window = globalThis; globalThis.document = {{
          addEventListener(){{}}, getElementById(){{return null}}, querySelector(){{return null}},
          querySelectorAll(){{return []}}, readyState: "loading", body: {{classList:{{add(){{}}}}}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        globalThis.location = {{hash:"", search:"", pathname:"/app"}};
        require({str(HERE / "static" / "ds" / "status.js")!r});
        require({str(HERE / "static" / "ds" / "format.js")!r});
        require({str(HERE / "static" / "ds" / "ds.js")!r});
        require({str(SHELL)!r});
        const S = window.DS.shell2;
        console.log(JSON.stringify({{
          loaded: typeof S === "object",
          po: S.href("purchases"), poTab: S.href("purchases", "packages"),
          gm: S.href("gaashmail", "docs"), bad: S.href("nope"),
          badTab: S.href("purchases", "not-a-tab"),
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("shell.js runs under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:3])
        else:
            import json as _json
            o = _json.loads(r.stdout.strip().splitlines()[-1])
            check("DS.shell2 is exported", o["loaded"])
            check("href() builds the canonical path", o["po"] == "#/fulfillment/purchase-orders")
            check("href() appends a known sub-tab", o["poTab"] == "#/fulfillment/purchase-orders/packages")
            check("href() addresses a GAASH mail tab", o["gm"] == "#/shipping/gaash-mail/docs")
            check("href() falls back for an unknown view", o["bad"] == "#/sales/orders")
            check("href() ignores an unknown sub-tab", o["badTab"] == "#/fulfillment/purchase-orders")

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
