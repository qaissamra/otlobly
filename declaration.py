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
PURPOSE_DEFAULT = "personal daily use"


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

    def text(self, x, y, s, size=10.5, bold=False, font=None):
        b = _esc(_enc(s) or b"")
        f = font or (b"F2" if bold else b"F1")
        self.ops.append(
            b"BT /%s %g Tf %g %g Td (%s) Tj ET" % (f, size, x, PAGE_H - y, b))

    def line(self, x1, y1, x2, y2, w=0.6, grey=0.75):
        self.ops.append(b"q %g G %g w %g %g m %g %g l S Q"
                        % (grey, w, x1, PAGE_H - y1, x2, PAGE_H - y2))

    def ellipse(self, cx, cy, rx, ry, w=1.4):
        """The template circles the chosen import type by hand; this is that
        circle. A ring around a printed word is a choice being marked — it says
        nothing about who signed, which is the line we do not cross."""
        k = 0.5523
        y = PAGE_H - cy
        self.ops.append(
            b"q 0 G %g w %g %g m %g %g %g %g %g %g c %g %g %g %g %g %g c "
            b"%g %g %g %g %g %g c %g %g %g %g %g %g c S Q"
            % (w, cx - rx, y,
               cx - rx, y + ry * k, cx - rx * k, y + ry, cx, y + ry,
               cx + rx * k, y + ry, cx + rx, y + ry * k, cx + rx, y,
               cx + rx, y - ry * k, cx + rx * k, y - ry, cx, y - ry,
               cx - rx * k, y - ry, cx - rx, y - ry * k, cx - rx, y))

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
        b"/Resources<</Font<</F1 5 0 R/F2 6 0 R/F3 7 0 R>>>>/Contents 4 0 R>>"
        % (PAGE_W, PAGE_H),
        b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>",
        # F3 is the signature face. Times-Italic, because the base-14 PDF fonts
        # hold no script face and embedding one would mean shipping a TTF — and
        # a slanted TYPED name is an e-signature, which is what this is, rather
        # than an imitation of anybody's handwriting.
        b"<</Type/Font/Subtype/Type1/BaseFont/Times-Italic/Encoding/WinAnsiEncoding>>",
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
# Helvetica / Helvetica-Bold advance widths (units per 1000), ASCII 32..126.
# Guessing an average character width put the "circle the relevant answer" ring
# around "pe…com" instead of around "personal" — the real metrics are 95 numbers.
_W_REG = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
          278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
          584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
          500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
          667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
          278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
          278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
_W_BOLD = (278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333,
           278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333,
           584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278,
           556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
           667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
           333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556,
           333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584)


def _w(s, size, bold=False):
    """Helvetica advance width of `s` at `size`, in points."""
    tbl = _W_BOLD if bold else _W_REG
    total = 0
    for ch in str(s):
        i = ord(ch) - 32
        total += tbl[i] if 0 <= i < len(tbl) else 556
    return total * size / 1000.0


