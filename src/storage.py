"""Storage efemeryczny per zadanie — patrz Warstwa 10 specyfikacji.

Katalog per zadanie żyje tylko na czas pobierania; plik wynikowy jest
przenoszony do katalogu linków (src/downloads.py::publish), a reszta
katalogu joba jest natychmiast usuwana (shutil.rmtree) przez warstwę
wołającą (app.py). Baza danych nie przechowuje plików.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.config import settings
from src.errors import FileTooLargeError

# Własny podkatalog zadań w STORAGE_BASE_DIR (analogicznie do
# downloads._LINKS_DIRNAME) — dzięki temu purge_all() poniżej może sprzątać
# WYŁĄCZNIE swoje dane, nigdy całego STORAGE_BASE_DIR (który w produkcji to
# domyślnie /tmp — dzielony z systemem/innymi procesami, nie nasz do kasowania).
_JOBS_DIRNAME = "yt-multidownloader-jobs"


def create(session_id: str, job_id: str) -> Path:
    """Tworzy izolowany katalog tymczasowy:
    {STORAGE_BASE_DIR}/yt-multidownloader-jobs/{session_id}/{job_id}/."""
    path = Path(settings.storage_base_dir) / _JOBS_DIRNAME / session_id / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup(path: str | Path) -> None:
    """Usuwa katalog zadania — brak katalogu (już usunięty) nie jest błędem."""
    shutil.rmtree(path, ignore_errors=True)


def purge_all() -> None:
    """Usuwa WSZYSTKIE katalogi zadań — wyłącznie przy starcie procesu.

    cleanup() pojedynczego joba jest wołany dopiero po jego zakończeniu, więc
    awaria procesu w trakcie pobierania (crash, restart kontenera) zostawia
    katalog joba osieroconym na dysku — nic go wtedy nie usuwa. Bezpieczne
    tylko na starcie serwera (patrz asgi_app.py::lifespan): żaden job nie
    jest jeszcze w toku, więc nie ma czego przerwać. Sprząta wyłącznie
    własny podkatalog `_JOBS_DIRNAME`, nigdy całego STORAGE_BASE_DIR."""
    shutil.rmtree(Path(settings.storage_base_dir) / _JOBS_DIRNAME, ignore_errors=True)


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
