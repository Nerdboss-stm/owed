"""Seed Stripe test mode with the demo invoice: $3,400, 19 days overdue, INV-0042.
Test-data setup, not agent behaviour. Idempotent: skips if the invoice_id already exists.

  python scripts/seed_stripe.py [--client-email stmallela.us01@gmail.com] [--days-overdue 19] [--amount 3400]
"""
from __future__ import annotations
import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stripe  # noqa: E402

from owed.adapters.ledger_live import _meta  # noqa: E402
from owed.config import env, today  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoice-id", default="INV-0042")
    ap.add_argument("--client-name", default="Marlow & Finch Studio")
    ap.add_argument("--client-email", default="stmallela.us01@gmail.com")
    ap.add_argument("--amount", type=float, default=3400.0)
    ap.add_argument("--days-overdue", type=int, default=19)
    a = ap.parse_args()

    stripe.api_key = env("STRIPE_TEST_KEY")
    client_email = a.client_email

    for inv in stripe.Invoice.list(limit=100).auto_paging_iter():
        if _meta(inv.metadata).get("invoice_id") != a.invoice_id:
            continue
        if inv.status == "open":
            print(f"exists: {a.invoice_id} = {inv.id} status={inv.status} remaining=${inv.amount_remaining/100:,.2f}")
            return
        # A paid/void/uncollectible copy is stale demo data. Retag it so invoice_id stays unique, then reseed.
        stale_id = f"{a.invoice_id}-stale-{inv.id[-6:]}"
        stripe.Invoice.modify(inv.id, metadata={"invoice_id": stale_id})
        print(f"retagged stale {inv.id} status={inv.status} as {stale_id}")

    existing = stripe.Customer.list(email=client_email, limit=1).data
    cust = existing[0] if existing else stripe.Customer.create(name=a.client_name, email=client_email)
    print(f"customer: {cust.id} ({'reused' if existing else 'created'}) {cust.name} <{cust.email}>")
    due = today() - timedelta(days=a.days_overdue)
    inv = stripe.Invoice.create(
        customer=cust.id,
        collection_method="send_invoice",
        days_until_due=30,  # Stripe refuses past due dates; the real due date lives in metadata
        metadata={"invoice_id": a.invoice_id, "due_date": due.isoformat(), "last_chased_step": "0"},
    )
    # Attach the line item to this invoice explicitly: recent Stripe API versions no longer
    # sweep pending invoice items into a new invoice, which finalizes it at $0 and marks it paid.
    stripe.InvoiceItem.create(customer=cust.id, invoice=inv.id, amount=round(a.amount * 100), currency="usd",
                              description=f"Brand identity system, final delivery ({a.invoice_id})")
    inv = stripe.Invoice.finalize_invoice(inv.id)
    if inv.amount_remaining == 0:
        raise RuntimeError(f"seed produced a $0 invoice {inv.id}; line item did not attach")
    print(f"created: {a.invoice_id} = {inv.id} status={inv.status} customer={client_email} "
          f"amount=${inv.amount_remaining/100:,.2f} due={due} ({a.days_overdue}d overdue as of {today()})")


if __name__ == "__main__":
    main()
