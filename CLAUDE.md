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

## Znane problemy z testów manualnych (2026-09-16)

Sesja A (błędy silnika — ROZWIĄZANE):
2. Audio MP3: plik wynikowy ma rozszerzenie .webm zamiast .mp3. ROZWIĄZANE —
   _resolve_result w engine.py wyciąga rzeczywistą ścieżkę PO postprocessingu.
3. Brak wsparcia dla wyboru języka napisów/transkryptu — dla filmu z polskim 
   audio tryb Napisy/Transkrypt nie generuje żadnego pliku. ROZWIĄZANE —
   subtitleslangs jawnie ustawiane z wybranego języka (profiles.py).
5. Komunikat dla trybu Transkrypt ujawnia wewnętrzną nazwę pliku 
   (transcript_cleaner.py) — nieprofesjonalne dla użytkownika końcowego.
   ROZWIĄZANE — placeholder usunięty, tryb faktycznie działa (patrz sekcja
   "Tryb Transkrypt — zaimplementowany" niżej).

Sesja B (rozbudowa UX — zaplanowana, jeszcze nie zaczęta):
1. Blokada pola URL po wprowadzeniu + przycisk "Nowy URL" resetujący cały stan.
4. Konwencja nazw pobieranych plików: Autor-Tytuł_wideo.jezyk.rozszerzenie 
   (separator: łącznik).
6. Zmiana trybu/formatu przy już pobranym pliku ma czyścić komunikaty 
   i ukrywać "Zapisz plik".

## Stan diagnozy — tryb Subtitle, sesja 2026-09-16 (kontynuacja)

Kontekst: po naprawie 3 bugów (przedwczesny on_finished, fantomowa ścieżka .webm,
filtr języków pl/de/en), tryb Subtitle nadal zgłaszał w przeglądarce
"Zadanie zakończone, ale nie znaleziono pliku wynikowego" dla języków FAKTYCZNIE
dostępnych — mimo że testy engine.py/job_runner.py (bezpośrednie i przez pełny
JobRunner) przechodzą poprawnie dla tych samych scenariuszy (Manual EN/DE SRT,
Manual EN VTT, Automatic PL SRT).

Kluczowa obserwacja: JobRunner emituje DWA zdarzenia on_finished dla trybu
Subtitle (pierwsze bez result_path, drugie z result_path) — inaczej niż
Video/Audio (prawdopodobnie jedno on_finished od razu z path).

Hipoteza (niepotwierdzona): pętla drenująca queue.Queue w app.py (wewnątrz
st.fragment) może nie wyciągać wszystkich zdarzeń z kolejki w jednym cyklu
odświeżenia, i/lub traktuje jako terminalne KAŻDE zdarzenie event_type=="finished"
niezależnie od obecności result_path — co dla Subtitle skutkowałoby zatrzymaniem
się na pierwszym (pustym) on_finished, zanim drugie (z prawidłową ścieżką)
zostanie odczytane.

Następny krok: zbadać dokładny mechanizm drenowania kolejki w app.py, naprawić
tak by zawsze wyciągał wszystkie dostępne zdarzenia w jednym cyklu, dodać test
AppTest symulujący dwa zdarzenia finished w jednej kolejce, potwierdzić testami
i ponownym testem w przeglądarce PO TWARDYM RESTARCIE streamlit run app.py.

Nic niecommitowane. Poprzednie 3 fixy (on_finished bez result_path traktowany
jako informacyjny, .exists() guard w _resolve_result, filtr pl/de/en) są
zweryfikowane testami, ale wciąż nie potwierdzone w przeglądarce z powodu
powyższego, oddzielnego problemu.

### Weryfikacja hipotezy (kontynuacja, ten sam dzień)

Hipoteza "pętla drenująca nie wyciąga wszystkich zdarzeń w jednym cyklu"
zweryfikowana i OBALONA: `_render_progress` w app.py już używa `while True: ...
get_nowait() ... except Empty: break` — to WYCIĄGA cały zawartość kolejki
w jednym cyklu fragmentu, poprawnie rozróżniając premature `on_finished`
(result_path=None, tylko `set_progress`) od prawdziwie terminalnego
(result_path ustawiony, ląduje w `terminal_event`).

