# Projekt: YT MultiDownloader - Architecture Specification
Wszechstronny Downloader YouTube (yt-dlp + Streamlit + HF Spaces + Neon)
Oficjalna specyfikacja architektury projektu YT MultiDownloader

**Autor:** Bogdan Hemer 
**Projekt:** YT MultiDownloader 
**Data specyfikacji:** 2026-09-15 (finalna wersja po doprecyzowaniu otwartych punktów) 
**Status:** Architektura zatwierdzona w całości, implementacja nierozpoczęta 
**Wersja dokumentu** 3.01 ---

## 1. Cel projektu

Aplikacja webowa do pobierania treści z YouTube (i innych serwisów
wspieranych przez `yt-dlp`) w wielu formatach:

-   **Wideo** (MP4, wybór jakości)
-   **Audio** (MP3 lub FLAC, wybór)
-   **Playlisty** (wsadowe pobieranie w wybranym trybie, limit pozycji
    konfigurowalny)
-   **Napisy z timestampami** (SRT/VTT)
-   **Transkrypt bez timestampów** (czysty TXT, np. do wklejenia w LLM)

Docelowo aplikacja ma być publicznie dostępna w sieci, hostowana **za
darmo** na Hugging Face Spaces (Docker SDK, widoczność **Public**), z
interfejsem w **Streamlit**, oraz z trwałą, anonimową historią
wykonanych zadań przechowywaną w darmowej bazie **Neon Postgres**.

### 1.1 Model wdrożenia --- lokalny development przed publikacją

Projekt jest **implementowany i testowany lokalnie** (Windows 11 +
Visual Studio Code + UV + Python). Publikacja na Hugging Face Spaces
następuje **dopiero po przejściu testów jakościowych** lokalnie. To
wymusza kluczową zasadę architektoniczną:

> Wszystkie limity i ograniczenia wynikające z darmowego tieru HF Spaces
> (RAM, rozmiar plików, liczba pozycji w playlistach, współbieżność)
> muszą być **symulowane lokalnie przez konfigurację**, nie zakodowane
> na twardo. Przełączenie środowiska lokalny → HF Spaces odbywa się
> przez zmianę wartości w warstwie konfiguracji (Warstwa 12), nigdy
> przez zmianę kodu aplikacji.

Konsekwencja: każdy limit (rozmiar pliku, liczba pozycji playlisty,
liczba równoczesnych zadań) jest odczytywany z jednego źródła
konfiguracji (zmienne środowiskowe / plik `.env` lokalnie, HF
Secrets/Variables w produkcji), z tymi samymi domyślnymi wartościami w
obu środowiskach --- więc zachowanie aplikacji lokalnie odpowiada
zachowaniu na HF, umożliwiając realistyczne testy przed publikacją.

------------------------------------------------------------------------

## 2. Stack technologiczny

  -----------------------------------------------------------------------
  Warstwa                 Technologia             Uzasadnienie
  ----------------------- ----------------------- -----------------------
  Silnik pobierania       `yt-dlp` (moduł Python, Aktywnie rozwijany fork
                          nie subprocess)         youtube-dl, pełna
                                                  kontrola przez API
                                                  Pythona

  Przetwarzanie mediów    `ffmpeg`                Wymagany do remuxu
                                                  wideo i
                                                  ekstrakcji/konwersji
                                                  audio

  Interfejs użytkownika   `Streamlit`             Szybkie budowanie
                                                  webowego UI w Pythonie,
                                                  bez frontendu JS

  Środowisko              Windows 11 + Visual     Lokalny development i
  deweloperskie           Studio Code + UV +      testy bez Dockera
                          Python                  

  Hosting docelowy        Hugging Face Spaces     Darmowy tier: 2 vCPU,
                          (Docker SDK, widoczność 16 GB RAM, \~50 GB
                          **Public**)             dysku efemerycznego

  Baza danych             Neon Postgres (darmowy  Serverless Postgres,
                          tier)                   trwała historia zadań
                                                  między restartami
                                                  kontenera; ta sama
                                                  instancja używana
                                                  lokalnie i w produkcji

  Dostęp do bazy          `psycopg` (czysty SQL,  Lekka zależność, pełna
                          bez ORM)                kontrola nad
                                                  zapytaniami

  Schemat bazy            Jeden plik `schema.sql` Brak migracji (Alembic)
                                                  --- nieuzasadnione przy
                                                  jednej tabeli
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 3. Ograniczenia platformy i ich symulacja lokalna

