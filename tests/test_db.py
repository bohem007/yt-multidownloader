"""Testy integracyjne src/db.py — wymagają realnego DATABASE_URL (Neon).

Pomijane automatycznie, gdy DATABASE_URL nie jest ustawiony (np. na CI
bez dostępu do bazy). Zapisują i czyszczą własne wiersze w tabeli
{DB_SCHEMA}.jobs, więc lokalnie warto trzymać DB_SCHEMA=dev (patrz
CLAUDE.md — izolacja danych testowych od produkcyjnych).

Oznaczone `slow` + `db_integration`: poza szybkim zestawem i poza izolacją
bazy z tests/conftest.py — jedyne testy, które wolno łączyć z Neon.
"""

import uuid

import pytest

from src.config import settings

pytestmark = [
    pytest.mark.slow,
    pytest.mark.db_integration,
    pytest.mark.skipif(
        not settings.database_url,
        reason="Wymaga DATABASE_URL wskazującego na rzeczywistą bazę Neon (patrz .env)",
    ),
]


@pytest.fixture
def db():
    from src.db import Database

    return Database()


def test_log_job_start_and_finish_roundtrip(db):
    marker_url = f"https://youtu.be/test-{uuid.uuid4().hex}"

    job_id = db.log_job_start(
        marker_url, mode="audio", output_format="mp3", client_ip_hash="testhash"
    )
    assert isinstance(job_id, int)

    db.log_job_finish(job_id, status="done", duration_ms=1234, file_size_bytes=5678)

    history = db.get_recent_history(limit=50)
    matching = [row for row in history if row["id"] == job_id]
    assert len(matching) == 1

    row = matching[0]
    assert row["source_url"] == marker_url
    assert row["mode"] == "audio"
    assert row["output_format"] == "mp3"
    assert row["status"] == "done"
    assert row["duration_ms"] == 1234
    assert row["file_size_bytes"] == 5678
    assert row["client_ip_hash"] == "testhash"
    assert row["finished_at"] is not None


def test_log_job_finish_records_error_message(db):
    marker_url = f"https://youtu.be/test-{uuid.uuid4().hex}"
    job_id = db.log_job_start(
        marker_url, mode="video", output_format="mp4", client_ip_hash="testhash"
    )

    db.log_job_finish(job_id, status="error", error_message="bot-check")

    history = db.get_recent_history(limit=50)
    matching = [row for row in history if row["id"] == job_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "error"
    assert matching[0]["error_message"] == "bot-check"


def test_get_recent_history_orders_by_created_at_desc(db):
    first_url = f"https://youtu.be/test-{uuid.uuid4().hex}"
    second_url = f"https://youtu.be/test-{uuid.uuid4().hex}"

    first_id = db.log_job_start(first_url, mode="video", output_format="mp4")
    second_id = db.log_job_start(second_url, mode="video", output_format="mp4")

    history = db.get_recent_history(limit=2)
    ids_in_order = [row["id"] for row in history if row["id"] in (first_id, second_id)]

    assert ids_in_order[0] == second_id
