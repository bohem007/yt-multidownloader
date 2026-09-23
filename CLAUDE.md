# YT MultiDownloader — CLAUDE.md

Kontekst projektu dla Claude Code. Ten plik jest źródłem prawdy o architekturze —
przy sprzeczności między kodem a tym plikiem, zgłoś rozbieżność zamiast zgadywać.

## Cel projektu

Aplikacja webowa (Streamlit) do pobierania treści z YouTube przez `yt-dlp`:
wideo (MP4), audio (MP3/FLAC), playlisty (przycinane do pierwszych
`MAX_PLAYLIST_ITEMS` pozycji, z turami wg `MAX_ZIP_SIZE_MB` wewnątrz tego zakresu
oraz trybem pobierania wybranych numerów pozycji), napisy (SRT/VTT), transkrypt bez
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
- `MAX_PLAYLIST_ITEMS=10` (walidacja `>= 1` przy imporcie configu — `0` przycinałoby
  playlisty do cichego pustego wyniku, bez błędu)
- `MAX_PLAYLIST_RD_ITEMS=20` (limit migawki listy Mix/Radio `list=RD…`;
  dla tych URL-i zastępuje `MAX_PLAYLIST_ITEMS`, we wszystkich trybach; też `>= 1`)
- `MAX_ZIP_SIZE_MB=500` (twardy stop pętli pobierania playlisty po przekroczeniu
  rozmiaru ZIP-a tury — patrz „Kontrakty playlisty i pobierania")
- `MAX_CONCURRENT_JOBS=2`
- `ITEM_DOWNLOAD_TIMEOUT_SECONDS=180` (twardy limit ścienny na pobranie JEDNEJ
  pozycji — defense-in-depth, niezależny od retries/fragment_retries yt-dlp)
- `DOWNLOAD_LINK_TTL_MINUTES=30` (jak długo plik wynikowy — pojedynczy lub ZIP
  playlisty — czeka na dysku pod linkiem)
- `RATE_LIMIT_PER_IP=10` (żądań/godzinę)
- `RATE_LIMITING_ENABLED` — domyślnie włączone w `production`, opcjonalne lokalnie
- `DB_CONNECT_TIMEOUT_SECONDS=5` (walidacja `>= 1`; twardy limit czasu na SAM
  `psycopg.connect()` w `db.py::_connect` — patrz „Odporność warstwy bazy")
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
# bez owijki st.App nie ma trasy /api/download i żaden link "Zapisz plik"
# by nie działał). Ta sama komenda w Dockerfile (CMD) dla HF.
uv run streamlit run asgi_app.py
# Uwaga: st.App nie otwiera przeglądarki sam — wejdź na http://localhost:8501

# Testy — szybki zestaw (bez integracyjnych, patrz "Oszczędność kontekstu")
uv run pytest -q -m "not slow"

# Pełny zestaw (włącznie z testami integracyjnymi @slow — realne pobrania z sieci)
uv run pytest
```

Szybki zestaw nie dotyka bazy ani sieci i nie zależy od lokalnego `.env`: izolacja w
`tests/conftest.py` (autouse `database_calls` podmienia metody `Database` na atrapę i blokuje
`psycopg.connect`; limity pinowane w `os.environ` przed importem `config`). Blokada dotyczy
bazy — sieć YouTube nadal podstawiają same testy. Wyjątek: znacznik `db_integration`
(`tests/test_db.py`, razem z `slow`) wyłącza izolację i łączy się z Neon; znacznik `db_sql`
testuje prawdziwy SQL na fałszywym połączeniu (w szybkim zestawie, `psycopg.connect` nadal zablokowane).

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
├── Dockerfile              # tylko do wdrożenia HF, nie lokalnie
├── .dockerignore
├── README.md               # nagłówek YAML HF (sdk: docker, app_port: 7860) + opis
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
│   ├── storage.py            # katalog tymczasowy per-job; wynik -> downloads.publish, reszta od razu rmtree
│   ├── downloads.py          # linki do pobrania z dysku (KAŻDY plik wynikowy): token, TTL, sprzątanie
│   ├── download_routes.py    # trasa HTTP GET /api/download/{token} (FileResponse, streaming, Content-Type z rozszerzenia)
│   ├── ui_focus.py           # jednorazowy fokus klawiatury (st.html + JS, selektory .st-key-<key>)
│   ├── transcript_cleaner.py # czyszczenie VTT/SRT -> TXT
│   ├── db.py                 # psycopg, pooled connection (host -pooler), 1-2 conn
│   ├── rate_limit.py          # licznik per IP, in-memory (deque + timestamp window)
│   └── errors.py             # mapowanie DownloadError -> czytelne komunikaty
├── tests/
└── .vscode/
```

Produkcja (HF Spaces): `Dockerfile` uruchamia `uv run streamlit run asgi_app.py
--server.port=7860 --server.address=0.0.0.0 --server.headless=true` jako
uid 1000, nasłuch na porcie 7860 (`app_port` w README.md). Wymaga `ffmpeg`
w `PATH` (instalowany w obrazie z apt, nie jest opcją konfigurowalną w
kodzie). Zmienne środowiskowe (limity + `DATABASE_URL`/`IP_HASH_SECRET`
jako sekrety) trafiają do HF Secrets/Variables, nigdy do obrazu.

## Zasady architektoniczne (nienaruszalne)

- **Jeden moduł = jedna odpowiedzialność.** Tylko `engine.py` importuje `yt_dlp`.
  `app.py` nie zawiera logiki pobierania — tylko renderuje stan i woła inne warstwy.
- **Brak ORM.** Dostęp do bazy wyłącznie przez czysty SQL w `psycopg`.
- **Brak systemu migracji.** Jeden `schema.sql` z `CREATE TABLE IF NOT EXISTS`;
  zmiany schematu = ręczny `ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS`.
- **Baza nie przechowuje plików.** Tylko metadane zadań (`jobs`). Pliki multimedialne
  żyją tymczasowo na dysku: katalog joba (`storage.py`) znika zaraz po zakończeniu,
  a plik wynikowy — **każdy, pojedynczy i ZIP playlisty** — NIE trafia do RAM:
  `downloads.publish()` przenosi go pod nieodgadywalny token (`src/downloads.py`),
  skąd jest serwowany strumieniowo przez `GET /api/download/{token}`
  (`asgi_app.py`) do `DOWNLOAD_LINK_TTL_MINUTES`. „Nowy URL", zmiana
  trybu/formatu ani nowy job go nie kasują (świadoma decyzja 2026-09-23 — do TTL
  plik pobierze każdy, kto zna token); wcześniej znika tylko ZIP tury playlisty
  zastąpiony kolejną turą. `SessionState` trzyma token/nazwę/rozmiar, nigdy bajty.
  `server.enableStaticServing` odpada: Streamlit 1.63 zwraca 404 dla plików
  >200 MB w `static/`.
- **Zapis pliku wyłącznie przez `/api/download/<token>` — zakaz `st.download_button`.**
  Każdy plik wynikowy (pojedynczy i ZIP) dostaje `st.link_button` z
  `app.py::_render_save_link`. Powód: sonda frontendu Streamlit 1.63
  (`checkSourceUrlResponse` w `DownloadButton.tsx`) przy każdym zamontowaniu
  przycisku wysyła pełny `GET /media/<id>` i nie czyta ani nie anuluje
  odpowiedzi — duży plik trzymał połączenie do zamknięcia karty, a po ~6 dużych
  wynikach pula połączeń przeglądarki była pełna („Zapisz plik" nie reagował,
  F5 zamarzał). Tryb „deferred" (`data` jako callable) też sonduje, przy każdym
  kliknięciu. Pilnuje test statyczny `test_app_source_never_uses_st_download_button`;
  pełna diagnoza w `docs/HISTORIA.md`.
- **Godzina ważności linku.** Pod „Zapisz plik": „Link do pobrania jest ważny do
  godziny HH:MM." (plik i ZIP; tura z kontynuacją zostaje przy „Ten plik zostanie
  zastąpiony..."). Godzina to `DownloadLink.expires_at_local` — ta sama chwila co
  monotoniczny `expires_at` (on decyduje o wygaśnięciu), liczona w `publish()` z
  tego samego TTL, czas lokalny serwera; UI nie przelicza jej z TTL. Jeśli temat
  wróci: na HF czas serwera to UTC, więc godzina w UI byłaby w UTC — świadomie
  nierozwiązane (2026-09-23).
- **Sygnał „plik niezapisany" na przyciskach porzucających wynik.** Źródło prawdy:
  trasa `/api/download/<token>` oznacza token (`downloads.mark_fetched`, tylko GET)
  w chwili rozpoczęcia odpowiedzi — „zapisano" = „przeglądarka rozpoczęła
  pobieranie", anulowania okna zapisu nie widać. W stanie „wynik gotowy +
  niepobrany + link ważny" (`app.py::_has_unsaved_result`) „Nowy URL" — a przy
  turze playlisty z kontynuacją także „Pobierz kolejne pozycje" (kolejna tura po
  zakończeniu zwalnia ZIP poprzedniej: `_publish_result`) — ma pastelowe czerwone
  tło (warunkowy CSS: `st.html` z samym `<style>`, kolory wg
  `st.context.theme.type`) i natywne `help=`; podpowiedzi i przyciski w jednej mapie
  `_UNSAVED_SIGNAL_HELP`. Sygnał tylko ostrzega — bez blokady i potwierdzenia.
  Kliknięcie linku nie robi rerunu, więc „Nowy URL" żyje w
  `st.fragment` z `run_every="1s"` WYŁĄCZNIE w tym stanie (inaczej `None`), a
  fragment, który wykryje zmianę (pobrano, TTL), robi pełny `st.rerun()` — pełny
  przebieg kasuje interwały auto-rerunu we frontendzie, więc odpytywanie się
  kończy. Callback „Nowy URL" kończy się `st.rerun()`: w Streamlit 1.63 to głos
  za pełnym rerunem także z widżetu we fragmencie.
- **Fokus klawiatury (`src/ui_focus.py`).** Streamlit nie ma API fokusu:
  `request_focus(state, cel)` (np. w callbacku) + `render_focus_script(state)`
  wołane RAZ, na końcu `app.py`. Skrypt przez `st.html(...,
  unsafe_allow_javascript=True)` (nie przestarzałe `st.components.v1.html`):
  wykonuje się w głównym dokumencie przy każdej zmianie treści, więc nonce
  (`focus_nonce`, poza `_DEFAULTS`) = jedno wykonanie na prośbę. Selektory tylko
  przez `.st-key-<key>` w `FOCUS_SELECTORS` — zmiana `key=` widżetu wymaga zmiany
  mapy (pilnuje test). ENTER z poprawnym URL → „Pobierz" (tylko gdy fokus wciąż
  jest w polu URL: `on_change` odpala się też przy opuszczeniu pola); „Nowy URL"
  → pole URL.
