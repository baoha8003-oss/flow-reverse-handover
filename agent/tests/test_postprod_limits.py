"""Request-shape ceilings on the post-production routes.

These endpoints are unauthenticated, and each unit of work is a full
re-encode, a frame explosion to disk, or a paid Gemini call. Every ceiling
below was added because "the caller decides how much work to ask for" is not
a safe default here — and none of them had a test, so removing one would
have gone unnoticed.
"""
from __future__ import annotations

import pytest

from flowboard.routes.postprod import MAX_CONCAT_CLIPS, MAX_NARRATION_CHARS
from flowboard.services import upscale


def _post(client, path, body):
    return client.post(f"/api/postprod/{path}", json=body)


def test_concat_refuses_more_clips_than_the_ceiling(client):
    body = {"clips": ["a.mp4"] * (MAX_CONCAT_CLIPS + 1), "output": "out.mp4"}
    assert _post(client, "concat", body).status_code == 422


def test_concat_refuses_an_empty_clip_list(client):
    assert _post(client, "concat", {"clips": [], "output": "out.mp4"}).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [("width", 0), ("width", 99999), ("height", 0), ("fps", 0), ("fps", 1000)],
)
def test_concat_refuses_absurd_frame_geometry(client, field, value):
    body = {"clips": ["a.mp4"], "output": "out.mp4", field: value}
    assert _post(client, "concat", body).status_code == 422


def test_narration_text_is_capped(client):
    body = {"text": "x" * (MAX_NARRATION_CHARS + 1)}
    assert _post(client, "narrate", body).status_code == 422


def test_narration_persona_is_capped(client):
    """Free-text persona is prepended to the prompt — same billed input as
    `text`, so it needs the same kind of bound."""
    body = {"text": "hello", "persona": "x" * 5000}
    assert _post(client, "narrate", body).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", 0),
        ("size", 10**9),
        ("outlineWidth", -1),
        ("outlineWidth", 10**6),
        ("marginV", -1),
        ("marginV", 10**9),
    ],
)
def test_subtitle_geometry_is_bounded(client, field, value):
    body = {"video": "v.mp4", "srt": "s.srt", "output": "o.mp4", field: value}
    assert _post(client, "subtitles", body).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [("x", -1), ("y", 10**9), ("width", 0), ("height", 10**9)],
)
def test_logo_geometry_is_bounded(client, field, value):
    body = {"video": "v.mp4", "logo": "l.png", "output": "o.mp4", field: value}
    assert _post(client, "logo", body).status_code == 422


@pytest.mark.parametrize(
    "field,value",
    [
        ("bgmVolume", -1),
        ("bgmVolume", 1000),
        ("origVolume", -0.5),
        ("fadeIn", -1),
        ("fadeOut", 10**6),
    ],
)
def test_bgm_mix_levels_are_bounded(client, field, value):
    body = {"video": "v.mp4", "output": "o.mp4", "track": "t.mp3", field: value}
    assert _post(client, "bgm", body).status_code == 422


def test_bgm_rejects_non_finite_volumes():
    """Python's json module accepts a bare `Infinity` literal on the way in,
    and `volume=inf` is not a filter ffmpeg can build. Asserted on the model
    itself: the test client refuses to serialise inf at all, so it cannot
    exercise this path over HTTP."""
    import pydantic

    from flowboard.routes.postprod import BgmBody

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(pydantic.ValidationError):
            BgmBody(video="v.mp4", output="o.mp4", track="t.mp3", bgmVolume=bad)


def test_video_upscale_refuses_a_source_longer_than_the_cap(tmp_path, monkeypatch):
    """Every frame is written to PNG before the engine runs: a 10-minute
    1080p clip is ~18k files and >100 GB into STORAGE_DIR/tmp."""
    src = tmp_path / "long.mp4"
    src.write_bytes(b"x")

    monkeypatch.setattr(upscale.assets, "ffprobe_bin", lambda: "ffprobe")

    class _Result:
        stdout = str(upscale.MAX_UPSCALE_DURATION_S + 60)

    monkeypatch.setattr(upscale.subprocess, "run", lambda *a, **k: _Result())

    with pytest.raises(ValueError, match="capped at"):
        upscale._reject_if_too_long(src)


def test_video_upscale_allows_a_short_source(tmp_path, monkeypatch):
    src = tmp_path / "short.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(upscale.assets, "ffprobe_bin", lambda: "ffprobe")

    class _Result:
        stdout = "12.5"

    monkeypatch.setattr(upscale.subprocess, "run", lambda *a, **k: _Result())
    upscale._reject_if_too_long(src)  # must not raise
