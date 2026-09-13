"""Seed Stripe test mode with the demo invoice: $3,400, 19 days overdue, INV-0042.
Test-data setup, not agent behaviour. Idempotent: skips if the invoice_id already exists.

  python scripts/seed_stripe.py [--client-email stmallela.us01@gmail.com] [--days-overdue 19] [--amount 3400]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from owed.seed import seed_invoice  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoice-id", default="INV-0042")
    ap.add_argument("--client-name", default="Marlow & Finch Studio")
    ap.add_argument("--client-email", default="stmallela.us01@gmail.com")
    ap.add_argument("--amount", type=float, default=3400.0)
    ap.add_argument("--days-overdue", type=int, default=19)
    a = ap.parse_args()

    r = seed_invoice(a.client_email, client_name=a.client_name, invoice_id=a.invoice_id,
                     amount=a.amount, days_overdue=a.days_overdue)
    verb = "created" if r["created"] else "exists"
    print(f"{verb}: {r['invoice_id']} = {r['stripe_id']} customer={r['client_email']} "
          f"amount=${r['amount_due']:,.2f} due={r['due_date']}")


if __name__ == "__main__":
    main()
