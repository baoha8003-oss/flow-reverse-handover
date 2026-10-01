"""End-to-end tests for the local ffmpeg post-production module.

Every test drives the real ffmpeg binary that ``services.assets`` resolves.
Inputs are synthesised with ffmpeg's own lavfi sources (tiny, ~1s) and the
assertions read the actual output back — probed duration, stream layout, and
decoded pixels — because the failure modes this module exists to prevent
(stretched picture, a clip with no audio track, a silent music tail, libass
silently falling back to Arial) all produce a file that exists and plays.

Nothing is mocked. The suite skips wholesale when ffmpeg is unavailable.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from flowboard.services import assets
from flowboard.services import postprod
from flowboard.services.postprod import PostProdError

pytestmark = pytest.mark.skipif(
    not postprod.available(),
    reason="ffmpeg/ffprobe not available in the asset root or on PATH",
)


# ── helpers ───────────────────────────────────────────────────────────────


def _ffmpeg(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    return proc


def _probe(path: Path, *entries: str, select: str = "v:0") -> list[str]:
    """Read stream properties straight from ffprobe, independent of the module."""
    proc = subprocess.run(
        [
            assets.ffprobe_bin(), "-v", "error",
            "-select_streams", select,
            "-show_entries", f"stream={','.join(entries)}",
            "-of", "csv=p=0:s=,",
            str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    return [line for line in proc.stdout.strip().splitlines() if line]


def _gray_frame(path: Path, width: int, height: int, *, at: float = 0.5) -> bytes:
    """Decode one frame as 8-bit grayscale so pixels can be asserted on."""
    proc = subprocess.run(
        [
            assets.ffmpeg_bin(), "-hide_banner", "-v", "error", "-nostdin",
            "-ss", f"{at}", "-i", str(path),
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-1500:]
    assert len(proc.stdout) == width * height, (
        f"expected a {width}x{height} frame, got {len(proc.stdout)} bytes"
    )
    return proc.stdout


def _region_mean(frame: bytes, width: int, x0: int, x1: int, y0: int, y1: int) -> float:
    values = [frame[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]
    return sum(values) / len(values)


def _mean_volume_db(path: Path, *, start: float = 0.0) -> float:
    """volumedetect's mean_volume for a slice; -inf-ish means true silence."""
    proc = subprocess.run(
        [
            assets.ffmpeg_bin(), "-hide_banner", "-nostdin",
            "-ss", f"{start}", "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    match = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)
    assert match, proc.stderr[-1500:]
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


# ── fixtures: real, tiny media built once for the module ──────────────────


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    """Synthesise every input the tests need with ffmpeg's lavfi sources."""
    root = tmp_path_factory.mktemp("postprod-inputs")

    silent = root / "silent-4x3.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(silent),
    )

    with_audio = root / "sound-16x9.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(with_audio),
    )

    black = root / "black.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=color=black:size=320x240:rate=15:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(black),
    )

    short_music = root / "short-music.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=0.4",
            "-c:a", "pcm_s16le", str(short_music))

    long_music = root / "long-music.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=3",
            "-c:a", "pcm_s16le", str(long_music))

    long_narration = root / "narration-long.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=2.4",
            "-c:a", "pcm_s16le", str(long_narration))

    short_narration = root / "narration-short.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=0.5",
            "-c:a", "pcm_s16le", str(short_narration))

    logo = root / "logo.png"
    _ffmpeg("-f", "lavfi", "-i", "color=color=white:size=32x32",
            "-frames:v", "1", str(logo))

    srt = root / "captions.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n\n",
        encoding="utf-8",
    )

    return {
        "silent": silent,
        "with_audio": with_audio,
        "black": black,
        "short_music": short_music,
        "long_music": long_music,
        "long_narration": long_narration,
        "short_narration": short_narration,
        "logo": logo,
        "srt": srt,
    }


# ── probing ───────────────────────────────────────────────────────────────


def test_probe_duration_reads_real_length(media):
    assert postprod.probe_duration(media["silent"]) == pytest.approx(1.0, abs=0.15)
    assert postprod.probe_duration(media["long_narration"]) == pytest.approx(2.4, abs=0.05)


