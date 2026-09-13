"""Shared plumbing for Shadow adapters. stdlib only. Never imports a network library.

A Shadow adapter implements the same ABC as its Live twin, but every write becomes an
Intent appended to a shared `intents` list and a mutation of in-memory state. Nothing
leaves the process. The rehearsal loop calls `set_context(invoice_id, step, amount=...)`
before drafting an invoice so the Intent produced by a write is tagged correctly.
"""
from __future__ import annotations
from typing import Optional

from owed.contract import Intent, Step


class ShadowWrites:
    def __init__(self, intents: Optional[list[Intent]] = None):
        self.intents: list[Intent] = intents if intents is not None else []
        self.writes: list[dict] = []          # every write call, including ones with no Intent kind
        self._invoice_id: str = ""
        self._step: Step = 0
        self._extra: dict = {}

    def set_context(self, invoice_id: str, step: Step, **extra) -> None:
        self._invoice_id, self._step, self._extra = invoice_id, step, dict(extra)

    def _intent(self, kind: str, payload: dict, invoice_id: Optional[str] = None) -> Intent:
        it = Intent(
            kind=kind,  # type: ignore[arg-type]
            invoice_id=invoice_id or self._invoice_id,
            step=self._step,
            payload={**self._extra, **payload},
        )
        self.intents.append(it)
        self.writes.append({"op": kind, "invoice_id": it.invoice_id, "step": it.step})
        return it
