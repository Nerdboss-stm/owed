"""Executor: the ONLY module that performs a live write. Everything else touches Shadow.

Every (invoice_id, step) group of intents is one unit of work keyed by idempotency_key. The key is
recorded as "pending" in state/sent.json BEFORE the first live call and set to "sent" after the
last one. A key already "sent" is never executed again; a key left "pending" (a crash mid-group)
is never retried automatically either: retries create duplicates, so it is reported for a human.

execute(plan, ledger, inbox, calendar, chat, state_dir, run_id) -> list[TraceLine]
post(chat, text) -> message ts         the Slack plan/result posts also go through here
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from owed.config import ET
from owed.contract import Calendar, Chat, Inbox, Intent, Ledger, Plan, TraceLine


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line(run_id: str, phase: str, invoice_id: Optional[str], decision: str, **data) -> TraceLine:
    return TraceLine(ts=_now(), run_id=run_id, phase=phase, invoice_id=invoice_id, decision=decision, data=data)  # type: ignore[arg-type]


# ---------- idempotency ledger ----------

def sent_path(state_dir: Path) -> Path:
    return Path(state_dir) / "sent.json"


def load_sent(state_dir: Path) -> dict:
    p = sent_path(state_dir)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_sent(state_dir: Path, sent: dict) -> None:
    p = sent_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sent, indent=2, default=str))


# ---------- writes ----------

def post(chat: Chat, text: str) -> str:
    """The one Slack write path."""
    return chat.post(text)


def _group(plan: Plan) -> list[tuple[str, list[Intent]]]:
    groups: dict[str, list[Intent]] = {}
    for it in plan.intents:
        groups.setdefault(it.idempotency_key, []).append(it)
    return list(groups.items())


def execute(plan: Plan, ledger: Ledger, inbox: Inbox, calendar: Optional[Calendar], chat: Chat,
            state_dir: Path, run_id: str) -> list[TraceLine]:
    state_dir = Path(state_dir)
    sent = load_sent(state_dir)
    lines: list[TraceLine] = []

    for key, intents in _group(plan):
        iid, step = intents[0].invoice_id, intents[0].step
        prior = sent.get(key)
        if prior and prior.get("status") == "sent":
            lines.append(_line(run_id, "execute", iid, f"SKIPPED {iid} step {step}: already sent in run {prior.get('run_id')} at {prior.get('ts')}",
                               reason="already_sent", key=key))
            continue
        if prior and prior.get("status") == "pending":
            lines.append(_line(run_id, "execute", iid, f"SKIPPED {iid} step {step}: key {key} is pending from run {prior.get('run_id')} "
                               f"(a write may have gone out); check the apps, then clear it by hand", reason="already_sent", key=key))
            continue

        # Record the key BEFORE any live call.
        sent[key] = {"status": "pending", "run_id": run_id, "ts": _now(), "kinds": [i.kind for i in intents]}
        _save_sent(state_dir, sent)

        results: dict = {}
        link_url: Optional[str] = None
        slot_text: Optional[str] = None
        for it in intents:
            if it.kind == "create_payment_link":
                link_url = ledger.create_payment_link(iid)
                results["payment_link"] = link_url
                lines.append(_line(run_id, "execute", iid, f"CREATED payment link for {iid} step {step}: {link_url}", key=key))
            elif it.kind == "create_event":
                if calendar is None:
                    raise RuntimeError(f"{key}: create_event intent but no calendar adapter")
                start = datetime.fromisoformat(it.payload["start_iso"])
                if start.tzinfo is None:
                    start = start.replace(tzinfo=ET)
                ev = calendar.create_event(it.payload["title"], start, it.payload["attendee"])
                slot_text = start.astimezone(ET).strftime("%A %B %-d, %-I:%M %p ET")
                results["event_id"] = ev
                lines.append(_line(run_id, "execute", iid, f"CREATED calendar event {ev} for {iid} step {step}: {slot_text}", key=key))
        for it in intents:
            if it.kind == "send_email" and it.payload.get("receipt"):
                p = it.payload
                mid = inbox.send(p["to"], p["subject"], p["body"], p.get("thread_id"))
                results["message_id"] = mid
                mark = getattr(ledger, "mark_receipted", None)
                if callable(mark):
                    mark(iid)
                lines.append(_line(run_id, "execute", iid,
                                   f"CLOSED {iid}: ${p.get('amount', 0):,.2f} received, receipt sent -> {p['to']} ({mid})",
                                   key=key, message_id=mid, amount=p.get("amount"), closed=True))
            elif it.kind == "send_email":
                p = it.payload
                body = p["body"].rstrip("\n")
                if link_url:
                    body += f"\n\nPay online: {link_url}"
                if slot_text:
                    body += f"\n\nProposed slot: {slot_text}. The calendar invite is on its way."
                body += "\n"
                mid = inbox.send(p["to"], p["subject"], body, p.get("thread_id"))
                results["message_id"] = mid
                ledger.mark_chased(iid, step)
                lines.append(_line(run_id, "execute", iid,
                                   f"SENT {iid} step {step} -> {p['to']} ({mid}), amount ${p.get('amount', 0):,.2f}, ledger marked chased step {step}",
                                   key=key, message_id=mid, amount=p.get("amount")))
            elif it.kind == "post_chat":
                results["chat_ts"] = post(chat, it.payload["text"])

        sent[key] = {"status": "sent", "run_id": run_id, "ts": _now(), "kinds": [i.kind for i in intents], "results": results}
        _save_sent(state_dir, sent)
    return lines
