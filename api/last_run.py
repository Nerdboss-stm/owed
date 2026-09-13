"""GET /api/last_run -> the latest real run, fetched from Vercel Blob under key prefix last_run/.

run.py (local machine, behind the Slack tap) PUTs two objects after assert:
  last_run/<run_id>.json    the Plan (+ end_state if present)
  last_run/<run_id>.jsonl   the trace, one TraceLine per line

This function only READS Blob with BLOB_READ_WRITE_TOKEN. Reads may retry once; there are no
writes here. Stdlib only. Without the token it answers {"available": false} and the page says so.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

BLOB_LIST_URL = "https://blob.vercel-storage.com/?prefix=last_run/&limit=100"
TIMEOUT_S = 8


def _get(url: str, headers: dict | None = None, attempts: int = 2) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(0.5)
    raise RuntimeError(f"read failed after {attempts} attempts: {last_exc}")


def _parse_jsonl(raw: bytes) -> list[dict]:
    lines: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            lines.append(json.loads(line))
    return lines


def fetch_last_run() -> dict:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        return {"available": False, "reason": "BLOB_READ_WRITE_TOKEN not set on this deployment"}

    listing = json.loads(_get(BLOB_LIST_URL, {"Authorization": f"Bearer {token}"}))
    blobs = listing.get("blobs", [])
    if not blobs:
        return {"available": False, "reason": "no objects under last_run/ yet"}

    blobs.sort(key=lambda b: b.get("uploadedAt", ""), reverse=True)
    plan_blob = next((b for b in blobs if b["pathname"].endswith(".json")), None)
    trace_blob = next((b for b in blobs if b["pathname"].endswith(".jsonl")), None)

    out: dict = {
        "available": True,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "uploaded_at": (plan_blob or trace_blob or {}).get("uploadedAt"),
        "plan": None,
        "trace": [],
    }
    if plan_blob:
        out["plan"] = json.loads(_get(plan_blob["url"]))
        out["run_id"] = out["plan"].get("run_id")
    if trace_blob:
        out["trace"] = _parse_jsonl(_get(trace_blob["url"]))
        out.setdefault("run_id", out["trace"][0].get("run_id") if out["trace"] else None)
    return out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel Python runtime convention)
        try:
            payload = fetch_last_run()
        except Exception as exc:  # noqa: BLE001 - surfaced to the page, not hidden
            payload = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
