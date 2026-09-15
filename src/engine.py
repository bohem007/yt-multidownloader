"""Jedyny moduł w projekcie importujący `yt_dlp` bezpośrednio.

DownloadEngine.submit() jest wywoływalny synchronicznie — integracja
z threading.Thread/Semaphore(MAX_CONCURRENT_JOBS) wchodzi w sesji z app.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL

from src import storage
from src.config import settings
from src.errors import InvalidUrlError, PlaylistTooLargeError, map_download_error
from src.profiles import DownloadProfile, get_profile
from src.progress import ProgressEvent
from src.validators import validate_url


@dataclass
class DownloadJob:
    url: str
    mode: str
    output_format: str
    session_id: str
    job_id: str
    cookiefile: str | None = None
    audio_bitrate_kbps: int | None = None


class EngineError(Exception):
    """Czytelny błąd silnika — komunikat już przetworzony przez errors.map_download_error.

    `original_exception` niesie jawnie oryginalny przechwycony wyjątek
    (InvalidUrlError, PlaylistTooLargeError, yt_dlp.utils.DownloadError...),
    żeby wołający mógł po typie rozróżnić przyczynę bez zaglądania w
    __cause__ (który wciąż jest ustawiany przez `raise ... from exc`).
    """

    def __init__(self, message: str, original_exception: Exception | None = None) -> None:
        super().__init__(message)
        self.original_exception = original_exception


OnEventCallback = Callable[[ProgressEvent], None]


class DownloadEngine:
    def submit(self, job: DownloadJob, on_event: OnEventCallback | None = None) -> Path:
        job_dir: Path | None = None
        try:
            if not validate_url(job.url):
                raise InvalidUrlError(job.url)

            self._check_playlist_limit(job.url)

            job_dir = storage.create(job.session_id, job.job_id)

            profile = get_profile(job.mode, job.output_format, audio_bitrate_kbps=job.audio_bitrate_kbps)
            ydl_opts = self._build_ydl_opts(job, profile, job_dir, on_event)

            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([job.url])

            # yt-dlp nie zawsze zna finalny rozmiar pliku z góry (np. po
            # transkodowaniu FFmpeg do mp3/flac) — limit sprawdzamy
            # post-factum, na podstawie tego, co faktycznie wylądowało na dysku.
            storage.enforce_size_limit(job_dir)

            return job_dir

        except Exception as exc:
            message = map_download_error(exc)
            self._emit(on_event, "on_error", 0.0, message)
            if job_dir is not None:
                storage.cleanup(job_dir)
            raise EngineError(message, original_exception=exc) from exc

    def _check_playlist_limit(self, url: str) -> None:
        # extract_flat=True: enumeruje pozycje playlisty bez rozwiązywania
        # pełnych metadanych każdego wideo — szybka walidacja PRZED pobraniem.
        probe_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
        }
        with YoutubeDL(probe_opts) as probe:
            info = probe.extract_info(url, download=False)

        entries = info.get("entries") if info else None
        if entries is None:
            return

        item_count = sum(1 for entry in entries if entry is not None)
        if item_count > settings.max_playlist_items:
            raise PlaylistTooLargeError(
                f"playlist ma {item_count} pozycji, limit to {settings.max_playlist_items}"
            )

    def _build_ydl_opts(
        self,
        job: DownloadJob,
        profile: DownloadProfile,
        job_dir: Path,
        on_event: OnEventCallback | None,
    ) -> dict:
        outtmpl_template = profile.extra_opts.get("outtmpl_template", "%(title)s.%(ext)s")
        outtmpl = str(job_dir / outtmpl_template)

        ydl_opts: dict = {
            "outtmpl": outtmpl,
            "retries": 3,
            "fragment_retries": 3,
            "progress_hooks": [self._make_progress_hook(on_event)],
            "quiet": True,
            "noprogress": True,
        }

        if profile.selector:
            ydl_opts["format"] = profile.selector

        if profile.postprocessors:
            ydl_opts["postprocessors"] = profile.postprocessors

        extra_opts = {
            key: value for key, value in profile.extra_opts.items() if key != "outtmpl_template"
        }
        ydl_opts.update(extra_opts)

        if job.cookiefile:
            ydl_opts["cookiefile"] = job.cookiefile

        return ydl_opts

    def _make_progress_hook(self, on_event: OnEventCallback | None) -> Callable[[dict], None]:
        started = False

        def hook(d: dict) -> None:
            nonlocal started
            status = d.get("status")

            if status == "downloading":
                if not started:
                    started = True
                    self._emit(on_event, "on_start", 0.0, "Pobieranie rozpoczęte")

                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total else 0.0
                self._emit(on_event, "on_progress", percent, f"Pobrano {downloaded} B")

            elif status == "finished":
                self._emit(on_event, "on_finished", 100.0, "Pobieranie zakończone, przetwarzanie...")

        return hook

    @staticmethod
    def _emit(on_event: OnEventCallback | None, event_type: str, percent: float, message: str) -> None:
        if on_event is not None:
            on_event(ProgressEvent(event_type=event_type, percent=percent, message=message))
