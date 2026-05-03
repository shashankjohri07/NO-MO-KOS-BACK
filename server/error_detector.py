#!/usr/bin/env python3
"""
Appeal Document Scanner — 2 active rules.

Rules:
  1. DOC_UPLOAD   — required sections must be present (Appeal, Index, etc.)
  2. PAGINATION   — pages after the user-supplied index range must carry
                    sequential top-right digits (1, 2, 3, ...). Only bare
                    digits count; "Page 1" / "1/235" are invalid.

The user supplies `--index-end-page N` (1-indexed). Pages 1..N are skipped.
Page N+1 must show "1" top-right, page N+2 must show "2", and so on.

Usage:
    python server/error_detector.py --file /path/to/document.pdf --index-end-page 3
"""

import argparse
import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fitz  # PyMuPDF
except ImportError:
    import subprocess
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pymupdf', '-q'],
                              stderr=subprocess.DEVNULL)
        import fitz
    except Exception:
        fitz = None

# Tesseract OCR
try:
    import pytesseract
    from PIL import Image
    import io
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# Config
TESSERACT_CONFIG = '--oem 3 --psm 6'
# PSM 7 = single line; ideal for top-right page-number strips
TESSERACT_CONFIG_SINGLE_LINE = '--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789'
TESSERACT_DPI = 200
TESSERACT_PAGENUM_DPI = 300  # higher DPI on a tiny crop is cheap and more accurate
MIN_TEXT_CHARS = 50

# Annotation colors
COLOR_ERROR = (1, 0.2, 0.2)       # Red
COLOR_PASS = (0.2, 0.7, 0.3)      # Green


# =============================================================================
# Tesseract OCR helpers
# =============================================================================

def _ocr_page_with_tesseract(doc, page_idx: int, page_num: int) -> Optional[str]:
    """OCR a single page using Tesseract."""
    if not TESSERACT_AVAILABLE or not fitz:
        return None
    try:
        page = doc[page_idx]
        mat = fitz.Matrix(TESSERACT_DPI / 72, TESSERACT_DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img, config=TESSERACT_CONFIG)
        return text.strip() if text and len(text.strip()) > 10 else None
    except Exception as e:
        print(f"  Tesseract error page {page_num}: {e}", file=sys.stderr)
        return None


def _ocr_topright_page_number(doc, page_idx: int) -> Optional[int]:
    """OCR the top-right corner for a printed page number.

    Two-pass strategy:
      Pass 1 (tight + strict): top 8% × right 25%, digit-only whitelist.
              Catches clean corner digits on most pages.
      Pass 2 (wider + lenient): top 14% × right 35%, full PSM 6, line-by-line.
              Catches numbers that print slightly inside the corner or that
              the whitelist mode rejects (low contrast, bold raster digits).
              Picks the first line whose stripped content is a bare 1-4
              digit number — rejects table values like "23,157", dates,
              and case numbers.
    """
    if not TESSERACT_AVAILABLE or not fitz:
        return None
    try:
        page = doc[page_idx]
        rect = page.rect
        mat = fitz.Matrix(TESSERACT_PAGENUM_DPI / 72, TESSERACT_PAGENUM_DPI / 72)

        # --- Pass 1: tight crop, digit-only whitelist
        crop_tight = fitz.Rect(
            rect.width * 0.75, 0,
            rect.width, rect.height * 0.08,
        )
        pix = page.get_pixmap(matrix=mat, clip=crop_tight)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, config=TESSERACT_CONFIG_SINGLE_LINE) or ""
        m = re.search(r"\b(\d{1,4})\b", text)
        if m:
            num = int(m.group(1))
            if 1 <= num <= 9999:
                return num

        # --- Pass 2: wider crop, full text mode, line-aware
        crop_wide = fitz.Rect(
            rect.width * 0.65, 0,
            rect.width, rect.height * 0.14,
        )
        pix = page.get_pixmap(matrix=mat, clip=crop_wide)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        full = pytesseract.image_to_string(img, config='--oem 3 --psm 6') or ""
        # Line-by-line: a true page number sits on its own line;
        # table values like "23,157 2,27,581" share a line.
        # Also reject lines noisy with punctuation — degraded scans of
        # pages without a real corner digit produce things like "7 — : ;"
        # which the bare-digit regex would otherwise accept as "7".
        for line in full.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            noise = sum(1 for c in stripped if not c.isalnum() and not c.isspace())
            if noise > 2:
                continue
            core = re.sub(r'^[^\w]+|[^\w]+$', '', stripped)
            mm = re.fullmatch(r'\d{1,4}', core)
            if mm:
                num = int(mm.group(0))
                if 1 <= num <= 9999:
                    return num
        return None
    except Exception as e:
        print(f"  Top-right OCR error page {page_idx+1}: {e}", file=sys.stderr)
        return None


