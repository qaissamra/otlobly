#!/usr/bin/env python3
"""inventory.py — read-only census of the Otlobly staff app for the UX restructure.

Parses web/index.html (+ app.py routes) and prints the numbers AUDIT.md cites, so every count is
reproducible and the same script can become the Phase 1 lint (warn level) later.

    python3 docs/ux-restructure/tools/inventory.py [repo_root] [--json PATH] [--md PATH]

Writes docs/ux-restructure/inventory.json and inventory.md by default. Never modifies product code.
"""
import collections
import json
import re
import sys
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
ROOT = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parents[1]
HTML = ROOT / "web" / "index.html"
APP = ROOT / "app.py"
src = HTML.read_text(encoding="utf-8")
lines = src.split("\n")


def lineno(pos):
    return src.count("\n", 0, pos) + 1


def count(pattern, text=src, flags=0):
    return len(re.findall(pattern, text, flags))


def sites(pattern, text=src, flags=0, width=110, base=1):
    out = []
    for m in re.finditer(pattern, text, flags):
        ln = text.count("\n", 0, m.start()) + base
        out.append({"line": ln, "text": lines[ln - 1].strip()[:width] if text is src else ""})
    return out


# ---------------------------------------------------------------- regions
style_m = re.search(r"<style>(.*?)</style>", src, re.S)
style_start = lineno(style_m.start()) if style_m else 0
style_end = lineno(style_m.end()) if style_m else 0
style = style_m.group(1) if style_m else ""
scripts = [(lineno(m.start()), lineno(m.end()), m.group(2)) for m in re.finditer(r"<script([^>]*)>(.*?)</script>", src, re.S)]
scripts_sorted = sorted(scripts, key=lambda t: -(t[1] - t[0]))
js_start, js_end, js = scripts_sorted[0] if scripts_sorted else (0, 0, "")
markup_start, markup_end = style_end + 1, js_start - 1
report = {
    "file": str(HTML.relative_to(ROOT)),
    "total_lines": len(lines),
    "bytes": len(src.encode("utf-8")),
    "regions": {
        "style": [style_start, style_end],
        "markup": [markup_start, markup_end],
        "script": [js_start, js_end],
        "script_blocks": [[a, b] for a, b, _ in scripts],
    },
    "html_head_body_elements": {
        "<html": count(r"<html[\s>]", flags=re.I),
        "<head": count(r"<head[\s>]", flags=re.I),
        "<body": count(r"<body[\s>]", flags=re.I),
    },
}

# ---------------------------------------------------------------- sidebar + top bar
def nav_items(nav_id):
    m = re.search(r'<nav id="%s"[^>]*>(.*?)</nav>' % nav_id, src, re.S)
    if not m:
        return []
    items, base = [], lineno(m.start(1))
    body = m.group(1)
    for mm in re.finditer(r'<(button|div|a)([^>]*)>(.*?)</\1>', body, re.S):
        tag, attrs, inner = mm.group(1), mm.group(2), mm.group(3)
        if tag == "div" and "nav-sec" not in attrs:
            continue
        text = re.sub(r"<[^>]+>", "", inner).strip()
        text = re.sub(r"\s+", " ", text)
        get = lambda k: (re.search(r'%s="([^"]*)"' % k, attrs) or [None, None])[1]
        items.append({
            "line": base + body.count("\n", 0, mm.start()),
            "kind": "group" if "nav-sec" in attrs else tag,
            "id": get("id"), "onclick": get("onclick"),
            "data_en": get("data-en"), "data_ar": get("data-ar"),
            "hidden_by_default": bool(re.search(r"display:\s*none", attrs or "")),
            "text": text[:80],
        })
    return items

report["sidebar"] = nav_items("sideNav")
report["platform_nav"] = nav_items("platNav")
gates = {}
for it in report["sidebar"] + report["platform_nav"]:
    if it["id"]:
        hits = [{"line": s["line"], "text": s["text"]} for s in sites(r'"%s"' % re.escape(it["id"]))
                if s["line"] >= js_start]
        gates[it["id"]] = hits[:6]
