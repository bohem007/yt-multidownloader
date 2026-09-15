# YT MultiDownloader — CLAUDE.md

Kontekst projektu dla Claude Code. Ten plik jest źródłem prawdy o architekturze —
przy sprzeczności między kodem a tym plikiem, zgłoś rozbieżność zamiast zgadywać.

## Cel projektu

Aplikacja webowa (Streamlit) do pobierania treści z YouTube przez `yt-dlp`:
wideo (MP4), audio (MP3/FLAC), playlisty (limit 10 pozycji), napisy (SRT/VTT),
transkrypt bez timestampów (TXT). Docelowy hosting: Hugging Face Spaces
(Docker SDK, widoczność **Public**, darmowy tier). Baza: Neon Postgres
(darmowy tier), tylko anonimowa historia zadań.

**Model wdrożenia:** implementacja i testy lokalnie (Windows 11 + VS Code + UV +
Python 3.13), Docker **wyłącznie** jako artefakt wdrożeniowy dla HF Spaces —
nigdy nie uruchamiaj lokalnie w Dockerze.

## Zasada nadrzędna: symulacja limitów przez konfigurację

Wszystkie limity darmowego tieru HF (rozmiar pliku, liczba pozycji playlisty,
współbieżność) MUSZĄ być odczytywane z `src/config.py` / zmiennych środowiskowych,
NIGDY zakodowane na twardo w logice. Te same nazwy zmiennych i wartości domyślne
obowiązują lokalnie (`.env`) i w produkcji (HF Secrets/Variables) — patrz `.env.example`.

Ustalone wartości domyślne:
- `MAX_FILE_SIZE_MB=500`
- `MAX_PLAYLIST_ITEMS=10`
- `MAX_CONCURRENT_JOBS=2`
- `RATE_LIMIT_PER_IP=10` (żądań/godzinę)
- `RATE_LIMITING_ENABLED` — domyślnie włączone w `production`, opcjonalne lokalnie
- `ENVIRONMENT=local|production`

## Stack technologiczny

| Warstwa | Technologia |
|---|---|
| Silnik pobierania | `yt-dlp` jako moduł Python (import, nie subprocess) |
| Media | `ffmpeg` |
| UI | Streamlit |
| Baza | Neon Postgres (darmowy tier), `psycopg` bez ORM |
| Env/deps | UV, Python 3.13 |
| Hosting docelowy | Hugging Face Spaces, Docker SDK, Public |

## Stan projektu (aktualny)

Środowisko developerskie jest już gotowe — Claude Code nie musi (i nie powinien)
wykonywać kroków setupowych:

- Środowisko UV jest zainicjowane, biblioteki (`streamlit`, `yt-dlp`,
  `psycopg[binary]`, `python-dotenv`) są zainstalowane.
- Baza Neon Postgres jest założona i działająca.
- `.env` zawiera **rzeczywiste** dane połączenia (`DATABASE_URL` do Neon,
  host z `-pooler`) — plik ten NIE jest commitowany (patrz `.gitignore`),
  Claude Code nie powinien go nadpisywać ani czytać jego wartości na głos.
- `.env.example` zawiera te same klucze co `.env`, ale z wartościami
  placeholderowymi/przykładowymi — to jedyny plik env, który trafia do repo
  i jest wzorcem do skonfigurowania HF Secrets/Variables przy wdrożeniu.
  Każda nowa zmienna dodana do `config.py` musi mieć odpowiednik w
  `.env.example` (z wartością przykładową, nie realną).

Punkt wejścia do pracy to więc od razu implementacja modułów z sekcji
"Kolejność implementacji" — bez `uv init`, `uv add` itp.

## Komendy (development, nie setup)

```powershell
# Uruchomienie
uv run streamlit run app.py

# Testy
uv run pytest
```

## Struktura repozytorium

```
/
├── Dockerfile              # tylko do wdrożenia HF, nie lokalnie
├── docker-compose.yml
├── pyproject.toml / uv.lock
├── schema.sql
├── .env.example
├── app.py                  # UI, bez logiki yt-dlp
├── src/
│   ├── config.py            # JEDYNE miejsce odczytu env/limitów
│   ├── validators.py        # walidacja URL (whitelist domen YouTube)
│   ├── session.py            # st.session_state — app.py nigdy nie dotyka go bezpośrednio
│   ├── progress.py           # zdarzenia postępu (on_start/on_progress/on_finished/on_error)
│   ├── engine.py             # JEDYNY moduł importujący yt_dlp bezpośrednio
│   ├── profiles.py           # profile formatów (video/mp3/flac/subtitle/transcript)
│   ├── storage.py            # katalog tymczasowy per-job, wczytanie do RAM, natychmiastowy rmtree
│   ├── transcript_cleaner.py # czyszczenie VTT/SRT -> TXT
│   ├── db.py                 # psycopg, pooled connection (host -pooler), 1-2 conn
│   ├── rate_limit.py          # licznik per IP, in-memory (deque + timestamp window)
│   └── errors.py             # mapowanie DownloadError -> czytelne komunikaty
├── tests/
└── .vscode/
```

