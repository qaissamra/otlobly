/* ── Config: the business WhatsApp number is injected by the Flask template
      (window.OTL_WA_NUMBER). Empty (e.g. local dev) → WA buttons hide. ── */
const WA_NUMBER = window.OTL_WA_NUMBER || "";

/* ── i18n dictionary ── */
const I18N = {
  ar: {
    "hero.eyebrow":   "أمازون ← فلسطين",
    "hero.title1":    "اطلب من أمازون،",
    "hero.title2":    "ونوصلها إلى فلسطين",
    "hero.sub":       "اطلبلي تشتري لك أي منتج من أمازون وتشحنه حتى باب بيتك — بدون بطاقة ائتمان: عربون بسيط يؤكّد طلبك، وتدفع الباقي بالدولار كاش عند الاستلام.",
    "sheet.title":    "استلم العالم على باب بيتك",
    "sheet.sub":      "من الطلب حتى التسليم — نحن نتكفّل بكل شيء.",
    "cta.order":      "اطلب الآن",
    "cta.track":      "تتبّع طلبك",
    "trust.cod":      "الدفع عند الاستلام",
    "trust.door":     "حتى باب البيت",
    "trust.nocard":   "بدون بطاقة ائتمان",
    "track.head":     "شحنتك الحالية",
    "track.seeall":   "عرض الكل",
    "track.prod":     "سماعة Sony XM5",
    "track.s1":       "تم الطلب",
    "track.s2":       "تم الشحن",
    "track.s3":       "وصلت فلسطين",
    "track.s4":       "قيد التوصيل",
    "track.loc":      "الموقع",
    "track.locv":     "رام الله — المستودع",
    "track.eta":      "الوصول المتوقّع",
    "track.etav":     "خلال ٢٤ ساعة",
    "track.cta":      "تتبّع هذه الشحنة",
    "track.inputph":  "أدخل رقم تتبّع OTL أو جوالك…",
    "track.submit":   "تتبّع",
    "track.searching":"…",
    "track.notfound": "لم نجد شحنة بهذا الرقم. تأكد من رقم OTL أو أدخل رقم جوالك كاملاً.",
    "track.status":   "الحالة الحالية",
    "track.yourship": "شحنتك",
    "track.newsearch":"↺ بحث جديد",
    "track.showtl":   "عرض التتبّع",
    "track.hidetl":   "إخفاء التتبّع",
    "track.asof":     "🕓 آخر تحديث",
    "track.count":    "وجدنا {n} شحنات",
    "how.head":       "كيف تعمل الخدمة؟",
    "how.s1t":        "أرسل الرابط",
    "how.s1p":        "ابعتلنا رابط المنتج من أمازون على واتساب.",
    "how.s2t":        "أكّد بعربون",
    "how.s2p":        "ادفع عربون بسيط لتأكيد الطلب والبدء بالشراء.",
    "how.s3t":        "نشتري ونشحن",
    "how.s3p":        "نطلب المنتج ونشحنه مع رقم تتبّع OTL خاص فيك.",
    "how.s4t":        "استلم وادفع",
    "how.s4p":        "استلم على باب البيت وادفع الباقي بالدولار كاش.",
    "why.head":       "ليش اطلبلي؟",
    "why.1t":         "تتبّع مباشر OTL",
    "why.1p":         "رقم تتبّع خاص لكل شحنة، وتحديثات لحظية من الطلب حتى بابك.",
    "why.2t":         "بدون بطاقة ائتمان",
    "why.2p":         "عربون بسيط فقط، والباقي بالدولار كاش عند الاستلام.",
    "why.3t":         "حساب شخصي",
    "why.3p":         "كل طلباتك ومدفوعاتك في مكان واحد.",
    "why.3l":         "افتح حسابك ←",
    "foot.tag":       "اطلبلي — نشتري لك من أمازون ونوصلها إلى فلسطين.",
    "foot.staff":     "دخول الموظفين",
    "nav.order":      "اطلب الآن",
    "nav.track":      "تتبّع",
    "nav.how":        "كيف تعمل",
    "nav.plans":      "الباقات",
    "nav.account":    "حسابي · تسجيل الدخول",
    "nav.wa":         "واتساب",
    "nav.wa.cta":     "واتساب",
    "ph.truck":       "شاحنة توصيل ثلاثية الأبعاد",
    "wa.msg":         "مرحباً اطلبلي 👋 بدي أطلب منتج من أمازون. الرابط:"
  },
  en: {
    "hero.eyebrow":   "Amazon → Palestine",
    "hero.title1":    "Shop Amazon,",
    "hero.title2":    "delivered to Palestine",
    "hero.sub":       "Otlobly buys any Amazon product and ships it to your door — no credit card: a small deposit confirms your order, and you pay the rest in USD cash on delivery.",
    "sheet.title":    "Receive the world at your doorstep",
    "sheet.sub":      "From order to delivery — we handle everything.",
    "cta.order":      "Order now",
    "cta.track":      "Track your order",
    "trust.cod":      "Cash on delivery",
    "trust.door":     "To your door",
    "trust.nocard":   "No credit card",
    "track.head":     "Current shipment",
    "track.seeall":   "See all",
    "track.prod":     "Sony XM5 Headphones",
    "track.s1":       "Ordered",
    "track.s2":       "Shipped",
    "track.s3":       "Arrived in Palestine",
    "track.s4":       "Out for delivery",
    "track.loc":      "Location",
    "track.locv":     "Ramallah — Hub",
    "track.eta":      "Est. arrival",
    "track.etav":     "Within 24 hours",
    "track.cta":      "Track this shipment",
    "track.inputph":  "Enter your OTL number or mobile…",
    "track.submit":   "Track",
    "track.searching":"…",
    "track.notfound": "No shipment found for that. Check the OTL number, or enter your full mobile number.",
    "track.status":   "Current status",
    "track.yourship": "Your shipment",
    "track.newsearch":"↺ New search",
    "track.showtl":   "Show tracking",
    "track.hidetl":   "Hide tracking",
    "track.asof":     "🕓 last updated",
    "track.count":    "Found {n} shipments",
    "how.head":       "How it works",
    "how.s1t":        "Send the link",
    "how.s1p":        "Send us the Amazon product link on WhatsApp.",
    "how.s2t":        "Confirm with a deposit",
    "how.s2p":        "Pay a small deposit to confirm and we start buying.",
    "how.s3t":        "We order & ship",
    "how.s3p":        "We order it and ship with your own OTL tracking number.",
    "how.s4t":        "Receive & pay",
    "how.s4p":        "Receive at your door and pay the rest in USD cash.",
    "why.head":       "Why Otlobly?",
    "why.1t":         "Live OTL tracking",
    "why.1p":         "A tracking number for every shipment, with live updates from order to door.",
    "why.2t":         "No card needed",
    "why.2p":         "Just a small deposit; pay the rest in USD cash on delivery.",
    "why.3t":         "Personal account",
    "why.3p":         "All your orders and payments in one place.",
    "why.3l":         "Open your account →",
    "foot.tag":       "Otlobly — we buy from Amazon and deliver it to Palestine.",
    "foot.staff":     "Staff login",
    "nav.order":      "Order now",
    "nav.track":      "Track",
    "nav.how":        "How it works",
    "nav.plans":      "Pricing",
    "nav.account":    "My account · Sign in",
    "nav.wa":         "WhatsApp",
    "nav.wa.cta":     "WhatsApp",
    "ph.truck":       "3D delivery truck",
    "wa.msg":         "Hi Otlobly 👋 I'd like to order a product from Amazon. Link:"
  }
};

