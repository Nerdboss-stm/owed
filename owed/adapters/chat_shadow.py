"""Shadow chat: posts go to a list, the tap is a preset answer. stdlib only. Never imports a network library."""
from __future__ import annotations
from typing import Optional

from owed.adapters.shadow_base import ShadowWrites
from owed.contract import Chat, Intent


class ShadowChat(ShadowWrites, Chat):
    def __init__(self, posts: Optional[list[str]] = None, tap: bool = False,
                 intents: Optional[list[Intent]] = None):
        super().__init__(intents)
        self._posts: list[str] = list(posts or [])
        self._tap = tap  # scenarios set True to simulate the ✅; default False so step 3 never runs by accident

    # ---- reads ----
    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        return self._tap  # returns at once; a shadow never blocks

    def count_posts(self, contains: str) -> int:
        return sum(1 for p in self._posts if contains in p)

    # ---- write: records an Intent, posts nowhere ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        self._intent("post_chat", {"text": text})
        self._posts.append(text)
        return f"shadow-ts-{len(self._posts)}"
