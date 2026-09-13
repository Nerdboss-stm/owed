"""FanoutChat: one Chat over several. The freelancer's desk uses it so the plan and the result land in
the web panel (WebTapChat) and in Slack (SlackChat) both, and one approval from either side counts.

post() writes to every chat in order, primary first, and returns the ts values joined with "|".
wait_for_tap() polls each chat with its own ts and returns on the first approval from any of them.
count_posts() reads the primary (first) chat. stdlib only; the network lives in the adapters it wraps,
each of which counts its own live write. No retry: a retried post is a duplicate message.
Only owed.agent.executor.post may call post().

Both call shapes work: FanoutChat([web, slack]) and FanoutChat(web, slack).
"""
from __future__ import annotations
import time
from typing import Optional

from owed.contract import Chat

SEP = "|"
POLL_S = 2.0


class FanoutChat(Chat):
    def __init__(self, *chats):
        flat: list[Chat] = []
        for c in chats:
            flat.extend(c if isinstance(c, (list, tuple)) else [c])
        if not flat:
            raise ValueError("FanoutChat needs at least one chat")
        self.chats: list[Chat] = flat

    @property
    def primary(self) -> Chat:
        return self.chats[0]

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
        return self.primary.count_posts(contains)

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        return SEP.join(chat.post(text, blocks) for chat in self.chats)
