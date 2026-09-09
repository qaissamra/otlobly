/*
  Purchase orders — the reference page (docs/ux-restructure Phase 3).

  This is the page every other one is rebuilt to match, so it is where the brief's
  section 14 list is answered. What changed and why, in the order the owner listed:

    2  the page has a real header: breadcrumb, title, the numbers that matter, and
       one primary action - instead of a middle-dot sentence with a seconds clock
    3  it is called "Purchase orders" and counts purchase orders (decision D7)
    4  one fact per column: the identity cell carries the PO number and nothing
       that another column already says
    5  Paid has its own column, over the numbers, right-aligned
    6  the estimate is a SEPARATE column labelled "Est. cost", so two dollar signs
       can never mean two different things in one cell
    7  every text cell is dir="auto" with the full value in its tooltip, so an
       Arabic name truncates at its own end
    8  Status and the row actions are PINNED to the end - they can never scroll
       off - and the scroller shows an edge shadow while there is more to see
    9  sentence case everywhere; the shouty "40 DAYS LATE" pill is now "40 d late"
   10  one attention vocabulary (DS.attention): late, no tracking number, missing
       name. Status pills stay status; a problem is never also a status colour
   11  flat rows, no card shadows, no phantom gap before the first row
   12  a package's products get their own aligned grid WITH a header row
   13  the buying-account column explains what a profile code is, in its header
   14  the open row is marked by its own treatment, never the nav's accent fill
   15  the column control is a labelled "Columns" button in the table bar

  It renders four boards - orders, packages, products, customers - on one DS
  DataTable each. The cells still call the app's own builders (poNameCell,
  pkgCuSelect, pkgRdCell...) because those carry the inline editing; nothing about
  how a value is SAVED changed in this phase. Everything the page needs from the
  app arrives in `ctx` (index.html's top-level bindings are not window properties).
*/
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const P = (D.purchases = {});
  const esc = D.esc;
  const money = (v) => D.fmt.money(v, "USD");
  const num = (v) => D.fmt.number(v);
  const W = window;

  // ---------------------------------------------------------------- shared cells
  /** Text that may be Arabic inside an English page: isolate it, keep the whole
      value in the tooltip, and let it truncate at its OWN end (item 7). */
  const text = (v) => {
    v = (v == null ? "" : String(v)).trim();
    return v ? `<span class="ds-truncate" dir="auto" title="${esc(v)}">${esc(v)}</span>` : D.dash();
  };
  /** What we think the goods cost. Never rendered in the same cell as what was paid. */
  const est = (e) => {
    if (!e.priced) return D.dash();
    const missing = e.total - e.priced;
    return `<span class="ds-pu-est" title="Estimated cost of the products${missing ? ", " + missing + " still unpriced" : ""}">${esc(money(e.sum))}${missing ? `<span class="ds-muted"> +${esc(num(missing))}?</span>` : ""}</span>`;
  };
  const poEst = (p) => (p.packages || []).reduce((a, pk) => {
    const e = W.pkgEstTotal(pk); a.sum += e.sum; a.priced += e.priced; a.total += e.total; return a;
  }, { sum: 0, priced: 0, total: 0 });

  /** The one attention vocabulary for this page (item 10). A problem is stated once,
      in words, in its own column — never as a fourth colour on something else. */
  function attention(ctx, p, pk) {
    const pkgs = pk ? [pk] : p.packages || [];
    const out = [];
    let worst = 0;
    pkgs.forEach((x) => { const n = ctx.lateDays(x); if (n != null && n > worst) worst = n; });
    if (worst) out.push({ kind: "late", detail: `${worst} d`, title: "Past the date it was promised" });
    const noTrk = pkgs.filter((x) => !(x.tracking_number || "").trim()).length;
    if (noTrk) out.push({ kind: "no_tracking", detail: pkgs.length > 1 ? String(noTrk) : "", title: "No GAASH tracking number yet" });
    if (!pk && !(p.ship_to || "").trim()) out.push({ kind: "missing_name", title: "This order ships under no name" });
    return out.map((a) => D.attention(a)).join("");
  }

  const PROFILE_HELP = "The Amazon buying account the order was placed under - the same code as its Multilogin browser profile (B19, E-B15).";

  const thumb = (it) => (it.image ? `<img class="ds-pu-thumb" src="${esc(it.image)}" alt="">` : `<span class="ds-pu-thumb"></span>`);
  const productName = (it) => `<span class="ds-pu-prod">${thumb(it)}${text(it.title || it.asin || "(unnamed product)")}${it.clean_url
    ? `<a class="ds-pu-out" href="${esc(it.clean_url)}" target="_blank" rel="noopener" title="Open on Amazon" aria-label="Open on Amazon">${D.icon("arrow-top-right-on-square", { size: 13 })}</a>` : ""}</span>`;
  const gwdCell = (pk) => {
    const g = (pk.tracking_number || "").trim();
    return `<span class="ds-pu-id"><b>${esc(pk.package_no)}</b>${g ? `<span class="ds-mono ds-truncate" title="${esc(g)}">${esc(g)}</span>` : ""}</span>`;
  };

  // ---------------------------------------------------------------- sub-grids
  /** A stateless aligned grid for nested content: the packages inside an order, the
      products inside a package. It carries a HEADER ROW and lines its cells up with
      each other (item 12) without registering a second DataTable instance. */
  function grid(cols, rows, o) {
    o = o || {};
    // Same floor as DS.subTable, and for the same reason: `minmax(0,1fr)` let grid delete
    // this grid's identity column outright. packageGrid below declares 1272px of fixed
    // columns against a ceiling of about 1292px, so the Package column - chevron, number,
    // thumbnails, GAASH number - had at most 20px on the widest monitor and none at all
    // below a ~1612px window. (This builder is a copy of DS.subTable; merging them is its
    // own task, so the fix has to be made in both places for now.)
    const tpl = cols.map((c) => (c.w ? c.w + "px" : `minmax(${c.min || 160}px,var(--ds-pu-flex,620px))`)).join(" ");
    const head = `<div class="ds-pu-sub-head" role="row">${cols.map((c) =>
      `<div class="ds-pu-th${c.align === "end" ? " ds-num" : ""}" role="columnheader">${esc(c.label || "")}</div>`).join("")}</div>`;
    // `o.expand(row)` is a row's OWN nested content — the products of a package — and it
    // belongs directly under that row. It used to be appended after the whole table, so
    // opening two packages of a twelve-package order left their products stacked at the
    // bottom, each under a heading naming the package you had to scroll back up to find.
    // `grid-column: 1 / -1` spans every track, so the nested grid lays out on its own terms.
    const body = rows.map((r) => {
      const cells = `<div class="ds-pu-sub-row" role="row">${cols.map((c) =>
        `<div class="ds-pu-td${c.align === "end" ? " ds-num" : ""}" role="cell">${c.render(r) || ""}</div>`).join("")}</div>`;
      const ex = o.expand ? (o.expand(r) || "") : "";
      return ex ? cells + `<div class="ds-pu-sub-exp">${ex}</div>` : cells;
    }).join("");
    return `<div class="ds-pu-sub" role="table" style="--ds-pu-cols:${tpl}"${o.label ? ` aria-label="${esc(o.label)}"` : ""}>${head}${body}</div>`;
  }

  /** The products of one package: an aligned grid with a header, so nested content
      no longer floats free of every column. */
  function productGrid(ctx, p, pk, pi) {
    const items = pk.items || [];
    const add = D.button({ label: "Add product", icon: "plus", size: "sm", variant: "ghost", onclick: `poAddItem('${esc(p.po_id)}',${pi})` });
    if (!items.length) return `<div class="ds-pu-empty">No products in this package yet. ${add}</div>`;
    const cols = [
      { key: "prod", label: "Product", render: ([it]) => productName(it) },
      { key: "cust", label: "Customer", w: 170, render: ([it]) => ((it.customer_name || "").trim()
        ? text(it.customer_name) : D.attention({ kind: "missing_name", title: "Not linked to a customer yet" })) },
      { key: "qty", label: "Qty", w: 56, align: "end", render: ([it]) => `<span class="ds-num">${esc(num(it.qty || 1))}</span>` },
      { key: "exc", label: "Exception", w: 130, render: ([it]) => ctx.excChip(it) || D.dash() },
      { key: "act", label: "", w: 44, render: ([, ii]) => D.button({ icon: "pencil", size: "sm", variant: "ghost", iconOnly: true, ariaLabel: "Edit product", title: "Edit product - link, ASIN, photo, notes", onclick: `itemEditOpen('${esc(p.po_id)}',${pi},${ii})` }) },
    ];
    return grid(cols, items.map((it, ii) => [it, ii]), { label: `Products in package ${pk.package_no}` })
      + `<div class="ds-pu-sub-foot">${add}</div>`;
  }

  /** The identity of a package: its number, its GAASH number, and what it holds.
      `o.disclosure` adds its own open control (the nested grid has no lead column of
      its own); `o.thumbs` shows what is in the box. On the flat Packages board both
      are off - the row already has a chevron, and the tracking number is the fact
      that column exists for, so it gets the width. */
  function pkgCell(ctx, p, pk, o) {
    o = o || {};
    const gwd = (pk.tracking_number || "").trim();
    return `<span class="ds-pu-id">${o.disclosure
      ? `<button type="button" class="ds-pu-disc" aria-expanded="${ctx.pkgOpen(p.po_id, pk.package_no) ? "true" : "false"}" aria-label="Show the products in package ${esc(pk.package_no)}" onclick="event.stopPropagation();DS.purchases.togglePkg('${esc(p.po_id)}',${esc(pk.package_no)})">${D.icon("chevron-right", { size: 13 })}</button>` : ""}
      <b>${esc(pk.package_no)}</b>${o.thumbs ? (W.poThumbStrip(pk.items) || "") : ""}
      ${gwd ? `<span class="ds-mono ds-truncate" title="${esc(gwd)}">${esc(gwd)}</span>${W.lxCopyRawBtn(gwd)}`
        : D.attention({ kind: "no_tracking", title: "No GAASH tracking number yet" })}
      ${W.pkgPhotoBadge(pk) || ""}</span>`;
  }

  /** The packages of one order: the same facts as the Packages board, in a grid with
      its own header, so nested content is readable on its own terms. */
  function packageGrid(ctx, p) {
    const pkgs = p.packages || [];
    const add = D.button({ label: "Add package", icon: "plus", size: "sm", variant: "ghost", onclick: `poAddPkg('${esc(p.po_id)}')` });
    if (!pkgs.length) return `<div class="ds-pu-empty">No packages yet. ${add}</div>`;
    const cols = [
      // min: the identity column of this grid. Its 11 fixed siblings declare 1272px against
      // a ceiling of ~1292px, so with a 0 floor grid deleted this column outright - chevron,
      // package number, thumbnails and all - on every screen. Measured natural width 363px.
      { key: "pkg", label: "Package", min: 240, render: ([pk]) => pkgCell(ctx, p, pk, { disclosure: true, thumbs: true }) },
      { key: "who", label: "Customer", w: 150, render: ([pk]) => text(W.pkgWho(pk)) },
      ctx.money ? { key: "est", label: "Est. cost", w: 108, align: "end", render: ([pk]) => est(W.pkgEstTotal(pk)) } : null,
      { key: "arrival", label: "Arrival", w: 104, render: ([pk]) => W.pkgDatePill(pk) || D.dash() },
      { key: "due", label: "Due", w: 106, render: ([pk, pi]) => W.poPkgDueCell(p, pk, pi) || D.dash() },
      { key: "customs", label: "Customs", w: 150, render: ([pk]) => W.pkgGaashPill(pk) || D.dash() },
      { key: "deadline", label: "Deadline", w: 112, render: ([pk, pi]) => W.pkgDeadlinePill(p, pk, pi) || D.dash() },
      { key: "docs", label: "Documents", w: 96, render: ([pk, pi]) => W.pkgDocsPill(p, pk, pi) || D.dash() },
      { key: "lastmile", label: "Last mile", w: 130, render: ([pk]) => W.pkgGerizimCell(pk, (pk.tracking_number || "").trim()) || D.dash() },
      { key: "rd", label: "RD number", w: 112, render: ([pk, pi]) => W.pkgRdCell(p, pk, pi) || D.dash() },
      { key: "status", label: "Status", w: 160, render: ([pk, pi]) => W.pkgCuSelect(p, pk, pi) },
      { key: "act", label: "", w: 44, render: ([pk, pi]) => D.menu({ items: pkgMenu(ctx, p, pk, pi), button: { icon: "ellipsis-horizontal", size: "sm", variant: "ghost", ariaLabel: "Package actions" } }) },
    ].filter(Boolean);
    const tuples = pkgs.map((pk, pi) => [pk, pi]);
    // Open a package and its products appear UNDER it. The heading that used to name
    // which package they belonged to is gone with the reason for it: the nesting says
    // so, and the nested grid still carries `Products in package N` as its aria-label.
    return grid(cols, tuples, {
      label: `Packages in ${p.po_id}`,
      expand: ([pk, pi]) => (ctx.pkgOpen(p.po_id, pk.package_no) ? productGrid(ctx, p, pk, pi) : ""),
    }) + `<div class="ds-pu-sub-foot">${add}</div>`;
  }

  function pkgMenu(ctx, p, pk, pi) {
    const gwd = (pk.tracking_number || "").trim();
    return [
      { label: "Edit package", icon: "pencil-square", onclick: `pkgEditOpen('${esc(p.po_id)}',${pi})` },
      { label: "Package details", icon: "arrows-pointing-out", onclick: `pkgInfoOpen('${esc(p.po_id)}',${pi})` },
      { label: "Notify customers", icon: "device-phone-mobile", onclick: `pkgNotifyOpen('${esc(p.po_id)}',${pi})` },
      { label: "Check shipping", icon: "truck", onclick: `trackPkg(this,'${esc(p.po_id)}',${pi})` },
      gwd ? { label: "Upload documents to GAASH", icon: "document-arrow-up", onclick: `gaashUploadOpen('${esc(p.po_id)}',${pi})` } : null,
      gwd && ctx.mailReady ? { label: "Chase via GAASH mail", icon: "envelope", onclick: `gmEnrollFrom(['${esc(gwd)}'])` } : null,
      ctx.flags.multilogin && p.profile_box ? { label: "Get tracking automatically", icon: "bolt", onclick: `azTrackFetch(this,'${esc(p.po_id)}',${pi})` } : null,
      { label: "Estimate cost", icon: "calculator", onclick: `pkgEstimateCost('${esc(p.po_id)}',${pi})` },
      { label: "Add product", icon: "plus", onclick: `poAddItem('${esc(p.po_id)}',${pi})` },
      { divider: true },
      { label: "Delete package", icon: "trash", danger: true, onclick: `poDelPkg('${esc(p.po_id)}',${pi})` },
    ].filter(Boolean);
  }

  function orderMenu(ctx, p) {
    return [
      { label: "Edit order", icon: "pencil-square", onclick: `poEditOpen('${esc(p.po_id)}')` },
      { label: "Open detail", icon: "arrows-pointing-out", onclick: `poOpenDetail('${esc(p.po_id)}')` },
      ctx.flags.screenshot ? { label: "Attach a screenshot", icon: "camera", onclick: `poUpload(this,'${esc(p.po_id)}')` } : null,
      ctx.flags.clickup ? { label: "Preview in ClickUp", icon: "eye", onclick: `clickupPO(this,'${esc(p.po_id)}',true)` } : null,
      ctx.flags.clickup ? { label: "Send to ClickUp", icon: "arrow-top-right-on-square", onclick: `clickupPO(this,'${esc(p.po_id)}',false)` } : null,
      { label: "Estimate every package", icon: "calculator", onclick: `poEstimateCost('${esc(p.po_id)}')` },
      { divider: true },
      { label: "Delete order", icon: "trash", danger: true, onclick: `poDelete('${esc(p.po_id)}')` },
    ].filter(Boolean);
  }

  // ---------------------------------------------------------------- boards
  const cfCols = (ctx) => ctx.cf().map((f) => ({
    key: "cf_" + f.key, label: f.name || f.key, w: 120, sortable: false,
    render: (r) => ctx.cfCell(Array.isArray(r) ? r[0] : r, f) || D.dash(),
  }));
  const actionsCol = (menu) => ({ key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false, menu });

  function ordersBoard(ctx, mount, list) {
    const cols = [
      { key: "po", label: "Purchase order", w: 190, pin: "start", locked: true, sortVal: (p) => p.po_id,
        render: (p) => `<span class="ds-pu-id"><b>${esc(p.po_id)}</b>${(p.amazon_order_number || "").trim()
          ? `<span class="ds-mono ds-muted" title="Amazon order ${esc(p.amazon_order_number)}">\u2026${esc(p.amazon_order_number.trim().slice(-4))}</span>${W.lxCopyRawBtn(p.amazon_order_number.trim())}` : ""}</span>` },
      { key: "oname", label: "Order name", w: 150, sortVal: (p) => (p.ship_to || "~").toLowerCase(), render: (p) => text(p.ship_to) },
      { key: "profile", label: ctx.boxTerm, w: 128, title: PROFILE_HELP, sortVal: (p) => (p.profile_box || "~").toLowerCase(), render: (p) => W.poProfileCell(p) },
      { key: "customers", label: "Customers", w: 170, sortVal: (p) => (W.poWho(p) || "~").toLowerCase(), render: (p) => text(W.poWho(p)) },
      { key: "items", label: "Items", w: 70, align: "end", sortVal: (p) => p.n_items || 0, render: (p) => `<span class="ds-num">${esc(num(p.n_items || 0))}</span>` },
      { key: "packages", label: "Packages", w: 84, align: "end", sortVal: (p) => (p.packages || []).length, render: (p) => `<span class="ds-num">${esc(num((p.packages || []).length))}</span>` },
      ctx.money ? { key: "paid", label: "Paid", w: 104, align: "end", sortVal: (p) => Number(p.total_usd) || 0,
        render: (p) => (p.total_usd != null ? `<b class="ds-num">${esc(money(p.total_usd))}</b>` : D.dash()) } : null,
      ctx.money ? { key: "est", label: "Est. cost", w: 110, align: "end", sortVal: (p) => poEst(p).sum, render: (p) => est(poEst(p)) } : null,
      { key: "attention", label: "Needs attention", w: 176, sortable: false, render: (p) => attention(ctx, p) || D.dash() },
      { key: "shipping", label: "Shipping", w: 170, sortable: false, render: (p) => W.poSummaryPill(p) || D.dash() },
      // A rollup of the last-mile courier across every package: useful, but not what
      // the board is scanned for, so it starts folded away behind the Columns button.
      { key: "lastmile", label: "Last mile", w: 140, sortable: false, defaultHidden: true,
        render: (p) => W.gzRollup((p.packages || []).map((pk) => (pk.tracking_number || "").trim()).filter(Boolean)) || D.dash() },
    ].filter(Boolean).concat(cfCols(ctx), [actionsCol((p) => orderMenu(ctx, p))]);

    const t = list.reduce((a, p) => {
      const e = poEst(p);
      a.paid += Number(p.total_usd) || 0; a.est += e.sum; a.items += p.n_items || 0; a.pkgs += (p.packages || []).length;
      return a;
    }, { paid: 0, est: 0, items: 0, pkgs: 0 });

    D.tableRender(mount, {
      id: "po_orders", ariaLabel: "Purchase orders", columns: cols, rows: list, rowKey: (p) => p.po_id,
      expandable: { render: (p) => packageGrid(ctx, p), open: (p) => ctx.isOpen(p) },
      onToggle: (key, open) => ctx.setOpen(key, open),
      empty: { title: "No purchase orders match this filter", text: "Clear the filters, or create the first one.", action: { label: "New purchase order", icon: "plus", onclick: "addPO()" } },
      footer: {
        po: `<b>${esc(num(list.length))} purchase orders</b>`,
        items: `<span class="ds-num">${esc(num(t.items))}</span>`,
        packages: `<span class="ds-num">${esc(num(t.pkgs))}</span>`,
        paid: ctx.money ? `<b class="ds-num">${esc(money(t.paid))}</b>` : "",
        est: ctx.money && t.est ? `<span class="ds-num ds-pu-est">${esc(money(t.est))}</span>` : "",
      },
    });
  }

  function packagesBoard(ctx, mount, list) {
    const rows = [];
    list.forEach((p) => (p.packages || []).forEach((pk, pi) => rows.push([p, pk, pi])));
    const cols = [
      { key: "pkg", label: "Package", w: 230, pin: "start", locked: true, sortVal: ([p, pk]) => `${p.po_id} ${String(pk.package_no).padStart(3, "0")}`, render: ([p, pk]) => pkgCell(ctx, p, pk) },
      { key: "order", label: "Order", w: 104, sortVal: ([p]) => p.po_id,
        render: ([p]) => D.button({ label: p.po_id, size: "sm", variant: "ghost", title: `Open ${p.po_id}`, onclick: `event.stopPropagation();poJumpOrder('${esc(p.po_id)}')` }) },
      { key: "oname", label: "Order name", defaultHidden: true, w: 140, sortVal: ([p]) => (p.ship_to || "~").toLowerCase(), render: ([p]) => text(p.ship_to) },
      { key: "profile", label: ctx.boxTerm, defaultHidden: true, w: 128, title: PROFILE_HELP, sortVal: ([p]) => (p.profile_box || "~").toLowerCase(), render: ([p]) => W.poProfileCell(p) },
      { key: "customer", label: "Customer", w: 150, sortVal: ([, pk]) => (W.pkgWho(pk) || "~").toLowerCase(), render: ([, pk]) => text(W.pkgWho(pk)) },
      ctx.money ? { key: "est", label: "Est. cost", w: 110, align: "end", sortVal: ([, pk]) => W.pkgEstTotal(pk).sum, render: ([, pk]) => est(W.pkgEstTotal(pk)) } : null,
      { key: "arrival", label: "Arrival", w: 106, sortVal: ([, pk]) => String(pk.arrival || "~"), render: ([, pk]) => W.pkgDatePill(pk) || D.dash() },
      { key: "due", label: "Due", w: 106, sortVal: ([, pk]) => { const d = W.poPkgDue(pk); return d ? Date.parse(d) || Infinity : Infinity; },
        render: ([p, pk, pi]) => W.poPkgDueCell(p, pk, pi) || D.dash() },
      { key: "attention", label: "Needs attention", w: 168, sortable: false, render: ([p, pk]) => attention(ctx, p, pk) || D.dash() },
      { key: "customs", label: "Customs", w: 150, sortable: false, render: ([, pk]) => W.pkgGaashPill(pk) || D.dash() },
      { key: "deadline", label: "Deadline", w: 112, sortable: false, render: ([p, pk, pi]) => W.pkgDeadlinePill(p, pk, pi) || D.dash() },
      { key: "docs", label: "Documents", w: 96, sortable: false, render: ([p, pk, pi]) => W.pkgDocsPill(p, pk, pi) || D.dash() },
      { key: "lastmile", label: "Last mile", defaultHidden: true, w: 130, sortable: false, render: ([, pk]) => W.pkgGerizimCell(pk, (pk.tracking_number || "").trim()) || D.dash() },
      { key: "idnum", label: "ID number", defaultHidden: true, w: 122, sortVal: ([, pk]) => W.pkgIdNumber(pk) || "~", render: ([, pk]) => W.idNumCell(W.pkgIdNumber(pk)) || D.dash() },
      { key: "rd", label: "RD number", defaultHidden: true, w: 112, sortVal: ([, pk]) => (pk.rd_number || "~").toLowerCase(), render: ([p, pk, pi]) => W.pkgRdCell(p, pk, pi) || D.dash() },
    ].filter(Boolean).concat(cfCols(ctx), [
      // Status is pinned: on this board it is the thing the owner scans for, and it
      // used to sit off the right-hand edge with its pills cut in half (item 8).
      { key: "status", label: "Status", w: 164, pin: "end", sortable: false, render: ([p, pk, pi]) => W.pkgCuSelect(p, pk, pi) },
      actionsCol(([p, pk, pi]) => pkgMenu(ctx, p, pk, pi)),
    ]);
    const totEst = rows.reduce((a, [, pk]) => a + W.pkgEstTotal(pk).sum, 0);
    const nItems = rows.reduce((a, [, pk]) => a + (pk.items || []).length, 0);
    D.tableRender(mount, {
      id: "po_packages", seedVersion: 1, ariaLabel: "Packages", columns: cols, rows,
      rowKey: ([p, pk]) => `${p.po_id}#${pk.package_no}`,
      // A flat board is for scanning: nothing is open until it is asked for.
      expandable: { render: ([p, pk, pi]) => productGrid(ctx, p, pk, pi), open: () => false },
      onRowClick: ([p, , pi]) => W.pkgInfoOpen(p.po_id, pi),
      empty: { title: "No packages match this filter" },
      footer: {
        pkg: `<b>${esc(num(rows.length))} packages, ${esc(num(nItems))} products</b>`,
        est: ctx.money && totEst ? `<span class="ds-num ds-pu-est">${esc(money(totEst))}</span>` : "",
      },
    });
  }

  function productRows(list) {
    const rows = [];
    list.forEach((p) => (p.packages || []).forEach((pk, pi) => (pk.items || []).forEach((it, ii) => rows.push([p, pk, pi, it, ii]))));
    return rows;
  }

  function productsBoard(ctx, mount, list) {
    const rows = productRows(list);
    const co = (it) => W.poOrderMap()[(it.customer_order_id || "").trim()];
    const cols = [
      { key: "product", label: "Product", w: 280, pin: "start", locked: true, sortVal: ([, , , it]) => (it.title || it.asin || "").toLowerCase(), render: ([, , , it]) => productName(it) },
      { key: "order", label: "Order", w: 104, sortVal: ([p]) => p.po_id,
        render: ([p]) => D.button({ label: p.po_id, size: "sm", variant: "ghost", title: `Open ${p.po_id}`, onclick: `event.stopPropagation();poJumpOrder('${esc(p.po_id)}')` }) },
      { key: "oname", label: "Order name", defaultHidden: true, w: 140, sortVal: ([p]) => (p.ship_to || "~").toLowerCase(), render: ([p]) => text(p.ship_to) },
      { key: "profile", label: ctx.boxTerm, defaultHidden: true, w: 128, title: PROFILE_HELP, sortVal: ([p]) => (p.profile_box || "~").toLowerCase(), render: ([p]) => W.poProfileCell(p) },
      { key: "package", label: "Package", w: 150, sortVal: ([, pk]) => pk.package_no, render: ([, pk]) => gwdCell(pk) },
      { key: "pkgstatus", label: "Package status", w: 156, sortVal: ([, pk]) => pk.otlobly_status || "~", render: ([, pk]) => ctx.pkgStatusPill(pk) || D.dash() },
      { key: "customer", label: "Customer", w: 150, sortVal: ([, , , it]) => (it.customer_name || "~").toLowerCase(),
        render: ([, , , it]) => ((it.customer_name || "").trim() ? text(it.customer_name) : D.attention({ kind: "missing_name", title: "Not linked to a customer yet" })) },
      ctx.money ? { key: "collect", label: "To collect", w: 118, align: "end", sortVal: ([, , , it]) => (co(it) || {}).amount_to_collect_usd || 0,
        render: ([, , , it]) => {
          const o = co(it);
          if (!o || o.amount_to_collect_usd == null) return D.dash();
          const left = Math.round(((o.amount_to_collect_usd || 0) - (o.deposit_usd || 0)) * 100) / 100;
          return `<b class="ds-num ds-pu-collect" title="${esc(o.order_id)}: quoted ${esc(money(o.amount_to_collect_usd))}, deposit ${esc(money(o.deposit_usd || 0))}, still owed ${esc(money(left))}">${esc(money(o.amount_to_collect_usd))}</b>`;
        } } : null,
      { key: "ostatus", label: "Order status", w: 134, sortVal: ([, , , it]) => (co(it) || {}).status || "~",
        render: ([, , , it]) => { const o = co(it); return o ? W.statusPill(o.status) : D.dash(); } },
      { key: "due", label: "Due", w: 106, sortVal: ([, pk]) => { const d = W.poPkgDue(pk); return d ? Date.parse(d) || Infinity : Infinity; },
        render: ([p, pk, pi]) => W.poPkgDueCell(p, pk, pi) || D.dash() },
      { key: "qty", label: "Qty", w: 60, align: "end", sortVal: ([, , , it]) => Number(it.qty) || 1, render: ([, , , it]) => `<span class="ds-num">${esc(num(it.qty || 1))}</span>` },
      { key: "idnum", label: "ID number", defaultHidden: true, w: 122, sortVal: ([, , , it]) => (co(it) || {}).id_number || "~", render: ([, , , it]) => W.idNumCell((co(it) || {}).id_number) || D.dash() },
    ].filter(Boolean).concat(cfCols(ctx), [
      { key: "exception", label: "Exception", w: 136, pin: "end", sortable: false, render: ([, , , it]) => ctx.excChip(it) || D.dash() },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        quick: ([p, , pi, , ii]) => [{ icon: "pencil", ariaLabel: "Edit product", title: "Edit product", onclick: `itemEditOpen('${esc(p.po_id)}',${pi},${ii})` }] },
    ]);
    const totQty = rows.reduce((a, [, , , it]) => a + (Number(it.qty) || 1), 0);
    D.tableRender(mount, {
      id: "po_products", seedVersion: 1, ariaLabel: "Products", columns: cols, rows,
      rowKey: ([p, pk, , , ii]) => `${p.po_id}#${pk.package_no}#${ii}`,
      onRowClick: ([p, , pi]) => W.pkgInfoOpen(p.po_id, pi),
      empty: { title: "No products match this filter" },
      footer: { product: `<b>${esc(num(rows.length))} products</b>`, qty: `<span class="ds-num">${esc(num(totQty))}</span>` },
    });
  }

  // A sentinel that no real customer name can equal. It used to be a literal NUL
  // ("\x00none"), which made `file` report this whole module as binary data and made
  // grep/ripgrep skip it silently - three searches of this file returned nothing at
  // all before anyone noticed the file was never being read.
  const NO_CUSTOMER = "\u0001none";

  /** One row per customer; open it to see their products, kept separate per order. */
  function customersBoard(ctx, mount, list) {
    const groups = new Map();
    productRows(list).forEach((t) => {
      const name = (t[3].customer_name || "").trim() || NO_CUSTOMER;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(t);
    });
    const rows = [...groups.entries()].map(([name, tuples]) => {
      const ids = [...new Set(tuples.map(([, , , it]) => (it.customer_order_id || "").trim()).filter(Boolean))];
      const orders = ids.map((id) => W.poOrderMap()[id]).filter(Boolean);
      return {
        name, tuples, orders, pos: [...new Set(tuples.map(([p]) => p.po_id))],
        qty: tuples.reduce((a, [, , , it]) => a + (Number(it.qty) || 1), 0),
        est: tuples.reduce((a, [, , , it]) => a + (it.est_cost_usd != null ? it.est_cost_usd * (it.qty || 1) : 0), 0),
        quoted: orders.reduce((a, o) => a + (o.amount_to_collect_usd || 0), 0),
        deposit: orders.reduce((a, o) => a + (o.deposit_usd || 0), 0),
        info: name === NO_CUSTOMER ? null : W.poCustInfo(name, orders.map((o) => ({ id: o.order_id, o }))),
      };
    }).sort((a, b) => (a.name === NO_CUSTOMER ? 1 : b.name === NO_CUSTOMER ? -1 : a.name.localeCompare(b.name, undefined, { numeric: true })));

    const cols = [
      { key: "customer", label: "Customer", w: 230, pin: "start", locked: true, sortVal: (r) => (r.name === NO_CUSTOMER ? "~~~" : r.name.toLowerCase()),
        render: (r) => (r.name === NO_CUSTOMER
          ? D.attention({ kind: "missing_name", detail: "no customer", title: "These products are not linked to a customer yet" })
          : `<span class="ds-pu-id">${D.avatar({ name: r.name })}${text(r.name)}</span>`) },
      { key: "products", label: "Products", w: 92, align: "end", sortVal: (r) => r.tuples.length, render: (r) => `<span class="ds-num">${esc(num(r.tuples.length))}</span>` },
      { key: "qty", label: "Qty", w: 60, align: "end", sortVal: (r) => r.qty, render: (r) => `<span class="ds-num">${esc(num(r.qty))}</span>` },
      { key: "orders", label: "Orders", w: 84, align: "end", sortVal: (r) => r.pos.length, render: (r) => `<span class="ds-num">${esc(num(r.pos.length))}</span>` },
      ctx.money ? { key: "quoted", label: "Quoted", w: 112, align: "end", sortVal: (r) => r.quoted,
        render: (r) => (r.quoted ? `<b class="ds-num ds-pu-collect" title="What this customer was quoted, across their orders">${esc(money(r.quoted))}</b>` : D.dash()) } : null,
      ctx.money ? { key: "left", label: "Still owed", w: 112, align: "end", sortVal: (r) => r.quoted - r.deposit,
        render: (r) => (r.quoted ? `<span class="ds-num" title="Quoted ${esc(money(r.quoted))} less deposits ${esc(money(r.deposit))}">${esc(money(Math.round((r.quoted - r.deposit) * 100) / 100))}</span>` : D.dash()) } : null,
      ctx.money ? { key: "est", label: "Est. cost", w: 110, align: "end", sortVal: (r) => r.est,
        render: (r) => (r.est ? `<span class="ds-num ds-pu-est">${esc(money(Math.round(r.est * 100) / 100))}</span>` : D.dash()) } : null,
      { key: "phone", label: "Phone", w: 154, sortable: false,
        render: (r) => (r.info && r.info.phone
          ? (r.info.wa ? `<a class="ds-mono" href="https://wa.me/${esc(r.info.wa)}" target="_blank" rel="noopener" title="Message on WhatsApp">${esc(r.info.phone)}</a>` : `<span class="ds-mono">${esc(r.info.phone)}</span>`)
          : D.dash()) },
      { key: "city", label: "City", w: 130, sortVal: (r) => (r.info || {}).city || "~", render: (r) => text((r.info || {}).city) },
      { key: "idnum", label: "ID number", defaultHidden: true, w: 122, sortVal: (r) => (r.info || {}).id_number || "~", render: (r) => W.idNumCell((r.info || {}).id_number) || D.dash() },
      { key: "ostatus", label: "Order status", w: 150, pin: "end", sortable: false,
        render: (r) => (r.orders.length === 1 ? W.statusPill(r.orders[0].status)
          : r.orders.length > 1 ? `<span class="ds-muted">${esc(num(r.orders.length))} orders</span>` : D.dash()) },
    ].filter(Boolean);

    D.tableRender(mount, {
      id: "po_customers", seedVersion: 1, ariaLabel: "Customers", columns: cols, rows, rowKey: (r) => r.name,
      expandable: { render: (r) => customerDetail(ctx, r), open: (r) => !ctx.custCollapsed(r.name) },
      onToggle: (key) => ctx.custToggle(key),
      empty: { title: "No customers match this filter" },
      footer: {
        customer: `<b>${esc(num(rows.length))} customers</b>`,
        products: `<span class="ds-num">${esc(num(rows.reduce((a, r) => a + r.tuples.length, 0)))}</span>`,
        qty: `<span class="ds-num">${esc(num(rows.reduce((a, r) => a + r.qty, 0)))}</span>`,
      },
    });
  }

  function customerDetail(ctx, r) {
    const facts = [];
    if (r.info && r.info.address) facts.push(["Address", text(r.info.address)]);
    if (r.deposit) facts.push(["Deposit paid", esc(money(Math.round(r.deposit * 100) / 100))]);
    if (r.orders.length) facts.push(["Customer orders", r.orders.map((o) => esc(o.order_id)).join(", ")]);
    const strip = facts.length
      ? `<div class="ds-pu-facts">${facts.map(([k, v]) => `<span class="ds-kv"><span class="ds-muted">${esc(k)}</span><span>${v}</span></span>`).join("")}</div>` : "";
    // One block per purchase order: a customer's products from separate orders stay
    // separate - merging them would hide which Amazon order a piece is coming on.
    const byPo = new Map();
    r.tuples.forEach((t) => { if (!byPo.has(t[0].po_id)) byPo.set(t[0].po_id, []); byPo.get(t[0].po_id).push(t); });
    const cols = [
      { key: "prod", label: "Product", render: ([, , , it]) => productName(it) },
      { key: "pkg", label: "Package", w: 150, render: ([, pk]) => gwdCell(pk) },
      { key: "pkgstatus", label: "Package status", w: 156, render: ([, pk]) => ctx.pkgStatusPill(pk) || D.dash() },
      { key: "qty", label: "Qty", w: 56, align: "end", render: ([, , , it]) => `<span class="ds-num">${esc(num(it.qty || 1))}</span>` },
      { key: "exc", label: "Exception", w: 130, render: ([, , , it]) => ctx.excChip(it) || D.dash() },
      { key: "act", label: "", w: 44, render: ([p, , pi, , ii]) => D.button({ icon: "pencil", size: "sm", variant: "ghost", iconOnly: true, ariaLabel: "Edit product", onclick: `itemEditOpen('${esc(p.po_id)}',${pi},${ii})` }) },
    ];
    const who = r.name === NO_CUSTOMER ? "Unlinked" : r.name;
    return strip + [...byPo.entries()].sort().map(([po, tuples]) =>
      `<div class="ds-pu-pkg-open"><h4>${esc(po)}${D.button({ label: "Open", size: "sm", variant: "ghost", icon: "arrow-right", onclick: `poJumpOrder('${esc(po)}')` })}</h4>${grid(cols, tuples, { label: `${who} products in ${po}` })}</div>`).join("");
  }

  // ---------------------------------------------------------------- public
  P.togglePkg = (poId, no) => { if (W.poToggle) W.poToggle(W.poKey(poId, Number(no))); };

  /** Draw one board into `mount`. `ctx` is the bridge to the app (see index.html). */
  P.board = (mount, list, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    if (ctx.board === "packages") return packagesBoard(ctx, el, list);
    if (ctx.board === "products") return productsBoard(ctx, el, list);
    if (ctx.board === "customers") return customersBoard(ctx, el, list);
    return ordersBoard(ctx, el, list);
  };

  P.BOARDS = [
    { key: "orders", label: "Orders" }, { key: "packages", label: "Packages" },
    { key: "products", label: "Products" }, { key: "customers", label: "Customers" },
  ];

  /** The page header and the filter bar. Rendered on every paint into `host`. */
  P.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    const stats = [{ label: "Purchase orders", value: num(s.orders) }, { label: "Packages", value: num(s.packages) }];
    if (s.late) stats.push({ label: "Late", value: num(s.late), tone: "danger" });
    if (s.noTracking) stats.push({ label: "No tracking number", value: num(s.noTracking), tone: "warning" });
    if (ctx.money) stats.push({ label: "Paid", value: money(s.paid) }, { label: "Est. cost", value: money(s.est) });
    const header = D.pageHeader({
      crumbs: [{ label: "Fulfillment" }, { label: "Purchase orders" }],
      title: "Purchase orders", stats,
      primary: { label: "New purchase order", icon: "plus", onclick: "addPO()" },
      secondary: [{ label: "Register at Gerizim", icon: "map-pin", title: "Register several parcels at Gerizim at once", onclick: "gzBulkOpen()" }],
      overflow: [
        { head: "Sync and maintenance" },
        { label: "Check all shipping", icon: "truck", title: "Re-check every GAASH number, skipping what you already received or sent", onclick: "poCheckAllShipping(this)" },
        { label: "Estimate all costs", icon: "calculator", title: "Price every product that has no estimate yet", onclick: "poEstimateAll(this)" },
        { label: "Import from ClickUp", icon: "arrow-down-tray", onclick: "importClickup(this)" },
        // Products is one row per product with nothing to open; offering these there
        // is a control that does nothing when you press it.
        ...(ctx.canExpand === false ? [] : [
          { divider: true },
          { label: "Expand every row", icon: "chevron-double-right", onclick: "poViewAll(true)" },
          { label: "Collapse every row", icon: "chevron-double-left", onclick: "poViewAll(false)" },
        ]),
      ],
    });
    const bar = D.filterBar({
      search: { id: "poSearch", value: ctx.query, placeholder: "Search PO, Amazon number, customer, product, GWD", oninput: "poSearchInput(this.value)" },
      views: P.BOARDS.map((b) => ({ key: b.key, label: b.label, active: ctx.board === b.key })),
      onView: "poSetView(KEY)",
      chips: ctx.quick.map((q) => ({ label: q.label, value: q.count, active: q.active, onclick: `poQuickFilter('${q.key}')` })),
      add: { onclick: "poFilterAdd()", label: "Add filter" },
      clear: { show: !!ctx.hasFilters, onclick: "poFilterClear()" },
    });
    D.paintHost(el, header + bar);   // keeps the caret in the search box (DS.paintHost)
  };
})();
