"""One run: snapshot -> rehearse against Shadow -> post plan -> tap -> re-verify live -> execute -> assert.

Entry points:
  run(...)                 the documented signature (see evals/harness.py); writes traces/<run_id>.jsonl
                           and state/plans/<run_id>.json
  rehearse_shadow(dict)    scenario/snapshot dict -> Plan; Shadow adapters only, no network, no files
                           unless traces_dir is given. This is what api/rehearse.py calls on Vercel.
  rehearse_for(email, ..)  Be the client: live snapshot filtered to one client's invoices, shadow rehearsal,
                           state under state/clients/<email>/. ui/client_server.py calls this.
  execute_for(email, ..)   the tap -> re-verify -> execute -> assert half of the same flow.

Rehearsal never writes live: every write in the loop below hits a Shadow adapter and becomes an Intent.
Only owed.agent.executor turns Intents into live writes, and only after the Slack tap (PLAN 3:45).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from owed.adapters.snapshot import ShadowWorld, snapshot
from owed.agent.gate import classify
from owed.agent.planner import decide
from owed.agent.verifier import verify
from owed.config import ROOT, offline
from owed.contract import Calendar, Chat, Inbox, Invoice, Ledger, Plan, Refusal, Step, TraceLine

Drafter = Callable[[Invoice, Step, dict], dict]
MANDATE_PATH = ROOT / "owed" / "mandate" / "mandate.yaml"


# ---------- trace ----------

class Tracer:
    """Appends one TraceLine per step to traces/<run_id>.jsonl and echoes the decision. The log is the demo."""

    def __init__(self, run_id: str, path: Optional[Path] = None, echo: bool = True, append: bool = False):
        self.run_id, self.path, self.echo = run_id, path, echo
        self.lines: list[TraceLine] = []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not (append and path.exists()):
                path.write_text("")

    def __call__(self, phase: str, invoice_id: Optional[str], decision: str, **data: Any) -> TraceLine:
        return self.write(TraceLine(ts=datetime.now(timezone.utc).isoformat(), run_id=self.run_id, phase=phase,  # type: ignore[arg-type]
                                    invoice_id=invoice_id, decision=decision, data=data))

    def write(self, line: TraceLine) -> TraceLine:
        self.lines.append(line)
        if self.path:
            with self.path.open("a") as f:
                f.write(line.line() + "\n")
        if self.echo:
            print(f"[{line.phase}] {line.decision}")
        return line


# ---------- mandate ----------

def load_mandate(overrides: Optional[dict] = None, path: Optional[Path] = None) -> dict:
    """The mandate from disk, read every call so an update lands on the next run. path: a workspace's
    own mandate.yaml; default owed/mandate/mandate.yaml."""
    path = Path(path or MANDATE_PATH)
    mandate = yaml.safe_load(path.read_text())
    if not isinstance(mandate, dict):
        raise ValueError(f"{path} must contain a mapping")
    mandate.update(overrides or {})
    return mandate


def _emails(scope, clients=None) -> Optional[set[str]]:
    """None or ALL_CLIENTS ("*") -> whole ledger; a str or list of str -> that set of client mailboxes,
    lowercased. `clients` (the desk's roster) narrows a whole-ledger scope to those mailboxes."""
    if clients:
        return {c.strip().lower() for c in clients if c and c.strip()}
    if scope is None or scope == "*":
        return None
    items = [scope] if isinstance(scope, str) else list(scope)
    out = {e.strip().lower() for e in items if e and e.strip()}
    if not out:
        raise ValueError("scope must be None, an email, or a non-empty list of emails")
    return out


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
            trace("decide", iid, f"SKIP duplicate ledger row for {iid}: one chase, not two", skipped="duplicate_row")
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

        refusals = verify(draft, inv, mandate)
        if refusals:
            plan.refusals.extend(refusals)
            for r in refusals:
                trace("verify", iid, f"REFUSED {iid} step {step}: {r.detail}", reason=r.reason, step=step)
            continue
        trace("verify", iid, f"PASS {iid} step {step}: amount {bal} unchanged, no discount, no new deadline, tone ok",
              verifier="deterministic+model" if not offline() else "deterministic")

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

    # Close the loop: a chased invoice that has since been paid gets one receipt reply (same tap gate).
    closing = getattr(world.ledger, "paid_after_chase", None)
    for inv in (closing() if callable(closing) else []):
        iid = inv.invoice_id
        trace("decide", iid, f"CLOSE {iid}: paid in full (${inv.amount_total:,.2f} received) after step {inv.last_chased_step}; receipt reply due")
        from owed.agent.drafter import receipt_draft
        from owed.agent.verifier import receipt_checks
        draft = receipt_draft(inv, mandate)
        refusals = receipt_checks(draft, inv, mandate)
        if refusals:
            plan.refusals.extend(refusals)
            for r in refusals:
                trace("verify", iid, f"REFUSED {iid} receipt: {r.detail}", reason=r.reason)
            continue
        world.set_context(iid, 0, amount=inv.amount_total, receipt=True)
        world.inbox.send(inv.client_email, draft["subject"], draft["body"], inv.thread_id)
        world.set_context("", 0)

    plan.intents = list(world.intents)
    return plan


def plan_from_json(d: dict) -> Plan:
    """Rebuild a Plan from state/plans/<run_id>.json."""
    from owed.contract import Intent
    intents = [Intent(kind=i["kind"], invoice_id=i["invoice_id"], step=i["step"], payload=i.get("payload", {}),
                      idempotency_key=i.get("idempotency_key", ""), requires_tap=bool(i.get("requires_tap", False)))
               for i in d.get("intents", [])]
    refusals = [Refusal(r["invoice_id"], r["step"], r["reason"], r.get("detail", "")) for r in d.get("refusals", [])]
    try:
        created = datetime.fromisoformat(str(d.get("created_at")))
    except ValueError:
        created = datetime.now(timezone.utc)
    return Plan(run_id=d["run_id"], created_at=created, intents=intents, refusals=refusals)


def _write_plan(state_dir: Path, plan: Plan, end_state: Optional[list] = None) -> Path:
    p = state_dir / "plans" / f"{plan.run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if end_state is None:
        p.write_text(plan.to_json())
    else:
        data = json.loads(plan.to_json())
        data["end_state"] = [{"app": s.app, "expected": s.expected, "actual": s.actual, "detail": s.detail, "ok": s.ok}
                             for s in end_state]
        p.write_text(json.dumps(data, indent=2, default=str))
    return p


def _push_blob(trace: Tracer, run_id: str, state_dir: Path, traces_dir: Path) -> None:
    """DEPLOY step 4: one upload after assert. Skipped without a token; a failure is traced, never raised."""
    from owed.blob import push_last_run
    result = push_last_run(run_id, state_dir / "plans" / f"{run_id}.json", traces_dir / f"{run_id}.jsonl")
    if result == "ok":
        trace("done", None, f"pushed plan + trace to Vercel Blob under last_run/{run_id}")
    elif result is not None:
        trace("done", None, f"Blob push failed (run unaffected): {result}")


def plan_summary(plan: Plan) -> str:
    sends = sum(1 for i in plan.intents if i.kind == "send_email")
    taps = sum(1 for i in plan.intents if i.requires_tap)
    return f"would send {sends}, refused {len(plan.refusals)}, {taps} intent(s) need the tap"


def rehearse_shadow(scenario: dict, *, drafter: Optional[Drafter] = None, mandate: Optional[dict] = None,
                    run_id: Optional[str] = None, traces_dir: Optional[Path] = None,
                    state_dir: Optional[Path] = None, offline: bool = False,
                    mandate_path: Optional[Path] = None) -> Plan:
    """Scenario/snapshot dict -> Plan. Honors the scenario's `actor_draft` and `mandate` overrides.
    offline=True: no model call anywhere (template draft, deterministic classifier and verifier)."""
    if offline:
        import os
        os.environ["OWED_OFFLINE"] = "1"
        from owed.agent.drafter import template_draft
        drafter = drafter or template_draft
    run_id = run_id or f"rehearsal-{scenario.get('name', 'snapshot')}-{datetime.now().strftime('%H%M%S')}"
    world = ShadowWorld.from_snapshot(scenario)
    mandate = mandate or load_mandate(scenario.get("mandate"), path=mandate_path)
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
    trace("rehearsal_done", None, plan_summary(plan),
          intents=[i.idempotency_key for i in plan.intents], refusals=[r.reason for r in plan.refusals])
    if state_dir:
        _write_plan(Path(state_dir), plan)
    return plan


def format_plan(plan: Plan) -> str:
    """The plan as people read it: WILL SEND lines, REFUSED lines with reasons."""
    by_key: dict[str, list] = {}
    for it in plan.intents:
        by_key.setdefault(it.idempotency_key, []).append(it)
    out = [f"PLAN {plan.run_id}"]
    for key, its in by_key.items():
        send = next((i for i in its if i.kind == "send_email"), None)
        extras = []
        for i in its:
            if i.kind == "create_payment_link":
                extras.append("+ payment link")
            elif i.kind == "create_event":
                extras.append(f"+ calendar event {i.payload.get('start_iso', '')[:16].replace('T', ' ')}")
            elif i.kind == "post_chat":
                extras.append("+ slack post")
        tap = "  REQUIRES TAP" if any(i.requires_tap for i in its) else ""
        if send:
            p = send.payload
            amt = f"${p['amount']:,.2f}" if isinstance(p.get("amount"), (int, float)) else ""
            what = "receipt (closed)" if p.get("receipt") else f"step {send.step}"
            out.append(f"WILL SEND  {send.invoice_id} {what} -> {p.get('to')}  {p.get('subject')!r}  {amt}  "
                       f"{' '.join(extras)}{tap}".rstrip())
        else:
            out.append(f"WILL DO    {key}  {' '.join(extras)}{tap}".rstrip())
    for r in plan.refusals:
        out.append(f"REFUSED    {r.invoice_id} step {r.step}: {r.reason} -- {r.detail}")
    out.append(plan_summary(plan))
    return "\n".join(out)


# ---------- the documented entry point ----------


def run(*, ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar], chat: Chat, today: date, run_id: str,
        mandate: dict, state_dir: Path, traces_dir: Path,
        drafter: Optional[Drafter] = None, rehearse_only: bool = False) -> Plan:
    state_dir, traces_dir = Path(state_dir), Path(traces_dir)
    trace = Tracer(run_id, traces_dir / f"{run_id}.jsonl")

    if not rehearse_only:  # 0. payment links settle outside the invoice; bring the ledger up to date first
        from owed.agent import executor
        for line in executor.reconcile_links(ledger, today, state_dir, run_id):
            trace.write(line)
    snap = snapshot(ledger, inbox, calendar, today)
    world = ShadowWorld.from_snapshot(snap)
    plan = rehearse(world, mandate, drafter, run_id, trace)
    _write_plan(state_dir, plan)
    trace("rehearsal_done", None, plan_summary(plan),
          intents=[i.idempotency_key for i in plan.intents], refusals=[r.reason for r in plan.refusals])
    if rehearse_only:
        return plan

    from owed.agent import executor  # the only live-write path

    # 6. Post the plan to Slack and wait for the tap.
    ts = executor.post(chat, slack_plan_text(plan))
    trace("posted_plan", None, f"posted plan to Slack ({ts}): {plan_summary(plan)}", ts=ts)
    return gate_and_execute(plan, ts, ledger=ledger, inbox=inbox, calendar=calendar, chat=chat,
                            state_dir=state_dir, traces_dir=traces_dir, trace=trace, run_id=run_id, push_blob=True)


