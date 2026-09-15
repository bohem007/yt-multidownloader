"""Profile formatów pobierania — patrz Warstwa 6 specyfikacji.

Wybór MP3 vs FLAC jest parametrem profilu audio, nie osobną ścieżką kodu.
`outtmpl_template` w `extra_opts` to tylko szablon nazwy pliku (bez
katalogu) — engine.py doklei do niego katalog zadania ze storage.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_OUTTMPL = "%(title)s.%(ext)s"
PLAYLIST_OUTTMPL = "%(playlist_index)s - %(title)s.%(ext)s"


@dataclass(frozen=True)
class DownloadProfile:
    selector: str | None
    postprocessors: list[dict] = field(default_factory=list)
    extra_opts: dict = field(default_factory=dict)


VIDEO = DownloadProfile(
    selector="bestvideo*+bestaudio/best",
    postprocessors=[
        {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        {"key": "FFmpegMetadata"},
        {"key": "EmbedThumbnail"},
    ],
    extra_opts={
        "merge_output_format": "mp4",
        "writethumbnail": True,
        "outtmpl_template": DEFAULT_OUTTMPL,
    },
)

AUDIO_MP3 = DownloadProfile(
    selector="bestaudio/best",
    postprocessors=[
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
    ],
    extra_opts={"outtmpl_template": DEFAULT_OUTTMPL},
)

AUDIO_FLAC = DownloadProfile(
    selector="bestaudio/best",
    postprocessors=[
        {"key": "FFmpegExtractAudio", "preferredcodec": "flac"},
    ],
    extra_opts={"outtmpl_template": DEFAULT_OUTTMPL},
)


def _subtitle_profile(output_format: str) -> DownloadProfile:
    subtitle_format = output_format if output_format in ("srt", "vtt") else "srt"
    return DownloadProfile(
        selector=None,
        postprocessors=[],
        extra_opts={
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": subtitle_format,
            "outtmpl_template": DEFAULT_OUTTMPL,
        },
    )


def _playlist_profile(output_format: str) -> DownloadProfile:
    if output_format == "mp4":
        base = VIDEO
    elif output_format == "flac":
        base = AUDIO_FLAC
    else:
        base = AUDIO_MP3

    extra_opts = {
        **base.extra_opts,
        "outtmpl_template": PLAYLIST_OUTTMPL,
        "ignoreerrors": True,
    }
    return DownloadProfile(
        selector=base.selector,
        postprocessors=base.postprocessors,
        extra_opts=extra_opts,
    )


def get_profile(mode: str, output_format: str) -> DownloadProfile:
    if mode == "video":
        return VIDEO

    if mode == "audio":
        return AUDIO_FLAC if output_format == "flac" else AUDIO_MP3

    if mode == "subtitle":
        return _subtitle_profile(output_format)

    if mode == "playlist":
        return _playlist_profile(output_format)

    if mode == "transcript":
        # TODO(następna sesja): profil transcript wymaga skip_download=True
        # + VTT i post-processingu tekstowego przez transcript_cleaner.py
        # (Warstwa 7 specyfikacji) — nie implementowany w tej sesji.
        raise NotImplementedError(
            "Profil transcript wchodzi w kolejnej sesji wraz z transcript_cleaner.py."
        )

    raise ValueError(f"Nieznany tryb pobierania: {mode!r}")
