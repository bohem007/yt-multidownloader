"""Nazwa pliku widoczna dla użytkownika w st.download_button.

Niezależna od wewnętrznej nazwy pliku na dysku serwera (ten i tak jest
usuwany zaraz po wczytaniu do RAM — patrz storage.py). Konwencja:
"{Uploader}-{Tytuł}.{ext}", albo "{Uploader}-{Tytuł}.{jezyk}.{ext}" dla
napisów.
"""

from __future__ import annotations

import re

_ILLEGAL_WINDOWS_CHARS = re.compile(r'[:/\\*?"<>|]')
_MAX_BASE_LENGTH = 200


def _sanitize(value: str) -> str:
    """Usuwa znaki niedozwolone w nazwach plików na Windows i przycina
    nadmiarowe białe znaki."""
    cleaned = _ILLEGAL_WINDOWS_CHARS.sub("_", value)
    return re.sub(r"\s+", " ", cleaned).strip()


def build_display_filename(uploader: str, title: str, ext: str, lang: str | None = None) -> str:
    uploader = _sanitize(uploader) or "Unknown"
    title = _sanitize(title) or "download"
    ext = ext.lstrip(".")

    base = f"{uploader}-{title}"
    if len(base) > _MAX_BASE_LENGTH:
        # Przycinamy TYLKO "{uploader}-{title}" — {lang} i {ext} są zawsze
        # krótkie i muszą zostać w całości, żeby nazwa pozostała sensowna.
        base = base[:_MAX_BASE_LENGTH].rstrip()

    stem = f"{base}.{lang}" if lang else base
    return f"{stem}.{ext}"
