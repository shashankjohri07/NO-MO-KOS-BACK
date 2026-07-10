"""Bookmark detection + injection.

Two halves, mirroring the pagination module's split:

detect_bookmarks()  scans a PDF and proposes a bookmark tree using two
                    zero-cost layers — regex patterns over line text
                    (Layer 1) and font/style analysis of text spans
                    (Layer 2). If the document already carries an embedded
                    TOC it is returned as a high-confidence seed instead.
                    Output is JSON: the frontend renders it for review,
                    the user approves/edits/adds, and sends the final tree
                    back to apply_bookmarks(). No state server-side.

apply_bookmarks()   takes the finalized [{title, level, page}] list and
                    injects it via doc.set_toc(). Pure manipulation, no AI.

CLI (spawned from Node the same way error_detector.py is):

  python bookmarks.py detect --file vol-1.pdf [--file vol-2.pdf]
      → JSON on stdout: {ok, existing_toc, headings: [...]}

  python bookmarks.py apply --file vol-1.pdf --toc-json /tmp/toc.json
      → bookmarked PDF bytes on stdout
"""

import argparse
import json
import re
import sys
from collections import Counter
from typing import Dict, List, Optional

from config import fitz
from merge import merge_to_doc

# ── Layer 1: pattern-based heading detection ─────────────────────────────
# Each entry: (compiled regex, hierarchy level, confidence). Legal-filing
# heavy — Chapters/Parts/Articles outrank numbered sections which outrank
# lettered/roman clauses. Confidence is the prior that a matching line is
# really a heading (short ALL-CAPS regex hits on body text do happen).
_PATTERNS = [
    (re.compile(r"^(CHAPTER|Chapter)\s+([IVXLCDM]+|\d+)\b"), 1, 0.95),
    (re.compile(r"^(PART|Part)\s+([IVXLCDM]+|\d+)\b"), 1, 0.95),
    (re.compile(r"^(SCHEDULE|Schedule)\s+([IVXLCDM]+|\d+|[A-Z])\b"), 1, 0.9),
    (re.compile(r"^(ANNEXURE|Annexure)\s+[A-Z]?-?\s*\d+\b"), 1, 0.9),
    (re.compile(r"^(APPENDIX|Appendix)\s+([A-Z]|\d+)\b"), 1, 0.9),
    (re.compile(r"^(ARTICLE|Article)\s+\d+\b"), 2, 0.9),
    (re.compile(r"^(SECTION|Section)\s+\d+\b"), 2, 0.9),
    (re.compile(r"^\d+\.\d+\.\d+\s+\S"), 3, 0.85),
    (re.compile(r"^\d+\.\d+\s+\S"), 2, 0.85),
    (re.compile(r"^\d+\.\s+[A-Z]"), 1, 0.7),
    (re.compile(r"^\([a-z]\)\s+\S"), 4, 0.5),
    (re.compile(r"^\([ivxl]+\)\s+\S"), 5, 0.5),
]

# Lines longer than this are body text no matter what they match — real
# headings are short. Also the cap we store as a bookmark title.
_MAX_HEADING_LEN = 120


def _pattern_match(text: str):
    """Return (level, confidence) for the first Layer-1 pattern hit, else None."""
    for rx, level, conf in _PATTERNS:
        if rx.match(text):
            return level, conf
    return None


def _body_font_size(doc) -> float:
    """The dominant span font size across the document = body text size.
    Sampled from every page's text dict; falls back to 11 for image-only
    (scanned) PDFs where there are no text spans at all."""
    sizes: Counter = Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        # Round to halves so 11.98 and 12.02 pool together.
                        sizes[round(span["size"] * 2) / 2] += len(span["text"])
    return sizes.most_common(1)[0][0] if sizes else 11.0


