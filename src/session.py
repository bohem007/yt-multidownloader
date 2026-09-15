"""Zarządzanie stanem pojedynczego zadania — Warstwa 3 specyfikacji.

SessionState NIE importuje `st.session_state` — przyjmuje w konstruktorze
dowolny obiekt mapujący (Mapping/MutableMapping). Domyślnie (parametr
`store=None`) używa zwykłego dict(), co pozwala testować ten moduł bez
uruchomionego Streamlit. Przekazanie faktycznego `st.session_state`
nastąpi w app.py w kolejnej sesji — app.py nigdy nie manipuluje stanem
bezpośrednio, tylko przez tę klasę.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, MutableMapping

Status = Literal["idle", "running", "done", "error"]

_STATUS = "status"
_PERCENT = "percent"
_MESSAGE = "message"
_RESULT_PATH = "result_path"
_ERROR_MESSAGE = "error_message"

_DEFAULTS: dict = {
    _STATUS: "idle",
    _PERCENT: 0.0,
    _MESSAGE: "",
    _RESULT_PATH: None,
    _ERROR_MESSAGE: None,
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
    def error_message(self) -> str | None:
        return self._store[_ERROR_MESSAGE]

    def reset(self) -> None:
        self._store.update(_DEFAULTS)

    def set_running(self) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = 0.0
        self._store[_MESSAGE] = ""
        self._store[_RESULT_PATH] = None
        self._store[_ERROR_MESSAGE] = None

    def set_progress(self, percent: float, message: str) -> None:
        self._store[_STATUS] = "running"
        self._store[_PERCENT] = percent
        self._store[_MESSAGE] = message

    def set_done(self, result_path: Path | str) -> None:
        self._store[_STATUS] = "done"
        self._store[_PERCENT] = 100.0
        self._store[_RESULT_PATH] = result_path

    def set_error(self, message: str) -> None:
        self._store[_STATUS] = "error"
        self._store[_ERROR_MESSAGE] = message

    def is_terminal(self) -> bool:
        return self.status in ("done", "error")
