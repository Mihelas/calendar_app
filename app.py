"""EINSATZBESTÄTIGUNG app - Streamlit UI.

Two main features (in tabs):

1. **Generate confirmations** - pick existing Google Calendar appointments,
   edit a prefilled form, export PDFs of the EINSATZBESTÄTIGUNG template.
2. **Create from email** - paste a request email, Gemini parses it into
   structured fields, the user reviews/edits, and the app creates a new
   Google Calendar event.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, time, timedelta
from typing import Optional

import streamlit as st
from googleapiclient.errors import HttpError

import auth
from calendar_client import (
    CalendarEvent,
    create_event,
    event_to_fields,
    list_events,
)
from email_parser import (
    EmailParseError,
    ParsedAppointment,
    default_event_title,
    parse_email,
)
from pdf_converter import PdfConversionError, docx_to_pdf
from template_renderer import REQUIRED_FIELDS, render_docx

st.set_page_config(
    page_title="EINSATZBESTÄTIGUNG",
    page_icon=":memo:",
    layout="centered",
    initial_sidebar_state="auto",
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(value: str, fallback: str = "einsatz") -> str:
    """Return a filesystem-safe filename fragment."""
    cleaned = re.sub(r"[^\w\-. ]+", "_", value, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or fallback


def _pdf_filename(fields: dict[str, str]) -> str:
    name = _sanitize_filename(fields.get("name", ""), fallback="kunde")
    datum = _sanitize_filename(fields.get("date", ""), fallback="datum")
    return f"einsatzbestaetigung_{name}_{datum}.pdf"


def _try_parse_iso_date(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _try_parse_hhmm(value: str) -> Optional[time]:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


def _login_screen() -> None:
    st.title("EINSATZBESTÄTIGUNG")
    st.write(
        "Sign in with your Google account to access your calendar. "
        "The app needs permission to **read your events** (to fill the "
        "EINSATZBESTÄTIGUNG template) and to **add new events** (when you "
        "create one from a pasted email)."
    )

    try:
        login_url = auth.get_login_url()
    except KeyError as exc:
        st.error(
            "Missing Streamlit secret: "
            f"`{exc.args[0]}`. Copy `.streamlit/secrets.toml.example` to "
            "`.streamlit/secrets.toml` and fill in the required values."
        )
        return
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Could not build the Google sign-in URL: {exc}")
        return

    st.link_button("Sign in with Google", login_url, type="primary")

    st.caption(
        "First time signing in? Google may show a warning that this app "
        "isn't verified. Click **Advanced** -> **Continue to ... (unsafe)** "
        "to proceed -- this is expected for a small private app and the "
        "screen only appears for new users. You may also need to sign in "
        "again about once a week."
    )


def _process_oauth_callback() -> None:
    """If `?code=...` is present in the URL, finish the OAuth flow."""
    params = st.query_params
    code = params.get("code")
    if not code:
        return
    if isinstance(code, list):
        code = code[0]

    try:
        session = auth.handle_oauth_callback(code)
    except PermissionError as exc:
        st.query_params.clear()
        st.error(str(exc))
        st.stop()
    except Exception as exc:  # pragma: no cover - defensive
        st.query_params.clear()
        st.error(f"Sign-in failed: {exc}")
        st.stop()

    st.session_state["user_session"] = session
    st.query_params.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _clear_user_state() -> None:
    """Remove all per-user state from `st.session_state`.

    Used on sign-out so a different user signing into the same browser
    doesn't see leftover form data (calendar selections, parsed emails,
    edited fields, etc.).
    """
    explicit_keys = {
        "events_cache",
        "generated_output",
        "email_created_event",
        "email_parsed",
        "email_body",
    }
    prefix_keys = {"ef_", "field_", "sel_"}

    for key in list(st.session_state.keys()):
        if key in explicit_keys or any(key.startswith(p) for p in prefix_keys):
            st.session_state.pop(key, None)


def _sidebar(session: auth.UserSession) -> None:
    with st.sidebar:
        st.markdown(f"**Signed in as**\n\n{session.email}")
        if st.button("Sign out", use_container_width=True):
            auth.logout()
            _clear_user_state()
            st.rerun()


# ---------------------------------------------------------------------------
# Tab 1: Generate confirmations (existing PDF flow)
# ---------------------------------------------------------------------------


def _date_range_picker() -> tuple[date, date]:
    today = date.today()
    default_range = (today, today + timedelta(days=14))
    picked = st.date_input(
        "Show events between",
        value=default_range,
        format="DD.MM.YYYY",
    )
    if isinstance(picked, tuple) and len(picked) == 2:
        start, end = picked
    else:
        start = end = picked  # single date selected
    if start > end:
        start, end = end, start
    return start, end


def _fetch_events(
    session: auth.UserSession, start: date, end: date
) -> list[CalendarEvent]:
    cache_key = ("events_cache", start.isoformat(), end.isoformat(), session.email)
    cached = st.session_state.get("events_cache")
    if cached and cached.get("key") == cache_key:
        return cached["events"]

    with st.spinner("Loading calendar events..."):
        events = list_events(session.credentials, start, end)
    st.session_state["events_cache"] = {"key": cache_key, "events": events}
    return events


def _event_form(event: CalendarEvent) -> dict[str, str]:
    """Render the editable form for one event and return the current field values."""
    defaults = event_to_fields(event)
    field_labels = {
        "name": "VORNAME, NACHNAME",
        "date": "DATUM",
        "time": "UHRZEIT",
        "location": "ORT",
        "occasion": "ANLASS",
    }
    values: dict[str, str] = {}
    for field_name in REQUIRED_FIELDS:
        key = f"field_{event.id}_{field_name}"
        values[field_name] = st.text_input(
            field_labels[field_name],
            value=st.session_state.get(key, defaults.get(field_name, "")),
            key=key,
        )
    return values


def _generate_outputs(
    selected: list[tuple[CalendarEvent, dict[str, str]]],
) -> Optional[tuple[bytes, str, str]]:
    """Generate PDF (single) or ZIP (multiple). Returns (data, filename, mime) or None."""
    if not selected:
        st.warning("No appointments selected.")
        return None

    pdfs: list[tuple[str, bytes]] = []
    progress = st.progress(0.0, text="Generating...")
    for idx, (event, fields) in enumerate(selected, start=1):
        progress.progress(
            (idx - 1) / len(selected),
            text=f"Rendering {idx}/{len(selected)}: {event.summary or '(untitled)'}",
        )
        try:
            docx_bytes = render_docx(fields)
            pdf_bytes = docx_to_pdf(docx_bytes)
        except PdfConversionError as exc:
            progress.empty()
            st.error(f"PDF conversion failed: {exc}")
            return None
        except Exception as exc:  # pragma: no cover - defensive
            progress.empty()
            st.error(f"Failed to render '{event.summary}': {exc}")
            return None
        pdfs.append((_pdf_filename(fields), pdf_bytes))
    progress.progress(1.0, text="Done.")
    progress.empty()

    if len(pdfs) == 1:
        filename, data = pdfs[0]
        return data, filename, "application/pdf"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: dict[str, int] = {}
        for filename, data in pdfs:
            count = used.get(filename, 0)
            used[filename] = count + 1
            unique = (
                filename
                if count == 0
                else filename.replace(".pdf", f"_{count + 1}.pdf")
            )
            zf.writestr(unique, data)
    return buf.getvalue(), "einsatzbestaetigungen.zip", "application/zip"


def _pdf_tab(session: auth.UserSession) -> None:
    st.caption(
        "Pick the appointments you want to confirm. Each selected appointment "
        "gets an editable form below; PDFs are generated from your final values."
    )

    start, end = _date_range_picker()

    try:
        events = _fetch_events(session, start, end)
    except HttpError as exc:
        st.error(f"Could not load calendar: {exc}")
        return
    except Exception as exc:
        st.error(f"Could not load calendar: {exc}")
        return

    if not events:
        st.info("No events in the selected date range.")
        return

    st.subheader(f"Appointments ({len(events)} found)")

    selected_pairs: list[tuple[CalendarEvent, dict[str, str]]] = []
    for event in events:
        sel_key = f"sel_{event.id}"
        is_selected = st.checkbox(event.label, key=sel_key)
        if is_selected:
            with st.expander("Edit fields", expanded=True):
                fields = _event_form(event)
                selected_pairs.append((event, fields))

    st.divider()

    cols = st.columns([1, 1])
    with cols[0]:
        generate = st.button(
            f"Generate PDF{'s' if len(selected_pairs) != 1 else ''}",
            type="primary",
            disabled=not selected_pairs,
            use_container_width=True,
        )
    with cols[1]:
        if st.session_state.get("generated_output"):
            if st.button("Clear last result", use_container_width=True):
                st.session_state.pop("generated_output", None)
                st.rerun()

    if generate:
        result = _generate_outputs(selected_pairs)
        if result is not None:
            data, filename, mime = result
            st.session_state["generated_output"] = {
                "data": data,
                "filename": filename,
                "mime": mime,
                "count": len(selected_pairs),
            }

    output = st.session_state.get("generated_output")
    if output:
        label = (
            "Download PDF"
            if output["count"] == 1
            else f"Download ZIP ({output['count']} PDFs)"
        )
        st.success(
            f"{output['count']} PDF{'s' if output['count'] != 1 else ''} ready."
        )
        st.download_button(
            label=label,
            data=output["data"],
            file_name=output["filename"],
            mime=output["mime"],
            type="primary",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Tab 2: Create from email
# ---------------------------------------------------------------------------


_EMAIL_FORM_DEFAULTS = {
    "ef_title": "",
    "ef_date": None,        # set lazily to today + 1
    "ef_start": time(9, 0),
    "ef_end": time(10, 0),
    "ef_location": "",
    "ef_description": "",
}


def _ensure_email_form_defaults() -> None:
    for key, default in _EMAIL_FORM_DEFAULTS.items():
        if key not in st.session_state:
            if key == "ef_date":
                st.session_state[key] = date.today() + timedelta(days=1)
            else:
                st.session_state[key] = default


def _apply_parsed_to_form(parsed: ParsedAppointment, raw_body: str) -> None:
    """Update the form's session_state keys based on parsed Gemini output."""
    st.session_state["ef_title"] = default_event_title(parsed)

    parsed_date = _try_parse_iso_date(parsed.date)
    if parsed_date is not None:
        st.session_state["ef_date"] = parsed_date

    parsed_start = _try_parse_hhmm(parsed.start_time)
    if parsed_start is not None:
        st.session_state["ef_start"] = parsed_start

    parsed_end = _try_parse_hhmm(parsed.end_time)
    if parsed_end is not None:
        st.session_state["ef_end"] = parsed_end
    elif parsed_start is not None:
        st.session_state["ef_end"] = (
            datetime.combine(date.today(), parsed_start) + timedelta(hours=1)
        ).time()

    if parsed.location:
        st.session_state["ef_location"] = parsed.location

    description_parts = []
    if parsed.sender_note:
        description_parts.append(parsed.sender_note)
    if raw_body:
        description_parts.append("--- Originale E-Mail ---")
        description_parts.append(raw_body.strip())
    st.session_state["ef_description"] = "\n\n".join(description_parts).strip()


