"""GET /api/scenarios -> list of scenario names + one-line descriptions.

Reads scenarios/*.json when the evals session has landed them, and ui/fixtures/*.json
otherwise. Stdlib only. No network, no adapters, no writes.
"""
from __future__ import annotations

import glob
import json
import os
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = (("scenario", os.path.join(ROOT, "scenarios")),
           ("fixture", os.path.join(ROOT, "ui", "fixtures")))


def list_scenarios() -> list[dict]:
    """The ten scenarios/*.json when they exist (their 01_..10_ prefixes are the SPEC order);
    the ui/fixtures set only as a fallback for a checkout without scenarios/."""
    out: list[dict] = []
    seen: set[str] = set()
    for source, folder in SOURCES:
        if out:
            break
        for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
            name = os.path.splitext(os.path.basename(path))[0]
            if name.startswith("_") or name in seen:
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                continue
            seen.add(name)
            out.append({
                "name": name,
                "order": data.get("order", 999),
                "title": data.get("title") or data.get("row") or name.replace("_", " "),
                "description": data.get("description", ""),
                "expected": data.get("expected", ""),
                "source": source,
            })
    out.sort(key=lambda s: (s["order"], s["name"]) if s["source"] == "fixture" else (0, s["name"]))
    return out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel Python runtime convention)
        body = json.dumps({"scenarios": list_scenarios()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
