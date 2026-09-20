# YT MultiDownloader — CLAUDE.md

Kontekst projektu dla Claude Code. Ten plik jest źródłem prawdy o architekturze —
przy sprzeczności między kodem a tym plikiem, zgłoś rozbieżność zamiast zgadywać.

## Cel projektu

Aplikacja webowa (Streamlit) do pobierania treści z YouTube przez `yt-dlp`:
wideo (MP4), audio (MP3/FLAC), playlisty (limit `MAX_PLAYLIST_ITEMS` pozycji na
turę, z kontynuacją kolejnych tur do limitu `MAX_ZIP_SIZE_MB` oraz trybem
pobierania wybranych numerów pozycji), napisy (SRT/VTT), transkrypt bez
timestampów (TXT). Docelowy hosting: Hugging Face Spaces
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
- `MAX_ZIP_SIZE_MB=500` (twardy stop pętli pobierania playlisty po przekroczeniu
  rozmiaru ZIP-a tury — patrz „Kontrakty playlisty i pobierania")
- `MAX_CONCURRENT_JOBS=2`
- `ITEM_DOWNLOAD_TIMEOUT_SECONDS=180` (twardy limit ścienny na pobranie JEDNEJ
  pozycji — defense-in-depth, niezależny od retries/fragment_retries yt-dlp)
- `DOWNLOAD_LINK_TTL_MINUTES=30` (jak długo ZIP playlisty czeka na dysku pod linkiem)
- `RATE_LIMIT_PER_IP=10` (żądań/godzinę)
- `RATE_LIMITING_ENABLED` — domyślnie włączone w `production`, opcjonalne lokalnie
- `ENVIRONMENT=local|production`

(pełna lista zmiennych, włącznie z tymi niebędącymi limitami — `DB_SCHEMA`,
`IP_HASH_SECRET`, `STORAGE_BASE_DIR` — jest w `.env.example`, ta lista nie jest
wyczerpująca)

`settings` (`config.py`) to zamrożony singleton (`frozen=True`) tworzony raz
przy imporcie — zmiana `.env` wymaga restartu procesu, nie samego odświeżenia
strony.

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

Moduły z sekcji „Kolejność implementacji" (punkty 1–3) są zaimplementowane;
funkcjonalność playlist (Fazy 1, 2a, 2b, 2c — limit pozycji, ZIP na dysku,
tury kontynuacji, wybrane numery pozycji) jest zamknięta i przetestowana.
Aktualne, otwarte zadania robocze są prowadzone w dokumencie „stan projektu"
poza repozytorium — nie kopiuj go do tego pliku; przy potrzebie zapytaj
użytkownika o aktualny stan.

## Komendy (development, nie setup)

```powershell
# Uruchomienie — ZAWSZE przez asgi_app.py (nie `streamlit run app.py`:
# bez owijki st.App nie ma trasy /api/download i link "Zapisz plik" dla
# playlist byłby martwy). Ta sama komenda w Dockerfile (CMD) dla HF.
uv run streamlit run asgi_app.py
# Uwaga: st.App nie otwiera przeglądarki sam — wejdź na http://localhost:8501

# Testy — szybki zestaw (bez integracyjnych, patrz "Oszczędność kontekstu")
uv run pytest -q -m "not slow"

# Pełny zestaw (włącznie z testami integracyjnymi @slow — realne pobrania z sieci)
uv run pytest
```

## Oszczędność kontekstu

- Przed odczytem dużego pliku (zwłaszcza `src/engine.py`, `app.py`,
  `tests/test_engine.py`) użyj Grep, znajdź właściwą funkcję/zakres i czytaj
  fragment (`offset`/`limit`), nie cały plik.
- Nie czytaj ponownie pliku już odczytanego w tej sesji, jeśli się nie zmienił —
  wynik zostaje w kontekście do końca rozmowy.
- Testy uruchamiaj skrótowo (`uv run pytest -q -m "not slow"`; `-x`/konkretny
  test dopiero przy błędzie), długie wyjścia przycinaj (`| Select-Object -Last 50`).
- Szerokie przeszukiwanie ("gdzie wołane jest X") deleguj do sub-agenta
  (Explore) — jego odczyty nie trafiają do głównego kontekstu.
- Jedno zadanie = jedna sesja: `/clear` przy zmianie tematu, `/compact`
  z instrukcją co zachować w połowie długiej sesji.

## Struktura repozytorium

```
/
├── Dockerfile              # tylko do wdrożenia HF, nie lokalnie (jeszcze nie istnieje)
├── docker-compose.yml      # (jeszcze nie istnieje)
├── pyproject.toml / uv.lock
├── schema.sql
├── .env.example
├── asgi_app.py             # PUNKT WEJŚCIA serwera: st.App("app.py") + trasa /api/download
├── app.py                  # skrypt UI (ładowany przez asgi_app.py) i cel testów AppTest; bez logiki yt-dlp
├── src/
│   ├── config.py            # JEDYNE miejsce odczytu env/limitów
│   ├── validators.py        # walidacja URL (whitelist domen YouTube)
│   ├── session.py            # st.session_state — app.py nigdy nie dotyka go bezpośrednio
│   ├── progress.py           # zdarzenia postępu (on_start/on_progress/on_finished/on_error)
│   ├── job_runner.py         # wątek roboczy per job: woła engine.py, emituje ProgressEvent do queue.Queue
│   ├── engine.py             # JEDYNY moduł importujący yt_dlp; opcje w _build_ydl_opts, timeout w _download_one
│   ├── profiles.py           # profile formatów (video/mp3/flac/subtitle/transcript)
│   ├── naming.py             # build_display_filename — konwencja nazw plików do pobrania
│   ├── storage.py            # katalog tymczasowy per-job, wczytanie do RAM, natychmiastowy rmtree
│   ├── downloads.py          # linki do pobrania z dysku (ZIP playlisty): token, TTL, sprzątanie
│   ├── download_routes.py    # trasa HTTP GET /api/download/{token} (FileResponse, streaming)
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
  **Wyjątek: ZIP playlisty** (tryb "Cała playlista") NIE trafia do RAM —
  `st.download_button(data=<~1 GB>)` zawieszał się bezterminowo. ZIP zostaje na
  dysku (`src/downloads.py`) pod nieodgadywalnym tokenem i jest serwowany
  strumieniowo przez `GET /api/download/{token}` (`asgi_app.py`), do
  `DOWNLOAD_LINK_TTL_MINUTES`, albo do zastąpienia nowym wynikiem / resetu
  sesji. `server.enableStaticServing` odpada: Streamlit 1.63 zwraca 404 dla
  plików >200 MB w `static/`.
- **Anonimowość.** Brak tabeli użytkowników/sesji. `client_ip_hash` — hash, nigdy
  surowy IP.
- **Współbieżność:** `threading.Semaphore(MAX_CONCURRENT_JOBS)`, domyślnie 2.
- **Rate limiting per IP jest obowiązkowy** (Space jest Public) — Warstwa 11a,
  włącz/wyłącz przez `RATE_LIMITING_ENABLED`.
- **Walidacja przed pobraniem.** `engine.py` sprawdza limity (rozmiar, liczba
  pozycji playlisty) przez wstępny `extract_info(download=False)` PRZED pobraniem.
- **Cookies.txt (bot-check YouTube)** — jeden `st.file_uploader` w `app.py`
  (`cookie_data: bytes`). `engine.py::_base_ydl_opts` wstrzykuje `cookiefile`
  do KAŻDEJ instancji `YoutubeDL`, z jednego miejsca. Sondy poza jobem
  (`list_available_subtitles`, liczenie pozycji playlisty) używają
  `_temp_cookiefile` — plik tymczasowy usuwany natychmiast po `with`; właściwe
  pobranie zapisuje `cookies.txt` w `job_dir` przez `_write_cookiefile`, na
  czas życia joba. `_zip_job_dir` wyklucza `cookies.txt` z ZIP-a playlisty —
  sprzątanie idzie przez `storage.cleanup(job_dir)` jak dla innych plików.
- **Timeout na pojedynczą pozycję pobierania.** `engine.py::DownloadEngine._download_one`
  to wrapper (`ThreadPoolExecutor(max_workers=1)` + `future.result(timeout=
  ITEM_DOWNLOAD_TIMEOUT_SECONDS)`) wokół właściwej logiki w `_download_one_impl`.
  Obejmuje OBIE ścieżki — pętlę `submit_playlist()` i pojedynczy `submit()` —
  bo zawieszone pobranie trzymałoby permit `Semaphore(MAX_CONCURRENT_JOBS)` bez
  końca i degradowało serwer wszystkim. Każda nowa ścieżka pobierania MUSI
  wołać `_download_one`, nigdy `_download_one_impl` bezpośrednio. Po przekroczeniu
  limitu leci `ItemDownloadTimeoutError` (`errors.py`), obsługiwany istniejącym
  mechanizmem pojedynczych niepowodzeń (`status="error"`, job idzie dalej).
  Wątku roboczego nie da się bezpiecznie ubić (subprocess dla yt-dlp zabroniony) —
  po timeoucie `shutdown(wait=False)`, wątek może dokończyć się w tle (patrz
  dług techniczny niżej). `_build_ydl_opts` ma jawne `retries`/`fragment_retries`/
  `extractor_retries=3`, ale to dodatkowa warstwa, nie twardy bound — rozstrzyga
  wyłącznie timeout powyżej.
- **FLAC z YouTube to transkodowanie z lossy źródła** (Opus/AAC) — UI musi to
  jasno komunikować (`st.caption`), to nie jest realny wzrost jakości.

## Profile formatów (profiles.py)

| Profil | Selector | Postprocessor |
|---|---|---|
| Video (MP4) | `bestvideo*+bestaudio/best` | Remux → MP4, embed metadata/thumbnail |
| Audio MP3 | `bestaudio/best` | `FFmpegExtractAudio` → mp3 (VBR 0 lub bitrate) |
| Audio FLAC | `bestaudio/best` | `FFmpegExtractAudio` → flac |
| Playlist | dziedziczy profil audio/video | `outtmpl` z `%(playlist_index)s`, ZIP na dysku (`/api/download/{token}`), `ignoreerrors=True` |
| Subtitle | `skip_download=True` | zapis SRT/VTT |
| Transcript | `skip_download=True` + VTT | post-processing tekstowy (transcript_cleaner.py) |

## Kontrakty playlisty i pobierania

- **Tury:** `start_index`/`next_start_index` (pozycje absolutne, 1-based) wznawiają
  ciągłe pobieranie (przycisk „Pobierz kolejne pozycje"). Twardy stop przy
  przekroczeniu `MAX_ZIP_SIZE_MB`; pozycje spoza aktualnej tury dostają
  `status="skipped"`.
- **Wybrane numery (`playlist_scope="selected"`):** `selected_indices` waliduje
  UI (`app.py::_parse_selected_indices`) i niezależnie silnik
  (`InvalidPlaylistSelectionError` w `errors.py`) — backstop, nie duplikat.
  `MAX_PLAYLIST_ITEMS` liczy się od liczby WYBRANYCH pozycji. Brak kontynuacji
  tur w tym trybie (`next_start_index` zawsze `None`); `ProgressEvent.playlist_scope`
  niesie oryginalny scope joba do UI.
- **Nazwa ZIP-a tury** (`app.py::_build_playlist_zip_filename`): sufiks
  `-pozycje-{start}-{end}` zawsze, zero-padded do szerokości większej liczby;
  dla `selected` — lista numerów (`-pozycje-15,21`, ≤5 pozycji) albo fallback
  `-pozycje-wybrane` dla dłuższych.
- **Stopka źródłowa w TXT:** `_finalize_transcript` dopisuje po
  `format_paragraphs` linię `Źródło: {Autor}-{Tytuł} {webpage_url} {data}` —
  dla pojedynczego wideo i każdej pozycji playlisty.
- **Pułapka regresyjna:** `list_available_subtitles()` MUSI wymuszać
  `noplaylist=True` bezwarunkowo — bez tego URL z `v=`+`list=` zwraca
  info_dict playlisty i UI zgłasza fałszywe „brak napisów".
- **Błędy pozycji:** pojedyncze niepowodzenie = `status="error"`, job idzie
  dalej; zawieszenie pozycji = `ItemDownloadTimeoutError` (patrz zasada
  timeoutu wyżej).

## Kolejność implementacji

Zrobione: 1. `config.py`/`.env.example`/`schema.sql`/`db.py`; 2. `profiles.py`/
`engine.py` (walidacja limitów PRZED pobraniem); 3. `app.py` (UI łączący warstwy).

Do zrobienia przed wdrożeniem na HF Spaces:

4. `Dockerfile` (artefakt wdrożeniowy, nieużywany lokalnie) + weryfikacja
   `pyproject.toml`/`uv.lock` pod kątem obrazu Docker
5. Testy jakościowe lokalne — wszystkie profile, limity, obsługa błędów
6. `README.md` (nagłówek YAML `sdk: docker`, `app_port: 8501`, obecnie puste
   pliki) + migracja Secrets/Variables do panelu HF + publikacja

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

## Znane problemy z testów manualnych (2026-09-16)

Wszystkie rozwiązane (Sesja A — błędy silnika; Sesja B — rozbudowa UX, m.in.
blokada URL/„Nowy URL", konwencja nazw plików). Szczegóły: `docs/HISTORIA.md`.

## Stan diagnozy — tryb Subtitle (2026-09-16, zamknięte)

Zgłoszony błąd ("zadanie zakończone, ale nie znaleziono pliku wynikowego" dla
dostępnych języków napisów) nie miał przyczyny w pętli drenującej kolejkę —
`_render_progress` w `app.py` poprawnie wyciąga WSZYSTKIE zdarzenia z
`queue.Queue` w jednym cyklu i poprawnie rozróżnia premature/terminalny
`on_finished` po `result_path`. Status: **zamknięte, nie badać ponownie** —
potwierdzone testem manualnym po restarcie, testami engine.py/JobRunner i
dedykowanym testem regresyjnym `test_both_finished_events_in_same_queue_batch_resolve_to_done`
(`tests/test_app_smoke.py`). Pełny przebieg diagnozy: `docs/HISTORIA.md`.

## Tryb Transkrypt — zaimplementowany (2026-09-16)

Pipeline `engine.py::_finalize_transcript` (wołany z `submit()` po
`_resolve_result`, tylko dla `mode="transcript"`): `clean_vtt_to_text` (usuwa
nagłówek/tagi/znaczniki czasu, dedup LOKALNY sąsiadujących linii) →
`format_paragraphs` (akapity po 4 zdania, z lookaheadem na skróty typu „np.") →
zapis `.txt` i usunięcie oryginalnego `.vtt`. `profiles.py::_transcript_profile`
wymusza `subtitlesformat="vtt"` niezależnie od formatu joba. Znane edge case'y
(niekrytyczne, bez fixu): napisy bez interpunkcji dają jeden akapit (poprawne
zachowanie); skrót przed wielką literą bywa mylnie rozdzielany jako koniec
zdania. Pełny opis: `docs/HISTORIA.md`.

## Znane ograniczenie: filmy z ograniczeniem wiekowym dla zalogowanych sesji (2026-09-16)

Pobieranie filmów z ograniczeniem wiekowym z prawidłowymi, świeżymi cookies
kończy się błędem yt-dlp "Sorry, this content is age-restricted" — NIE jest
to problem z weryfikacją wieku konta Google (potwierdzone: konto użytkownika
jest w pełni zweryfikowane i może oglądać tę treść normalnie w przeglądarce).

To udokumentowane, aktualne ograniczenie yt-dlp dla zalogowanych sesji
(https://github.com/yt-dlp/yt-dlp/issues/17619) — wymaga środowiska
JavaScript (Deno/Node) do rozwiązania wyzwań szyfrujących YouTube, którego
świadomie nie dodajemy jako zależności projektu (zbyt duży wzrost zakresu:
nowa zależność binarna + PO tokens wymagające odnawiania).

Wypróbowane i ODRZUCONE obejście: wymuszenie extractor_args
player_client=["mweb"] we wszystkich opcjach yt_dlp — powoduje regresję
("No video formats found!") dla zwykłych, nieograniczonych wideo. Nie
próbować ponownie bez realnych, zalogowanych cookies do weryfikacji korzyści
przeciw temu kosztowi.

Komunikat błędu w UI (errors.py) uczciwie informuje użytkownika, że to znane
ograniczenie narzędzia, z linkiem do zgłoszenia — nie sugeruje problemu
po stronie konta użytkownika.

## Dług techniczny: timeout pojedynczej pozycji jest best-effort (2026-09-20)

- (a) limit ścienny może uciąć poprawne, duże pobranie na wolnym łączu —
  docelowo limit bezczynności resetowany w `progress_hooks`, nie sztywny czas.
- (b) brak kooperatywnego anulowania (flaga `threading.Event` sprawdzana
  w `progress_hooks`), żeby osierocony wątek faktycznie się kończył.
- (c) brak jawnego `socket_timeout` i logowania per pozycja (numer, start/koniec,
  powód) — brak dowodów przy kolejnej diagnozie podobnego przypadku.
- (d) niepotwierdzone, czy komunikat „Przekroczono limit czasu…" faktycznie
  dociera do UI, czy ląduje w ogólnym „Wystąpił nieoczekiwany błąd…".

## Dziennik (2026-09-20): pętla retry przy pobieraniu pozycji playlisty

Zgłoszenie: nieskończona pętla 403/connection timeout na 1 pozycji testowej
playlisty blokowała cały job. Fix: timeout ścienny na pozycję + jawny
`extractor_retries=3` (patrz „Zasady architektoniczne", punkt o timeoucie).
Wynik: testy zielone; pozycje sprawiające problem były niepobieralne z
YouTube także pojedynczo (problem po stronie YouTube, nie aplikacji).