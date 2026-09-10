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
import struct
import zlib
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


def read_png(data):
    """(rgb_bytes, width, height, colours) from PNG bytes — pure stdlib.

    Why by hand: the server has no image library (see the module docstring),
    and a PDF wants exactly what a PNG already holds — 8-bit samples, deflate
    compressed. So the only real work is undoing PNG's per-row filters and
    dropping the alpha channel onto white, which is what a screenshot pasted
    from a browser carries. Returns None for anything this cannot read
    (16-bit, interlaced) rather than guessing at a picture of a customs
    document. A JPEG is handled by read_jpeg instead."""
    if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w = h = None
    bits = ct = interlace = 0
    idat, palette, trns = bytearray(), b"", b""
    i = 8
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, bits, ct, _cm, _fm, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            palette = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        i += 12 + ln
    if not w or not h or bits != 8 or interlace != 0 or not idat:
        return None
    chan = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ct)
    if not chan or (ct == 3 and not palette):
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = w * chan
    if len(raw) < (stride + 1) * h:
        return None
    out = bytearray(stride * h)
    prev = bytearray(stride)
    pos = 0
    for row in range(h):
        f = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        # PNG filters: each byte is stored as a difference from a neighbour
        if f == 1:                              # Sub
            for x in range(chan, stride):
                line[x] = (line[x] + line[x - chan]) & 0xFF
        elif f == 2:                            # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:                            # Average
            for x in range(stride):
                a = line[x - chan] if x >= chan else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:                            # Paeth
            for x in range(stride):
                a = line[x - chan] if x >= chan else 0
                b = prev[x]
                c = prev[x - chan] if x >= chan else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        elif f != 0:
            return None
        out[row * stride:(row + 1) * stride] = line
        prev = line
    # → what PDF takes: 8-bit gray or RGB, alpha composited onto white paper
    if ct == 0:
        return bytes(out), w, h, 1
    if ct == 2:
        return bytes(out), w, h, 3
    if ct == 3:
        rgb = bytearray(w * h * 3)
        for k, ix in enumerate(out):
            rgb[k * 3:k * 3 + 3] = palette[ix * 3:ix * 3 + 3] or b"\xff\xff\xff"
        return bytes(rgb), w, h, 3
    if ct == 4:                                 # gray + alpha
        px = bytearray(w * h)
        for k in range(w * h):
            g, a = out[k * 2], out[k * 2 + 1]
            px[k] = (g * a + 255 * (255 - a)) // 255
        return bytes(px), w, h, 1
    px = bytearray(w * h * 3)                   # ct == 6, RGBA
    for k in range(w * h):
        a = out[k * 4 + 3]
        if a == 255:
            px[k * 3:k * 3 + 3] = out[k * 4:k * 4 + 3]
        else:
            for ch in range(3):
                px[k * 3 + ch] = (out[k * 4 + ch] * a + 255 * (255 - a)) // 255
    return bytes(px), w, h, 3


def read_image(data):
    """(rgb, w, h, colours) for a pasted screenshot, or None. PNG today — the
    format every browser and every macOS screenshot pastes."""
    got = read_png(data)
    return got


