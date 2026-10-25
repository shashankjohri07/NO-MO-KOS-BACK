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

A second, independent mitigation skips pages where the redaction zone
has nothing in it at all (the common case: a first-time upload has no
stale number to remove) — a pure no-op skipped, not a behavior change.
See _zone_has_content's docstring in pagination.py.

These tests lock in the CONTRACT change the flush fix required
(paginate_doc now returns the document, or None, instead of a bool —
because the periodic flush can reassign `doc` to a new object mid-call,
so a caller holding its own separate reference would silently operate on
a stale, closed one) and that neither mitigation corrupts output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from pagination import paginate_doc
from PIL import Image


def _make_image_heavy_pdf(tmp_path, pages: int, stale_number: bool = True) -> str:
    """Each page gets a genuinely distinct JPEG (not dedup-able, unlike a
    repeated identical image) so apply_redactions has real decode work to
    do on every page — the exact shape that triggers the memory growth.

    `stale_number=True` (the default) stamps a number in the redaction
    zone, simulating a filing that's being re-processed (already has a
    page number there) — the case that NEEDS redaction, as opposed to a
    first-time upload's genuinely blank corner (see
    test_skip_blank_zone_pages_entirely, which wants stale_number=False).
    """
    doc = fitz.open()
    for i in range(pages):
        img = Image.new("RGB", (600, 850), (250 - i % 5, 248, 245))
        img_path = str(tmp_path / f"_pg{i}.jpg")
        img.save(img_path, "JPEG", quality=70)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=img_path)
        if stale_number:
            # "999" on every page -- deliberately NOT what Pass 2 would
            # stamp (page i gets fresh digit i+1), so a test can tell
            # "old stale value correctly removed" apart from "coincidentally
            # already matched the new value".
            page.insert_text((540, 35), "999", fontsize=10)
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


def test_skip_blank_zone_pages_entirely(tmp_path, monkeypatch):
    """A page with nothing in the redaction zone (first-time upload, no
    stale number) must not call apply_redactions at all — same output
    either way (redacting blank space is a no-op), but skipping the call
    avoids the expensive decode that produces it. Verified via call count,
    not memory bytes, for the same portability reason as the flush test
    above."""
    import pagination

    path = _make_image_heavy_pdf(tmp_path, pages=10, stale_number=False)

    redact_calls = []
    real_apply = fitz.Page.apply_redactions

    def counting_apply(self, *a, **kw):
        redact_calls.append(1)
        return real_apply(self, *a, **kw)

    monkeypatch.setattr(fitz.Page, "apply_redactions", counting_apply)

    doc = fitz.open(path)
    result = pagination.paginate_doc(doc, 0)
    assert result is not None
    assert len(result) == 10
    assert redact_calls == [], "blank-zone pages must skip apply_redactions entirely"
    result.close()


def test_does_not_skip_pages_that_need_redaction(tmp_path, monkeypatch):
    """The inverse of the above: a page WITH a stale number must still be
    redacted — the skip-optimization must never cause it to be missed."""
    import pagination

    path = _make_image_heavy_pdf(tmp_path, pages=10, stale_number=True)

    redact_calls = []
    real_apply = fitz.Page.apply_redactions

    def counting_apply(self, *a, **kw):
        redact_calls.append(1)
        return real_apply(self, *a, **kw)

    monkeypatch.setattr(fitz.Page, "apply_redactions", counting_apply)

    doc = fitz.open(path)
    result = pagination.paginate_doc(doc, 0)
    assert result is not None
    assert len(redact_calls) == 10, "every page with a stale number must be redacted"

    # And the stale "999" is actually gone (not just that apply_redactions
    # was called — it must have had the right effect), replaced by Pass
    # 2's fresh digit ("1" on page 0).
    mask = fitz.Rect(0.75 * 595, 0, 595, 0.06 * 842)
    zone_text = result[0].get_text("text", clip=mask).strip()
    assert "999" not in zone_text, f"stale number survived redaction: {zone_text!r}"
    assert zone_text == "1", f"expected Pass 2's fresh digit, got: {zone_text!r}"
    result.close()
