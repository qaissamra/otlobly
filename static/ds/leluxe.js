/* ============================================================================
   Otlobly design system — Leluxe, the orders board (static/ds/leluxe.js)
   Batch F1 of the UX restructure: the ⌚ Leluxe ORDERS board leaves the LXT
   engine (LX_TABLES[""] / LXT_COLS[""] / LXT_CLS[""]) for DS.tableRender, the
   same move Purchases made in Phase 3 and Orders/Customers in Batch B.
   The products ("p") and packages ("k") boards still run on LXT — F2 and F3.

   What this file owns: the board (one row per ORDER, nine columns) and the row
   expansion (its parcels, each with its products). Everything a cell shows is
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
    { key: "name", label: "الطلب · order", w: 330, min: 200, pin: "start", sortable: true,
      sortVal: (o) => String(o.name || "").toLowerCase(),
      render: (o) => nameCell(ctx, o) },
    { key: "profile", label: "الحساب · profile", w: 78, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "profile"),
      render: (o) => W.lxProfileCell(W.lxOrderAcct(o)) },
    { key: "status", label: "الحالة · status", w: 100, min: 84, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "status"),
      render: (o) => headStatus(o) },
    { key: "total", label: "الإجمالي · total", w: 74, align: "end", sortable: true,
      sortVal: (o) => Number(W.lxF(o, "total amount")) || 0,
      render: (o) => { const a = W.lxF(o, "total amount");
        return a == null || a === "" ? "" : `<b title="Total Amount (₪)">${esc(money(a))}</b>`; } },
    { key: "tracking", label: "التتبع · tracking", w: 104, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "tracking"),
      render: (o) => headTracking(o) },
    { key: "gash", label: "الجمارك · gash", w: 160, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "gash"),
      render: (o) => { const tr = W.lxTrackedRow(o);
        return W.lxMergedStatusPill(tr) || W.lxNoDataPill(tr) || ""; } },
    { key: "deadline", label: "الموعد النهائي · deadline", w: 118, sortable: true,
      sortVal: (o) => W.lxSortVal(o, "deadline"),
      render: (o) => deadlineCell(o) },
    { key: "created", label: "أُنشئ · created", w: 84, sortable: true,
      sortVal: (o) => Number(o.date_created) || 0,
      render: (o) => o.date_created
        ? `<span class="ds-muted" title="date created (set automatically on the day the order is made)">⊕ ${esc(W.lxDate(o.date_created))}</span>` : "" },
    { key: "due", label: "تاريخ الاستحقاق · due date", w: 84, sortable: true,
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
  const PROD_COLS = [
    { key: "product", label: "المنتج · product" },
    { key: "profile", label: "الحساب · profile", w: 78 },
    { key: "status", label: "الحالة · status", w: 118 },
    { key: "qty", label: "الكمية · qty", w: 46, align: "end" },
    { key: "tracking", label: "التتبع · tracking", w: 104 },
    { key: "gash", label: "الجمارك · gash", w: 120 },
    { key: "rd", label: "RD", w: 96 },
    { key: "due", label: "الاستحقاق · due", w: 84 },
    { key: "menu", label: "", w: 30 },
  ];

  // `pkgTn` is the parcel's number: a product that has none of its own inherits
  // it MUTED rather than claiming "no tracking" under a tracked parcel.
  function productRow(it, pkgTn, ordTn) {
    const tn = String(W.lxF(it, "tracking number") || (it.data && it.data.tracking_number) || "").trim();
    const gwd = tn || pkgTn || "";
    const qty = W.lxF(it, "quantity ordered");
    return {
      product: j(W.lxThumb(W.lxAmz(it), 30),
        `<span class="ds-truncate" title="${esc(it.name || "")}">${esc(W.lxShort3(it.name))}</span>`,
        `<button class="lx-copy" title="copy the full product title" data-t="${esc(it.name || "")}" onclick="event.stopPropagation();lxCopyTitle(this)">📋</button>`),
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
