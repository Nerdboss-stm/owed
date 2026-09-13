"""WebTapChat: a Chat adapter whose tap is a file, for the Rehearsal Room. stdlib only.

Layout, per client, under state/clients/<email>/:
  status.jsonl    one line per post(): {"ts", "text", "at"}. The UI reads this; it is the API.
  approve.json    written by the UI when the freelancer taps: {"approved": true, "message_ts": "<ts>"}.
                  wait_for_tap(message_ts) polls for it and consumes it (renamed to approve.used.json),
                  so one flag approves exactly one plan post. A flag for a different message_ts is ignored.

post() optionally forwards the text to a Slack incoming webhook (SLACK_WEBHOOK_URL or the constructor
argument). That is the only network write here and it counts as a live write. The freelancer's own
channel keeps using adapters/chat_live.py (reactions); this adapter is for the client-facing web tap.
"""
from __future__ import annotations
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from owed.config import ROOT, load_env
from owed.contract import Chat, live_write

POLL_S = 2.0


def client_dir(client_email: str, root: Optional[Path] = None) -> Path:
    """state/clients/<email>/, with the email reduced to a safe path segment."""
    safe = re.sub(r"[^A-Za-z0-9._@+-]", "_", client_email.strip().lower())
    return (root or ROOT / "state" / "clients") / safe


class WebTapChat(Chat):
    def __init__(self, client_email: str, root: Optional[Path] = None, webhook_url: Optional[str] = None):
        load_env()
        self.dir = client_dir(client_email, root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status = self.dir / "status.jsonl"
        self.flag = self.dir / "approve.json"
        self.webhook = webhook_url or os.environ.get("SLACK_WEBHOOK_URL") or None

    # ---- reads ----
    def _read_flag(self) -> Optional[dict]:
        if not self.flag.exists():
            return None
        try:
            data = json.loads(self.flag.read_text() or "{}")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def wait_for_tap(self, message_ts: str, emoji: str = "white_check_mark", timeout_s: int = 600) -> bool:
        """True when approve.json says approved for this message_ts. The flag is consumed on success."""
        deadline = time.monotonic() + timeout_s
        while True:
            flag = self._read_flag()
            if flag and flag.get("approved") is True and str(flag.get("message_ts", message_ts)) == str(message_ts):
                used = self.dir / "approve.used.json"
                flag["used_at"] = datetime.now(timezone.utc).isoformat()
                used.write_text(json.dumps(flag, indent=2))
                self.flag.unlink()
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_S)

    def count_posts(self, contains: str) -> int:
        if not self.status.exists():
            return 0
        return sum(1 for ln in self.status.read_text().splitlines() if ln.strip() and contains in json.loads(ln)["text"])

    # ---- write (only executor.py may call this) ----
    def post(self, text: str, blocks: Optional[list] = None) -> str:
        ts = f"{time.time():.6f}"
        with self.status.open("a") as f:
            f.write(json.dumps({"ts": ts, "text": text, "at": datetime.now(timezone.utc).isoformat()}) + "\n")
        if self.webhook:
            live_write()
            req = urllib.request.Request(self.webhook, data=json.dumps({"text": text}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except (urllib.error.URLError, TimeoutError) as e:
                raise RuntimeError(f"Slack webhook post failed: {e}") from e  # fail loud, never retried
        return ts
