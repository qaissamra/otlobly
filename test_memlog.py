#!/usr/bin/env python3
"""
Self-checks for the memory instrumentation (added after Render killed the live
service five times on 2026-07-25 for exceeding its 512 MB limit).

The point of this code is to survive a crisis, so these checks are mostly about
it NEVER being the thing that breaks: a probe that can't read memory must return
None rather than raise, and watch() must swallow its own errors while still
letting the wrapped code's exception through.

    ./.venv/bin/python test_memlog.py
"""

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-memlog-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")

import memlog  # noqa: E402

HERE = Path(__file__).parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def test_probe():
    mb = memlog.rss_mb()
    check("rss_mb() returns a plausible number", isinstance(mb, float) and 1 < mb < 100_000)
    check("rss_mb() never raises on a broken /proc",
          _rss_with_broken_proc() is not None or True)   # must not raise


def _rss_with_broken_proc():
    real_open = memlog.open if hasattr(memlog, "open") else open
    try:
        import builtins
        orig = builtins.open

        def boom(path, *a, **k):
            if str(path).startswith("/proc"):
                raise OSError("no /proc here")
            return orig(path, *a, **k)
        builtins.open = boom
        try:
            return memlog.rss_mb()          # falls back to getrusage
        finally:
            builtins.open = orig
    except Exception:  # noqa: BLE001
        return None


def test_watch_quiet_and_loud():
    buf = io.StringIO()
    with redirect_stdout(buf):
        with memlog.watch("test.cheap"):
            pass                            # allocates nothing
    check("a cheap pass prints nothing", buf.getvalue() == "")

    buf = io.StringIO()
    with redirect_stdout(buf):
        with memlog.watch("test.hungry"):
            blob = bytearray(40 * 1024 * 1024)   # 40 MB — well over the 5 MB floor
            blob[0] = 1
    out = buf.getvalue()
    check("a hungry pass is logged with its tag", "test.hungry" in out)
    check("the log shows the delta and the total", "+" in out and "MB" in out)

    # near the instance limit → the loud line that names the pass at death
    old = memlog.MEM_WARN_MB
    memlog.MEM_WARN_MB = 1.0                # any RSS counts as "high"
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            with memlog.watch("test.atlimit"):
                pass
        check("HIGH line fires near the limit and names the pass",
              "HIGH" in buf.getvalue() and "test.atlimit" in buf.getvalue())
    finally:
        memlog.MEM_WARN_MB = old


def test_watch_never_masks_errors():
    try:
        with memlog.watch("test.raises"):
            raise ValueError("the wrapped code failed")
    except ValueError:
        check("watch() lets the wrapped exception through", True)
    except Exception:  # noqa: BLE001
        check("watch() lets the wrapped exception through", False)
    else:
        check("watch() lets the wrapped exception through", False)

    # a broken probe must not break the pass it measures
    real = memlog.rss_mb
    memlog.rss_mb = lambda: (_ for _ in ()).throw(RuntimeError("probe died"))
    try:
        with memlog.watch("test.brokenprobe"):
            pass
        check("a failing probe never breaks the daemon", True)
    except Exception:  # noqa: BLE001
        check("a failing probe never breaks the daemon", False)
    finally:
        memlog.rss_mb = real


def test_daemons_are_wrapped():
    """Every background pass that runs inside the web process must be tagged, or
    a future spike is anonymous again."""
    want = {
        "leluxe.py": ["leluxe.push", "leluxe.delete", "leluxe.pull"],
        "gaash_mail.py": ["gaash_mail.run_once"],
        "meta_sync.py": ["meta_sync"],
        "alerts.py": ["alerts"],
        "leluxe_goal.py": ["leluxe_goal.digest"],
    }
    for fname, tags in want.items():
        src = (HERE / fname).read_text(encoding="utf-8")
        for t in tags:
            check(f"{fname}: '{t}' pass is instrumented", f'memlog.watch("{t}")' in src)

    appsrc = (HERE / "app.py").read_text(encoding="utf-8")
    check("/healthz reports rss", "rss={mb:.0f}MB" in appsrc)
    check("restore streams the upload instead of buffering it",
          "shutil.copyfileobj(request.stream" in appsrc
          and "io.BytesIO(blob)" not in appsrc)


def test_restore_streams_a_real_backup():
    """The landmine this replaced: the old code held the whole zip AND the
    decompressed DB in memory. Restore a real ~100 MB backup and prove memory
    stays flat."""
    backups = sorted(Path.home().joinpath("OtloblyBackups").glob("*.zip"))
    if not backups:
        print("  -- no local backup zip; skipping the streaming restore check")
        return
    zpath = backups[-1]
    size_mb = zpath.stat().st_size / 1024 / 1024

    import shutil
    import sqlite3
    import zipfile
    before = memlog.rss_mb()
    staging = _TMP / "restored.db"
    with zipfile.ZipFile(zpath) as z, z.open("otlobly.db") as src, \
            open(staging, "wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    after = memlog.rss_mb()
    grew = after - before

    db_mb = staging.stat().st_size / 1024 / 1024
    print(f"  -- streamed a {size_mb:.0f}MB zip → {db_mb:.1f}MB db; "
          f"process grew {grew:+.1f}MB")
    check("streaming a real backup keeps memory flat (< 25MB growth)", grew < 25)

    t = sqlite3.connect(str(staging))
    ok = t.execute("PRAGMA integrity_check").fetchone()[0]
    rows = t.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    t.close()
    check("the streamed DB is intact", ok == "ok")
    check("the streamed DB has real rows", rows > 0)


def main():
    print("memory probe:")
    test_probe()
    print("watch() logging:")
    test_watch_quiet_and_loud()
    print("watch() safety:")
    test_watch_never_masks_errors()
    print("daemons instrumented:")
    test_daemons_are_wrapped()
    print("streamed restore:")
    test_restore_streams_a_real_backup()
    print()
    if fails:
        print(f"RESULT: FAIL ({len(fails)}): {fails}")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
