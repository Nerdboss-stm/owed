"""WebTapChat: the Chat adapter for the Be-the-client flow.

Posts go to the client's Slack incoming webhook when one was given, and always to
state/clients/<email>/posts.jsonl (the status panel reads that). The tap is
state/clients/<email>/approve.json, written by POST /client/approve; it counts only when the
run_id inside it is the run being executed. stdlib only (urllib for the webhook).
Only owed.agent.executor.post may call post(). A webhook post is a live write and is counted.
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


class WebTapChat(Chat):
    def __init__(self, posts_path: Path, approval_path: Path, run_id: str, webhook_url: Optional[str] = None):
        if webhook_url and not webhook_url.startswith(WEBHOOK_PREFIX):
            raise ValueError(f"slack webhook must start with {WEBHOOK_PREFIX}")
        self._posts = Path(posts_path)
        self._approval = Path(approval_path)
        self._run_id = run_id
        self._webhook = webhook_url or None

    # ---- reads ----
    def approved(self) -> bool:
        """True only when approve.json exists and names this run."""
        if not self._approval.exists():
            return False
        try:
            return json.loads(self._approval.read_text()).get("run_id") == self._run_id
        except (ValueError, OSError):
            return False

    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            if self.approved():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)

    def count_posts(self, contains: str) -> int:
        if not self._posts.exists():
            return 0
        return sum(1 for line in self._posts.read_text().splitlines()
                   if line.strip() and contains in json.loads(line).get("text", ""))

    def posts(self) -> list[dict]:
        if not self._posts.exists():
            return []
        return [json.loads(line) for line in self._posts.read_text().splitlines() if line.strip()]

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        live_write()
        ts = f"{time.time():.6f}"
        if self._webhook:  # no retry: a retried webhook post is a duplicate message
            req = urllib.request.Request(self._webhook, data=json.dumps({"text": text}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"slack webhook refused the post: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from e
            if body.strip() != "ok":
                raise RuntimeError(f"slack webhook answered {body[:200]!r}, expected 'ok'")
        self._posts.parent.mkdir(parents=True, exist_ok=True)
        with self._posts.open("a") as f:
            f.write(json.dumps({"ts": ts, "at": datetime.now(timezone.utc).isoformat(), "text": text,
                                "delivered": "slack_webhook" if self._webhook else "panel"}) + "\n")
        return ts
