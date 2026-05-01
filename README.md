# EINSATZBESTÄTIGUNG App

A small Streamlit app that connects to a user's Google Calendar, prefills the
EINSATZBESTÄTIGUNG template with data from one or more selected appointments,
lets the user edit each form, and exports the result as PDF.

Designed for 2-3 users who want to access the app from both desktop and mobile
through a single hosted URL.

## Features

- Sign in with Google (OAuth, read-only Calendar access).
- Pick a date range and select one or many appointments via checkboxes.
- For each selected appointment, edit the prefilled fields (name, date, time,
  location, occasion) before exporting.
- Download a single PDF or, when multiple appointments are selected, a ZIP of
  all generated PDFs.
- Email allowlist so only the intended users can use the deployed app.

## Local development

### 1. Prerequisites

- Python 3.10 or newer.
- LibreOffice installed on your machine. The `soffice` binary must be on PATH
  (Windows default install location is auto-detected by the app).
  - Windows: <https://www.libreoffice.org/download/download/>
  - macOS:   `brew install --cask libreoffice`
  - Linux:   `sudo apt install libreoffice`

### 2. Install Python dependencies

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Create a Google OAuth client

1. Open <https://console.cloud.google.com/> and create (or pick) a project.
2. Enable the **Google Calendar API**: APIs & Services -> Library -> search
   "Google Calendar API" -> Enable.
3. Configure the **OAuth consent screen**: User type = External, fill the
   required fields, add the scope
   `https://www.googleapis.com/auth/calendar.readonly`, and add each user's
   Gmail address as a "Test user" (so they can log in while the app is in
   testing mode).
4. Create OAuth credentials: APIs & Services -> Credentials -> Create
   credentials -> OAuth client ID -> Application type **Web application**.
5. Under "Authorized redirect URIs", add the URL where the app will run:
   - Local: `http://localhost:8501`
   - Streamlit Cloud: `https://<your-app-name>.streamlit.app`
6. Copy the **Client ID** and **Client secret**.

### 4. Configure secrets

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and fill in:

- `redirect_uri` -> the URL from step 5 above.
- `allowed_emails` -> the Gmail addresses that may use the app.
- `[google_oauth] client_id` and `client_secret` from step 6.

### 5. Run

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. Click "Sign in with Google", grant
calendar access, then pick appointments and export PDFs.

## Deploy to Streamlit Community Cloud

1. Push this repository to GitHub (private repo recommended).
2. Go to <https://share.streamlit.io/> and click "New app", then point it at
   the repo and at `app.py`.
3. After it deploys, copy the public URL
   (e.g. `https://your-app-name.streamlit.app`).
4. In Google Cloud Console, edit the OAuth client and add that URL to
   "Authorized redirect URIs".
5. In Streamlit Cloud, open the app's **Settings -> Secrets** and paste the
   contents of your local `.streamlit/secrets.toml` (with `redirect_uri` set
   to the Streamlit Cloud URL). Save.
6. Restart the app from Streamlit Cloud. Each user can now visit the URL,
   sign in with Google, and use the app from desktop or mobile.

LibreOffice is installed automatically on Streamlit Cloud because of the
`packages.txt` file in this repo.

## Project layout

```
einsatzbestaetigung-app/
├── app.py                    Streamlit UI and main flow
├── auth.py                   Google OAuth helpers
├── calendar_client.py        Calendar API wrapper + event-to-fields mapping
├── template_renderer.py      DOCX template rendering with docxtpl
├── pdf_converter.py          DOCX -> PDF via LibreOffice headless
├── requirements.txt          Python dependencies
├── packages.txt              System packages for Streamlit Cloud (libreoffice)
├── templates/
│   └── einsatzbestaetigung.docx   Template with Jinja2 placeholders
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example  Template for your local secrets.toml
└── README.md
```

## Notes

- Tokens are kept in `st.session_state` only, so each user re-logs in once per
  session. Acceptable for 2-3 users; can be extended later to persist refresh
  tokens.
- Calendar scope is `calendar.readonly`. The app cannot modify your calendar.
- The field mapping from a Google Calendar event to template fields is one
  function (`event_to_fields` in `calendar_client.py`); update it once the
  exact appointment structure is defined. Until then, every field is editable
  in the UI before PDF export.
