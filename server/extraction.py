"""Page-level text extraction with Tesseract OCR fallback.

The detect / both modes call extract_pages() to get a list of page dicts
holding text, span positions, and a pre-computed `printed_num` (the
top-right page-number digit). The write-only fast path skips this module
entirely — its only job is to feed the rule engine.

Memory shape: each page dict carries a list of span dicts. For a 1000-page
PDF with dense text this can be a few hundred MB. Keep that in mind when
deploying — write-only paths sidestep this.
"""

import io
import re
import sys
from typing import List, Optional

from config import (
    MIN_TEXT_CHARS,
    TESSERACT_AVAILABLE,
    TESSERACT_CONFIG,
    TESSERACT_CONFIG_SINGLE_LINE,
    TESSERACT_DPI,
    TESSERACT_PAGENUM_DPI,
    fitz,
)


def _ocr_page_with_tesseract(doc, page_idx: int, page_num: int) -> Optional[str]:
    """OCR a single page using Tesseract. Returns None on any failure."""
    if not TESSERACT_AVAILABLE or not fitz:
        return None
    try:
        # Lazy import — write-only paths never reach this code, no reason
        # to pay PIL/pytesseract import cost there.
        import pytesseract
        from PIL import Image

        page = doc[page_idx]
        mat = fitz.Matrix(TESSERACT_DPI / 72, TESSERACT_DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, config=TESSERACT_CONFIG)
        return text.strip() if text and len(text.strip()) > 10 else None
    except Exception as e:
        print(f"  Tesseract error page {page_num}: {e}", file=sys.stderr)
        return None


def _ocr_topright_page_number(doc, page_idx: int) -> Optional[int]:
    """OCR the top-right corner for a printed page number.

    Two-pass strategy:
      Pass 1 (tight + strict): top 8% × right 25%, digit-only whitelist.
      Pass 2 (wider + lenient): top 14% × right 35%, full PSM 6, line-by-line.
        Filters out lines noisy with punctuation (degraded scans).
    """
    if not TESSERACT_AVAILABLE or not fitz:
        return None
    try:
        import pytesseract
        from PIL import Image

        page = doc[page_idx]
        rect = page.rect
        mat = fitz.Matrix(TESSERACT_PAGENUM_DPI / 72, TESSERACT_PAGENUM_DPI / 72)

        # Pass 1
        crop_tight = fitz.Rect(rect.width * 0.75, 0, rect.width, rect.height * 0.08)
        pix = page.get_pixmap(matrix=mat, clip=crop_tight)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, config=TESSERACT_CONFIG_SINGLE_LINE) or ""
        m = re.search(r"\b(\d{1,4})\b", text)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 9999:
                return num

        # Pass 2
        crop_wide = fitz.Rect(rect.width * 0.65, 0, rect.width, rect.height * 0.14)
        pix = page.get_pixmap(matrix=mat, clip=crop_wide)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        full = pytesseract.image_to_string(img, config="--oem 3 --psm 6") or ""
        for line in full.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            noise = sum(1 for c in stripped if not c.isalnum() and not c.isspace())
            if noise > 2:
                continue
            core = re.sub(r"^[^\w]+|[^\w]+$", "", stripped)
            mm = re.fullmatch(r"\d{1,4}", core)
            if mm:
                num = int(mm.group(0))
                if 1 <= num <= 9999:
                    return num
        return None
    except Exception as e:
        print(f"  Top-right OCR error page {page_idx+1}: {e}", file=sys.stderr)
        return None


_PAGENUM_RE = re.compile(r"^[^\w]?(\d{1,4})[^\w]?$")


def _bare_digit(text: str) -> Optional[int]:
    """A 1-4 digit number, optionally with one leading and one trailing
    non-word char (typical scan artefacts: leading apostrophe, trailing
    dash). Rejects mangled text like "1//-" or "1.•)" that happens to
    start with a digit."""
    m = _PAGENUM_RE.match(text.strip())
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 9999 else None


