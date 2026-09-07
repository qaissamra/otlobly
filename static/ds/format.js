/* ============================================================================
   Otlobly design system — formatters (static/ds/format.js)
   One formatMoney / formatNumber / formatDate / formatRelative for the whole app,
   all through Intl pinned to en-US (owner decision D9: Western digits, no seconds).
   Replaces the three `money()`, `money0`, `fmt`, `cfNum`, `lxGm`, `relTime`,
   `agoTxt`, `gmAgo`, `fmtDue`, `lxDate`, `cfFmtDate`, `gmChgLabel/Time` in
   web/index.html as pages migrate (docs/ux-restructure/AUDIT.md §5.9).
   ========================================================================== */
(function () {
  "use strict";
  const DS = (window.DS = window.DS || {});
  const LOCALE = "en-US";
  const _nf = {};
  const nf = (min, max) => (_nf[min + ":" + max] ||= new Intl.NumberFormat(LOCALE, { minimumFractionDigits: min, maximumFractionDigits: max }));
  const MON = new Intl.DateTimeFormat(LOCALE, { month: "short" });
  const rtf = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });

  // Base currency USD shows its symbol; any other currency carries its CODE (never a second "$").
  const CUR = { USD: { sym: "$", dec: 2 }, ILS: { code: "ILS", dec: 0 }, AED: { code: "AED", dec: 2 }, EUR: { code: "EUR", dec: 2 },
                JOD: { code: "JOD", dec: 2 }, GBP: { code: "GBP", dec: 2 }, SAR: { code: "SAR", dec: 2 }, EGP: { code: "EGP", dec: 2 }, TRY: { code: "TRY", dec: 2 } };

  /** DS.fmt.money(827.55) -> "$827.55" · money(3050,"ILS") -> "3,050 ILS" · money(3050,"ILS",{approx:true}) -> "≈ 3,050 ILS" */
  function money(n, currency, opts) {
    const o = opts || {};
    if (n == null || n === "" || isNaN(Number(n))) return o.empty != null ? o.empty : "—";
    const c = CUR[(currency || "USD").toUpperCase()] || { code: String(currency).toUpperCase(), dec: 2 };
    const dec = o.decimals != null ? o.decimals : c.dec;
    const num = nf(dec, dec).format(Math.abs(Number(n)));
    const sign = Number(n) < 0 ? "−" : "";
    const body = c.sym ? `${sign}${c.sym}${num}` : `${sign}${num} ${c.code}`;
    return (o.approx ? "≈ " : "") + body;
  }
  /** A converted secondary amount, muted, with its own code: DS.fmt.secondary(3050,"ILS") -> "≈ 3,050 ILS" */
  const secondary = (n, currency, opts) => money(n, currency, Object.assign({ approx: true }, opts || {}));

  function number(n, opts) {
    const o = opts || {};
    if (n == null || n === "" || isNaN(Number(n))) return o.empty != null ? o.empty : "—";
    const dec = o.decimals != null ? o.decimals : (Number.isInteger(Number(n)) ? 0 : 2);
    return nf(o.minDecimals != null ? o.minDecimals : dec, dec).format(Number(n));
  }
  function percent(n, opts) { return n == null || isNaN(Number(n)) ? "—" : number(Number(n) * (opts && opts.ratio ? 100 : 1), { decimals: (opts && opts.decimals) || 0 }) + "%"; }

  /** Accepts ISO, "YYYY-MM-DD", "D/M/YYYY", a Date, a ms epoch (number or numeric string). Returns a Date or null. */
  function parse(v) {
    if (v == null || v === "") return null;
    if (v instanceof Date) return isNaN(v) ? null : v;
    if (typeof v === "number") return new Date(v);
    const s = String(v).trim();
    let m;
    if ((m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s))) return new Date(+m[1], +m[2] - 1, +m[3]);
    if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s))) return new Date(+m[3], +m[2] - 1, +m[1]);
    if (/^\d{10,13}$/.test(s)) return new Date(s.length === 10 ? +s * 1000 : +s);
    const d = new Date(s);
    return isNaN(d) ? null : d;
  }
  const pad = (n) => String(n).padStart(2, "0");
  /** "5 Sep 2026" · {time:true} -> "5 Sep 2026, 14:30" · {year:false} -> "5 Sep" */
  function date(v, opts) {
    const o = opts || {}; const d = parse(v);
    if (!d) return o.empty != null ? o.empty : "—";
    let s = `${d.getDate()} ${MON.format(d)}` + (o.year === false ? "" : ` ${d.getFullYear()}`);
    if (o.time) s += `, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return s;
  }
  const datetime = (v, opts) => date(v, Object.assign({ time: true }, opts || {}));
  const time = (v) => { const d = parse(v); return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : "—"; };
  /** "just now" · "5 min ago" · "3 h ago" · "yesterday" · "2 d ago" · "in 3 d" · beyond 30 d -> the absolute date */
  function relative(v, opts) {
    const o = opts || {}; const d = parse(v);
    if (!d) return o.empty != null ? o.empty : "—";
    const now = o.now ? parse(o.now) : new Date();
    const diff = (d - now) / 1000; const abs = Math.abs(diff); const sign = diff < 0 ? -1 : 1;
    if (abs < 45) return "just now";
    if (abs < 3600) return rtf.format(sign * Math.round(abs / 60), "minute").replace(" minutes", " min").replace(" minute", " min");
    if (abs < 86400) return rtf.format(sign * Math.round(abs / 3600), "hour").replace(" hours", " h").replace(" hour", " h");
    const days = Math.round(abs / 86400);
    if (days <= 30) return rtf.format(sign * days, "day").replace(" days", " d").replace(" day", " d");
    return date(d, { year: d.getFullYear() !== now.getFullYear() });
  }
  /** Whole days from today to v (negative = past). Uses local midnight boundaries like the old dueDays(). */
  function daysUntil(v, now) {
    const d = parse(v); if (!d) return null;
    const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const n = now ? parse(now) : new Date(); const b = new Date(n.getFullYear(), n.getMonth(), n.getDate());
    return Math.round((a - b) / 86400000);
  }
  /** Tooltip text: the absolute date-time behind a relative label. */
  const title = (v) => datetime(v);
  const iso = (v) => { const d = parse(v); return d ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : ""; };

  DS.fmt = { money, secondary, number, percent, parse, date, datetime, time, relative, daysUntil, title, iso, LOCALE };
})();
