#!/usr/bin/env python3
"""
Self-checks: 🪪 uploading documents to GAASH (gaash_upload.py).

NO network. The page fixture below is a trimmed copy of GAASH's REAL
fileUpload response (captured 2026-08-14 for GWD004721753 with type=8&type=7),
so the parser is pinned against their actual markup — including the Hebrew slot
labels, which are what the review screen shows to prove the right slot was
opened.

The one rule worth guarding above all: dry-run must not POST.

    ./.venv/bin/python test_gaash_upload.py
"""

import binascii
import struct
import zlib

import gaash_upload as gu

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


# ── the real page, trimmed to the parts we parse ──────────────────────────────
PAGE = '''<!DOCTYPE html><html><body>
<form action="/FileUpload" enctype="multipart/form-data" id="import-form" method="post">
<input name="__RequestVerificationToken" type="hidden" value="TOKEN-abc_123-XYZ" />
<input class="text-box single-line" id="FileTypes_0_" name="FileTypes[0]" type="number" value="8" />
<input class="text-box single-line" id="FileTypesDescription_0_" name="FileTypesDescription[0]" type="text" value="צילום דרכון" />
<input class="text-box single-line" id="FileTypes_1_" name="FileTypes[1]" type="number" value="7" />
<input class="text-box single-line" id="FileTypesDescription_1_" name="FileTypesDescription[1]" type="text" value="הצהרת שימוש בטובין" />
<input class="form-control" id="PackageId" name="PackageId" readonly="readonly" type="text" value="GWD004721753" />
<input accept="application/pdf" class="input-file" id="UploadedFiles" max="2097152" name="UploadedFiles" type="file" />
<input type="submit" id="form-submit" value="&#215;&#169;&#215;&#156;&#215;&#151;" />
</form></body></html>'''


