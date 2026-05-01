"""Parse a free-form appointment email into structured fields using Gemini.

Returns a `ParsedAppointment` dataclass. Any field the model cannot find is
returned as an empty string -- the UI then renders an empty form input that
the user fills manually.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai


MODEL_NAME = "gemini-2.5-flash"


@dataclass
class ParsedAppointment:
    customer_name: str = ""
    date: str = ""        # ISO YYYY-MM-DD
    start_time: str = ""  # HH:MM (24h)
    end_time: str = ""    # HH:MM (24h), empty if missing
    location: str = ""
    topic: str = ""
    sender_note: str = ""
    raw_response: str = field(default="", repr=False)


class EmailParseError(RuntimeError):
    """Raised when Gemini cannot be reached or returns an unusable response."""


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "customer_name": {"type": "string"},
        "date": {"type": "string"},
        "start_time": {"type": "string"},
        "end_time": {"type": "string"},
        "location": {"type": "string"},
        "topic": {"type": "string"},
        "sender_note": {"type": "string"},
    },
    "required": [
        "customer_name",
        "date",
        "start_time",
        "end_time",
        "location",
        "topic",
        "sender_note",
    ],
}


_PROMPT = """\
You receive a German or English email about an upcoming interpreter assignment
or appointment. Extract these fields. Return ONLY valid JSON matching the
schema -- no markdown, no commentary.

Field rules:
- customer_name: the person the appointment is FOR. In German emails this is
  usually mentioned with "Herr X" or "Frau Y" inside the free-form message
  body ("Nachricht"). It is NOT the sender of the email and NOT the contact
  person listed in a "Name" field at the bottom of a Jimdo-style template.
  If you genuinely cannot find a customer name, return an empty string.
- date: in ISO format YYYY-MM-DD. If the email gives the date in DD.MM.YYYY
  or another format, convert it. If only a year/month is given, return empty.
- start_time: HH:MM in 24-hour format (e.g. "08:30").
- end_time: HH:MM in 24-hour format. Empty string if not specified.
- location: the most specific location available. Prefer the full address
  ("Adresse Arzt", "Adresse:", a multi-line block including street and city)
  over a short field like just a postal code or city name.
- topic: short occasion / reason in 1-3 words (e.g. "Arzt Besuch", "Behoerde",
  "Schulgespraech", "Notar"). Take it from the "Thema" or "Bereich" field if
  present, otherwise infer from the message body.
- sender_note: one short sentence describing who sent the request and any
  particular instruction (e.g. "Anfrage von Gerald Mielke-Weyel, bittet um
  Bestaetigung."). Empty string if nothing notable.

Email:
\"\"\"
{body}
\"\"\"
"""


def _configure(api_key: str) -> None:
    genai.configure(api_key=api_key)


def parse_email(body: str, api_key: str) -> ParsedAppointment:
    """Send `body` to Gemini and return the parsed fields.

    Raises `EmailParseError` if the API call fails or the response cannot be
    decoded as JSON. An *empty* response (model returns `""`) becomes a
    `ParsedAppointment` with all fields empty -- not an error.
    """
    if not body.strip():
        raise EmailParseError("Empty email body.")
    if not api_key:
        raise EmailParseError(
            "Missing Gemini API key. Add `gemini_api_key` to your Streamlit secrets."
        )

    _configure(api_key)
    model = genai.GenerativeModel(MODEL_NAME)

    try:
        response = model.generate_content(
            _PROMPT.format(body=body),
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
                "temperature": 0.1,
            },
        )
    except Exception as exc:  # network / quota / auth
        raise EmailParseError(f"Gemini API call failed: {exc}") from exc

    text = (response.text or "").strip()
    if not text:
        return ParsedAppointment()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EmailParseError(
            f"Gemini returned non-JSON content: {text[:200]!r}"
        ) from exc

    return ParsedAppointment(
        customer_name=str(data.get("customer_name", "") or "").strip(),
        date=str(data.get("date", "") or "").strip(),
        start_time=str(data.get("start_time", "") or "").strip(),
        end_time=str(data.get("end_time", "") or "").strip(),
        location=str(data.get("location", "") or "").strip(),
        topic=str(data.get("topic", "") or "").strip(),
        sender_note=str(data.get("sender_note", "") or "").strip(),
        raw_response=text,
    )


def default_event_title(parsed: ParsedAppointment) -> str:
    """Build a sensible default event title from parsed fields.

    Uses `topic - customer_name` when both are present, falls back gracefully.
    Aligns with the existing `event_to_fields` mapping that reads `summary`
    as the customer-name guess.
    """
    topic = parsed.topic.strip()
    name = parsed.customer_name.strip()
    if topic and name:
        return f"{topic} - {name}"
    return name or topic or "Termin"
