"""Annexure cover labelling and append flow.

Each uploaded annexure file becomes one annexure: file 1 → "Annexure A-1",
file 2 → "Annexure A-2", and so on. The label is stamped on the annexure's
own first page BEFORE the file is concatenated onto the merged main PDF —
that way we don't have to track page-offset arithmetic in the merged doc.

Optional client/advocate signatures, if provided, are stamped on EVERY
page of every annexure (court-filing convention). Stamping happens on
the source annexure document before the insert_pdf merge, mirroring the
label flow.

Rotation handling: scanned annexure PDFs (court orders, certificates)
often carry a /Rotate flag set to 90/180/270 — the viewer rotates them
into portrait, but PyMuPDF's insert_text/insert_image use the unrotated
mediabox coordinate system by default. We compensate by transforming
visible coords through page.derotation_matrix and passing
rotate=page.rotation so stamps appear correctly oriented at the visible
"top" regardless of the source page's /Rotate value.
"""

import os
import sys
from typing import List, Optional

from config import fitz
from signatures import stamp_signatures_on_page


def stamp_annexure_label(page, label: str) -> None:
    """Stamp `label` (e.g. "Annexure A-1") horizontally centered at the very
    top of the page (in the page's VISIBLE orientation), sharing the top
    header band with the page-number digit. Bold, large, black.

    Works correctly on pages with non-zero /Rotate flag: visible coords
    are projected to mediabox coords via page.derotation_matrix, and
    rotate=page.rotation aligns the glyph orientation with the rendered
    view.

    Geometry: page-number digit lives in y∈[28, 44] (top-right). Annexure
    baseline at y=40 → visible glyph spans roughly y=17..47. Horizontally
    separated: digit at x≈552, label centered around x≈300.
    """
    if not fitz:
        return
    FONTSIZE = 22
    FONTNAME = "hebo"  # Helvetica-Bold (PyMuPDF built-in)
    Y_BASELINE = 40

    try:
        text_w = fitz.get_text_length(label, fontsize=FONTSIZE, fontname=FONTNAME)
        # page.rect IS rotation-aware → reflects the visible page dimensions
        visible_w = page.rect.width
        x_visible = (visible_w - text_w) / 2

        # Project visible-coord point to mediabox coord that insert_text needs.
        # For rotation==0 this is a no-op (derotation_matrix is identity).
        point_visible = fitz.Point(x_visible, Y_BASELINE)
        point_mediabox = point_visible * page.derotation_matrix

        page.insert_text(
            point_mediabox,
            label,
            fontsize=FONTSIZE,
            fontname=FONTNAME,
            color=(0, 0, 0),
            rotate=page.rotation,
        )
    except Exception as e:
        print(
            f"  stamp_annexure_label failed for '{label}' "
            f"(rotation={getattr(page, 'rotation', '?')}): {e}",
            file=sys.stderr,
        )


def append_annexures_to_pdf(
    base_path: str,
    annexure_paths: List[str],
    output_path: str,
    client_sig_path: Optional[str] = None,
    advocate_sig_path: Optional[str] = None,
) -> bool:
    """Append each annexure file to base_path with a centered "Annexure A-N"
    label stamped on the annexure's first page. N is sequential starting at 1.

    If client_sig_path and/or advocate_sig_path are provided, signatures are
    stamped in the footer of EVERY page of every annexure.

    Returns True on success.
    """
    if not fitz:
        return False
    if not annexure_paths:
        try:
            with fitz.open(base_path) as doc:
                doc.save(output_path)
            return True
        except Exception as e:
            print(f"append_annexures: copy failed: {e}", file=sys.stderr)
            return False

    out = None
    try:
        out = fitz.open(base_path)
        for idx, path in enumerate(annexure_paths, start=1):
            label = f"Annexure A-{idx}"
            try:
                annex = fitz.open(path)
            except Exception as e:
                print(f"  could not open annexure {path}: {e}", file=sys.stderr)
                continue
            try:
                if len(annex) == 0:
                    continue

                # Cover-page header label.
                stamp_annexure_label(annex[0], label)

                # Footer signatures on every page of this annexure.
                if client_sig_path or advocate_sig_path:
                    sides = []
                    if client_sig_path: sides.append("client")
                    if advocate_sig_path: sides.append("advocate")
                    print(
                        f"  stamping {'+'.join(sides)} sig on {len(annex)} "
                        f"page(s) of {os.path.basename(path)}",
                        file=sys.stderr,
                    )
                    for p_idx in range(len(annex)):
                        stamp_signatures_on_page(
                            annex[p_idx], client_sig_path, advocate_sig_path,
                        )
                else:
                    print(
                        f"  no sig paths passed — skipping signature stamping "
                        f"on {os.path.basename(path)}",
                        file=sys.stderr,
                    )

                out.insert_pdf(annex)
            finally:
                annex.close()

            print(
                f"  appended annexure {idx}: {os.path.basename(path)} (label '{label}')",
                file=sys.stderr,
            )

        out.save(output_path, garbage=3, deflate=True)
        return True
    except Exception as e:
        print(f"append_annexures error: {e}", file=sys.stderr)
        return False
    finally:
        if out is not None:
            out.close()