def detect_bookmarks(doc) -> Dict:
    """Run detection over the open document and return the proposal dict.

    If the PDF already embeds a TOC we trust it outright (confidence 1.0,
    source 'existing_toc') — the user still reviews it client-side, and
    Layer 1+2 detection is skipped to avoid drowning a good TOC in noise.
    """
    existing = doc.get_toc()
    if existing:
        headings = [
            {
                "title": title.strip()[:_MAX_HEADING_LEN],
                "level": max(1, int(level)),
                "page": int(page),
                "confidence": 1.0,
                "source": "existing_toc",
            }
            for level, title, page in existing
            if title.strip() and page >= 1
        ]
        return {"ok": True, "existing_toc": True, "headings": headings}

    body_size = _body_font_size(doc)
    headings: List[Dict] = []

    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join(s["text"] for s in spans).strip()
                if not text or len(text) > _MAX_HEADING_LEN:
                    continue

                # Layer 2 signals from the line's first span (headings are
                # rendered in one style; mixed-style lines are body text).
                size = spans[0]["size"]
                bold = "bold" in spans[0]["font"].lower()
                caps = text.isupper() and len(text) >= 4
                big = size >= body_size + 1.5

                hit = _pattern_match(text)
                if hit:
                    level, conf = hit
                    # Style agreement bumps confidence; a pattern hit set in
                    # plain body style is usually a sentence that happens to
                    # start with "Article 5 ..." — penalize it into the
                    # needs-review band rather than dropping it outright.
                    if big or bold:
                        conf = min(1.0, conf + 0.1)
                    else:
                        conf = max(0.3, conf - 0.25)
                elif big and (bold or caps):
                    # Style-only heading (no numbering) — e.g. a centered
                    # "PRAYER" or "SYNOPSIS" page header in a filing.
                    level, conf = (1 if size >= body_size + 4 else 2), 0.6
                elif bold and caps:
                    level, conf = 2, 0.5
                else:
                    continue

                headings.append(
                    {
                        "title": text,
                        "level": level,
                        "page": pno,
                        "y": round(spans[0]["bbox"][1], 1),
                        "confidence": round(conf, 2),
                        "source": "auto_detected",
                    }
                )

    # De-dupe: identical title+page pairs (multi-column layouts double-hit),
    # keeping the higher-confidence one.
    best: Dict = {}
    for h in headings:
        key = (h["title"], h["page"])
        if key not in best or h["confidence"] > best[key]["confidence"]:
            best[key] = h
    ordered = sorted(best.values(), key=lambda h: (h["page"], h.get("y", 0)))

    # set_toc rejects a jump deeper than one level per step (1 → 3); clamp
    # each entry to parent_level + 1 so the tree is always injectable.
    prev_level = 0
    for h in ordered:
        h["level"] = min(h["level"], prev_level + 1)
        prev_level = h["level"]

    return {"ok": True, "existing_toc": False, "headings": ordered}


def apply_bookmarks(doc, headings: List[Dict]) -> bool:
    """Inject the finalized tree into the open document via set_toc.
    Entries are re-clamped (level jumps, page bounds) because the list has
    round-tripped through the browser and may have been hand-edited."""
    try:
        total = len(doc)
        toc = []
        prev_level = 0
        for h in headings:
            title = str(h.get("title", "")).strip()[:_MAX_HEADING_LEN]
            page = int(h.get("page", 0))
            level = int(h.get("level", 1))
            if not title or page < 1 or page > total:
                continue
            level = max(1, min(level, prev_level + 1))
            prev_level = level
            toc.append([level, title, page])
        doc.set_toc(toc)
        return True
    except Exception as e:
        print(f"apply_bookmarks failed: {e}", file=sys.stderr)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────

def _open_merged(paths: List[str]):
    """One path opens directly; several merge in order (same volume
    semantics as the pagination pipeline)."""
    if fitz is None:
        print("PyMuPDF is not available", file=sys.stderr)
        sys.exit(1)
    doc = fitz.open(paths[0]) if len(paths) == 1 else merge_to_doc(paths)
    if doc is None:
        print("failed to open/merge input PDFs", file=sys.stderr)
        sys.exit(1)
    return doc


def main():
    parser = argparse.ArgumentParser(description="Bookmark detect/apply")
    parser.add_argument("command", choices=("detect", "apply"))
    parser.add_argument("--file", action="append", required=True,
                        help="Input PDF (repeatable; merged in order)")
    parser.add_argument("--toc-json", default=None,
                        help="apply only: path to the finalized headings JSON "
                             '(either a bare list or {"headings": [...]})')
    args = parser.parse_args()

    doc = _open_merged(args.file)

    if args.command == "detect":
        print(json.dumps(detect_bookmarks(doc)))
        return

    if not args.toc_json:
        parser.error("apply requires --toc-json")
    with open(args.toc_json, "r", encoding="utf-8") as f:
        payload = json.load(f)
    headings = payload["headings"] if isinstance(payload, dict) else payload

    if not apply_bookmarks(doc, headings):
        sys.exit(1)
    sys.stdout.buffer.write(doc.tobytes(garbage=3, deflate=True))


if __name__ == "__main__":
    main()