def gate_and_execute(plan: Plan, plan_ts: str, *, ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar],
                     chat: Chat, state_dir: Path, traces_dir: Path, trace: Tracer, run_id: str,
                     push_blob: bool = False) -> Plan:
    """Steps 6b-10 after the plan is posted: wait for the tap, re-verify live, execute, assert.
    Shared by run() (Slack tap) and execute_for() (web tap). Every live write goes through executor."""
    from owed.agent import asserter, executor  # the only live-write path

    if not plan.intents:
        trace("done", None, "nothing to execute; refusals reported to the freelancer")
        states = asserter.assert_end_state(plan, ledger, inbox, calendar, chat)
        trace("assert", None, "end state ok: " + ", ".join(f"{s.app} {s.actual}/{s.expected}" for s in states))
        _write_plan(state_dir, plan, states)
        if push_blob:
            _push_blob(trace, run_id, state_dir, traces_dir)
        return plan
    timeout = int(os.environ.get("OWED_TAP_TIMEOUT", "600"))
    tapped = chat.wait_for_tap(plan_ts, timeout_s=timeout)
    trace("tap", None, "tap received: freelancer approved the plan" if tapped else f"no tap within {timeout}s: nothing sends",
          tapped=tapped)
    if not tapped:
        for key, its in _groups(plan).items():
            plan.refusals.append(Refusal(its[0].invoice_id, its[0].step, "waiting_tap", "no tap; nothing sent"))
            trace("decide", its[0].invoice_id, f"REFUSED {key}: waiting for tap", reason="waiting_tap")
        plan.intents = []
        _write_plan(state_dir, plan)
        executor.post(chat, f"OWED result {run_id}: no tap, nothing sent")
        trace("done", None, "no tap, nothing sent")
        return plan

    # 7. Re-verify live state after the tap. Money may have arrived since the rehearsal, maybe via the link.
    from owed.config import today as today_fn
    for line in executor.reconcile_links(ledger, today_fn(), state_dir, run_id):
        trace.write(line)
    keep: list = []
    aborts: list[str] = []
    for key, its in _groups(plan).items():
        iid, step = its[0].invoice_id, its[0].step
        planned = next((i.payload.get("amount") for i in its if i.kind == "send_email"), None)
        live = ledger.get(iid)
        if any(i.payload.get("receipt") for i in its):
            if live.paid:
                trace("reverify", iid, f"REVERIFIED {iid} receipt: ledger still shows paid (${live.amount_total:,.2f})")
                keep.extend(its)
            else:
                plan.refusals.append(Refusal(iid, 0, "verifier_amount", "no longer shows paid; receipt withheld"))
                aborts.append(f"ABORTED: no longer shows paid -- {iid} receipt")
                trace("abort", iid, f"ABORTED {iid} receipt: ledger no longer shows paid", reason="verifier_amount")
            continue
        if live.paid or live.amount_due <= 0:
            detail = f"paid since rehearsal (${(planned or live.amount_total):,.2f} received)"
        elif planned is not None and abs(live.amount_due - float(planned)) > 0.005:
            detail = f"balance changed since rehearsal (${float(planned):,.2f} -> ${live.amount_due:,.2f})"
        elif live.last_chased_step >= step:
            detail = f"step {step} already chased since rehearsal"
        else:
            trace("reverify", iid, f"REVERIFIED {iid} step {step}: still ${live.amount_due:,.2f} due, unchanged since rehearsal")
            keep.extend(its)
            continue
        reason = "already_sent" if "already chased" in detail else "paid"
        plan.refusals.append(Refusal(iid, step, reason, detail))
        aborts.append(f"ABORTED: {detail} -- {iid} step {step}")
        trace("abort", iid, f"ABORTED {iid} step {step}: {detail}", reason=reason)
    plan.intents = keep
    _write_plan(state_dir, plan)

    # 8. Execute with idempotency keys, then 9. assert end state in every app.
    try:
        for line in executor.execute(plan, ledger, inbox, calendar, chat, state_dir, run_id):
            trace.write(line)
    except Exception as e:  # re-raised: fail loud, but leave a readable trace line and a Slack result first
        msg = f"FAILED during execute: {type(e).__name__}: {str(e)[:300]}"
        trace("execute", None, msg + f" -- keys left pending in {executor.sent_path(state_dir)}; check the apps before clearing")
        executor.post(chat, f"OWED result {run_id}: {msg}")
        raise
    sent = sum(1 for i in plan.intents if i.kind == "send_email" and not i.payload.get("receipt"))
    closed = [f"closed, ${i.payload.get('amount', 0):,.2f} received -- {i.invoice_id}"
              for i in plan.intents if i.kind == "send_email" and i.payload.get("receipt")]
    executor.post(chat, f"OWED result {run_id}: sent {sent}, closed {len(closed)}, aborted {len(aborts)}"
                  + "".join(f"\n{c}" for c in closed) + "".join(f"\n{a}" for a in aborts))
    try:
        states = asserter.assert_end_state(plan, ledger, inbox, calendar, chat)
    except AssertionError as e:
        trace("assert", None, f"END STATE MISMATCH: {e}")
        raise
    trace("assert", None, "end state ok: " + ", ".join(f"{s.app} {s.actual}/{s.expected}" for s in states),
          states=[{"app": s.app, "expected": s.expected, "actual": s.actual} for s in states])
    trace("done", None, f"sent {sent}, aborted {len(aborts)}, refused {len(plan.refusals) - len(aborts)}")
    _write_plan(state_dir, plan, states)
    if push_blob:
        _push_blob(trace, run_id, state_dir, traces_dir)
    return plan


