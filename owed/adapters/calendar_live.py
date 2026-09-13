"""Google Calendar: the step-3 call. Slots are weekdays 10:00/11:00/14:00/15:00 ET, 30 min."""
from __future__ import annotations
from datetime import date, datetime, timedelta

from owed.adapters.google_auth import calendar
from owed.adapters.util import retry_read
from owed.config import ET
from owed.contract import Calendar, live_write

SLOT_HOURS = (10, 11, 14, 15)
SLOT_MINUTES = 30


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class GoogleCalendar(Calendar):
    def __init__(self, calendar_id: str = "primary"):
        self._svc = calendar()
        self._cal = calendar_id

    # ---- reads ----
    def _busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        r = retry_read(lambda: self._svc.events().list(
            calendarId=self._cal, timeMin=_iso(start), timeMax=_iso(end),
            singleEvents=True, orderBy="startTime", maxResults=250).execute())
        out = []
        for e in r.get("items", []):
            s, t = e.get("start", {}).get("dateTime"), e.get("end", {}).get("dateTime")
            if s and t:
                out.append((datetime.fromisoformat(s), datetime.fromisoformat(t)))
        return out

    def free_slots(self, start: date, n: int) -> list[datetime]:
        first = datetime.combine(start + timedelta(days=1), datetime.min.time(), tzinfo=ET)
        last = first + timedelta(days=14)
        busy = self._busy(first, last)
        slots: list[datetime] = []
        d = first
        while d < last and len(slots) < n:
            if d.weekday() < 5:
                for h in SLOT_HOURS:
                    s = d.replace(hour=h, minute=0)
                    e = s + timedelta(minutes=SLOT_MINUTES)
                    if not any(bs < e and be > s for bs, be in busy):
                        slots.append(s)
                        if len(slots) >= n:
                            break
            d += timedelta(days=1)
        return slots

    def count_events(self, title_contains: str) -> int:
        now = datetime.now(ET)
        r = retry_read(lambda: self._svc.events().list(
            calendarId=self._cal, q=title_contains, timeMin=_iso(now - timedelta(days=30)),
            singleEvents=True, maxResults=250).execute())
        return sum(1 for e in r.get("items", []) if title_contains in e.get("summary", ""))

    # ---- write (only executor.py may call this) ----
    def create_event(self, title: str, start: datetime, attendee: str) -> str:
        if start.tzinfo is None:
            start = start.replace(tzinfo=ET)
        body = {
            "summary": title,
            "start": {"dateTime": _iso(start), "timeZone": "America/New_York"},
            "end": {"dateTime": _iso(start + timedelta(minutes=SLOT_MINUTES)), "timeZone": "America/New_York"},
            "attendees": [{"email": attendee}],
        }
        live_write()
        ev = self._svc.events().insert(calendarId=self._cal, body=body, sendUpdates="none").execute()
        return ev["id"]