def test_probe_duration_rejects_missing_file(tmp_path):
    with pytest.raises(PostProdError, match="not found"):
        postprod.probe_duration(tmp_path / "nope.mp4")


def test_has_audio_distinguishes_the_two_sources(media):
    assert postprod.has_audio(media["with_audio"]) is True
    assert postprod.has_audio(media["silent"]) is False


# ── normalize ─────────────────────────────────────────────────────────────


def test_normalize_pads_without_distorting(media, tmp_path):
    """A 4:3 source into a 16:9 frame must be pillar-boxed, not stretched."""
    out = postprod.normalize(media["silent"], tmp_path / "norm.mp4", 640, 360, fps=15)

    assert out.is_file() and out.stat().st_size > 0
    assert _probe(out, "width", "height") == ["640,360"]
    assert _probe(out, "sample_aspect_ratio") in (["1:1"], ["1/1"])

    # 320x240 fitted into 640x360 is 480x360, leaving 80px black bars each side.
    frame = _gray_frame(out, 640, 360, at=0.3)
    left_bar = _region_mean(frame, 640, 0, 70, 100, 260)
    right_bar = _region_mean(frame, 640, 570, 640, 100, 260)
    content = _region_mean(frame, 640, 90, 550, 100, 260)
    assert left_bar < 3, f"left pillar-box is not black ({left_bar:.1f})"
    assert right_bar < 3, f"right pillar-box is not black ({right_bar:.1f})"
    assert content > 20, f"picture area looks empty ({content:.1f})"


def test_normalize_grafts_silent_audio_onto_a_silent_source(media, tmp_path):
    """Concat desyncs on a clip with no audio stream, so one must be created."""
    assert postprod.has_audio(media["silent"]) is False

    out = postprod.normalize(media["silent"], tmp_path / "norm-audio.mp4", 480, 270, fps=15)

    assert postprod.has_audio(out) is True
    # ffprobe emits stream fields in its own order, so ask for one at a time.
    assert _probe(out, "channels", select="a:0") == [str(postprod.AUDIO_CHANNELS)]
    assert _probe(out, "sample_rate", select="a:0") == [str(postprod.AUDIO_RATE)]
    # -shortest must cut the infinite anullsrc at the video length.
    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.2)


def test_normalize_keeps_existing_audio_and_applies_fps(media, tmp_path):
    out = postprod.normalize(media["with_audio"], tmp_path / "norm-keep.mp4", 480, 270, fps=15)

    assert _probe(out, "width", "height") == ["480,270"]
    assert _probe(out, "r_frame_rate") == ["15/1"]
    assert _mean_volume_db(out) > -50, "existing narration was dropped"


def test_normalize_letterboxes_a_portrait_source_without_stretching(media, tmp_path):
    """9:16 into 16:9 is the pipeline's real case and fits to an odd width, so
    it is where a stretch or a lost sample aspect ratio would show up."""
    portrait = tmp_path / "portrait.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=color=white:size=360x640:rate=15:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(portrait),
    )

    out = postprod.normalize(portrait, tmp_path / "wide.mp4", 1280, 720, fps=15)

    assert _probe(out, "width", "height") == ["1280,720"]
    assert _probe(out, "sample_aspect_ratio") in (["1:1"], ["1/1"])

    frame = _gray_frame(out, 1280, 720, at=0.4)
    row = 360
    lit = [x for x in range(1280) if frame[row * 1280 + x] > 10]
    picture_width = lit[-1] - lit[0] + 1
    # 360x640 fitted into a 720-high frame is 405 wide if the ratio is kept and
    # 1280 wide if it was stretched. Allow the two pixels pad's even-column
    # rounding costs, nothing like the 875 a stretch would add.
    assert abs(picture_width - 405) <= 2, f"picture is {picture_width}px wide, not ~405"
    assert lit[0] > 400 and lit[-1] < 880, "letterbox bars are missing"


def test_normalize_rejects_nonsense_geometry(media, tmp_path):
    with pytest.raises(PostProdError, match="invalid target size"):
        postprod.normalize(media["silent"], tmp_path / "x.mp4", 0, 360)
    with pytest.raises(PostProdError, match="invalid frame rate"):
        postprod.normalize(media["silent"], tmp_path / "x.mp4", 640, 360, fps=0)