def build(*, gwd, name, id_number, contents, purpose=None, today=None):
    """(filename, bytes) for one package's DECLARATION OF USE — the same form
    the owner fills in by hand, with the blanks already filled. Raises
    ValueError naming any field this document cannot print."""
    purpose = (purpose or PURPOSE_DEFAULT).strip()
    day = (today or date.today()).strftime("%d/%m/%Y")
    # ~5 words is what identifies a product to customs; the rest of an Amazon
    # title is marketing. One item takes one line, several take a line each so
    # the officer can count them against the box.
    words = [str(c.get("title") or "").split() for c in (contents or [])]
    qtys = [c.get("qty") or 1 for c in (contents or [])]
    take = [5] * len(words)
    # 5 words, UNLESS that makes two products read alike: "Crave for Google
    # Pixel 6" twice tells an officer nothing and looks like a duplicated row,
    # so any colliding pair grows a word at a time until it is distinguishable.
    for _ in range(9):
        seen = {}
        for i, w in enumerate(words):
            seen.setdefault(" ".join(w[:take[i]]).lower(), []).append(i)
        clash = [ix for ix in seen.values() if len(ix) > 1]
        if not clash:
            break
        for group in clash:
            for i in group:
                if take[i] < len(words[i]):
                    take[i] += 1
    goods_lines = []
    for i, w in enumerate(words):
        short = " ".join(w[:take[i]])
        if short:
            goods_lines.append(f"{short} x{qtys[i]}" if qtys[i] > 1 else short)
    goods = " / ".join(goods_lines)

    for label, val in (("name on the parcel", name), ("ID number", id_number),
                       ("purpose of use", purpose), ("contents", goods)):
        bad = _unprintable(val)
        if bad:
            raise ValueError(
                f"the {label} contains characters this document cannot print "
                f"({' '.join(bad)}) — set a Latin spelling before generating it")
    if not str(name or "").strip():
        raise ValueError("no name on the parcel — set one before declaring it")

    p = _Page()
    L, R = MARGIN + 20, PAGE_W - MARGIN - 20
    ttl, tsz = "DECLARATION OF USE", 15
    tx = (PAGE_W - _w(ttl, tsz, True)) / 2
    p.text(tx, 130, ttl, tsz, bold=True)
    p.line(tx, 134, tx + _w(ttl, tsz, True), 134, w=1.1, grey=0)

    def filled(label, value, y, x=None, end=None, size=11):
        """`label ____value____` — the value centred on its rule, as if typed
        into the blank of the printed form."""
        x = L if x is None else x
        end = R if end is None else end
        p.text(x, y, label, size)
        x0 = x + _w(label, size) + 6
        p.line(x0, y + 4, end, y + 4, w=0.9, grey=0.15)
        v = str(value)
        p.text(max(x0 + 4, (x0 + end) / 2 - _w(v, size, True) / 2), y - 1,
               v, size, bold=True)
        return x0

    filled("Name:", str(name).strip(), 205, end=L + 250)
    filled("ID number:", str(id_number).strip() or "—", 205, x=L + 268)
    filled("I declare that the parcel number:", gwd, 275, end=R - 60)

    y = 345
    if not goods_lines:
        filled("contain", "(not itemised)", y)
        y += 70
    else:
        x0 = filled("contain", goods_lines[0], y)
        for extra in goods_lines[1:8]:          # a line each, stacked
            y += 26
            p.line(x0, y + 4, R, y + 4, w=0.9, grey=0.15)
            p.text(max(x0 + 4, (x0 + R) / 2 - _w(extra, 11, True) / 2), y - 1,
                   extra, 11, bold=True)
        if len(goods_lines) > 8:
            y += 26
            p.text(x0 + 4, y - 1, f"and {len(goods_lines) - 8} more item(s)", 10)
        y += 70
    filled("In purpose of", purpose, y)
    y += 70

    # "and it is personal / commercial import (circle the relevant answer)"
    pre, word, post = "and it is ", "personal", " / commercial import (circle the relevant answer)"
    p.text(L, y, pre + word + post, 11)
    wx = L + _w(pre, 11)
    p.ellipse(wx + _w(word, 11) / 2, y - 3.5, _w(word, 11) / 2 + 5, 11)

    y += 105                                    # was ~155 — the gap was dead space
    filled("", day, y, x=L + 30, end=L + 230)
    p.text(L + 108, y + 20, "DATE", 9.5)
    p.line(R - 250, y + 4, R, y + 4, w=0.9, grey=0.15)
    p.text(R - 148, y + 20, "SIGNATURE", 9.5)
    # The signature is the name, TYPED in a second face — an e-signature, not a
    # drawn mark. The line beneath keeps the page honest about who produced it:
    # the name and ID here are a customer's, and the reader must not be left to
    # assume they put pen to this page.
    sig = str(name).strip()
    p.text((R - 250 + R) / 2 - _w(sig, 17) * 0.46, y - 2, sig, 17, font=b"F3")
    p.text(R - 250, y + 32, "Submitted electronically by Otlobly on behalf of "
           + sig + " — no physical signature", 6.5)

    p.text(MARGIN, PAGE_H - 46,
           "Generated by Otlobly · one declaration per package · " + gwd, 7.5)
    return f"{gwd} - declaration.pdf", _pdf(p, f"Declaration of use {gwd}")


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
