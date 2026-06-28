#!/usr/bin/env python3
"""
Phase 2 — auto-place Otlobly orders on Amazon via the existing Multilogin
anti-detection automation, and read the AUTHORITATIVE landed price straight off
the checkout/review page (item + shipping + Import Fees Deposit → order total),
then apply the markup.

Safety, by design (matches the multilogin project's fail-closed discipline):
  * Health-gated: ip_check (proxy residential/right-country) THEN leak_test
    (automation signals) run BEFORE any action. Bad profile -> skip, never act.
  * Capacity-aware: never exceed a profile/box's daily_cap (config.profiles).
  * STOP BEFORE PAY by default: it fills the cart, reaches the order-review page,
    scrapes the totals and writes the price back — but does NOT buy. Pass --place
    to actually click "Place order".

    python3 order_placer.py --dry-run        # capacity plan only, no browser
    python3 order_placer.py --selftest       # offline: checkout-summary parser
    python3 order_placer.py --limit 1        # price 1 order (review page, NO buy)
    python3 order_placer.py --limit 1 --place  # actually place 1 order

The browser flow needs the multilogin-claude-code env (selenium + a started
Multilogin agent) and each profile's folder_id/profile_id filled into config.json.
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import cfg
import pricing
import store

# The sibling anti-detection project we extend.
MLX_DIR = Path(__file__).resolve().parent.parent / "multilogin-claude-code"

# Add-to-cart / buy selectors (reused from the multilogin scenarios/human code).
ADD_TO_CART = ("#add-to-cart-button", "#add-to-cart-button-ubb")
PLACE_ORDER = ("#placeYourOrder input", "#submitOrderButtonId input",
               "input[name='placeYourOrder1']", "#bottomSubmitOrderButtonId input")


# --------------------------------------------------------------------------- #
# Checkout-summary parsing (pure, offline-testable)
# --------------------------------------------------------------------------- #
def parse_money(text):
    """First $-amount in a string -> float, or None."""
    if not text:
        return None
    m = re.search(r"\$?\s*([\d,]+\.\d{2})", text)
    return float(m.group(1).replace(",", "")) if m else None


_SUMMARY_PATTERNS = {
    "subtotal_usd":    r"(?:Items?|Subtotal)\b[^$\n]*\$\s*([\d,]+\.\d{2})",
    "shipping_usd":    r"(?:Shipping\s*&\s*handling|Shipping|Delivery)\b[^$\n]*\$\s*([\d,]+\.\d{2})",
    "import_fees_usd": r"Import\s*Fees?\s*Deposit\b[^$\n]*\$\s*([\d,]+\.\d{2})",
    "order_total_usd": r"(?:Order\s*total|Grand\s*Total|Order\s*Total)\b[^$\n]*\$\s*([\d,]+\.\d{2})",
}


def parse_summary(page_text):
    """Extract {subtotal_usd, shipping_usd, import_fees_usd, order_total_usd}
    from the visible text of Amazon's order-summary/review area. Missing lines
    come back as None (e.g. no import fees under the de-minimis threshold)."""
    out = {}
    for key, pat in _SUMMARY_PATTERNS.items():
        m = re.search(pat, page_text, re.IGNORECASE)
        out[key] = float(m.group(1).replace(",", "")) if m else None
    return out


# --------------------------------------------------------------------------- #
# Capacity-aware profile assignment (pure, offline-testable)
# --------------------------------------------------------------------------- #
def _today():
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def placements_today(db, box):
    """How many orders this box already placed today (respects daily_cap)."""
    today = _today()
    return sum(1 for o in db["orders"]
               if o.get("profile_box") == box and (o.get("placed_at") or "")[:10] == today)


def assign_profile(db, config, prefer=None):
    """Pick the box with remaining daily capacity (fewest placements first).
    Returns a box name or None if every profile is at its cap."""
    profiles = cfg.get(config, "profiles", {}) or {}
    boxes = [prefer] if prefer else [b for b in profiles if not b.startswith("_")]
    best, best_used = None, None
    for box in boxes:
        meta = profiles.get(box, {})
        cap = int(meta.get("daily_cap", 0) or 0)
        used = placements_today(db, box)
        if used < cap and (best is None or used < best_used):
            best, best_used = box, used
    return best


def needs_placing(db):
    """Orders still to be ordered (REQUESTED/QUOTED, not yet placed)."""
    return [o for o in db["orders"]
            if o["status"] in ("REQUESTED", "QUOTED") and not o.get("placed_at")]


# --------------------------------------------------------------------------- #
# Write-back
# --------------------------------------------------------------------------- #
def record_result(db, order, checkout, box, placed):
    order["checkout"] = checkout
    amt = pricing.price_from_checkout(checkout)
    if amt is not None:
        order["amount_to_collect_usd"] = amt
    order["profile_box"] = box
    if placed:
        order["placed_at"] = store.now_iso()
        order["status"] = "ORDERED"
    elif order["status"] == "REQUESTED":
        order["status"] = "QUOTED"
    order["updated_at"] = store.now_iso()
    store.save(db)
    return amt


# --------------------------------------------------------------------------- #
# Browser flow (needs the multilogin env; imported lazily)
# --------------------------------------------------------------------------- #
def _mlx_imports():
    sys.path.insert(0, str(MLX_DIR))
    from ip_check import ip_check
    from mlx_driver import start_profile, stop_profile, get_driver
    from leak_test import preflight
    from human import wait_for_content
    import scenarios
    return ip_check, start_profile, stop_profile, get_driver, preflight, wait_for_content, scenarios


def gated_driver(box, config):
    """Stage-1 IP health + launch + Stage-2 leak check. Returns (driver, stop_fn)
    or (None, reason)."""
    (ip_check, start_profile, stop_profile,
     get_driver, preflight, _wait, _sc) = _mlx_imports()
    meta = cfg.get(config, f"profiles.{box}", {}) or {}
    folder_id, profile_id = meta.get("folder_id"), meta.get("profile_id")
    if not folder_id or not profile_id:
        return None, f"{box}: missing folder_id/profile_id in config"

    ipv = ip_check(profile_id, folder_id)             # STAGE 1 — no browser
    if not ipv.get("ok"):
        return None, f"{box}: bad_proxy ({', '.join(ipv.get('flags', []))})"
    try:
        stop_profile(profile_id)
    except Exception:
        pass
    port = start_profile(folder_id, profile_id)
    driver = get_driver(port, page_load_strategy="none")
    if not preflight(driver):                         # STAGE 2 — in-browser leak
        return None, f"{box}: automation signals leaking"
    return driver, (lambda: stop_profile(profile_id))


def place_one(driver, order, wait_for_content, do_place):
    """Add each item to cart, go to checkout, scrape the order summary. Returns
    (checkout_dict, placed_bool). Best-effort, never spends unless do_place."""
    from selenium.webdriver.common.by import By

    def first(selectors):
        for s in selectors:
            els = driver.find_elements(By.CSS_SELECTOR, s)
            if els:
                return els[0]
        return None

    for it in order["items"]:
        if not it.get("asin"):
            continue
        driver.get(f"https://www.amazon.com/dp/{it['asin']}")
        wait_for_content(driver, "#add-to-cart-button, #buy-now-button", 15)
        btn = first(ADD_TO_CART)
        if btn:
            btn.click()
            wait_for_content(driver, "#nav-cart-count, #attachDisplayAddBaseAlert", 10)

    driver.get("https://www.amazon.com/gp/cart/view.html")
    wait_for_content(driver, "[name='proceedToRetailCheckout'], #sc-buy-box-ptc-button", 12)
    ptc = first(("[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input"))
    if ptc:
        ptc.click()
    wait_for_content(driver, "#subtotals, #order-summary-container, .order-summary", 15)

    summary_el = first(("#subtotals", "#order-summary-container", ".order-summary"))
    page_text = (summary_el.text if summary_el else driver.find_element(By.TAG_NAME, "body").text)
    checkout = parse_summary(page_text)

    placed = False
    if do_place:
        po = first(PLACE_ORDER)
        if po:
            po.click()
            placed = True
    return checkout, placed


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show the capacity plan (which box gets which order). No browser.")
    ap.add_argument("--selftest", action="store_true",
                    help="Offline test of the checkout-summary parser.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--box", default=None, help="Force a specific profile/box.")
    ap.add_argument("--place", action="store_true",
                    help="ACTUALLY click 'Place order' (default: stop at review).")
    args = ap.parse_args()

    if args.selftest:
        return sys.exit(_selftest())

    config = cfg.load()
    db = store.load()
    queue = needs_placing(db)
    if args.limit:
        queue = queue[:args.limit]

    if not queue:
        print("Nothing to place (no REQUESTED/QUOTED orders).")
        return

    if args.dry_run:
        print(f"PLAN — {len(queue)} order(s) to place "
              f"({'WILL BUY' if args.place else 'price only, no buy'}):\n")
        plan_db = {"orders": [dict(o) for o in db["orders"]]}  # don't mutate
        for o in queue:
            po = next(x for x in plan_db["orders"] if x["order_id"] == o["order_id"])
            box = assign_profile(plan_db, config, prefer=args.box)
            if not box:
                print(f"  {o['order_id']:9} {o['customer']['name'][:22]:22} -> "
                      f"NO CAPACITY (all profiles at daily_cap)")
                continue
            po["profile_box"] = box
            po["placed_at"] = store.now_iso()           # simulate to test capacity
            asins = ", ".join(it.get("asin") or "?" for it in o["items"])
            print(f"  {o['order_id']:9} {o['customer']['name'][:22]:22} -> box {box:4} "
                  f"[{asins}]")
        print("\n(DRY RUN — no browser launched, nothing written)")
        return

    # Live placement.
    try:
        _imp = _mlx_imports()
    except Exception as e:  # noqa
        sys.exit(f"Multilogin env not available ({e}). "
                 f"Run from a machine with the multilogin-claude-code venv, "
                 f"or use --dry-run / --selftest.")
    wait_for_content = _imp[5]

    open_drivers = {}      # box -> (driver, stop_fn)
    placed = priced = 0
    try:
        for o in queue:
            box = assign_profile(db, config, prefer=args.box)
            if not box:
                print(f"  {o['order_id']}: no capacity — stopping.")
                break
            if box not in open_drivers:
                driver, stop_or_reason = gated_driver(box, config)
                if driver is None:
                    print(f"  ⚠️ {stop_or_reason} — skipping box {box}")
                    continue
                open_drivers[box] = (driver, stop_or_reason)
            driver, _stop = open_drivers[box]
            checkout, did_place = place_one(driver, o, wait_for_content, args.place)
            amt = record_result(db, o, checkout, box, did_place)
            priced += amt is not None
            placed += did_place
            print(f"  {o['order_id']} box {box} -> total {checkout.get('order_total_usd')} "
                  f"=> collect ${amt}  {'PLACED' if did_place else '(review only)'}")
    finally:
        for _box, (driver, stop_fn) in open_drivers.items():
            try:
                stop_fn()
            except Exception:
                pass
    print(f"\nDone. {priced} priced · {placed} placed.")


# --------------------------------------------------------------------------- #
def _selftest():
    sample = (
        "Order Summary\n"
        "Items (2):           $84.96\n"
        "Shipping & handling: $12.34\n"
        "Import Fees Deposit: $9.80\n"
        "Total before tax:    $107.10\n"
        "Order total:         $107.10\n"
    )
    got = parse_summary(sample)
    want = {"subtotal_usd": 84.96, "shipping_usd": 12.34,
            "import_fees_usd": 9.80, "order_total_usd": 107.10}
    ok = got == want
    print("parse_summary:", "OK" if ok else f"XX got={got}")
    collect = pricing.price_from_checkout(got)
    exp = round(107.10 * 1.10, 2)
    ok2 = collect == exp
    print(f"price_from_checkout: {'OK' if ok2 else 'XX'} {collect} (want {exp})")

    # No import fees under the de-minimis -> import_fees_usd None, total still read.
    sample2 = "Items (1): $40.00\nShipping & handling: $0.00\nOrder total: $40.00"
    g2 = parse_summary(sample2)
    ok3 = g2["import_fees_usd"] is None and g2["order_total_usd"] == 40.00
    print("no-import-fees case:", "OK" if ok3 else f"XX {g2}")
    return 0 if (ok and ok2 and ok3) else 1


if __name__ == "__main__":
    main()