# ── concat ────────────────────────────────────────────────────────────────


def test_concat_joins_normalized_clips(media, tmp_path):
    first = postprod.normalize(media["silent"], tmp_path / "a.mp4", 320, 180, fps=15)
    second = postprod.normalize(media["with_audio"], tmp_path / "b.mp4", 320, 180, fps=15)

    out = postprod.concat([first, second], tmp_path / "joined.mp4")

    assert out.stat().st_size > 0
    assert postprod.probe_duration(out) == pytest.approx(2.0, abs=0.25)
    assert _probe(out, "width", "height") == ["320,180"]
    assert postprod.has_audio(out) is True


def test_concat_handles_a_single_quote_in_the_path(media, tmp_path):
    """The concat list is quote-delimited, so ' inside a path must be escaped."""
    tricky = tmp_path / "it's a scene"
    try:
        tricky.mkdir()
    except OSError:  # pragma: no cover - filesystem refuses the character
        pytest.skip("filesystem rejects a single quote in path names")

    clip = postprod.normalize(media["silent"], tricky / "clip'1.mp4", 320, 180, fps=15)
    assert "'" in str(clip)

    out = postprod.concat([clip, clip], tmp_path / "quoted.mp4")
    assert postprod.probe_duration(out) == pytest.approx(2.0, abs=0.25)


def test_concat_entry_escapes_quotes_and_uses_absolute_paths(tmp_path):
    entry = postprod._concat_entry((tmp_path / "it's.mp4").resolve())
    assert entry.startswith("file '") and entry.endswith("'")
    assert "'\\''" in entry
    assert entry.count("\\") == 1  # only the escape, no Windows separators


def test_concat_rejects_an_empty_clip_list(tmp_path):
    with pytest.raises(PostProdError, match="at least one clip"):
        postprod.concat([], tmp_path / "out.mp4")


def test_concat_refuses_clips_of_different_sizes(media, tmp_path):
    """The demuxer copies a 640x360 clip into a 320x180 container without a
    word of complaint, so the mismatch has to be caught before ffmpeg runs."""
    small = postprod.normalize(media["silent"], tmp_path / "small.mp4", 320, 180, fps=15)
    large = postprod.normalize(media["silent"], tmp_path / "large.mp4", 640, 360, fps=15)

    with pytest.raises(PostProdError, match=r"width 320 != 640|height 180 != 360"):
        postprod.concat([small, large], tmp_path / "mismatch.mp4")
    assert not (tmp_path / "mismatch.mp4").exists()


def test_concat_refuses_a_clip_with_no_audio_stream(media, tmp_path):
    """Audio-less clip second: video runs 2s, audio stops at 1s, nothing warns."""
    clip = postprod.normalize(media["silent"], tmp_path / "with.mp4", 320, 180, fps=15)
    stripped = tmp_path / "without.mp4"
    _ffmpeg("-i", str(clip), "-an", "-c:v", "copy", str(stripped))
    assert postprod.has_audio(stripped) is False

    with pytest.raises(PostProdError, match="audio codec"):
        postprod.concat([clip, stripped], tmp_path / "desync.mp4")


def test_concat_refuses_clips_of_different_frame_rates(media, tmp_path):
    slow = postprod.normalize(media["silent"], tmp_path / "slow.mp4", 320, 180, fps=15)
    fast = postprod.normalize(media["silent"], tmp_path / "fast.mp4", 320, 180, fps=30)

    with pytest.raises(PostProdError, match="frame rate"):
        postprod.concat([slow, fast], tmp_path / "rates.mp4")


def test_concat_accepts_clips_normalized_to_the_same_profile(media, tmp_path):
    """The uniformity gate must not reject the case it exists to protect."""
    first = postprod.normalize(media["silent"], tmp_path / "u1.mp4", 320, 180, fps=15)
    second = postprod.normalize(media["with_audio"], tmp_path / "u2.mp4", 320, 180, fps=15)

    out = postprod.concat([first, second], tmp_path / "uniform.mp4")

    assert _probe(out, "width", "height") == ["320,180"]
    video_len, audio_len = (
        float(x) for x in (_probe(out, "duration")[0], _probe(out, "duration", select="a:0")[0])
    )
    assert video_len == pytest.approx(audio_len, abs=0.1), "audio and video drifted apart"


