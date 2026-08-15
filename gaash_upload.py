#!/usr/bin/env python3
"""
🪪 Upload documents to GAASH — the real thing, from our own server.

Until now the app could only *link* the owner to GAASH's upload page; every
document was carried there by hand. Their ops page turns out to be plain
ASP.NET MVC with no session and no login:

    GET  https://ops.gaashwd.com/fileUpload?packageId=<GWD>&type=8[&type=7…]
         → the form, carrying a hidden __RequestVerificationToken AND the
           matching __RequestVerificationToken cookie, plus one slot per
           `type` — each with GAASH's OWN Hebrew label (צילום דרכון …).
    POST https://ops.gaashwd.com/FileUpload   (multipart/form-data)
         __RequestVerificationToken, PackageId,
         FileTypes[i] / FileTypesDescription[i] per slot,
         UploadedFiles repeated once per slot, IN SLOT ORDER.

Two hard limits their form declares: `accept="application/pdf"` and
`max=2097152` — PDF only, 2 MB each. The library holds JPGs and PNGs and there
is no Pillow here (stdlib only), so `to_pdf` WRAPS an image rather than
re-encoding it: a JPEG rides as /DCTDecode, a PNG's IDAT rides as /FlateDecode
with /Predictor 15 — PNG's own row filtering IS PDF predictor 15, so nothing is
recompressed and nothing is lost.

`dry_run` is the default everywhere: it resolves the token, converts the files
and returns exactly what WOULD be sent, without POSTing.

    ./.venv/bin/python gaash_upload.py GWD004721753        # inspect the slots
"""

import html
import os
import re
import struct
import zlib
from urllib import error as urlerror
from urllib import request as urlrequest

OPS_BASE = "https://ops.gaashwd.com"
UPLOAD_PAGE = OPS_BASE + "/fileUpload"
UPLOAD_POST = OPS_BASE + "/FileUpload"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
MAX_BYTES = 2 * 1024 * 1024          # their form's own `max` attribute
TOKEN_FIELD = "__RequestVerificationToken"

# The slots their page offers, decoded from it (the same table the board's
# manual link-builder uses). GAASH names them in Hebrew; these are ours.
DOC_TYPES = {
    1: "فاتورة · Invoice",
    2: "توكيل لمعهد المواصفات · Standards POA",
    3: "إقرار زبون للمواصفات · Standards declaration",
    4: "إضافة زبون للمواصفات · Standards add-customer",
    5: "موافقة وزارة الزراعة · Agriculture approval",
    6: "هوية · ID card",
    7: "إقرار استخدام البضاعة · Goods-use declaration",
    8: "جواز سفر · Passport",
    9: "مواصفات فنية · Technical spec",
    10: "موافقة مستحضرات تجميل · Cosmetics approval",
}


class UploadError(Exception):
    """Anything the owner should read verbatim in the wizard."""


# --------------------------------------------------------------------------- #
# Their page: token + the slots it opened
# --------------------------------------------------------------------------- #
def page_url(gwd, types):
    q = "".join(f"&type={int(t)}" for t in types)
    return f"{UPLOAD_PAGE}?packageId={gwd}{q}"


def _get(url, timeout=25):
    req = urlrequest.Request(url, headers={"User-Agent": UA})
    with urlrequest.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.headers.get_all("Set-Cookie") or []


