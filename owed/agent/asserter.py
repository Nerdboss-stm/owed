"""Asserter: after execute, read the end state back from each app and compare with the plan.
Exactly one sent email per (invoice, step), exactly one payment link, exactly one event, exactly one
Slack plan post for the run. Reads only. Raises AssertionError if any count differs.

assert_end_state(plan, ledger, inbox, calendar, chat) -> list[EndState]
"""
from __future__ import annotations
from typing import Optional

from owed.contract import Calendar, Chat, EndState, Inbox, Ledger, Plan


def plan_post_marker(run_id: str) -> str:
    return f"OWED plan {run_id}"


def assert_end_state(plan: Plan, ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar], chat: Chat) -> list[EndState]:
    states: list[EndState] = []
    for it in plan.intents:
        iid = it.invoice_id
        if it.kind == "send_email":
            to = it.payload["to"]
            needle = it.payload["subject"] if it.payload.get("receipt") else iid  # a receipt follows an earlier chase
            states.append(EndState("gmail", 1, inbox.count_sent(to, needle), f"sent to {to} with {needle!r} in subject"))
        elif it.kind == "create_payment_link":
            states.append(EndState("stripe", 1, ledger.count_links(iid), f"active payment links for {iid}"))
        elif it.kind == "create_event":
            if calendar is None:
                raise AssertionError(f"plan has a create_event for {iid} but there is no calendar adapter")
            title = f"OWED call {iid}"
            states.append(EndState("calendar", 1, calendar.count_events(title), f"events titled {title!r}"))
    states.append(EndState("slack", 1, chat.count_posts(plan_post_marker(plan.run_id)), "plan post for this run"))

    bad = [s for s in states if not s.ok]
    if bad:
        raise AssertionError("end state mismatch: " + "; ".join(f"{s.app} expected {s.expected} got {s.actual} ({s.detail})" for s in bad))
    return states