- **Anonimowość.** Brak tabeli użytkowników/sesji. `client_ip_hash` — hash, nigdy
  surowy IP.
- **Historia = własne pobrania z `HISTORY_RETENTION_DAYS` dni** (domyślnie 5). `client_ip_hash` =
  HMAC-SHA256(`IP_HASH_SECRET`, pierwszy poprawny adres z `X-Forwarded-For`), 32 znaki hex
  (`src/client_identity.py`); `st.context.ip_address` NIE służy do tego (za proxy HF wspólny dla
  wszystkich). Brak adresu → `"unknown"`: historia takiej sesji tylko przy JAWNYM `ENVIRONMENT=local`.
  Wiersze starsze niż retencja kasuje `Database.purge_old_jobs` (best effort, raz/h na proces).
- **Odporność warstwy bazy.** `db.py::_connect` przekazuje `connect_timeout=
  DB_CONNECT_TIMEOUT_SECONDS` (domyślnie 5s) do `psycopg.connect` — bez tego
  pojedyncza próba połączenia nie miała żadnego limitu czasu. Zapis historii
  (`log_job_start`/`log_job_finish`, wołane z `app.py`) NIGDY nie blokuje ani
  nie przerywa pobierania: wyjątek jest tam cicho połykany (`try/except:
  pass`/`db_job_id=None`, bez logowania) — świadomy kompromis, priorytet to
  nieprzerwanie ścieżki pobierania, nie diagnostyka awarii zapisu. Historia w
  `app.py` (zakładka „Historia") jest czytana z cache w `SessionState`
  (`history_loaded`/`set_history_cache`/`invalidate_history_cache`), NIE przy
  każdym rerunie — `st.tabs()` to tylko layout, ciało obu zakładek wykonuje
  się w każdym rerunie skryptu; cache invaliduje wyłącznie koniec joba.
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
- **Solver wyzwań JS dla treści z ograniczeniem wiekowym.** `yt-dlp` wymaga
  lokalnie zainstalowanego Deno (wymóg środowiska, NIE zależność pip/uv —
  jak `ffmpeg`) do rozwiązania wyzwań podpisu/„n" YouTube; Deno jest
  wykrywane automatycznie, ale sam solver EJS pobiera się i aktywuje
  dopiero z jawnie ustawionym `YTDLP_REMOTE_COMPONENTS` (np. `ejs:github`,
  domyślnie puste = wyłączone). `engine.py::_base_ydl_opts` przekazuje
  `remote_components` do KAŻDEJ instancji `YoutubeDL`, tym samym
  mechanizmem co `cookiefile` — tylko gdy zmienna jest ustawiona, bez
  twardej zależności funkcjonalnej dla materiałów, którym solver nie jest
  potrzebny. `engine.py::warn_if_deno_missing()` (wołane z `asgi_app.py`
  przy starcie serwera) loguje ostrzeżenie, gdy `deno` nie ma w PATH — nie
  blokuje startu. Patrz „Znane ograniczenie: filmy z ograniczeniem
  wiekowym" niżej po pełny kontekst i zakres (standardowa jakość vs
  wysoka jakość/PO token).
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

