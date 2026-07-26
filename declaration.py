#!/usr/bin/env python3
"""
Customs declaration PDFs, generated per package — pure stdlib.

Why hand-rolled: the server has no PDF or image library (Flask only, see
requirements.txt), and adding one to install on Render for a one-page form of
plain text is a poor trade. A PDF is a text format; a single page of Helvetica
is a few hundred bytes of operators.

Whose document this is: it is signed by NOBODY. The declarant block states,
in typed text, that Otlobly submitted it electronically on the named person's
behalf. It never draws a mark that could pass for someone's handwriting — the
name and ID on this page belong to a customer, and inventing their signature on
a customs document is not ours to do.

Encoding: Helvetica with the standard WinAnsi encoding cannot render Arabic. A
field it cannot represent is a HARD ERROR, never a row of "?" — a mangled name
on a customs document is exactly the failure the parcel-name rules exist to
prevent. The caller surfaces the message and the owner sets a Latin spelling.
"""

import re
from datetime import date

PAGE_W, PAGE_H = 595, 842          # A4 in points
MARGIN = 56
PURPOSE_DEFAULT = "Personal use — not for resale"


# --------------------------------------------------------------------------- #
# PDF primitives
# --------------------------------------------------------------------------- #
def _enc(s):
    """Text → WinAnsi bytes, or None if this string cannot be printed."""
    try:
        return str(s or "").encode("cp1252")
    except UnicodeEncodeError:
        return None


def _esc(b):
    """Escape a PDF literal string's bytes."""
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _unprintable(s):
    """The characters of `s` that WinAnsi cannot carry (for the error message)."""
    bad = []
    for ch in str(s or ""):
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            if ch not in bad:
                bad.append(ch)
    return bad


class _Page:
    """Accumulates content-stream operators, top-down in page coordinates."""

    def __init__(self):
        self.ops = []

    def text(self, x, y, s, size=10.5, bold=False):
        b = _esc(_enc(s) or b"")
        self.ops.append(
            b"BT /%s %g Tf %g %g Td (%s) Tj ET"
            % (b"F2" if bold else b"F1", size, x, PAGE_H - y, b))

    def line(self, x1, y1, x2, y2, w=0.6, grey=0.75):
        self.ops.append(b"q %g G %g w %g %g m %g %g l S Q"
                        % (grey, w, x1, PAGE_H - y1, x2, PAGE_H - y2))

    def box(self, x, y, w, h, grey=0.92):
        self.ops.append(b"q %g g %g %g %g %g re f Q"
                        % (grey, x, PAGE_H - y - h, w, h))

    def stream(self):
        return b"\n".join(self.ops)


def _pdf(page, title):
    """One-page PDF document bytes with a proper xref table."""
    content = page.stream()
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 %d %d]"
        b"/Resources<</Font<</F1 5 0 R/F2 6 0 R>>>>/Contents 4 0 R>>"
        % (PAGE_W, PAGE_H),
        b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>",
        b"<</Title(%s)/Producer(Otlobly)>>" % _esc(_enc(title) or b""),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<</Size %d/Root 1 0 R/Info %d 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, len(objs), xref))
    return bytes(out)


def _wrap(s, width):
    """Greedy wrap on words; Helvetica averages ~0.5em so `width` is in chars."""
    words, lines, cur = str(s or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w if len(w) <= width else w[:width - 1] + "-"
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
def build(*, gwd, name, id_number, contents, purpose=None, today=None):
    """(filename, bytes) for one package's declaration, or raise ValueError with
    a message naming the field that cannot be printed."""
    purpose = (purpose or PURPOSE_DEFAULT).strip()
    day = (today or date.today()).strftime("%d %b %Y")
    lines = [f"{c['title']}  x{c.get('qty') or 1}" for c in (contents or [])]
    if not lines:
        lines = ["(contents not itemised)"]

    for label, val in (("name on the parcel", name), ("ID number", id_number),
                       ("purpose of use", purpose), ("contents", " ".join(lines))):
        bad = _unprintable(val)
        if bad:
            raise ValueError(
                f"the {label} contains characters this document cannot print "
                f"({' '.join(bad)}) — set a Latin spelling before generating it")
    if not str(name or "").strip():
        raise ValueError("no name on the parcel — set one before declaring it")

    p = _Page()
    x, y = MARGIN, 74
    p.text(x, y, "CUSTOMS DECLARATION", 19, bold=True)
    p.text(PAGE_W - MARGIN - 96, y, day, 10.5)
    y += 10
    p.line(x, y, PAGE_W - MARGIN, y, w=1.1, grey=0.2)
    y += 30

    def field(label, values, gap=25):
        nonlocal y
        p.text(x, y, label.upper(), 8, bold=True)
        for i, v in enumerate(values):
            p.text(x + 150, y + i * 15, v, 11.5, bold=(i == 0 and len(values) == 1))
        y += max(gap, 15 * len(values) + 10)

    field("Tracking number", [gwd])
    field("Name", [str(name).strip()])
    field("ID number", [str(id_number).strip() or "— not on file —"])
    field("Description of goods", _sum_lines(lines))
    field("Purpose of use", _wrap(purpose, 52))

    y += 6
    p.line(x, y, PAGE_W - MARGIN, y)
    y += 26
    p.box(x, y - 14, PAGE_W - 2 * MARGIN, 76, grey=0.96)
    p.text(x + 12, y, "DECLARED BY", 8, bold=True)
    y += 18
    p.text(x + 12, y, f"Otlobly, on behalf of {str(name).strip()}", 11.5, bold=True)
    y += 16
    p.text(x + 12, y, f"Submitted electronically  ·  {day}", 10)
    y += 15
    # said plainly, because a reader must never take this for a signed page
    p.text(x + 12, y, "No physical signature — submitted by the shipping agent.", 9.5)

    p.text(x, PAGE_H - 46,
           "Generated by Otlobly · one declaration per package · " + gwd, 8)
    return f"{gwd} - declaration.pdf", _pdf(p, f"Customs declaration {gwd}")


def _sum_lines(lines, cap=8):
    """At most `cap` product lines, then a count — a declaration is a summary,
    not a packing list, and an unbounded list would run off the page."""
    out = [ln for ln in lines[:cap]]
    wrapped = []
    for ln in out:
        wrapped += _wrap("• " + ln, 46)
    if len(lines) > cap:
        wrapped.append(f"• and {len(lines) - cap} more item(s)")
    return wrapped[:14]


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9 ._-]+", "_", str(s or "")).strip() or "declaration"
