"""Verifier, deterministic half: amount unchanged, no discount, no new deadline, no threats/apologies.
Pure functions. The verifier MODEL (a different role from the actor) is added at PLAN 3:15.

deterministic_checks(draft, invoice, mandate) -> list[Refusal]   (empty list = pass)
"""
from __future__ import annotations
import re

from owed.contract import Invoice, Refusal

_DISCOUNT = [
    r"\bdiscount", r"\d+\s?% off\b", r"\bpercent off\b", r"\bknock (?:off|down)\b", r"\bwaive\b",
    r"\bwrite (?:it |this )?off\b", r"\bsettle for less\b", r"\breduced? (?:the |this )?(?:amount|price|invoice|fee)\b",
    r"\bround(?:ing)? (?:it )?down\b", r"\bhalf\b[^.\n]{0,20}\b(?:amount|invoice)\b",
]
_DEADLINE = [
    r"\bextension\b", r"\bextend (?:the |your )?(?:deadline|due date|terms)\b", r"\bno rush\b",
    r"\btake your time\b", r"\bwhenever (?:you|it|works)\b", r"\bnew (?:due date|deadline)\b",
    r"\bpush (?:the |it )?(?:back|out)\b", r"\bmore time\b",
]
_TONE = [
    r"\blegal action\b", r"\blawyer\b", r"\battorney\b", r"\bcollections?\b(?! of)", r"\bcourt\b", r"\bsue\b",
    r"\bfinal (?:warning|notice)\b", r"\bunacceptable\b", r"\bfrankly\b", r"\bridiculous\b",
    r"\bsorry\b", r"\bapologi[sz]e", r"\bapologies\b", r"\bhate to (?:bother|ask)\b",
]
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")


def _amounts(text: str) -> list[float]:
    out = []
    for whole, cents in _MONEY.findall(text):
        out.append(float(whole.replace(",", "")) + (float(cents) / 100 if cents else 0.0))
    return out


def _hits(patterns: list[str], text: str) -> list[str]:
    return [m.group(0) for p in patterns for m in [re.search(p, text, flags=re.IGNORECASE)] if m]


def deterministic_checks(draft: dict, invoice: Invoice, mandate: dict) -> list[Refusal]:
    iid, step = invoice.invoice_id, int(draft.get("step", 0))
    text = f"{draft.get('subject', '')}\n{draft.get('body', '')}"
    out: list[Refusal] = []

    amt = draft.get("amount")
    if amt is None or abs(float(amt) - invoice.amount_due) > 0.005:
        out.append(Refusal(iid, step, "verifier_amount",
                           f"draft amount {amt} != ledger balance ${invoice.amount_due:,.2f}"))
    wrong = [a for a in _amounts(text) if abs(a - invoice.amount_due) > 0.005]
    if wrong:
        out.append(Refusal(iid, step, "verifier_amount",
                           f"email states ${wrong[0]:,.2f}; ledger balance is ${invoice.amount_due:,.2f}"))
    if h := _hits(_DISCOUNT, text):
        out.append(Refusal(iid, step, "verifier_discount", f"draft offers a discount ({h[0]!r}); mandate: never"))
    if h := _hits(_DEADLINE, text):
        out.append(Refusal(iid, step, "verifier_deadline", f"draft promises more time ({h[0]!r}); mandate: never"))
    if h := _hits(_TONE, text):
        out.append(Refusal(iid, step, "verifier_tone", f"draft breaks tone ({h[0]!r}); mandate: never angry, apologetic, threatening"))
    return out
