"""Shared constants and library-import shims used across the scanner modules.

Splitting these out keeps every other module's top-of-file noise low and gives
a single place to tweak knobs (DPI, page-number regex zones, annotation colors).

The fitz / pytesseract imports are wrapped with the same auto-install + soft-
import behaviour the original monolith used, so any module can do
`from .config import fitz, TESSERACT_AVAILABLE` and not have to repeat the
try/except dance. `fitz` may be None if PyMuPDF can't be installed; callers
must guard.
"""

import sys

# --- PyMuPDF (hard dependency for everything we do) ----------------------
try:
    import fitz  # type: ignore
except ImportError:
    import subprocess
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pymupdf", "-q"],
            stderr=subprocess.DEVNULL,
        )
        import fitz  # type: ignore
    except Exception:
        fitz = None  # type: ignore

# --- Tesseract OCR (soft dependency — scanned-PDF fallback only) ----------
try:
    import pytesseract  # type: ignore # noqa: F401
    from PIL import Image  # type: ignore # noqa: F401
    import io  # noqa: F401

    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# --- OCR knobs ------------------------------------------------------------
TESSERACT_CONFIG = "--oem 3 --psm 6"
# PSM 7 = single line; ideal for top-right page-number strips.
TESSERACT_CONFIG_SINGLE_LINE = (
    "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789"
)
TESSERACT_DPI = 200
# Higher DPI on a tiny corner crop is cheap and noticeably more accurate.
TESSERACT_PAGENUM_DPI = 300
# A page with fewer than this many text chars is treated as scanned and
# routed through Tesseract.
MIN_TEXT_CHARS = 50

# --- Annotation colours (for the dormant detect-mode annotated PDF) -------
COLOR_ERROR = (1, 0.2, 0.2)   # Red
COLOR_PASS = (0.2, 0.7, 0.3)  # Green
