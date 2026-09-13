"""WebTapChat: a Chat adapter whose tap is a file. Used by Be the client (ui/client_server.py). stdlib only.

Per client, under state/clients/<email>/:
  posts.jsonl    one line per post(): {"ts", "at", "text", "delivered"}. The status panel reads this.
  approve.json   written by POST /client/approve: {"run_id": ..., "email": ..., "approved_at": ...}.
                 wait_for_tap() returns True only when the flag names THIS run (or this plan's message ts),
                 then consumes it (renamed to approve.used.json) so one flag approves exactly one plan.

post() forwards the text to the client's Slack incoming webhook when one was given; that webhook call
is the only network write here and it counts as a live write, never retried. Without a webhook the post
is a file write for the panel. The freelancer's own channel keeps adapters/chat_live.py (reactions).
Only owed.agent.executor.post may call post().
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from owed.contract import Chat, live_write

WEBHOOK_PREFIX = "https://hooks.slack.com/"
TIMEOUT_S = 10
POLL_S = 1.0


class WebTapChat(Chat):
    def __init__(self, posts_path: Path, approval_path: Path, run_id: str, webhook_url: Optional[str] = None):
        if webhook_url and not webhook_url.startswith(WEBHOOK_PREFIX):
            raise ValueError(f"slack webhook must start with {WEBHOOK_PREFIX}")
        self._posts = Path(posts_path)
        self._approval = Path(approval_path)
        self._run_id = run_id
        self._webhook = webhook_url or None

    @classmethod
    def for_client(cls, client_dir: Path, run_id: str, webhook_url: Optional[str] = None) -> "WebTapChat":
        d = Path(client_dir)
        return cls(d / "posts.jsonl", d / "approve.json", run_id, webhook_url)

    # ---- reads ----
    def _flag(self) -> Optional[dict]:
        if not self._approval.exists():
            return None
        try:
            data = json.loads(self._approval.read_text() or "{}")
        except (ValueError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def approved(self, message_ts: str = "") -> bool:
        """True only when approve.json names this run (or this plan post) and is not marked rejected."""
        flag = self._flag()
        if not flag or flag.get("approved") is False:
            return False
        return flag.get("run_id") == self._run_id or (bool(message_ts) and str(flag.get("message_ts")) == str(message_ts))

    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        """Polls the flag. On success the flag is consumed: one approval, one plan."""
        deadline = time.monotonic() + timeout_s
        while True:
            if self.approved(message_ts):
                flag = self._flag() or {}
                flag["used_at"] = datetime.now(timezone.utc).isoformat()
                self._approval.with_name("approve.used.json").write_text(json.dumps(flag, indent=2))
                self._approval.unlink()
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_S)

    def posts(self) -> list[dict]:
        if not self._posts.exists():
            return []
        return [json.loads(line) for line in self._posts.read_text().splitlines() if line.strip()]

    def count_posts(self, contains: str) -> int:
        return sum(1 for p in self.posts() if contains in p.get("text", ""))

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        ts = f"{time.time():.6f}"
        if self._webhook:  # a live write; never retried, a retried webhook post is a duplicate message
            live_write()
            req = urllib.request.Request(self._webhook, data=json.dumps({"text": text}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"slack webhook refused the post: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                raise RuntimeError(f"slack webhook post failed: {e}") from e
            if body.strip() != "ok":
                raise RuntimeError(f"slack webhook answered {body[:200]!r}, expected 'ok'")
        self._posts.parent.mkdir(parents=True, exist_ok=True)
        with self._posts.open("a") as f:
            f.write(json.dumps({"ts": ts, "at": datetime.now(timezone.utc).isoformat(), "text": text,
                                "delivered": "slack_webhook" if self._webhook else "panel"}) + "\n")
        return ts
