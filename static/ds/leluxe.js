/* ============================================================================
   Otlobly design system — Leluxe boards (static/ds/leluxe.js)
   Batch F of the UX restructure: the ⌚ Leluxe ORDERS (F1), PRODUCTS (F2) and
   PACKAGES (F3) boards leave the LXT engine (LX_TABLES / LXT_COLS / LXT_CLS
   entries "", "p" and "k") for DS.tableRender — the same move Purchases made
   in Phase 3 and Orders/Customers in Batch B. Bulk search ("bs") is the last
   LXT table on this page.

   What this file owns: `DS.lxOrders` (one row per ORDER, its parcels and their
   products in the expansion), `DS.lxProducts` (every product standalone, with
   grouping and same-order tie runs) and `DS.lxPackages` (one row per physical
   parcel, with its estimated value and the customs pills). Everything a cell shows is
   still built by the app's own functions on `window` — statuses stay editable,
   the GAASH pills stay live, the ⋯ menus keep every action — so this is a
   change of TABLE, not of behaviour.

   Three levels, two grids (the lesson of Phase 3): the legacy board pushed
   order rows, package heads and product rows through ONE column grid, which is
   why a product's "tracking" cell had to be blanked when its package carried
   the number. Here the order row lives on the DataTable's grid and each
   parcel's products live in their own aligned sub-grid, so a product can show
   its own facts without pretending to be an order.

   State that must survive a re-render stays in the app (`ctx`): which orders
   are open (LX_OPEN), which parcels are open (LX_PCOL), and the sort. This
   file holds no state of its own.
   ========================================================================== */
