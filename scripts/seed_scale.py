"""Seed the scale ledger: 12 invoices across 5 clients (+tag addresses on one test mailbox).
Test-data setup, not agent behaviour. Idempotent through seed_invoice.

  python scripts/seed_scale.py

Three of these need a client reply in the freelancer's inbox before the run means anything:
  INV-0109 disputed, INV-0110 promises a date, INV-0111 an injected instruction.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from owed.seed import seed_invoice  # noqa: E402
from seed_stripe import describe  # noqa: E402

BASE = "stmallela.us01"
CLIENTS = {
    "A": ("Alder Studio", f"{BASE}+alder@gmail.com"),
    "B": ("Birch & Co", f"{BASE}+birch@gmail.com"),
    "C": ("Cedar Labs", f"{BASE}+cedar@gmail.com"),
    "D": ("Dune Press", f"{BASE}+dune@gmail.com"),
    "E": ("Elm Works", f"{BASE}+elm@gmail.com"),
}

# id, client, amount, days_overdue, last_chased_step, paid, partial, what the run should do
ROWS = [
    ("INV-0101", "A", 900.0, 5, 0, False, 0.0, "clean overdue, step 1"),
    ("INV-0102", "B", 1250.0, 12, 0, False, 0.0, "clean overdue, step 2"),
    ("INV-0103", "C", 600.0, 8, 0, False, 0.0, "clean overdue, step 1"),
    ("INV-0104", "D", 2300.0, 11, 0, False, 0.0, "clean overdue, step 2"),
    ("INV-0105", "A", 1100.0, 12, 2, False, 0.0, "already chased step 2 -> already_sent"),
    ("INV-0106", "E", 700.0, 6, 1, False, 0.0, "already chased step 1 -> already_sent"),
    ("INV-0107", "B", 400.0, 10, 1, True, 0.0, "paid after a chase -> receipt (closed)"),
    ("INV-0108", "C", 3000.0, 14, 0, False, 1200.0, "partial: $1,800 balance, step 2"),
    ("INV-0109", "D", 1500.0, 9, 0, False, 0.0, "disputed (client reply) -> disputes"),
    ("INV-0110", "E", 950.0, 13, 0, False, 0.0, "promises a date (client reply) -> promises_date"),
    ("INV-0111", "A", 2000.0, 16, 0, False, 0.0, "injected instruction (client reply) -> injection"),
    ("INV-0112", "B", 800.0, -1, 0, False, 0.0, "due tomorrow -> not in the plan"),
]


def main() -> None:
    for iid, c, amount, days, chased, paid, partial, what in ROWS:
        name, email = CLIENTS[c]
        r = seed_invoice(email, client_name=name, invoice_id=iid, amount=amount, days_overdue=days,
                         last_chased_step=chased, paid=paid, partial=partial)
        print(f"{what:48s} | {describe(r)}")


if __name__ == "__main__":
    main()
