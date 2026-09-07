/*
  Phase 2 shell — the grouped sidebar, top bar, router and Needs attention queue.

  It is ADDITIVE on purpose. The legacy shell (#sidebar and .topbar in
  web/index.html) is untouched and stays the default; this file only takes over
  when the per-user flag is on (owner decision D13), and every page it navigates
  to is the same legacy view container as before. Nothing here re-implements a
  page — setView() still does all the work, so a page can be migrated onto the
  design system later without the shell noticing.

  How it hooks in, in one paragraph: boot() renders a DS sidebar and top bar next
  to the legacy ones and hides those with CSS; it MOVES the live bell / add-order
  / refresh nodes into the new bar rather than rebuilding them, so their handlers
  keep working; it wraps window.setView so every navigation - from a nav click, a
  legacy onclick or a deep link - writes the hash and repaints the active item;
  and it reads each nav item's visibility off its legacy button, so every role and
  feature gate is inherited rather than re-stated (one place to be wrong, not two).

    ?shell=new / ?shell=old        turn it on or off from a link
    localStorage.otl_shell         "ds" while it is on
*/
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const S = (D.shell2 = {});
  const $ = (id) => document.getElementById(id);
  const esc = D.esc;
  // index.html's top-level `let` bindings are NOT window properties (global lexical
  // scope), so the app exposes exactly what the shell needs through window.APP.
  const A = () => window.APP || {};
  const store = {
    get(k, dflt) { try { const v = localStorage.getItem(k); return v === null ? dflt : v; } catch (e) { return dflt; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* private window */ } },
  };

  // ---------------------------------------------------------------- the flag
  const FLAG = "otl_shell";
  S.enabled = () => store.get(FLAG, "") === "ds";
  S.set = (on) => {
    on ? store.set(FLAG, "ds") : store.del(FLAG);
    // A ?shell=new left in the address bar would switch it straight back on, so the
    // toggle rewrites the URL rather than only the stored flag.
    const q = new URLSearchParams(location.search); q.delete("shell");
    const url = location.pathname + (q.toString() ? "?" + q : "") + (on ? "#/overview" : "");
    // replace() already reloads when the URL changes; calling reload() as well races it
    // and can re-load the old address (?shell=new included), switching straight back on.
    if (location.pathname + location.search + location.hash === url) location.reload();
    else location.replace(url);
  };

  // ---------------------------------------------------------------- the IA (AUDIT 5.8)
  // path      the hash route, stable and linkable
  // view      the legacy setView id it shows
  // btn       the legacy nav button whose visibility carries the role/feature gate
  // badge     which count to show, and only when it is not zero
  const NAV = [
    { key: "sales", label: "Sales", items: [
      { key: "leads", label: "Leads", icon: "megaphone", path: "/sales/leads", view: "metaleads", btn: "metaLeadsBtn" },
      { key: "customers", label: "Customers", icon: "users", path: "/sales/customers", view: "customers", btn: "custBtn" },
      { key: "orders", label: "Orders", icon: "clipboard-document-list", path: "/sales/orders", view: "orders", btn: "homeBtn" },
    ] },
    // D8: the pipeline is ONE page with four stage tabs, so it is ONE nav item.
    // The stages are still separate routes (and still separate legacy views until
    // Phase 4 rebuilds them); STAGES renders them as this page's tab strip.
    { key: "fulfillment", label: "Fulfillment", items: [
      { key: "fulfil", label: "Fulfillment", icon: "queue-list", path: "/fulfillment", view: null, badge: "fulfil" },
    ] },
    { key: "shipping", label: "Shipping", items: [
      { key: "tracking", label: "Tracking", icon: "map-pin", path: "/shipping/tracking", view: "bulksearch", btn: "bulkSearchBtn" },
      { key: "gaash", label: "GAASH mail", icon: "envelope", path: "/shipping/gaash-mail", view: "gaashmail", btn: "gaashMailBtn" },
    ] },
    { key: "finance", label: "Finance", items: [
      { key: "deposits", label: "Deposits", icon: "banknotes", path: "/finance/deposits", view: "deposits", btn: "depositsBtn" },
      { key: "pnl", label: "P&L", icon: "chart-bar", path: "/finance/pnl", view: "pnl", btn: "pnlBtn" },
    ] },
    { key: "insights", label: "Insights", items: [
      { key: "goals", label: "Goals", icon: "trophy", path: "/insights/goals", view: "goals", btn: "goalsBtn" },
      { key: "activity", label: "Activity", icon: "clock", path: "/insights/activity", view: "activity", btn: "activityBtn" },
    ] },
  ];
  // Routable but not in the sidebar: the home, the queue, the settings family and
  // the two other workspaces. Every legacy view keeps an address (nothing is lost).
  // The four pipeline stages: routes and tabs, not sidebar items.
  const STAGES = [
    { key: "toorder", label: "To order", icon: "light-bulb", path: "/fulfillment/to-order", view: "needorder", btn: "needBtn", badge: "toorder" },
    { key: "incart", label: "In cart", icon: "shopping-cart", path: "/fulfillment/in-cart", view: "incart", btn: "cartBtn", badge: "incart" },
    { key: "po", label: "Purchase orders", icon: "shopping-bag", path: "/fulfillment/purchase-orders", view: "purchases", btn: "poBtn" },
    { key: "pkgprep", label: "Package prep", icon: "gift", path: "/fulfillment/package-prep", view: "pkgprep", btn: "pkgprepBtn", badge: "pkgprep" },
  ];
  const EXTRA = STAGES.concat([
    { key: "overview", label: "Overview", icon: "squares-2x2", path: "/overview", view: "brain", btn: "brainBtn" },
    { key: "attention", label: "Needs attention", icon: "bell-alert", path: "/attention", view: "attention" },
    { key: "settings", label: "Settings", icon: "cog-6-tooth", path: "/settings", view: "settings", btn: "settingsBtn" },
    { key: "team", label: "Team", icon: "user-circle", path: "/settings/team", view: "team", btn: "teamBtn" },
    { key: "trash", label: "Trash", icon: "trash", path: "/settings/trash", view: "trash", btn: "trashBtn" },
    { key: "leluxe", label: "Leluxe", icon: "briefcase", path: "/leluxe", view: "leluxe", btn: "leluxeBtn" },
    { key: "platoverview", label: "Overview", icon: "chart-bar", path: "/tatabu", view: "platoverview" },
    { key: "brokers", label: "Brokers", icon: "building-office", path: "/tatabu/brokers", view: "brokers" },
    { key: "brokerprofile", label: "Broker", icon: "building-office", path: "/tatabu/broker", view: "brokerprofile" },
    { key: "plans", label: "Plans & pricing", icon: "credit-card", path: "/tatabu/plans", view: "plans" },
    { key: "usage", label: "Usage", icon: "signal", path: "/tatabu/usage", view: "usage" },
    { key: "platactivity", label: "Platform activity", icon: "clock", path: "/tatabu/activity", view: "platactivity" },
    // Flags dissolves into Needs attention, but its inbox setup still lives on its own
    // page until Settings gains an Integrations section (Phase 7) - so it keeps an address.
    { key: "flags", label: "Watched inboxes", icon: "at-symbol", path: "/settings/inboxes", view: "flags", btn: "flagsBtn" },
  ]);
  const GROUP_OF = {};
  NAV.forEach((g) => g.items.forEach((it) => { GROUP_OF[it.key] = g; }));
  const ALL = NAV.reduce((a, g) => a.concat(g.items), []).concat(EXTRA);
  const BY_VIEW = {}, BY_PATH = {};
  ALL.forEach((it) => { if (it.view && !BY_VIEW[it.view]) BY_VIEW[it.view] = it; BY_PATH[it.path] = it; });
  // "/fulfillment" with no stage means "the stage you were last on" (the owner's daily
  // page stays one click away even though the pipeline is a single nav item).
  const stageOf = (key) => STAGES.find((s) => s.key === key) || STAGES[0];
  BY_PATH["/fulfillment"] = { get view() { return stageOf(store.get("ds_stage", "po")).view; },
    get path() { return stageOf(store.get("ds_stage", "po")).path; }, key: "fulfil", label: "Fulfillment" };

  // Sub-tabs a route can address. Each one is "the page's own tab strip", so a
  // link can land on GAASH mail's Docs tab or the Purchases packages board.
  const TABS = {
    gaashmail: { keys: ["conv", "ov", "seq", "tpl", "ready", "docs", "fcast", "dash"],
      labels: { conv: "Conversations", ov: "Overview", seq: "Workflows", tpl: "Templates", ready: "Readiness", docs: "Docs", fcast: "Forecast", dash: "Analyze" },
      get: () => A().gmTab, set: (t) => window.gmTab && window.gmTab(t) },
    purchases: { keys: ["orders", "packages", "products", "customers"],
      labels: { orders: "Orders", packages: "Packages", products: "Products", customers: "Customers" },
      get: () => A().poBoard, set: (t) => window.poSetView && window.poSetView(t) },
    needorder: { keys: ["pending", "incart", "ordered", "deleted"],
      labels: { pending: "To order", incart: "In cart", ordered: "Ordered", deleted: "Deleted" },
      get: () => A().neView, set: (t) => window.neSetFilter && window.neSetFilter(t) },
    pkgprep: { keys: ["ready", "waiting", "reviews"],
      labels: { ready: "Ready to pack", waiting: "Waiting for pieces", reviews: "Ask for a review" },
      get: () => A().ppView, set: (t) => window.ppSetView && window.ppSetView(t) },
    leluxe: { keys: ["orders", "products", "packages", "dashboard", "goal"],
      labels: { orders: "Orders", products: "Products", packages: "Packages", dashboard: "Board", goal: "Goal" },
      get: () => A().lxView, set: (t) => window.lxSetView && window.lxSetView(t) },
  };

  // ---------------------------------------------------------------- router (D4)
  let applying = false;   // guards the setView <-> hashchange loop

  function parse(hash) {
    const raw = String(hash || "").replace(/^#\/?/, "");
    if (!raw) return null;
    const parts = raw.split("/").filter(Boolean);
    for (let n = parts.length; n > 0; n--) {
      const it = BY_PATH["/" + parts.slice(0, n).join("/")];
      if (it) return { item: it, rest: parts.slice(n) };
    }
    // Pre-Phase-2 links and bare view ids: "#purchases" still works.
    const it = BY_VIEW[parts[0]];
    return it ? { item: it, rest: parts.slice(1) } : null;
  }

  S.href = (view, tab) => {
    const it = BY_VIEW[view];
    if (!it) return "#/sales/orders";
    const t = TABS[view];
    return "#" + it.path + (tab && t && t.keys.indexOf(tab) >= 0 ? "/" + tab : "");
  };
  S.go = (path) => { location.hash = path.charAt(0) === "#" ? path : "#" + path; };

  /** Hash changed (a click, the back button, a pasted link) -> show that page. */
  function applyHash() {
    if (applying) return;
    const r = parse(location.hash);
    if (!r) { syncHash(); return; }
    applying = true;
    try {
      if (A().view !== r.item.view) window.setView(r.item.view);
      const t = TABS[r.item.view];
      if (t && r.rest[0] && t.keys.indexOf(r.rest[0]) >= 0 && t.get() !== r.rest[0]) t.set(r.rest[0]);
    } finally { applying = false; }
    // Moved routes: "#purchases" and other pre-Phase-2 links land on the page and
    // then get rewritten to the canonical address, so what the user copies is the
    // address the app documents. replaceState keeps the back button honest.
    const canon = S.href(r.item.view, r.rest[0]);
    if (location.hash !== canon) { try { history.replaceState(null, "", location.pathname + location.search + canon); } catch (e) { /* file:// */ } }
    paint();
  }

  /** The app navigated on its own -> make the address bar tell the truth. */
  function syncHash() {
    const v = A().view || "orders";
    const t = TABS[v];
    const want = S.href(v, t && t.get());
    if (location.hash !== want) { applying = true; try { location.hash = want; } finally { setTimeout(() => { applying = false; }, 0); } }
  }

  // ---------------------------------------------------------------- gates + badges
  /** A nav item is visible exactly when its legacy button is — one source of truth
      for every role, feature and tenancy gate (applyRole() in index.html). */
  function visible(it) {
    if (!it.btn) return true;
    const b = $(it.btn);
    return !!b && b.style.display !== "none";
  }
  const COUNTS = { toorder: null, incart: null, pkgprep: null, attention: null, fulfil: null };
  function counts() {
    const d = A().data;
    if (d && d.summary && d.summary.by_status) {
      const s = d.summary.by_status;
      COUNTS.toorder = (s.REQUESTED || 0) + (s.QUOTED || 0) + (s.PAID || 0) || null;
      COUNTS.incart = s.IN_CART || null;
      // The pipeline's own badge is the work waiting in it; the tabs break it down.
      COUNTS.fulfil = (COUNTS.toorder || 0) + (COUNTS.incart || 0) || null;
    }
    return COUNTS;
  }

  // ---------------------------------------------------------------- rendering
  function navItems(group) {
    const c = counts();
    return group.items.filter(visible).map((it) => ({
      key: it.key, label: it.label, icon: it.icon,
      badge: it.badge && c[it.badge] ? String(c[it.badge]) : null,
      active: A().view === it.view,
      href: "#" + it.path,
    }));
  }

  function brand() {
    const b = document.querySelector("#sidebar .side-brand");
    return b ? `<a href="#/overview" class="ds-brand-link">${b.innerHTML}</a>` : "";
  }

  // The Tatabu console is its own shell mode (D11): same chrome, its own five pages,
  // reached from (and left through) the workspace switcher.
  const PLAT = EXTRA.filter((it) => it.path.indexOf("/tatabu") === 0 && it.key !== "brokerprofile");

  let sig = "";
  function render(force) {
    const plat = !!A().platform;
    const groups = plat
      ? [{ key: "platform", label: "Platform", open: true, items: PLAT.map((it) => ({ key: it.key, label: it.label, icon: it.icon, href: "#" + it.path })) }]
      : NAV.map((g) => ({ key: g.key, label: g.label, open: store.get("ds_g_" + g.key, "1") === "1", items: navItems(g) })).filter((g) => g.items.length);
    const home = !plat && visible(BY_VIEW.brain) ? [{ key: "overview", label: "Overview", icon: "squares-2x2", href: "#/overview" }] : [];
    // Rebuilding the sidebar on every navigation would drop focus and flash; the
    // signature says whether anything a user can see has actually changed.
    const next = JSON.stringify([plat, groups, home, COUNTS.attention, (A().me || {}).username]);
    if (next === sig && !force) return;
    sig = next;
    const html = D.sidebar({
      brand: brand(),
      groups: (home.length ? [{ key: "home", items: home }] : []).concat(groups),
      attention: plat ? null : { label: "Needs attention", count: COUNTS.attention || null, onclick: "DS.shell2.go('/attention')" },
      workspace: { name: S.workspace(), onclick: "DS.shell2.workspaceMenu(this)" },
      user: userChip(),
    });
    $("dsSidebar").outerHTML = html.replace('<aside class="ds-sidebar"', '<aside class="ds-sidebar" id="dsSidebar"');
    $("dsSidebar").querySelectorAll("details").forEach((d) => d.addEventListener("toggle", () => store.set("ds_g_" + d.dataset.group, d.open ? "1" : "0")));
  }

  function userChip() {
    const me = A().me || {};
    if (!me.username) return null;
    return { name: me.name || me.username, role: me.role || "", onclick: "DS.shell2.userMenu(this)" };
  }

  S.workspace = () => (A().platform ? "Tatabu" : A().view === "leluxe" || A().view === "goals" ? "Leluxe" : "Otlobly");
  S.exitPlatform = () => { if (window.exitPlatform) window.exitPlatform(); S.go("/sales/orders"); };

  S.workspaceMenu = (btn) => {
    const items = [{ label: "Otlobly", icon: "building-storefront", onclick: A().platform ? "DS.shell2.exitPlatform()" : "DS.shell2.go('/sales/orders')" }];
    if (visible(BY_VIEW.leluxe)) items.push({ label: "Leluxe", icon: "briefcase", onclick: "DS.shell2.go('/leluxe')" });
    if ($("brokersBtn") && $("brokersBtn").style.display !== "none") items.push({ label: "Tatabu console", icon: "building-office", onclick: "DS.shell2.go('/tatabu')" });
    D.menuOpenAt(items, btn.getBoundingClientRect().left, btn.getBoundingClientRect().top - 8, { align: "start", label: "Workspace" });
  };

  S.userMenu = (btn) => {
    const items = [];
    if (visible(BY_VIEW.settings)) items.push({ label: "Settings", icon: "cog-6-tooth", onclick: "DS.shell2.go('/settings')" });
    if (visible(BY_VIEW.team)) items.push({ label: "Team", icon: "users", onclick: "DS.shell2.go('/settings/team')" });
    if (visible(BY_VIEW.trash)) items.push({ label: "Trash", icon: "trash", onclick: "DS.shell2.go('/settings/trash')" });
    items.push({ divider: true }, { label: "Keyboard shortcuts", icon: "question-mark-circle", kbd: "?", onclick: "DS.shortcutsSheet()" });
    items.push({ label: "Switch to the classic layout", icon: "arrow-uturn-left", onclick: "DS.shell2.set(false)" });
    items.push({ divider: true }, { label: "Sign out", icon: "arrow-right-circle", danger: true, onclick: "location.href='/logout'" });
    const r = btn.getBoundingClientRect();
    D.menuOpenAt(items, r.left, r.top - 8, { align: "start", label: "Account" });
  };

  /** The top bar. The bell, "Add order" and "Refresh" are the LEGACY nodes, moved
      here once — re-creating them would mean re-creating notifOpen's menu too. */
  function topbar() {
    const bar = $("dsTopbar");
    bar.innerHTML = D.topbar({
      menuToggle: "document.body.classList.toggle('nav-open')",
      search: { id: "dsSearch", placeholder: "Search orders, customers, PO, GWD…", oninput: "DS.shell2.search(this.value)", onkeydown: "DS.shell2.searchKey(event)" },
      notifications: null, language: { hidden: true },
      extra: ['<span id="dsTopSlot" class="ds-topslot"></span>'],
    }) + '<div class="ds-menu-list ds-search-results" id="dsSearchResults" role="listbox" aria-label="Search results"></div>';
    const slot = $("dsTopSlot");
    ["notifBell", "addOrderBtn", "refreshBtn"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      slot.appendChild(el.closest(".pop") || el);   // the bell travels with its menu
    });
  }

  // ---------------------------------------------------------------- page header
  /** Views that render their own DS.pageHeader (docs/ux-restructure Phase 3+). */
  const OWN_HEADER = new Set(["purchases", "needorder", "incart", "pkgprep", "orders", "customers"]);

  function paint() {
    const v = A().view || "orders";
    const it = BY_VIEW[v] || { label: v, key: v };
    const g = GROUP_OF[it.key];
    const t = TABS[v];
    const stage = STAGES.some((s) => s.view === v);
    // The four stages share one nav item, so any of them lights up "Fulfillment".
    const navKey = stage ? "fulfil" : it.key;
    document.querySelectorAll("#dsSidebar .ds-nav-item").forEach((b) => {
      b.dataset.nav === navKey ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
    });
    let below = "";
    if (stage) {
      const c = counts();
      below = D.tabs({ id: "dsStages", variant: "pills", ariaLabel: "Fulfillment stages",
        items: STAGES.filter(visible).map((s) => ({ key: s.key, label: s.label, count: s.badge && c[s.badge] ? c[s.badge] : null })),
        active: it.key, onchange: "DS.shell2.stage(KEY)" });
    } else if (t) {
      below = D.tabs({ id: "dsTabs", ariaLabel: it.label + " tabs",
        items: t.keys.map((k) => ({ key: k, label: t.labels[k] })), active: t.get(),
        onchange: "DS.shell2.tab(KEY)" });
    }
    if (stage) store.set("ds_stage", it.key);
    const sub = v === "orders" && $("sub") ? $("sub").textContent : "";
    // A migrated page draws its own header (breadcrumb, title, numbers, actions), so
    // the shell must not draw a second one over it - it contributes only the tabs
    // that are navigation. Pages join this set as Phase 4 migrates them.
    $("dsPageHead").innerHTML = OWN_HEADER.has(v)
      ? below
      : D.pageHeader({
        crumbs: stage ? [{ label: "Fulfillment" }, { label: it.label }] : g ? [{ label: g.label }, { label: it.label }] : [],
        title: it.label, below,
      }) + (sub ? `<p class="ds-pagesub">${esc(sub)}</p>` : "");
    if (v === "attention") attnRender();
  }

  S.stage = (key) => { const s = STAGES.find((x) => x.key === key); if (s) { store.set("ds_stage", key); S.go(s.path); } };
  S.tab = (key) => { const t = TABS[A().view]; if (t) { t.set(key); syncHash(); } };
  /** A page switched one of its own tabs without going through the shell - make the
      address say so, so the tab a link points at is the tab that opens. */
  S.syncTab = () => { if (S.enabled()) syncHash(); };

  // ---------------------------------------------------------------- global search
  let POS_CACHE = null, SEARCH_ROWS = [], SEARCH_I = -1;
  async function ensurePos() {
    if (POS_CACHE !== null) return POS_CACHE;
    POS_CACHE = A().pos || [];
    if (!POS_CACHE.length) {
      try {
        const r = await fetch("/api/purchases");
        POS_CACHE = r.ok ? ((await r.json()).purchase_orders || []) : [];
      } catch (e) { POS_CACHE = []; }
    }
    return POS_CACHE;
  }

  function results(q) {
    const s = q.trim().toLowerCase();
    if (s.length < 2) return [];
    const hit = (v) => String(v || "").toLowerCase().indexOf(s) >= 0;
    const out = [];
    ALL.filter((it) => visible(it) && hit(it.label)).slice(0, 3)
      .forEach((it) => out.push({ icon: it.icon, label: it.label, meta: "Page", path: it.path }));
    const d = A().data;
    (d && d.orders || []).filter((o) => hit(o.order_id) || hit(o.customer) || hit(o.phone) || hit(o.amazon_order_number) || hit(o.tracking_number)).slice(0, 5)
      .forEach((o) => out.push({ icon: "clipboard-document-list", label: o.order_id + (o.customer ? " \u00b7 " + o.customer : ""), meta: "Order", path: "/sales/orders" }));
    (POS_CACHE || []).forEach((p) => {
      if (out.length > 14) return;
      if (hit(p.po_id) || hit(p.amazon_order_number) || hit(p.ship_to)) out.push({ icon: "shopping-bag", label: p.po_id + (p.ship_to ? " · " + p.ship_to : ""), meta: "Purchase order", path: "/fulfillment/purchase-orders" });
      (p.packages || []).forEach((pk) => {
        if (hit(pk.tracking_number) || hit(pk.customer_tracking)) out.push({ icon: "map-pin", label: pk.tracking_number || pk.customer_tracking, meta: p.po_id + " · package " + pk.package_no, path: "/fulfillment/purchase-orders" });
      });
    });
    if (/^[a-z]*\d{4,}$/i.test(q.trim())) out.push({ icon: "magnifying-glass", label: "Look up " + q.trim() + " in Tracking", meta: "Tracking", path: "/shipping/tracking" });
    return out.slice(0, 8);
  }

  S.search = (q) => {
    if (q.trim().length >= 2) ensurePos().then(() => { if ($("dsSearch").value === q) paintResults(q); });
    paintResults(q);
  };
  function paintResults(q) {
    SEARCH_ROWS = results(q); SEARCH_I = SEARCH_ROWS.length ? 0 : -1;
    const box = $("dsSearchResults");
    if (!SEARCH_ROWS.length) { box.dataset.open = ""; box.innerHTML = ""; return; }
    box.dataset.open = "1";
    box.innerHTML = SEARCH_ROWS.map((r, i) => `<button type="button" role="option" class="ds-menu-item${i === SEARCH_I ? " ds-menu-item-on" : ""}" onclick="DS.shell2.pick(${i})">${D.icon(r.icon)}<span class="ds-truncate">${esc(r.label)}</span><span class="ds-muted">${esc(r.meta)}</span></button>`).join("");
  }
  S.pick = (i) => {
    const r = SEARCH_ROWS[i]; if (!r) return;
    $("dsSearch").value = ""; $("dsSearchResults").dataset.open = ""; $("dsSearchResults").innerHTML = "";
    S.go(r.path);
  };
  S.searchKey = (e) => {
    if (e.key === "Escape") { e.target.value = ""; paintResults(""); e.target.blur(); return; }
    if (!SEARCH_ROWS.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); SEARCH_I = (SEARCH_I + (e.key === "ArrowDown" ? 1 : SEARCH_ROWS.length - 1)) % SEARCH_ROWS.length;
      paintResultsKeepQuery();
    } else if (e.key === "Enter") { e.preventDefault(); S.pick(SEARCH_I < 0 ? 0 : SEARCH_I); }
  };
  function paintResultsKeepQuery() {
    const box = $("dsSearchResults");
    box.querySelectorAll(".ds-menu-item").forEach((b, i) => b.classList.toggle("ds-menu-item-on", i === SEARCH_I));
  }

  // ---------------------------------------------------------------- Needs attention
  let ATTN = null, ATTN_AT = 0;
  S.attnLoad = async (force) => {
    if (!force && ATTN && Date.now() - ATTN_AT < 60000) return ATTN;
    try {
      const r = await fetch("/api/attention");
      ATTN = r.ok ? await r.json() : { count: 0, groups: [] };
    } catch (e) { ATTN = { count: 0, groups: [], error: true }; }
    ATTN_AT = Date.now();
    COUNTS.attention = ATTN.count || null;
    const badge = document.querySelector('#dsSidebar [data-nav="attention"] .ds-nav-badge');
    if (badge) badge.textContent = ATTN.count || "";
    else render(true);
    if (A().view === "attention") attnRender();
    return ATTN;
  };

  function attnRender() {
    const host = $("attentionView");
    if (!host) return;
    if (!ATTN) { host.innerHTML = D.skeleton({ rows: 6 }); S.attnLoad(); return; }
    if (!ATTN.count) {
      host.innerHTML = D.empty({ icon: "check-circle", title: "Nothing needs attention", text: "No action-required email, no package past its date, no missing tracking number and no document request." });
      return;
    }
    // Flags dissolved into this queue; the page that manages the watched inboxes is
    // still where it was, so the group that comes from it links there.
    const more = { flags: { label: "Manage watched inboxes", path: "/settings/inboxes" },
      docs: { label: "Open the Docs tab", path: "/shipping/gaash-mail/docs" } };
    host.innerHTML = ATTN.groups.map((g) => `<section class="ds-attn-group"><h2>${esc(g.label)} <span class="ds-count">${g.count}</span>${more[g.key] ? `<a class="ds-attn-more" href="#${more[g.key].path}">${esc(more[g.key].label)}</a>` : ""}</h2>${g.items.map((i) =>
      `<button type="button" class="ds-attn-row" onclick="DS.shell2.open(${esc(JSON.stringify(i.link || {}))})">
        ${D.attention({ kind: i.kind })}
        <span class="ds-attn-text"><span class="ds-attn-title"><bdi>${esc(i.title)}</bdi></span>${i.detail ? `<span class="ds-muted"><bdi>${esc(i.detail)}</bdi></span>` : ""}</span>
        ${i.age_days != null ? `<span class="ds-muted ds-attn-age">${esc(i.age_days)}d</span>` : ""}
        ${D.icon("chevron-right", { size: 14 })}
      </button>`).join("")}${g.count > g.items.length ? `<p class="ds-muted">and ${g.count - g.items.length} more</p>` : ""}</section>`).join("");
  }
  S.open = (link) => {
    const it = BY_VIEW[(link || {}).view];
    S.go(it ? it.path : "/sales/orders");
  };
  S.refreshAttention = () => S.attnLoad(true);

  // ---------------------------------------------------------------- boot
  function hosts() {
    const side = document.getElementById("sidebar");
    side.insertAdjacentHTML("afterend", '<aside class="ds-sidebar" id="dsSidebar"></aside>');
    const top = document.querySelector(".main > .topbar");
    top.insertAdjacentHTML("afterend", '<div id="dsTopbar"></div>');
    const wrap = document.querySelector(".wrap");
    wrap.insertAdjacentHTML("afterbegin", '<div id="dsPageHead"></div>');
  }

  function boot() {
    if (!S.enabled() || !window.setView || document.getElementById("dsSidebar")) return;
    document.body.classList.add("ds-shell-on", "ds-root");
    hosts();
    topbar();
    render();
    // Every navigation, wherever it came from, lands here: address bar, active
    // item and page header stay in step with the app instead of drifting.
    // The app finishes booting AFTER this runs (load() awaits /api/me, then calls
    // setView(restoreView())), so a deep link would be thrown away by the app's own
    // restore. `pending` lets the URL win exactly once, which is what a link means.
    let pending = parse(location.hash);
    const inner = window.setView;
    window.setView = function (v) {
      const out = inner.apply(this, arguments);
      try {
        // `applying` means this call came from the router itself - there is nothing to
        // reconcile, and consuming `pending` here would hand the page back to the app's
        // restore a moment later.
        if (applying) { render(); paint(); return out; }
        if (pending) {
          const want = pending.item.view; pending = null;
          if (A().view !== want) { applyHash(); return out; }
        }
        render(); paint(); syncHash();
      } catch (e) { /* never break navigation */ }
      return out;
    };
    window.addEventListener("hashchange", applyHash);
    D.installShortcuts();
    if (location.hash) applyHash(); else { paint(); syncHash(); }
    S.attnLoad();
    setInterval(() => S.attnLoad(true), 5 * 60 * 1000);
    // The subtitle and the counts arrive with /api/report, after the first paint.
    const tick = setInterval(() => { if (A().data) { clearInterval(tick); render(); paint(); } }, 400);
    setTimeout(() => clearInterval(tick), 30000);
  }

  /** The one control the classic shell shows: a way in. Rendered by this file so
      index.html carries nothing about a shell it does not use. */
  function offerSwitch() {
    if (S.enabled()) return;
    const bar = document.querySelector(".main > .topbar");
    if (!bar || document.getElementById("dsTryBtn")) return;
    const b = document.createElement("button");
    b.id = "dsTryBtn"; b.className = "iconbtn"; b.type = "button";
    b.title = "Try the new navigation — grouped menu, search, page addresses. You can switch back any time.";
    b.style.cssText = "font-size:12px;font-weight:700;padding-inline:10px";
    b.textContent = "New layout";
    b.onclick = () => S.set(true);
    bar.insertBefore(b, document.getElementById("langToggle"));
  }

  const q = (() => { try { return new URLSearchParams(location.search).get("shell"); } catch (e) { return null; } })();
  if (q === "new") store.set(FLAG, "ds");
  if (q === "old") store.del(FLAG);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { boot(); offerSwitch(); });
  else { boot(); offerSwitch(); }
})();
