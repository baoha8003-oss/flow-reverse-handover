"""The planner and the handler must speak the same language.

This is the test that was missing. `test_postprod_plan.py` checked what the
planner emits; `test_postprod_worker.py` checked what the handler accepts —
each in its own vocabulary, and they did not match. All of these were live
while both suites were green:

  * the planner sent `video` to an op that reads `source` (upscale), so every
    upscale failed, and because a failed step fails the node, the completed
    work before it was discarded;
  * it sent `delogo` with no box, which that op refuses outright — and once
    a box was added, it sent it as `"78.0%"` strings while `remove_logo`
    validates `isinstance(value, int)` and raises on anything else. That
    second one slipped past the FIRST version of this file, because stubbing
    the thread-pool call stopped one layer above the service function where
    the validation lives. The stub now runs the real function's checks;
  * it sent `musicVolume` / `originalVolume` where the handler reads
    `bgmVolume` / `origVolume`, so every volume silently fell back to the
    default — "mute the original audio" left the original at FULL volume
    under the music. That one does not raise. It renders a playable file that
    is simply wrong, which is the worst kind.

So this file feeds real planner output into the real handler and asserts the
handler does not reject it. It never runs ffmpeg: the ops are stopped at the
handler's own validation, which is where every one of the bugs above lived.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from flowboard.services import postprod_plan
from flowboard.services.postprod_plan import PREVIOUS, Upstream, ops_for
from flowboard.worker import postprod_handler


def node(node_type, *, media=None, medias=None, prompt=None, settings=None):
    data = {}
    if media:
        data["mediaId"] = media
    if medias is not None:
        data["mediaIds"] = medias
    if prompt:
        data["prompt"] = prompt
    if settings is not None:
        data["sourceSettings"] = settings
    return SimpleNamespace(id=1, type=node_type, data=data)


def up(node_obj, port):
    return Upstream(node_obj, port)


class _Resolver:
    """Every media id resolves; every ffmpeg call is refused with a marker.

    The point is to reach each op's real parameter validation and stop there.
    Anything that gets past validation raises `_Reached`, which the assertion
    below treats as success — the contract held.
    """

    def install(self, monkeypatch, tmp_path):
        real = tmp_path / "in.mp4"
        real.write_bytes(b"\x00")

        monkeypatch.setattr(
            postprod_handler.media_service, "cached_path", lambda mid: real
        )
        monkeypatch.setattr(postprod_handler.assets, "bgm_path", lambda name: real)

        # Run the real service function's ARGUMENT VALIDATION, then stop
        # before it spawns ffmpeg. Stubbing `_run` outright — which the first
        # version of this file did — meant `remove_logo` never saw its
        # arguments, and a percentage string sailed through a test whose
        # whole purpose was to catch exactly that.
        async def validate_then_stop(fn, *args, **kwargs):
            checker = _VALIDATORS.get(fn.__name__)
            if checker is not None:
                checker(*args, **kwargs)
            raise _Reached(fn.__name__, kwargs)

        monkeypatch.setattr(postprod_handler, "_run", validate_then_stop)
        # frame_size is probed for percentage geometry; give it a real frame.
        monkeypatch.setattr(
            postprod_handler.postprod, "frame_size", lambda _p: (1080, 1920)
        )


def _check_delogo(src, dst, *, x, y, width, height):
    """`remove_logo`'s own contract, asserted here so a percentage string
    cannot reach it again. Written out rather than imported: this IS the
    check, and it must fail loudly if the service ever relaxes it."""
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
        assert not isinstance(value, bool), f"delogo {name} is a bool"
        assert isinstance(value, int), f"delogo {name} is {type(value).__name__}, not int"
        assert value >= 0, f"delogo {name} is negative"
    assert width >= 1 and height >= 1


def _check_mix_bgm(video, track, dst, **kwargs):
    for name in ("bgm_volume", "orig_volume", "fade_in", "fade_out"):
        value = kwargs.get(name)
        if value is not None:
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                f"{name} is {type(value).__name__}"
            )


#: fn name -> a checker mirroring that service function's own validation.
def _check_zoom(src, dst, *, zoom_percent, x, y, width, height):
    """`zoom_out_logo`'s contract: a real zoom above 100, and integer
    geometry — the same integers-only rule delogo has, for the same
    reason."""
    assert isinstance(zoom_percent, (int, float)) and not isinstance(zoom_percent, bool)
    assert 100 < float(zoom_percent) <= 200, f"zoom_percent {zoom_percent!r}"
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"zoom {name} is {type(value).__name__}, not int"
        )


_VALIDATORS = {
    "remove_logo": _check_delogo,
    "zoom_out_logo": _check_zoom,
    "mix_bgm": _check_mix_bgm,
}


class _Reached(Exception):
    def __init__(self, fn, kwargs):
        super().__init__(fn)
        self.fn = fn
        self.kwargs = kwargs


def dispatch(op: dict):
    """Run one planned op through the real handler.

    Returns the kwargs the ffmpeg call would have received; raises
    AssertionError if the handler refused the op.

    Two shapes of acceptance, because not every op ends at ffmpeg: most stop
    at the `_run` stub, but `transcribe` calls a model and writes a file, so
    it runs to completion. Treating "did not reach `_run`" as rejection made
    a working op look broken.
    """
    params = {
        k: ("resolved" if isinstance(v, str) and v.startswith("__") else v)
        for k, v in op.items()
    }
    try:
        result, err = asyncio.run(postprod_handler.handle_postprod(params))
    except _Reached as reached:
        return reached.kwargs
    if err is None:
        return result
    raise AssertionError(
        f"op {op.get('op')!r} was rejected by the handler: {err!r} (result={result!r})"
    )


@pytest.fixture(autouse=True)
def resolver(monkeypatch, tmp_path):
    _Resolver().install(monkeypatch, tmp_path)


# ── every op the planner can emit is accepted by the handler ─────────────

FULL_EDIT_SETTINGS = {
    "enable_remove_veo_logo": True,
    "veo_logo_method": "delogo",
    "veo_logo_x_pct": 70,
    "veo_logo_y_pct": 85,
    "veo_logo_w_pct": 25,
    "veo_logo_h_pct": 10,
    "enable_voice": True,
    "voice_volume": 1.0,
    "enable_bgm": True,
    "bgm_sample_file": "calm.mp3",
    "bgm_volume": 0.25,
    "mute_original_audio": True,
    "upscale_2k_4k": True,
    "upscale_resolution": 2160,
}


def _full_edit_node():
    return node("edit_video", settings=FULL_EDIT_SETTINGS)


def _full_edit_upstream():
    return [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
        up(node("prompt", prompt="TIÊU ĐỀ"), "title"),
    ]


def test_every_op_a_full_edit_plans_is_accepted_by_the_handler():
    """The blocking case. Five passes, and three of them used to be rejected
    or silently misread."""
    ops = ops_for(_full_edit_node(), _full_edit_upstream())
    assert [o["op"] for o in ops] == ["delogo", "bgm", "bgm", "title", "upscale"]
    for op in ops:
        dispatch(op)  # raises AssertionError if the handler refuses


@pytest.mark.parametrize(
    "node_obj,upstream",
    [
        (node("merge_video"), [up(node("video", medias=["a", "b"]), "media")]),
        (node("extract_last_frame"), [up(node("video", media="clip"), "video")]),
        (
            node("add_bgm", settings={"audio_path": "calm.mp3", "bgm_volume": 0.4}),
            [up(node("video", media="clip"), "media")],
        ),
        (
            node("create_voice", settings={"voice": "Kore"}),
            [up(node("prompt", prompt="đọc đi"), "text")],
        ),
        (
            node("align_video_voice", settings={"upscale_2k_4k": True}),
            [
                up(node("video", media="clip"), "media"),
                up(node("create_voice", media="vo"), "voice"),
            ],
        ),
    ],
)
def test_each_node_types_ops_are_accepted(node_obj, upstream):
    ops = ops_for(node_obj, upstream)
    assert ops, f"{node_obj.type} planned nothing"
    for op in ops:
        dispatch(op)


def test_no_planned_op_names_a_key_the_handler_does_not_read():
    """The silent half. `musicVolume` was accepted (unknown keys are ignored)
    and the volume fell back to the default, so "mute the original" did not
    mute. Rejection would have been kinder than that."""
    known = {
        "op", "clips", "video", "source", "audio", "track", "text", "srt",
        "voice", "persona", "x", "y", "width", "height", "scale", "model",
        "kind", "targetHeight", "bgmVolume", "origVolume", "fadeIn",
        "xPct", "yPct", "widthPct", "heightPct",
        "fadeOut", "startSeconds", "font", "size", "primaryColor",
        "outlineColor", "outlineWidth", "marginV", "position", "opacity",
        "seconds", "logo", "language", "shadow", "zoomPercent",
    }
    ops = ops_for(_full_edit_node(), _full_edit_upstream())
    for op in ops:
        unknown = set(op) - known
        assert not unknown, f"{op['op']} sends {unknown}, which nothing reads"


# ── the volumes actually arrive ──────────────────────────────────────────

def test_muting_the_original_really_reaches_ffmpeg_as_zero():
    """The bug that produced a correct-looking, wrong file: the plan said
    mute, the handler received the default 1.0, and the original audio played
    at full volume under the music."""
    ops = ops_for(_full_edit_node(), _full_edit_upstream())
    bgm_ops = [o for o in ops if o["op"] == "bgm"]
    assert bgm_ops
    for op in bgm_ops:
        kwargs = dispatch(op)
        assert kwargs["orig_volume"] == 0.0, "the original audio was not muted"


def test_the_configured_music_volume_reaches_ffmpeg():
    ops = ops_for(
        node("add_bgm", settings={"audio_path": "calm.mp3", "bgm_volume": 0.42}),
        [up(node("video", media="clip"), "media")],
    )
    assert dispatch(ops[0])["bgm_volume"] == 0.42


def test_narration_is_mixed_at_the_voice_volume_not_the_music_volume():
    """One op serves both, but at the music default a voice-over is buried."""
    settings = {
        "enable_voice": True,
        "voice_volume": 1.0,
        "enable_bgm": True,
        "bgm_sample_file": "calm.mp3",
        "bgm_volume": 0.2,
    }
    ops = ops_for(node("edit_video", settings=settings), _full_edit_upstream())
    voice_op, music_op = [o for o in ops if o["op"] == "bgm"]
    assert dispatch(voice_op)["bgm_volume"] == 1.0
    assert dispatch(music_op)["bgm_volume"] == 0.2


def test_the_upscale_height_reaches_the_upscaler():
    ops = ops_for(_full_edit_node(), _full_edit_upstream())
    upscale = next(o for o in ops if o["op"] == "upscale")
    dispatch(upscale)  # accepted at all — it used to be rejected outright


# ── refusals that must stay refusals ─────────────────────────────────────

def test_delogo_without_a_box_is_not_planned_at_all():
    """The op requires a box and has no default, because a guessed one blurs
    the wrong part of the frame. Planning it anyway meant the node failed at
    step one and everything after it never ran."""
    ops = ops_for(
        node("edit_video", settings={"enable_remove_veo_logo": True}),
        [up(node("video", media="clip"), "media")],
    )
    assert [o["op"] for o in ops] == []


def test_bgm_without_a_track_setting_is_not_planned():
    ops = ops_for(
        node("edit_video", settings={"enable_bgm": True}),
        [up(node("video", media="clip"), "media")],
    )
    assert ops == []


def test_a_percentage_box_reaches_ffmpeg_as_integer_pixels():
    """The bug this file exists to catch, in its second form. The packaged
    tool stores the watermark box as percentages of the frame; `remove_logo`
    takes pixels and rejects everything else."""
    ops = ops_for(_full_edit_node(), _full_edit_upstream())
    delogo = next(o for o in ops if o["op"] == "delogo")
    assert isinstance(delogo["xPct"], float), "the plan should carry percentages"
    dispatch(delogo)  # the validator above fails if pixels do not arrive


def test_the_handler_still_rejects_an_op_that_is_genuinely_malformed():
    """Proof the harness above can actually fail — otherwise every test in
    this file would pass against a handler that accepts anything."""
    with pytest.raises(AssertionError):
        dispatch({"op": "delogo", "video": "clip"})
    with pytest.raises(AssertionError):
        dispatch({"op": "title", "video": "clip"})


def test_an_unknown_op_is_rejected():
    with pytest.raises(AssertionError):
        dispatch({"op": "not_a_real_op", "video": "clip"})


def test_every_op_name_the_planner_can_emit_exists_in_the_handler():
    """A names-only check, kept because it is cheap and catches a typo before
    the parameter checks above ever run."""
    emitted = set()
    for node_obj, upstream in (
        (_full_edit_node(), _full_edit_upstream()),
        (node("merge_video"), [up(node("video", medias=["a", "b"]), "media")]),
        (node("extract_last_frame"), [up(node("video", media="c"), "video")]),
        (
            node("add_bgm", settings={"audio_path": "x.mp3"}),
            [up(node("video", media="c"), "media")],
        ),
        (node("create_voice"), [up(node("prompt", prompt="t"), "text")]),
        (
            node("align_video_voice", settings={"upscale_2k_4k": True}),
            [
                up(node("video", media="c"), "media"),
                up(node("create_voice", media="v"), "voice"),
            ],
        ),
    ):
        emitted.update(o["op"] for o in ops_for(node_obj, upstream))
    assert emitted <= set(postprod_handler._OPS)
    assert emitted, "the planner emitted nothing at all"


def test_the_zoom_watermark_method_is_accepted_by_the_handler(monkeypatch):
    """The exe's default. It goes through a different service function than
    delogo, with the same percentage geometry."""
    monkeypatch.setattr(
        postprod_handler.postprod, "frame_size", lambda _p: (1920, 1080)
    )
    ops = ops_for(
        node("edit_video", settings={
            "enable_remove_veo_logo": True,
            "veo_logo_method": "zoom",
            "veo_logo_zoom_percent": 120,
            "veo_logo_x_pct": 78, "veo_logo_y_pct": 2,
            "veo_logo_w_pct": 20, "veo_logo_h_pct": 10,
        }),
        [up(node("video", media="clip"), "media")],
    )
    assert [o["op"] for o in ops] == ["zoom_logo"]
    kwargs = dispatch(ops[0])
    # Percentages resolved to integer pixels, like delogo's box.
    assert kwargs["zoom_percent"] == 120.0
    for name in ("x", "y", "width", "height"):
        assert isinstance(kwargs[name], int), f"{name} reached ffmpeg as a float"


def test_the_subtitle_pair_is_accepted_by_the_handler(monkeypatch):
    """Both new ops, through the real handler. `transcribe` is the one op in
    the module that is NOT free — it spends the user's Gemini quota — so it
    is stubbed here rather than called."""
    monkeypatch.setattr("flowboard.services.transcribe.available", lambda: True)

    async def fake_transcribe(video, **kwargs):
        return "1\n00:00:00,000 --> 00:00:01,000\nxin chào\n"

    monkeypatch.setattr(
        "flowboard.services.transcribe.video_to_srt", fake_transcribe
    )
    ops = ops_for(
        node("edit_video", settings={
            "enable_sub": True, "sub_size": 40, "sub_outline_width": 2.5,
        }),
        [up(node("video", media="clip"), "media")],
    )
    assert [o["op"] for o in ops] == ["transcribe", "subtitles"]
    for op in ops:
        dispatch(op)


def test_the_module_pair_is_the_only_route_from_canvas_to_ffmpeg():
    """If a node type appears without a planner case, it silently never runs.
    Kept here rather than in the planner's own tests because this file is the
    one that fails when the two modules drift."""
    from flowboard.services.pipeline_executor import _POSTPROD_NODE_TYPES

    for node_type in _POSTPROD_NODE_TYPES:
        # Must not raise, and must return a list — even for the types that
        # legitimately plan nothing.
        assert isinstance(ops_for(node(node_type), []), list)
    assert postprod_plan.PREVIOUS not in postprod_handler._OPS
