"""Smoke testy app.py przez streamlit.testing.v1.AppTest.

Bez realnego pobierania i bez realnych zapytań do Neon —
Database.get_recent_history jest podstawiony atrapą (zakładka "Historia"
wykonuje zapytanie na KAŻDYM rerunie skryptu, niezależnie od aktywnej
zakładki — Streamlit renderuje treść wszystkich st.tabs() w każdym
przebiegu), żeby testy były szybkie, deterministyczne i offline.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.db import Database

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _run_app(monkeypatch) -> AppTest:
    monkeypatch.setattr(Database, "get_recent_history", lambda self, limit=20: [])
    at = AppTest.from_file(APP_PATH)
    at.run()
    return at


def test_app_loads_without_exceptions(monkeypatch):
    at = _run_app(monkeypatch)
    assert not at.exception


def test_selecting_audio_mode_reveals_format_selectbox(monkeypatch):
    at = _run_app(monkeypatch)

    at.selectbox(key="mode_select").select("Audio (MP3 / FLAC)").run()

    assert not at.exception
    assert at.selectbox(key="audio_format_select") is not None


def test_selecting_playlist_shows_in_progress_message_instead_of_running_job(monkeypatch):
    at = _run_app(monkeypatch)

    at.selectbox(key="mode_select").select("Playlist").run()

    assert not at.exception
    info_messages = [info.value for info in at.info]
    assert any("w przygotowaniu" in message for message in info_messages)


def test_invalid_url_shows_error_on_download_click(monkeypatch):
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://vimeo.com/12345").run()
    at.button(key="download_button").click().run()

    assert not at.exception
    assert len(at.error) >= 1
    assert "URL" in at.error[0].value