report["sidebar_gates_js_refs"] = gates
tb = re.search(r'<div class="topbar">', src)
if tb:
    tb_start = lineno(tb.start())
    chunk = "\n".join(lines[tb_start - 1: tb_start + 14])
    report["topbar"] = {"line": tb_start, "ids": re.findall(r'id="([^"]+)"', chunk),
                        "buttons": count(r"<button", chunk), "excerpt_lines": [tb_start, tb_start + 14]}

# ---------------------------------------------------------------- views
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿←-⇿⌀-⏿■-◿"
                   "⤀-⥿⬀-⯿∀-⋿⁉‼ℹ〰〽㊗㊙Ⓜ™]")
views = [{"id": m.group(1), "line": lineno(m.start())} for m in re.finditer(r'<div[^>]*\bid="([A-Za-z]+View)"', src)]
first_modal = re.search(r'class="az-modal', src)
modal_line = lineno(first_modal.start()) if first_modal else markup_end
for i, v in enumerate(views):
    end = (views[i + 1]["line"] - 1) if i + 1 < len(views) else modal_line - 1
    v["end"] = end
    chunk = "\n".join(lines[v["line"] - 1: end])
    v["lines"] = end - v["line"] + 1
    v["census"] = {
        "button": count(r"<button", chunk), "onclick": count(r"onclick=", chunk),
        "input": count(r"<input", chunk), "select": count(r"<select", chunk), "textarea": count(r"<textarea", chunk),
        "file_input": count(r'type="file"', chunk), "table": count(r"<table", chunk), "h2": count(r"<h2", chunk),
        "toolbar": count(r'class="toolbar', chunk), "po_btn": count(r"po-btn", chunk), "minibtn": count(r"minibtn", chunk),
        "iconbtn": count(r"iconbtn", chunk), "emoji": len(EMOJI.findall(chunk)),
        "emoji_distinct": len(set(EMOJI.findall(chunk))),
    }
    nested = [w["id"] for w in views if w is not v and v["line"] < w["line"] <= end]
    if nested:
        v["nested_view_ids"] = nested
report["views"] = views
vb = re.search(r"const VIEW_BTN\s*=\s*\{(.*?)\};", src, re.S)
view_btn = dict(re.findall(r'(\w+)\s*:\s*"(\w+)"', vb.group(1))) if vb else {}
report["view_btn"] = {"line": lineno(vb.start()) if vb else None, "map": view_btn,
                      "dead_button_ids": [b for b in view_btn.values() if not re.search(r'id="%s"' % b, src)]}
toggles = re.findall(r'classList\.toggle\("hidden",\s*v!==?"(\w+)"', src)
report["setview"] = {
    "line": (sites(r"function setView\(") or [{"line": None}])[0]["line"],
    "hidden_toggles": len(re.findall(r'classList\.toggle\("hidden"', "\n".join(lines[(sites(r"function setView\(") or [{"line": 1}])[0]["line"] - 1:(sites(r"function setView\(") or [{"line": 1}])[0]["line"] + 120]))),
    "toggle_view_ids": sorted(set(toggles)),
    "call_sites": count(r"\bsetView\("),
    "router_signals": {"pushState": count(r"pushState"), "hashchange": count(r"hashchange"), "location.hash": count(r"location\.hash")},
    "localStorage_keys": sorted(set(re.findall(r'localStorage\.(?:getItem|setItem|removeItem)\("([^"]+)"', src))),
}

