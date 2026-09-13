"""FanoutChat: one Chat that posts to several. The freelancer's desk uses it so the plan and the result
land in the web panel (WebTapChat, the tap and the assertion count) and in Slack #owed (SlackChat) both.

Reads (wait_for_tap, count_posts) come from the primary only. post() writes to every chat in order,
primary first; each underlying adapter counts its own live write. No retry: a retried post is a
duplicate message. stdlib only; the network lives in the adapters it wraps.
"""
from __future__ import annotations
from typing import Optional

from owed.contract import Chat


class FanoutChat(Chat):
    def __init__(self, primary: Chat, *others: Chat):
        self._primary = primary
        self._others = list(others)

    # ---- reads ----
    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        return self._primary.wait_for_tap(message_ts, emoji=emoji, timeout_s=timeout_s)

    def count_posts(self, contains: str) -> int:
        return self._primary.count_posts(contains)

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        ts = self._primary.post(text, blocks)
        for chat in self._others:
            chat.post(text, blocks)
        return ts
