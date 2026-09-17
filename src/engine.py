"""Jedyny moduł w projekcie importujący `yt_dlp` bezpośrednio.

DownloadEngine.submit() jest wywoływalny synchronicznie — integracja
z threading.Thread/Semaphore(MAX_CONCURRENT_JOBS) wchodzi w sesji z app.py.
Zwraca DownloadResult ze ścieżką do KONKRETNEGO pliku wynikowego (nie
katalogu zadania) plus metadane (uploader/title) do budowy nazwy pliku
widocznej dla użytkownika — patrz _resolve_result.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from yt_dlp import YoutubeDL

from src import storage
from src.config import settings
from src.errors import InvalidUrlError, PlaylistTooLargeError, map_download_error
from src.profiles import DownloadProfile, get_profile
from src.progress import ProgressEvent
from src.transcript_cleaner import clean_vtt_to_text, format_paragraphs
from src.validators import validate_url


@dataclass
class DownloadJob:
    url: str
    mode: str
    output_format: str
    session_id: str
    job_id: str
    # Surowa zawartość wgranego cookies.txt (nie ścieżka!) — yt_dlp wymaga
    # ŚCIEŻKI DO PLIKU na dysku w opcji cookiefile, więc submit() zapisuje
    # te bajty do pliku w katalogu roboczym TEGO joba (patrz _write_cookiefile)
    # i przekazuje wynikową ścieżkę dalej. Trzymanie tu tylko bytes (nie
    # ścieżki wyznaczonej wcześniej przez app.py) gwarantuje, że plik cookie
    # żyje wyłącznie w job_dir i jest sprzątany razem z resztą (storage.cleanup),
    # zamiast osierocanego pliku w systemowym katalogu temp.
    cookie_data: bytes | None = None
    audio_bitrate_kbps: int | None = None
    subtitle_lang: str | None = None
    # "single" (domyślnie) — noplaylist=True, ściągane jest WYŁĄCZNIE wideo
    # wskazane przez `v=`, nawet jeśli URL zawiera też `list=` (patrz
    # validators.classify_url). "all" — Faza 2 (jeszcze niezaimplementowana
    # w engine.py): app.py na razie pokazuje dla tego placeholder, nigdy nie
    # tworzy z tym joba, który faktycznie dotarłby do submit().
    playlist_scope: str = "single"


@dataclass
class DownloadResult:
    """Wynik DownloadEngine.submit() — plik PO postprocessingu plus
    metadane materiału, do budowy nazwy pliku widocznej dla użytkownika
    (patrz src/naming.py) bez ponownego odpytywania yt-dlp."""

    path: Path
    uploader: str | None = None
    title: str | None = None


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


def _base_ydl_opts(cookiefile: str | None = None) -> dict:
    """Opcje wspólne dla KAŻDEJ instancji YoutubeDL w tym module — sond
    (list_available_subtitles, _check_playlist_limit) i głównego pobrania
    (_build_ydl_opts). cookiefile musi trafiać do WSZYSTKICH, w jednym
    miejscu, bez duplikowania logiki — inaczej materiał z ograniczeniem
    wiekowym pada już na sondzie, która go nie miała (dokładnie to był bug
    w _check_playlist_limit przed naprawą).

    Próba obejścia github.com/yt-dlp/yt-dlp/issues/17619 przez wymuszenie
    extractor_args player_client=["mweb"] tutaj została WYCOFANA — łamała
    ekstrakcję formatów dla zwykłych żądań bez ograniczenia wiekowego
    (potwierdzone: "No video formats found!" na standardowym wideo
    testowym). Dla treści z ograniczeniem wiekowym, którego cookies nie
    ominą, patrz komunikat _AGE_RESTRICTED_MARKERS w errors.py."""
    opts: dict = {"quiet": True, "no_warnings": True}
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


@contextmanager
def _temp_cookiefile(cookie_data: bytes | None) -> Iterator[str | None]:
    """Zapisuje cookie_data do tymczasowego pliku na czas sond wykonywanych
    POZA kontekstem joba (przed jego utworzeniem — list_available_subtitles
    — więc bez job_dir do zapisania przez DownloadEngine._write_cookiefile).
    Plik usuwany natychmiast po wyjściu z bloku `with`, nie przeżywa sondy —
    w przeciwieństwie do cookiefile właściwego joba, który żyje w job_dir
    do storage.cleanup()."""
    if not cookie_data:
        yield None
        return

    fd, raw_path = tempfile.mkstemp(suffix=".txt", prefix="cookies-probe-")
    path = Path(raw_path)
    try:
        with open(fd, "wb") as f:
            f.write(cookie_data)
        yield str(path)
    finally:
        path.unlink(missing_ok=True)


def list_available_subtitles(url: str, cookie_data: bytes | None = None) -> dict[str, list[str]]:
    """Dostępne języki napisów dla materiału — osobno manualne
    (info_dict['subtitles']) i automatyczne (info_dict['automatic_captions']).

    Bez tego wiele filmów (zwłaszcza nieanglojęzycznych) nie ma ŻADNYCH
    napisów w domyślnym języku yt-dlp (subtitleslangs=["en"]).

    Ta sonda ma ten sam problem, jaki miał _check_playlist_limit przed
    naprawą: dla materiału z ograniczeniem wiekowym YouTube wymaga cookies
    już na etapie SAMEJ próby odczytu metadanych (nie tylko pobrania) — bez
    cookiefile tutaj UI nigdy nie pokaże listy języków, niezależnie od tego,
    czy użytkownik wgrał cookies.txt do właściwego pobrania."""
    with _temp_cookiefile(cookie_data) as cookiefile_path:
        probe_opts = _base_ydl_opts(cookiefile_path)
        probe_opts["skip_download"] = True

        with YoutubeDL(probe_opts) as probe:
            info = probe.extract_info(url, download=False)

    if not info:
        return {"manual": [], "automatic": []}

    manual = sorted((info.get("subtitles") or {}).keys())
    automatic = sorted((info.get("automatic_captions") or {}).keys())
    return {"manual": manual, "automatic": automatic}


class DownloadEngine:
    def submit(self, job: DownloadJob, on_event: OnEventCallback | None = None) -> DownloadResult:
        job_dir: Path | None = None
        try:
            if not validate_url(job.url):
                raise InvalidUrlError(job.url)

            job_dir = storage.create(job.session_id, job.job_id)
            cookiefile_path = self._write_cookiefile(job.cookie_data, job_dir)

            # Sonda limitu playlisty MUSI dostać te same cookies co główne
            # pobranie — dla materiału z ograniczeniem wiekowym błąd "Sign in
            # to confirm your age" pojawia się już na etapie tej sondy
            # (extract_flat=True nie omija weryfikacji wieku dla pojedynczego
            # wideo), więc bez cookiefile TUTAJ żądanie nigdy nie dociera do
            # dalszej części submit(), która cookies faktycznie miała.
            self._check_playlist_limit(job.url, cookiefile_path)

            profile = get_profile(
                job.mode,
                job.output_format,
                audio_bitrate_kbps=job.audio_bitrate_kbps,
                subtitle_lang=job.subtitle_lang,
            )
            ydl_opts = self._build_ydl_opts(job, profile, job_dir, on_event, cookiefile_path)

            with YoutubeDL(ydl_opts) as ydl:
                # extract_info(download=True) (a nie ydl.download()) — tylko
                # ten wariant zwraca info_dict, z którego wyciągamy
                # RZECZYWISTĄ ścieżkę pliku PO postprocessingu (patrz
                # _resolve_result_path). ydl.download() zwraca wyłącznie
                # kod wyjścia, więc bez tego app.py musiałoby zgadywać
                # nazwę pliku na podstawie outtmpl sprzed konwersji ffmpeg
                # (np. .webm zamiast finalnego .mp3) — dokładnie ten bug.
                info = ydl.extract_info(job.url, download=True)

            result = self._resolve_result(info)
            if result is None:
                raise RuntimeError("Nie udało się ustalić ścieżki pliku wynikowego po pobraniu.")

            if job.mode == "transcript":
                # Ta sama ścieżka resolvowania co Subtitle (profil transcript
                # zawsze wymusza VTT — patrz profiles.py) — różnica jest w
                # tym, co użytkownik dostaje: surowe napisy nigdy nie
                # docierają na wierzch, tylko oczyszczony tekst.
                result = self._finalize_transcript(result)

            # yt-dlp nie zawsze zna finalny rozmiar pliku z góry (np. po
            # transkodowaniu FFmpeg do mp3/flac) — limit sprawdzamy
            # post-factum, na podstawie tego, co faktycznie wylądowało na dysku.
            storage.enforce_size_limit(result.path)

            return result

        except Exception as exc:
            message = map_download_error(exc)
            self._emit(on_event, "on_error", 0.0, message)
            if job_dir is not None:
                storage.cleanup(job_dir)
            raise EngineError(message, original_exception=exc) from exc

    @staticmethod
    def _resolve_result(info: dict | None) -> DownloadResult | None:
        """Wyciąga rzeczywistą ścieżkę pliku wynikowego z info_dict PO
        wszystkich postprocessorach — nigdy nie zgaduje na podstawie
        outtmpl czy zawartości katalogu — plus uploader/title do nazwy
        pliku widocznej dla użytkownika (src/naming.py).

        Sprawdzamy .exists() dla każdego kandydata: przy skip_download=True
        (tryb Subtitle) yt-dlp i tak wypełnia `requested_downloads` wpisem
        z hipotetyczną ścieżką pliku medialnego, który NIGDY nie został
        zapisany (download jest pominięty) — bez tej weryfikacji
        _resolve_result zwracał tę fantomową ścieżkę zamiast prawdziwego
        pliku napisów z `requested_subtitles`."""
        if not info:
            return None

        path: Path | None = None

        requested_downloads = info.get("requested_downloads") or []
        if requested_downloads:
            filepath = requested_downloads[0].get("filepath")
            if filepath and Path(filepath).exists():
                path = Path(filepath)

        if path is None:
            # Tryb Subtitle (skip_download=True) — ścieżka zapisanego
            # pliku napisów jest w requested_subtitles.
            requested_subtitles = info.get("requested_subtitles") or {}
            for subtitle_info in requested_subtitles.values():
                filepath = subtitle_info.get("filepath")
                if filepath and Path(filepath).exists():
                    path = Path(filepath)
                    break

        if path is None:
            return None

        return DownloadResult(path=path, uploader=info.get("uploader"), title=info.get("title"))

    @staticmethod
    def _finalize_transcript(result: DownloadResult) -> DownloadResult:
        """Zamienia surowy plik VTT (rozwiązany przez _resolve_result tą samą
        ścieżką co Subtitle) na czysty tekst — użytkownik trybu Transkrypt
        dostaje WYŁĄCZNIE .txt, nigdy surowych napisów z timestampami."""
        vtt_content = result.path.read_text(encoding="utf-8")
        text = clean_vtt_to_text(vtt_content)
        # Podział na akapity działa na JUŻ OCZYSZCZONYM tekście (po
        # deduplikacji) — nigdy na surowym VTT, żadnego zgadywania granic
        # po znacznikach czasu (patrz transcript_cleaner.format_paragraphs).
        text = format_paragraphs(text)

        txt_path = result.path.with_suffix(".txt")
        txt_path.write_text(text, encoding="utf-8")
        result.path.unlink(missing_ok=True)

        return DownloadResult(path=txt_path, uploader=result.uploader, title=result.title)

    @staticmethod
    def _write_cookiefile(cookie_data: bytes | None, job_dir: Path) -> str | None:
        """yt_dlp wymaga ŚCIEŻKI DO PLIKU na dysku w opcji cookiefile — nie
        przyjmuje bajtów/UploadedFile bezpośrednio. Zapisuje do job_dir (nie
        systemowego katalogu temp), żeby plik zniknął wraz z resztą przy
        storage.cleanup(job_dir) — zarówno po sukcesie, jak i po błędzie
        (patrz except w submit()) — cookies sesji nie mają powodu przeżyć
        joba, który je zużył."""
        if not cookie_data:
            return None
        cookiefile_path = job_dir / "cookies.txt"
        cookiefile_path.write_bytes(cookie_data)
        return str(cookiefile_path)

    def _check_playlist_limit(self, url: str, cookiefile: str | None = None) -> None:
        # extract_flat=True: enumeruje pozycje playlisty bez rozwiązywania
        # pełnych metadanych każdego wideo — szybka walidacja PRZED pobraniem.
        probe_opts = _base_ydl_opts(cookiefile)
        probe_opts["skip_download"] = True
        probe_opts["extract_flat"] = True
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
        cookiefile_path: str | None = None,
    ) -> dict:
        outtmpl_template = profile.extra_opts.get("outtmpl_template", "%(title)s.%(ext)s")
        outtmpl = str(job_dir / outtmpl_template)

        ydl_opts = _base_ydl_opts(cookiefile_path)
        ydl_opts.update(
            {
                "outtmpl": outtmpl,
                "retries": 3,
                "fragment_retries": 3,
                "progress_hooks": [self._make_progress_hook(on_event)],
                "noprogress": True,
                # Bez tego URL zawierający jednocześnie v= i list= (typowy
                # link "autoplay z listy") ściągnąłby domyślnie (yt-dlp:
                # noplaylist=False) CAŁĄ playlistę, mimo że użytkownik chciał
                # jedno wideo — _resolve_result zakłada jeden plik wynikowy
                # (requested_downloads[0]), więc przy wielu ściągniętych
                # plikach cicho zwracał None → "Nie udało się ustalić
                # ścieżki..." (dokładnie ten bug).
                "noplaylist": job.playlist_scope == "single",
            }
        )

        if profile.selector:
            ydl_opts["format"] = profile.selector

        if profile.postprocessors:
            ydl_opts["postprocessors"] = profile.postprocessors

        extra_opts = {
            key: value for key, value in profile.extra_opts.items() if key != "outtmpl_template"
        }
        ydl_opts.update(extra_opts)

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
