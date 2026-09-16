"""Test integracyjny engine.py — realne pobranie krótkiego, publicznego klipu.

Oznaczony @pytest.mark.slow — pomijany przez `pytest -m "not slow"`.
Wymaga dostępu do internetu i ffmpeg na PATH.
"""

from __future__ import annotations

import pytest

from src import storage
from src.engine import DownloadEngine, DownloadJob, EngineError, list_available_subtitles
from src.errors import InvalidUrlError
from src.progress import ProgressEvent

# "Me at the zoo" — pierwsze wideo wgrane na YouTube, ~19s, publiczne,
# stabilne od 2005 roku — dobry, szybki fixture do testu integracyjnego.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Film z polskim audio/napisami, na którym wykryto błąd "brak napisów" —
# domyślne subtitleslangs=["en"] w yt-dlp nic nie znajdowało.
POLISH_TEST_VIDEO_URL = "https://www.youtube.com/watch?v=6eBSHbLKuN0"


def test_submit_invalid_url_raises_engine_error_with_original_exception():
    engine = DownloadEngine()

    job = DownloadJob(
        url="https://vimeo.com/12345",
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-invalid-url",
    )

    with pytest.raises(EngineError) as exc_info:
        engine.submit(job)

    engine_error = exc_info.value
    assert isinstance(engine_error.original_exception, InvalidUrlError)


@pytest.mark.slow
def test_engine_submit_downloads_audio_and_respects_size_limit():
    engine = DownloadEngine()
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job",
    )

    result = None
    try:
        result = engine.submit(job, on_event=events.append)

        assert result.path.exists()
        assert result.path.is_file()
        assert result.path.stat().st_size > 0
        assert result.uploader
        assert result.title

        # enforce_size_limit już przeszedł wewnątrz submit() bez wyjątku —
        # tu tylko potwierdzamy, że wynik faktycznie jest pod limitem.
        storage.enforce_size_limit(result.path)

        assert any(event.event_type == "on_finished" for event in events)
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_engine_submit_audio_mp3_returns_path_with_mp3_extension():
    """Regresja: submit() musi zwrócić ścieżkę PO postprocessingu ffmpeg
    (.mp3), nie ścieżkę opartą na outtmpl sprzed konwersji (oryginalny
    kontener pobranego audio, np. .webm)."""
    engine = DownloadEngine()

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-mp3-extension",
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.suffix == ".mp3"
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_list_available_subtitles_returns_non_empty_for_polish_video():
    result = list_available_subtitles(POLISH_TEST_VIDEO_URL)

    assert result["manual"] or result["automatic"]


@pytest.mark.slow
def test_engine_submit_subtitle_downloads_detected_language():
    """Regresja: bez subtitleslangs jawnie ustawionego na wykryty język,
    yt-dlp domyślnie szuka tylko 'en' — dla filmu bez angielskich
    napisów/auto-napisów nic się nie zapisywało."""
    available = list_available_subtitles(POLISH_TEST_VIDEO_URL)
    lang_candidates = available["manual"] + available["automatic"]
    assert lang_candidates, "brak jakichkolwiek napisów do przetestowania"

    lang = next((code for code in lang_candidates if code.startswith("pl")), lang_candidates[0])

    engine = DownloadEngine()
    job = DownloadJob(
        url=POLISH_TEST_VIDEO_URL,
        mode="subtitle",
        output_format="srt",
        session_id="test-session",
        job_id="test-job-subtitle-lang",
        subtitle_lang=lang,
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.stat().st_size > 0
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)


@pytest.mark.slow
def test_engine_submit_subtitle_downloads_manual_caption_not_phantom_media_file():
    """Regresja: dla MANUALNYCH napisów (nie tylko automatycznych, patrz
    test wyżej) yt-dlp też wypełnia requested_downloads fantomowym wpisem
    wskazującym na plik medialny, który nigdy nie zostaje zapisany
    (skip_download=True) — _resolve_result musi poprawnie sięgnąć po
    requested_subtitles, weryfikując .exists() na każdym kandydacie."""
    available = list_available_subtitles(TEST_VIDEO_URL)
    assert available["manual"], "oczekiwano realnych manualnych napisów dla tego filmu"

    lang = available["manual"][0]

    engine = DownloadEngine()
    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="subtitle",
        output_format="srt",
        session_id="test-session",
        job_id="test-job-subtitle-manual",
        subtitle_lang=lang,
    )

    result = None
    try:
        result = engine.submit(job)
        assert result.path.exists()
        assert result.path.suffix == ".srt"
        assert result.path.stat().st_size > 0
    finally:
        if result is not None:
            storage.cleanup(result.path.parent)
