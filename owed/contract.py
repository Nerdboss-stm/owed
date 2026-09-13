"""
OWED shared contract. Written first, committed first, never edited after 1:30 PM ET
without telling every session. All three sessions build against this file.

Ownership:
  session core  -> adapters/*_live.py, adapters/*_shadow.py, agent/*, run.py
  session evals -> scenarios/*, evals/*, tests/*
  session ui    -> ui/*
Only main branch edits README.md.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Literal, Optional
import json

# ---------- domain ----------

ThreadState = Literal["none", "says_paid", "disputes", "asks_docs", "promises_date", "injection", "other"]
Step = Literal[0, 1, 2, 3]  # 0 = do nothing

@dataclass
class Invoice:
    invoice_id: str
    client_email: str
    client_name: str
    amount_due: float          # remaining balance, from ledger only
    amount_total: float
    due_date: date
    paid: bool
    last_chased_step: Step = 0
    thread_id: Optional[str] = None

    def days_overdue(self, today: date) -> int:
        return max(0, (today - self.due_date).days)

@dataclass
class Message:
    thread_id: str
    from_addr: str
    to_addr: str
    subject: str
    body: str
    ts: datetime

# ---------- intents (what the agent WANTS to do; only executor turns these into live writes) ----------

@dataclass
class Intent:
    kind: Literal["send_email", "create_event", "create_payment_link", "post_chat"]
    invoice_id: str
    step: Step
    payload: dict               # send_email: {to, subject, body, amount}; create_event: {title, start_iso, attendee}
    idempotency_key: str = ""   # f"{invoice_id}:{step}"
    requires_tap: bool = False  # step 3 always True

    def __post_init__(self):
        if not self.idempotency_key:
            self.idempotency_key = f"{self.invoice_id}:{self.step}"
        if self.step == 3:
            self.requires_tap = True

@dataclass
class Refusal:
    invoice_id: str
    step: Step
    reason: Literal["paid", "says_paid", "disputes", "promises_date", "injection",
                    "verifier_discount", "verifier_amount", "verifier_tone", "verifier_deadline",
                    "already_sent", "waiting_tap"]
    detail: str

@dataclass
class Plan:
    run_id: str
    created_at: datetime
    intents: list[Intent] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, indent=2)

# ---------- assertions (end state read back from each app after execute) ----------

@dataclass
class EndState:
    app: Literal["stripe", "gmail", "calendar", "slack", "ledger_json"]
    expected: int
    actual: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.expected == self.actual

# ---------- adapter interfaces (Live and Shadow implement the same ABC) ----------

class Ledger(ABC):
    @abstractmethod
    def overdue(self, today: date) -> list[Invoice]: ...
    @abstractmethod
    def get(self, invoice_id: str) -> Invoice: ...
    @abstractmethod
    def create_payment_link(self, invoice_id: str) -> str: ...
    @abstractmethod
    def mark_chased(self, invoice_id: str, step: Step) -> None: ...
    @abstractmethod
    def count_links(self, invoice_id: str) -> int: ...

class Inbox(ABC):
    @abstractmethod
    def thread(self, thread_id: str) -> list[Message]: ...
    @abstractmethod
    def send(self, to: str, subject: str, body: str, thread_id: Optional[str]) -> str: ...
    @abstractmethod
    def count_sent(self, to: str, subject_contains: str) -> int: ...

class Calendar(ABC):
    @abstractmethod
    def free_slots(self, start: date, n: int) -> list[datetime]: ...
    @abstractmethod
    def create_event(self, title: str, start: datetime, attendee: str) -> str: ...
    @abstractmethod
    def count_events(self, title_contains: str) -> int: ...

class Chat(ABC):
    @abstractmethod
    def post(self, text: str, blocks: Optional[list] = None) -> str: ...
    @abstractmethod
    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool: ...
    @abstractmethod
    def count_posts(self, contains: str) -> int: ...

# ---------- trace line (one JSON object per line in traces/<run_id>.jsonl; UI reads only this) ----------

@dataclass
class TraceLine:
    ts: str
    run_id: str
    phase: Literal["read_ledger", "read_thread", "classify", "decide", "draft", "verify",
                   "rehearsal_done", "posted_plan", "tap", "reverify", "execute", "assert", "abort", "done"]
    invoice_id: Optional[str]
    decision: str               # human readable, this is the demo: "REFUSED INV-0042 step 2: paid since rehearsal"
    data: dict = field(default_factory=dict)

    def line(self) -> str:
        return json.dumps(asdict(self), default=str)

# ---------- live write counter, used by AC1 ----------

LIVE_WRITES = {"count": 0}

def live_write():
    LIVE_WRITES["count"] += 1
