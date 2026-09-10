#!/usr/bin/env python3
"""The 2026-09-10 UI sweep (UI_QA.md section 24): the Customers row that painted over
its neighbour, the CSS comment that swallowed every LXT pinned column, the profile
drawer, and the smaller ones. Every check names the surface it protects; the file must
FAIL on the tree before the sweep (proved with git stash)."""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
idx = (ROOT / "web/index.html").read_text()
sales = (ROOT / "static/ds/sales.js").read_text()
leluxe = (ROOT / "static/ds/leluxe.js").read_text()
shell = (ROOT / "static/ds/shell.js").read_text()
dsjs = (ROOT / "static/ds/ds.js").read_text()
table = (ROOT / "static/ds/table.js").read_text()
css = (ROOT / "static/ds/ds.css").read_text()
sw = (ROOT / "web/sw.js").read_text()

fails = 0
def check(label, ok):
    global fails
    print(("  OK  " if ok else "  FAIL") + " " + label)
    if not ok: fails += 1

def between(src, a, b):
    """the slice between two markers, or "" when either is missing (a test that
    crashes on the pre-fix tree proves nothing)"""
    i = src.find(a)
    if i < 0: return ""
    j = src.find(b, i + len(a))
    return src[i:j] if j >= 0 else src[i:]

def live_css(style):
    """the style block with comments stripped the way the parser reads them - a rule
    inside an unclosed comment is not CSS"""
    return re.sub(r"/\*.*?\*/", "", style, flags=re.S)

# ---------------------------------------------------------------- Q-051 the swallowed comment
style = between(idx, "<style>", "</style>")
live = live_css(style)
check("the style block has no comment that never closes",
      "/*" not in live)
check(".bt-pin (the LXT pinned column) is live CSS again, not commented out",
      ".bt-pin{position:sticky" in live and "flex:0 0 var(--btpin" in live)
check(".bt-wrap .poc-meta got its own rule back", ".bt-wrap .poc-meta{width:max-content" in live)
check(".btscrolled .bt-pin (the scroll shadow) is live", ".btscrolled .bt-pin{" in live)

# ---------------------------------------------------------------- Q-052 the Customers cell
who = between(sales, 'key: "who"', 'key: "vip"')
check("the customer cell exists", bool(who))
check("the photo strip is INSIDE the identity row (one line), not appended after it",
      "D.thumbs(" in who and who.find("D.thumbs(") < who.rfind("</span>"))
check("the identity row keeps its phone", "ds-sl-phone" in who)
check("the customer column grew to hold the strip", "w: 360" in who)
check("ds.css keeps the strip a fixed-size flex item at the end of the row",
      ".ds-sl-who > .ds-thumbs { flex: 0 0 auto; margin-inline-start: auto; }" in css)

# ---------------------------------------------------------------- Q-053 the profile drawer
prof = between(sales, "C.profile = (c, ctx) =>", "C.board = ")
check("DS.customers.profile builds a DS drawer", "D.drawer({" in prof and 'id: "cuProfileDlg"' in prof)
for piece in ("WhatsApp", "Email", "City", "Address", "Payment", "Notes", "ID document", "ID number",
              "Order history", "Edit customer", "ID photo", "custIdNumberEdit", "wa.me/"):
    check(f"the drawer keeps what the panel showed: {piece}", piece in prof)
check("the profile drawer closes before an action hands over to a page flow",
      prof.count("DS.dialogClose('cuProfileDlg');") >= 3)
check("the permanent profile panel is gone from the Customers grid", 'id="custProfile"' not in idx)
check("the grid no longer reserves a third of the width for it", "1.3fr .7fr" not in idx)
sp = between(idx, "function showProfile(id){", "function editCustomer(id){")
check("showProfile opens the drawer through the module", "DS.customers.profile(" in sp and "DS.dialogOpen(" in sp)
check("the drawer is transient (removed on close) and never stacked",
      'dataset.transient="1"' in sp and "old.remove()" in sp)
check("the bridge passes money, edit rights and the ID image url", "function cuProfileCtx()" in idx
      and "idImageUrl" in idx and "canEdit:cuCanEdit()" in idx)

