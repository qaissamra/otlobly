#!/usr/bin/env python3
"""
Arabic WhatsApp message templates for Otlobly customers + wa.me deep links.

Nothing is sent — each function returns ready-to-copy text, and wa_link() wraps
it in a https://wa.me/<number>?text=… URL you click to open the chat prefilled.
No email, ever. Amounts in USD ($).

    python3 messages.py        # preview all templates for the first order
"""

from urllib.parse import quote

import store

BRAND = "Otlobly"

# Logical status -> customer-facing Arabic line.
STATUS_AR = {
    "REQUESTED": "📝 تم استلام طلبك",
    "QUOTED":    "💲 تم تسعير طلبك",
    "PAID":      "💰 تم تأكيد الدفع — جاري تجهيز الطلب",
    "ORDERED":   "✅ تم شراء طلبك من أمازون",
    "SHIPPED":   "🚚 طلبك في طريقه إليك (تم الشحن)",
    "ARRIVED":   "📦 وصل طلبك إلى البلد",
    "DELIVERED": "🛵 طلبك جاهز للتسليم",
    "COLLECTED": "🤝 تم التسليم والتحصيل — شكراً لك",
    "CANCELLED": "❌ تم إلغاء طلبك",
}


def _amount(order):
    a = order.get("amount_to_collect_usd")
    return f"{a:.2f}$" if a is not None else "—"


def _items_block(order):
    lines = []
    for i, it in enumerate(order["items"], 1):
        lines.append(f"{i}. {it.get('clean_url') or it.get('raw_url')}")
    return "\n".join(lines)


def _deposit_line(order):
    note = order["customer"].get("notes", "")
    return f"\n({note})" if "عربون" in note or "Deposit" in note else ""


def quote(order):
    """Price quote to send the customer — the one-click message after import."""
    name = order["customer"]["name"] or "عميلنا العزيز"
    return (
        f"مرحباً {name} 👋\n"
        f"عرض السعر لطلبك من {BRAND}:\n\n"
        f"🧾 المنتجات:\n{_items_block(order)}\n\n"
        f"💵 السعر النهائي (شامل الشحن والجمارك حتى فلسطين): {_amount(order)}\n\n"
        f"للتأكيد والمتابعة أرسل (نعم) ✅"
    )


def order_placed(order):
    """Confirmation that the Amazon order was placed."""
    name = order["customer"]["name"] or "عميلنا العزيز"
    return (
        f"مرحباً {name} 👋\n"
        f"✅ تم شراء طلبك ({order['order_id']}) من أمازون وهو الآن قيد التجهيز.\n"
        f"سنوافيك برقم التتبع فور شحنه. شكراً لثقتك — {BRAND}"
    )


def confirmation(order):
    """Order received + items + amount to collect."""
    name = order["customer"]["name"] or "عميلنا العزيز"
    return (
        f"مرحباً {name} 👋\n"
        f"شكراً لطلبك من {BRAND}.\n\n"
        f"🧾 طلبك:\n{_items_block(order)}\n\n"
        f"💵 المبلغ المطلوب عند الاستلام: {_amount(order)}"
        f"{_deposit_line(order)}\n\n"
        f"سنخبرك بكل تحديث على طلبك. 🙏"
    )


def payment_due(order):
    """Reminder of the amount to collect on delivery."""
    name = order["customer"]["name"] or "عميلنا العزيز"
    return (
        f"مرحباً {name} 👋\n"
        f"المبلغ المطلوب لطلبك ({order['order_id']}) عند الاستلام هو "
        f"{_amount(order)}.\n"
        f"الدفع نقداً عند التسليم. شكراً لك 🙏 — {BRAND}"
    )


def status_update(order):
    """Generic status notification in Arabic."""
    name = order["customer"]["name"] or "عميلنا العزيز"
    line = STATUS_AR.get(order["status"], order["status"])
    tail = ""
    if order["status"] in ("ARRIVED", "DELIVERED"):
        tail = f"\n💵 المبلغ عند الاستلام: {_amount(order)}"
    return f"مرحباً {name} 👋\n{line} ({order['order_id']}).{tail}\n— {BRAND}"


def ready_for_pickup(order):
    name = order["customer"]["name"] or "عميلنا العزيز"
    return (
        f"مرحباً {name} 👋\n"
        f"🛵 طلبك ({order['order_id']}) جاهز للتسليم.\n"
        f"💵 المبلغ المطلوب: {_amount(order)}\n"
        f"يرجى تجهيز المبلغ نقداً. شكراً لك — {BRAND}"
    )


def wa_link(order, text):
    """Build a click-to-chat link with the message prefilled."""
    ph = store.primary_phone(order)
    if not ph:
        return None
    return f"https://wa.me/{ph['wa']}?text={quote(text)}"


# Template registry used by the dashboard ("kind" -> builder).
TEMPLATES = {
    "quote": quote,
    "confirmation": confirmation,
    "payment_due": payment_due,
    "order_placed": order_placed,
    "status_update": status_update,
    "ready_for_pickup": ready_for_pickup,
}


def render(order, kind):
    fn = TEMPLATES.get(kind, status_update)
    text = fn(order)
    return {"kind": kind, "text": text, "wa_link": wa_link(order, text)}


if __name__ == "__main__":
    db = store.load()
    if not db["orders"]:
        raise SystemExit("No orders — run ingest.py first.")
    o = db["orders"][0]
    for kind in TEMPLATES:
        r = render(o, kind)
        print(f"\n===== {kind} =====")
        print(r["text"])
        print("link:", (r["wa_link"] or "")[:90], "…")
