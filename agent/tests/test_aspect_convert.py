"""Re-framing a clip into another aspect ratio over a background.

`enable_aspect_convert` is not a letterbox. Two of the shipped workflows
(`nguoi_que_new`, `nguoi_que_tao_tu_anh`) turn their 16:9 generations into a
9:16 card-on-a-backdrop: the clip scaled to fit, corners rounded by
`video_border_radius` (69 and 81), centred over `aspect_bg_image`, leaving
room above for the title.

The filtergraph follows the packaged tool's own, mined from its binary rather
than invented: `force_original_aspect_ratio=increase` plus a crop to fill the
backdrop, `format=yuva444p` with a `geq` alpha for the corners, then
`overlay=(W-w)/2:(H-h)/2:shortest=1`.

Geometry is measured with ffprobe on real output, never asserted from the
command string — a filtergraph that parses and produces the wrong frame is
exactly the failure a string assertion waves through.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowboard.services import assets
from flowboard.services import media as media_service
from flowboard.services import postprod
from flowboard.services.postprod import frame_for_ratio, parse_ratio
from flowboard.services.postprod_plan import Upstream, ops_for
from flowboard.worker.postprod_handler import handle_postprod

pytestmark = pytest.mark.skipif(
    not postprod.available(),
    reason="ffmpeg/ffprobe not available in the asset root or on PATH",
)


@pytest.fixture(scope="module")
def landscape(tmp_path_factory) -> dict[str, Path]:
    """A 16:9 clip with audio, and a backdrop — the shipped shape."""
    root = tmp_path_factory.mktemp("aspect")
    clip = root / "landscape.mp4"
    subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip)],
        capture_output=True, check=True,
    )
    backdrop = root / "backdrop.png"
    subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
         "-f", "lavfi", "-i", "gradients=s=200x400:c0=blue:c1=purple",
         "-frames:v", "1", str(backdrop)],
        capture_output=True, check=True,
    )
    return {"clip": clip, "backdrop": backdrop}


# ── the frame the conversion targets ──────────────────────────────────


def test_the_long_side_is_the_sources_own():
    """Never invents pixels the source does not have. A 1280x720 clip
    becomes a 720x1280 frame, so every pixel of the result came from one of
    the source. Wanting a larger frame is an upscale, and this node has a
    separate, explicit pass for that."""
    assert frame_for_ratio(1280, 720, "9:16") == (720, 1280)


def test_a_portrait_source_converts_back_to_landscape():
    assert frame_for_ratio(1080, 1920, "16:9") == (1920, 1080)


def test_dimensions_are_even():
    """yuv420p subsamples chroma 2x2 and ffmpeg refuses an odd dimension
    outright, so the rounding cannot be left to chance."""
    for ratio in ("9:16", "16:9", "4:5", "1:1", "3:4"):
        width, height = frame_for_ratio(1001, 563, ratio)
        assert width % 2 == 0 and height % 2 == 0, ratio


@pytest.mark.parametrize("bad", ["", "9-16", "9:", "0:16", "abc", None])
def test_a_ratio_that_is_not_a_ratio_is_refused(bad):
    """Interpolated into the filtergraph, so it is validated before it can
    reach one."""
    with pytest.raises(postprod.PostProdError):
        parse_ratio(bad)


# ── the conversion itself, measured on real output ────────────────────


@pytest.mark.parametrize("ratio, expected", [("9:16", 0.5625), ("1:1", 1.0)])
def test_the_output_frame_has_the_ratio_asked_for(landscape, tmp_path, ratio, expected):
    out = postprod.convert_aspect(
        landscape["clip"], tmp_path / "out.mp4",
        ratio=ratio, background_color="#101020",
    )
    width, height = postprod.frame_size(out)
    assert width / height == pytest.approx(expected, abs=1e-4)


def test_the_clip_keeps_its_audio_and_its_length(landscape, tmp_path):
    """`shortest=1` on the overlay is what ends the output: without it a
    still backdrop is an infinite source and the render never stops."""
    source_duration = postprod.probe_duration(landscape["clip"])
    out = postprod.convert_aspect(
        landscape["clip"], tmp_path / "out.mp4",
        ratio="9:16", background_image=landscape["backdrop"],
    )
    assert postprod.has_audio(out)
    assert postprod.probe_duration(out) == pytest.approx(source_duration, abs=0.2)


def test_a_rounded_clip_still_renders(landscape, tmp_path):
    """The `geq` alpha pass is where a malformed expression shows up: ffmpeg
    fails to build the graph rather than producing a square-cornered file."""
    out = postprod.convert_aspect(
        landscape["clip"], tmp_path / "out.mp4",
        ratio="9:16", background_color="#101020", border_radius=40,
    )
    assert postprod.frame_size(out) == frame_for_ratio(640, 360, "9:16")


def _pixel(video: Path, x: int, y: int) -> tuple[int, int, int]:
    """One pixel of the first frame, as RGB.

    Sampled through ffmpeg rather than an image library — the geometry
    questions here are about which pixels end up where, and a frame size
    alone cannot answer them."""
    proc = subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(video),
         "-vf", f"crop=2:2:{x}:{y}", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    return tuple(proc.stdout[:3])  # type: ignore[return-value]


@pytest.fixture(scope="module")
def red_on_blue(tmp_path_factory) -> Path:
    """A flat red clip, so a pixel sample says plainly whether it is video
    or backdrop underneath."""
    clip = tmp_path_factory.mktemp("flat") / "red.mp4"
    subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=640x360:d=1:r=15",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, check=True,
    )
    return clip


def test_the_corners_are_cut_and_the_middle_is_not(red_on_blue, tmp_path):
    """The mask has to remove the corners of the card and nothing else."""
    out = postprod.convert_aspect(
        red_on_blue, tmp_path / "out.mp4",
        ratio="9:16", background_color="#0000ff", border_radius=40,
    )
    width, height = postprod.frame_size(out)
    centre = _pixel(out, width // 2, height // 2)
    assert centre[0] > centre[2], f"the card's middle must be video, got {centre}"
    corner = _pixel(out, 2, 2)
    assert corner[2] > corner[0], f"outside the card must be backdrop, got {corner}"


def test_a_radius_past_the_half_width_is_clamped_not_refused(red_on_blue, tmp_path):
    """Corner circles that overlap fold the mask in on itself, and measured
    on 2026-09-02 the result is not a cosmetic wobble: unclamped, the whole
    card vanishes and the middle of the frame samples as backdrop. The cap
    is 'as round as this box can be' — a pill — which is friendlier than
    failing a node over a slider pushed to its end."""
    out = postprod.convert_aspect(
        red_on_blue, tmp_path / "out.mp4",
        ratio="9:16", background_color="#0000ff", border_radius=99999,
    )
    width, height = postprod.frame_size(out)
    centre = _pixel(out, width // 2, height // 2)
    assert centre[0] > centre[2], f"the card must survive the clamp, got {centre}"


@pytest.mark.parametrize("bad", ["red", "#12345", "', drawbox=0:0:9:9:red@1:t=fill,'"])
def test_a_background_colour_that_is_not_hex_is_refused(landscape, tmp_path, bad):
    """The colour is interpolated into an ffmpeg `color=` source. An
    unchecked value is filtergraph injection, not a cosmetic bug."""
    with pytest.raises(postprod.PostProdError):
        postprod.convert_aspect(
            landscape["clip"], tmp_path / "out.mp4",
            ratio="9:16", background_color=bad,
        )


# ── the planner ───────────────────────────────────────────────────────


def _ops(settings: dict) -> list[dict]:
    node = SimpleNamespace(id=1, type="edit_video", data={"sourceSettings": settings})
    clip = SimpleNamespace(id=2, type="video", data={"mediaId": "clip"})
    return ops_for(node, [Upstream(clip, "media")])


def test_the_shipped_settings_produce_an_aspect_pass():
    """Verbatim from `nguoi_que_new.json`."""
    ops = _ops({
        "enable_aspect_convert": True,
        "aspect_output": "9:16",
        "aspect_bg_color": "#000000",
        "aspect_bg_image": "D:/TOOL/anh nv/Người Que/background_nguoi_que.png",
        "video_border_radius": 81,
        "video_zoom": 100,
    })
    aspect = [op for op in ops if op["op"] == "aspect"]
    assert len(aspect) == 1
    assert aspect[0]["ratio"] == "9:16"
    assert aspect[0]["borderRadius"] == 81


def test_no_aspect_pass_when_the_flag_is_off():
    assert not [op for op in _ops({"aspect_output": "9:16"}) if op["op"] == "aspect"]


def test_the_conversion_runs_before_anything_that_draws_on_the_frame():
    """`nguoi_que_new` asks for `sub_margin_v: 357` on what becomes a
    1280-tall frame — 28% up from the bottom, below the video card rather
    than on it. Burning first would scale the captions down into the card."""
    import flowboard.services.transcribe as transcribe_mod

    original = transcribe_mod.available
    transcribe_mod.available = lambda: True
    try:
        ops = [op["op"] for op in _ops({
            "enable_aspect_convert": True,
            "aspect_output": "9:16",
            "enable_sub": True,
        })]
    finally:
        transcribe_mod.available = original
    assert ops.index("aspect") < ops.index("subtitles")


# ── the handler ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_op_produces_a_retrievable_clip(landscape):
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "aspect", "video": video_id, "ratio": "9:16", "borderRadius": 30,
    })
    assert err is None, err
    out = media_service.cached_path(result["media_ids"][0])
    assert out is not None
    assert postprod.frame_size(out) == frame_for_ratio(640, 360, "9:16")


@pytest.mark.asyncio
async def test_a_backdrop_from_the_media_cache_is_used(landscape):
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    bg_id = media_service.ingest_local_file(landscape["backdrop"], kind="image")
    result, err = await handle_postprod({
        "op": "aspect", "video": video_id, "ratio": "9:16", "backgroundImage": bg_id,
    })
    assert err is None, err
    assert not result.get("note"), "a backdrop that was found needs no excuse"


@pytest.mark.asyncio
async def test_a_missing_backdrop_falls_back_and_says_so(landscape):
    """The shipped workflows name the original author's own machine, so this
    is the common case for an imported board. Failing the node over a
    backdrop would throw away the generation; falling back silently would
    look like the setting had no effect."""
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "aspect", "video": video_id, "ratio": "9:16",
        "backgroundImage": "D:/TOOL/anh nv/Người Que/background_nguoi_que.png",
        "backgroundColor": "#101020",
    })
    assert err is None, err
    assert result.get("note"), "the fallback has to be visible to the user"
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_a_missing_ratio_is_an_error_not_a_default(landscape):
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({"op": "aspect", "video": video_id})
    assert result == {}
    assert err == "missing_ratio"


# ── the styled captions the converted frame leaves room for ───────────

#: Verbatim from `nguoi_que_new.json`'s `aspect_texts`.
SHIPPED_CAPTION = {
    "text": "{title}", "font": "Bungee", "size": 14, "color": "#FFFFFF",
    "line_spacing": 1.5, "outline_width": 3.0, "outline_color": "#000000",
    "glow_width": 0.0, "glow_color": "#60a5fa", "art_effect": "Gold Luxury",
    "x_pct": 50, "y_type": "custom", "y_pct": 29,
}


def _ops_with_title(settings: dict, title: str = "NGƯỜI QUE") -> list[dict]:
    node = SimpleNamespace(id=1, type="edit_video", data={"sourceSettings": settings})
    clip = SimpleNamespace(id=2, type="video", data={"mediaId": "clip"})
    title_node = SimpleNamespace(id=3, type="prompt", data={"prompt": title})
    return ops_for(node, [Upstream(clip, "media"), Upstream(title_node, "title")])


def test_the_title_placeholder_is_filled_from_the_wired_title():
    ops = _ops_with_title({"aspect_texts": [SHIPPED_CAPTION]})
    art = [op for op in ops if op["op"] == "text_art"]
    assert len(art) == 1
    assert art[0]["texts"][0]["text"] == "NGƯỜI QUE"


def test_a_placeholder_with_nothing_to_fill_it_drops_the_caption():
    """Burning a literal "{title}" into the picture is worse than leaving
    the space the layout already allows."""
    node = SimpleNamespace(
        id=1, type="edit_video",
        data={"sourceSettings": {"aspect_texts": [SHIPPED_CAPTION]}},
    )
    clip = SimpleNamespace(id=2, type="video", data={"mediaId": "clip"})
    ops = ops_for(node, [Upstream(clip, "media")])
    assert not [op for op in ops if op["op"] == "text_art"]


def test_styled_captions_replace_the_plain_title_pass():
    """Both draw the same title. Running the plain drawtext as well would
    stack a second unstyled copy over the styled one."""
    ops = [op["op"] for op in _ops_with_title({"aspect_texts": [SHIPPED_CAPTION]})]
    assert "text_art" in ops and "title" not in ops


def test_a_title_with_no_styling_still_takes_the_plain_pass():
    ops = [op["op"] for op in _ops_with_title({})]
    assert "title" in ops and "text_art" not in ops


@pytest.mark.asyncio
async def test_a_shipped_caption_renders(landscape):
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "text_art", "video": video_id,
        "texts": [{**SHIPPED_CAPTION, "text": "NGƯỜI QUE"}],
    })
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None
    assert not result.get("note"), "Gold Luxury is one of the two carried"


@pytest.mark.asyncio
async def test_an_unknown_art_effect_falls_back_and_says_which(landscape):
    """The packaged tool ships 38 gradient presets (counted in the binary); this build carries the
    two its workflows select. Quietly drawing a flat caption under a preset's
    name would look like the setting did nothing."""
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "text_art", "video": video_id,
        "texts": [{**SHIPPED_CAPTION, "text": "XIN CHÀO", "art_effect": "Neon Green"}],
    })
    assert err is None, err
    assert "Neon Green" in result.get("note", "")


@pytest.mark.asyncio
async def test_a_caption_carrying_fields_this_build_ignores_still_renders(landscape):
    """`y_type` is written by the packaged tool's dialog and has no pass
    here. An unexpected key must not become a TypeError in a constructor."""
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "text_art", "video": video_id,
        "texts": [{**SHIPPED_CAPTION, "text": "XIN CHÀO", "unheard_of_key": 7}],
    })
    assert err is None, err
    assert media_service.cached_path(result["media_ids"][0]) is not None


@pytest.mark.asyncio
async def test_no_usable_caption_is_an_error_not_a_silent_pass_through(landscape):
    video_id = media_service.ingest_local_file(landscape["clip"], kind="video")
    result, err = await handle_postprod({
        "op": "text_art", "video": video_id, "texts": [{"text": "   "}],
    })
    assert result == {}
    assert err == "missing_texts"
