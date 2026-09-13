"""
Scenario files: scenarios/*.json, one per row of the SPEC reliability table.

File format (all dates ISO; amounts are numbers, never strings):

{
  "name": "01_clean_day12",
  "row": "clean overdue, day 12",              # exact SPEC table text
  "expected": "step 2 sent, link attached",    # SPEC 'Expected' column
  "end_state": "Gmail 1 sent, Stripe link 1",  # SPEC 'End state checked' column
  "today": "2026-09-13",
  "invoices": [ {Invoice fields, due_date as YYYY-MM-DD} ],
  "threads": { "<thread_id>": [ {Message fields, ts as ISO datetime} ] },
  "calendar_slots": ["2026-09-15T10:00:00"],   # optional
  "tap": true,                                 # scripted Slack reaction
  "events": [ {"on": "tap", "op": "receive_payment", "invoice_id": "INV-0042", "amount": null} ],
  "runs": 1,                                   # how many times run.py is executed on the same state
  "actor_draft": { "INV-0042:2": {"subject": "...", "body": "..."} },   # scripted actor output
  "mandate": {},                               # overrides merged into the default mandate
  "expect": {                                  # every key optional; checked by evals/checks.py
    "gmail_sent": 1, "stripe_links": 1, "calendar_events": 0, "ledger_writes": 0,
    "slack_posts_min": 1, "slack_contains": "ABORTED: paid",
    "refusal_reasons": ["injection"], "no_refusals": true,
    "email_amount": 1400.0, "link_in_email": true,
    "refused_before_tap": true, "tap_awaited": true
  }
}

Amounts in `actor_draft` are ignored on purpose: the drafter receives the amount
from the ledger and may not alter it (CLAUDE.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from evals.stubs import StubCalendar, StubChat, StubInbox, StubLedger, Stubs
from owed.contract import Invoice, Message

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios"

_INVOICE_KEYS = {"invoice_id", "client_email", "client_name", "amount_due", "amount_total", "due_date", "paid"}
_MESSAGE_KEYS = {"thread_id", "from_addr", "to_addr", "subject", "body", "ts"}
_EVENT_OPS = {"receive_payment"}
_EVENT_HOOKS = {"tap"}


@dataclass
class Event:
    on: str
    op: str
    invoice_id: str
    amount: Optional[float] = None


@dataclass
class Expect:
    gmail_sent: Optional[int] = None
    stripe_links: Optional[int] = None
    calendar_events: Optional[int] = None
    ledger_writes: Optional[int] = None
    slack_posts_min: Optional[int] = None
    slack_contains: Optional[str] = None
    refusal_reasons: list[str] = field(default_factory=list)
    no_refusals: bool = False
    email_amount: Optional[float] = None
    link_in_email: bool = False
    refused_before_tap: bool = False
    tap_awaited: bool = False


@dataclass
class Scenario:
    name: str
    row: str
    expected: str
    end_state: str
    today: date
    invoices: list[Invoice]
    threads: dict[str, list[Message]]
    calendar_slots: list[datetime]
    tap: bool
    events: list[Event]
    runs: int
    actor_draft: dict[str, dict]
    mandate: dict
    expect: Expect
    path: Path


def _require(d: dict, keys: set[str], where: str) -> None:
    missing = keys - set(d)
    if missing:
        raise ValueError(f"{where}: missing keys {sorted(missing)}")


def _invoice(d: dict, where: str) -> Invoice:
    _require(d, _INVOICE_KEYS, where)
    inv = Invoice(
        invoice_id=d["invoice_id"],
        client_email=d["client_email"],
        client_name=d["client_name"],
        amount_due=float(d["amount_due"]),
        amount_total=float(d["amount_total"]),
        due_date=date.fromisoformat(d["due_date"]),
        paid=bool(d["paid"]),
        last_chased_step=int(d.get("last_chased_step", 0)),
        thread_id=d.get("thread_id"),
    )
    if inv.amount_due > inv.amount_total:
        raise ValueError(f"{where}: amount_due {inv.amount_due} exceeds amount_total {inv.amount_total}")
    if inv.last_chased_step not in (0, 1, 2, 3):
        raise ValueError(f"{where}: last_chased_step must be 0..3")
    return inv


def _message(d: dict, where: str) -> Message:
    _require(d, _MESSAGE_KEYS, where)
    return Message(
        thread_id=d["thread_id"],
        from_addr=d["from_addr"],
        to_addr=d["to_addr"],
        subject=d["subject"],
        body=d["body"],
        ts=datetime.fromisoformat(d["ts"]),
    )


def _event(d: dict, where: str) -> Event:
    _require(d, {"on", "op", "invoice_id"}, where)
    if d["on"] not in _EVENT_HOOKS:
        raise ValueError(f"{where}: unknown hook {d['on']!r}; allowed {sorted(_EVENT_HOOKS)}")
    if d["op"] not in _EVENT_OPS:
        raise ValueError(f"{where}: unknown op {d['op']!r}; allowed {sorted(_EVENT_OPS)}")
    amount = d.get("amount")
    return Event(on=d["on"], op=d["op"], invoice_id=d["invoice_id"], amount=None if amount is None else float(amount))


def _expect(d: dict, where: str) -> Expect:
    allowed = set(Expect.__dataclass_fields__)
    unknown = set(d) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown expect keys {sorted(unknown)}")
    return Expect(**d)


def load(name_or_path: str | Path) -> Scenario:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{path.name}.json"
    where = f"scenario {path.name}"
    raw = json.loads(path.read_text())
    _require(raw, {"name", "row", "expected", "end_state", "today", "invoices", "threads", "expect"}, where)

    invoices = [_invoice(i, f"{where} invoice[{n}]") for n, i in enumerate(raw["invoices"])]
    if not invoices:
        raise ValueError(f"{where}: no invoices")
    threads = {
        tid: [_message(m, f"{where} thread {tid}[{n}]") for n, m in enumerate(msgs)]
        for tid, msgs in raw["threads"].items()
    }
    for tid, msgs in threads.items():
        for m in msgs:
            if m.thread_id != tid:
                raise ValueError(f"{where}: message in thread {tid} carries thread_id {m.thread_id}")
    for inv in invoices:
        if inv.thread_id is not None and inv.thread_id not in threads:
            raise ValueError(f"{where}: invoice {inv.invoice_id} references unknown thread {inv.thread_id}")

    runs = int(raw.get("runs", 1))
    if runs < 1:
        raise ValueError(f"{where}: runs must be >= 1")

    return Scenario(
        name=raw["name"],
        row=raw["row"],
        expected=raw["expected"],
        end_state=raw["end_state"],
        today=date.fromisoformat(raw["today"]),
        invoices=invoices,
        threads=threads,
        calendar_slots=[datetime.fromisoformat(s) for s in raw.get("calendar_slots", [])],
        tap=bool(raw.get("tap", True)),
        events=[_event(e, f"{where} event[{n}]") for n, e in enumerate(raw.get("events", []))],
        runs=runs,
        actor_draft=dict(raw.get("actor_draft", {})),
        mandate=dict(raw.get("mandate", {})),
        expect=_expect(raw["expect"], where),
        path=path,
    )


def load_all(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no scenarios in {directory}")
    return [load(p) for p in paths]


def seed(scenario: Scenario) -> Stubs:
    """Fresh stubs seeded from the scenario, with scenario events wired to the tap."""
    ledger = StubLedger(scenario.invoices)
    inbox = StubInbox(scenario.threads)
    calendar = StubCalendar(scenario.calendar_slots)

    tap_events = [e for e in scenario.events if e.on == "tap"]

    def on_tap() -> None:
        for e in tap_events:
            if e.op == "receive_payment":
                ledger.receive_payment(e.invoice_id, e.amount)

    chat = StubChat(tap=scenario.tap, on_tap=on_tap if tap_events else None)
    return Stubs(ledger=ledger, inbox=inbox, calendar=calendar, chat=chat)
