"""Jedyny moduł w projekcie, w którym wolno odczytywać zmienne środowiskowe.

Wszystkie inne moduły importują gotowy obiekt `settings` stąd — nigdy
os.getenv/os.environ bezpośrednio. Patrz CLAUDE.md, sekcja
"Zasada nadrzędna: symulacja limitów przez konfigurację".
"""

from __future__ import annotations

import logging
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
    # Playlisty Mix/Radio (list=RD…) — limit rozmiaru MIGAWKI listy (patrz
    # engine.py::snapshot_playlist); dla nich ZASTĘPUJE max_playlist_items.
    max_playlist_rd_items: int = 20
    max_zip_size_mb: int = 500
    max_concurrent_jobs: int = 2
    item_download_timeout_seconds: int = 180
    download_link_ttl_minutes: int = 30
    rate_limit_per_ip: int = 10
    rate_limiting_enabled: bool = True
    ip_hash_secret: str = ""
    # Retencja historii zadań (widok "Historia" + kasowanie starych wierszy).
    history_retention_days: int = 5
    # Twardy limit czasu (sekundy) na SAM psycopg.connect() — bez tego
    # niedostępna/wolna baza blokuje wątek Streamlita bezterminowo (obserwowane:
    # >6 minut lokalnie bez .env). Niezależny od retry/backoff w db.py::_connect
    # (_COLD_START_ATTEMPTS) — ten limit dotyczy KAŻDEJ pojedynczej próby.
    db_connect_timeout_seconds: int = 5
    # True tylko gdy ENVIRONMENT był jawnie ustawiony (nie wzięty z domyślnej
    # wartości "local") — fail-closed dla historii sesji o nieznanym adresie.
    environment_explicit: bool = False
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
        history_retention_days = int(
            env.get("HISTORY_RETENTION_DAYS", defaults.history_retention_days)
        )
        if history_retention_days < 1:
            raise ValueError(
                f"HISTORY_RETENTION_DAYS musi być >= 1 (jest: {history_retention_days})"
            )
        db_connect_timeout_seconds = int(
            env.get("DB_CONNECT_TIMEOUT_SECONDS", defaults.db_connect_timeout_seconds)
        )
        if db_connect_timeout_seconds < 1:
            raise ValueError(
                f"DB_CONNECT_TIMEOUT_SECONDS musi być >= 1 (jest: {db_connect_timeout_seconds})"
            )
        max_playlist_items = int(env.get("MAX_PLAYLIST_ITEMS", defaults.max_playlist_items))
        if max_playlist_items < 1:
            raise ValueError(
                f"MAX_PLAYLIST_ITEMS musi być >= 1 (jest: {max_playlist_items}) — "
                "0 przycina playlisty do pustego wyniku bez błędu, patrz engine.py::submit_playlist"
            )
        max_playlist_rd_items = int(
            env.get("MAX_PLAYLIST_RD_ITEMS", defaults.max_playlist_rd_items)
        )
        if max_playlist_rd_items < 1:
            raise ValueError(
                f"MAX_PLAYLIST_RD_ITEMS musi być >= 1 (jest: {max_playlist_rd_items})"
            )
        return cls(
            environment=environment,
            database_url=database_url,
            db_schema=env.get("DB_SCHEMA", defaults.db_schema),
            max_file_size_mb=int(env.get("MAX_FILE_SIZE_MB", defaults.max_file_size_mb)),
            max_playlist_items=max_playlist_items,
            max_playlist_rd_items=max_playlist_rd_items,
            max_zip_size_mb=int(env.get("MAX_ZIP_SIZE_MB", defaults.max_zip_size_mb)),
            max_concurrent_jobs=int(env.get("MAX_CONCURRENT_JOBS", defaults.max_concurrent_jobs)),
            item_download_timeout_seconds=int(
                env.get("ITEM_DOWNLOAD_TIMEOUT_SECONDS", defaults.item_download_timeout_seconds)
            ),
            download_link_ttl_minutes=int(
                env.get("DOWNLOAD_LINK_TTL_MINUTES", defaults.download_link_ttl_minutes)
            ),
            rate_limit_per_ip=int(env.get("RATE_LIMIT_PER_IP", defaults.rate_limit_per_ip)),
            rate_limiting_enabled=_parse_bool(
                env.get("RATE_LIMITING_ENABLED"), defaults.rate_limiting_enabled
            ),
            ip_hash_secret=env.get("IP_HASH_SECRET", defaults.ip_hash_secret),
            history_retention_days=history_retention_days,
            db_connect_timeout_seconds=db_connect_timeout_seconds,
            environment_explicit=bool(env.get("ENVIRONMENT")),
            storage_base_dir=storage_base_dir,
        )


def warn_if_insecure(current: Settings) -> None:
    """Ostrzega (bez logowania wartości) o pustym IP_HASH_SECRET w produkcji —
    hash IP bez sekretu da się odwrócić przeszukaniem przestrzeni IPv4."""
    if current.environment == "production" and not current.ip_hash_secret:
        logging.getLogger(__name__).warning(
            "IP_HASH_SECRET jest pusty w ENVIRONMENT=production — client_ip_hash "
            "nie jest chroniony sekretem. Ustaw IP_HASH_SECRET w HF Secrets."
        )


settings = Settings.from_env(os.environ)
warn_if_insecure(settings)
