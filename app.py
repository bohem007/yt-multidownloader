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
from src.engine import DownloadJob, list_available_subtitles
from src.errors import InvalidUrlError, map_download_error
from src.job_runner import JobRunner
from src.naming import build_display_filename
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
READY_MODES = {"video", "audio", "subtitle", "transcript"}
# YouTube automatic_captions zawiera pełną listę celów auto-tłumaczenia
# (potrafi być >150 kodów) — dla auto-napisów pokazujemy tylko te języki,
# niezależnie od tego, czy dla danego filmu istnieją też manualne napisy.
AUTOMATIC_SUBTITLE_LANGS = ("pl", "de", "en")


@st.cache_resource
def get_database() -> Database:
    """Jedna, współdzielona instancja Database per proces (pooled connection)."""
    return Database()


@st.cache_data(ttl=300, show_spinner="Sprawdzanie dostępnych napisów...")
def _cached_list_available_subtitles(url: str) -> dict[str, list[str]]:
    """Cache po URL — bez tego zapytanie do YouTube powtarzałoby się przy
    każdym rerunie skryptu (Streamlit reruje cały plik na każdą interakcję)."""
    return list_available_subtitles(url)


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

            if event.event_type == "on_finished" and event.result_path is None:
                # Wczesny "on_finished" z progress_hooks w engine.py — sam
                # DOWNLOAD się skończył, ale postprocessing (ffmpeg/zapis
                # napisów) jeszcze trwa, więc job_runner.py NIE dołączył
                # jeszcze prawdziwego result_path (patrz jego komentarz).
                # To NIE jest terminal — inaczej UI kończyłoby z "nie
                # znaleziono pliku wynikowego" niemal przy każdym pobraniu,
                # bo ten event prawie zawsze trafia do kolejki, zanim
                # postprocessing zdąży się zakończyć.
                state.set_progress(event.percent, event.message)
            elif event.event_type in ("on_start", "on_progress"):
                state.set_progress(event.percent, event.message)
            elif event.event_type in ("on_finished", "on_error"):
                # Ostatni terminal event w kolejce wygrywa — dociera tu
                # tylko prawdziwie terminalny "on_finished" (result_path
                # ustawiony przez job_runner.py) albo "on_error".
                terminal_event = event

    if terminal_event is not None:
        duration_ms = int((time.monotonic() - (state.started_at or time.monotonic())) * 1000)

        if terminal_event.event_type == "on_finished":
            # Prawdziwa ścieżka pliku PO postprocessingu, zwrócona przez
            # yt_dlp (engine.py) — nigdy zgadywana z zawartości katalogu
            # (to była przyczyna bugu: .webm serwowane zamiast .mp3).
            result_file = terminal_event.result_path

            if result_file is None or not result_file.exists():
                state.set_error("Zadanie zakończone, ale nie znaleziono pliku wynikowego.")
                if result_file is not None:
                    storage.cleanup(result_file.parent)
                _log_job_finish(
                    state, status="error", duration_ms=duration_ms, error_message=state.error_message
                )
            else:
                data = result_file.read_bytes()
                # Wczytane do RAM — katalog tymczasowy natychmiast usuwamy,
                # zgodnie z Warstwą 10 (brak trwałych plików na serwerze).
                storage.cleanup(result_file.parent)
                state.set_done(
                    result_file,
                    data=data,
                    file_name=result_file.name,
                    uploader=terminal_event.result_uploader,
                    title=terminal_event.result_title,
                )
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

    # Nazwa widoczna dla użytkownika ({Uploader}-{Tytuł}[.{jezyk}].{ext})
    # jest NIEZALEŻNA od wewnętrznej nazwy pliku na dysku serwera (patrz
    # src/naming.py) — ta ostatnia i tak już nie istnieje (usunięta zaraz
    # po wczytaniu do RAM), result_file_name służy tu tylko do ustalenia
    # prawdziwego rozszerzenia i do zgadywania MIME.
    ext = Path(state.result_file_name or "").suffix.lstrip(".") or "bin"
    display_name = build_display_filename(
        state.result_uploader or "",
        state.result_title or (state.result_file_name or "download"),
        ext,
        lang=state.subtitle_lang,
    )

    st.success("Pobieranie zakończone.")
    st.download_button(
        "Zapisz plik",
        data=state.result_data,
        file_name=display_name,
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
    # disabled=state.url_locked celowo czyta wartość USTAWIONĄ NA KOŃCU
    # POPRZEDNIEGO przebiegu, nie przeliczoną tutaj na nowo: Streamlit nie
    # przyjmuje nowej wartości widgetu, jeśli w TYM SAMYM przebiegu jest on
    # renderowany jako disabled=True (przetestowane empirycznie przez
    # AppTest — próba przeliczenia "na żywo" powodowała, że wpisany URL
    # nigdy nie był przyjmowany). Blokada włącza się więc z jednorenderowym
    # opóźnieniem: widoczna dopiero przy NASTĘPNEJ interakcji po wpisaniu
    # URL — to jest poprawne zachowanie Streamlit, nie błąd.
    url = st.text_input(
        "URL",
        placeholder="https://www.youtube.com/watch?v=...",
        key="url_input",
        disabled=state.url_locked,
    )
    # Blokada włącza się, gdy URL jest wypełniony — jedyny sposób jego
    # odblokowania to przycisk "Nowy URL" niżej (pełny reset).
    state.set_url_locked(bool(url))

    mode_label = st.selectbox("Tryb", list(MODE_LABELS.values()), key="mode_select")
    mode = MODE_KEYS_BY_LABEL[mode_label]

    output_format = "mp4"
    audio_bitrate_kbps: int | None = None
    subtitle_lang: str | None = None
    subtitle_lang_missing = False

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

    elif mode in ("subtitle", "transcript"):
        if mode == "subtitle":
            subtitle_format_label = st.selectbox(
                "Format napisów", ["SRT", "VTT"], key="subtitle_format_select"
            )
            output_format = subtitle_format_label.lower()
        else:
            # Transkrypt zawsze czyści VTT do czystego tekstu wewnętrznie
            # (patrz profiles.py::_transcript_profile) — nie pytamy o
            # format napisów, użytkownik i tak dostaje tylko .txt.
            output_format = "txt"

        if url:
            try:
                available = _cached_list_available_subtitles(url)
            except Exception:
                st.warning("Nie udało się sprawdzić dostępnych napisów dla tego adresu.")
                subtitle_lang_missing = True
            else:
                # automatic_captions z YouTube zawiera pełną listę CELÓW
                # auto-tłumaczenia (potrafi być >150 kodów), nie tylko
                # natywny język auto-napisów — bez ograniczenia do
                # wybranych języków selectbox pokazywałby praktycznie
                # każdy język świata. Manualne napisy pokazujemy zawsze
                # w całości, bez duplikatów.
                allowed_automatic = [
                    lang for lang in AUTOMATIC_SUBTITLE_LANGS if lang in available["automatic"]
                ]
                lang_options = list(dict.fromkeys(available["manual"] + allowed_automatic))
                if not lang_options:
                    st.warning("Nie znaleziono żadnych napisów dla tego materiału.")
                    subtitle_lang_missing = True
                else:
                    subtitle_lang = st.selectbox("Język napisów", lang_options, key="subtitle_lang_select")
        else:
            subtitle_lang_missing = True

    elif mode == "playlist":
        st.caption(f"Limit playlisty: maksymalnie {settings.max_playlist_items} pozycji.")
        st.info("Tryb Playlist jest w przygotowaniu — wkrótce dostępny.")

    # Zmiana trybu/formatu przy URL wciąż wypełnionym chowa wynik/błąd
    # POPRZEDNIEGO zadania (i "Zapisz plik") — ale nie dotyka pola URL.
    # Tylko jeśli jest faktycznie coś do wyczyszczenia (is_terminal()) —
    # w trakcie pobierania (status "running") zmiana widgetów nie ma tu
    # znaczenia, bo są zablokowane niżej przez download_disabled/inne joby.
    current_mode_format = (mode, output_format)
    if state.is_terminal() and state.last_mode_format not in (None, current_mode_format):
        state.clear_result()
    state.set_last_mode_format(current_mode_format)

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
    subtitle_blocked = mode in ("subtitle", "transcript") and subtitle_lang_missing
    download_disabled = mode not in READY_MODES or not url or job_in_progress or subtitle_blocked
    # "Nowy URL" nie może przerwać aktywnego pobierania — zerwałoby to
    # wątek w tle i zostawiłoby niezwolniony permit semafora współbieżności.
    new_url_disabled = not url or job_in_progress

    def _start_new_url() -> None:
        # on_click (nie st.rerun() po zwykłym if-bloku): callback wykonuje
        # się PRZED narysowaniem widgetów w tym samym przebiegu, więc
        # czyszczenie st.session_state["url_input"] tutaj jest bezpieczne —
        # zrobione PO tym, jak text_input() już narysuje pole w tym
        # przebiegu, Streamlit rzuciłby wyjątkiem ("cannot be modified
        # after widget ... is instantiated").
        state.reset()
        st.session_state["url_input"] = ""

    col_download, col_new_url = st.columns(2)
    with col_download:
        download_clicked = st.button(
            "Pobierz", key="download_button", disabled=download_disabled, width="stretch"
        )
    with col_new_url:
        st.button(
            "Nowy URL",
            key="new_url_button",
            disabled=new_url_disabled,
            on_click=_start_new_url,
            width="stretch",
        )

    if download_clicked:
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

            job_queue = state.begin_job(job_id, subtitle_lang=subtitle_lang)
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
                subtitle_lang=subtitle_lang,
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
            st.dataframe(history)