# =============================================================================
# Text extraction
# =============================================================================

def _extract_printed_page_number(page: dict) -> Optional[int]:
    """Read a STRICT bare-digit number from the top-right corner only.

    Per spec: "Page 1", "1/235", "-1-" are all INVALID. Only `1`, `2`, `3`
    written alone (after stripping whitespace) qualify.

    Geometry: a true top-right page number sits within the top ~6% of the
    page height and the rightmost ~25% of the page width. A looser cutoff
    falsely picks up centered title text like "I.A. (PLAN) NO. 104 OF 2025".
    """
    page_width = page.get("width", 595)
    page_height = page.get("height", 842)

    top_limit = page_height * 0.06
    right_limit = page_width * 0.75

    # Helper: a span/line passes if it is a 1-4 digit number, optionally
    # with AT MOST one leading and one trailing non-word char (typical
    # scan artifacts: leading apostrophe, trailing dash). Tighter than
    # an unbounded strip — rejects mangled scan text like "1//-" or
    # "1.•)" that happens to start with a digit.
    _PAGENUM_RE = re.compile(r'^[^\w]?(\d{1,4})[^\w]?$')

    def _bare_digit(text: str) -> Optional[int]:
        m = _PAGENUM_RE.match(text.strip())
        if not m:
            return None
        n = int(m.group(1))
        return n if 1 <= n <= 9999 else None

    # Method 1: text-layer spans. Collect all candidates in the corner,
    # then prefer the right-most (largest x0) — that one wins over any
    # header digit that strayed into the same y-band.
    candidates = []
    for span in page.get("spans", []):
        text = span["text"]
        if not text.strip():
            continue
        bbox = span.get("bbox", [0, 0, 0, 0])
        x0, y0 = bbox[0], bbox[1]

        if y0 > top_limit:
            continue
        if x0 < right_limit:
            continue

        n = _bare_digit(text)
        if n is None:
            continue
        candidates.append((x0, y0, n))

    if candidates:
        # Top-most wins (smallest y0). The actual page number sits at the
        # very top edge; body-text fragments that survive the corner filter
        # (e.g. "Start Time: 13 Mar 2025" → "5,") are always lower in y.
        # Tie-break by right-most (largest x0).
        candidates.sort(key=lambda c: (c[1], -c[0]))
        return candidates[0][2]

    # Method 2: OCR'd pages have no spans — fall back to first lines of text.
    # ONLY runs for pages where Tesseract was used (text layer was missing).
    # For pages with a (possibly garbage) text layer, trust Method 1's null
    # result rather than risk matching mangled body fragments like "1//-".
    if page.get("ocr_used"):
        text = page["text"].strip()
        if text:
            for line in text.split('\n')[:4]:
                n = _bare_digit(line)
                if n is not None:
                    return n

    return None


