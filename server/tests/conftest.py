"""Shared fixtures for the PDF pipeline test suite.

Builds small synthetic PDFs in a tmp_path per test instead of checking in
binary fixtures — keeps the repo diffable and the fixtures obviously
traceable to what they're meant to catch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz


@pytest.fixture
def portrait_pdf(tmp_path) -> str:
    """A trivial one-page, unrotated portrait PDF."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "MAIN DOCUMENT PAGE 1", fontsize=16)
    path = str(tmp_path / "main.pdf")
    doc.save(path)
    doc.close()
    return path


def _make_rotated_dense_annexure(tmp_path, rotation: int) -> str:
    """A page that, once rotated into its visible orientation, has text
    filling the footer band — the exact shape that triggers
    signatures._reserve_footer_strip's page-rebuild path. Content is
    rasterised from a correctly-oriented portrait reference and embedded
    pre-compensated for `rotation`, the same way a real scanner embeds a
    rotated page: the /Rotate flag undoes an inverse pre-rotation of the
    pixels, so the page renders upright exactly like a real scanned filing.
    """
    portrait = fitz.open()
    pp = portrait.new_page(width=595, height=842)
    pp.insert_text((40, 40), "TOP", fontsize=16)
    pp.draw_rect(fitz.Rect(0, 0, 595, 842), color=(0, 0, 0), width=3)
    for i in range(8):
        pp.insert_text((40, 760 + i * 8), f"dense footer line {i}", fontsize=7)
    img_path = str(tmp_path / f"ref_{rotation}.png")
    portrait[0].get_pixmap(dpi=100).save(img_path)
    portrait.close()

    annex = fitz.open()
    if rotation in (90, 270):
        page = annex.new_page(width=842, height=595)
    else:
        page = annex.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=img_path, rotate=rotation)
    page.set_rotation(rotation)
    path = str(tmp_path / f"annex_rot{rotation}.pdf")
    annex.save(path, garbage=3, deflate=True)
    annex.close()
    return path


@pytest.fixture(params=[0, 90, 180, 270])
def rotated_dense_annexure_pdf(request, tmp_path) -> str:
    """Parametrised over all four /Rotate quadrants — regression coverage
    for the annexure-rotation bug (signatures.py's _reserve_footer_strip)."""
    return _make_rotated_dense_annexure(tmp_path, request.param)


@pytest.fixture
def make_dense_annexure(tmp_path):
    """Factory fixture: call with a rotation value to build one on demand,
    for tests that need a specific rotation outside the parametrised set."""
    def _make(rotation: int) -> str:
        return _make_rotated_dense_annexure(tmp_path, rotation)
    return _make


@pytest.fixture
def signature_png(tmp_path) -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (200, 100), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 180, 80], outline=(0, 0, 200), width=6)
    path = str(tmp_path / "sig.png")
    img.save(path)
    return path
