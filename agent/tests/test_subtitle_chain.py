"""Subtitles: transcribe the clip, then burn the result into it.

Six of the nine shipped workflows set `enable_sub`, and every piece needed
already existed — `POST /api/postprod/transcribe`, `postprod.burn_subtitles`,
and the worker's `subtitles` op. Only the wiring was missing.

The wiring has one trap, and it is the reason this file exists. The burn
needs TWO earlier results: the SRT the transcribe produced, AND the clip
that went into it. A chain that can only say "the previous output" resolves
both to the SRT — so the burn would try to draw subtitles onto a subtitle
file. Step references (`__step_N__`) exist for exactly that, and the first
test below is what would catch the mistake coming back.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import postprod_plan
from flowboard.services.pipeline_executor import _resolve_step_refs
from flowboard.services.postprod_plan import PREVIOUS, Upstream, ops_for, step_output


@pytest.fixture
def with_key(monkeypatch):
    """A Gemini key is present. Without one the planner skips subtitles
    entirely, which is why the rest of the suite never exercised this."""
    monkeypatch.setattr(
        "flowboard.services.transcribe.available", lambda: True
    )


def node(node_type, *, media=None, settings=None):
    data = {}
    if media:
        data["mediaId"] = media
    if settings is not None:
        data["sourceSettings"] = settings
    return SimpleNamespace(id=1, type=node_type, data=data)


def up(node_obj, port):
    return Upstream(node_obj, port)


SUB_SETTINGS = {
    "enable_sub": True,
    "sub_font": "Bungee",
    "sub_size": 40,
    "sub_color": "&H0000FFFF",
    "sub_outline_color": "&H00000000",
    "sub_outline_width": 2.5,
    "sub_shadow": 1.2,
    "sub_margin_v": 154,
}


def plan(settings=None, ups=None):
    return ops_for(
        node("edit_video", settings=settings or SUB_SETTINGS),
        ups if ups is not None else [up(node("video", media="clip"), "media")],
    )


# ── the trap ─────────────────────────────────────────────────────────────

def test_the_burn_reads_the_clip_not_the_subtitle_file(with_key):
    """The mistake this whole mechanism guards against. With only a
    "previous output" placeholder, the burn's video input resolves to the
    SRT the transcribe just produced."""
    ops = plan()
    assert [o["op"] for o in ops] == ["transcribe", "subtitles"]
    burn = ops[1]
    assert burn["video"] != burn["srt"], "the burn is reading its own SRT as the clip"
    assert burn["video"] == "clip"
    assert burn["srt"] == step_output(1)


def test_the_same_trap_when_subtitles_are_not_the_first_pass(with_key):
    """With an earlier pass in the chain the clip is no longer a literal
    media id — it is that pass's output, which still must not be the SRT."""
    settings = {**SUB_SETTINGS, "upscale_2k_4k": False,
                "enable_remove_veo_logo": True, "veo_logo_method": "delogo",
                "veo_logo_x_pct": 70, "veo_logo_y_pct": 85,
                "veo_logo_w_pct": 25, "veo_logo_h_pct": 10}
    ops = plan(settings)
    assert [o["op"] for o in ops] == ["delogo", "transcribe", "subtitles"]
    burn = ops[2]
    # Step 1 is the delogo; step 2 the transcribe.
    assert burn["video"] == step_output(1)
    assert burn["srt"] == step_output(2)
    assert burn["video"] != burn["srt"]


def test_the_transcribe_reads_the_same_clip_the_burn_does(with_key):
    ops = plan()
    assert ops[0]["video"] == ops[1]["video"]


# ── settings carried across ──────────────────────────────────────────────

def test_the_style_settings_reach_the_burn(with_key):
    burn = plan()[1]
    assert burn["font"] == "Bungee"
    assert burn["size"] == 40
    assert burn["primaryColor"] == "&H0000FFFF"
    assert burn["outlineColor"] == "&H00000000"
    assert burn["marginV"] == 154