let currentLang = "ar";

function setLang(lang) {
  currentLang = lang;
  var dict = I18N[lang];
  document.documentElement.lang = lang;
  document.documentElement.dir = (lang === "ar") ? "rtl" : "ltr";

  /* text content nodes */
  document.querySelectorAll("[data-i18n]").forEach(function(el) {
    var k = el.getAttribute("data-i18n");
    if (dict[k] != null) el.textContent = dict[k];
  });

  /* input placeholders */
  document.querySelectorAll("[data-i18n-ph]").forEach(function(el) {
    var k = el.getAttribute("data-i18n-ph");
    if (dict[k] != null) el.placeholder = dict[k];
  });

  /* lang toggle active state — the shared navbar (_nav.html) owns the toggle now,
     so these may be absent; guard against null. */
  var _la = document.getElementById("lang-ar"); if (_la) _la.classList.toggle("on", lang === "ar");
  var _le = document.getElementById("lang-en"); if (_le) _le.classList.toggle("on", lang === "en");

  buildWa();
}

function buildWa() {
  var msg = I18N[currentLang]["wa.msg"];
  var url = "https://wa.me/" + WA_NUMBER + "?text=" + encodeURIComponent(msg);
  ["wa-mob"].forEach(function(id) {
    var el = document.getElementById(id);
    if (!el) return;
    if (WA_NUMBER) { el.href = url; el.style.display = ""; }
    else { el.style.display = "none"; }   /* no number configured → hide */
  });
}

