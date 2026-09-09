/*
  The rest of the Fulfillment pipeline (docs/ux-restructure Phase 4):
  To order, In cart and Package prep, on the same recipe Purchase orders set.

  Each page renders its own header (DS.pageHeader) and filter bar, then one
  DS DataTable per saved view, and expands a row into an aligned sub-grid. The
  cells still call the app's own builders for anything that carries editing
  (ppStatusPicker, neLocEdit, the quote and WhatsApp actions), so how a value is
  SAVED did not change here either. Everything the page needs from index.html
  arrives in `ctx` - its top-level `let` bindings are not window properties.

  What this phase fixes on these three pages, in the brief's own terms:

    section 9.3  three hand-built tables (a raw <table> with its own drag-resize,
                 an LXT table, and a wall of cards) become one DataTable with a
                 sticky header, pinned identity and actions, sort, resize, hide
    section 6    counts live on the stage tabs, so the sidebar badge and the page
                 agree; Package prep finally publishes a count of its own
    section 9.4  "waiting for pieces", "not priced yet" and "no ID" stop being
                 four different colours and become the attention vocabulary
    section 7.2  sentence case, one icon set, no emoji; the Arabic section
                 headings become English labels (owner decision, 2026-09-06)
    section 8    every row's detail is the same shape: a fact strip, then grids
*/
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const esc = D.esc;
  const W = window;
  const money = (v) => D.fmt.money(v, "USD");
  const num = (v) => D.fmt.number(v);
  const text = (v) => {
    v = (v == null ? "" : String(v)).trim();
    return v ? `<span class="ds-truncate" dir="auto" title="${esc(v)}">${esc(v)}</span>` : D.dash();
  };
  const mono = (v) => (String(v || "").trim() ? `<span class="ds-mono">${esc(v)}</span>` : D.dash());
  /** The stateless aligned grid the Purchases page introduced - now DS.subTable,
      because the Orders expansion needs the same one. */
  const grid = D.subTable;
  // product photos are DS.thumb / DS.thumbs - the Orders board lists products too,
  // and two implementations would drift the moment one of them was tuned
  const thumb = D.thumb;
  const thumbs = (items, max) => D.thumbs(items, { max });
  const factStrip = (facts) => (facts.length
    ? `<div class="ds-pu-facts">${facts.map(([k, v]) => `<span class="ds-kv"><span class="ds-muted">${esc(k)}</span><span>${v}</span></span>`).join("")}</div>` : "");

  // ================================================================= To order
  const TO = (D.toOrder = {});
  /** The four buckets the queue holds. "In cart" also has a page of its own; it
      stays a view here so the whole queue can still be seen in one place. */
  TO.VIEWS = [
    { key: "pending", label: "To order", bucket: "orders" },
    { key: "incart", label: "In cart", bucket: "in_cart" },
    { key: "ordered", label: "Ordered", bucket: "ordered" },
    { key: "deleted", label: "Deleted", bucket: "deleted" },
  ];

  /** The tags an order wears: how it is paid for, where it came from, what is on file. */
  function orderTags(o) {
    const out = [];
    if (o.source === "website") out.push(D.tag({ label: "From the website", icon: "globe-alt", title: "Placed through the public catalog" }));
    if (o.payment_plan === "prepaid") out.push(D.tag({ label: "Prepaid", tone: "success", title: "Pays in full up front, no commission" }));
    if (o.payment_plan === "zero_risk") out.push(D.tag({ label: "Zero risk", tone: "warning", title: "Deposit now, the rest on delivery" }));
    if (o.customer_confirmed_at) out.push(D.tag({ label: "Confirmed", icon: "check", tone: "success", title: "The customer confirmed the quote link" }));
    if (o.has_id || o.has_id_number) out.push(D.tag({ label: o.id_number ? String(o.id_number) : "ID on file", icon: "identification", tone: "info", title: o.id_number ? "ID number " + o.id_number : "ID document on file" }));
    return out.length ? out.join("") : D.dash();
  }

  /** One vocabulary for what is missing, instead of four colours (brief 9.4). */
  function orderAttention(ctx, o, kind) {
    const out = [];
    if (kind === "pending" && o.amount_to_collect_usd == null) out.push({ kind: "unpriced", title: "No price yet - the totals do not include this order" });
    if (o.source === "website" && !o.payment_plan) out.push({ kind: "stale", detail: "no plan", title: "Left the website before choosing a payment plan" });
    if (!(o.has_id || o.has_id_number)) out.push({ kind: "missing_id", title: "No ID on file - customs needs one before the parcel can clear" });
    const late = ctx.lateDays(o);
    if (late) out.push({ kind: "late", detail: `${late} d`, title: "Past the date promised to the customer" });
    return out.length ? out.map((a) => D.attention(a)).join("") : D.dash();
  }

  function orderDetail(ctx, o, kind) {
    const facts = [];
    const phone = (o.phones && o.phones.length ? o.phones.join(" · ") : o.phone) || "";
    if (phone) facts.push(["Phone", mono(phone)]);
    if (o.id_number) facts.push(["ID number", mono(o.id_number)]);
    if (ctx.canEdit || o.city) facts.push(["City", W.neLocCell(o, "city") || D.dash()]);
    if (ctx.canEdit || o.address) facts.push(["Address", W.neLocCell(o, "address") || D.dash()]);
    if (o.deposit_usd > 0) facts.push(["Deposit", esc(money(o.deposit_usd))]);
    if (kind === "ordered" && o.amazon_order_number) facts.push(["Amazon order", mono(o.amazon_order_number)]);
    if (o.est_delivery_customer) facts.push(["Promised", `${esc(o.est_delivery_customer)} ${W.dueChip(o.est_delivery_customer)}`]);
    if (kind === "deleted" && o.deleted_at) facts.push(["Deleted", `${esc(o.deleted_at)}${o.deleted_by ? " by " + esc(o.deleted_by) : ""}`]);
    if (o.notes) facts.push(["Notes", text(o.notes)]);
    const items = o.items || [];
    if (!items.length) return factStrip(facts) + `<div class="ds-pu-empty">This order has no products yet.</div>`;
    const cols = [
      { key: "p", label: "Product", render: (it) => `<span class="ds-pu-prod">${thumb(it)}${text(it.title || it.asin || "(unnamed product)")}${ctx.itemUrl(it) ? `<a class="ds-pu-out" href="${esc(ctx.itemUrl(it))}" target="_blank" rel="noopener" title="Open on Amazon" aria-label="Open on Amazon">${D.icon("arrow-top-right-on-square", { size: 13 })}</a>` : ""}</span>` },
      { key: "asin", label: "ASIN", w: 120, render: (it) => mono(it.asin) },
      { key: "qty", label: "Qty", w: 56, align: "end", render: (it) => `<span class="ds-num">${esc(num(it.qty || 1))}</span>` },
      // The SERP price is what Amazon was showing when we looked: internal only,
      // never quoted to the customer, so it is labelled and muted.
      { key: "serp", label: "Amazon price", w: 130, align: "end",
        render: (it) => (it.serp_price_usd == null ? D.dash()
          : `<span class="ds-pu-est" title="Amazon's price when we last looked${it.serp_price_at ? " on " + esc(String(it.serp_price_at).slice(0, 10)) : ""} - internal only">${esc(money(it.serp_price_usd))}</span>`) },
    ];
    return factStrip(facts) + grid(cols, items, `Products in ${o.order_id}`);
  }

  function orderActions(ctx, o, kind) {
    if (kind === "pending") {
      const url = (it) => ctx.itemUrl(it);
      const needPrice = (o.items || []).some((it) => it.serp_price_usd == null && url(it));
      const needImg = (o.items || []).some((it) => !it.image && url(it));
      return [
        o.amount_to_collect_usd == null ? { label: "Price this order", icon: "calculator", onclick: `neQuotePrice('${esc(o.order_id)}')` } : null,
        { label: "Send the quote link", icon: "link", onclick: `neQuoteLink('${esc(o.order_id)}')` },
        { label: "Edit order", icon: "pencil-square", onclick: `openOrderEdit('${esc(o.order_id)}')` },
        { label: "Request the ID", icon: "identification", onclick: `neRequestId('${esc(o.order_id)}')` },
        needPrice ? { label: "Get the missing prices", icon: "currency-dollar", onclick: `neGetPricesAll('${esc(o.order_id)}')` } : null,
        needImg ? { label: "Get the missing photos", icon: "photo", onclick: `neImgsAll('${esc(o.order_id)}')` } : null,
        { divider: true },
        { label: "Delete order", icon: "trash", danger: true, onclick: `neDelete('${esc(o.order_id)}','${esc((o.customer || "").replace(/'/g, ""))}')` },
      ].filter(Boolean);
    }
    if (kind === "incart") return [{ label: "Back to the To-order queue", icon: "arrow-uturn-left", onclick: `neUncart('${esc(o.order_id)}')` }];
    if (kind === "deleted") return [
      ctx.canEdit ? { label: "Restore", icon: "arrow-path", onclick: `neRestore('${esc(o.trash_id)}')` } : null,
      ctx.canAdmin ? { label: "Remove permanently", icon: "x-mark", danger: true, onclick: `nePurge('${esc(o.trash_id)}','${esc((o.customer || "").replace(/'/g, ""))}')` } : null,
    ].filter(Boolean);
    return [{ label: "Edit order", icon: "pencil-square", onclick: `openOrderEdit('${esc(o.order_id)}')` }];
  }

  TO.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    const stats = [{ label: "Waiting to be bought", value: num(s.pending) }, { label: "Products", value: num(s.products) }];
    if (ctx.money) stats.push({ label: "Total", value: money(s.total) });
    if (ctx.money && s.deposits) stats.push({ label: "Deposits held", value: money(s.deposits) });
    if (s.unpriced) stats.push({ label: "Not priced yet", value: num(s.unpriced), tone: "warning" });
    el.innerHTML = D.pageHeader({
      crumbs: [{ label: "Fulfillment" }, { label: "To order" }],
      title: "To order", stats,
      primary: { label: "Open every product on Amazon", icon: "arrow-top-right-on-square", title: "Opens one tab per product so you can add them to the cart", onclick: "neOpenAllProducts()" },
      secondary: [{ label: "Quick quote", icon: "calculator", title: "Price a basket before the order exists", onclick: "neQuoteExpand()" }],
      overflow: [{ label: "Refresh", icon: "arrow-path", onclick: "loadNeedOrder()" }],
    }) + D.filterBar({
      views: TO.VIEWS.map((v) => ({ key: v.key, label: v.label, count: ctx.counts[v.key], active: ctx.view === v.key })),
      onView: "neSetFilter(KEY)",
    });
  };

  TO.board = (mount, rows, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    const kind = ctx.view;
    const cols = [
      { key: "customer", label: "Customer", w: 230, pin: "start", locked: true, sortVal: (o) => (o.customer || "~").toLowerCase(),
        render: (o) => `<span class="ds-pu-id">${D.avatar({ name: o.customer || "?" })}<span class="ds-fl-who">${text(o.customer)}<span class="ds-mono ds-muted">${esc(o.order_id)}</span></span></span>` },
      { key: "products", label: "Products", w: 190, sortVal: (o) => (o.items || []).length, render: (o) => thumbs(o.items) },
      ctx.money ? { key: "amount", label: "Amount", w: 110, align: "end", sortVal: (o) => o.amount_to_collect_usd || 0,
        render: (o) => (o.amount_to_collect_usd != null ? `<b class="ds-num ds-pu-collect">${esc(money(o.amount_to_collect_usd))}</b>` : D.dash()) } : null,
      ctx.money ? { key: "deposit", label: "Deposit", w: 106, align: "end", sortVal: (o) => o.deposit_usd || 0,
        render: (o) => (o.deposit_usd > 0 ? `<span class="ds-num">${esc(money(o.deposit_usd))}</span>` : D.dash()) } : null,
      ctx.money ? { key: "remaining", label: "Still owed", w: 110, align: "end", sortVal: (o) => (o.amount_to_collect_usd || 0) - (o.deposit_usd || 0),
        render: (o) => (o.amount_to_collect_usd != null ? `<span class="ds-num">${esc(money(o.amount_to_collect_usd - (o.deposit_usd || 0)))}</span>` : D.dash()) } : null,
      { key: "tags", label: "Tags", w: 200, sortable: false, render: orderTags },
      { key: "promised", label: "Promised", w: 130, sortVal: (o) => (o.est_delivery_customer ? Date.parse(o.est_delivery_customer) || Infinity : Infinity),
        render: (o) => (o.est_delivery_customer ? `${esc(D.fmt.date(o.est_delivery_customer))} ${W.dueChip(o.est_delivery_customer, ["DELIVERED", "COLLECTED", "CANCELLED"].includes(o.status))}` : D.dash()) },
      { key: "attention", label: "Needs attention", w: 190, sortable: false, render: (o) => orderAttention(ctx, o, kind) },
    ].filter(Boolean).concat([
      { key: "status", label: "Status", w: 140, pin: "end", sortVal: (o) => o.status || "~",
        render: (o) => (kind === "deleted" ? D.badge({ label: "Deleted", tone: "danger" }) : W.statusPill(o.status)) },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false, menu: (o) => orderActions(ctx, o, kind) },
    ]);
    D.tableRender(el, {
      id: "ne_" + kind, ariaLabel: "Orders " + kind, columns: cols, rows, rowKey: (o) => o.order_id,
      // Only the queue itself can be moved to the cart, so only it offers selection.
      selectable: kind === "pending",
      bulk: kind === "pending" ? [{ label: "Move to cart", icon: "shopping-cart", variant: "primary", onclick: "neMoveSelected()" }] : null,
      onSelect: (keys) => ctx.setSelection(keys),
      expandable: { render: (o) => orderDetail(ctx, o, kind), open: () => false },
      empty: kind === "pending"
        ? { title: "Nothing waiting", text: "Every order has been placed." }
        : { title: "Nothing here" },
      footer: ctx.money ? {
        customer: `<b>${esc(num(rows.length))} orders</b>`,
        amount: `<b class="ds-num">${esc(money(rows.reduce((a, o) => a + (o.amount_to_collect_usd || 0), 0)))}</b>`,
        deposit: `<span class="ds-num">${esc(money(rows.reduce((a, o) => a + (o.deposit_usd || 0), 0)))}</span>`,
      } : { customer: `<b>${esc(num(rows.length))} orders</b>` },
    });
  };

  // ================================================================== In cart
  const IC = (D.inCart = {});

  IC.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    // Revenue belongs to the decision strip below, not twice on the same screen.
    const stats = [{ label: "Orders", value: num(s.count) }, { label: "Products", value: num(s.products) }];
    if (!ctx.money) stats.push({ label: "Revenue", value: "hidden", hint: "Your role does not see money" });
    el.innerHTML = D.pageHeader({
      crumbs: [{ label: "Fulfillment" }, { label: "In cart" }],
      title: "In cart", stats,
      overflow: [{ label: "Refresh", icon: "arrow-path", onclick: "loadIncart()" }],
      // What the cart is FOR: deciding whether to buy. The one number you cannot
      // read off Amazon is what it costs, so it is an input, next to the answer.
      below: ctx.money ? `<div class="ds-fl-profit">
        ${D.stat({ label: "Revenue", value: money(s.revenue), title: "What these orders are quoted at, together" })}
        <div class="ds-stat"><span class="ds-stat-label"><label for="cartCostIn">Cart cost on Amazon</label></span>
          <span class="ds-stat-value">${D.numberInput({ id: "cartCostIn", value: s.cost || "", placeholder: "0.00", min: 0, step: "0.01", oninput: "cartCostInput(this.value)", cls: "ds-fl-cost" })}</span>
          <span class="ds-stat-hint">Type what Amazon shows</span></div>
        ${D.stat({ label: "Profit if you buy now", value: money(s.revenue - s.cost), tone: s.revenue - s.cost >= 0 ? "success" : "danger", title: "Revenue less the cart cost", cls: "ds-fl-profit-out" })}
      </div>` : "",
    });
  };

  IC.board = (mount, rows, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    const cols = [
      { key: "customer", label: "Customer", w: 250, pin: "start", locked: true, sortVal: (o) => (o.customer || "~").toLowerCase(),
        render: (o) => `<span class="ds-pu-id">${D.avatar({ name: o.customer || "?" })}<span class="ds-fl-who">${text(o.customer)}<span class="ds-mono ds-muted">${esc(o.order_id)}</span></span></span>` },
      { key: "phone", label: "Phone", w: 150, sortVal: (o) => o.phone || "~", render: (o) => mono(o.phone) },
      { key: "products", label: "Products", w: 220, sortVal: (o) => (o.items || []).length, render: (o) => thumbs(o.items, 6) },
      ctx.money ? { key: "revenue", label: "Revenue", w: 120, align: "end", sortVal: (o) => o.amount_to_collect_usd || 0,
        render: (o) => (o.amount_to_collect_usd != null ? `<b class="ds-num ds-pu-collect">${esc(money(o.amount_to_collect_usd))}</b>` : D.dash()) } : null,
    ].filter(Boolean).concat([
      { key: "actions", label: "", w: 130, type: "actions", pin: "end", locked: true, sortable: false,
        quick: (o) => [{ icon: "check", ariaLabel: "Mark as ordered", title: "Bought it - move this order to ORDERED", onclick: `event.stopPropagation();cartSetStatus('${esc(o.order_id)}','ORDERED')` },
          { icon: "arrow-uturn-left", ariaLabel: "Back to the queue", title: "Take it out of the cart", onclick: `event.stopPropagation();cartRemove('${esc(o.order_id)}')` }] },
    ]);
    D.tableRender(el, {
      id: "ic_cart", ariaLabel: "Orders in the Amazon cart", columns: cols, rows, rowKey: (o) => o.order_id,
      expandable: { render: (o) => grid([
        { key: "p", label: "Product", render: (it) => `<span class="ds-pu-prod">${thumb(it)}${text(it.title || it.asin || "(unnamed product)")}</span>` },
        { key: "asin", label: "ASIN", w: 120, render: (it) => mono(it.asin) },
        { key: "qty", label: "Qty", w: 56, align: "end", render: (it) => `<span class="ds-num">${esc(num(it.qty || 1))}</span>` },
      ], o.items || [], `Products in ${o.order_id}`), open: () => false },
      empty: { title: "The cart is empty", text: "Tick orders on the To-order page, then choose Move to cart.",
        action: { label: "Go to To order", icon: "light-bulb", onclick: "setView('needorder')" } },
      footer: ctx.money ? { customer: `<b>${esc(num(rows.length))} orders</b>`, revenue: `<b class="ds-num">${esc(money(rows.reduce((a, o) => a + (o.amount_to_collect_usd || 0), 0)))}</b>` }
        : { customer: `<b>${esc(num(rows.length))} orders</b>` },
    });
  };

  // ============================================================= Package prep
  const PP = (D.pkgPrep = {});
  PP.VIEWS = [
    { key: "ready", label: "Ready to pack", hint: "Every piece is in the office" },
    { key: "waiting", label: "Waiting for pieces", hint: "Some of their order has arrived" },
    { key: "reviews", label: "Ask for a review", hint: "Delivered - worth asking" },
  ];

  PP.chrome = (host, ctx) => {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    const s = ctx.stats;
    el.innerHTML = D.pageHeader({
      crumbs: [{ label: "Fulfillment" }, { label: "Package prep" }],
      title: "Package prep",
      stats: [{ label: "Ready to pack", value: num(s.ready), tone: s.ready ? "success" : null },
        { label: "Waiting for pieces", value: num(s.waiting), tone: s.waiting ? "warning" : null },
        { label: "Ask for a review", value: num(s.reviews) },
        { label: "Exchange rate", value: `${s.rate} ILS`, hint: "Set in Settings" }],
      overflow: [{ label: "Refresh", icon: "arrow-path", onclick: "loadPkgprep()" }],
    }) + D.filterBar({
      views: PP.VIEWS.map((v) => ({ key: v.key, label: v.label, count: s[v.key], active: ctx.view === v.key })),
      onView: "ppSetView(KEY)",
    });
  };

  /** The WhatsApp actions. A Palestinian number answers on either country code,
      which is why there are two links and not one. */
  function prepActions(c, view, i) {
    const out = [];
    if (view === "reviews") {
      if (c.wa_url_970) out.push({ href: c.wa_url_972, label: "+972" }, { href: c.wa_url_970, label: "+970" });
    } else if (c.wa_review_970) {
      out.push({ href: c.wa_review_972, label: "+972" }, { href: c.wa_review_970, label: "+970" });
    }
    const links = out.map((a) => `<a class="ds-btn ds-btn-sm ds-btn-secondary" href="${esc(a.href)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Open WhatsApp with the message ready">${D.icon("chat-bubble-left-right", { size: 13 })}<span>${esc(a.label)}</span></a>`).join("");
    const copy = D.button({ icon: "clipboard", size: "sm", variant: "ghost", iconOnly: true, ariaLabel: "Copy the message", title: "Copy the message",
      onclick: view === "reviews" ? `event.stopPropagation();ppReviewCopy(${i})` : `event.stopPropagation();ppCopyReview('${esc(view)}',${i})` });
    const more = [];
    if (view !== "reviews" && c.wa_url) more.push({ label: "Send the order-status message", icon: "device-phone-mobile", onclick: `window.open(${JSON.stringify(c.wa_url)},'_blank','noopener')` });
    if (view === "reviews") more.push({ label: "Mark as asked", icon: "check-circle", onclick: `ppReviewDone('${esc(c.key)}')` });
    return `<span class="ds-actions">${links || (out.length ? "" : D.attention({ kind: "no_reply", detail: "no number", title: "No phone number on file - copy the message instead" }))}${copy}`
      + (more.length ? D.menu({ items: more, button: { icon: "ellipsis-horizontal", size: "sm", variant: "ghost", ariaLabel: "More actions" } }) : "") + `</span>`;
  }

  PP.board = (mount, rows, ctx) => {
    const el = typeof mount === "string" ? document.getElementById(mount) : mount;
    if (!el) return;
    const view = ctx.view;
    const t = (c) => c.totals || {};
    const cols = [
      { key: "customer", label: "Customer", w: 230, pin: "start", locked: true, sortVal: (c) => (c.name || c.phone || "~").toLowerCase(),
        render: (c) => `<span class="ds-pu-id">${D.avatar({ name: c.name || c.phone || "?" })}<span class="ds-fl-who">${text(c.name || c.phone)}${(c.orders && c.orders.length > 1) || c.n_orders > 1 ? `<span class="ds-muted">${esc(num((c.orders || []).length || c.n_orders))} orders</span>` : ""}</span></span>` },
      { key: "phone", label: "Phone", w: 150, sortVal: (c) => c.phone || "~", render: (c) => mono(c.phone) },
      { key: "pieces", label: "Pieces", w: 110, align: "end", sortVal: (c) => (c.n_received != null ? c.n_received / Math.max(1, c.n_items) : 1),
        render: (c) => (c.n_received != null
          ? `<span class="ds-num" title="${esc(c.n_received)} of ${esc(c.n_items)} pieces are in the office">${esc(num(c.n_received))}/${esc(num(c.n_items))}</span>`
          : (c.n_items ? `<span class="ds-num">${esc(num(c.n_items))}</span>` : D.dash())) },
      { key: "attention", label: "Needs attention", w: 190, sortable: false,
        render: (c) => {
          const out = [];
          if (view === "waiting" && c.n_missing) out.push({ kind: "stale", detail: `${c.n_missing} to come`, title: "Still waiting for pieces of this order" });
          if (t(c).unpriced) out.push({ kind: "unpriced", title: "One of their orders has no price, so the total is short" });
          if (!c.phone) out.push({ kind: "no_reply", detail: "no number", title: "No phone number on file" });
          return out.length ? out.map((a) => D.attention(a)).join("") : D.dash();
        } },
      ctx.money ? { key: "total", label: "Total", w: 130, align: "end", sortVal: (c) => t(c).usd || 0,
        render: (c) => (t(c).usd != null ? `<b class="ds-num" title="About ${esc(t(c).ils)} ILS">${esc(money(t(c).usd))}</b>` : D.dash()) } : null,
      ctx.money ? { key: "remaining", label: "To collect", w: 130, align: "end", sortVal: (c) => t(c).remaining_usd || 0,
        render: (c) => (t(c).deposit_usd > 0
          ? `<span class="ds-num ds-pu-collect" title="Deposit ${esc(money(t(c).deposit_usd))} already paid, about ${esc(t(c).remaining_ils)} ILS left">${esc(money(t(c).remaining_usd))}</span>`
          : (t(c).usd != null ? `<span class="ds-num">${esc(money(t(c).usd))}</span>` : D.dash())) } : null,
      { key: "city", label: "City", w: 140, sortVal: (c) => c.city || "~", render: (c, tbl) => ctx.locCell(c, view, ctx.indexOf(c), "city") },
      // The city decides the courier; the full address only matters at the label,
      // so it starts folded away behind the Columns button.
      { key: "address", label: "Address", w: 220, defaultHidden: true, sortVal: (c) => c.address || "~", render: (c) => ctx.locCell(c, view, ctx.indexOf(c), "address") },
    ].filter(Boolean).concat([
      { key: "actions", label: "", w: 190, type: "actions", pin: "end", locked: true, sortable: false,
        render: (c) => prepActions(c, view, ctx.indexOf(c)) },
    ]);
    D.tableRender(el, {
      id: "pp_" + view, ariaLabel: "Package prep " + view, columns: cols, rows, rowKey: (c) => c.key,
      expandable: { render: (c) => W.ppBody(c), open: () => false },
      empty: view === "ready"
        ? { title: "Nothing to pack", text: "When a shipment lands, set its package status to received on the Purchase orders page." }
        : view === "waiting" ? { title: "Nobody is waiting on pieces" } : { title: "Nobody to ask yet" },
      footer: { customer: `<b>${esc(num(rows.length))} customers</b>`,
        total: ctx.money ? `<b class="ds-num">${esc(money(rows.reduce((a, c) => a + ((c.totals || {}).usd || 0), 0)))}</b>` : "" },
    });
  };
})();
