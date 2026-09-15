-- Schemat bazy danych YT MultiDownloader.
--
-- Placeholder {schema} jest podstawiany przez src/db.py na podstawie
-- zmiennej środowiskowej DB_SCHEMA (patrz src/config.py) — pozwala to
-- rozdzielić dane deweloperskie (domyślnie "dev") od produkcyjnych
-- ("public") bez duplikowania tego pliku.
--
-- Brak systemu migracji: zmiany schematu = ręczny
-- ALTER TABLE IF EXISTS {schema}.jobs ADD COLUMN IF NOT EXISTS ...
-- dopisany obok istniejącego CREATE TABLE.

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.jobs (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    source_url      TEXT NOT NULL,
    mode            VARCHAR(20) NOT NULL,
    output_format   VARCHAR(10) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',
    duration_ms     INTEGER,
    file_size_bytes BIGINT,
    error_message   TEXT,
    client_ip_hash  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON {schema}.jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON {schema}.jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_mode ON {schema}.jobs (mode);
