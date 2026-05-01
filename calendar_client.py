"""Google Calendar API wrapper and event-to-template-field mapping.

The mapping in `event_to_fields` is a best-guess until the real appointment
structure is defined. Every field is editable in the UI, so wrong guesses
are easy to fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from dateutil import parser as date_parser
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass
class CalendarEvent:
    """A simplified, UI-friendly view of a Google Calendar event."""

    id: str
    summary: str
    description: str
    location: str
    start: datetime
    end: datetime
    is_all_day: bool

    @property
    def label(self) -> str:
        """A short label suitable for a checkbox row."""
        if self.is_all_day:
            when = self.start.strftime("%a %d.%m.%Y") + " (all day)"
        else:
            when = (
                self.start.strftime("%a %d.%m.%Y %H:%M")
                + " - "
                + self.end.strftime("%H:%M")
            )
        title = self.summary or "(untitled)"
        location_suffix = f"  --  {self.location}" if self.location else ""
        return f"{when}  --  {title}{location_suffix}"


def _build_service(credentials: Credentials):
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _parse_event_time(value: dict) -> tuple[datetime, bool]:
    """Parse an event start/end dict into (datetime, is_all_day)."""
    if "dateTime" in value:
        return date_parser.isoparse(value["dateTime"]), False
    # All-day event: only `date` is present (YYYY-MM-DD).
    d = date_parser.isoparse(value["date"]).date()
    return datetime.combine(d, time(0, 0), tzinfo=timezone.utc), True


def _to_calendar_event(raw: dict) -> Optional[CalendarEvent]:
    if raw.get("status") == "cancelled":
        return None
    start_raw = raw.get("start") or {}
    end_raw = raw.get("end") or {}
    if not start_raw or not end_raw:
        return None
    start, is_all_day_start = _parse_event_time(start_raw)
    end, _ = _parse_event_time(end_raw)
    return CalendarEvent(
        id=raw.get("id", ""),
        summary=raw.get("summary", ""),
        description=raw.get("description", ""),
        location=raw.get("location", ""),
        start=start,
        end=end,
        is_all_day=is_all_day_start,
    )


def list_events(
    credentials: Credentials,
    start: date,
    end: date,
    calendar_id: str = "primary",
    max_results: int = 250,
) -> list[CalendarEvent]:
    """List events between `start` (inclusive) and `end` (inclusive)."""
    service = _build_service(credentials)

    time_min = datetime.combine(start, time(0, 0), tzinfo=timezone.utc).isoformat()
    time_max = datetime.combine(
        end + timedelta(days=1), time(0, 0), tzinfo=timezone.utc
    ).isoformat()

    result: dict[str, Any] = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )

    events: list[CalendarEvent] = []
    for raw in result.get("items", []):
        ev = _to_calendar_event(raw)
        if ev is not None:
            events.append(ev)
    return events


def create_event(
    credentials: Credentials,
    title: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
    timezone_name: str = "Europe/Berlin",
    calendar_id: str = "primary",
) -> dict:
    """Insert a new event into the user's calendar and return the API response.

    `start` and `end` are naive `datetime` objects in local (`timezone_name`)
    time. Google Calendar interprets them in the supplied IANA timezone.

    Returns the full event resource dict from the API (includes `htmlLink`,
    `id`, etc.).
    """
    service = _build_service(credentials)

    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {
            "dateTime": start.replace(tzinfo=None).isoformat(timespec="seconds"),
            "timeZone": timezone_name,
        },
        "end": {
            "dateTime": end.replace(tzinfo=None).isoformat(timespec="seconds"),
            "timeZone": timezone_name,
        },
    }

    return service.events().insert(calendarId=calendar_id, body=body).execute()


def event_to_fields(event: CalendarEvent) -> dict[str, str]:
    """Best-guess mapping from a calendar event to template fields.

    Replace this when the real appointment structure is defined; until then
    every field is editable in the UI.

    Returns the dict with keys: name, date, time, location, occasion.
    """
    date_str = event.start.strftime("%d.%m.%Y")

    if event.is_all_day:
        time_str = "ganztägig"
    else:
        time_str = (
            event.start.strftime("%H:%M") + " - " + event.end.strftime("%H:%M")
        )

    title = (event.summary or "").strip()
    description_first_line = ""
    if event.description:
        for line in event.description.splitlines():
            if line.strip():
                description_first_line = line.strip()
                break

    name_guess = title
    occasion_guess = description_first_line or title

    return {
        "name": name_guess,
        "date": date_str,
        "time": time_str,
        "location": (event.location or "").strip(),
        "occasion": occasion_guess,
    }
