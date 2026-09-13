"""Verifier: a second model role that checks every draft against the mandate.

deterministic_checks(draft, invoice, mandate) -> list[Refusal]   pure: amount unchanged, no discount,
                                                                 no new deadline, no threats/apologies
verify(draft, invoice, mandate) -> list[Refusal]                 deterministic first; if clean, one
                                                                 verifier model call (skipped when offline)

The verifier is not the actor. Different system prompt, and it never sees the actor's prompt or the
voice samples: it sees the mandate rules, the ledger balance, and the draft. Any fail -> Refusal.
"""
from __future__ import annotations
import json
import re

from owed.contract import Invoice, Refusal

MODEL = "claude-opus-5"
_CODES = ("discount", "amount", "deadline", "tone")
_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"code": {"type": "string", "enum": list(_CODES)}, "detail": {"type": "string"}},
                "required": ["code", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pass", "reasons"],
    "additionalProperties": False,
}

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


def verifier_system(mandate: dict) -> str:
    tone = ", ".join(mandate.get("tone", []))
    never = "; ".join(mandate.get("never", []))
    return (
        "You are a strict reviewer of invoice follow-up emails written on behalf of a freelancer. You did not "
        "write the email and you do not rewrite it. You decide whether it may be sent under this mandate:\n"
        f"- Tone: {tone}.\n- Never: {never}.\n"
        "- The only dollar figure allowed is the ledger balance given to you. It may appear in both the subject "
        "and the body; any other figure, or any arithmetic on it, is a fail (code amount).\n"
        "- No mention of consequences, legal action, collections, or deadlines the freelancer would set.\n"
        "- No apology for asking. No sarcasm, no guilt, no pressure phrasing.\n"
        "Return JSON: pass (true only if every rule holds) and reasons, a list of {code, detail} where code is "
        "one of discount, amount, deadline, tone. Quote the offending words in detail. An empty reasons list "
        "means pass."
    )


def model_verify(draft: dict, invoice: Invoice, mandate: dict) -> list[Refusal]:
    """The verifier model call. Never sees the actor prompt. Raises on refusal or malformed output."""
    import anthropic  # here so the module imports without the SDK

    from owed.config import env
    iid, step = invoice.invoice_id, int(draft.get("step", 0))
    payload = {
        "ledger_balance": f"${invoice.amount_due:,.2f}",
        "step": step,
        "subject": draft.get("subject", ""),
        "body": draft.get("body", ""),
    }
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    r = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=verifier_system(mandate),
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": "Review this draft:\n" + json.dumps(payload, indent=2)}],
    )
    if r.stop_reason == "refusal":
        raise RuntimeError(f"verifier refused to review {iid} step {step}: {r.stop_details}")
    data = json.loads(next(b.text for b in r.content if b.type == "text"))
    reasons = data.get("reasons") or []
    if data.get("pass") and not reasons:
        return []
    if not reasons:  # pass=false with no reason: fail closed, but say so
        reasons = [{"code": "tone", "detail": "verifier failed the draft without a stated reason"}]
    return [Refusal(iid, step, f"verifier_{x['code']}", f"verifier: {x['detail']}") for x in reasons]  # type: ignore[arg-type]


def verify(draft: dict, invoice: Invoice, mandate: dict) -> list[Refusal]:
    """Deterministic checks first; only a clean draft reaches the verifier model. Offline: deterministic only."""
    from owed.config import offline
    refusals = deterministic_checks(draft, invoice, mandate)
    if refusals or offline():
        return refusals
    return model_verify(draft, invoice, mandate)