# ---------------------------------------------------------------- JS regions by anchor
ANCHORS = [
    ("shell: globals · i18n · roles · notifications · load()", r"^const STATUS_COLOR"),
    ("orders board", r"^function render\("),
    ("picking list (hidden)", r"function renderPicking"),
    ("add-order panel", r"function aoOpenForm"),
    ("view registry · setView", r"function restoreView|const VIEW_BTN"),
    ("package prep", r"function loadPkgprep"),
    ("catalog (hidden)", r"function loadCatalog"),
    ("leluxe (boards, filters, widths)", r"function loadLeluxe"),
    ("LXT table engine (inside leluxe)", r"const LXT_COLS"),
    ("goals + leluxe tail (goal, parcels, sync, push, move)", r"function loadGoals"),
    ("deposits", r"function loadDeposits"),
    ("meta leads", r"function loadMetaLeads"),
    ("brain", r"function loadBrain"),
    ("platform console", r"const PLAT_VIEWS|function loadPlatOverview"),
    ("brokers", r"function loadBrokers"),
    ("team", r"function loadTeam"),
    ("activity + trash", r"const ACT_ICON|function loadActivity"),
    ("to order + quick quote", r"const NE_COLS|function loadNeedOrder"),
    ("in cart", r"function loadIncart"),
    ("order edit modal · price image", r"function closeOrderEdit|function openOrderEdit"),
    ("quote tool (QSTR)", r"const QSTR"),
    ("settings", r"function loadSettings"),
    ("az / multilogin modal", r"function openAzModal"),
    ("purchases (cont.) + bulk-progress modal", r"function bulkOpen"),
    ("purchases: constants head (PO_BOXES…)", r"const PO_BOXES"),
    ("purchases: load · render · cards · packages", r"function loadPurchases"),
    ("customers", r"function loadCustomers"),
    ("p&l", r"function loadPnl"),
    ("bulk search", r"function bsInit|function bsRun"),
    ("gaash mail (+ forecast, analyze)", r"const GM_STATE|function gmLoad"),
    ("gaash mail tail: flags + forecast + analyze", r"function flagLoad"),
]
anchors = []
for label, pat in ANCHORS:
    m = re.search(pat, js, re.M)
    if m:
        anchors.append((js_start + js.count("\n", 0, m.start()), label))
anchors.sort()
regions = []
for i, (ln, label) in enumerate(anchors):
    end = anchors[i + 1][0] - 1 if i + 1 < len(anchors) else js_end
    chunk = "\n".join(lines[ln - 1: end])
    regions.append({"region": label, "start": ln, "end": end, "lines": end - ln + 1, "census": {
        "onclick": count(r"onclick=", chunk), "popMenu": count(r"popMenu\(", chunk), "button": count(r"<button", chunk),
        "input": count(r"<input", chunk), "select": count(r"<select", chunk), "table": count(r"<table", chunk),
        "toast": count(r"\btoast\(", chunk), "confirm": count(r"\bconfirm\(", chunk), "prompt": count(r"\bprompt\(", chunk),
        "alert": count(r"\balert\(", chunk), "raw_pill": count(r'class="pill', chunk),
        "pill_helpers": count(r"\b(tonePill|hexPill|solidPill|statusPill|lxStatusPill|lxCfPill|gaashBucketPill)\(", chunk),
        "lxtHead": count(r"lxtHead\(", chunk), "fld": count(r"\bfld\(", chunk), "editCell": count(r"\beditCell\(", chunk),
        "T_calls": count(r"\bT\(", chunk), "emoji": len(EMOJI.findall(chunk)), "emoji_distinct": len(set(EMOJI.findall(chunk))),
    }})
report["js_regions"] = regions

