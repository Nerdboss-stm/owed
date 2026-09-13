"""Seed Stripe test mode with a demo invoice. Test-data setup, not agent behaviour.
Idempotent: skips if an OPEN invoice with the invoice_id exists; a paid/void copy is retagged as stale.

  python scripts/seed_stripe.py [--invoice-id INV-0042] [--client-email stmallela.us01@gmail.com]
                                [--client-name "Marlow & Finch Studio"] [--amount 3400] [--days-overdue 19]
                                [--last-chased-step 0] [--paid] [--partial 1200]

seed_invoice(...) is importable (scripts/seed_scale.py uses it).
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stripe  # noqa: E402

from owed.adapters.ledger_live import _meta  # noqa: E402
from owed.config import env, today  # noqa: E402


def seed_invoice(invoice_id: str, client_name: str, client_email: str, amount: float, days_overdue: int,
                 last_chased_step: int = 0, paid: bool = False, partial: float = 0.0) -> str:
    """Create (or find) one open invoice. Returns a one-line description. Never touches the agent."""
    stripe.api_key = env("STRIPE_TEST_KEY")
    for inv in stripe.Invoice.list(limit=100).auto_paging_iter():
        if _meta(inv.metadata).get("invoice_id") != invoice_id:
            continue
        if inv.status == "open" or (paid and inv.status == "paid"):
            return f"exists: {invoice_id} = {inv.id} status={inv.status} remaining=${inv.amount_remaining/100:,.2f}"
        stale_id = f"{invoice_id}-stale-{inv.id[-6:]}"
        stripe.Invoice.modify(inv.id, metadata={"invoice_id": stale_id})
        print(f"retagged stale {inv.id} status={inv.status} as {stale_id}")

    existing = stripe.Customer.list(email=client_email, limit=1).data
    cust = existing[0] if existing else stripe.Customer.create(name=client_name, email=client_email)
    due = today() - timedelta(days=days_overdue)
    meta = {"invoice_id": invoice_id, "due_date": due.isoformat(), "last_chased_step": str(last_chased_step)}
    if last_chased_step:
        meta["last_chased_at"] = datetime.now(timezone.utc).isoformat()
    inv = stripe.Invoice.create(customer=cust.id, collection_method="send_invoice", days_until_due=30, metadata=meta)
    # Attach the line item explicitly: recent API versions no longer sweep pending items into a new invoice.
    stripe.InvoiceItem.create(customer=cust.id, invoice=inv.id, amount=round(amount * 100), currency="usd",
                              description=f"Project work, final delivery ({invoice_id})")
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
    state = "paid" if paid else f"due=${inv.amount_remaining/100:,.2f} of ${inv.total/100:,.2f}"
    return (f"created: {invoice_id} = {inv.id} {cust.name} <{client_email}> {state} "
            f"due_date={due} ({days_overdue}d overdue as of {today()}) chased={last_chased_step}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoice-id", default="INV-0042")
    ap.add_argument("--client-name", default="Marlow & Finch Studio")
    ap.add_argument("--client-email", default="stmallela.us01@gmail.com")
    ap.add_argument("--amount", type=float, default=3400.0)
    ap.add_argument("--days-overdue", type=int, default=19)
    ap.add_argument("--last-chased-step", type=int, default=0)
    ap.add_argument("--paid", action="store_true")
    ap.add_argument("--partial", type=float, default=0.0, help="amount already received (credit note)")
    a = ap.parse_args()
    print(seed_invoice(a.invoice_id, a.client_name, a.client_email, a.amount, a.days_overdue,
                       a.last_chased_step, a.paid, a.partial))


if __name__ == "__main__":
    main()