# ---------------------------------------------------------------- Q-054 editable blank city
city = between(idx, "cityCell: c=>", "idCell:")
check("the editable city keeps its dashed mark through a class, not an inline style",
      "ds-sl-edit" in city and "border-bottom:1px dashed" not in city)
check("the target is at least 24px wide", ".ds-sl-edit {" in css and "min-inline-size: 24px" in css.split(".ds-sl-edit {", 1)[-1].split("}", 1)[0])

# ---------------------------------------------------------------- Q-055 Leluxe names
check("Leluxe product cells no longer cut the name to three words (the cell ellipsizes)",
      "esc(W.lxShort3(" not in leluxe)
pk = between(leluxe, "function packageCell(r)", "function kOrderCell")
check("a Leluxe package shows two photos, so the count stays inside the cell", "W.lxThumbs(its, 2)" in pk)
check("the package count truncates with an ellipsis and a tooltip instead of clipping",
      'class="ds-muted ds-truncate" title=' in pk)

# ---------------------------------------------------------------- Q-056 P&L batch chart
check("the batch chart grows with its labels instead of overflowing the heading",
      "#pnlChart{display:flex;align-items:flex-end;gap:7px;min-height:96px" in style)
check("batch labels are single-line with an ellipsis", ".pnl-lbl{max-inline-size:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" in style
      and 'class="pnl-lbl"' in idx)
check("a batch that already starts with # is not printed as ##", "b.startsWith('#')?b:'#'+b" in idx)

# ---------------------------------------------------------------- Q-057 the Purchases help box
check("the help box reserves room for its Hide link", "padding-inline-end:56px}" in between(style, ".po-help{", ".po-help .hk"))

# ---------------------------------------------------------------- Q-058 activity feed
act = between(idx, "function actRow(e){", "return `<div class=\"act-row\">")
check("the 'on …' line needs a named field (otherwise it repeats the title)",
      'e.action==="set"&&e.field&&e.detail' in act)

# ---------------------------------------------------------------- Q-059 double titles
m = re.search(r"const OWN_TITLE = new Set\(\[(.*?)\]\)", shell)
keys = set(re.findall(r'"(\w+)"', m.group(1))) if m else set()
check("the shell knows which legacy pages draw their own title",
      keys == {"metaleads", "deposits", "pnl", "goals", "activity", "settings", "team", "trash", "flags", "leluxe"})
check("those pages get the breadcrumb only", "title: OWN_TITLE.has(v) ? false : it.label" in shell)
check("DS.pageHeader renders a crumb-only header for title:false",
      "if (o.title === false) return crumbs ?" in dsjs and "ds-pagehead-crumbs" in dsjs)
check("OWN_HEADER pages are untouched by it", "OWN_HEADER.has(v)\n      ? below" in shell)

# ---------------------------------------------------------------- Q-060 the check cell
rc = between(table, 'case "rowclick": {', 'case "allcell"')
check("a click anywhere in the check cell toggles the row (not just the 15px box)",
      'ev.target.closest(".ds-td-check")' in rc and 'new Event("change", { bubbles: true })' in rc)
check("the click beside the box no longer falls through to onRowClick", rc.find('closest(".ds-td-check")') < rc.find("onRowClick"))
check("the select-all header cell does the same", 'case "allcell":' in table and "'allcell',null,event" in table)
check("both cells show a pointer", ".ds-td-check, .ds-th-check { cursor: pointer; }" in css)

# ---------------------------------------------------------------- housekeeping
# v24 was this sweep's bump; later PRs keep moving it on (v25 = the operator role) — never backwards
check("the offline cache moved on (ds.css / sales.js / leluxe.js / table.js / shell.js changed)",
      int((re.search(r'otl-off-v(\d+)', sw) or ["", "0"])[1]) >= 24)
new_css = css.split("/* ---- Customers: the photo strip", 1)[-1]
check("every new ds.css selector carries .ds-", all(".ds-" in sel for sel in re.findall(r"(?m)^([^{}/\n][^{]*)\{", new_css)))
check("no raw hex in the new ds.css rules", not re.search(r"#[0-9a-fA-F]{3,6}\b", new_css))

print(f"\n{'all UI-sweep checks passed' if not fails else f'{fails} check(s) FAILED'}")
sys.exit(1 if fails else 0)