# ── subtitles ─────────────────────────────────────────────────────────────


def test_font_family_reads_the_name_libass_matches_on(tmp_path):
    """libass resolves force_style=FontName against the font's family name;
    the file stem silently falls back to Arial."""
    resolved = assets.font_path(assets.DEFAULT_SUBTITLE_FONT)
    if resolved is None:
        pytest.skip("CapCut font library not installed")
    assert postprod.font_family(str(resolved)) == "Be Vietnam Pro"


def test_burn_subtitles_actually_renders_glyphs(media, tmp_path):
    """Burning onto pure black makes the assertion unambiguous: any bright
    pixel in the caption band can only be the subtitle."""
    if assets.font_path(assets.DEFAULT_SUBTITLE_FONT) is None:
        pytest.skip("CapCut font library not installed")

    out = postprod.burn_subtitles(
        media["black"], media["srt"], tmp_path / "subbed.mp4",
        size=36, margin_v=30, outline_width=2,
    )

    assert _probe(out, "width", "height") == ["320,240"]
    frame = _gray_frame(out, 320, 240, at=0.4)
    assert max(frame) > 180, "no bright pixels — subtitles were not rendered"
    caption_band = _region_mean(frame, 320, 0, 320, 150, 230)
    top_band = _region_mean(frame, 320, 0, 320, 0, 60)
    assert caption_band > top_band + 1, "subtitle is not in the bottom caption band"


def test_burn_subtitles_carries_the_original_audio(media, tmp_path):
    if assets.font_path(assets.DEFAULT_SUBTITLE_FONT) is None:
        pytest.skip("CapCut font library not installed")

    out = postprod.burn_subtitles(
        media["with_audio"], media["srt"], tmp_path / "subbed-audio.mp4"
    )
    assert postprod.has_audio(out) is True
    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.2)


def test_burn_subtitles_rejects_a_non_ass_colour(media, tmp_path):
    with pytest.raises(PostProdError, match="ASS colour"):
        postprod.burn_subtitles(
            media["black"], media["srt"], tmp_path / "x.mp4", primary_color="#FFFF00"
        )


@pytest.mark.parametrize("colour", ["&H0000FFF", "&HFF", "&H0000FFFFF", "&HZZZZZZ", ""])
def test_ass_colour_validation_rejects_wrong_digit_counts(colour):
    """ASS colours are 6 or 8 hex digits; libass reads anything else as a
    different colour instead of complaining."""
    with pytest.raises(PostProdError, match="ASS colour"):
        postprod._ass_color(colour, field="primary_color")


@pytest.mark.parametrize("colour", ["&H00FFFFFF", "&HFFFFFF", "&h00ffffff", "&H00FFFFFF&"])
def test_ass_colour_validation_accepts_the_real_forms(colour):
    assert postprod._ass_color(colour, field="primary_color") == colour


def test_burn_subtitles_survives_a_vietnamese_path_with_spaces_and_commas(media, tmp_path):
    """Subtitle paths reach libass through the filtergraph, where the drive
    colon, separators and commas are all syntax."""
    if assets.font_path(assets.DEFAULT_SUBTITLE_FONT) is None:
        pytest.skip("CapCut font library not installed")

    scene = tmp_path / "dự án của tôi"
    scene.mkdir()
    srt = scene / "phụ đề, tập 1.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nXin chào thế giới\n\n", encoding="utf-8"
    )

    out = postprod.burn_subtitles(media["black"], srt, scene / "bản dựng cuối.mp4")

    frame = _gray_frame(out, 320, 240, at=0.4)
    assert max(frame) > 180, "libass loaded no subtitle file from the unicode path"


