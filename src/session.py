"""Zarządzanie stanem pojedynczego zadania — Warstwa 3 specyfikacji.

SessionState NIE importuje `st.session_state` — przyjmuje w konstruktorze
dowolny obiekt mapujący (Mapping/MutableMapping). Domyślnie (parametr
`store=None`) używa zwykłego dict(), co pozwala testować ten moduł bez
uruchomionego Streamlit. Przekazanie faktycznego `st.session_state`
następuje w app.py — app.py nigdy nie manipuluje stanem bezpośrednio,
tylko przez tę klasę (włącznie z kolejką na zdarzenia postępu z wątku
w tle — most opisany w Warstwie 4).
"""

from __future__ import annotations

import queue as queue_module
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, MutableMapping

if TYPE_CHECKING:
    # Tylko do podpowiedzi typów (patrz analogiczny import w progress.py) —
    # engine.py nie importuje session.py, więc cyklu tu nie ma, ale bez
    # potrzeby resolwowania w runtime i tak importujemy leniwie/warunkowo.
    from src.engine import PlaylistItemResult, PlaylistSnapshot

Status = Literal["idle", "running", "done", "error"]

_STATUS = "status"
_PERCENT = "percent"
_MESSAGE = "message"
_RESULT_PATH = "result_path"
_RESULT_DOWNLOAD_TOKEN = "result_download_token"
_RESULT_FILE_NAME = "result_file_name"
_RESULT_FILE_SIZE = "result_file_size"
_ERROR_MESSAGE = "error_message"
_JOB_ID = "job_id"
_DB_JOB_ID = "db_job_id"
_STARTED_AT = "started_at"
_QUEUE = "queue"
_SESSION_ID = "session_id"
_CLIENT_IP_HASH = "client_ip_hash"
_URL_LOCKED = "url_locked"
_LAST_MODE_FORMAT = "last_mode_format"
_SUBTITLE_LANG = "subtitle_lang"
_PLAYLIST_SCOPE = "playlist_scope"
_PLAYLIST_REPORT = "playlist_report"
_PLAYLIST_TITLE = "playlist_title"
_PLAYLIST_NEXT_START_INDEX = "playlist_next_start_index"
_PLAYLIST_SNAPSHOT = "playlist_snapshot"
_HISTORY_LOADED = "history_loaded"
_HISTORY_ROWS = "history_rows"
_HISTORY_ERROR = "history_error"

# Pola "wyniku" zadania — czyszczone razem przy starcie nowego zadania
# (set_running) i przy zmianie trybu/formatu z URL wciąż wypełnionym
# (clear_result). NIE obejmuje url_locked/last_mode_format/subtitle_lang
# (Warstwa 2, UX) — te żyją niezależnie od pojedynczego zadania.
_RESULT_FIELDS: dict = {
    _RESULT_PATH: None,
    _RESULT_DOWNLOAD_TOKEN: None,
    _RESULT_FILE_NAME: None,
    _RESULT_FILE_SIZE: None,
    _ERROR_MESSAGE: None,
    _PLAYLIST_REPORT: None,
    _PLAYLIST_TITLE: None,
    _PLAYLIST_NEXT_START_INDEX: None,
}

_DEFAULTS: dict = {
    _STATUS: "idle",
    _PERCENT: 0.0,
    _MESSAGE: "",
    _JOB_ID: None,
    _DB_JOB_ID: None,
    _STARTED_AT: None,
    _QUEUE: None,
    _URL_LOCKED: False,
    _LAST_MODE_FORMAT: None,
    _SUBTITLE_LANG: None,
    _PLAYLIST_SCOPE: "single",
    # Celowo POZA _RESULT_FIELDS: migawka Mix/Radio musi przeżyć kolejne
    # tury/wznowienia (set_running/clear_result jej nie ruszają), a znika
    # dopiero przy reset() ("Nowy URL") albo zmianie URL-a (app.py).
    _PLAYLIST_SNAPSHOT: None,
    **_RESULT_FIELDS,
}


