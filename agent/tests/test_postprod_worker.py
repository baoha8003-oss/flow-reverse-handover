"""Tests for the single `postprod` worker request type.

One request type, `{"op": <name>, ...}`, dispatches every canvas
post-production node through `handle_postprod`. These call it directly the
same way test_processor_tier_fallback.py exercises the other handlers,
rather than round-tripping through the HTTP queue.

Every op that touches ffmpeg/RealESRGAN drives the real binary against a
tiny lavfi-generated clip — same convention as test_postprod.py — so a
regression that produces a plausible-but-wrong file (a dropped clip, an
unreachable output) is actually caught. Only Gemini TTS is stubbed: it is a
real network call billed against the developer's own API key, and the repo
already stubs it the same way in test_postprod_routes.py.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from flowboard.services import assets
from flowboard.services import media as media_service
from flowboard.services import postprod, upscale
from flowboard.worker.postprod_handler import handle_postprod

pytestmark = pytest.mark.skipif(
    not postprod.available(),
    reason="ffmpeg/ffprobe not available in the asset root or on PATH",
)


def _ffmpeg(*args: str) -> None:
    proc = subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr[-1500:]


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> dict[str, Path]:
    """Real, tiny media built once with ffmpeg's own lavfi sources."""
    root = tmp_path_factory.mktemp("postprod-worker-inputs")

    clip_a = root / "clip-a.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip_a),
    )

    clip_b = root / "clip-b.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(clip_b),
    )

    narration = root / "narration.wav"
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
        "-c:a", "pcm_s16le", str(narration),
    )

    logo = root / "logo.png"
    _ffmpeg("-f", "lavfi", "-i", "color=color=white:size=16x16", "-frames:v", "1", str(logo))

    srt = root / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chao\n\n", encoding="utf-8")

    return {"clip_a": clip_a, "clip_b": clip_b, "narration": narration, "logo": logo, "srt": srt}


def _ingest(raw_path: Path, *, kind: str) -> str:
    return media_service.ingest_local_file(raw_path, kind=kind)


# ── media.ingest_local_file ─────────────────────────────────────────────────


def test_ingest_local_file_produces_a_retrievable_media_id(raw):
    """The two invariants the handler depends on: the minted id passes
    `is_valid_media_id`, and `cached_path` can then find the bytes."""
    media_id = _ingest(raw["clip_a"], kind="video")
    assert media_service.is_valid_media_id(media_id)
    cached = media_service.cached_path(media_id)
    assert cached is not None and cached.is_file()
    assert cached.read_bytes() == raw["clip_a"].read_bytes()


def test_ingest_local_file_rejects_a_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        media_service.ingest_local_file(tmp_path / "nope.mp4", kind="video")


# ── handler-level dispatch ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_op_is_rejected():
    result, err = await handle_postprod({})
    assert result == {}
    assert err == "missing_op"


@pytest.mark.asyncio
async def test_unknown_op_is_rejected():
    result, err = await handle_postprod({"op": "not_a_real_op"})
    assert result == {}
    assert err == "unknown_postprod_op:not_a_real_op"


# ── concat: the op the spec explicitly warns must not drop inputs ──────────


@pytest.mark.asyncio
async def test_concat_resolves_every_clip_and_output_is_retrievable(raw):
    id_a = _ingest(raw["clip_a"], kind="video")
    id_b = _ingest(raw["clip_b"], kind="video")

    result, err = await handle_postprod(
        {"op": "concat", "clips": [id_a, id_b], "width": 320, "height": 240, "fps": 15}
    )

    assert err is None, err
    assert result["media_ids"]
    out_path = media_service.cached_path(result["media_ids"][0])
    assert out_path is not None and out_path.is_file()
    # Two 1s sources normalized to one profile and joined: ~2s total.
    assert postprod.probe_duration(out_path) == pytest.approx(2.0, abs=0.3)


