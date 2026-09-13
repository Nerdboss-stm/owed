"""Seed Stripe test mode with one invoice. Test-data setup, not agent behaviour. The one implementation:
scripts/seed_stripe.py, scripts/seed_scale.py and ui/client_server.py all call seed_invoice().

seed_invoice(client_email, ...) -> dict

Idempotent per client. Without an explicit invoice_id the id is INV-<4 hex of the email>; an open
invoice with that prefix is reused, and once one is paid or voided the next seed gets a "-2", "-3"
suffix so every (invoice, step) idempotency key stays unique. With an explicit invoice_id (the demo's
INV-0042) stale copies are retagged and the same id is reused.

Options for scenario ledgers: last_chased_step (metadata), paid (paid out of band), partial (a credit
note that lowers the balance and leaves the total intact). Nothing in owed/agent imports this.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

import stripe

from owed.adapters.ledger_live import _meta
from owed.config import env, today
from owed.run import client_key

DEFAULT_AMOUNT = 3400.0
DEFAULT_DAYS_OVERDUE = 12  # step 2 (day 10) is due, step 3 (day 21) is not: a direct ask with a payment link


def _name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    parts = [p for p in local.replace(".", " ").replace("_", " ").replace("-", " ").split() if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or "Client"


def _row(iid: str, inv, email: str, created: bool, days_overdue: int, name: Optional[str] = None) -> dict:
    return {"invoice_id": iid, "stripe_id": inv.id, "created": created, "client_email": email,
            "client_name": name, "status": inv.status, "amount_due": inv.amount_remaining / 100,
            "amount_total": inv.total / 100, "due_date": _meta(inv.metadata).get("due_date"),
            "days_overdue": days_overdue, "last_chased_step": int(_meta(inv.metadata).get("last_chased_step", 0))}


def seed_invoice(client_email: str, *, client_name: Optional[str] = None, invoice_id: Optional[str] = None,
                 amount: float = DEFAULT_AMOUNT, days_overdue: int = DEFAULT_DAYS_OVERDUE,
                 description: Optional[str] = None, last_chased_step: int = 0, paid: bool = False,
                 partial: float = 0.0) -> dict:
    stripe.api_key = env("STRIPE_TEST_KEY")
    email = client_email.strip().lower()
    base_id = invoice_id or f"INV-{client_key(email)}"

    matches = []
    for inv in stripe.Invoice.list(limit=100).auto_paging_iter():
        iid = _meta(inv.metadata).get("invoice_id") or ""
        if iid == base_id or iid.startswith(base_id + "-"):
            matches.append((iid, inv))
    for iid, inv in matches:
        if inv.status == "open" and inv.amount_remaining > 0:
            return _row(iid, inv, email, False, days_overdue)
        if paid and invoice_id and iid == invoice_id and inv.status == "paid":
            return _row(iid, inv, email, False, days_overdue)

    if invoice_id:
        for iid, inv in matches:
            if iid == invoice_id:  # paid/void copy of a fixed demo id: retag so the id stays unique, then reseed
                stripe.Invoice.modify(inv.id, metadata={"invoice_id": f"{iid}-stale-{inv.id[-6:]}"})
        new_id = invoice_id
    else:
        new_id = base_id if not matches else f"{base_id}-{len(matches) + 1}"

    existing = stripe.Customer.list(email=email, limit=1).data
    name = client_name or (existing[0].name if existing and existing[0].name else _name_from_email(email))
    cust = existing[0] if existing else stripe.Customer.create(name=name, email=email)
    due = today() - timedelta(days=days_overdue)
    meta = {"invoice_id": new_id, "due_date": due.isoformat(), "last_chased_step": str(last_chased_step)}
    if last_chased_step:
        meta["last_chased_at"] = datetime.now(timezone.utc).isoformat()
    inv = stripe.Invoice.create(
        customer=cust.id,
        collection_method="send_invoice",
        days_until_due=30,  # Stripe refuses past due dates; the real due date lives in metadata
        metadata=meta,
    )
    # Attach the line item explicitly: recent API versions no longer sweep pending items into a new invoice.
    stripe.InvoiceItem.create(customer=cust.id, invoice=inv.id, amount=round(amount * 100), currency="usd",
                              description=description or f"Brand identity system, final delivery ({new_id})")
    inv = stripe.Invoice.finalize_invoice(inv.id)
    if inv.amount_remaining == 0:
        raise RuntimeError(f"seed produced a $0 invoice {inv.id}; line item did not attach")
    if partial:
        # A credit note on an open invoice lowers amount_due and leaves the total intact: a partial payment.
        stripe.CreditNote.create(invoice=inv.id, memo="Partial payment received",
                                 lines=[{"type": "custom_line_item", "description": "Partial payment received",
                                         "quantity": 1, "unit_amount": round(partial * 100)}])
        inv = stripe.Invoice.retrieve(inv.id)
    if paid:
        inv = stripe.Invoice.pay(inv.id, paid_out_of_band=True)
        stripe.Invoice.modify(inv.id, metadata={"paid_at": datetime.now(timezone.utc).isoformat()})
        inv = stripe.Invoice.retrieve(inv.id)
    return _row(new_id, inv, email, True, days_overdue, name)
