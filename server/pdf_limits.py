"""Hard page-count ceiling checked before any real PDF work starts.

The 500MB upload cap and the job timeout alone don't stop a small,
maliciously crafted PDF that declares a huge number of pages from tying up
the single worker. Opening a PDF only parses its xref, so counting pages is
cheap compared with the processing that follows.
"""

import sys

from config import fitz

# Well above real filings (the heaviest seen is a few hundred pages; large
# paper books run to the low thousands) but far below what would exhaust
# the dyno.
MAX_TOTAL_PAGES = 3000


def enforce_page_limit(paths):
    """Exit with a recognizable error if the inputs together exceed the cap."""
    if fitz is None:
        return
    total = 0
    for path in paths or []:
        if not path:
            continue
        with fitz.open(path) as doc:
            total += doc.page_count
        if total > MAX_TOTAL_PAGES:
            print(
                f"TOO_MANY_PAGES: these files have more than {MAX_TOTAL_PAGES} pages in total",
                file=sys.stderr,
            )
            sys.exit(2)