/* ── Hamburger menu toggle ── */
function toggleNav() {
  var nav = document.getElementById("sitenav");
  if (nav) nav.classList.toggle("open");
}

/* Close mobile nav when clicking outside */
document.addEventListener("click", function(e) {
  var nav = document.getElementById("sitenav");
  if (nav && nav.classList.contains("open") && !nav.contains(e.target)) {
    nav.classList.remove("open");
  }
});

/* ── Smooth scroll helper ── */
function smoothTo(id, e) {
  var el = document.getElementById(id);
  if (el) {
    if (e) e.preventDefault();
    var navH = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--nav-h")) || 60;
    var top = el.getBoundingClientRect().top + window.scrollY - navH - 12;
    window.scrollTo({ top: top, behavior: "smooth" });
  }
}

/* ── Track section submit ── */
function trackSubmit() {
  var input = document.getElementById("track-input");
  var bar = document.querySelector(".track-search");
  var val = input.value.trim();
  if (!val) {
    bar.classList.remove("err");
    void bar.offsetWidth;
    bar.classList.add("err");
    bar.addEventListener("animationend", function() { bar.classList.remove("err"); }, { once: true });
    input.focus();
    return;
  }
  doTrackLookup(val);
}

/* ── Inline live tracking: query /api/track and render the real result card(s)
      right inside the #track section — no navigation. The endpoint itself
      branches on OTL-number vs full-mobile, so we just pass the raw query.
      "عرض الكل" (.see) still routes to the full-history /track page. ── */
function trEsc(s){ return (s||"").replace(/[&<>"]/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]; }); }
function tL(k){ var d=I18N[currentLang]||I18N.en; return (d[k]!=null)?d[k]:(I18N.en[k]||k); }
function trFmt(d){ if(!d) return ""; var x=new Date(d); if(isNaN(x)) return trEsc(d);
  return x.toLocaleDateString(currentLang==="ar"?"ar":"en-GB",{day:"2-digit",month:"short",year:"numeric"}); }
function trFmtRange(a,b){ var loc=currentLang==="ar"?"ar":"en-GB";
  var f=function(d){ return new Date(d).toLocaleDateString(loc,{day:"2-digit",month:"short"}); };
  return f(a)+" – "+f(b)+" "+new Date(b).getFullYear(); }
/* bucket → progress step (1..4); 5 = fully delivered. Same mapping as the account portal. */
function trStep(s){
  var b=(s.current&&s.current.bucket)||"";
  var hasTrack=!!(s.tracking&&(s.events||[]).length);
  if(b==="delivered") return 5;
  if(b==="arrived") return 4;
  if(b==="customs"||b==="cleared") return 3;
  if(b==="transit") return (hasTrack||s.est_delivery)?2:1;
  return 1;
}
var TR_PIN='<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 6-9 12-9 12s-9-6-9-12a9 9 0 0 1 18 0Z"></path><circle cx="12" cy="10" r="3"></circle></svg>';
var TR_CLK='<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>';
var TR_BOX='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>';

function doTrackLookup(code) {
  var btn = document.getElementById("track-btn");
  var status = document.getElementById("track-status");
  var results = document.getElementById("track-results");
  var demo = document.getElementById("track-demo");
  var oldTxt = btn.textContent;
  btn.disabled = true; btn.textContent = tL("track.searching");
  status.innerHTML = "";
  fetch("/api/track", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: code }) })
    .then(function(r){ return r.json(); })
    .catch(function(){ return { found: false }; })
    .then(function(d){
      btn.disabled = false; btn.textContent = oldTxt;
      if (!d || !d.found || !(d.shipments && d.shipments.length)) {
        results.innerHTML = ""; if (demo) demo.style.display = "";
        status.innerHTML = '<div class="track-msg">' + tL("track.notfound") + '</div>';
        return;
      }
      if (demo) demo.style.display = "none";
      var head = (d.count > 1) ? '<div class="track-count">' + tL("track.count").replace("{n}", d.count) + '</div>' : "";
      var reset = '<button class="track-newsearch" onclick="trackReset()">' + tL("track.newsearch") + '</button>';
      results.innerHTML = head + d.shipments.map(renderLandingShip).join("") + reset;
    });
}

function trackReset(){
  var results = document.getElementById("track-results");
  var status = document.getElementById("track-status");
  var demo = document.getElementById("track-demo");
  var input = document.getElementById("track-input");
  if (results) results.innerHTML = "";
  if (status) status.innerHTML = "";
  if (demo) demo.style.display = "";
  if (input) { input.value = ""; input.focus(); }
}

