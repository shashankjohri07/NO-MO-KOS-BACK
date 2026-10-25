"""Regression tests for the annexure-rotation bug.

A scanned annexure page with /Rotate 90/180/270 whose footer already has
content used to come out sideways and cropped after signature stamping
(signatures.py's _reserve_footer_strip rebuilt the page via show_pdf_page,
which does not reliably bake in the source page's /Rotate). Fixed by
rasterising rotated pages via get_pixmap() instead, which does apply
/Rotate correctly. See the git history on signatures.py for the full
writeup.

These tests assert on RENDERED PIXELS, not on page.rotation alone — the
bug was specifically that the rotation flag got reset to 0 while the
content inside was still sideways, so checking page.rotation == 0 alone
would have passed on the broken code too.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from annexures import append_annexures_to_doc


def _is_mostly_blank(page, box) -> bool:
    """True if the given (x0, y0, x1, y1) visible-coord band renders
    near-white. Same clip-render approach signatures._zone_is_blank uses."""
    clip = fitz.Rect(*box)
    pm = page.get_pixmap(clip=clip, colorspace=fitz.csGRAY, alpha=False)
    if pm.width < 1 or pm.height < 1:
        return True
    return min(pm.samples) >= 240


def test_rotated_dense_annexure_renders_upright(
    portrait_pdf, rotated_dense_annexure_pdf, signature_png,
):
    """For every /Rotate quadrant, after append + signature stamping the
    annexure page must render upright: the output page's pixel dimensions
    must match a portrait page (taller than wide), and content must not be
    cropped off — the TOP label text must be visible in the top band."""
    main = fitz.open(portrait_pdf)
    ok = append_annexures_to_doc(
        main, [rotated_dense_annexure_pdf], client_sig_path=signature_png,
    )
    assert ok
    assert len(main) == 2

    annex_page = main[1]
    pix = annex_page.get_pixmap()

    # Portrait orientation preserved (width < height), regardless of the
    # source /Rotate value.
    assert pix.width < pix.height, (
        f"annexure page rendered landscape ({pix.width}x{pix.height}) — "
        "looks rotated sideways"
    )

    # The top band (where "Annexure A-1" + "TOP" are stamped/drawn) must
    # have visible content, not be blank — a cropped/mispositioned render
    # would push this content out of frame.
    top_band = (0, 0, annex_page.rect.width, 60)
    assert not _is_mostly_blank(annex_page, top_band), (
        "top of the annexure page is blank — content got cropped or "
        "shifted out of frame during the footer-strip rebuild"
    )

    main.close()


def test_unrotated_dense_annexure_still_works(
    portrait_pdf, make_dense_annexure, signature_png,
):
    """Baseline: the common (unrotated) case must be untouched by the fix."""
    annex_path = make_dense_annexure(0)
    main = fitz.open(portrait_pdf)
    ok = append_annexures_to_doc(main, [annex_path], client_sig_path=signature_png)
    assert ok

    pix = main[1].get_pixmap()
    assert pix.width < pix.height
    main.close()
