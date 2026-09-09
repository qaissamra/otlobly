/* static/ds/gaash.js - GAASH mail on the design system (docs/ux-restructure Batch C).

   The page's eight tabs, one module. Every LIST surface of the page lives here:
   the header and safe-mode strip, the page's own tab strip (for the classic
   shell), and one DataTable per tab - Conversations, Overview, Workflows (with
   its enrolment expansion and the triggers table), Readiness, Docs, Forecast
   (queue + cases), Templates and Analyze.

   What stays in web/index.html, on purpose: everything that FETCHES (gmLoad,
   gmRenderReady, gmDocsRender, gmFcastRender, gmCasesRender, gmDashLoad - the
   honest "couldn't load" paths are theirs), the thread body a conversation row
   opens into (gmChatHtml - a T2 detail, Phase 5), the workflow builder, the rule
   editor, the wizards and every modal. This file draws; index.html decides.

   index.html's top-level `let` bindings are not window properties, so each
   bridge hands the page a `ctx` with what it needs; legacy cell builders are
   `function` declarations and reachable as W.<name>. */
(function () {
  "use strict";
  const D = window.DS;
  if (!D) return;
  const G = (D.gaash = {});
  const esc = D.esc;
  const W = window;
  const num = (v) => D.fmt.number(v);
  const text = (v) => { v = (v == null ? "" : String(v)).trim(); return v ? `<span class="ds-truncate" dir="auto" title="${esc(v)}">${esc(v)}</span>` : D.dash(); };
  const mono = (v) => (v ? `<span class="ds-mono">${esc(v)}</span>` : D.dash());
  const muted = (v) => (v ? `<span class="ds-muted">${esc(v)}</span>` : D.dash());
  const pct = (a, b) => (b ? Math.round(((a || 0) / b) * 100) + "%" : D.dash());
  const rel = (iso) => (iso ? `<span title="${esc(D.fmt.title(iso))}">${esc(D.fmt.relative(iso))}</span>` : D.dash());
  const when = (iso) => (iso ? `<span class="ds-num" title="${esc(D.fmt.title(iso))}">${esc(D.fmt.datetime(iso))}</span>` : D.dash());
  const day = (iso) => (iso ? `<span class="ds-mono" title="${esc(D.fmt.title(iso))}">${esc(String(iso).slice(0, 10))}</span>` : D.dash());
  const q = (s) => JSON.stringify(String(s == null ? "" : s));
  const hostOf = (h) => (typeof h === "string" ? document.getElementById(h) : h);
  /** Paint a table (plus any chrome above it) without stealing the caret from a search
      box or the reply box inside an open conversation. */
  const paintTable = (el, before, o, after) => {
    D.paintHost(el, (before || "") + D.table(o) + (after || ""));
    D.tableMount(o.id);
  };
  const DONE = ["goal_met", "done", "cleared"];
  const isDone = (t) => DONE.includes(t.state);
  const srcLabel = (s) => (s === "leluxe" ? "Leluxe" : s === "purchases" ? "Purchases" : s ? String(s) : "");
  /** The parcel identity cell: the GWD, which board it came from, and any extras. */
  const parcel = (gwd, source, extra) => `<span class="ds-gm-id"><b class="ds-mono">${esc(gwd || "")}</b>${source ? `<span class="ds-gm-src">${esc(srcLabel(source))}</span>` : ""}${extra || ""}</span>`;
  const iconBtn = (icon, label, onclick, o) => D.button(Object.assign({ icon, size: "sm", variant: "ghost", iconOnly: true, ariaLabel: label, title: label, onclick }, o || {}));
  const openConv = (gwd) => iconBtn("chat-bubble-left-right", "Open the conversation", `gmTab('conv');gmOpen(${q(gwd)})`);
  const enrollBtn = (gwd) => iconBtn("envelope", "Enroll in a workflow", `gmNewOpen([${q(gwd)}])`);

  // ---------------------------------------------------------------- tabs
  G.TABS = [
    { key: "conv", label: "Conversations" }, { key: "ov", label: "Overview" }, { key: "seq", label: "Workflows" },
    { key: "ready", label: "Readiness" }, { key: "docs", label: "Docs" }, { key: "fcast", label: "Forecast" },
    { key: "tpl", label: "Templates" }, { key: "dash", label: "Analyze" },
  ];
  const TAB_ID = { conv: "gmTabConv", ov: "gmTabOv", seq: "gmTabSeq", ready: "gmTabReady", docs: "gmTabDocs", fcast: "gmTabFcast", tpl: "gmTabTpl", dash: "gmTabDash" };
  /** The page's own strip. The new shell draws the same eight tabs above the page and
      hides this one (ds.css `body.ds-shell-on #gmTabs`); the classic layout still needs it. */
  G.tabs = (host, active) => {
    const el = hostOf(host); if (!el) return;
    el.innerHTML = D.tabs({ id: "gmTabStrip", ariaLabel: "GAASH mail tabs", active,
      items: G.TABS.map((t) => ({ key: t.key, label: t.label, id: TAB_ID[t.key] })), onchange: "gmTab(KEY)" });
  };

  // ---------------------------------------------------------------- header + the safe-mode strip
  function banner(ctx) {
    const out = [];
    const e = ctx.acctErr;
    if (e) {
      out.push(D.callout({ tone: "warning", icon: "key", title: String(e.last_error || ""),
        html: `<b dir="ltr">${esc(e.email || "")}</b>: ${esc(String(e.last_error || "").slice(0, 120))}`,
        actions: ctx.canAdmin ? [{ label: "Update password", icon: "key", size: "sm", onclick: `gmSettingsOpen();gmAcctFix(${q(e.email || "")})` }] : [] }));
    }
    const locks = [];
    if (ctx.frozen) locks.push("all workflows Off");
    if (ctx.dryRun) locks.push("dry-run");
    if (!(ctx.accounts || []).length) locks.push("no Gmail account yet");
    if (!ctx.mailerEnv) locks.push("auto-sequencer off on this server");
    // The one big switch sits beside the state it changes, not in a toolbar.
    const freeze = ctx.canAdmin && ctx.seqsLive
      ? [{ label: ctx.frozen ? "Resume all" : "Freeze all", icon: ctx.frozen ? "play" : "pause", size: "sm",
        variant: ctx.frozen ? "secondary" : "danger", onclick: "gmFreeze(this)",
        title: ctx.frozen ? "Turn every workflow back On" : "Turn every workflow Off - nothing sends or enrolls until you resume" }]
      : [];
    out.push(locks.length
      ? D.callout({ tone: "neutral", icon: "lock-closed", html: `<b>Safe mode</b> · no email goes out by itself · ${locks.map(esc).join(" · ")} <span class="ds-muted">· everything else works, explore freely</span>`, actions: freeze })
      : D.callout({ tone: "success", icon: "check-circle", html: `<b>Sending is live</b> · real emails go out to <b dir="ltr">${esc(ctx.toAddress || "?")}</b>`, actions: freeze }));
    return out.join("");
  }
  /** ctx: {threads, proposed, accounts, seqsLive, frozen, dryRun, mailerEnv, toAddress, acctErr, canAdmin} */
  G.chrome = (host, ctx) => {
    const el = hostOf(host); if (!el) return;
    const th = ctx.threads || [];
    const n = (f) => th.filter(f).length;
    const unread = th.reduce((a, t) => a + (Number(t.unread) || 0), 0);
    const proposed = (ctx.proposed || []).length, docs = n((t) => t.state === "missing_docs" || t.missing_docs);
    const stats = [
      { label: "Conversations", value: num(th.length) },
      { label: "Active", value: num(n((t) => t.state === "active")) },
      { label: "Replies to read", value: num(unread), tone: unread ? "info" : null },
      { label: "Awaiting approval", value: num(proposed), tone: proposed ? "warning" : null },
      { label: "Missing documents", value: num(docs), tone: docs ? "danger" : null },
      { label: "Cleared", value: num(n(isDone)), tone: "success" },
    ];
    D.paintHost(el, D.pageHeader({
      crumbs: [{ label: "Shipping" }, { label: "GAASH mail" }],
      title: "GAASH mail", stats,
      primary: { label: "Enroll packages", icon: "envelope", onclick: "gmNewOpen()", title: "Pick parcels and start their clearance emails" },
      secondary: [
        { label: "Check replies", icon: "inbox", onclick: "gmCheck(this)", title: "Read the Gmail inboxes now" },
        { label: "Check tracking", icon: "truck", onclick: "gmTrackAll(this)", title: "Ask GAASH where every open parcel is" },
      ],
      overflow: [
        { label: "What the checks moved", icon: "clock", onclick: "gmChangesOpen()" },
        { label: "Accounts & templates", icon: "cog-6-tooth", onclick: "gmSettingsOpen()" },
      ],
      below: banner(ctx),
    }));
  };

  // ---------------------------------------------------------------- conversations
  /** ctx: {cur, view, query, quick, counts{open,cleared,all,unread,proposed,docs}, multi, none,
            acctOf(t), seqOf(t), thumbs(t)->html, thread(t)->html, canAdmin} */
  G.conversations = (mount, rows, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const c = ctx.counts || {};
    const bar = D.filterBar({
      search: { id: "gmConvSearch", value: ctx.query || "", placeholder: "Search parcel, name, subject, last message", oninput: "gmConvSearch(this.value)" },
      views: [{ key: "open", label: "Open", count: c.open }, { key: "cleared", label: "Cleared", count: c.cleared }, { key: "all", label: "All", count: c.all }]
        .map((v) => Object.assign(v, { active: ctx.view === v.key })),
      onView: "gmConvView(KEY)",
      chips: [
        c.unread ? { label: "Unread replies", value: c.unread, icon: "envelope-open", active: ctx.quick === "unread", onclick: "gmConvQuick('unread')" } : null,
        c.proposed ? { label: "Awaiting approval", value: c.proposed, icon: "bolt", active: ctx.quick === "proposed", onclick: "gmConvQuick('proposed')" } : null,
        c.docs ? { label: "Missing documents", value: c.docs, icon: "document-text", active: ctx.quick === "docs", onclick: "gmConvQuick('docs')" } : null,
      ].filter(Boolean),
      clear: { show: !!(ctx.query || ctx.quick), onclick: "gmConvClear()" },
    });
    const total = (t) => (((ctx.seqOf && ctx.seqOf(t)) || {}).steps || []).length || 4;
    const acct = (t) => (ctx.acctOf ? ctx.acctOf(t) : null);
    const cols = [
      { key: "gwd", label: "Parcel", w: 210, pin: "start", locked: true, sortVal: (t) => t.gwd,
        render: (t) => parcel(t.gwd, t.source,
          ((t.members || []).length > 1 ? `<span class="ds-tag ds-tone-neutral" title="Grouped conversation · one email covers ${esc(t.members.join(", "))}"><span>+${esc(t.members.length - 1)}</span></span>` : "")
          + (t.unread ? `<span class="ds-gm-unread" title="${esc(t.unread)} unread ${t.unread === 1 ? "reply" : "replies"}">${esc(t.unread)}</span>` : "")) },
      { key: "products", label: "Products", w: 132, sortable: false, render: (t) => (ctx.thumbs ? ctx.thumbs(t) : "") || D.dash() },
      { key: "state", label: "Status", w: 150, sortVal: (t) => t.state || "~", render: (t) => D.status.badge("gmThread", t.state) },
      { key: "step", label: "Progress", w: 118, sortVal: (t) => (t.step || 0) / total(t),
        render: (t) => `<span class="ds-gm-prog">${W.gmDots ? W.gmDots(t.step || 0, total(t)) : ""}<span class="ds-muted ds-num">${esc(t.step || 0)}/${esc(total(t))}</span></span>` },
      { key: "name", label: "Name on parcel", w: 180, sortVal: (t) => t.pname || "~",
        render: (t) => (t.pname ? `<span class="ds-gm-name">${text(t.pname)}${t.pname_src === "default" ? `<span class="ds-muted" title="No board names this parcel - the name comes from the Settings default">default</span>` : ""}${t.pname_id ? "" : D.attention({ kind: "missing_id", title: t.source === "purchases" ? "Add the customer's ID number on their Customers page" : "Give this name an ID in Accounts & templates, or pick another name" })}</span>` : D.dash()) },
      { key: "gash", label: "GAASH status", w: 150, sortVal: (t) => ((t.gash || {}).gash_status || (t.gash || {}).label || "~"),
        render: (t) => (W.gmGashPill ? W.gmGashPill(Object.assign({}, t.gash || {}, { source: t.source }), true) : "") || D.dash() },
      { key: "last", label: "Last message", w: 320, sortVal: (t) => t.last_activity || "~",
        render: (t) => { const m = t.last_msg || {}; const body = m.body ? String(m.body) : (t.last_error ? "Error: " + t.last_error : "");
          return body ? `<span class="ds-truncate" dir="auto" title="${esc(body)}">${m.dir === "in" ? `<span class="ds-gm-in" title="A reply from GAASH">${D.icon("arrow-uturn-left", { size: 12 })}</span>` : ""}${esc(body)}</span>` : D.dash(); } },
      { key: "activity", label: "Last activity", w: 120, sortVal: (t) => t.last_activity || "~", render: (t) => rel(t.last_activity) },
      { key: "next", label: "Next email", w: 150, sortVal: (t) => (t.state === "active" && t.next_send_at) || "~", render: (t) => (t.state === "active" && t.next_send_at ? when(t.next_send_at) : D.dash()) },
      { key: "sender", label: "Sends from", w: 180, sortVal: (t) => ((acct(t) || {}).label || (acct(t) || {}).email || "~"),
        render: (t) => { const a = acct(t); if (!a) return D.attention({ kind: "no_reply", label: "No sender", title: "No sending account on this conversation - open it and pick one" });
          return `<span class="ds-truncate" dir="ltr" title="Sends from ${esc(a.email)}">${esc(a.label || a.email)}</span>`; } },
      { key: "reads", label: "Opened", w: 92, sortVal: (t) => ((t.reads || {}).opens || 0), render: (t) => (W.gmReadPill ? W.gmReadPill(t.reads) : "") || D.dash() },
      { key: "seq", label: "Workflow", w: 170, defaultHidden: true, sortVal: (t) => (((ctx.seqOf && ctx.seqOf(t)) || {}).name || "~"), render: (t) => text(((ctx.seqOf && ctx.seqOf(t)) || {}).name) },
      { key: "attention", label: "Needs attention", w: 200, sortable: false,
        render: (t) => { const out = [];
          if (t.missing_docs || t.state === "missing_docs") out.push({ kind: "missing_docs", detail: t.missing_note || "", title: "GAASH is waiting for a document" });
          if (t.state === "proposed") out.push({ kind: "stale", label: "Awaiting approval", tone: "warning", title: "A trigger proposed this parcel - approve it on the Workflows tab" });
          if (t.state === "exhausted") out.push({ kind: "no_reply", title: "Every email went out and nobody answered" });
          if (t.last_error) out.push({ kind: "conflict", label: "Send failed", title: t.last_error });
          return out.length ? out.map((a) => D.attention(a)).join("") : D.dash(); } },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false, menu: (t) => ctx.menu ? ctx.menu(t) : [] },
    ];
    const o = {
      id: "gm_conv", seedVersion: 1, ariaLabel: "GAASH mail conversations", columns: cols, rows, rowKey: (t) => t.gwd,
      expandable: { render: (t) => (ctx.thread ? ctx.thread(t) : ""), open: (t) => t.gwd === ctx.cur },
      onToggle: (key, open) => ctx.setCur && ctx.setCur(open ? key : null),
      onRowClick: (t) => ctx.setCur && ctx.setCur(t.gwd === ctx.cur ? null : t.gwd),
      rowClass: (t) => (t.unread ? "ds-gm-hasunread" : null),
      empty: ctx.none
        ? { title: "No conversations yet", text: "Enroll a package and its clearance emails start from here.", action: { label: "Enroll packages", icon: "envelope", onclick: "gmNewOpen()" } }
        : ctx.view === "open" && !ctx.query && !ctx.quick
          ? { title: "Nothing waiting", text: "Every parcel is done. Cleared conversations keep their history under the Cleared view." }
          : { title: "Nothing matches", text: "Clear the search, or switch the view." },
      footer: { gwd: `<b>${esc(num(rows.length))} conversations</b>`, reads: rows.some((t) => t.unread) ? `<span class="ds-num">${esc(num(rows.reduce((a, t) => a + (Number(t.unread) || 0), 0)))} unread</span>` : "" },
    };
    paintTable(el, bar, o);
  };

  // ---------------------------------------------------------------- overview
  /** ctx: {stats (GM.stats or null), seqs, onN} */
  G.overview = (mount, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const st = ctx.stats, o = st && st.overall;
    if (!o) { el.innerHTML = D.skeleton({ rows: 4 }); return; }
    const seqs = ctx.seqs || [];
    const kpi = (s, drill) => `<div class="ds-kpi${drill ? " ds-kpi-click" : ""}"${drill ? ` role="button" tabindex="0" onclick="gmStatDrill('${esc(drill)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.click()}" title="See every email behind this number"` : ""}>${D.stat(s)}</div>`;
    const kpis = `<div class="ds-kpis">${[
      kpi({ label: "Workflows", value: num(seqs.length) }),
      kpi({ label: "On", value: `${num(ctx.onN)} / ${num(seqs.length)}` }),
      kpi({ label: "Enrolled", value: num(o.enrolled) }),
      kpi({ label: "Last 7 days", value: num(o.enrolled_7d || 0) }),
      kpi({ label: "Active", value: num(o.active) }),
      kpi({ label: "Sent", value: num(o.sent) }, "sent"),
      kpi({ label: "Open rate", value: pct(o.opened, o.sent), hint: "approximate" }, "opened"),
      kpi({ label: "Reply rate", value: pct(o.replied, o.enrolled) }, "replied"),
      kpi({ label: "Goal rate", value: pct(o.goal_met, o.enrolled) }),
    ].join("")}</div>`;
    const stOf = (s) => ((st.sequences || []).find((y) => y.seq_id === s.id) || {});
    const cols = [
      { key: "name", label: "Workflow", w: 260, pin: "start", locked: true, sortVal: (s) => (s.name || "").toLowerCase(), render: (s) => `<b>${esc(s.name || "")}</b>` },
      { key: "onoff", label: "On / Off", w: 100, sortVal: (s) => (s.paused ? 1 : 0), render: (s) => D.badge({ label: s.paused ? "Off" : "On", tone: s.paused ? "neutral" : "success", size: "sm" }) },
      { key: "enrolled", label: "Enrolled", w: 100, align: "end", sortVal: (s) => stOf(s).enrolled || 0, render: (s) => `<span class="ds-num">${esc(num(stOf(s).enrolled || 0))}</span>` },
      { key: "active", label: "Active", w: 90, align: "end", sortVal: (s) => s.active_threads || 0, render: (s) => `<span class="ds-num">${esc(num(s.active_threads || 0))}</span>` },
      { key: "goal", label: "Goal met", w: 100, align: "end", sortVal: (s) => stOf(s).goal_met || 0, render: (s) => `<span class="ds-num">${esc(num(stOf(s).goal_met || 0))}</span>` },
      { key: "desc", label: "Description", w: 320, sortVal: (s) => s.description || "~", render: (s) => text(s.description) },
    ];
    el.innerHTML = kpis + `<h2 class="ds-gm-h2">Workflows</h2><div id="gmOvTable"></div>`;
    D.tableRender(el.querySelector("#gmOvTable"), { id: "gm_ov_wf", ariaLabel: "Workflows summary", columns: cols, rows: seqs, rowKey: (s) => s.id,
      onRowClick: (s) => { W.gmTab("seq"); W.gmSeqEdit(s.id); },
      empty: { title: "No workflows yet", text: "Create one on the Workflows tab." } });
  };

  // ---------------------------------------------------------------- workflows (+ the triggers table)
  /** Cells for one parcel model {kind:"po"|"lx"|"miss", tn, p, pk, lx} - the same facts the
      Bulk-search row shows, built for a DS.subTable instead of the LXT grid. */
  function modelCells(m, ctx) {
    if (m.kind === "miss") return { found: D.attention({ kind: "no_tracking", label: "Not found", title: "No board carries this tracking number" }) };
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
    const lxF = W.lxF || (() => null);
    const oname = (lx.order && lx.order.name) || (lx.pkgRow && lx.pkgRow.name) || "";
    const prof = items.map((it) => lxF(it, "name")).find(Boolean) || (lx.order && lxF(lx.order, "name")) || "";
    const gd = (lx.pkgRow && lxF(lx.pkgRow, "gash date")) || items.map((it) => lxF(it, "gash date")).find(Boolean);
    return {
      found: D.tag({ label: "Leluxe", tone: "neutral" }),
      oname: text(oname),
      profile: prof && W.lxProfileChip ? W.lxProfileChip(prof, `Profile / account (NAME): ${prof}`) : D.dash(),
      imgs: W.lxThumbs ? W.lxThumbs(items) : "",
      cust: `<span class="ds-muted">${esc(num(items.length))} products</span>`,
      status: (lx.sts || []).length ? lx.sts.map((s) => D.status.badge("pkg", s)).join(" ") : D.dash(),
      gash: (W.lxGashRollupPill && W.lxGashRollupPill(lx.gash)) || (lx.trk ? D.tag({ label: lx.trk, tone: "neutral" }) : D.dash()),
      gashdate: gd ? ((W.lxDueChip && W.lxDueChip(gd, true)) || muted(W.lxMs ? W.lxMs(gd) : gd)) : D.dash(),
      value: lx.tot ? `<b class="ds-num">₪${esc(Number(lx.tot).toLocaleString())}</b>` : D.dash(),
    };
  }
  const PARCEL_COLS = [
    { key: "found", label: "Found in", w: 150 }, { key: "oname", label: "Order name", w: 160 }, { key: "profile", label: "Profile", w: 128 },
    { key: "imgs", label: "Products", w: 150 }, { key: "cust", label: "Customer", w: 140 }, { key: "status", label: "Status", w: 150 },
    { key: "gash", label: "GAASH", w: 140 }, { key: "gashdate", label: "GASH date", w: 106 }, { key: "value", label: "Value", w: 100, align: "end" },
  ];
  /** The expansion under a workflow row: the parcels enrolled in it and the ones its triggers
      proposed. ctx: {enrolled:[{m, state}], proposed:[m], seqId, money} */
  G.wfExpansion = (ctx) => {
    const cols = (lead) => [{ label: "Parcel", w: 300, render: (r) => `${parcel(r.m.tn)}${W.lxCopyRawBtn ? W.lxCopyRawBtn(r.m.tn) : ""}${lead(r)}` }]
      .concat(PARCEL_COLS.map((c) => Object.assign({}, c, { render: (r) => r.cells[c.key] || D.dash() })));
    const rowsOf = (list) => { const seen = new Set(); return list.map((x) => ({ m: x.m || x, state: x.state, first: !seen.has((x.m || x).tn) && !!seen.add((x.m || x).tn), cells: modelCells(x.m || x, ctx) })); };
    let html = "";
    if ((ctx.enrolled || []).length) {
      const rows = rowsOf(ctx.enrolled);
      html += D.subTable(cols((r) => (r.first ? `<span class="ds-gm-lead">${D.status.badge("gmThread", r.state)}${openConv(r.m.tn)}</span>` : "")), rows, `Enrolled · ${num(rows.filter((r) => r.first).length)}`);
    }
    if ((ctx.proposed || []).length) {
      const rows = rowsOf(ctx.proposed);
      html += `<div class="ds-gm-sub-h"><b>Suggested · awaiting your approval</b>${D.button({ label: "Approve all", icon: "check", size: "sm", variant: "secondary", onclick: `gmPropAllSeq(${q(ctx.seqId)},'approve')` })}${D.button({ label: "Dismiss all", icon: "x-mark", size: "sm", variant: "ghost", onclick: `gmPropAllSeq(${q(ctx.seqId)},'dismiss')` })}</div>`
        + D.subTable(cols((r) => (r.first ? `<span class="ds-gm-lead">${iconBtn("check", "Approve and start", `event.stopPropagation();gmPropAct(${q(r.m.tn)},'approve')`)}${iconBtn("x-mark", "Dismiss", `event.stopPropagation();gmPropAct(${q(r.m.tn)},'dismiss')`)}</span>` : "")), rows, `Suggested · ${num(rows.filter((r) => r.first).length)}`);
    }
    return html || D.empty({ icon: "envelope", title: "No enrollments yet", text: "Enroll parcels with the Enroll packages button, or let a trigger propose them." });
  };
  /** ctx: {q, state, counts{all,on,off,trig}, rules, matches{ruleId:{count}}, canAdmin, seqName(id),
            ruleSummary(cond) -> already-escaped html, propOf(id)->[gwd], isOpen(id), setOpen(id,open), expansion(id)->html,
            seqDays(s), loading} */
  G.workflows = (mount, rows, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const c = ctx.counts || {};
    const bar = D.filterBar({
      search: { id: "gmWfSearch", value: ctx.q || "", placeholder: "Search workflows", oninput: "gmWfSearch(this.value)" },
      views: [{ key: "all", label: "All", count: c.all }, { key: "on", label: "On", count: c.on }, { key: "off", label: "Off", count: c.off }, { key: "trig", label: "Has trigger", count: c.trig }]
        .map((v) => Object.assign(v, { active: (ctx.state || "all") === v.key })),
      onView: "gmWfState(KEY)",
      right: ctx.canAdmin ? [D.button({ label: "New workflow", icon: "plus", variant: "secondary", size: "sm", onclick: "gmSeqNew()" })] : [],
    });
    const matchBtn = (ruleId) => { const m = (ctx.matches || {})[ruleId]; if (!m) return "";
      return D.button({ label: String(m.count), icon: "bolt", size: "sm", variant: m.count ? "secondary" : "ghost", title: "Packages matching this trigger now · click to see them with full columns", onclick: `event.stopPropagation();gmMatchesViewRule(${q(ruleId)})` }); };
    const cols = [
      { key: "name", label: "Workflow", w: 250, pin: "start", locked: true, sortVal: (r) => (r.s.name || "").toLowerCase(),
        render: (r) => `<span class="ds-gm-id"><b class="ds-truncate" title="${esc(r.s.name || "")}">${esc(r.s.name || "")}</b>${ctx.propOf(r.s.id).length ? D.badge({ label: `${ctx.propOf(r.s.id).length} suggested`, tone: "warning", size: "sm", icon: "bolt", title: "Trigger suggestions waiting for approval - open the row" }) : ""}</span>` },
      { key: "onoff", label: "On / Off", w: 96, sortVal: (r) => (r.s.paused ? 1 : 0),
        render: (r) => D.switch({ checked: !r.s.paused, disabled: !ctx.canAdmin, ariaLabel: `${r.s.name} on or off`, title: "Off stops sends and new enrollment", onchange: `event.stopPropagation();gmWfToggle(${q(r.s.id)})` }) },
      { key: "trigger", label: "Trigger", w: 230, sortable: false,
        render: (r) => (r.rules.length ? r.rules.map((x) => `<span class="ds-gm-trig"><span class="ds-truncate" title="${esc(x.name)}: ${ctx.ruleSummary(x.cond)}">${ctx.ruleSummary(x.cond)}</span>${matchBtn(x.id)}</span>`).join("") : muted("manual")) },
      { key: "steps", label: "Steps", w: 74, align: "end", sortVal: (r) => (r.s.steps || []).length, render: (r) => `<span class="ds-num">${esc(num((r.s.steps || []).length))}</span>` },
      { key: "days", label: "Days", w: 72, align: "end", sortVal: (r) => ctx.seqDays(r.s), render: (r) => `<span class="ds-num">${esc(num(ctx.seqDays(r.s)))}</span>` },
      { key: "goal", label: "Goal", w: 100, sortVal: (r) => String(r.s.goal || ""), render: (r) => D.tag({ label: r.s.goal || "", icon: "flag", tone: "neutral" }) },
      { key: "active", label: "Active", w: 80, align: "end", sortVal: (r) => r.s.active_threads || 0, render: (r) => `<b class="ds-num">${esc(num(r.s.active_threads || 0))}</b>` },
      { key: "enrolled", label: "Enrolled", w: 92, align: "end", sortVal: (r) => (r.st || {}).enrolled || 0, render: (r) => ((r.st || {}).enrolled == null ? D.dash() : `<span class="ds-num">${esc(num(r.st.enrolled))}</span>`) },
      { key: "enr7d", label: "Last 7 d", w: 84, align: "end", sortVal: (r) => (r.st || {}).enrolled_7d || 0, render: (r) => ((r.st || {}).enrolled_7d == null ? D.dash() : `<span class="ds-num">${esc(num(r.st.enrolled_7d))}</span>`) },
      { key: "goalmet", label: "Goal met", w: 92, align: "end", sortVal: (r) => (r.st || {}).goal_met || 0, render: (r) => ((r.st || {}).goal_met == null ? D.dash() : `<span class="ds-num">${esc(num(r.st.goal_met))}</span>`) },
      { key: "sent", label: "Sent", w: 72, align: "end", sortVal: (r) => (r.st || {}).sent || 0, render: (r) => ((r.st || {}).sent == null ? D.dash() : `<span class="ds-num">${esc(num(r.st.sent))}</span>`) },
      { key: "open", label: "Open %", w: 80, align: "end", sortVal: (r) => ((r.st || {}).sent ? (r.st.opened || 0) / r.st.sent : 0), render: (r) => `<span class="ds-num">${esc(pct((r.st || {}).opened, (r.st || {}).sent))}</span>` },
      { key: "reply", label: "Reply %", w: 80, align: "end", sortVal: (r) => ((r.st || {}).enrolled ? (r.st.replied || 0) / r.st.enrolled : 0), render: (r) => `<span class="ds-num">${esc(pct((r.st || {}).replied, (r.st || {}).enrolled))}</span>` },
      { key: "desc", label: "Description", w: 200, sortVal: (r) => r.s.description || "~", render: (r) => text(r.s.description) },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        menu: (r) => (ctx.canAdmin ? [
          { label: "Edit workflow", icon: "pencil-square", onclick: `gmSeqEdit(${q(r.s.id)})` },
          { label: "Clone", icon: "document-duplicate", onclick: `gmWfClone(${q(r.s.id)})` },
          { divider: true },
          { label: "Archive", icon: "archive-box", danger: true, onclick: `gmSeqDelete(${q(r.s.id)})` },
        ] : [{ label: "Open", icon: "eye", onclick: `gmSeqEdit(${q(r.s.id)})` }]) },
    ];
    const o = {
      id: "gm_wf", seedVersion: 1, ariaLabel: "Workflows", columns: cols, rows, rowKey: (r) => r.s.id,
      rowClass: (r) => (r.s.paused ? "ds-gm-off" : null),
      expandable: { render: (r) => ctx.expansion(r.s.id), open: (r) => ctx.isOpen(r.s.id) },
      onToggle: (key, open) => ctx.setOpen(key, open),
      onRowClick: (r) => W.gmSeqEdit(r.s.id),
      loading: !!ctx.loading && !rows.length,
      empty: ctx.q || (ctx.state && ctx.state !== "all")
        ? { title: "No workflows match", text: "Clear the search, or switch the view." }
        : { title: "No workflows yet", text: "A workflow is the sequence of emails a parcel gets until it clears.", action: ctx.canAdmin ? { label: "New workflow", icon: "plus", onclick: "gmSeqNew()" } : null },
      footer: { name: `<b>${esc(num(rows.length))} workflows</b>`, active: `<span class="ds-num">${esc(num(rows.reduce((a, r) => a + (r.s.active_threads || 0), 0)))}</span>` },
    };
    // The triggers that enroll parcels by themselves.
    const rules = ctx.rules || [];
    const rcols = [
      { key: "name", label: "Trigger", w: 220, pin: "start", locked: true, sortVal: (r) => (r.name || "").toLowerCase(), render: (r) => `<b class="ds-truncate" title="${esc(r.name || "")}">${esc(r.name || "")}</b>` },
      { key: "enabled", label: "On / Off", w: 96, sortVal: (r) => (r.enabled ? 0 : 1), render: (r) => D.switch({ checked: !!r.enabled, disabled: !ctx.canAdmin, ariaLabel: `${r.name} on or off`, onchange: `event.stopPropagation();gmRuleToggle(${q(r.id)},DS.switchOn(this))` }) },
      { key: "cond", label: "When", w: 320, sortable: false, render: (r) => `<span class="ds-truncate" title="${ctx.ruleSummary(r.cond)}">${ctx.ruleSummary(r.cond)}</span>` },
      { key: "seq", label: "Enrolls into", w: 200, sortVal: (r) => ctx.seqName(r.seq_id) || "~", render: (r) => text(ctx.seqName(r.seq_id) || "?") },
      { key: "matches", label: "Matching now", w: 120, sortVal: (r) => (((ctx.matches || {})[r.id] || {}).count || 0), render: (r) => matchBtn(r.id) || D.dash() },
      { key: "mode", label: "Mode", w: 110, sortVal: (r) => r.mode || "~", render: (r) => D.tag({ label: r.mode === "auto" ? "Automatic" : "Needs approval", tone: r.mode === "auto" ? "info" : "neutral" }) },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        menu: (r) => (ctx.canAdmin ? [{ label: "Edit trigger", icon: "pencil-square", onclick: `gmRuleEdit(${q(r.id)})` }, { divider: true }, { label: "Delete", icon: "trash", danger: true, onclick: `gmRuleDelete(${q(r.id)})` }] : []) },
    ];
    const rulesHead = `<div class="ds-gm-sec"><h2 class="ds-gm-h2">Auto-enroll triggers</h2>${ctx.canAdmin ? D.button({ label: "New trigger", icon: "plus", size: "sm", variant: "secondary", onclick: "gmRuleNew()" }) : ""}</div><div id="gmRulesTable"></div>`;
    paintTable(el, bar, o, rulesHead);
    D.tableRender(el.querySelector("#gmRulesTable"), { id: "gm_rules", ariaLabel: "Auto-enroll triggers", columns: rcols, rows: rules, rowKey: (r) => r.id,
      onRowClick: ctx.canAdmin ? (r) => W.gmRuleEdit(r.id) : null,
      empty: { title: "No triggers", text: "A trigger enrolls matching parcels into a workflow on its own." } });
  };

  // ---------------------------------------------------------------- readiness
  const READY = {
    blocked: (r) => !r.pname_id && !r.state, tagged: (r) => !!(r.app_tag || r.cu_tag),
    ready: (r) => !!r.pname_id && !r.state, enrolled: (r) => !!r.state, all: () => true,
  };
  G.readyFilter = (rows, mode) => (rows || []).filter(READY[mode] || READY.all);
  /** ctx: {mode, thumbs(r)->html} */
  G.readiness = (mount, all, ctx) => {
    const el = hostOf(mount); if (!el) return;
    all = all || [];
    const rows = G.readyFilter(all, ctx.mode);
    const n = (k) => all.filter(READY[k]).length;
    const bar = D.filterBar({
      views: [{ key: "blocked", label: "No ID", count: n("blocked") }, { key: "tagged", label: "Tagged", count: n("tagged") }, { key: "ready", label: "Ready", count: n("ready") },
        { key: "enrolled", label: "Enrolled", count: n("enrolled") }, { key: "all", label: "All", count: all.length }].map((v) => Object.assign(v, { active: ctx.mode === v.key })),
      onView: "gmReadyMode(KEY)",
      right: [`<span class="ds-muted ds-gm-hint">Name, ID and tag per parcel, before any email leaves</span>`],
    });
    const cols = [
      { key: "gwd", label: "Parcel", w: 190, pin: "start", locked: true, sortVal: (r) => r.gwd, render: (r) => parcel(r.gwd, r.source) },
      { key: "products", label: "Products", w: 132, sortable: false, render: (r) => (ctx.thumbs ? ctx.thumbs(r) : "") || D.dash() },
      { key: "name", label: "Name on parcel", w: 180, sortVal: (r) => r.pname || "~",
        render: (r) => (r.pname ? `<span class="ds-gm-name">${text(r.pname)}${r.pname_src === "default" ? `<span class="ds-muted" title="No board names this parcel - the name comes from the Settings default">default</span>` : ""}</span>` : D.dash()) },
      { key: "id", label: "ID number", w: 160, sortVal: (r) => (r.pname_id ? 0 : 1),
        render: (r) => (r.pname_id ? D.badge({ label: r.pname_id, tone: "success", icon: "identification", title: "The ID this parcel's emails will carry", cls: "ds-mono" })
          : D.attention({ kind: "missing_id", title: r.source === "purchases" ? "Add the ID on the customer's Customers page" : "Give this name an ID in Accounts & templates, or pick another name" })) },
      { key: "tag", label: "Auto-clear tag", w: 150, sortVal: (r) => (r.app_tag || r.cu_tag ? 0 : 1),
        render: (r) => (r.source === "purchases"
          ? D.checkbox({ checked: !!r.app_tag, label: r.app_tag ? "Tagged" : "Tag", title: "In-app tag: clear this parcel automatically", onchange: `gmReadyTag(${q(r.gwd)},this.checked)` })
          : (r.cu_tag ? D.badge({ label: String(r.cu_tag).slice(0, 14), tone: "success", icon: "check", size: "sm", title: "Tagged through the ClickUp AUTO CLEAR column" }) : `<span class="ds-muted" title="Tag it in ClickUp: the AUTO CLEAR column">${D.dash()}</span>`)) },
      { key: "state", label: "Conversation", w: 150, sortVal: (r) => r.state || "~", render: (r) => (r.state ? D.status.badge("gmThread", r.state) : muted("Not enrolled")) },
      { key: "status", label: "Board status", w: 200, sortVal: (r) => r.status || r.who || "~", render: (r) => text(r.status || r.who) },
      { key: "actions", label: "", w: 60, type: "actions", pin: "end", locked: true, sortable: false, render: (r) => `<span class="ds-actions">${r.state ? openConv(r.gwd) : enrollBtn(r.gwd)}</span>` },
    ];
    paintTable(el, bar, { id: "gm_ready", ariaLabel: "Clearance readiness", columns: cols, rows, rowKey: (r) => r.gwd,
      empty: { title: "Nothing here", text: ctx.mode === "blocked" ? "Every parcel has an ID." : "Switch the view to see other parcels." },
      footer: { gwd: `<b>${esc(num(rows.length))} parcels</b>` } });
  };

  // ---------------------------------------------------------------- docs
  const DOCS_MODE = {
    action: (r) => r.state === "action" || r.state === "stopped", unchecked: (r) => r.state === "unchecked" || r.state === "error",
    watching: (r) => r.state === "info" || r.state === "plain", noanswer: (r) => r.state === "noanswer", all: () => true,
  };
  G.docsFilter = (rows, mode) => (rows || []).filter(DOCS_MODE[mode] || DOCS_MODE.all);
  /** The documents state as one badge, from the shared status registry. */
  G.docsBadge = (r) => {
    let key = r.state, title = "";
    if (r.state === "action") title = "GAASH asks for documents" + ((r.codes || []).includes(818) ? " (customer ID)" : "");
    else if (r.state === "stopped") title = "Customs clearance stopped";
    else if (r.state === "noanswer") title = "GAASH has no record of this number - check the tracking number";
    else if (r.state === "error") { key = "unchecked"; title = "The last check failed - try again"; }
    else if (r.state === "unchecked") title = "Not checked yet - press Check";
    else if (!r.arrived) { key = "notarrived"; title = "Not in Israel yet - nothing is asked before arrival"; }
    else title = "No request, customs is processing";
    return D.status.badge("docs", key, { title });
  };
  const DOCS_RANK = { action: 0, stopped: 1, unchecked: 2, error: 2, noanswer: 3, info: 4, plain: 4 };
  /** ctx: {mode, view:"order"|"flat", busy, done, total, thumbs(r), boardStatus(r)->html, gaash(r)->html,
            orderKey(r), orderName(r), asked(r)->text, uploadLink(r)->url, errChip(r)->html, ago(iso)} */
  G.docs = (mount, all, ctx) => {
    const el = hostOf(mount); if (!el) return;
    all = all || [];
    let rows = G.docsFilter(all, ctx.mode);
    const n = (k) => all.filter(DOCS_MODE[k]).length;
    const byOrder = ctx.view !== "flat";
    const bar = D.filterBar({
      views: [{ key: "action", label: "Upload asked", count: n("action") }, { key: "unchecked", label: "Unchecked", count: n("unchecked") }, { key: "watching", label: "In customs", count: n("watching") },
        n("noanswer") ? { key: "noanswer", label: "No answer", count: n("noanswer") } : null, { key: "all", label: "All", count: all.length }].filter(Boolean).map((v) => Object.assign(v, { active: ctx.mode === v.key })),
      onView: "gmDocsMode(KEY)",
      chips: [{ label: "By order", icon: "rectangle-stack", active: byOrder, onclick: "gmDocsView('order')", title: "One order's parcels sit together" },
        { label: "Flat parcels", icon: "list-bullet", active: !byOrder, onclick: "gmDocsView('flat')", title: "One row per parcel, no grouping" }],
      right: ctx.busy
        ? [`<span class="ds-muted ds-gm-hint" aria-live="polite">Checking <b class="ds-num">${esc(ctx.done)}/${esc(ctx.total)}</b></span>`, D.button({ label: "Stop", icon: "x-mark", size: "sm", variant: "secondary", onclick: "gmDocsStop()" })]
        : (rows.length ? [D.button({ label: `Check all (${num(rows.length)})`, icon: "document-magnifying-glass", size: "sm", variant: "secondary", onclick: "gmDocsCheckAll()", title: "Check every row shown, one after another · each check takes 10-25 s" })] : []),
    });
    // By order: parcels sharing an order sit together, first-appearance order, whatever the sort.
    let runs = null;
    if (byOrder) {
      const t = D.tableGet("gm_docs"), st = t && t.state && t.state.sort;
      const col = st && [...docsCols(ctx, true)].find((c) => c.key === st.key);
      const groups = [], byKey = new Map();
      rows.forEach((r) => { const k = ctx.orderKey(r); let g = byKey.get(k); if (!g) { g = { key: k, rows: [] }; byKey.set(k, g); groups.push(g); } g.rows.push(r); });
      if (col && col.sortVal) {
        const dir = st.dir === "desc" ? -1 : 1;
        const val = (g) => g.rows.map(col.sortVal).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))[dir < 0 ? g.rows.length - 1 : 0];
        groups.sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
      }
      runs = new Map();
      rows = [];
      groups.forEach((g) => g.rows.forEach((r, i) => { runs.set(r.gwd, { n: g.rows.length, i, name: ctx.orderName(r), gwds: g.rows.map((x) => x.gwd), allThreads: g.rows.every((x) => x.thread_state) }); rows.push(r); }));
    }
    const cols = docsCols(ctx, byOrder, runs);
    paintTable(el, bar, {
      id: "gm_docs", seedVersion: 1, ariaLabel: "Customs documents", columns: cols, rows, rowKey: (r) => r.gwd,
      onSort: byOrder ? () => W.gmDocsDraw() : null,
      rowClass: (r) => { const run = runs && runs.get(r.gwd); return run && run.n > 1 ? (run.i ? "ds-gm-tied" : "ds-gm-tie-first") : null; },
      empty: { title: "Nothing here", text: ctx.mode === "action" ? "GAASH is not asking anyone for documents right now." : "Switch the view to see other parcels." },
      footer: { gwd: `<b>${esc(num(rows.length))} parcels</b>` },
    });
  };
  function docsCols(ctx, byOrder, runs) {
    const run = (r) => (runs && runs.get(r.gwd)) || null;
    return [
      { key: "gwd", label: "Parcel", w: 200, pin: "start", locked: true, sortVal: (r) => r.gwd, render: (r) => parcel(r.gwd, r.source) },
      byOrder ? { key: "order", label: "Order", w: 220, sortable: false,
        render: (r) => { const x = run(r); if (!x || x.n < 2) return text(ctx.orderName(r));
          return x.i ? `<span class="ds-gm-tie" title="Same order as the parcel above">${D.icon("arrow-uturn-left", { size: 12 })}<span>same order</span></span>`
            : `<span class="ds-gm-run">${text(x.name || "Order")}${D.tag({ label: `${x.n} parcels`, tone: "neutral", icon: "cube" })}${x.allThreads ? "" : D.button({ label: `Enroll all ${x.n}`, icon: "envelope", size: "sm", variant: "ghost", title: "One email for all these parcels - enroll them together", onclick: `event.stopPropagation();gmNewOpen(${JSON.stringify(x.gwds)},{group:true})` })}</span>`; } } : null,
      { key: "products", label: "Products", w: 132, sortable: false, render: (r) => (ctx.thumbs ? ctx.thumbs(r) : "") || D.dash() },
      { key: "name", label: "Name", w: 150, sortVal: (r) => r.pname || r.name || "~",
        render: (r) => { const nm = r.pname || r.name || ""; return nm ? `<span class="ds-truncate${/^faisal$/i.test(nm.trim()) ? " ds-muted" : ""}" dir="auto" title="${esc(r.name || nm)}">${esc(nm)}</span>` : D.dash(); } },
      { key: "docs", label: "Documents", w: 170, sortVal: (r) => (DOCS_RANK[r.state] == null ? 3 : DOCS_RANK[r.state]), render: (r) => G.docsBadge(r) + (ctx.errChip ? ctx.errChip(r) : "") },
      { key: "asked", label: "Asked for", w: 190, sortVal: (r) => (r.state === "action" ? ctx.asked(r) || "~" : "~"), render: (r) => (r.state === "action" ? text(ctx.asked(r)) : D.dash()) },
      { key: "status", label: "Board status", w: 150, sortable: false, render: (r) => (ctx.boardStatus ? ctx.boardStatus(r) : D.dash()) },
      { key: "gash", label: "GAASH status", w: 150, sortVal: (r) => r.label || r.bucket || "~", render: (r) => (ctx.gaash ? ctx.gaash(r) : "") || D.dash() },
      { key: "deadline", label: "Deadline", w: 120, sortVal: (r) => (r.gaash_deadline ? (r.days_left == null ? 9999 : r.days_left) : 99999),
        render: (r) => { if (!r.gaash_deadline) return D.dash(); const tip = "GAASH deadline " + r.gaash_deadline;
          if (r.days_left == null) return `<span class="ds-mono" title="${esc(tip)}">${esc(r.gaash_deadline)}</span>`;
          return r.days_left < 0 ? D.attention({ kind: "late", detail: `${-r.days_left} d`, title: tip }) : `<span class="ds-num" title="${esc(tip)}">${esc(r.days_left)} d left</span>`; } },
      { key: "checked", label: "Checked", w: 130, sortVal: (r) => r.docs_checked || "~",
        render: (r) => (r.docs_checked ? rel(r.docs_checked) : D.dash()) + (r.stale && r.state !== "unchecked" ? D.tag({ label: "stale", tone: "neutral", title: "The last check is old - re-check" }) : "") },
      { key: "actions", label: "", w: 170, type: "actions", pin: "end", locked: true, sortable: false,
        render: (r) => `<span class="ds-actions">${ctx.uploadLink(r) ? D.button({ label: "Upload", icon: "arrow-up-tray", size: "sm", variant: "secondary", title: "Pick the documents, review them, then upload to GAASH", onclick: `guOpen(${q(r.gwd)})` }) : ""}${iconBtn("document-magnifying-glass", "Check with GAASH now", `gmDocsCheck(${q(r.gwd)},this)`)}${r.thread_state ? openConv(r.gwd) : enrollBtn(r.gwd)}</span>` },
    ].filter(Boolean);
  }

  // ---------------------------------------------------------------- forecast (queue + cases)
  const FC_MODE = { all: () => true, overdue: (r) => !!r.overdue, unknown: (r) => !r.ok };
  G.fcFilter = (rows, mode) => (rows || []).filter(FC_MODE[mode] || FC_MODE.all);
  const viewChips = (view) => [{ label: "What happens next", icon: "presentation-chart-line", active: view !== "cases", onclick: "gmFcView('queue')" },
    { label: "How long GAASH took", icon: "clock", active: view === "cases", onclick: "gmFcView('cases')" }];
  const dayTxt = (v) => { const x = Number(v || 0); return (x < 10 ? x.toFixed(1) : Math.round(x)) + " d"; };
  /** ctx: {mode, view, thumbs(r), reasonPill(r)->html, nextPill(r,tip)->html} ; data = the /api/gaash/forecast payload */
  G.forecast = (mount, data, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const all = (data && data.rows) || [], m = (data && data.model) || {};
    const rows = G.fcFilter(all, ctx.mode);
    const n = (k) => all.filter(FC_MODE[k]).length;
    const bar = D.filterBar({
      views: [{ key: "all", label: "All", count: all.length }, n("overdue") ? { key: "overdue", label: "Overdue", count: n("overdue") } : null, n("unknown") ? { key: "unknown", label: "Unknown", count: n("unknown") } : null]
        .filter(Boolean).map((v) => Object.assign(v, { active: ctx.mode === v.key })),
      onView: "gmFcMode(KEY)",
      chips: viewChips(ctx.view),
      right: [iconBtn("arrow-path", "Recompute", "gmFcastRender()")],
    });
    const top = (m.next || [])[0];
    const basis = m.ready
      ? D.callout({ tone: "info", icon: "presentation-chart-line", html: `Learned from <b>${esc(num(m.n))}</b> transitions · <b>${esc(num(m.parcels))}</b> parcels · <b>${esc(num(m.sweeps))}</b> pickup runs · median <b>${esc((m.p50 || 0).toFixed(1))}</b> working days${top ? ` · <b>${esc(Math.round(100 * top.p))}%</b> ${esc(top.label)}` : ""} <span class="ds-muted">· Fri/Sat are the weekend, a pickup has never landed on one</span>` })
      : D.callout({ tone: "warning", icon: "exclamation-triangle", html: `Not enough history yet · <b>${esc(num(m.n || 0))}</b> transitions from <b>${esc(num(m.parcels || 0))}</b> parcels (we need ${esc((m.thresholds || {}).model_n || 12)}+)` });
    const cols = [
      { key: "gwd", label: "Parcel", w: 200, pin: "start", locked: true, sortVal: (r) => r.gwd, render: (r) => parcel(r.gwd, r.board) },
      { key: "products", label: "Products", w: 132, sortable: false, render: (r) => (ctx.thumbs ? ctx.thumbs(r) : "") || D.dash() },
      { key: "name", label: "Name", w: 150, sortVal: (r) => r.name || "~", render: (r) => text(r.name) },
      { key: "cleared", label: "Cleared", w: 140, sortVal: (r) => r.cleared_at || "~", render: (r) => (r.cleared_at ? `${day(r.cleared_at)} <span class="ds-muted">${esc(r.cleared_weekday || "")}</span>` : D.dash()) },
      { key: "waiting", label: "Waiting", w: 110, align: "end", sortVal: (r) => (r.dwell_work_days == null ? -1 : r.dwell_work_days),
        render: (r) => { if (r.dwell_work_days == null) return D.dash(); const tip = `Working days since clearance (Fri/Sat don't count) · calendar ${dayTxt(r.dwell_days)}`;
          return r.overdue ? D.attention({ kind: "late", label: dayTxt(r.dwell_work_days), title: tip + " · past the historical p90" }) : `<span class="ds-num" title="${esc(tip)}">${esc(dayTxt(r.dwell_work_days))}</span>`; } },
      { key: "next", label: "Predicted next", w: 210, sortVal: (r) => (r.ok ? -(r.next_p || 0) : 1),
        render: (r) => { if (!r.ok) return ctx.reasonPill(r);
          const alts = (r.alternatives || []).slice(0, 5).map((a) => `${Math.round(100 * a.p)}% ${a.label} (n=${a.n})`).join("\n");
          return `${ctx.nextPill(r, "Most likely\n" + alts)} <span class="ds-muted ds-num" title="Out of n=${esc(r.basis_n)} cleared parcels">${esc(Math.round(100 * (r.next_p || 0)))}%</span>`; } },
      { key: "eta", label: "Expected", w: 150, sortVal: (r) => (r.ok ? r.eta || "~" : "~"),
        render: (r) => { if (!r.ok) return D.dash(); const tip = `Range ${r.eta_early} to ${r.eta_late}\nMedian ${r.gap_work_days} working days\nBased on n=${r.basis_n} (${r.basis_sweeps} pickup runs)`;
          return `<b class="ds-mono" title="${esc(tip)}">${esc(r.eta)}</b> <span class="ds-muted">${esc(r.eta_weekday || "")}</span>`; } },
      { key: "stale", label: "Freshness", w: 100, sortVal: (r) => (r.stale ? 1 : 0), render: (r) => (r.stale ? D.tag({ label: "stale", tone: "neutral", title: "Tracking was not refreshed in a while - it may already have moved" }) : muted("fresh")) },
      { key: "actions", label: "", w: 80, type: "actions", pin: "end", locked: true, sortable: false, render: (r) => `<span class="ds-actions">${iconBtn("document-magnifying-glass", "Check with GAASH now", `gmDocsCheck(${q(r.gwd)},this)`)}${openConv(r.gwd)}</span>` },
    ];
    paintTable(el, bar + basis, { id: "gm_fc", ariaLabel: "What happens next", columns: cols, rows, rowKey: (r) => r.gwd,
      rowClass: (r) => (r.overdue ? "ds-gm-overdue" : null),
      empty: { title: "Nothing waiting after clearance", text: "Parcels appear here once GAASH clears them and until Gerizim picks them up." },
      footer: { gwd: `<b>${esc(num(rows.length))} parcels</b>` } });
  };
  /** data = the /api/gaash/case_report payload; ctx: {view} */
  G.cases = (mount, data, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const d = data || {}, o = d.overall || {};
    const bar = D.filterBar({ chips: viewChips(ctx.view),
      right: [D.button({ label: "AZ (2) columns", icon: "arrow-path", size: "sm", variant: "secondary", title: "Re-read the Gaash columns from ClickUp - press this after creating them", onclick: "gmCaseCols(this)" }), iconBtn("arrow-path", "Recompute", "gmCasesRender()")] });
    const SRC = { upload: ["Uploaded by the app", "cloud-arrow-up"], link: ["GAASH's page was opened and the files carried by hand", "link"], email: ["The clearance email", "envelope"] };
    const cards = (d.cases || []).length ? `<div class="ds-kpis">${(d.cases || []).map((c) => `<div class="ds-kpi">${D.badge({ label: c.case, hex: c.color })}${c.ready
      ? D.stat({ label: "median", value: `${c.p50} days`, hint: `of ${c.n} · slowest 10% ${c.p90} d · ${c.fastest} to ${c.slowest} d` })
      : D.stat({ label: "median", html: D.dash(), hint: `n=${c.n} · too few to compare · we need ${d.min_n}+` })}</div>`).join("")}</div>` : "";
    const summary = D.callout({ tone: "info", icon: "clock", html: `${o.n ? `Overall median <b>${esc(o.p50)}</b> days over <b>${esc(num(o.n))}</b> cleared parcels · ` : ""}<b>${esc(num((d.counts || {}).waiting || 0))}</b> still with GAASH${d.weird ? ` · <b>${esc(num(d.weird))}</b> released before we sent, not counted` : ""} <span class="ds-muted">· plain calendar days, holidays included</span>` });
    const cols = [
      { key: "gwd", label: "Parcel", w: 200, pin: "start", locked: true, sortVal: (r) => r.gwd, render: (r) => parcel(r.gwd, r.board) },
      { key: "name", label: "Name", w: 160, sortVal: (r) => r.name || "~", render: (r) => text(r.name) },
      { key: "case", label: "Case", w: 200, sortVal: (r) => r.case || "~", render: (r) => (r.case && r.case !== "(no case)" ? D.badge({ label: r.case, hex: (W.gmCaseColor && W.gmCaseColor(r.case)) || null }) : D.dash()) },
      { key: "sent", label: "Sent", w: 140, sortVal: (r) => r.sent_at || "~", render: (r) => { const s = SRC[r.sent_src] || ["", "document-text"]; return r.sent_at ? `<span title="${esc(s[0])}">${day(r.sent_at)} ${D.icon(s[1], { size: 13 })}</span>` : D.dash(); } },
      { key: "released", label: "Released", w: 120, sortVal: (r) => r.released_at || "~", render: (r) => day(r.released_at) },
      { key: "days", label: "Took", w: 100, align: "end", sortVal: (r) => (r.days == null ? -1 : r.days),
        render: (r) => (r.days == null ? `<span class="ds-muted" title="${esc(r.reason || "")}">${esc(r.reason || "")}</span>` : `<b class="ds-num" title="Sent ${esc(String(r.sent_at).slice(0, 10))}, released ${esc(String(r.released_at).slice(0, 10))}">${esc(r.days)}</b> <span class="ds-muted">d</span>`) },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false, render: (r) => `<span class="ds-actions">${openConv(r.gwd)}</span>` },
    ];
    paintTable(el, bar + summary + cards, { id: "gm_fc_cases", ariaLabel: "How long GAASH took", columns: cols, rows: d.rows || [], rowKey: (r) => r.gwd,
      empty: { title: "Nothing recorded as sent yet", text: "Cases appear once documents have been sent for a parcel." },
      footer: { gwd: `<b>${esc(num((d.rows || []).length))} parcels</b>` } });
  };

  // ---------------------------------------------------------------- templates
  /** ctx: {canAdmin, editor: {id,name,subject_tpl,body_tpl}|null, tokBar: html} */
  G.templates = (mount, rows, ctx) => {
    const el = hostOf(mount); if (!el) return;
    const bar = D.filterBar({ right: [`<span class="ds-muted ds-gm-hint">Click a variable in the editor to insert it</span>`].concat(ctx.canAdmin ? [D.button({ label: "New template", icon: "plus", size: "sm", variant: "secondary", onclick: "gmRenderTpl('new')" })] : []) });
    const cols = [
      { key: "name", label: "Template", w: 240, pin: "start", locked: true, sortVal: (t) => (t.name || "").toLowerCase(), render: (t) => `<b class="ds-truncate" title="${esc(t.name || "")}">${esc(t.name || "")}</b>` },
      { key: "subject", label: "Subject", w: 420, sortVal: (t) => t.subject_tpl || "~", render: (t) => text(t.subject_tpl) },
      { key: "used", label: "Used by", w: 110, align: "end", sortVal: (t) => t.used_by || 0, render: (t) => `<span class="ds-num" title="Workflow steps using this template">${esc(num(t.used_by || 0))}</span>` },
      { key: "actions", label: "", w: 52, type: "actions", pin: "end", locked: true, sortable: false,
        menu: (t) => (ctx.canAdmin ? [{ label: "Edit", icon: "pencil-square", onclick: `gmRenderTpl(${q(t.id)})` }, { divider: true }, { label: "Delete", icon: "trash", danger: true, onclick: `gmTplDelete(${q(t.id)})` }] : [{ label: "View", icon: "eye", onclick: `gmRenderTpl(${q(t.id)})` }]) },
    ];
    const ed = ctx.editor;
    const editor = ed ? `<section class="ds-gm-editor" aria-label="${ed.id ? "Edit template" : "New template"}">
      <h2 class="ds-gm-h2">${ed.id ? "Edit template" : "New template"}</h2>
      ${D.formRow([D.field({ id: "gmTplName", label: "Name", required: true, input: D.input({ id: "gmTplName", value: ed.name || "", placeholder: "Template name", disabled: !ctx.canAdmin }) }),
        D.field({ id: "gmTplSubj", label: "Subject", help: "e.g. Customs clearance · {gwd}", input: D.input({ id: "gmTplSubj", value: ed.subject_tpl || "", placeholder: "Customs clearance - {gwd}", disabled: !ctx.canAdmin, attrs: { onfocus: "gmTokCaret(this)", onblur: "gmTokCaret(this)" } }) })], { cols: 2 })}
      ${ctx.tokBar || ""}
      ${D.field({ id: "gmTplBody", label: "Body", input: D.textarea({ id: "gmTplBody", value: ed.body_tpl || "", rows: 18, disabled: !ctx.canAdmin, attrs: { onfocus: "gmTokCaret(this)", onblur: "gmTokCaret(this)" } }) })}
      <div class="ds-gm-editor-foot">${D.button({ label: "Cancel", variant: "ghost", onclick: "gmRenderTpl()" })}${ctx.canAdmin ? D.button({ label: "Save template", variant: "primary", icon: "check", onclick: `gmTplSave(${q(ed.id || "")},this)` }) : ""}</div>
    </section>` : "";
    paintTable(el, bar, { id: "gm_tpl", ariaLabel: "Template library", columns: cols, rows, rowKey: (t) => t.id,
      rowClass: (t) => (ed && ed.id === t.id ? "is-open" : null),
      onRowClick: (t) => W.gmRenderTpl(t.id),
      empty: { title: "No templates yet", text: "A template is the email a workflow step sends.", action: ctx.canAdmin ? { label: "New template", icon: "plus", onclick: "gmRenderTpl('new')" } : null },
      footer: { name: `<b>${esc(num(rows.length))} templates</b>` } }, editor);
    if (ed && !ed.id) { const i = el.querySelector("#gmTplName"); if (i) i.focus(); }
  };

  // ---------------------------------------------------------------- analyze
  /** data = the /api/gaash/stats payload */
  G.analyze = (mount, data) => {
    const el = hostOf(mount); if (!el) return;
    const o = (data && data.overall) || {};
    const kpis = D.kpis([
      { label: "Enrolled", value: num(o.enrolled) }, { label: "Emails sent", value: num(o.sent) },
      { label: "Open rate", value: pct(o.opened, o.sent), hint: "approximate", title: "Some mail apps auto-load images" },
      { label: "Click rate", value: pct(o.clicked, o.sent) }, { label: "Reply rate", value: pct(o.replied, o.enrolled) },
      { label: "Goal rate", value: pct(o.goal_met, o.enrolled) }, { label: "Bounces", value: num(o.bounces || 0), tone: o.bounces ? "danger" : null },
    ]);
    const rows = ((data && data.sequences) || []).filter((s) => s.enrolled || s.sent);
    const rate = (a, b) => `<span class="ds-num">${esc(pct(a, b))}</span>`;
    const cols = [
      { key: "name", label: "Workflow", w: 240, pin: "start", locked: true, sortVal: (s) => (s.name || "").toLowerCase(), render: (s) => `<b class="ds-truncate" title="${esc(s.name || "")}">${esc(s.name || "")}</b>` },
      { key: "enrolled", label: "Enrolled", w: 100, align: "end", sortVal: (s) => s.enrolled || 0, render: (s) => `<span class="ds-num">${esc(num(s.enrolled))}</span>` },
      { key: "sent", label: "Sent", w: 90, align: "end", sortVal: (s) => s.sent || 0, render: (s) => `<span class="ds-num">${esc(num(s.sent))}</span>` },
      { key: "open", label: "Open", w: 90, align: "end", sortVal: (s) => (s.sent ? (s.opened || 0) / s.sent : 0), render: (s) => rate(s.opened, s.sent) },
      { key: "click", label: "Click", w: 90, align: "end", sortVal: (s) => (s.sent ? (s.clicked || 0) / s.sent : 0), render: (s) => rate(s.clicked, s.sent) },
      { key: "reply", label: "Reply", w: 90, align: "end", sortVal: (s) => (s.enrolled ? (s.replied || 0) / s.enrolled : 0), render: (s) => rate(s.replied, s.enrolled) },
      { key: "goal", label: "Goal", w: 90, align: "end", sortVal: (s) => (s.enrolled ? (s.goal_met || 0) / s.enrolled : 0), render: (s) => rate(s.goal_met, s.enrolled) },
      { key: "bounce", label: "Bounce", w: 90, align: "end", sortVal: (s) => s.bounces || 0, render: (s) => `<span class="ds-num">${esc(num(s.bounces || 0))}</span>` },
      { key: "funnel", label: "Step funnel", w: 260, sortable: false,
        render: (s) => { const steps = s.steps || []; if (!steps.length) return D.dash(); const mx = Math.max(1, ...steps);
          return `<span class="ds-gm-fun" role="img" aria-label="${esc(steps.map((n, i) => `email ${i + 1}: ${n}`).join(", "))}">${steps.map((n, i) => `<span style="block-size:${Math.max(4, Math.round((n / mx) * 28))}px" title="Email #${i + 1}: ${esc(n)}"><i>${esc(n)}</i></span>`).join("")}</span>`; } },
    ];
    paintTable(el, kpis, { id: "gm_dash", ariaLabel: "Workflow analytics", columns: cols, rows, rowKey: (s) => s.seq_id,
      empty: { title: "No data yet", text: "Numbers appear once a workflow has sent its first email." },
      footer: { name: `<b>${esc(num(rows.length))} workflows</b>`, enrolled: `<span class="ds-num">${esc(num(o.enrolled || 0))}</span>`, sent: `<span class="ds-num">${esc(num(o.sent || 0))}</span>` } });
  };
})();
