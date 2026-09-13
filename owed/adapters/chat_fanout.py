"""FanoutChat: one Chat over several. post() posts to every chat; wait_for_tap() returns as soon as any
one of them is tapped; count_posts() reads the first (primary) chat. stdlib only; the network is
whatever the wrapped adapters do, so a Slack Live plus a WebTapChat gives the freelancer's channel and
the client's web panel the same plan and one approval from either side.

Only owed.agent.executor.post may call post(). A failed post on any chat raises: never retried.
"""
from __future__ import annotations
import time
from typing import Optional

from owed.contract import Chat

SEP = "|"
POLL_S = 2.0


class FanoutChat(Chat):
    def __init__(self, chats: list[Chat]):
        if not chats:
            raise ValueError("FanoutChat needs at least one chat")
        self.chats = list(chats)

    # ---- reads ----
    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        """message_ts is the joined ts from post(); each chat is polled with its own ts, no waiting inside."""
        parts = message_ts.split(SEP)
        if len(parts) != len(self.chats):
            parts = [message_ts] * len(self.chats)
        deadline = time.monotonic() + timeout_s
        while True:
            for chat, ts in zip(self.chats, parts):
                if chat.wait_for_tap(ts, emoji=emoji, timeout_s=0):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_S)

    def count_posts(self, contains: str) -> int:
        return self.chats[0].count_posts(contains)

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        return SEP.join(chat.post(text, blocks) for chat in self.chats)
