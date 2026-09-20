"""Threading Bridge + limit współbieżności — Warstwy 4 i 11 specyfikacji.

JobRunner opakowuje DownloadEngine.submit() (synchroniczny) w
threading.Thread, żeby nie blokować wątku wywołującego (docelowo: UI
Streamlit). Limit równoczesnych pobrań pilnuje modułowy singleton
`_concurrency_gate` — musi być modułowy (nie per-instancja JobRunner),
bo w Streamlicie app.py jest re-uruchamiane na każdą interakcję i tworzy
nowe obiekty; limit współbieżności ma obowiązywać cały proces.

WAŻNE dla przyszłej integracji z app.py: `on_state` (przekazywany dalej
jako `on_event` do silnika) będzie wołany Z WĄTKU W TLE — nie wolno w nim
bezpośrednio modyfikować `st.session_state` (Streamlit nie gwarantuje
bezpieczeństwa wątkowego dla `session_state` poza wątkiem głównym skryptu).
Docelowo trzeba to odpytywać przez `queue.Queue` z wątku głównego
(`st.rerun()`/`st.fragment`) — to wchodzi w sesji z app.py, tutaj tylko
sygnalizujemy to ograniczenie, bez implementacji.
"""

from __future__ import annotations

import threading
from typing import Callable

from src.config import settings
from src.engine import DownloadEngine, DownloadJob, EngineError
from src.progress import ProgressEvent

OnStateCallback = Callable[[ProgressEvent], None]


class _ConcurrencyGate:
    """Semafor współbieżności, którego pojemność śledzi settings.max_concurrent_jobs.

    threading.Semaphore nie wspiera zmiany pojemności w locie, a testy
    muszą móc podmienić limit przez monkeypatching `settings` (bez
    dotykania .env) — dlatego ten wrapper przebudowuje właściwy Semaphore
    leniwie, gdy wykryje, że skonfigurowany limit się zmienił, zamiast
    zapisywać sztywną wartość raz, przy imporcie modułu.

    `acquire()` zwraca konkretny obiekt Semaphore, z którego przyznano
    permit (token), a `release()` przyjmuje ten token i zwalnia zawsze
    na TYM SAMYM obiekcie — nigdy na "aktualnie bieżącym". To jest
    kluczowe: jeśli capacity zmieni się (i semafor zostanie przebudowany)
    w trakcie, gdy jakieś zadanie wciąż trzyma permit ze starej instancji,
    release() tego zadania musi trafić w starą instancję, inaczej doszłoby
    do niespójności liczenia dostępnych slotów (release na nowym,
    "podmienionym" semaforze sztucznie zwolniłby slot, który wciąż jest
    zajęty przez inne zadanie).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capacity: int | None = None
        self._semaphore: threading.Semaphore | None = None

    def _current(self) -> threading.Semaphore:
        capacity = settings.max_concurrent_jobs
        with self._lock:
            if self._semaphore is None or capacity != self._capacity:
                self._capacity = capacity
                self._semaphore = threading.Semaphore(capacity)
            return self._semaphore

    def acquire(self, blocking: bool = True) -> threading.Semaphore | None:
        """Zwraca token (konkretny Semaphore) po udanym acquire, albo None."""
        semaphore = self._current()
        if semaphore.acquire(blocking=blocking):
            return semaphore
        return None

    @staticmethod
    def release(semaphore: threading.Semaphore) -> None:
        """Zwalnia permit na TYM SAMYM obiekcie, z którego go przyznano."""
        semaphore.release()


_concurrency_gate = _ConcurrencyGate()


class JobRunner:
    def __init__(self, engine: DownloadEngine | None = None) -> None:
        self._engine = engine if engine is not None else DownloadEngine()

    def is_slot_available(self) -> bool:
        """Sprawdza z zewnątrz, czy jest wolny slot — bez zajmowania go."""
        semaphore = _concurrency_gate.acquire(blocking=False)
        if semaphore is None:
            return False
        _concurrency_gate.release(semaphore)
        return True

    def start(self, job: DownloadJob, on_state: OnStateCallback) -> str:
        """Uruchamia pobranie w tle, bez blokowania wątku wywołującego.

        Zwraca "started", jeśli zadanie faktycznie wystartowało, albo
        "queued", jeśli limit współbieżności jest wyczerpany (bez
        blokowania — wołający dostaje odpowiedź natychmiast).
        """
        semaphore = _concurrency_gate.acquire(blocking=False)
        if semaphore is None:
            return "queued"

        thread = threading.Thread(target=self._run, args=(job, on_state, semaphore), daemon=True)
        thread.start()
        return "started"

    def _run(self, job: DownloadJob, on_state: OnStateCallback, semaphore: threading.Semaphore) -> None:
        try:
            if job.playlist_scope in ("all", "selected"):
                # submit_playlist() (Faza 2a) emituje już "on_progress" per
                # pozycja przez on_state — tu dołączamy jedyny finalny
                # "on_finished", niosący ZIP + raport per pozycja, żeby
                # odbiorca (app.py) nie musiał doodpytywać engine.py.
                # "selected" (2026-09-20) — selected_indices ZAMIAST
                # start_index (wzajemnie wyłączne, patrz submit_playlist).
                result = self._engine.submit_playlist(
                    job,
                    on_event=on_state,
                    start_index=job.start_index,
                    selected_indices=(
                        job.selected_indices if job.playlist_scope == "selected" else None
                    ),
                )
                on_state(
                    ProgressEvent(
                        event_type="on_finished",
                        percent=100.0,
                        message="Zakończono",
                        result_path=result.zip_path,
                        result_title=result.playlist_title,
                        playlist_items=result.items,
                        playlist_title=result.playlist_title,
                        next_start_index=result.next_start_index,
                        playlist_scope=job.playlist_scope,
                    )
                )
            else:
                result = self._engine.submit(job, on_event=on_state)
                # engine.py emituje "on_finished" już z progress_hooks, czyli
                # gdy sam DOWNLOAD się skończy — postprocessing (ffmpeg: mp3/
                # flac/remux) dzieje się PO tym, wciąż wewnątrz submit(). Ten
                # drugi "on_finished", wysyłany dopiero gdy submit() faktycznie
                # wróci, jest jedynym jednoznacznym sygnałem "naprawdę gotowe,
                # bezpiecznie czytać plik z dysku" dla odbiorcy (app.py) —
                # niesie od razu prawdziwy DownloadResult (ścieżka + uploader/
                # title) zwrócony przez yt_dlp, żeby odbiorca nie musiał
                # zgadywać/doodpytywać go sam.
                on_state(
                    ProgressEvent(
                        event_type="on_finished",
                        percent=100.0,
                        message="Zakończono",
                        result_path=result.path,
                        result_uploader=result.uploader,
                        result_title=result.title,
                    )
                )
        except EngineError:
            # engine.submit()/submit_playlist() już wyemitował on_error
            # przez on_state i posprzątał katalog zadania (storage.cleanup)
            # — tu nic więcej.
            pass
        finally:
            _concurrency_gate.release(semaphore)