- **Limit liczby pozycji:** `MAX_PLAYLIST_ITEMS` PRZYCINA zadanie do pierwszych N
  pozycji (nie blokuje) we WSZYSTKICH trybach: „Cała playlista" = pozycje 1..N,
  „Wybrane numery" = pierwsze N z posortowanych wybranych. UI (`app.py`) tylko
  informuje (`st.info`, etykieta „pierwsze N z M"); przycina niezależnie
  `engine.py::submit_playlist`. Tury (`MAX_ZIP_SIZE_MB`) działają wewnątrz zakresu —
  `next_start_index` nie wychodzi poza N. Mix `RD` ma osobny limit (migawka).
- **Tury:** `start_index`/`next_start_index` (pozycje absolutne, 1-based) wznawiają
  ciągłe pobieranie (przycisk „Pobierz kolejne pozycje"). Twardy stop przy
  przekroczeniu `MAX_ZIP_SIZE_MB`; pozycje spoza aktualnej tury dostają
  `status="skipped"`.
- **Wybrane numery (`playlist_scope="selected"`):** `selected_indices` waliduje
  UI (`app.py::_parse_selected_indices`) i niezależnie silnik
  (`InvalidPlaylistSelectionError` w `errors.py`) — backstop, nie duplikat.
  Limit `MAX_PLAYLIST_ITEMS` liczy się od liczby WYBRANYCH pozycji. Brak kontynuacji
  tur w tym trybie (`next_start_index` zawsze `None`); `ProgressEvent.playlist_scope`
  niesie oryginalny scope joba do UI.
- **Nazwa ZIP-a tury** (`app.py::_build_playlist_zip_filename`): sufiks
  `-pozycje-{start}-{end}` zawsze, zero-padded do szerokości większej liczby;
  dla `selected` — lista numerów (`-pozycje-15,21`, ≤5 pozycji) albo fallback
  `-pozycje-wybrane` dla dłuższych; na końcu rozszerzenie formatu joba tuż
  przed `.zip` (`...-pozycje-01-07.mp4.zip`, z `ProgressEvent.output_format`).
- **Mix/Radio (`list=RD…`):** lista dynamiczna (dwa odczyty = inne pozycje), więc
  JEDEN odczyt → `PlaylistSnapshot` (`engine.snapshot_playlist`, limit
  `MAX_PLAYLIST_RD_ITEMS`) w `st.session_state`, związany z URL-em, unieważniany
  przez „Nowy URL". Tury, `selected_indices` i „do N" liczone względem migawki;
  `submit_playlist` z `job.playlist_snapshot` NIGDY nie czyta listy ponownie.
  Bez `v=` (`playlist?list=RD…`) YouTube zwraca „unviewable" — link odrzucany w UI.
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
- Treść komunikatu `errors.py::_AGE_RESTRICTED_MARKERS` — nieaktualna od
  2026-09-22 dla standardowej jakości ze skonfigurowanym Deno/
  `YTDLP_REMOTE_COMPONENTS` (patrz „Znane ograniczenie: filmy z
  ograniczeniem wiekowym"), czeka na nowe sformułowanie
- Czy i jak odróżnić w UI wygasłe/rotowane cookies od ogólnej blokady 18+ —
  wymaga customowego `logger` w opcjach `YoutubeDL` (yt-dlp zgłasza to jako
  `report_warning`, nie wyjątek), nie samego markera tekstowego w
  `map_download_error`
- Czy w przyszłości dodać PO token provider (`bgutil-ytdlp-pot-provider` lub
  odpowiednik) dla wysokiej jakości materiałów 18+ — obecnie świadomie
  pominięte, standardowa jakość (Deno + `YTDLP_REMOTE_COMPONENTS`) uznana za
  wystarczającą

## Czego NIE robić

- Nie uruchamiaj Dockera lokalnie do developmentu.
- Nie dodawaj Alembic ani innego systemu migracji.
- Nie hardkoduj limitów (rozmiaru, playlisty, współbieżności) w kodzie logiki.
- Nie przechowuj plików multimedialnych w bazie ani trwale na dysku.
- Nie używaj `st.download_button` — plik wynikowy tylko przez `/api/download/<token>`
  (patrz „Zasady architektoniczne").
- Nie używaj przestarzałego `st.components.v1` — JS przez
  `st.html(..., unsafe_allow_javascript=True)`, tylko z treścią generowaną w kodzie.
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

## Znane ograniczenie: filmy z ograniczeniem wiekowym dla zalogowanych sesji (2026-09-16, zrewidowane 2026-09-22)

Historia: pobieranie filmów z ograniczeniem wiekowym z prawidłowymi, świeżymi
cookies kończyło się błędem yt-dlp "Sorry, this content is age-restricted" —
potwierdzone, że NIE jest to problem weryfikacji wieku konta Google (konto
użytkownika w pełni zweryfikowane, ogląda tę treść normalnie w przeglądarce).
Pierwotna decyzja (2026-09-16) była świadomym pominięciem: yt-dlp wymaga
środowiska JavaScript (Deno/Node) do rozwiązania wyzwań szyfrujących YouTube,
uznanym wtedy za zbyt duży wzrost zakresu. **Ta decyzja została odwrócona
2026-09-22** — patrz „Solver wyzwań JS..." w „Zasadach architektonicznych".

**Zweryfikowane manualnie (CLI, poza aplikacją, 3 materiały 18+, `yt-dlp`
2026.08.19):** Deno + `YTDLP_REMOTE_COMPONENTS=ejs:github` + prawidłowe,
świeże cookies → pobranie w **standardowej jakości** (itag 18, klient
`web_creator`) kończy się sukcesem, 3/3. Zwykłe materiały (wideo, audio, mała
playlista) z tą samą konfiguracją → bez regresji. Przyczyna pierwotnego
błędu okazała się węższa niż zakładano: nie brak PO tokenu, tylko brak
lokalnie pobranego/aktywnego solvera podpisu/„n" (mechanizm „remote
components"/EJS, nowszy niż opisane wyżej issue #17619) — sam Deno bez
`YTDLP_REMOTE_COMPONENTS` nie wystarczał.

**Nadal NIEOBSŁUGIWANE — świadome ograniczenie, nie błąd do naprawienia:**
wyższa jakość tego samego materiału 18+ (`bestvideo*+bestaudio`) — klient
`web_creator` wymaga GVS PO Token, którego to rozwiązanie nie dostarcza
(brak `bgutil-ytdlp-pot-provider` czy innego PO token providera — świadomie
NIE dodany, patrz „Otwarte decyzje"/dług do rozważenia w przyszłości, jeśli
standardowa jakość okaże się niewystarczająca).

Obserwacja uboczna z testów: wygasłe/rotowane cookies dają ODRĘBNY,
jednoznaczny komunikat yt-dlp ("account cookies are no longer valid...
rotated as a security measure") — ale to `report_warning`, nie wyjątek, i
`_base_ydl_opts` ma `no_warnings: True`, więc obecnie NIE dociera do
`errors.py::map_download_error` żadną istniejącą ścieżką. Odróżnienie tego
przypadku w UI (osobny komunikat od ogólnej blokady 18+) wymagałoby
przechwycenia warningów yt-dlp przez customowy `logger` w opcjach
`YoutubeDL`, nie samego dopisania markera tekstowego — nie zaimplementowane,
czeka na decyzję zakresu.

Komunikat błędu w `errors.py` (`_AGE_RESTRICTED_MARKERS`) wciąż zawiera
zdanie "Aplikacja obecnie nie obsługuje obejścia tego ograniczenia" — to
**nieaktualne** dla standardowej jakości przy skonfigurowanym Deno/
`YTDLP_REMOTE_COMPONENTS`, czeka na aktualizację treści (decyzja UX, nie
zamykać samodzielnie bez potwierdzenia, patrz „Otwarte decyzje").

Wypróbowane i ODRZUCONE (nadal aktualne, niezależne od powyższego): wymuszenie
extractor_args player_client=["mweb"] we wszystkich opcjach yt_dlp — powoduje
regresję ("No video formats found!") dla zwykłych, nieograniczonych wideo.

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