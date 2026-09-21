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
from src.engine import DownloadJob, DownloadResult, PlaylistDownloadResult, PlaylistItemResult
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

    def submit(self, job: DownloadJob, on_event=None) -> DownloadResult:
        self.started.set()
        self.release_gate.wait(timeout=10.0)
        if on_event is not None:
            on_event(ProgressEvent(event_type="on_finished", percent=100.0, message="fake done"))
        return DownloadResult(path=Path("/fake/path"), uploader="Fake Uploader", title="Fake Title")


class _BlockingFakePlaylistEngine:
    """Podobny do _BlockingFakeEngine, ale rozróżnia submit()/submit_playlist()
    — testuje, że JobRunner._run() kieruje playlist_scope=="all" do
    submit_playlist(), a NIE do submit() (Faza 2b)."""

    def __init__(self, next_start_index_to_return: int | None = None) -> None:
        self.started = threading.Event()
        self.release_gate = threading.Event()
        self.submit_called = False
        self.submit_playlist_called = False
        self.received_start_index: int | None = None
        self.received_selected_indices: list[int] | None = None
        self.next_start_index_to_return = next_start_index_to_return

    def submit(self, job: DownloadJob, on_event=None) -> DownloadResult:
        self.submit_called = True
        raise AssertionError("submit() nie powinno być wołane dla playlist_scope w ('all', 'selected')")

    def submit_playlist(
        self,
        job: DownloadJob,
        on_event=None,
        start_index: int = 1,
        selected_indices: list[int] | None = None,
    ) -> PlaylistDownloadResult:
        self.submit_playlist_called = True
        self.received_start_index = start_index
        self.received_selected_indices = selected_indices
        self.started.set()
        self.release_gate.wait(timeout=10.0)
        return PlaylistDownloadResult(
            zip_path=Path("/fake/playlist.zip"),
            items=[
                PlaylistItemResult(index=1, title="Wideo 1", status="done"),
                PlaylistItemResult(index=2, title="Wideo 2", status="error", error_message="boom"),
            ],
            playlist_title="Fake Playlist",
            next_start_index=self.next_start_index_to_return,
        )


def test_run_routes_playlist_scope_all_to_submit_playlist_and_carries_items():
    fake_engine = _BlockingFakePlaylistEngine()
    runner = JobRunner(engine=fake_engine)
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="job-playlist-routing",
        playlist_scope="all",
    )

    assert runner.start(job, on_state=events.append) == "started"
    assert fake_engine.started.wait(timeout=5.0)
    fake_engine.release_gate.set()

    assert _wait_until(
        lambda: any(e.event_type == "on_finished" and e.playlist_items is not None for e in events),
        timeout=5.0,
    )

    assert fake_engine.submit_playlist_called is True
    assert fake_engine.submit_called is False

    final_events = [e for e in events if e.event_type == "on_finished"]
    final = final_events[-1]
    assert fake_engine.received_start_index == 1  # domyślne, job.start_index nie ustawiony
    assert fake_engine.received_selected_indices is None
    assert final.result_path == Path("/fake/playlist.zip")
    assert final.playlist_title == "Fake Playlist"
    assert final.result_title == "Fake Playlist"
    assert final.playlist_scope == "all"
    assert final.output_format == "mp4"
    assert [item.status for item in final.playlist_items] == ["done", "error"]


def test_run_routes_playlist_scope_selected_to_submit_playlist_with_indices():
    """2026-09-20 (punkt 4): playlist_scope=="selected" musi trafić do
    submit_playlist() z job.selected_indices, NIE z job.start_index (te
    dwa mechanizmy są wzajemnie wyłączne, patrz engine.py::submit_playlist)."""
    fake_engine = _BlockingFakePlaylistEngine()
    runner = JobRunner(engine=fake_engine)
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="job-playlist-selected-routing",
        playlist_scope="selected",
        selected_indices=[21, 28],
    )

    assert runner.start(job, on_state=events.append) == "started"
    assert fake_engine.started.wait(timeout=5.0)
    fake_engine.release_gate.set()

    assert _wait_until(
        lambda: any(e.event_type == "on_finished" and e.playlist_items is not None for e in events),
        timeout=5.0,
    )

    assert fake_engine.submit_playlist_called is True
    assert fake_engine.submit_called is False
    assert fake_engine.received_selected_indices == [21, 28]

    final = [e for e in events if e.event_type == "on_finished"][-1]
    assert final.playlist_scope == "selected"
    assert final.output_format == "mp4"


def test_run_passes_job_start_index_to_submit_playlist_and_carries_next_start_index():
    """Faza 2c: DownloadJob.start_index (ustawiony przez "Pobierz kolejne
    pozycje" w app.py) musi trafić do submit_playlist() jako start_index=,
    a wynikowe next_start_index musi dotrzeć do finalnego ProgressEvent."""
    fake_engine = _BlockingFakePlaylistEngine(next_start_index_to_return=6)
    runner = JobRunner(engine=fake_engine)
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="video",
        output_format="mp4",
        session_id="test-session",
        job_id="job-playlist-resume-routing",
        playlist_scope="all",
        start_index=5,
    )

    assert runner.start(job, on_state=events.append) == "started"
    assert fake_engine.started.wait(timeout=5.0)
    fake_engine.release_gate.set()

    assert _wait_until(
        lambda: any(e.event_type == "on_finished" and e.playlist_items is not None for e in events),
        timeout=5.0,
    )

    assert fake_engine.received_start_index == 5
    final = [e for e in events if e.event_type == "on_finished"][-1]
    assert final.next_start_index == 6


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


def test_final_on_finished_event_carries_result_path_from_engine():
    """job_runner._run() musi doczepić do finalnego on_finished ścieżkę
    wyniku zwróconą przez engine.submit() — app.py opiera na niej
    wczytanie pliku do RAM, bez zgadywania po katalogu."""
    fake_engine = _BlockingFakeEngine()
    runner = JobRunner(engine=fake_engine)
    events: list[ProgressEvent] = []

    job = DownloadJob(
        url=TEST_VIDEO_URL,
        mode="audio",
        output_format="mp3",
        session_id="test-session",
        job_id="job-result-path",
    )

    assert runner.start(job, on_state=events.append) == "started"
    assert fake_engine.started.wait(timeout=5.0)
    fake_engine.release_gate.set()

    assert _wait_until(
        lambda: any(e.event_type == "on_finished" and e.result_path is not None for e in events),
        timeout=5.0,
    )
    final_events = [e for e in events if e.event_type == "on_finished"]
    assert final_events[-1].result_path == Path("/fake/path")
    assert final_events[-1].result_uploader == "Fake Uploader"
    assert final_events[-1].result_title == "Fake Title"


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