### 3.1 Hugging Face Spaces (darmowy tier, Docker, Public)

-   2 vCPU, 16 GB RAM na kontener
-   \~50 GB dysku, ale **efemerycznego** --- czyszczony przy każdym
    restarcie/uśpieniu
-   Repozytorium Space (kod) limitowane do \~1 GB
-   Space usypia po okresie bezczynności → "cold start" przy pierwszym
    żądaniu
-   Trwały storage (`/data`) jest **płatny** --- świadomie nieużywany w
    tym projekcie
-   Widoczność **Public**: aplikacja i kod widoczne dla każdego, Space
    indeksowany w wyszukiwarce HF
-   Konfiguracja wrażliwych danych przez **Secrets** (np.
    `DATABASE_URL`), publiczne ustawienia przez **Variables**

### 3.2 Widoczność Public --- konsekwencja projektowa

Ponieważ każdy użytkownik internetu może otworzyć aplikację i uruchamiać
zadania pobierania, **rate-limiting per IP jest obowiązkowym
elementem**, nie opcjonalnym. Bez tego dwa równoczesne żądania od
różnych anonimowych użytkowników mogą łatwo przeciążyć 2 vCPU/16 GB RAM
darmowego tieru. Rate-limiting per IP jest dodany jako nowa **Warstwa
11a** (patrz sekcja 4).

### 3.3 Neon Postgres (darmowy tier)

-   0.5 GB storage na projekt
-   100 CU-hours obliczeń miesięcznie, autoscaling do 2 CU
-   Scale-to-zero po \~5 minutach bezczynności (dodatkowy delay przy
    pierwszym zapytaniu po uśpieniu)
-   Połączenia zawsze przez wbudowany PgBouncer (pooled connection, host
    z `-pooler`)
-   Rekomendacja: 1--2 połączenia klienckie per kontener
-   Egress: 5 GB/miesiąc/projekt (nieistotne --- pliki multimedialne nie
    przechodzą przez bazę)

### 3.4 Konsekwencja dla przechowywania plików

Baza danych **nie przechowuje plików multimedialnych** --- tylko
metadane zadań. Wszystkie pliki wideo/audio/napisów żyją tymczasowo na
dysku (lokalnie: katalog tymczasowy systemu; na HF: `/tmp` kontenera),
są wczytywane do pamięci (RAM) i przekazywane do przeglądarki
użytkownika przez `st.download_button`, po czym są natychmiast usuwane z
dysku.

### 3.5 Ustalone limity darmowego tieru (finalne wartości)

  -------------------------------------------------------------------------
  Parametr                Wartość ustalona        Zmienna konfiguracyjna
  ----------------------- ----------------------- -------------------------
  Maks. rozmiar           **500 MB**              `MAX_FILE_SIZE_MB=500`
  pojedynczego pliku                              
  wynikowego                                      

  Maks. liczba pozycji w  **10**                  `MAX_PLAYLIST_ITEMS=10`
  playliście                                      

  Maks. liczba            2 (dopasowane do 2      `MAX_CONCURRENT_JOBS=2`
  równoczesnych zadań     vCPU)                   
  (semafor)                                       

  Limit żądań per IP      do ustalenia w kolejnej `RATE_LIMIT_PER_IP`
  (rate limiting, tylko   rundzie (np. N          
  Public)                 żądań/godzinę)          
  -------------------------------------------------------------------------

Wartość 500 MB została przyjęta jako górna granica z dwóch rozważanych
(300/500 MB) --- pozwala na pobranie większości filmów w rozsądnej
jakości (do \~1080p, kilkanaście minut) bez ryzyka wyczerpania 16 GB RAM
przy 2 równoczesnych zadaniach (2 × 500 MB = 1 GB, bezpieczny margines).

------------------------------------------------------------------------

## 4. Architektura warstwowa

