"""Jedyny moduł w projekcie importujący `yt_dlp` bezpośrednio.

DownloadEngine.submit() jest wywoływalny synchronicznie — integracja
z threading.Thread/Semaphore(MAX_CONCURRENT_JOBS) wchodzi w sesji z app.py.
Zwraca ścieżkę do KONKRETNEGO pliku wynikowego (nie katalogu zadania) —
patrz _resolve_result_path.
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
    subtitle_lang: str | None = None


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


def list_available_subtitles(url: str) -> dict[str, list[str]]:
    """Dostępne języki napisów dla materiału — osobno manualne
    (info_dict['subtitles']) i automatyczne (info_dict['automatic_captions']).

    Bez tego wiele filmów (zwłaszcza nieanglojęzycznych) nie ma ŻADNYCH
    napisów w domyślnym języku yt-dlp (subtitleslangs=["en"])."""
    probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with YoutubeDL(probe_opts) as probe:
        info = probe.extract_info(url, download=False)

    if not info:
        return {"manual": [], "automatic": []}

    manual = sorted((info.get("subtitles") or {}).keys())
    automatic = sorted((info.get("automatic_captions") or {}).keys())
    return {"manual": manual, "automatic": automatic}


class DownloadEngine:
    def submit(self, job: DownloadJob, on_event: OnEventCallback | None = None) -> Path:
        job_dir: Path | None = None
        try:
            if not validate_url(job.url):
                raise InvalidUrlError(job.url)

            self._check_playlist_limit(job.url)

            job_dir = storage.create(job.session_id, job.job_id)

            profile = get_profile(
                job.mode,
                job.output_format,
                audio_bitrate_kbps=job.audio_bitrate_kbps,
                subtitle_lang=job.subtitle_lang,
            )
            ydl_opts = self._build_ydl_opts(job, profile, job_dir, on_event)

            with YoutubeDL(ydl_opts) as ydl:
                # extract_info(download=True) (a nie ydl.download()) — tylko
                # ten wariant zwraca info_dict, z którego wyciągamy
                # RZECZYWISTĄ ścieżkę pliku PO postprocessingu (patrz
                # _resolve_result_path). ydl.download() zwraca wyłącznie
                # kod wyjścia, więc bez tego app.py musiałoby zgadywać
                # nazwę pliku na podstawie outtmpl sprzed konwersji ffmpeg
                # (np. .webm zamiast finalnego .mp3) — dokładnie ten bug.
                info = ydl.extract_info(job.url, download=True)

            result_path = self._resolve_result_path(info)
            if result_path is None:
                raise RuntimeError("Nie udało się ustalić ścieżki pliku wynikowego po pobraniu.")

            # yt-dlp nie zawsze zna finalny rozmiar pliku z góry (np. po
            # transkodowaniu FFmpeg do mp3/flac) — limit sprawdzamy
            # post-factum, na podstawie tego, co faktycznie wylądowało na dysku.
            storage.enforce_size_limit(result_path)

            return result_path

        except Exception as exc:
            message = map_download_error(exc)
            self._emit(on_event, "on_error", 0.0, message)
            if job_dir is not None:
                storage.cleanup(job_dir)
            raise EngineError(message, original_exception=exc) from exc

    @staticmethod
    def _resolve_result_path(info: dict | None) -> Path | None:
        """Wyciąga rzeczywistą ścieżkę pliku wynikowego z info_dict PO
        wszystkich postprocessorach — nigdy nie zgaduje na podstawie
        outtmpl czy zawartości katalogu."""
        if not info:
            return None

        requested_downloads = info.get("requested_downloads") or []
        if requested_downloads:
            filepath = requested_downloads[0].get("filepath")
            if filepath:
                return Path(filepath)

        # Tryb Subtitle (skip_download=True) nie populuje requested_downloads
        # — ścieżka zapisanego pliku napisów jest w requested_subtitles.
        requested_subtitles = info.get("requested_subtitles") or {}
        for subtitle_info in requested_subtitles.values():
            filepath = subtitle_info.get("filepath")
            if filepath:
                return Path(filepath)

        return None

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
