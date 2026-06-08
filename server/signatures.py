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
never cover that content. We check the footer zone's ACTUAL rendered
pixels (so text, images, scans and vector graphics are all detected);
if it isn't blank, the page content is scaled up into the area above a
reserved footer strip (page size unchanged) and the signature lands in
that clean strip — robust for any signature (including opaque photos),
survives insert_pdf (the page is rebuilt, not mediabox-hacked), and
normalises /Rotate.

Transparency: the signature's white/near-white background is dropped to
transparent (chroma-key) before stamping, and no opaque backing card is
drawn. So in the rare case the signature does sit over content, the text
shows THROUGH the gaps in the ink instead of being hidden. Best for dark
ink scanned on white paper; full-colour photos/logos have no white
background to drop and should be supplied as ready-made transparent PNGs.
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
        try:
            # Fast vectorised path when numpy is available.
            import numpy as _np
            arr = _np.array(img)
            mask = (
                (arr[:, :, 0] >= WHITE)
                & (arr[:, :, 1] >= WHITE)
                & (arr[:, :, 2] >= WHITE)
            )
            arr[:, :, 3][mask] = 0
            img = PILImage.fromarray(arr, "RGBA")
        except ImportError:
            # Pure-PIL fallback (slower, but cached so it runs once).
            px = img.load()
            w, h = img.size
            for yy in range(h):
                for xx in range(w):
                    r, g, b, a = px[xx, yy]
                    if r >= WHITE and g >= WHITE and b >= WHITE:
                        px[xx, yy] = (r, g, b, 0)

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


def _footer_is_blank(page, strip_h: float) -> bool:
    """True if the bottom `strip_h` (visible) band of the page renders blank
    (near-white). Decides from the ACTUAL rendered pixels, so it cannot be
    fooled by content type — body text, embedded images, full-page scans and
    vector drawings are all detected (a text-only scan silently missed
    image/scanned annexures, which is how signatures ended up over content).

    Rotation-agnostic: get_pixmap renders the visible (rotation-applied) view,
    so the pixmap's bottom rows ARE the visible footer regardless of /Rotate.
    On any error we return False (assume content) so the caller reserves a
    clean strip rather than risk stamping over something.
    """
    try:
        zoom = 0.4  # low-res is plenty for a blank / not-blank decision
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        band_rows = int(strip_h * zoom)
        if band_rows < 1 or pm.height < 1 or pm.width < 1:
            return True
        data = pm.samples
        stride = pm.stride
        n = pm.n
        WHITE = 244
        for y in range(max(0, pm.height - band_rows), pm.height):
            base = y * stride
            for x in range(0, pm.width, 2):  # subsample columns for speed
                off = base + x * n
                if data[off] < WHITE or data[off + 1] < WHITE or data[off + 2] < WHITE:
                    return False
        return True
    except Exception as e:
        print(f"  footer blank-check failed ({e}); assuming content", file=sys.stderr)
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

    Overlap protection (signatures must NEVER cover content): looks at the
    ACTUAL rendered pixels of the footer zone — content-agnostic, so body
    text, embedded images, full-page scans and vector graphics are all caught
    (a text-only scan used to miss image/scanned annexures). If the footer is
    already blank the signature is stamped there; otherwise a clean footer
    strip is reserved by scaling the page content up into the area above it
    (page size unchanged, works for any signature incl. opaque photos,
    survives insert_pdf, and normalises /Rotate).

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
    STRIP_H = 92.0    # clean footer strip the signature lives in
    band_h = 60.0     # signature bounding-box height (consistent on every page)

    # Use rotation-aware (visible) page dims everywhere.
    page_w = page.rect.width
    page_h = page.rect.height
    rotation = page.rotation

    # ── Placement: the signature must NEVER cover content ──
    # Decide from the ACTUAL rendered pixels of the footer zone, so it is fully
    # content-agnostic — body text, embedded images, full-page scans and vector
    # graphics are all caught (a text-only scan silently missed image/scanned
    # annexures, which is how signatures ended up over content):
    #   * footer already blank -> stamp the signature there, page untouched.
    #   * footer has content   -> reserve a clean strip by scaling the page
    #     content up into the area above it. Page size stays the same, works
    #     for ANY signature (incl. opaque photos), survives insert_pdf (the
    #     page is rebuilt, not mediabox-hacked) and normalises /Rotate.
    if not _footer_is_blank(page, STRIP_H):
        try:
            page = _reserve_footer_strip(page, STRIP_H)
            page_w = page.rect.width
            page_h = page.rect.height
            rotation = page.rotation  # rebuilt page is upright
            print(
                f"  footer had content; scaled page and reserved a "
                f"{STRIP_H:.0f}pt strip for the signature",
                file=sys.stderr,
            )
        except Exception as e:
            print(
                f"  footer-strip reserve failed ({e}); stamping at the bottom",
                file=sys.stderr,
            )

    # Signature band sits inside the (now guaranteed-clean) footer strip.
    band_top = page_h - 14.0 - band_h

    # Client (left)
    if client_sig_path:
        import os as _os
        if not _os.path.exists(client_sig_path):
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
        import os as _os
        if not _os.path.exists(advocate_sig_path):
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