# ---------------------------------------------------------------- global counts
G = {
    "<table": count(r"<table"), "</table>": count(r"</table>"), "<button": count(r"<button"), "<input": count(r"<input"),
    "<select": count(r"<select"), "<textarea": count(r"<textarea"), "onclick=": count(r"onclick="), 'title="': count(r'title="'),
    "aria-*": count(r"\baria-[a-z]+="), 'role="': count(r'\brole="'), 'alt="': count(r'\balt="'), "tabindex": count(r"tabindex"),
    "confirm(": count(r"\bconfirm\("), "prompt(": count(r"\bprompt\("), "alert(": count(r"\balert\("), "toast(": count(r"\btoast\("),
    "popMenu(": count(r"popMenu\("), 'class="pill (raw literal)': count(r'class="pill'), "az-modal roots": count(r'class="az-modal'),
    "<dialog": count(r"<dialog"), '<h2': count(r"<h2"), 'class="toolbar': count(r'class="toolbar'), '.search inputs': count(r'class="search'),
    "cu-search": count(r"cu-search"), "muted2": count(r"muted2"), 'class="empty"': count(r'class="empty"'),
    "T( calls": count(r"\bT\("), "data-en=": count(r"data-en="), "data-ar=": count(r"data-ar="), "data-i18n": count(r"data-i18n"),
    'dir="auto"': count(r'dir="auto"'), "<bdi": count(r"<bdi"), 'dir="rtl"': count(r'dir="rtl"'), "unicode-bidi": count(r"unicode-bidi"),
    "Intl.": count(r"\bIntl\."), "toLocaleString(": count(r"toLocaleString\("), "toLocaleDateString(": count(r"toLocaleDateString\("),
    "toLocaleTimeString(": count(r"toLocaleTimeString\("), "toLocaleString(undefined": count(r"toLocale(?:Date|Time)?String\(undefined"), "toLocale*String with a pinned locale": count(r"toLocale(?:Date|Time)?String\(\s*[\"']"),
    "prefers-color-scheme": count(r"prefers-color-scheme"), "prefers-reduced-motion": count(r"prefers-reduced-motion"),
    'type="file"': count(r'type="file"'), '"paste" listeners': count(r'addEventListener\("paste"'), "ondrop/dragover": count(r"\bondrop=|\bdragover\b"),
    "fetch(": count(r"\bfetch\("), "localStorage refs": count(r"localStorage\."), "<svg": count(r"<svg"),
    "external icon libs": count(r"font-awesome|material-icons|lucide|feather|heroicons|bootstrap-icons|remixicon", flags=re.I),
    "bilingual literals (ar · en)": count("[؀-ۿ][^\"'`\\n]{0,60} · [^\"'`\\n]{0,60}[A-Za-z]|[A-Za-z][^\"'`\\n]{0,60} · [^\"'`\\n]{0,60}[؀-ۿ]"),
    "lines containing Arabic": sum(1 for l in lines if re.search("[؀-ۿ]", l)),
}
report["global_counts"] = G

# ---------------------------------------------------------------- emoji census
em_all = EMOJI.findall(src)
em_side = EMOJI.findall(re.search(r'<nav id="sideNav".*?</nav>', src, re.S).group(0)) if re.search(r'<nav id="sideNav"', src) else []
c = collections.Counter(em_all)
report["emoji"] = {"total": len(em_all), "distinct": len(c), "sidebar": len(em_side),
                   "top": [{"glyph": g, "count": k, "codepoint": "U+%04X" % ord(g)} for g, k in c.most_common(40)],
                   "directional": {g: c[g] for g in "→←↩↪▸▾▴◂⤴↗↙⇒⇐" if c.get(g)}}

# ---------------------------------------------------------------- physical CSS
PHYS = [("margin-left", r"margin-left\s*:"), ("margin-right", r"margin-right\s*:"), ("padding-left", r"padding-left\s*:"),
        ("padding-right", r"padding-right\s*:"), ("left:", r"(?<![-\w])left\s*:"), ("right:", r"(?<![-\w])right\s*:"),
        ("text-align:left", r"text-align\s*:\s*left"), ("text-align:right", r"text-align\s*:\s*right"),
        ("float", r"float\s*:\s*(?:left|right)"), ("border-left", r"border-left(?!-radius)"), ("border-right", r"border-right(?!-radius)")]
phys = {}
for name, pat in PHYS:
    in_style = [{"line": style_start + style.count("\n", 0, m.start()), "text": lines[style_start + style.count("\n", 0, m.start()) - 1].strip()[:110]}
                for m in re.finditer(pat, style)]
    phys[name] = {"style_block": len(in_style), "whole_file": count(pat), "style_sites": in_style}
LOGICAL = {k: count(k) for k in ["padding-inline", "margin-inline", "inset-inline", "border-inline", "text-align:start", "text-align:end"]}
report["physical_css"] = {"by_property": phys, "style_total": sum(v["style_block"] for v in phys.values()),
                          "file_total": sum(v["whole_file"] for v in phys.values()), "logical_counts": LOGICAL}