### Warstwa 0 --- Struktura repozytorium

    /
    ├── Dockerfile
    ├── docker-compose.yml      
    ├── requirements.txt
    ├── schema.sql
    ├── .env.example            (wzorzec zmiennych, kopiowany do .env lokalnie / Secrets+Variables na HF)
    ├── app.py
    ├── src/
    │   ├── engine.py
    │   ├── profiles.py
    │   ├── session.py
    │   ├── storage.py
    │   ├── transcript_cleaner.py
    │   ├── db.py
    │   ├── errors.py
    │   └── config.py           (jedyne miejsce odczytu limitow/zmiennych srodowiskowych)
    └── README.md   (naglowek YAML: sdk: docker, app_port: 8501)

Lokalny development odbywa się na Windows 11 z wykorzystaniem Visual
Studio Code, UV i Pythona. `Dockerfile` oraz `docker-compose.yml` są
przeznaczone wyłącznie do wdrożenia na Hugging Face Spaces.

### Warstwa 1 --- Dockerfile

Instaluje `ffmpeg`, `yt-dlp`, `streamlit`, `psycopg`; ustawia
użytkownika non-root; wystawia port 8501; definiuje środowisko systemowe
dla wdrożenia na Hugging Face Spaces.

### Warstwa 2 --- UI (Streamlit Frontend, `app.py`)

-   Pole URL + wykrywanie typu źródła (wideo/playlista)
-   Wybór trybu: Video / Audio / Playlist / Subtitles / Transcript
-   Warunkowe widgety: dla Audio --- `selectbox` MP3/FLAC, `slider`
    bitrate (tylko dla MP3, ukryty dla FLAC)
-   `st.caption()` z ostrzeżeniem, że FLAC z YouTube jest
    transkodowaniem z lossy źródła (Opus/AAC), nie realnym wzrostem
    jakości
-   Komunikat o limicie playlisty (maks. 10 pozycji) widoczny przed
    startem zadania playlist
-   `st.progress()` / `st.status()` do wizualizacji postępu
-   `st.download_button` aktywny po zakończeniu zadania
-   `st.info()` o możliwym "cold starcie" kontenera/bazy po okresie
    bezczynności (istotne dopiero w wersji produkcyjnej na HF; lokalnie
    brak cold-startu, ale komunikat pozostaje w kodzie sterowany flagą
    środowiska)
-   Opcjonalna zakładka "Historia" (odczyt z Neon przez
    `db.get_recent_history()`)
-   Nie zawiera logiki yt-dlp --- tylko renderuje stan i woła funkcje
    innych warstw

### Warstwa 3 --- Session & State Manager (`session.py`)

Zarządza `st.session_state` (stan zadania: `idle|running|done|error`,
ścieżka wyniku, procent postępu, ewentualny błąd). `app.py` nigdy nie
manipuluje `st.session_state` bezpośrednio.

### Warstwa 4 --- Threading Bridge

Pobieranie uruchamiane w osobnym `threading.Thread`, żeby nie blokować
UI Streamlit. Komunikacja postępu przez `queue.Queue` lub bezpośredni
zapis do `st.session_state`, odpytywane przez
`st.rerun()`/`st.fragment`.

### Warstwa 5 --- Orkiestrator (`engine.py`)

Jedyny moduł importujący `yt_dlp` bezpośrednio. Przyjmuje obiekt zadania
(`DownloadJob`), waliduje URL przez wstępny
`extract_info(download=False)`, sprawdza limity z `config.py` (rozmiar,
liczba pozycji playlisty) **przed** pobraniem, buduje `ydl_opts` na
bazie profilu, wykonuje pobranie, przechwytuje wyjątki do warstwy
błędów.