def test_burn_subtitles_honours_the_requested_typeface(media, tmp_path):
    """force_style=FontName falls back to Arial silently when it is given the
    file stem, so two different faces must render two different pictures."""
    if assets.font_path("Anton-Regular.ttf") is None or assets.font_path("Pacifico-Regular.ttf") is None:
        pytest.skip("CapCut font library not installed")

    anton = postprod.burn_subtitles(
        media["black"], media["srt"], tmp_path / "anton.mp4", font="Anton-Regular.ttf", size=48
    )
    pacifico = postprod.burn_subtitles(
        media["black"], media["srt"], tmp_path / "pacifico.mp4",
        font="Pacifico-Regular.ttf", size=48,
    )

    a, b = _gray_frame(anton, 320, 240, at=0.4), _gray_frame(pacifico, 320, 240, at=0.4)
    changed = sum(1 for i in range(len(a)) if abs(a[i] - b[i]) > 40)
    assert changed > 500, f"both faces rendered the same picture ({changed} px differ)"


def test_burn_subtitles_reports_an_unknown_font(media, tmp_path):
    with pytest.raises(PostProdError, match="not found"):
        postprod.burn_subtitles(
            media["black"], media["srt"], tmp_path / "x.mp4", font="NoSuchFace.ttf"
        )


def test_filter_path_escapes_the_drive_colon(tmp_path):
    escaped = postprod._filter_path(tmp_path / "sub.srt")
    assert escaped.startswith("'") and escaped.endswith("'")
    assert "\\:" in escaped
    assert "\\\\" not in escaped  # separators became forward slashes


def test_filter_path_refuses_an_unescapable_quote(tmp_path):
    with pytest.raises(PostProdError, match="single quote"):
        postprod._filter_path(tmp_path / "it's.srt")


# ── overlay ───────────────────────────────────────────────────────────────


def test_overlay_logo_lands_at_the_requested_position(media, tmp_path):
    out = postprod.overlay_logo(
        media["black"], media["logo"], tmp_path / "logo.mp4", 10, 10
    )

    frame = _gray_frame(out, 320, 240, at=0.4)
    assert _region_mean(frame, 320, 14, 38, 14, 38) > 200, "logo missing at (10,10)"
    assert _region_mean(frame, 320, 200, 300, 150, 220) < 5, "logo bled across the frame"


def test_overlay_logo_scales_and_accepts_expressions(media, tmp_path):
    out = postprod.overlay_logo(
        media["black"], media["logo"], tmp_path / "logo-br.mp4",
        "W-w-8", "H-h-8", width=16, height=16,
    )

    frame = _gray_frame(out, 320, 240, at=0.4)
    # 16x16 logo, 8px inset from the bottom-right corner.
    assert _region_mean(frame, 320, 298, 310, 218, 230) > 200
    assert _region_mean(frame, 320, 0, 100, 0, 100) < 5


def test_overlay_logo_rejects_a_missing_logo(media, tmp_path):
    with pytest.raises(PostProdError, match="logo not found"):
        postprod.overlay_logo(
            media["black"], tmp_path / "gone.png", tmp_path / "x.mp4", 0, 0
        )


# ── background music ──────────────────────────────────────────────────────


def test_mix_bgm_loops_short_music_so_the_tail_is_not_silent(media, tmp_path):
    """0.4s of music under a 1s clip: without looping the last 0.6s is dead air."""
    out = postprod.mix_bgm(
        media["silent"], media["short_music"], tmp_path / "looped.mp4", loop=True
    )

    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.15)
    assert _mean_volume_db(out, start=0.6) > -50, "music tail is silent — it did not loop"


def test_mix_bgm_trims_music_longer_than_the_video(media, tmp_path):
    out = postprod.mix_bgm(
        media["silent"], media["long_music"], tmp_path / "trimmed.mp4"
    )
    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.15)


def test_mix_bgm_keeps_the_original_audio_under_the_music(media, tmp_path):
    """amix normalisation would halve the narration; the mix must stay loud."""
    original = _mean_volume_db(media["with_audio"])
    out = postprod.mix_bgm(
        media["with_audio"], media["short_music"], tmp_path / "mixed.mp4",
        bgm_volume=0.2, fade_in=0.1, fade_out=0.1,
    )

    assert postprod.has_audio(out) is True
    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.15)
    assert _mean_volume_db(out) > original - 3.0, "original narration was ducked away"


