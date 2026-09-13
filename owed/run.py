"""One run: snapshot -> rehearse against Shadow -> post plan -> tap -> re-verify live -> execute -> assert.

Entry points:
  run(...)                 the documented signature (see evals/harness.py); writes traces/<run_id>.jsonl
                           and state/plans/<run_id>.json
  rehearse_shadow(dict)    scenario/snapshot dict -> Plan; Shadow adapters only, no network, no files
                           unless traces_dir is given. This is what api/rehearse.py calls on Vercel.

Rehearsal never writes live: every write in the loop below hits a Shadow adapter and becomes an Intent.
Only owed.agent.executor turns Intents into live writes, and only after the Slack tap (PLAN 3:45).
"""
from __future__ import annotations
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from owed.adapters.snapshot import ShadowWorld, snapshot
from owed.agent.gate import classify
from owed.agent.planner import decide
from owed.agent.verifier import deterministic_checks
from owed.config import ROOT
from owed.contract import Calendar, Chat, Inbox, Invoice, Ledger, Plan, Refusal, Step, TraceLine

Drafter = Callable[[Invoice, Step, dict], dict]
MANDATE_PATH = ROOT / "owed" / "mandate" / "mandate.yaml"


# ---------- trace ----------

class Tracer:
    """Appends one TraceLine per step to traces/<run_id>.jsonl and echoes the decision. The log is the demo."""

    def __init__(self, run_id: str, path: Optional[Path] = None, echo: bool = True):
        self.run_id, self.path, self.echo = run_id, path, echo
        self.lines: list[TraceLine] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def __call__(self, phase: str, invoice_id: Optional[str], decision: str, **data: Any) -> TraceLine:
        line = TraceLine(ts=datetime.now(timezone.utc).isoformat(), run_id=self.run_id, phase=phase,  # type: ignore[arg-type]
                         invoice_id=invoice_id, decision=decision, data=data)
        self.lines.append(line)
        if self.path:
            with self.path.open("a") as f:
                f.write(line.line() + "\n")
        if self.echo:
            print(f"[{phase}] {decision}")
        return line


# ---------- mandate ----------

def load_mandate(overrides: Optional[dict] = None) -> dict:
    mandate = yaml.safe_load(MANDATE_PATH.read_text())
    if not isinstance(mandate, dict):
        raise ValueError(f"{MANDATE_PATH} must contain a mapping")
    mandate.update(overrides or {})
    return mandate


def _actor() -> Drafter:
    from owed.agent.drafter import draft  # the actor model, PLAN 2:45
    return draft


def _money(x: float) -> str:
    return f"${x:,.2f}"


# ---------- rehearsal (pure over a ShadowWorld) ----------

def rehearse(world: ShadowWorld, mandate: dict, drafter: Optional[Drafter], run_id: str, trace: Tracer) -> Plan:
    plan = Plan(run_id=run_id, created_at=datetime.now(timezone.utc))
    draft_fn = drafter or _actor()
    today = world.today

    invoices = world.ledger.overdue(today)
    trace("read_ledger", None, f"{len(invoices)} overdue invoice(s) as of {today}",
          invoices=[{"invoice_id": i.invoice_id, "amount_due": i.amount_due, "days": i.days_overdue(today)} for i in invoices])

    seen: set[str] = set()
    for inv in invoices:
        iid, days, bal = inv.invoice_id, inv.days_overdue(today), _money(inv.amount_due)
        if iid in seen:
            trace("decide", iid, f"SKIP duplicate ledger row for {iid}: one chase, not two", reason="duplicate")
            continue
        seen.add(iid)

        msgs = world.inbox.thread(inv.thread_id) if inv.thread_id else []
        trace("read_thread", iid, f"thread {inv.thread_id or '(none)'}: {len(msgs)} message(s)")
        state = classify(msgs, client_email=inv.client_email)
        trace("classify", iid, f"client thread state: {state}", state=state)

        decision = decide(inv, state, today, mandate)
        if isinstance(decision, Refusal):
            plan.refusals.append(decision)
            trace("decide", iid, f"REFUSED {iid} step {decision.step}: {decision.detail}",
                  reason=decision.reason, step=decision.step)
            continue
        step = decision
        if step == 0:
            trace("decide", iid, f"WAIT {iid}: day {days} overdue, {bal} due, thread {state}; no step due yet", step=0)
            continue
        trace("decide", iid, f"step {step} for {iid}: day {days} overdue, {bal} due, last chased step {inv.last_chased_step}",
              step=step)

        world.set_context(iid, step, amount=inv.amount_due)
        draft = draft_fn(replace(inv), step, mandate)
        trace("draft", iid, f"draft step {step}: subject {draft.get('subject', '')!r}, {len(draft.get('body', ''))} chars",
              subject=draft.get("subject"), body=draft.get("body"), amount=draft.get("amount"))

        refusals = deterministic_checks(draft, inv, mandate)
        if refusals:
            plan.refusals.extend(refusals)
            for r in refusals:
                trace("verify", iid, f"REFUSED {iid} step {step}: {r.detail}", reason=r.reason, step=step)
            continue
        trace("verify", iid, f"PASS {iid} step {step}: amount {bal} unchanged, no discount, no new deadline, tone ok")

        # Exercise the shadow adapters: each write becomes an Intent, nothing leaves the process.
        if step == 2:
            world.ledger.create_payment_link(iid)
        if step == 3:
            slots = world.calendar.free_slots(today, 1)
            if not slots:
                plan.refusals.append(Refusal(iid, 3, "waiting_tap", "no free calendar slot in the next two weeks"))
                trace("decide", iid, f"REFUSED {iid} step 3: no free calendar slot", reason="waiting_tap", step=3)
                continue
            world.calendar.create_event(f"OWED call {iid} with {inv.client_name or inv.client_email}", slots[0], inv.client_email)
        world.inbox.send(inv.client_email, draft["subject"], draft["body"], inv.thread_id)
        world.set_context("", 0)

    plan.intents = list(world.intents)
    return plan


