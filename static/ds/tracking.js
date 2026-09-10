/* static/ds/tracking.js - Tracking (the Bulk search page) on the design system
   (docs/ux-restructure Batch D).

   Paste many GWD numbers; every one is looked up on BOTH boards - Purchases (POS,
   the customer purchase orders) and Leluxe (LX, the ClickUp AZ (2) mirror) - and
   the answer is one row per match: a package on a purchase order, a Leluxe
   parcel, or "not found". This file draws the page's header, the saved views and
   the results DataTable. web/index.html keeps the data (bsEnsureData, the finders
   bsFindPo / bsFindLx, the token parser, the search itself) and hands the module
   a `ctx` on every repaint.

   It also owns the PARCEL MODEL CELLS: the nine facts every "packages as a table"
   surface shows for a {kind:"po"|"lx"|"miss"} model. GAASH mail's workflow
   expansion (static/ds/gaash.js) draws the same cells through
   DS.tracking.modelCells, so the two surfaces cannot drift apart.

   index.html's top-level `let` bindings are not window properties; legacy cell
   builders are `function` declarations and reachable as W.<name>. */
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const G = (D.tracking = {});
  const esc = D.esc;
  const W = window;
  const num = (v) => D.fmt.number(v);
  const text = (v) => { v = (v == null ? "" : String(v)).trim(); return v ? `<span class="ds-truncate" dir="auto" title="${esc(v)}">${esc(v)}</span>` : D.dash(); };
  const muted = (v) => (v ? `<span class="ds-muted">${esc(v)}</span>` : D.dash());
  const hostOf = (m) => (typeof m === "string" ? document.getElementById(m) : m);
  const lxF = (r, n) => (W.lxF ? W.lxF(r, n) : null);

  // ---------------------------------------------------------------- the parcel model
  /** Cells for one parcel model {kind:"po"|"lx"|"miss", tn, p, pk, pi, lx} - the facts the
      Tracking row shows, keyed by PARCEL_COLS. ctx: {money} (PO_MONEY - a role without
      view_cost gets no dollar values). Every legacy builder is reached through W.* with a
      fallback, so the cells render even before index.html has defined it. */
  G.modelCells = (m, ctx) => {
    ctx = ctx || {};
    if (m.kind === "miss") return { found: D.attention({ kind: "no_tracking", label: "Not found", title: "No package on Purchases or Leluxe carries this number" }) };
    if (m.kind === "po") {
      const p = m.p, pk = m.pk, pst = (pk.otlobly_status || "").trim(), est = W.pkgEstTotal ? W.pkgEstTotal(pk) : { sum: 0, priced: 0 };
      return {
        found: `<button type="button" class="ds-btn ds-btn-sm ds-btn-secondary" title="Open on the Purchases board" onclick="event.stopPropagation();bsOpenPo('${esc(p.po_id)}')">${D.icon("cube", { size: 13 })}<span>${esc(p.po_id)}</span></button><span class="ds-muted">pkg ${esc(pk.package_no)}</span>`,
        oname: W.poNameCell ? W.poNameCell(p.ship_to, true) : text(p.ship_to),
        profile: W.poProfileCell ? W.poProfileCell(p) : D.dash(),
        imgs: W.poThumbStrip ? W.poThumbStrip(pk.items) : "",
        cust: text(W.pkgWho ? W.pkgWho(pk) : ""),
        status: pst ? D.status.badge("pkg", pst) : D.dash(),
        gash: W.pkgGaashPill ? W.pkgGaashPill(pk) : D.dash(),
        gashdate: D.dash(),
        value: ctx.money && est.priced ? `<span class="ds-num">${esc(D.fmt.money(est.sum, "USD", { approx: true }))}</span>` : D.dash(),
      };
    }
    const lx = m.lx || {}, items = lx.items || [];
    const oname = (lx.order && lx.order.name) || (lx.pkgRow && lx.pkgRow.name) || "";
    const prof = lxProfile(lx), gd = lxGashDate(lx);
    return {
      found: D.tag({ label: "Leluxe", tone: "neutral" }),
      oname: text(oname),
      profile: prof && W.lxProfileChip ? W.lxProfileChip(prof, `Profile / account (NAME): ${prof}`) : D.dash(),
      imgs: W.lxThumbs ? W.lxThumbs(items) : "",
      cust: `<span class="ds-muted">${esc(num(items.length))} products</span>`,
      status: (lx.sts || []).length ? lx.sts.map((s) => D.status.badge("pkg", s)).join(" ") : D.dash(),
      gash: (W.lxGashRollupPill && W.lxGashRollupPill(lx.gash)) || (lx.trk ? D.tag({ label: lx.trk, tone: "neutral" }) : D.dash()),
      gashdate: gd ? ((W.lxDueChip && W.lxDueChip(gd, true)) || muted(W.lxMs ? W.lxMs(gd) : gd)) : D.dash(),
      value: lx.tot ? `<b class="ds-num">₪${esc(Number(lx.tot).toLocaleString("en-US"))}</b>` : D.dash(),
    };
  };
  G.PARCEL_COLS = [
    { key: "found", label: "Found in", w: 150 }, { key: "oname", label: "Order name", w: 160 }, { key: "profile", label: "Profile", w: 128 },
    { key: "imgs", label: "Products", w: 150 }, { key: "cust", label: "Customers", w: 140 }, { key: "status", label: "Status", w: 150 },
    { key: "gash", label: "GAASH", w: 140 }, { key: "gashdate", label: "GASH date", w: 106 }, { key: "value", label: "Value", w: 110, align: "end" },
  ];
  const lxProfile = (lx) => ((lx.items || []).map((it) => lxF(it, "name")).find(Boolean) || (lx.order && lxF(lx.order, "name")) || "");
  const lxGashDate = (lx) => (lx.pkgRow && lxF(lx.pkgRow, "gash date")) || (lx.items || []).map((it) => lxF(it, "gash date")).find(Boolean);

  // ---------------------------------------------------------------- sorting
  // "~" is "no value here"; like the DataTable, blanks sort LAST - in BOTH directions,
  // which is why the page sorts here instead of letting the table flip the whole order.
  const lower = (v) => String(v == null ? "" : v).trim().toLowerCase() || "~";
  const blank = (v) => v == null || v === "" || (typeof v === "string" && /^~+$/.test(v));
  const cmp = (a, b) => {
    if (typeof a === "number" && typeof b === "number") return a - b;
    const na = Number(a), nb = Number(b);
    if (!isNaN(na) && !isNaN(nb) && String(a).trim() !== "" && String(b).trim() !== "") return na - nb;
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
  };
  G.sortVal = (m, key) => {
    if (key === "tn") return m.tn;
    if (m.kind === "miss") return "~";
    if (m.kind === "po") {
      const p = m.p, pk = m.pk;
      switch (key) {
        case "found": return "1" + lower(p.po_id);                       // Purchases before Leluxe, as before
        case "oname": return lower(p.ship_to);
        case "profile": return lower(p.profile_box);
        case "imgs": return (pk.items || []).length;
        case "cust": return lower(W.pkgWho ? W.pkgWho(pk) : "");
        case "status": return lower(pk.otlobly_status);
        case "gash": return lower(pk.tracking_status && (pk.tracking_status.label || pk.tracking_status.bucket));
        case "value": { const est = W.pkgEstTotal ? W.pkgEstTotal(pk) : null; return est && est.priced ? est.sum : "~"; }
      }
      return "~";
    }
    const lx = m.lx || {};
    switch (key) {
      case "found": return "2leluxe";
      case "oname": return lower((lx.order && lx.order.name) || (lx.pkgRow && lx.pkgRow.name));
      case "profile": return lower(lxProfile(lx));
      case "imgs": return (lx.items || []).length;
      case "status": return lower((lx.sts || [])[0]);
      case "gash": return lower((lx.gash && lx.gash.val) || lx.trk);
      case "gashdate": { const gd = lxGashDate(lx); const ms = gd ? Number(gd) : 0; return ms || "~"; }
      case "value": return lx.tot || "~";
    }
    return "~";
  };
  /** The found rows in the table's sort order, then every miss in paste order. */
  G.sortRows = (models, sort) => {
    const found = (models || []).filter((m) => m.kind !== "miss"), miss = (models || []).filter((m) => m.kind === "miss");
    if (!sort || !sort.key) return found.concat(miss);
    const dir = sort.dir === "desc" ? -1 : 1;
    const keyed = found.map((m, i) => ({ m, i, v: G.sortVal(m, sort.key) }));
    keyed.sort((a, b) => {
      const ba = blank(a.v), bb = blank(b.v);
      if (ba && bb) return a.i - b.i; if (ba) return 1; if (bb) return -1;
      return (cmp(a.v, b.v) * dir) || (a.i - b.i);
    });
    return keyed.map((x) => x.m).concat(miss);
  };

  // ---------------------------------------------------------------- views + sums
  G.VIEWS = [{ key: "all", label: "All" }, { key: "found", label: "Found" }, { key: "missing", label: "Missing" }, { key: "po", label: "Purchases" }, { key: "lx", label: "Leluxe" }];
  G.inView = (m, view) => (view === "found" ? m.kind !== "miss" : view === "missing" ? m.kind === "miss" : view === "po" ? m.kind === "po" : view === "lx" ? m.kind === "lx" : true);
  G.rowKey = (m) => (m.kind === "po" ? `po:${m.tn}:${m.p.po_id}:${m.pi}` : `${m.kind}:${m.tn}`);
  /** USD over the purchase-order rows (when money is visible), ILS over the Leluxe rows. */
  G.sums = (models, ctx) => {
    let usd = 0, ils = 0;
    (models || []).forEach((m) => {
      if (m.kind === "po") { const est = W.pkgEstTotal ? W.pkgEstTotal(m.pk) : null; if (ctx.money && est) usd += Number(est.sum) || 0; }
      else if (m.kind === "lx") ils += Number(m.lx && m.lx.tot) || 0;
    });
    return { usd: Math.round(usd * 100) / 100, ils: Math.round(ils * 100) / 100 };
  };
  G.moneyText = (s, ctx) => [ctx.money && s.usd ? D.fmt.money(s.usd, "USD", { approx: true }) : "", s.ils ? "₪" + Number(s.ils).toLocaleString("en-US") : ""].filter(Boolean).join(" · ");

  // ---------------------------------------------------------------- chrome
  /** ctx: {pos, lx, loading, last:{models,nTok,found}|null, money, canEnroll} */
  G.chrome = (host, ctx) => {
    const el = hostOf(host); if (!el) return;
    ctx = ctx || {};
    const last = ctx.last, stats = [];
    if (ctx.loading) stats.push({ label: "Loading", value: "…", hint: "purchase orders and Leluxe" });
    else {
      stats.push({ label: "Purchase orders", value: num((ctx.pos || []).length) });
      stats.push(ctx.lx ? { label: "Leluxe orders", value: num((ctx.lx.orders || []).length) } : { label: "Leluxe orders", value: "—", hint: "not loaded for your role" });
    }
    if (last) {
      const missing = last.nTok - last.found, money = G.moneyText(G.sums(last.models, ctx), ctx);
      stats.push({ label: "Numbers", value: num(last.nTok) }, { label: "Found", value: num(last.found), tone: last.found ? "success" : null }, { label: "Missing", value: num(missing), tone: missing ? "warning" : null });
      if (money) stats.push({ label: "Value", value: money });
    }
    const overflow = [];
    if (last && ctx.canEnroll && last.found) overflow.push({ label: "Enroll all found in GAASH mail", icon: "envelope", onclick: "bsEnrollFound()" });
    if (last && last.nTok - last.found > 0) overflow.push({ label: "Copy missing numbers", icon: "clipboard-document", onclick: "bsCopyMissing()" });
    D.paintHost(el, D.pageHeader({
      crumbs: [{ label: "Shipping" }, { label: "Tracking" }], title: "Tracking", stats,
      primary: { label: "Search", icon: "magnifying-glass", onclick: "bsRun()" },
      secondary: [{ label: "Clear", icon: "x-mark", onclick: "bsClear()" }, { label: "Reload data", icon: "arrow-path", onclick: "bsReload(this)" }],
      overflow,
    }));
  };

  // ---------------------------------------------------------------- results
  /** ctx: {money, lx, view, canEnroll, menu(m)->[menu items], nTok, found, loading, error} */
  G.results = (mount, models, ctx) => {
    const el = hostOf(mount); if (!el) return;
    ctx = ctx || {};
    if (ctx.loading) { el.innerHTML = D.skeleton({ rows: 4 }); return; }
    if (ctx.error) { el.innerHTML = D.errorState({ text: ctx.error, retry: { onclick: "bsReload(this)" } }); return; }
    models = models || [];
    const view = ctx.view || "all";
    const counts = {}; G.VIEWS.forEach((v) => { counts[v.key] = models.filter((m) => G.inView(m, v.key)).length; });
    const bar = D.filterBar({ views: G.VIEWS.map((v) => ({ key: v.key, label: v.label, count: counts[v.key], active: v.key === view })), onView: "bsView(KEY)" });
    const cache = new Map();
    const cellsOf = (m) => { let c = cache.get(m); if (!c) { c = G.modelCells(m, ctx); cache.set(m, c); } return c; };
    const cols = [{ key: "tn", label: "Tracking number", w: 190, pin: "start", locked: true, sortVal: (m) => m.tn,
      render: (m) => `<span class="ds-trk-tn"><span class="ds-mono" title="${esc(m.tn)}">${esc(m.tn)}</span>${W.lxCopyRawBtn ? W.lxCopyRawBtn(m.tn) : ""}</span>` }]
      .concat(G.PARCEL_COLS.filter((c) => c.key !== "value" || ctx.money || ctx.lx)
        .map((c) => Object.assign({}, c, { sortVal: (m) => G.sortVal(m, c.key), render: (m) => (m.kind === "miss" ? (c.key === "found" ? cellsOf(m).found : "") : (cellsOf(m)[c.key] || D.dash())) })))
      .concat([{ key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false, menu: (m) => (ctx.menu ? ctx.menu(m) : []) }]);
    const t = D.tableGet("trk"), sort = (t && t.state && t.state.sort) || null;
    const rows = G.sortRows(models.filter((m) => G.inView(m, view)), sort);
    const nTok = ctx.nTok != null ? ctx.nTok : models.length, found = ctx.found != null ? ctx.found : models.filter((m) => m.kind !== "miss").length, missing = nTok - found;
    const money = G.moneyText(G.sums(models, ctx), ctx);
    const o = {
      id: "trk", ariaLabel: "Tracking results", columns: cols, rows, rowKey: G.rowKey, seedVersion: 1,
      rowClass: (m) => (m.kind === "miss" ? "ds-trk-miss" : ""),
      onSort: () => { if (W.bsRender) W.bsRender(); },
      selectable: !!ctx.canEnroll,
      bulk: ctx.canEnroll ? [{ label: "Enroll in GAASH mail", icon: "envelope", onclick: "bsEnrollSel(keys,rows)" }] : [],
      footer: { tn: `<b>Σ ${esc(num(nTok))}</b> · ${esc(num(found))} found${missing ? ` · <span class="ds-trk-missing">${esc(num(missing))} missing</span>` : ""}`, value: money ? `<b class="ds-num">${esc(money)}</b>` : "" },
      empty: { icon: "map-pin", title: view === "all" ? "Nothing to show" : "Nothing in this view", text: view === "all" ? "Paste tracking numbers above and search." : "Switch to All to see every result of this search." },
    };
    D.paintHost(el, bar + D.table(o));
    D.tableMount("trk");
  };
})();
