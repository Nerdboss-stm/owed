"""Shadow ledger: in-memory copy of Stripe state. stdlib only. Never imports a network library."""
from __future__ import annotations
from dataclasses import replace
from datetime import date
from typing import Optional

from owed.adapters.shadow_base import ShadowWrites
from owed.contract import Intent, Invoice, Ledger, Step

SHADOW_LINK = "https://shadow.local/pay/{invoice_id}"


class ShadowLedger(ShadowWrites, Ledger):
    def __init__(self, invoices: list[Invoice], intents: Optional[list[Intent]] = None,
                 links: Optional[dict[str, list[str]]] = None):
        super().__init__(intents)
        # A list, not a dict: the "duplicate invoice numbers" scenario needs both copies visible.
        self._inv: list[Invoice] = [replace(i) for i in invoices]
        self._links: dict[str, list[str]] = {k: list(v) for k, v in (links or {}).items()}

    # ---- reads ----
    def overdue(self, today: date) -> list[Invoice]:
        return [replace(i) for i in self._inv if not i.paid and i.due_date < today]

    def get(self, invoice_id: str) -> Invoice:
        for i in self._inv:
            if i.invoice_id == invoice_id:
                return replace(i)
        raise KeyError(f"invoice {invoice_id} not in shadow ledger")

    def count_links(self, invoice_id: str) -> int:
        return len(self._links.get(invoice_id, []))

    # ---- writes: mutate shadow state, record an Intent, touch nothing outside the process ----
    def create_payment_link(self, invoice_id: str) -> str:
        inv = self.get(invoice_id)
        url = SHADOW_LINK.format(invoice_id=invoice_id)
        self._links.setdefault(invoice_id, []).append(url)
        self._intent("create_payment_link", {"amount": inv.amount_due, "url": url}, invoice_id=invoice_id)
        return url

    def mark_chased(self, invoice_id: str, step: Step) -> None:
        for i in self._inv:
            if i.invoice_id == invoice_id:
                i.last_chased_step = step
        self.writes.append({"op": "mark_chased", "invoice_id": invoice_id, "step": step})

    # ---- scenario helpers (test setup, not agent behaviour) ----
    def mark_paid(self, invoice_id: str, amount: Optional[float] = None) -> None:
        """Simulates money arriving between rehearsal and execute (AC3). Partial if amount < balance."""
        for i in self._inv:
            if i.invoice_id == invoice_id:
                paid = i.amount_due if amount is None else min(amount, i.amount_due)
                i.amount_due = round(i.amount_due - paid, 2)
                i.paid = i.amount_due == 0
