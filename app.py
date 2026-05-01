"""EINSATZBESTÄTIGUNG app - Streamlit UI.

Flow:
1. Sign in with Google (OAuth web flow, calendar.readonly scope).
2. Pick a date range; the app lists upcoming Google Calendar events.
3. Tick the events you want to confirm. For each selected event, an editable
   form is shown with prefilled fields.
4. Click "Generate PDF(s)" to render the EINSATZBESTÄTIGUNG template and
   convert it to PDF. Download a single PDF or a ZIP of all generated PDFs.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, timedelta
from typing import Optional

import streamlit as st

import auth
from calendar_client import CalendarEvent, event_to_fields, list_events
from pdf_converter import PdfConversionError, docx_to_pdf
from template_renderer import REQUIRED_FIELDS, render_docx

st.set_page_config(
    page_title="EINSATZBESTÄTIGUNG",
    page_icon=":memo:",
    layout="centered",
    initial_sidebar_state="auto",
)


# ---------------------------------------------------------------------------
# Helpers
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


def _login_screen() -> None:
    st.title("EINSATZBESTÄTIGUNG")
    st.write(
        "Sign in with your Google account to access your calendar. "
        "The app only requests **read-only** access to your calendar events."
    )

    try:
        login_url = auth.get_login_url()
    except KeyError as exc:
        st.error(
            "Missing Streamlit secret: "
            f"`{exc.args[0]}`. Copy `.streamlit/secrets.toml.example` to "
            "`.streamlit/secrets.toml` and fill in the OAuth client values."
        )
        return
    except Exception as exc:  # pragma: no cover - defensive
        st.error(f"Could not build the Google sign-in URL: {exc}")
        return

    st.link_button("Sign in with Google", login_url, type="primary")


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
# Main UI
# ---------------------------------------------------------------------------


def _sidebar(session: auth.UserSession) -> tuple[date, date]:
    with st.sidebar:
        st.markdown(f"**Signed in as**\n\n{session.email}")
        if st.button("Sign out", use_container_width=True):
            auth.logout()
            st.session_state.pop("events_cache", None)
            st.session_state.pop("generated_output", None)
            st.rerun()

        st.divider()
        st.subheader("Date range")
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


def _fetch_events(session: auth.UserSession, start: date, end: date) -> list[CalendarEvent]:
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
    for field in REQUIRED_FIELDS:
        key = f"field_{event.id}_{field}"
        values[field] = st.text_input(
            field_labels[field],
            value=st.session_state.get(key, defaults.get(field, "")),
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
            # Avoid name collisions inside the ZIP.
            count = used.get(filename, 0)
            used[filename] = count + 1
            unique = filename if count == 0 else filename.replace(".pdf", f"_{count + 1}.pdf")
            zf.writestr(unique, data)
    return buf.getvalue(), "einsatzbestaetigungen.zip", "application/zip"


def _main_screen(session: auth.UserSession) -> None:
    start, end = _sidebar(session)

    st.title("EINSATZBESTÄTIGUNG")
    st.caption(
        "Pick the appointments you want to confirm. Each selected appointment gets "
        "an editable form below; PDFs are generated from your final values."
    )

    try:
        events = _fetch_events(session, start, end)
    except Exception as exc:
        st.error(f"Could not load calendar: {exc}")
        if st.button("Sign out and retry"):
            auth.logout()
            st.rerun()
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
