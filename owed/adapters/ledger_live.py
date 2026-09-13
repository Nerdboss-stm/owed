"""Stripe test mode as the ledger. Source of truth for owed / paid.

Invoice identity: metadata.invoice_id (e.g. INV-0042) if set, else Stripe's invoice number.
Due date: metadata.due_date (ISO) if set (Stripe refuses past due dates on create), else Stripe due_date.
"""
from __future__ import annotations
from datetime import date, datetime, timezone

import stripe

from owed.adapters.util import retry_read
from owed.config import env
from owed.contract import Invoice, Ledger, Step, live_write


def _meta(obj) -> dict:
    """stripe>=8 returns a StripeObject for metadata (not a mapping); normalise to a plain dict."""
    if obj is None:
        return {}
    if hasattr(obj, "to_dict"):
        return dict(obj.to_dict())
    return dict(obj)


class StripeLedger(Ledger):
    def __init__(self, api_key: str | None = None):
        stripe.api_key = api_key or env("STRIPE_TEST_KEY")
        self._ids: dict[str, str] = {}  # invoice_id -> stripe invoice id

    # ---- reads ----
    def _all_open(self) -> list[stripe.Invoice]:
        return retry_read(lambda: list(stripe.Invoice.list(limit=100, status="open").auto_paging_iter()))

    def _to_invoice(self, inv: stripe.Invoice) -> Invoice:
        meta = _meta(inv.metadata)
        invoice_id = meta.get("invoice_id") or inv.number or inv.id
        self._ids[invoice_id] = inv.id
        if meta.get("due_date"):
            due = date.fromisoformat(meta["due_date"])
        elif inv.due_date:
            due = datetime.fromtimestamp(inv.due_date, tz=timezone.utc).date()
        else:
            due = datetime.fromtimestamp(inv.created, tz=timezone.utc).date()
        return Invoice(
            invoice_id=invoice_id,
            client_email=inv.customer_email or "",
            client_name=inv.customer_name or "",
            amount_due=inv.amount_remaining / 100,
            amount_total=inv.total / 100,
            due_date=due,
            paid=(inv.status == "paid" or inv.amount_remaining == 0),
            last_chased_step=int(meta.get("last_chased_step", 0)),  # type: ignore[arg-type]
            thread_id=meta.get("thread_id") or None,
        )

    def overdue(self, today: date) -> list[Invoice]:
        out = [self._to_invoice(i) for i in self._all_open()]
        return [i for i in out if not i.paid and i.due_date < today]

    def get(self, invoice_id: str) -> Invoice:
        # Re-read live every time: this is the paid-since-rehearsal check.
        if invoice_id in self._ids:
            inv = retry_read(lambda: stripe.Invoice.retrieve(self._ids[invoice_id]))
            return self._to_invoice(inv)
        for inv in retry_read(lambda: list(stripe.Invoice.list(limit=100).auto_paging_iter())):
            meta = _meta(inv.metadata)
            if (meta.get("invoice_id") or inv.number or inv.id) == invoice_id:
                return self._to_invoice(inv)
        raise KeyError(f"invoice {invoice_id} not in Stripe")

    def count_links(self, invoice_id: str) -> int:
        links = retry_read(lambda: list(stripe.PaymentLink.list(limit=100, active=True).auto_paging_iter()))
        return sum(1 for l in links if _meta(l.metadata).get("invoice_id") == invoice_id)

    # ---- writes (only executor.py may call these) ----
    def create_payment_link(self, invoice_id: str) -> str:
        inv = self.get(invoice_id)
        live_write()
        price = stripe.Price.create(
            currency="usd",
            unit_amount=round(inv.amount_due * 100),
            product_data={"name": f"Invoice {invoice_id}"},
        )
        link = stripe.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            metadata={"invoice_id": invoice_id},
        )
        return link.url

    def mark_chased(self, invoice_id: str, step: Step) -> None:
        self.get(invoice_id)  # populates _ids
        live_write()
        stripe.Invoice.modify(
            self._ids[invoice_id],
            metadata={"last_chased_step": str(step), "last_chased_at": datetime.now(timezone.utc).isoformat()},
        )
