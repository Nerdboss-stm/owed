"""Reset the Stripe test ledger to a fresh demo morning. Test-data setup, not agent behaviour.

  python scripts/reset_demo_ledger.py [--dry-run]

1. Void every OPEN invoice except the keepers (INV-0044, the injection thread). Paid invoices are never
   touched, so INV-0042 (paid, receipted) stays for the receipt trace.
2. Retag any earlier copy of the fresh ids (any status) as stale so the ids stay unique.
3. Seed the morning: two step-1, two step-2 (one on a partial balance), one step-3, one receipt due.
4. Drop state/sent.json keys for every voided or retagged invoice so the fresh ids start clean.

Expected rehearsal afterwards: would send 6, refused 1 (INV-0044, injection).
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stripe  # noqa: E402

from owed.adapters.ledger_live import _meta  # noqa: E402
from owed.config import ROOT, env  # noqa: E402
from owed.seed import seed_invoice  # noqa: E402

KEEP_OPEN = {"INV-0044"}
BASE = "stmallela.us01"
FRESH = [
    # invoice_id, client_name, client_email, amount, days_overdue, last_chased_step, paid, partial, expect
    ("INV-0201", "Alder Studio", f"{BASE}+alder@gmail.com", 900.0, 4, 0, False, 0.0, "step 1"),
    ("INV-0202", "Birch & Co", f"{BASE}+birch@gmail.com", 1250.0, 5, 0, False, 0.0, "step 1"),
    ("INV-0203", "Dune Press", f"{BASE}+dune@gmail.com", 2300.0, 12, 0, False, 0.0, "step 2 + link"),
    ("INV-0204", "Cedar Labs", f"{BASE}+cedar@gmail.com", 3000.0, 13, 0, False, 1200.0, "step 2 + link, balance $1,800"),
    ("INV-0205", "Elm Works", f"{BASE}+elm@gmail.com", 4200.0, 22, 0, False, 0.0, "step 3, requires tap"),
    ("INV-0206", "Marlow & Finch Studio", f"{BASE}@gmail.com", 400.0, 9, 1, True, 0.0, "paid after step 1, receipt due"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list what would be voided, change nothing")
    a = ap.parse_args()
    stripe.api_key = env("STRIPE_TEST_KEY")
    fresh_ids = {row[0] for row in FRESH}
    touched: set[str] = set()

    # 1. void open invoices
    for inv in stripe.Invoice.list(limit=100, status="open").auto_paging_iter():
        iid = _meta(inv.metadata).get("invoice_id") or inv.number or inv.id
        if iid in KEEP_OPEN:
            print(f"keep    {iid} (open, {inv.amount_remaining/100:,.2f} due)")
            continue
        print(f"void    {iid} = {inv.id} ${inv.amount_remaining/100:,.2f} due" + ("  [dry run]" if a.dry_run else ""))
        if not a.dry_run:
            stripe.Invoice.void_invoice(inv.id)
        touched.add(iid)

    # 2. retag earlier copies of the fresh ids (any status) so each id is unique again
    for inv in stripe.Invoice.list(limit=100).auto_paging_iter():
        iid = _meta(inv.metadata).get("invoice_id") or ""
        if iid in fresh_ids:
            print(f"retag   {iid} = {inv.id} status={inv.status} -> {iid}-stale-{inv.id[-6:]}" + ("  [dry run]" if a.dry_run else ""))
            if not a.dry_run:
                stripe.Invoice.modify(inv.id, metadata={"invoice_id": f"{iid}-stale-{inv.id[-6:]}"})
            touched.add(iid)

    # 3. seed the fresh morning
    if not a.dry_run:
        for iid, name, email, amount, days, chased, paid, partial, expect in FRESH:
            r = seed_invoice(email, client_name=name, invoice_id=iid, amount=amount, days_overdue=days,
                             last_chased_step=chased, paid=paid, partial=partial)
            state = "paid" if r["status"] == "paid" else f"${r['amount_due']:,.2f} of ${r['amount_total']:,.2f}"
            print(f"seed    {iid} {name:22s} {state:22s} day {days:>2}  chased={chased}  -> {expect}")

    # 4. prune idempotency keys for everything voided or retagged
    sent_path = ROOT / "state" / "sent.json"
    if sent_path.exists() and not a.dry_run:
        sent = json.loads(sent_path.read_text())
        drop = [k for k in sent if k.split(":")[0] in touched]
        for k in drop:
            del sent[k]
        sent_path.write_text(json.dumps(sent, indent=2, default=str))
        print(f"sent.json: dropped {len(drop)} key(s) for {sorted(touched)}; kept {sorted(sent)}")
    print("done" + (" (dry run, nothing changed)" if a.dry_run else ""))


if __name__ == "__main__":
    main()