# ---------- Be the client: one invoice per email, web tap instead of Slack reaction ----------

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ALL_CLIENTS = "*"  # rehearse_for / execute_for over the whole ledger: the freelancer's desk, not one client


def client_key(email: str) -> str:
    return hashlib.sha1(email.strip().lower().encode("utf-8")).hexdigest()[:4].upper()


def client_dir(email: str, state_root: Path) -> Path:
    """state/clients/<email>. The email is validated so it is one safe path segment."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or ".." in email:
        raise ValueError(f"not an email address: {email!r}")
    return Path(state_root) / "clients" / email


def _live_adapters(with_chat: bool) -> tuple:
    """Live adapters, imported here so owed.run stays importable where the SDKs are absent (Vercel api/)."""
    from owed.adapters.calendar_live import GoogleCalendar
    from owed.adapters.inbox_live import GmailInbox
    from owed.adapters.ledger_live import StripeLedger
    chat = None
    if with_chat:
        from owed.adapters.chat_live import SlackChat
        chat = SlackChat()
    return StripeLedger(), GmailInbox(), GoogleCalendar(), chat


def _as_chat(chat) -> Optional[Chat]:
    """A Chat, or a list of Chats fanned out so one plan post and one approval reach all of them."""
    if isinstance(chat, (list, tuple)):
        from owed.adapters.chat_fanout import FanoutChat
        return FanoutChat(list(chat))
    return chat


def rehearse_for(email, state_dir: Path, *, ledger: Optional[Ledger] = None,
                 inbox: Optional[Inbox] = None, calendar: Optional[Calendar] = None, today: Optional[date] = None,
                 chat=None, run_id: Optional[str] = None, drafter: Optional[Drafter] = None,
                 mandate: Optional[dict] = None, mandate_path: Optional[Path] = None,
                 clients: Optional[set[str]] = None) -> tuple[Plan, str]:
    """Snapshot the apps, keep only the scoped clients' invoices (email: one address, a list of addresses,
    None or ALL_CLIENTS for the whole ledger; `clients` narrows a desk to its roster), rehearse against
    Shadow. Reads only unless a chat (or list of chats) is given, in which case the plan is posted through
    the executor and its ts returned. state_dir is the workspace: plans/, traces/, latest.json and sent.json
    live under it. mandate_path picks that workspace's mandate.yaml. Adapters default to the live ones
    (needs credentials; never call from api/)."""
    from owed.config import today as today_fn
    emails = _emails(email, clients)
    chat = _as_chat(chat)
    state_dir = Path(state_dir)
    if email is None or email == ALL_CLIENTS or emails is None:
        label = "desk"  # the freelancer's desk: whole ledger, or its roster via `clients`
    elif len(emails) == 1:
        label = f"client-{client_key(next(iter(emails)))}"
    else:
        label = f"client-MULTI{len(emails)}"
    run_id = run_id or f"{label}-{datetime.now().strftime('%H%M%S')}"
    if ledger is None or inbox is None:
        live_ledger, live_inbox, live_calendar, _ = _live_adapters(with_chat=False)
        ledger, inbox = ledger or live_ledger, inbox or live_inbox
        calendar = calendar or live_calendar
    today = today or today_fn()
    trace = Tracer(run_id, state_dir / "traces" / f"{run_id}.jsonl")

    snap = snapshot(ledger, inbox, calendar, today)
    mine = [i for i in snap["invoices"] if emails is None or (i.get("client_email") or "").lower() in emails]
    who = "all clients" if emails is None else (f"this desk's {len(emails)} client(s)" if clients else ", ".join(sorted(emails)))
    trace("read_ledger", None, f"{len(snap['invoices'])} overdue invoice(s) in the ledger, {len(mine)} for {who}",
          invoices=[i["invoice_id"] for i in mine])
    snap["invoices"] = mine
    snap["threads"] = {t: m for t, m in snap["threads"].items() if any(i.get("thread_id") == t for i in mine)}
    snap["links"] = {i["invoice_id"]: [] for i in mine}

    world = ShadowWorld.from_snapshot(snap)
    plan = rehearse(world, mandate or load_mandate(path=mandate_path), drafter, run_id, trace)
    _write_plan(state_dir, plan)
    trace("rehearsal_done", None, plan_summary(plan),
          intents=[i.idempotency_key for i in plan.intents], refusals=[r.reason for r in plan.refusals])
    (state_dir / "latest.json").write_text(json.dumps({"run_id": run_id, "email": email if isinstance(email, str) else
                                                       (sorted(emails) if emails else None)}))

    ts = ""
    if chat is not None:
        from owed.agent import executor  # the only live-write path
        ts = executor.post(chat, slack_plan_text(plan))
        trace("posted_plan", None, f"posted plan ({ts}): {plan_summary(plan)}", ts=ts)
    return plan, ts


def send_receipt_for(email: str, run_id: str, invoice_id: str, state_dir: Path, *, ledger: Ledger, inbox: Inbox,
                     chat: Chat, mandate: Optional[dict] = None, mandate_path: Optional[Path] = None) -> list[TraceLine]:
    """Be the client, last step: the invoice is paid, so send one receipt in the chase thread and post the
    CLOSED line. The same receipt_draft/receipt_checks as the core loop, no model; idempotent on key
    <invoice_id>:receipt through the executor, so a run.py receipt and a panel receipt never both send."""
    from owed.agent import executor  # the only live-write path
    from owed.agent.drafter import receipt_draft
    from owed.agent.verifier import receipt_checks
    from owed.contract import Intent
    state_dir = Path(state_dir)
    inv = ledger.get(invoice_id)
    if not inv.paid:
        raise ValueError(f"{invoice_id} does not read paid in the ledger; no receipt")
    mandate = mandate or load_mandate(path=mandate_path)
    draft = receipt_draft(inv, mandate)
    refusals = receipt_checks(draft, inv, mandate)
    if refusals:
        raise ValueError(f"receipt for {invoice_id} refused: {refusals[0].detail}")
    thread_id = inv.thread_id
    if not thread_id and hasattr(inbox, "latest_thread_id"):
        thread_id = inbox.latest_thread_id(f"subject:{invoice_id}")  # type: ignore[attr-defined]
    amount = _money(inv.amount_total)
    plan = Plan(run_id=run_id, created_at=datetime.now(timezone.utc), intents=[Intent(
        kind="send_email", invoice_id=invoice_id, step=0, idempotency_key=f"{invoice_id}:receipt",
        payload={"to": inv.client_email, "subject": draft["subject"], "body": draft["body"],
                 "amount": inv.amount_total, "thread_id": thread_id, "receipt": True})])
    trace = Tracer(run_id, state_dir / "traces" / f"{run_id}.jsonl", append=True)
    trace("reverify", invoice_id, f"CLOSED {invoice_id}: {amount} received; sending receipt in the chase thread")
    lines = executor.execute(plan, ledger, inbox, None, chat, state_dir, run_id)
    for line in lines:
        trace.write(line)
    executor.post(chat, f"OWED closed {invoice_id}: closed, {amount} received, receipt sent to {inv.client_email}")
    trace("done", invoice_id, f"closed {invoice_id}, receipt sent")
    return lines


def execute_for(email, run_id: str, state_dir: Path, *, ledger: Optional[Ledger] = None,
                inbox: Optional[Inbox] = None, calendar: Optional[Calendar] = None, chat=None,
                plan_ts: str = "", clients: Optional[set[str]] = None) -> Plan:
    """The tap -> re-verify live -> execute -> assert half, for a rehearsed plan. The tap cannot be
    skipped: with plan_ts the plan was already posted (rehearse_for with a chat); without it the plan is
    posted here first. chat is the tap channel: a WebTapChat for the web Approve, the freelancer's Slack
    (the default), or a list of both, in which case the plan posts to each and either one's approval
    counts. Adapters default to the live ones. A plan holding an invoice outside the scope (an email, a
    list, or the desk's `clients`) is refused; None or ALL_CLIENTS means the whole ledger."""
    emails = _emails(email, clients)
    chat = _as_chat(chat)
    state_dir = Path(state_dir)
    plan_path = state_dir / "plans" / f"{run_id}.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"no plan {run_id} in {state_dir}; call rehearse_for first")
    plan = plan_from_json(json.loads(plan_path.read_text()))
    if ledger is None or inbox is None or chat is None:
        live_ledger, live_inbox, live_calendar, live_chat = _live_adapters(with_chat=chat is None)
        ledger, inbox = ledger or live_ledger, inbox or live_inbox
        calendar, chat = calendar or live_calendar, chat or live_chat
    if emails is not None:
        for iid in {i.invoice_id for i in plan.intents}:
            owner = ledger.get(iid).client_email.lower()
            if owner not in emails:
                raise ValueError(f"plan {run_id} has an intent for {iid}, whose client is {owner}, outside {sorted(emails)}")
    trace = Tracer(run_id, state_dir / "traces" / f"{run_id}.jsonl", append=True)
    if not plan_ts:
        from owed.agent import executor  # the only live-write path
        plan_ts = executor.post(chat, slack_plan_text(plan))
        trace("posted_plan", None, f"posted plan ({plan_ts}): {plan_summary(plan)}", ts=plan_ts)
    return gate_and_execute(plan, plan_ts, ledger=ledger, inbox=inbox, calendar=calendar, chat=chat,
                            state_dir=state_dir, traces_dir=state_dir / "traces", trace=trace, run_id=run_id)


def _groups(plan: Plan) -> dict[str, list]:
    out: dict[str, list] = {}
    for it in plan.intents:
        out.setdefault(it.idempotency_key, []).append(it)
    return out


def slack_plan_text(plan: Plan) -> str:
    from owed.agent.asserter import plan_post_marker
    taps = sum(1 for i in plan.intents if i.requires_tap)
    head = f"{plan_post_marker(plan.run_id)}: {plan_summary(plan)}"
    if plan.intents:
        head += "  -- react with :white_check_mark: to approve" + (" (step 3 needs it)" if taps else "")
    body = "\n".join(format_plan(plan).splitlines()[1:-1])
    return f"{head}\n{body}" if body else head