def extract_pages(file_path: str) -> dict:
    """Extract text from each page using PyMuPDF + Tesseract OCR fallback."""
    if not fitz:
        return {"ok": False, "error": "PyMuPDF not installed: pip install pymupdf"}

    try:
        doc = fitz.open(file_path)
        pages = []
        ocr_pages = []
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

            page_data = {
                "page_num": i + 1,
                "text": text,
                "text_upper": text.upper(),
                "spans": spans,
                "width": page.rect.width,
                "height": page.rect.height,
                "ocr_used": False,
            }
            pages.append(page_data)

            if len(text.strip()) < MIN_TEXT_CHARS:
                ocr_pages.append(i)

        # Full-page Tesseract OCR for every scanned page
        if ocr_pages and TESSERACT_AVAILABLE:
            print(f"{len(ocr_pages)} pages need OCR — running full-page Tesseract...", file=sys.stderr)
            ocr_method = "pymupdf+tesseract"

            for idx, page_idx in enumerate(ocr_pages):
                page_num = page_idx + 1
                if idx % 10 == 0:
                    print(f"  OCR progress {idx+1}/{len(ocr_pages)}...", file=sys.stderr)
                ocr_text = _ocr_page_with_tesseract(doc, page_idx, page_num)
                if ocr_text:
                    pages[page_idx]["text"] = ocr_text
                    pages[page_idx]["text_upper"] = ocr_text.upper()
                    pages[page_idx]["ocr_used"] = True

            ocr_success = sum(1 for p in pages if p["ocr_used"])
            print(f"Tesseract OCR done: {ocr_success}/{len(ocr_pages)} pages", file=sys.stderr)
        elif ocr_pages:
            print(f"{len(ocr_pages)} pages need OCR but Tesseract not available. Install: pip install pytesseract", file=sys.stderr)

        # Pre-compute printed page number (top-right) for each page.
        # Pass 1 — text-layer / full-OCR strict reader.
        # Pass 2 — for pages still unnumbered, OCR just the top-right strip.
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
            print(f"Top-right strip OCR for {len(needing_strip)} pages still unnumbered...", file=sys.stderr)
            filled = 0
            for idx_i, page_idx in enumerate(needing_strip):
                if idx_i % 25 == 0:
                    print(f"  strip-OCR progress {idx_i+1}/{len(needing_strip)}...", file=sys.stderr)
                n = _ocr_topright_page_number(doc, page_idx)
                if n and n <= max_plausible:
                    pages[page_idx]["printed_num"] = n
                    filled += 1
            print(f"Strip OCR filled {filled}/{len(needing_strip)} page numbers.", file=sys.stderr)

        full_text = "\n".join(p["text"] for p in pages)
        doc.close()

        return {
            "ok": True,
            "pages": pages,
            "full_text": full_text.strip(),
            "total_pages": len(pages),
            "ocr_method": ocr_method,
        }
    except Exception as e:
        return {"ok": False, "error": f"PDF extraction failed: {e}"}


# =============================================================================
# Rule 1 — Required document sections present
# =============================================================================

def check_rule_doc_upload(pages: list) -> dict:
    """All required sections must be present in the filing."""
    required_docs = {
        "Appeal/Petition": ["APPEAL", "PETITION", "COMP. APP", "COMPANY APPEAL"],
        "Impugned Order": ["IMPUGNED ORDER", "CERTIFIED COPY", "ORDER DATED", "ORDER DT"],
        "Affidavit": ["AFFIDAVIT", "SOLEMNLY AFFIRM", "DEPONENT", "SWORN"],
        "Vakalatnama": ["VAKALATNAMA", "VAKALAT", "MEMO OF APPEARANCE", "POWER OF ATTORNEY"],
        "Index": ["INDEX", "TABLE OF CONTENTS", "PARTICULARS"],
        "Annexures": ["ANNEXURE", "EXHIBIT", "ANNEXURE-"],
    }

    found = []
    missing = []
    found_details = []
    for doc_name, keywords in required_docs.items():
        found_on = []
        for page in pages:
            if any(kw in page["text_upper"] for kw in keywords):
                found_on.append(page["page_num"])
        if found_on:
            found.append(doc_name)
            found_details.append({"doc": doc_name, "pages": found_on[:3]})
        else:
            missing.append(doc_name)

    if not missing:
        detail_str = "; ".join(
            f"{d['doc']} (page {', '.join(str(p) for p in d['pages'])})"
            for d in found_details
        )
        return {
            "rule_id": "DOC_UPLOAD",
            "status": "pass",
            "severity": "high",
            "description": "All required documents must be in your filing",
            "detail": f"Good — all required documents are present. Found: {detail_str}.",
            "found": found,
        }

    return {
        "rule_id": "DOC_UPLOAD",
        "status": "fail" if len(missing) > 1 else "warning",
        "severity": "high",
        "description": "All required documents must be in your filing",
        "detail": (
            f"Some required documents are missing. Please add: {', '.join(missing)}. "
            f"You already have: {', '.join(found) if found else 'none'}."
        ),
        "missing": missing,
        "found": found,
    }


# =============================================================================
# Rule 2 — Sequential top-right pagination after user-supplied index range
# =============================================================================

