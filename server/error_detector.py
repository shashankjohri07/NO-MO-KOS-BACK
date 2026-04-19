#!/usr/bin/env python3
"""
Appeal Document Scanner — checks 5 critical rules using Tesseract OCR.

Rules:
  1. Draft check — document must not be a draft
  2. Document upload — all required docs must be present
  3. Pagination — sequential page numbers, no duplicates
  4. Sign and stamp on Annexures
  5. True Copy on Annexures
  6. Index page number entries (if possible)

Usage:
    python server/error_detector.py --file /path/to/document.pdf
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
COLOR_WARNING = (1, 0.7, 0.1)     # Orange
COLOR_PASS = (0.2, 0.7, 0.3)      # Green


# =============================================================================
# Tesseract OCR
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
    """OCR only the top-right strip of a page to read the printed page number.

    Much faster and more accurate than full-page OCR for this specific task:
    crops the top ~12% × right 40% of the page, upscales, runs digit-only
    Tesseract in single-line mode.
    """
    if not TESSERACT_AVAILABLE or not fitz:
        return None
    try:
        page = doc[page_idx]
        rect = page.rect
        # Top-right crop: y in [0, 12%], x in [55%, 100%]
        crop = fitz.Rect(
            rect.width * 0.55,
            0,
            rect.width,
            rect.height * 0.12,
        )
        mat = fitz.Matrix(TESSERACT_PAGENUM_DPI / 72, TESSERACT_PAGENUM_DPI / 72)
        pix = page.get_pixmap(matrix=mat, clip=crop)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img, config=TESSERACT_CONFIG_SINGLE_LINE)
        # Take the first 1-4 digit run
        m = re.search(r"\b(\d{1,4})\b", text or "")
        if not m:
            return None
        num = int(m.group(1))
        if 1 <= num <= 9999:
            return num
        return None
    except Exception as e:
        print(f"  Top-right OCR error page {page_idx+1}: {e}", file=sys.stderr)
        return None


# =============================================================================
# Text Extraction
# =============================================================================

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

        # Full-page Tesseract OCR for every scanned page (no cap).
        if ocr_pages and TESSERACT_AVAILABLE:
            print(f"{len(ocr_pages)} pages need OCR — running full-page Tesseract on all of them...", file=sys.stderr)
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
        # Two-pass strategy so scanned PDFs work reliably:
        #   Pass 1 — text-layer / full-OCR heuristic (existing extractor).
        #   Pass 2 — for pages that still have no number, OCR just the top-right
        #            strip at high DPI with a digits-only whitelist. Much
        #            faster and more accurate than full-page OCR for numerals.
        # Drop OCR-noise values (numbers > total pages * 2) as they are almost
        # certainly misreads (e.g. year "2020" in a running header).
        max_plausible = max(len(pages) * 2, 100)
        needing_strip = []
        for p in pages:
            n = _extract_printed_page_number(p)
            if n and n <= max_plausible:
                p["printed_num"] = n
            else:
                p["printed_num"] = None
                needing_strip.append(p["page_num"] - 1)  # physical index

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
# The 5 Rules
# =============================================================================

def _label(page: dict) -> Optional[str]:
    """Printed (top-right) page number if detected, else None."""
    n = page.get("printed_num")
    return str(n) if n else None


def _labels(page_nums: list, pages: list) -> list:
    """Return printed page numbers only. Pages without a printed number are dropped."""
    idx = {p["page_num"]: p for p in pages}
    out = []
    for n in page_nums:
        p = idx.get(n)
        if p and p.get("printed_num"):
            out.append(str(p["printed_num"]))
    return out


def _count_unnumbered(page_nums: list, pages: list) -> int:
    idx = {p["page_num"]: p for p in pages}
    return sum(1 for n in page_nums if n in idx and not idx[n].get("printed_num"))


def _printed_refs(page_nums: list, pages: list) -> list:
    """Build page_references using printed (top-right) page numbers only."""
    idx = {p["page_num"]: p for p in pages}
    out = []
    seen = set()
    for n in page_nums:
        p = idx.get(n)
        pn = p.get("printed_num") if p else None
        if pn and pn not in seen:
            seen.add(pn)
            out.append({"page_num": pn})
    return out


def check_rule_1_draft(pages: list, full_text_upper: str) -> dict:
    """Rule 1: Document must NOT be in draft state."""
    draft_pages = []
    for page in pages:
        if re.search(r"\bDRAFT\b", page["text_upper"]):
            draft_pages.append(page["page_num"])
        for span in page.get("spans", []):
            if "DRAFT" in span["text"].upper() and span.get("size", 0) > 20:
                if page["page_num"] not in draft_pages:
                    draft_pages.append(page["page_num"])

    if draft_pages:
        labels = _labels(draft_pages, pages)
        unnum = _count_unnumbered(draft_pages, pages)
        page_list = ', '.join(labels[:10]) if labels else '(pages without printed numbers)'
        detail = f"The word 'DRAFT' is written on your document. Please remove it from page(s): {page_list}"
        if unnum:
            detail += f" and {unnum} more page(s) that are not numbered"
        detail += ". The court only accepts the final version — not a draft."
        return {
            "rule_id": "DRAFT_CHECK",
            "status": "fail",
            "severity": "high",
            "description": "Your document should not have the word 'DRAFT' anywhere",
            "detail": detail,
            "page_references": _printed_refs(draft_pages, pages),
        }
    return {
        "rule_id": "DRAFT_CHECK",
        "status": "pass",
        "severity": "high",
        "description": "Your document should not have the word 'DRAFT' anywhere",
        "detail": "Good — the word 'DRAFT' was not found anywhere in your document. This looks like a final version.",
    }


def check_rule_2_doc_upload(pages: list, full_text_upper: str) -> dict:
    """Rule 2: All required documents must be uploaded/present."""
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
            f"{d['doc']} (on page {', '.join(_labels(d['pages'], pages)) or 'not numbered'})"
            for d in found_details
        )
        return {
            "rule_id": "DOC_UPLOAD",
            "status": "pass",
            "severity": "high",
            "description": "All required documents must be in your filing",
            "detail": f"Good — all required documents are present. Found: {detail_str}.",
        }
    else:
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


def _extract_printed_page_number(page: dict) -> Optional[int]:
    """Extract the printed page number written on the document (top-right corner).

    Reads the actual number printed on the doc — NOT the PDF page index.
    Looks at top-right area first (most common in court filings),
    then top-center, then falls back to first few lines of text.
    """
    page_width = page.get("width", 595)

    # Method 1: Use text span positions — find standalone numbers in top-right
    for span in page.get("spans", []):
        text = span["text"].strip()
        if not text:
            continue
        bbox = span.get("bbox", [0, 0, 0, 0])
        x, y = bbox[0], bbox[1]

        # Only look at TOP area (y < 100 pts ≈ top ~3.5cm)
        if y > 100:
            continue

        # Must be a standalone number (possibly with "Page" prefix or dashes)
        m = re.match(r'^[-–]?\s*(?:Page\s*(?:No\.?\s*)?)?\s*(\d{1,4})\s*[-–]?\s*$', text, re.IGNORECASE)
        if not m:
            continue

        num = int(m.group(1))
        if num < 1 or num > 9999:
            continue

        # Prefer top-right (x > 50% of page width)
        if x > page_width * 0.5:
            return num

        # Accept top-center too (x > 30%)
        if x > page_width * 0.3:
            return num

    # Method 2: For OCR'd pages (no spans), check the first 3 lines of text
    # Top-right content typically appears in the first lines after OCR
    text = page["text"].strip()
    if text:
        lines = text.split('\n')
        for line in lines[:4]:
            line = line.strip()
            m = re.match(r'^[-–]?\s*(?:Page\s*(?:No\.?\s*)?)?\s*(\d{1,4})\s*[-–]?\s*$', line, re.IGNORECASE)
            if m:
                num = int(m.group(1))
                if 1 <= num <= 9999:
                    return num

    return None


def check_rule_3_pagination(pages: list) -> dict:
    """Rule 3: Read printed page numbers from the document (top-right) and check for
    sequential numbering with no duplicates. This checks the DOCUMENT's own numbering,
    not the PDF page index."""
    if not pages:
        return {"rule_id": "PAGINATION", "status": "info", "severity": "high",
                "description": "Page numbering — no duplicate pagination", "detail": "No pages."}

    # Pagination begins where the document's own numbering starts — the first
    # page whose printed (top-right) number is 1. Pages before that (cover,
    # title, memo of parties, index, etc.) are front matter and intentionally
    # unnumbered — they must NOT be checked for pagination.
    start_idx = None
    for i, page in enumerate(pages):
        if page.get("printed_num") == 1:
            start_idx = i
            break

    if start_idx is None:
        return {
            "rule_id": "PAGINATION",
            "status": "warning",
            "severity": "high",
            "description": "Page numbers must be correct and in order",
            "detail": "We could not find page number '1' written at the top-right of any page. Page numbering should start with '1' on the first main page (after the cover and index). Please check your page numbers.",
        }

    # Walk forward and detect the end of the paginated zone. Two cases we need
    # to handle correctly:
    #   (a) Continuous filings: body ends at 10, Annexure A-1 is 11, A-2 is 12.
    #       The zone should INCLUDE the annexures so the whole 1..12 is checked.
    #   (b) Old-style filings: body ends at ~8, then annexures have their own
    #       internal numbering (e.g. certified-order pages reading 21, 52, 175-C).
    #       The zone should STOP at the body so that annexure numbers aren't
    #       flagged as duplicates/gaps against the body.
    # Heuristic: keep extending the zone while the next printed number stays
    # "close" to the running maximum (ascending by at most a small step, or
    # revisiting an earlier number which will be flagged as a duplicate). A
    # large forward jump signals a separate numbering scheme — stop there.
    MAX_FORWARD_JUMP = 3
    max_seen = 0
    zone_end = start_idx
    for i in range(start_idx, len(pages)):
        pn = pages[i].get("printed_num")
        if pn is None:
            zone_end = i + 1
            continue
        if pn > max_seen + MAX_FORWARD_JUMP:
            # Big forward jump — likely the start of an annexure section with
            # its own independent numbering. End the zone BEFORE this page.
            break
        if pn > max_seen:
            max_seen = pn
        zone_end = i + 1

    paginated = pages[start_idx:zone_end]
    front_matter_count = start_idx
    annexure_tail_count = len(pages) - zone_end

    page_numbers_found = {}  # printed_number -> [physical_pages]
    pages_without_number = []

    for page in paginated:
        printed_num = page.get("printed_num")
        if printed_num is not None:
            page_numbers_found.setdefault(printed_num, []).append(page["page_num"])
        else:
            pages_without_number.append(page["page_num"])

    # Check 1: Duplicate page numbers (same printed number on multiple pages)
    duplicates = {num: phys for num, phys in page_numbers_found.items() if len(phys) > 1}

    # Check 2: Sequence gaps — numbers missing between first and last detected
    detected_nums = sorted(page_numbers_found.keys())
    gaps = []
    if len(detected_nums) >= 2:
        for i in range(len(detected_nums) - 1):
            for missing in range(detected_nums[i] + 1, detected_nums[i + 1]):
                gaps.append(missing)

    total_paginated = len(paginated)
    detected = total_paginated - len(pages_without_number)

    issues = []
    refs = []

    if duplicates:
        dup_nums = sorted(duplicates.keys())
        dup_list = ", ".join(f"'{n}' (appears {len(duplicates[n])} times)" for n in dup_nums[:12])
        issues.append(
            f"Same page number is written on more than one page: {dup_list}. "
            "Every page must have a different number."
        )
        refs.extend({"page_num": n} for n in dup_nums[:12])

    if gaps:
        gap_list = ", ".join(str(g) for g in gaps[:20])
        if len(gaps) > 20:
            gap_list += f" and {len(gaps) - 20} more"
        issues.append(
            f"These page numbers are missing from your document: {gap_list}. "
            "The pages should go in order without skipping any number."
        )
        refs.extend({"page_num": n} for n in gaps[:20])

    # Build detail
    header = (
        f"We checked the page numbers written at the top-right of every page "
        f"(we ignored the first {front_matter_count} page(s) — cover and index, which don't need page numbers"
    )
    if annexure_tail_count:
        header += f"; and the last {annexure_tail_count} page(s) — annexures that use their own numbering, which shouldn't be compared with the main body"
    header += ")."
    if detected_nums:
        header += f" Your page numbers run from {detected_nums[0]} to {detected_nums[-1]}."

    if issues:
        status = "fail"
        detail = header + " Problem: " + " Also, ".join(issues) + " Please fix these before filing."
    elif len(pages_without_number) > total_paginated * 0.3:
        status = "warning"
        detail = header + f" Note: {len(pages_without_number)} page(s) in the main document don't have a page number written on them — please add page numbers."
    else:
        status = "pass"
        detail = header + " All page numbers are in order with no repeats and no missing numbers — good."

    return {
        "rule_id": "PAGINATION",
        "status": status,
        "severity": "high",
        "description": "Page numbers must be correct and in order",
        "detail": detail,
        "page_references": refs,
    }


def check_rule_4_sign_stamp_annexures(pages: list) -> dict:
    """Rule 4: Sign and stamp on Annexures."""
    annexure_start = None
    for page in pages:
        if re.search(r"ANNEXURE", page["text_upper"]):
            annexure_start = page["page_num"]
            break

    if annexure_start is None:
        return {
            "rule_id": "SIGN_STAMP_ANNEXURES",
            "status": "warning",
            "severity": "high",
            "description": "Every annexure page needs a signature and stamp",
            "detail": "We could not find any annexures in your document, so we cannot check this rule. If your filing does not need annexures, you can ignore this.",
        }

    annexure_pages = [p for p in pages if p["page_num"] >= annexure_start]
    sign_keywords = ["ADVOCATE", "COUNSEL", "SIGNED", "SD/-", "SD/", "SIGNATURE",
                      "AUTHORIZED REPRESENTATIVE", "AUTHORISED REPRESENTATIVE",
                      "TRUE COPY", "CERTIFIED"]
    pages_with = []
    pages_without = []

    for page in annexure_pages:
        has = any(kw in page["text_upper"] for kw in sign_keywords)
        if has:
            pages_with.append(page["page_num"])
        else:
            pages_without.append(page["page_num"])

    total = len(annexure_pages)
    coverage = len(pages_with) / total if total else 0

    if coverage > 0.6:
        return {
            "rule_id": "SIGN_STAMP_ANNEXURES",
            "status": "pass",
            "severity": "high",
            "description": "Every annexure page needs a signature and stamp",
            "detail": (
                f"Good — {len(pages_with)} out of {total} annexure pages look like they have a signature or stamp. "
                "Please double-check by eye that every annexure page is actually signed and stamped."
            ),
        }
    else:
        missing_pages = _labels(pages_without[:40], pages) or []
        unnum = _count_unnumbered(pages_without[:40], pages)
        miss_str = ', '.join(missing_pages) if missing_pages else 'on pages without printed numbers'
        if unnum and missing_pages:
            miss_str += f" and {unnum} more unnumbered page(s)"
        return {
            "rule_id": "SIGN_STAMP_ANNEXURES",
            "status": "fail",
            "severity": "high",
            "description": "Every annexure page needs a signature and stamp",
            "detail": (
                f"Only {len(pages_with)} out of {total} annexure pages have a signature or stamp. "
                f"Please sign and stamp these pages: {miss_str}. "
                "The court requires every annexure page to be signed and stamped."
            ),
            "page_references": _printed_refs(pages_without[:40], pages),
        }


def check_rule_5_true_copy_annexures(pages: list) -> dict:
    """Rule 5: True Copy on Annexures."""
    annexure_start = None
    for page in pages:
        if re.search(r"ANNEXURE", page["text_upper"]):
            annexure_start = page["page_num"]
            break

    if annexure_start is None:
        return {
            "rule_id": "TRUE_COPY_ANNEXURES",
            "status": "warning",
            "severity": "high",
            "description": "Every annexure page must say 'True Copy'",
            "detail": "We could not find any annexures in your document, so we cannot check this rule. If your filing does not need annexures, you can ignore this.",
        }

    annexure_pages = [p for p in pages if p["page_num"] >= annexure_start]
    tc_keywords = ["TRUE COPY", "CERTIFIED COPY", "CERTIFIED TRUE COPY"]
    pages_with = []
    pages_without = []

    for page in annexure_pages:
        has = any(kw in page["text_upper"] for kw in tc_keywords)
        if has:
            pages_with.append(page["page_num"])
        else:
            pages_without.append(page["page_num"])

    total = len(annexure_pages)
    coverage = len(pages_with) / total if total else 0

    if coverage > 0.7:
        return {
            "rule_id": "TRUE_COPY_ANNEXURES",
            "status": "pass",
            "severity": "high",
            "description": "Every annexure page must say 'True Copy'",
            "detail": f"Good — {len(pages_with)} out of {total} annexure pages have 'True Copy' written on them.",
        }
    missing_pages = _labels(pages_without[:40], pages) or []
    unnum = _count_unnumbered(pages_without[:40], pages)
    miss_str = ', '.join(missing_pages) if missing_pages else 'on pages without printed numbers'
    if unnum and missing_pages:
        miss_str += f" and {unnum} more unnumbered page(s)"
    if coverage > 0.2:
        return {
            "rule_id": "TRUE_COPY_ANNEXURES",
            "status": "warning",
            "severity": "high",
            "description": "Every annexure page must say 'True Copy'",
            "detail": (
                f"Only {len(pages_with)} out of {total} annexure pages have 'True Copy' written on them. "
                f"Please write 'True Copy' on these pages: {miss_str}. "
                "Every annexure page needs this, otherwise the court can reject your filing."
            ),
            "page_references": _printed_refs(pages_without[:40], pages),
        }
    return {
        "rule_id": "TRUE_COPY_ANNEXURES",
        "status": "fail",
        "severity": "high",
        "description": "Every annexure page must say 'True Copy'",
        "detail": (
            f"Only {len(pages_with)} out of {total} annexure pages have 'True Copy' written on them. "
            "The court will reject your filing unless you write 'True Copy' on every annexure page."
        ),
        "page_references": _printed_refs(pages_without[:40], pages),
    }


def check_rule_6_index_page_numbers(pages: list) -> dict:
    """Rule 6: Index must have page number entries (if possible)."""
    index_page = None
    index_text = ""

    for page in pages[:5]:
        if any(kw in page["text_upper"] for kw in ["INDEX", "TABLE OF CONTENTS", "PARTICULARS"]):
            index_page = page["page_num"]
            index_text = page["text"]
            break

    if not index_page:
        return {
            "rule_id": "INDEX_PAGE_NUMBERS",
            "status": "warning",
            "severity": "medium",
            "description": "The index should show a page number next to each item",
            "detail": "We could not find an index (table of contents) in the first 5 pages of your document. Please add an index that lists each part of your filing with its page number.",
        }

    page_ranges = re.findall(r"\b(\d{1,4})\s*[-–to]+\s*(\d{1,4})\b", index_text)
    line_nums = re.findall(r"(\d{1,4})\s*$", index_text, re.MULTILINE)
    total_refs = len(page_ranges) + len(line_nums)

    if total_refs >= 3:
        return {
            "rule_id": "INDEX_PAGE_NUMBERS",
            "status": "pass",
            "severity": "medium",
            "description": "The index should show a page number next to each item",
            "detail": f"Good — your index (on page {index_page}) has {total_refs} page numbers listed for the different items.",
        }
    elif total_refs >= 1:
        return {
            "rule_id": "INDEX_PAGE_NUMBERS",
            "status": "warning",
            "severity": "medium",
            "description": "The index should show a page number next to each item",
            "detail": f"Your index (on page {index_page}) only has {total_refs} page number(s) written next to items. Please add a page number next to every item listed in the index.",
        }
    else:
        return {
            "rule_id": "INDEX_PAGE_NUMBERS",
            "status": "fail",
            "severity": "medium",
            "description": "The index should show a page number next to each item",
            "detail": f"We found an index on page {index_page}, but it does not have any page numbers next to the items listed. Please write the page number next to every item (for example: 'Memo of Parties — page 1, Appeal — page 2').",
        }


# =============================================================================
# Annotated PDF Generator
# =============================================================================

def generate_annotated_pdf(input_path: str, output_path: str, results: list, pages: list) -> bool:
    """Generate PDF with error annotations marked on relevant pages."""
    if not fitz:
        return False

    try:
        doc = fitz.open(input_path)

        # Summary sticky note on page 1
        if len(doc) > 0:
            errors = [r for r in results if r["status"] == "fail"]
            warnings = [r for r in results if r["status"] == "warning"]
            passed = [r for r in results if r["status"] == "pass"]

            summary = f"APPEAL DOCUMENT SCAN RESULTS\n{'='*40}\n"
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

        # Mark pages with errors
        for result in results:
            if result["status"] != "fail":
                continue

            page_refs = result.get("page_references", [])
            for ref in page_refs[:30]:
                pn = ref.get("page_num", 0)
                if 1 <= pn <= len(doc):
                    page = doc[pn - 1]
                    # Red bar at top
                    page.draw_rect(fitz.Rect(0, 0, page.rect.width, 4), color=None, fill=COLOR_ERROR)
                    # Sticky note
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
# Main Pipeline
# =============================================================================

def run_full_analysis(file_path: str) -> dict:
    """Run the 5-rule analysis pipeline."""

    # Step 1: Extract text
    print(f"Extracting text from: {file_path}", file=sys.stderr)
    extraction = extract_pages(file_path)
    if not extraction["ok"]:
        return {"ok": False, "error": extraction["error"]}

    pages = extraction["pages"]
    full_text = extraction["full_text"]
    full_text_upper = full_text.upper()
    total_pages = extraction["total_pages"]
    ocr_method = extraction.get("ocr_method", "pymupdf")

    print(f"Extracted from {total_pages} pages (method: {ocr_method})", file=sys.stderr)

    # Step 2: Run the 5 rules
    print("Running 5-rule scan...", file=sys.stderr)
    all_results = [
        check_rule_1_draft(pages, full_text_upper),
        check_rule_2_doc_upload(pages, full_text_upper),
        check_rule_3_pagination(pages),
        check_rule_4_sign_stamp_annexures(pages),
        check_rule_5_true_copy_annexures(pages),
        check_rule_6_index_page_numbers(pages),
    ]

    errors = [r for r in all_results if r["status"] == "fail"]
    warnings = [r for r in all_results if r["status"] == "warning"]
    passed = [r for r in all_results if r["status"] == "pass"]
    info = [r for r in all_results if r["status"] == "info"]

    for r in all_results:
        icon = "FAIL" if r["status"] == "fail" else "WARN" if r["status"] == "warning" else "PASS"
        print(f"  [{icon}] {r['rule_id']}: {r['detail'][:100]}", file=sys.stderr)

    # Step 3: Generate annotated PDF + return the clean merged source
    annotated_b64 = None
    merged_b64 = None
    output_dir = tempfile.mkdtemp(prefix="appeal-scan-")
    annotated_file = os.path.join(output_dir, f"ERRORS_MARKED_{os.path.basename(file_path)}")

    # The input here is already the merged document when multiple volumes were
    # supplied — pass it through to the client so the user can download a
    # single combined PDF.
    try:
        with open(file_path, "rb") as f:
            merged_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        print(f"Could not read merged source for passthrough: {e}", file=sys.stderr)

    if generate_annotated_pdf(file_path, annotated_file, all_results, pages):
        with open(annotated_file, "rb") as f:
            annotated_b64 = base64.b64encode(f.read()).decode("ascii")
        print(f"Annotated PDF saved: {annotated_file}", file=sys.stderr)

    # Step 4: Build report
    total_checkable = len(errors) + len(warnings) + len(passed)
    compliance = round((len(passed) / total_checkable * 100) if total_checkable else 0, 1)

    return {
        "ok": True,
        "summary": {
            "document_type": "appeal",
            "total_pages": total_pages,
            "total_rules_checked": len(all_results),
            "errors_count": len(errors),
            "warnings_count": len(warnings),
            "passed_count": len(passed),
            "info_count": len(info),
            "compliance_score": compliance,
        },
        "errors": errors,
        "warnings": warnings,
        "passed": passed,
        "info": info,
        "all_results": all_results,
        "ocr_method": ocr_method,
        "file": os.path.basename(file_path),
        "annotated_pdf": annotated_b64,
        "merged_pdf": merged_b64,
    }


# =============================================================================
# CLI
# =============================================================================

def merge_pdfs(input_paths: list, output_path: str) -> bool:
    """Concatenate multiple PDFs in order. Annotations preserved.

    The detector then reads the combined document as one continuous volume,
    so pagination checks see the cross-volume sequence (e.g. Vol-1 ends at
    printed 199, Vol-2 must start at printed 200).
    """
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


def main():
    parser = argparse.ArgumentParser(description="Appeal Document Scanner — 6 Rule Check")
    parser.add_argument("--file", action="append", help="Path to PDF (repeat for multiple volumes, processed in order)")
    args = parser.parse_args()

    if not args.file:
        parser.error("at least one --file is required")

    if len(args.file) == 1:
        target_path = args.file[0]
    else:
        # Merge multiple volumes into one temp file, then analyze.
        tmp = tempfile.NamedTemporaryFile(suffix="_merged.pdf", delete=False)
        tmp.close()
        if not merge_pdfs(args.file, tmp.name):
            print(json.dumps({"ok": False, "error": "Failed to merge input PDFs"}))
            return
        target_path = tmp.name
        print(f"Merged {len(args.file)} PDFs -> {target_path}", file=sys.stderr)

    report = run_full_analysis(target_path)
    # Preserve the original filename(s) in the report
    if len(args.file) > 1:
        report["file"] = " + ".join(os.path.basename(f) for f in args.file)

    # Don't dump base64 PDF to stdout
    annotated = report.pop("annotated_pdf", None)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if annotated:
        base_for_output = args.file[0]
        out_path = base_for_output.rsplit(".", 1)[0] + "_ERRORS_MARKED.pdf"
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(annotated))
        print(f"\nAnnotated PDF saved to: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
