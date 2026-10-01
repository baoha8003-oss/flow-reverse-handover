"""The SDK side of the five recovered RPCs: what refuses, and what it costs.

The builders are pinned in `test_flow_batch_new_rpcs.py`. What is pinned here is
the layer that decides whether to dispatch at all — which is where this build's
money rules live. Two of them matter most:

* an extension defaults to the FREE lane, and paying is only ever explicit;
* an Omni source is refused with a reason instead of dispatched to find out.

Flow greys the extend button out for Omni clips. Sending anyway is a round trip
at best; the refusal names which clip and why, the way every other unsupported
lane in this codebase does.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import flow_batch as fb
from flowboard.services.flow_sdk import FlowSDK
from tests.flow_fakes import BatchFakeClient, envelope


def _canned(rpcid: str, payload) -> dict:
    return {"data": envelope(rpcid, payload), "status": 200}


def _sent(fake: BatchFakeClient, rpcid: str):
    """The inner payload of the one call made for `rpcid`."""
    call = next(c for c in fake.calls if c["rpcid"] == rpcid)
    return json.loads(json.loads(call["freq"])[0][0][1])


# ── credits ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_balance_comes_back_as_a_number():
    fake = BatchFakeClient({"nzlxg": _canned("nzlxg", [989, 2, 3, 3, None, 989])})
    assert (await FlowSDK(fake).get_credits())["credits"] == 989


@pytest.mark.asyncio
async def test_an_unreadable_balance_is_reported_as_unknown():
    """None, not 0 — the two send a user in opposite directions."""
    fake = BatchFakeClient({"nzlxg": _canned("nzlxg", ["?"])})
    out = await FlowSDK(fake).get_credits()
    assert out["credits"] is None and out["raw_present"] is False


@pytest.mark.asyncio
async def test_reading_the_balance_needs_no_captcha():
    """It is a read. Asking the page to mint a token for it would burn a
    single-use captcha on something that does not generate."""
    fake = BatchFakeClient({"nzlxg": _canned("nzlxg", [1])})
    await FlowSDK(fake).get_credits()
    assert fake.calls[0]["captcha"] is None


# ── create a Character ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_created_character_returns_the_id_the_store_needs():
    fake = BatchFakeClient({"C4BZMd": _canned("C4BZMd", [["pid-1", "char-1"]])})
    out = await FlowSDK(fake).create_character("pid-1", "Mai Anh")
    assert out == {"project_id": "pid-1", "character_id": "char-1"}


@pytest.mark.asyncio
async def test_a_blank_character_name_never_reaches_flow():
    """A name that is only whitespace would create an unnameable character, and
    a tag can never match it afterwards."""
    fake = BatchFakeClient()
    with pytest.raises(ValueError):
        await FlowSDK(fake).create_character("pid", "   ")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_creating_a_character_without_a_project_never_dispatches():
    fake = BatchFakeClient()
    with pytest.raises(ValueError):
        await FlowSDK(fake).create_character("", "NV")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_creating_a_character_needs_no_captcha_either():
    fake = BatchFakeClient({"C4BZMd": _canned("C4BZMd", [["p", "c"]])})
    await FlowSDK(fake).create_character("p", "NV")
    assert fake.calls[0]["captcha"] is None


# ── scene ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scene_hands_back_the_clone_extend_must_start_from():
    fake = BatchFakeClient({"rqZuUc": _canned("rqZuUc", [
        ["scene-1", "S"],
        [[["wf", None, None, ["t", 0, None, None, "clone-1", "x"], "pid"],
          "scene-1", []]],
    ])})
    out = await FlowSDK(fake).create_scene("pid", ["media-1"])
    assert out == {"scene_id": "scene-1", "clone_media_id": "clone-1"}


@pytest.mark.asyncio
async def test_a_scene_with_no_clips_never_dispatches():
    fake = BatchFakeClient()
    with pytest.raises(ValueError):
        await FlowSDK(fake).create_scene("pid", [])
    assert fake.calls == []


# ── extend: the money rules ───────────────────────────────────────────


def _extend_ok() -> BatchFakeClient:
    return BatchFakeClient({"fZytfe": _canned("fZytfe", [
        None, 988, [["media-ext-1"]],
        [["op-ext-1", "pid", "media-ext-1", "MEDIA_GENERATION_STATUS_PENDING"]],
    ])})


@pytest.mark.asyncio
async def test_an_extension_defaults_to_the_free_lane():
    """The caller did not ask to pay, so the caller is not billed. The extension
    family has its own low-priority key and the capture tested it at 0 credit."""
    fake = _extend_ok()
    out = await FlowSDK(fake).extend_video("nối", "pid", "sc", "clone")
    assert out["model_key"] == "veo_3_1_extension_lite_low_priority"
    assert _sent(fake, "fZytfe")[0][0][2] == "veo_3_1_extension_lite_low_priority"


@pytest.mark.asyncio
async def test_paying_for_an_extension_is_never_inferred():
    fake = _extend_ok()
    out = await FlowSDK(fake).extend_video(
        "nối", "pid", "sc", "clone", paid_lane=True
    )
    assert out["model_key"] == "veo_3_1_extension_lite"


@pytest.mark.asyncio
async def test_an_omni_source_is_refused_without_dispatching():
    """Flow greys extend out for Omni clips. Sending anyway spends a round trip
    to be told no, and the user learns nothing about why."""
    fake = _extend_ok()
    with pytest.raises(fb.FlowBatchError) as exc:
        await FlowSDK(fake).extend_video(
            "nối", "pid", "sc", "clone", source_model_key="abra_i2v_8s"
        )
    assert "omni" in str(exc.value).lower()
    assert fake.calls == [], "a refused extension must not reach Flow"


@pytest.mark.asyncio
async def test_the_refusal_names_the_clip_so_the_card_can_explain():
    fake = _extend_ok()
    with pytest.raises(fb.FlowBatchError) as exc:
        await FlowSDK(fake).extend_video(
            "x", "pid", "sc", "clone", source_model_key="abra_r2v_10s"
        )
    assert "abra_r2v_10s" in str(exc.value)


@pytest.mark.asyncio
async def test_a_veo_source_is_allowed_through():
    fake = _extend_ok()
    out = await FlowSDK(fake).extend_video(
        "x", "pid", "sc", "clone", source_model_key="veo_3_1_i2v_lite"
    )
    assert out["operation_names"] == ["op-ext-1"]


@pytest.mark.asyncio
async def test_an_extension_returns_the_shape_the_poll_already_understands():
    """Same keys `gen_video` returns, so the poll, the review loop and the money
    guard read an extension exactly like any other clip."""
    fake = _extend_ok()
    out = await FlowSDK(fake).extend_video("x", "pid", "sc", "clone")
    assert set(out) == {"raw", "operation_names", "model_key", "model_substitutions"}
    assert out["model_substitutions"] == {}


@pytest.mark.asyncio
async def test_an_extension_mints_a_captcha_because_it_generates():
    fake = _extend_ok()
    await FlowSDK(fake).extend_video("x", "pid", "sc", "clone")
    assert fake.calls[0]["captcha"] == fb.CAPTCHA_VIDEO


@pytest.mark.asyncio
async def test_the_frame_window_follows_the_clip_length():
    """A 4s source must not be handed an 8s window."""
    fake = _extend_ok()
    await FlowSDK(fake).extend_video("x", "pid", "sc", "clone", duration_s=4)
    assert _sent(fake, "fZytfe")[0][0][0] == [None, "clone", 73, 96]


@pytest.mark.asyncio
async def test_a_submit_whose_id_cannot_be_read_is_an_error_not_a_success():
    """The dangerous half of this call. If Flow accepted the extension then the
    clip is being rendered and, on a paid lane, billed — so answering "ok, zero
    operations" strands something the user paid for with nothing to poll. It has
    to come back as an error so the node shows a failure someone can act on."""
    fake = BatchFakeClient({"fZytfe": _canned("fZytfe", [None, 988, [], []])})
    out = await FlowSDK(fake).extend_video("x", "pid", "sc", "clone")
    assert out.get("error")
    assert not out.get("operation_names")
