"""Testy src/config.py — wartości domyślne i nadpisywanie przez env.

Settings.from_env przyjmuje dowolną mapę, więc testy nie muszą
monkeypatchować os.environ ani przeładowywać modułu.
"""

import pytest

import logging

from src.config import Settings, warn_if_insecure


def test_defaults_match_claude_md():
    s = Settings.from_env({})

    assert s.environment == "local"
    assert s.database_url == ""
    assert s.db_schema == "dev"
    assert s.max_file_size_mb == 500
    assert s.max_playlist_items == 10
    assert s.max_playlist_rd_items == 20
    assert s.max_zip_size_mb == 500
    assert s.max_concurrent_jobs == 2
    assert s.item_download_timeout_seconds == 180
    assert s.download_link_ttl_minutes == 30
    assert s.rate_limit_per_ip == 10
    assert s.rate_limiting_enabled is True
    assert s.ip_hash_secret == ""
    assert s.history_retention_days == 5
    assert s.environment_explicit is False


def test_env_vars_override_defaults():
    env = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pass@host-pooler.neon.tech/db",
        "DB_SCHEMA": "public",
        "MAX_FILE_SIZE_MB": "250",
        "MAX_PLAYLIST_ITEMS": "5",
        "MAX_PLAYLIST_RD_ITEMS": "7",
        "MAX_ZIP_SIZE_MB": "300",
        "MAX_CONCURRENT_JOBS": "4",
        "ITEM_DOWNLOAD_TIMEOUT_SECONDS": "60",
        "DOWNLOAD_LINK_TTL_MINUTES": "10",
        "RATE_LIMIT_PER_IP": "20",
        "RATE_LIMITING_ENABLED": "false",
        "IP_HASH_SECRET": "super-secret",
        "HISTORY_RETENTION_DAYS": "14",
    }
    s = Settings.from_env(env)

    assert s.environment == "production"
    assert s.database_url == env["DATABASE_URL"]
    assert s.db_schema == "public"
    assert s.max_file_size_mb == 250
    assert s.max_playlist_items == 5
    assert s.max_playlist_rd_items == 7
    assert s.max_zip_size_mb == 300
    assert s.max_concurrent_jobs == 4
    assert s.item_download_timeout_seconds == 60
    assert s.download_link_ttl_minutes == 10
    assert s.rate_limit_per_ip == 20
    assert s.rate_limiting_enabled is False
    assert s.ip_hash_secret == "super-secret"
    assert s.history_retention_days == 14
    assert s.environment_explicit is True


def test_max_playlist_rd_items_is_independent_of_max_playlist_items():
    s = Settings.from_env({"MAX_PLAYLIST_ITEMS": "3"})

    assert s.max_playlist_items == 3
    assert s.max_playlist_rd_items == 20


def test_max_playlist_rd_items_rejects_non_numeric_value():
    with pytest.raises(ValueError):
        Settings.from_env({"MAX_PLAYLIST_RD_ITEMS": "dwadziescia"})


def test_rate_limiting_enabled_accepts_common_truthy_and_falsy_strings():
    for value in ("1", "true", "True", "yes", "on"):
        assert Settings.from_env({"RATE_LIMITING_ENABLED": value}).rate_limiting_enabled is True

    for value in ("0", "false", "False", "no", "off"):
        assert Settings.from_env({"RATE_LIMITING_ENABLED": value}).rate_limiting_enabled is False


def test_settings_is_frozen():
    s = Settings.from_env({})

    try:
        s.max_file_size_mb = 999  # type: ignore[misc]
    except Exception as exc:
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("Settings powinien być frozen=True")


def test_module_level_settings_is_settings_instance():
    from src.config import settings

    assert isinstance(settings, Settings)


def test_database_url_built_from_pg_vars_when_not_set_explicitly():
    env = {
        "PGHOST": "ep-example-pooler.eu-central-1.aws.neon.tech",
        "PGDATABASE": "mydatabase",
        "PGUSER": "owner",
        "PGPASSWORD": "myp@ss/word",
        "PGSSLMODE": "require",
        "PGCHANNELBINDING": "require",
    }
    s = Settings.from_env(env)

    assert s.database_url == (
        "postgresql://owner:myp%40ss%2Fword@ep-example-pooler.eu-central-1.aws.neon.tech"
        "/mydatabase?sslmode=require&channel_binding=require"
    )


def test_explicit_database_url_wins_over_pg_vars():
    env = {
        "DATABASE_URL": "postgresql://explicit@host-pooler.neon.tech/db",
        "PGHOST": "should-be-ignored",
    }
    s = Settings.from_env(env)

    assert s.database_url == "postgresql://explicit@host-pooler.neon.tech/db"


def test_database_url_empty_when_neither_provided():
    assert Settings.from_env({}).database_url == ""


@pytest.mark.parametrize("value", ["0", "-3"])
def test_history_retention_days_rejects_values_below_one(value):
    with pytest.raises(ValueError, match="HISTORY_RETENTION_DAYS"):
        Settings.from_env({"HISTORY_RETENTION_DAYS": value})


def test_history_retention_days_rejects_non_numeric_value():
    with pytest.raises(ValueError):
        Settings.from_env({"HISTORY_RETENTION_DAYS": "kilka"})


def test_environment_is_not_explicit_when_missing_or_empty():
    assert Settings.from_env({}).environment_explicit is False
    assert Settings.from_env({"ENVIRONMENT": ""}).environment_explicit is False
    assert Settings.from_env({"ENVIRONMENT": "local"}).environment_explicit is True


def test_warn_if_insecure_warns_only_for_production_with_empty_secret(caplog):
    with caplog.at_level(logging.WARNING, logger="src.config"):
        warn_if_insecure(Settings.from_env({"ENVIRONMENT": "production"}))
        assert "IP_HASH_SECRET" in caplog.text

        caplog.clear()
        warn_if_insecure(Settings.from_env({"ENVIRONMENT": "production", "IP_HASH_SECRET": "s3cret"}))
        warn_if_insecure(Settings.from_env({"ENVIRONMENT": "local"}))
        warn_if_insecure(Settings.from_env({}))
        assert caplog.text == ""


def test_warn_if_insecure_never_logs_the_secret_value(caplog):
    with caplog.at_level(logging.WARNING, logger="src.config"):
        warn_if_insecure(Settings.from_env({"ENVIRONMENT": "production", "IP_HASH_SECRET": ""}))
    assert "s3cret" not in caplog.text
