"""Izolacja testów od lokalnego `.env` i od bazy (Neon).

1. Limity są pinowane w `os.environ` NA POZIOMIE MODUŁU, zanim ktokolwiek
   zaimportuje `src.config` — `settings` to zamrożony singleton czytany raz
   przy imporcie, a `load_dotenv()` nie nadpisuje zmiennych już ustawionych.
   Dzięki temu testy dają ten sam wynik z `.env` dowolnej treści i bez niego.
2. Autouse `database_calls` podmienia warstwę bazy (`src.db.Database`) na
   atrapę zapisującą wywołania i blokuje `psycopg.connect` — żaden test poza
   oznaczonymi `@pytest.mark.db_integration` nie łączy się z bazą. Testy
   `@pytest.mark.db_sql` sprawdzają PRAWDZIWY kod SQL warstwy bazy na
   fałszywym połączeniu (wstrzykniętym do `Database._connect`): metody nie
   są wtedy podmieniane, ale `psycopg.connect` nadal jest zablokowane (wcześniej
   kliknięcie „Pobierz" w AppTest robiło realny INSERT do Neon, a bez
   `DATABASE_URL` wisiało w `psycopg.connect`).
"""

from __future__ import annotations

import os

# Wartości domyślne z CLAUDE.md ("Ustalone wartości domyślne"). Przypisanie,
# nie setdefault: zmienne wyeksportowane w powłoce też nie mogą zmienić testów.
_PINNED_ENV = {
    "ENVIRONMENT": "local",
    "MAX_FILE_SIZE_MB": "500",
    "MAX_PLAYLIST_ITEMS": "10",
    "MAX_PLAYLIST_RD_ITEMS": "20",
    "MAX_ZIP_SIZE_MB": "500",
    "MAX_CONCURRENT_JOBS": "2",
    "ITEM_DOWNLOAD_TIMEOUT_SECONDS": "180",
    "DOWNLOAD_LINK_TTL_MINUTES": "30",
    "RATE_LIMIT_PER_IP": "10",
    "RATE_LIMITING_ENABLED": "false",
    "HISTORY_RETENTION_DAYS": "5",
    "DB_CONNECT_TIMEOUT_SECONDS": "5",
    "IP_HASH_SECRET": "test-secret",
}
os.environ.update(_PINNED_ENV)

from dataclasses import dataclass, field  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402

from src.db import Database  # noqa: E402


@dataclass
class DatabaseCalls:
    """Rejestr wywołań atrapy Database (zamiast zapisu do prawdziwej bazy)."""

    starts: list[dict] = field(default_factory=list)
    finishes: list[dict] = field(default_factory=list)
    history_queries: list[dict] = field(default_factory=list)
    purges: list[int] = field(default_factory=list)


def _refuse_connect(*args, **kwargs):
    # RuntimeError, nie OperationalError: Database._connect ponawia tylko
    # OperationalError (cold start Neon), więc blokada zadziała natychmiast.
    raise RuntimeError("Testy nie łączą się z bazą (tests/conftest.py::database_calls)")


@pytest.fixture(autouse=True)
def database_calls(request, monkeypatch):
    if request.node.get_closest_marker("db_integration"):
        yield None
        return

    monkeypatch.setattr(psycopg, "connect", _refuse_connect)
    if request.node.get_closest_marker("db_sql"):
        yield None
        return

    calls = DatabaseCalls()

    def _log_job_start(self, url, mode, output_format, client_ip_hash=None):
        calls.starts.append(
            {"url": url, "mode": mode, "output_format": output_format, "client_ip_hash": client_ip_hash}
        )
        return len(calls.starts)

    def _log_job_finish(
        self, job_id, status, duration_ms=None, file_size_bytes=None, error_message=None
    ):
        calls.finishes.append(
            {
                "job_id": job_id,
                "status": status,
                "duration_ms": duration_ms,
                "file_size_bytes": file_size_bytes,
                "error_message": error_message,
            }
        )

    def _get_recent_history(self, client_ip_hash, days, limit=20):
        calls.history_queries.append(
            {"client_ip_hash": client_ip_hash, "days": days, "limit": limit}
        )
        return []

    def _purge_old_jobs(self, days):
        calls.purges.append(days)
        return 0

    monkeypatch.setattr(Database, "log_job_start", _log_job_start)
    monkeypatch.setattr(Database, "log_job_finish", _log_job_finish)
    monkeypatch.setattr(Database, "get_recent_history", _get_recent_history)
    monkeypatch.setattr(Database, "purge_old_jobs", _purge_old_jobs)
    yield calls
