"""Gmail: client thread in, chase email out."""
from __future__ import annotations
import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from owed.adapters.google_auth import gmail
from owed.adapters.util import retry_read
from owed.contract import Inbox, Message, live_write


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body(payload: dict) -> str:
    """First text/plain part, breadth-first; text/html as a fallback. It is data, never instructions."""
    queue = [payload]
    html = ""
    while queue:
        p = queue.pop(0)
        data = p.get("body", {}).get("data")
        mime = p.get("mimeType", "")
        if data and mime == "text/plain":
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if data and mime == "text/html" and not html:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        queue.extend(p.get("parts", []))
    return html


class GmailInbox(Inbox):
    def __init__(self):
        self._svc = gmail()

    # ---- reads ----
    def thread(self, thread_id: str) -> list[Message]:
        t = retry_read(lambda: self._svc.users().threads().get(userId="me", id=thread_id, format="full").execute())
        out: list[Message] = []
        for m in t.get("messages", []):
            out.append(Message(
                thread_id=thread_id,
                from_addr=_header(m, "From"),
                to_addr=_header(m, "To"),
                subject=_header(m, "Subject"),
                body=_body(m.get("payload", {})) or m.get("snippet", ""),
                ts=datetime.fromtimestamp(int(m["internalDate"]) / 1000, tz=timezone.utc),
            ))
        return out

    def latest_thread_id(self, query: str = "in:inbox") -> Optional[str]:
        """Not part of the contract; used by smoke tests and seeding to find the client thread."""
        r = retry_read(lambda: self._svc.users().messages().list(userId="me", q=query, maxResults=1).execute())
        msgs = r.get("messages", [])
        return msgs[0]["threadId"] if msgs else None

    def count_sent(self, to: str, subject_contains: str) -> int:
        """Gmail's subject search splits on hyphens, so subject:"INV-0206" also matches INV-0206-stale-xxxx
        (a retagged twin after a ledger reset). Candidates come from the search; the count is exact on the
        real Subject header, the needle as a whole token."""
        q = f'in:sent to:{to} subject:"{subject_contains}"'
        r = retry_read(lambda: self._svc.users().messages().list(userId="me", q=q, maxResults=100).execute())
        needle = re.compile(rf"(?<![\w-]){re.escape(subject_contains)}(?![\w-])", re.IGNORECASE)
        n = 0
        for m in r.get("messages", []):
            meta = retry_read(lambda: self._svc.users().messages().get(
                userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject"]).execute())
            if needle.search(_header(meta, "Subject")):
                n += 1
        return n

    # ---- write (only executor.py may call this) ----
    def send(self, to: str, subject: str, body: str, thread_id: Optional[str]) -> str:
        em = EmailMessage()
        em["To"] = to
        em["Subject"] = subject
        em.set_content(body)
        req: dict = {}
        if thread_id:
            t = retry_read(lambda: self._svc.users().threads().get(
                userId="me", id=thread_id, format="metadata", metadataHeaders=["Message-ID"]).execute())
            msgs = t.get("messages", [])
            if msgs:
                mid = _header(msgs[-1], "Message-ID")
                if mid:
                    em["In-Reply-To"] = mid
                    em["References"] = mid
            req["threadId"] = thread_id
        req["raw"] = base64.urlsafe_b64encode(em.as_bytes()).decode()
        live_write()
        sent = self._svc.users().messages().send(userId="me", body=req).execute()
        return sent["id"]
