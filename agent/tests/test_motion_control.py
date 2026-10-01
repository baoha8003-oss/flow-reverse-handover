"""Motion transfer: what was mined correctly, and what was mined off the
wrong screen.

The claim this file used to open with — that the driving clip never leaves the
machine, evidenced by ``[Thành Phần] Endpoint: … | Images= | Entities=`` and the
``veo_3_1_r2v_*`` keys — was reading a different function. Those belong to the
Component dispatch (`_run_sync_character`). `run_motion_control` itself takes an
``upload_video`` callable, posts to ``/v1/video:batchAsyncGenerateVideoEditVideo``
with model ``abra_edit``, and carries ``videoInput`` frame indices. See the
module docstring of `services/motion_control` for the full read-out.

The node is therefore **off the canvas** in this build, and three tests below
pin that rather than pretending otherwise. What stays is the part that was mined
right: the port order, the four refusals and the audio plan — each refusal a
board that would otherwise dispatch, come back, and be wrong in a way the user
pays for.

The r2v lane tests at the top are still live production code, but for a
different caller: the character-socket (Component) dispatch resolves those keys
so a board on a 0-credit lane is not billed for Omni Flash.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import flow_sdk
from flowboard.services import motion_control as mc
from flowboard.services.postprod_plan import Upstream

PORTRAIT = "VIDEO_ASPECT_RATIO_PORTRAIT"
LANDSCAPE = "VIDEO_ASPECT_RATIO_LANDSCAPE"


def node(node_type="visual_asset", media=None):
    data = {}
    if media is not None:
        data["mediaId"] = media
    return SimpleNamespace(id=1, type=node_type, data=data)


def wire(port, media="m-1", node_type="visual_asset"):
    return Upstream(node(node_type, media), port)


def ready(**settings):
    """A node that would dispatch, so a test can break exactly one thing."""
    base = {"motion_duration": 8}
    base.update(settings)
    return base


# ── the r2v model family ──────────────────────────────────────────────


def test_the_veo_r2v_family_has_no_key_to_send_at_all():
    """This section used to pin four Veo r2v keys mined from the binary.

    Two of them were 0-credit, which mattered: they were the lanes automation
    was allowed to use without asking. All four described the REST payload Flow
    stopped authenticating in September 2026, and no `veo_3_1_r2v_*` request has
    been captured on `batchexecute` since. So the table is gone rather than kept
    as four keys that would be rejected.

    What replaces it is a refusal in `_handle_gen_video_omni`. The keys are
    named here so a future capture has something to compare against, and so
    nobody re-adds them from this file believing they once worked on this path.
    """
    assert not hasattr(flow_sdk, "resolve_r2v_model")
    assert not hasattr(flow_sdk, "VIDEO_R2V_MODEL_KEYS")
    # Not offered under any other name either.
    assert not any(
        "r2v" in key for key in flow_sdk.BATCH_VIDEO_LANES.values()
    )


@pytest.mark.asyncio
async def test_a_veo_reference_lane_with_no_key_is_refused_before_dispatch():
    """The money rule, where it now lives.

    `lite_relaxed` is no longer here: it was measured working on 19/09/2026 and
    dispatches Veo's own 0-credit r2v key. What is still refused is every other
    Veo lane, because the remaining three r2v names carry `portrait` and the
    batch path answers `[5]` NOT_FOUND for those. Serving Omni instead would
    bill 15-30 credits to a board that asked for something else.
    """
    from flowboard.worker import processor

    _, err = await processor._handle_gen_video_omni({
        "prompt": "x",
        "project_id": "abcd1234",
        "ref_media_ids": ["m-1"],
        "duration_s": 8,
        "video_quality": "fast",
    })
    assert err is not None
    assert "veo_r2v_lane_fast" in err
    # And it names the lane that does work rather than looking like a bug.
    assert "OMNI" in err


def test_the_one_zero_credit_key_left_is_the_i2v_low_priority_lane():
    """Automation may re-run a clip unasked only on a key this set names, and
    after the migration there is exactly one. Pinned because the review loop
    reads it: an extra entry here is money spent without a decision."""
    assert flow_sdk.ZERO_CREDIT_MODEL_KEYS == frozenset(
        {"veo_3_1_i2v_lite_low_priority"}
    )
    assert flow_sdk.is_zero_credit_model("veo_3_1_r2v_lite_low_priority") is False


def test_omni_serves_the_reference_lane_and_says_what_it_costs():
    """The lane that does work here. Its price is by duration, and the table is
    informational -- Google can change it and nothing can read a balance to
    check -- so an unknown length reports no price rather than zero."""
    for duration, credits in ((4, 15), (6, 20), (8, 25), (10, 30)):
        assert flow_sdk.omni_model_key("references", duration) == f"abra_r2v_{duration}s"
        assert flow_sdk.OMNI_FLASH_CREDIT_COST[duration] == credits
    assert flow_sdk.OMNI_FLASH_CREDIT_COST.get(5) is None


# ── which images go, and in what order ────────────────────────────────


def test_the_slots_keep_their_order():
    """Order is not decoration — it is the order they enter `Images=`, and
    swapping the model with the product is a different video."""
    wires = [wire("image_3", "m-bg"), wire("image_1", "m-model"), wire("image_2", "m-item")]
    assert mc.images_in(wires) == [
        ("image_1", "m-model"), ("image_2", "m-item"), ("image_3", "m-bg"),
    ]


def test_a_partly_wired_node_is_fine():
    """A dance video needs only the model. Requiring all three would refuse
    the commonest use."""
    assert mc.images_in([wire("image_1", "m-model")]) == [("image_1", "m-model")]


def test_an_empty_slot_is_skipped_not_counted():
    wires = [wire("image_1", "m-model"), wire("image_2", None)]
    assert mc.images_in(wires) == [("image_1", "m-model")]


def test_wires_on_other_ports_are_not_images():
    assert mc.images_in([wire("prompt"), wire("motion_input_video")]) == []


# ── the driving clip ──────────────────────────────────────────────────


def test_the_driving_clip_is_found_but_is_not_an_image():
    wires = [wire("motion_input_video", "m-dance"), wire("image_1", "m-model")]
    assert mc.driving_video(wires) == "m-dance"
    assert "m-dance" not in [m for _, m in mc.images_in(wires)], (
        "the driving clip must never reach the Images payload"
    )


def test_no_driving_clip_is_a_normal_state():
    assert mc.driving_video([wire("image_1")]) is None


# ── duration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (4, 4), (6, 6), (8, 8), (10, 10), ("8s", 8), ("⏱️ 10s", 10),
    (5, None),        # the number an earlier pass mined from the wrong screen
    (7, None), (None, None), ("", None), (True, None),
])
def test_duration_accepts_only_what_the_dispatch_accepts(raw, expected):
    """4/6/8/10 — the exe's own set for this node, and the only set the worker
    will take. `5` was mined from another tab's label and could never have
    dispatched."""
    assert mc.duration_s({"motion_duration": raw}) == expected


def test_a_missing_duration_is_refused_rather_than_defaulted():
    """r2v bills by length. A default bills a length nobody chose."""
    assert mc.validate([wire("image_1")], {}) == "motion_no_duration"


# ── the refusals ──────────────────────────────────────────────────────


def test_no_images_at_all_is_refused():
    assert mc.validate([wire("motion_input_video")], ready()) == "motion_no_images"


def test_an_outfit_swap_without_the_garment_is_refused():
    """The dispatch would return the model unchanged and bill for it."""
    code = mc.validate(
        [wire("image_1")], ready(motion_replace_outfit_enabled=True)
    )
    assert code == "motion_outfit_without_garment"
    assert "image_2" in mc.explain(code)


def test_an_outfit_swap_with_the_garment_is_allowed():
    assert mc.validate(
        [wire("image_1", "m-model"), wire("image_2", "m-shirt")],
        ready(motion_replace_outfit_enabled=True),
    ) is None


def test_adding_a_background_without_one_is_refused():
    code = mc.validate([wire("image_1")], ready(motion_add_background_enabled=True))
    assert code == "motion_background_without_image"


def test_a_fully_wired_node_passes():
    assert mc.validate([wire("image_1"), wire("image_2"), wire("image_3")], ready()) is None


@pytest.mark.parametrize("code", [
    "motion_no_images", "motion_outfit_without_garment",
    "motion_background_without_image", "motion_no_duration",
])
def test_every_refusal_says_something_actionable(code):
    text = mc.explain(code)
    assert text != code and len(text) > 25


# ── audio ─────────────────────────────────────────────────────────────


def test_source_audio_needs_a_source():
    """"Use the source audio" with no video wired reads as configured and
    silently does nothing — the failure worth naming."""
    plan = mc.audio_plan({"motion_use_source_audio": True}, None)
    assert plan["useSourceAudio"] is False
    assert "chưa nối video" in plan["warning"]


def test_source_audio_with_a_source_is_on_and_quiet():
    plan = mc.audio_plan({"motion_use_source_audio": True}, "m-dance")
    assert plan["useSourceAudio"] is True
    assert "warning" not in plan


def test_the_three_audio_switches_are_independent():
    plan = mc.audio_plan(
        {"motion_mute_original_audio": True, "motion_enable_bgm": True}, "m-dance"
    )
    assert plan == {"useSourceAudio": False, "muteOriginal": True, "bgm": True}


@pytest.mark.parametrize("raw", ["true", "on", "yes", "checked", True])
def test_the_packaged_tools_spellings_of_true_are_understood(raw):
    """Its settings arrive as strings as often as booleans."""
    assert mc.audio_plan({"motion_use_source_audio": raw}, "m-x")["useSourceAudio"] is True


@pytest.mark.parametrize("raw", ["false", "", None, False, "0"])
def test_anything_else_is_off(raw):
    assert mc.audio_plan({"motion_use_source_audio": raw}, "m-x")["useSourceAudio"] is False


# ── manual actions ────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("outfit_only", "outfit_only"),
    ("video_only", "video_only"),
    ("OUTFIT_ONLY", "outfit_only"),
    ("", None), ("everything", None),
])
def test_only_the_two_real_manual_actions_are_honoured(raw, expected):
    """An unknown value must not silently become "do everything" — that is
    the expensive reading."""
    assert mc.manual_action({"motion_manual_action": raw}) == expected


# ── the node is OFF the canvas, on purpose ────────────────────────────


def test_the_node_cannot_be_created_on_a_board(client):
    """Decided 2026-09-18: until Flow's Edit Video endpoint is built, this node
    can only dispatch a reference-image request — a different generation at a
    different price from the one its name promises. So it is not offered."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    resp = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "motion_control", "x": 0, "y": 0,
        "data": {"title": "Nhảy", "prompt": "cô gái nhảy"},
    })
    assert resp.status_code == 422, resp.text


def test_an_exe_workflow_carrying_the_node_imports_as_a_note(client):
    """Importing it as something runnable would spend credits on the wrong
    generation. As a note, the step survives the round trip and says so."""
    from flowboard.services import template_import

    assert template_import.NODE_TYPE_MAP["motion_control"] == "note"


def test_nothing_dispatches_it():
    """The guard that actually keeps the money safe: even a node that reached
    the database some other way is not a generation node."""
    from flowboard.services import pipeline_executor
    from flowboard.routes import estimate

    assert "motion_control" not in pipeline_executor._GENERATION_NODE_TYPES
    assert "motion_control" not in estimate.BILLABLE_TYPES


def test_the_mined_knowledge_is_kept_for_the_rebuild():
    """The port order, the refusals and the audio plan were read out of the
    binary and are still right; only the dispatch was wrong. Deleting them
    would mean mining them again."""
    assert mc.IMAGE_PORTS == ("image_1", "image_2", "image_3")
    assert mc.validate([wire("motion_input_video")], ready()) == "motion_no_images"