## Zasady architektoniczne (nienaruszalne)

- **Jeden moduł = jedna odpowiedzialność.** Tylko `engine.py` importuje `yt_dlp`.
  `app.py` nie zawiera logiki pobierania — tylko renderuje stan i woła inne warstwy.
- **Brak ORM.** Dostęp do bazy wyłącznie przez czysty SQL w `psycopg`.
- **Brak systemu migracji.** Jeden `schema.sql` z `CREATE TABLE IF NOT EXISTS`;
  zmiany schematu = ręczny `ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS`.
- **Baza nie przechowuje plików.** Tylko metadane zadań (`jobs`). Pliki multimedialne
  żyją tymczasowo na dysku, są wczytywane do RAM i natychmiast usuwane po wysyłce.
- **Anonimowość.** Brak tabeli użytkowników/sesji. `client_ip_hash` — hash, nigdy
  surowy IP.
- **Współbieżność:** `threading.Semaphore(MAX_CONCURRENT_JOBS)`, domyślnie 2.
- **Rate limiting per IP jest obowiązkowy** (Space jest Public) — Warstwa 11a,
  włącz/wyłącz przez `RATE_LIMITING_ENABLED`.
- **Walidacja przed pobraniem.** `engine.py` sprawdza limity (rozmiar, liczba
  pozycji playlisty) przez wstępny `extract_info(download=False)` PRZED pobraniem.
- **Cookies.txt (bot-check YouTube)** — prosta implementacja od pierwszej iteracji:
  jeden `st.file_uploader`, ścieżka pliku jako `cookiefile` w opcjach `yt_dlp`.
- **FLAC z YouTube to transkodowanie z lossy źródła** (Opus/AAC) — UI musi to
  jasno komunikować (`st.caption`), to nie jest realny wzrost jakości.

## Profile formatów (profiles.py)

| Profil | Selector | Postprocessor |
|---|---|---|
| Video (MP4) | `bestvideo*+bestaudio/best` | Remux → MP4, embed metadata/thumbnail |
| Audio MP3 | `bestaudio/best` | `FFmpegExtractAudio` → mp3 (VBR 0 lub bitrate) |
| Audio FLAC | `bestaudio/best` | `FFmpegExtractAudio` → flac |
| Playlist | dziedziczy profil audio/video | `outtmpl` z `%(playlist_index)s`, ZIP w pamięci, `ignoreerrors=True` |
| Subtitle | `skip_download=True` | zapis SRT/VTT |
| Transcript | `skip_download=True` + VTT | post-processing tekstowy (transcript_cleaner.py) |

## Kolejność implementacji (trzymaj się tej sekwencji)

1. `config.py` + `.env.example` + `schema.sql` + `db.py` — fundament, testowalny
   w izolacji od yt-dlp i UI
2. `profiles.py` + `engine.py` — rdzeń logiki, walidacja limitów PRZED pobraniem
3. `app.py` — UI łączący warstwy, uwzględniający flagę `ENVIRONMENT`
4. `requirements`/`pyproject.toml` + lokalne uruchomienie przez UV + `Dockerfile`
   (przygotowany, ale nieużywany lokalnie)
5. Testy jakościowe lokalne — wszystkie profile, limity, obsługa błędów
6. `README.md` (nagłówek YAML `sdk: docker`, `app_port: 8501`) + migracja
   Secrets/Variables do panelu HF + publikacja

## Konwencja commitów

```
feat(config)
feat(engine)
feat(streamlit)
test(rate-limit)
```

## Otwarte decyzje (nie zamykaj ich samodzielnie bez potwierdzenia)

- Dokładny mechanizm i próg rate-limitingu per IP (in-memory vs DB-backed w Neon)
- Izolacja danych testowych od produkcyjnych w Neon (wspólna tabela `jobs` vs
  osobny schemat/`DB_SCHEMA`)
- Formalny checklist testów jakościowych przed przejściem local → HF

## Czego NIE robić

- Nie uruchamiaj Dockera lokalnie do developmentu.
- Nie dodawaj Alembic ani innego systemu migracji.
- Nie hardkoduj limitów (rozmiaru, playlisty, współbieżności) w kodzie logiki.
- Nie przechowuj plików multimedialnych w bazie ani trwale na dysku.
- Nie loguj surowego adresu IP — tylko hash.
