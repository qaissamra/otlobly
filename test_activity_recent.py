#!/usr/bin/env python3
"""
Self-checks for activity.recent() reading the log BACKWARDS.

Why this exists: recent() used to slurp the whole activity.jsonl, split it into
every line and json-parse all of them just to keep the newest few. At the live
file's 12 MB that cost ~36 MB per call — and /api/notifications, which the staff
board polls every 5 seconds, was walking the process into Render's 512 MB limit
until it was OOM-killed (repeatedly, on 2026-07-27).

The rewrite must be a pure optimisation: identical output in every case. These
checks compare it against the original implementation, and lean on the nasty
parts of reading a file backwards — chunk boundaries, a missing trailing
newline, blank and corrupt lines.

    ./.venv/bin/python test_activity_recent.py
"""

import json
import os
import tempfile
import tracemalloc
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-activity-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import activity  # noqa: E402

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def reference(path, limit=60, entity=None, entity_id=None):
    """The ORIGINAL implementation, kept as the oracle."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if entity and ev.get("entity") != entity:
            continue
        if entity_id and ev.get("entity_id") != str(entity_id):
            continue
        out.append(ev)
    out.reverse()
    return out[:limit]


def _write(path, n=500, trailing_newline=True, dirt=False):
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "ts": f"2026-07-27T00:{i // 60:02d}:{i % 60:02d}",
            "action": "set", "entity": "purchase" if i % 3 else "order",
            "entity_id": str(i % 7), "label": f"event {i}",
            # a long field so lines straddle chunk boundaries
            "detail": "x" * (i % 97)}))
    if dirt:
        lines.insert(len(lines) // 2, "")             # blank line
        lines.insert(len(lines) // 3, "{not json")    # corrupt line
        lines.append("")                              # trailing blank
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    Path(path).write_text(text, encoding="utf-8")


def _compare(path, **kw):
    """activity.recent() against the oracle, on the same file."""
    activity._activity_file = lambda business_id=None: Path(path)
    return activity.recent(**kw) == reference(path, **kw)


def test_matches_the_original():
    p = _TMP / "a.jsonl"
    _write(p, n=500)
    for kw in (dict(limit=200), dict(limit=60), dict(limit=1), dict(limit=10_000),
               dict(limit=50, entity="purchase"), dict(limit=50, entity="order"),
               dict(limit=30, entity_id="3"), dict(limit=5, entity="order", entity_id="6")):
        check(f"identical output for {kw}", _compare(p, **kw))


def test_awkward_files():
    p = _TMP / "b.jsonl"
    _write(p, n=300, trailing_newline=False)
    check("file with NO trailing newline", _compare(p, limit=50))
    check("  …and its newest event is the real last line",
          activity.recent(limit=1)[0]["label"] == "event 299")

    _write(p, n=300, dirt=True)
    check("blank and corrupt lines are skipped, same as before", _compare(p, limit=80))

    _write(p, n=1)
    check("single-line file", _compare(p, limit=10))

    Path(p).write_text("", encoding="utf-8")
    activity._activity_file = lambda business_id=None: Path(p)
    check("empty file → []", activity.recent(limit=10) == [])

    missing = _TMP / "nope.jsonl"
    activity._activity_file = lambda business_id=None: missing
    check("missing file → []", activity.recent(limit=10) == [])


def test_chunk_boundaries():
    """The backward reader stitches partial lines across chunk reads — force it
    with a tiny chunk so every read lands mid-line."""
    p = _TMP / "c.jsonl"
    _write(p, n=400)
    real = activity._lines_newest_first
    try:
        for chunk in (7, 13, 64, 1024):
            activity._lines_newest_first = (
                lambda path, _c=chunk: real(path, chunk=_c))
            ok = _compare(p, limit=120)
            check(f"identical with a {chunk}-byte chunk (lines split mid-read)", ok)
    finally:
        activity._lines_newest_first = real


def test_reads_only_the_tail():
    """The whole point: a big log must not be fully loaded to get a few events."""
    p = _TMP / "big.jsonl"
    _write(p, n=40_000)
    size_mb = Path(p).stat().st_size / 1024 / 1024
    activity._activity_file = lambda business_id=None: Path(p)

    tracemalloc.start()
    got = activity.recent(limit=200)
    _, peak_new = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    reference(p, limit=200)
    _, peak_old = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"  -- {size_mb:.1f}MB log: old peak {peak_old / 1024 / 1024:.1f}MB, "
          f"new peak {peak_new / 1024 / 1024:.2f}MB")
    check("returns the events asked for", len(got) == 200)
    check("newest first", got[0]["label"] == "event 39999")
    check("uses a small fraction of the old memory", peak_new < peak_old / 10)
    check("stays under 5MB on a multi-MB log", peak_new < 5 * 1024 * 1024)


def test_max_scan_bounds_a_filtered_read():
    """`limit` only bounds a read that keeps MATCHING. A filtered read that
    matches little walks to EOF — which is what the per-row Activity panel does
    on every product click. max_scan caps it and says so."""
    p = _TMP / "scan.jsonl"
    _write(p, n=5_000)
    activity._activity_file = lambda business_id=None: Path(p)

    page = activity.recent_page(limit=50, entity="nope", max_scan=500)
    check("a no-match filtered read stops at max_scan", page["events"] == [])
    check("  …and reports itself truncated", page["truncated"] is True)

    page = activity.recent_page(limit=50, entity="nope", max_scan=0)
    check("max_scan=0 means walk it all (not truncated)", page["truncated"] is False)

    page = activity.recent_page(limit=10, max_scan=5_000)
    check("a read that fills up early is NOT truncated",
          len(page["events"]) == 10 and page["truncated"] is False)
    check("recent() still returns a plain list",
          activity.recent(limit=10) == page["events"])


def test_entity_ids_union():
    """The order-level panel rolls its packages + products up in ONE pass."""
    p = _TMP / "ids.jsonl"
    _write(p, n=300)
    activity._activity_file = lambda business_id=None: Path(p)

    got = activity.recent(limit=500, entity_ids=["2", "5"])
    one = activity.recent(limit=500, entity_id="2")
    two = activity.recent(limit=500, entity_id="5")
    check("ids= returns exactly the union of the single-id reads",
          sorted(e["label"] for e in got)
          == sorted(e["label"] for e in one + two))
    check("  …still newest-first",
          [e["ts"] for e in got] == sorted((e["ts"] for e in got), reverse=True))
    check("blank ids are ignored, not matched as ''",
          activity.recent(limit=5, entity_ids=["", "  "]) == activity.recent(limit=5))
    check("id= and ids= combine",
          len(activity.recent(limit=500, entity_id="2", entity_ids=["5"]))
          == len(one) + len(two))


def main():
    print("matches the original implementation:")
    test_matches_the_original()
    print("awkward files:")
    test_awkward_files()
    print("chunk boundaries:")
    test_chunk_boundaries()
    print("reads only the tail:")
    test_reads_only_the_tail()
    print("max_scan bounds a filtered read:")
    test_max_scan_bounds_a_filtered_read()
    print("entity_ids union:")
    test_entity_ids_union()
    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}): {fails}")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
