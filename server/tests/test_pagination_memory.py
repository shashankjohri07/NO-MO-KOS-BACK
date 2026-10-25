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


def test_flush_actually_reopens_the_document(tmp_path, monkeypatch):
    """Deliberately NOT a memory-byte-count assertion — tried that
    (comparing resource.ru_maxrss between a flushed and unflushed run) and
    it was flaky across platforms/allocators (confirmed: passed locally on
    macOS, failed in CI on Linux, because the actual memory delta a given
    page count produces is itself allocator/libmupdf-build dependent, not
    just the flush behavior under test). The real, portable claim the fix
    needs to make is behavioral: the flush path actually runs and produces
    a genuinely different Document object, not a byte-count that varies by
    environment. So: count save+reopen calls instead of measuring RSS.
    """
    import pagination

    path = _make_image_heavy_pdf(tmp_path, pages=60)
    monkeypatch.setattr(pagination, "_REDACT_FLUSH_EVERY", 10)

    reopen_calls = []
    real_open = fitz.open

    def counting_open(*args, **kwargs):
        if kwargs.get("stream") is not None:
            reopen_calls.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pagination.fitz, "open", counting_open)

    doc = fitz.open(path)
    result = pagination.paginate_doc(doc, 0)
    assert result is not None
    assert len(result) == 60

    # 60 pages / flush_every=10 -> 6 flush points mid-loop.
    assert len(reopen_calls) == 6
    result.close()
