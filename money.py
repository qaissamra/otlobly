#!/usr/bin/env python3
"""
Exact money arithmetic (audit B-4).

All customer money is USD to the cent. Binary floats can't represent 0.10
exactly, so repeated addition drifts (sum([0.1]*10) == 0.9999999999999999) and a
running total that's reused can end a cent off. These helpers do the arithmetic
in `Decimal` and round half-up to cents.

IMPORTANT: values are still STORED and RETURNED as ordinary floats (JSON numbers /
SQLite REAL), exactly as before — only the computation in between is exact. Nothing
about the on-disk format or the API shape changes, so existing data reads back
unchanged.

  python3 money.py --selftest
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")


def D(x):
    """Coerce to Decimal via str (so float repr noise like 0.1→0.1000000000000000055
    never enters the math). None / "" / unparseable → Decimal('0')."""
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def q2(x):
    """`x` as a Decimal quantized to cents, round-half-up."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def to_usd(x):
    """A money value rounded to cents, as a float (for storage / JSON)."""
    return float(q2(x))


def usd_sum(values):
    """Exact sum of money values → rounded-to-cents float. No float drift."""
    total = Decimal("0")
    for v in values:
        total += D(v)
    return float(total.quantize(CENT, rounding=ROUND_HALF_UP))


def mul(a, b):
    """a * b as a rounded-to-cents float (e.g. landed cost × (1 + markup))."""
    return float((D(a) * D(b)).quantize(CENT, rounding=ROUND_HALF_UP))


def div(a, b):
    """a / b as a rounded-to-cents float (e.g. ₪ amount ÷ fx rate → $). b==0 → 0."""
    db = D(b)
    if db == 0:
        return 0.0
    return float((D(a) / db).quantize(CENT, rounding=ROUND_HALF_UP))


def _selftest():
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'OK ' if ok else 'XX '} {name:38} got={got!r} want={want!r}")

    # exactness where float drifts
    ck("sum of ten 0.10 == 1.00", usd_sum([0.10] * 10), 1.00)
    ck("0.10 + 0.20 == 0.30", usd_sum([0.10, 0.20]), 0.30)
    ck("many small deposits exact", usd_sum([0.01] * 100), 1.00)
    # equivalence for ordinary values
    ck("mul markup 100*1.10", mul(100, 1.10), 110.00)
    ck("mul markup 49.99*1.10 half-up", mul(49.99, 1.10), 54.99)
    ck("div 50 / 3.7", div(50, 3.7), 13.51)
    ck("div by zero → 0", div(10, 0), 0.0)
    # netting (deposit + collect − refund)
    ck("50 + 20 − 10 net", usd_sum([50.0, 20.0, -10.0]), 60.00)
    # coercion
    ck("None sums as 0", usd_sum([None, 5.0, ""]), 5.00)
    ck("to_usd rounds half-up", to_usd(1.005), 1.01)
    print("money:", "ALL PASS" if not fails else f"FAILURES {fails}")
    return fails


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(1 if _selftest() else 0)
    print(__doc__)