### Warstwa 6 --- Profile formatów (`profiles.py`)

  ----------------------------------------------------------------------------------------------
  Profil            Selector formatu              Postprocessor           Uwagi
  ----------------- ----------------------------- ----------------------- ----------------------
  Video (MP4)       `bestvideo*+bestaudio/best`   Remux → MP4, embed      Remux bez
                                                  metadata/thumbnail      transkodowania gdy
                                                                          kodeki zgodne

  Audio MP3         `bestaudio/best`              `FFmpegExtractAudio` →  Lossy, uniwersalna
                                                  mp3, VBR 0 lub bitrate  kompatybilność
                                                  (np. 320K)              

  Audio FLAC        `bestaudio/best`              `FFmpegExtractAudio` →  Bezstratny kontener,
                                                  flac                    ale transkodowany z
                                                                          lossy źródła --- brak
                                                                          realnego zysku jakości

  Playlist          dziedziczy profil audio/video `outtmpl` z             `ignoreerrors=True`,
                                                  `%(playlist_index)s`,   limit rozmiaru
                                                  ZIP w pamięci, limit 10 całkowitego z
                                                  pozycji                 `config.py`

  Subtitle          `skip_download=True`          zapis SRT/VTT           Zachowuje strukturę
                                                                          czasową

  Transcript        `skip_download=True` + VTT    post-processing         Bez timestampów
                                                  tekstowy (warstwa 7)    
  ----------------------------------------------------------------------------------------------

Wybór MP3 vs FLAC jest parametrem profilu Audio, nie osobną ścieżką
kodu.

### Warstwa 7 --- Transcript Cleaner (`transcript_cleaner.py`)

Odseparowany od yt-dlp moduł tekstowy: usuwa znaczniki czasu, numerację,
tagi stylu (`<c>...</c>`) z plików VTT/SRT, deduplikuje powtarzające się
linie (typowe dla auto-napisów), zapisuje `.txt` obok oryginalnego pliku
z timestampami.

### Warstwa 8 --- Observability (Progress)

Silnik emituje zdarzenia (`on_start`, `on_progress`, `on_finished`,
`on_error`) przez callback; warstwa ta decyduje o sposobie prezentacji
(pasek w Streamlit, log) --- oddzielona od silnika dla łatwiejszej
wymiany UI w przyszłości.

### Warstwa 9 --- Resilience / Errors (`errors.py`)

Mapuje `yt_dlp.utils.DownloadError` na czytelne komunikaty: - Bot-check
YouTube → instrukcja wgrania `cookies.txt` przez `st.file_uploader` (bo
`--cookies-from-browser` nie działa na serwerze bez przeglądarki
użytkownika) --- **wchodzi do pierwszej iteracji** jako prosta
implementacja (jeden `file_uploader`, przekazanie ścieżki pliku jako
`cookiefile` do `yt_dlp`, bez dodatkowej walidacji formatu) -
Przekroczenie limitu rozmiaru/liczby pozycji playlisty → czytelny
komunikat z podaniem aktualnego limitu (z `config.py`) - Brak napisów w
żądanym języku → automatyczny fallback + ostrzeżenie - Throttling/błąd
sieci → retry z backoff (częściowo wbudowane w `yt_dlp` przez
`retries`/`fragment_retries`)

### Warstwa 10 --- Storage efemeryczny (`storage.py`)

-   Izolowany katalog per zadanie: lokalnie
    `tempfile.gettempdir()/{session_id}/{job_id}/`, na HF
    `/tmp/{session_id}/{job_id}/` --- ścieżka bazowa odczytywana z
    `config.py`, identyczny kod w obu środowiskach
-   Natychmiastowe wczytanie wyniku do RAM (`bytes`/`io.BytesIO`) po
    zakończeniu pobierania
-   Hard limit rozmiaru pliku: **500 MB**, odczytywany z
    `MAX_FILE_SIZE_MB` --- ta sama wartość domyślna lokalnie i w
    produkcji, żeby testy lokalne odzwierciedlały rzeczywiste zachowanie
    na HF
-   Dla playlist: pakowanie do ZIP w pamięci (`zipfile` + `io.BytesIO`),
    limit **10 pozycji** z `MAX_PLAYLIST_ITEMS`
-   Natychmiastowe `shutil.rmtree()` katalogu tymczasowego po wysłaniu
    pliku do klienta

### Warstwa 11 --- Limity współbieżności

`threading.Semaphore(MAX_CONCURRENT_JOBS)`, domyślnie **2** (dopasowany
do 2 vCPU free tier), odczytywany z `config.py`; kolejne żądania czekają
w kolejce z komunikatem w UI. Ta sama wartość używana lokalnie do testów
obciążeniowych przed publikacją.

