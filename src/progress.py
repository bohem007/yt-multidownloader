"""Zdarzenia postępu emitowane przez engine.py. Patrz Warstwa 8 specyfikacji.

Silnik emituje zdarzenia przez callback; warstwa prezentacji (Streamlit,
log) jest oddzielona od silnika dla łatwiejszej wymiany UI w przyszłości.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

EventType = Literal["on_start", "on_progress", "on_finished", "on_error"]


@dataclass
class ProgressEvent:
    event_type: EventType
    percent: float
    message: str
    # Ustawiane tylko na finalnym "on_finished" (patrz job_runner.py) —
    # rzeczywista ścieżka pliku wynikowego PO postprocessingu, zwrócona
    # przez yt_dlp (nigdy zgadywana z zawartości katalogu).
    result_path: Path | None = None
