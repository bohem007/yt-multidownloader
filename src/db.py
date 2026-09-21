"""Warstwa trwałości: czysty SQL przez psycopg, bez ORM.

Jedyny zakres danych: anonimowa historia zadań (tabela `jobs`).
Brak plików multimedialnych w bazie — patrz CLAUDE.md.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.config import settings

_SCHEMA_SQL_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
_SCHEMA_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_COLD_START_ATTEMPTS = 3
_COLD_START_BACKOFF_SECONDS = 1.0

# Neon (host -pooler) jest rekomendowany dla 1-2 połączeń klienckich per
# proces — semafor ogranicza to procesowo, niezależnie od liczby wątków
# jednocześnie wołających Database.
_CONNECTION_LIMIT = threading.Semaphore(2)

logger = logging.getLogger(__name__)

# Kasowanie starych wierszy (retencja) najwyżej raz na tyle sekund na proces:
# DELETE po indeksowanym created_at jest tani, ale każde wywołanie to osobne
# połączenie z Neon (cold start do kilku sekund) — nie robimy tego per zapis.
_PURGE_INTERVAL_SECONDS = 3600.0


def _validate_schema_name(name: str) -> str:
    if not _SCHEMA_NAME_RE.match(name):
        raise ValueError(f"Niepoprawna nazwa schematu DB_SCHEMA: {name!r}")
    return name


def _spawn_daemon(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="jobs-purge", daemon=True).start()


class Database:
    """Dostęp do tabeli `jobs` w schemacie wskazanym przez settings.db_schema.

    `clock` i `spawn` są wstrzykiwane wyłącznie dla testów (deterministyczny
    throttling purge bez sleep i bez prawdziwych wątków).
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _spawn_daemon,
    ) -> None:
        self._schema = _validate_schema_name(settings.db_schema)
        self._table = sql.Identifier(self._schema, "jobs")
        self._schema_ready = False
        self._clock = clock
        self._spawn = spawn
        self._purge_lock = threading.Lock()
        self._last_purge_at: float | None = None

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        _CONNECTION_LIMIT.acquire()
        try:
            last_error: Exception | None = None
            conn: psycopg.Connection | None = None
            for attempt in range(_COLD_START_ATTEMPTS):
                try:
                    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
                    break
                except psycopg.OperationalError as exc:
                    last_error = exc
                    if attempt < _COLD_START_ATTEMPTS - 1:
                        time.sleep(_COLD_START_BACKOFF_SECONDS * (attempt + 1))
            if conn is None:
                assert last_error is not None
                raise last_error

            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        finally:
            _CONNECTION_LIMIT.release()

    def init_schema(self) -> None:
        """Wykonuje schema.sql idempotentnie (CREATE ... IF NOT EXISTS)."""
        if self._schema_ready:
            return

        raw_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8").format(schema=self._schema)
        without_comments = re.sub(r"^\s*--.*$", "", raw_sql, flags=re.MULTILINE)
        statements = [statement.strip() for statement in without_comments.split(";") if statement.strip()]

        with self._connect() as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)

        self._schema_ready = True

    def log_job_start(
        self,
        url: str,
        mode: str,
        output_format: str,
        client_ip_hash: str | None = None,
    ) -> int:
        self.init_schema()
        query = sql.SQL(
            "INSERT INTO {table} (source_url, mode, output_format, status, client_ip_hash) "
            "VALUES (%s, %s, %s, 'running', %s) RETURNING id"
        ).format(table=self._table)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (url, mode, output_format, client_ip_hash))
                row = cur.fetchone()

        try:
            self._schedule_purge()
        except Exception as exc:
            # Retencja jest best effort — nigdy nie może zepsuć startu pobierania.
            logger.warning("scheduling purge_old_jobs failed: %s", type(exc).__name__)

        return row["id"]

    def log_job_finish(
        self,
        job_id: int,
        status: str,
        duration_ms: int | None = None,
        file_size_bytes: int | None = None,
        error_message: str | None = None,
    ) -> None:
        self.init_schema()
        query = sql.SQL(
            "UPDATE {table} SET finished_at = now(), status = %s, duration_ms = %s, "
            "file_size_bytes = %s, error_message = %s WHERE id = %s"
        ).format(table=self._table)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (status, duration_ms, file_size_bytes, error_message, job_id))

    def get_recent_history(
        self, client_ip_hash: str, days: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Zadania WYŁĄCZNIE danego klienta z ostatnich `days` dni. Kolumna
        client_ip_hash nie jest zwracana (UI jej nie potrzebuje)."""
        self.init_schema()
        query = sql.SQL(
            "SELECT id, created_at, finished_at, source_url, mode, output_format, "
            "status, duration_ms, file_size_bytes, error_message "
            "FROM {table} "
            "WHERE client_ip_hash = %s AND created_at >= now() - make_interval(days => %s) "
            "ORDER BY created_at DESC LIMIT %s"
        ).format(table=self._table)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (client_ip_hash, days, limit))
                return cur.fetchall()

    def purge_old_jobs(self, days: int) -> int:
        """Kasuje wiersze starsze niż `days` dni. Best effort: wyjątki są
        łapane i logowane (tylko nazwa klasy — bez danych użytkownika), zwraca
        liczbę skasowanych wierszy albo 0."""
        try:
            self.init_schema()
            query = sql.SQL(
                "DELETE FROM {table} WHERE created_at < now() - make_interval(days => %s)"
            ).format(table=self._table)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (days,))
                    deleted = cur.rowcount
        except Exception as exc:
            logger.warning("purge_old_jobs failed: %s", type(exc).__name__)
            return 0
        logger.info("purge_old_jobs: deleted %s rows older than %s days", deleted, days)
        return deleted

    def _schedule_purge(self) -> bool:
        """Uruchamia purge w tle, najwyżej raz na _PURGE_INTERVAL_SECONDS na
        proces. Okno jest rezerwowane PRZED startem (nieudany purge też czeka
        do następnego okna — nie ponawiamy w pętli przy niedostępnej bazie)."""
        now = self._clock()
        with self._purge_lock:
            if self._last_purge_at is not None and now - self._last_purge_at < _PURGE_INTERVAL_SECONDS:
                return False
            self._last_purge_at = now
        days = settings.history_retention_days
        self._spawn(lambda: self.purge_old_jobs(days))
        return True
