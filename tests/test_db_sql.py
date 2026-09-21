"""Testy PRAWDZIWEGO SQL warstwy bazy (src/db.py) na fałszywym połączeniu.

Znacznik `db_sql`: fixture `database_calls` nie podmienia metod Database, ale
psycopg.connect jest nadal zablokowane — połączenie wstrzykujemy do
`Database._connect`. Zero sieci, zero sleep (zegar i "wątek" są wstrzykiwane).
"""

import logging
import threading
from contextlib import contextmanager

import pytest

from src.config import settings
from src.db import _PURGE_INTERVAL_SECONDS, Database, _spawn_daemon

pytestmark = pytest.mark.db_sql


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self.rowcount = 7

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self._conn.executed.append((query.as_string(None), params))

    def fetchone(self):
        return {"id": 42}

    def fetchall(self):
        return [{"id": 1, "source_url": "https://youtu.be/x"}]


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple | None]] = []

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture
def conn(monkeypatch) -> FakeConnection:
    fake = FakeConnection()

    @contextmanager
    def _fake_connect(self):
        yield fake

    monkeypatch.setattr(Database, "_connect", _fake_connect)
    return fake


def _make_db(**kwargs) -> Database:
    db = Database(**kwargs)
    db._schema_ready = True  # bez wykonywania schema.sql na fałszywym połączeniu
    return db


def test_history_query_filters_by_client_hash_and_days_with_placeholders(conn):
    rows = _make_db().get_recent_history("abc123", 5)

    (query, params), = conn.executed
    assert "WHERE client_ip_hash = %s" in query
    assert "created_at >= now() - make_interval(days => %s)" in query
    assert "ORDER BY created_at DESC" in query
    assert query.rstrip().endswith("LIMIT %s")
    assert params == ("abc123", 5, 20)
    assert rows == [{"id": 1, "source_url": "https://youtu.be/x"}]


def test_history_query_does_not_select_client_ip_hash_column(conn):
    _make_db().get_recent_history("abc123", 5)

    query = conn.executed[0][0]
    select_list = query.split(" FROM ")[0]
    assert "client_ip_hash" not in select_list


def test_history_query_passes_custom_limit_and_days(conn):
    _make_db().get_recent_history("abc123", 14, limit=3)

    assert conn.executed[0][1] == ("abc123", 14, 3)


def test_history_values_never_get_concatenated_into_sql(conn):
    hostile = "x'; DROP TABLE jobs; --"
    _make_db().get_recent_history(hostile, 5)

    query, params = conn.executed[0]
    assert hostile not in query
    assert params[0] == hostile


def test_purge_old_jobs_deletes_by_created_at_with_days_parameter(conn):
    deleted = _make_db().purge_old_jobs(5)

    (query, params), = conn.executed
    assert query.startswith("DELETE FROM")
    assert '"jobs"' in query
    assert "WHERE created_at < now() - make_interval(days => %s)" in query
    assert params == (5,)
    assert deleted == 7


def test_purge_old_jobs_swallows_exceptions_and_logs_no_user_data(monkeypatch, caplog):
    @contextmanager
    def _broken_connect(self):
        raise RuntimeError("https://youtu.be/secret-video from 203.0.113.7")
        yield

    monkeypatch.setattr(Database, "_connect", _broken_connect)

    with caplog.at_level(logging.WARNING, logger="src.db"):
        assert _make_db().purge_old_jobs(5) == 0

    assert "purge_old_jobs failed: RuntimeError" in caplog.text
    assert "youtu.be" not in caplog.text
    assert "203.0.113.7" not in caplog.text


def _db_with_recording_purge(clock_values):
    ticks = iter(clock_values)
    purges: list[int] = []
    db = _make_db(clock=lambda: next(ticks), spawn=lambda fn: fn())
    db.purge_old_jobs = lambda days: purges.append(days) or 0
    return db, purges


def test_log_job_start_schedules_purge_with_configured_retention(conn):
    db, purges = _db_with_recording_purge([100.0])

    job_id = db.log_job_start("https://youtu.be/x", "video", "mp4", client_ip_hash="h")

    assert job_id == 42
    assert purges == [settings.history_retention_days]
    insert_query, insert_params = conn.executed[0]
    assert insert_query.startswith("INSERT INTO")
    assert insert_params == ("https://youtu.be/x", "video", "mp4", "h")


def test_purge_is_throttled_to_one_run_per_interval(conn):
    interval = _PURGE_INTERVAL_SECONDS
    db, purges = _db_with_recording_purge([0.0, 10.0, interval - 1, interval, interval + 5, 2 * interval])

    for _ in range(6):
        db.log_job_start("https://youtu.be/x", "video", "mp4")

    # t=0 (start), t=interval (okno minęło), t=2*interval (kolejne okno)
    assert len(purges) == 3


def test_failing_scheduler_never_breaks_job_start(conn, caplog):
    def _boom(fn):
        raise RuntimeError("cannot start thread")

    db = _make_db(clock=lambda: 0.0, spawn=_boom)

    with caplog.at_level(logging.WARNING, logger="src.db"):
        assert db.log_job_start("https://youtu.be/x", "video", "mp4") == 42

    assert "scheduling purge_old_jobs failed: RuntimeError" in caplog.text


def test_spawn_daemon_runs_function_on_a_daemon_thread():
    done = threading.Event()
    seen: dict[str, bool] = {}

    def _work():
        seen["daemon"] = threading.current_thread().daemon
        done.set()

    _spawn_daemon(_work)

    assert done.wait(timeout=5.0)
    assert seen["daemon"] is True