(function () {
  "use strict";
  const DS = (window.DS = window.DS || {});
  const W = window;
  const esc = DS.esc;
  const LXO = (DS.lxOrders = {});

  const j = (...x) => x.filter(Boolean).join("");
  const money = (v) => `₪${Number(v).toLocaleString()}`;
  // The app's own escaper for anything going into an inline onclick — poEsc is
  // what every legacy row uses, so a quote in an order name behaves the same.
  const q = (s) => W.poEsc(String(s == null ? "" : s));

  /* ---------------------------------------------------------------- columns
     Mirrors LXT_COLS[""] one for one — same keys, same labels, same widths, in
     the same order — because a migration that quietly drops a column is the
     one failure this program has already paid for twice (Batch B → B3, and
     the owner's "do not remove anything"). Nothing here is defaultHidden. */
  const COLS = (ctx) => [
    { key: "name", label: "الطلب · order", w: 330, min: 200, pin: "start", locked: true, sortable: true,
      sortVal: (o) => String(o.name || "").toLowerCase(),
      render: (o) => nameCell(ctx, o) },
    { key: "profile", label: "الحساب · profile", w: 100, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "profile"),
      render: (o) => W.lxProfileCell(W.lxOrderAcct(o)) },
    { key: "status", label: "الحالة · status", w: 124, min: 84, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "status"),
      render: (o) => headStatus(o) },
    { key: "total", label: "الإجمالي · total", w: 96, align: "end", sortable: true,
      sortVal: (o) => Number(W.lxF(o, "total amount")) || 0,
      render: (o) => { const a = W.lxF(o, "total amount");
        return a == null || a === "" ? "" : `<b title="Total Amount (₪)">${esc(money(a))}</b>`; } },
    { key: "tracking", label: "التتبع · tracking", w: 148, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "tracking"),
      render: (o) => headTracking(o) },
    { key: "gash", label: "الجمارك · gash", w: 180, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "gash"),
      render: (o) => { const tr = W.lxTrackedRow(o);
        return W.lxMergedStatusPill(tr) || W.lxNoDataPill(tr) || ""; } },
    { key: "deadline", label: "الموعد · deadline", w: 140, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "deadline"),
      render: (o) => deadlineCell(o) },
    { key: "created", label: "أُنشئ · created", w: 96, sortable: true,
      sortVal: (o) => Number(o.date_created) || 0,
      render: (o) => o.date_created
        ? `<span class="ds-muted" title="date created (set automatically on the day the order is made)">⊕ ${esc(W.lxDate(o.date_created))}</span>` : "" },
    { key: "due", label: "الاستحقاق · due", w: 106, sortable: true,
      sortVal: (o) => Number(o.due_date) || Infinity,
      render: (o) => { const tr = W.lxTrackedRow(o);
        return (tr && W.lxGzDone(tr)) ? "" : (W.lxDueChip(o.due_date, W.lxIsDone(o)) || ""); } },
    { key: "menu", label: "", w: 30, locked: true, pin: "end",
      render: (o) => orderMenu(o) },
  ];

  /* ------------------------------------------------------------ order cells */
  function nameCell(ctx, o) {
    return `<span class="ds-lx-name">`
      + j(W.lxDot(o),
          W.lxConfPill(o.sync_state === "conflict" ? o
            : [...(o.packages || []), ...W.lxItemsOf(o)].find((r) => r.sync_state === "conflict")),
          // lxShortName returns escaped HTML (a muted # + the last five digits) —
          // escaping it again printed the markup on every row
          `<b class="ds-mono ds-truncate" title="${esc(o.name || "")}">${W.lxShortName(o.name)}</b>`,
          W.lxCopyBtn(o.name),
          W.lxThumbs(W.lxItemsOf(o)),
          W.gzRollup(W.lxOrderGwds(o)))
      + `</span>`;
  }

  // DISPLAY ONLY, exactly as the legacy header was: the products' shared status
  // when they agree, "mixed" when they do not, the order's own status when it
  // has no products. It never edits and never bulk-edits — the editable status
  // is the one on each product, inside the expansion.
  function headStatus(o) {
    const its = W.lxItemsOf(o);
    const u = [...new Set(its.map((i) => i.status).filter(Boolean))];
    if (!its.length) return W.lxStatusPill(o.status);
    return u.length === 1 ? W.lxStatusPill(u[0])
      : W.tonePill("gray", "mixed", "products have different statuses — expand to see each");
  }

  // The order's own number and its packages' numbers count too — an order-level
  // tracking number used to save fine and then show nowhere.
  function trackingNumbers(o) {
    const own = String((o.data && o.data.tracking_number) || W.lxF(o, "tracking number") || "").trim();
    const pkgs = (o.packages || []).map((pk) =>
      String((pk.data && pk.data.tracking_number) || W.lxF(pk, "tracking number") || "").trim());
    const items = W.lxItemsOf(o).map((i) =>
      String(W.lxF(i, "tracking number") || (i.data && i.data.tracking_number) || "").trim());
    return { own, list: [...new Set([...items, ...pkgs, own].filter(Boolean))] };
  }

  function headTracking(o) {
    const { list } = trackingNumbers(o);
    if (list.length === 1) return W.poTn4(list[0], "var(--ds-t-xs)") + W.lxCopyRawBtn(list[0]);
    return list.length > 1
      ? `<span class="ds-muted" title="products have different tracking numbers">📦 multiple tracking</span>`
      : `<span class="ds-muted" title="no GAASH tracking number yet">📦 no tracking</span>`;
  }

  function deadlineCell(o) {
    const tr = W.lxTrackedRow(o);
    const gashDate = W.lxF(o, "gash date") || (tr && W.lxF(tr, "gash date"));
    const delivered = tr && W.lxGzDone(tr);
    return j(tr ? W.lxDeadlinePill(tr, true) + W.lxDocsPill(tr) : "",
      gashDate && !delivered ? `<span title="gash date">${W.lxDueChip(gashDate, W.lxIsDone(o))}</span>` : "");
  }

  function orderMenu(o) {
    const { list } = trackingNumbers(o);
    return W.popMenu([
      ["✏️ تعديل الطلب · Edit order", `lxOpenEditor('order',${o.id},null)`],
      ["🚚 رقم التتبع للطلب · Set tracking number", `lxOrderSetTracking(${o.id})`],
      list.length === 1 ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(list[0])}')`] : null,
      list.length ? ["🚚 تسجيل في جيرزيم · Register at Gerizim", `lxGzFor('order',${o.id})`] : null,
      (o.packages || []).length ? ["📦⤴ نظّم الطرود في ClickUp · Organize in ClickUp", `lxAz2Organize(${o.id})`] : null,
      ["＋ إضافة طرد · Add package", `lxOpenEditor('package',null,${o.id})`],
      ["＋ إضافة منتج · Add product", `lxOpenEditor('item',null,${o.id})`],
      ["🗑 إخفاء · hide", `lxDelete(${o.id})`, true],
    ]);
  }

  /* ------------------------------------------------------- the expansion
     One block per PHYSICAL PARCEL — the same lxPkgRows model the 📦 Packages
     view uses, so a real 📦 subtask and the loose products carrying its GWD
     are one row, never twins. */
  // Widths MEASURED in this nested grid (90th-percentile natural content width across 30
  // expanded parcels), not copied from the full-width Products board - that copy is what
  // Batch G got wrong: it added 210px of fixed columns to a box that never had the room,
  // and the product column, the only flexible one, absorbed all of it and vanished.
  // The measurement also showed Batch G had over-declared rd by 75px and status by 38.
  // `min` on the product column is the floor it can never fall below; past that the
  // sub-table scrolls (ds.css .ds-pu-sub is overflow-x:auto) rather than dropping a column.
  const PROD_COLS = [
    { key: "product", label: "المنتج · product", min: 220 },
    { key: "profile", label: "الحساب · profile", w: 106 },
    { key: "status", label: "الحالة · status", w: 120 },
    { key: "qty", label: "الكمية · qty", w: 80, align: "end" },
    { key: "tracking", label: "التتبع · tracking", w: 146 },
    { key: "gash", label: "الجمارك · gash", w: 164 },
    { key: "rd", label: "RD", w: 88 },
    { key: "due", label: "الاستحقاق · due", w: 110 },
    { key: "menu", label: "", w: 44 },
  ];

  // `pkgTn` is the parcel's number: a product that has none of its own inherits
  // it MUTED rather than claiming "no tracking" under a tracked parcel.
  function productRow(it, pkgTn, ordTn) {
    const tn = String(W.lxF(it, "tracking number") || (it.data && it.data.tracking_number) || "").trim();
    const gwd = tn || pkgTn || "";
    const qty = W.lxF(it, "quantity ordered");
    return {
      product: `<span class="ds-lx-name">` + j(DS.thumb(it, { src: W.lxAmz, label: (x) => x.name || "", emptyLabel: "no image yet", add: true }),
        `<span class="ds-truncate" title="${esc(it.name || "")}">${esc(it.name || "")}</span>`,
        `<button class="lx-copy" title="copy the full product title" data-t="${esc(it.name || "")}" onclick="event.stopPropagation();lxCopyTitle(this)">📋</button>`) + `</span>`,
      profile: W.lxProfileCell(W.lxAcctChain(it, null)),
      status: j(W.lxDot(it), W.lxConfPill(it), W.lxStatusSelect(it)),
      qty: qty == null || qty === "" ? "" : `<span class="ds-muted" title="Quantity ordered">×${esc(qty)}</span>`,
      tracking: tn ? W.poTn4(tn, "var(--ds-t-xs)") + W.lxCopyRawBtn(tn)
        : pkgTn ? `<span style="opacity:.7" title="من الطرد · from the package">${W.poTn4(pkgTn, "var(--ds-t-xs)")}</span>${W.lxCopyRawBtn(pkgTn)}`
        : `<span class="ds-muted" title="no GAASH tracking number yet">📦 —</span>`,
      gash: W.lxCfPill("gash status", W.lxF(it, "gash status")) || "",
      rd: W.lxCfPill("rd status", W.lxF(it, "rd status")) || "",
      due: W.lxGzDone(it) ? "" : (W.lxDueChip(it.due_date, W.lxIsDone(it)) || ""),
      // `gwd` is the product's own number, else the parcel's: the legacy virtual
      // parcel gave every product inside it the customs actions, and a product
      // that inherits its parcel's GWD can be papered exactly the same way.
      menu: W.popMenu([
        ["✏️ تعديل المنتج · Edit product", `lxOpenEditor('item',${it.id},null)`],
        ["↔️ نقل إلى طرد · Move to package", `lxMovePrompt(${it.id})`],
        ["🚚 تعيين رقم التتبع · Set tracking", `lxTrackingPrompt([${it.id}],'${q(tn)}','${q(pkgTn || ordTn || "")}')`],
        gwd ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(gwd)}')`] : null,
        gwd ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(gwd)}')`] : null,
        gwd ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(gwd)}')`] : null,
        ["🗑 إخفاء · hide", `lxDelete(${it.id})`, true],
      ]),
      _click: `lxInfoOpen('item',${it.id})`,
    };
  }

  function productGrid(items, pkgTn, ordTn, addTo) {
    if (!items.length && !addTo) return `<div class="ds-muted ds-lx-none">no products yet</div>`;
    const rows = items.map((it) => productRow(it, pkgTn, ordTn));
    const cols = PROD_COLS.map((c) => Object.assign({}, c, { render: (r) => r[c.key] }));
    return DS.subTable(cols, rows, "products")
      + (addTo ? `<div class="ds-lx-add">${DS.button({ label: "＋ Add product", size: "sm",
          onclick: `lxOpenEditor('item',null,${addTo})` })}</div>` : "");
  }

  // Header line for one parcel: its GWD, count, thumbs and the pills that
  // belong to the PARCEL (GAASH stage, deadline, documents), plus its menu.
  function parcelHead(ctx, o, p) {
    const pk = p.pk, tn = p.tn || (pk ? W.lxPkgTn(pk) : "");
    const items = p.items || [];
    const en = pk && W.lxEnriched(pk) ? pk : (items.find(W.lxEnriched) || pk || null);
    const gashStatus = W.lxGashRollup(pk ? [pk, ...items] : items);
    const gashDate = (pk && W.lxF(pk, "gash date")) || items.map((it) => W.lxF(it, "gash date")).find(Boolean);
    const key = pk ? String(pk.id) : `v${o.id}:${tn}`;   // same key the legacy lxVPkgHtml used
    const open = ctx.pkgOpen(pk ? pk.id : key);
    const ids = items.map((it) => it.id).join(",");
    const ordTn = trackingNumbers(o).own;
    const menu = pk ? W.popMenu([
        ["✏️ تعديل الطرد · Edit package", `lxOpenEditor('package',${pk.id},${o.id})`],
        ["🚚 تعيين رقم التتبع · Set tracking", `lxTrackingPrompt([${pk.id}],'${q(tn)}','')`],
        tn ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(tn)}')`] : null,
        tn ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(tn)}')`] : null,
        tn ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(tn)}')`] : null,
        tn ? ["🚚 تسجيل في جيرزيم · Register at Gerizim", `lxGzFor('package',${pk.id})`] : null,
        ["＋ إضافة منتج · Add product", `lxOpenEditor('item',null,${pk.id})`],
        ["🗑 إخفاء · hide", `lxDelete(${pk.id})`, true],
      ]) : W.popMenu([
        [`🚚 تعيين رقم التتبع للكل (${items.length}) · Set tracking for all`,
          `lxTrackingPrompt([${ids}],'${q(tn)}','${q(ordTn)}')`],
        tn ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(tn)}')`] : null,
        tn ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(tn)}')`] : null,
        tn ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(tn)}')`] : null,
      ]);
    const toggle = pk ? `lxPkgToggle(${pk.id})` : `lxVPkgToggle('${q(key)}')`;
    const title = pk
      ? `onclick="if(event.target.closest('select,.pop,.caret,button,a,input,label,img'))return;lxInfoOpen('package',${pk.id})" style="cursor:pointer"`
      : "";
    return `<div class="ds-lx-parcel-head" ${title}>
      <button type="button" class="ds-exp-btn" aria-expanded="${open ? "true" : "false"}"
        aria-label="${open ? "Collapse" : "Expand"} parcel" onclick="event.stopPropagation();${toggle}">${DS.icon("chevron-right", { size: 14 })}</button>
      ${pk ? j(W.lxDot(pk), W.lxConfPill(pk)) : ""}
      <b class="ds-lx-parcel-tn">📦 ${tn ? esc(tn) : "no tracking"}</b>${tn ? W.lxCopyRawBtn(tn) : ""}
      <span class="ds-muted">${esc(W.lxCountLbl(items))}</span>
      ${W.lxThumbs(items)}
      ${!tn && items.length ? `<button class="minibtn" title="ضع رقم تتبع واحد لكل هذه المنتجات دفعة واحدة · one tracking number for all these products at once" onclick="event.stopPropagation();lxTrackingPrompt([${ids}],'','${q(ordTn)}')">🚚 set tracking</button>` : ""}
      <span class="ds-lx-parcel-pills">
        ${en ? W.lxGashCell(en, gashStatus, tn) : ""}
        ${en ? W.lxDeadlinePill(en) + W.lxDocsPill(en) : ""}
        ${gashDate && !(en && W.lxGzDone(en)) ? `<span title="gash date">${W.lxDueChip(gashDate, true)}</span>` : ""}
      </span>
      ${menu}
    </div>`;
  }

  function expansion(ctx, o) {
    const parcels = W.lxPkgRows(o);
    const ordTn = trackingNumbers(o).own;
    const body = parcels.map((p) => {
      const key = p.pk ? p.pk.id : `v${o.id}:${p.tn || ""}`;
      const open = p.flat ? true : ctx.pkgOpen(key);
      return `<div class="ds-lx-parcel${open ? " is-open" : ""}">`
        + (p.flat
            ? `<div class="ds-lx-parcel-head">
                 <span class="ds-muted" title="no GAASH tracking number yet">📦 no tracking</span>
                 ${W.lxThumbs(p.items || [], 4)}
                 <span class="ds-muted">${esc(W.lxCountLbl(p.items))}</span>
                 ${p.items.length ? `<button class="minibtn" title="ضع رقم تتبع واحد لكل هذه المنتجات دفعة واحدة · one tracking number for all these products at once" onclick="lxTrackingPrompt([${p.items.map((i) => i.id).join(",")}],'','${q(ordTn)}')">🚚 set tracking</button>` : ""}
               </div>`
            : parcelHead(ctx, o, p))
        + (open ? productGrid(p.items || [], p.tn || (p.pk ? W.lxPkgTn(p.pk) : ""), ordTn, p.pk ? p.pk.id : 0) : "")
        + `</div>`;
    }).join("");
    return `<div class="ds-lx-exp">${body}
      <div class="ds-lx-exp-foot">
        ${DS.button({ label: "＋ Add package", size: "sm", onclick: `lxOpenEditor('package',null,${o.id})` })}
        <span class="ds-spacer"></span>
        ${DS.button({ label: "🗑 hide", size: "sm", variant: "danger",
          title: "hide from this page only — the ClickUp task is NOT deleted", onclick: `lxDelete(${o.id})` })}
      </div></div>`;
  }

  /* -------------------------------------------------------------- the board
     ctx = { orders, orphans, isOpen(id), setOpen(id, open), pkgOpen(id),
             sort:{key,dir}|null, onSort(key,dir), empty:{title,text} }        */
  LXO.render = function (el, ctx) {
    const orders = ctx.orders || [];
    const totalAmt = orders.reduce((a, o) => {
      const v = Number(W.lxF(o, "total amount")); return a + (isNaN(v) ? 0 : v); }, 0);
    const totalItems = orders.reduce((a, o) => a + W.lxItemsOf(o).length, 0);
    DS.tableRender(el, {
      id: "lxo",
      columns: COLS(ctx),
      rows: orders,
      rowKey: (o) => String(o.id),
      rowTitle: () => "انقر لعرض كل التفاصيل · click for full order details",
      onRowClick: (o) => W.lxInfoOpen("order", o.id),
      expandable: { render: (o) => expansion(ctx, o), open: (o) => ctx.isOpen(o.id) },
      onToggle: (key, open) => ctx.setOpen(key, open),
      sort: ctx.sort || null,
      onSort: ctx.onSort || null,
      empty: ctx.empty || { title: "No orders match the filter" },
      footer: orders.length ? {
        name: `<b>Σ ${esc(String(orders.length))} طلب · ${esc(String(totalItems))} منتج</b>`,
        total: `<b title="مجموع Total Amount لكل الطلبات المعروضة · total of all shown orders">${esc(money(Math.round(totalAmt)))}</b>`,
      } : null,
    });
  };

  /* ========================================================================
     Batch F2 — the PRODUCTS board (LXT table "p").
     Every product standalone, with its parent order named beside it. Two
     things here are not in any other DS board and both are kept:

     · SAME-ORDER TIE RUNS. Consecutive rows sharing a parent order render as
       one visual run — the order pill and ×N on the first row, "└ نفس الطلب"
       on the rest. Adjacency depends on the FINAL row order, so the app sorts
       (LXT_SORT["p"], the preference the board already had) and this file is
       handed the rows in the order they will appear. That is why `onSort` is
       passed: with it the DataTable renders the given order instead of
       sorting behind our back.
     · GROUPING by any field, with collapsible sections. A DataTable has no
       group rows and two tables cannot share an id, so a grouped view is a
       table PER SECTION. They would drift apart on width/hide/order, so the
       layout is kept in one canonical key and copied into each section's key
       before it renders — resize a column in one section and every section
       follows.
     ==================================================================== */
  const PCOLS = () => [
    { key: "product", label: "المنتج · product", w: 330, min: 200, pin: "start", locked: true, sortable: true,
      // the full title in a flex row (.ds-lx-name) so it ellipsizes at the cell's edge
      // and the copy button stays in view - the old three-word cut showed
      // "5 Laurel Tab…" in a column with 200px to spare; a bare full title pushed the
      // copy button out of the cell instead
      render: (r) => `<span class="ds-lx-name">` + j(DS.thumb(r.it, { src: W.lxAmz, label: (x) => x.name || "", emptyLabel: "no image yet", add: true }),
        `<span class="ds-truncate" title="${esc(r.it.name || "")}">${esc(r.it.name || "")}</span>`,
        `<button class="lx-copy" title="copy the full product title" data-t="${esc(r.it.name || "")}" onclick="event.stopPropagation();lxCopyTitle(this)">📋</button>`) + `</span>` },
    { key: "order", label: "الطلب · order", w: 126, sortable: true, render: (r) => orderCell(r) },
    { key: "profile", label: "الحساب · profile", w: 100, sortable: true,
      render: (r) => W.lxProfileCell(W.lxAcctChain(r.it, r.o)) },
    { key: "status", label: "الحالة · status", w: 154, min: 84,
      render: (r) => j(W.lxDot(r.it), W.lxConfPill(r.it), W.lxStatusSelect(r.it)) },
    { key: "qty", label: "الكمية · qty", w: 74, align: "end", sortable: true,
      render: (r) => { const q2 = W.lxF(r.it, "quantity ordered");
        return q2 == null || q2 === "" ? "" : `<span class="ds-muted" title="Quantity ordered">×${esc(q2)}</span>`; } },
    { key: "tracking", label: "التتبع · tracking", w: 138,
      render: (r) => { const tn = itemTn(r.it);
        return tn ? W.poTn4(tn, "var(--ds-t-xs)") + W.lxCopyRawBtn(tn)
          : `<span class="ds-muted" title="no GAASH tracking number yet">📦 —</span>`; } },
    { key: "gashstatus", label: "حالة الجمارك · gaash status", w: 172,
      render: (r) => W.lxCfPill("gash status", W.lxF(r.it, "gash status")) || "" },
    { key: "rdstatus", label: "حالة RD · rd status", w: 114,
      render: (r) => W.lxCfPill("rd status", W.lxF(r.it, "rd status")) || "" },
    { key: "due", label: "الاستحقاق · due", w: 96, sortable: true,
      render: (r) => W.lxGzDone(r.it) ? "" : (W.lxDueChip(r.it.due_date, W.lxIsDone(r.it)) || "") },
    { key: "menu", label: "", w: 30, locked: true, pin: "end", render: (r) => productMenu(r) },
  ];

  const itemTn = (it) =>
    String(W.lxF(it, "tracking number") || (it.data && it.data.tracking_number) || "").trim();

  // The run's first row keeps the order pill (+ ×N); the rest say "same order"
  // and stay clickable, so a product never loses the way back to its order.
  function orderCell(r) {
    if (!r.o) return "";
    if (r.tie === "mid" || r.tie === "end")
      return `<span class="ds-muted ds-lx-tie" title="نفس طلب السطر أعلاه · same order as the row above — ${esc(r.o.name || "")}"`
        + ` onclick="event.stopPropagation();lxJumpOrder(${r.o.id})">└ نفس الطلب</span>`;
    return `<button class="pill ds-lx-orderpill" title="open ${esc(r.o.name || "")}"`
      + ` onclick="event.stopPropagation();lxJumpOrder(${r.o.id})">${W.lxShortName(r.o.name)}</button>`
      + (r.tieN > 1 ? `<span class="ds-muted" title="${esc(String(r.tieN))} products in this order">×${esc(String(r.tieN))}</span>` : "");
  }

  function productMenu(r) {
    const it = r.it, tn = itemTn(it);
    const ordTn = r.o ? trackingNumbers(r.o).own : "";
    return W.popMenu([
      ["✏️ تعديل المنتج · Edit product", `lxOpenEditor('item',${it.id},null)`],
      ["↔️ نقل إلى طرد · Move to package", `lxMovePrompt(${it.id})`],
      ["🚚 تعيين رقم التتبع · Set tracking", `lxTrackingPrompt([${it.id}],'${q(tn)}','${q(ordTn)}')`],
      tn ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(tn)}')`] : null,
      tn ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(tn)}')`] : null,
      tn ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(tn)}')`] : null,
      ["🗑 إخفاء · hide", `lxDelete(${it.id})`, true],
    ]);
  }

  // [{it,o}] in final order -> the same rows carrying their tie marks
  function withTies(pairs) {
    return pairs.map((r, i) => {
      const id = r.o ? r.o.id : null;
      const prevId = i > 0 && pairs[i - 1].o ? pairs[i - 1].o.id : null;
      const nextId = i < pairs.length - 1 && pairs[i + 1].o ? pairs[i + 1].o.id : null;
      const prev = id != null && id === prevId, next = id != null && id === nextId;
      let tie = "", tieN = 0;
      if (prev || next) {
        tie = prev ? (next ? "mid" : "end") : "start";
        if (tie === "start") { tieN = 1; for (let k = i + 1; k < pairs.length && pairs[k].o && pairs[k].o.id === id; k++) tieN++; }
      }
      return Object.assign({}, r, { tie, tieN });
    });
  }

  const LAYOUT = "ds_table_lxp";                 // the one layout every section shares
  function seedLayout(id) {
    try {
      const v = localStorage.getItem(LAYOUT);
      if (v && id !== "lxp") localStorage.setItem("ds_table_" + id, v);
    } catch (e) { /* private mode */ }
    // DS.table REUSES a registered table and only calls load() when it has no
    // state — so writing the key is not enough for a section that has already
    // rendered once. Clearing `state` is how the component itself asks for a
    // reload, and without it a column hidden in one section reached no other.
    const t = DS.tableGet && DS.tableGet(id);
    if (t) t.state = null;
  }
  function keepLayout(state, rerender) {
    try { localStorage.setItem(LAYOUT, JSON.stringify(state)); } catch (e) { /* private mode */ }
    if (rerender) rerender();
  }

  function productTable(el, ctx, id, rows, footer) {
    seedLayout(id);
    DS.tableRender(el, {
      id, columns: PCOLS(), rows: withTies(rows),
      rowKey: (r) => String(r.it.id),
      rowTitle: () => "انقر لعرض كل التفاصيل · click for full product details",
      rowClass: (r) => r.tie ? "ds-lx-tied" : "",
      onRowClick: (r) => W.lxInfoOpen("item", r.it.id),
      sort: ctx.sort || null,
      onSort: (key, dir) => ctx.onSort(key, dir),   // the app sorts, so ties stay true
      empty: { title: "No products match the filter" },
      footer: footer || null,
      onStateChange: (st) => keepLayout(st, null),
    });
  }

  /* ctx = { rows:[{it,o}] already filtered, sort, onSort(key,dir),
             group:{def,keys,map,chip(v),open(k),key(v)} | null }          */
  DS.lxProducts = {
    render(el, ctx) {
      const rows = ctx.rows || [];
      const totQty = rows.reduce((a, r) => a + (Number(W.lxF(r.it, "quantity ordered")) || 0), 0);
      const foot = {
        product: `<b>Σ ${esc(String(rows.length))} منتج · products</b>`,
        qty: `<b title="مجموع الكميات · total quantity">×${esc(String(totQty))}</b>`,
      };
      if (!ctx.group) {
        el.innerHTML = `<div id="lxpBoard"></div>`;
        productTable(document.getElementById("lxpBoard"), ctx, "lxp", rows, foot);
        return;
      }
      const g = ctx.group;
      el.innerHTML = g.keys.map((v, i) => {
        const open = g.open(g.key(v)), n = (g.map.get(v) || []).length;
        return `<div class="ds-lx-group${open ? " is-open" : ""}">
          <div class="ds-lx-group-head" onclick="lxGroupToggle('${q(g.key(v))}')">
            <button type="button" class="ds-exp-btn" aria-expanded="${open ? "true" : "false"}"
              aria-label="${open ? "Collapse" : "Expand"} group">${DS.icon("chevron-right", { size: 14 })}</button>
            ${g.chip(v)}
            <span class="ds-muted">${esc(String(n))} product${n === 1 ? "" : "s"}</span>
          </div>
          ${open ? `<div id="lxpG${i}"></div>` : ""}</div>`;
      }).join("") + `<div class="ds-lx-groupfoot"><b>Σ ${esc(String(rows.length))} منتج · products</b>`
        + `<span class="ds-spacer"></span><b title="مجموع الكميات · total quantity">×${esc(String(totQty))}</b></div>`;
      g.keys.forEach((v, i) => {
        const mount = document.getElementById("lxpG" + i);
        if (mount) productTable(mount, ctx, "lxp__" + i, g.map.get(v) || [], null);
      });
    },
  };

  /* ========================================================================
     Batch F3 — the PACKAGES board (LXT table "k"). One row per PHYSICAL PARCEL
     with its estimated value, the customs pills, and the ✉ clearance-mail pill.

     Row-level status editing follows the rule the legacy board set and the
     owner asked for: a real 📦 subtask edits ITS OWN ClickUp task; a loose
     group of exactly ONE product edits that product; a multi-product group
     stays READ-ONLY, because one control must never bulk-write N tasks.

     The app sorts (LXT_SORT["k"], the preference this board already had) and
     `onSort` is passed so the DataTable renders the order it is given — the
     same arrangement as the products board.
     ==================================================================== */
  const fmt2 = (n) => (Math.round(n * 100) / 100).toLocaleString();

  const KCOLS = () => [
    { key: "package", label: "الطرد · package", w: 380, min: 190, pin: "start", locked: true, sortable: true,
      render: (r) => packageCell(r) },
    { key: "order", label: "الطلب · order", w: 126, sortable: true, render: (r) => kOrderCell(r) },
    { key: "profile", label: "الحساب · profile", w: 100, sortable: true,
      render: (r) => W.lxProfileCell(W.lxKAcct(r)) },
    { key: "value", label: "≈ القيمة · ≈ value", w: 106, align: "end", sortable: true,
      render: (r) => valueCell(r) },
    { key: "status", label: "الحالة · status", w: 134, min: 84, sortable: true,
      render: (r) => kStatusCell(r) },
    { key: "gash", label: "الجمارك · gash", w: 180,
      render: (r) => { const en = enrichedOf(r);
        return en ? (W.lxGashCell(en, W.lxGashRollup([r.pk, ...(r.items || [])]), r.tn) || "") : ""; } },
    { key: "mail", label: "✉ البريد · ✉ mail", w: 95, render: (r) => W.lxMailPill(r.tn) || "" },
    { key: "deadline", label: "الموعد · deadline", w: 135, render: (r) => kDeadlineCell(r) },
    { key: "due", label: "الاستحقاق · due", w: 96, sortable: true,
      render: (r) => { const en = enrichedOf(r);
        if (en && W.lxGzDone(en)) return "";
        const its = r.items || [], u = uniqStatuses(its);
        return W.lxDueChip(W.lxKDue(r), its.length && u.length === 1 ? W.lxIsDone(its[0]) : false) || ""; } },
    { key: "menu", label: "", w: 30, locked: true, pin: "end", render: (r) => kMenu(r) },
  ];

  const uniqStatuses = (its) => [...new Set((its || []).map((i) => i.status).filter(Boolean))];
  // the pills read from the row that actually carries the GAASH enrichment: an
  // absorbed shell is usually the empty one, so the products hold the live data
  function enrichedOf(r) {
    const its = r.items || [];
    return (r.pk && W.lxEnriched(r.pk)) ? r.pk : (its.find(W.lxEnriched) || r.pk || its[0] || null);
  }

  function packageCell(r) {
    const its = r.items || [];
    return `<span class="ds-lx-name">`
      + j(r.pk ? W.lxDot(r.pk) : "",
          W.lxConfPill((r.pk && r.pk.sync_state === "conflict") ? r.pk : its.find((i) => i.sync_state === "conflict")),
          `<b class="ds-lx-parcel-tn">📦 ${r.tn ? W.poTn4(r.tn) : `<span class="ds-muted" title="no GAASH tracking number yet">no tracking</span>`}</b>`,
          r.tn ? W.lxCopyRawBtn(r.tn) : "",
          // two photos, like every identity cell: four pushed the count - the one
          // fact in the strip you cannot see - past the edge of the cell
          W.lxThumbs(its, 2),
          `<span class="ds-muted ds-truncate" title="${esc(W.lxCountLbl(its))}">${esc(W.lxCountLbl(its))}</span>`)
      + `</span>`;
  }

  function kOrderCell(r) {
    if (r.o) return `<button class="pill ds-lx-orderpill" title="open ${esc(r.o.name || "")}"`
      + ` onclick="event.stopPropagation();lxJumpOrder(${r.o.id})">${W.lxShortName(r.o.name)}</button>`;
    return r.pk ? W.tonePill("amber", "w/o parent", "its parent order isn't in this list") : "";
  }

  function valueCell(r) {
    const e = r.est; if (!e) return "";
    const tip = `≈ قيمة الطرد · منتجات ₪${fmt2(e.items)}`
      + (e.share > 0 ? ` + حصة شحن/جمارك ₪${fmt2(e.share)} (إجمالي الطلب ₪${fmt2(e.total)} − المنتجات، ÷ ${e.n} طرود)` : "")
      + (e.unpriced ? ` · ${e.unpriced} منتج بلا سعر — قيمته داخل الحصة · ${e.unpriced} unpriced (absorbed in the split)` : "");
    if (!(e.priced || e.share > 0)) return "";
    return `<b title="${esc(tip)}">₪${esc(fmt2(e.sum))}</b>`
      + (e.unpriced ? `<span class="ds-lx-unpriced" title="${esc(String(e.unpriced))} products with no individual price — their value sits inside the shipping/tax share">+${esc(String(e.unpriced))}?</span>` : "");
  }

  function kStatusCell(r) {
    const its = r.items || [], u = uniqStatuses(its);
    const mixed = u.length > 1
      ? W.tonePill("gray", "mixed", "products have different statuses") : "";
    if (r.pk) return W.lxStatusSelect(r.pk, W.lxKEffStatus(its, r.pk)) + mixed;
    if (its.length === 1) return W.lxStatusSelect(its[0]);
    return u.length === 1 ? W.lxStatusPill(u[0]) : mixed;
  }

  function kDeadlineCell(r) {
    const en = enrichedOf(r), its = r.items || [], u = uniqStatuses(its);
    const gashDate = (r.pk && W.lxF(r.pk, "gash date")) || its.map((it) => W.lxF(it, "gash date")).find(Boolean);
    const done = en ? W.lxGzDone(en) : false;
    const doneSt = its.length && u.length === 1 ? W.lxIsDone(its[0]) : false;
    return j(en ? W.lxDeadlinePill(en) + W.lxDocsPill(en) : "",
      gashDate && !done ? `<span title="gash date">${W.lxDueChip(gashDate, doneSt)}</span>` : "");
  }

  function kMenu(r) {
    const pk = r.pk, its = r.items || [], tn = r.tn, o = r.o;
    const ids = its.map((it) => it.id).join(",");
    const ordTn = o ? trackingNumbers(o).own : "";
    if (pk) return W.popMenu([
      ["✏️ تعديل الطرد · Edit package", `lxOpenEditor('package',${pk.id},${o ? o.id : null})`],
      ["🚚 تعيين رقم التتبع · Set tracking", `lxTrackingPrompt([${pk.id}],'${q(tn)}','')`],
      tn ? ["📧 بريد التخليص · Clearance email", `lxMailCompose('${q(tn)}',${o ? o.id : 0})`] : null,
      tn ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(tn)}')`] : null,
      tn ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(tn)}')`] : null,
      tn ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(tn)}')`] : null,
      tn ? ["🚚 تسجيل في جيرزيم · Register at Gerizim", `lxGzFor('package',${pk.id})`] : null,
      ["＋ إضافة منتج · Add product", `lxOpenEditor('item',null,${pk.id})`],
      ["🗑 إخفاء · hide", `lxDelete(${pk.id})`, true],
    ]);
    return W.popMenu([
      its.length ? [`🚚 تعيين رقم التتبع للكل (${its.length}) · Set tracking for all`,
        `lxTrackingPrompt([${ids}],'${q(tn || "")}','${q(ordTn)}')`] : null,
      tn ? ["📧 بريد التخليص · Clearance email", `lxMailCompose('${q(tn)}',${o ? o.id : 0})`] : null,
      tn ? ["🔎 تتبع الشحنة · Check shipping", `lxCheckShipping('${q(tn)}')`] : null,
      tn ? ["🪪 رفع مستندات لغاش · Upload docs", `gaashUploadOpenGwd('${q(tn)}')`] : null,
      tn ? ["📄 فحص المستندات · Check docs", `lxCheckDocs('${q(tn)}')`] : null,
    ]);
  }

  /* ctx = { rows (already filtered, valued and sorted), total, nItems,
             sort, onSort(key,dir) }                                        */
  DS.lxPackages = {
    render(el, ctx) {
      const rows = ctx.rows || [];
      DS.tableRender(el, {
        id: "lxk",
        columns: KCOLS(),
        rows,
        rowKey: (r) => String(r.pk ? "pk" + r.pk.id : "tn" + (r.tn || "") + ":" + (r.o ? r.o.id : "0")),
        rowTitle: (r) => r.pk ? "انقر لعرض كل التفاصيل · click for full package details"
                              : "انقر للانتقال إلى الطلب · click to jump to the order",
        onRowClick: (r) => { if (r.pk) W.lxInfoOpen("package", r.pk.id); else if (r.o) W.lxJumpOrder(r.o.id); },
        sort: ctx.sort || null,
        onSort: (key, dir) => ctx.onSort(key, dir),
        empty: { title: "No packages match the filter" },
        footer: rows.length ? {
          package: `<b>Σ ${esc(String(rows.length))} طرد · ${esc(String(ctx.nItems || 0))} منتج</b>`,
          value: `<b title="مجموع قيمة كل الطرود المعروضة · total of all shown packages">₪${esc(fmt2(ctx.total || 0))}</b>`,
        } : null,
      });
    },
  };

  /* Orphans — rows the sync could not hang off an order. They are not orders,
     so they are NOT rows of this table (sorting them among orders was always a
     lie); they render underneath it, in the same shape as before. */
  LXO.orphans = function (rows) {
    if (!rows || !rows.length) return "";
    return `<div class="ds-lx-orphans"><div class="ds-lx-orphans-h">`
      + `${esc(String(rows.length))} row${rows.length === 1 ? "" : "s"} with no parent order — `
      + `<span class="ds-muted">synced from AZ (2) without an order above them</span></div>`
      + rows.map((r) => `<div class="ds-lx-orphan" onclick="lxInfoOpen('${esc(r.kind || "item")}',${r.id})">`
          + j(W.lxDot(r), `<b class="ds-truncate" title="${esc(r.name || "")}">${W.lxShortName(r.name)}</b>`,
              W.lxStatusPill(r.status))
          + `</div>`).join("")
      + `</div>`;
  };
})();
