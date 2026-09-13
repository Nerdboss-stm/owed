"""Slack: rehearsal report out, ✅ reaction in."""
from __future__ import annotations
import time
from typing import Optional

from slack_sdk import WebClient

from owed.adapters.util import retry_read
from owed.config import env
from owed.contract import Chat, live_write


class SlackChat(Chat):
    def __init__(self, token: str | None = None, channel: str | None = None):
        self._c = WebClient(token=token or env("SLACK_BOT_TOKEN"))
        self._channel = channel or env("SLACK_CHANNEL")
        self._me = retry_read(lambda: self._c.auth_test())["user_id"]

    # ---- reads ----
    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        """Polls reactions on the plan message. Only a human's reaction counts; the bot cannot approve itself."""
        deadline = time.monotonic() + timeout_s
        while True:
            r = retry_read(lambda: self._c.reactions_get(channel=self._channel, timestamp=message_ts))
            for rx in r.get("message", {}).get("reactions", []):
                if rx["name"] == emoji and any(u != self._me for u in rx.get("users", [])):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(3)

    def count_posts(self, contains: str) -> int:
        r = retry_read(lambda: self._c.conversations_history(channel=self._channel, limit=200))
        return sum(
            1 for m in r.get("messages", [])
            if contains in (m.get("text") or "") and (m.get("user") == self._me or m.get("bot_id"))
        )

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        live_write()
        r = self._c.chat_postMessage(channel=self._channel, text=text, blocks=blocks)
        return r["ts"]
