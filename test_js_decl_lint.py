#!/usr/bin/env python3
"""
Static JS declaration lint for web/index.html.

2026-09-12 — the whole Purchases board had been silently unable to SAVE for two
days. 34f5920 changed `const PO_BOXES` to `let PO_BOXES` and appended a trailing
`//` comment to that line; the same line also ended with `const poSaveTimers={};`,
so the comment swallowed the declaration. poSave() then threw
`ReferenceError: poSaveTimers is not defined` on its very first statement, and
every write on the board died with it — Otlobly status, tracking numbers, due
dates, RD numbers, product edits. The file still parsed, so all 68 python suites
stayed green and nothing said a word.

Two guards, both cheap and both generic:

  1. no line may start a declaration AFTER a `//` comment on the same line
  2. every debounce-timer map a saver indexes into must actually be declared

    ./.venv/bin/python test_js_decl_lint.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI = HERE / "web" / "index.html"
fails = []


def check(name, cond, detail=""):
    print(f"  {'OK ' if cond else 'XX '} {name}{(' — ' + detail) if detail and not cond else ''}")
    if not cond:
        fails.append(name)


# a `//` that opens a line comment: not part of `http://`, not inside a regex or
# string we care about — good enough, and it costs one pass over the file
_COMMENT = re.compile(r'(?<![:/\\"\'])//')
# a real declarator, not the English words "let"/"class" in a sentence:
# the name must be followed by `=`, `;` or `,`
_DECL = re.compile(r'\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*[=;,]')


def swallowed_declarations(text):
    """Lines where a declaration sits AFTER a // comment — i.e. commented out by accident."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _COMMENT.search(line)
        if not m:
            continue
        if line.lstrip().startswith("//"):        # a whole-line comment is prose, fine
            continue
        d = _DECL.search(line, m.end())
        if d:
            out.append((n, d.group(0), line.strip()[:120]))
    return out


def strip_line_comments(text):
    """The file with // comments removed — so a declaration that only SURVIVES inside
    a comment does not count as a declaration."""
    out = []
    for line in text.splitlines():
        m = _COMMENT.search(line)
        out.append(line[:m.start()] if m else line)
    return "\n".join(out)


def undeclared_timer_maps(text):
    """Every NAME in `clearTimeout(NAME[…])` / `NAME[…]=setTimeout` must be declared
    in real CODE — this is the check that fires on its own if a declaration was
    commented out rather than deleted."""
    code = strip_line_comments(text)
    names = set(re.findall(r'clearTimeout\(\s*([A-Za-z_$][\w$]*)\s*\[', code))
    names |= set(re.findall(r'([A-Za-z_$][\w$]*)\s*\[[^\]\n]*\]\s*=\s*setTimeout', code))
    return sorted(n for n in names
                  if not re.search(r'\b(?:const|let|var)\s+' + re.escape(n) + r'\b', code))


def main():
    check("web/index.html exists", UI.exists())
    if not UI.exists():
        return 1
    text = UI.read_text(encoding="utf-8")

    bad = swallowed_declarations(text)
    check("no declaration hidden behind a // comment", not bad,
          "; ".join(f"L{n}: {d} in «{ln}»" for n, d, ln in bad[:3]))

    undecl = undeclared_timer_maps(text)
    check("every debounce-timer map is declared", not undecl, ", ".join(undecl))

    # the specific global the 2026-09-12 outage lost
    check("poSaveTimers is declared",
          bool(re.search(r'\b(?:const|let|var)\s+poSaveTimers\b', strip_line_comments(text))))

    print("――――――――――――――――――――――")
    print(f"{'FAIL' if fails else 'PASS'} · test_js_decl_lint · {len(fails)} problem(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
