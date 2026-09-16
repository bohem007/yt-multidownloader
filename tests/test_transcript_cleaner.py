from __future__ import annotations

from src.transcript_cleaner import clean_vtt_to_text, format_paragraphs


def test_simple_vtt_without_duplicates_becomes_clean_text():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
Hello world.

00:00:02.000 --> 00:00:04.000
This is a test.
"""
    assert clean_vtt_to_text(vtt) == "Hello world. This is a test."


def test_realistic_auto_caption_vtt_deduplicates_rolling_lines():
    """Format typowy dla auto-napisów YouTube: kolejny cue powtarza koniec
    poprzedniego (tu: pierwszą linię), zanim dopisze nowy fragment (druga
    linia) — dedup lokalny musi to zwinąć bez utraty nowego tekstu."""
    vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.030 --> 00:00:02.070 align:start position:0%
next up on the tonight show

00:00:02.070 --> 00:00:02.080 align:start position:0%
next up on the tonight show


00:00:02.080 --> 00:00:05.480 align:start position:0%
next up on the tonight show
we talk about

00:00:05.480 --> 00:00:05.490 align:start position:0%
we talk about
"""
    assert clean_vtt_to_text(vtt) == "next up on the tonight show we talk about"


def test_style_and_karaoke_tags_are_stripped_text_preserved():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
<i>Hello</i> <b>world</b><00:00:01.000><c> test</c>
"""
    assert clean_vtt_to_text(vtt) == "Hello world test"


def test_note_and_style_blocks_are_ignored():
    vtt = """WEBVTT

NOTE This is a comment, not part of the transcript.

STYLE
::cue { color: yellow; }

00:00:00.000 --> 00:00:02.000
Actual subtitle text.
"""
    assert clean_vtt_to_text(vtt) == "Actual subtitle text."


def test_empty_content_does_not_crash():
    assert clean_vtt_to_text("") == ""
    assert clean_vtt_to_text("WEBVTT\n\n") == ""


def test_single_cue_produces_sensible_result():
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nOnly one line here.\n"
    assert clean_vtt_to_text(vtt) == "Only one line here."


def test_polish_diacritics_are_preserved():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
Cześć, jak się masz?

00:00:02.000 --> 00:00:04.000
Zażółć gęślą jaźń.
"""
    assert clean_vtt_to_text(vtt) == "Cześć, jak się masz? Zażółć gęślą jaźń."


def test_whitespace_is_normalized_within_lines():
    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nToo    many     spaces.\n"
    assert clean_vtt_to_text(vtt) == "Too many spaces."


def test_format_paragraphs_short_text_stays_single_paragraph():
    text = "Hello world. This is a test."
    result = format_paragraphs(text, sentences_per_paragraph=4)
    assert "\n\n" not in result
    assert result == "Hello world. This is a test."


def test_format_paragraphs_splits_into_correct_number_of_paragraphs_preserving_order():
    sentences = [f"Sentence number {i}." for i in range(1, 10)]  # 9 zdań
    text = " ".join(sentences)

    result = format_paragraphs(text, sentences_per_paragraph=4)
    paragraphs = result.split("\n\n")

    assert len(paragraphs) == 3  # 4 + 4 + 1, ostatni akapit krótszy
    # zdania nienaruszone i w oryginalnej kolejności
    assert " ".join(paragraphs) == text


def test_format_paragraphs_empty_text_returns_empty_string():
    assert format_paragraphs("") == ""


def test_format_paragraphs_does_not_split_abbreviation_followed_by_lowercase():
    """Skrót ('Np.') followed by małą literą nie tworzy fałszywego podziału
    zdania — to oczywisty przypadek, który musi działać (patrz znane
    ograniczenie przy _SENTENCE_SPLIT_RE dla skrótu followed by WIELKĄ
    literą, nierozwiązywane bez słownika skrótów)."""
    text = "Np. wczoraj było ciepło. Dziś jest zimno."
    result = format_paragraphs(text, sentences_per_paragraph=4)
    assert "\n\n" not in result
    assert result == text


def test_format_paragraphs_does_not_split_decimal_numbers():
    """Liczba dziesiętna ('3.14') nie ma spacji po kropce, więc nie
    wygląda jak koniec zdania — jedyny prawdziwy podział jest po 'sztukę.'."""
    text = "Cena wynosi 3.14 zł za sztukę. To jest drugie zdanie."
    result = format_paragraphs(text, sentences_per_paragraph=1)
    paragraphs = result.split("\n\n")
    assert paragraphs == ["Cena wynosi 3.14 zł za sztukę.", "To jest drugie zdanie."]


def test_legitimate_non_adjacent_repetition_is_kept():
    """Dedup jest lokalny — to samo zdanie powtórzone w odległych miejscach
    transkryptu (nie sąsiadująco) nie powinno zniknąć."""
    vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
I said hello.

00:00:02.000 --> 00:00:04.000
Then something else happened.

00:00:04.000 --> 00:00:06.000
I said hello.
"""
    assert clean_vtt_to_text(vtt) == "I said hello. Then something else happened. I said hello."
