"""GET /api/rehearse?scenario=<name>[&live=1] -> Plan JSON. SHADOW ONLY.

Default: serve the precomputed Plan instantly, from state/rehearsals/<name>.json if the
evals session has written it, else ui/fixtures/<name>.json. Response carries "cached": true.

With live=1: attempt a real shadow rehearsal (planner -> drafter -> verifier) through
owed.run.rehearse_shadow(scenario_dict) with a 40 s budget. If the agent module is not
present, the call fails, exceeds the budget, or any network library (stripe, googleapiclient,
slack_sdk) shows up in sys.modules afterwards, fall back to the precomputed file.

This module never imports stripe, googleapiclient, or slack_sdk. It performs no live write.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUDGET_S = 40
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
FORBIDDEN_MODULES = ("stripe", "googleapiclient", "slack_sdk")


def _precomputed(name: str) -> tuple[dict | None, str]:
    for source, folder in (("precomputed", os.path.join(ROOT, "state", "rehearsals")),
                           ("fixture", os.path.join(ROOT, "ui", "fixtures"))):
        path = os.path.join(folder, f"{name}.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("scenario", name)
            data["source"] = data.get("source", source)
            data["cached"] = True
            return data, source
    return None, "missing"


def _live_rehearsal(name: str) -> dict:
    """Runs the agent against Shadow adapters. Raises on any problem; the caller falls back."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    scenario_path = os.path.join(ROOT, "scenarios", f"{name}.json")
    if not os.path.isfile(scenario_path):
        raise FileNotFoundError(f"scenarios/{name}.json not present")
    with open(scenario_path, encoding="utf-8") as fh:
        scenario = json.load(fh)

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import importlib
    run_mod = importlib.import_module("owed.run")
    rehearse_shadow = getattr(run_mod, "rehearse_shadow")
    plan = rehearse_shadow(scenario)

    leaked = [m for m in FORBIDDEN_MODULES if m in sys.modules]
    if leaked:
        raise RuntimeError(f"network libraries imported during shadow rehearsal: {leaked}")

    if hasattr(plan, "to_json"):
        result = json.loads(plan.to_json())
    elif isinstance(plan, dict):
        result = plan
    else:
        raise TypeError(f"rehearse_shadow returned {type(plan).__name__}, expected Plan or dict")
    result["scenario"] = name
    result["source"] = "live"
    result["cached"] = False
    return result


def _with_budget(fn, *args, budget_s: float):
    box: dict = {}

    def target():
        try:
            box["value"] = fn(*args)
        except Exception as exc:  # noqa: BLE001 - reported to caller, never swallowed silently
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(budget_s)
    if worker.is_alive():
        return None, f"live rehearsal exceeded {budget_s:.0f}s budget"
    if "error" in box:
        return None, box["error"]
    return box["value"], None


def rehearse(name: str, live: bool) -> tuple[int, dict]:
    started = time.monotonic()
    if not NAME_RE.match(name or ""):
        return 400, {"error": "scenario must match ^[a-z0-9_-]{1,64}$"}

    live_error = None
    if live:
        result, live_error = _with_budget(_live_rehearsal, name, budget_s=LIVE_BUDGET_S)
        if result is not None:
            result["served_ms"] = int((time.monotonic() - started) * 1000)
            return 200, result

    data, source = _precomputed(name)
    if data is None:
        return 404, {"error": f"no scenario named {name!r}", "live_error": live_error}
    if live_error:
        data["live_error"] = live_error
    data["served_ms"] = int((time.monotonic() - started) * 1000)
    return 200, data


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel Python runtime convention)
        query = parse_qs(urlparse(self.path).query)
        name = (query.get("scenario") or [""])[0].strip().lower()
        live = (query.get("live") or ["0"])[0] in ("1", "true", "yes")
        status, payload = rehearse(name, live)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