def page_info(gwd, types, timeout=25):
    """{token, cookie, slots:[{i,type,label}], url} for a parcel + wanted slots.

    The token is one-shot and paired with the cookie — both come from THIS
    fetch and must travel together on the POST."""
    gwd = (gwd or "").strip().upper()
    if not re.match(r"GWD\d+$", gwd):
        raise UploadError("not a GWD tracking number")
    types = [int(t) for t in (types or []) if str(t).strip()]
    if not types:
        raise UploadError("pick at least one document type")
    url = page_url(gwd, types)
    try:
        body, cookies = _get(url, timeout)
    except urlerror.HTTPError as e:
        raise UploadError(f"GAASH's upload page answered {e.code}") from e
    except Exception as e:  # noqa - offline / DNS / timeout
        raise UploadError(f"couldn't reach GAASH's upload page ({e})") from e

    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', body)
    if not m:
        raise UploadError("GAASH's page didn't hand out an upload token — "
                          "open it in the browser and check the parcel number")
    token = html.unescape(m.group(1))
    cookie = ""
    for c in cookies:
        cm = re.match(r"\s*(__RequestVerificationToken=[^;]+)", c)
        if cm:
            cookie = cm.group(1)
            break

    # read the slots back from THEIR html — the labels are the real proof the
    # right document slot was opened, so the review screen shows their wording
    got_types = {int(i): int(v) for i, v in
                 re.findall(r'name="FileTypes\[(\d+)\]"[^>]*value="(\d+)"', body)}
    labels = {int(i): html.unescape(v) for i, v in
              re.findall(r'name="FileTypesDescription\[(\d+)\]"[^>]*value="([^"]*)"', body)}
    slots = [{"i": i, "type": got_types[i],
              "label": labels.get(i, ""), "our_label": DOC_TYPES.get(got_types[i], "")}
             for i in sorted(got_types)]
    if not slots:
        raise UploadError("GAASH's page opened no upload slots for those types")
    return {"url": url, "token": token, "cookie": cookie, "slots": slots}


# --------------------------------------------------------------------------- #
# PDF wrapping (their form takes application/pdf only)
# --------------------------------------------------------------------------- #
def _pdf(objs):
    """Assemble numbered PDF objects into a document (obj 1 = catalog)."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref}\n%%EOF\n").encode()
    return bytes(out)


def _image_pdf(img_obj, w, h):
    """One image, one page, at A4-ish scale keeping the aspect ratio."""
    pw, ph = 595.0, 842.0
    scale = min(pw / w, ph / h)
    dw, dh = w * scale, h * scale
    dx, dy = (pw - dw) / 2, (ph - dh) / 2
    content = f"q {dw:.2f} 0 0 {dh:.2f} {dx:.2f} {dy:.2f} cm /Im0 Do Q".encode()
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.0f} {ph:.0f}] "
         f"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content
        + b"\nendstream",
        img_obj,
    ])


def _jpeg_size(data):
    """(w, h, components) from a JPEG's SOF marker."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker, seg = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h, data[i + 9]
        i += 2 + seg
    raise UploadError("that JPEG is unreadable — try re-saving it")


def _png_parts(data):
    """(w, h, bitdepth, colortype, idat_bytes) — chunks concatenated."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise UploadError("not a PNG")
    w = h = bd = ct = None
    idat = bytearray()
    i = 8
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = (*struct.unpack(">II", body[:8]), body[8], body[9])
            if body[12] != 0:
                raise UploadError("interlaced PNGs aren't supported — re-save it")
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        i += 12 + ln
    if w is None or not idat:
        raise UploadError("that PNG is unreadable — try re-saving it")
    return w, h, bd, ct, bytes(idat)


def _inflate(idat, need, what="PNG"):
    """Decompress a PNG's IDAT and INSIST it yields a whole image.

    Real files in the document library are not always well-formed (one 1×1 PNG
    on disk declares an IDAT one byte shorter than its own zlib stream), and a
    half-decoded scan must never be wrapped up and posted to customs looking
    fine. Salvage what a lenient decoder can, then refuse anything short."""
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        try:                                   # tolerate a truncated tail
            raw = zlib.decompressobj().decompress(idat)
        except zlib.error:
            raw = b""
    if len(raw) < need:
        raise UploadError(f"that {what} looks corrupt (only "
                          f"{len(raw)}/{need} bytes decoded) — re-save it and try again")
    return raw


def _png_drop_alpha(w, h, bd, ct, idat):
    """Un-filter an RGBA/gray+alpha PNG and re-deflate it without the alpha
    channel — PDF images carry alpha separately and GAASH doesn't need it."""
    if bd != 8:
        raise UploadError("only 8-bit PNGs are supported — re-save it as JPG")
    src_ch = 4 if ct == 6 else 2
    keep = 3 if ct == 6 else 1
    raw = _inflate(idat, h * (1 + w * src_ch))
    stride = w * src_ch
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        f = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for x in range(stride):          # undo PNG's per-row filter
            a = line[x - src_ch] if x >= src_ch else 0
            b = prev[x]
            c = prev[x - src_ch] if x >= src_ch else 0
            if f == 1:
                line[x] = (line[x] + a) & 0xFF
            elif f == 2:
                line[x] = (line[x] + b) & 0xFF
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        prev = line
        for x in range(0, stride, src_ch):     # keep colour, drop alpha
            out += line[x:x + keep]
    return zlib.compress(bytes(out), 6), keep