def check_rule_pagination(pages: list, index_end_page: int) -> dict:
    """Strict sequential pagination after the index.

    Args:
        pages: list of extracted pages.
        index_end_page: 1-indexed last physical page of the index. Pages
            1..index_end_page are skipped from the check. Page (index_end_page + 1)
            must show "1" top-right, the next must show "2", and so on.
            Pass 0 to start checking from page 1 of the document.
    """
    total = len(pages)
    if total == 0:
        return {
            "rule_id": "PAGINATION",
            "status": "info",
            "severity": "high",
            "description": "Page numbers must run in sequence after the index",
            "detail": "Document has no pages.",
        }

    if index_end_page < 0 or index_end_page >= total:
        return {
            "rule_id": "PAGINATION",
            "status": "fail",
            "severity": "high",
            "description": "Page numbers must run in sequence after the index",
            "detail": (
                f"Index end page is invalid. You said the index ends at page {index_end_page}, "
                f"but the document has {total} page(s). Please give a value between 0 and {total - 1}."
            ),
        }

    paginated = pages[index_end_page:]   # 0-indexed slice — skips first N pages
    skipped = index_end_page
    expected_total = len(paginated)

    missing_pages = []   # physical pages with no top-right digit
    wrong_pages = []     # printed digit mismatches expected sequence value
    seen_numbers: Dict[int, List[int]] = {}

    for i, page in enumerate(paginated):
        expected = i + 1
        printed = page.get("printed_num")
        physical = page["page_num"]

        if printed is None:
            missing_pages.append({"physical": physical, "expected": expected})
        else:
            if printed != expected:
                wrong_pages.append({
                    "physical": physical,
                    "expected": expected,
                    "actual": printed,
                })
            seen_numbers.setdefault(printed, []).append(physical)

    duplicates = {n: phys for n, phys in seen_numbers.items() if len(phys) > 1}

    issues = []
    refs: List[Dict[str, int]] = []

    if missing_pages:
        miss_list = ", ".join(str(m["physical"]) for m in missing_pages[:15])
        if len(missing_pages) > 15:
            miss_list += f" and {len(missing_pages) - 15} more"
        issues.append(
            f"{len(missing_pages)} page(s) have no digit in the top-right corner "
            f"(physical pages: {miss_list}). Please write the page number on each."
        )
        refs.extend({"page_num": m["physical"]} for m in missing_pages[:15])

    if wrong_pages:
        wrong_list = "; ".join(
            f"page {w['physical']} shows '{w['actual']}' (should be '{w['expected']}')"
            for w in wrong_pages[:15]
        )
        if len(wrong_pages) > 15:
            wrong_list += f"; and {len(wrong_pages) - 15} more"
        issues.append(f"{len(wrong_pages)} page(s) have the wrong number — {wrong_list}.")
        refs.extend({"page_num": w["physical"]} for w in wrong_pages[:15])

    if duplicates:
        dup_list = "; ".join(
            f"'{n}' on physical pages {', '.join(str(p) for p in phys)}"
            for n, phys in sorted(duplicates.items())[:10]
        )
        issues.append(f"Same digit appears on multiple pages — {dup_list}.")
        refs.extend({"page_num": phys[0]} for n, phys in sorted(duplicates.items())[:10])

    header = (
        f"We skipped pages 1 to {skipped} (the index you marked). "
        f"Pages {skipped + 1} to {total} should show top-right digits "
        f"in sequence: 1, 2, 3, …, {expected_total}."
    )

    if issues:
        return {
            "rule_id": "PAGINATION",
            "status": "fail",
            "severity": "high",
            "description": "Page numbers must run in sequence after the index",
            "detail": header + " Problems: " + " ".join(issues),
            "page_references": refs,
        }

    return {
        "rule_id": "PAGINATION",
        "status": "pass",
        "severity": "high",
        "description": "Page numbers must run in sequence after the index",
        "detail": header + f" All {expected_total} page(s) numbered correctly.",
    }


# =============================================================================
# Write pagination — print "1, 2, 3, …" in top-right of each post-index page
# =============================================================================

