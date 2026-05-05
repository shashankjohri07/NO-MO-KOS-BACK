"""Pagination — both write and detect halves.

write_pagination()    redacts whatever sits in the top-right corner of each
                      post-index page and stamps a fresh sequential digit
                      (1, 2, 3, ...). Redaction (not just visual mask)
                      because we need the underlying text spans gone — our
                      own detector would otherwise read the stale digits.

check_rule_pagination()  read-only check for the dormant detect mode.
                         Compares each page's pre-extracted printed_num
                         against the expected sequence value.

Pure module split — no logic changes.
"""

import sys
from typing import Dict, List

from config import fitz


def write_pagination(input_path: str, output_path: str, index_end_page: int) -> bool:
    """Stamp sequential page numbers in the top-right corner of every page
    after the user-supplied index range.

    Page (index_end_page + 1) gets "1", the next gets "2", and so on.
    Pages 1..index_end_page are left untouched.

    Existing top-right content (stale numbers, registry stamps, dates, etc.)
    is REDACTED — text spans are deleted from the content stream before
    the new digit is drawn. A plain white draw_rect would only hide them
    visually; the detector would still read the underlying spans.
    """
    if not fitz:
        return False
    doc = None
    try:
        doc = fitz.open(input_path)
        total = len(doc)
        if index_end_page < 0 or index_end_page >= total:
            print(
                f"write_pagination: invalid index_end_page {index_end_page} "
                f"for {total}-page doc",
                file=sys.stderr,
            )
            return False

        FONTSIZE = 12
        FONTNAME = "helv"
        TOP_MARGIN = 28
        RIGHT_MARGIN = 36

        # Pass 1: redact top-right zone on every post-index page. We can't
        # interleave redactions and inserts because apply_redactions
        # invalidates page objects.
        for i in range(index_end_page, total):
            page = doc[i]
            w = page.rect.width
            h = page.rect.height
            mask = fitz.Rect(w * 0.75, 0, w, h * 0.06)
            page.add_redact_annot(mask, fill=(1, 1, 1))
            page.apply_redactions()

        # Pass 2: stamp the fresh digit.
        for i in range(index_end_page, total):
            page = doc[i]
            number = i - index_end_page + 1
            text = str(number)
            w = page.rect.width
            text_width = fitz.get_text_length(text, fontsize=FONTSIZE, fontname=FONTNAME)
            x = w - RIGHT_MARGIN - text_width
            y = TOP_MARGIN
            page.insert_text(
                fitz.Point(x, y + FONTSIZE),
                text,
                fontsize=FONTSIZE,
                fontname=FONTNAME,
                color=(0, 0, 0),
            )

        doc.save(output_path, garbage=3, deflate=True)
        return True
    except Exception as e:
        print(f"write_pagination error: {e}", file=sys.stderr)
        return False
    finally:
        if doc is not None:
            doc.close()


def check_rule_pagination(pages: list, index_end_page: int) -> dict:
    """Strict sequential pagination check.

    Args:
        pages: list of extracted pages with `printed_num` populated.
        index_end_page: 1-indexed last physical page of the index. Pages
            1..index_end_page are skipped from the check. Page
            (index_end_page + 1) must show "1" top-right, the next "2",
            and so on. Pass 0 to start checking from page 1.
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

    paginated = pages[index_end_page:]
    skipped = index_end_page
    expected_total = len(paginated)

    missing_pages = []
    wrong_pages = []
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
