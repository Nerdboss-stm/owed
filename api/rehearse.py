"""GET /api/rehearse?scenario=<name>[&live=1] -> Plan JSON. SHADOW ONLY.

Order of preference for a scenario name:
  1. state/rehearsals/<name>.json   precomputed by evals, served as-is            source "precomputed"
  2. scenarios/<name>.json          run owed.run.rehearse_shadow(offline=True):   source "shadow"
                                    template draft, deterministic gate + verifier, no model, no network
     with live=1                    the same with the actor and verifier models   source "live"
  3. ui/fixtures/<name>.json        hand-written Plan fixtures                    source "precomputed"

The response is the Plan (intents, refusals) plus invoices, title, expected and trace lines so the
freelancer page can draw the overdue list and the timeline. A 40 s budget guards the model path; on
timeout or failure the offline result is returned with "live_error" set.

This module never imports stripe, googleapiclient, or slack_sdk, and checks after every shadow
rehearsal that none of them was imported. It performs no live write.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_BUDGET_S = 40
NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
FORBIDDEN_MODULES = ("stripe", "googleapiclient", "slack_sdk")
TRACES_TMP = Path(os.environ.get("TMPDIR", "/tmp")) / "owed_api_traces"


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _precomputed(name: str) -> dict | None:
    for folder in (os.path.join(ROOT, "state", "rehearsals"), os.path.join(ROOT, "ui", "fixtures")):
        path = os.path.join(folder, f"{name}.json")
        if os.path.isfile(path):
            data = _load(path)
            data.setdefault("scenario", name)
            data["source"] = "precomputed"
            data["cached"] = True
            return data
    return None


def _shadow_rehearsal(name: str, live: bool) -> dict:
    """scenarios/<name>.json through the real planner/drafter/verifier against Shadow adapters."""
    scenario_path = os.path.join(ROOT, "scenarios", f"{name}.json")
    if not os.path.isfile(scenario_path):
        raise FileNotFoundError(f"scenarios/{name}.json not present")
    scenario = _load(scenario_path)
    if live and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if live:
        os.environ.pop("OWED_OFFLINE", None)  # rehearse_shadow(offline=True) leaves this set in a warm instance
    from owed.run import rehearse_shadow

    TRACES_TMP.mkdir(parents=True, exist_ok=True)
    run_id = f"api-{name}-{int(time.time())}"
    plan = rehearse_shadow(scenario, run_id=run_id, offline=not live, traces_dir=TRACES_TMP)

    leaked = [m for m in FORBIDDEN_MODULES if m in sys.modules]
    if leaked:
        raise RuntimeError(f"network libraries imported during shadow rehearsal: {leaked}")

    result = json.loads(plan.to_json())
    trace_path = TRACES_TMP / f"{run_id}.jsonl"
    result["trace"] = [json.loads(l) for l in trace_path.read_text().splitlines() if l.strip()] if trace_path.exists() else []
    today = scenario.get("today")
    result["invoices"] = [dict(i, days_overdue=_days(i.get("due_date"), today)) for i in scenario.get("invoices", [])]
    result["scenario"] = name
    result["title"] = scenario.get("row") or scenario.get("title") or name
    result["expected"] = scenario.get("expected", "")
    result["description"] = scenario.get("description", "")
    result["source"] = "live" if live else "shadow"
    result["cached"] = False
    return result


def _days(due: str | None, today: str | None) -> int | None:
    from datetime import date
    if not due or not today:
        return None
    return max(0, (date.fromisoformat(today) - date.fromisoformat(due)).days)


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

    errors: list[str] = []
    has_scenario = os.path.isfile(os.path.join(ROOT, "scenarios", f"{name}.json"))
    precomputed = None if has_scenario and live else _precomputed(name)

    if precomputed is None and has_scenario:
        if live:
            result, err = _with_budget(_shadow_rehearsal, name, True, budget_s=LIVE_BUDGET_S)
            if result is not None:
                result["served_ms"] = int((time.monotonic() - started) * 1000)
                return 200, result
            errors.append(err)
        result, err = _with_budget(_shadow_rehearsal, name, False, budget_s=LIVE_BUDGET_S)
        if result is not None:
            if errors:
                result["live_error"] = errors[0]
            result["served_ms"] = int((time.monotonic() - started) * 1000)
            return 200, result
        errors.append(err)
        precomputed = _precomputed(name)

    if precomputed is None:
        return 404, {"error": f"no scenario named {name!r}", "errors": errors}
    if errors:
        precomputed["live_error"] = errors[0]
    precomputed["served_ms"] = int((time.monotonic() - started) * 1000)
    return 200, precomputed


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel Python runtime convention)
        query = parse_qs(urlparse(self.path).query)
        name = (query.get("scenario") or [""])[0].strip().lower()
        live = (query.get("live") or ["0"])[0] in ("1", "true", "yes")
        status, payload = rehearse(name, live)
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
