"""Testy src/config.py — wartości domyślne i nadpisywanie przez env.

Settings.from_env przyjmuje dowolną mapę, więc testy nie muszą
monkeypatchować os.environ ani przeładowywać modułu.
"""

from src.config import Settings


def test_defaults_match_claude_md():
    s = Settings.from_env({})

    assert s.environment == "local"
    assert s.database_url == ""
    assert s.db_schema == "dev"
    assert s.max_file_size_mb == 500
    assert s.max_playlist_items == 10
    assert s.max_zip_size_mb == 500
    assert s.max_concurrent_jobs == 2
    assert s.download_link_ttl_minutes == 30
    assert s.rate_limit_per_ip == 10
    assert s.rate_limiting_enabled is True
    assert s.ip_hash_secret == ""


def test_env_vars_override_defaults():
    env = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pass@host-pooler.neon.tech/db",
        "DB_SCHEMA": "public",
        "MAX_FILE_SIZE_MB": "250",
        "MAX_PLAYLIST_ITEMS": "5",
        "MAX_ZIP_SIZE_MB": "300",
        "MAX_CONCURRENT_JOBS": "4",
        "DOWNLOAD_LINK_TTL_MINUTES": "10",
        "RATE_LIMIT_PER_IP": "20",
        "RATE_LIMITING_ENABLED": "false",
        "IP_HASH_SECRET": "super-secret",
    }
    s = Settings.from_env(env)

    assert s.environment == "production"
    assert s.database_url == env["DATABASE_URL"]
    assert s.db_schema == "public"
    assert s.max_file_size_mb == 250
    assert s.max_playlist_items == 5
    assert s.max_zip_size_mb == 300
    assert s.max_concurrent_jobs == 4
    assert s.download_link_ttl_minutes == 10
    assert s.rate_limit_per_ip == 20
    assert s.rate_limiting_enabled is False
    assert s.ip_hash_secret == "super-secret"


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
