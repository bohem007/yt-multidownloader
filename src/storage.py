"""Storage efemeryczny per zadanie — patrz Warstwa 10 specyfikacji.

Katalog per zadanie żyje tylko na czas pobierania; wynik jest wczytywany
do RAM i katalog jest natychmiast usuwany (shutil.rmtree) przez warstwę
wołającą (docelowo app.py). Baza danych nie przechowuje plików.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.config import settings
from src.errors import FileTooLargeError


def create(session_id: str, job_id: str) -> Path:
    """Tworzy izolowany katalog tymczasowy: {STORAGE_BASE_DIR}/{session_id}/{job_id}/."""
    path = Path(settings.storage_base_dir) / session_id / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup(path: str | Path) -> None:
    """Usuwa katalog zadania — brak katalogu (już usunięty) nie jest błędem."""
    shutil.rmtree(path, ignore_errors=True)


def directory_size_bytes(path: str | Path) -> int:
    """Sumuje rozmiar pliku albo całego katalogu (rekurencyjnie) — jedna
    droga liczenia rozmiaru, używana przez enforce_size_limit (limit
    pojedynczego pliku) i engine.py::submit_playlist (limit ZIP-a w trakcie
    pobierania wielu pozycji), bez duplikowania tej samej logiki rglob."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def enforce_size_limit(path: str | Path, max_mb: int | None = None) -> None:
    """Sprawdza rozmiar wynikowego pliku/katalogu względem MAX_FILE_SIZE_MB.

    Podnosi FileTooLargeError, jeśli limit jest przekroczony — wołający
    (engine.py) decyduje, co dalej (cleanup + mapowanie na komunikat).
    """
    path = Path(path)
    limit_mb = max_mb if max_mb is not None else settings.max_file_size_mb
    size_bytes = directory_size_bytes(path)
    limit_bytes = limit_mb * 1024 * 1024

    if size_bytes > limit_bytes:
        raise FileTooLargeError(
            f"Rozmiar wynikowy ({size_bytes / (1024 * 1024):.1f} MB) "
            f"przekracza limit {limit_mb} MB."
        )
