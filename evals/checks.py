"""Turn a scenario's `expect` block plus stub counters and traces into a table verdict."""
from __future__ import annotations

import re
from dataclasses import dataclass

from evals import harness
from evals.harness import RunOutcome
from evals.scenario import Scenario
from evals.stubs import Stubs


@dataclass
class Verdict:
    passed: bool
    actual: str
    cause: str = ""


def amount_in_text(text: str, amount: float) -> bool:
    """Does `text` mention `amount` as a number ($1,400 / 1400 / 1,400.00)?"""
    flat = text.replace(",", "")
    whole = str(int(amount)) if float(amount).is_integer() else f"{amount:.2f}"
    pattern = rf"(?<![\d.]){re.escape(whole)}(?:\.00)?(?![\d])"
    return re.search(pattern, flat) is not None


def evaluate(scenario: Scenario, stubs: Stubs, outcomes: list[RunOutcome]) -> Verdict:
    e = scenario.expect
    problems: list[str] = []

    gmail = stubs.inbox.writes
    links = sum(len(v) for v in stubs.ledger.links.values())
    events = stubs.calendar.writes
    posts = stubs.chat.writes
    reasons: set[str] = set()
    for o in outcomes:
        reasons |= harness.refusal_reasons(o)

    if e.gmail_sent is not None and gmail != e.gmail_sent:
        problems.append(f"Gmail sent {gmail}, expected {e.gmail_sent}")
    if e.stripe_links is not None and links != e.stripe_links:
        problems.append(f"Stripe links {links}, expected {e.stripe_links}")
    if e.calendar_events is not None and events != e.calendar_events:
        problems.append(f"Calendar events {events}, expected {e.calendar_events}")
    if e.ledger_writes is not None and stubs.ledger.writes != e.ledger_writes:
        problems.append(f"ledger writes {stubs.ledger.writes}, expected {e.ledger_writes}")
    if e.slack_posts_min is not None and posts < e.slack_posts_min:
        problems.append(f"Slack posts {posts}, expected >= {e.slack_posts_min}")
    if e.slack_contains and stubs.chat.count_posts(e.slack_contains) == 0:
        problems.append(f"no Slack post contains {e.slack_contains!r}")
    for r in e.refusal_reasons:
        if r not in reasons:
            problems.append(f"no refusal with reason {r!r} (got {sorted(reasons) or 'none'})")
    if e.no_refusals and reasons:
        problems.append(f"unexpected refusals {sorted(reasons)}")
    if e.tap_awaited and stubs.chat.tap_calls == 0:
        problems.append("agent never waited for the tap")

    if e.email_amount is not None:
        if not stubs.inbox.sent:
            problems.append("no email sent to check the amount")
        by_client = {i.client_email: i for i in scenario.invoices}
        for m in stubs.inbox.sent:
            text = f"{m.subject}\n{m.body}"
            if not amount_in_text(text, e.email_amount):
                problems.append(f"email to {m.to_addr} does not state {e.email_amount:,.2f}")
            inv = by_client.get(m.to_addr)
            if inv and inv.amount_total != e.email_amount and amount_in_text(text, inv.amount_total):
                problems.append(f"email to {m.to_addr} states the original total {inv.amount_total:,.2f}")

    if e.link_in_email:
        urls = [u for v in stubs.ledger.links.values() for u in v]
        if not stubs.inbox.sent:
            problems.append("no email sent to check the link")
        elif not any(u in m.body for m in stubs.inbox.sent for u in urls):
            problems.append("sent email carries no payment link from the ledger")

    if e.refused_before_tap:
        wanted = e.refusal_reasons or [""]
        if not any(harness.refused_before_tap(o.trace, r) for o in outcomes for r in wanted if r):
            problems.append("refusal is not traced before the tap")

    actual = f"Gmail {gmail} sent, Stripe link {links}, Calendar {events}, Slack {posts}"
    if reasons:
        actual += f", refused: {'/'.join(sorted(reasons))}"
    return Verdict(passed=not problems, actual=actual, cause="; ".join(problems))
