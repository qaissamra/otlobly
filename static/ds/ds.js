/* ============================================================================
   Otlobly design system — primitives, overlays, composites (static/ds/ds.js)
   Plain script, no build step. Every helper returns an HTML string in the
   app's own idiom (string templates + inline handlers); a few controllers
   (menus, dialogs, toasts, tabs, drop zones) are functions on window.DS.
   Depends on: tokens.css, ds.css, status.js (DS.status), format.js (DS.fmt),
   icons.svg. The DataTable lives in table.js. Catalogue: /design-system.
   ========================================================================== */
(function () {
  "use strict";
  const DS = (window.DS = window.DS || {});
  const ICON_BASE = "/static/ds/icons.svg#i-";
  const DIR_ICONS = new Set(["arrow-right", "arrow-left", "chevron-right", "chevron-left", "chevron-double-left", "chevron-double-right",
    "arrow-uturn-left", "arrow-top-right-on-square", "arrow-right-circle", "arrow-trending-up", "arrow-trending-down", "arrows-right-left"]);

  // ---------------------------------------------------------------- utilities
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const attrs = (o) => Object.entries(o || {}).filter(([, v]) => v !== undefined && v !== null && v !== false && v !== "")
    .map(([k, v]) => (v === true ? ` ${k}` : ` ${k}="${esc(v)}"`)).join("");
  const cls = (...a) => a.filter(Boolean).join(" ");
  let _uid = 0;
  const uid = (p) => `${p || "ds"}-${(++_uid).toString(36)}`;
  const $ = (id) => (typeof id === "string" ? document.getElementById(id) : id);
  const inkFor = (hex) => { // luminance-picked text colour for solid badges
    const h = String(hex || "").replace("#", ""); if (h.length < 6) return "#fff";
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.62 ? "#1f2937" : "#fff";
  };
  DS.esc = esc; DS.attrs = attrs; DS.cls = cls; DS.uid = uid; DS.inkFor = inkFor;
  DS.dash = () => `<span class="ds-dash">—</span>`;          // the empty value

  // ---------------------------------------------------------------- icon
  /** DS.icon("truck") · DS.icon("plus",{size:"nav"|"lg"|"xl"|14, cls, label}) — label makes it an accessible image. */
  DS.icon = (name, o) => {
    o = o || {};
    const size = o.size === "nav" ? "ds-icon-nav" : o.size === "lg" ? "ds-icon-lg" : o.size === "xl" ? "ds-icon-xl" : "";
    const style = typeof o.size === "number" ? ` style="width:${o.size}px;height:${o.size}px"` : "";
    const a11y = o.label ? ` role="img" aria-label="${esc(o.label)}"` : ` aria-hidden="true"`;
    return `<svg class="${cls("ds-icon", size, DIR_ICONS.has(name) && "ds-icon-dir", o.cls)}"${style}${a11y} focusable="false"><use href="${ICON_BASE}${esc(name)}"></use></svg>`;
  };

  // ---------------------------------------------------------------- button
  /** DS.button({label, variant:primary|secondary|ghost|danger, size:sm|md, icon, iconEnd, iconOnly, loading, disabled, onclick, href, title, tip, ariaLabel, type, cls, attrs}) */
  DS.button = (o) => {
    o = o || {};
    const v = o.variant || "secondary";
    const iconOnly = !!o.iconOnly || (!o.label && !!o.icon);
    const aria = o.ariaLabel || (iconOnly ? (o.title || o.tip || o.label) : null);
    if (iconOnly && !aria && window.console) console.warn("DS.button: icon-only button without ariaLabel/title", o);
    const c = cls("ds-btn", v !== "secondary" && `ds-btn-${v}`, o.solid && "ds-btn-solid", o.size === "sm" && "ds-btn-sm", iconOnly && "ds-btn-icon",
      o.loading && "is-loading", o.tip && "ds-tip", o.cls);
    const inner = `${o.icon ? DS.icon(o.icon) : ""}${o.label && !iconOnly ? `<span>${esc(o.label)}</span>` : ""}${o.iconEnd ? DS.icon(o.iconEnd) : ""}`;
    const base = { class: c, title: o.title, "data-tip": o.tip, "aria-label": aria, "aria-busy": o.loading ? "true" : null, id: o.id };
    if (o.href) return `<a${attrs(Object.assign(base, { href: o.href, target: o.target, rel: o.target === "_blank" ? "noopener" : null, "aria-disabled": o.disabled ? "true" : null }, o.attrs))}>${inner}</a>`;
    return `<button${attrs(Object.assign(base, { type: o.type || "button", onclick: o.onclick, disabled: !!o.disabled }, o.attrs))}>${inner}</button>`;
  };
  DS.buttonGroup = (buttons) => `<span class="ds-btn-group">${(buttons || []).map((b) => (typeof b === "string" ? b : DS.button(b))).join("")}</span>`;

  // ---------------------------------------------------------------- badge · attention · tag
  /** DS.badge({label, tone, hex, solid, dot, icon, size:"sm", title, tip}) — tone from tokens; hex keeps a ClickUp colour. */
  DS.badge = (o) => {
    o = o || {};
    const hex = o.hex && /^#[0-9a-f]{6}$/i.test(o.hex) ? o.hex : null;
    const c = cls("ds-badge", hex ? (o.solid ? "ds-badge-solid" : "ds-badge-hex") : `ds-tone-${o.tone || "neutral"}`, o.size === "sm" && "ds-badge-sm", o.tip && "ds-tip", o.cls);
    const style = hex ? ` style="--hex:${hex}${o.solid ? `;--hex-ink:${inkFor(hex)}` : ""}"` : "";
    return `<span${attrs({ class: c, title: o.title, "data-tip": o.tip, id: o.id })}${style}>${o.dot ? `<i class="ds-dot"></i>` : ""}${o.icon ? DS.icon(o.icon, { size: 12 }) : ""}<span>${esc(o.label)}</span></span>`;
  };
  /** DS.attention({kind:late|no_tracking|missing_name|unpaid|missing_docs|no_reply|stale|missing_id|conflict, detail:"40 d", action:{label,onclick}, title}) */
  DS.attention = (o) => {
    o = o || {};
    const e = (DS.status && DS.status.get("attention", o.kind)) || { label: o.kind || "Attention", tone: "warning" };
    const tone = o.tone || e.tone;
    const icon = tone === "danger" ? "exclamation-triangle" : tone === "neutral" ? "clock" : "exclamation-circle";
    const label = `${o.label || e.label}${o.detail ? ` · ${o.detail}` : ""}`;
    const act = o.action ? `<button type="button" class="ds-attn-action" onclick="${esc(o.action.onclick || "")}">${esc(o.action.label)}</button>` : "";
    return `<span${attrs({ class: cls("ds-attn", `ds-tone-${tone}`, o.cls), title: o.title })}>${DS.icon(icon)}<span>${esc(label)}</span>${act}</span>`;
  };
  DS.tag = (o) => {
    o = o || {};
    const x = o.onRemove ? `<button type="button" class="ds-tag-x" aria-label="Remove ${esc(o.label)}" onclick="${esc(o.onRemove)}">${DS.icon("x-mark", { size: 12 })}</button>` : "";
    return `<span${attrs({ class: cls("ds-tag", o.tone && `ds-tone-${o.tone}`, o.cls), title: o.title })}>${o.icon ? DS.icon(o.icon, { size: 12 }) : ""}<span>${esc(o.label)}</span>${x}</span>`;
  };
  /** One product photo. `alt=""` on purpose: the title is already in the tooltip and
      in the text next to it, so a screen reader must not read the product twice. */
  DS.thumb = (it, alt) => {
    it = it || {};
    const tip = alt || it.title || it.asin || "";
    return it.image
      ? `<img class="ds-pu-thumb" src="${esc(it.image)}" alt="" title="${esc(tip)}" loading="lazy">`
      : `<span class="ds-pu-thumb" title="${esc(tip)}"></span>`;
  };
  /** A row of product photos that says how many there are without spelling it out
      twice. With no photos at all, a wall of empty grey squares says nothing that
      the count does not - so the count goes alone.
      Lived twice (fulfillment.js, and nearly a third time in sales.js) before it
      moved here; every board that lists products must render them identically. */
  DS.thumbs = (items, o) => {
    items = items || []; o = o || {};
    const n = items.length;
    if (!n) return DS.dash();
    const num = (v) => (DS.fmt ? DS.fmt.number(v) : String(v));
    const word = o.word || "product";
    const names = items.map((it) => it.title || it.asin || "").filter(Boolean).join(" · ");
    if (!items.some((it) => it.image))
      return `<span class="ds-muted" title="${esc(names)}">${esc(num(n))} ${esc(n === 1 ? word : word + "s")}</span>`;
    const max = o.max || 5, more = n - max;
    return `<span class="ds-fl-thumbs" title="${esc(names)}">${items.slice(0, max).map((it) => DS.thumb(it)).join("")}`
      + (more > 0 ? `<span class="ds-fl-more">+${esc(num(more))}</span>` : "")
      + `<span class="ds-muted">${esc(num(n))}</span></span>`;
  };
  /** The stateless aligned grid a row expansion opens into. Purchases introduced it,
      fulfillment copied it, and sales.js hand-rolled a near-miss - `.ds-pu-subrow`
      instead of `.ds-pu-sub-row`, and no `.ds-pu-td` at all - so the Orders expander
      rendered with no padding, no borders and no overflow control. One builder now.
      cols: [{ label, w, align:"end", render(row) }]
      A row carrying `_click` becomes clickable, with the same guard the boards use so a
      select, a menu or a button inside the row still wins. Batch F1 moved the Leluxe
      products into this grid and set `_click` on every row, but nothing ever read it -
      the rows kept their hover highlight and stopped opening anything. */
  DS.subTable = (cols, rows, label) => {
    // A width-less column used to be `1fr`, so it swallowed every spare pixel: a product
    // title got 956px of a 1062px panel and left Qty stranded at the far edge. It now grows
    // to a comfortable reading width and stops. Fixed-width columns are untouched, so a
    // wide sub-table (the parcel table on Orders) keeps its shape.
    const tpl = cols.map((c) => (c.w ? c.w + "px" : "minmax(0,var(--ds-pu-flex,620px))")).join(" ");
    return `<div class="ds-pu-sub" role="table" style="--ds-pu-cols:${tpl}"${label ? ` aria-label="${esc(label)}"` : ""}>`
      + `<div class="ds-pu-sub-head" role="row">${cols.map((c) => `<div class="ds-pu-th${c.align === "end" ? " ds-num" : ""}" role="columnheader">${esc(c.label || "")}</div>`).join("")}</div>`
      + rows.map((r) => {
        const click = r && r._click;
        // No tabindex: the row is `display:contents`, so it generates no box and is not
        // reliably focusable. The row's own ⋯ menu carries the same actions for the keyboard.
        const open = click ? ` class="ds-pu-sub-row is-clickable" role="row"`
          + ` onclick="if(event.target.closest('select,.pop,.caret,button,a,input,label,img'))return;${esc(click)}"`
          : ` class="ds-pu-sub-row" role="row"`;
        return `<div${open}>${cols.map((c) => `<div class="ds-pu-td${c.align === "end" ? " ds-num" : ""}" role="cell">${c.render(r) || ""}</div>`).join("")}</div>`;
      }).join("")
      + `</div>`;
  };
  DS.kbd = (k) => `<kbd>${esc(k)}</kbd>`;
  DS.tipWrap = (inner, text) => `<span class="ds-tip" tabindex="0" data-tip="${esc(text)}">${inner}</span>`;

  // ---------------------------------------------------------------- inputs
  const inputBase = (o, extra) => Object.assign({ id: o.id, name: o.name, placeholder: o.placeholder, value: o.value, disabled: !!o.disabled, readonly: !!o.readonly,
    required: !!o.required, autocomplete: o.autocomplete, inputmode: o.inputmode, maxlength: o.maxlength, min: o.min, max: o.max, step: o.step, pattern: o.pattern,
    "aria-label": o.ariaLabel, "aria-invalid": o.invalid ? "true" : null, dir: o.dirAuto ? "auto" : o.dir, oninput: o.oninput, onchange: o.onchange, onkeydown: o.onkeydown, list: o.list,
    "data-ds-search": o.search ? "1" : null }, extra, o.attrs);
  /** DS.input({id,name,value,type,placeholder,size:"sm",invalid,disabled,readonly,mono,num,dirAuto,icon,affix,search,oninput,onchange,attrs}) */
  DS.input = (o) => {
    o = o || {};
    const c = cls("ds-input", o.size === "sm" && "ds-input-sm", o.mono && "ds-input-mono", o.num && "ds-input-num", o.invalid && "is-invalid", o.cls);
    const el = `<input${attrs(inputBase(o, { type: o.type || "text", class: c }))}>`;
    if (!o.icon && !o.affix) return el;
    return `<span class="ds-input-wrap">${o.icon ? DS.icon(o.icon) : ""}${el}${o.affix ? `<span class="ds-affix">${esc(o.affix)}</span>` : ""}</span>`;
  };
  DS.numberInput = (o) => DS.input(Object.assign({ type: "number", inputmode: "decimal", num: true }, o || {}));
  DS.datePicker = (o) => DS.input(Object.assign({ type: "date" }, o || {}, { value: o && o.value && DS.fmt ? DS.fmt.iso(o.value) : (o || {}).value }));
  DS.search = (o) => DS.input(Object.assign({ type: "search", icon: "magnifying-glass", search: true, placeholder: "Search", cls: "ds-search" }, o || {}));
  /** DS.select({id,name,value,options:[{value,label,disabled}|"str"],placeholder,size,invalid,onchange}) */
  DS.select = (o) => {
    o = o || {};
    const opts = (o.options || []).map((x) => (typeof x === "object" ? x : { value: x, label: x }));
    const cur = o.value == null ? "" : String(o.value);
    const ph = o.placeholder != null ? `<option value=""${cur === "" ? " selected" : ""}${o.required ? " disabled" : ""}>${esc(o.placeholder)}</option>` : "";
    const c = cls("ds-select", o.size === "sm" && "ds-select-sm", o.invalid && "is-invalid", o.cls);
    return `<select${attrs(inputBase(o, { class: c, value: undefined, placeholder: undefined }))}>${ph}${opts.map((x) =>
      `<option value="${esc(x.value)}"${String(x.value) === cur ? " selected" : ""}${x.disabled ? " disabled" : ""}>${esc(x.label)}</option>`).join("")}</select>`;
  };
  DS.textarea = (o) => { o = o || {}; return `<textarea${attrs(inputBase(o, { class: cls("ds-textarea", o.invalid && "is-invalid", o.cls), rows: o.rows || 4, value: undefined }))}>${esc(o.value)}</textarea>`; };
  DS.checkbox = (o) => { o = o || {}; return `<label${attrs({ class: cls("ds-check", o.cls), title: o.title })}><input${attrs({ type: "checkbox", id: o.id, name: o.name, value: o.value, checked: !!o.checked, disabled: !!o.disabled, onchange: o.onchange, "aria-label": o.ariaLabel })}>${o.label != null ? `<span>${esc(o.label)}</span>` : ""}</label>`; };
  /** DS.switch({id,checked,label,onchange,disabled}) — a real role="switch" button; onchange runs with `this` = the switch (aria-checked already flipped). */
  DS.switch = (o) => { o = o || {}; return `<button${attrs({ type: "button", role: "switch", id: o.id, class: cls("ds-switch", o.cls), "aria-checked": o.checked ? "true" : "false", disabled: !!o.disabled,
    "aria-label": o.label ? null : (o.ariaLabel || "Toggle"), onclick: `DS.switchToggle(this);${o.onchange || ""}`, title: o.title })}><span class="ds-switch-track"></span>${o.label != null ? `<span>${esc(o.label)}</span>` : ""}</button>`; };
  DS.switchToggle = (btn) => { if (btn.disabled) return false; const on = btn.getAttribute("aria-checked") !== "true"; btn.setAttribute("aria-checked", on ? "true" : "false"); return on; };
  DS.switchOn = (btn) => btn.getAttribute("aria-checked") === "true";
  /** Native datalist combobox for Phase 1 (type-to-search over known values). */
  DS.combobox = (o) => { o = o || {}; const id = o.listId || uid("dl"); const opts = (o.options || []).map((x) => (typeof x === "object" ? x : { value: x }));
    return DS.input(Object.assign({}, o, { list: id, autocomplete: "off" })) + `<datalist id="${id}">${opts.map((x) => `<option value="${esc(x.value)}">${x.label ? esc(x.label) : ""}</option>`).join("")}</datalist>`; };

  // ---------------------------------------------------------------- field · form
  /** DS.field({id,label,required,help,error,input,cls}) — label above, help below, error below (shown when .is-invalid). */
  DS.field = (o) => { o = o || {}; return `<div${attrs({ class: cls("ds-field", o.invalid && "is-invalid", o.cls), "data-field": o.id })}>${o.label != null ? `<label class="ds-label" for="${esc(o.id)}">${esc(o.label)}${o.required ? ` <span class="ds-req" aria-hidden="true">*</span>` : ""}</label>` : ""}${o.input || ""}${o.help ? `<div class="ds-help">${esc(o.help)}</div>` : ""}<div class="ds-error" role="alert">${esc(o.error)}</div></div>`; };
  DS.formRow = (cells, o) => `<div class="${cls("ds-form-row", (o && o.cols) === 3 && "ds-cols-3")}">${cells.join("")}</div>`;
  DS.formSection = (o) => { o = o || {}; return `<section class="ds-form-section">${o.title ? `<h3>${esc(o.title)}</h3>` : ""}${o.hint ? `<div class="ds-help">${esc(o.hint)}</div>` : ""}${(o.rows || []).map((r) => (Array.isArray(r) ? DS.formRow(r) : r)).join("")}</section>`; };
  /** DS.form({id,sections:[...],footer:{primary,cancel,destructive},onsubmit}) — validates on submit, focuses the first error, then runs onsubmit. */
  DS.form = (o) => {
    o = o || {};
    const f = o.footer || {};
    const foot = `<div class="ds-form-footer">${f.destructive ? DS.button(Object.assign({ variant: "danger" }, f.destructive)) : ""}<span class="ds-spacer"></span>${f.cancel ? DS.button(Object.assign({ label: "Cancel", variant: "ghost" }, f.cancel)) : ""}${f.primary ? DS.button(Object.assign({ variant: "primary", type: "submit" }, f.primary)) : ""}</div>`;
    return `<form${attrs(Object.assign({ id: o.id, class: cls("ds-form", o.cls), novalidate: true, onsubmit: `if(!DS.formValidate(this)){event.preventDefault();return false;} ${o.onsubmit || "event.preventDefault();"}` }, o.attrs))}>${(o.sections || []).map((s) => (typeof s === "string" ? s : DS.formSection(s))).join("")}${foot}</form>`;
  };
  /** Marks every .ds-field whose control fails native validity (or has data-error) and focuses the first. Returns true when valid. */
  DS.formValidate = (form) => {
    let first = null;
    form.querySelectorAll(".ds-field").forEach((fld) => {
      const ctl = fld.querySelector("input,select,textarea");
      const err = fld.querySelector(".ds-error");
      const custom = fld.dataset.error || "";
      const bad = custom || (ctl && !ctl.disabled && !ctl.checkValidity());
      fld.classList.toggle("is-invalid", !!bad);
      if (ctl) ctl.setAttribute("aria-invalid", bad ? "true" : "false");
      if (bad) { if (err) err.textContent = custom || (ctl && ctl.validationMessage) || "Please check this field"; if (!first) first = ctl || fld; }
    });
    if (first) { first.focus(); return false; }
    return true;
  };
  DS.fieldError = (id, msg) => { const fld = document.querySelector(`.ds-field[data-field="${CSS.escape(id)}"]`); if (!fld) return; if (msg) fld.dataset.error = msg; else delete fld.dataset.error; fld.classList.toggle("is-invalid", !!msg); const e = fld.querySelector(".ds-error"); if (e) e.textContent = msg || ""; };

  // ---------------------------------------------------------------- dropdown menu
  /** DS.menu({id, button:{...DS.button opts} | trigger:html, items:[{label,icon,onclick,danger,disabled,kbd}|{divider:true}|{head:"…"}], align:"end"|"start"}) */
  DS.menuItems = (items) => (items || []).map((it) => it.divider ? `<div class="ds-menu-sep" role="separator"></div>` : it.head ? `<div class="ds-menu-head">${esc(it.head)}</div>` :
    `<button${attrs({ type: "button", role: "menuitem", class: cls("ds-menu-item", it.danger && "ds-danger", it.cls), disabled: !!it.disabled, onclick: `DS.menuClose();${it.onclick || ""}`, title: it.title })}>${it.icon ? DS.icon(it.icon) : ""}<span>${esc(it.label)}</span>${it.kbd ? DS.kbd(it.kbd) : ""}</button>`).join("");
  DS.menu = (o) => {
    o = o || {}; const id = o.id || uid("menu");
    const btn = o.trigger || DS.button(Object.assign({ icon: "ellipsis-horizontal", ariaLabel: "More actions", title: "More actions" }, o.button || {}, { attrs: Object.assign({ "aria-haspopup": "menu", "aria-expanded": "false", "aria-controls": id + "-list" }, (o.button || {}).attrs), onclick: `DS.menuToggle(this,event)` }));
    return `<span class="ds-menu" id="${id}" data-align="${o.align || "end"}">${btn}<div class="ds-menu-list" id="${id}-list" role="menu" aria-labelledby="${id}">${DS.menuItems(o.items)}</div></span>`;
  };
  let _openMenu = null, _menuBound = false;
  const menuItemsOf = (list) => Array.from(list.querySelectorAll('[role="menuitem"]:not([disabled])'));
  DS.menuClose = () => { if (!_openMenu) return; const { list, btn } = _openMenu; list.dataset.open = ""; list.style.top = list.style.left = ""; if (btn) btn.setAttribute("aria-expanded", "false"); _openMenu = null;
    const host = list.closest(".ds-menu"); if (host && host.dataset.transient === "1") setTimeout(() => host.remove(), 0); };
  /** DS.menuOpenAt(items, x, y) — a transient menu anchored at viewport coordinates (context menus, column headers). */
  DS.menuOpenAt = (items, x, y, o) => {
    o = o || {}; const id = uid("ctx");
    document.body.insertAdjacentHTML("beforeend", `<span class="ds-menu" id="${id}" data-transient="1" data-align="${o.align || "start"}" style="position:fixed;left:${Math.round(x)}px;top:${Math.round(y)}px;width:0;height:0"><button type="button" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(o.label || "Context menu")}" style="opacity:0;width:0;height:0;border:0;padding:0;margin:0;position:absolute"></button><div class="ds-menu-list" role="menu">${DS.menuItems(items)}</div></span>`);
    const host = $(id); DS.menuToggle(host.querySelector("button"), null); return host;
  };
  DS.menuToggle = (btn, ev) => {
    if (ev) { ev.stopPropagation(); ev.preventDefault(); }
    const host = btn.closest(".ds-menu"); const list = host && host.querySelector(".ds-menu-list");
    if (!list) return;
    if (_openMenu && _openMenu.list === list) { DS.menuClose(); btn.focus(); return; }
    DS.menuClose();
    list.dataset.open = "1"; btn.setAttribute("aria-expanded", "true"); _openMenu = { list, btn };
    const r = btn.getBoundingClientRect(); const w = list.offsetWidth, h = list.offsetHeight; const align = host.dataset.align || "end";
    const rtl = getComputedStyle(host).direction === "rtl";
    let left = (align === "end") !== rtl ? r.right - w : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    let top = r.bottom + 4; if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    list.style.left = left + "px"; list.style.top = top + "px";
    const items = menuItemsOf(list); if (items[0]) items[0].focus();
    if (!_menuBound) {
      _menuBound = true;
      document.addEventListener("mousedown", (e) => { if (_openMenu && !_openMenu.list.contains(e.target) && !_openMenu.btn.contains(e.target)) DS.menuClose(); }, true);
      document.addEventListener("keydown", (e) => {
        if (!_openMenu) return;
        const items = menuItemsOf(_openMenu.list); const i = items.indexOf(document.activeElement);
        if (e.key === "Escape") { const b = _openMenu.btn; DS.menuClose(); b.focus(); e.preventDefault(); }
        else if (e.key === "ArrowDown") { (items[(i + 1) % items.length] || items[0]).focus(); e.preventDefault(); }
        else if (e.key === "ArrowUp") { (items[(i - 1 + items.length) % items.length] || items[0]).focus(); e.preventDefault(); }
        else if (e.key === "Home") { items[0] && items[0].focus(); e.preventDefault(); }
        else if (e.key === "End") { items[items.length - 1] && items[items.length - 1].focus(); e.preventDefault(); }
        else if (e.key === "Tab") DS.menuClose();
      });
      window.addEventListener("scroll", () => DS.menuClose(), true);
      window.addEventListener("resize", () => DS.menuClose());
    }
  };

  // ---------------------------------------------------------------- tabs
  /** DS.tabs({id, items:[{key,label,count,icon}], active, onchange:"gmTab(KEY)", variant:"line"|"pills", ariaLabel}) — KEY is replaced by the JSON key. */
  DS.tabs = (o) => {
    o = o || {}; const id = o.id || uid("tabs");
    return `<div${attrs({ class: cls("ds-tabs", o.variant === "pills" && "ds-tabs-pills", o.cls), role: "tablist", id, "aria-label": o.ariaLabel })}>${(o.items || []).map((t) => {
      const sel = t.key === o.active;
      // Substitute the key RAW. `attrs()` runs the finished handler through esc() once,
      // which is what turns the quotes into &quot; for the attribute - and the HTML parser
      // turns them back into quotes before compiling the handler. Pre-escaping here escaped
      // the & a second time, so every tab shipped `foo(&quot;bar&quot;)` as its JS source and
      // threw `Unexpected token '&'` on click. Dead since Phase 1 in every tab strip and
      // view pill in the app; only ever noticed once a page lost its legacy strip.
      const on = (o.onchange || "").replace(/KEY/g, JSON.stringify(t.key));
      return `<button${attrs({ type: "button", role: "tab", class: "ds-tab", "aria-selected": sel ? "true" : "false", tabindex: sel ? "0" : "-1", "data-key": t.key, id: `${id}-${t.key}`, onclick: `DS.tabSelect(this);${on}`, onkeydown: "DS.tabsKey(event)", title: t.title })}>${t.icon ? DS.icon(t.icon) : ""}<span>${esc(t.label)}</span>${t.count != null ? `<span class="ds-count">${esc(t.count)}</span>` : ""}</button>`;
    }).join("")}</div>`;
  };
  DS.tabSelect = (btn) => { const list = btn.closest('[role="tablist"]'); if (!list) return; list.querySelectorAll('[role="tab"]').forEach((t) => { const on = t === btn; t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1; }); };
  DS.tabsKey = (e) => {
    const list = e.currentTarget.closest('[role="tablist"]'); const tabs = Array.from(list.querySelectorAll('[role="tab"]')); const i = tabs.indexOf(e.currentTarget);
    const rtl = getComputedStyle(list).direction === "rtl"; let j = null;
    if (e.key === (rtl ? "ArrowLeft" : "ArrowRight")) j = (i + 1) % tabs.length; else if (e.key === (rtl ? "ArrowRight" : "ArrowLeft")) j = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") j = 0; else if (e.key === "End") j = tabs.length - 1;
    if (j == null) return; e.preventDefault(); tabs[j].focus(); tabs[j].click();
  };

  // ---------------------------------------------------------------- avatar · skeleton · stat · kpi · empty · feed
  DS.avatar = (o) => { o = o || {}; const init = String(o.name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    return `<span${attrs({ class: cls("ds-avatar", o.size === "lg" && "ds-avatar-lg", o.cls), title: o.name, "aria-label": o.name, role: "img" })}>${o.src ? `<img src="${esc(o.src)}" alt="">` : esc(init)}</span>`; };
  DS.skeleton = (o) => { o = o || {}; const n = o.rows || 3; const ws = o.widths || ["100%", "80%", "60%"]; return `<div class="ds-skeletons" aria-busy="true" aria-label="Loading">${Array.from({ length: n }, (_, i) => `<span class="ds-skeleton" style="width:${ws[i % ws.length]};margin:6px 0"></span>`).join("")}</div>`; };
  DS.stat = (o) => { o = o || {}; return `<div${attrs({ class: cls("ds-stat", o.cls), title: o.title })}><span class="ds-stat-label">${esc(o.label)}</span><span class="${cls("ds-stat-value", o.mono && "ds-mono")}"${o.tone ? ` style="color:var(--ds-${o.tone}-ink)"` : ""}>${o.html || esc(o.value)}</span>${o.hint ? `<span class="ds-stat-hint">${esc(o.hint)}</span>` : ""}</div>`; };
  DS.kpis = (stats) => `<div class="ds-kpis">${(stats || []).map((s) => `<div class="ds-kpi">${DS.stat(s)}</div>`).join("")}</div>`;
  DS.empty = (o) => { o = o || {}; return `<div${attrs({ class: cls("ds-empty", o.cls), id: o.id })}>${DS.icon(o.icon || "inbox", { size: "xl" })}<div class="ds-empty-title">${esc(o.title || "Nothing here yet")}</div>${o.text || o.hint ? `<div class="ds-empty-text">${esc(o.text || o.hint)}</div>` : ""}${o.action ? DS.button(Object.assign({ variant: "primary" }, o.action)) : ""}</div>`; };
  DS.errorState = (o) => { o = o || {}; return `<div class="ds-error-state" role="alert">${DS.icon("exclamation-triangle")}<span>${esc(o.text || "Something went wrong.")}</span><span class="ds-spacer" style="flex:1"></span>${o.retry ? DS.button(Object.assign({ label: "Retry", size: "sm", icon: "arrow-path" }, o.retry)) : ""}</div>`; };
  DS.feed = (o) => { o = o || {}; return `<div class="ds-feed">${(o.items || []).map((it) => `<div class="ds-feed-item"><span class="ds-feed-icon">${DS.icon(it.icon || "clock", { size: 14 })}</span><div><div>${it.html || esc(it.text)}</div>${it.by ? `<div class="ds-muted" style="font-size:11px">${esc(it.by)}</div>` : ""}</div><span class="ds-feed-when" title="${esc(DS.fmt ? DS.fmt.title(it.when) : it.when)}">${esc(DS.fmt ? DS.fmt.relative(it.when) : it.when)}</span></div>`).join("")}</div>`; };

  // ---------------------------------------------------------------- toast (one queue, variants, live region)
  let _toasts = null;
  const toastHost = () => { if (!_toasts) { _toasts = document.createElement("div"); _toasts.className = "ds-toasts"; _toasts.setAttribute("role", "status"); _toasts.setAttribute("aria-live", "polite"); document.body.appendChild(_toasts); } return _toasts; };
  /** DS.toast("Marked as received", {tone:"success"|"danger"|"warning"|"info", action:{label,onclick}, duration:4000}) -> {dismiss()} */
  DS.toast = (msg, o) => {
    o = o || {}; const host = toastHost();
    while (host.children.length >= 3) host.firstChild.remove();
    const el = document.createElement("div"); el.className = cls("ds-toast", o.tone && `ds-toast-${o.tone}`);
    const icon = { success: "check-circle", danger: "x-circle", warning: "exclamation-triangle", info: "information-circle" }[o.tone];
    el.innerHTML = `${icon ? DS.icon(icon) : ""}<span class="ds-toast-text">${o.html || esc(msg)}</span>${o.action ? `<button type="button" class="ds-toast-action">${esc(o.action.label)}</button>` : ""}<button type="button" class="ds-toast-x" aria-label="Dismiss">${DS.icon("x-mark", { size: 14 })}</button>`;
    const dismiss = () => { if (el.parentNode) el.remove(); };
    el.querySelector(".ds-toast-x").onclick = dismiss;
    if (o.action) el.querySelector(".ds-toast-action").onclick = () => { dismiss(); if (typeof o.action.onclick === "function") o.action.onclick(); else if (o.action.onclick) new Function(o.action.onclick)(); };
    host.appendChild(el);
    const ms = o.duration === 0 ? 0 : (o.duration || (o.tone === "danger" ? 7000 : 4000));
    if (ms) setTimeout(dismiss, ms);
    return { dismiss, el };
  };
  DS.toast.success = (m, o) => DS.toast(m, Object.assign({ tone: "success" }, o)); DS.toast.error = (m, o) => DS.toast(m, Object.assign({ tone: "danger" }, o));
  DS.toast.warn = (m, o) => DS.toast(m, Object.assign({ tone: "warning" }, o)); DS.toast.info = (m, o) => DS.toast(m, Object.assign({ tone: "info" }, o));

  // ---------------------------------------------------------------- dialogs: Modal · DetailDrawer · ConfirmDialog · prompt (native <dialog>)
  const _openers = new Map();
  /** DS.modal({id,title,size:sm|md|lg,body,primary,secondary:[...],cancel,destructive,dismissible:true,unsavedGuard:false}) — footer: destructive at inline-start, Cancel + primary at inline-end. */
  DS.modal = (o) => {
    o = o || {}; const id = o.id || uid("modal"); const f = o;
    const foot = (f.primary || f.secondary || f.cancel !== false || f.destructive) ? `<div class="ds-modal-foot">${f.destructive ? DS.button(Object.assign({ variant: "danger" }, f.destructive)) : ""}<span class="ds-spacer"></span>${(f.secondary || []).map((b) => DS.button(b)).join("")}${f.cancel !== false ? DS.button(Object.assign({ label: "Cancel", variant: "ghost", onclick: `DS.dialogClose('${id}')` }, f.cancel || {})) : ""}${f.primary ? DS.button(Object.assign({ variant: "primary" }, f.primary)) : ""}</div>` : "";
    return `<dialog${attrs({ class: cls("ds-dialog", o.cls), id, "aria-labelledby": `${id}-title`, "data-dismiss": o.dismissible === false ? "0" : "1", "data-guard": o.unsavedGuard ? "1" : null, onclick: "DS.dialogBackdrop(event,this)", oncancel: "DS.dialogCancel(event,this)", onclose: "DS._dialogClosed(this)" })}><div class="ds-modal ds-modal-${o.size || "md"}"><div class="ds-modal-head"><h2 id="${id}-title">${esc(o.title)}</h2>${DS.button({ icon: "x-mark", variant: "ghost", size: "sm", ariaLabel: "Close", onclick: `DS.dialogClose('${id}')` })}</div><div class="ds-modal-body">${o.body || ""}</div>${foot}</div></dialog>`;
  };
  /** DS.drawer({id,title,subtitle,badge,facts:[{label,value}],tabs,body,side,actions:[...],primary,size:"lg"}) — the T2 detail surface. */
  DS.drawer = (o) => {
    o = o || {}; const id = o.id || uid("drawer");
    const facts = (o.facts || []).map((f) => DS.stat(f)).join("");
    const body = o.side ? `<div class="ds-drawer-layout"><div>${o.body || ""}</div><aside class="ds-drawer-side">${o.side}</aside></div>` : (o.body || "");
    const foot = (o.actions || o.primary) ? `<div class="ds-drawer-foot">${(o.actions || []).map((b) => DS.button(b)).join("")}<span class="ds-spacer" style="flex:1"></span>${o.primary ? DS.button(Object.assign({ variant: "primary" }, o.primary)) : ""}</div>` : "";
    return `<dialog${attrs({ class: cls("ds-dialog", o.cls), id, "aria-labelledby": `${id}-title`, "data-dismiss": o.dismissible === false ? "0" : "1", "data-guard": o.unsavedGuard ? "1" : null, onclick: "DS.dialogBackdrop(event,this)", oncancel: "DS.dialogCancel(event,this)", onclose: "DS._dialogClosed(this)" })}><div class="${cls("ds-drawer", o.size === "lg" && "ds-drawer-lg")}"><div class="ds-drawer-head"><div class="ds-drawer-title">${o.icon ? DS.icon(o.icon, { size: "lg" }) : ""}<h2 id="${id}-title"><span class="ds-mono" style="font-size:.85em">${esc(o.identifier || "")}</span>${o.identifier ? " " : ""}${esc(o.title)}</h2>${o.badge || ""}${o.menu || ""}${DS.button({ icon: "x-mark", variant: "ghost", size: "sm", ariaLabel: "Close", onclick: `DS.dialogClose('${id}')` })}</div>${o.subtitle ? `<div class="ds-muted">${esc(o.subtitle)}</div>` : ""}${facts ? `<div class="ds-drawer-facts">${facts}</div>` : ""}${o.tabs || ""}</div><div class="ds-drawer-body">${body}</div>${foot}</div></dialog>`;
  };
  DS.dialogOpen = (id, o) => { const el = $(id); if (!el) return null; o = o || {}; _openers.set(el.id, document.activeElement); if (o.dirty != null) el.dataset.dirty = o.dirty ? "1" : ""; if (!el.open) el.showModal(); const f = el.querySelector("[autofocus],input:not([type=hidden]),select,textarea,button.ds-btn-primary"); if (f && o.focus !== false) f.focus(); return el; };
  DS.dialogDirty = (id, on) => { const el = $(id); if (el) el.dataset.dirty = on ? "1" : ""; };
  DS.dialogClose = async (id, value, force) => {
    const el = $(id); if (!el || !el.open) return false;
    if (!force && el.dataset.guard === "1" && el.dataset.dirty === "1") { const ok = await DS.confirm({ title: "Discard changes?", text: "You have unsaved changes. Close without saving?", confirmLabel: "Discard", tone: "danger" }); if (!ok) return false; }
    el.close(value == null ? "" : String(value)); return true;
  };
  DS.dialogBackdrop = (ev, el) => { if (ev.target === el && el.dataset.dismiss !== "0") DS.dialogClose(el.id); };
  DS.dialogCancel = (ev, el) => { ev.preventDefault(); DS.dialogClose(el.id); };
  DS._dialogClosed = (el) => { const op = _openers.get(el.id); _openers.delete(el.id); if (op && op.focus && document.contains(op)) op.focus(); if (el.dataset.transient === "1") setTimeout(() => el.remove(), 0); };
  /** DS.confirm({title,text,confirmLabel,cancelLabel,tone:"danger"|"primary",icon}) -> Promise<boolean> — only for destructive or irreversible actions. */
  DS.confirm = (o) => new Promise((resolve) => {
    o = o || {}; const id = uid("confirm");
    const html = DS.modal({ id, title: o.title || "Are you sure?", size: "sm", body: `<div class="ds-confirm-text">${o.html || esc(o.text || "")}</div>`, cancel: { label: o.cancelLabel || "Cancel", onclick: `DS.dialogClose('${id}','cancel')` },
      primary: { label: o.confirmLabel || "Confirm", variant: o.tone === "danger" ? "danger" : "primary", solid: o.tone === "danger", icon: o.icon, onclick: `DS.dialogClose('${id}','ok')`, attrs: { autofocus: true } } });
    document.body.insertAdjacentHTML("beforeend", html); const el = $(id); el.dataset.transient = "1";
    el.addEventListener("close", () => resolve(el.returnValue === "ok"), { once: true }); DS.dialogOpen(id);
  });
  /** DS.prompt({title,label,value,placeholder,type,required,submitLabel,help}) -> Promise<string|null> — replaces window.prompt(). */
  DS.prompt = (o) => new Promise((resolve) => {
    o = o || {}; const id = uid("prompt"); const fid = id + "-v";
    const body = DS.form({ id: id + "-f", sections: [DS.field({ id: fid, label: o.label || o.title, required: o.required !== false, help: o.help, input: (o.type === "textarea" ? DS.textarea : o.type === "number" ? DS.numberInput : DS.input)({ id: fid, value: o.value, placeholder: o.placeholder, required: o.required !== false, attrs: { autofocus: true } }) })], onsubmit: `event.preventDefault();DS.dialogClose('${id}','ok')` });
    document.body.insertAdjacentHTML("beforeend", DS.modal({ id, title: o.title || "Enter a value", size: "sm", body, cancel: { onclick: `DS.dialogClose('${id}','cancel')` }, primary: { label: o.submitLabel || "Save", onclick: `document.getElementById('${id}-f').requestSubmit()` } }));
    const el = $(id); el.dataset.transient = "1";
    el.addEventListener("close", () => resolve(el.returnValue === "ok" ? $(fid).value : null), { once: true }); DS.dialogOpen(id);
  });

  // ---------------------------------------------------------------- page header · filter bar · wizard · drop zone
  /** DS.pageHeader({crumbs:[{label,onclick|href}],title,badge,stats:[{label,value,hint}],updated,primary,secondary:[max 2],overflow:[menu items],below}) */
  DS.pageHeader = (o) => {
    o = o || {};
    const crumbs = (o.crumbs || []).map((c, i) => `${i ? DS.icon("chevron-right", { size: 12 }) : ""}${c.href || c.onclick ? `<a${attrs({ href: c.href || "#", onclick: c.onclick ? `${c.onclick};return false;` : null })}>${esc(c.label)}</a>` : `<span>${esc(c.label)}</span>`}`).join("");
    const sec = (o.secondary || []).slice(0, 2).map((b) => DS.button(b)).join("");
    const extra = (o.secondary || []).slice(2).map((b) => ({ label: b.label, icon: b.icon, onclick: b.onclick }));
    const over = (o.overflow || []).concat(extra.length ? [{ divider: true }].concat(extra) : []);
    const stats = (o.stats || []).map((s) => DS.stat(s)).join("");
    const upd = o.updated ? `<span class="ds-updated" title="${esc(DS.fmt.title(o.updated))}">Updated ${esc(DS.fmt.relative(o.updated))}</span>` : "";
    return `<header class="ds-pagehead">${crumbs ? `<nav class="ds-crumbs" aria-label="Breadcrumb">${crumbs}</nav>` : ""}<div class="ds-pagehead-row"><h1>${esc(o.title)}</h1>${o.badge || ""}<span class="ds-spacer"></span>${sec}${over.length ? DS.menu({ items: over, button: { icon: "ellipsis-horizontal", ariaLabel: "More actions" } }) : ""}${o.primary ? DS.button(Object.assign({ variant: "primary" }, o.primary)) : ""}</div>${stats || upd ? `<div class="ds-pagehead-stats">${stats}${upd}</div>` : ""}${o.below || ""}</header>`;
  };
  /** DS.filterBar({search:{...},views:[{key,label,count,active,onclick}],chips:[{label,value,active,onclick,onremove}],add:{onclick},clear:{onclick},right:[html]}) */
  DS.filterBar = (o) => {
    o = o || {};
    const views = o.views && o.views.length ? DS.tabs({ variant: "pills", items: o.views.map((v) => ({ key: v.key, label: v.label, count: v.count })), active: (o.views.find((v) => v.active) || {}).key, onchange: o.onView || "" }) : "";
    const chips = (o.chips || []).map((c) => `<button${attrs({ type: "button", class: cls("ds-chip", c.cls), "aria-pressed": c.active ? "true" : "false", onclick: c.onclick, title: c.title })}>${c.icon ? DS.icon(c.icon, { size: 13 }) : ""}<span>${esc(c.label)}</span>${c.value != null ? `<span class="ds-chip-val">${esc(c.value)}</span>` : ""}${c.onremove ? `<span class="ds-chip-x" role="button" aria-label="Remove filter" onclick="event.stopPropagation();${esc(c.onremove)}">${DS.icon("x-mark", { size: 12 })}</span>` : ""}</button>`).join("");
    const add = o.add ? `<button type="button" class="ds-chip ds-chip-dashed" onclick="${esc(o.add.onclick || "")}">${DS.icon("plus", { size: 13 })}<span>${esc(o.add.label || "Add filter")}</span></button>` : "";
    const clear = o.clear && o.clear.show !== false ? DS.button({ label: o.clear.label || "Clear all", variant: "ghost", size: "sm", onclick: o.clear.onclick }) : "";
    return `<div class="ds-filterbar" role="search">${o.search ? DS.search(Object.assign({ size: "sm" }, o.search)) : ""}${views}${chips}${add}${clear}<span class="ds-spacer"></span>${(o.right || []).join("")}</div>`;
  };
  /** DS.wizard({id,steps:["Source","Map","Validate","Confirm","Result"],current:0,body,back,next,cancel}) — the ImportWizard shell. */
  DS.wizard = (o) => {
    o = o || {}; const cur = o.current || 0;
    const steps = (o.steps || []).map((s, i) => `<span class="${cls("ds-wizard-step", i === cur && "is-current", i < cur && "is-done")}"${i === cur ? ' aria-current="step"' : ""}><span class="ds-step-n">${i < cur ? DS.icon("check", { size: 12 }) : i + 1}</span>${esc(s)}</span>`).join("");
    const foot = `<div class="ds-form-footer">${o.cancel ? DS.button(Object.assign({ label: "Cancel", variant: "ghost" }, o.cancel)) : ""}<span class="ds-spacer"></span>${o.back ? DS.button(Object.assign({ label: "Back", icon: "arrow-left" }, o.back)) : ""}${o.next ? DS.button(Object.assign({ label: cur >= (o.steps || []).length - 1 ? "Done" : "Next", variant: "primary", iconEnd: cur >= (o.steps || []).length - 1 ? null : "arrow-right" }, o.next)) : ""}</div>`;
    return `<div${attrs({ class: cls("ds-wizard", o.cls), id: o.id })}><div class="ds-wizard-steps" aria-label="Steps">${steps}</div><div class="ds-wizard-body">${o.body || ""}</div>${foot}</div>`;
  };
  const _dz = {};
  /** DS.dropzone({id,accept,multiple,paste:true,hint,onFiles:fn|"fnName"}) — a REAL drop zone: drag-and-drop, click to pick, CmdV paste. */
  DS.dropzone = (o) => {
    o = o || {}; const id = o.id || uid("dz"); if (o.onFiles) _dz[id] = o.onFiles;
    return `<div${attrs({ class: cls("ds-dropzone", o.cls), id, tabindex: "0", role: "button", "aria-label": o.ariaLabel || "Upload files", "data-paste": o.paste ? "1" : null, ondragover: "DS.dzOver(event,this,true)", ondragleave: "DS.dzOver(event,this,false)", ondrop: "DS.dzDrop(event,this)", onclick: "DS.dzPick(this)", onkeydown: "if(event.key==='Enter'||event.key===' '){event.preventDefault();DS.dzPick(this)}" })}><input type="file" hidden${attrs({ accept: o.accept, multiple: !!o.multiple })} onchange="DS.dzFiles(this.closest('.ds-dropzone'),this.files);this.value=''">${DS.icon("cloud-arrow-up", { size: "lg" })}<div><b>${esc(o.title || "Drop files here or click to choose")}</b></div><div>${esc(o.hint || [o.accept ? `Accepted: ${o.accept}` : "", o.paste ? "or paste with Cmd/Ctrl+V" : ""].filter(Boolean).join(" · "))}</div><div class="ds-files" data-files></div></div>`;
  };
  DS.dropzoneBind = (id, fn) => { _dz[id] = fn; };
  DS.dzOver = (ev, el, on) => { ev.preventDefault(); el.classList.toggle("is-over", !!on); };
  DS.dzDrop = (ev, el) => { ev.preventDefault(); el.classList.remove("is-over"); DS.dzFiles(el, ev.dataTransfer && ev.dataTransfer.files); };
  DS.dzPick = (el) => { const inp = el.querySelector('input[type="file"]'); if (inp) inp.click(); };
  DS.dzFiles = (el, files) => {
    if (!el || !files || !files.length) return; const list = Array.from(files); const inp = el.querySelector('input[type="file"]');
    const accept = (inp && inp.accept ? inp.accept.split(",").map((s) => s.trim().toLowerCase()) : []);
    const ok = list.filter((f) => !accept.length || accept.some((a) => a.endsWith("/*") ? (f.type || "").startsWith(a.slice(0, -1)) : a.startsWith(".") ? f.name.toLowerCase().endsWith(a) : (f.type || "").toLowerCase() === a));
    if (ok.length < list.length) DS.toast.warn(`${list.length - ok.length} file(s) skipped: type not accepted`);
    const picked = inp && !inp.multiple ? ok.slice(0, 1) : ok;
    const box = el.querySelector("[data-files]"); if (box) box.innerHTML = picked.map((f) => `<div class="ds-file">${DS.icon(f.type.startsWith("image/") ? "photo" : "document-text")}<span class="ds-truncate">${esc(f.name)}</span><span class="ds-muted">${DS.fmt ? DS.fmt.number(Math.round(f.size / 1024)) : f.size} KB</span></div>`).join("");
    const fn = _dz[el.id]; if (typeof fn === "function") fn(picked, el); else if (typeof fn === "string" && typeof window[fn] === "function") window[fn](picked, el);
  };
  document.addEventListener("paste", (e) => {
    const files = e.clipboardData && Array.from(e.clipboardData.files || []); if (!files || !files.length) return;
    const zones = Array.from(document.querySelectorAll('.ds-dropzone[data-paste="1"]')).filter((z) => z.offsetParent !== null);
    if (zones.length === 1) { e.preventDefault(); DS.dzFiles(zones[0], files); }
  });

  // ---------------------------------------------------------------- app shell (used by the demo now, by /app in Phase 2)
  /** DS.sidebar({brand,groups:[{key,label,open,items:[{key,label,icon,badge,badgeMuted,active,onclick,href}]}],attention:{count,onclick,active},workspace:{name,onclick},user:{name,role,onclick}}) */
  DS.sidebar = (o) => {
    o = o || {};
    const item = (it) => `<${it.href ? "a" : "button"}${attrs({ class: cls("ds-nav-item", it.cls), href: it.href, type: it.href ? null : "button", onclick: it.onclick, "aria-current": it.active ? "page" : null, "data-nav": it.key })}>${DS.icon(it.icon || "cube", { size: "nav" })}<span class="ds-truncate">${esc(it.label)}</span>${it.badge ? `<span class="${cls("ds-nav-badge", it.badgeMuted && "ds-nav-badge-muted")}">${esc(it.badge)}</span>` : ""}</${it.href ? "a" : "button"}>`;
    const groups = (o.groups || []).map((g) => g.label ? `<details class="ds-nav-group" ${g.open === false ? "" : "open"} data-group="${esc(g.key || g.label)}"><summary>${DS.icon("chevron-down")}<span>${esc(g.label)}</span></summary>${(g.items || []).map(item).join("")}</details>` : `<div class="ds-nav-group">${(g.items || []).map(item).join("")}</div>`).join("");
    const attn = o.attention ? item({ key: "attention", label: o.attention.label || "Needs attention", icon: "bell-alert", badge: o.attention.count || null, active: o.attention.active, onclick: o.attention.onclick }) : "";
    const foot = `<div class="ds-sidebar-foot">${o.workspace ? `<button type="button" class="ds-workspace" onclick="${esc(o.workspace.onclick || "")}" aria-haspopup="menu">${DS.icon("building-storefront")}<span class="ds-truncate">${esc(o.workspace.name)}</span>${DS.icon("chevron-down")}</button>` : ""}${o.user ? `<button type="button" class="ds-nav-item" onclick="${esc(o.user.onclick || "")}">${DS.avatar({ name: o.user.name })}<span class="ds-truncate">${esc(o.user.name)}<br><span class="ds-muted" style="font-size:11px">${esc(o.user.role || "")}</span></span></button>` : ""}</div>`;
    return `<aside class="ds-sidebar" aria-label="Main navigation"><div class="ds-sidebar-brand">${o.brand || ""}</div><nav>${groups}${attn ? `<div class="ds-nav-group" style="margin-top:8px;border-top:1px solid var(--ds-line);padding-top:8px">${attn}</div>` : ""}</nav>${foot}</aside>`;
  };
  /** DS.topbar({search:{id,placeholder,oninput},notifications:{count,onclick},language:{label,onclick},user:{name,onclick},extra:[html],menuToggle}) */
  DS.topbar = (o) => {
    o = o || {};
    return `<div class="ds-topbar">${o.menuToggle ? DS.button({ icon: "bars-3", variant: "ghost", ariaLabel: "Menu", onclick: o.menuToggle, cls: "ds-topbar-menu" }) : ""}${o.search ? `<span class="ds-search ds-input-wrap">${DS.icon("magnifying-glass")}<input${attrs({ type: "search", class: "ds-input ds-input-sm", id: o.search.id || "ds-global-search", placeholder: o.search.placeholder || "Search orders, customers, GWD…", "aria-label": "Global search", oninput: o.search.oninput, onkeydown: o.search.onkeydown, "data-ds-search": "1" })}><span class="ds-affix"><kbd>/</kbd></span></span>` : ""}<span class="ds-spacer"></span>${(o.extra || []).join("")}${o.language && !o.language.hidden ? DS.button({ label: o.language.label || "ع", variant: "ghost", ariaLabel: "Language", onclick: o.language.onclick, title: "Language" }) : ""}${o.notifications ? `<span class="ds-relative">${DS.button({ icon: "bell", variant: "ghost", ariaLabel: "Notifications", onclick: o.notifications.onclick, attrs: { "aria-haspopup": "menu" } })}${o.notifications.count ? `<span class="ds-notif-dot" aria-hidden="true"></span>` : ""}</span>` : ""}${o.user ? DS.menu({ items: o.user.items || [], button: { label: o.user.name, icon: "user-circle", variant: "ghost", ariaLabel: "User menu" } }) : ""}</div>`;
  };
  DS.shell = (o) => { o = o || {}; return `<div class="ds-shell ds-root">${o.sidebar || ""}<div class="ds-main">${o.banner || ""}${o.topbar || ""}<div class="ds-content">${o.content || ""}</div></div></div>`; };
  /** "/" focuses the global search, "?" opens the shortcut sheet. Call once per page. */
  DS.installShortcuts = (o) => {
    o = o || {};
    document.addEventListener("keydown", (e) => {
      const t = e.target; const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { const s = document.querySelector("[data-ds-search]"); if (s) { e.preventDefault(); s.focus(); s.select && s.select(); } }
      else if (e.key === "?") { e.preventDefault(); DS.shortcutsSheet(o.shortcuts); }
    });
  };
  DS.shortcutsSheet = (list) => {
    const rows = (list || [["/", "Focus search"], ["?", "This sheet"], ["Esc", "Close menus and dialogs"], ["Arrow keys", "Move between rows"], ["Enter", "Open the focused row"], ["Space", "Select the focused row"]]).map(([k, d]) => `<div class="ds-kv"><span>${esc(d)}</span><b>${DS.kbd(k)}</b></div>`).join("");
    const id = "ds-shortcuts"; if (!$(id)) document.body.insertAdjacentHTML("beforeend", DS.modal({ id, title: "Keyboard shortcuts", size: "sm", body: rows, cancel: { label: "Close" } })); DS.dialogOpen(id);
  };
})();
