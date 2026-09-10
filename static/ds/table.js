/* ============================================================================
   Otlobly design system — DataTable (static/ds/table.js)
   The one list component (brief §9.3): CSS-grid rows with pixel column widths,
   sticky header strip mirrored to the body scroller, sticky start/end columns
   (status + actions pinned to the end), per-user resize / reorder / hide / sort
   persisted in localStorage, selection + BulkActionBar, RowExpansion, keyboard
   navigation, Skeleton / EmptyState / error states, typed cell formatting.
   Successor of the LXT engine in web/index.html (LX_TABLES / LXT_COLS / lxt*),
   which pages leave behind as they migrate (docs/ux-restructure/MIGRATION.md).

     DS.tableRender(el, {
       id: "po",                                   // persistence key
       columns: [{ key, label, type, w, min, sortable, pin:"start"|"end", locked, align, entity, currency,
                   render(row, tbl) -> html, sortVal(row), quick(row) -> [button opts], menu(row) -> [menu items], title }],
       rows: [...], rowKey: r => r.id, rowClass(r), rowTitle(r), onRowClick(r, ev),
       selectable: true, bulk: [{ label, icon, danger, onclick(keys, rows) }], onSelect(keys, rows, table),
       expandable: { render(r) -> html, open(r) -> bool },
       sort: { key, dir }, onSort(key, dir),            // omit onSort to sort locally (blanks last, both directions)
       loading: false, error: null, retry(), empty: { title, text, action },
       footer: { key: html }, page: { from, to, total, onPrev(), onNext() }, density: "compact"|"comfortable",
       onStateChange(state)                            // width / order / hidden / sort / density (for the router later)
       seedVersion: n                                  // bump when a column's defaultHidden CHANGES, so saved layouts pick it up once
     })
   Column types: id · text · number · money · date · status · attention · actions · bool.
   ========================================================================== */
