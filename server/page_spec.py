"""Parser for user-supplied page-range strings.

The "sign specified pages" feature lets a user say which post-pagination
page numbers (i.e. the stamped digits in the top-right corner) should
also receive client/advocate signatures, in addition to the every-page
stamping that already happens on annexures.

Accepted format (whitespace tolerant):

    "1"            → {1}
    "1, 3, 5"      → {1, 3, 5}
    "1-3"          → {1, 2, 3}
    "1, 3-5, 8"    → {1, 3, 4, 5, 8}
    "10-12, 12"    → {10, 11, 12}         (duplicates deduped)
    " "            → set()                 (empty/blank is OK)
    "5-3"          → {3, 4, 5}             (reversed range tolerated)

Rejected (raises ValueError so the API layer can surface a clean
4xx instead of failing silently):

    "abc"          → ValueError
    "1-2-3"        → ValueError            (malformed range)
    "1,,2"         → fine (empty tokens skipped)
    "-3"           → ValueError            (no left bound)
    "0"            → silently dropped      (pages are 1-indexed)
    "-5"           → silently dropped      (negatives are nonsense)

Bound-checking against actual document length is the caller's job — this
parser stays pure so it's trivial to unit-test.
"""

from typing import Set


class PageSpecError(ValueError):
    """Raised when the input string can't be parsed into a page set."""


def parse_page_spec(spec: str) -> Set[int]:
    """Parse `spec` into a set of 1-indexed page numbers.

    Raises PageSpecError on malformed input. Returns empty set for
    None/empty/whitespace-only input.
    """
    if spec is None:
        return set()
    text = spec.strip()
    if not text:
        return set()

    out: Set[int] = set()
    tokens = [t.strip() for t in text.split(",")]
    for tok in tokens:
        if not tok:
            # "1,,2" — tolerate stray commas.
            continue
        if "-" in tok:
            parts = tok.split("-")
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise PageSpecError(
                    f"Bad range '{tok}'. Use 'a-b' with both ends, "
                    f"e.g. '3-5'."
                )
            try:
                lo = int(parts[0].strip())
                hi = int(parts[1].strip())
            except ValueError:
                raise PageSpecError(
                    f"Range '{tok}' contains non-numeric values."
                )
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                if n > 0:
                    out.add(n)
        else:
            try:
                n = int(tok)
            except ValueError:
                raise PageSpecError(
                    f"'{tok}' is not a number. Use comma-separated pages "
                    f"and ranges, e.g. '1, 3-5, 8'."
                )
            if n > 0:
                out.add(n)
    return out


def format_page_set(pages: Set[int]) -> str:
    """Render a sorted page set back into a compact human string with
    ranges collapsed.  Mirror of parse_page_spec — useful for log lines
    and the "Will sign pages: …" preview the UI will eventually show.

    {1, 3, 4, 5, 8, 12, 13, 14, 15} → "1, 3-5, 8, 12-15"
    """
    if not pages:
        return ""
    sorted_pages = sorted(pages)
    chunks = []
    run_start = run_end = sorted_pages[0]
    for n in sorted_pages[1:]:
        if n == run_end + 1:
            run_end = n
        else:
            chunks.append(
                str(run_start) if run_start == run_end else f"{run_start}-{run_end}"
            )
            run_start = run_end = n
    chunks.append(
        str(run_start) if run_start == run_end else f"{run_start}-{run_end}"
    )
    return ", ".join(chunks)
