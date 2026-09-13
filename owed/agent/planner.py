"""Planner: (days overdue, thread state, last chased) -> step or Refusal. Pure function, no I/O.

Thread state overrides days (SPEC step 3): says_paid -> verify ledger, chase nothing;
disputes -> escalate to the freelancer; promises_date -> wait; injection -> refuse.
Step thresholds come from the mandate (step_N.day); defaults are the SPEC values 3/10/21.
"""
from __future__ import annotations
from datetime import date
from typing import Optional, Union

from owed.contract import Invoice, Refusal, Step, ThreadState

DEFAULT_DAYS: dict[int, int] = {1: 3, 2: 10, 3: 21}


def step_days(mandate: Optional[dict]) -> dict[int, int]:
    out = dict(DEFAULT_DAYS)
    for s in (1, 2, 3):
        cfg = (mandate or {}).get(f"step_{s}")
        if isinstance(cfg, dict) and "day" in cfg:
            out[s] = int(cfg["day"])
    return out


def target_step(days_overdue: int, mandate: Optional[dict] = None) -> Step:
    """Highest step whose day threshold has been reached; 0 if none."""
    return max((s for s, d in step_days(mandate).items() if days_overdue >= d), default=0)  # type: ignore[return-value]


def decide(invoice: Invoice, thread_state: ThreadState, today: date,
           mandate: Optional[dict] = None) -> Union[Step, Refusal]:
    iid = invoice.invoice_id
    days = invoice.days_overdue(today)
    target = target_step(days, mandate)
    bal = f"${invoice.amount_due:,.2f}"

    if invoice.paid or invoice.amount_due <= 0:
        return Refusal(iid, target, "paid", f"ledger shows paid (balance {bal}); nothing to chase")
    if thread_state == "injection":
        return Refusal(iid, target, "injection",
                       "client email contains instruction-like text; it was classified, not followed")
    if thread_state == "says_paid":
        return Refusal(iid, target, "says_paid",
                       f"client says payment was sent; ledger still shows {bal} due. Verify, do not chase")
    if thread_state == "disputes":
        return Refusal(iid, target, "disputes",
                       "client disputes the invoice; escalate to the freelancer, no client email")
    if thread_state == "promises_date":
        return Refusal(iid, target, "promises_date", "client promised a payment date; wait for it")
    if thread_state == "asks_docs":
        return 0  # the freelancer answers a document request; the agent does not chase on top of it
    if target == 0:
        return 0
    if target <= invoice.last_chased_step:
        nxt = next((s for s in (1, 2, 3) if s > invoice.last_chased_step), None)
        when = f"step {nxt} at day {step_days(mandate)[nxt]}" if nxt else "no further steps in the mandate"
        return Refusal(iid, target, "already_sent",
                       f"step {invoice.last_chased_step} already sent (day {days}); {when}")
    return target
