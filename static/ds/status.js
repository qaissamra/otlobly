/* ============================================================================
   Otlobly design system — status registry (static/ds/status.js)
   The ONE place that maps every stored status / enum value of every entity to
   { label, tone, hex? }. UI code never hard-codes a status label or colour.
     tone ∈ neutral | info | success | warning | danger      (tokens.css semantic tones)
     hex   = the ClickUp colour for values mirrored from ClickUp — kept because the
             owner reads those boards by colour; DS.badge renders hex when given one.
   Owner decision 2026-09-06: stored ClickUp values and their spelling stay as they
   are ("recieved rd" is a real value). Only the *rendering* is centralised here.
   Sources: store.STATUSES (orders), purchases.ITEM_EXCEPTIONS, config.json ->
   leluxe.schema.statuses (packages, 35 live values + 3 client-only legacy ones),
   tracking.BUCKET_RANK, gerizim.STATUS_LABEL, tracking.docs_status, gaash_mail
   thread states, meta_leads, payments.kind, leluxe sync_state.
   ========================================================================== */
(function () {
  "use strict";
  const DS = (window.DS = window.DS || {});
  const T = (label, tone, hex) => (hex ? { label, tone, hex } : { label, tone });

  // Customer order — store.STATUSES (SCREAMING_SNAKE stored, sentence case shown)
  const order = {
    REQUESTED: T("Requested", "neutral"), QUOTED: T("Quoted", "info"), PAID: T("Paid", "info"),
    IN_CART: T("In cart", "warning"), ORDERED: T("Ordered", "info"), SHIPPED: T("Shipped", "info"),
    ARRIVED: T("Arrived", "warning"), DELIVERED: T("Delivered", "success"), COLLECTED: T("Collected", "success"),
    CANCELLED: T("Cancelled", "danger"),
  };
  // PO line exceptions — purchases.ITEM_EXCEPTIONS ("" = inherits the package)
  const poItem = {
    CANCELLED: T("Cancelled", "danger"), REFUNDED: T("Refunded", "danger"),
    OUT_OF_STOCK: T("Out of stock", "warning"), RETURNED: T("Returned", "neutral"),
  };
  // Package otlobly_status — the ClickUp list vocabulary, verbatim, colour = ClickUp's
  const pkg = {
    "order number": T("order number", "neutral", "#f5cf78"),
    "oredered": T("oredered", "info", "#9faec7"),
    "shipped": T("shipped", "info", "#4466ff"),
    "parcelto destination": T("parcelto destination", "info", "#0f9d9f"),
    "doc sent to gash": T("doc sent to gash", "info", "#ab4aba"),
    "in clearance": T("in clearance", "warning", "#0f9d9f"),
    "waiting verification": T("waiting verification", "warning", "#f76808"),
    "refund request": T("refund request", "warning", "#aa8d80"),
    "cancelled": T("cancelled", "danger", "#e5484d"),
    "not correct address": T("not correct address", "danger", "#e93d82"),
    "required customer id": T("required customer id", "warning", "#d33d44"),
    "rd request": T("rd request", "info", "#0091ff"),
    "mixed": T("mixed", "neutral", "#6647f0"),
    "delivered": T("delivered", "success", "#a18072"),
    "request cancel": T("request cancel", "warning", "#f76808"),
    "parcel check": T("parcel check", "warning", "#f76808"),
    "tracking api": T("tracking api", "neutral", "#6647f0"),
    "undeliverable": T("undeliverable", "danger", "#8d8d8d"),
    "picked up by ger": T("picked up by ger", "info", "#f76808"),
    "arrived at destination": T("arrived at destination", "info", "#e5484d"),
    "cleared customs": T("cleared customs", "success", "#f8ae00"),
    "az id": T("az id", "neutral", "#e93d82"),
    "az id sub": T("az id sub", "neutral", "#30a46c"),
    "package": T("package", "neutral", "#8d8d8d"),
    "documents sent": T("documents sent", "info", "#4466ff"),
    "rd": T("rd", "warning", "#008844"),
    "delievered rd": T("delievered rd", "success", "#008844"),
    "delievered no rd": T("delievered no rd", "success", "#d33d44"),
    "not recieved no rd": T("not recieved no rd", "warning", "#d33d44"),
    "not recieved rd": T("not recieved rd", "warning", "#b660e0"),
    "recieved rd": T("recieved rd", "success", "#0ff17e"),
    "recieved no rd": T("recieved no rd", "success", "#1090e0"),
    "sent no rd": T("sent no rd", "info", "#3e63dd"),
    "sent rd": T("sent rd", "info", "#3dcc4e"),
    "complete": T("complete", "success", "#008844"),
    // client-only legacy values still present in stored data (not in ClickUp)
    "cleared": T("cleared", "success", "#16a34a"),
    "id request": T("id request", "warning", "#d33d44"),
    "ariived at destnation": T("ariived at destnation", "info", "#e5484d"),
  };
  // The four disagreeing "parcel is done" sets (APP_AUDIT F-003) -> one, mirroring alerts.STOP_DEFAULT
  const pkgDone = new Set(["rd", "delivered", "delievered rd", "delievered no rd", "recieved rd", "recieved no rd", "sent rd", "sent no rd", "complete"]);
  const pkgReceived = new Set(["recieved rd", "recieved no rd"]);          // pkgprep.RECEIVED_STATUSES
  const pkgDispatched = new Set(["sent rd", "sent no rd", "complete"]);   // pkgprep.DISPATCHED_STATUSES

  // Carrier buckets — tracking.BUCKET_RANK (one palette instead of five)
  const bucket = {
    transit: T("In transit", "info"), customs: T("In customs", "warning"), cleared: T("Cleared customs", "info"),
    arrived: T("At destination", "success"), delivered: T("Delivered", "success"),
  };
  // Gerizim last mile — gerizim.STATUS_LABEL buckets
  const gerizim = {
    office: T("At Gerizim office", "neutral"), sms: T("SMS sent, awaiting pickup", "info"), pickup: T("Ready for pickup", "warning"),
    out: T("Out for delivery", "info"), delivered: T("Delivered by Gerizim", "success"), notfound: T("Not at Gerizim yet", "neutral"),
  };
  // Customs documents — tracking.docs_status states (+ the UI-only ones)
  const docs = {
    action: T("Upload asked", "warning"), info: T("In customs", "info"), plain: T("In customs", "info"), cleared: T("Cleared", "success"),
    stopped: T("Clearance stopped", "danger"), noanswer: T("No answer", "neutral"), unchecked: T("Unchecked", "neutral"),
    error: T("Check failed", "neutral"), notarrived: T("Not arrived", "neutral"),
  };
  // GAASH mail thread states — gaash_mail sequencer
  const gmThread = {
    active: T("Active", "info"), waiting_reply: T("Reply received", "info"), missing_docs: T("Missing documents", "danger"),
    paused: T("Paused", "neutral"), cleared: T("Cleared", "success"), goal_met: T("Goal met", "success"),
    waiting_task: T("Task waiting", "warning"), proposed: T("Proposed", "warning"), exhausted: T("No reply", "warning"), done: T("Done", "neutral"),
  };
  const lead = { new: T("New", "info"), contacted: T("Contacted", "warning"), converted: T("Converted", "success"), lost: T("Lost", "neutral") };
  const payment = { deposit: T("Deposit", "success"), collect: T("Collected", "success"), refund: T("Refund", "danger") };
  const sync = { synced: T("Synced", "success"), dirty: T("Unsaved", "warning"), pushing: T("Pushing", "warning"), error: T("Error", "danger"), conflict: T("Conflict", "danger") };
  const flag = { open: T("Open", "warning"), done: T("Done", "neutral") };
  const role = { admin: T("Admin", "info"), operator: T("Operator", "info"), sales: T("Sales", "neutral"), fulfillment: T("Fulfillment", "neutral") };
  const tier = { starter: T("Starter", "neutral"), growth: T("Growth", "info"), pro: T("Pro", "success") };
  // Attention states are SEPARATE from status (brief §9.4): a small fixed vocabulary
  const attention = {
    late: T("Late", "danger"), no_tracking: T("No tracking #", "warning"), missing_name: T("Missing name", "warning"),
    unpaid: T("Unpaid", "warning"), missing_docs: T("Documents missing", "danger"), no_reply: T("No reply", "warning"),
    stale: T("Stale", "neutral"), missing_id: T("No ID", "warning"), conflict: T("Conflict", "danger"),
    // Added in Phase 2 for the Needs attention queue (attention.py emits these three).
    action_email: T("Action required", "danger"), unpriced: T("No price", "warning"), over_quota: T("Over plan limit", "danger"),
    // The AZ Studio hand-off (attention.py, 2026-09-11): refused, flagged by the buyer, nobody took it.
    az_failed: T("AZ Studio refused it", "danger"), az_issue: T("Issue on AZ Studio", "danger"), az_waiting: T("Waiting for AZ Studio", "neutral"),
  };

  const maps = { order, poItem, pkg, bucket, gerizim, docs, gmThread, lead, payment, sync, flag, role, tier, attention };

  function get(entity, value) {
    const m = maps[entity]; if (!m || value == null) return null;
    const k = String(value);
    return m[k] || m[k.trim()] || m[k.trim().toLowerCase()] || null;
  }
  function label(entity, value) {
    const e = get(entity, value);
    if (e) return e.label;
    return value == null || value === "" ? "—" : String(value);
  }
  function tone(entity, value) { const e = get(entity, value); return e ? e.tone : "neutral"; }
  function hex(entity, value) { const e = get(entity, value); return e && e.hex ? e.hex : null; }
  /** Merge live values (e.g. ClickUp's schema [{status,color}]) into an entity map — unknown values get a neutral tone. */
  function extend(entity, entries) {
    const m = maps[entity] || (maps[entity] = {});
    (entries || []).forEach((e) => {
      const key = e.status != null ? String(e.status) : String(e.value || "");
      if (!key) return;
      const cur = m[key] || {};
      m[key] = { label: cur.label || key, tone: cur.tone || "neutral", hex: e.color || e.hex || cur.hex };
    });
    return m;
  }
  function values(entity) { return Object.keys(maps[entity] || {}); }
  const isPkgDone = (v) => pkgDone.has(String(v || "").trim().toLowerCase());
  /** Badge HTML for a stored value: DS.status.badge("order", o.status) — colour by hex when the value has one. */
  function badge(entity, value, opts) {
    const o = Object.assign({}, opts || {});
    const e = get(entity, value);
    const lab = o.label || (e ? e.label : label(entity, value));
    const b = { label: lab, tone: e ? e.tone : "neutral", title: o.title, size: o.size, dot: o.dot, icon: o.icon };
    if (e && e.hex && o.hex !== false) b.hex = e.hex;
    return DS.badge ? DS.badge(b) : lab;
  }
  DS.status = { maps, get, label, tone, hex, extend, values, badge, isPkgDone, pkgDone, pkgReceived, pkgDispatched };
})();