### Warstwa 11a --- Rate limiting per IP (nowa warstwa, wymagana przez widoczność Public)

Ponieważ Space będzie **Public**, dodaje się warstwę ograniczającą
liczbę żądań z jednego adresu IP w danym oknie czasowym, zapobiegającą
nadużyciom przez pojedynczego anonimowego użytkownika. Implementacja:
prosty licznik w pamięci (dict `{ip: [timestamps]}`) lub --- jeśli
wymagana trwałość między restartami --- pomocnicza tabela w Neon.
Dokładna wartość limitu i mechanizm (in-memory vs DB-backed) pozostają
do ustalenia w kolejnej rundzie; warstwa jest już zarezerwowana w
architekturze i włączana/wyłączana przez flagę `RATE_LIMITING_ENABLED` w
`config.py` (włączona domyślnie w profilu produkcyjnym HF, opcjonalna
lokalnie).

### Warstwa 12 --- Konfiguracja i Secrets (`config.py`) --- centralny punkt symulacji środowiska

To kluczowa warstwa dla modelu "test lokalny → publikacja na HF". Jeden
moduł `config.py` odczytuje wszystkie zmienne środowiskowe i eksponuje
je jako typowane stałe/funkcje używane przez resztę aplikacji:

-   **Zmienne wspólne dla obu środowisk** (te same nazwy, te same
    wartości domyślne): `MAX_FILE_SIZE_MB`, `MAX_PLAYLIST_ITEMS`,
    `MAX_CONCURRENT_JOBS`, `RATE_LIMITING_ENABLED`,
    `DEFAULT_AUDIO_FORMAT`
-   **Zmienne środowiskowo-specyficzne**: `ENVIRONMENT=local|production`
    --- flaga determinująca np. czy wyświetlać komunikat o cold-starcie,
    czy używać `tempfile.gettempdir()` czy `/tmp`
-   Lokalnie: zmienne wczytywane z pliku `.env` (przez `python-dotenv`)
-   Na HF: **Variables** (publiczne, np. `MAX_FILE_SIZE_MB=500`) i
    **Secrets** (prywatne, `DATABASE_URL`) ustawiane w panelu Space ---
    te same nazwy zmiennych jak w `.env.example`, więc przejście lokalny
    → HF wymaga tylko przeklejenia wartości do panelu HF, bez zmiany
    kodu
-   `DATABASE_URL` (connection string do Neon z hostem `-pooler`) --- ta
    sama instancja Neon używana i lokalnie, i w produkcji, więc dane z
    testów trafiają do tej samej historii (do rozważenia: osobna
    baza/schema dla testów, jeśli chcemy odizolować dane testowe od
    produkcyjnych --- otwarte do ustalenia)

### Warstwa 13 --- Persistence Layer (`db.py` + `schema.sql`)

-   Czysty SQL przez `psycopg`, bez ORM
-   Jeden plik `schema.sql` z `CREATE TABLE IF NOT EXISTS`, wykonywany
    raz przy starcie kontenera (identycznie lokalnie i na HF)
-   Zakres danych: **tylko anonimowa historia zadań** --- brak tabeli
    użytkowników/sesji, brak cache metadanych wideo
-   Funkcje: `log_job_start(url, mode, format) -> job_id`,
    `log_job_finish(job_id, status, duration, error=None)`,
    `get_recent_history(limit=20)`
-   Pooled connection (host `-pooler`), limit 1--2 połączeń klienckich
    per kontener
-   Tolerancja na cold start Neon (retry z krótkim backoff przy
    pierwszym zapytaniu po uśpieniu)

------------------------------------------------------------------------

## 5. Schemat bazy danych --- `schema.sql` (typy kolumn i indeksy)

Tabela `jobs` --- anonimowa historia zadań, jedna tabela, bez systemu
migracji:

