#!/usr/bin/env python3
"""
Landed-cost split for a purchase order (💵 cost split).

WHY THIS EXISTS
---------------
One Amazon checkout bundles items for several books at once: Otlobly customer
orders, graphics cards, other IT, and Le Luxe watches (partner money). The PO
stores what was PAID as one lump (`total_usd`), while items carry only the raw
item price (`est_cost_usd` / `unit_paid_usd`). The difference — shipping, tax,
import charges — is real money that belonged to nobody: on live PO-0001 that
gap was $248.31 of a $827.55 order.

The naive alternative is to price each category alone (buy the watches, empty
the cart, price the rest). That is WRONG, not just slow: Amazon's shipping
tiers and free-shipping thresholds are non-linear and import charges are
computed over the whole cart, so the separate totals add up to MORE than the
combined checkout. Splitting them that way bills somebody for a discount that
does not exist.

So: one real checkout, then allocate the non-item costs back onto the lines
pro-rata by value — the way a freight forwarder apportions a consolidated
shipment. The combined-cart saving is shared automatically, and the split
provably re-sums to the amount actually paid.

THE ALGORITHM
-------------
1. extras = shipping + tax + import + other − promo.
   If no breakdown was typed but `total_usd` was, run in LUMP mode instead:
   extras = total_usd − Σ(unit × qty). The split still works with one number.
2. Lines carrying a manual `extra_override_usd` take exactly that and drop out
   of the weighting (the heavy/bulky item you know the shipping for).
   pool = extras − Σ(overrides). If the overrides exceed the extras the pool is
   clamped to 0 and a warning is raised — the owner's typed numbers are never
   silently rescaled.
3. Weights are extended item value, w = unit × qty. Σw == 0 falls back to qty,
   and qty == 0 falls back to equal shares, so a PO with no prices yet still
   splits sanely instead of dividing by zero.
4. pool × w/Σw floored to cents, then the leftover cents handed out one at a
   time by LARGEST FRACTIONAL REMAINDER (ties: bigger weight, then item_id, so
   the same PO always splits the same way).
5. Hard invariant, asserted — not a tolerance: the allocated shares sum to the
   pool exactly, and Σ(landed) == Σ(items) + allocated. Re-summing to the cent
   is the entire point of the feature; a drift here is a bug, not a rounding
   quirk.

All arithmetic is Decimal via money.py; values are returned as plain floats for
JSON, exactly like the rest of the app.

  python3 landed.py --selftest
"""

import re
from decimal import Decimal, ROUND_DOWN

import money

CENT = Decimal("0.01")

# Category buckets. Keys and labels mirror goals.py DEFAULTS["categories"] so the
# split can feed the 🏆 Goals cards later without a second vocabulary.
BUCKETS = ("otlobly", "watches", "it", "pc")
BUCKET_LABELS = {
    "otlobly": "Otlobly",
    "watches": "Watches (Le Luxe)",
    "it": "Grafic cards",
    "pc": "Other IT products",
}
UNASSIGNED = "_none"

# The money lines of a PO breakdown. `promo_usd` is stored POSITIVE and
# subtracted (Amazon prints it as "Free Shipping Promo: -$8.99").
COST_KEYS = ("items_usd", "shipping_usd", "promo_usd", "tax_usd",
             "import_usd", "other_usd")
_EXTRA_KEYS = ("shipping_usd", "tax_usd", "import_usd", "other_usd")


def clean_bucket(v):
    """Normalize a bucket tag; anything unknown → None (unassigned)."""
    v = (v or "").strip().lower()
    return v if v in BUCKETS else None


