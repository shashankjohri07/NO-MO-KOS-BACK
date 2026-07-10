"""Court-filing index page generator (NCLT/High Court "Master Index" style).

Takes a JSON payload of the case's variable details — court name, case
numbers, parties, index rows, advocates, place/date — and renders the
standard filing index:

    BEFORE THE HON'BLE ... TRIBUNAL,        (centered, bold)
    <case number lines>                     (centered, bold)
    IN THE MATTER OF:                       (bold, underlined)
      party lines ....................Role  (role right-aligned)
                    Versus                  (centered)
    MASTER INDEX                            (centered, bold)
    ┌──────┬───────────────────────┬──────────┐
    │ S.NO │ PARTICULARS           │ PAGE NO. │
    ...rows, wrapping across pages as needed...
    FILED THROUGH:                          (centered, gap for signatures)
    <advocate block>                        (right-aligned, bold)
    PLACE / DATE                            (left, bold)

Payload shape (all fields optional except rows):

{
  "court":     ["BEFORE THE HON'BLE NATIONAL COMPANY LAW TRIBUNAL,", "NEW DELHI"],
  "caseLines": ["IA NO. ____ OF 2026", "IN", "IA NO. 26/2025", "OF", "CP (IB) NO. 2340/(ND)/2019"],
  "matters": [
    {"label": "IN THE MATTER OF:",
     "parties": [
       {"lines": ["Ms. Rashmi Mintri", "Resolution Professional"], "role": "Applicant"},
       {"lines": ["Viveck Goel and Ors."], "role": "Respondents/Contemnors"}
     ]}
  ],
  "indexTitle": "MASTER INDEX",
  "rows": [
    {"title": "MEMO OF PARTIES", "description": "", "pages": "1-2"},
    {"title": "ANNEXURE A-1:", "description": "Copy of Application ...", "pages": "24-160"}
  ],
  "advocates": ["ADV. ADITYA GAURI, ADV. AMAR VIVEK", "ADVOCATES", "..."],
  "place": "NEW DELHI",
  "date": "12.06.2026"
}

CLI (spawned from Node like bookmarks.py):

  python index_page.py generate --payload payload.json
      → index PDF bytes on stdout
  python index_page.py generate --payload payload.json --file doc.pdf [--file ...]
      → index prepended to the (merged) document, PDF bytes on stdout
"""

import argparse
import json
import sys
from typing import Dict, List

from config import fitz
from merge import merge_to_doc

PAGE_W, PAGE_H = 595, 842  # A4
MARGIN_X = 70
MARGIN_TOP = 90
MARGIN_BOT = 70

FONT = "tiro"       # Times-Roman
FONT_B = "tibo"     # Times-Bold

SZ_COURT = 12.5
SZ_CASE = 11.5
SZ_BODY = 11.5
SZ_TABLE = 11
LEAD = 1.55         # line spacing multiplier

# Table column widths (S.NO | PARTICULARS | PAGE NO.)
COL_SNO = 48
COL_PAGE = 78
CELL_PAD = 7


def _w(text: str, size: float, font: str) -> float:
    return fitz.get_text_length(text, fontname=font, fontsize=size)


