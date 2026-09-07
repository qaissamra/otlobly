#!/usr/bin/env python3
"""
Design-system lint (UX restructure, brief §4 rule 9) — WARN level during Phases 1–6.

Runs docs/ux-restructure/tools/inventory.py --lint, which counts the things the
design system is meant to retire in web/index.html (emoji icons, raw <table>/<button>/
<select>/<input>, physical CSS properties, hex literals, native confirm/prompt/alert,
duplicate formatters, .az-modal roots) and compares them with lint-baseline.json.
A metric that got WORSE prints a WARN line; this suite still passes (warn level).
Phase 7 flips it to --strict (a regression fails the suite).

    ./.venv/bin/python test_ds_lint.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def main():
    tool = HERE / "docs" / "ux-restructure" / "tools" / "inventory.py"
    check("inventory tool exists", tool.exists())
    r = subprocess.run([sys.executable, str(tool), "--lint"], capture_output=True, text=True, cwd=str(HERE))
    print(r.stdout.rstrip())
    check("lint ran (exit 0 at warn level)", r.returncode == 0)
    check("baseline file exists", (HERE / "docs" / "ux-restructure" / "lint-baseline.json").exists())
    for f in ("tokens.css", "ds.css", "ds.js", "status.js", "format.js", "icons.svg"):
        check(f"static/ds/{f} present", (HERE / "static" / "ds" / f).exists())
    warns = [l for l in r.stdout.splitlines() if l.strip().startswith("WARN")]
    if warns:
        print(f"  !! {len(warns)} WARN line(s) — see above (warn level, not failing)")
    print("――――――――――――――――――――――")
    print("PASS" if not fails else f"FAIL: {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