/* toggle a card's inline event timeline open/closed */
function trTimeline(btn){
  var tl = btn.parentNode.querySelector(".track-tl");
  if (!tl) return;
  var open = tl.classList.toggle("open");
  btn.textContent = open ? tL("track.hidetl") : tL("track.showtl");
}

function renderLandingShip(s){
  var cur = s.current || {};
  var first = (s.items || [])[0] || {};
  var more = ((s.items || []).length > 1) ? ' <span class="tk-more">+' + (s.items.length - 1) + '</span>' : "";
  var step = trStep(s);
  var nodes = "";
  for (var n = 1; n <= 4; n++) {
    var done = (step === 5 || n < step), isCur = (n === step);
    nodes += '<span class="node' + (done ? ' done' : (isCur ? ' cur' : '')) + '"></span>';
    if (n < 4) nodes += '<span class="seg' + ((step === 5 || n < step) ? ' fill' : '') + '"></span>';
  }
  var eta = (s.est_from && s.est_to) ? trFmtRange(s.est_from, s.est_to)
          : (s.est_delivery ? trFmt(s.est_delivery) : "—");
  var thumb = first.image
    ? '<div class="illo"><img src="' + trEsc(first.image) + '" loading="lazy" alt="" onerror="showPh(this)"><div class="ph" style="display:none"><small>📦</small></div></div>'
    : '<div class="tk-noimg">' + TR_BOX + '</div>';
  var evs = (s.events || []).slice().reverse();
  var tl = evs.length ? ('<ul class="track-tl">' + evs.map(function(e){
      return '<li class="b-' + (e.bucket || 'transit') + '"><span class="tl-dot"></span>'
           + '<div class="tl-lab" dir="auto">' + trEsc(e.label) + '</div>'
           + '<div class="tl-when">' + trFmt(e.date) + '</div></li>';
    }).join("") + '</ul>') : "";
  var asof = (s.stale && s.as_of) ? '<div class="track-asof">' + tL("track.asof") + ' ' + trFmt(s.as_of) + '</div>' : "";
  var toggle = tl ? '<button class="btn" onclick="trTimeline(this)">' + tL("track.showtl") + '</button>' : "";
  return '<div class="track-card">'
    + '<div class="track-top">'
    +   '<div class="track-thumb">' + thumb + '</div>'
    +   '<div class="track-meta"><div class="nm" dir="auto">' + (trEsc(first.title) || tL("track.yourship")) + more + '</div>'
    +     (s.tracking ? '<div class="id">' + trEsc(s.tracking) + '</div>' : '') + '</div>'
    +   '<div class="track-rider"><div class="illo"><img src="/static/assets/rider.png" alt="" loading="lazy" onerror="showPh(this)"><div class="ph" style="display:none"><small>rider.png</small></div></div></div>'
    + '</div>'
    + '<div class="prog" aria-hidden="true">' + nodes + '</div>'
    + '<div class="prog-labels"><span>' + tL("track.s1") + '</span><span>' + tL("track.s2") + '</span><span>' + tL("track.s3") + '</span><span>' + tL("track.s4") + '</span></div>'
    + '<div class="track-fields">'
    +   '<div class="f"><div class="k">' + TR_PIN + '<span>' + tL("track.status") + '</span></div><div class="v" dir="auto">' + (trEsc(cur.label) || "—") + '</div></div>'
    +   '<div class="f"><div class="k">' + TR_CLK + '<span>' + tL("track.eta") + '</span></div><div class="v">' + eta + '</div></div>'
    + '</div>'
    + asof + toggle + tl
    + '</div>';
}

/* keyboard: Enter triggers track */
document.addEventListener("keydown", function(e) {
  if (e.key === "Enter") {
    if (document.activeElement && document.activeElement.id === "track-input") trackSubmit();
  }
});

function showPh(img) {
  img.style.display = "none";
  var ph = img.nextElementSibling;
  if (ph) ph.style.display = "flex";
}

/* Scroll reveal */
var revealObserver = new IntersectionObserver(function(entries) {
  entries.forEach(function(entry) {
    if (entry.isIntersecting) {
      entry.target.classList.add("in");
      revealObserver.unobserve(entry.target);
    }
  });
}, { threshold: 0.12 });

document.querySelectorAll(".reveal").forEach(function(el) {
  revealObserver.observe(el);
});

window.addEventListener("load", function() {
  setTimeout(function() {
    document.querySelectorAll(".reveal:not(.in)").forEach(function(el) {
      el.classList.add("in");
    });
  }, 900);
});

buildWa();