def _extract_printed_page_number(page: dict) -> Optional[int]:
    """Strict bare-digit number from the top-right corner only.

    Geometry: top ~6% × right ~25%. A looser cutoff falsely picks up
    centered title text like "I.A. (PLAN) NO. 104 OF 2025".
    """
    page_width = page.get("width", 595)
    page_height = page.get("height", 842)
    top_limit = page_height * 0.06
    right_limit = page_width * 0.75

    candidates = []
    for span in page.get("spans", []):
        text = span["text"]
        if not text.strip():
            continue
        bbox = span.get("bbox", [0, 0, 0, 0])
        x0, y0 = bbox[0], bbox[1]
        if y0 > top_limit or x0 < right_limit:
            continue
        n = _bare_digit(text)
        if n is None:
            continue
        candidates.append((x0, y0, n))

    if candidates:
        # Top-most wins (smallest y0). Body fragments that survive the
        # corner filter are always lower in y. Tie-break: right-most.
        candidates.sort(key=lambda c: (c[1], -c[0]))
        return candidates[0][2]

    # Fallback for pages where Tesseract was used (text layer missing).
    if page.get("ocr_used"):
        text = page["text"].strip()
        if text:
            for line in text.split("\n")[:4]:
                n = _bare_digit(line)
                if n is not None:
                    return n
    return None


def extract_pages(file_path: str) -> dict:
    """Extract text + spans for every page, with Tesseract OCR fallback
    for any page whose embedded text is below MIN_TEXT_CHARS.

    Returns a dict with `ok`, `pages` (list of page dicts), `full_text`,
    `total_pages`, `ocr_method`. On failure: `ok=False, error=...`.
    """
    if not fitz:
        return {"ok": False, "error": "PyMuPDF not installed: pip install pymupdf"}

    doc = None
    try:
        doc = fitz.open(file_path)
        pages: List[dict] = []
        ocr_pages: List[int] = []
        ocr_method = "pymupdf"

        for i, page in enumerate(doc):
            text = page.get_text()
            spans = []
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            spans.append({
                                "text": span["text"],
                                "bbox": list(span["bbox"]),
                                "font": span.get("font", ""),
                                "size": span.get("size", 0),
                            })
            pages.append({
                "page_num": i + 1,
                "text": text,
                "text_upper": text.upper(),
                "spans": spans,
                "width": page.rect.width,
                "height": page.rect.height,
                "ocr_used": False,
            })
            if len(text.strip()) < MIN_TEXT_CHARS:
                ocr_pages.append(i)

        # Full-page OCR for scanned pages.
        if ocr_pages and TESSERACT_AVAILABLE:
            print(f"{len(ocr_pages)} pages need OCR — running full-page Tesseract...",
                  file=sys.stderr)
            ocr_method = "pymupdf+tesseract"
            for idx, page_idx in enumerate(ocr_pages):
                if idx % 10 == 0:
                    print(f"  OCR progress {idx+1}/{len(ocr_pages)}...", file=sys.stderr)
                ocr_text = _ocr_page_with_tesseract(doc, page_idx, page_idx + 1)
                if ocr_text:
                    pages[page_idx]["text"] = ocr_text
                    pages[page_idx]["text_upper"] = ocr_text.upper()
                    pages[page_idx]["ocr_used"] = True
            ocr_success = sum(1 for p in pages if p["ocr_used"])
            print(f"Tesseract OCR done: {ocr_success}/{len(ocr_pages)} pages",
                  file=sys.stderr)
        elif ocr_pages:
            print(
                f"{len(ocr_pages)} pages need OCR but Tesseract not available. "
                f"Install: pip install pytesseract",
                file=sys.stderr,
            )

        # Pre-compute printed page numbers — text layer first, then strip OCR.
        max_plausible = max(len(pages) * 2, 100)
        needing_strip = []
        for p in pages:
            n = _extract_printed_page_number(p)
            if n and n <= max_plausible:
                p["printed_num"] = n
            else:
                p["printed_num"] = None
                needing_strip.append(p["page_num"] - 1)

        if needing_strip and TESSERACT_AVAILABLE:
            print(
                f"Top-right strip OCR for {len(needing_strip)} pages still unnumbered...",
                file=sys.stderr,
            )
            filled = 0
            for idx_i, page_idx in enumerate(needing_strip):
                if idx_i % 25 == 0:
                    print(f"  strip-OCR progress {idx_i+1}/{len(needing_strip)}...",
                          file=sys.stderr)
                n = _ocr_topright_page_number(doc, page_idx)
                if n and n <= max_plausible:
                    pages[page_idx]["printed_num"] = n
                    filled += 1
            print(f"Strip OCR filled {filled}/{len(needing_strip)} page numbers.",
                  file=sys.stderr)

        full_text = "\n".join(p["text"] for p in pages)
        return {
            "ok": True,
            "pages": pages,
            "full_text": full_text.strip(),
            "total_pages": len(pages),
            "ocr_method": ocr_method,
        }
    except Exception as e:
        return {"ok": False, "error": f"PDF extraction failed: {e}"}
    finally:
        if doc is not None:
            doc.close()
