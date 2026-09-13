"""Env + clock. stdlib only. Reads .env at repo root; os.environ wins over the file."""
from __future__ import annotations
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")

_loaded = False


def load_env() -> dict[str, str]:
    global _loaded
    env_path = ROOT / ".env"
    if not _loaded and env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
        _loaded = True
    return dict(os.environ)


def env(key: str, default: str | None = None) -> str:
    load_env()
    val = os.environ.get(key, default)
    if val is None or val == "":
        raise KeyError(f"missing env var {key} (set it in .env)")
    return val


def offline() -> bool:
    """True when no model call may be made: OWED_OFFLINE=1, or no ANTHROPIC_API_KEY. Classifier falls back
    to its deterministic rules, verifier to its deterministic checks; the actor needs a scripted drafter."""
    load_env()
    return bool(os.environ.get("OWED_OFFLINE")) or not os.environ.get("ANTHROPIC_API_KEY")


def today() -> date:
    """TODAY from .env so scenarios are reproducible; falls back to the ET wall clock."""
    load_env()
    raw = os.environ.get("TODAY")
    if raw:
        return date.fromisoformat(raw)
    return datetime.now(ET).date()
