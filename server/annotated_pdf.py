"""Annotated-PDF generator for the dormant detect mode.

Drops a summary sticky-note on page 1 and a red bar + per-rule comment
on every page flagged as failing. Used by `run_full_analysis` only when
mode is `detect` or `both` — the production write-only path never calls
this.

Pure module split — no logic changes.
"""

import sys
from typing import List

from config import COLOR_ERROR, COLOR_PASS, fitz


def generate_annotated_pdf(input_path: str, output_path: str, results: List[dict]) -> bool:
    """Generate a PDF with sticky-note annotations on pages with errors."""
    if not fitz:
        return False

    doc = None
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
                    note = (
                        f"{result['rule_id']}\n{result['description']}\n\n"
                        f"{result['detail'][:200]}"
                    )
                    annot = page.add_text_annot(
                        fitz.Point(page.rect.width - 50, 10), note, icon="Comment"
                    )
                    annot.set_colors(stroke=COLOR_ERROR)
                    annot.set_info(title=f"Error: {result['rule_id']}", content=note)
                    annot.update()

        doc.save(output_path)
        return True
    except Exception as e:
        print(f"Annotated PDF error: {e}", file=sys.stderr)
        return False
    finally:
        if doc is not None:
            doc.close()
