"""Render the EINSATZBESTÄTIGUNG DOCX template with user-provided fields."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Mapping

from docxtpl import DocxTemplate

TEMPLATE_PATH = Path(__file__).parent / "templates" / "einsatzbestaetigung.docx"

REQUIRED_FIELDS = ("name", "date", "time", "location", "occasion")


def render_docx(fields: Mapping[str, str]) -> bytes:
    """Render the template with the given fields and return the DOCX bytes.

    `fields` must contain the keys `name`, `date`, `time`, `location`,
    `occasion`. Missing keys are rendered as empty strings.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")

    context = {key: str(fields.get(key, "") or "") for key in REQUIRED_FIELDS}

    doc = DocxTemplate(str(TEMPLATE_PATH))
    doc.render(context)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
