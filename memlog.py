#!/usr/bin/env python3
"""
Memory probe for the background daemons — no dependencies, never fatal.

Render's Starter instance is 512 MB and kills the whole process when it's
exceeded (five automatic restarts on 2026-07-25), taking the evidence with it.
Every background pass runs inside `watch()`, so the log lines immediately before
a restart name the pass that was running and how much it had just allocated.

Quiet on purpose: a pass that costs nothing prints nothing. Only a real jump
(>= _DELTA_MB) or a dangerous total (>= MEM_WARN_MB) says anything, so the
service log stays readable.

    python3 memlog.py            # print this process's RSS
"""

import os
import sys

# ~75% of Render's 512 MB Starter instance — early enough to see the climb, high
# enough that a healthy app never trips it. Override with env MEM_WARN_MB.
MEM_WARN_MB = float(os.environ.get("MEM_WARN_MB") or 380)
_DELTA_MB = 5.0          # a pass has to move the needle this much to be logged


def rss_mb():
    """Resident memory of THIS process in MB, or None if it can't be read.
    Linux (Render) reads /proc; macOS falls back to getrusage. Never raises —
    a probe that fails must not take a daemon down with it."""
    try:
        with open("/proc/self/status") as f:            # Linux / Render
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0   # kB → MB
    except Exception:  # noqa: BLE001 — not Linux, or /proc unreadable
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is BYTES on macOS/BSD, kilobytes on Linux
        return peak / (1024.0 ** 2) if sys.platform == "darwin" else peak / 1024.0
    except Exception:  # noqa: BLE001
        return None


class watch:
    """Context manager around one background pass:

        with memlog.watch("leluxe.pull"):
            run_pull_pass()

    Prints nothing for a cheap pass; prints the delta when it allocates real
    memory, and a loud HIGH line once the process is near the instance limit.
    Swallows its own errors — instrumentation must never break the thing it
    measures — but never swallows the wrapped code's exception."""

    def __init__(self, tag):
        self.tag = tag
        self.before = None

    def __enter__(self):
        try:
            self.before = rss_mb()
        except Exception:  # noqa: BLE001 — a probe failure is never fatal
            self.before = None
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            after = rss_mb()
            if after is None:
                return False
            delta = after - self.before if self.before is not None else 0.0
            if after >= MEM_WARN_MB:
                print(f"[mem] HIGH {self.tag} {delta:+.1f}MB → {after:.1f}MB "
                      f"(limit ~512MB — this pass was running)", flush=True)
            elif abs(delta) >= _DELTA_MB:
                print(f"[mem] {self.tag} {delta:+.1f}MB → {after:.1f}MB", flush=True)
        except Exception:  # noqa: BLE001 — a probe failure is never fatal
            pass
        return False       # never suppress the wrapped code's exception


if __name__ == "__main__":
    mb = rss_mb()
    print(f"rss = {mb:.1f} MB" if mb is not None else "rss = unavailable")
