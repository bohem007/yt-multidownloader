"""Test integracyjny engine.py — realne pobranie krótkiego, publicznego klipu.

Oznaczony @pytest.mark.slow — pomijany przez `pytest -m "not slow"`.
Wymaga dostępu do internetu i ffmpeg na PATH.
"""

from __future__ import annotations

import pytest

from src import storage
from src.engine import DownloadEngine, DownloadJob, EngineError
from src.errors import InvalidUrlError
from src.progress import ProgressEvent

# "Me at the zoo" — pierwsze wideo wgrane na YouTube, ~19s, publiczne,
# stabilne od 2005 roku — dobry, szybki fixture do testu integracyjnego.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


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

    job_dir = None
    try:
        job_dir = engine.submit(job, on_event=events.append)

        assert job_dir.exists()

        mp3_files = list(job_dir.glob("*.mp3"))
        assert len(mp3_files) == 1
        assert mp3_files[0].stat().st_size > 0

        # enforce_size_limit już przeszedł wewnątrz submit() bez wyjątku —
        # tu tylko potwierdzamy, że wynik faktycznie jest pod limitem.
        storage.enforce_size_limit(job_dir)

        assert any(event.event_type == "on_finished" for event in events)
    finally:
        if job_dir is not None:
            storage.cleanup(job_dir)
