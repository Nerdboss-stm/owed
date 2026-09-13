"""Seed Stripe test mode with a demo invoice. Thin CLI over owed.seed.seed_invoice.

  python scripts/seed_stripe.py [--invoice-id INV-0042] [--client-email stmallela.us01@gmail.com]
                                [--client-name "Marlow & Finch Studio"] [--amount 3400] [--days-overdue 19]
                                [--last-chased-step 0] [--paid] [--partial 1200]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from owed.seed import seed_invoice  # noqa: E402


def describe(r: dict) -> str:
    verb = "created" if r["created"] else "exists"
    state = "paid" if r["status"] == "paid" else f"due=${r['amount_due']:,.2f} of ${r['amount_total']:,.2f}"
    return (f"{verb}: {r['invoice_id']} = {r['stripe_id']} <{r['client_email']}> {state} "
            f"due_date={r['due_date']} ({r['days_overdue']}d overdue) chased={r['last_chased_step']}")


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
    print(describe(seed_invoice(a.client_email, client_name=a.client_name, invoice_id=a.invoice_id, amount=a.amount,
                                days_overdue=a.days_overdue, last_chased_step=a.last_chased_step, paid=a.paid,
                                partial=a.partial)))


if __name__ == "__main__":
    main()