def _png(ct=2, w=2, h=2):
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    raw = b"".join(b"\x00" + bytes([9] * (w * ch)) for _ in range(h))

    def chunk(t, b):
        return (struct.pack(">I", len(b)) + t + b
                + struct.pack(">I", binascii.crc32(t + b) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _jpeg(w=8, h=6):
    """Smallest thing our SOF reader must cope with: APP0 then SOF0."""
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
            + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", h, w) + b"\x03" + b"\x00" * 9
            + b"\xff\xd9")


def main():
    print("— their page: token, cookie, slots —")
    calls = []

    def fake_get(url, timeout=25):
        calls.append(url)
        return PAGE, ["__RequestVerificationToken=COOKIE-999; path=/; HttpOnly"]

    gu._get = fake_get
    info = gu.page_info("GWD004721753", [8, 7])
    check("token parsed off their hidden field", info["token"] == "TOKEN-abc_123-XYZ")
    check("the paired cookie is captured (the POST fails without it)",
          info["cookie"] == "__RequestVerificationToken=COOKIE-999")
    check("both slots read back, in their order",
          [s["type"] for s in info["slots"]] == [8, 7])
    check("GAASH's own Hebrew labels survive, entity-decoded",
          info["slots"][0]["label"] == "צילום דרכון"
          and info["slots"][1]["label"] == "הצהרת שימוש בטובין")
    check("our bilingual label rides alongside",
          "Passport" in info["slots"][0]["our_label"])
    check("the requested types go into the URL", "type=8" in calls[0] and "type=7" in calls[0])
    try:
        gu.page_info("nope", [8])
        check("a non-GWD is refused", False)
    except gu.UploadError:
        check("a non-GWD is refused", True)

    print("— PDF wrapping (their form takes application/pdf only) —")
    pdf = b"%PDF-1.4 already a pdf"
    check("a PDF passes through untouched", gu.to_pdf("a.pdf", pdf) == (pdf, ""))
    out, note = gu.to_pdf("id.jpg", _jpeg())
    check("a JPEG is wrapped, not re-encoded",
          out.startswith(b"%PDF-") and b"/DCTDecode" in out and _jpeg() in out and "JPG" in note)
    out, _ = gu.to_pdf("id.png", _png(2))
    check("an RGB PNG rides as FlateDecode with predictor 15",
          out.startswith(b"%PDF-") and b"/Predictor 15" in out and b"/DeviceRGB" in out)
    out, note = gu.to_pdf("id.png", _png(6))
    check("an RGBA PNG is flattened (alpha dropped) and still valid",
          out.startswith(b"%PDF-") and b"/DeviceRGB" in out and "transparency" in note)
    out, _ = gu.to_pdf("id.png", _png(0))
    check("a grayscale PNG keeps DeviceGray", b"/DeviceGray" in out)
    for bad, why in ((b"GIF89a123456", "a GIF is refused"),
                     (b"", "an empty file is refused")):
        try:
            gu.to_pdf("x.gif", bad)
            check(why, False)
        except gu.UploadError:
            check(why, True)
    # a real file in the library declares an IDAT one byte short of its own
    # zlib stream — a half-decoded scan must never be posted looking fine
    truncated = _png(2).replace(b"IDAT", b"IDAT", 1)[:-30] + b"\x00" * 4
    try:
        gu.to_pdf("broken.png", truncated)
        check("a corrupt PNG is refused, not silently half-drawn", False)
    except gu.UploadError as e:
        check("a corrupt PNG is refused, not silently half-drawn", "corrupt" in str(e))

    print("— the request body —")
    docs = [{"type": 8, "filename": "passport.jpg", "data": _jpeg(), "source": "library"},
            {"type": 7, "filename": "decl.pdf", "data": b"%PDF-1.4 decl", "source": "declaration"}]
    req = gu.build_request("GWD004721753", docs, info)
    body = req["body"]
    check("the anti-forgery token is field #1",
          body.index(b'name="__RequestVerificationToken"') < body.index(b'name="FileTypes[0]"'))
    check("every slot carries its type AND their description",
          all(s in body for s in [b'name="FileTypes[0]"', b'name="FileTypes[1]"',
                                  "צילום דרכון".encode(), "הצהרת שימוש בטובין".encode()]))
    check("the parcel number rides along", b'name="PackageId"' in body and b"GWD004721753" in body)
    check("one UploadedFiles part per slot, in slot order",
          body.count(b'name="UploadedFiles"') == 2
          and body.index(b"passport.pdf") < body.index(b"decl.pdf"))
    check("a JPEG pick is sent as a .pdf filename", b'filename="passport.pdf"' in body)
    check("the boundary closes the body",
          body.rstrip().endswith(f"--{req['boundary']}--".encode()))
    check("the review data names each file, its size and its slot",
          [d["type"] for d in req["docs"]] == [8, 7]
          and all(d["bytes"] > 0 for d in req["docs"]))
    try:
        gu.build_request("GWD004721753", [docs[0]], info)
        check("a slot with no document refuses (never uploads a blank)", False)
    except gu.UploadError:
        check("a slot with no document refuses (never uploads a blank)", True)
    # Since 2026-08-17 the wizard opens EVERY slot GAASH asked for, so the picks
    # arrive in the operator's ticking order, not GAASH's slot order. Pairing is
    # by TYPE (build_request's by_type dict), so the two need never agree — this
    # is what stops a passport being filed as a declaration.
    rev = gu.build_request("GWD004721753", [docs[1], docs[0]], info)
    check("picks handed in reverse still file by slot, not by position",
          [d["type"] for d in rev["docs"]] == [8, 7]
          and rev["body"].index(b"passport.pdf") < rev["body"].index(b"decl.pdf"))
    check("page_url repeats the param once per slot",
          gu.page_url("GWD004775212", [6, 7])
          == "https://ops.gaashwd.com/fileUpload?packageId=GWD004775212&type=6&type=7")
    big = {"type": 8, "filename": "huge.pdf", "data": b"%PDF-" + b"x" * (gu.MAX_BYTES + 1)}
    try:
        gu.build_request("GWD004721753", [big, docs[1]], info)
        check("anything over their 2 MB limit refuses by name", False)
    except gu.UploadError as e:
        check("anything over their 2 MB limit refuses by name", "huge.pdf" in str(e))

    print("— dry-run must not post —")
    posted = []

    def boom(req, timeout=None):
        posted.append(req)
        raise AssertionError("dry-run POSTed to GAASH")

    res = gu.upload("GWD004721753", docs, dry_run=True, opener=boom)
    check("nothing left the machine", not posted)
    check("...and it still reports exactly what WOULD be sent",
          res["ok"] and res["dry_run"] and len(res["docs"]) == 2 and res["bytes"] > 0)
    check("the dry-run says so in words", "dry-run" in res["note"])

    print("— arming it actually posts, and reads their answer —")
    class Resp:
        def __init__(self, status, body):
            self.status, self._b = status, body.encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sent = {}

    def ok_opener(req, timeout=None):
        sent["url"], sent["headers"] = req.full_url, dict(req.headers)
        sent["len"] = len(req.data)
        return Resp(200, "<div>הקבצים הועלו בהצלחה</div>")

    res = gu.upload("GWD004721753", docs, dry_run=False, opener=ok_opener)
    check("it posts to their FileUpload endpoint", sent["url"] == gu.UPLOAD_POST)
    check("the cookie travels with the token",
          "COOKIE-999" in str(sent["headers"].get("Cookie", "")))
    check("multipart content-type carries the boundary",
          "multipart/form-data; boundary=" in str(sent["headers"].get("Content-type", "")))
    check("a Hebrew success is read as success", res["ok"] and "confirmed" in res["note"])
    res = gu.upload("GWD004721753", docs, dry_run=False,
                    opener=lambda r, timeout=None: Resp(200, "<div>שגיאה בהעלאה</div>"))
    check("a Hebrew error is NOT reported as sent", not res["ok"])
    res = gu.upload("GWD004721753", docs, dry_run=False,
                    opener=lambda r, timeout=None: Resp(200, "<html>ok</html>"))
    check("an ambiguous answer is honest about it",
          res["ok"] and "no explicit confirmation" in res["note"])

    print()
    print("RESULT:", "PASS" if not fails else f"FAIL ({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
