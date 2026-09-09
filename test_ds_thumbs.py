#!/usr/bin/env python3
"""
One product photo, everywhere — and the seven lists that had none.

The owner asked for product photos "on every view we have in the dashboard". An
inventory found seven lists with none, and something worse underneath: photos were
drawn FOUR different ways — 24-26px vs 32px, `cover` vs `contain`, caps of 4/4/5/6,
and only two of the four merged duplicate photos. Open one purchase order and both
styles were on screen at once, 32px letterboxed above 26px cropped.

Every check below exists because of one of those, or because of a regression this
change can cause:

  * a photo-less slot in Leluxe is not an absence, it is the BUTTON that opens the
    item editor to attach an ASIN — and the only way in. Collapsing an all-photoless
    parcel to "3 products" deletes that workflow;
  * "+K" and the trailing total are two different real numbers: distinct photos you
    cannot see, and item lines on the order;
  * the empty placeholder is a <span>, and a non-replaced INLINE element ignores
    width and height — outside a flex parent it collapsed to 2px wide;
  * `emptyLabel` has to beat `label` on a photo-less slot, or the product's name
    shadows the hint that says clicking is how you fix it;
  * the "+31" is the only part of a strip carrying a fact you cannot get elsewhere,
    so it must never be the thing a narrow column clips away;
  * both legacy wrappers must stay `function` declarations — a top-level `const` in
    index.html is not a window property, and `W.poThumbStrip(...)` would throw,
    blanking the Purchases packages grid.

    ./.venv/bin/python test_ds_thumbs.py
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
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", ln) for ln in src.splitlines())


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def rule(css, sel):
    """The body of one CSS rule, or "" — a check about a rule that does not exist
    yet must FAIL, not die."""
    m = re.search(r"(?m)^%s\s*\{([^}]*)\}" % re.escape(sel), css)
    return m.group(1) if m else ""


def main():
    ds = (DS / "ds.js").read_text(encoding="utf-8")
    css = (DS / "ds.css").read_text(encoding="utf-8")
    pur = (DS / "purchases.js").read_text(encoding="utf-8")
    sales = (DS / "sales.js").read_text(encoding="utf-8")
    ful = (DS / "fulfillment.js").read_text(encoding="utf-8")
    lx = (DS / "leluxe.js").read_text(encoding="utf-8")
    idx = (HERE / "web" / "index.html").read_text(encoding="utf-8")

    # ---- 1. one size, one fit, one place that declares them -----------------
    thumb = rule(css, ".ds-thumb, .ds-pu-thumb")
    check("the photo rule exists", bool(thumb))
    check("  one size: 32px", "inline-size: 32px" in thumb and "block-size: 32px" in thumb)
    check("  one fit: contain, never a square crop", "object-fit: contain" in thumb)
    # the empty placeholder is a <span>; inline elements ignore width/height
    check("  and it keeps its box outside a flex parent", "display: inline-block" in thumb)
    check("the 24px override that split the sizes is gone",
          ".ds-fl-thumbs .ds-pu-thumb" not in css)
    strip = rule(css, ".ds-thumbs, .ds-fl-thumbs")
    check("the strip clips rather than wraps or overflows",
          "flex-wrap: nowrap" in strip and "overflow: clip" in strip
          and re.search(r"min-inline-size:\s*(?!0)", strip) is not None)
    slot = rule(css, ".ds-thumb-slot")
    check("a clickable slot is positioned for its badge", "position: relative" in slot)
    check("  and stops looking like the app's bare buttons",
          "padding: 0" in slot and "border: 0" in slot and "background: transparent" in slot)
    check("the count is pinned, so a narrow column never clips it away",
          "position: sticky" in rule(css, ".ds-thumb-more, .ds-fl-more"))

    # ---- 2. ds.js is the only file that BUILDS the markup -------------------
    for name, src in (("purchases.js", pur), ("sales.js", sales),
                      ("fulfillment.js", ful), ("leluxe.js", lx)):
        check(f"  {name} does not re-implement the photo",
              "ds-pu-thumb" not in code_only(src) and "ds-fl-thumbs" not in code_only(src))
    check("purchases.js delegates instead of keeping its own copy", "const thumb = D.thumb;" in pur)

    # ---- 3. both legacy names survive, as delegating wrappers ---------------
    # A test pins the literal poThumbStrip call, and purchases.js reaches both through
    # `W.` — a top-level const in index.html is not a window property.
    for fn in ("poThumbStrip", "lxThumbs"):
        check(f"{fn} is still a function declaration", f"function {fn}(" in idx)
    for fn, body in (("poThumbStrip", "function poThumbStrip(items,max=4){"),
                     ("lxThumbs", "function lxThumbs(items,max){")):
        i = idx.find(body)
        seg = idx[i:i + 900] if i >= 0 else ""
        check(f"  {fn} delegates to DS.thumbs", "DS.thumbs(" in seg)
        check(f"  {fn} renders nothing for an empty list, not a dash", 'empty:""' in seg.replace(" ", ""))
    i = idx.find("function lxThumbs(items,max){")
    lxseg = idx[i:i + 900] if i >= 0 else ""
    check("  Leluxe never collapses a photo-less parcel to a count",
          "countWhenBlank:false" in lxseg.replace(" ", ""))
    check("  ...and its slots still open the item editor", "lxOpenEditor('item'," in lxseg)

    # ---- 4. every list that has products shows them -------------------------
    for label, src, needle in (
        ("Purchases: Orders row", pur, "D.thumbs(poItems(p)"),
        ("Purchases: Packages row", pur, "pkgCell(ctx, p, pk, { thumbs: true })"),
        ("Purchases: Customers row", pur, "D.thumbs(r.tuples.map"),
        ("Package prep row", ful, "D.thumbs((c.orders || []).flatMap"),
        ("Sales: parcels in an order", sales, "D.thumbs(pk.items"),
        ("Sales: Customers row", sales, "ctx.items ? ctx.items(c)"),
        ("Leluxe: the untracked parcel", lx, "W.lxThumbs(p.items"),
    ):
        check(f"  {label} shows its products", needle in src)
    check("the Customers board can reach a customer's products", "function cuItemsOf(c)" in idx)
    check("  by the same name/phone match the app already uses",
          "normName(" in idx[idx.find("function cuItemMap()"):idx.find("function cuItemsOf(c)")]
          and "phoneCoreJs(" in idx[idx.find("function cuItemMap()"):idx.find("function cuItemsOf(c)")])

    # ---- 5. Est. cost is folded away on the Orders board, not deleted -------
    i = pur.find("function ordersBoard(")
    orders = pur[i:pur.find("\n  function ", i + 1)] if i >= 0 else ""
    m = re.search(r'\{ key: "est",[^\n]*', orders)
    check("the Orders board still HAS an Est. cost column", bool(m))
    check("  it just does not start visible", bool(m) and "defaultHidden: true" in m.group(0))
    check("  with a seed bump, since it shipped visible", 'id: "po_orders", seedVersion:' in pur)
    check("  the Packages board keeps its estimate", 'key: "est"' in pur[pur.find("function packagesBoard("):])
    check("  and the total is still in the page header", 'label: "Est. cost", value: money(s.est)' in pur)
    # a bump that hides a column must not leave the board sorted by it
    tbl = (DS / "table.js").read_text(encoding="utf-8")
    check("a seed bump drops a sort pointing at a column it just hid",
          "seed.includes(sort.key)" in tbl)

    # ---- 6. it runs, and it behaves ----------------------------------------
    node = shutil.which("node")
    if not node:
        print("  -- node not found: skipping the executed checks")
    else:
        js = f"""
        globalThis.window = globalThis;
        globalThis.document = {{ getElementById(){{return null}}, querySelector(){{return null}},
                                 querySelectorAll(){{return []}}, addEventListener(){{}} }};
        globalThis.localStorage = {{getItem(){{return null}}, setItem(){{}}, removeItem(){{}}}};
        for (const f of ["status.js","format.js","ds.js"]) require({str(DS)!r} + "/" + f);
        const D = window.DS;
        const a = {{asin:"A", title:"Kettle", image:"u/a.jpg"}};
        const b = {{asin:"B", title:"Cable",  image:"u/b.jpg"}};
        const dup = D.thumbs([a,a,a,b], {{max:2}});
        const lelu = D.thumbs([{{id:7}},{{id:8}}], {{countWhenBlank:false, add:true,
          emptyLabel:"no image - click to add an ASIN", onclick:(it)=>"lxOpenEditor('item',"+it.id+",null)"}});
        console.log(JSON.stringify({{
          // the three the existing suite pins — they must not move
          photo:    D.thumbs([a]).includes("<img"),
          noPhoto:  D.thumbs([{{asin:"X"}},{{asin:"Y"}}]).includes("2 products"),
          empty:    D.thumbs([]) === D.dash(),
          // duplicates merge, and the badge counts them
          dupImgs:  (dup.match(/<img/g)||[]).length,
          dupPlus:  /\\+\\d/.test(dup),
          dupBadge: dup.indexOf("\\u00d73") >= 0,
          // each slot names its own product
          perSlot:  D.thumbs([a,b]).includes('title="Kettle"') && D.thumbs([a,b]).includes('title="Cable"'),
          emptyOpt: D.thumbs([], {{empty:""}}) === "",
          totalOff: !D.thumbs([a,b], {{total:false}}).includes("ds-muted"),
          // the Leluxe workflow
          leluSlots:  (lelu.match(/ds-thumb-slot/g)||[]).length,
          leluButton: lelu.includes("<button"),
          // The handler is entity-escaped for the attribute, which is right - so decode it
          // the way a browser does and COMPILE it. A plain string match would also pass on
          // a DOUBLE-escaped handler, which is exactly how every tab in the app went dead
          // once before.
          leluClick:  (() => {{
            const raw = (lelu.match(/onclick="([^"]*)"/) || [])[1] || "";
            const dec = raw.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
            if (dec.indexOf("&#") >= 0) return "still escaped: " + dec;
            try {{ new Function("lxOpenEditor", dec); }} catch (e) {{ return "will not compile: " + dec; }}
            return dec === "lxOpenEditor('item',7,null)";
          }})(),
          leluDashed: lelu.includes("ds-thumb-add"),
          leluHint:   lelu.includes('title="no image - click to add an ASIN"'),
          // a custom accessor, the way Leluxe keeps images at data.image
          srcOpt:   D.thumbs([{{data:{{image:"u/z.jpg"}}}}], {{src:(it)=>it.data&&it.data.image}}).includes("u/z.jpg"),
          // the string third argument still means `alt`
          altStr:   D.thumb(a, "Custom tip").includes('title="Custom tip"'),
        }}));
        """
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, cwd=HERE)
        if r.returncode:
            check("the modules run under node", False)
            print("   ", (r.stderr or "").strip().splitlines()[:4])
        else:
            o = json.loads(r.stdout.strip().splitlines()[-1])
            check("the modules run under node", True)
            check("  a single photo still renders", o["photo"] is True)
            check("  no photos anywhere still falls back to a count", o["noPhoto"] is True)
            check("  an empty list is still a dash by default", o["empty"] is True)
            check(f"  duplicates merge into one slot ({o['dupImgs']} images)", o["dupImgs"] == 2)
            check("  ...so there is no +K when everything distinct is shown", o["dupPlus"] is False)
            check("  ...and the merged slot says how many", o["dupBadge"] is True)
            check("  every slot names its own product", o["perSlot"] is True)
            check("  a caller can ask for nothing on an empty list", o["emptyOpt"] is True)
            check("  a caller can drop the trailing count", o["totalOff"] is True)
            check(f"  Leluxe keeps its slots with no photos at all ({o['leluSlots']})", o["leluSlots"] == 2)
            check("  ...as real buttons", o["leluButton"] is True)
            check("  ...that open the item editor (%s)" % o["leluClick"], o["leluClick"] is True)
            check("  ...drawn as a placeholder you can fill", o["leluDashed"] is True)
            check("  ...and say so, rather than showing the product name", o["leluHint"] is True)
            check("  a caller can say where its images live", o["srcOpt"] is True)
            check("  the old string argument still means alt", o["altStr"] is True)

    print()
    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("all thumbnail checks passed")


if __name__ == "__main__":
    main()
