"""
The one place the evals session touches the agent.

ASSUMED AGENT INTERFACE — adjust HERE at merge, never in tests/ or run_evals.py.
The core session owns these modules; until they are merged every AC test xfails
with the reason from `missing_reason()`.

run.py (repo root)
    run(*, ledger, inbox, calendar, chat, today: date, run_id: str, mandate: dict,
        state_dir: Path, traces_dir: Path,
        drafter: Callable[[Invoice, Step, dict], dict] | None = None,
        rehearse_only: bool = False) -> Any
      - adapters are the contract ABCs; the run snapshots them into Shadow copies
        for rehearsal and only executor.py writes to them live
      - drafter(invoice, step, mandate) -> {"subject", "body", "amount", "step"};
        None means the real actor model
      - writes traces/<run_id>.jsonl (contract TraceLine, one per line) and
        state/plans/<run_id>.json (Plan.to_json); keeps idempotency keys in
        state/sent.json
      - rehearse_only=True stops after phase "rehearsal_done": no Slack post,
        no tap, no execute

owed.agent.gate.classify(messages: list[Message]) -> ThreadState        ("injection" for instruction-like text)
owed.agent.planner.decide(invoice, thread_state, today) -> Step | Refusal
owed.agent.drafter.draft(invoice, step, mandate) -> dict                (real actor model)
owed.agent.verifier.deterministic_checks(draft: dict, invoice, mandate) -> list[Refusal]
owed.agent.executor.execute(plan, ledger, inbox, calendar, chat, state_dir, run_id) -> list[TraceLine]
owed.agent.asserter.assert_end_state(plan, ledger, inbox, calendar, chat) -> list[EndState]   (raises AssertionError on mismatch)

Outputs are read from the trace file and the plan file, which the contract
defines, so nothing here depends on run()'s return value.
"""
from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from owed.contract import Intent, Invoice, Plan, Refusal, Step

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MANDATE_PATH = ROOT / "owed" / "mandate" / "mandate.yaml"


class AgentMissing(ImportError):
    """owed.agent / run.py are not merged into this checkout yet."""


# ---------- availability ----------

_agent_state: dict[str, Any] = {}


def _probe() -> None:
    if _agent_state:
        return
    try:
        run_mod = importlib.import_module("run")
        importlib.import_module("owed.agent")
    except ImportError as exc:  # missing until the core merge
        _agent_state.update(ok=False, reason=f"agent not merged: {exc}", run=None)
        return
    if not hasattr(run_mod, "run"):
        _agent_state.update(ok=False, reason="run.py has no run() entry point", run=None)
        return
    _agent_state.update(ok=True, reason="", run=run_mod)


def agent_available() -> bool:
    _probe()
    return bool(_agent_state["ok"])


def missing_reason() -> str:
    _probe()
    return str(_agent_state["reason"])


def require_agent():
    _probe()
    if not _agent_state["ok"]:
        raise AgentMissing(_agent_state["reason"])
    return _agent_state["run"]


def agent_module(name: str):
    """owed.agent.<name>, or AgentMissing."""
    try:
        return importlib.import_module(f"owed.agent.{name}")
    except ImportError as exc:
        raise AgentMissing(f"owed.agent.{name} not merged: {exc}") from exc


def real_drafter() -> Callable[[Invoice, Step, dict], dict]:
    return agent_module("drafter").draft


# ---------- mandate ----------

def default_mandate() -> dict:
    """The SPEC mandate, as a dict. Replaced by owed/mandate/mandate.yaml once core lands it."""
    return {
        "steps": 3,
        "voice": "mine",
        "tone": ["never angry", "never apologetic", "never threatening"],
        "never": ["offer a discount", "change the amount", "promise a deadline extension"],
        "approval": "nothing past step 2 sends without my tap",
        "step_1": {"day": 3, "action": "soft nudge"},
        "step_2": {"day": 10, "action": "direct ask + payment link"},
        "step_3": {"day": 21, "action": "propose a call (calendar slot)", "requires_tap": True},
    }


def load_mandate(overrides: Optional[dict] = None) -> dict:
    mandate = default_mandate()
    if MANDATE_PATH.exists():
        import yaml  # stdlib-adjacent, listed in requirements

        loaded = yaml.safe_load(MANDATE_PATH.read_text())
        if not isinstance(loaded, dict):
            raise ValueError(f"{MANDATE_PATH} must contain a mapping")
        mandate = loaded
    mandate.update(overrides or {})
    return mandate


# ---------- drafter stand-ins ----------

def template_draft(invoice: Invoice, step: Step) -> dict:
    """A mandate-compliant draft with no model call. Amount is the ledger balance, untouched."""
    amt = f"${invoice.amount_due:,.2f}"
    due = f"{invoice.due_date.strftime('%B')} {invoice.due_date.day}"
    first = invoice.client_name.split()[0] if invoice.client_name else "there"
    inv = invoice.invoice_id
    if step == 1:
        subject = f"Invoice {inv}"
        body = (
            f"Hi {first},\n\nA quick note that invoice {inv} for {amt} was due on {due}. "
            f"Could you let me know when it is scheduled?\n\nThanks,\n"
        )
    elif step == 2:
        subject = f"Invoice {inv} - {amt} outstanding"
        body = (
            f"Hi {first},\n\nInvoice {inv} for {amt} was due on {due} and is still open. "
            f"Could you settle it, or let me know when I can expect it? "
            f"A payment link is below.\n\nThanks,\n"
        )
    elif step == 3:
        subject = f"Invoice {inv} - a quick call?"
        body = (
            f"Hi {first},\n\nInvoice {inv} for {amt} has been open since {due}. "
            f"Would a 15-minute call help sort it out? I will send a calendar invite "
            f"for a slot that works.\n\nThanks,\n"
        )
    else:
        raise ValueError(f"no draft for step {step}")
    return {"subject": subject, "body": body, "amount": invoice.amount_due, "step": step}


