#!/usr/bin/env python3
"""
Self-checks: the customer ID-number feature.

Covers: id_number on the customer record (round-trip + dict-upsert preserve
guard); the staff "Request ID" link mint; the public token-gated /api/id/submit
(number AND photo required, single-use, rejections don't burn the token, writes
to the CRM + stamps the order); the id_number flowing onto order rows
(/api/report); and the GAASH-mail
{id_number} template token resolving gwd → customer.

    ./.venv/bin/python test_customer_id.py
"""

import base64
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="otlobly-custid-"))
os.environ["OTLOBLY_DATA_DIR"] = str(_TMP)
os.environ["OTLOBLY_DB"] = str(_TMP / "t.db")
os.environ.pop("OTLOBLY_SECURE", None)
os.environ["OTLOBLY_SECRET"] = "x"

import app as appmod       # noqa: E402
import auth                # noqa: E402
import customers as cust_mod  # noqa: E402
import db                  # noqa: E402
import gaash_mail as gm    # noqa: E402
import normalize           # noqa: E402
import purchases           # noqa: E402

# a 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

fails = []


def check(name, cond):
    print(f"  {'OK ' if cond else 'XX '} {name}")
    if not cond:
        fails.append(name)


def client(user, pw="s1"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": user, "password": pw})
    return c


def main():
    db.init_db()
    db.create_user("id-adm", auth.hash_pw("s1"), "admin", "Owner", business_id=1)
    adm = client("id-adm")
    anon = appmod.app.test_client()
    PHONE = "+970599111222"

    print("— customer record carries id_number —")
    c = appmod.make_customer(name="Sami", whatsapp=PHONE, id_number="ABC123")
    db.upsert_customer(c)
    got = db.get_customer(c["customer_id"])
    check("id_number persists on the customer", got.get("id_number") == "ABC123")
    # the dict-upsert guard keeps a stored number when a re-sync lacks one
    dd = {"customers": [dict(got)], "seq": 1}
    fresh = cust_mod.new_customer(dd, name="Sami", whatsapp=PHONE)   # no id_number
    merged, _ = cust_mod.upsert(dd, fresh)
    check("re-sync without a number preserves the stored one",
          merged.get("id_number") == "ABC123")
    # 2026-08-04 (UI_AUDIT Batch 6): /api/customer used to rebuild the record
    # from the posted fields only and REPLACE data_json — a profile save (or a
    # board inline edit) without ID fields silently wiped the stored ID. The
    # endpoint now merge-guards against the existing record.
    ps = adm.post("/api/customer", json={"name": "Sami", "whatsapp": PHONE,
                                         "city": "Ramallah"}).get_json()
    check("profile save succeeds", ps.get("ok") is True)
    kept = next((x for x in db.list_customers()
                 if x.get("match_key") == c.get("match_key")), {})
    check("profile save without ID fields keeps the stored id_number",
          kept.get("id_number") == "ABC123")
    check("profile save keeps the original customer_id",
          kept.get("customer_id") == c["customer_id"] and ps.get("customer_id") == c["customer_id"])
    check("profile save applied the edited field", kept.get("city") == "Ramallah")

    print("— an order + its Purchases package (for the flow below) —")
    phones = normalize.collect_phones(PHONE)
    o = appmod.make_order(name="Sami", phones=phones, address="Nablus",
                          items=[{"title": "Watch", "asin": "B0IDTEST"}],
                          status="REQUESTED", amount_to_collect_usd=50.0)
    db.insert_new_order(o)
    oid = o["order_id"]
    pdb = purchases.load()
    pdb["purchase_orders"].append({
        "po_id": "PO-ID1", "ship_to": "Sami", "created_at": db.now_iso(),
        "packages": [{"package_no": 1, "tracking_number": "GWDID000001",
                      "items": [{"asin": "B0IDTEST", "customer_name": "Sami",
                                 "customer_order_id": oid}]}]})
    purchases.save(pdb)

    print("— staff mints a Request-ID link —")
    r = adm.post("/api/order/request_id_link", json={"id": oid}).get_json()
    check("link minted with a /id/<token> url", r.get("ok") and "/id/" in r.get("url", ""))
    token = r["url"].rsplit("/", 1)[-1]
    hy = anon.get(f"/api/idreq/{token}").get_json()
    check("public hydrate returns the customer name", hy.get("ok") and hy.get("name") == "Sami")

    print("— public submit: number AND photo required, single-use —")
    bad = anon.post("/api/id/submit", json={"token": token, "id_number": " "})
    check("blank ID number is rejected", bad.status_code == 400)
    noimg = anon.post("/api/id/submit", json={"token": token, "id_number": "PAL-1"})
    check("a submit with no photo is rejected", noimg.status_code == 400)
    badimg = anon.post("/api/id/submit", json={"token": token, "id_number": "PAL-1",
                                               "data_base64": "not-valid-base64!!"})
    check("an unreadable photo is rejected", badimg.status_code == 400)
    notpic = anon.post("/api/id/submit",
                       json={"token": token, "id_number": "PAL-1",
                             "data_base64": base64.b64encode(b"plain text, no image").decode(),
                             "filename": "id.png"})
    check("a photo that can't become a PDF (not JPG/PNG/PDF) is rejected",
          notpic.status_code == 400)
    # a rejection must not burn the single-use token — the customer retries
    check("a rejected submit leaves the link usable",
          anon.get(f"/api/idreq/{token}").get_json().get("ok") is True)
    b64 = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    ok = anon.post("/api/id/submit",
                   json={"token": token, "id_number": "PAL-99887766",
                         "data_base64": b64, "filename": "id.png"})
    check("submit succeeds", ok.status_code == 200 and ok.get_json().get("ok"))
    again = anon.post("/api/id/submit", json={"token": token, "id_number": "X"})
    check("token is single-use (second submit 404s)", again.status_code == 404)

    print("— it landed on the customer + stamped the order —")
    cust2 = db.get_customer(c["customer_id"])
    check("customer now has the submitted ID number", cust2.get("id_number") == "PAL-99887766")
    check("customer ID image saved + on disk",
          bool(cust2.get("id_image")) and (cust_mod.ID_DIR / cust2["id_image"]).exists())
    check("the order was stamped id_submitted_at", bool(db.get_order(oid).get("id_submitted_at")))

    print("— staff CRM photo upload gets the same PDF-convertible gate —")
    badc = adm.post("/api/customer_image",
                    json={"customer_id": c["customer_id"], "filename": "x.png",
                          "data_base64": base64.b64encode(b"not an image at all").decode()})
    check("staff upload refuses a non-JPG/PNG/PDF", badc.status_code == 400)
    bigc = adm.post("/api/customer_image",
                    json={"customer_id": c["customer_id"], "filename": "big.jpg",
                          "data_base64": base64.b64encode(b"\xff" * (16 * 1024 * 1024)).decode()})
    check("staff upload refuses an over-15MB file (route had NO cap before)",
          bigc.status_code == 400)
    okc = adm.post("/api/customer_image",
                   json={"customer_id": c["customer_id"], "filename": "id2.png",
                         "data_base64": base64.b64encode(_PNG).decode()})
    check("a real PNG still lands on the profile",
          okc.status_code == 200 and okc.get_json().get("ok"))

    print("— id_number flows onto order rows (Purchases order-map) —")
    rep = adm.get("/api/report").get_json()
    row = next((x for x in rep.get("orders", []) if x["order_id"] == oid), None)
    check("report order row carries id_number",
          row and row.get("id_number") == "PAL-99887766" and row.get("has_id_number"))

    print("— GAASH mail {id_number} token resolves via gwd → customer —")
    toks = {t["token"] for t in gm.overview()["tpl_tokens"]}
    check("{id_number} is a template token", "{id_number}" in toks)
    check("_fill resolves {id_number} for the package's customer",
          gm._fill("ID: {id_number}", "GWDID000001") == "ID: PAL-99887766")
    check("_fill leaves {id_number} blank for an unknown package",
          gm._fill("ID:{id_number}", "GWDNOBODY9") == "ID:")

    print("— the upload wizard's plan carries customer_id + library sizes —")
    # the wizard route insists on a REAL GWD shape (GWD + digits only), so give
    # the same order a second, numeric package; page_info hits GAASH's real
    # page — stub it; everything else is local
    pdb = purchases.load()
    pdb["purchase_orders"][-1]["packages"].append(
        {"package_no": 2, "tracking_number": "GWD000001234",
         "items": [{"asin": "B0IDTEST", "customer_name": "Sami",
                    "customer_order_id": oid}]})
    purchases.save(pdb)
    import gaash_upload as gu
    orig_pi = gu.page_info
    gu.page_info = lambda tn, types: {"slots": [{"type": 8, "label": "צילום דרכון"}]}
    try:
        added = gm.ids_add("qais ID", "qais.png", _PNG, folder="id")
        plan = adm.get("/api/gaash/upload/plan?gwd=GWD000001234").get_json()
        check("plan answers ok", plan.get("ok") is True)
        check("plan customer carries customer_id (the inline ID-number fix posts with it)",
              (plan.get("customer") or {}).get("customer_id") == c["customer_id"])
        lib = [d for d in plan.get("library") or [] if d["id"] == added["id"]]
        check("library rows carry their real byte size",
              lib and lib[0].get("size") == len(_PNG))
        # a vanished file must not sink the plan — its size just reads 0
        (gm.IDS_DIR / added["filename"]).unlink()
        plan2 = adm.get("/api/gaash/upload/plan?gwd=GWD000001234").get_json()
        lib2 = [d for d in plan2.get("library") or [] if d["id"] == added["id"]]
        check("a missing library file reads size 0 and the plan still opens",
              plan2.get("ok") is True and lib2 and lib2[0].get("size") == 0)
    finally:
        gu.page_info = orig_pi

    print()
    if fails:
        print(f"FAILED: {len(fails)} — {fails}")
        raise SystemExit(1)
    print("All customer-ID checks passed ✓")


if __name__ == "__main__":
    main()
