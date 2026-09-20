# Archiwum zamkniętych diagnoz i decyzji

NIE wczytywać do kontekstu, chyba że zadanie tego wymaga. Treści przeniesione
dosłownie z `CLAUDE.md` — patrz tam skrócone streszczenia z odsyłaczem do tego
pliku.

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
