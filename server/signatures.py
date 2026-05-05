"""Footer signature stamping with strict aspect-ratio preservation.

Stamps a client image (left) and an advocate image (right) at the bottom
of any page. Both are optional; missing side is skipped.

Aspect handling: PyMuPDF's `keep_proportion` letterboxes inside the rect
but the rect itself is what callers think the image occupies. For
accurate visual placement (and to match what client expects), we read
the image's pixel dimensions via PIL and compute a rect that *exactly*
matches the image's natural ratio. A 1:1 stamp lands as 100×100, a 3:1
cursive sig fills 180×60.

Note: this is a pure module split from the previous monolith. Identical
behaviour as before — no xref-reuse / dedupe optimisation here yet
(planned for a separate hardening pass).
"""

import sys
from typing import Optional

from config import fitz


def _read_image_aspect(path: str) -> Optional[float]:
    """Pixel width/height of the image at `path`, or None if unreadable.
    PIL is imported lazily so write-only paths that never touch images
    don't pay its import cost on cold start."""
    try:
        from PIL import Image as PILImage
    except ImportError:
        return None
    try:
        with PILImage.open(path) as img:
            iw, ih = img.size
    except Exception as e:
        print(f"  could not read image {path}: {e}", file=sys.stderr)
        return None
    if iw <= 0 or ih <= 0:
        return None
    return iw / ih


def _fit_rect_to_image(image_path: str, x_left: float, y_top: float,
                       max_w: float, max_h: float) -> Optional["fitz.Rect"]:
    """Compute a rect that exactly preserves the image's natural aspect
    ratio inside the (max_w, max_h) bounding box. Returns None if the
    image can't be read."""
    aspect = _read_image_aspect(image_path)
    if aspect is None:
        # Square fallback — better than nothing.
        side = min(max_w, max_h)
        return fitz.Rect(x_left, y_top, x_left + side, y_top + side)
    h = max_h
    w = h * aspect
    if w > max_w:
        w = max_w
        h = w / aspect
    return fitz.Rect(x_left, y_top, x_left + w, y_top + h)


def stamp_signatures_on_page(
    page,
    client_sig_path: Optional[str],
    advocate_sig_path: Optional[str],
) -> None:
    """Stamp client (left) and advocate (right) signatures in the page footer
    with native aspect ratio preserved.

    Bounding box per side: 180×100pt. Real rect is sized to the image's
    natural ratio so square stamps land at 100×100 and wide cursive sigs
    fill 180×60.

    Overlap protection: scans existing text spans, finds the lowest line,
    and pushes the sig row up so it sits cleanly below the content.
    Defensive cap on span scan keeps pathological inputs (10k+ spans/page)
    bounded.
    """
    if not fitz:
        return
    if not client_sig_path and not advocate_sig_path:
        return

    SIG_MAX_W = 180
    SIG_MAX_H = 100
    LEFT_MARGIN = 60
    RIGHT_MARGIN = 60
    BOTTOM_MARGIN = 30
    TEXT_BUFFER = 12
    MAX_SPANS_TO_SCAN = 5000

    page_w = page.rect.width
    page_h = page.rect.height

    max_text_y = 0.0
    span_count = 0
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_count += 1
                if span_count > MAX_SPANS_TO_SCAN:
                    break
                _, _, _, y1 = span["bbox"]
                if y1 > max_text_y:
                    max_text_y = y1
            if span_count > MAX_SPANS_TO_SCAN:
                break
        if span_count > MAX_SPANS_TO_SCAN:
            break

    sig_top = page_h - BOTTOM_MARGIN - SIG_MAX_H
    if max_text_y + TEXT_BUFFER > sig_top:
        sig_top = max_text_y + TEXT_BUFFER

    if sig_top + SIG_MAX_H > page_h - 5:
        print(
            f"  warning: page too dense for signatures "
            f"(text reaches y={max_text_y:.0f}); skipping",
            file=sys.stderr,
        )
        return

    # Client (left)
    if client_sig_path:
        rect = _fit_rect_to_image(client_sig_path, LEFT_MARGIN, sig_top,
                                  SIG_MAX_W, SIG_MAX_H)
        if rect:
            try:
                page.insert_image(rect, filename=client_sig_path, keep_proportion=True)
            except Exception as e:
                print(f"  client sig insert failed: {e}", file=sys.stderr)

    # Advocate (right). Right-edge anchored — compute width first.
    if advocate_sig_path:
        aspect = _read_image_aspect(advocate_sig_path) or 1.0
        h = SIG_MAX_H
        w = h * aspect
        if w > SIG_MAX_W:
            w = SIG_MAX_W
            h = w / aspect
        right_x = page_w - RIGHT_MARGIN - w
        rect = fitz.Rect(right_x, sig_top, right_x + w, sig_top + h)
        try:
            page.insert_image(rect, filename=advocate_sig_path, keep_proportion=True)
        except Exception as e:
            print(f"  advocate sig insert failed: {e}", file=sys.stderr)
