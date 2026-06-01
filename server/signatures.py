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
on every page, even when text reaches close to the bottom — but it must
never cover that text. We shrink the signature to fit the whitespace
below the text; if there isn't enough, an upright page gets a clean
white footer band appended at the bottom (the page grows slightly;
content never shifts), so the signature always lands on blank space.
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


def _draw_white_card(page, visible_rect: "fitz.Rect", padding: float = 4.0) -> None:
    """Stamp a small opaque white rectangle with a thin gray border at
    `visible_rect` (plus `padding` on every side). Drawn BEFORE the
    signature image so the user's sig — even when scanned with a
    transparent background — pops cleanly off any underlying text,
    notary seal, or registry stamp instead of being washed out.

    Court convention treats the signature as the canonical mark; the
    card around it is consistent with how attorneys stick signature
    labels onto filed copies.
    """
    if not fitz:
        return
    card_visible = fitz.Rect(
        visible_rect.x0 - padding,
        visible_rect.y0 - padding,
        visible_rect.x1 + padding,
        visible_rect.y1 + padding,
    )
    # Clamp to page bounds so the rect never escapes the visible area.
    page_rect = page.rect
    card_visible = fitz.Rect(
        max(0, card_visible.x0),
        max(0, card_visible.y0),
        min(page_rect.width, card_visible.x1),
        min(page_rect.height, card_visible.y1),
    )
    card_mediabox = _project_to_mediabox(page, card_visible)
    try:
        page.draw_rect(
            card_mediabox,
            color=(0.7, 0.7, 0.7),   # thin gray border for definition
            fill=(1, 1, 1),           # opaque white background
            width=0.5,
            fill_opacity=1.0,
        )
    except Exception as e:
        print(f"  white card draw failed: {e}", file=sys.stderr)


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

    Overlap protection (signatures must NEVER cover text): scans existing
    text spans to find the lowest line, then picks the largest of three
    strategies that keeps the signature off the text —
      1. full size in the footer when there's room,
      2. shrink-to-fit the gap below the text (any rotation),
      3. append a clean white footer band to the page bottom when there's
         no room (upright pages; content never moves).
    Rotated pages with no room fall back to a minimum legible size.

    Works on rotated pages: positions are computed in the visible
    (rotation-aware) coord system and projected to mediabox before
    insert_image.
    """
    if not fitz:
        return
    if not client_sig_path and not advocate_sig_path:
        return

    # Sized for legal filings — visible but never dominant. Court convention
    # is that the sig sits cleanly in the footer; this is the compromise
    # between "too small to read against notary seals" and "swallowing the
    # whole bottom of the page".
    SIG_MAX_W = 150
    SIG_MAX_H = 80
    SIG_MIN_H = 30          # smallest height we still consider legible
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

    # ── Smart placement: shrink-to-fit, then extend-page as last resort ──
    # The whole point is that the signature must NEVER cover document text.
    #
    # `avail_h` is the clean whitespace between the lowest text line and the
    # bottom margin. Three regimes:
    #   1. Roomy        -> full-size signature at the usual footer slot.
    #   2. Tight        -> shrink the signature (aspect preserved) to fit the
    #                      gap. Works on any rotation since it only changes the
    #                      image rect, not the page.
    #   3. No room      -> upright pages get a clean white footer band appended
    #                      below the content (page grows; text never moves).
    #                      Rotated pages (can't cleanly extend the visible
    #                      bottom) fall back to the smallest legible size.
    #
    # `band_top` / `band_h` define the bounding box both signatures share.
    avail_h = (page_h - BOTTOM_MARGIN) - (max_text_y + TEXT_BUFFER)

    if avail_h >= SIG_MAX_H:
        band_h = SIG_MAX_H
        band_top = page_h - BOTTOM_MARGIN - SIG_MAX_H
    elif avail_h >= SIG_MIN_H:
        # Shrink to exactly the gap below the text.
        band_h = avail_h
        band_top = max_text_y + TEXT_BUFFER
    elif rotation == 0:
        # Append a clean footer band by extending the page's mediabox at the
        # visible bottom. Content keeps its coordinates, so nothing shifts and
        # no text is covered. (Verified: for rotation==0, growing mediabox y1
        # adds space at the visible bottom.)
        band_h = SIG_MAX_H
        band_top = max_text_y + TEXT_BUFFER
        extend_by = (band_top + band_h + BOTTOM_MARGIN) - page_h
        if extend_by > 0:
            try:
                mb = page.mediabox
                page.set_mediabox(fitz.Rect(mb.x0, mb.y0, mb.x1, mb.y1 + extend_by))
                page_h = page.rect.height
                print(
                    f"  dense page (text reaches y={max_text_y:.0f}); extended "
                    f"bottom by {extend_by:.0f}pt for a clean signature band",
                    file=sys.stderr,
                )
            except Exception as e:
                # If the page can't be resized, degrade to a minimum-size
                # signature at the bottom rather than dropping it entirely.
                print(f"  page extend failed ({e}); using minimum size", file=sys.stderr)
                band_h = SIG_MIN_H
                band_top = page_h - BOTTOM_MARGIN - SIG_MIN_H
    else:
        # Rotated AND no room: extending the visible bottom isn't reliable for
        # /Rotate 90/180/270, so use the smallest legible size at the bottom.
        band_h = SIG_MIN_H
        band_top = page_h - BOTTOM_MARGIN - SIG_MIN_H
        print(
            f"  warning: dense rotated page (text reaches y={max_text_y:.0f}); "
            f"using minimum signature size at bottom",
            file=sys.stderr,
        )

    # Client (left)
    if client_sig_path:
        import os as _os
        if not _os.path.exists(client_sig_path):
            print(
                f"  client sig file missing on disk: {client_sig_path}",
                file=sys.stderr,
            )
        else:
            visible_rect = _fit_visible_rect_to_image(
                client_sig_path, LEFT_MARGIN, band_top, SIG_MAX_W, band_h,
            )
            if visible_rect:
                # Card first (so sig draws ON TOP), then sig.
                _draw_white_card(page, visible_rect)
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
                        f"  client sig insert failed "
                        f"(rotation={rotation}, rect={mediabox_rect}): {e}",
                        file=sys.stderr,
                    )

    # Advocate (right). Right-edge anchored — compute width first in
    # visible coords, then project to mediabox.
    if advocate_sig_path:
        import os as _os
        if not _os.path.exists(advocate_sig_path):
            print(
                f"  advocate sig file missing on disk: {advocate_sig_path}",
                file=sys.stderr,
            )
            return
        aspect = _read_image_aspect(advocate_sig_path) or 1.0
        h = band_h
        w = h * aspect
        if w > SIG_MAX_W:
            w = SIG_MAX_W
            h = w / aspect
        right_x = page_w - RIGHT_MARGIN - w
        visible_rect = fitz.Rect(right_x, band_top, right_x + w, band_top + h)
        # Card first, then sig on top.
        _draw_white_card(page, visible_rect)
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
                f"  advocate sig insert failed "
                f"(rotation={rotation}, rect={mediabox_rect}): {e}",
                file=sys.stderr,
            )