def _wrap(text: str, size: float, font: str, width: float) -> List[str]:
    """Greedy word wrap into lines that fit `width`."""
    lines: List[str] = []
    for para in str(text).split("\n"):
        words = para.split()
        if not words:
            continue
        cur = words[0]
        for word in words[1:]:
            if _w(cur + " " + word, size, font) <= width:
                cur += " " + word
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class _Writer:
    """Cursor-based page writer with helpers for the index layout."""

    def __init__(self):
        self.doc = fitz.open()
        self.page = None
        self.y = 0.0
        self._new_page()

    def _new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = MARGIN_TOP

    def need(self, height: float):
        if self.y + height > PAGE_H - MARGIN_BOT:
            self._new_page()

    def gap(self, h: float):
        self.y += h

    def line(self, text: str, size=SZ_BODY, font=FONT, align="left",
             underline=False, x0=MARGIN_X, x1=PAGE_W - MARGIN_X):
        lh = size * LEAD
        self.need(lh)
        tw = _w(text, size, font)
        if align == "center":
            x = x0 + (x1 - x0 - tw) / 2
        elif align == "right":
            x = x1 - tw
        else:
            x = x0
        baseline = self.y + size
        self.page.insert_text((x, baseline), text, fontsize=size, fontname=font)
        if underline:
            self.page.draw_line((x, baseline + 2), (x + tw, baseline + 2), width=0.8)
        self.y += lh

    def party_line(self, text: str, role: str = ""):
        """Party name left, dotted role right-aligned on the same line
        (e.g. 'Auto Needs (India) Private Limited      .... Applicant')."""
        lh = SZ_BODY * LEAD
        self.need(lh)
        baseline = self.y + SZ_BODY
        self.page.insert_text((MARGIN_X, baseline), text, fontsize=SZ_BODY, fontname=FONT)
        if role:
            r = role if role.startswith(("…", ".")) else f".... {role}"
            rw = _w(r, SZ_BODY, FONT)
            self.page.insert_text((PAGE_W - MARGIN_X - rw, baseline), r,
                                  fontsize=SZ_BODY, fontname=FONT)
        self.y += lh


def _render_row_lines(row: Dict, text_width: float):
    """Returns [(text, font)] wrapped lines for a row's particulars cell."""
    out = []
    title = str(row.get("title", "")).strip()
    desc = str(row.get("description", "")).strip()
    if title:
        for ln in _wrap(title, SZ_TABLE, FONT_B, text_width):
            out.append((ln, FONT_B))
    if desc:
        for ln in _wrap(desc, SZ_TABLE, FONT, text_width):
            out.append((ln, FONT))
    return out or [("", FONT)]


def _draw_table(w: _Writer, rows: List[Dict]):
    """Bordered 3-column index table; continues across pages, repeating the
    header row on each new page."""
    x0 = MARGIN_X - 10
    x1 = PAGE_W - MARGIN_X + 10
    xs = [x0, x0 + COL_SNO, x1 - COL_PAGE, x1]  # column edges
    text_width = xs[2] - xs[1] - 2 * CELL_PAD
    line_h = SZ_TABLE * 1.5

    def draw_header():
        h = line_h + 2 * CELL_PAD - 4
        top = w.y
        cells = [("S.NO.", 0), ("PARTICULARS", 1), ("PAGE NO.", 2)]
        for text, ci in cells:
            cx = xs[ci] + (xs[ci + 1] - xs[ci] - _w(text, SZ_TABLE, FONT_B)) / 2
            w.page.insert_text((cx, top + CELL_PAD + SZ_TABLE - 2), text,
                               fontsize=SZ_TABLE, fontname=FONT_B)
        w.y = top + h
        return top

    def close_segment(seg_top):
        """Verticals + bottom rule for the table segment on this page."""
        w.page.draw_line((x0, w.y), (x1, w.y), width=0.9)
        for x in xs:
            w.page.draw_line((x, seg_top), (x, w.y), width=0.9)

    w.need(line_h * 4)
    seg_top = w.y
    w.page.draw_line((x0, seg_top), (x1, seg_top), width=0.9)
    draw_header()
    w.page.draw_line((x0, w.y), (x1, w.y), width=0.9)

    for i, row in enumerate(rows, start=1):
        lines = _render_row_lines(row, text_width)
        row_h = len(lines) * line_h + 2 * CELL_PAD - 4

        # Row doesn't fit → close this segment, new page, re-draw header.
        if w.y + row_h > PAGE_H - MARGIN_BOT:
            close_segment(seg_top)
            w._new_page()
            seg_top = w.y
            w.page.draw_line((x0, seg_top), (x1, seg_top), width=0.9)
            draw_header()
            w.page.draw_line((x0, w.y), (x1, w.y), width=0.9)

        top = w.y
        sno = str(row.get("sno") or i) + "."
        w.page.insert_text((xs[0] + CELL_PAD, top + CELL_PAD + SZ_TABLE - 2),
                           sno, fontsize=SZ_TABLE, fontname=FONT_B)
        pages = str(row.get("pages", "")).strip()
        if pages:
            pw = _w(pages, SZ_TABLE, FONT_B)
            w.page.insert_text(
                (xs[2] + (xs[3] - xs[2] - pw) / 2, top + CELL_PAD + SZ_TABLE - 2),
                pages, fontsize=SZ_TABLE, fontname=FONT_B)
        yy = top + CELL_PAD + SZ_TABLE - 2
        for text, font in lines:
            w.page.insert_text((xs[1] + CELL_PAD, yy), text,
                               fontsize=SZ_TABLE, fontname=font)
            yy += line_h
        w.y = top + row_h
        w.page.draw_line((x0, w.y), (x1, w.y), width=0.9)

    close_segment(seg_top)