def clean_costs(raw):
    """Whitelist a posted breakdown → {COST_KEYS…, source, at} or None.

    Returns None when nothing was actually typed, so an empty form never
    shadows lump mode."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in COST_KEYS:
        v = raw.get(k)
        try:
            out[k] = round(abs(float(v)), 2) if v not in (None, "") else None
        except (TypeError, ValueError):
            out[k] = None
    if all(out[k] is None for k in COST_KEYS):
        return None
    src = raw.get("source")
    out["source"] = src if src in ("typed", "parsed") else "typed"
    out["at"] = raw.get("at") or None
    return out


def has_breakdown(costs):
    return bool(costs) and any(costs.get(k) is not None for k in _EXTRA_KEYS)


# --------------------------------------------------------------------------- #
# Allocation
# --------------------------------------------------------------------------- #
def _alloc(pool, weights):
    """Split `pool` (Decimal, any sign) across `weights` (list of (key, Decimal)).

    Floor-to-cents + largest fractional remainder. Returns {key: Decimal}, and
    Σ of the values is EXACTLY `pool`."""
    out = {k: Decimal("0") for k, _ in weights}
    if not weights or pool == 0:
        return out
    total_w = sum(w for _, w in weights)
    if total_w <= 0:                      # caller already fell back; nothing to weigh
        return out

    sign = Decimal(-1) if pool < 0 else Decimal(1)
    amount = abs(pool)

    floors, rems = {}, {}
    for k, w in weights:
        raw = amount * w / total_w
        f = raw.quantize(CENT, rounding=ROUND_DOWN)
        floors[k] = f
        rems[k] = raw - f

    leftover = int(((amount - sum(floors.values())) / CENT).to_integral_value())
    # Deterministic: biggest fractional remainder first, then biggest weight,
    # then key — so re-opening a PO never shuffles the odd cents around.
    order = sorted(weights, key=lambda kw: (-rems[kw[0]], -kw[1], str(kw[0])))
    for i in range(max(0, leftover)):
        floors[order[i % len(order)][0]] += CENT

    return {k: sign * v for k, v in floors.items()}


def _unit_of(item, fallback_to_est):
    """(unit Decimal, source) — the paid unit price, else the estimate."""
    v = item.get("unit_paid_usd")
    if v not in (None, ""):
        return money.q2(v), "paid"
    if fallback_to_est:
        v = item.get("est_cost_usd")
        if v not in (None, ""):
            return money.q2(v), "est"
    return Decimal("0"), "none"


def _roll(bucketed, key, line):
    row = bucketed.setdefault(key, {"n_lines": 0, "qty": 0, "items_usd": Decimal("0"),
                                    "extras_usd": Decimal("0"), "landed_usd": Decimal("0")})
    row["n_lines"] += 1
    row["qty"] += line["qty"]
    row["items_usd"] += line["_items"]
    row["extras_usd"] += line["_extras"]
    row["landed_usd"] += line["_landed"]


def _floatify(rows, label_map=None):
    out = {}
    for k, r in rows.items():
        out[k] = {"n_lines": r["n_lines"], "qty": r["qty"],
                  "items_usd": money.to_usd(r["items_usd"]),
                  "extras_usd": money.to_usd(r["extras_usd"]),
                  "landed_usd": money.to_usd(r["landed_usd"])}
        if label_map is not None:
            out[k]["label"] = label_map.get(k, "—")
    return out


def split_po(po, *, fallback_to_est=True):
    """Landed cost per line + rollups by package / bucket / customer order.

    See the module docstring for the algorithm. Never raises on bad data — a
    PO with no prices, no totals or no items comes back with zeroed lines and a
    warning, because this runs on every board render."""
    po = po or {}
    warnings = []
    costs = po.get("costs") if isinstance(po.get("costs"), dict) else None
    total_usd = po.get("total_usd")
    total = money.q2(total_usd) if total_usd not in (None, "") else None

    lines = []
    for pkg in po.get("packages") or []:
        for it in pkg.get("items") or []:
            unit, src = _unit_of(it, fallback_to_est)
            qty = int(it.get("qty") or 1)
            ov = it.get("extra_override_usd")
            # An item already matched to a customer order IS an Otlobly line —
            # derived, not stored, so it keeps following the match if the item
            # is later re-assigned to another customer.
            bucket = clean_bucket(it.get("bucket"))
            auto = not bucket and bool(it.get("customer_order_id"))
            lines.append({
                "item_id": it.get("item_id"),
                "package_no": pkg.get("package_no"),
                "title": it.get("title") or "",
                "asin": it.get("asin"),
                "bucket": bucket or ("otlobly" if auto else None),
                "bucket_auto": auto,
                "customer_order_id": it.get("customer_order_id"),
                "customer_name": it.get("customer_name"),
                "qty": qty,
                "unit_usd": money.to_usd(unit),
                "unit_src": src,
                "override": ov not in (None, ""),
                "_unit": unit,
                "_override": money.q2(ov) if ov not in (None, "") else None,
                "_items": unit * qty,
            })

    items_value = sum((ln["_items"] for ln in lines), Decimal("0"))

    # --- how much is there to share out? ---
    if has_breakdown(costs):
        basis = "breakdown"
        extras = sum((money.q2(costs.get(k)) for k in _EXTRA_KEYS
                      if costs.get(k) is not None), Decimal("0"))
        extras -= money.q2(costs.get("promo_usd") or 0)
        declared_items = (money.q2(costs["items_usd"])
                          if costs.get("items_usd") is not None else items_value)
        delta = (total - (declared_items + extras)) if total is not None else Decimal("0")
        if total is None:
            warnings.append("no_total")
        elif delta != 0:
            warnings.append("unbalanced")
        # The typed Items line is the truth about what the goods cost; if the
        # per-line prices don't add up to it, that gap is still real money and
        # has to land somewhere, so it rides along with the extras.
        if costs.get("items_usd") is not None and declared_items != items_value:
            extras += declared_items - items_value
            warnings.append("items_mismatch")
    elif total is not None:
        basis = "lump"
        extras = total - items_value
        delta = Decimal("0")
        declared_items = items_value
        if extras < 0:
            warnings.append("negative_extras")
    else:
        basis = "none"
        extras = Decimal("0")
        delta = Decimal("0")
        declared_items = items_value
        warnings.append("no_costs")

    # --- overrides come off the top ---
    overrides = sum((ln["_override"] for ln in lines
                     if ln["_override"] is not None), Decimal("0"))
    pool = extras - overrides
    if extras >= 0 and pool < 0:
        warnings.append("overrides_exceed_extras")
        pool = Decimal("0")

    # --- weights: value → qty → equal ---
    free = [ln for ln in lines if ln["_override"] is None]
    weights = [(ln["item_id"], max(ln["_items"], Decimal("0"))) for ln in free]
    if free and sum(w for _, w in weights) <= 0:
        weights = [(ln["item_id"], Decimal(ln["qty"])) for ln in free]
        warnings.append("weighted_by_qty")
        if sum(w for _, w in weights) <= 0:
            weights = [(ln["item_id"], Decimal(1)) for ln in free]
            warnings.append("weighted_equally")
    if not free and pool != 0:
        warnings.append("nothing_to_allocate")
        pool = Decimal("0")

    shares = _alloc(pool, weights)

    for ln in lines:
        ln["_extras"] = (ln["_override"] if ln["_override"] is not None
                         else shares.get(ln["item_id"], Decimal("0")))
        ln["_landed"] = ln["_items"] + ln["_extras"]

    allocated = sum((ln["_extras"] for ln in lines), Decimal("0"))
    # The whole point of the feature — re-summing to the cent is guaranteed,
    # not hoped for.
    assert allocated == overrides + pool, (allocated, overrides, pool)
    assert (sum((ln["_landed"] for ln in lines), Decimal("0"))
            == items_value + allocated)

    by_pkg, by_bucket, by_order = {}, {}, {}
    for ln in lines:
        _roll(by_pkg, str(ln["package_no"]), ln)
        _roll(by_bucket, ln["bucket"] or UNASSIGNED, ln)
        _roll(by_order, ln["customer_order_id"] or UNASSIGNED, ln)

    out_lines = []
    for ln in lines:
        out_lines.append({k: v for k, v in ln.items() if not k.startswith("_")}
                         | {"items_usd": money.to_usd(ln["_items"]),
                            "extras_usd": money.to_usd(ln["_extras"]),
                            "landed_usd": money.to_usd(ln["_landed"])})

    return {
        "basis": basis,
        "items_usd": money.to_usd(items_value),
        "declared_items_usd": money.to_usd(declared_items),
        "extras_usd": money.to_usd(extras),
        "allocated_usd": money.to_usd(allocated),
        "unallocated_usd": money.to_usd(extras - allocated),
        "total_usd": money.to_usd(total) if total is not None else None,
        "landed_usd": money.to_usd(items_value + allocated),
        "balanced": bool(total is not None and delta == 0),
        "delta_usd": money.to_usd(delta),
        "n_lines": len(lines),
        "n_unassigned": sum(1 for ln in lines if not ln["bucket"]),
        "lines": out_lines,
        "by_package": _floatify(by_pkg),
        "by_bucket": _floatify(by_bucket, {**BUCKET_LABELS, UNASSIGNED: "—"}),
        "by_order": _floatify(by_order),
        "warnings": warnings,
    }


def bucket_totals(po, *, fallback_to_est=True):
    """Just the per-bucket landed totals — the compact block the board list needs."""
    return split_po(po, fallback_to_est=fallback_to_est)["by_bucket"]


# --------------------------------------------------------------------------- #
# Paste helper — Amazon's order summary → the breakdown fields
# --------------------------------------------------------------------------- #
# Same shape as order_placer.parse_summary (which predates this and lacks tax +
# promo); keys here match COST_KEYS so a paste fills the form directly.
_AMT = r"(?:USD\s*)?\$?\s*(-?[\d,]+\.\d{2})"
_LINE_PATTERNS = [
    ("order_total_usd", r"(?:Order\s*total|Grand\s*total)\b[^\n]*?" + _AMT),
    ("promo_usd", r"(?:Free\s*Shipping\s*Promo|Promotions?\s*Applied|Promo)\b[^\n]*?" + _AMT),
    ("import_usd", r"(?:Estimated\s*)?Import\s*(?:Charges?|Fees?(?:\s*Deposit)?)\b[^\n]*?" + _AMT),
    # "Total BEFORE tax" is a subtotal line, not the tax line — never let it match.
    ("tax_usd", r"(?<!before\s)(?:Estimated\s*)?tax(?:\s*to\s*be\s*collected)?\b[^\n]*?" + _AMT),
    ("shipping_usd", r"Shipping\s*(?:&|and)?\s*handling\b[^\n]*?" + _AMT),
    ("shipping_usd", r"(?:AmazonGlobal\s*)?(?:Shipping|Delivery)\b[^\n]*?" + _AMT),
    ("items_usd", r"Items?\b(?:\s*\([^)]*\))?\s*[^\n]*?" + _AMT),
]


def parse_amazon_summary(text):
    """Amazon's order-summary text → {items_usd, shipping_usd, promo_usd,
    tax_usd, import_usd, order_total_usd}. Missing lines come back None.

    Scanned line by line, first match wins per key, so a promo line can never be
    read as shipping and a 'Total before tax' line can never be read as tax."""
    out = {k: None for k in ("items_usd", "shipping_usd", "promo_usd", "tax_usd",
                             "import_usd", "order_total_usd")}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for key, pat in _LINE_PATTERNS:
            if out[key] is not None:
                continue
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                out[key] = round(abs(float(m.group(1).replace(",", ""))), 2)
                break          # one number per line
    return out


# --------------------------------------------------------------------------- #
def _selftest():
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'OK ' if ok else 'XX '} {name:44} got={got!r} want={want!r}")

    def po(items, **kw):
        return {"packages": [{"package_no": 1, "items": items}], **kw}

    def item(iid, unit, qty=1, **kw):
        return {"item_id": iid, "qty": qty, "unit_paid_usd": unit, **kw}

    # --- awkward extras that don't divide evenly ---
    p = po([item("a", 100), item("b", 50), item("c", 25)],
           total_usd=185.01,
           costs={"items_usd": 175, "shipping_usd": 10.01})
    s = split_po(p)
    ck("extras", s["extras_usd"], 10.01)
    ck("shares sum to extras", round(sum(l["extras_usd"] for l in s["lines"]), 2), 10.01)
    ck("landed sums to paid total", s["landed_usd"], 185.01)
    ck("value pro-rata (100/175)", s["lines"][0]["extras_usd"], 5.72)
    ck("balanced", s["balanced"], True)

    # deterministic across runs
    ck("deterministic", [l["extras_usd"] for l in split_po(p)["lines"]],
       [l["extras_usd"] for l in s["lines"]])

    # --- lump mode: only the paid total typed ---
    s = split_po(po([item("a", 30, 2), item("b", 40)], total_usd=120))
    ck("lump basis", s["basis"], "lump")
    ck("lump extras", s["extras_usd"], 20.0)
    ck("lump landed == total", s["landed_usd"], 120.0)
    ck("lump share by value 60/100", s["lines"][0]["extras_usd"], 12.0)

    # --- override takes exactly its amount, rest re-prorates ---
    s = split_po(po([item("a", 100, extra_override_usd=30), item("b", 100)],
                    total_usd=230, costs={"items_usd": 200, "shipping_usd": 30}))
    ck("override honoured", s["lines"][0]["extras_usd"], 30.0)
    ck("override leaves 0 pool", s["lines"][1]["extras_usd"], 0.0)

    s = split_po(po([item("a", 100, extra_override_usd=50), item("b", 100)],
                    total_usd=210, costs={"items_usd": 200, "shipping_usd": 10}))
    ck("over-override warns", "overrides_exceed_extras" in s["warnings"], True)
    ck("over-override not rescaled", s["lines"][0]["extras_usd"], 50.0)

    # --- no prices yet → qty, then equal ---
    s = split_po(po([item("a", None, 3), item("b", None, 1)], total_usd=40))
    ck("qty fallback", "weighted_by_qty" in s["warnings"], True)
    ck("qty split 3:1", [l["extras_usd"] for l in s["lines"]], [30.0, 10.0])
    s = split_po(po([item("a", None, 0), item("b", None, 0)], total_usd=10))
    ck("equal fallback", [l["extras_usd"] for l in s["lines"]], [5.0, 5.0])

    # --- rollups re-sum to the paid total ---
    p = po([item("a", 100, bucket="watches", customer_order_id=None),
            item("b", 50, bucket="otlobly", customer_order_id="OTL-1"),
            item("c", 25, bucket="it")], total_usd=185.01,
           costs={"items_usd": 175, "shipping_usd": 10.01})
    s = split_po(p)
    for name, roll in (("bucket", s["by_bucket"]), ("order", s["by_order"]),
                       ("package", s["by_package"])):
        ck(f"by_{name} re-sums", round(sum(r["landed_usd"] for r in roll.values()), 2), 185.01)
    ck("watches bucket landed", s["by_bucket"]["watches"]["landed_usd"], 105.72)
    ck("unassigned counted", s["n_unassigned"], 0)

    # a matched customer line is Otlobly without being tagged
    s = split_po(po([item("a", 10, customer_order_id="OTL-9"), item("b", 10)], total_usd=20))
    ck("matched line auto-buckets", s["lines"][0]["bucket"], "otlobly")
    ck("auto flagged", s["lines"][0]["bucket_auto"], True)
    ck("untagged stays unassigned", s["n_unassigned"], 1)

    # --- unbalanced breakdown is reported, never silently fixed ---
    s = split_po(po([item("a", 100)], total_usd=120, costs={"items_usd": 100, "shipping_usd": 10}))
    ck("delta reported", s["delta_usd"], 10.0)
    ck("unbalanced flagged", s["balanced"], False)

    # --- empty / degenerate ---
    s = split_po({})
    ck("empty PO safe", (s["n_lines"], s["landed_usd"]), (0, 0.0))
    s = split_po(po([], total_usd=50))
    ck("no items warns", "nothing_to_allocate" in s["warnings"], True)

    # --- the paste parser ---
    txt = ("Items (3):                 $114.81\n"
           "Shipping & handling:        $12.00\n"
           "Free Shipping Promo:        -$8.99\n"
           "Total before tax:          $117.82\n"
           "Estimated tax to be collected: $5.00\n"
           "Estimated Import Charges:   $30.87\n"
           "Order total:               $153.69\n")
    g = parse_amazon_summary(txt)
    ck("parse items", g["items_usd"], 114.81)
    ck("parse shipping", g["shipping_usd"], 12.00)
    ck("parse promo positive", g["promo_usd"], 8.99)
    ck("parse tax (not 'before tax')", g["tax_usd"], 5.00)
    ck("parse import", g["import_usd"], 30.87)
    ck("parse order total", g["order_total_usd"], 153.69)
    ck("promo nets out of extras",
       split_po(po([item("a", 114.81)], total_usd=153.69,
                   costs=clean_costs(g)))["extras_usd"], 38.88)

    txt2 = "Item(s) Subtotal: USD 200.00\nAmazonGlobal Shipping: USD 15.00\nOrder Total: USD 215.00"
    g = parse_amazon_summary(txt2)
    ck("USD-style items", g["items_usd"], 200.00)
    ck("USD-style shipping", g["shipping_usd"], 15.00)

    # --- whitelist ---
    ck("clean_costs drops empties", clean_costs({"shipping_usd": ""}), None)
    ck("clean_bucket rejects junk", clean_bucket("nope"), None)
    ck("clean_bucket accepts", clean_bucket(" Watches "), "watches")

    print("landed:", "ALL PASS" if not fails else f"FAILURES {fails}")
    return fails


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print(__doc__)
