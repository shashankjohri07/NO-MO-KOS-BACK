"""Persistent PDF-pipeline worker.

Why this exists: the old model had Node spawn a brand-new `python3`
process for EVERY upload, which pays PyMuPDF/PIL/pytesseract's C-extension
import cost (and tesseract binary discovery) on every single request. That
is the single biggest latency/CPU cost on a free-tier single-core dyno.

This process starts ONCE (launched by server.ts at boot, see pdfWorker.ts),
imports everything up front, and then serves jobs over a local HTTP socket
that never leaves 127.0.0.1. Node's spawn() call sites are unchanged in
shape — pdfWorker.ts gives them a drop-in replacement for
child_process.spawn() that talks to this process instead, and transparently
falls back to a real subprocess spawn if this worker is ever unreachable.

Concurrency is deliberately ONE job at a time: http.server.HTTPServer
(no ThreadingMixIn) handles requests sequentially on its own accept loop,
which (a) avoids the correctness hazard of multiple jobs fighting over the
process-global sys.stdout/sys.stderr redirect below, and (b) matches what a
single-core free-tier dyno can actually do in parallel anyway — today's
per-process model already serializes on CPU, just with worse overhead.
Requests that arrive while a job is running simply wait in the OS accept
queue, which IS the job queue; pdfWorker.ts adds an explicit queue-depth
cap on top so the client gets a fast 503 instead of hanging forever under
real overload.
"""

import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bookmarks  # noqa: E402
import error_detector  # noqa: E402
import index_page  # noqa: E402

_SCRIPTS = {
    "error_detector": error_detector.main,
    "bookmarks": bookmarks.main,
    "index_page": index_page.main,
}


class _CapturingStdout:
    """Stands in for sys.stdout during a job. Scripts in this codebase
    write BOTH text (`print(...)`) and raw bytes (`sys.stdout.buffer.write`)
    depending on the code path, so this captures both into one buffer in
    the order they were written — exactly what a real subprocess's stdout
    pipe would see."""

    def __init__(self):
        self._buf = io.BytesIO()
        self.buffer = self  # `sys.stdout.buffer.write(bytes)` lands here too

    def write(self, data):
        if isinstance(data, bytes):
            self._buf.write(data)
        else:
            self._buf.write(data.encode("utf-8", errors="replace"))

    def flush(self):
        pass

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _run_job(script: str, argv: list) -> tuple:
    """Runs one job in-process with stdout/stderr captured, mirroring
    exactly what `python3 <script>.py <argv>` as a subprocess would
    produce on its stdout/stderr/exit-code. Returns
    (exit_code, stdout_bytes, stderr_text).
    """
    main_fn = _SCRIPTS.get(script)
    if main_fn is None:
        return 1, b"", f"unknown script: {script}"

    real_stdout, real_stderr = sys.stdout, sys.stderr
    captured_out = _CapturingStdout()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err
    exit_code = 0
    try:
        main_fn(argv)
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    except Exception as e:  # must never take the daemon down
        exit_code = 1
        print(f"worker: unhandled exception running {script}: {e}", file=captured_err)
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr

    stderr_text = captured_err.getvalue()
    # Still surface stderr in the daemon's OWN logs (Render log stream),
    # exactly like today's per-process stderr piping did.
    if stderr_text:
        for line in stderr_text.splitlines():
            print(f"  [{script}] {line}", file=real_stderr)

    return exit_code, captured_out.getvalue(), stderr_text


class Handler(BaseHTTPRequestHandler):
    # Keep http.server's own access log out of the way; worker.py's own
    # prints (and the per-job stderr relay above) are the useful signal.
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/ping":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            script = payload.get("script", "")
            argv = payload.get("argv", [])
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"bad request: {e}".encode())
            return

        exit_code, stdout_bytes, stderr_text = _run_job(script, argv)

        # stderr goes back as a header (base64 — header values must be
        # latin-1-safe and this text is log output, not large, by far the
        # common case; absolute worst case a very chatty run gets truncated
        # here, but the full text is still in the worker's own stderr log
        # above). stdout is the raw response body, byte-for-byte what a
        # subprocess's stdout pipe would have delivered.
        import base64
        stderr_b64 = base64.b64encode(stderr_text.encode("utf-8")[:16_000]).decode("ascii")

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(stdout_bytes)))
        self.send_header("X-Exit-Code", str(exit_code))
        self.send_header("X-Stderr-B64", stderr_b64)
        self.end_headers()
        self.wfile.write(stdout_bytes)


def main():
    port = int(os.environ.get("NOMIKOS_WORKER_PORT", "8991"))
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[worker] ready on 127.0.0.1:{port} (pid {os.getpid()})", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
