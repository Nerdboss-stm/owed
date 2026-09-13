"""
In-memory stand-ins for the Live adapters.

They implement the contract ABCs exactly and count every write, so tests and
evals can run the real agent end to end and read back "end state in each app"
without a network. They stand in for adapters/*_live.py only; the agent still
builds its own Shadow copies from them during rehearsal.

Nothing in this module imports a network library.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from owed.contract import Calendar, Chat, Inbox, Invoice, Ledger, Message, Step


class StubLedger(Ledger):
    """Ledger with rows in memory. Rows may share an invoice_id (duplicate scenario)."""

    def __init__(self, invoices: list[Invoice]):
        self._rows: list[Invoice] = [replace(i) for i in invoices]
        self.writes = 0
        self.links: dict[str, list[str]] = defaultdict(list)
        self.chased: list[tuple[str, Step]] = []

    def _rows_for(self, invoice_id: str) -> list[Invoice]:
        rows = [r for r in self._rows if r.invoice_id == invoice_id]
        if not rows:
            raise KeyError(f"ledger has no invoice {invoice_id}")
        return rows

    # -- reads -------------------------------------------------------------
    def overdue(self, today: date) -> list[Invoice]:
        return [
            replace(r)
            for r in self._rows
            if not r.paid and r.amount_due > 0 and r.due_date < today
        ]

    def get(self, invoice_id: str) -> Invoice:
        return replace(self._rows_for(invoice_id)[0])

    def count_links(self, invoice_id: str) -> int:
        return len(self.links.get(invoice_id, []))

    # -- writes (counted) --------------------------------------------------
    def create_payment_link(self, invoice_id: str) -> str:
        self._rows_for(invoice_id)
        self.writes += 1
        url = f"https://pay.stub.local/{invoice_id}/{self.count_links(invoice_id) + 1}"
        self.links[invoice_id].append(url)
        return url

    def mark_chased(self, invoice_id: str, step: Step) -> None:
        rows = self._rows_for(invoice_id)
        self.writes += 1
        for r in rows:
            r.last_chased_step = step
        self.chased.append((invoice_id, step))

    # -- scenario-side mutation: money arriving is not an agent write --------
    def receive_payment(self, invoice_id: str, amount: Optional[float] = None) -> None:
        for r in self._rows_for(invoice_id):
            pay = r.amount_due if amount is None else amount
            r.amount_due = max(0.0, round(r.amount_due - pay, 2))
            r.paid = r.amount_due == 0.0

    def snapshot(self) -> list[tuple]:
        """Comparable view of every row; used for 'ledger unchanged' checks."""
        return [(r.invoice_id, r.paid, r.amount_due, r.last_chased_step) for r in self._rows]


class StubInbox(Inbox):
    def __init__(self, threads: dict[str, list[Message]], owner: str = "freelancer@owed.test"):
        self._threads: dict[str, list[Message]] = {k: list(v) for k, v in threads.items()}
        self.owner = owner
        self.writes = 0
        self.sent: list[Message] = []

    def thread(self, thread_id: str) -> list[Message]:
        if thread_id is None:
            return []
        return list(self._threads.get(thread_id, []))

    def send(self, to: str, subject: str, body: str, thread_id: Optional[str]) -> str:
        self.writes += 1
        msg_id = f"msg-{len(self.sent) + 1}"
        msg = Message(
            thread_id=thread_id or f"t-{msg_id}",
            from_addr=self.owner,
            to_addr=to,
            subject=subject,
            body=body,
            ts=datetime.now(),
        )
        self.sent.append(msg)
        self._threads.setdefault(msg.thread_id, []).append(msg)
        return msg_id

    def count_sent(self, to: str, subject_contains: str) -> int:
        needle = subject_contains.lower()
        return sum(1 for m in self.sent if m.to_addr == to and needle in m.subject.lower())


class StubCalendar(Calendar):
    def __init__(self, slots: Optional[list[datetime]] = None):
        self._slots: list[datetime] = sorted(slots or [])
        self.writes = 0
        self.events: list[tuple[str, datetime, str]] = []

    def free_slots(self, start: date, n: int) -> list[datetime]:
        if self._slots:
            return [s for s in self._slots if s.date() >= start][:n]
        out: list[datetime] = []
        day = start + timedelta(days=1)
        while len(out) < n:
            if day.weekday() < 5:
                out.append(datetime.combine(day, time(10, 0)))
            day += timedelta(days=1)
        return out

    def create_event(self, title: str, start: datetime, attendee: str) -> str:
        self.writes += 1
        self.events.append((title, start, attendee))
        return f"evt-{len(self.events)}"

    def count_events(self, title_contains: str) -> int:
        needle = title_contains.lower()
        return sum(1 for t, _, _ in self.events if needle in t.lower())


class StubChat(Chat):
    """Slack stand-in. `tap` is the scripted reaction; `on_tap` fires scenario events
    (for example: money arrives between rehearsal and execute)."""

    def __init__(self, tap: bool = True, on_tap: Optional[Callable[[], None]] = None):
        self.tap = tap
        self.on_tap = on_tap
        self.writes = 0
        self.posts: list[str] = []
        self.tap_calls = 0

    def post(self, text: str, blocks: Optional[list] = None) -> str:
        self.writes += 1
        self.posts.append(text)
        return f"{len(self.posts)}.000000"

    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        self.tap_calls += 1
        if self.on_tap is not None:
            self.on_tap()
        return self.tap

    def count_posts(self, contains: str) -> int:
        return sum(1 for p in self.posts if contains in p)


@dataclass
class Stubs:
    ledger: StubLedger
    inbox: StubInbox
    calendar: StubCalendar
    chat: StubChat

    def write_counts(self) -> dict[str, int]:
        return {
            "stripe": self.ledger.writes,
            "gmail": self.inbox.writes,
            "calendar": self.calendar.writes,
            "slack": self.chat.writes,
        }

    def total_writes(self) -> int:
        return sum(self.write_counts().values())

    def as_kwargs(self) -> dict:
        return {"ledger": self.ledger, "inbox": self.inbox, "calendar": self.calendar, "chat": self.chat}
