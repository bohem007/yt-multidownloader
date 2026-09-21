"""UI Streamlit — Warstwa 2 specyfikacji.

SKRYPT UI i cel testów (AppTest). NIE uruchamiaj go wprost — serwer startuje
przez asgi_app.py (`uv run streamlit run asgi_app.py`), który wczytuje ten
plik i dokłada trasę HTTP pobierania dużych plików (src/downloads.py).

Nie zawiera logiki yt-dlp — tylko renderuje stan (przez SessionState) i
woła inne warstwy (job_runner/engine/db/storage). Jedyne miejsce, gdzie
ten plik dotyka `st.session_state` bezpośrednio, jest konstrukcja
`SessionState(st.session_state)` poniżej — cała reszta idzie przez tę
klasę, zgodnie z CLAUDE.md.
"""

from __future__ import annotations

import logging
import mimetypes
import queue as queue_module
import re
import time
import uuid
from pathlib import Path

import streamlit as st

# Defensywne logowanie nieoczekiwanych błędów sondy — bez tego wyjątki
# w blokach `except Exception: ... = None` giną bez śladu (sondy sieciowe
# do YouTube mogą zawodzić z wielu przyczyn: timeout, 429, zmiana API —
# warto to widzieć w logach, nie tylko przy jednorazowej diagnozie).
logger = logging.getLogger(__name__)

from src import downloads, storage
from src.config import settings
from src.db import Database
from src.engine import (
    DownloadJob,
    PlaylistSnapshot,
    count_playlist_items,
    list_available_subtitles,
    resolve_representative_video_url,
    snapshot_playlist,
)
from src.errors import InvalidUrlError, map_download_error
from src.job_runner import JobRunner
from src.naming import build_display_filename
from src.progress import ProgressEvent
from src.session import SessionState
from src.validators import classify_url, is_mix_playlist_url, validate_url

