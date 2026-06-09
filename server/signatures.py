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
never cover that content. Placement is decided from the page's ACTUAL
rendered pixels (so text, images, scans and vector graphics are all
detected): a normal page signs at the bottom footer; a dense page with
a clear top margin signs in the top header area; and if both ends are
busy (e.g. full-page scans) the content is scaled up into the area
above a reserved bottom strip (page size unchanged; survives insert_pdf;
normalises /Rotate).

Transparency: the signature's white/near-white background is dropped to
transparent (chroma-key) before stamping, and no opaque backing card is
drawn. So in the rare case the signature does sit over content, the text
shows THROUGH the gaps in the ink instead of being hidden. Best for dark
ink scanned on white paper; full-colour photos/logos have no white
background to drop and should be supplied as ready-made transparent PNGs.
"""

import os
import sys
from typing import Optional

from config import fitz


# Cache: image path -> aspect ratio (or None). The same one or two signature
# files are measured on EVERY page of a filing, so without this a 100-page
# document costs ~200 disk opens instead of 2.
_ASPECT_CACHE: dict = {}


def _read_image_aspect(path: str) -> Optional[float]:
    """Pixel width/height of the image at `path`, or None if unreadable.
    Cached per path (signature files are immutable for the run). PIL is
    imported lazily so write-only paths that never touch images don't pay
    its import cost on cold start."""
    if path in _ASPECT_CACHE:
        return _ASPECT_CACHE[path]
    aspect = None
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as img:
            iw, ih = img.size
        if iw > 0 and ih > 0:
            aspect = iw / ih
    except ImportError:
        pass
    except Exception as e:
        print(f"  could not read image {path}: {e}", file=sys.stderr)
    _ASPECT_CACHE[path] = aspect
    return aspect


# Cache: source signature path -> transparent-background PNG path. The same
# signature is stamped on many pages, so the chroma-key runs ONCE per image.
_TRANSPARENT_CACHE: dict = {}


def _transparent_signature(path: str) -> str:
    """Return a path to a copy of the signature whose white / near-white
    background has been made transparent, so document text shows THROUGH the
    gaps in the ink instead of being hidden by an opaque white box. Cached per
    source path; falls back to the original path if PIL is missing or anything
    fails.

    Tuned for the common case (dark ink scanned on white paper). Full-colour
    images (photos, colour logos) have no white background to drop, so they
    come back essentially unchanged — those need a real transparent PNG.
    """
    if path in _TRANSPARENT_CACHE:
        return _TRANSPARENT_CACHE[path]

    result = path
    WHITE = 238  # R,G,B all at/above this -> treated as background -> transparent
    try:
        from PIL import Image as PILImage
        import tempfile

        img = PILImage.open(path).convert("RGBA")
        # Vectorised entirely in PIL's C layer (no numpy dependency, no
        # Python pixel loop): build a "background" mask of pixels whose R,
        # G and B are ALL >= WHITE, then zero the alpha channel there.
        from PIL import ImageChops
        r, g, b, a = img.split()
        thr = [0] * WHITE + [255] * (256 - WHITE)
        bg = ImageChops.multiply(
            ImageChops.multiply(r.point(thr), g.point(thr)), b.point(thr)
        )
        a = ImageChops.subtract(a, bg)  # alpha -> 0 where background
        img.putalpha(a)

        tmp = tempfile.NamedTemporaryFile(suffix="_sig_transparent.png", delete=False)
        tmp.close()
        img.save(tmp.name, "PNG")
        result = tmp.name
    except ImportError:
        pass  # PIL unavailable — stamp the original image unchanged
    except Exception as e:
        print(f"  signature transparency failed for {path}: {e}", file=sys.stderr)

    _TRANSPARENT_CACHE[path] = result
    return result


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


def _zone_is_blank(page, y0: float, y1: float) -> bool:
    """True if the horizontal band between visible-y `y0` and `y1` renders
    blank (near-white). Decides from the ACTUAL rendered pixels, so it cannot
    be fooled by content type — body text, embedded images, full-page scans
    and vector drawings are all detected (a text-only scan silently missed
    image/scanned annexures).

    Rotation-agnostic: get_pixmap renders the visible (rotation-applied) view,
    so a y-band maps to the same band of pixmap rows regardless of /Rotate.
    On any error we return False (assume content) so the caller plays safe.
    """
    try:
        zoom = 0.4  # low-res is plenty for a blank / not-blank decision
        clip = fitz.Rect(0, max(0.0, y0), page.rect.width,
                         min(page.rect.height, y1))
        if clip.is_empty:
            return True
        # Render ONLY the zone (not the whole page) in grayscale: ~10x less
        # rasterisation work per call, and the blank check becomes a single
        # C-level pass over the bytes instead of a Python pixel loop.
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip,
                             colorspace=fitz.csGRAY, alpha=False)
        if pm.width < 1 or pm.height < 1:
            return True
        WHITE = 244
        return min(pm.samples) >= WHITE
    except Exception as e:
        print(f"  zone blank-check failed ({e}); assuming content", file=sys.stderr)
        return False


def _reserve_footer_strip(page, strip_h: float):
    """Rebuild `page` in its document so the existing content is scaled to fit
    the area ABOVE a `strip_h`-tall clean footer strip. Page SIZE is unchanged;
    the bottom strip is left blank for the signature(s). Returns the fresh page.

    The content is re-imported with show_pdf_page as a vector XObject, so
    quality is preserved and it survives a later insert_pdf (unlike a mediabox
    resize, which the page origin re-normalisation breaks on copy). The rebuilt
    page is upright — show_pdf_page bakes in any /Rotate of the source, so
    rotated pages need no special handling downstream.
    """
    doc = page.parent
    idx = page.number
    w = page.rect.width
    h = page.rect.height

    src = fitz.open()
    src.insert_pdf(doc, from_page=idx, to_page=idx)
    try:
        doc.delete_page(idx)
        new = doc.new_page(pno=idx, width=w, height=h)
        # Fit the original content into the top region, aspect preserved so the
        # text is never distorted (a thin side margin is acceptable).
        target = fitz.Rect(0, 0, w, h - strip_h)
        new.show_pdf_page(target, src, 0, keep_proportion=True)
    finally:
        src.close()
    return new


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

    Overlap protection (signatures must NEVER cover content): placement is
    decided from the ACTUAL rendered pixels, so it is content-agnostic — body
    text, embedded images, full-page scans and vector graphics are all caught.
      * Normal page (footer blank)            -> sign at the BOTTOM footer.
      * Dense page with a clear top margin     -> sign in the TOP header area.
      * Both ends busy (e.g. full-page scans)  -> reserve a clean bottom strip
        by scaling the content up (page size unchanged; survives insert_pdf;
        normalises /Rotate).

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
    LEFT_MARGIN = 60
    RIGHT_MARGIN = 60
    STRIP_H = 92.0      # footer / scan band height to test for content
    band_h = 60.0       # signature bounding-box height
    TOP_HEADER = 48.0   # below the page-number / annexure-label band

    # Use rotation-aware (visible) page dims everywhere.
    page_w = page.rect.width
    page_h = page.rect.height
    rotation = page.rotation

    # ── Placement (decided from ACTUAL rendered pixels, so any content type —
    # text, images, scans, vector graphics — is caught) ──
    #   * Normal page (footer blank)        -> sign at the BOTTOM footer.
    #   * Dense page, top header is clear    -> sign at the TOP (header area).
    #   * Both ends occupied (rare)          -> reserve a clean bottom strip by
    #                                           scaling the content up.
    TOP_BAND_H = 42.0   # signature is a bit smaller at the top (tighter margin)
    if _zone_is_blank(page, page_h - STRIP_H, page_h):
        # Normal page — bottom footer, as before.
        band_top = page_h - 14.0 - band_h
    elif _zone_is_blank(page, TOP_HEADER, TOP_HEADER + TOP_BAND_H + 6):
        # Dense page — put the signature in the clear top header area.
        band_h = TOP_BAND_H
        band_top = TOP_HEADER
        print("  dense page; signing in the top header area", file=sys.stderr)
    else:
        # Both ends busy — scale the content up and reserve a clean strip.
        try:
            page = _reserve_footer_strip(page, STRIP_H)
            page_w = page.rect.width
            page_h = page.rect.height
            rotation = page.rotation  # rebuilt page is upright
            print(
                f"  dense page (top also full); scaled content and reserved a "
                f"{STRIP_H:.0f}pt bottom strip",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"  strip reserve failed ({e}); stamping at the bottom", file=sys.stderr)
        band_top = page_h - 14.0 - band_h

    # Client (left)
    if client_sig_path:
        if not os.path.exists(client_sig_path):
            print(
                f"  client sig file missing on disk: {client_sig_path}",
                file=sys.stderr,
            )
        else:
            # Drop the white background to transparent so any content behind
            # shows through the gaps in the ink (no opaque backing card).
            render_path = _transparent_signature(client_sig_path)
            visible_rect = _fit_visible_rect_to_image(
                render_path, LEFT_MARGIN, band_top, SIG_MAX_W, band_h,
            )
            if visible_rect:
                mediabox_rect = _project_to_mediabox(page, visible_rect)
                try:
                    page.insert_image(
                        mediabox_rect,
                        filename=render_path,
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
        if not os.path.exists(advocate_sig_path):
            print(
                f"  advocate sig file missing on disk: {advocate_sig_path}",
                file=sys.stderr,
            )
            return
        render_path = _transparent_signature(advocate_sig_path)
        aspect = _read_image_aspect(render_path) or 1.0
        h = band_h
        w = h * aspect
        if w > SIG_MAX_W:
            w = SIG_MAX_W
            h = w / aspect
        right_x = page_w - RIGHT_MARGIN - w
        visible_rect = fitz.Rect(right_x, band_top, right_x + w, band_top + h)
        mediabox_rect = _project_to_mediabox(page, visible_rect)
        try:
            page.insert_image(
                mediabox_rect,
                filename=render_path,
                keep_proportion=True,
                rotate=rotation,
            )
        except Exception as e:
            print(
                f"  advocate sig insert failed "
                f"(rotation={rotation}, rect={mediabox_rect}): {e}",
                file=sys.stderr,
            )
