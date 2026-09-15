"""UI Streamlit — Warstwa 2 specyfikacji.

Nie zawiera logiki yt-dlp — tylko renderuje stan (przez SessionState) i
woła inne warstwy (job_runner/engine/db/storage). Jedyne miejsce, gdzie
ten plik dotyka `st.session_state` bezpośrednio, jest konstrukcja
`SessionState(st.session_state)` poniżej — cała reszta idzie przez tę
klasę, zgodnie z CLAUDE.md.
"""

from __future__ import annotations

import mimetypes
import queue as queue_module
import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st

from src import storage
from src.config import settings
from src.db import Database
from src.engine import DownloadJob
from src.errors import InvalidUrlError, map_download_error
from src.job_runner import JobRunner
from src.progress import ProgressEvent
from src.session import SessionState
from src.validators import validate_url

MODE_LABELS = {
    "video": "Video (MP4)",
    "audio": "Audio (MP3 / FLAC)",
    "playlist": "Playlist",
    "subtitle": "Napisy (SRT / VTT)",
    "transcript": "Transkrypt (TXT)",
}
MODE_KEYS_BY_LABEL = {label: key for key, label in MODE_LABELS.items()}
READY_MODES = {"video", "audio", "subtitle"}


@st.cache_resource
def get_database() -> Database:
    """Jedna, współdzielona instancja Database per proces (pooled connection)."""
    return Database()


def _find_result_file(job_dir: Path) -> Path | None:
    """Zwraca największy plik w katalogu zadania (nasze profile produkują
    dokładnie jeden plik wynikowy dla video/audio/subtitle)."""
    if not job_dir.is_dir():
        return None
    files = [entry for entry in job_dir.iterdir() if entry.is_file()]
    if not files:
        return None
    return max(files, key=lambda entry: entry.stat().st_size)


def _guess_mime(file_name: str | None) -> str:
    if not file_name:
        return "application/octet-stream"
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"


def _log_job_finish(
    state: SessionState,
    *,
    status: str,
    duration_ms: int,
    file_size_bytes: int | None = None,
    error_message: str | None = None,
) -> None:
    if state.db_job_id is None:
        return
    try:
        get_database().log_job_finish(
            state.db_job_id,
            status=status,
            duration_ms=duration_ms,
            file_size_bytes=file_size_bytes,
            error_message=error_message,
        )
    except Exception:
        # Baza może być w cold-starcie / przejściowo niedostępna — logowanie
        # historii nie może wywalić UI (aplikacja ma działać bez niej).
        pass


@st.fragment(run_every=0.5)
def _render_progress(state: SessionState) -> None:
    """Odpytuje kolejkę zdarzeń z wątku w tle i aktualizuje SessionState
    WYŁĄCZNIE z wątku głównego (Warstwa 4) — callback z JobRunner.start()
    tylko wrzuca ProgressEvent do kolejki, nigdy nie dotyka stanu sam."""
    job_queue = state.queue
    terminal_event: ProgressEvent | None = None

    if job_queue is not None:
        while True:
            try:
                event = job_queue.get_nowait()
            except queue_module.Empty:
                break

            if event.event_type in ("on_start", "on_progress"):
                state.set_progress(event.percent, event.message)
            elif event.event_type in ("on_finished", "on_error"):
                # Ostatni terminal event w kolejce wygrywa — patrz komentarz
                # w job_runner.py o dwóch "on_finished" (koniec pobierania
                # vs. koniec całego submit(), łącznie z postprocessingiem).
                terminal_event = event

    if terminal_event is not None:
        duration_ms = int((time.monotonic() - (state.started_at or time.monotonic())) * 1000)

        if terminal_event.event_type == "on_finished":
            job_dir = Path(settings.storage_base_dir) / state.session_id / (state.job_id or "")
            result_file = _find_result_file(job_dir)

            if result_file is None:
                state.set_error("Zadanie zakończone, ale nie znaleziono pliku wynikowego.")
                storage.cleanup(job_dir)
                _log_job_finish(
                    state, status="error", duration_ms=duration_ms, error_message=state.error_message
                )
            else:
                data = result_file.read_bytes()
                # Wczytane do RAM — katalog tymczasowy natychmiast usuwamy,
                # zgodnie z Warstwą 10 (brak trwałych plików na serwerze).
                storage.cleanup(job_dir)
                state.set_done(job_dir, data=data, file_name=result_file.name)
                _log_job_finish(state, status="done", duration_ms=duration_ms, file_size_bytes=len(data))
        else:
            state.set_error(terminal_event.message)
            _log_job_finish(
                state, status="error", duration_ms=duration_ms, error_message=terminal_event.message
            )

        st.rerun()
        return

    if state.status == "running":
        st.progress(min(state.percent, 100.0) / 100, text=state.message or "Pobieranie...")


def _render_result(state: SessionState) -> None:
    if state.status == "error":
        st.error(state.error_message or "Wystąpił nieoczekiwany błąd.")
        return

    if state.result_data is None:
        st.error("Brak danych wynikowych.")
        return

    st.success("Pobieranie zakończone.")
    st.download_button(
        "Zapisz plik",
        data=state.result_data,
        file_name=state.result_file_name or "output",
        mime=_guess_mime(state.result_file_name),
        key="download_result_button",
    )