``` sql
CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    source_url      TEXT NOT NULL,
    mode            VARCHAR(20) NOT NULL,      -- 'video' | 'audio' | 'playlist' | 'subtitle' | 'transcript'
    output_format   VARCHAR(10) NOT NULL,      -- 'mp4' | 'mp3' | 'flac' | 'srt' | 'vtt' | 'txt'
    status          VARCHAR(20) NOT NULL DEFAULT 'running',  -- 'running' | 'done' | 'error'
    duration_ms     INTEGER,                   -- czas trwania zadania w milisekundach
    file_size_bytes BIGINT,                    -- rozmiar wynikowego pliku (lub sumy dla playlist)
    error_message   TEXT,                      -- wypełniane tylko gdy status = 'error'
    client_ip_hash  TEXT                       -- hash IP (do rate limitingu / diagnostyki, nie surowy IP -- zachowanie anonimowosci)
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_mode ON jobs (mode);
```

Uwagi do schematu: - `client_ip_hash` (hash, nie surowy adres IP) jest
dodany z myślą o warstwie 11a (rate limiting) i pozostaje zgodny z
zasadą anonimowości --- nie przechowujemy identyfikowalnych danych
osobowych, tylko nieodwracalny hash do liczenia żądań w oknie
czasowym. - Indeks na `created_at DESC` wspiera najczęstsze zapytanie:
`get_recent_history(limit=20)`. - Indeks na `status` przydatny do
ewentualnego monitoringu (np. liczba błędów w ostatniej dobie). - Indeks
na `mode` przydatny do statystyk użycia (np. czy FLAC jest w ogóle
wybierany względem MP3) --- opcjonalny, ale tani w utrzymaniu przy tak
małej tabeli. - Brak `alembic`: jeśli schemat się zmieni, edytujemy ten
plik ręcznie i dopisujemy
`ALTER TABLE IF EXISTS jobs ADD COLUMN IF NOT EXISTS ...` obok
istniejącego `CREATE TABLE`.

------------------------------------------------------------------------

## 6. Kompletny diagram przepływu danych

    Browser (klient, Internet - Space Public)
       ↕ HTTPS (lokalnie: Streamlit uruchomiony przez UV/Python | produkcja: HF Spaces proxy -> kontener Docker, port 8501)
    app.py (Streamlit UI, watek glowny)
       → config.py [ENVIRONMENT=local|production, wspolne limity z .env / HF Variables+Secrets]
       → session.py (st.session_state per-user session)
          → [Warstwa 11a: rate limiting per IP -- aktywna gdy RATE_LIMITING_ENABLED]
          → db.py [raw psycopg, pooled connection, 1-2 conn]
          │     schema.sql wykonany raz przy starcie
          │     - log_job_start(url, mode, format, client_ip_hash) -> job_id
          │     - log_job_finish(job_id, status, duration, file_size, error=None)
          │     - get_recent_history(limit=20)
          ↕
          Neon Postgres (tabela: jobs -- anonimowa, ta sama instancja lokalnie i produkcyjnie)
          → [limit wspolbieznosci: Semaphore(MAX_CONCURRENT_JOBS=2)]
             → engine.py (Thread w tle, walidacja limitow z config.py PRZED pobraniem)
                → profiles.py (video/mp3/flac/subtitle/transcript, limit MAX_PLAYLIST_ITEMS=10)
                   → yt_dlp.YoutubeDL (+ opcjonalny cookiefile z uploadu, wchodzi w iteracji 1)
                      → plik na dysku tymczasowym (lokalnie: temp systemowy | HF: /tmp), limit MAX_FILE_SIZE_MB=500
          ← wczytanie do RAM (bytes/BytesIO)
          ← db.log_job_finish()
          ← shutil.rmtree(katalog tymczasowy zadania)
       → st.download_button (transfer pliku do przegladarki klienta)
       → [koniec: brak trwalych plikow na serwerze, tylko log w Neon]

------------------------------------------------------------------------

## 7. Otwarte punkty --- status po tej rundzie

  ---------------------------------------------------------------------------------
  \#                Punkt             Status            Ustalona wartość / decyzja
  ----------------- ----------------- ----------------- ---------------------------
  1                 Limit rozmiaru    ✅ Zamknięty      **500 MB**
                    pliku                               (`MAX_FILE_SIZE_MB=500`)

  2                 Moment            ✅ Zamknięty      Prosta implementacja
                    wprowadzenia                        wchodzi **od pierwszej
                    obsługi                             iteracji**
                    cookies.txt                         

  3                 Widoczność Space  ✅ Zamknięty      **Public** (wymaga
                                                        rate-limitingu per IP ---
                                                        Warstwa 11a)

  4                 Limit pozycji w   ✅ Zamknięty      **10**
                    playlistach                         (`MAX_PLAYLIST_ITEMS=10`)

  5                 Typy kolumn i     ✅ Zamknięty      Schemat kompletny w sekcji
                    indeksy w                           5
                    `schema.sql`                        
  ---------------------------------------------------------------------------------