class SessionState:
    def __init__(self, store: MutableMapping | None = None) -> None:
        self._store: MutableMapping = store if store is not None else {}
        for key, value in _DEFAULTS.items():
            self._store.setdefault(key, value)

    @property
    def status(self) -> Status:
        return self._store[_STATUS]

    @property
    def percent(self) -> float:
        return self._store[_PERCENT]

    @property
    def message(self) -> str:
        return self._store[_MESSAGE]

    @property
    def result_path(self) -> Path | None:
        return self._store[_RESULT_PATH]

    @property
    def result_download_token(self) -> str | None:
        """Token linku do pobrania z dysku (src/downloads.py) — jedyna droga
        do pliku wynikowego, pojedynczego i ZIP-a. Stan sesji nigdy nie
        trzyma bajtów pliku."""
        return self._store[_RESULT_DOWNLOAD_TOKEN]

    @property
    def result_file_name(self) -> str | None:
        """Nazwa, pod którą przeglądarka zapisze plik (Content-Disposition)."""
        return self._store[_RESULT_FILE_NAME]

    @property
    def result_file_size(self) -> int | None:
        return self._store[_RESULT_FILE_SIZE]

    @property
    def error_message(self) -> str | None:
        return self._store[_ERROR_MESSAGE]

    @property
    def playlist_report(self) -> "list[PlaylistItemResult] | None":
        """Lista PlaylistItemResult (src/engine.py) ostatniego zakończonego
        joba playlisty — None dla pojedynczych pobrań (playlist_scope="single")
        i czyszczone razem z resztą wyniku (clear_result/set_running)."""
        return self._store[_PLAYLIST_REPORT]

    @property
    def playlist_title(self) -> str | None:
        """Tytuł playlisty z ostatniego zakończonego joba — używany do
        nazwy ZIP-a widocznej dla użytkownika (app.py), NIE przez
        build_display_filename (ta jest dla pojedynczych materiałów)."""
        return self._store[_PLAYLIST_TITLE]

    @property
    def playlist_next_start_index(self) -> int | None:
        """Faza 2c — pozycja BEZWZGLĘDNA, od której wznowić pobieranie
        ("Pobierz kolejne pozycje" w app.py). None = nic do wznowienia
        (playlista przetworzona do końca) — przycisk się nie pokazuje."""
        return self._store[_PLAYLIST_NEXT_START_INDEX]

    @property
    def job_id(self) -> str | None:
        return self._store[_JOB_ID]

    @property
    def db_job_id(self) -> int | None:
        return self._store[_DB_JOB_ID]

    @property
    def started_at(self) -> float | None:
        """Znacznik czasu (time.monotonic()) startu zadania — do liczenia duration_ms."""
        return self._store[_STARTED_AT]

    @property
    def queue(self) -> "queue_module.Queue | None":
        """Kolejka zdarzeń ProgressEvent z wątku w tle dla aktywnego zadania."""
        return self._store[_QUEUE]

    @property
    def session_id(self) -> str:
        """Identyfikator przeglądarkowej sesji — generowany raz, przetrwa reset()."""
        if self._store.get(_SESSION_ID) is None:
            self._store[_SESSION_ID] = str(uuid.uuid4())
        return self._store[_SESSION_ID]

    @property
    def url_locked(self) -> bool:
        """True, gdy pole URL ma być zablokowane do edycji (URL już wpisany)."""
        return self._store[_URL_LOCKED]

    @property
    def last_mode_format(self) -> tuple[str, ...] | None:
        """(mode, output_format, playlist_scope) zapamiętane z poprzedniego
        przebiegu — do wykrywania zmiany trybu/formatu/zakresu (Warstwa 2,
        UX; playlist_scope dołączony w Fazie 2b, żeby przełączenie "Tylko
        to wideo" <-> "Cała playlista" też czyściło poprzedni wynik/raport)."""
        return self._store[_LAST_MODE_FORMAT]

    @property
    def subtitle_lang(self) -> str | None:
        """Język napisów użyty w OSTATNIM zleconym zadaniu — potrzebny
        przy budowie nazwy pliku (src/naming.py) już po zakończeniu, gdy
        widżet wyboru języka z chwili kliknięcia "Pobierz" dawno przestał
        istnieć w bieżącym przebiegu skryptu."""
        return self._store[_SUBTITLE_LANG]

    @property
    def playlist_scope(self) -> str:
        """"single" (domyślnie) albo "all" — wybór z radia widocznego dla
        URL-i z parametrem `list` (validators.classify_url), przeliczany
        na nowo w KAŻDYM przebiegu skryptu (jak url_locked/last_mode_format),
        nie tylko przy starcie joba."""
        return self._store[_PLAYLIST_SCOPE]

    @property
    def playlist_snapshot(self) -> "PlaylistSnapshot | None":
        """Migawka listy Mix/Radio (engine.snapshot_playlist) dla bieżącego
        URL-a — jedyne źródło pozycji dla wszystkich tur/wybranych numerów
        (bez ponownego odczytu playlisty). None = brak/unieważniona."""
        return self._store[_PLAYLIST_SNAPSHOT]

    @property
    def history_loaded(self) -> bool:
        """Czy historia była już wczytana od ostatniej invalidacji (koniec
        joba, patrz invalidate_history_cache) — cache PRZETRWA reset()/"Nowy
        URL" (jak client_ip_hash/session_id, celowo POZA _DEFAULTS), bo
        historia nie zależy od aktualnego URL-a. Bez tego cache'a zakładka
        "Historia" (app.py) odpytywałaby bazę przy KAŻDYM rerunie skryptu
        (Streamlit wykonuje ciało OBU zakładek co rerun, nie tylko aktywnej),
        nie tylko wtedy, gdy coś w historii faktycznie mogło się zmienić."""
        return self._store.get(_HISTORY_LOADED, False)

    @property
    def history_rows(self) -> list[dict] | None:
        """Wiersze z ostatniego udanego odczytu historii — None, dopóki nic
        nie wczytano albo ostatnia próba się nie powiodła (history_error)."""
        return self._store.get(_HISTORY_ROWS)

    @property
    def history_error(self) -> bool:
        """True, gdy OSTATNIA próba wczytania historii rzuciła wyjątek —
        app.py pokazuje wtedy neutralny komunikat zamiast tabeli/pustego
        stanu, bez ponawiania próby przy każdym kolejnym rerunie."""
        return self._store.get(_HISTORY_ERROR, False)

    def reset(self) -> None:
        self._store.update(_DEFAULTS)

    def set_running(self) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = 0.0
        self._store[_MESSAGE] = ""
        self._store.update(_RESULT_FIELDS)

    def clear_result(self) -> None:
        """Czyści wynik/komunikaty poprzedniego zadania (status->idle),
        ale NIE dotyka url_locked, session_id ani last_mode_format —
        używane przy zmianie trybu/formatu z URL wciąż wypełnionym.
        Odpowiednik set_running(), tylko status wraca do "idle", nie
        "running" (żadne zadanie faktycznie nie startuje)."""
        self._store[_STATUS] = "idle"
        self._store[_PERCENT] = 0.0
        self._store[_MESSAGE] = ""
        self._store.update(_RESULT_FIELDS)

    def begin_job(
        self,
        job_id: str,
        subtitle_lang: str | None = None,
        *,
        clear_previous_result: bool = True,
    ) -> "queue_module.Queue":
        """Startuje nowe zadanie: status->running, nowe job_id, świeża
        kolejka na zdarzenia postępu (most z wątku w tle) i znacznik czasu
        startu. Zwraca kolejkę, którą wołający ma podłączyć do callbacku
        przekazywanego dalej do JobRunner.start().

        clear_previous_result=False (Faza 2c, "Pobierz kolejne pozycje"):
        NIE czyści pól wyniku poprzedniego zadania (ZIP/raport playlisty)
        — pozostają widoczne w UI, dopóki NOWE zadanie faktycznie się nie
        zakończy (set_done/set_error nadpisze je dopiero wtedy). Domyślne
        True = dotychczasowe zachowanie (set_running() czyści od razu)."""
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = 0.0
        self._store[_MESSAGE] = ""
        if clear_previous_result:
            self._store.update(_RESULT_FIELDS)
        self._store[_JOB_ID] = job_id
        self._store[_STARTED_AT] = time.monotonic()
        self._store[_SUBTITLE_LANG] = subtitle_lang
        q: queue_module.Queue = queue_module.Queue()
        self._store[_QUEUE] = q
        return q

    def cancel_queued_job(self) -> None:
        """Odwraca begin_job(), gdy JobRunner.start() zwróci "queued"
        (rzadki wyścig z is_slot_available() — nierezerwujące sprawdzenie) —
        zadanie nigdy faktycznie nie wystartowało. Status wraca do "done",
        jeśli wynik poprzedniego zadania wciąż jest w store (kontynuacja
        playlisty z clear_previous_result=False), inaczej do "idle"."""
        self._store[_JOB_ID] = None
        self._store[_QUEUE] = None
        self._store[_STARTED_AT] = None
        has_result = self._store[_RESULT_DOWNLOAD_TOKEN] is not None
        self._store[_STATUS] = "done" if has_result else "idle"

    def client_ip_hash(self, resolve: Callable[[], str]) -> str:
        """Hash klienta liczony RAZ na sesję (jak session_id — poza _DEFAULTS,
        więc przeżywa reset())."""
        if self._store.get(_CLIENT_IP_HASH) is None:
            self._store[_CLIENT_IP_HASH] = resolve()
        return self._store[_CLIENT_IP_HASH]

    def set_db_job_id(self, db_job_id: int) -> None:
        self._store[_DB_JOB_ID] = db_job_id

    def set_url_locked(self, locked: bool) -> None:
        self._store[_URL_LOCKED] = locked

    def set_last_mode_format(self, mode_format: tuple[str, str]) -> None:
        self._store[_LAST_MODE_FORMAT] = mode_format

    def set_playlist_scope(self, scope: str) -> None:
        self._store[_PLAYLIST_SCOPE] = scope

    def set_playlist_snapshot(self, snapshot: "PlaylistSnapshot | None") -> None:
        self._store[_PLAYLIST_SNAPSHOT] = snapshot

    def set_history_cache(self, rows: list[dict] | None, *, error: bool = False) -> None:
        """Zapisuje wynik odczytu historii (albo błąd) — patrz history_loaded."""
        self._store[_HISTORY_LOADED] = True
        self._store[_HISTORY_ROWS] = rows
        self._store[_HISTORY_ERROR] = error

    def invalidate_history_cache(self) -> None:
        """Wymusza ponowny odczyt historii przy najbliższym renderze zakładki
        "Historia" — wołane po zakończeniu joba (app.py::_log_job_finish),
        jedynym zdarzeniu, które realnie mogło zmienić wiersze w bazie."""
        self._store[_HISTORY_LOADED] = False
        self._store[_HISTORY_ROWS] = None
        self._store[_HISTORY_ERROR] = False

    def set_progress(self, percent: float, message: str) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = percent
        self._store[_MESSAGE] = message

    def set_done(
        self,
        result_path: Path | str,
        *,
        download_token: str | None = None,
        file_name: str | None = None,
        file_size: int | None = None,
        playlist_report: "list[PlaylistItemResult] | None" = None,
        playlist_title: str | None = None,
        playlist_next_start_index: int | None = None,
    ) -> None:
        self._store[_STATUS] = "done"
        self._store[_PERCENT] = 100.0
        self._store[_RESULT_PATH] = result_path
        self._store[_RESULT_DOWNLOAD_TOKEN] = download_token
        self._store[_RESULT_FILE_NAME] = file_name
        self._store[_RESULT_FILE_SIZE] = file_size
        self._store[_PLAYLIST_REPORT] = playlist_report
        self._store[_PLAYLIST_TITLE] = playlist_title
        self._store[_PLAYLIST_NEXT_START_INDEX] = playlist_next_start_index

    def set_error(self, message: str) -> None:
        self._store[_STATUS] = "error"
        self._store[_ERROR_MESSAGE] = message

    def is_terminal(self) -> bool:
        return self.status in ("done", "error")