class ScriptedDrafter:
    """Callable drafter. `overrides` maps "INV-0042:2" -> {"subject", "body"}.
    Keys without an override go to `fallback` (real actor) or the template."""

    def __init__(self, overrides: Optional[dict[str, dict]] = None,
                 fallback: Optional[Callable[[Invoice, Step, dict], dict]] = None):
        self.overrides = dict(overrides or {})
        self.fallback = fallback
        self.calls: list[tuple[str, Step]] = []

    def __call__(self, invoice: Invoice, step: Step, mandate: dict) -> dict:
        self.calls.append((invoice.invoice_id, step))
        key = f"{invoice.invoice_id}:{step}"
        if key in self.overrides:
            o = self.overrides[key]
            return {"subject": o["subject"], "body": o["body"], "amount": invoice.amount_due, "step": step}
        if self.fallback is not None:
            return self.fallback(invoice, step, mandate)
        return template_draft(invoice, step)


# ---------- running ----------

@dataclass
class RunOutcome:
    run_id: str
    trace: list[dict]
    plan: Optional[dict]
    result: Any


def run_scenario(scenario, stubs, state_dir: Path, traces_dir: Path, *,
                 run_id: Optional[str] = None,
                 drafter: Optional[Callable] = None,
                 rehearse_only: bool = False,
                 tap: Optional[bool] = None) -> RunOutcome:
    """One `run.py` run of `scenario` with `stubs` standing in for the live adapters."""
    run_mod = require_agent()
    run_id = run_id or f"{scenario.name}-{uuid4().hex[:6]}"
    state_dir = Path(state_dir)
    traces_dir = Path(traces_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)
    if tap is not None:
        stubs.chat.tap = tap
    if drafter is None and not rehearse_only and scenario.actor_draft:
        raise ValueError(f"{scenario.name} scripts an actor draft; pass a ScriptedDrafter")

    result = run_mod.run(
        **stubs.as_kwargs(),
        today=scenario.today,
        run_id=run_id,
        mandate=load_mandate(scenario.mandate),
        state_dir=state_dir,
        traces_dir=traces_dir,
        drafter=drafter,
        rehearse_only=rehearse_only,
    )
    return RunOutcome(
        run_id=run_id,
        trace=read_trace(traces_dir / f"{run_id}.jsonl"),
        plan=read_plan(state_dir / "plans" / f"{run_id}.json"),
        result=result,
    )


def read_trace(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"run wrote no trace at {path}")
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def read_plan(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def plan_from_json(d: dict) -> Plan:
    """Rebuild a contract Plan from state/plans/<run_id>.json."""
    intents = [
        Intent(
            kind=i["kind"], invoice_id=i["invoice_id"], step=i["step"], payload=i.get("payload", {}),
            idempotency_key=i.get("idempotency_key", ""), requires_tap=bool(i.get("requires_tap", False)),
        )
        for i in d.get("intents", [])
    ]
    refusals = [
        Refusal(invoice_id=r["invoice_id"], step=r["step"], reason=r["reason"], detail=r.get("detail", ""))
        for r in d.get("refusals", [])
    ]
    raw_ts = d.get("created_at")
    try:
        created = datetime.fromisoformat(str(raw_ts)) if raw_ts else datetime.now()
    except ValueError:
        created = datetime.now()
    return Plan(run_id=d["run_id"], created_at=created, intents=intents, refusals=refusals)


# ---------- reading outcomes ----------

def phases(trace: list[dict]) -> list[str]:
    return [str(ln.get("phase") or ln.get("step") or "") for ln in trace]


def refusal_reasons(outcome: RunOutcome) -> set[str]:
    """Reasons from the posted plan plus any trace line that carries one."""
    reasons: set[str] = set()
    if outcome.plan:
        reasons.update(r["reason"] for r in outcome.plan.get("refusals", []))
    for ln in outcome.trace:
        data = ln.get("data") or {}
        for r in (data.get("reason"), ln.get("reason")):
            if r:
                reasons.add(str(r))
    return reasons


def _first_index(trace: list[dict], pred: Callable[[dict], bool]) -> Optional[int]:
    for i, ln in enumerate(trace):
        if pred(ln):
            return i
    return None


def refused_before_tap(trace: list[dict], reason: str) -> bool:
    """True when a refusal with `reason` is traced before any tap/posted_plan line (or no tap happens)."""
    def is_refusal(ln: dict) -> bool:
        data = ln.get("data") or {}
        return data.get("reason") == reason or ln.get("reason") == reason or (
            "REFUSED" in str(ln.get("decision", "")) and reason in json.dumps(ln)
        )

    r = _first_index(trace, is_refusal)
    if r is None:
        return False
    t = _first_index(trace, lambda ln: (ln.get("phase") or ln.get("step")) in ("tap", "posted_plan"))
    return t is None or r < t
