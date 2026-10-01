"""The five RPCs this build did not have, and where each payload came from.

All five were recovered from other people's captures, not from our own wire, so
every test here pins the SHAPE against the source that was read — and the source
is named. That matters more than usual: the last time this codebase trusted a
table it had not measured, `VALID_DURATIONS` said 5/10 for a worker that only
ever accepted 4/6/8/10, and a lane table shipped three invented key names.

Sources:
  * `nzlxg` (credits), `rqZuUc` (scene), `fZytfe` (extend) — spsocial/PD-Auto-Footage
    `content/flow-api-new.js`, captured 2026-09-03.
  * `C4BZMd` (create Character) — hieuh0/flowkit `feat/native-character`,
    live-validated 2026-09-17.
  * `p0UkFb` (upscale) — flowkit PR #47 (2026-09-17) AND PD-Auto-Footage, which
    DISAGREE; see `test_four_k_is_refused_because_the_sources_disagree`.

`nzlxg` and `C4BZMd` have since been confirmed live on this account: 989 credits
read back, and a Character created with its id stored. The other three have not.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import flow_batch as fb


def _inner(envelope: str):
    """The decoded inner payload of a built envelope."""
    return json.loads(json.loads(envelope)[0][0][1])


def _rpcid(envelope: str) -> str:
    return json.loads(envelope)[0][0][0]


def _reply(rpcid: str, inner) -> str:
    """One RPC reply on the wire: sentinel, length prefix, chunk.

    Built rather than pasted so a test says what it means. The parser is the
    real one, so a reply shaped wrongly here fails the same way Flow's would.
    """
    body = json.dumps([["wrb.fr", rpcid, json.dumps(inner)]])
    return ")]}'\n\n" + str(len(body)) + "\n" + body + "\n"


# ── nzlxg: the credit balance ─────────────────────────────────────────


def test_the_credits_envelope_is_byte_exact():
    """Empty inner args. A capture, not a guess — and the whole call is this."""
    assert fb.credits_request() == '[[["nzlxg","[]",null,"generic"]]]'


def test_the_balance_is_read_out_of_slot_zero():
    assert fb.read_credits(_reply("nzlxg", [989, 2, 3, 3, None, 989])) == 989


def test_an_unreadable_balance_is_none_not_zero():
    """Zero is a number a user would act on. Unknown must not look like broke."""
    assert fb.read_credits(_reply("nzlxg", [None, 2, 3])) is None


def test_a_shape_drift_in_the_balance_reply_is_none():
    assert fb.read_credits(_reply("nzlxg", {"credits": 5})) is None


# ── C4BZMd: create a Character ────────────────────────────────────────


def test_the_character_create_sends_a_bare_project_uuid():
    """Every other builder here prefixes `projects/`. This one must not — the
    capture shows the bare uuid, and the prefix makes it a malformed request."""
    inner = _inner(fb.create_character_request("8c0c7ec4-ba4b", "Mai Anh"))
    assert inner == [["8c0c7ec4-ba4b", None, None, [1, "Mai Anh", []]]]
    assert "projects/" not in json.dumps(inner)


def test_the_character_create_carries_no_client_context():
    """The generate builders all stamp `_context(project_id)`. This one does not,
    and adding it is the kind of "make it consistent" edit that turns a working
    call into a 400."""
    inner = _inner(fb.create_character_request("pid", "NV"))
    assert len(inner) == 1
    assert len(inner[0]) == 4


def test_the_character_create_is_addressed_to_the_captured_rpcid():
    assert _rpcid(fb.create_character_request("pid", "NV")) == "C4BZMd"


def test_a_create_that_answers_without_an_id_raises():
    """"Accepted and useless" must not become a stored character the app cannot
    dispatch — that is the silent-no-op failure this codebase keeps finding."""
    with pytest.raises(fb.FlowBatchError):
        fb.read_created_character(_reply("C4BZMd", [[]]))


def test_a_create_that_answers_with_non_string_ids_raises():
    with pytest.raises(fb.FlowBatchError):
        fb.read_created_character(_reply("C4BZMd", [[1, 2]]))


def test_both_identifiers_come_back_from_a_real_shaped_reply():
    reply = _reply("C4BZMd", [["proj-uuid", "char-uuid"]])
    assert fb.read_created_character(reply) == {
        "project_id": "proj-uuid",
        "character_id": "char-uuid",
    }


# ── p0UkFb: the video upscale ─────────────────────────────────────────


def test_the_upscale_object_is_thirty_two_slots_wide():
    assert len(_inner(fb.upscale_request("op", "media", "pid"))[0][0]) == 32


@pytest.mark.parametrize("aspect,expected", [
    (fb.VIDEO_ASPECT_PORTRAIT, 1),
    (fb.VIDEO_ASPECT_LANDSCAPE, 2),
])
def test_slot_two_carries_the_aspect_not_a_constant(aspect, expected):
    """The two sources disagree here: PR #47 read it as a constant 1, PD derives
    it from the source clip. PD wins, because a capture of one portrait clip
    cannot tell a constant from an aspect — and if it IS the aspect, sending 1
    for a landscape clip asks for the wrong frame and pays for it."""
    obj = _inner(fb.upscale_request("op", "media", "pid", aspect=aspect))[0][0]
    assert obj[2] == expected


def test_the_operation_id_and_the_media_id_go_in_different_slots():
    """Slot 0 is the operation id, slot 4 the media id. PD resolves one to the
    other on purpose; swapping them is a request Flow accepts and then fails."""
    obj = _inner(fb.upscale_request("op-1", "media-1", "pid"))[0][0]
    assert obj[0] == [None, "op-1"]
    assert obj[4][1] == "media-1"


def test_the_tier_and_model_are_what_the_two_sources_agree_on():
    obj = _inner(fb.upscale_request("op", "media", "pid"))[0][0]
    assert obj[6] == 2
    assert obj[31] == "veo_3_1_upsampler_1080p"


def test_four_k_is_refused_because_the_sources_disagree():
    """flowkit PR #47 puts tier 3 in slot 6 for 4K; PD-Auto-Footage always sends
    2 and changes only the model. 4K costs 50 credits, so guessing spends the
    user's money to find out who was right. Refuse until one real capture."""
    with pytest.raises(fb.FlowBatchError) as exc:
        fb.upscale_request("op", "media", "pid", resolution="4k")
    assert "4k" in str(exc.value).lower()


