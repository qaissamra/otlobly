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
    "track.inputph":  "أدخل رقم تتبّع OTL…",
    "track.submit":   "تتبّع",
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
    "nav.shop":       "تسوّق",
    "nav.track":      "تتبّع",
    "nav.how":        "كيف تعمل",
    "nav.plans":      "الباقات",
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
    "track.inputph":  "Enter OTL tracking number…",
    "track.submit":   "Track",
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
    "nav.shop":       "Shop",
    "nav.track":      "Track",
    "nav.how":        "How it works",
    "nav.plans":      "Pricing",
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

  /* lang toggle active state */
  document.getElementById("lang-ar").classList.toggle("on", lang === "ar");
  document.getElementById("lang-en").classList.toggle("on", lang === "en");

  buildWa();
}

function buildWa() {
  var msg = I18N[currentLang]["wa.msg"];
  var url = "https://wa.me/" + WA_NUMBER + "?text=" + encodeURIComponent(msg);
  ["wa-hero", "wa-sitenav", "wa-mob"].forEach(function(id) {
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

/* ── Shared track lookup: hand off to the real tracking page.
      /track auto-runs from ?t= (OTL number) or ?q= (phone). ── */
function doTrackLookup(code) {
  var normalized = code.toUpperCase().replace(/\s+/g, "");
  var param = /^OTL/i.test(normalized) ? "t" : "q";
  window.location.href = "/track?" + param + "=" + encodeURIComponent(normalized);
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
