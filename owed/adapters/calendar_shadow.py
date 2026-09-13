"""Shadow calendar: a fixed list of free slots. stdlib only. Never imports a network library."""
from __future__ import annotations
from datetime import date, datetime
from typing import Optional

from owed.adapters.shadow_base import ShadowWrites
from owed.contract import Calendar, Intent


class ShadowCalendar(ShadowWrites, Calendar):
    def __init__(self, free: list[datetime], intents: Optional[list[Intent]] = None):
        super().__init__(intents)
        self._free: list[datetime] = sorted(free)
        self._events: list[dict] = []

    # ---- reads ----
    def free_slots(self, start: date, n: int) -> list[datetime]:
        return [s for s in self._free if s.date() > start][:n]

    def count_events(self, title_contains: str) -> int:
        return sum(1 for e in self._events if title_contains in e["title"])

    # ---- write: records an Intent, books the slot in memory only ----
    def create_event(self, title: str, start: datetime, attendee: str) -> str:
        self._intent("create_event", {"title": title, "start_iso": start.isoformat(), "attendee": attendee})
        self._events.append({"title": title, "start": start, "attendee": attendee})
        self._free = [s for s in self._free if s != start]
        return f"shadow-event-{len(self._events)}"