def write_pagination(input_path: str, output_path: str, index_end_page: int) -> bool:
    """Stamp sequential page numbers in the top-right corner of every page
    after the user-supplied index range.

    Page (index_end_page + 1) gets "1", the next gets "2", and so on. Pages
    1..index_end_page are left untouched.

    Whatever already sits in the top-right corner — a stale page number,
    a registry stamp, a date, a header digit — gets masked under a white
    rectangle BEFORE we stamp the new digit, so the new number is the
    sole content in that zone. Necessary because some volumes already
    carry incorrect numbering that we don't want bleeding through.

    Args:
        input_path: source PDF.
        output_path: where to save the numbered PDF.
        index_end_page: 1-indexed last page of the index. Numbering starts
            on page (index_end_page + 1).

    Returns True on success.
    """
    if not fitz:
        return False
    try:
        doc = fitz.open(input_path)
        total = len(doc)
        if index_end_page < 0 or index_end_page >= total:
            print(f"write_pagination: invalid index_end_page {index_end_page} for {total}-page doc",
                  file=sys.stderr)
            doc.close()
            return False

        FONTSIZE = 12
        FONTNAME = "helv"  # built-in Helvetica
        TOP_MARGIN = 28    # pt from top edge to baseline-ish
        RIGHT_MARGIN = 36  # pt from right edge to right-most glyph

        for i in range(index_end_page, total):
            page = doc[i]
            number = i - index_end_page + 1
            text = str(number)

            w = page.rect.width
            h = page.rect.height

            # 1. Mask the entire top-right zone with white. Sized to cover
            #    the detector's reading region (top 6% × right 25%) — this
            #    way any pre-existing digit in the corner is wiped before
            #    we stamp the new one.
            mask = fitz.Rect(w * 0.75, 0, w, h * 0.06)
            page.draw_rect(mask, color=None, fill=(1, 1, 1))

            # 2. Stamp the new digit, right-aligned within the masked zone.
            text_width = fitz.get_text_length(text, fontsize=FONTSIZE, fontname=FONTNAME)
            x = w - RIGHT_MARGIN - text_width
            y = TOP_MARGIN
            # insert_text anchors at baseline; nudge down by fontsize so the
            # glyph sits cleanly inside TOP_MARGIN.
            page.insert_text(
                fitz.Point(x, y + FONTSIZE),
                text,
                fontsize=FONTSIZE,
                fontname=FONTNAME,
                color=(0, 0, 0),
            )

        doc.save(output_path, garbage=3, deflate=True)
        doc.close()
        return True
    except Exception as e:
        print(f"write_pagination error: {e}", file=sys.stderr)
        return False


# =============================================================================
# Annotated PDF generator
# =============================================================================

def generate_annotated_pdf(input_path: str, output_path: str, results: list) -> bool:
    """Generate a PDF with sticky-note annotations on pages with errors."""
    if not fitz:
        return False

    try:
        doc = fitz.open(input_path)

        if len(doc) > 0:
            errors = [r for r in results if r["status"] == "fail"]
            warnings = [r for r in results if r["status"] == "warning"]
            passed = [r for r in results if r["status"] == "pass"]

            summary = f"APPEAL DOCUMENT SCAN\n{'=' * 40}\n"
            summary += f"Errors: {len(errors)} | Warnings: {len(warnings)} | Passed: {len(passed)}\n\n"
            for r in results:
                icon = "FAIL" if r["status"] == "fail" else "WARN" if r["status"] == "warning" else "PASS"
                summary += f"[{icon}] {r['rule_id']}: {r['description']}\n"
                summary += f"  {r['detail'][:150]}\n\n"

            page = doc[0]
            annot = page.add_text_annot(fitz.Point(10, 10), summary, icon="Note")
            annot.set_colors(stroke=COLOR_ERROR if errors else COLOR_PASS)
            annot.set_info(title="Appeal Document Scan", content=summary)
            annot.update()

        for result in results:
            if result["status"] != "fail":
                continue
            for ref in result.get("page_references", [])[:30]:
                pn = ref.get("page_num", 0)
                if 1 <= pn <= len(doc):
                    page = doc[pn - 1]
                    page.draw_rect(fitz.Rect(0, 0, page.rect.width, 4), color=None, fill=COLOR_ERROR)
                    note = f"{result['rule_id']}\n{result['description']}\n\n{result['detail'][:200]}"
                    annot = page.add_text_annot(
                        fitz.Point(page.rect.width - 50, 10), note, icon="Comment")
                    annot.set_colors(stroke=COLOR_ERROR)
                    annot.set_info(title=f"Error: {result['rule_id']}", content=note)
                    annot.update()

        doc.save(output_path)
        doc.close()
        return True
    except Exception as e:
        print(f"Annotated PDF error: {e}", file=sys.stderr)
        return False


