"""Multi-volume PDF concatenation.

Sole responsibility: take N PDF paths and produce one merged PDF on disk.
Each input is opened, inserted, and immediately closed so RAM doesn't
balloon on big inputs (a 1000-page volume is held only for the duration
of its insert_pdf() call).
"""

import sys
from typing import List

from config import fitz


def merge_pdfs(input_paths: List[str], output_path: str) -> bool:
    """Concatenate `input_paths` (in order) into `output_path`.

    Returns True on success. Caller is responsible for cleanup of the
    input files.
    """
    if not fitz or not input_paths:
        return False
    out = None
    try:
        out = fitz.open()
        for p in input_paths:
            with fitz.open(p) as src:
                out.insert_pdf(src)
        out.save(output_path)
        return True
    except Exception as e:
        print(f"merge_pdfs: {e}", file=sys.stderr)
        return False
    finally:
        if out is not None:
            out.close()