MODE_LABELS = {
    "video": "Video (MP4)",
    "audio": "Audio (MP3 / FLAC)",
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
def _cached_list_available_subtitles(
    url: str, cookie_data: bytes | None
) -> dict[str, list[str]]:
    """Cache po (URL, cookie_data) — bez tego zapytanie do YouTube powtarzałoby
    się przy każdym rerunie skryptu (Streamlit reruje cały plik na każdą
    interakcję). cookie_data w kluczu cache: dla materiału z ograniczeniem
    wiekowym lista języków zależy od tego, czy sonda miała cookies."""
    return list_available_subtitles(url, cookie_data=cookie_data)


@st.cache_data(ttl=300, show_spinner="Sprawdzanie liczby pozycji playlisty...")
def _cached_count_playlist_items(url: str, cookie_data: bytes | None) -> int | None:
    """Ten sam wzorzec cache co _cached_list_available_subtitles — bez tego
    sonda extract_flat powtarzałaby się przy każdym rerunie skryptu."""
    return count_playlist_items(url, cookie_data=cookie_data)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_resolve_representative_video_url(url: str, cookie_data: bytes | None) -> str | None:
    """Faza 2b: dla playlist_scope=="all" sonda języka napisów
    (_cached_list_available_subtitles) nie może dostać surowego URL-a
    playlisty — patrz resolve_representative_video_url w engine.py.
    show_spinner=False: to wewnętrzny krok tej samej operacji, spinner
    sondy napisów niżej już informuje użytkownika o oczekiwaniu."""
    return resolve_representative_video_url(url, cookie_data=cookie_data)


def _get_or_create_mix_snapshot(
    state: SessionState, url: str, cookie_data: bytes | None
) -> PlaylistSnapshot:
    """Migawka listy Mix/Radio dla `url` — z st.session_state (przez
    SessionState), NIE z st.cache_data: cache_data jest współdzielony między
    sesjami i kluczowany po TTL, a migawka ma być prywatna dla użytkownika i
    stała do zmiany URL-a. Istniejąca migawka dla TEGO SAMEGO URL-a jest
    zwracana bez żadnego odczytu — kolejne tury i wznowienia nigdy nie
    odpytują listy ponownie (lista mixa zmienia się między odczytami)."""
    existing = state.playlist_snapshot
    if existing is not None and existing.url == url:
        return existing
    with st.spinner("Odczytywanie listy Mix/Radio..."):
        snapshot = snapshot_playlist(url, cookie_data=cookie_data)
    state.set_playlist_snapshot(snapshot)
    return snapshot


def _guess_mime(file_name: str | None) -> str:
    if not file_name:
        return "application/octet-stream"
    mime_type, _ = mimetypes.guess_type(file_name)
    return mime_type or "application/octet-stream"


_ILLEGAL_WINDOWS_CHARS = re.compile(r'[:/\\*?"<>|]')


_SELECTED_INDEX_TOKEN = re.compile(r"^\d+$")


def _parse_selected_indices(raw: str, total: int | None) -> tuple[list[int] | None, str | None]:
    """Parsuje pole "Wybrane numery wideo z playlisty" (np. "21, 28") na
    listę numerów pozycji BEZWZGLĘDNYCH (1-based), posortowaną rosnąco.
    Zwraca (lista, None) przy sukcesie, (None, komunikat_błędu) inaczej.

    To jest wygoda UX (błąd widoczny PRZED kliknięciem "Pobierz") —
    engine.py::submit_playlist waliduje zakres niezależnie jako backstop
    (InvalidPlaylistSelectionError), na wypadek gdyby playlista zmieniła
    długość między tą sondą a właściwym pobraniem. `total=None` (liczba
    pozycji playlisty nieznana — sonda się nie powiodła) pomija walidację
    zakresu, ale nie blokuje składniowo poprawnego wejścia."""
    tokens = [t.strip() for t in raw.split(",")]
    tokens = [t for t in tokens if t]  # puste fragmenty (np. przecinek na końcu) ignorujemy
    if not tokens:
        return None, "Podaj co najmniej jeden numer pozycji, np. 15 albo 21, 28."

    numbers: list[int] = []
    for token in tokens:
        if not _SELECTED_INDEX_TOKEN.match(token):
            return None, (
                f'Nieprawidłowy numer pozycji: "{token}" — podaj liczby całkowite '
                "oddzielone przecinkami."
            )
        numbers.append(int(token))

    if any(n < 1 for n in numbers):
        return None, "Numery pozycji muszą być większe od zera."

    if len(set(numbers)) != len(numbers):
        return None, "Lista zawiera powtórzone numery pozycji."

    if total is not None:
        out_of_range = sorted(n for n in numbers if n > total)
        if out_of_range:
            return None, (
                f"Playlista ma {total} pozycji — numery poza zakresem: "
                f"{', '.join(str(n) for n in out_of_range)}."
            )

    return sorted(set(numbers)), None


def _playlist_processed_range(items: list) -> tuple[int, int] | None:
    """(pierwsza, ostatnia) BEZWZGLĘDNA pozycja faktycznie spróbowana w TYM
    wywołaniu submit_playlist() — czyli status != "skipped" (pozycje
    "skipped" nigdy nie zostały spróbowane, dodane tylko dla kompletności
    raportu po zatrzymaniu limitem ZIP-a, patrz engine.py). None, jeśli nic
    nie było spróbowane (edge case: start_index już poza ZIP-limitem)."""
    processed = [item for item in items if item.status != "skipped"]
    if not processed:
        return None
    return processed[0].index, processed[-1].index


_MAX_SELECTED_INDICES_IN_FILENAME = 5


def _build_playlist_zip_filename(
    playlist_title: str | None,
    items: list,
    playlist_scope: str = "all",
    output_format: str | None = None,
) -> str:
    """Nazwa ZIP-a widoczna dla użytkownika — celowo NIE przez
    build_display_filename() (ta jest dla pojedynczych materiałów, format
    "Autor-Tytuł", semantycznie nie pasuje do ZIP-a całej playlisty).

    Faza 2c: KAŻDA tura ciągłego pobierania ("all", łącznie z pierwszą)
    dostaje sufiks zakresu bezwzględnych pozycji ("-pozycje-{start}-{end}"),
    żeby kilka paczek pobranych w tym samym folderze nie kolidowały nazwą —
    fix regresji: poprzednio pierwsza tura (start=1) nie dostawała sufiksu
    wcale, co dla playlist dzielonych na >1 turę dawało niespójną, myloną
    nazwę ("Playlista-X.zip" obok "Playlista-X-pozycje-14-21.zip"). Zakres
    jest zero-padded do szerokości większej liczby w PARZE (min. 2 cyfry),
    żeby "01-13" wizualnie pasowało do "14-21" z kolejnej tury.

    2026-09-20 (punkt 4): playlist_scope=="selected" ma osobną gałąź — wybór
    jest z natury nieciągły, więc "-pozycje-{start}-{end}" sugerowałby
    błędnie, że pobrano WSZYSTKO między start a end. Krótka lista (≤5
    pozycji) trafia do nazwy wprost ("-pozycje-15,21,28"), dłuższa dostaje
    fallback "-pozycje-wybrane", żeby nazwa pliku nie urosła bez ograniczeń.

    `output_format` (job.output_format: mp4/mp3/flac/srt/vtt/txt) trafia
    tuż przed ".zip", PO całej nazwie z sufiksem pozycji ("...-pozycje-01-07.mp4.zip"),
    żeby z samej nazwy było widać, jakie pliki są w środku. None (zdarzenie
    bez tego pola) = stara nazwa bez rozszerzenia formatu."""
    if not playlist_title:
        base = "playlista"
    else:
        sanitized = _ILLEGAL_WINDOWS_CHARS.sub("_", playlist_title)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        base = f"Playlista-{sanitized}" if sanitized else "playlista"

    if playlist_scope == "selected":
        indices = sorted({item.index for item in items if item.status != "skipped"})
        if not indices:
            stem = base
        elif len(indices) <= _MAX_SELECTED_INDICES_IN_FILENAME:
            stem = f"{base}-pozycje-{','.join(str(i) for i in indices)}"
        else:
            stem = f"{base}-pozycje-wybrane"
    else:
        processed_range = _playlist_processed_range(items)
        if processed_range is None:
            stem = base
        else:
            start, end = processed_range
            width = max(2, len(str(end)))
            stem = f"{base}-pozycje-{start:0{width}d}-{end:0{width}d}"

    format_ext = f".{output_format}" if output_format else ""
    return f"{stem}{format_ext}.zip"


def _playlist_report_summary(items: list) -> tuple[int, int, str]:
    """(liczba pobranych, liczba pozycji ogółem, tekst podsumowania) —
    reużywane przez _render_progress (do error_message w historii DB) i
    _render_result (do wyświetlenia w UI), żeby nie liczyć tego dwukrotnie
    z możliwością rozjazdu tekstu."""
    done_count = sum(1 for item in items if item.status == "done")
    total_count = len(items)
    summary = f"{done_count}/{total_count} pobranych"
    if done_count < total_count:
        summary += f"; {total_count - done_count} błędów/pominiętych"
    return done_count, total_count, summary


def _render_playlist_report(items: list, next_start_index: int | None) -> None:
    done_count, total_count, _ = _playlist_report_summary(items)
    st.caption(f"{done_count} z {total_count} pozycji pobranych.")

    if next_start_index is not None:
        # Faza 2c: zatrzymanie limitem ZIP-a jest ZAMIERZONE, nie błędem —
        # bez tego zdania użytkownik mógłby pomyśleć, że coś padło.
        st.info(
            f"Zatrzymano po pozycji {next_start_index - 1} z powodu limitu "
            f"rozmiaru ZIP-a ({settings.max_zip_size_mb} MB). Kliknij "
            f'"Pobierz kolejne pozycje", aby kontynuować od pozycji {next_start_index}.'
        )

    problems = [item for item in items if item.status != "done"]
    if problems:
        with st.expander(f"Pozycje z błędami lub pominięte ({len(problems)})"):
            for item in problems:
                label = item.title or f"Pozycja {item.index}"
                st.write(f"**{item.index}. {label}** — {item.error_message or item.status}")


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
            elif terminal_event.playlist_items is not None:
                # Faza 2b: wynik submit_playlist() — result_path to ZIP,
                # playlist_items niesie raport per pozycja (done/error/skipped).
                # ZIP-a NIE wczytujemy do RAM (st.download_button(data=...)
                # zawieszał się dla ~1 GB) — przenosimy go do src/downloads.py
                # i serwujemy jako zwykły link HTTP z dysku.
                items = terminal_event.playlist_items
                zip_name = _build_playlist_zip_filename(
                    terminal_event.playlist_title,
                    items,
                    terminal_event.playlist_scope or "all",
                    terminal_event.output_format,
                )
                previous_token = state.result_download_token
                try:
                    link = downloads.publish(result_file, zip_name)
                    file_size_bytes = link.path.stat().st_size
                except OSError:
                    logger.exception("publishing playlist zip failed for job_id=%s", state.job_id)
                    storage.cleanup(result_file.parent)
                    state.set_error("Nie udało się przygotować pliku do pobrania.")
                    _log_job_finish(
                        state,
                        status="error",
                        duration_ms=duration_ms,
                        error_message=state.error_message,
                    )
                else:
                    storage.cleanup(result_file.parent)
                    done_count, _total_count, summary = _playlist_report_summary(items)
                    state.set_done(
                        link.path,
                        download_token=link.token,
                        file_name=zip_name,
                        playlist_report=items,
                        playlist_title=terminal_event.playlist_title,
                        playlist_next_start_index=terminal_event.next_start_index,
                    )
                    if previous_token is not None:
                        downloads.release(previous_token)
                    # Decyzja produktowa: status="done" gdy CHOĆ JEDNA pozycja
                    # się udała (z podsumowaniem w error_message) — "error"
                    # tylko gdy zero pozycji się udało.
                    _log_job_finish(
                        state,
                        status="done" if done_count > 0 else "error",
                        duration_ms=duration_ms,
                        file_size_bytes=file_size_bytes,
                        error_message=summary,
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

    if state.result_data is None and state.result_download_token is None:
        st.error("Brak danych wynikowych.")
        return

    key_suffix = "running" if state.status == "running" else "final"

    if state.playlist_report is not None:
        # ZIP całej playlisty leży na dysku (src/downloads.py) i jest serwowany
        # zwykłym linkiem HTTP — nie przez st.download_button (dla ~1 GB
        # zawieszał się bezterminowo, patrz historia buga "Zapisz plik").
        has_continuation = state.playlist_next_start_index is not None
        if has_continuation:
            # "Zakończone" obok przycisku "Pobierz kolejne pozycje" byłoby
            # sprzeczne — playlista jeszcze się nie skończyła.
            processed_range = _playlist_processed_range(state.playlist_report)
            st.success(
                f"Tura zakończona: pobrano pozycje {processed_range[0]}-{processed_range[1]}."
                if processed_range
                else "Tura zakończona."
            )
        else:
            st.success("Pobieranie playlisty zakończone.")
        _render_playlist_report(state.playlist_report, state.playlist_next_start_index)
        token = state.result_download_token
        if not downloads.is_route_enabled():
            st.error(
                "Serwer uruchomiono bez trasy pobierania plików. "
                "Uruchom aplikację poleceniem: uv run streamlit run asgi_app.py"
            )
        elif token is not None and downloads.lookup(token) is not None:
            st.link_button(
                "Zapisz plik",
                downloads.download_url(token),
                key=f"download_result_link-{state.job_id}-{key_suffix}",
                icon=":material/download:",
            )
            # Dla tury z kontynuacją plik zastąpi następny wynik (akcja
            # użytkownika), więc stały czas ważności byłby mylący.
            if has_continuation:
                st.caption("Ten plik zostanie zastąpiony, gdy pobierzesz kolejne pozycje.")
            else:
                st.caption(
                    f"Link do pobrania jest ważny przez {settings.download_link_ttl_minutes} minut."
                )
        else:
            st.warning(
                "Link do pobrania wygasł. Uruchom pobieranie ponownie, "
                "aby wygenerować nowy plik."
            )
        return
    else:
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

    # Klucz unikalny per job_id (Faza 2c) — przy kontynuacjach playlisty
    # ten sam widget bywał renderowany z różną zawartością; sufiks
    # running/final rozdziela renderowanie "poprzedni wynik widoczny w
    # trakcie kolejnego joba" od właściwego wyniku.
    download_button_key = f"download_result_button-{state.job_id}-{key_suffix}"
    st.download_button(
        "Zapisz plik",
        data=state.result_data,
        file_name=display_name,
        mime=_guess_mime(state.result_file_name),
        key=download_button_key,
        # Domyślne on_click="rerun" niepotrzebnie reruje skrypt przy każdym
        # kliknięciu — wynik widgetu nie jest nigdzie odczytywany.
        on_click="ignore",
    )


def _release_result_download(state: SessionState) -> None:
    """Zwalnia plik z dysku (src/downloads.py) przed wyczyszczeniem wyniku
    w stanie sesji — inaczej ZIP czekałby na sprzątanie po TTL."""
    token = state.result_download_token
    if token is not None:
        downloads.release(token)


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

    cookie_upload = st.file_uploader(
        "cookies.txt (opcjonalnie — dla filmów 18+ lub blokady bot-check)",
        type=["txt"],
        key="cookies_uploader",
    )
    # Tylko surowe bajty — NIE zapisujemy tu pliku na dysk. yt_dlp wymaga
    # ścieżki do pliku w opcji cookiefile, ale ten plik musi żyć w katalogu
    # roboczym KONKRETNEGO joba (engine.py::_write_cookiefile), żeby zniknął
    # razem z resztą przy storage.cleanup() — plik w systemowym katalogu
    # temp (poprzednia implementacja) nigdy nie był sprzątany. Zdefiniowane
    # PRZED blokiem wyboru trybu, bo lista języków napisów (poniżej, dla
    # Subtitle/Transcript) też potrzebuje cookies dla materiału z
    # ograniczeniem wiekowym — patrz list_available_subtitles w engine.py.
    cookie_data: bytes | None = cookie_upload.getvalue() if cookie_upload is not None else None

    # URL zawierający `list=` (obok/bez `v=`) domyślnie w yt-dlp ściągnąłby
    # CAŁĄ playlistę niezależnie od wybranego Trybu (noplaylist=False) —
    # to jest dokładnie zgłoszony bug. Radio poniżej wymusza świadomy wybór
    # zamiast polegania na tym domyślnym zachowaniu; renderowany PRZED
    # selectboxem Tryb, bo dotyczy każdego trybu jednakowo.
    url_classification = classify_url(url) if url else "single"
    # Mix/Radio (list=RD…) — lista generowana dynamicznie przez YouTube, więc
    # pracujemy na JEDNEJ migawce (patrz _get_or_create_mix_snapshot), nie na
    # ponownych odczytach. Bez `v=` (playlist_only) YouTube zwraca "This
    # playlist type is unviewable" — takiego linku nie da się pobrać.
    is_mix_url = url_classification != "single" and is_mix_playlist_url(url)
    playlist_snapshot: PlaylistSnapshot | None = None
    mix_unreadable = False
    playlist_item_count: int | None = None
    playlist_scope = "single"
    selected_indices: list[int] | None = None
    selected_indices_error: str | None = None

    _SCOPE_SINGLE_LABEL = "Tylko to wideo"
    _SCOPE_SELECTED_LABEL = "Wybrane numery wideo z playlisty"

    if is_mix_url and url_classification == "playlist_only":
        mix_unreadable = True
        st.error(
            "Ten link do Mix/Radio nie zawiera parametru v= — YouTube nie pozwala "
            "odczytać takiej listy. Otwórz mix w YouTube i wklej link z paska "
            "przeglądarki (zawiera v=)."
        )
    elif url_classification != "single":
        if is_mix_url:
            try:
                playlist_snapshot = _get_or_create_mix_snapshot(state, url, cookie_data)
            except Exception as exc:
                logger.exception("snapshot_playlist failed for url=%s", url)
                st.error(map_download_error(exc))
            else:
                playlist_item_count = len(playlist_snapshot.entries)
                st.warning(
                    "Mix/Radio jest generowany dynamicznie przez YouTube; pobieramy "
                    f"pierwsze {playlist_item_count} pozycji ze zrobionej teraz migawki — "
                    "ponowne uruchomienie może dać inną listę."
                )
        else:
            try:
                playlist_item_count = _cached_count_playlist_items(url, cookie_data)
            except Exception:
                logger.exception("count_playlist_items failed for url=%s", url)
                playlist_item_count = None
        # MAX_PLAYLIST_ITEMS PRZYCINA zadanie do pierwszych N pozycji (nie
        # blokuje) we wszystkich trybach — patrz engine.py::submit_playlist,
        # który egzekwuje to niezależnie od UI. Mix/Radio ma własny limit
        # (migawka), więc go tu nie dotyczy.
        item_limit = settings.max_playlist_items
        is_trimmed = (
            not is_mix_url and playlist_item_count is not None and playlist_item_count > item_limit
        )
        if playlist_item_count is None:
            count_label = "nieznana liczba pozycji"
        elif is_mix_url:
            count_label = f"do {playlist_item_count} pozycji"
        elif is_trimmed:
            count_label = f"pierwsze {item_limit} z {playlist_item_count}"
        else:
            count_label = f"{playlist_item_count} pozycji"
        scope_all_label = f"Cała playlista ({count_label})"

        if is_mix_url and playlist_snapshot is None:
            # Migawka się nie udała — bez niej "cała playlista"/wybrane numery
            # wymagałyby ponownego odczytu mixa, więc zostaje tylko pojedyncze wideo.
            scope_options = [_SCOPE_SINGLE_LABEL]
        elif url_classification == "mixed":
            scope_options = [_SCOPE_SINGLE_LABEL, scope_all_label, _SCOPE_SELECTED_LABEL]
        else:
            # "playlist_only" (np. /playlist?list=...) — nie ma pojedynczego
            # wideo do wybrania.
            scope_options = [scope_all_label, _SCOPE_SELECTED_LABEL]

        scope_choice = st.radio("Zakres pobierania", scope_options, key="playlist_scope_radio")

        if scope_choice == _SCOPE_SINGLE_LABEL:
            playlist_scope = "single"
        elif scope_choice == _SCOPE_SELECTED_LABEL:
            playlist_scope = "selected"
            # 2026-09-20 (punkt 4): obejście przejściowych błędów 403 na
            # pojedynczych pozycjach playlisty bez ściągania jej od nowa —
            # patrz DownloadJob.selected_indices / submit_playlist w engine.py.
            selected_indices_raw = st.text_input(
                "Numery pozycji do pobrania (oddzielone przecinkami)",
                placeholder="np. 15 albo 21, 28",
                key="selected_indices_input",
            )
            if selected_indices_raw.strip():
                selected_indices, selected_indices_error = _parse_selected_indices(
                    selected_indices_raw, playlist_item_count
                )
                if selected_indices_error:
                    st.error(selected_indices_error)
                elif not is_mix_url and len(selected_indices) > item_limit:
                    st.info(
                        f"Wybrano {len(selected_indices)} pozycji — pobierzemy pierwsze "
                        f"{item_limit} z wybranych (limit)."
                    )
            else:
                selected_indices_error = "Podaj co najmniej jeden numer pozycji."
        else:
            playlist_scope = "all"
            if is_trimmed:
                st.info(
                    f"Playlista ma {playlist_item_count} pozycji — pobierzemy pierwsze "
                    f"{item_limit} (limit). Inne pozycje możesz pobrać opcją „Wybrane numery”."
                )

    # Zmiana URL-a (albo URL nie jest już odczytywalnym mixem) unieważnia
    # migawkę; "Nowy URL" robi to przez state.reset().
    if playlist_snapshot is None and state.playlist_snapshot is not None:
        state.set_playlist_snapshot(None)

    state.set_playlist_scope(playlist_scope)

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
            # Sonda języka napisów dla playlist_scope w ("all", "selected")
            # nie może dostać surowego URL-a playlisty — patrz
            # resolve_representative_video_url w engine.py. submit_playlist()
            # (job dalej w tym pliku) wciąż dostaje oryginalny `url`, tylko
            # TA sonda używa reprezentanta (pierwszej pozycji playlisty —
            # przybliżenie: dla "selected" prawdziwa wybrana pozycja może
            # mieć inny zestaw języków, ale to tylko sonda pomocnicza do UI,
            # nie blokuje faktycznego pobrania konkretnej pozycji).
            subtitle_probe_url = url
            if playlist_scope in ("all", "selected"):
                try:
                    # Mix/Radio: pierwsza pozycja MIGAWKI — bez tego sonda
                    # odczytałaby cały mix od nowa (13-27 s, inna lista).
                    representative_url = (
                        playlist_snapshot.entries[0]["url"]
                        if playlist_snapshot is not None
                        else _cached_resolve_representative_video_url(url, cookie_data)
                    )
                except Exception:
                    logger.exception("resolve_representative_video_url failed for url=%s", url)
                    representative_url = None
                if representative_url:
                    subtitle_probe_url = representative_url

            try:
                available = _cached_list_available_subtitles(subtitle_probe_url, cookie_data)
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

    # Zmiana trybu/formatu przy URL wciąż wypełnionym chowa wynik/błąd
    # POPRZEDNIEGO zadania (i "Zapisz plik") — ale nie dotyka pola URL.
    # Tylko jeśli jest faktycznie coś do wyczyszczenia (is_terminal()) —
    # w trakcie pobierania (status "running") zmiana widgetów nie ma tu
    # znaczenia, bo są zablokowane niżej przez download_disabled/inne joby.
    current_mode_format = (mode, output_format, playlist_scope)
    if state.is_terminal() and state.last_mode_format not in (None, current_mode_format):
        _release_result_download(state)
        state.clear_result()
    state.set_last_mode_format(current_mode_format)

    job_in_progress = state.status == "running"
    subtitle_blocked = mode in ("subtitle", "transcript") and subtitle_lang_missing
    selected_indices_blocked = playlist_scope == "selected" and selected_indices_error is not None
    download_disabled = (
        mode not in READY_MODES
        or not url
        or job_in_progress
        or subtitle_blocked
        or selected_indices_blocked
        or mix_unreadable
    )
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
        _release_result_download(state)
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

    def _launch_job(start_index: int = 1, *, clear_previous_result: bool = True) -> str:
        """Wspólna ścieżka startu joba dla przycisku "Pobierz" (start_index=1,
        domyślne clear_previous_result=True) i "Pobierz kolejne pozycje"
        (Faza 2c, start_index=state.playlist_next_start_index,
        clear_previous_result=False) — ten sam URL/tryb/format/cookiefile
        z bieżącego przebiegu skryptu, tylko nowy job_id i (dla kontynuacji)
        inna pozycja startowa. Zwraca "started"/"queued" z runner.start()."""
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

        if clear_previous_result:
            _release_result_download(state)
        job_queue = state.begin_job(
            job_id, subtitle_lang=subtitle_lang, clear_previous_result=clear_previous_result
        )
        if db_job_id is not None:
            state.set_db_job_id(db_job_id)

        job = DownloadJob(
            url=url,
            mode=mode,
            output_format=output_format,
            session_id=state.session_id,
            job_id=job_id,
            cookie_data=cookie_data,
            audio_bitrate_kbps=audio_bitrate_kbps,
            subtitle_lang=subtitle_lang,
            playlist_scope=playlist_scope,
            start_index=start_index,
            selected_indices=selected_indices if playlist_scope == "selected" else None,
            playlist_snapshot=(
                playlist_snapshot if playlist_scope in ("all", "selected") else None
            ),
        )

        def on_state(event: ProgressEvent, _queue: "queue_module.Queue" = job_queue) -> None:
            # Wołane Z WĄTKU W TLE — wyłącznie wrzucenie do kolejki,
            # NIGDY bezpośrednia modyfikacja SessionState/session_state
            # (Streamlit nie gwarantuje tu bezpieczeństwa wątkowego).
            _queue.put(event)

        return runner.start(job, on_state=on_state)

    if download_clicked:
        if not validate_url(url):
            st.error(map_download_error(InvalidUrlError(url)))
        elif not runner.is_slot_available():
            st.warning(
                f"Wszystkie {settings.max_concurrent_jobs} miejsca pobierania są zajęte "
                "— spróbuj ponownie za chwilę."
            )
        else:
            status = _launch_job()
            if status == "queued":
                # Rzadki wyścig z is_slot_available() (nierezerwujące
                # sprawdzenie) — inny job zdążył zająć slot pierwszy.
                st.warning("Zadanie czeka w kolejce — spróbuj ponownie za chwilę.")
                state.reset()
            else:
                st.rerun()

    def _render_terminal_result_area() -> None:
        """Jedna, ujednolicona ścieżka renderowania wyniku (ZIP + ewentualny
        przycisk "Pobierz kolejne pozycje") — WSPÓLNA dla ostatniej tury
        (playlist_next_start_index=None, brak przycisku "kontynuuj") i
        każdej wcześniejszej (przycisk widoczny). Fix regresji: różna LICZBA
        widgetów renderowanych w tym bloku między turami (z przyciskiem vs
        bez) potrafiła zostawić w prawdziwej przeglądarce Streamlit "widmowy"
        element z poprzedniego przebiegu nakładający się na przycisk
        "Zapisz plik" — niewidoczne w AppTest (mockowy backend mediów), ale
        zgłoszone manualnie (przycisk aktywny, ale nie reaguje, dokładnie
        i TYLKO gdy poprzedni render miał WIĘCEJ elementów, czyli po turze
        z przyciskiem "kontynuuj", a bieżąca go już nie ma). st.empty()
        wymusza pełne wyczyszczenie tego miejsca w DOM PRZED narysowaniem
        nowej zawartości, więc liczba/rodzaj widgetów w tym bloku nigdy nie
        "dziedziczy" niczego z poprzedniego przebiegu."""
        _render_result(state)
        if state.playlist_next_start_index is not None:
            if st.button("Pobierz kolejne pozycje", key="continue_playlist_button"):
                if not runner.is_slot_available():
                    st.warning(
                        f"Wszystkie {settings.max_concurrent_jobs} miejsca pobierania są zajęte "
                        "— spróbuj ponownie za chwilę."
                    )
                else:
                    status = _launch_job(
                        start_index=state.playlist_next_start_index, clear_previous_result=False
                    )
                    if status == "queued":
                        state.cancel_queued_job()
                        st.warning("Zadanie czeka w kolejce — spróbuj ponownie za chwilę.")
                    else:
                        st.rerun()

    if state.status == "running":
        if state.playlist_report is not None:
            # Faza 2c: to jest kontynuacja ("Pobierz kolejne pozycje")
            # wystartowana z clear_previous_result=False — poprzedni
            # ZIP/raport zostają widoczne, dopóki NOWY job się nie zakończy
            # (użytkownik mógł jeszcze nie zdążyć zapisać poprzedniego pliku).
            # Bez przycisku "kontynuuj" tutaj — nowy job już jedzie.
            with st.empty().container():
                _render_result(state)
            st.divider()
        _render_progress(state)
    elif state.is_terminal():
        with st.empty().container():
            _render_terminal_result_area()

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
