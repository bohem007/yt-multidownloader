# Artefakt wdrożeniowy dla Hugging Face Spaces (Docker SDK) — NIE uruchamiać
# lokalnie do developmentu (patrz CLAUDE.md, "Model wdrożenia").

FROM python:3.13-slim

# ffmpeg: wymagany przez src/profiles.py (FFmpegVideoRemuxer/FFmpegMetadata
# dla wideo, FFmpegExtractAudio dla mp3/flac). yt-dlp bierze binarkę z PATH —
# nigdzie w kodzie nie jest skonfigurowana jawna ścieżka (ffmpeg_location),
# więc musi być zainstalowana systemowo i widoczna w PATH.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv — statyczna binarka z oficjalnego obrazu narzędziowego, bez pip install.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Użytkownik nie-root, uid 1000 (wymóg Hugging Face Spaces).
RUN useradd --create-home --uid 1000 --shell /bin/bash appuser

ENV HOME=/home/appuser \
    ENVIRONMENT=production \
    STORAGE_BASE_DIR=/home/appuser/storage \
    UV_PROJECT_ENVIRONMENT=/home/appuser/.venv \
    PATH=/home/appuser/.venv/bin:$PATH \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /home/appuser/app

# Warstwa zależności osobno od kodu (cache warstw Dockera) — deterministyczna
# instalacja z zablokowanego lockfile'a, bez grupy dev (pytest).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Kod aplikacji. Bez .env/cookies/.git/tests/.venv — wykluczone przez
# .dockerignore niezależnie od tego, co jest tu wymienione.
COPY asgi_app.py app.py schema.sql ./
COPY src ./src

# Katalog na pliki tymczasowe/ZIP-y (src/storage.py, src/downloads.py) —
# utworzony i należący do appuser; chown obejmuje też .venv z poprzedniego
# kroku (był tworzony jako root).
RUN mkdir -p "$STORAGE_BASE_DIR" && chown -R appuser:appuser /home/appuser

USER appuser

EXPOSE 7860

# Bez HEALTHCHECK — HF Spaces samo sonduje $app_port z README.md.
#
# --no-sync: bez tego `uv run` przy KAŻDYM starcie kontenera sam od siebie
# sprawdza i dociąga grupę dev (pytest + zależności, w tym pygments) z PyPI —
# zweryfikowane budując i uruchamiając ten obraz lokalnie. Środowisko jest
# już kompletne i zamrożone przez `uv sync --frozen --no-dev` wyżej; --no-sync
# każe `uv run` użyć go as-is, bez sieci i bez cichego odejścia od --no-dev.
CMD ["uv", "run", "--no-sync", "streamlit", "run", "asgi_app.py", "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
