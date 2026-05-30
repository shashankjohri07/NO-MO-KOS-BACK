"""Footer signature stamping with strict aspect-ratio preservation.

Stamps a client image (left) and an advocate image (right) at the bottom
of any page. Both are optional; missing side is skipped.

Aspect handling: PyMuPDF's `keep_proportion` letterboxes inside the rect
but the rect itself is what callers think the image occupies. For
accurate visual placement (and to match what client expects), we read
the image's pixel dimensions via PIL and compute a rect that *exactly*
matches the image's natural ratio. A 1:1 stamp lands as 100×100, a 3:1
cursive sig fills 180×60.

Rotation handling: scanned PDFs often have /Rotate 90/180/270 — viewer
shows portrait, but PyMuPDF coordinates default to the unrotated
mediabox. We compute placement in the visible (rotation-aware) coord
system, project to mediabox via page.derotation_matrix, and pass
rotate=page.rotation so the stamped image appears upright at the
visible footer.

Dense-page handling: court convention requires the signature to appear
on every page, even when text content reaches close to the bottom. If
no headroom exists, we force the stamp to the bottom edge and accept a
minor overlap rather than silently dropping the signature.
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


def _fit_visible_rect_to_image(
    image_path: str, x_left: float, y_top: float,
    max_w: float, max_h: float,
) -> Optional["fitz.Rect"]:
    """Compute a VISIBLE-coord rect that exactly preserves the image's
    natural aspect ratio inside the (max_w, max_h) bounding box. Returns
    None if the image can't be read.

    Caller is responsible for projecting this rect into mediabox coords
    via page.derotation_matrix before passing to insert_image.
    """
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


def _project_to_mediabox(page, visible_rect: "fitz.Rect") -> "fitz.Rect":
    """Project a visible-coord rect to mediabox coords for insert_image.

    For rotation==0 this is a no-op. For 90/180/270 the rect is
    multiplied by the derotation matrix and re-normalized (matrix
    multiplication can flip x0>x1 / y0>y1).
    """
    if page.rotation == 0:
        return visible_rect
    r = visible_rect * page.derotation_matrix
    # Normalize: insert_image requires x0<=x1, y0<=y1.
    return fitz.Rect(
        min(r.x0, r.x1), min(r.y0, r.y1),
        max(r.x0, r.x1), max(r.y0, r.y1),
    )


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
    and tries to push the sig row up so it sits cleanly below the
    content. If the page is too dense for any clean placement, we still
    stamp at the very bottom (court convention — sig must appear on
    every page).

    Works on rotated pages: positions are computed in the visible
    (rotation-aware) coord system and projected to mediabox before
    insert_image.
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

    # Use rotation-aware (visible) page dims everywhere.
    page_w = page.rect.width
    page_h = page.rect.height
    rotation = page.rotation

    # Text-overlap scan: PyMuPDF's get_text() returns coordinates in the
    # page's CURRENT (visible) coord system, so this comparison stays
    # valid regardless of rotation.
    max_text_y = 0.0
    span_count = 0
    try:
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
    except Exception as e:
        # Pathological page — fall back to default footer position.
        print(f"  text scan failed (rotation={rotation}): {e}", file=sys.stderr)
        max_text_y = 0.0

    # Preferred top of the signature band, then adjust upward if text
    # would clash. If even the bottom is shared with text, force the
    # default footer position and accept slight overlap.
    sig_top = page_h - BOTTOM_MARGIN - SIG_MAX_H
    if max_text_y + TEXT_BUFFER > sig_top:
        sig_top = max_text_y + TEXT_BUFFER
    if sig_top + SIG_MAX_H > page_h - 5:
        sig_top = page_h - BOTTOM_MARGIN - SIG_MAX_H
        print(
            f"  warning: dense page (text reaches y={max_text_y:.0f}); "
            f"forcing signature to bottom margin",
            file=sys.stderr,
        )

    # Client (left)
    if client_sig_path:
        visible_rect = _fit_visible_rect_to_image(
            client_sig_path, LEFT_MARGIN, sig_top, SIG_MAX_W, SIG_MAX_H,
        )
        if visible_rect:
            mediabox_rect = _project_to_mediabox(page, visible_rect)
            try:
                page.insert_image(
                    mediabox_rect,
                    filename=client_sig_path,
                    keep_proportion=True,
                    rotate=rotation,
                )
            except Exception as e:
                print(
                    f"  client sig insert failed (rotation={rotation}): {e}",
                    file=sys.stderr,
                )

    # Advocate (right). Right-edge anchored — compute width first in
    # visible coords, then project to mediabox.
    if advocate_sig_path:
        aspect = _read_image_aspect(advocate_sig_path) or 1.0
        h = SIG_MAX_H
        w = h * aspect
        if w > SIG_MAX_W:
            w = SIG_MAX_W
            h = w / aspect
        right_x = page_w - RIGHT_MARGIN - w
        visible_rect = fitz.Rect(right_x, sig_top, right_x + w, sig_top + h)
        mediabox_rect = _project_to_mediabox(page, visible_rect)
        try:
            page.insert_image(
                mediabox_rect,
                filename=advocate_sig_path,
                keep_proportion=True,
                rotate=rotation,
            )
        except Exception as e:
            print(
                f"  advocate sig insert failed (rotation={rotation}): {e}",
                file=sys.stderr,
            )
