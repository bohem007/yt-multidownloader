"""PUNKT WEJŚCIA SERWERA — uruchamiaj TYM plikiem:

    uv run streamlit run asgi_app.py

Relacja z app.py (literalnie): `st.App("app.py", ...)` WCZYTUJE app.py jako
skrypt UI Streamlita i dokłada do tego samego serwera trasę HTTP pobierania
dużych plików (src/download_routes.py). To nie są dwa równoległe
punkty wejścia — app.py to wyłącznie UI (i cel testów AppTest), a ten plik
jest jedynym, który go uruchamia z pełną funkcjonalnością.

Dlaczego osobny plik: skrypt UI wykonuje się WEWNĄTRZ serwera, więc sam nie
może dodać do niego trasy HTTP — to potrafi tylko owijka st.App.

Uwaga: przy uruchomieniu przez st.App Streamlit 1.63 nie otwiera
przeglądarki automatycznie (robi to tylko klasyczne `streamlit run app.py`)
— otwórz http://localhost:8501 ręcznie.
"""

from contextlib import asynccontextmanager

import streamlit as st
from starlette.routing import Route

from src import downloads, storage
from src.download_routes import DOWNLOAD_ROUTE, download_endpoint
from src.engine import warn_if_deno_missing


@asynccontextmanager
async def lifespan(_app):
    warn_if_deno_missing()
    downloads.purge_all()
    # Katalogi pojedynczych jobów (storage.py) nie mają swojego TTL-sprzątania
    # w tle jak linki ZIP powyżej — po awarii procesu w trakcie pobierania
    # (crash, restart kontenera na HF) zostają osierocone na dysku. Bezpieczne
    # tylko tu, na starcie: żaden job nie jest jeszcze w toku.
    storage.purge_all()
    downloads.set_route_enabled(True)
    yield
    downloads.set_route_enabled(False)
    downloads.purge_all()


app = st.App(
    "app.py",
    routes=[Route(DOWNLOAD_ROUTE, download_endpoint)],
    lifespan=lifespan,
)