def test_mix_bgm_takes_a_real_library_track(media, tmp_path):
    """Every shipped track is named in Vietnamese with spaces and a leading
    ``N.``, which is the whole point of passing paths as argv and not a string."""
    tracks = assets.list_bgm()
    if not tracks:
        pytest.skip("background-music library not installed")

    for index, track in enumerate(tracks):
        out = postprod.mix_bgm(media["silent"], track, tmp_path / f"bgm-{index}.mp4")
        assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.15)
        assert _mean_volume_db(out) > -60, f"{track.name} mixed in as silence"


def test_mix_bgm_rejects_a_missing_track(media, tmp_path):
    with pytest.raises(PostProdError, match="background track not found"):
        postprod.mix_bgm(media["silent"], tmp_path / "gone.mp3", tmp_path / "x.mp4")


# ── fit to narration ──────────────────────────────────────────────────────


def test_trim_to_audio_loops_the_video_for_longer_narration(media, tmp_path):
    out = postprod.trim_to_audio(
        media["silent"], media["long_narration"], tmp_path / "fit-long.mp4"
    )

    assert postprod.probe_duration(out) == pytest.approx(2.4, abs=0.15)
    assert postprod.has_audio(out) is True
    # The picture must keep moving past the 1s source, not freeze on black.
    assert _region_mean(_gray_frame(out, 320, 240, at=2.0), 320, 0, 320, 0, 240) > 20


def test_trim_to_audio_cuts_the_video_for_shorter_narration(media, tmp_path):
    out = postprod.trim_to_audio(
        media["with_audio"], media["short_narration"], tmp_path / "fit-short.mp4"
    )
    assert postprod.probe_duration(out) == pytest.approx(0.5, abs=0.12)


def test_trim_to_audio_keeps_a_real_voice_sample_whole(media, tmp_path):
    """The shipped samples are 24 kHz mono; the result has to reach the shared
    48 kHz stereo profile without losing the tail of the take."""
    voices = assets.list_voice_samples()
    if not voices:
        pytest.skip("voice sample library not installed")
    sample = assets.voice_sample_path(voices[0])
    spoken = postprod.probe_duration(sample)

    out = postprod.trim_to_audio(media["black"], sample, tmp_path / "voiced.mp4")

    assert _probe(out, "sample_rate", select="a:0") == [str(postprod.AUDIO_RATE)]
    assert _probe(out, "channels", select="a:0") == [str(postprod.AUDIO_CHANNELS)]
    audio_len = float(_probe(out, "duration", select="a:0")[0])
    assert audio_len == pytest.approx(spoken, abs=0.05), "narration was cut short"
    # Looping the 1s source must fill the whole take, not leave a frozen tail.
    assert postprod.probe_duration(out) >= spoken


def test_trim_to_audio_rejects_missing_inputs(media, tmp_path):
    with pytest.raises(PostProdError, match="narration not found"):
        postprod.trim_to_audio(media["silent"], tmp_path / "gone.wav", tmp_path / "x.mp4")


# ── missing toolchain ─────────────────────────────────────────────────────


def test_every_step_fails_clearly_without_ffmpeg(media, tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "ffmpeg_bin", lambda: None)
    monkeypatch.setattr(assets, "ffprobe_bin", lambda: None)

    assert postprod.available() is False
    with pytest.raises(PostProdError, match="ffprobe not found"):
        postprod.probe_duration(media["silent"])
    # concat probes each clip for uniformity before it encodes anything.
    with pytest.raises(PostProdError, match="ffprobe not found"):
        postprod.concat([media["silent"]], tmp_path / "x.mp4")
    with pytest.raises(PostProdError, match="ffmpeg not found"):
        postprod.burn_subtitles(media["black"], media["srt"], tmp_path / "x.mp4")


def test_overlay_logo_refuses_a_filtergraph_injection(media, tmp_path):
    """x/y are interpolated raw into `overlay=x:y`, where ':' starts another
    option and ',' starts another filter. Expressions are a real feature
    (corner placement), so they are validated rather than banned."""
    with pytest.raises(PostProdError, match="invalid overlay"):
        postprod.overlay_logo(
            media["black"], media["logo"], tmp_path / "x.mp4",
            "0:format=gray,scale=2", 0,
        )
