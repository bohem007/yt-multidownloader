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
from typing import Literal, MutableMapping

Status = Literal["idle", "running", "done", "error"]

_STATUS = "status"
_PERCENT = "percent"
_MESSAGE = "message"
_RESULT_PATH = "result_path"
_RESULT_DATA = "result_data"
_RESULT_FILE_NAME = "result_file_name"
_ERROR_MESSAGE = "error_message"
_JOB_ID = "job_id"
_DB_JOB_ID = "db_job_id"
_STARTED_AT = "started_at"
_QUEUE = "queue"
_SESSION_ID = "session_id"

_DEFAULTS: dict = {
    _STATUS: "idle",
    _PERCENT: 0.0,
    _MESSAGE: "",
    _RESULT_PATH: None,
    _RESULT_DATA: None,
    _RESULT_FILE_NAME: None,
    _ERROR_MESSAGE: None,
    _JOB_ID: None,
    _DB_JOB_ID: None,
    _STARTED_AT: None,
    _QUEUE: None,
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
    def result_data(self) -> bytes | None:
        return self._store[_RESULT_DATA]

    @property
    def result_file_name(self) -> str | None:
        return self._store[_RESULT_FILE_NAME]

    @property
    def error_message(self) -> str | None:
        return self._store[_ERROR_MESSAGE]

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

    def reset(self) -> None:
        self._store.update(_DEFAULTS)

    def set_running(self) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = 0.0
        self._store[_MESSAGE] = ""
        self._store[_RESULT_PATH] = None
        self._store[_RESULT_DATA] = None
        self._store[_RESULT_FILE_NAME] = None
        self._store[_ERROR_MESSAGE] = None

    def begin_job(self, job_id: str) -> "queue_module.Queue":
        """Startuje nowe zadanie: status->running, nowe job_id, świeża
        kolejka na zdarzenia postępu (most z wątku w tle) i znacznik czasu
        startu. Zwraca kolejkę, którą wołający ma podłączyć do callbacku
        przekazywanego dalej do JobRunner.start()."""
        self.set_running()
        self._store[_JOB_ID] = job_id
        self._store[_STARTED_AT] = time.monotonic()
        q: queue_module.Queue = queue_module.Queue()
        self._store[_QUEUE] = q
        return q

    def set_db_job_id(self, db_job_id: int) -> None:
        self._store[_DB_JOB_ID] = db_job_id

    def set_progress(self, percent: float, message: str) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = percent
        self._store[_MESSAGE] = message

    def set_done(
        self,
        result_path: Path | str,
        *,
        data: bytes | None = None,
        file_name: str | None = None,
    ) -> None:
        self._store[_STATUS] = "done"
        self._store[_PERCENT] = 100.0
        self._store[_RESULT_PATH] = result_path
        self._store[_RESULT_DATA] = data
        self._store[_RESULT_FILE_NAME] = file_name

    def set_error(self, message: str) -> None:
        self._store[_STATUS] = "error"
        self._store[_ERROR_MESSAGE] = message

    def is_terminal(self) -> bool:
        return self.status in ("done", "error")
