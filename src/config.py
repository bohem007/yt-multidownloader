"""Jedyny moduł w projekcie, w którym wolno odczytywać zmienne środowiskowe.

Wszystkie inne moduły importują gotowy obiekt `settings` stąd — nigdy
os.getenv/os.environ bezpośrednio. Patrz CLAUDE.md, sekcja
"Zasada nadrzędna: symulacja limitów przez konfigurację".
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return default


def _build_database_url_from_pg_vars(env: Mapping[str, str]) -> str:
    """Składa connection string z PGHOST/PGDATABASE/PGUSER/PGPASSWORD/...

    Neon udostępnia dane połączenia jako osobne zmienne PG* — jeśli
    DATABASE_URL nie jest ustawiony wprost, budujemy go z nich, żeby
    .env nie musiał duplikować tych samych danych pod dwiema postaciami.
    """
    host = env.get("PGHOST")
    if not host:
        return ""

    user = quote(env.get("PGUSER", ""), safe="")
    password = quote(env.get("PGPASSWORD", ""), safe="")
    dbname = env.get("PGDATABASE", "")

    query_params = []
    sslmode = env.get("PGSSLMODE")
    if sslmode:
        query_params.append(f"sslmode={sslmode}")
    channel_binding = env.get("PGCHANNELBINDING")
    if channel_binding:
        query_params.append(f"channel_binding={channel_binding}")
    query = f"?{'&'.join(query_params)}" if query_params else ""

    return f"postgresql://{user}:{password}@{host}/{dbname}{query}"


@dataclass(frozen=True)
class Settings:
    environment: str = "local"
    database_url: str = ""
    db_schema: str = "dev"
    max_file_size_mb: int = 500
    max_playlist_items: int = 10
    max_concurrent_jobs: int = 2
    rate_limit_per_ip: int = 10
    rate_limiting_enabled: bool = True
    ip_hash_secret: str = ""
    storage_base_dir: str = field(default_factory=tempfile.gettempdir)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        """Buduje Settings z dowolnej mapy env — bez dotykania os.environ.

        Dzięki temu testy mogą sprawdzać wartości domyślne i nadpisania
        bez monkeypatchowania procesu.
        """
        defaults = cls()
        environment = env.get("ENVIRONMENT", defaults.environment)
        database_url = (
            env.get("DATABASE_URL")
            or _build_database_url_from_pg_vars(env)
            or defaults.database_url
        )
        storage_base_dir = env.get("STORAGE_BASE_DIR") or (
            tempfile.gettempdir() if environment == "local" else "/tmp"
        )
        return cls(
            environment=environment,
            database_url=database_url,
            db_schema=env.get("DB_SCHEMA", defaults.db_schema),
            max_file_size_mb=int(env.get("MAX_FILE_SIZE_MB", defaults.max_file_size_mb)),
            max_playlist_items=int(env.get("MAX_PLAYLIST_ITEMS", defaults.max_playlist_items)),
            max_concurrent_jobs=int(env.get("MAX_CONCURRENT_JOBS", defaults.max_concurrent_jobs)),
            rate_limit_per_ip=int(env.get("RATE_LIMIT_PER_IP", defaults.rate_limit_per_ip)),
            rate_limiting_enabled=_parse_bool(
                env.get("RATE_LIMITING_ENABLED"), defaults.rate_limiting_enabled
            ),
            ip_hash_secret=env.get("IP_HASH_SECRET", defaults.ip_hash_secret),
            storage_base_dir=storage_base_dir,
        )


settings = Settings.from_env(os.environ)
