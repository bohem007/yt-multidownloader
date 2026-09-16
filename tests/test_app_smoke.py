"""Smoke testy app.py przez streamlit.testing.v1.AppTest.

Bez realnego pobierania i bez realnych zapytań do Neon —
Database.get_recent_history jest podstawiony atrapą (zakładka "Historia"
wykonuje zapytanie na KAŻDYM rerunie skryptu, niezależnie od aktywnej
zakładki — Streamlit renderuje treść wszystkich st.tabs() w każdym
przebiegu), żeby testy były szybkie, deterministyczne i offline.
"""

from __future__ import annotations

import queue as queue_module
from pathlib import Path

from streamlit.testing.v1 import AppTest

import src.engine as engine_module
from src.db import Database
from src.progress import ProgressEvent

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


def test_subtitle_language_selectbox_filters_automatic_captions_to_allowed_set(monkeypatch):
    """automatic_captions z YouTube potrafi zawierać >150 kodów (pełna
    lista celów auto-tłumaczenia) — selectbox ma pokazywać WYŁĄCZNIE
    manualne napisy plus automatyczne ograniczone do {pl, de, en}."""
    fake_subtitles = {
        "manual": ["fr"],
        "automatic": ["pl", "de", "en", "es", "it", "ja"],
    }
    monkeypatch.setattr(engine_module, "list_available_subtitles", lambda url: fake_subtitles)

    at = _run_app(monkeypatch)
    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()
    at.selectbox(key="mode_select").select("Napisy (SRT / VTT)").run()

    assert not at.exception
    lang_select = at.selectbox(key="subtitle_lang_select")
    assert set(lang_select.proto.options) == {"fr", "pl", "de", "en"}


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


def test_new_url_button_disabled_while_url_empty(monkeypatch):
    at = _run_app(monkeypatch)

    assert not at.exception
    assert at.button(key="new_url_button").proto.disabled is True


def test_filled_url_enables_new_url_button_and_locks_input_on_next_rerun(monkeypatch):
    """La blokada text_input ma jednorenderowe opóźnienie z konieczności:
    Streamlit nie przyjmuje nowej wartości widgetu renderowanego w TYM
    SAMYM przebiegu jako disabled=True — więc pole jest zablokowane od
    NASTĘPNEGO przebiegu, nie od razu. "Nowy URL" nie ma tego problemu,
    bo jego `disabled` liczy się z wartości `url` zwróconej w tym samym
    przebiegu, nie z osobno przechowywanego flaga."""
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()

    assert not at.exception
    assert at.button(key="new_url_button").proto.disabled is False
    assert at.text_input(key="url_input").proto.disabled is False

    at.run()

    assert not at.exception
    assert at.text_input(key="url_input").value == "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    assert at.text_input(key="url_input").proto.disabled is True


def test_new_url_button_clears_and_unlocks_url_input(monkeypatch):
    at = _run_app(monkeypatch)

    at.text_input(key="url_input").input("https://www.youtube.com/watch?v=jNQXAC9IVRw").run()
    at.button(key="new_url_button").click().run()

    assert not at.exception
    assert at.text_input(key="url_input").value == ""
    assert at.text_input(key="url_input").proto.disabled is False


def _simulate_job_in_flight(at: AppTest, job_queue: "queue_module.Queue") -> None:
    """Symuluje "zadanie w toku" bez realnego wątku/silnika — wstrzykuje
    stan bezpośrednio do session_state (klucze SessionState — status/queue/
    job_id/started_at — są tu celowo odczytane po nazwie, bo to jedyny
    sposób odtworzenia stanu w połowie joba bez prawdziwego JobRunner)."""
    at.session_state["status"] = "running"
    at.session_state["queue"] = job_queue
    at.session_state["job_id"] = "job-smoke-test"
    at.session_state["started_at"] = 0.0


def test_premature_on_finished_without_result_path_is_not_treated_as_terminal(monkeypatch):
    """Regresja: engine.py emituje "on_finished" z progress_hooks w chwili,
    gdy sam DOWNLOAD się kończy — ZANIM postprocessing (ffmpeg/zapis
    napisów) faktycznie się skończy, więc ten event nie ma result_path.
    Jeśli fragment odpytujący kolejkę złapie WYŁĄCZNIE ten wczesny event
    (typowe: postprocessing trwa dłużej niż jeden tick pollingu 0.5s),
    nie może potraktować go jako zakończenie joba — inaczej UI zawsze
    kończy z "nie znaleziono pliku wynikowego", nawet gdy pobranie się
    naprawdę powiodło (i job_runner.py wysyłał już poprawny drugi event)."""
    at = _run_app(monkeypatch)

    premature_queue: queue_module.Queue = queue_module.Queue()
    premature_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Pobieranie zakończone, przetwarzanie...",
            # BEZ result_path — dokładnie tak, jak faktycznie emituje to
            # engine.py z progress_hooks (patrz komentarz w job_runner.py).
        )
    )
    _simulate_job_in_flight(at, premature_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "running"
    assert at.session_state["error_message"] is None


def test_completed_job_with_result_path_shows_download_button(monkeypatch, tmp_path):
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.mp3"
    result_file.write_bytes(b"fake mp3 bytes")

    finished_queue: queue_module.Queue = queue_module.Queue()
    finished_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, finished_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["result_data"] == b"fake mp3 bytes"
    assert len(at.download_button) >= 1


def test_both_finished_events_in_same_queue_batch_resolve_to_done(monkeypatch, tmp_path):
    """Regresja: dla trybu Subtitle engine.py (progress_hooks) i job_runner.py
    wysyłają DWA zdarzenia "on_finished" pod rząd — pierwsze bez result_path
    (sam download skończony, postprocessing/zapis napisów jeszcze w locie),
    drugie z result_path (prawdziwie terminalne). Jeśli oba trafią do kolejki
    ZANIM fragment zdąży ją odpytać (typowe — czas między nimi jest krótszy
    niż jeden tick pollingu 0.5s), pętla drenująca w _render_progress musi
    wyciągnąć OBA w jednym cyklu i potraktować jako terminalne wyłącznie
    drugie — inaczej UI zatrzymałoby się na pierwszym (puste result_path)
    i pokazałoby "nie znaleziono pliku wynikowego" mimo realnego sukcesu."""
    at = _run_app(monkeypatch)

    result_file = tmp_path / "Uploader-Title.srt"
    result_file.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nfake subtitle\n")

    batched_queue: queue_module.Queue = queue_module.Queue()
    batched_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Pobieranie zakończone, przetwarzanie...",
            # BEZ result_path — dokładnie jak wczesny event z progress_hooks.
        )
    )
    batched_queue.put(
        ProgressEvent(
            event_type="on_finished",
            percent=100.0,
            message="Zakończono",
            result_path=result_file,
            result_uploader="Test Uploader",
            result_title="Test Title",
        )
    )
    _simulate_job_in_flight(at, batched_queue)

    at.run()

    assert not at.exception
    assert at.session_state["status"] == "done"
    assert at.session_state["error_message"] is None
    assert at.session_state["result_data"] is not None
    assert len(at.download_button) >= 1
