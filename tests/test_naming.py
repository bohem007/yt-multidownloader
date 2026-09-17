from src.naming import build_display_filename


def test_basic_video_filename_without_lang():
    name = build_display_filename("Some Channel", "Cool Video Title", "mp4")
    assert name == "Some Channel-Cool Video Title.mp4"


def test_subtitle_filename_includes_lang():
    name = build_display_filename("Some Channel", "Cool Video Title", "srt", lang="pl")
    assert name == "Some Channel-Cool Video Title.pl.srt"


def test_illegal_windows_characters_are_replaced():
    name = build_display_filename('Chan:nel/\\*?"<>|', 'Tit:le/\\*?"<>|', "mp3")
    for illegal in ':/\\*?"<>|':
        assert illegal not in name


def test_whitespace_is_collapsed_and_trimmed():
    name = build_display_filename("  Channel   Name  ", "  Video   Title  ", "mp4")
    assert name == "Channel Name-Video Title.mp4"


def test_ext_leading_dot_is_tolerated():
    name = build_display_filename("Channel", "Title", ".mp4")
    assert name == "Channel-Title.mp4"


def test_very_long_title_is_truncated_before_extension():
    long_title = "A" * 500
    name = build_display_filename("Channel", long_title, "mp4")

    assert name.endswith(".mp4")
    stem = name.rsplit(".", 1)[0]
    assert len(stem) <= 200


def test_very_long_title_with_lang_keeps_lang_intact():
    long_title = "A" * 500
    name = build_display_filename("Channel", long_title, "srt", lang="pl")

    assert name.endswith(".pl.srt")


def test_empty_uploader_and_title_fall_back_to_placeholders():
    name = build_display_filename("", "", "mp4")
    assert name == "Unknown-download.mp4"


def test_transcript_filename_with_txt_extension_and_lang():
    """Tryb Transkrypt reużywa tę samą funkcję co Subtitle — .txt z lang
    musi działać bez żadnych zmian w naming.py (generyczny parametr ext)."""
    name = build_display_filename("Some Channel", "Cool Video Title", "txt", lang="en")
    assert name == "Some Channel-Cool Video Title.en.txt"


def test_index_none_does_not_change_existing_behavior():
    """Domyślne index=None — wywołania dla pojedynczych plików (przed Fazą
    2a) muszą dawać identyczny wynik co dotychczas."""
    name = build_display_filename("Some Channel", "Cool Video Title", "mp4", index=None)
    assert name == "Some Channel-Cool Video Title.mp4"


def test_index_adds_zero_padded_prefix():
    name = build_display_filename("Some Channel", "Cool Video Title", "mp4", index=1)
    assert name == "01 - Some Channel-Cool Video Title.mp4"


def test_index_prefix_combines_with_lang():
    name = build_display_filename("Some Channel", "Cool Video Title", "srt", lang="pl", index=3)
    assert name == "03 - Some Channel-Cool Video Title.pl.srt"


def test_index_larger_than_99_is_not_truncated():
    name = build_display_filename("Channel", "Title", "mp4", index=123)
    assert name == "123 - Channel-Title.mp4"
