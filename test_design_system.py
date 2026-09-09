#!/usr/bin/env python3
"""
Self-checks: the design system (UX restructure, Phase 1).

Proves the foundations are real and safe to load app-wide:
  * all seven static/ds files exist and are wired into the staff shell + service worker;
  * /design-system is registered and admin-only;
  * ds.css cannot restyle existing markup (every selector is anchored on .ds-),
    and every token is --ds- prefixed, so nothing collides with the legacy :root;
  * the status registry covers every order status in store.STATUSES, every ClickUp
    package status in the live schema, and agrees with the Python "parcel is done" sets;
  * the icon sprite has no emoji and the DS sources carry none either;
  * with node available, status.js + format.js are executed and their output asserted.

    ./.venv/bin/python test_design_system.py
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


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿]")


def main():
    # ---- 1. files exist -----------------------------------------------------
    files = ["tokens.css", "ds.css", "ds.js", "table.js", "status.js", "format.js", "icons.svg"]
    for f in files:
        check(f"static/ds/{f} exists", (DS / f).exists())
    css = (DS / "ds.css").read_text(encoding="utf-8")
    tokens = (DS / "tokens.css").read_text(encoding="utf-8")
    sprite = (DS / "icons.svg").read_text(encoding="utf-8")

    # ---- 2. wired into the shell and the service worker ---------------------
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    for f in files[:-1]:
        check(f"index.html loads {f}", f"/static/ds/{f}" in idx)
    head = idx.split("<style>")[0]
    check("design system loads BEFORE the page's own <style>", "/static/ds/ds.css" in head)
    sw = (HERE / "web" / "sw.js").read_text(encoding="utf-8")
    check("sw.js precaches the design system", "DS_ASSETS" in sw and "/static/ds/ds.css" in sw)
    check("sw.js cache name bumped off v1", 'CACHE = "otl-off-v1"' not in sw)
    check("sw.js serves /static/ds/ network-first", "/static/ds/" in sw and "function ds(" in sw)

    # ---- 3. the catalogue route --------------------------------------------
    appsrc = (HERE / "app.py").read_text(encoding="utf-8")
    check("/design-system route registered", '@app.route("/design-system")' in appsrc)
    ds_fn = appsrc.split('@app.route("/design-system")')[1].split("@app.route")[0]
    check("/design-system requires login", "@login_required" in ds_fn)
    check("/design-system is admin-only", 'has("admin_actions")' in ds_fn)
    check("design-system.html exists", (HERE / "web" / "design-system.html").exists())

    # ---- 4. ds.css cannot restyle the existing app --------------------------
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    unanchored = []
    for m in re.finditer(r"([^{}]+)\{", stripped):
        sel = m.group(1).strip()
        if not sel or sel.startswith("@") or sel in ("from", "to") or re.match(r"^\d+%$", sel):
            continue
        for part in sel.split(","):
            p = part.strip()
            if p and ".ds-" not in p and not p.startswith("dialog.ds-"):
                unanchored.append(p)
    check(f"every ds.css selector is anchored on .ds- ({len(unanchored)} strays)", not unanchored)
    if unanchored:
        print("      strays:", unanchored[:6])
    names = set(re.findall(r"(--[\w-]+)\s*:", tokens))
    check(f"all {len(names)} tokens are --ds- prefixed", all(n.startswith("--ds-") for n in names))
    legacy = set(re.findall(r"(--[\w-]+)\s*:", idx.split("<script")[0]))
    check("no token collides with the legacy :root", not (names & legacy))
    check("ds.css uses no physical CSS properties",
          not re.search(r"(?<![-\w])(margin|padding)-(left|right)\s*:|(?<![-\w])(left|right)\s*:\s*[-\d]|text-align\s*:\s*(left|right)", stripped))
    check("ds.css colours come from tokens (no raw hex outside a data: URI)",
          not [h for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"url\([^)]*\)", "", stripped))])

    # ---- 5. no emoji in the design system -----------------------------------
    for f in ["ds.js", "table.js", "status.js", "format.js", "ds.css"]:
        txt = (DS / f).read_text(encoding="utf-8")
        found = sorted(set(EMOJI.findall(txt)))
        check(f"{f} carries no emoji glyphs", not found)
        if found:
            print("      found:", found[:8])
    check("icon sprite has >100 symbols", len(re.findall(r'id="i-', sprite)) > 100)
    check("icon sprite keeps its MIT licence text", "MIT" in sprite or "Permission is hereby granted" in sprite)
    check("icons use currentColor (themeable)", 'stroke="currentColor"' in sprite)

    # ---- 6. the status registry matches the Python truth --------------------
    reg = (DS / "status.js").read_text(encoding="utf-8")
    sys.path.insert(0, str(HERE))
    import store, purchases, pkgprep, alerts  # noqa: E402
    order_block = reg.split("const order = {")[1].split("};")[0]
    for s in store.STATUSES:
        check(f"status registry knows order status {s}", f"{s}:" in order_block)
    check("PAID has a tone (it had no colour in the old map)", re.search(r"PAID:\s*T\(", order_block) is not None)
    pkg_block = reg.split("const pkg = {")[1].split("};")[0]
    for s in purchases.ITEM_EXCEPTIONS:
        check(f"status registry knows PO item exception {s}", f"{s}:" in reg)
    cfg = HERE / "config.json"
    if cfg.exists():
        live = [x["status"] for x in json.loads(cfg.read_text()).get("leluxe", {}).get("schema", {}).get("statuses", [])]
        missing = [s for s in live if f'"{s}"' not in pkg_block]
        check(f"registry covers all {len(live)} live ClickUp package statuses", not missing)
        if missing:
            print("      missing:", missing)
    else:
        print("  -- config.json absent; skipping the live ClickUp status cross-check")
    done_js = set(re.findall(r'"([^"]+)"', reg.split("const pkgDone = new Set([")[1].split("]")[0]))
    check("pkgDone mirrors alerts.STOP_DEFAULT (F-003: one 'done' set)", done_js == set(alerts.STOP_DEFAULT))
    rec_js = set(re.findall(r'"([^"]+)"', reg.split("const pkgReceived = new Set([")[1].split("]")[0]))
    check("pkgReceived mirrors pkgprep.RECEIVED_STATUSES", rec_js == set(pkgprep.RECEIVED_STATUSES))
    dis_js = set(re.findall(r'"([^"]+)"', reg.split("const pkgDispatched = new Set([")[1].split("]")[0]))
    check("pkgDispatched mirrors pkgprep.DISPATCHED_STATUSES", dis_js == set(pkgprep.DISPATCHED_STATUSES))
    check("typo spellings are preserved verbatim (owner decision)",
          all(f'"{s}"' in pkg_block for s in ["oredered", "recieved rd", "delievered no rd", "not recieved rd"]))

    # ---- 7. run the JS if node is available ---------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found; skipping the JavaScript behaviour checks")
    else:
        for f in ["status.js", "format.js", "ds.js", "table.js"]:
            r = subprocess.run([node, "--check", str(DS / f)], capture_output=True, text=True)
            check(f"{f} parses", r.returncode == 0)
            if r.returncode:
                print("      ", r.stderr.strip()[:200])
        probe = r"""
          global.window = {}; global.document = { addEventListener(){}, documentElement:{} };
          require(process.argv[1]); require(process.argv[2]);
          const DS = window.DS, out = {};
          out.paidTone = DS.status.tone("order", "PAID");
          out.paidLabel = DS.status.label("order", "PAID");
          out.typoLabel = DS.status.label("pkg", "recieved rd");
          out.typoHex = DS.status.hex("pkg", "recieved rd");
          out.doneTrue = DS.status.isPkgDone("complete");
          out.doneFalse = DS.status.isPkgDone("not recieved rd");
          out.unknown = DS.status.label("pkg", "something new");
          out.emptyLabel = DS.status.label("order", "");
          out.money = DS.fmt.money(827.55);
          out.moneyIls = DS.fmt.money(3050, "ILS", {approx:true});
          out.moneyNeg = DS.fmt.money(-12.5);
          out.moneyEmpty = DS.fmt.money(null);
          out.num = DS.fmt.number(1234567.891);
          out.dateIso = DS.fmt.date("2026-09-05");
          out.dateSlash = DS.fmt.date("5/9/2026");
          out.dateMs = DS.fmt.date(1757030400000).length > 0;
          out.rel = DS.fmt.relative(new Date(Date.now() - 3*3600e3));
          out.days = DS.fmt.daysUntil("2026-09-01", "2026-09-07");
          out.iso = DS.fmt.iso("1/2/2026");
          console.log(JSON.stringify(out));
        """
        r = subprocess.run([node, "-e", probe, str(DS / "status.js"), str(DS / "format.js")], capture_output=True, text=True)
        if r.returncode:
            check("status.js + format.js execute", False)
            print("      ", r.stderr.strip()[:400])
        else:
            o = json.loads(r.stdout)
            check("PAID renders with a tone, not the grey fallback", o["paidTone"] in ("info", "neutral", "success") and o["paidLabel"] == "Paid")
            check("ClickUp typo value keeps its spelling and colour", o["typoLabel"] == "recieved rd" and o["typoHex"] == "#0ff17e")
            check("isPkgDone('complete') is true", o["doneTrue"] is True)
            check("isPkgDone('not recieved rd') is FALSE (fixes APP_AUDIT F-008)", o["doneFalse"] is False)
            check("an unknown status falls back to its raw value", o["unknown"] == "something new")
            check("an empty status renders as a dash", o["emptyLabel"] == "—")
            check("money is USD with a symbol and 2 decimals", o["money"] == "$827.55")
            check("a converted amount carries its own code, never a second $", o["moneyIls"] == "≈ 3,050 ILS")
            check("negative money uses a minus sign", o["moneyNeg"].startswith("−$") or o["moneyNeg"].startswith("-$"))
            check("empty money renders as a dash", o["moneyEmpty"] == "—")
            check("numbers group with Western digits", o["num"] == "1,234,567.89")
            check("dates: ISO and D/M/YYYY parse to the same day", o["dateIso"] == "5 Sep 2026" and o["dateSlash"] == "5 Sep 2026")
            check("dates: ms-epoch parses", o["dateMs"] is True)
            check("relative time reads '3 h ago'", "3 h ago" in o["rel"])
            check("daysUntil counts backwards correctly", o["days"] == -6)
            check("iso() normalises D/M/YYYY", o["iso"] == "2026-02-01")

        # ---- 8. an inline handler this file builds must be runnable JS -------
        # Every tab strip and view pill in the app shipped `foo(&quot;bar&quot;)` as the
        # SOURCE of its onclick for two days: DS.tabs pre-escaped the quotes and then
        # attrs() escaped the & again. Clicking one threw `Unexpected token '&'`, and
        # nothing caught it because the suites only ever called the target function
        # directly. Compile the handler exactly as a browser does - decode the attribute
        # once, then parse it - so a double-escape can never ship again.
        probe2 = r"""
          global.window = {}; global.document = { addEventListener(){}, documentElement:{} };
          require(process.argv[1]);
          const DS = window.DS;
          const html = DS.tabs({ id:"t", active:"a",
            items:[{key:"a",label:"A"},{key:"b-2",label:"B"}], onchange:"go(KEY)" });
          const raw = [...html.matchAll(/onclick="([^"]*)"/g)].map(m => m[1]);
          const dec = s => s.replace(/&quot;/g,'"').replace(/&#39;/g,"'")
                            .replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&amp;/g,"&");
          const out = { n: raw.length, calls: [], ok: true, err: null };
          for (const r of raw) {
            const src = dec(r);
            out.calls.push(src);
            try { new Function(src); } catch (e) { out.ok = false; out.err = String(e); }
          }
          console.log(JSON.stringify(out));
        """
        r2 = subprocess.run([node, "-e", probe2, str(DS / "ds.js")], capture_output=True, text=True)
        if r2.returncode:
            check("DS.tabs renders", False)
            print("      ", r2.stderr.strip()[:300])
        else:
            o2 = json.loads(r2.stdout)
            check("every DS.tabs onclick compiles as JavaScript", o2["ok"])
            if not o2["ok"]:
                print("      ", o2["err"], o2["calls"][:2])
            check("the key reaches the handler as a plain string literal",
                  any('go("b-2")' in c for c in o2["calls"]))
            check("no HTML entity survives into the handler source",
                  not any("&quot;" in c or "&amp;" in c for c in o2["calls"]))

    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
