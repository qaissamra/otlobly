#!/usr/bin/env node
// screenshots.mjs — capture every staff view of the running app with headless Chrome over CDP.
//
//   node docs/ux-restructure/tools/screenshots.mjs --base http://127.0.0.1:8796 --cookie "session=…" \
//        --out docs/ux-restructure/screens/before [--width 1440] [--height 1000] [--only purchases,gm-docs]
//
// Read-only: it logs in with the cookie you pass (get one with `curl -c jar -d 'username=…&password=…' /login`),
// runs the same setView()/tab calls a person would click, and saves one JPEG per screen. No product code is
// touched; the plan of screens lives in SCREENS below so the "after" set of every later phase is captured
// by the same list. Requires Node ≥ 22 (global WebSocket) and Google Chrome.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf("--" + k); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("base", "http://127.0.0.1:8796"), COOKIE = arg("cookie", ""), OUT = arg("out", "screens");
const W = +arg("width", 1440), H = +arg("height", 1000), ONLY = (arg("only", "") || "").split(",").filter(Boolean);
const CHROME = arg("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const PORT = +arg("port", 9222);

// name → JS to run in the page (after the app booted). `wait` = ms to let fetches settle.
const SCREENS = [
  ["brain", "setView('brain')", 2500],
  ["purchases-orders", "setView('purchases'); poSetView('orders')", 3500],
  ["purchases-packages", "poSetView('packages')", 2000],
  ["purchases-products", "poSetView('products')", 2000],
  ["purchases-customers", "poSetView('customers')", 2000],
  ["purchases-split", "poSetView('split')", 2000],
  ["purchases-new-order-modal", "poSetView('orders'); addPO()", 1500],
  ["purchases-fields-panel", "setView('purchases'); poSetView('orders'); const fb=document.querySelector('.fld-anchor button'); if(fb) fb.click()", 1500],
  ["to-order", "setView('needorder')", 3000],
  ["to-order-quote-tool", "const q=document.getElementById('quoteView'); if(q && getComputedStyle(q).display==='none' && typeof neQuoteToggle==='function') neQuoteToggle()", 1200],
  ["leluxe-orders", "setView('leluxe'); lxSetView('orders')", 4000],
  ["leluxe-packages", "lxSetView('packages')", 2000],
  ["leluxe-products", "lxSetView('products')", 2000],
  ["leluxe-dashboard", "lxSetView('dashboard')", 2000],
  ["leluxe-goal", "lxSetView('goal')", 2500],
  ["goals", "lxSetView('orders'); setView('goals')", 3000],
  ["deposits", "setView('deposits')", 2500],
  ["orders", "setView('orders')", 2500],
  ["orders-add-order-panel", "aoOpenForm&&aoOpenForm()", 1500],
  ["in-cart", "aoCloseForm&&aoCloseForm(); setView('incart')", 2500],
  ["leads", "setView('metaleads')", 2500],
  ["customers", "setView('customers')", 2500],
  ["package-prep", "setView('pkgprep')", 3000],
  ["bulk-search", "setView('bulksearch')", 1500],
  ["bulk-search-results", "(()=>{const t=document.getElementById('bsInput'); if(t){ t.value=(window.POS||[]).flatMap(p=>(p.packages||[]).map(k=>k.tracking_number)).filter(Boolean).slice(0,8).join('\\n')||'GWD000000000'; bsRun(); }})()", 3000],
  ["gaash-mail-conversations", "setView('gaashmail'); gmTab('conv')", 4000],
  ["gaash-mail-overview", "gmTab('ov')", 2500],
  ["gaash-mail-workflows", "gmTab('seq')", 2500],
  ["gaash-mail-templates", "gmTab('tpl')", 2500],
  ["gaash-mail-readiness", "gmTab('ready')", 2500],
  ["gaash-mail-docs", "gmTab('docs')", 2500],
  ["gaash-mail-forecast", "gmTab('fcast')", 2500],
  ["gaash-mail-analyze", "gmTab('dash')", 2500],
  ["flags", "setView('flags')", 2500],
  ["pnl", "setView('pnl')", 3000],
  ["activity", "setView('activity')", 2500],
  ["settings", "setView('settings')", 2500],
  ["team", "setView('team')", 2000],
  ["trash", "setView('trash')", 2000],
  ["catalog-hidden", "setView('catalog')", 2500],
  ["picking-hidden", "setView('picking')", 2500],
  ["tatabu-overview", "enterPlatform()", 3000],
  ["tatabu-brokers", "setView('brokers')", 2500],
  ["tatabu-plans", "setView('plans')", 2500],
  ["tatabu-usage", "setView('usage')", 2500],
  ["tatabu-activity", "setView('platactivity')", 2500],
  ["back-to-brain", "exitPlatform(); setView('brain')", 2000],
  ["language-arabic-purchases", "setView('purchases'); poSetView('orders'); if(LANG!=='ar') langToggle()", 3000],
  ["language-back-to-english", "if(LANG!=='en') langToggle()", 1000],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(OUT, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), "otl-shots-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, `--window-size=${W},${H}`,
  "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"], { stdio: "ignore" });