def test_the_four_k_model_name_is_recorded_but_not_dispatchable():
    """Knowing the name is not knowing the payload. Kept so a future capture has
    something to check against, not so a caller can reach it."""
    assert fb.UPSCALE_MODEL_4K == "veo_3_1_upsampler_4k"
    assert "4k" not in fb.upscale_request("op", "m", "p")


def test_the_upscaled_operation_id_is_read_back():
    reply = _reply("p0UkFb", [[[["op-1_upsampled"], "", None, None, 1]]])
    assert fb.read_upscale_submit(reply) == "op-1_upsampled"


def test_an_upscale_reply_with_no_id_is_none():
    assert fb.read_upscale_submit(_reply("p0UkFb", [[]])) is None


# ── rqZuUc + fZytfe: scene, then extend ───────────────────────────────


def test_the_scene_prefixes_the_project_but_the_character_create_does_not():
    """Both are community captures and they differ here. Pinned side by side so
    nobody "makes them consistent" and breaks one of them."""
    assert _inner(fb.create_scene_request("pid", ["m1"]))[0] == "projects/pid"
    assert _inner(fb.create_character_request("pid", "NV"))[0][0] == "pid"


def test_a_scene_needs_at_least_one_clip():
    with pytest.raises(fb.FlowBatchError):
        fb.create_scene_request("pid", [])