# =============================================================================
# Main pipeline
# =============================================================================

def run_full_analysis(file_path: str, index_end_page: int, mode: str = "detect") -> dict:
    """Run the analysis pipeline.

    mode:
      "detect"  — full text extraction + rule checks + annotated PDF.
                  No numbered PDF.
      "write"   — fast path. Skips extraction and rules entirely; only
                  stamps sequential digits in the top-right corner of
                  every post-index page. Returns the numbered PDF as
                  base64. Use this for high-throughput numbering when
                  the user has already verified the document or simply
                  wants pages stamped without analysis.
      "both"    — detect + write. Same as "detect" plus the numbered
                  PDF in `paginated_pdf`.
    """
    if mode not in {"detect", "write", "both"}:
        return {"ok": False, "error": f"Invalid mode: {mode!r}. Must be detect, write, or both."}

    output_dir = tempfile.mkdtemp(prefix="appeal-scan-")
    base_name = os.path.basename(file_path)

    # Pass-through of the merged source — frontend offers a "Download Merged PDF"
    # button that uses this. Cheap to read either way.
    merged_b64 = None
    try:
        with open(file_path, "rb") as f:
            merged_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        print(f"Could not read merged source for passthrough: {e}", file=sys.stderr)

    # ---- Fast path: write-only. No text extraction, no rules. -------------
    if mode == "write":
        # Need total pages to validate index_end_page, but a quick fitz.open
        # gives us that without running OCR or text extraction.
        if not fitz:
            return {"ok": False, "error": "PyMuPDF not installed: pip install pymupdf"}
        try:
            d = fitz.open(file_path)
            total_pages = len(d)
            d.close()
        except Exception as e:
            return {"ok": False, "error": f"Could not open PDF: {e}"}

        paginated_file = os.path.join(output_dir, f"NUMBERED_{base_name}")
        paginated_b64 = None
        if write_pagination(file_path, paginated_file, index_end_page):
            with open(paginated_file, "rb") as f:
                paginated_b64 = base64.b64encode(f.read()).decode("ascii")
            print(f"Numbered PDF saved: {paginated_file}", file=sys.stderr)
        else:
            return {"ok": False, "error": "Failed to stamp page numbers."}

        return {
            "ok": True,
            "mode": "write",
            "summary": {
                "document_type": "appeal",
                "total_pages": total_pages,
                "total_rules_checked": 0,
                "errors_count": 0,
                "warnings_count": 0,
                "passed_count": 0,
                "info_count": 0,
                "compliance_score": 0,
                "index_end_page": index_end_page,
            },
            "errors": [], "warnings": [], "passed": [], "info": [],
            "all_results": [],
            "ocr_method": "n/a",
            "file": base_name,
            "annotated_pdf": None,
            "merged_pdf": merged_b64,
            "paginated_pdf": paginated_b64,
        }

    # ---- Detect (and optionally write) -----------------------------------
    print(f"Extracting text from: {file_path}", file=sys.stderr)
    extraction = extract_pages(file_path)
    if not extraction["ok"]:
        return {"ok": False, "error": extraction["error"]}

    pages = extraction["pages"]
    total_pages = extraction["total_pages"]
    ocr_method = extraction.get("ocr_method", "pymupdf")

    print(f"Extracted {total_pages} pages (method: {ocr_method})", file=sys.stderr)
    print(f"Index end page: {index_end_page}", file=sys.stderr)

    print("Running rule scan...", file=sys.stderr)
    all_results = [
        check_rule_doc_upload(pages),
        check_rule_pagination(pages, index_end_page),
    ]

    errors = [r for r in all_results if r["status"] == "fail"]
    warnings = [r for r in all_results if r["status"] == "warning"]
    passed = [r for r in all_results if r["status"] == "pass"]
    info = [r for r in all_results if r["status"] == "info"]

    for r in all_results:
        icon = "FAIL" if r["status"] == "fail" else "WARN" if r["status"] == "warning" else "PASS"
        print(f"  [{icon}] {r['rule_id']}: {r['detail'][:100]}", file=sys.stderr)

    annotated_b64 = None
    paginated_b64 = None
    annotated_file = os.path.join(output_dir, f"ERRORS_MARKED_{base_name}")
    paginated_file = os.path.join(output_dir, f"NUMBERED_{base_name}")

    if generate_annotated_pdf(file_path, annotated_file, all_results):
        with open(annotated_file, "rb") as f:
            annotated_b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"Annotated PDF saved: {annotated_file}", file=sys.stderr)

    if mode == "both":
        if write_pagination(file_path, paginated_file, index_end_page):
            with open(paginated_file, "rb") as f:
                paginated_b64 = base64.b64encode(f.read()).decode("ascii")
            print(f"Numbered PDF saved: {paginated_file}", file=sys.stderr)
        else:
            print("Numbered PDF generation failed", file=sys.stderr)

    total_checkable = len(errors) + len(warnings) + len(passed)
    compliance = round((len(passed) / total_checkable * 100) if total_checkable else 0, 1)

    return {
        "ok": True,
        "mode": mode,
        "summary": {
            "document_type": "appeal",
            "total_pages": total_pages,
            "total_rules_checked": len(all_results),
            "errors_count": len(errors),
            "warnings_count": len(warnings),
            "passed_count": len(passed),
            "info_count": len(info),
            "compliance_score": compliance,
            "index_end_page": index_end_page,
        },
        "errors": errors,
        "warnings": warnings,
        "passed": passed,
        "info": info,
        "all_results": all_results,
        "ocr_method": ocr_method,
        "file": base_name,
        "annotated_pdf": annotated_b64,
        "merged_pdf": merged_b64,
        "paginated_pdf": paginated_b64,
    }


