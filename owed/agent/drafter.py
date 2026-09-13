"""Drafter, the ACTOR. Writes the chase email from the mandate, the voice samples and invoice facts.

Rules it is built around:
- It never sees client email bodies (untrusted data). It gets facts from the ledger only.
- It never computes or alters the amount. The number comes from the ledger and is copied into the
  output by code, not by the model.
- It never creates a payment link or a calendar event. The executor appends the real link / slot.
- Actor = Astra when ASTRA_API_KEY is set (not today); otherwise Claude in the actor role.
  The verifier is a different model call with a different system prompt and never sees this one.

draft(invoice, step, mandate) -> {"subject", "body", "amount", "step"}
"""
from __future__ import annotations
import json

from owed.config import ROOT
from owed.contract import Invoice, Step

MODEL = "claude-opus-5"
VOICE_PATH = ROOT / "owed" / "mandate" / "voice_samples.md"

STEP_BRIEF = {
    1: "Step 1, soft nudge. The invoice is a few days late. Assume it slipped. One light ask: could they let "
       "you know when it is scheduled. Two or three short sentences.",
    2: "Step 2, direct ask. The invoice is well past due. Say plainly that it is open, state the balance once, "
       "and ask them to settle it or tell you when to expect it. Mention that a payment link is below, in one "
       "sentence, and do not invent a URL. The sending system adds the real link under your sign-off.",
    3: "Step 3, propose a call. The invoice is three weeks past due. Stay calm. Say the balance once, propose a "
       "15-minute call to sort it out, and say a calendar invite follows. Do not invent a date or time.",
}

_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def actor_system(mandate: dict) -> str:
    voice = VOICE_PATH.read_text() if VOICE_PATH.exists() else ""
    tone = ", ".join(mandate.get("tone", []))
    never = "; ".join(mandate.get("never", []))
    return (
        f"You draft invoice follow-up emails on behalf of {mandate.get('signoff', 'the freelancer')}, a freelance "
        "designer, to a client. You write as them, in their voice, matching the samples below.\n\n"
        f"Tone: {tone}.\nNever: {never}. Never mention lawyers, collections, legal action, or consequences. "
        "Never apologise for asking. Never propose or imply a new due date.\n"
        "State the balance at most once, using exactly the amount string you are given. Do not write any other "
        "dollar figure. The subject line must contain the invoice number.\n"
        "Return JSON with two fields, subject and body. The body is plain text, ends with the sign-off on its own "
        "line, and contains no placeholders in brackets.\n\n"
        f"{voice}"
    )


def template_draft(invoice: Invoice, step: Step, mandate: dict) -> dict:
    """Offline stand-in for the actor: a fixed, mandate-compliant draft with no model call.
    Same contract as draft(); the amount is still the ledger's number."""
    amt = f"${invoice.amount_due:,.2f}"
    due = f"{invoice.due_date.strftime('%B')} {invoice.due_date.day}"
    first = (invoice.client_name.split() or ["there"])[0]
    iid, sign = invoice.invoice_id, mandate.get("signoff", "")
    if step == 1:
        subject = f"Invoice {iid}"
        body = (f"Hi {first},\n\nA quick note that invoice {iid} for {amt} was due on {due}. "
                f"Could you let me know when it is scheduled?\n\nThanks,\n{sign}\n")
    elif step == 2:
        subject = f"Invoice {iid} - {amt} outstanding"
        body = (f"Hi {first},\n\nInvoice {iid} for {amt} was due on {due} and is still open. "
                f"Could you settle it, or let me know when I can expect it? A payment link is below.\n\nThanks,\n{sign}\n")
    elif step == 3:
        subject = f"Invoice {iid} - a quick call?"
        body = (f"Hi {first},\n\nInvoice {iid} for {amt} has been open since {due}. "
                f"Would a 15-minute call help sort it out? I will send a calendar invite for a slot that works.\n\nThanks,\n{sign}\n")
    else:
        raise ValueError(f"no draft for step {step}")
    return {"subject": subject, "body": body, "amount": invoice.amount_due, "step": step}


def receipt_draft(invoice: Invoice, mandate: dict) -> dict:
    """Close the loop: a short thank-you once a chased invoice is paid. Deterministic, no model call.
    The figure is the invoice total (what was received), from the ledger."""
    amt = f"${invoice.amount_total:,.2f}"
    first = (invoice.client_name.split() or ["there"])[0]
    iid, sign = invoice.invoice_id, mandate.get("signoff", "")
    return {
        "subject": f"Received: invoice {iid}",
        "body": f"Hi {first},\n\nPayment of {amt} for invoice {iid} has come through. Thank you, all settled.\n\n{sign}\n",
        "amount": invoice.amount_total,
        "step": 0,
        "receipt": True,
    }


def _facts(invoice: Invoice, step: Step, days: int) -> dict:
    first = (invoice.client_name.split() or ["there"])[0]
    return {
        "invoice_id": invoice.invoice_id,
        "client_first_name": first,
        "amount_string": f"${invoice.amount_due:,.2f}",
        "partial_payment_received": invoice.amount_due < invoice.amount_total,
        "due_date": invoice.due_date.strftime("%B %-d"),
        "days_overdue": days,
        "previous_reminders_sent": invoice.last_chased_step,
        "step": step,
        "brief": STEP_BRIEF[step],
    }


def draft(invoice: Invoice, step: Step, mandate: dict, today=None) -> dict:
    """The actor model call. Raises on refusal or malformed output; nothing is sent from here."""
    import anthropic  # here so the package imports without the SDK

    from owed.config import env, today as today_fn
    days = invoice.days_overdue(today or today_fn())
    if step not in STEP_BRIEF:
        raise ValueError(f"no draft for step {step}")
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    r = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=actor_system(mandate),
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": "Write the email for these facts:\n" + json.dumps(_facts(invoice, step, days), indent=2)}],
    )
    if r.stop_reason == "refusal":
        raise RuntimeError(f"actor refused to draft {invoice.invoice_id} step {step}: {r.stop_details}")
    text = next(b.text for b in r.content if b.type == "text")
    data = json.loads(text)
    subject, body = str(data["subject"]).strip(), str(data["body"]).strip() + "\n"
    if invoice.invoice_id not in subject:
        subject = f"Invoice {invoice.invoice_id}: {subject}"
    # The amount is the ledger's number, never the model's.
    return {"subject": subject, "body": body, "amount": invoice.amount_due, "step": step}