Dodano regresyjny test `test_both_finished_events_in_same_queue_batch_resolve_to_done`
w tests/test_app_smoke.py — symuluje DOKŁADNIE opisany scenariusz (oba zdarzenia
w kolejce ZANIM fragment ją odpyta). Test PRZECHODZI bez żadnej zmiany w app.py —
potwierdza, że kod już obsługuje ten przypadek poprawnie.

Wynik: nie znaleziono dalszego błędu poprzez statyczną analizę + testy
(engine.py bezpośrednio, JobRunner wątkowo, AppTest na poziomie UI) dla
kombinacji: napisy manualne/automatyczne × SRT/VTT × 2 filmy testowe ×
pojedyncze/wsadowe zdarzenia w kolejce. Jedyny napotkany błąd w tej sesji to
zewnętrzny HTTP 429 z endpointu napisów YouTube (przejściowy rate-limit po
wielu zapytaniach diagnostycznych) — generuje INNY komunikat ("Brak napisów
w żądanym języku...") niż zgłoszony ("nie znaleziono pliku wynikowego").

Status: zablokowane na braku konkretnej reprodukcji od użytkownika (URL +
język + typ napisów manual/automatic + format SRT/VTT + dokładny komunikat
z przeglądarki). Do potwierdzenia: świeży `streamlit run app.py` (restart,
nie tylko hot-reload) i retest w przeglądarce.

**OSTATECZNIE ZAMKNIĘTE (2026-09-16):** Trzykrotnie potwierdzone jako non-issue —
(1) ręczny test w przeglądarce po twardym restarcie streamlita: działa; (2) testy
izolowane engine.py/JobRunner: przechodzą; (3) dedykowany test regresyjny
test_both_finished_events_in_same_queue_batch_resolve_to_done w test_app_smoke.py,
symulujący dokładnie sporny scenariusz (dwa on_finished w jednej kolejce): przechodzi
bez zmian w kodzie. _render_progress poprawnie drenuje całą kolejkę w pętli
while/get_nowait/except Empty i poprawnie rozróżnia premature/terminal po result_path.
Brak dalszego działania. Nie badać tego ponownie.

## Tryb Transkrypt — zaimplementowany (2026-09-16)

Reużywa całą infrastrukturę Subtitle (DownloadResult, _resolve_result,
list_available_subtitles, naming.build_display_filename, JobRunner, kolejkę
postępu w app.py). profiles.py::_transcript_profile wymusza subtitlesformat="vtt"
niezależnie od output_format joba — transcript_cleaner.py czyści wyłącznie VTT.

Pipeline w engine.py::_finalize_transcript (wołany z submit() po
_resolve_result, tylko dla mode="transcript"):
1. clean_vtt_to_text — usuwa nagłówek/tagi/znaczniki czasu, dedup LOKALNY
   (tylko sąsiadujące linie — rolling captions YouTube) żeby nie usuwać
   legalnych odległych powtórzeń tego samego zdania.
2. format_paragraphs — dzieli oczyszczony tekst na akapity po 4 zdania
   (podział po . ! ? z lookaheadem na wielką literę/cyfrę, żeby odróżnić
   koniec zdania od skrótu typu "np."). Działa WYŁĄCZNIE na już
   zdeduplikowanym tekście, nigdy na surowym VTT/znacznikach czasu.
3. Zapis .txt, usunięcie oryginalnego .vtt — użytkownik dostaje tylko
   czysty tekst, nigdy surowych napisów.

Znany edge case (udokumentowany testem, nie wymaga fixu): filmy z bardzo
krótkimi/nieformalnymi napisami manualnymi (np. "Me at the zoo", 19s) mogą
mieć ZERO interpunkcji kończącej zdanie — cały transkrypt wychodzi jako
jeden akapit. To jest poprawne zachowanie (nie ma zdań do podziału), nie bug.
Test end-to-end z podziałem na akapity (test_engine_submit_transcript_downloads_and_cleans_manual_caption_to_txt)
używa więc LONG_TEST_VIDEO_URL (TED talk, ~20 min, manualne napisy EN z
realną interpunkcją) — TEST_VIDEO_URL do tego nie wystarcza strukturalnie.

Drugi udokumentowany edge case (w kodzie transcript_cleaner.py, niekrytyczny):
skrót przed WIELKĄ literą ("godz. Warszawa nie śpi") wygląda identycznie jak
koniec zdania i zostanie rozdzielony — rzadkie w praktyce (YouTube
auto-punktuacja jest uboga), nierozwiązywane bez słownika skrótów.

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