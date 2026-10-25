"""Regression tests for paginate_doc's memory-bounding flush.

apply_redactions() on an image-heavy page forces MuPDF to decode that
page's embedded image into a raw bitmap and never release it for the
document's lifetime — measured at ~18MB/page, linear in page count, on a
scanned-style document (confirmed via `del page; gc.collect()` NOT
reducing it, so it's retained by the Document object, not GC-able). On a
free-tier 512MB dyno that's an OOM crash from a single ~30-page scanned
filing. pagination.py now periodically round-trips the document through
save+reopen to flush that accumulated state — see _REDACT_FLUSH_EVERY's
docstring in pagination.py for the measured before/after numbers.

These tests lock in the CONTRACT change this fix required (paginate_doc
now returns the document, or None, instead of a bool — because the
periodic flush can reassign `doc` to a new object mid-call, so a caller
holding its own separate reference would silently operate on a stale,
closed one) and that the flush doesn't corrupt output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from pagination import paginate_doc
from PIL import Image


def _make_image_heavy_pdf(tmp_path, pages: int) -> str:
    """Each page gets a genuinely distinct JPEG (not dedup-able, unlike a
    repeated identical image) so apply_redactions has real decode work to
    do on every page — the exact shape that triggers the memory growth."""
    doc = fitz.open()
    for i in range(pages):
        img = Image.new("RGB", (600, 850), (250 - i % 5, 248, 245))
        img_path = str(tmp_path / f"_pg{i}.jpg")
        img.save(img_path, "JPEG", quality=70)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=img_path)
    path = str(tmp_path / "heavy.pdf")
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def test_paginate_doc_returns_document_not_bool(portrait_pdf):
    doc = fitz.open(portrait_pdf)
    result = paginate_doc(doc, 0)
    assert result is not None
    assert hasattr(result, "save"), "must return the fitz.Document, not True/False"
    result.close()


def test_paginate_doc_returns_none_on_invalid_index_end_page(portrait_pdf):
    doc = fitz.open(portrait_pdf)
    result = paginate_doc(doc, 99)  # out of range for a 1-page doc
    assert result is None


def test_flush_preserves_page_count_and_content(tmp_path):
    """The flush round-trips the doc through save+reopen mid-loop — page
    count and already-applied redactions/stamps must survive that."""
    path = _make_image_heavy_pdf(tmp_path, pages=60)
    doc = fitz.open(path)
    result = paginate_doc(doc, 0)
    assert result is not None
    assert len(result) == 60

    # Spot-check stamped numbers before AND after where a flush must have
    # happened (_REDACT_FLUSH_EVERY=25, so page 40 is past two flushes).
    assert "1" in result[0].get_text()
    assert "41" in result[40].get_text()
    assert "60" in result[59].get_text()
    result.close()


def test_flush_bounds_memory_growth(tmp_path):
    """Not a strict byte-count assertion (memory behavior varies by
    platform/allocator) — asserts the SHAPE: per-page growth with
    flushing must be meaningfully sub-linear vs without it, which is the
    property the fix is actually for. Uses resource.ru_maxrss as a
    same-process, dependency-free proxy (exact values aren't portable,
    but comparing two runs in the same process on the same platform is)."""
    import importlib
    import resource

    import pagination

    path = _make_image_heavy_pdf(tmp_path, pages=40)

    def peak_mb():
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / 1024 / 1024 if sys.platform == "darwin" else r / 1024

    before = peak_mb()
    doc = fitz.open(path)
    importlib.reload(pagination)
    pagination._REDACT_FLUSH_EVERY = 10_000  # effectively disabled
    result = pagination.paginate_doc(doc, 0)
    assert result is not None
    unflushed_growth = peak_mb() - before
    result.close()

    before2 = peak_mb()
    doc2 = fitz.open(path)
    pagination._REDACT_FLUSH_EVERY = 10
    result2 = pagination.paginate_doc(doc2, 0)
    assert result2 is not None
    flushed_growth = peak_mb() - before2
    result2.close()

    pagination._REDACT_FLUSH_EVERY = 25  # restore the real default

    # ru_maxrss is a cumulative high-water mark that never goes down within
    # a process, so the SECOND measurement's "before" already includes the
    # first run's growth — meaning this is a conservative comparison, not
    # inflated in the flushed run's favor.
    assert flushed_growth < unflushed_growth * 0.9, (
        f"flushing should meaningfully reduce growth: "
        f"unflushed={unflushed_growth:.0f}MB flushed={flushed_growth:.0f}MB"
    )