def _pdf(page, title, images=()):
    """PDF document bytes with a proper xref table. `images` are extra pages,
    one picture each — the proof a customs officer asks for next: the Amazon
    order confirmation behind the declaration, in the SAME file, because
    GAASH's upload page takes one PDF per slot and a separate screenshot has
    nowhere to go. Each is (rgb_bytes, width, height, colours, caption)."""
    pages = [(page.stream(), None)]
    for k, img in enumerate(images):
        pages.append(_image_page(img, k))
    n_pages = len(pages)
    # object ids: 1 catalog, 2 pages, then per page (page, content), then
    # one XObject per image, then 3 fonts, then info
    first = 3
    page_ids = [first + 2 * i for i in range(n_pages)]
    img_ids = [first + 2 * n_pages + i for i in range(len(images))]
    font_ids = [first + 2 * n_pages + len(images) + i for i in range(3)]
    info_id = font_ids[-1] + 1
    objs = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[%s]/Count %d>>"
           % (b" ".join(b"%d 0 R" % i for i in page_ids), n_pages),
    }
    fonts = (b"<</F1 %d 0 R/F2 %d 0 R/F3 %d 0 R>>"
             % (font_ids[0], font_ids[1], font_ids[2]))
    for i, (content, img_ix) in enumerate(pages):
        xo = (b"/XObject<</Im0 %d 0 R>>" % img_ids[img_ix]) if img_ix is not None else b""
        objs[page_ids[i]] = (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 %d %d]"
            b"/Resources<</Font%s%s>>/Contents %d 0 R>>"
            % (PAGE_W, PAGE_H, fonts, xo, page_ids[i] + 1))
        objs[page_ids[i] + 1] = (b"<</Length %d>>stream\n%s\nendstream"
                                 % (len(content), content))
    for i, (rgb, w, h, colours, _cap) in enumerate(images):
        data = zlib.compress(rgb, 6)
        objs[img_ids[i]] = (
            b"<</Type/XObject/Subtype/Image/Width %d/Height %d/ColorSpace/%s"
            b"/BitsPerComponent 8/Filter/FlateDecode/Length %d>>stream\n%s\n"
            b"endstream" % (w, h, b"DeviceRGB" if colours == 3 else b"DeviceGray",
                            len(data), data))
    for i, face in enumerate((b"Helvetica", b"Helvetica-Bold",
                              # F3 is the signature face. Times-Italic, because
                              # the base-14 PDF fonts hold no script face and
                              # embedding one would mean shipping a TTF — and a
                              # slanted TYPED name is an e-signature, which is
                              # what this is, rather than an imitation of
                              # anybody's handwriting.
                              b"Times-Italic")):
        objs[font_ids[i]] = (b"<</Type/Font/Subtype/Type1/BaseFont/%s"
                             b"/Encoding/WinAnsiEncoding>>" % face)
    objs[info_id] = b"<</Title(%s)/Producer(Otlobly)>>" % _esc(_enc(title) or b"")

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for i in range(1, info_id + 1):
        offsets[i] = len(out)
        out += b"%d 0 obj\n" % i + objs[i] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (info_id + 1)
    for i in range(1, info_id + 1):
        out += b"%010d 00000 n \n" % offsets[i]
    out += (b"trailer\n<</Size %d/Root 1 0 R/Info %d 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (info_id + 1, info_id, xref))
    return bytes(out)


def _image_page(img, index):
    """(content stream, image index) for one annex page: the picture fitted
    inside the margins with its caption above it, never stretched — a squashed
    screenshot of an order is a screenshot an officer will squint at."""
    rgb, w, h, colours, caption = img
    p = _Page()
    top = 92
    if caption:
        p.text(MARGIN, top - 22, caption, 11, bold=True)
    box_w, box_h = PAGE_W - 2 * MARGIN, PAGE_H - top - MARGIN
    scale = min(box_w / float(w), box_h / float(h), 1.0)
    dw, dh = w * scale, h * scale
    x = (PAGE_W - dw) / 2
    y = PAGE_H - top - dh                       # PDF origin is bottom-left
    p.ops.append(b"q %g 0 0 %g %g %g cm /Im0 Do Q" % (dw, dh, x, y))
    return p.stream(), index


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


def _clean_title(title, qty):
    """A ClickUp mirror row is named "7 Anne Klein Women's Bracelet Watch Sold
    by: Amazon Export Sales LLC" — the count is already in the row's own
    quantity, and the seller is not the product. Left alone the line reads
    "7 Anne Klein Womens Bracelet x7", which on a customs paper looks like two
    different numbers for one box. A leading number is dropped ONLY when it is
    the quantity we are already printing."""
    t = re.sub(r"(?is)\s*[-–|,]?\s*sold by\s*:.*$", "", str(title or "")).strip()
    m = re.match(r"^(\d+)\s*[x×]?\s+(.+)$", t)
    if m and int(m.group(1)) == int(qty or 1):
        t = m.group(2).strip()
    return t


def goods_lines(contents):
    """["Anne Klein Womens Bracelet Watch x7", …] — the product lines both
    documents print. Shared, because the officer who reads the originality
    declaration next to the use declaration must see the SAME goods on both."""
    # ~5 words is what identifies a product to customs; the rest of an Amazon
    # title is marketing. One item takes one line, several take a line each so
    # the officer can count them against the box.
    qtys = [c.get("qty") or 1 for c in (contents or [])]
    words = [_clean_title(c.get("title"), q).split()
             for c, q in zip(contents or [], qtys)]
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
    return goods_lines


def _check(name, fields):
    """Refuse to print a field WinAnsi cannot carry, and refuse a nameless
    parcel — the two failures that put a wrong identity on a customs paper."""
    for label, val in fields:
        bad = _unprintable(val)
        if bad:
            raise ValueError(
                f"the {label} contains characters this document cannot print "
                f"({' '.join(bad)}) — set a Latin spelling before generating it")
    if not str(name or "").strip():
        raise ValueError("no name on the parcel — set one before declaring it")


def _filled(p, label, value, y, x=None, end=None, size=11):
    """`label ____value____` — the value centred on its rule, as if typed
    into the blank of the printed form."""
    L, R = MARGIN + 20, PAGE_W - MARGIN - 20
    x = L if x is None else x
    end = R if end is None else end
    p.text(x, y, label, size)
    x0 = x + _w(label, size) + 6
    p.line(x0, y + 4, end, y + 4, w=0.9, grey=0.15)
    v = str(value)
    p.text(max(x0 + 4, (x0 + end) / 2 - _w(v, size, True) / 2), y - 1,
           v, size, bold=True)
    return x0


def _sign_block(p, name, day, y):
    """DATE and SIGNATURE, the p.p. e-signature rule of this module: a TYPED
    name in a second face, never a drawn mark."""
    L, R = MARGIN + 20, PAGE_W - MARGIN - 20
    _filled(p, "", day, y, x=L + 30, end=L + 230)
    p.text(L + 108, y + 20, "DATE", 9.5)
    p.line(R - 250, y + 4, R, y + 4, w=0.9, grey=0.15)
    p.text(R - 148, y + 20, "SIGNATURE", 9.5)
    sig = str(name).strip()
    pp, ppw = "p.p. ", _w("p.p. ", 10)
    total = ppw + _w(sig, 17) * 0.92
    sx = (R - 250 + R) / 2 - total / 2
    p.text(sx, y - 2, pp, 10)
    p.text(sx + ppw, y - 2, sig, 17, font=b"F3")


def build(*, gwd, name, id_number, contents, purpose=None, today=None):
    """(filename, bytes) for one package's DECLARATION OF USE — the same form
    the owner fills in by hand, with the blanks already filled. Raises
    ValueError naming any field this document cannot print."""
    purpose = (purpose or PURPOSE_DEFAULT).strip()
    day = (today or date.today()).strftime("%d/%m/%Y")
    goods_ln = goods_lines(contents)
    goods = " / ".join(goods_ln)
    _check(name, (("name on the parcel", name), ("ID number", id_number),
                  ("purpose of use", purpose), ("contents", goods)))

    p = _Page()
    L, R = MARGIN + 20, PAGE_W - MARGIN - 20
    ttl, tsz = "DECLARATION OF USE", 15
    tx = (PAGE_W - _w(ttl, tsz, True)) / 2
    p.text(tx, 130, ttl, tsz, bold=True)
    p.line(tx, 134, tx + _w(ttl, tsz, True), 134, w=1.1, grey=0)

    _filled(p, "Name:", str(name).strip(), 205, end=L + 250)
    _filled(p, "ID number:", str(id_number).strip() or "—", 205, x=L + 268)
    _filled(p, "I declare that the parcel number:", gwd, 275, end=R - 60)

    y = 345
    if not goods_ln:
        _filled(p, "contain", "(not itemised)", y)
        y += 70
    else:
        x0 = _filled(p, "contain", goods_ln[0], y)
        for extra in goods_ln[1:8]:             # a line each, stacked
            y += 26
            p.line(x0, y + 4, R, y + 4, w=0.9, grey=0.15)
            p.text(max(x0 + 4, (x0 + R) / 2 - _w(extra, 11, True) / 2), y - 1,
                   extra, 11, bold=True)
        if len(goods_ln) > 8:
            y += 26
            p.text(x0 + 4, y - 1, f"and {len(goods_ln) - 8} more item(s)", 10)
        y += 70
    _filled(p, "In purpose of", purpose, y)
    y += 70

    # "and it is personal / commercial import (circle the relevant answer)"
    pre, word, post = "and it is ", "personal", " / commercial import (circle the relevant answer)"
    p.text(L, y, pre + word + post, 11)
    wx = L + _w(pre, 11)
    p.ellipse(wx + _w(word, 11) / 2, y - 3.5, _w(word, 11) / 2 + 5, 11)

    y += 105                                    # was ~155 — the gap was dead space
    # The signature is the name, TYPED in a second face — an e-signature, not a
    # drawn mark. "p.p." (per procurationem) is the standard notation for an
    # agent signing on someone's behalf: two characters, and the page stops
    # asserting that this customer put pen to it. The name and ID here are
    # theirs, not ours, so something has to say who actually produced this.
    _sign_block(p, name, day, y)
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


# --------------------------------------------------------------------------- #
# The second document: originality
# --------------------------------------------------------------------------- #
# GAASH, 08/09/2026, on GWD004791532: "לדרישת המכס יש לצרף הצהרת מקוריות" — the
# customs authority wants a declaration of ORIGINALITY, which is a different
# paper from the declaration of use above. Use says what the goods are FOR;
# originality says the branded goods are genuine, bought new from Amazon, and
# not counterfeit. Branded parcels (watches, bags, electronics) get asked for it.
#
# It says NOTHING about personal vs commercial import (owner, 08/09/2026:
# "remove that it's for personal import"). This paper answers one question —
# are the goods genuine — and 23 watches in one box is not a personal import;
# claiming it here would give customs a reason to disbelieve the part that
# matters. That question is answered on the use declaration, where it belongs.
ORIGINALITY_BODY = (
    "The goods listed above are new and original branded products, bought "
    "online from Amazon.com and its sellers and shipped from there. They are "
    "not counterfeit, imitation or replica goods; no trademark, label or "
    "serial marking on them has been altered or removed. The Amazon order "
    "confirmation is %s, and I take responsibility for this declaration.")
# The last clause tells the truth about THIS copy: a paper that says "available
# on request" while the order is stapled behind it reads as though nobody looked
# at it, and one that says "attached" with nothing attached is worse.
ORIGINALITY_PROOF = ("attached to this declaration", "available on request")


def _para(p, text, x, y, width, size=10.5, lead=15):
    """Justify-free paragraph wrapped on REAL Helvetica widths (the char-count
    wrap in _wrap overflows the margin on capital-heavy lines). Returns the y
    below the last line."""
    line, out = "", []
    for word in str(text or "").split():
        trial = (line + " " + word).strip()
        if _w(trial, size) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    for ln in out:
        p.text(x, y, ln, size)
        y += lead
    return y


MAX_ANNEX_PX = 3_000_000                        # ~3 MP: the unfilter loop above
# is pure Python, and a 12 MP phone photo would take the send call minutes.
# A pasted browser screenshot is well under this; a camera photo is not.


def build_originality(*, gwd, name, id_number, contents, order_code=None,
                      today=None, annex=()):
    """(filename, bytes) for one package's DECLARATION OF ORIGINALITY. Same
    identity rules as build(): everything is READ from the boards, nothing is
    signed by hand, and an unprintable field is a hard error.

    `annex` is [(image_bytes, caption)] — the proof pages that follow the
    declaration, normally the Amazon order confirmation. They go in the SAME
    PDF because GAASH's upload page takes one file per slot: a screenshot sent
    beside the declaration has nowhere to land."""
    day = (today or date.today()).strftime("%d/%m/%Y")
    order = str(order_code or "").strip()
    goods_ln = goods_lines(contents)
    goods = " / ".join(goods_ln)
    _check(name, (("name on the parcel", name), ("ID number", id_number),
                  ("Amazon order number", order), ("contents", goods)))

    p = _Page()
    L, R = MARGIN + 20, PAGE_W - MARGIN - 20
    ttl, tsz = "DECLARATION OF ORIGINALITY", 15
    tx = (PAGE_W - _w(ttl, tsz, True)) / 2
    p.text(tx, 120, ttl, tsz, bold=True)
    p.line(tx, 124, tx + _w(ttl, tsz, True), 124, w=1.1, grey=0)

    _filled(p, "Name:", str(name).strip(), 190, end=L + 250)
    _filled(p, "ID number:", str(id_number).strip() or "—", 190, x=L + 268)
    _filled(p, "I declare that the parcel number:", gwd, 250, end=R - 60)
    # The order number is what an officer can actually check against Amazon —
    # printed only when we know it, never as an empty rule pretending to a fact.
    y = 305
    if order:
        _filled(p, "bought from Amazon.com under order number:", order, y)
        y += 55
    if not goods_ln:
        _filled(p, "containing", "(not itemised)", y)
        y += 55
    else:
        x0 = _filled(p, "containing", goods_ln[0], y)
        for extra in goods_ln[1:8]:
            y += 26
            p.line(x0, y + 4, R, y + 4, w=0.9, grey=0.15)
            p.text(max(x0 + 4, (x0 + R) / 2 - _w(extra, 11, True) / 2), y - 1,
                   extra, 11, bold=True)
        if len(goods_ln) > 8:
            y += 26
            p.text(x0 + 4, y - 1, f"and {len(goods_ln) - 8} more item(s)", 10)
        y += 55
    pics = []
    for raw, caption in (annex or ()):
        got = read_image(raw)
        # an unreadable or enormous picture is skipped, never fatal: the
        # declaration itself is the document, the proof page is the bonus
        if got and got[1] * got[2] <= MAX_ANNEX_PX:
            rgb, w, h, colours = got
            pics.append((rgb, w, h, colours, caption or ""))
    body = ORIGINALITY_BODY % ORIGINALITY_PROOF[0 if pics else 1]
    y = _para(p, body, L, y, R - L) + 60
    _sign_block(p, name, day, min(y, PAGE_H - 120))
    if pics:
        p.text(L, PAGE_H - 70, "Attached: the Amazon order this parcel was "
               "bought on" + (f", {len(pics)} pages." if len(pics) > 1 else "."),
               9.5)
    return (f"{gwd} - originality.pdf",
            _pdf(p, f"Declaration of originality {gwd}", pics))
