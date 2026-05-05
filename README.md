# Nomikos Backend (`NO-MO-KOS-BACK`)

> **Live:** https://no-mi-kos-back.onrender.com · `GET /api/health` returns OK when the service is awake.

Express HTTP layer + Python PDF engine. The PDF heavy-lifting (redaction,
stamping, merging, OCR) lives in the Python modules under `server/`; the
TypeScript file `server.ts` is a thin adapter that routes multipart
requests into a Python subprocess and pipes its stdout back to the
client.

Pairs with [`NO-MI-KOS-FRONT`](https://github.com/shashankjohri07/NO-MI-KOS-FRONT).
See the [project-level README](../README.md) for the full picture.

---

## What lives where

```
server.ts                Express server. One streaming POST route
                         (/api/write-pagination), one health probe,
                         one dormant /api/detect-errors JSON endpoint.

server/error_detector.py CLI orchestrator. Parses args, calls the right
                         pipeline, dumps either streaming bytes or JSON.

server/config.py         Shared constants and library-import shims.
                         All other modules `from config import …`.

server/extraction.py     extract_pages, OCR helpers, top-right
                         page-number reader. Detect/both modes only.

server/pagination.py     write_pagination (redact + restamp) and
                         check_rule_pagination (read-only rule).

server/annexures.py      stamp_annexure_label and append_annexures_to_pdf.

server/signatures.py     stamp_signatures_on_page with PIL-based
                         aspect-ratio preservation.

server/merge.py          merge_pdfs — multi-volume concatenation.

server/rules.py          DOC_UPLOAD keyword rule (dormant).

server/annotated_pdf.py  generate_annotated_pdf for detect mode (dormant).
```

---

## Local development

```bash
npm install
pip install pymupdf pytesseract pillow   # python deps
brew install tesseract                   # OCR fallback (macOS)
npm run dev                              # tsx server.ts → :3001
```

The server.ts spawn passes `cwd=server/` so the Python modules' bare
imports (`from extraction import …`) resolve. The CLI also works
directly: `error_detector.py` prepends its own directory to `sys.path`
so the modules import either way.

---

## CLI reference

```
python3 server/error_detector.py
  --file PATH                    main PDF (repeatable, max 5 in HTTP route)
  --annex PATH                   annexure PDF (repeatable, max 20 in HTTP)
  --client-sig PATH              PNG/JPG, stamped bottom-LEFT of every annexure page
  --advocate-sig PATH            PNG/JPG, stamped bottom-RIGHT of every annexure page
  --index-end-page N             1-indexed last page of index. Default 0.
  --mode {detect,write,both}     write = fast path (no extraction, no rules).
                                 detect/both = run rules + produce annotated PDF.
  --write-stdout                 stream PDF bytes on stdout instead of base64 JSON.
                                 Only valid with --mode write.
```

Examples:

```bash
# Number a single document
python3 server/error_detector.py --file vol-1.pdf --index-end-page 2 --mode write

# Full pipeline, streaming
python3 server/error_detector.py \
  --file v1.pdf --file v2.pdf \
  --annex order.pdf --annex resolution.pdf \
  --client-sig client.png --advocate-sig adv.png \
  --index-end-page 2 --mode write --write-stdout > final.pdf

# Detect rules only (dormant; for debugging)
python3 server/error_detector.py --file v1.pdf --index-end-page 2 --mode detect | jq
```

---

## API surface (HTTP)

### `POST /api/write-pagination`
Multipart/form-data → `application/pdf` stream.

| Field | Required | Notes |
|---|---|---|
| `document` | yes | Main-volume PDFs (max 5) |
| `annex` | no | Annexure PDFs (max 20). File 1 → `Annexure A-1`, etc. |
| `clientSignature` | no | PNG/JPG. Bottom-LEFT of every annexure page. |
| `advocateSignature` | no | PNG/JPG. Bottom-RIGHT of every annexure page. |
| `indexEndPage` | no | 1-indexed; pages 1..N skipped from numbering. Default 0. |

The Express route forwards each field to the Python CLI via the matching
flag, then pipes Python stdout straight to the response. Cleanup of all
upload files happens on `proc.close`.

### `GET /api/health`
Liveness probe. Frontend pings this on page mount to wake up the dyno.

### `POST /api/detect-errors`
Dormant — returns the full detect/both JSON report (rules, annotated
PDF, base64 paginated PDF). No production UI hits it; kept for CLI
parity and future use.

---

## Deployment

`render.yaml` declares a Docker web service. Render auto-deploys on push
to `main`. If a deploy doesn't pick up, trigger **Manual Deploy → Deploy
latest commit** in the Render dashboard.

Live URL: https://no-mi-kos-back.onrender.com

The Render free dyno sleeps after ~15 min idle. Cold start adds ~10 s
on the first request. The frontend mitigates this with a `/api/health`
ping when its page mounts.

---

## Design notes

See the project-level [README's "Design decisions worth remembering"
section](../README.md#design-decisions-worth-remembering) for the
reasoning behind:

- redaction over white rectangles (existing wrong numbers actually
  removed, not just hidden)
- per-image aspect-ratio computation for signatures (no distortion)
- streaming `application/pdf` over base64-in-JSON (memory-flat in Node)
- annexure label position at y=40 (top header band)
- "one annexure file = one annexure" (user owns the boundary decision)