# =============================================================================
# Multi-volume merge
# =============================================================================

def merge_pdfs(input_paths: list, output_path: str) -> bool:
    """Concatenate multiple PDFs in order. Annotations preserved."""
    if not fitz or not input_paths:
        return False
    try:
        out = fitz.open()
        for p in input_paths:
            src = fitz.open(p)
            out.insert_pdf(src)
            src.close()
        out.save(output_path)
        out.close()
        return True
    except Exception as e:
        print(f"Merge error: {e}", file=sys.stderr)
        return False


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Appeal Document Scanner")
    parser.add_argument("--file", action="append",
                        help="Path to PDF (repeat for multiple volumes, processed in order)")
    parser.add_argument("--index-end-page", type=int, default=0,
                        help="1-indexed last page of the index. Pages 1..N are skipped from "
                             "the pagination check (0 = no skip).")
    parser.add_argument("--mode", choices=("detect", "write", "both"), default="detect",
                        help="detect: rule check only. write: stamp page numbers only "
                             "(skips text extraction + rules — much faster). both: do both.")
    args = parser.parse_args()

    if not args.file:
        parser.error("at least one --file is required")

    if len(args.file) == 1:
        target_path = args.file[0]
    else:
        tmp = tempfile.NamedTemporaryFile(suffix="_merged.pdf", delete=False)
        tmp.close()
        if not merge_pdfs(args.file, tmp.name):
            print(json.dumps({"ok": False, "error": "Failed to merge input PDFs"}))
            return
        target_path = tmp.name
        print(f"Merged {len(args.file)} PDFs -> {target_path}", file=sys.stderr)

    report = run_full_analysis(target_path, args.index_end_page, args.mode)
    if len(args.file) > 1:
        report["file"] = " + ".join(os.path.basename(f) for f in args.file)

    # Print the FULL report (with base64 fields) — server.ts captures stdout
    # and forwards the parsed JSON to the frontend. Direct CLI users can still
    # access the saved files printed below.
    print(json.dumps(report, ensure_ascii=False))

    # Convenience: also write the PDFs to the user's filesystem alongside the
    # input. Doesn't mutate the JSON above.
    annotated = report.get("annotated_pdf")
    paginated = report.get("paginated_pdf")
    if annotated:
        out_path = args.file[0].rsplit(".", 1)[0] + "_ERRORS_MARKED.pdf"
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(annotated))
        print(f"Annotated PDF saved to: {out_path}", file=sys.stderr)
    if paginated:
        out_path = args.file[0].rsplit(".", 1)[0] + "_NUMBERED.pdf"
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(paginated))
        print(f"Numbered PDF saved to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