# ---------------------------------------------------------------- tokens · type · colour
root_blocks = [(lineno(m.start()), m.group(1)) for m in re.finditer(r":root\s*\{([^}]*)\}", src, re.S)]
tokens = []
for ln, body in root_blocks:
    tokens += re.findall(r"(--[\w-]+)\s*:", body)
var_use = collections.Counter(re.findall(r"var\((--[\w-]+)", style))
report["tokens"] = {"root_blocks": [ln for ln, _ in root_blocks], "declared": tokens, "count": len(tokens),
                    "var_uses_in_css": sum(var_use.values()), "unused_in_css": [t for t in tokens if var_use.get(t, 0) == 0],
                    "top_uses": var_use.most_common(12),
                    "runtime_grid_vars": sorted(set(re.findall(r"var\((--\w*grid|--btpin)", src)))}
fs = collections.Counter(re.findall(r"font-size\s*:\s*([0-9.]+px)", style))
report["font_sizes"] = {"distinct_in_css": len(fs), "values": sorted(fs.items(), key=lambda kv: float(kv[0][:-2])),
                        "fonts_loaded": re.findall(r'fonts\.googleapis\.com/css2\?family=([^"&]+)', src),
                        "font_family_decls": collections.Counter(re.findall(r"font-family\s*:\s*([^;}]+)", style)).most_common(10)}
hex6 = re.findall(r"#[0-9a-fA-F]{6}\b", src)
hex6_css = re.findall(r"#[0-9a-fA-F]{6}\b", style)
report["hex_literals"] = {"css": len(hex6_css), "css_distinct": len(set(h.lower() for h in hex6_css)),
                          "file": len(hex6), "file_distinct": len(set(h.lower() for h in hex6))}
regs = []
for m in re.finditer(r"^(?:\s{0,2})const\s+([A-Z][A-Z0-9_]+)\s*=\s*[\[{]", js, re.M):
    ln = js_start + js.count("\n", 0, m.start())          # 1-based line of the const
    end = ln
    for j in range(ln, min(ln + 60, len(lines))):          # walk until the next top-level declaration
        if re.match(r"^\s*(?:const|let|var|function)\b", lines[j]):
            break
        end = j + 1
    block = "\n".join(lines[ln - 1: end])
    hexes = re.findall(r"#[0-9a-fA-F]{6}\b", block)
    if hexes:
        regs.append({"name": m.group(1), "line": ln, "end": end, "hex_values": len(hexes), "distinct_hex": len(set(h.lower() for h in hexes))})
report["colour_registries"] = {"count": len(regs), "items": regs}
report["pill_css_rules"] = [{"line": style_start + style.count("\n", 0, m.start()), "text": m.group(0)[:160]} for m in re.finditer(r"(?m)^\s*\.pill\s*\{[^}]*\}", style)]
report["modals"] = [{"line": s["line"], "id": (re.search(r'id="(\w+)"', s["text"]) or [None, None])[1]} for s in sites(r'class="az-modal')]

# ---------------------------------------------------------------- helpers · formatters · landmarks
def defs(name):
    return [s["line"] for s in sites(r"(?:function\s+%s\s*\(|(?:const|let|var)\s+%s\s*=)" % (name, name))]
report["formatters"] = {n: defs(n) for n in ["money", "money0", "fmt", "cfNum", "lxGm", "relTime", "agoTxt", "gmAgo", "fmtDue", "lxDate", "cfFmtDate", "gmChgLabel", "gmChgTime", "dueChip"]}
report["helper_usage"] = {n: count(r"\b%s\(" % n) for n in ["statusPill", "tonePill", "hexPill", "solidPill", "gaashBucketPill", "lxStatusPill", "lxCfPill",
                                                                "fld", "editCell", "openStore", "popMenu", "popToggle", "lxtHead", "lxtCells", "lxtWrap", "toast", "T", "cuLabel"]}
