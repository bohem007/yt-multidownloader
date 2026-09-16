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
    # przez yt_dlp (nigdy zgadywana z zawartości katalogu), plus metadane
    # materiału do budowy nazwy pliku widocznej dla użytkownika
    # (src/naming.py). Płaskie pola (nie DownloadResult z engine.py) —
    # engine.py importuje z progress.py, więc odwrotna zależność
    # utworzyłaby cykl importów.
    result_path: Path | None = None
    result_uploader: str | None = None
    result_title: str | None = None
