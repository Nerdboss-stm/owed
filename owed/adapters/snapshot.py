"""snapshot(): copy live state into a JSON-safe dict. ShadowWorld: the four Shadow adapters built from it.

stdlib only. Takes adapters by their ABC, so it never imports a network library itself.
Scenarios (scenarios/*.json) are this same dict, hand-written:

{
  "today": "2026-09-13",
  "invoices": [ {invoice_id, client_email, client_name, amount_due, amount_total,
                 due_date (ISO), paid, last_chased_step, thread_id} ],
  "threads":  { "<thread_id>": [ {thread_id, from_addr, to_addr, subject, body, ts (ISO)} ] },
  "free_slots": [ "<ISO datetime>" ],
  "links":    { "<invoice_id>": [ "<url>" ] },
  "posts":    [ "<text>" ],
  "tap": false
}
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import date, datetime
import json
from typing import Optional

from owed.adapters.calendar_shadow import ShadowCalendar
from owed.adapters.chat_shadow import ShadowChat
from owed.adapters.inbox_shadow import ShadowInbox
from owed.adapters.ledger_shadow import ShadowLedger
from owed.contract import Calendar, Inbox, Intent, Invoice, Ledger, Message, Step


def snapshot(ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar], today: date,
             n_slots: int = 6) -> dict:
    """Reads only. Finds each invoice's thread via metadata, else the latest mail from the client."""
    invoices = ledger.overdue(today)
    threads: dict[str, list[Message]] = {}
    for inv in invoices:
        if not inv.thread_id and hasattr(inbox, "latest_thread_id"):
            # Prefer the thread that names this invoice; else the client's latest thread.
            find = inbox.latest_thread_id  # type: ignore[attr-defined]
            inv.thread_id = find(f"from:{inv.client_email} {inv.invoice_id}") or find(f"from:{inv.client_email}")
        if inv.thread_id and inv.thread_id not in threads:
            threads[inv.thread_id] = inbox.thread(inv.thread_id)
    free = calendar.free_slots(today, n_slots) if calendar else []
    return {
        "today": today.isoformat(),
        "invoices": [asdict(i) | {"due_date": i.due_date.isoformat()} for i in invoices],
        "threads": {tid: [asdict(m) | {"ts": m.ts.isoformat()} for m in msgs] for tid, msgs in threads.items()},
        "free_slots": [s.isoformat() for s in free],
        "links": {i.invoice_id: [] for i in invoices},
        "posts": [],
        "tap": False,
    }


def to_json(snap: dict) -> str:
    return json.dumps(snap, indent=2, default=str)


def from_json(text: str) -> dict:
    return json.loads(text)


def _invoice(d: dict) -> Invoice:
    return Invoice(
        invoice_id=d["invoice_id"], client_email=d["client_email"], client_name=d.get("client_name", ""),
        amount_due=float(d["amount_due"]), amount_total=float(d.get("amount_total", d["amount_due"])),
        due_date=date.fromisoformat(d["due_date"]), paid=bool(d.get("paid", False)),
        last_chased_step=int(d.get("last_chased_step", 0)),  # type: ignore[arg-type]
        thread_id=d.get("thread_id"),
    )


def _message(d: dict) -> Message:
    return Message(thread_id=d["thread_id"], from_addr=d["from_addr"], to_addr=d.get("to_addr", ""),
                   subject=d.get("subject", ""), body=d.get("body", ""), ts=datetime.fromisoformat(d["ts"]))


class ShadowWorld:
    """The four Shadow adapters sharing one intents list. This is what the rehearsal runs against."""

    def __init__(self, ledger: ShadowLedger, inbox: ShadowInbox, calendar: ShadowCalendar,
                 chat: ShadowChat, intents: list[Intent], today: date):
        self.ledger, self.inbox, self.calendar, self.chat = ledger, inbox, calendar, chat
        self.intents, self.today = intents, today

    @classmethod
    def from_snapshot(cls, snap: dict) -> "ShadowWorld":
        intents: list[Intent] = []
        return cls(
            ledger=ShadowLedger([_invoice(d) for d in snap["invoices"]], intents, snap.get("links")),
            inbox=ShadowInbox({t: [_message(m) for m in ms] for t, ms in snap.get("threads", {}).items()}, intents),
            # scenarios/*.json spell the slots "calendar_slots"; live snapshots spell them "free_slots"
            calendar=ShadowCalendar([datetime.fromisoformat(s)
                                     for s in (snap.get("free_slots") or snap.get("calendar_slots") or [])], intents),
            chat=ShadowChat(snap.get("posts"), bool(snap.get("tap", False)), intents),
            intents=intents,
            today=date.fromisoformat(snap["today"]),
        )

    def set_context(self, invoice_id: str, step: Step, **extra) -> None:
        for a in (self.ledger, self.inbox, self.calendar, self.chat):
            a.set_context(invoice_id, step, **extra)