@pytest.mark.asyncio
async def test_concat_with_missing_media_id_fails_loud_instead_of_dropping_it(raw):
    """A concat that silently skipped an unresolvable clip would still write
    a playable file — just the wrong one, one clip short. It must fail."""
    id_a = _ingest(raw["clip_a"], kind="video")

    result, err = await handle_postprod(
        {"op": "concat", "clips": [id_a, "not-a-real-media-id"]}
    )

    assert result == {}
    assert err == "missing_media:not-a-real-media-id"


@pytest.mark.asyncio
async def test_concat_requires_at_least_one_clip():
    result, err = await handle_postprod({"op": "concat", "clips": []})
    assert result == {}
    assert err == "missing_clips"


# ── one representative test per remaining op ───────────────────────────────


@pytest.mark.asyncio
async def test_thumbnail_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod({"op": "thumbnail", "video": video_id})
    assert err is None, err
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None and out.suffix == ".jpg" and out.stat().st_size > 0


@pytest.mark.asyncio
async def test_thumbnail_missing_video_id_is_rejected():
    result, err = await handle_postprod({"op": "thumbnail", "video": "does-not-exist"})
    assert result == {}
    assert err == "missing_media:does-not-exist"


@pytest.mark.asyncio
async def test_last_frame_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod({"op": "last_frame", "video": video_id})
    assert err is None, err
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None and out.is_file()


@pytest.mark.asyncio
async def test_extract_audio_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_b"], kind="video")  # has an audio stream
    result, err = await handle_postprod({"op": "extract_audio", "video": video_id})
    assert err is None, err
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None and out.suffix == ".mp3" and out.stat().st_size > 0


@pytest.mark.asyncio
async def test_fit_narration_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    audio_id = _ingest(raw["narration"], kind="audio")
    result, err = await handle_postprod(
        {"op": "fit_narration", "video": video_id, "audio": audio_id}
    )
    assert err is None, err
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None
    assert postprod.probe_duration(out) == pytest.approx(1.0, abs=0.2)