def _reset_email_form() -> None:
    for key in list(_EMAIL_FORM_DEFAULTS.keys()) + [
        "email_body",
        "email_parsed",
        "email_created_event",
    ]:
        st.session_state.pop(key, None)


def _email_tab(session: auth.UserSession) -> None:
    # If an event was just created, show only the success state. The form
    # disappears so the user can't accidentally create a duplicate by
    # double-clicking.
    if event := st.session_state.get("email_created_event"):
        st.success("Event added to your Google Calendar.")
        if html_link := event.get("htmlLink"):
            st.markdown(f"[Open it in Google Calendar]({html_link})")
        st.caption(
            "Tip: it now also shows up in the **Generate confirmations** tab."
        )
        if st.button(
            "Create another from email",
            type="primary",
            use_container_width=True,
        ):
            _reset_email_form()
            st.rerun()
        return

    st.caption(
        "Paste a request email below. The app will use Gemini to extract the "
        "appointment details, then you review/edit and add it to your Google "
        "Calendar. Always double-check the parsed values before saving."
    )
    st.warning(
        "Privacy: the pasted email is sent to Google's Gemini API for parsing. "
        "Don't paste anything you don't want shared with that service.",
        icon=":material/lock:",
    )

    _ensure_email_form_defaults()

    st.text_area(
        "Email body",
        key="email_body",
        height=240,
        placeholder="Hallo, du hast eine Nachricht...\n\nThema: ...\nDatum: ...",
    )

    parse_col, reset_col = st.columns([1, 1])
    with parse_col:
        # Buttons stay enabled regardless of widget state. Streamlit only
        # commits text-area edits to session_state on commit (Ctrl+Enter or
        # focus loss), so a `disabled=` based on the textarea value would
        # leave the button greyed out the moment the user typed. We validate
        # at click time instead.
        parse_clicked = st.button("Parse email", use_container_width=True)
    with reset_col:
        if st.button("Reset form", use_container_width=True):
            _reset_email_form()
            st.rerun()

    if parse_clicked:
        body = st.session_state.get("email_body", "").strip()
        if not body:
            st.warning("Paste an email first.")
        else:
            try:
                api_key = st.secrets["gemini_api_key"]
            except KeyError:
                st.error(
                    "Missing `gemini_api_key` in Streamlit secrets. "
                    "See README.md for setup."
                )
                return
            try:
                with st.spinner("Asking Gemini to extract the appointment..."):
                    parsed = parse_email(body, api_key=api_key)
            except EmailParseError as exc:
                st.error(f"Parsing failed: {exc}")
                return

            st.session_state["email_parsed"] = parsed
            _apply_parsed_to_form(parsed, body)
            st.rerun()

    if parsed := st.session_state.get("email_parsed"):
        if not parsed.customer_name and not parsed.date:
            st.warning(
                "Gemini couldn't find appointment details in this email. "
                "Fill the form manually or paste a different email."
            )
        else:
            st.info("Parsed. Review and edit before adding to your calendar.")

    st.divider()
    st.subheader("Event details")

    st.text_input(
        "Title",
        key="ef_title",
        help="Will appear as the event summary in Google Calendar.",
    )
    st.date_input("Date", key="ef_date", format="DD.MM.YYYY")
    time_cols = st.columns(2)
    with time_cols[0]:
        st.time_input("Start time", key="ef_start", step=300)
    with time_cols[1]:
        st.time_input("End time", key="ef_end", step=300)
    st.text_area("Location", key="ef_location", height=100)
    st.text_area("Description / notes", key="ef_description", height=200)

    create_clicked = st.button(
        "Add to my Google Calendar",
        type="primary",
        use_container_width=True,
    )

    if create_clicked:
        title = st.session_state["ef_title"].strip()
        if not title:
            st.warning("Title is required.")
            return

        event_date = st.session_state["ef_date"]
        start_t = st.session_state["ef_start"]
        end_t = st.session_state["ef_end"]
        start_dt = datetime.combine(event_date, start_t)
        end_dt = datetime.combine(event_date, end_t)

        if end_dt <= start_dt:
            st.error("End time must be after start time.")
            return

        try:
            with st.spinner("Creating the event..."):
                event = create_event(
                    credentials=session.credentials,
                    title=title,
                    start=start_dt,
                    end=end_dt,
                    location=st.session_state["ef_location"].strip(),
                    description=st.session_state["ef_description"],
                )
        except HttpError as exc:
            if exc.resp.status in (401, 403):
                st.error(
                    "Permission denied. The app's calendar permissions "
                    "have changed: please sign out and sign in again to "
                    "grant access to add events, then try once more."
                )
            else:
                st.error(f"Could not create the event: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            st.error(f"Could not create the event: {exc}")
        else:
            st.session_state["email_created_event"] = event
            st.session_state.pop("events_cache", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Top-level layout
# ---------------------------------------------------------------------------


def _main_screen(session: auth.UserSession) -> None:
    _sidebar(session)
    st.title("EINSATZBESTÄTIGUNG")

    pdf_tab, email_tab = st.tabs(["Generate confirmations", "Create from email"])
    with pdf_tab:
        _pdf_tab(session)
    with email_tab:
        _email_tab(session)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    _process_oauth_callback()

    session = auth.get_current_session()
    if session is None:
        _login_screen()
        return

    _main_screen(session)


if __name__ == "__main__":
    main()
