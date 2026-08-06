#!/bin/bash
# Run EVERY test suite (test_*.py) and print a per-suite PASS/FAIL table.
#
# The suites are script-style (main() + check(), plain python) — NOT pytest;
# 33/37 would collect zero cases under pytest, so never run them that way.
#
#   bash run_all_tests.sh              # all suites
#   bash run_all_tests.sh leluxe gaash # only suites whose name matches a term
#
# Exit code: 0 = everything passed, 1 = at least one suite failed.
# Worktrees have no venv of their own — fall back to the main checkout's.

set -u
if [ -x ./.venv/bin/python ]; then PY=./.venv/bin/python
elif [ -x "$HOME/projects/otlobly-orders/.venv/bin/python" ]; then PY="$HOME/projects/otlobly-orders/.venv/bin/python"
else PY=python3; fi

pass=0; fail=0; failed=()
for t in test_*.py; do
  if [ "$#" -gt 0 ]; then
    hit=""
    for term in "$@"; do case "$t" in *"$term"*) hit=1;; esac; done
    [ -z "$hit" ] && continue
  fi
  start=$(date +%s)
  out=$("$PY" "$t" 2>&1); rc=$?
  secs=$(( $(date +%s) - start ))
  if [ $rc -eq 0 ]; then
    printf "PASS  %-38s %3ss\n" "$t" "$secs"; pass=$((pass+1))
  else
    printf "FAIL  %-38s %3ss\n" "$t" "$secs"; fail=$((fail+1)); failed+=("$t")
    echo "$out" | tail -15 | sed 's/^/      | /'
  fi
done
echo "――――――――――――――――――――――――――――――――――――――――――――"
echo "$pass passed · $fail failed"
if [ $fail -gt 0 ]; then printf 'failed: %s\n' "${failed[*]}"; exit 1; fi
