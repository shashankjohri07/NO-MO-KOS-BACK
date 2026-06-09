"""Regression tests for the signature-stamping pipeline.

Plain Python on purpose (no pytest dependency in the deploy image). Run:

    cd server && python3 tests/test_signatures.py

Locks the externally observable behaviour of stamp_signatures_on_page and the
CLI so performance work cannot silently change functionality:

  * placement: normal page -> bottom footer; dense page with a clear top
    margin -> top header; both ends busy -> content scaled into a reserved
    bottom strip (page size unchanged);
  * the signature never overlaps content (text stays above the sig band);
  * aspect ratio of the stamped image is preserved;
  * white signature backgrounds become transparent (ink-only stamping);
  * end-to-end CLI run: page count, exit code, per-kind placement counts.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, SERVER)

import fitz  # noqa: E402  (resolved via PyMuPDF, same as config.fitz)
from PIL import Image, ImageDraw  # noqa: E402

import signatures  # noqa: E402

LINE = "In the matter of the IBC 2016, the RP respectfully submits the following."
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ── fixtures ────────────────────────────────────────────────────────────────

def make_sig_images(tmp):
    """White-background ink signature + opaque photo-style image."""
    ink = os.path.join(tmp, "ink.png")
    img = Image.new("RGB", (400, 140), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.line([(20, 110), (120, 30), (220, 110), (320, 40), (390, 90)],
           fill=(10, 10, 120), width=6)
    img.save(ink)

    photo = os.path.join(tmp, "photo.jpg")
    img2 = Image.new("RGB", (720, 1280), (150, 170, 200))
    ImageDraw.Draw(img2).rectangle([80, 300, 640, 760], fill=(170, 110, 80))
    img2.save(photo)
    return ink, photo


def page_text_lines(page_factory):
    doc = fitz.open()
    page_factory(doc)
    return doc


def normal_page(doc):
    p = doc.new_page(width=595, height=842)
    y = 120
    while y < 420:
        p.insert_text((50, y), LINE, fontsize=11)
        y += 18
    return p


def dense_page(doc):
    """Bottom full, top margin clear."""
    p = doc.new_page(width=595, height=842)
    y = 115
    while y < 835:
        p.insert_text((50, y), LINE, fontsize=10)
        y += 14
    return p


def both_dense_page(doc):
    """Top AND bottom full."""
    p = doc.new_page(width=595, height=842)
    y = 38
    while y < 838:
        p.insert_text((50, y), LINE, fontsize=10)
        y += 14
    return p


def scan_page(doc, tmp):
    """Image-only content reaching the bottom (no extractable text)."""
    img = os.path.join(tmp, "scanfill.png")
    im = Image.new("RGB", (560, 700), (235, 235, 245))
    dr = ImageDraw.Draw(im)
    for i in range(35):
        dr.text((20, 8 + i * 19), f"scan line {i+1}", fill=(20, 20, 20))
    im.save(img)
    p = doc.new_page(width=595, height=842)
    p.insert_image(fitz.Rect(18, 40, 578, 820), filename=img)
    return p


def max_text_y(page):
    out = 0.0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 0:
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    out = max(out, s["bbox"][3])
    return out


def sig_bboxes(page, before_xrefs):
    """Image bboxes added since `before_xrefs` (i.e. the stamped signatures)."""
    return [i["bbox"] for i in page.get_image_info(xrefs=True)
            if i["xref"] not in before_xrefs]


# ── unit: zone blank detection ──────────────────────────────────────────────

def test_zone_is_blank(tmp):
    print("zone_is_blank:")
    doc = fitz.open()
    p = normal_page(doc)
    check("normal page footer blank", signatures._zone_is_blank(p, 750, 842))
    check("normal page body NOT blank", not signatures._zone_is_blank(p, 120, 200))

    doc2 = fitz.open()
    p2 = dense_page(doc2)
    check("dense page footer NOT blank", not signatures._zone_is_blank(p2, 750, 842))
    check("dense page top header blank", signatures._zone_is_blank(p2, 48, 96))

    doc3 = fitz.open()
    p3 = scan_page(doc3, tmp)
    check("image-only footer NOT blank (pixel detection)",
          not signatures._zone_is_blank(p3, 750, 842))


# ── unit: placement regimes ────────────────────────────────────────────────

def test_placements(tmp):
    print("placement:")
    ink, photo = make_sig_images(tmp)

    # normal -> bottom
    doc = fitz.open()
    p = normal_page(doc)
    before = {i["xref"] for i in p.get_image_info(xrefs=True)}
    signatures.stamp_signatures_on_page(p, ink, photo)
    p = doc[0]
    sigs = sig_bboxes(p, before)
    check("normal: 2 sigs stamped", len(sigs) == 2, f"got {len(sigs)}")
    check("normal: sigs in bottom footer",
          all(bb[1] > 842 - 92 for bb in sigs), f"{sigs}")
    check("normal: page size unchanged",
          (round(p.rect.width), round(p.rect.height)) == (595, 842))

    # dense (top clear) -> top header
    doc = fitz.open()
    p = dense_page(doc)
    before = {i["xref"] for i in p.get_image_info(xrefs=True)}
    signatures.stamp_signatures_on_page(p, ink, photo)
    p = doc[0]
    sigs = sig_bboxes(p, before)
    check("dense: 2 sigs stamped", len(sigs) == 2)
    check("dense: sigs in top header (above body text)",
          all(bb[3] <= 115 for bb in sigs), f"{sigs}")

    # both dense -> scaled + bottom strip, no overlap, size unchanged
    doc = fitz.open()
    p = both_dense_page(doc)
    signatures.stamp_signatures_on_page(p, ink, photo)
    p = doc[0]
    mty = max_text_y(p)
    sigs = [i["bbox"] for i in p.get_image_info()]
    sig_top = min(bb[1] for bb in sigs)
    check("both-dense: page size unchanged",
          (round(p.rect.width), round(p.rect.height)) == (595, 842))
    check("both-dense: text above sig band (no overlap)",
          mty < sig_top, f"text {mty:.0f} vs sig {sig_top:.0f}")

    # scan/image-only -> must NOT stamp over the image
    doc = fitz.open()
    p = scan_page(doc, tmp)
    before = {i["xref"] for i in p.get_image_info(xrefs=True)}
    signatures.stamp_signatures_on_page(p, ink, photo)
    p = doc[0]
    sigs = sig_bboxes(p, before)
    surviving = [i["bbox"] for i in p.get_image_info(xrefs=True)
                 if i["xref"] in before]
    if len(sigs) == 2 and surviving:
        content_bottom = max(bb[3] for bb in surviving)
    else:
        # After a strip-reserve rebuild the xrefs change; the two smallest
        # images are the signatures, the largest is the scanned content.
        all_b = sorted((i["bbox"] for i in p.get_image_info()),
                       key=lambda bb: (bb[3] - bb[1]) * (bb[2] - bb[0]))
        sigs = all_b[:2]
        content_bottom = all_b[-1][3]
    sig_top = min(bb[1] for bb in sigs)
    check("scan: sigs below the scanned content",
          sig_top >= content_bottom - 1,
          f"sig {sig_top:.0f} vs content {content_bottom:.0f}")


# ── unit: aspect + transparency ────────────────────────────────────────────

def test_aspect_and_transparency(tmp):
    print("aspect / transparency:")
    ink, _ = make_sig_images(tmp)
    a = signatures._read_image_aspect(ink)
    check("aspect ratio read", a is not None and abs(a - 400 / 140) < 0.01, f"{a}")
    # cached path must return the same value
    check("aspect cache stable", signatures._read_image_aspect(ink) == a)

    tp = signatures._transparent_signature(ink)
    check("transparent copy created", tp != ink and os.path.exists(tp), tp)
    with Image.open(tp) as im:
        im = im.convert("RGBA")
        corner = im.getpixel((2, 2))       # white background corner
        check("white background is transparent", corner[3] == 0, f"{corner}")
        # the ink strokes themselves must survive fully opaque
        px = im.load()
        opaque_ink = sum(
            1 for yy in range(0, im.height, 3) for xx in range(0, im.width, 3)
            if px[xx, yy][3] == 255 and px[xx, yy][2] > px[xx, yy][0]
        )
        check("ink stays opaque", opaque_ink > 50, f"only {opaque_ink} ink px")
    check("transparency cached", signatures._transparent_signature(ink) == tp)


# ── end-to-end: CLI parity ─────────────────────────────────────────────────

def test_cli(tmp):
    print("CLI end-to-end:")
    ink, photo = make_sig_images(tmp)
    main = os.path.join(tmp, "m.pdf")
    d = fitz.open(); d.new_page().insert_text((72, 72), "MAIN"); d.save(main)

    mixed = os.path.join(tmp, "mixed.pdf")
    d = fitz.open()
    normal_page(d); dense_page(d); both_dense_page(d); scan_page(d, tmp)
    d.save(mixed)

    out = os.path.join(tmp, "out.pdf")
    with open(out, "wb") as fo:
        r = subprocess.run(
            [sys.executable, os.path.join(SERVER, "error_detector.py"),
             "--mode", "write", "--write-stdout", "--index-end-page", "0",
             "--file", main, "--annex", mixed,
             "--client-sig", ink, "--advocate-sig", photo],
            stdout=fo, stderr=subprocess.PIPE, text=False, timeout=120)
    err = r.stderr.decode()
    check("exit 0", r.returncode == 0, err[:200])
    doc = fitz.open(out)
    check("page count 5", len(doc) == 5, f"{len(doc)}")
    check("1 top-header placement", err.count("top header") == 1, err)
    check("2 scaled placements", err.count("scaled content") == 2, err)
    for i in range(1, 5):
        p = doc[i]
        check(f"page {i+1} size preserved",
              (round(p.rect.width), round(p.rect.height)) == (595, 842))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        test_zone_is_blank(tmp)
        test_placements(tmp)
        test_aspect_and_transparency(tmp)
        test_cli(tmp)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