def test_the_scene_reply_yields_the_id_and_the_clone():
    """The clone is the point: the first extend must reference it, not the
    original clip — the capture says the UI does exactly that."""
    reply = _reply("rqZuUc", [
        ["scene-1", "Scene 1", None, 0, 0, 2, None, []],
        [[["wf-1", None, None, ["t", 0, None, None, "clone-1", "x"], "pid"],
          "scene-1", []]],
    ])
    assert fb.read_created_scene(reply) == {
        "scene_id": "scene-1", "clone_media_id": "clone-1",
    }


def test_a_scene_reply_without_an_id_raises():
    """Extend refuses without a scene id, so a scene that answered without one
    must fail here rather than three calls later."""
    with pytest.raises(fb.FlowBatchError):
        fb.read_created_scene(_reply("rqZuUc", [[None], []]))


def test_a_scene_reply_without_a_clone_is_not_fatal():
    """A missing clone still leaves an explicit-source extend possible."""
    reply = _reply("rqZuUc", [["scene-2", "S"], []])
    assert fb.read_created_scene(reply) == {
        "scene_id": "scene-2", "clone_media_id": None,
    }


@pytest.mark.parametrize("seconds,expected", [
    (8, (169, 192)),   # the exact pair the capture recorded off the UI
    (4, (73, 96)),
    (6, (121, 144)),
    (10, (217, 240)),
])
def test_the_frame_window_is_derived_not_hard_coded(seconds, expected):
    """8s -> 169..192 is the measured pair; the rest fall out of the same rule.
    Hard-coding 169/192 would send an 8s window for a 4s clip."""
    assert fb.extend_frame_range(seconds) == expected


def test_extend_defaults_to_the_free_lane():
    """A caller who did not ask to pay must not be billed. The extension family
    has its own low-priority key, and the capture tested it at 0 credit."""
    req = _inner(fb.extend_video_request("p", "pid", "sc", "clone"))[0][0]
    assert req[2] == "veo_3_1_extension_lite_low_priority"


def test_paying_for_an_extension_is_explicit():
    req = _inner(
        fb.extend_video_request("p", "pid", "sc", "clone", paid_lane=True)
    )[0][0]
    assert req[2] == "veo_3_1_extension_lite"


def test_the_source_and_its_frame_window_share_slot_zero():
    req = _inner(
        fb.extend_video_request("p", "pid", "sc", "clone-1", duration_s=8)
    )[0][0]
    assert req[0] == [None, "clone-1", 169, 192]


def test_the_scene_and_position_ride_in_the_trailer():
    """Position is which link of the chain this is. The scene id appears twice,
    once in the request and once in the trailer, and both are required."""
    inner = _inner(fb.extend_video_request("p", "pid", "sc-9", "src", position=3))
    assert inner[0][0][5][0] == "sc-9"
    assert inner[2][3] == ["sc-9", 3]


def test_extend_refuses_without_a_scene_or_a_source():
    with pytest.raises(fb.FlowBatchError):
        fb.extend_video_request("p", "pid", "", "src")
    with pytest.raises(fb.FlowBatchError):
        fb.extend_video_request("p", "pid", "sc", "")


def test_extend_carries_a_captcha_slot_like_every_other_generate():
    assert fb.CAPTCHA_SLOT in fb.extend_video_request("p", "pid", "sc", "src")


def test_the_extend_reader_accepts_a_record_list_in_either_slot():
    """The capture this came from says the transport uses two shapes and does
    not say which one extend answers with: `jwpduf` puts records at slot 2,
    `eb1hJf` at slot 3. Reading only one would report "no operation" for a clip
    that had in fact been submitted — and on a paid lane that clip is billed."""
    at_three = _reply("fZytfe", [None, 988, [["m"]], [["op-a", "pid", "m", "S"]]])
    at_two = _reply("fZytfe", [None, 988, [["op-b", "pid", "m", "S"]]])
    assert fb.read_extend_submit(at_three) == "op-a"
    assert fb.read_extend_submit(at_two) == "op-b"


def test_the_extend_reader_returns_none_when_neither_slot_has_a_record():
    assert fb.read_extend_submit(_reply("fZytfe", [None, 988, [], []])) is None