### Nowe otwarte punkty wynikające z tej rundy

-   **Dokładny mechanizm i wartość rate-limitingu per IP** (Warstwa
    11a): liczba żądań na godzinę/dzień, implementacja in-memory
    (prostsza, ale gubi stan po restarcie kontenera) vs DB-backed w Neon
    (trwała, ale dodaje zapytania do bazy przy każdym żądaniu)
-   **Izolacja danych testowych od produkcyjnych w Neon**: lokalnie używać
    tabeli `dev.jobs`, a w produkcji `public.jobs`
-   **Zakres testów jakościowych przed publikacją**: czy potrzebny jest
    formalny checklist/scenariusze testowe (np. test każdego profilu
    formatu, test przekroczenia limitów, test rate-limitingu) przed
    przejściem na HF Spaces

------------------------------------------------------------------------

## 8. Kolejność implementacji (zaplanowana, zaktualizowana)

1.  `config.py` + `.env.example` + `schema.sql` + `db.py` --- fundament:
    konfiguracja i baza, testowalne w pełnej izolacji od yt-dlp i UI
2.  `profiles.py` + `engine.py` --- rdzeń logiki yt-dlp, z walidacją
    limitów z `config.py`, bez UI
3.  `app.py` --- interfejs Streamlit łączący wszystkie warstwy,
    uwzględniający flagę `ENVIRONMENT`
4.  `requirements.txt` + lokalne uruchomienie przez UV/Python w Windows
    11 + przygotowanie `Dockerfile` do wdrożenia na HF
5.  Testy jakościowe lokalne --- weryfikacja wszystkich profili
    formatów, limitów (rozmiar/playlist/współbieżność), obsługi błędów
    (bot-check, brak napisów)
6.  `README.md` z metadanymi YAML dla HF + migracja Secrets/Variables z
    lokalnego `.env` do panelu Space + publikacja

------------------------------------------------------------------------

## 9. Historia decyzji podjętych w toku całej rozmowy

  -----------------------------------------------------------------------
  Decyzja                             Wybór
  ----------------------------------- -----------------------------------
  Zakres funkcjonalny                 Wideo, audio (MP3 i FLAC),
                                      playlisty, napisy z timestampami,
                                      transkrypt bez timestampów

  Model aplikacji                     Webowa (nie lokalna aplikacja
                                      desktopowa)

  Interfejs                           Streamlit

  Hosting docelowy                    Hugging Face Spaces, darmowy tier,
                                      Docker SDK, widoczność **Public**

  Model wdrożenia                     Implementacja i testy **lokalnie w
                                      Dockerze**, publikacja na HF po
                                      testach jakościowych

  Symulacja limitów HF                Przez konfigurację (`config.py`,
                                      `.env`/HF Variables+Secrets), nie
                                      przez kod na twardo

  Baza danych                         Neon Postgres, darmowy tier

  Zakres danych w bazie               Tylko historia/logi zadań (bez
                                      cache metadanych)

  Dostęp do bazy                      Czysty SQL (`psycopg`), bez ORM

  Tożsamość w historii                Anonimowa (bez tabeli
                                      użytkowników/sesji, tylko hash IP
                                      do rate-limitingu)

  Zarządzanie schematem               Jeden plik `schema.sql`, bez
                                      systemu migracji

  Limit rozmiaru pliku                500 MB

  Limit pozycji w playliście          10

  Limit współbieżności                2 równoczesne zadania (Semaphore)

  Obsługa cookies.txt (bot-check)     Prosta implementacja od pierwszej
                                      iteracji

  Rate limiting per IP                Wymagany (widoczność Public),
                                      mechanizm do ustalenia
  -----------------------------------------------------------------------