st.set_page_config(page_title="YT MultiDownloader", page_icon="📥")

state = SessionState(st.session_state)
runner = JobRunner()

st.title("YT MultiDownloader")

if settings.environment == "production":
    st.info(
        "Pierwsze żądanie po dłuższej bezczynności może potrwać dłużej "
        "(cold start kontenera / bazy danych)."
    )

tab_download, tab_history = st.tabs(["Pobieranie", "Historia"])

with tab_download:
    url = st.text_input(
        "URL", placeholder="https://www.youtube.com/watch?v=...", key="url_input"
    )

    mode_label = st.selectbox("Tryb", list(MODE_LABELS.values()), key="mode_select")
    mode = MODE_KEYS_BY_LABEL[mode_label]

    output_format = "mp4"
    audio_bitrate_kbps: int | None = None

    if mode == "audio":
        audio_format_label = st.selectbox("Format audio", ["MP3", "FLAC"], key="audio_format_select")
        output_format = audio_format_label.lower()

        if output_format == "mp3":
            audio_bitrate_kbps = st.slider(
                "Bitrate MP3 (kbps)",
                min_value=64,
                max_value=320,
                value=192,
                step=32,
                key="bitrate_slider",
            )
        else:
            st.caption(
                "FLAC z YouTube to transkodowanie z lossy źródła (Opus/AAC) — "
                "to nie jest realny wzrost jakości."
            )

    elif mode == "subtitle":
        subtitle_format_label = st.selectbox("Format napisów", ["SRT", "VTT"], key="subtitle_format_select")
        output_format = subtitle_format_label.lower()

    elif mode == "playlist":
        st.caption(f"Limit playlisty: maksymalnie {settings.max_playlist_items} pozycji.")
        st.info("Tryb w przygotowaniu — wymaga jeszcze pakowania ZIP w pamięci.")

    elif mode == "transcript":
        st.info("Tryb w przygotowaniu — wymaga jeszcze czyszczenia napisów (transcript_cleaner.py).")

    cookie_upload = st.file_uploader(
        "cookies.txt (opcjonalnie — pomaga ominąć bot-check YouTube)",
        type=["txt"],
        key="cookies_uploader",
    )
    cookiefile_path: str | None = None
    if cookie_upload is not None:
        cookiefile_path = str(Path(tempfile.gettempdir()) / f"cookies-{state.session_id}.txt")
        Path(cookiefile_path).write_bytes(cookie_upload.getvalue())

    job_in_progress = state.status == "running"
    download_disabled = mode not in READY_MODES or not url or job_in_progress

    if st.button("Pobierz", key="download_button", disabled=download_disabled):
        if not validate_url(url):
            st.error(map_download_error(InvalidUrlError(url)))
        elif not runner.is_slot_available():
            st.warning(
                f"Wszystkie {settings.max_concurrent_jobs} miejsca pobierania są zajęte "
                "— spróbuj ponownie za chwilę."
            )
        else:
            job_id = str(uuid.uuid4())

            try:
                db_job_id = get_database().log_job_start(
                    url,
                    mode,
                    output_format,
                    # TODO(Warstwa 11a): realny hash IP wymaga nagłówka z
                    # proxy HF Spaces, którego jeszcze nie odczytujemy —
                    # placeholder do czasu implementacji rate limitingu.
                    client_ip_hash="local-dev",
                )
            except Exception:
                db_job_id = None

            job_queue = state.begin_job(job_id)
            if db_job_id is not None:
                state.set_db_job_id(db_job_id)

            job = DownloadJob(
                url=url,
                mode=mode,
                output_format=output_format,
                session_id=state.session_id,
                job_id=job_id,
                cookiefile=cookiefile_path,
                audio_bitrate_kbps=audio_bitrate_kbps,
            )

            def on_state(event: ProgressEvent, _queue: "queue_module.Queue" = job_queue) -> None:
                # Wołane Z WĄTKU W TLE — wyłącznie wrzucenie do kolejki,
                # NIGDY bezpośrednia modyfikacja SessionState/session_state
                # (Streamlit nie gwarantuje tu bezpieczeństwa wątkowego).
                _queue.put(event)

            status = runner.start(job, on_state=on_state)
            if status == "queued":
                # Rzadki wyścig z is_slot_available() (nierezerwujące
                # sprawdzenie) — inny job zdążył zająć slot pierwszy.
                st.warning("Zadanie czeka w kolejce — spróbuj ponownie za chwilę.")
                state.reset()
            else:
                st.rerun()

    if state.status == "running":
        _render_progress(state)
    elif state.is_terminal():
        _render_result(state)

with tab_history:
    try:
        history = get_database().get_recent_history(limit=20)
    except Exception:
        st.warning("Historia zadań jest tymczasowo niedostępna (baza może się właśnie wybudzać z uśpienia).")
    else:
        if not history:
            st.caption("Brak zapisanych zadań.")
        else:
            st.dataframe(history, use_container_width=True)
