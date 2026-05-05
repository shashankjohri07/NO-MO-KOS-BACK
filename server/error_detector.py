#!/usr/bin/env python3
"""
Appeal Document Scanner — CLI orchestrator.

Two operational paths:

  --mode write       Fast path. Skips text extraction and rule checks.
                     Just merges main files, optionally appends annexures
                     (each annex file = one annexure, labelled "Annexure A-N",
                     with optional client/advocate signature stamping on
                     every annexure page), and stamps sequential top-right
                     digits across the whole document. Streaming-friendly
                     when paired with --write-stdout.

  --mode detect      Rule check only — runs the (dormant) DOC_UPLOAD and
  --mode both        PAGINATION rules and produces an annotated PDF. Kept
                     for CLI debugging; the production UI doesn't surface
                     these modes.

Usage:
  python error_detector.py --file vol-1.pdf --index-end-page 2 --mode write
  python error_detector.py --file v1.pdf --file v2.pdf --annex a.pdf \\
      --client-sig client.png --advocate-sig adv.png --mode write \\
      --index-end-page 2 --write-stdout > out.pdf

This module is intentionally thin: it parses arguments, orchestrates the
right pipeline, and dumps results. All real PDF work lives in the
sibling modules (extraction, pagination, annexures, signatures, merge,
rules, annotated_pdf).
"""

import argparse
import base64
import json
import os
import sys
import tempfile

# Make sibling modules importable whether this script is invoked as
# `python error_detector.py ...` (cwd=server/) or
# `python /path/to/server/error_detector.py ...` (cwd elsewhere).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from config import fitz  # noqa: E402
from extraction import extract_pages  # noqa: E402
from pagination import write_pagination, check_rule_pagination  # noqa: E402
from rules import check_rule_doc_upload  # noqa: E402
from annotated_pdf import generate_annotated_pdf  # noqa: E402
from annexures import append_annexures_to_pdf  # noqa: E402
from merge import merge_pdfs  # noqa: E402


# =============================================================================
# Detect / both pipeline (write path is inlined in main() below)
# =============================================================================