@pytest.mark.asyncio
async def test_bgm_accepts_a_media_id_track_not_only_the_bundled_library(raw):
    video_id = _ingest(raw["clip_b"], kind="video")
    track_id = _ingest(raw["narration"], kind="audio")
    result, err = await handle_postprod(
        {"op": "bgm", "video": video_id, "track": track_id}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_logo_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    logo_id = _ingest(raw["logo"], kind="image")
    result, err = await handle_postprod(
        {"op": "logo", "video": video_id, "logo": logo_id, "x": 4, "y": 4}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_title_requires_text(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod({"op": "title", "video": video_id})
    assert result == {}
    assert err == "missing_text"


@pytest.mark.asyncio
async def test_title_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod(
        {"op": "title", "video": video_id, "text": "Xin chao"}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_subtitles_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    srt_id = _ingest(raw["srt"], kind="subtitle")
    result, err = await handle_postprod(
        {"op": "subtitles", "video": video_id, "srt": srt_id}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_karaoke_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    srt_id = _ingest(raw["srt"], kind="subtitle")
    result, err = await handle_postprod(
        {"op": "karaoke", "video": video_id, "srt": srt_id}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_karaoke_declares_the_clips_own_resolution(raw, monkeypatch):
    """libass scales every size in the ASS by the real frame height over the
    declared PlayResY. A fixed portrait default would render this 320x240
    clip's captions at a fraction of the requested size, so the handler must
    probe the clip and pass what it measured."""
    from flowboard.services import karaoke

    seen: dict = {}
    real = karaoke.build_ass

    def _spy(cues, **kwargs):
        seen.update(kwargs)
        return real(cues, **kwargs)

    monkeypatch.setattr(karaoke, "build_ass", _spy)

    video_id = _ingest(raw["clip_a"], kind="video")
    srt_id = _ingest(raw["srt"], kind="subtitle")
    _, err = await handle_postprod({"op": "karaoke", "video": video_id, "srt": srt_id})

    assert err is None, err
    assert (seen.get("play_res_x"), seen.get("play_res_y")) == (320, 240)


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["subtitles", "karaoke"])
async def test_a_cueless_srt_leaves_the_clip_alone_instead_of_failing(raw, op):
    """Measured 2026-09-02: ffmpeg's `subtitles` filter exits non-zero on an
    SRT with no cues, reporting `Unable to open <path>` for a file that is
    present and readable. Letting that through would fail the node and throw
    away every pass that already succeeded — over a clip whose only problem
    is having nothing to say. Both burns skip instead, and hand back the
    caller's own media id rather than copying identical bytes into the cache
    under a new one."""
    empty = raw["srt"].parent / "empty.srt"
    empty.write_text("\n", encoding="utf-8")
    video_id = _ingest(raw["clip_a"], kind="video")
    srt_id = _ingest(empty, kind="subtitle")

    result, err = await handle_postprod({"op": op, "video": video_id, "srt": srt_id})

    assert err is None, err
    assert result["media_ids"] == [video_id], "the untouched clip, not a copy"
    assert result.get("note")


@pytest.mark.asyncio
async def test_delogo_rejects_a_zero_size_box(raw):
    """The ffmpeg-layer PostProdError must come back as an error tuple,
    never as an unhandled exception out of the handler."""
    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod(
        {"op": "delogo", "video": video_id, "x": 0, "y": 0, "width": 0, "height": 0}
    )
    assert result == {}
    assert err is not None and "delogo" in err


@pytest.mark.asyncio
async def test_delogo_output_is_retrievable(raw):
    video_id = _ingest(raw["clip_a"], kind="video")
    # delogo needs a border around the box to interpolate from, so the box
    # cannot touch the frame edge (clip_a is 320x240).
    result, err = await handle_postprod(
        {"op": "delogo", "video": video_id, "x": 20, "y": 20, "width": 32, "height": 32}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_narrate_requires_text():
    result, err = await handle_postprod({"op": "narrate"})
    assert result == {}
    assert err == "missing_text"


@pytest.mark.asyncio
async def test_narrate_never_spends_real_api_quota(monkeypatch, tmp_path):
    """Gemini TTS is billed against the developer's own key. Stub it out —
    same pattern test_postprod_routes.py uses — and prove the handler still
    wires whatever synthesize() hands back into the media cache."""
    wav_path = tmp_path / "narration-fake.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 2400)

    captured = {}

    def _fake_synthesize(text, *, voice, persona):
        captured["text"] = text
        captured["voice"] = voice
        return wav_path

    monkeypatch.setattr(
        "flowboard.worker.postprod_handler.tts.synthesize", _fake_synthesize
    )

    result, err = await handle_postprod({"op": "narrate", "text": "hello there"})

    assert err is None, err
    assert captured["text"] == "hello there"
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None and out.suffix == ".wav"


@pytest.mark.asyncio
async def test_upscale_image_output_is_retrievable(raw):
    if not upscale.available():
        pytest.skip("RealESRGAN engine/models not available")
    logo_id = _ingest(raw["logo"], kind="image")
    result, err = await handle_postprod(
        {"op": "upscale", "source": logo_id, "kind": "image"}
    )
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


# ── event-loop safety ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocking_work_is_offloaded_to_a_thread(raw, monkeypatch):
    """ffmpeg must run through run_in_threadpool, not inline on the loop —
    inline would stall every other queued request for the run's duration."""
    from flowboard.worker import postprod_handler as ph

    real_run_in_threadpool = ph.run_in_threadpool
    calls: list[object] = []

    async def _spy(fn, *a, **kw):
        calls.append(fn)
        return await real_run_in_threadpool(fn, *a, **kw)

    monkeypatch.setattr(ph, "run_in_threadpool", _spy)

    video_id = _ingest(raw["clip_a"], kind="video")
    result, err = await handle_postprod({"op": "thumbnail", "video": video_id})

    assert err is None, err
    assert calls == [postprod.grab_thumbnail]