def test_a_fractional_outline_survives_as_a_fraction(with_key):
    """The shipped workflows use 0.2, 0.5 and 2.5. Rounding to an int turned
    a hairline outline into no outline at all."""
    burn = plan({**SUB_SETTINGS, "sub_outline_width": 0.2})[1]
    assert burn["outlineWidth"] == 0.2


def test_a_missing_style_setting_is_left_to_the_default(with_key):
    burn = plan({"enable_sub": True})[1]
    assert "font" not in burn
    assert "size" not in burn


def test_a_boolean_is_not_accepted_as_a_size(with_key):
    """`True` is an int in Python and would pass straight through as 1."""
    burn = plan({**SUB_SETTINGS, "sub_size": True})[1]
    assert "size" not in burn


# ── when it must not run ─────────────────────────────────────────────────

def test_no_subtitle_pass_without_a_gemini_key(monkeypatch):
    """The transcribe would fail, and a failed pass fails the whole node —
    discarding the passes that already succeeded. Skipping subtitles costs
    the user a caption track; scheduling them costs the entire edit."""
    monkeypatch.setattr("flowboard.services.transcribe.available", lambda: False)
    assert plan() == []


def test_no_subtitle_pass_when_the_flag_is_off(with_key):
    assert plan({"enable_sub": False}) == []


def test_subtitles_do_not_run_without_a_clip(with_key):
    assert plan(SUB_SETTINGS, ups=[]) == []


# ── step references, on their own ────────────────────────────────────────

def test_a_step_reference_resolves_to_that_steps_output():
    params, err = _resolve_step_refs(
        {"op": "subtitles", "video": step_output(1), "srt": step_output(2)},
        ["clipOut", "srtOut"],
    )
    assert err is None
    assert params == {"op": "subtitles", "video": "clipOut", "srt": "srtOut"}


def test_previous_still_means_the_last_output():
    params, err = _resolve_step_refs(
        {"op": "upscale", "source": PREVIOUS}, ["a", "b", "c"]
    )
    assert err is None
    assert params["source"] == "c"


def test_literals_pass_through_untouched():
    params, err = _resolve_step_refs(
        {"op": "bgm", "video": "m1", "track": "calm.mp3", "bgmVolume": 0.4}, ["x"]
    )
    assert err is None
    assert params["track"] == "calm.mp3"
    assert params["bgmVolume"] == 0.4


def test_a_reference_past_the_end_is_a_hard_stop():
    """Dispatching with the placeholder still in place would hand ffmpeg a
    media id that does not exist."""
    _, err = _resolve_step_refs({"op": "x", "video": step_output(3)}, ["a"])
    assert err == "step_reference_out_of_range"


def test_previous_with_nothing_before_it_is_a_hard_stop():
    _, err = _resolve_step_refs({"op": "x", "video": PREVIOUS}, [])
    assert err == "no_previous_output"


def test_a_malformed_step_reference_is_a_hard_stop():
    _, err = _resolve_step_refs(
        {"op": "x", "video": f"{postprod_plan.STEP_PREFIX}abc__"}, ["a"]
    )
    assert err == "bad_step_reference"


def test_step_indexes_are_one_based():
    with pytest.raises(ValueError):
        step_output(0)


def test_no_resolved_param_ever_keeps_a_placeholder(with_key):
    """The invariant across the whole mechanism: whatever the chain shape,
    nothing that reaches the worker may still be a sentinel."""
    ops = plan({**SUB_SETTINGS, "upscale_2k_4k": True})
    outputs: list[str] = []
    for i, op in enumerate(ops, start=1):
        params, err = _resolve_step_refs(op, outputs)
        assert err is None, f"step {i}: {err}"
        for key, value in params.items():
            assert value != PREVIOUS, f"step {i}: {key} is unsubstituted"
            assert not (
                isinstance(value, str)
                and value.startswith(postprod_plan.STEP_PREFIX)
            ), f"step {i}: {key} is an unresolved step reference"
        outputs.append(f"out{i}")
