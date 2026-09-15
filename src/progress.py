"""Zdarzenia postępu emitowane przez engine.py. Patrz Warstwa 8 specyfikacji.

Silnik emituje zdarzenia przez callback; warstwa prezentacji (Streamlit,
log) jest oddzielona od silnika dla łatwiejszej wymiany UI w przyszłości.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EventType = Literal["on_start", "on_progress", "on_finished", "on_error"]


@dataclass
class ProgressEvent:
    event_type: EventType
    percent: float
    message: str