LAND = ["function setView(", "const VIEW_BTN", "function restoreView", "const RESTRICTED_NAV", "const ADD_ORDER_VIEWS", "const REFRESH_VIEWS", "const PLAT_VIEWS",
        "const STATUS_COLOR", "const EXC_LABEL", "const CU_STATUSES", "const GASH_STATUS", "const GAASH_HEX", "const PO_GAASH_BUCKETS", "const BUCKET_HEX", "const GZ_HEX",
        "const GZ_AR", "const GM_STATE", "const ML_STATUS", "const TONE", "const MON", "const GM_DAY_LABELS", "const PO_BOXES", "const NE_COLS", "const LXT_COLS", "const LX_TABLES",
        "function lxtHead", "function lxtCells", "function lxtGridApply", "function neTable", "let LANG", "function T(", "function localizeStatic", "function langSet",
        "const QSTR", "let QLANG", "function toast(", "function statusPill", "function tonePill", "function hexPill", "function solidPill", "function fld(", "function editCell",
        "function openStore", "function popMenu", "function bulkOpen", "function load(", "function applyRole", "function renderSummary", 'id="sideNav"', 'class="topbar"',
        'id="sub"', '$("sub").textContent', 'id="langToggle"', 'id="notifBell"', "function poProfileCell", "function lxProfileChip", "function pkgDocsPill", "function gmDocsStatePill",
        "function lxIsDone", "function pkgStatus(", "function poRollupStatus", "function dueChip", "function pkgCuSelect", "function cuLabel", "function poCardHtml", "function poPkgHtml",
        "function renderPurchases", "function gmTab", "function bsTokens", "function gmNewGwds", "PILL POLICY", ".trk-cell:placeholder-shown", "function gaashUploadOpen"]
report["landmarks"] = {k: [s["line"] for s in sites(re.escape(k))][:4] for k in LAND}
report["file_inputs_and_paste"] = {
    "index.html": sites(r'type="file"|addEventListener\("paste"|ondrop=|"dragover"'),
    "templates": {p.name: [{"line": i + 1, "text": l.strip()[:100]} for i, l in enumerate(p.read_text(encoding="utf-8").split("\n"))
                           if re.search(r'type="file"|"paste"|ondrop=|dragover', l)] for p in sorted((ROOT / "templates").glob("*.html"))},
}

# ---------------------------------------------------------------- app.py routes without a UI caller
app_src = APP.read_text(encoding="utf-8")
routes = [(app_src.count("\n", 0, m.start()) + 1, m.group(1), m.group(2) or "") for m in re.finditer(r'@app\.route\(\s*["\']([^"\']+)["\']([^)]*)\)', app_src)]
ui_text = src + "\n" + "\n".join(p.read_text(encoding="utf-8") for p in list((ROOT / "templates").glob("**/*.html")) + list((ROOT / "static").glob("**/*.js")) + list(ROOT.glob("*.js")))
py_files = [p for p in ROOT.glob("*.py") if p.name != "app.py"]
script_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_files if not p.name.startswith("test_"))
test_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_files if p.name.startswith("test_"))
no_ui = []
for ln, path, extra in routes:
    needle = re.split(r"<", path)[0].rstrip("/") or path
    if needle in ("/", "/api"):
        continue
    in_ui, in_script, in_test = needle in ui_text, needle in script_text, needle in test_text
    if not in_ui:
        no_ui.append({"line": ln, "route": path, "methods": re.sub(r"\s+", "", extra)[:40], "script_caller": in_script, "test_caller": in_test})
report["routes"] = {"total": len(routes), "without_ui_caller": no_ui}

