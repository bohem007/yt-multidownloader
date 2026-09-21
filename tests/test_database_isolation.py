"""Kontrakt izolacji z tests/conftest.py: testy nie łączą się z bazą i nie
zależą od limitów z lokalnego `.env` (patrz docstring conftest.py)."""

import psycopg
import pytest

from src.config import settings
from src.db import Database


def test_database_methods_are_replaced_by_recording_fake(database_calls):
    db = Database()

    job_id = db.log_job_start("https://youtu.be/x", mode="video", output_format="mp4")
    db.log_job_finish(job_id, status="done", duration_ms=5)

    assert job_id == 1
    assert database_calls.starts[0]["url"] == "https://youtu.be/x"
    assert database_calls.finishes == [
        {
            "job_id": 1,
            "status": "done",
            "duration_ms": 5,
            "file_size_bytes": None,
            "error_message": None,
        }
    ]
    assert db.get_recent_history() == []
    assert database_calls.history_reads == 1


def test_psycopg_connect_is_blocked_without_retry_delay():
    # Ścieżki spoza atrapy (np. Database.init_schema) nie mogą dojść do sieci.
    with pytest.raises(RuntimeError, match="nie łączą się z bazą"):
        Database().init_schema()
    with pytest.raises(RuntimeError, match="nie łączą się z bazą"):
        psycopg.connect("postgresql://user:pass@example.invalid/db")


def test_limits_are_pinned_regardless_of_local_env():
    assert settings.max_playlist_items == 10
    assert settings.max_playlist_rd_items == 20
    assert settings.max_zip_size_mb == 500
    assert settings.max_file_size_mb == 500
    assert settings.max_concurrent_jobs == 2
