"""Testy job_runner.py — Threading Bridge + limit współbieżności.

Pobieranie w tle (test_start_...) jest integracyjne i @pytest.mark.slow:
wymaga internetu i ffmpeg na PATH, tak jak tests/test_engine.py.

Limit współbieżności jest testowany DETERMINISTYCZNIE, z podstawionym
fałszywym silnikiem (JobRunner(engine=...)) — realne, bardzo krótkie
pobranie (~19s klip) kończy się na tyle szybko, że sprawdzanie
is_slot_available() po prostym `threading.Event` na "on_start" jest
z natury zawodne (race: pobranie+ffmpeg mogą zdążyć się zakończyć,
zanim wątek testowy sprawdzi slot). Fałszywy silnik blokuje się na
sygnał testu, więc "zadanie w trakcie" jest gwarantowane bez zgadywania
czasu — dzięki temu test jest też szybki i nie wymaga sieci.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from src import job_runner as job_runner_module
from src import storage
from src.config import Settings
from src.engine import DownloadJob
from src.job_runner import JobRunner
from src.progress import ProgressEvent

TEST_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def _wait_until(predicate, timeout: float = 60.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _BlockingFakeEngine:
    """Podstawiany w miejsce DownloadEngine — submit() blokuje się do sygnału
    z testu, więc "zadanie w trakcie" jest deterministyczne, bez zależności
    od czasu trwania realnego pobrania."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release_gate = threading.Event()

    def submit(self, job: DownloadJob, on_event=None) -> Path:
        self.started.set()
        self.release_gate.wait(timeout=10.0)
        if on_event is not None:
            on_event(ProgressEvent(event_type="on_finished", percent=100.0, message="fake done"))
        return Path("/fake/path")


@pytest.mark.slow
def test_start_does_not_block_and_reports_finished_eventually():
    runner = JobRunner()
    finished = threading.Event()
    events: list[ProgressEvent] = []

    def on_state(event: ProgressEvent) -> None:
        events.append(event)
        if event.event_type in ("on_finished", "on_error"):
            finished.set()

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="test-job-runner-nonblocking",
    )

    started_at = time.monotonic()
    status = runner.start(job, on_state=on_state)
    call_duration = time.monotonic() - started_at

    try:
        assert status == "started"
        # start() musi wrócić natychmiast — pobieranie (kilka-kilkanaście
        # sekund) dzieje się w tle, nie w wątku wywołującym.
        assert call_duration < 1.0

        assert _wait_until(finished.is_set, timeout=60.0)
        assert any(event.event_type == "on_finished" for event in events)
    finally:
        storage.cleanup(storage.create(job.session_id, job.job_id))


def test_concurrency_limit_makes_second_job_queue(monkeypatch):
    monkeypatch.setattr(
        job_runner_module, "settings", Settings.from_env({"MAX_CONCURRENT_JOBS": "1"})
    )

    fake_engine = _BlockingFakeEngine()
    runner = JobRunner(engine=fake_engine)

    job_a = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="job-a-concurrency",
    )
    job_b = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="job-b-concurrency",
    )

    status_a = runner.start(job_a, on_state=lambda event: None)
    assert status_a == "started"

    # czekamy, aż fałszywy silnik faktycznie "zajmie się" job_a
    assert fake_engine.started.wait(timeout=5.0)
    assert runner.is_slot_available() is False

    status_b = runner.start(job_b, on_state=lambda event: None)
    assert status_b == "queued"  # limit=1, job_a wciąż trzyma slot

    fake_engine.release_gate.set()  # pozwól job_a "zakończyć się"

    assert _wait_until(runner.is_slot_available, timeout=5.0)


def test_capacity_change_mid_flight_does_not_corrupt_release(monkeypatch):
    """Regresja: release() musi trafić w semafor, z którego naprawdę
    przyznano permit — nie w "aktualnie bieżący", jeśli capacity zmieniło
    się (i semafor został przebudowany) w trakcie trwania zadania."""
    monkeypatch.setattr(
        job_runner_module, "settings", Settings.from_env({"MAX_CONCURRENT_JOBS": "2"})
    )

    fake_engine_a = _BlockingFakeEngine()
    runner_a = JobRunner(engine=fake_engine_a)
    job_a = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="job-a-midflight",
    )

    # job_a zajmuje 1 z 2 miejsc na starym semaforze (capacity=2).
    assert runner_a.start(job_a, on_state=lambda event: None) == "started"
    assert fake_engine_a.started.wait(timeout=5.0)

    # limit zmienia się w trakcie, gdy job_a WCIĄŻ trzyma permit —
    # kolejny acquire() musi przebudować semafor na capacity=1.
    monkeypatch.setattr(
        job_runner_module, "settings", Settings.from_env({"MAX_CONCURRENT_JOBS": "1"})
    )

    fake_engine_b = _BlockingFakeEngine()
    runner_b = JobRunner(engine=fake_engine_b)
    job_b = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="job-b-midflight",
    )

    # job_b bierze jedyny permit z NOWEGO semafora (capacity=1) — 0 wolnych.
    assert runner_b.start(job_b, on_state=lambda event: None) == "started"
    assert fake_engine_b.started.wait(timeout=5.0)
    assert runner_b.is_slot_available() is False

    # job_a (ze STAREGO semafora) kończy się i zwalnia swój permit.
    fake_engine_a.release_gate.set()

    # Błędna implementacja zwolniłaby permit na NOWYM semaforze (bo
    # release() czytałby "aktualnie bieżący"), co sztucznie zrobiłoby
    # miejsce, mimo że job_b wciąż trzyma jedyny slot capacity=1.
    # Poprawna implementacja: slot ma zostać zajęty, dopóki job_b nie
    # skończy — sprawdzamy to z małym zapasem czasowym.
    time.sleep(0.2)
    assert runner_b.is_slot_available() is False

    fake_engine_b.release_gate.set()
    assert _wait_until(runner_b.is_slot_available, timeout=5.0)