# ---------------------------------------------------------------- cross-checks
ALIAS = {"gaashmail": "gmView"}                       # view id → container id when the names differ
NESTED = {"quoteView": "needorderView"}                    # containers that live inside another view
missing_containers = [v for v in report["setview"]["toggle_view_ids"] if not re.search(r'id="%s"' % ALIAS.get(v, v + "View"), src, re.I) and v not in ("orders",)]
report["cross_checks"] = {
    "view_btn_keys": sorted(view_btn.keys()),
    "view_btn_dead_buttons": report["view_btn"]["dead_button_ids"],
    "toggle_ids_without_container": missing_containers,
    "containers_without_toggle": [v["id"] for v in views if v["id"] not in NESTED and v["id"] not in ALIAS.values() and v["id"].replace("View", "").lower() not in [t.lower() for t in report["setview"]["toggle_view_ids"]]],
    "aliases": ALIAS, "nested_containers": NESTED,
}

# ---------------------------------------------------------------- write
json_path = OUT_DIR / "inventory.json"
md_path = OUT_DIR / "inventory.md"
for a in sys.argv[1:]:
    if a.startswith("--json="): json_path = Path(a[7:])
    if a.startswith("--md="): md_path = Path(a[5:])
json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def md():
    o = []
    p = o.append
    p("# inventory.md — mechanical census of `web/index.html` (generated by tools/inventory.py)\n")
    p(f"Lines {report['total_lines']:,} · {report['bytes']/1024:.0f} KB · style {report['regions']['style']} · markup {report['regions']['markup']} · script {report['regions']['script']} · `<html>/<head>/<body>` elements: {report['html_head_body_elements']}\n")
    p("## Sidebar (`#sideNav`)\n\n| line | kind | id | label (data-en) | hidden by default | onclick |\n|---|---|---|---|---|---|")
    for it in report["sidebar"]:
        p(f"| {it['line']} | {it['kind']} | {it['id'] or ''} | {(it['data_en'] or it['text'])[:40]} | {'yes' if it['hidden_by_default'] else ''} | {(it['onclick'] or '')[:40]} |")
    p(f"\nPlatform nav (`#platNav`): {len([i for i in report['platform_nav'] if i['kind']!='group'])} items. Top bar ids: {report.get('topbar',{}).get('ids')}\n")
    p(f"`VIEW_BTN` ids with no matching button: {report['view_btn']['dead_button_ids']} · setView call sites {report['setview']['call_sites']} · router signals {report['setview']['router_signals']} · localStorage keys {len(report['setview']['localStorage_keys'])}\n")
    p("## View containers (markup)\n\n| id | lines | size | button | onclick | input | select | file | table | h2 | emoji (distinct) |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for v in report["views"]:
        c = v["census"]
        p(f"| {v['id']} | {v['line']}–{v['end']} | {v['lines']} | {c['button']} | {c['onclick']} | {c['input']} | {c['select']} | {c['file_input']} | {c['table']} | {c['h2']} | {c['emoji']} ({c['emoji_distinct']}) |")
    p("\n## JS regions (by anchor function)\n\n| region | lines | size | onclick | popMenu | toast | confirm | prompt | raw pill | pill helpers | lxtHead | T() | emoji |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in report["js_regions"]:
        c = r["census"]
        p(f"| {r['region']} | {r['start']}–{r['end']} | {r['lines']:,} | {c['onclick']} | {c['popMenu']} | {c['toast']} | {c['confirm']} | {c['prompt']} | {c['raw_pill']} | {c['pill_helpers']} | {c['lxtHead']} | {c['T_calls']} | {c['emoji']} |")
    p("\n## Global counts\n\n| what | count |\n|---|---|")
    for k, v in report["global_counts"].items():
        p(f"| `{k}` | {v} |")
    e = report["emoji"]
    p(f"\n## Emoji / symbol glyphs used as icons\n\nTotal {e['total']:,} · distinct {e['distinct']} · in the sidebar {e['sidebar']} · directional {e['directional']}\n\nTop: " + " · ".join(f"{t['glyph']} {t['count']}" for t in e["top"][:30]) + "\n")
    ph = report["physical_css"]
    p(f"## Physical CSS properties\n\nIn the `<style>` block: {ph['style_total']} sites · whole file: {ph['file_total']} · logical properties already used: {ph['logical_counts']}\n\n| property | style block | whole file |\n|---|---|---|")
    for k, v in ph["by_property"].items():
        p(f"| {k} | {v['style_block']} | {v['whole_file']} |")
    p("\nStyle-block sites:\n")
    for k, v in ph["by_property"].items():
        for s in v["style_sites"]:
            p(f"- L{s['line']} `{k}` — `{s['text']}`")
    t = report["tokens"]
    p(f"\n## Tokens\n\n`:root` blocks at {t['root_blocks']} · {t['count']} custom properties: {' '.join(t['declared'])} · `var()` uses in CSS {t['var_uses_in_css']} · unused in CSS {t['unused_in_css']} · runtime grid vars {len(t['runtime_grid_vars'])}\n")
    f = report["font_sizes"]
    p(f"Font sizes in CSS: {f['distinct_in_css']} distinct — " + ", ".join(f"{k} ×{n}" for k, n in f["values"]) + f"\n\nFonts loaded: {f['fonts_loaded']} · font-family declarations: {f['font_family_decls']}\n")
    h = report["hex_literals"]
    p(f"Hex colour literals: CSS {h['css']} ({h['css_distinct']} distinct) · whole file {h['file']} ({h['file_distinct']} distinct)\n")
    p(f"## Colour / status registries in JS ({report['colour_registries']['count']} `const` objects/arrays carrying hex values)\n\n| name | line | hex values |\n|---|---|---|")
    for r in report["colour_registries"]["items"]:
        p(f"| `{r['name']}` | {r['line']} | {r['hex_values']} ({r['distinct_hex']} distinct) |")
    p("\n## `.pill` CSS rules\n")
    for r in report["pill_css_rules"]:
        p(f"- L{r['line']} `{r['text']}`")
    p(f"\n## Modals (`.az-modal` roots: {len(report['modals'])})\n\n" + ", ".join(f"{m['id']} (L{m['line']})" for m in report["modals"]) + "\n")
    p("## Formatter definitions (line numbers; >1 = duplicate/shadowed)\n\n| name | defined at |\n|---|---|")
    for k, v in report["formatters"].items():
        if v:
            p(f"| `{k}` | {v} |")
    p("\n## Helper call counts\n\n| helper | calls |\n|---|---|")
    for k, v in report["helper_usage"].items():
        p(f"| `{k}()` | {v} |")
    p("\n## File inputs, paste listeners, drop handlers\n\nindex.html:\n")
    for s in report["file_inputs_and_paste"]["index.html"]:
        p(f"- L{s['line']} `{s['text']}`")
    for name, ss in report["file_inputs_and_paste"]["templates"].items():
        for s in ss:
            p(f"- templates/{name} L{s['line']} `{s['text']}`")
    r = report["routes"]
    p(f"\n## app.py routes with no caller in the UI ({len(r['without_ui_caller'])} of {r['total']})\n\n| line | route | methods | called by a script | called by a test |\n|---|---|---|---|---|")
    for x in r["without_ui_caller"]:
        p(f"| {x['line']} | `{x['route']}` | {x['methods']} | {'yes' if x['script_caller'] else ''} | {'yes' if x['test_caller'] else ''} |")
    p(f"\n## Cross-checks\n\n{json.dumps(report['cross_checks'], ensure_ascii=False)}\n")
    p("## Landmarks (line numbers)\n\n| symbol | lines |\n|---|---|")
    for k, v in report["landmarks"].items():
        p(f"| `{k}` | {v} |")
    return "\n".join(o) + "\n"


md_path.write_text(md(), encoding="utf-8")
print(f"wrote {json_path} and {md_path}")
print(f"lines={report['total_lines']} views={len(views)} sidebar_items={len([i for i in report['sidebar'] if i['kind']!='group'])} emoji={report['emoji']['total']}/{report['emoji']['distinct']} tables={G['<table']} confirm={G['confirm(']} prompt={G['prompt(']} toast={G['toast(']} modals={len(report['modals'])} routes_no_ui={len(no_ui)}/{len(routes)} colour_regs={len(regs)} phys_css={report['physical_css']['style_total']}")