def to_pdf(filename, data):
    """(pdf_bytes, note) for a document GAASH will accept. PDFs pass through
    untouched; JPEG/PNG are WRAPPED, never re-encoded."""
    if not data:
        raise UploadError("that file is empty")
    name = (filename or "").lower()
    if data[:5] == b"%PDF-":
        return data, ""
    if data[:3] == b"\xff\xd8\xff":
        w, h, comps = _jpeg_size(data)
        cs = "/DeviceRGB" if comps == 3 else ("/DeviceGray" if comps == 1 else "/DeviceCMYK")
        obj = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
               f"/ColorSpace {cs} /BitsPerComponent 8 /Filter /DCTDecode "
               f"/Length {len(data)} >>\nstream\n").encode() + data + b"\nendstream"
        return _image_pdf(obj, w, h), "JPG wrapped as PDF"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h, bd, ct, idat = _png_parts(data)
        if ct in (0, 2):                  # gray / RGB — PNG filtering == predictor 15
            ch = 3 if ct == 2 else 1
            # riding the IDAT verbatim is only safe once we know it really
            # holds the whole image (see _inflate) — a short stream would
            # produce a PDF that opens to a half-drawn document
            _inflate(idat, h * (1 + w * ch * (bd // 8 or 1)))
            stream, note = idat, "PNG wrapped as PDF"
        elif ct in (4, 6):                # has alpha — decode, drop it, re-deflate
            stream, ch = _png_drop_alpha(w, h, bd, ct, idat)
            note = "PNG (transparency flattened) wrapped as PDF"
            bd = 8
        else:
            raise UploadError("palette PNGs aren't supported — re-save as JPG")
        cs = "/DeviceRGB" if ch == 3 else "/DeviceGray"
        parms = (f"/DecodeParms << /Predictor 15 /Colors {ch} "
                 f"/BitsPerComponent {bd} /Columns {w} >> " if stream is idat else "")
        obj = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
               f"/ColorSpace {cs} /BitsPerComponent {bd} /Filter /FlateDecode "
               f"{parms}/Length {len(stream)} >>\nstream\n").encode() + stream + b"\nendstream"
        return _image_pdf(obj, w, h), note
    raise UploadError(f"{filename or 'that file'} isn't a PDF, JPG or PNG — "
                      "GAASH only takes those")


# --------------------------------------------------------------------------- #
# The upload itself
# --------------------------------------------------------------------------- #
def _multipart(fields, files, boundary):
    """fields = [(name, value)], files = [(name, filename, bytes)] — order kept."""
    out = bytearray()
    for name, value in fields:
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n").encode("utf-8")
    for name, fn, blob in files:
        safe = str(fn).replace('"', "").replace("\r", "").replace("\n", "")
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                f"Content-Type: application/pdf\r\n\r\n").encode("utf-8")
        out += blob + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out)


