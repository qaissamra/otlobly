/*
  Sales — Orders and Customers, rebuilt on the design system (Batch B).

  Same recipe as purchases.js: this file draws the header, the filter bar and the
  board; index.html keeps a short bridge that filters the rows and hands over a
  `ctx` (its top-level bindings are not window properties, so the page cannot see
  them any other way). The cells still call the app's own builders — statusSelect,
  waMenu, odLocCell, itemsCell — because those carry the inline editing, and
  nothing about how a value is SAVED changed here.

  What the UI QA found on these two pages and what changed:

    orders  69 values were cut with no way to read them and 220 controls were
            under the 24px hit-target floor. Batch A fixed the shared causes;
            this batch removes the cause of the rest - the 13-column grid that
            gave an ASIN 42px. Identity and status are now pinned, money has its
            own right-aligned columns, and the address column starts hidden
            because it is the widest thing on the page and the rarest read.
    both    the bilingual "عربي · English" column labels became English (owner
            decision, 2026-09-06: English only for now). The DATA is untouched -
            Arabic customer names, addresses and cities still render, with
            dir="auto" so they read from their own end.
    both    a problem is stated once, in the attention vocabulary, in its own
            column - never as a fourth colour on the status pill.
*/
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const O = (D.orders = {});
  const C = (D.customers = {});
  const esc = D.esc;
  const money = (v) => D.fmt.money(v, "USD");
  const num = (v) => D.fmt.number(v);
  const W = window;

  /** Text that may be Arabic inside an English page: isolate it, keep the whole
      value in the tooltip, let it truncate at its own end. */
  const text = (v) => {
    v = (v == null ? "" : String(v)).trim();
    return v ? `<span class="ds-truncate" dir="auto" title="${esc(v)}">${esc(v)}</span>` : D.dash();
  };
  const mono = (v) => (v ? `<span class="ds-mono">${esc(v)}</span>` : D.dash());

  // ------------------------------------------------------------------- orders
  /** Who the order is for. One cell, because the id, the name and the phone are
      one fact - "which customer is this" - and splitting them across three
      columns is what made the old board need 1,850px. */
  function who(o) {
    const web = o.source === "website"
      ? D.tag({ label: "Website", icon: "globe-alt", title: "Placed through the public catalog" }) : "";
    return `<span class="ds-sl-who">
      <span class="ds-sl-id">${esc(o.order_id)}</span>${web}
      <span class="ds-sl-name">${text(o.customer)}</span>
      <span class="ds-sl-phone">${o.phone ? esc(o.phone) : ""}</span>
    </span>`;
  }

  /** Everything wrong with this order, said once, in words. */
  function attention(o) {
    const out = [];
    if (o.amount_to_collect_usd == null)
      out.push({ kind: "unpriced", title: "No price yet — this order is not in the outstanding total" });
    if (o.needs_expand)
      out.push({ kind: "stale", detail: "a.co link", title: "A short a.co link that still has to be expanded before it can be bought" });
    if (!(o.has_id || o.has_id_number))
      out.push({ kind: "missing_id", title: "No ID on file — customs needs one before the parcel can clear" });
    return out.length ? out.map((a) => D.attention(a)).join("") : D.dash();
  }

  function orderColumns(ctx) {
    const m = ctx.money;
    return [
      { key: "who", label: "Order & customer", w: 260, pin: "start", locked: true,
        sortVal: (o) => (o.customer || "~").toLowerCase(), render: who },
      // The old board stacked one block link per product, which grew taller than a row
      // and bled into the rows below. Batch B replaced that with a bare count, which
      // threw away the product itself. Photos: report.py carries item.image now, and
      // DS.thumbs falls back to the plain count for an order whose items have none.
      { key: "items", label: "Products", w: 150, sortVal: (o) => (o.items || []).length,
        render: (o) => {
          const warn = o.needs_expand
            ? D.attention({ kind: "stale", detail: "a.co", title: "A short a.co link that still has to be expanded" }) : "";
          return `${D.thumbs(o.items, { max: 4 })}${warn}`;
        } },
      m ? { key: "amount", label: "Amount", w: 112, align: "end",
        sortVal: (o) => (o.amount_to_collect_usd == null ? -1 : o.amount_to_collect_usd),
        render: (o) => ctx.amountCell(o) } : null,
      m ? { key: "deposit", label: "Deposit", w: 100, align: "end",
        sortVal: (o) => o.deposit_usd || 0, render: (o) => ctx.depositCell(o) } : null,
      m ? { key: "remaining", label: "Still owed", w: 104, align: "end",
        sortVal: (o) => (o.remaining_usd != null ? o.remaining_usd : o.amount_to_collect_usd || 0),
        render: (o) => { const v = o.remaining_usd != null ? o.remaining_usd : o.amount_to_collect_usd;
          return v == null ? D.dash() : `<b class="ds-num">${esc(money(v))}</b>`; } } : null,
      { key: "due", label: "Promised", w: 108, sortVal: (o) => (o.est_delivery_customer ? Date.parse(o.est_delivery_customer) || Infinity : Infinity),
        render: (o) => W.dueChip(o.est_delivery_customer, ["DELIVERED", "COLLECTED", "CANCELLED"].includes(o.status)) || D.dash() },
      // 190px, deliberately not narrowed with the rest: three pills can land in this
      // cell at once ("No price", "Stale · a.co link", "No ID") and a clipped warning
      // is worse than no warning.
      { key: "attention", label: "Needs attention", w: 190, sortable: false, render: attention },
      { key: "box", label: "Box", w: 68, sortVal: (o) => o.profile_box || "~",
        title: "The Amazon buying account this order is bought on — the same name as its Multilogin browser profile",
        render: (o) => ctx.boxCell(o) },
      { key: "batch", label: "Batch", w: 64, sortVal: (o) => o.batch || "~", render: (o) => text(o.batch) },
      { key: "amazon", label: "Amazon #", w: 140, defaultHidden: true, render: (o) => ctx.amazonCell(o) },
      { key: "city", label: "City", w: 100, sortVal: (o) => (o.city || "~").toLowerCase(),
        render: (o) => W.odLocCell(o, "city") },
      { key: "address", label: "Address", w: 150, defaultHidden: true, sortable: false,
        render: (o) => W.odLocCell(o, "address") },
      // visible again (owner, 2026-09-07). Batch B hid it, and the control that would
      // have brought it back was rendering below all 61 rows - see table.js.
      { key: "tracking", label: "Tracking", w: 112, sortVal: (o) => o.tracking_number || "~",
        title: "The OTL parcel number, once the package has one",
        render: (o) => mono(o.tracking_number) },
      { key: "status", label: "Status", w: 130, pin: "end", sortVal: (o) => o.status || "~",
        render: (o) => W.statusSelect(o) },
      // was a raw <select> of six message templates plus a 📦 button, 168px wide in
      // every row. The templates are actions, so they belong in the row's action menu.
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        menu: (o) => {
          const q = (k, label) => ({ label, icon: "chat-bubble-left-right", onclick: `sendWa('${esc(o.order_id)}','${k}')` });
          return [
            { head: "Send on WhatsApp" },
            q("quote", "Quote"), q("confirmation", "Confirmation"), q("payment_due", "Amount due"),
            q("order_placed", "Order placed"), q("status_update", "Status update"),
            q("ready_for_pickup", "Ready for pickup"),
            { divider: true },
            o.est_delivery_customer
              ? { label: "Tell them it is on the way", icon: "truck", title: "Sends the delivery date and the tracking link", onclick: `notifyTrack('${esc(o.order_id)}',this)` }
              : { label: "Tell them it is on the way", icon: "truck", disabled: true, title: "Set the package's arrival date on Purchase orders first" },
          ];
        } },
    ].filter(Boolean);
  }

  /** The Σ row the old board carried under the rows — kept, because it is the
      one place the outstanding total is broken down next to the deposits held. */
  function orderFooter(ctx, rows) {
    if (!ctx.money) return null;
    const live = rows.filter((o) => ctx.OPEN.includes(o.status));
    const sum = (f) => live.reduce((a, o) => a + (f(o) || 0), 0);
    const cells = {
      who: `<b>${esc(num(rows.length))} order${rows.length === 1 ? "" : "s"}</b> <span class="ds-muted">· outstanding</span>`,
      amount: `<b class="ds-num">${esc(money(sum((o) => o.amount_to_collect_usd)))}</b>`,
      deposit: `<b class="ds-num">${esc(money(sum((o) => o.deposit_usd)))}</b>`,
      remaining: `<b class="ds-num">${esc(money(sum((o) => (o.remaining_usd != null ? o.remaining_usd : o.amount_to_collect_usd))))}</b>`,
    };
    return cells;
  }

  O.board = (mount, rows, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    D.tableRender(el, {
      id: "od_orders",
      columns: orderColumns(ctx),
      rows,
      rowKey: (o) => o.order_id,
      ariaLabel: "Customer orders",
      expandable: {
        open: (o) => ctx.isOpen(o.order_id),
        // Was a hand-rolled grid with `.ds-pu-subrow` (the real class is
        // `.ds-pu-sub-row`) and no `.ds-pu-td`, so it drew unpadded and unbordered;
        // and it put the ASIN in its own column beside a "title" that falls back to
        // the ASIN, which is why every unnamed product read "B09CLKPMVC B09CLKPMVC".
        // The ASIN now sits NEXT TO a title, or stands in for one, never both.
        render: (o) => {
          const items = o.items || [];
          if (!items.length) return `<div class="ds-pu-sub-empty ds-muted">No products on this order.</div>`;
          return D.subTable([
            { label: "Product", render: (it) => {
              const named = it.title && it.title.trim() && it.title.trim() !== it.asin;
              return `<span class="ds-pu-prod">${D.thumb(it)}${named ? text(it.title) : mono(it.asin)}`
                + `${named && it.asin ? ` ${mono(it.asin)}` : ""}</span>`; } },
            { label: "Qty", w: 64, align: "end", render: (it) => esc(num(it.qty || 1)) },
            { label: "", w: 40, render: (it) => (it.url
              ? `<a class="ds-pu-out" href="${esc(it.url)}" target="_blank" rel="noopener" title="Open on Amazon" aria-label="Open on Amazon">${D.icon("arrow-top-right-on-square", { size: 13 })}</a>` : "") },
          ], items, `Products on ${o.order_id}`);
        },
      },
      onToggle: (key) => ctx.toggle(key),
      selectable: true,
      bulk: ctx.canDelete ? [{ label: "Delete selected", icon: "trash", variant: "danger", onclick: "odDeleteSelected()" }] : null,
      onSelect: (keys) => ctx.setSelection(keys),
      footer: orderFooter(ctx, rows),
      empty: { title: "No matching orders", hint: "Change the search or the filters above." },
    });
  };

  O.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    // deliberately NOT the four KPI cards above this header - repeating them would be
    // the duplication the brief calls out. These are the two facts nothing else states.
    const stats = [{ label: "Showing", value: s.shown === s.total ? num(s.total) : `${num(s.shown)} of ${num(s.total)}` }];
    if (s.unpriced) stats.push({ label: "Not priced yet", value: num(s.unpriced), tone: "warning" });
    const header = D.pageHeader({
      crumbs: [{ label: "Sales" }, { label: "Orders" }],
      title: "Orders", stats,
      primary: { label: "New order", icon: "plus", onclick: "newOrderOpen()" },
      overflow: [
        { label: "Refresh", icon: "arrow-path", onclick: "refreshAll(this)" },
        // it counts what is missing and shows you the number before it fetches anything
        { label: "Fetch missing product photos", icon: "photo",
          title: "Find every product with no photo and try to grab one. Amazon and a free proxy are tried first; it tells you how many before it starts.",
          onclick: "odFetchPhotos()" },
        { label: "Import from ClickUp", icon: "arrow-down-tray", onclick: "importClickup(this)" },
      ],
    });
    const bar = D.filterBar({
      search: { id: "odSearch", value: ctx.query, placeholder: "Search name, phone, ASIN, address, order number", oninput: "odSearchInput(this.value)" },
      right: [
        D.select({ id: "odStatus", value: ctx.status, options: ctx.statusOptions, placeholder: "All statuses", size: "sm", onchange: "odFilter('status',this.value)", ariaLabel: "Filter by status" }),
        D.select({ id: "odBatch", value: ctx.batch, options: ctx.batchOptions, placeholder: "All batches", size: "sm", onchange: "odFilter('batch',this.value)", ariaLabel: "Filter by batch" }),
      ],
    });
    el.innerHTML = header + bar;
  };

  // ---------------------------------------------------------------- customers
  function custColumns(ctx) {
    return [
      { key: "who", label: "Customer", w: 260, pin: "start", locked: true,
        sortVal: (c) => (c.name || "~").toLowerCase(),
        render: (c) => `<span class="ds-sl-who">
          ${c.vip ? D.tag({ label: "VIP", tone: "warning", icon: "star", title: "Marked as a VIP customer" }) : ""}
          <span class="ds-sl-name">${text(c.name)}</span>
          <span class="ds-sl-phone">${c.whatsapp ? esc(c.whatsapp) : ""}</span></span>` },
      { key: "city", label: "City", w: 130, sortVal: (c) => (c.city || "~").toLowerCase(),
        render: (c) => ctx.cityCell(c) },
      { key: "orders", label: "Orders", w: 90, align: "end",
        sortVal: (c) => c.order_count || 0,
        render: (c) => (c.order_count ? `<span class="ds-num">${esc(num(c.order_count))}</span>` : D.dash()) },
      // the column is DROPPED for a role without view_money. It used to render $0.00,
      // because the server redacts total_spent_usd to null and money(null) is "$0.00" -
      // a redacted figure that looks like a real one is worse than no column.
      ctx.money ? { key: "spent", label: "Spent", w: 120, align: "end",
        sortVal: (c) => c.total_spent_usd || 0,
        render: (c) => (c.total_spent_usd ? `<span class="ds-num">${esc(money(c.total_spent_usd))}</span>` : D.dash()) } : null,
      // both computed by customers.enrich() and never shown until now
      ctx.money ? { key: "collected", label: "Collected", w: 120, align: "end", defaultHidden: true,
        sortVal: (c) => c.collected_usd || 0,
        render: (c) => (c.collected_usd ? `<span class="ds-num">${esc(money(c.collected_usd))}</span>` : D.dash()) } : null,
      { key: "last", label: "Last order", w: 130, defaultHidden: true, type: "date",
        sortVal: (c) => (c.last_order_at ? Date.parse(c.last_order_at) || 0 : 0),
        render: (c) => (c.last_order_at ? `<span title="${esc(D.fmt.title(c.last_order_at))}">${esc(D.fmt.relative(c.last_order_at))}</span>` : D.dash()) },
      { key: "id", label: "ID on file", w: 140, sortVal: (c) => (c.id_number ? 2 : c.id_image ? 1 : 0),
        render: (c) => ctx.idCell(c) },
      { key: "payment", label: "Pays by", w: 150, defaultHidden: true,
        sortVal: (c) => c.payment_method || "~", render: (c) => text(c.payment_method) },
      { key: "email", label: "Email", w: 190, defaultHidden: true,
        sortVal: (c) => c.email || "~", render: (c) => text(c.email) },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        menu: (c) => ctx.rowMenu(c) },
    ].filter(Boolean);
  }

  C.board = (mount, rows, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    D.tableRender(el, {
      id: "cu_list",
      columns: custColumns(ctx),
      rows,
      rowKey: (c) => c.customer_id,
      ariaLabel: "Customers",
      onRowClick: (c) => ctx.openProfile(c.customer_id),
      rowTitle: () => "Open this customer's profile",
      empty: { title: "No matching customers", hint: "Change the search above, or add a customer." },
    });
  };

  C.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    const stats = [{ label: "Customers", value: num(s.total) }];
    if (s.withId) stats.push({ label: "ID on file", value: `${num(s.withId)} / ${num(s.total)}` });
    if (s.vip) stats.push({ label: "VIP", value: num(s.vip) });
    const header = D.pageHeader({
      crumbs: [{ label: "Sales" }, { label: "Customers" }],
      title: "Customers", stats,
      primary: { label: "Add customer", icon: "plus", onclick: "custAddOpen()" },
      secondary: [{ label: ctx.gallery ? "Back to the list" : "ID gallery", icon: "identification",
        title: "Every ID photo on file, as a wall", onclick: "toggleIdGallery()" }],
      overflow: [{ label: "Sync from orders", icon: "arrow-path",
        title: "Add anyone who has an order but no customer record yet", onclick: "custSync(this)" }],
    });
    const bar = D.filterBar({
      search: { id: "cuSearch", value: ctx.query, placeholder: "Search customer, phone, city", oninput: "cuSearchInput(this.value)" },
      // the ID gallery's two "Show only …" links were bare <a>s with no keyboard
      // affordance, both pushed to the right by margin-left:auto so neither said
      // which filter it was. They are filters, so they are filter chips now.
      chips: ctx.gallery ? [
        { label: "No photo yet", value: num(ctx.stats.noPhoto), active: ctx.idMissing,
          icon: "identification", onclick: "cuGalleryFilter('missing')",
          title: "Only customers with no ID photo. Some of these have a typed ID number - the header stat counts either." },
        { label: "Has an order waiting", value: num(ctx.stats.toOrder), active: ctx.idToOrder,
          icon: "light-bulb", onclick: "cuGalleryFilter('toorder')",
          title: "Only customers with an order in the To-order queue" },
      ] : null,
    });
    el.innerHTML = header + bar;
  };
})();
