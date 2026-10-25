"""Tests for the persistent worker's job dispatch (worker.py).

Runs _run_job directly (no actual HTTP socket) — this is the part that
has to behave EXACTLY like a real `python3 error_detector.py ...`
subprocess invocation would, since pdfWorker.ts's fallback-free path
depends on byte-for-byte stdout parity and correct exit codes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
import worker


def test_write_stdout_job_matches_direct_call(portrait_pdf, tmp_path):
    """The worker's in-process dispatch must produce the same rendered
    output as calling error_detector.main() directly (which is in turn
    what a real subprocess would have done)."""
    argv = [
        "--file", portrait_pdf,
        "--index-end-page", "0",
        "--mode", "write",
        "--write-stdout",
    ]
    exit_code, stdout_bytes, _stderr_text = worker._run_job("error_detector", argv)

    assert exit_code == 0
    assert stdout_bytes.startswith(b"%PDF")

    doc = fitz.open(stream=stdout_bytes, filetype="pdf")
    assert len(doc) == 1
    doc.close()


def test_json_job_returns_valid_json_on_stdout(portrait_pdf):
    exit_code, stdout_bytes, _ = worker._run_job(
        "bookmarks", ["detect", "--file", portrait_pdf],
    )
    assert exit_code == 0
    import json
    payload = json.loads(stdout_bytes.decode())
    assert payload["ok"] is True


def test_missing_required_arg_returns_nonzero_exit_and_stderr():
    """argparse's own error path (sys.exit(2) + usage on stderr) must
    survive being run in-process inside the worker, not crash the daemon."""
    exit_code, stdout_bytes, stderr_text = worker._run_job(
        "error_detector", ["--mode", "write", "--write-stdout"],
    )
    assert exit_code != 0
    assert stdout_bytes == b""
    assert "file" in stderr_text.lower()


def test_unknown_script_is_rejected_cleanly():
    exit_code, stdout_bytes, stderr_text = worker._run_job("not_a_real_script", [])
    assert exit_code == 1
    assert stdout_bytes == b""
    assert "unknown script" in stderr_text.lower()


def test_worker_recovers_after_a_failing_job(portrait_pdf):
    """sys.stdout/sys.stderr must be correctly restored after an error, so
    the NEXT job on the same long-lived process isn't corrupted — this is
    the exact failure mode a global stdout-redirect approach is at risk of."""
    worker._run_job("error_detector", ["--mode", "write", "--write-stdout"])  # fails

    exit_code, stdout_bytes, _ = worker._run_job(
        "bookmarks", ["detect", "--file", portrait_pdf],
    )
    assert exit_code == 0
    assert stdout_bytes  # got real output, not swallowed by a stale redirect
    assert not isinstance(sys.stdout, worker._CapturingStdout)