def build_request(gwd, docs, info):
    """The exact body that would be POSTed. docs = [{type, filename, data}] in
    the SAME order as info['slots'] — their form pairs the Nth file with
    FileTypes[N], so a wrong order files the passport as a declaration."""
    by_type = {}
    for d in docs:
        by_type.setdefault(int(d["type"]), []).append(d)
    fields = [(TOKEN_FIELD, info["token"])]
    files = []
    used = []
    for slot in info["slots"]:
        queue = by_type.get(slot["type"]) or []
        if not queue:
            raise UploadError(f"no document picked for {DOC_TYPES.get(slot['type'], slot['type'])}")
        d = queue.pop(0)
        pdf, note = to_pdf(d.get("filename"), d.get("data"))
        if len(pdf) > MAX_BYTES:
            raise UploadError(
                f"{d.get('filename') or 'that file'} is {len(pdf) // 1024} KB — "
                f"GAASH's limit is {MAX_BYTES // 1024} KB. Use a smaller scan.")
        fields += [(f"FileTypes[{slot['i']}]", str(slot["type"])),
                   (f"FileTypesDescription[{slot['i']}]", slot["label"])]
        fn = os.path.basename(str(d.get("filename") or f"{gwd}-{slot['type']}.pdf"))
        if not fn.lower().endswith(".pdf"):
            fn = fn.rsplit(".", 1)[0] + ".pdf"
        files.append(("UploadedFiles", fn, pdf))
        used.append({"slot": slot["i"], "type": slot["type"],
                     "gaash_label": slot["label"], "label": DOC_TYPES.get(slot["type"], ""),
                     "filename": fn, "bytes": len(pdf), "note": note,
                     "source": d.get("source", ""), "source_id": d.get("source_id", "")})
    fields.append(("PackageId", gwd))
    boundary = "----otlobly" + os.urandom(8).hex()
    return {"body": _multipart(fields, files, boundary), "boundary": boundary,
            "docs": used}


_OK_WORDS = ("הקבצים הועלו", "הועלו בהצלחה", "success", "תודה")
_BAD_WORDS = ("שגיאה", "error", "יש לבחור קובץ", "לא נמצא")


def _verdict(status, body):
    """Their page answers in Hebrew HTML, not JSON. Treat a 2xx as sent unless
    it shouts an error back — and say plainly when we can't tell."""
    low = (body or "").lower()
    if any(w.lower() in low for w in _BAD_WORDS):
        return False, "GAASH's page reported an error"
    if status in (200, 302):
        if any(w.lower() in low for w in _OK_WORDS):
            return True, "GAASH confirmed the upload"
        return True, "sent — GAASH returned no explicit confirmation"
    return False, f"GAASH answered {status}"


def upload(gwd, docs, dry_run=True, timeout=90, opener=None):
    """Send `docs` to GAASH. dry_run (the default) does everything EXCEPT the
    POST, so the wizard's review step is honest about what would go."""
    gwd = (gwd or "").strip().upper()
    types = [int(d["type"]) for d in docs or []]
    if not types:
        raise UploadError("nothing to upload")
    info = page_info(gwd, types, timeout=timeout)
    req = build_request(gwd, docs, info)
    out = {"ok": True, "gwd": gwd, "dry_run": bool(dry_run), "url": UPLOAD_POST,
           "docs": req["docs"], "bytes": len(req["body"]),
           "slots": info["slots"]}
    if dry_run:
        out["note"] = "dry-run — nothing was sent to GAASH"
        return out
    r = urlrequest.Request(UPLOAD_POST, data=req["body"], method="POST", headers={
        "User-Agent": UA, "Referer": info["url"],
        "Content-Type": f"multipart/form-data; boundary={req['boundary']}",
        **({"Cookie": info["cookie"]} if info["cookie"] else {})})
    try:
        with (opener or urlrequest.urlopen)(r, timeout=timeout) as resp:
            status, body = resp.status, resp.read().decode("utf-8", "replace")
    except urlerror.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")[:2000]
    except Exception as e:  # noqa
        raise UploadError(f"the upload didn't reach GAASH ({e})") from e
    ok, why = _verdict(status, body)
    out.update({"ok": ok, "status": status, "note": why})
    return out


if __name__ == "__main__":
    import sys
    g = (sys.argv[1] if len(sys.argv) > 1 else "").strip().upper()
    ts = [int(t) for t in sys.argv[2:]] or [8]
    if not g:
        print(__doc__)
        raise SystemExit(2)
    i = page_info(g, ts)
    print(f"{i['url']}\ntoken {i['token'][:24]}…  cookie {'yes' if i['cookie'] else 'NO'}")
    for s in i["slots"]:
        print(f"  slot {s['i']}  type {s['type']:>2}  {s['label']}   ({s['our_label']})")