def build_index(payload: Dict):
    """Render the index document and return the open fitz doc."""
    w = _Writer()

    for ln in payload.get("court") or []:
        w.line(str(ln).strip(), size=SZ_COURT, font=FONT_B, align="center")
    w.gap(4)
    for ln in payload.get("caseLines") or []:
        w.line(str(ln).strip(), size=SZ_CASE, font=FONT_B, align="center")
    w.gap(18)

    for matter in payload.get("matters") or []:
        label = str(matter.get("label", "")).strip()
        if label:
            w.line(label, font=FONT_B, underline=True)
            w.gap(6)
        parties = matter.get("parties") or []
        for pi, party in enumerate(parties):
            lines = [str(x).strip() for x in (party.get("lines") or []) if str(x).strip()]
            role = str(party.get("role", "")).strip()
            for li, ln in enumerate(lines):
                # Role rides on the last line of the party block.
                w.party_line(ln, role if li == len(lines) - 1 else "")
            if pi < len(parties) - 1:
                w.gap(2)
                w.line("Versus", align="center")
                w.gap(2)
        w.gap(12)

    title = str(payload.get("indexTitle") or "INDEX").strip()
    w.gap(4)
    w.line(title, font=FONT_B, align="center")
    w.gap(6)

    _draw_table(w, payload.get("rows") or [])

    # Filing block — keep it together on one page if possible.
    w.gap(30)
    w.need(160)
    w.line("FILED THROUGH:", font=FONT_B, align="center")
    w.gap(46)  # signature space
    for ln in payload.get("advocates") or []:
        w.line(str(ln).strip(), size=SZ_TABLE, font=FONT_B, align="right")
    w.gap(10)
    place = str(payload.get("place", "")).strip()
    date = str(payload.get("date", "")).strip()
    if place:
        w.line(f"PLACE: {place}", size=SZ_TABLE, font=FONT_B)
    if date:
        w.line(f"DATE: {date}", size=SZ_TABLE, font=FONT_B)

    return w.doc


def main():
    parser = argparse.ArgumentParser(description="Filing index generator")
    parser.add_argument("command", choices=("generate",))
    parser.add_argument("--payload", required=True,
                        help="Path to the JSON payload with case details + rows")
    parser.add_argument("--file", action="append", default=[],
                        help="Optional document PDF(s); index is prepended "
                             "to the merged result")
    args = parser.parse_args()

    if fitz is None:
        print("PyMuPDF is not available", file=sys.stderr)
        sys.exit(1)

    with open(args.payload, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not payload.get("rows"):
        print("payload must be an object with a non-empty rows array", file=sys.stderr)
        sys.exit(1)

    index_doc = build_index(payload)

    if args.file:
        body = fitz.open(args.file[0]) if len(args.file) == 1 else merge_to_doc(args.file)
        if body is None:
            print("failed to open/merge input PDFs", file=sys.stderr)
            sys.exit(1)
        index_doc.insert_pdf(body)

    sys.stdout.buffer.write(index_doc.tobytes(garbage=3, deflate=True))


if __name__ == "__main__":
    main()
