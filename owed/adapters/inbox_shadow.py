"""Shadow inbox: in-memory copy of the client threads. stdlib only. Never imports a network library."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from owed.adapters.shadow_base import ShadowWrites
from owed.contract import Inbox, Intent, Message


class ShadowInbox(ShadowWrites, Inbox):
    def __init__(self, threads: dict[str, list[Message]], intents: Optional[list[Intent]] = None,
                 me: str = "me"):
        super().__init__(intents)
        self._threads: dict[str, list[Message]] = {k: list(v) for k, v in threads.items()}
        self._sent: list[Message] = []
        self._me = me

    # ---- reads ----
    def thread(self, thread_id: str) -> list[Message]:
        if thread_id not in self._threads:
            raise KeyError(f"thread {thread_id} not in shadow inbox")
        return list(self._threads[thread_id])

    def count_sent(self, to: str, subject_contains: str) -> int:
        return sum(1 for m in self._sent if m.to_addr == to and subject_contains in m.subject)

    # ---- write: records an Intent and a shadow message, sends nothing ----
    def send(self, to: str, subject: str, body: str, thread_id: Optional[str]) -> str:
        self._intent("send_email", {"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        tid = thread_id or f"shadow-thread-{len(self._sent) + 1}"
        msg = Message(thread_id=tid, from_addr=self._me, to_addr=to, subject=subject, body=body,
                      ts=datetime.now(timezone.utc))
        self._sent.append(msg)
        self._threads.setdefault(tid, []).append(msg)
        return f"shadow-msg-{len(self._sent)}"
