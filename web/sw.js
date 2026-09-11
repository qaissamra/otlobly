/* Otlobly staff offline shell.
   Network-first everywhere — the cache is a fallback ONLY when the network itself
   fails (never on a 4xx/5xx), so deploys still show up immediately and a 401 can
   never be masked or stored. Cache-served responses carry X-Otl-Cache: 1 so the
   page can show its offline strip. Writes (non-GET) are never intercepted. */
const CACHE = "otl-off-v27";         // v27: Send to AZ Studio + the hand-off chips (step 2, depth 2)
                                     // v26: the AZ Studio roster picker + recommendation (step 2, depth 1)
                                     // v25: the operator role (Team roles, CAN_OPS gates)
                                     // v24: the 2026-09-10 UI sweep (Customers one-line cell +
                                     // profile drawer, LXT pinned column back, breadcrumb-only
                                     // headers, check cells, Leluxe full names)
                                     // v23: the DataTable keeps blanks LAST in both sort
                                     //      directions (static/ds/table.js changed)
                                     // v22: Batch D - Tracking (Bulk search) on the design system
                                     //      (static/ds/tracking.js is new; gaash.js/ds.css/shell.js changed)
                                     // v21: Batch C - GAASH mail on the design system
                                     //      (static/ds/gaash.js is new; ds.js/ds.css/shell.js changed)
                                     // v20: the Columns panel could open off the
                                     //      bottom of the window with "Reset layout"
                                     //      unreachable; strip columns sized for 32px.
                                     // v19: one thumbnail everywhere, product photos
                                     //      on seven more lists, Est. cost folded.
                                     // v18: a package's products render under that
                                     //      package, not after the whole table.
                                     // v17: the Purchases filter builder is design
                                     //      -system controls (ds.js, ds.css, index).
                                     // v16: Purchases QA - caret kept in the search box,
                                     //      expand/collapse reaches every board, blanks
                                     //      sort last, identity columns locked, .az-modal
                                     //      panels take Escape and focus.
                                     // v15: a sub-table's flexible column could resolve to
                                     // 0 and vanish (ds.js, purchases.js, leluxe.js).
                                     // v14: every tab and view pill was dead — DS.tabs
                                     // double-escaped its own onclick (ds.js).
                                     // v13: Batch H — the Leluxe workspace (shell.js) + the four
                                     // dead controls (ds.js, ds.css, table.js, index.html).
                                     // v12: Batch G — QA fixes (table.js Reset layout, leluxe.js
                                     // widths, ds.css, shell.js, sales.js, index.html).
                                     // v11: Batch F3 — the Leluxe packages board. v10: Batch F2 —
                                     // the Leluxe products board (leluxe.js, ds.css,
                                     // index.html). v9: Batch F1 — the Leluxe orders board (+ leluxe.js, ds.css,
                                     // index.html). v8: the clearance record — index.html grew the
                                     // Gaash Case picker and the 📊 report, so the old shell must not linger.
                                     // v7: Batch B5 rewrote the table's sticky layer (ds.css,
                                     // table.js). v6: Batch B3 changed ds.js/table.js/sales.js — bumping
                                     // drops the old cache on activate, so a plain refresh is
                                     // enough. Batch B and B2 did NOT bump, and the stale copy
                                     // cost an afternoon of "the fix did not land".
                                     // v5: + fulfillment.js (Phase 4); v4 added purchases.js (Phase 3)
/* Static design-system assets: same network-first rule as the shell, but cached on install too so the
   offline copy of /app is never served without its stylesheet. */
const DS_ASSETS = ["/static/ds/tokens.css","/static/ds/ds.css","/static/ds/status.js",
  "/static/ds/format.js","/static/ds/ds.js","/static/ds/table.js","/static/ds/shell.js","/static/ds/purchases.js","/static/ds/fulfillment.js","/static/ds/sales.js","/static/ds/leluxe.js","/static/ds/tracking.js","/static/ds/gaash.js","/static/ds/icons.svg"];
const NO_STORE = ["/api/backup","/api/po_image","/api/customer_image","/api/leluxe/image",
  "/api/gaash/idfile","/api/gaash/attachment","/api/gaash/px/","/api/gaash/r/",
  "/api/product_image","/api/az/"];   // PII scans, multi-MB zip, SerpAPI-credit scrape, pixels, job pollers

self.addEventListener("install", e => e.waitUntil((async () => {
  try { (await caches.open(CACHE)).addAll(DS_ASSETS); } catch (err) { /* offline install: the fetch handler fills it later */ }
  await self.skipWaiting();
})()));
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));

async function shell(req){                        // navigation to /app
  try{
    const resp = await fetch(req);                // logged-out → opaqueredirect (ok=false) → never cached
    if (resp.ok && !resp.redirected && resp.type === "basic")
      (await caches.open(CACHE)).put("/app", resp.clone()).catch(()=>{});
    return resp;
  }catch(err){
    const hit = await caches.match("/app");
    if (hit) return hit;
    throw err;
  }
}

async function ds(req){                           // /static/ds/* — network-first, cached fallback
  try{
    const resp = await fetch(req);
    if (resp.ok) (await caches.open(CACHE)).put(req, resp.clone()).catch(()=>{});
    return resp;
  }catch(err){
    const hit = await caches.match(req);
    if (hit) return hit;
    throw err;
  }
}

async function api(req){                          // same-origin GET /api/*
  try{
    const resp = await fetch(req);
    if (resp.ok && (resp.headers.get("Content-Type")||"").includes("application/json"))
      (await caches.open(CACHE)).put(req, resp.clone()).catch(()=>{});
    return resp;                                  // 401/403/5xx pass through live, never cached
  }catch(err){
    const hit = await caches.match(req);
    if (hit){
      const h = new Headers(hit.headers); h.set("X-Otl-Cache","1");
      return new Response(hit.body, {status: hit.status, statusText: hit.statusText, headers: h});
    }
    throw err;                                    // page sees a normal network error, like today
  }
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;               // writes are NEVER intercepted
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (req.mode === "navigate"){ if (url.pathname === "/app") e.respondWith(shell(req)); return; }
  if (url.pathname.startsWith("/static/ds/")){ e.respondWith(ds(req)); return; }
  if (url.pathname.startsWith("/api/") && !NO_STORE.some(p => url.pathname.startsWith(p)))
    e.respondWith(api(req));
});