def plan_summary(plan: Plan) -> str:
    sends = sum(1 for i in plan.intents if i.kind == "send_email")
    taps = sum(1 for i in plan.intents if i.requires_tap)
    return f"would send {sends}, refused {len(plan.refusals)}, {taps} intent(s) need the tap"


def rehearse_shadow(scenario: dict, *, drafter: Optional[Drafter] = None, mandate: Optional[dict] = None,
                    run_id: Optional[str] = None, traces_dir: Optional[Path] = None) -> Plan:
    """Scenario/snapshot dict -> Plan. Honors the scenario's `actor_draft` overrides and `mandate` overrides."""
    run_id = run_id or f"rehearsal-{scenario.get('name', 'snapshot')}-{datetime.now().strftime('%H%M%S')}"
    world = ShadowWorld.from_snapshot(scenario)
    mandate = mandate or load_mandate(scenario.get("mandate"))
    overrides: dict = scenario.get("actor_draft") or {}
    inner = drafter
    if overrides:
        def scripted(inv: Invoice, step: Step, m: dict) -> dict:
            o = overrides.get(f"{inv.invoice_id}:{step}")
            if o:  # amount always comes from the ledger, never from the script
                return {"subject": o["subject"], "body": o["body"], "amount": inv.amount_due, "step": step}
            return (inner or _actor())(inv, step, m)
        drafter = scripted
    trace = Tracer(run_id, traces_dir / f"{run_id}.jsonl" if traces_dir else None, echo=traces_dir is not None)
    plan = rehearse(world, mandate, drafter, run_id, trace)
    trace("rehearsal_done", None, plan_summary(plan))
    return plan


# ---------- the documented entry point ----------

def _write_plan(state_dir: Path, plan: Plan) -> Path:
    p = state_dir / "plans" / f"{plan.run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plan.to_json())
    return p


def run(*, ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar], chat: Chat, today: date, run_id: str,
        mandate: dict, state_dir: Path, traces_dir: Path,
        drafter: Optional[Drafter] = None, rehearse_only: bool = False) -> Plan:
    state_dir, traces_dir = Path(state_dir), Path(traces_dir)
    trace = Tracer(run_id, traces_dir / f"{run_id}.jsonl")

    snap = snapshot(ledger, inbox, calendar, today)
    world = ShadowWorld.from_snapshot(snap)
    plan = rehearse(world, mandate, drafter, run_id, trace)
    _write_plan(state_dir, plan)
    trace("rehearsal_done", None, plan_summary(plan),
          intents=[i.idempotency_key for i in plan.intents], refusals=[r.reason for r in plan.refusals])
    if rehearse_only:
        return plan

    # PLAN 3:45: post plan to Slack (via executor), wait for the tap, re-verify live ledger,
    # execute with idempotency keys in state/sent.json, assert end state.
    raise NotImplementedError("run(): post/tap/execute/assert land at PLAN 3:45; use rehearse_only=True until then")
