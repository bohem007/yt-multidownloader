---
title: YT MultiDownloader
emoji: 🎬
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# YT MultiDownloader

Aplikacja webowa (Streamlit) do pobierania z YouTube przez `yt-dlp`: wideo (MP4), audio (MP3/FLAC), napisy (SRT/VTT), transkrypt (TXT) oraz playlisty (przycinane do limitu pozycji, w turach wg limitu rozmiaru ZIP-a).

**Uruchomienie lokalne:** `uv run streamlit run asgi_app.py` (wymaga `ffmpeg` w `PATH` i skonfigurowanego `.env` — patrz `.env.example`).

**Zmienne środowiskowe** — pełna lista i wartości domyślne w `.env.example`. Sekrety: `DATABASE_URL`, `IP_HASH_SECRET`. Limity: `MAX_FILE_SIZE_MB`, `MAX_PLAYLIST_ITEMS`, `MAX_ZIP_SIZE_MB`, `MAX_CONCURRENT_JOBS`, `DOWNLOAD_LINK_TTL_MINUTES`, `RATE_LIMIT_PER_IP`, `HISTORY_RETENTION_DAYS` i inne.

**Limity darmowego tieru:** rozmiar pliku, liczba pozycji playlisty, współbieżność zadań i liczba żądań na adres IP są ograniczone — aplikacja informuje o tym w interfejsie.

**cookies.txt:** opcjonalny upload w UI (filmy z ograniczeniem wiekowym / bot-check YouTube) — plik żyje tylko w pamięci sesji i katalogu roboczym zadania, nigdy nie jest zapisywany trwale ani commitowany do repozytorium.

Używaj zgodnie z Regulaminem YouTube i obowiązującym prawem autorskim — pobieraj wyłącznie treści, do których masz prawo.
