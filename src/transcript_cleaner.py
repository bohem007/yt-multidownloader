"""Czyszczenie VTT -> czysty tekst (Warstwa 7 specyfikacji, tryb Transkrypt).

Jedna publiczna funkcja: clean_vtt_to_text(). Działa na tekście (nie na
pliku) — engine.py wczytuje .vtt, przepuszcza treść przez ten moduł, zapisuje
wynik jako .txt i usuwa oryginał (patrz DownloadEngine._finalize_transcript).

Wybór formatu wyniku: jeden ciągły tekst, cue połączone pojedynczą spacją,
bez sztucznych podziałów na akapity. VTT nie niesie żadnego sygnału o
granicach akapitów (tylko granice cue, które są artefaktem czasu wyświetlania
napisu, nie struktury tekstu) — próba zgadywania akapitów po pauzach między
cue byłaby zgadywaniem bez podstawy, a rezultat i tak zależy od odbiorcy
(łatwiej samemu podzielić czysty ciągły tekst, niż odwrócić błędny podział).
"""

from __future__ import annotations

import re

# Linia znacznika czasu cue, np. "00:00:01.000 --> 00:00:04.000" albo
# "00:00:01.000 --> 00:00:04.000 align:start position:0%" (ustawienia cue
# po drugim znaczniku ignorujemy, dopasowanie tylko na początku linii).
_TIMESTAMP_LINE_RE = re.compile(r"^\s*(?:\d+:)?\d{2}:\d{2}[.,]\d{3}\s*-->\s*(?:\d+:)?\d{2}:\d{2}[.,]\d{3}")

# Tagi inline: <c>, </c>, <i>, </i>, <b>, </u>, <c.colorFFFFFF>, oraz
# znaczniki czasu karaoke wewnątrz linii, np. <00:00:01.000>.
_INLINE_TAG_RE = re.compile(r"<[^>]*>")

# Granica zdania: znak kończący (. ! ?) + biały znak + kolejny znak, który
# wygląda na start nowego zdania (wielka litera z polskimi diakrytykami,
# cyfra, cudzysłów). Wymóg wielkiej litery/cyfry po spacji jest kluczowy:
# odróżnia prawdziwy koniec zdania od skrótu ("np." "godz." "tj.") followed
# by lowercase continuation, bez tego "np. wczoraj było ciepło" fałszywie
# rozpadłoby się na dwa zdania. Liczby dziesiętne ("3.14") nie mają spacji
# po kropce, więc nie wchodzą w ten wzorzec wcale.
#
# Znane ograniczenie (nietestowane jako wymagane zachowanie, tylko
# udokumentowane): skrót followed by słowem zaczynającym się wielką literą
# ("godz. Warszawa nie śpi") wygląda identycznie jak koniec zdania i ZOSTANIE
# rozdzielony — YouTube auto-punktuacja jest w praktyce uboga (rzadko stawia
# kropki po skrótach w środku zdania), więc to rzadki przypadek, nie
# rozwiązywany tu bez słownika skrótów.
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9"„])')


def clean_vtt_to_text(vtt_content: str) -> str:
    """Zwraca czysty tekst bez nagłówka WEBVTT, numerów/znaczników cue,
    tagów stylu/karaoke i sąsiadujących powtórzeń linii typowych dla
    auto-napisów YouTube (rolling captions — kolejny cue często powtarza
    koniec poprzedniego, zanim doda nowy fragment).

    Dedup jest LOKALNY (porównanie tylko z ostatnią wyemitowaną linią),
    nie globalny — legalne powtórzenie tego samego zdania w innym miejscu
    transkryptu nie jest usuwane, tylko bezpośrednio sąsiadujące duplikaty."""
    lines = vtt_content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    kept: list[str] = []
    last_line: str | None = None
    in_cue_text = False

    for raw_line in lines:
        if not raw_line.strip():
            # Pusta linia = koniec bieżącego bloku (nagłówek/cue/STYLE/NOTE).
            in_cue_text = False
            continue

        if _TIMESTAMP_LINE_RE.match(raw_line):
            # Od tej linii zaczyna się tekst cue, aż do najbliższej pustej linii.
            in_cue_text = True
            continue

        if not in_cue_text:
            # Linia nagłówka (WEBVTT, Kind:, Language:), identyfikatora cue,
            # albo wewnątrz blocku NOTE/STYLE/REGION — żadna z nich nie jest
            # tekstem transkryptu, bo nie widzieliśmy jeszcze linii ze
            # znacznikiem czasu w tym bloku.
            continue

        text = _INLINE_TAG_RE.sub("", raw_line)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if text == last_line:
            # Sąsiadujący duplikat (rolling captions) — pomijamy.
            continue

        kept.append(text)
        last_line = text

    return " ".join(kept)


def format_paragraphs(text: str, sentences_per_paragraph: int = 4) -> str:
    """Dzieli JUŻ OCZYSZCZONY tekst (wynik clean_vtt_to_text, po deduplikacji)
    na akapity po `sentences_per_paragraph` zdań — nigdy nie działa na
    surowym VTT i nigdy nie zgaduje granic po znacznikach czasu. Nie zmienia
    treści zdań, tylko wstawia podział (pojedyncza spacja między zdaniami w
    akapicie, podwójny znak nowej linii między akapitami)."""
    text = text.strip()
    if not text:
        return ""

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]

    paragraphs = [
        " ".join(sentences[i : i + sentences_per_paragraph])
        for i in range(0, len(sentences), sentences_per_paragraph)
    ]
    return "\n\n".join(paragraphs)