(function () {
  "use strict";
  const DS = (window.DS = window.DS || {});
  const esc = DS.esc, attrs = DS.attrs, cls = DS.cls;
  const TABLES = (DS._tables = DS._tables || {});
  const KEY = (id) => "ds_table_" + id;
  /** The pages mark "this row has no value here" with a "~" sentinel in `sortVal`,
      on the assumption that "~" sorts after every letter - true of ASCII (`"~" > "z"`),
      NOT of the locale-aware compare below, where punctuation collates FIRST. So every
      one of those columns put the blanks at the TOP of an ascending sort. Hoist the
      sentinel here, next to the null handling it belongs with, rather than in 35 call
      sites: blank is blank however a page spells it, and blank sorts last. */
  const blank = (v) => v == null || v === "" || (typeof v === "string" && /^~+$/.test(v));
  const cmp = (a, b) => {
    const an = blank(a), bn = blank(b);
    if (an && bn) return 0; if (an) return 1; if (bn) return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    const na = Number(a), nb = Number(b);
    if (!isNaN(na) && !isNaN(nb) && String(a).trim() !== "" && String(b).trim() !== "") return na - nb;
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  };
  /** The sort DIRECTION applies to values, never to blanks. `cmp` already puts a blank
      after every value, but the local sort used to multiply its WHOLE verdict by -1 for a
      descending sort - so a "~" that correctly sat last on the first click was lifted to
      the top of the board on the second (Q-036's other half). A blank is not the smallest
      value, it is the absence of one, and it sits under the real rows whichever way the
      column runs: blank vs blank is a tie (they keep their order), blank vs value is
      "after", and only value vs value is `cmp` times the direction. */
  const cmpDir = (a, b, dir) => {
    const an = blank(a), bn = blank(b);
    if (an || bn) return an && bn ? 0 : an ? 1 : -1;
    return cmp(a, b) * dir;
  };

  class Table {
    constructor(o) { this.o = o; this.id = o.id; this.selected = new Set(); this.open = new Set(); this.load(); }
    load() {
      let s = {}; try { s = JSON.parse(localStorage.getItem(KEY(this.id)) || "{}") || {}; } catch (e) { s = {}; }
      // `defaultHidden` folds a column away the first time someone meets this table,
      // and their saved layout owns it from then on. But a column ADDED later had no
      // way in: the saved `hidden` array simply won, so anyone who had ever used the
      // board saw every new defaultHidden column as visible, forever. `known` records
      // which columns a saved layout has actually seen, so a genuinely new one can be
      // seeded once without touching the choices they made.
      const all = (this.o.columns || []).map((c) => c.key);
      const dflt = (this.o.columns || []).filter((c) => c.defaultHidden).map((c) => c.key);
      // `known` covers a column ADDED as defaultHidden. It cannot cover a column that
      // already existed and has now BEEN MADE defaultHidden - the layout has seen it, so
      // it is never re-seeded and the new default reaches nobody who has used the board.
      // `seedVersion` is how a page says "the defaults changed": bump it and every default
      // is applied once more, on top of whatever else the user had hidden. It only ever
      // ADDS to `hidden`, so it cannot yank back a column they chose to show.
      const wantV = this.o.seedVersion || 0, haveV = s.seed || 0;
      let seed;
      if (!s.hidden || !s.known) seed = dflt;           // new table, or a layout predating `known`
      else if (haveV < wantV) seed = s.hidden.concat(dflt.filter((k) => !s.hidden.includes(k)));
      // A bump can hide the column the table is currently SORTED by - sorting reads the
      // column list, not the visible set, so the board would stay sorted by something
      // with no header and no arrow to explain it. Drop the sort with the column.
      else seed = s.hidden.concat(dflt.filter((k) => !s.known.includes(k) && !s.hidden.includes(k)));
      let sort = s.sort || this.o.sort || null;
      if (sort && sort.key && seed.includes(sort.key) && !(this.o.columns || []).some((c) => c.key === sort.key && c.locked)) sort = this.o.sort || null;
      this.state = { w: s.w || {}, hidden: seed, known: all, seed: wantV, order: s.order || null, sort, density: s.density || this.o.density || "compact" };
      // Stamp `known` now, not on the next change. Otherwise a layout saved before
      // this existed is re-seeded on EVERY load, so a column the user deliberately
      // un-hid folds itself away again each time they open the page.
      if (!s.known || haveV < wantV) { try { localStorage.setItem(KEY(this.id), JSON.stringify(this.state)); } catch (e) { /* private mode */ } }
    }
    save() { try { localStorage.setItem(KEY(this.id), JSON.stringify(this.state)); } catch (e) { /* private mode */ } if (this.o.onStateChange) this.o.onStateChange(this.state); }
    /** "Reset layout" means the layout the PAGE ships with, not "show me all 19 columns".
        It used to set `hidden: []`, so one click turned Orders from 12 columns into 19 and
        the row from 1166px into 2294px - and the only way back was hiding seven of them by
        hand. Re-seed from `defaultHidden`, exactly as a first visit does in load(). */
    reset() { this.state = { w: {}, hidden: (this.o.columns || []).filter((c) => c.defaultHidden).map((c) => c.key), known: (this.o.columns || []).map((c) => c.key), seed: this.o.seedVersion || 0, order: null, sort: this.o.sort || null, density: this.o.density || "compact" }; this.save(); }
    columns() {
      const base = this.o.columns.slice();
      if (this.state.order) { const idx = new Map(this.state.order.map((k, i) => [k, i])); base.sort((a, b) => (idx.has(a.key) ? idx.get(a.key) : 1e6 + this.o.columns.indexOf(a)) - (idx.has(b.key) ? idx.get(b.key) : 1e6 + this.o.columns.indexOf(b))); }
      return base.filter((c) => c.pin === "start").concat(base.filter((c) => !c.pin), base.filter((c) => c.pin === "end"));
    }
    visible() { return this.columns().filter((c) => c.locked || !this.state.hidden.includes(c.key)); }
    width(c) { return Math.max(c.min || 48, this.state.w[c.key] || c.w || 140); }
    rows() {
      const rows = (this.o.rows || []).slice(); const s = this.state.sort;
      if (s && s.key && !this.o.onSort) { const c = this.o.columns.find((x) => x.key === s.key); if (c) { const v = (r) => (c.sortVal ? c.sortVal(r) : r[c.key]); const dir = s.dir === "desc" ? -1 : 1; rows.sort((a, b) => cmpDir(v(a), v(b), dir)); } }
      return rows;
    }
    key(r, i) { return this.o.rowKey ? String(this.o.rowKey(r)) : String(r.id != null ? r.id : r.key != null ? r.key : i); }
    // ---- cells
    cell(r, c) {
      if (c.render) { const h = c.render(r, this); return h == null ? "" : h; }
      const v = r[c.key]; const empty = v == null || v === "";
      switch (c.type) {
        case "id": return empty ? DS.dash() : `<span class="ds-mono">${esc(v)}</span>`;
        case "number": return empty ? DS.dash() : esc(DS.fmt.number(v, { decimals: c.decimals }));
        case "money": return empty ? DS.dash() : esc(DS.fmt.money(v, c.currency || "USD"));
        case "date": return empty ? DS.dash() : `<span title="${esc(DS.fmt.title(v))}">${esc(c.relative === false ? DS.fmt.date(v) : DS.fmt.relative(v))}</span>`;
        case "status": return empty ? DS.dash() : DS.status.badge(c.entity || "order", v, { hex: c.hex });
        case "attention": return (Array.isArray(v) ? v : v ? [v] : []).map((a) => DS.attention(typeof a === "string" ? { kind: a } : a)).join("");
        case "bool": return v ? DS.icon("check", { size: 14 }) : "";
        case "actions": return this.actionsCell(r, c);
        default: return empty ? DS.dash() : `<span${c.dirAuto === false ? "" : ' dir="auto"'} title="${esc(v)}">${esc(v)}</span>`;
      }
    }
    actionsCell(r, c) {
      const quick = (c.quick ? c.quick(r) || [] : []).slice(0, 3).map((b) => DS.button(Object.assign({ size: "sm", variant: "ghost", iconOnly: true }, b))).join("");
      const items = c.menu ? c.menu(r) : null;
      return `<span class="ds-actions">${quick}${items && items.length ? DS.menu({ items, button: { size: "sm", variant: "ghost", icon: "ellipsis-horizontal", ariaLabel: "More actions" } }) : ""}</span>`;
    }
    // ---- layout
    lead() { const a = []; if (this.o.selectable) a.push({ kind: "check", w: 36 }); if (this.o.expandable) a.push({ kind: "exp", w: 28 }); return a; }
    template() { return this.lead().map((l) => l.w + "px").concat(this.visible().map((c) => this.width(c) + "px")).join(" "); }
    pins() { // sticky offsets for start / end pinned cells
      const lead = this.lead(); const vis = this.visible(); const start = [], end = [];
      let x = 0; lead.forEach((l) => { start.push({ off: x }); x += l.w; });
      vis.forEach((c) => { if (c.pin === "start") { start.push({ key: c.key, off: x }); x += this.width(c); } });
      let y = 0; vis.slice().reverse().forEach((c) => { if (c.pin === "end") { end.unshift({ key: c.key, off: y }); y += this.width(c); } });
      return { start, end };
    }
    cellAttrs(c, i, pins, isHead) {
      const s = pins.start.find((p) => p.key === c.key), e = pins.end.find((p) => p.key === c.key);
      const lastStart = s && pins.start[pins.start.length - 1] === s, firstEnd = e && pins.end[0] === e;
      const style = s ? `inset-inline-start:${s.off}px` : e ? `inset-inline-end:${e.off}px` : "";
      return { class: cls(isHead ? "ds-th" : "ds-td", `ds-col-${c.type || "text"}`, (c.align === "end" || c.type === "number" || c.type === "money") && "ds-num", s && "ds-pin-start", e && "ds-pin-end", lastStart && "is-last-pin", firstEnd && "is-first-pin", c.cls), style: style || null, "data-col": c.key, role: isHead ? "columnheader" : "cell" };
    }
    // ---- render
    head(pins) {
      const lead = this.lead(); const st = this.state.sort || {};
      const leadCells = lead.map((l, i) => l.kind === "check"
        ? `<div class="ds-th ds-th-check ds-pin-start" style="inset-inline-start:${pins.start[i].off}px" role="columnheader" onclick="DS.tableEv('${this.id}','allcell',null,event)"><input type="checkbox" aria-label="Select all rows" onchange="DS.tableEv('${this.id}','selectAll',null,event)"${this.allSelected() ? " checked" : ""}></div>`
        : `<div class="ds-th ds-td-exp ds-pin-start" style="inset-inline-start:${pins.start[i].off}px" role="columnheader"><span class="ds-sr">Expand</span></div>`).join("");
      const cells = this.visible().map((c) => {
        const sorted = st.key === c.key; const sortable = c.sortable !== false && c.type !== "actions";
        const a = this.cellAttrs(c, 0, pins, true);
        a.class = cls(a.class, sortable && "is-sortable"); a.tabindex = "0"; a["aria-sort"] = sorted ? (st.dir === "desc" ? "descending" : "ascending") : (sortable ? "none" : null);
        a.draggable = c.locked ? null : "true"; a.title = c.title || (sortable ? `Sort by ${c.label}` : null);
        a.onclick = sortable ? `DS.tableEv('${this.id}','sort','${esc(c.key)}',event)` : null;
        a.onkeydown = sortable ? `if(event.key==='Enter'||event.key===' '){event.preventDefault();DS.tableEv('${this.id}','sort','${esc(c.key)}',event)}` : null;
        a.oncontextmenu = `DS.tableEv('${this.id}','colmenu','${esc(c.key)}',event)`;
        a.ondragstart = c.locked ? null : `DS.tableEv('${this.id}','dragstart','${esc(c.key)}',event)`; a.ondragover = `DS.tableEv('${this.id}','dragover','${esc(c.key)}',event)`;
        a.ondragleave = `DS.tableEv('${this.id}','dragleave','${esc(c.key)}',event)`; a.ondrop = `DS.tableEv('${this.id}','drop','${esc(c.key)}',event)`; a.ondragend = `DS.tableEv('${this.id}','dragend','${esc(c.key)}',event)`;
        return `<div${attrs(a)}><span class="ds-th-label">${esc(c.label)}</span>${sorted ? DS.icon(st.dir === "desc" ? "arrow-down" : "arrow-up", { cls: "ds-sort" }) : ""}<span class="ds-rsz" draggable="false" onpointerdown="DS.tableEv('${this.id}','resize','${esc(c.key)}',event)" ondblclick="DS.tableEv('${this.id}','resizeReset','${esc(c.key)}',event)" aria-hidden="true"></span></div>`;
      }).join("");
      return `<div class="ds-table-headclip"><div class="ds-table-head" role="row">${leadCells}${cells}</div></div>`;
    }
    row(r, i, pins) {
      const k = this.key(r, i); const sel = this.selected.has(k); const open = this.open.has(k) || (this.o.expandable && this.o.expandable.open && this.o.expandable.open(r) && !this.closed?.has(k));
      const lead = this.lead().map((l, li) => l.kind === "check"
        ? `<div class="ds-td ds-td-check ds-pin-start" style="inset-inline-start:${pins.start[li].off}px" role="cell"><input type="checkbox" aria-label="Select row" onchange="DS.tableEv('${this.id}','select','${esc(k)}',event)"${sel ? " checked" : ""}></div>`
        : `<div class="ds-td ds-td-exp ds-pin-start" style="inset-inline-start:${pins.start[li].off}px" role="cell"><button type="button" class="ds-exp-btn" aria-expanded="${open ? "true" : "false"}" aria-label="${open ? "Collapse" : "Expand"}" onclick="DS.tableEv('${this.id}','toggle','${esc(k)}',event)">${DS.icon("chevron-right", { size: 14 })}</button></div>`).join("");
      const cells = this.visible().map((c) => `<div${attrs(this.cellAttrs(c, i, pins, false))}><span class="ds-cell">${this.cell(r, c)}</span></div>`).join("");
      const rowAttrs = { class: cls("ds-tr", sel && "is-selected", open && "is-open", this.o.onRowClick && "is-clickable", this.o.rowClass && this.o.rowClass(r)), role: "row", "data-key": k, tabindex: i === 0 ? "0" : "-1", "aria-selected": this.o.selectable ? (sel ? "true" : "false") : null, title: this.o.rowTitle ? this.o.rowTitle(r) : null,
        onclick: `DS.tableEv('${this.id}','rowclick','${esc(k)}',event)`, onkeydown: `DS.tableEv('${this.id}','rowkey','${esc(k)}',event)` };
      const exp = open && this.o.expandable ? `<div class="ds-tr-exp" data-exp="${esc(k)}">${this.o.expandable.render(r, this) || ""}</div>` : "";
      return `<div${attrs(rowAttrs)}>${lead}${cells}</div>${exp}`;
    }
    foot(pins) {
      if (!this.o.footer) return "";
      const lead = this.lead().map((l, li) => `<div class="ds-td ds-pin-start" style="inset-inline-start:${pins.start[li].off}px"></div>`).join("");
      return `<div class="ds-table-foot" role="row">${lead}${this.visible().map((c) => `<div${attrs(this.cellAttrs(c, 0, pins, false))}><span class="ds-cell">${this.o.footer[c.key] != null ? this.o.footer[c.key] : ""}</span></div>`).join("")}</div>`;
    }
    /** ABOVE the rows, not below them (Batch B3). It carries the Columns control,
        and under a 61-row board that put the only way to unhide a column ~2,800px
        down the page - the owner reported columns as "gone" because the escape
        hatch was unreachable.
        Moving it was not enough, and the claim that first stood here - that the
        bulk bar "is sticky to the bottom of the viewport, so it is never out of
        reach" - was WRONG. `.ds-table` was `overflow: hidden`, which made it a
        scroll container that never scrolls, so this bar, the bulk bar and the
        column headers all quietly scrolled away with the page. Measured: this bar
        at -115px and the bulk bar at 2843px in a 900px viewport. `overflow: clip`
        fixed all three; both bars are `position: sticky` in ds.css. */
    bar() {
      const p = this.o.page; const n = (this.o.rows || []).length;
      const count = p ? `${DS.fmt.number(p.from)}–${DS.fmt.number(p.to)} of ${DS.fmt.number(p.total)}` : `${DS.fmt.number(n)} ${n === 1 ? "row" : "rows"}`;
      const pager = p ? DS.buttonGroup([{ icon: "chevron-left", size: "sm", variant: "ghost", ariaLabel: "Previous page", disabled: p.from <= 1, onclick: `DS.tableEv('${this.id}','prev',null,event)` }, { icon: "chevron-right", size: "sm", variant: "ghost", ariaLabel: "Next page", disabled: p.to >= p.total, onclick: `DS.tableEv('${this.id}','next',null,event)` }]) : "";
      const hiddenN = this.state.hidden.filter((k) => this.o.columns.some((c) => c.key === k && !c.locked)).length;
      return `<div class="ds-table-bar"><span>${count}</span>${pager}<span class="ds-spacer"></span>${this.o.extraBar || ""}${DS.button({ icon: "adjustments-horizontal", label: hiddenN ? `Columns · ${hiddenN} hidden` : "Columns", size: "sm", variant: "ghost", onclick: `DS.tableEv('${this.id}','colcfg',null,event)`, attrs: { "aria-haspopup": "menu" } })}${DS.button({ icon: this.state.density === "comfortable" ? "bars-3" : "queue-list", size: "sm", variant: "ghost", ariaLabel: this.state.density === "comfortable" ? "Compact rows" : "Comfortable rows", title: this.state.density === "comfortable" ? "Compact rows" : "Comfortable rows", onclick: `DS.tableEv('${this.id}','density',null,event)` })}</div>`;
    }
    bulkbar() {
      if (!this.o.selectable || !this.selected.size) return "";
      const n = this.selected.size;
      return `<div class="ds-bulkbar" role="region" aria-label="Bulk actions"><b>${n} selected</b>${(this.o.bulk || []).map((b, i) => DS.button(Object.assign({ size: "sm" }, b, { onclick: `DS.tableEv('${this.id}','bulk','${i}',event)` }))).join("")}${DS.button({ label: "Clear", size: "sm", variant: "ghost", icon: "x-mark", onclick: `DS.tableEv('${this.id}','clear',null,event)` })}</div>`;
    }
    render() {
      const pins = this.pins(); const style = `--ds-cols:${this.template()}`;
      let body;
      if (this.o.loading) body = Array.from({ length: this.o.loadingRows || 6 }, () => `<div class="ds-tr" aria-hidden="true">${this.lead().map(() => `<div class="ds-td"></div>`).join("")}${this.visible().map((c) => `<div${attrs(this.cellAttrs(c, 0, pins, false))}><span class="ds-skeleton" style="width:${40 + ((c.key.length * 13) % 50)}%"></span></div>`).join("")}</div>`).join("");
      else if (this.o.error) body = `<div class="ds-table-state">${DS.errorState({ text: this.o.error, retry: this.o.retry ? { onclick: `DS.tableEv('${this.id}','retry',null,event)` } : null })}</div>`;
      else if (!(this.o.rows || []).length) body = `<div class="ds-table-state">${DS.empty(Object.assign({ icon: "table-cells", title: "No rows" }, this.o.empty || {}))}</div>`;
      else body = this.rows().map((r, i) => this.row(r, i, pins)).join("");
      return `<div${attrs({ class: cls("ds-table ds-root", this.state.density === "comfortable" && "ds-table-comfortable", this.o.cls), id: "dst-" + this.id, "data-table": this.id, role: "table", "aria-label": this.o.ariaLabel || this.o.label, "aria-busy": this.o.loading ? "true" : null })} style="${style}">${this.bar()}${this.head(pins)}<div class="ds-table-scroll" onscroll="DS.tableSync(this)"><div class="ds-table-body" role="rowgroup">${body}</div>${this.foot(pins)}</div>${this.bulkbar()}</div>`;
    }
    // ---- selection helpers
    allSelected() { const rows = this.o.rows || []; return rows.length > 0 && rows.every((r, i) => this.selected.has(this.key(r, i))); }
    selectedRows() { return (this.o.rows || []).filter((r, i) => this.selected.has(this.key(r, i))); }
    el() { return document.getElementById("dst-" + this.id); }
    /** Selection changes patch the DOM in place — a full re-render on every checkbox would
        drop focus, reset the scroll and cost a repaint of every row on a long list. */
    paintSelection() {
      const el = this.el(); if (!el) return;
      const rows = this.o.rows || [];
      el.querySelectorAll(".ds-tr[data-key]").forEach((tr) => {
        const on = this.selected.has(tr.dataset.key);
        tr.classList.toggle("is-selected", on);
        if (this.o.selectable) tr.setAttribute("aria-selected", on ? "true" : "false");
        const box = tr.querySelector(".ds-td-check input"); if (box) box.checked = on;
      });
      const all = el.querySelector(".ds-th-check input");
      if (all) { all.checked = this.allSelected(); all.indeterminate = this.selected.size > 0 && !this.allSelected(); }
      const bar = el.querySelector(".ds-bulkbar"); const html = this.bulkbar();
      if (bar && html) bar.outerHTML = html; else if (bar) bar.remove(); else if (html) el.insertAdjacentHTML("beforeend", html);
      if (this.o.onSelect) this.o.onSelect(Array.from(this.selected), this.selectedRows(), this);
    }
    rerender() { const el = this.el(); if (!el) return; const sc = el.querySelector(".ds-table-scroll"); const x = sc ? sc.scrollLeft : 0; const focus = document.activeElement && document.activeElement.closest && document.activeElement.closest(".ds-tr"); const fk = focus && focus.dataset.key;
      el.outerHTML = this.render(); DS.tableMount(this.id); const el2 = this.el(); const sc2 = el2 && el2.querySelector(".ds-table-scroll"); if (sc2) { sc2.scrollLeft = x; DS.tableSync(sc2); } if (fk) { const r = el2 && el2.querySelector(`.ds-tr[data-key="${CSS.escape(fk)}"]`); if (r) r.focus(); } }
  }

  // ---------------------------------------------------------------- public API
  DS.table = (o) => { const t = TABLES[o.id] ? Object.assign(TABLES[o.id], { o }) : (TABLES[o.id] = new Table(o)); if (!TABLES[o.id].state) t.load(); return t.render(); };
  DS.tableRender = (el, o) => { el = typeof el === "string" ? document.getElementById(el) : el; el.innerHTML = DS.table(o); DS.tableMount(o.id); return TABLES[o.id]; };
  DS.tableGet = (id) => TABLES[id] || null;
  /** Drop the table's own expand/collapse overrides so `expandable.open(row)` is the only
      truth again. A chevron records its choice in `open`/`closed`, and DS.table REUSES the
      instance across renders - so a page that drives expansion from its own store (Leluxe's
      ⊞/⊟, "jump to order") reset that store and then watched nothing happen, because the
      row the user had touched by hand still won. Call this whenever the page sets the open
      set wholesale. */
  DS.tableResetOpen = (id) => { const t = TABLES[id]; if (!t) return; t.open.clear(); if (t.closed) t.closed.clear(); };
  /** Expand or collapse EVERY row, whatever `expandable.open(row)` would say. A page's
      "Expand / Collapse every order" is exactly the row chevron applied to all rows at
      once, so it belongs in the same `open`/`closed` overrides the chevron writes -
      a board whose rows open from the table's own state (not from a page store) could
      not otherwise be reached, and the menu item did nothing. Returns false when the
      table has no expandable rows, so a page can hide a control that cannot act. */
  DS.tableSetAllOpen = (id, open) => {
    const t = TABLES[id];
    if (!t || !t.o.expandable) return false;
    t.open.clear(); t.closed = t.closed || new Set(); t.closed.clear();
    (t.rows() || []).forEach((r, i) => (open ? t.open : t.closed).add(t.key(r, i)));
    t.rerender();
    return true;
  };
  /** Whether a table can expand its rows at all (used to hide a dead control). */
  DS.tableExpandable = (id) => !!(TABLES[id] && TABLES[id].o.expandable);
  DS.tableSelected = (id) => (TABLES[id] ? Array.from(TABLES[id].selected) : []);
  DS.tableState = (id) => (TABLES[id] ? TABLES[id].state : null);
  DS.tableSync = (scroller) => {
    const table = scroller.closest(".ds-table"); const clip = table && table.querySelector(".ds-table-headclip");
    if (clip) clip.scrollLeft = scroller.scrollLeft;
    /* Row expansions used to be held still here, by setting transform:translateX(scrollLeft)
       on every scroll event. That paints a frame after the browser has already drawn the
       scrolled rows, so the expansion visibly lagged and jittered behind them. The old comment
       said `position: sticky` could not pin them because their containing block gave them
       "nothing to stick against" — true at the time, but the real cause was that
       `.ds-table-body` was only as wide as the scrollport while its rows were `max-content`,
       so a one-screen-wide box had ZERO travel inside its containing block. The body now has
       the rows' width and sticky holds them natively, on the compositor.
       This is the same shape the legacy board has used all along - see web/index.html's
       `.bt-wrap` rules: `width:max-content;min-width:100%` on every nesting level plus
       `position:sticky;inset-inline-start:0` on the pinned part, with a comment warning that
       an intermediate `overflow` would hijack the sticky scroll box. Exactly what
       `.ds-table`'s `overflow:hidden` was doing here. */
    scroller.classList.toggle("is-scrolled", Math.abs(scroller.scrollLeft) > 1);
    scroller.classList.toggle("has-more", Math.abs(scroller.scrollLeft) + scroller.clientWidth < scroller.scrollWidth - 1);
  };
  const _ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver((entries) => entries.forEach((e) => { const t = e.target.closest(".ds-table"); if (t) t.style.setProperty("--ds-view-w", e.target.clientWidth + "px"); DS.tableSync(e.target); })) : null;
  DS.tableMount = (id) => { const t = TABLES[id]; const el = t && t.el(); if (!el) return; const sc = el.querySelector(".ds-table-scroll"); if (sc) { el.style.setProperty("--ds-view-w", sc.clientWidth + "px"); DS.tableSync(sc); if (_ro) _ro.observe(sc); } };

  // ---------------------------------------------------------------- events
  let _drag = null, _rsz = null;
  DS.tableEv = (id, kind, key, ev) => {
    const t = TABLES[id]; if (!t) return;
    const rows = t.o.rows || []; const findRow = (k) => rows.find((r, i) => t.key(r, i) === k);
    switch (kind) {
      case "sort": { const s = t.state.sort || {}; const dir = s.key === key ? (s.dir === "asc" ? "desc" : s.dir === "desc" ? null : "asc") : "asc"; t.state.sort = dir ? { key, dir } : null; t.save(); if (t.o.onSort) t.o.onSort(dir ? key : null, dir); else t.rerender(); break; }
      case "colmenu": { ev.preventDefault(); const c = t.o.columns.find((x) => x.key === key); if (!c) return; const vis = t.visible().filter((x) => !x.pin); const i = vis.findIndex((x) => x.key === key);
        const items = [{ head: c.label }, { label: "Sort ascending", icon: "arrow-up", onclick: `DS.tableEv('${id}','sortSet',${JSON.stringify(key + "|asc")})` }, { label: "Sort descending", icon: "arrow-down", onclick: `DS.tableEv('${id}','sortSet',${JSON.stringify(key + "|desc")})` }, { divider: true },
          !c.pin && i > 0 ? { label: "Move to start", icon: "chevron-double-left", onclick: `DS.tableEv('${id}','moveTo',${JSON.stringify(key + "|start")})` } : null, !c.pin && i < vis.length - 1 ? { label: "Move to end", icon: "chevron-double-right", onclick: `DS.tableEv('${id}','moveTo',${JSON.stringify(key + "|end")})` } : null,
          { label: "Reset width", icon: "arrows-right-left", onclick: `DS.tableEv('${id}','resizeReset',${JSON.stringify(key)})` }, { divider: true }, !c.locked ? { label: "Hide column", icon: "eye-slash", onclick: `DS.tableEv('${id}','hide',${JSON.stringify(key)})` } : null].filter(Boolean);
        DS.menuOpenAt(items, ev.clientX, ev.clientY, { label: `${c.label} column menu` }); break; }
      case "sortSet": { const [k, d] = key.split("|"); t.state.sort = { key: k, dir: d }; t.save(); if (t.o.onSort) t.o.onSort(k, d); else t.rerender(); break; }
      case "moveTo": { const [k, where] = key.split("|"); const order = t.columns().map((c) => c.key).filter((x) => x !== k); const movable = order.filter((x) => !t.o.columns.find((c) => c.key === x).pin);
        if (where === "start") order.splice(order.indexOf(movable[0]), 0, k); else order.splice(order.indexOf(movable[movable.length - 1]) + 1, 0, k); t.state.order = order; t.save(); t.rerender(); break; }
      case "hide": { if (!t.state.hidden.includes(key)) t.state.hidden.push(key); t.save(); t.rerender(); break; }
      case "show": { t.state.hidden = t.state.hidden.filter((k) => k !== key); t.save(); t.rerender(); break; }
      case "toggleCol": { if (t.state.hidden.includes(key)) t.state.hidden = t.state.hidden.filter((k) => k !== key); else t.state.hidden.push(key); t.save(); t.rerender(); DS.tableEv(id, "colcfg", null, ev); break; }
      case "colcfg": { const anchor = ev && ev.currentTarget && ev.currentTarget.getBoundingClientRect ? ev.currentTarget.getBoundingClientRect() : { left: ev.clientX, bottom: ev.clientY };
        // A column with no label has nothing to say for itself - the row-actions "⋯"
        // column is the only one - and it is locked besides, so it listed as a blank
        // row with a dead switch at the bottom of the menu. Structure, not a choice.
        const cols = t.columns().filter((c) => c.label); const html = `<div class="ds-colcfg"><div class="ds-menu-head">Columns</div>${cols.map((c, i) => `<div class="ds-colcfg-row">${DS.switch({ checked: c.locked || !t.state.hidden.includes(c.key), label: c.label, disabled: !!c.locked, onchange: `DS.tableEv('${id}','toggleCol',${JSON.stringify(c.key)},event)` })}${!c.pin ? DS.button({ icon: "chevron-up", size: "sm", variant: "ghost", ariaLabel: `Move ${c.label} up`, disabled: i === 0 || !!cols[i - 1].pin, onclick: `DS.tableEv('${id}','moveUp',${JSON.stringify(c.key)},event)` }) + DS.button({ icon: "chevron-down", size: "sm", variant: "ghost", ariaLabel: `Move ${c.label} down`, disabled: i === cols.length - 1 || !!cols[i + 1].pin, onclick: `DS.tableEv('${id}','moveDown',${JSON.stringify(c.key)},event)` }) : ""}</div>`).join("")}<div class="ds-colcfg-foot">${DS.button({ label: "Reset layout", size: "sm", icon: "arrow-uturn-left", onclick: `DS.tableEv('${id}','reset',null,event)` })}</div></div>`;
        DS.menuClose(); const host = DS.menuOpenAt([], anchor.left, anchor.bottom + 4, { label: "Column settings" });
        host.querySelector(".ds-menu-list").innerHTML = html;
        DS.menuPlace();   // it was placed while empty; it has a size now
        break; }
      case "moveUp": case "moveDown": { const order = t.columns().map((c) => c.key); const i = order.indexOf(key); const j = kind === "moveUp" ? i - 1 : i + 1; if (j < 0 || j >= order.length) return; [order[i], order[j]] = [order[j], order[i]]; t.state.order = order; t.save(); t.rerender(); DS.tableEv(id, "colcfg", null, ev); break; }
      case "reset": { t.reset(); DS.menuClose(); t.rerender(); break; }
      case "density": { t.state.density = t.state.density === "comfortable" ? "compact" : "comfortable"; t.save(); t.rerender(); break; }
      case "resize": { ev.preventDefault(); const c = t.o.columns.find((x) => x.key === key); const rtl = getComputedStyle(t.el()).direction === "rtl"; _rsz = { t, c, x: ev.clientX, w: t.width(c), rtl }; t.el().classList.add("is-resizing");
        const move = (e) => { const d = (e.clientX - _rsz.x) * (_rsz.rtl ? -1 : 1); _rsz.t.state.w[_rsz.c.key] = Math.max(_rsz.c.min || 48, Math.round(_rsz.w + d)); _rsz.t.el().style.setProperty("--ds-cols", _rsz.t.template()); };
        const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); _rsz.t.el().classList.remove("is-resizing"); _rsz.t.save(); _rsz.t.rerender(); _rsz = null; };
        window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); break; }
      case "resizeReset": { delete t.state.w[key]; t.save(); t.rerender(); break; }
      case "dragstart": { _drag = { id, key }; ev.dataTransfer.effectAllowed = "move"; try { ev.dataTransfer.setData("text/plain", key); } catch (e) { /* ie */ } break; }
      case "dragover": { if (!_drag || _drag.id !== id || _drag.key === key) return; ev.preventDefault(); const el = ev.currentTarget; const r = el.getBoundingClientRect(); const after = ev.clientX > r.left + r.width / 2; el.classList.toggle("is-dragover-end", after); el.classList.toggle("is-dragover-start", !after); break; }
      case "dragleave": { ev.currentTarget.classList.remove("is-dragover-end", "is-dragover-start"); break; }
      case "drop": { ev.preventDefault(); const el = ev.currentTarget; const after = el.classList.contains("is-dragover-end"); el.classList.remove("is-dragover-end", "is-dragover-start"); if (!_drag || _drag.id !== id || _drag.key === key) return;
        const target = t.o.columns.find((x) => x.key === key), src = t.o.columns.find((x) => x.key === _drag.key); if (!target || !src || target.pin !== src.pin) { _drag = null; return; }
        const order = t.columns().map((c) => c.key).filter((k) => k !== _drag.key); order.splice(order.indexOf(key) + (after ? 1 : 0), 0, _drag.key); t.state.order = order; t.save(); _drag = null; t.rerender(); break; }
      case "dragend": { _drag = null; t.el().querySelectorAll(".is-dragover-end,.is-dragover-start").forEach((x) => x.classList.remove("is-dragover-end", "is-dragover-start")); break; }
      case "select": { if (ev.target.checked) t.selected.add(key); else t.selected.delete(key); t.paintSelection(); break; }
      case "selectAll": { if (ev.target.checked) rows.forEach((r, i) => t.selected.add(t.key(r, i))); else t.selected.clear(); t.paintSelection(); break; }
      case "clear": { t.selected.clear(); t.paintSelection(); break; }
      // a bulk action's onclick may be a function OR a string of JS, because every
      // other onclick in this design system is a string - passing one here used to
      // throw "b.onclick is not a function" the moment the button was clicked
      case "bulk": { const b = (t.o.bulk || [])[+key]; if (!b || !b.onclick) break;
        if (typeof b.onclick === "function") b.onclick(Array.from(t.selected), t.selectedRows(), t);
        else new Function("keys", "rows", "table", b.onclick)(Array.from(t.selected), t.selectedRows(), t);
        break; }
      case "toggle": { ev.stopPropagation(); if (t.open.has(key)) { t.open.delete(key); (t.closed = t.closed || new Set()).add(key); } else { t.open.add(key); if (t.closed) t.closed.delete(key); } t.rerender(); if (t.o.onToggle) t.o.onToggle(key, t.open.has(key)); break; }
      case "rowclick": {
        // The 15px box was the only target in a 42px check cell, and a click beside it
        // opened the row instead. The whole cell toggles the row now.
        const chk = ev.target.closest(".ds-td-check");
        if (chk && ev.target.tagName !== "INPUT") { const box = chk.querySelector("input"); if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change", { bubbles: true })); } return; }
        if (ev.target.closest("button,a,input,select,textarea,label,.ds-menu,[data-nostop]")) return; if (t.o.onRowClick) t.o.onRowClick(findRow(key), ev, t); break; }
      case "allcell": { if (ev.target.tagName === "INPUT") return; const box = ev.currentTarget.querySelector("input"); if (box) { box.checked = !box.checked; box.dispatchEvent(new Event("change", { bubbles: true })); } break; }
      case "rowkey": { const el = ev.currentTarget; const list = Array.from(t.el().querySelectorAll(".ds-tr[data-key]")); const i = list.indexOf(el); let j = null;
        if (ev.key === "ArrowDown") j = Math.min(i + 1, list.length - 1); else if (ev.key === "ArrowUp") j = Math.max(i - 1, 0); else if (ev.key === "Home") j = 0; else if (ev.key === "End") j = list.length - 1;
        else if (ev.key === "Enter" && t.o.onRowClick && !ev.target.closest("button,a,input,select")) { ev.preventDefault(); t.o.onRowClick(findRow(key), ev, t); return; }
        else if (ev.key === " " && t.o.selectable && ev.target === el) { ev.preventDefault(); if (t.selected.has(key)) t.selected.delete(key); else t.selected.add(key); t.paintSelection(); return; }
        else if ((ev.key === "ArrowRight" || ev.key === "ArrowLeft") && t.o.expandable && ev.target === el) { ev.preventDefault(); const open = t.open.has(key); if (ev.key === "ArrowRight" ? !open : open) DS.tableEv(id, "toggle", key, ev); return; }
        if (j == null) return; ev.preventDefault(); list.forEach((x) => (x.tabIndex = -1)); list[j].tabIndex = 0; list[j].focus(); break; }
      case "prev": { if (t.o.page && t.o.page.onPrev) t.o.page.onPrev(); break; }
      case "next": { if (t.o.page && t.o.page.onNext) t.o.page.onNext(); break; }
      case "retry": { if (t.o.retry) t.o.retry(); break; }
    }
  };
})();
