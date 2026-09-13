"""Push the finished run to Vercel Blob so the Rehearsal Room can show "last real run" (DEPLOY.md step 4).

Two PUTs after assert: last_run/<run_id>.json (Plan + end_state) and last_run/<run_id>.jsonl (trace).
Skipped silently when BLOB_READ_WRITE_TOKEN is unset. A failed upload is reported to the caller and
never blocks or fails a live run. stdlib only; this is not a live write to any of the four apps.
"""
from __future__ import annotations
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from owed.config import load_env

BLOB_BASE = "https://blob.vercel-storage.com"
TIMEOUT_S = 15


def _put(token: str, pathname: str, data: bytes, content_type: str) -> str:
    req = urllib.request.Request(
        f"{BLOB_BASE}/{pathname}", data=data, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-version": "7",
            "x-content-type": content_type,
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def push_last_run(run_id: str, plan_path: Path, trace_path: Path) -> Optional[str]:
    """Returns None when skipped (no token), 'ok' on success, or an error string. Never raises."""
    load_env()
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        return None
    try:
        _put(token, f"last_run/{run_id}.json", Path(plan_path).read_bytes(), "application/json")
        _put(token, f"last_run/{run_id}.jsonl", Path(trace_path).read_bytes(), "application/x-ndjson")
        return "ok"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        body = getattr(e, "read", None)
        detail = body().decode("utf-8", errors="replace")[:200] if callable(body) else str(e)
        return f"{type(e).__name__}: {detail}"
