# EINSATZBESTÄTIGUNG App

A small Streamlit app with two related features:

1. **Generate confirmations** - prefill the EINSATZBESTÄTIGUNG template with
   data from existing Google Calendar appointments and export as PDF.
2. **Create from email** - paste a request email; Gemini extracts the
   appointment details and the app creates a Google Calendar event after
   you review/edit them.

Designed for 2-3 users who want to access the app from both desktop and mobile
through a single hosted URL.

## Features

- Sign in with Google (OAuth, Calendar read + write events).
- Email allowlist so only the intended users can use the deployed app.
- Generate confirmations:
  - Pick a date range and select one or many appointments via checkboxes.
  - Edit the prefilled fields (name, date, time, location, occasion).
  - Download a single PDF or a ZIP of multiple PDFs.
- Create from email:
  - Paste an email body, click *Parse email* to call Gemini.
  - Edit the parsed title, date, time, location, description.
  - Add the event to your primary Google Calendar.

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
   required fields, add **both** scopes:
   - `https://www.googleapis.com/auth/calendar.readonly` (read events)
   - `https://www.googleapis.com/auth/calendar.events` (create new events)

   Then add each user's Gmail address as a "Test user" (so they can log in
   while the app is in testing mode).
4. Create OAuth credentials: APIs & Services -> Credentials -> Create
   credentials -> OAuth client ID -> Application type **Web application**.
5. Under "Authorized redirect URIs", add the URL where the app will run:
   - Local: `http://localhost:8501`
   - Streamlit Cloud: `https://<your-app-name>.streamlit.app`
6. Copy the **Client ID** and **Client secret**.

### 4. Get a Gemini API key

The "Create from email" feature uses Google's Gemini API to parse pasted
emails into structured fields.

1. Go to <https://aistudio.google.com/apikey> and sign in.
2. Click *Create API key*. The free tier is plenty for 2-3 users.
3. Copy the key (starts with `AI...`).

### 5. Configure secrets

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and fill in:

- `redirect_uri` -> the URL from step 3.5 above.
- `allowed_emails` -> the Gmail addresses that may use the app.
- `[google_oauth] client_id` and `client_secret` from step 3.6.
- `gemini_api_key` from step 4.

### 6. Run

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. Click "Sign in with Google", grant
the requested permissions, and start using either tab.

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

## Upgrading from the read-only version (existing users must re-authorize)

If you previously deployed this app with only `calendar.readonly` scope, the
new "Create from email" feature needs the additional `calendar.events` scope
to add events to your calendar. Existing access tokens don't include it, so:

1. Make sure both scopes are listed on the **OAuth consent screen** in Google
   Cloud Console (see step 3.3 above).
2. Each user must:
   - open the app,
   - click *Sign out* in the sidebar,
   - click *Sign in with Google* again,
   - and accept the new permission prompt.

After that, the *Add to my Google Calendar* button will work.

## Project layout

```
einsatzbestaetigung-app/
├── app.py                    Streamlit UI (tabs: confirmations / create from email)
├── auth.py                   Google OAuth helpers
├── calendar_client.py        Calendar API wrapper (list events, create event, mapping)
├── email_parser.py           Gemini-based email -> structured fields
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
- Calendar scopes: `calendar.readonly` (read events to fill the template) and
  `calendar.events` (create events from parsed emails). The app cannot delete
  or modify existing events.
- All times for created events are in `Europe/Berlin` (configurable in
  `calendar_client.create_event`).
- Privacy: when you click *Parse email*, the pasted email body is sent to
  Google's Gemini API. The app warns about this in the UI; don't paste
  anything you don't want shared with that service.
- The field mapping from a Google Calendar event to template fields is one
  function (`event_to_fields` in `calendar_client.py`); update it once the
  exact appointment structure is defined. Until then, every field is editable
  in the UI before PDF export.
