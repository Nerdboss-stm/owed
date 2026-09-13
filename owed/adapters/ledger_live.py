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
        return retry_read(lambda: list(
            stripe.Invoice.list(limit=100, status="open", expand=["data.customer"]).auto_paging_iter()))

    def _to_invoice(self, inv: stripe.Invoice) -> Invoice:
        meta = _meta(inv.metadata)
        invoice_id = meta.get("invoice_id") or inv.number or inv.id
        self._ids[invoice_id] = inv.id
        # A finalized invoice freezes customer_email; the Customer object is the live contact.
        cust = inv.customer if not isinstance(inv.customer, str) else None
        client_email = (getattr(cust, "email", None) or inv.customer_email or "") if cust else (inv.customer_email or "")
        client_name = (getattr(cust, "name", None) or inv.customer_name or "") if cust else (inv.customer_name or "")
        if meta.get("due_date"):
            due = date.fromisoformat(meta["due_date"])
        elif inv.due_date:
            due = datetime.fromtimestamp(inv.due_date, tz=timezone.utc).date()
        else:
            due = datetime.fromtimestamp(inv.created, tz=timezone.utc).date()
        return Invoice(
            invoice_id=invoice_id,
            client_email=client_email,
            client_name=client_name,
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
            inv = retry_read(lambda: stripe.Invoice.retrieve(self._ids[invoice_id], expand=["customer"]))
            return self._to_invoice(inv)
        for inv in retry_read(lambda: list(
                stripe.Invoice.list(limit=100, expand=["data.customer"]).auto_paging_iter())):
            meta = _meta(inv.metadata)
            if (meta.get("invoice_id") or inv.number or inv.id) == invoice_id:
                return self._to_invoice(inv)
        raise KeyError(f"invoice {invoice_id} not in Stripe")

    def paid_after_chase(self) -> list[Invoice]:
        """Paid invoices that were chased and have not been receipted: the close-the-loop candidates.
        Not part of the contract ABC; callers use getattr and skip adapters without it."""
        out = []
        for inv in retry_read(lambda: list(
                stripe.Invoice.list(limit=100, status="paid", expand=["data.customer"]).auto_paging_iter())):
            meta = _meta(inv.metadata)
            if int(meta.get("last_chased_step", 0)) > 0 and not meta.get("receipt_sent"):
                out.append(self._to_invoice(inv))
        return out

    def _links_for(self, invoice_id: str) -> list:
        links = retry_read(lambda: list(stripe.PaymentLink.list(limit=100, active=True).auto_paging_iter()))
        return [l for l in links if _meta(l.metadata).get("invoice_id") == invoice_id]

    def count_links(self, invoice_id: str) -> int:
        return len(self._links_for(invoice_id))

    def link_payments(self, invoice_id: str) -> list[dict]:
        """Completed checkout sessions on this invoice's payment links. A link settles a session, not the
        invoice; executor.reconcile_links turns these into 'paid out of band' on the invoice."""
        out = []
        for l in self._links_for(invoice_id):
            sessions = retry_read(lambda: list(
                stripe.checkout.Session.list(payment_link=l.id, limit=100).auto_paging_iter()))
            for s in sessions:
                if s.payment_status == "paid":
                    out.append({"session_id": s.id, "amount": (s.amount_total or 0) / 100,
                                "paid_at": datetime.fromtimestamp(s.created, tz=timezone.utc).isoformat(), "link": l.url})
        return out

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

    def mark_paid_out_of_band(self, invoice_id: str, session_id: str, amount: float) -> None:
        """The payment arrived through the link; record it on the invoice so the ledger reads paid."""
        self.get(invoice_id)
        live_write()
        stripe.Invoice.pay(self._ids[invoice_id], paid_out_of_band=True)
        stripe.Invoice.modify(self._ids[invoice_id], metadata={
            "paid_via_link": session_id, "paid_amount": f"{amount:.2f}",
            "paid_at": datetime.now(timezone.utc).isoformat()})

    def mark_receipted(self, invoice_id: str) -> None:
        self.get(invoice_id)
        live_write()
        stripe.Invoice.modify(self._ids[invoice_id], metadata={"receipt_sent": datetime.now(timezone.utc).isoformat()})