def run_full_analysis(file_path: str, index_end_page: int, mode: str = "detect") -> dict:
    """Run the analysis pipeline.

    mode:
      "detect"  — text extraction + rule checks + annotated PDF.
      "write"   — fast path. Skips extraction and rules entirely; only
                  stamps sequential digits in the top-right corner of
                  every post-index page.
      "both"    — detect + write. Same as detect plus the numbered PDF
                  in `paginated_pdf`.
    """
    if mode not in {"detect", "write", "both"}:
        return {"ok": False, "error": f"Invalid mode: {mode!r}. Must be detect, write, or both."}

    output_dir = tempfile.mkdtemp(prefix="appeal-scan-")
    base_name = os.path.basename(file_path)

    # Pass-through of the merged source — frontend offers a "Download
    # Merged PDF" button that uses this. Cheap to read either way.
    merged_b64 = None
    try:
        with open(file_path, "rb") as f:
            merged_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        print(f"Could not read merged source for passthrough: {e}", file=sys.stderr)

    # ---- Fast path: write-only. No text extraction, no rules. ------------
    if mode == "write":
        if not fitz:
            return {"ok": False, "error": "PyMuPDF not installed: pip install pymupdf"}
        try:
            with fitz.open(file_path) as d:
                total_pages = len(d)
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
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Appeal Document Scanner")
    parser.add_argument("--file", action="append",
                        help="Path to PDF (repeat for multiple volumes, processed in order)")
    parser.add_argument("--annex", action="append",
                        help="Path to an annexure PDF (repeat for multiple). Each file is treated "
                             "as one annexure: 'Annexure A-1' is stamped on file 1's first page, "
                             "'Annexure A-2' on file 2's, etc. Annexures are appended after the "
                             "merged main PDF and pagination continues across them.")
    parser.add_argument("--client-sig", default=None,
                        help="Path to a PNG/JPG of the client's signature. Stamped in the "
                             "bottom-LEFT footer of every page of every annexure. Overlap-aware: "
                             "shifts up if the page already has text near the bottom.")
    parser.add_argument("--advocate-sig", default=None,
                        help="Path to a PNG/JPG of the advocate's signature. Stamped in the "
                             "bottom-RIGHT footer of every page of every annexure (same overlap "
                             "rules as --client-sig).")
    parser.add_argument("--index-end-page", type=int, default=0,
                        help="1-indexed last page of the index. Pages 1..N are skipped from "
                             "the pagination check (0 = no skip).")
    parser.add_argument("--mode", choices=("detect", "write", "both"), default="detect",
                        help="detect: rule check only. write: stamp page numbers only "
                             "(skips text extraction + rules — much faster). both: do both.")
    parser.add_argument("--write-stdout", action="store_true",
                        help="Stream the numbered PDF as raw bytes on stdout instead of "
                             "embedding base64 in JSON. Only valid with --mode write. "
                             "Used by the streaming HTTP route to avoid the ~33%% base64 "
                             "inflation and JSON parsing overhead.")
    args = parser.parse_args()

    if not args.file:
        parser.error("at least one --file is required")

    if args.write_stdout and args.mode != "write":
        parser.error("--write-stdout requires --mode write")

    if args.annex and args.mode != "write":
        parser.error("--annex currently only works with --mode write")

    if (args.client_sig or args.advocate_sig) and not args.annex:
        parser.error("--client-sig / --advocate-sig require at least one --annex (signatures "
                     "are stamped on annexure pages only)")

    # Step 1: merge all main files into one base doc.
    if len(args.file) == 1:
        merged_path = args.file[0]
    else:
        tmp = tempfile.NamedTemporaryFile(suffix="_merged.pdf", delete=False)
        tmp.close()
        if not merge_pdfs(args.file, tmp.name):
            print(json.dumps({"ok": False, "error": "Failed to merge input PDFs"}))
            return
        merged_path = tmp.name
        print(f"Merged {len(args.file)} PDFs -> {merged_path}", file=sys.stderr)

    # Step 2: optionally append annexures (each file = one annexure) and
    # optionally stamp client/advocate signatures on every annexure page.
    if args.annex:
        with_annex = tempfile.NamedTemporaryFile(suffix="_with_annex.pdf", delete=False)
        with_annex.close()
        if not append_annexures_to_pdf(
            merged_path, args.annex, with_annex.name,
            client_sig_path=args.client_sig,
            advocate_sig_path=args.advocate_sig,
        ):
            print(json.dumps({"ok": False, "error": "Failed to append annexures"}))
            return
        target_path = with_annex.name
        sig_note = ""
        if args.client_sig or args.advocate_sig:
            parts = []
            if args.client_sig: parts.append("client")
            if args.advocate_sig: parts.append("advocate")
            sig_note = f" with {'+'.join(parts)} sig"
        print(f"Appended {len(args.annex)} annexure(s){sig_note} -> {target_path}", file=sys.stderr)
    else:
        target_path = merged_path

    # Streaming fast-path: write the numbered PDF directly to stdout. Skips
    # base64, skips JSON, skips the merged_pdf passthrough — server.ts
    # pipes this straight to the HTTP response.
    if args.write_stdout:
        out_tmp = tempfile.NamedTemporaryFile(suffix="_numbered.pdf", delete=False)
        out_tmp.close()
        if not write_pagination(target_path, out_tmp.name, args.index_end_page):
            print("write_pagination failed", file=sys.stderr)
            sys.exit(1)
        with open(out_tmp.name, "rb") as f:
            sys.stdout.buffer.write(f.read())
        try:
            os.unlink(out_tmp.name)
        except OSError:
            pass
        return

    report = run_full_analysis(target_path, args.index_end_page, args.mode)
    if len(args.file) > 1:
        report["file"] = " + ".join(os.path.basename(f) for f in args.file)

    # Print the FULL report (with base64 fields) — server.ts captures stdout
    # and forwards the parsed JSON to the frontend.
    print(json.dumps(report, ensure_ascii=False))

    # Convenience: also write the PDFs to the user's filesystem alongside
    # the input. Doesn't mutate the JSON above.
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