let wsUrl = "";
for (let i = 0; i < 50 && !wsUrl; i++) {
  try { wsUrl = (await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json()).webSocketDebuggerUrl; } catch { await sleep(200); }
}
if (!wsUrl) { chrome.kill(); throw new Error("Chrome did not expose CDP on port " + PORT); }
const ws = new WebSocket(wsUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let id = 0; const pending = new Map(); const events = [];
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d); };
const send = (method, params = {}, sessionId) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
const { result: { targetId } } = await send("Target.createTarget", { url: "about:blank" });
const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
const cdp = (m, p) => send(m, p, sessionId);
await cdp("Page.enable"); await cdp("Network.enable"); await cdp("Runtime.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
const u = new URL(BASE);
for (const c of COOKIE.split(";").map((s) => s.trim()).filter(Boolean)) {
  const [name, ...rest] = c.split("="); await cdp("Network.setCookie", { name, value: rest.join("="), domain: u.hostname, path: "/" });
}
await cdp("Page.navigate", { url: BASE + "/app" });
await sleep(4000);
const evalJs = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); const ex = r.result?.exceptionDetails; if (ex) return "ERR " + (ex.exception?.description || ex.text || "").split("\n")[0]; const v = r.result?.result?.value; return v == null ? "ok" : String(v); };
// Every screen starts from a clean slate: close every modal, popover and open menu the previous screen left behind.
const RESET = "document.querySelectorAll('.az-modal').forEach(m=>m.classList.add('hidden')); document.querySelectorAll('.pop-menu.open,.lxt-ctx').forEach(m=>m.classList.remove('open')); document.body.classList.remove('nav-open'); if(typeof popClose==='function'){ try{ popClose(); }catch(e){} }";
const title = await evalJs("document.title + ' | ' + (document.getElementById('pageTitle')||{}).textContent");
console.log("booted:", title);
const results = [];
for (const [name, js, wait] of SCREENS) {
  if (ONLY.length && !ONLY.includes(name)) continue;
  await evalJs(`(()=>{ try { ${RESET} } catch(e) {} })()`);
  const st = await evalJs(`(()=>{ try { ${js}; return 'ok'; } catch(e) { return 'ERR '+e.message; } })()`);
  await sleep(wait);
  const shot = await cdp("Page.captureScreenshot", { format: "jpeg", quality: 72 });
  const file = join(OUT, name + ".jpg");
  writeFileSync(file, Buffer.from(shot.result.data, "base64"));
  const meta = await evalJs("JSON.stringify({title:(document.getElementById('pageTitle')||{}).textContent, sub:(document.getElementById('sub')||{}).textContent, errors:(window.__consoleErrors||[]).length})");
  results.push({ name, status: st, meta });
  console.log(name.padEnd(30), st.padEnd(8), meta);
}
writeFileSync(join(OUT, "_index.json"), JSON.stringify(results, null, 1));
await send("Target.closeTarget", { targetId });
ws.close(); chrome.kill();
console.log("done:", results.length, "screens →", OUT);
