"""DOCX -> PDF conversion via LibreOffice headless.

Works on Windows (default LibreOffice install path is auto-detected),
macOS, and Linux (including Streamlit Community Cloud, where LibreOffice
is installed via `packages.txt`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class PdfConversionError(RuntimeError):
    """Raised when LibreOffice fails to convert a DOCX to PDF."""


def _find_soffice() -> str:
    """Locate the LibreOffice binary. Returns its absolute path."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

    raise PdfConversionError(
        "LibreOffice not found. Install it and ensure 'soffice' is on PATH "
        "(Windows default install paths are auto-detected)."
    )


def docx_to_pdf(docx_bytes: bytes, timeout_sec: int = 90) -> bytes:
    """Convert DOCX bytes to PDF bytes using LibreOffice headless.

    Each call uses an isolated temporary directory so concurrent conversions
    cannot collide on output filenames.
    """
    soffice = _find_soffice()

    with tempfile.TemporaryDirectory(prefix="einsatzbest_") as tmpdir:
        tmp = Path(tmpdir)
        in_path = tmp / "input.docx"
        in_path.write_bytes(docx_bytes)

        # Use a per-call user profile so parallel runs don't fight over locks.
        user_profile = tmp / "lo_profile"
        user_profile.mkdir()

        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation=file:///{user_profile.as_posix()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp),
            str(in_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfConversionError(
                f"LibreOffice timed out after {timeout_sec}s while converting DOCX to PDF."
            ) from exc

        out_path = tmp / "input.pdf"
        if proc.returncode != 0 or not out_path.exists():
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            stdout = proc.stdout.decode("utf-8", errors="replace").strip()
            raise PdfConversionError(
                "LibreOffice failed to produce a PDF.\n"
                f"return code: {proc.returncode}\n"
                f"stdout: {stdout}\n"
                f"stderr: {stderr}"
            )

        return out_path.read_bytes()
