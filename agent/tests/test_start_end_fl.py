"""First frame → last frame: the capability changed hands.

The failure this module was written for is worth restating, because it is the
reason the refusal below has to be a refusal and not a fallback. Originally the
endpoint was routed correctly but the model key was not: every key the resolver
knew belonged to the plain i2v families, so a start-end dispatch sent a non-FL
key to the FL endpoint. Flow either rejects that or — the expensive case —
accepts it, silently drops the end image, and bills for an ordinary clip the
user never asked for.

Since the September 2026 transport migration the six FL keys mined from the exe
are all rejected, and no `veo_3_1_i2v_*_fl*` request has been captured on
`batchexecute`. Omni gained the capability instead (`nprQif`, key
`omni_flash_i2v_<N>s_first_last`), so:

* the `omni` lane does first-to-last, and costs credits;
* every Veo lane is refused by name, dispatching nothing.

The old expensive case is exactly why the Veo path cannot quietly become a plain
first-frame dispatch: that is the same "accepted, end image dropped, billed"
outcome, reached by our own code instead of Google's.
"""
from __future__ import annotations

import pytest

from flowboard.services import flow_batch as fb
from flowboard.services import flow_sdk
from flowboard.services.flow_sdk import resolve_video_plan
from tests.flow_fakes import BatchFakeClient, operation_reply

L = "VIDEO_ASPECT_RATIO_LANDSCAPE"
P = "VIDEO_ASPECT_RATIO_PORTRAIT"


@pytest.mark.parametrize("quality", ["lite", "lite_relaxed", "fast", "quality"])
def test_every_veo_lane_refuses_a_start_end_dispatch(quality):
    """No fallback of any kind. Both available fallbacks are the old bug: a
    non-FL key ignores the end frame, and Omni bills 15-30 credits for a lane
    that may have asked to be free."""
    plan = resolve_video_plan(quality, L, start_end=True)
    assert plan["model_key"] is None
    assert plan["error"].startswith(flow_sdk.UNSUPPORTED_PREFIX + "veo_start_end")
    # And it points at the capability that does work.
    assert "OMNI" in plan["error"]


def test_the_refusal_is_the_same_on_both_aspects():
    """The old keys were per-aspect, so a portrait board and a landscape board
    took different code paths here. Aspect is its own payload slot now."""
    assert (
        resolve_video_plan("fast", P, start_end=True)["error"]
        == resolve_video_plan("fast", L, start_end=True)["error"]
    )


def test_plain_i2v_is_untouched_by_the_start_end_flag():
    """The normal path keeps its own key — no first-last key may leak into a
    dispatch that has no end frame, and no refusal may leak either."""
    plan = resolve_video_plan("fast", L)
    assert plan["error"] is None
    assert plan["model_key"] == "veo_3_1_i2v_s_fast_ultra"
    assert "first_last" not in plan["model_key"]


@pytest.mark.asyncio
async def test_the_omni_lane_sends_the_first_last_rpc_with_both_frames():
    """The capability, where it now lives. Asserted on the envelope rather than
    on the return value: a payload Flow accepts and then ignores looks exactly
    like success from the outside, and the envelope is where that shows."""
    fake = BatchFakeClient()
    fake.reply(
        fb.RPC_GEN_VIDEO_FIRST_LAST,
        operation_reply("op-fl", "proj-1"),
    )
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video(
        prompt="x",
        project_id="proj-1",
        start_media_id="img-start",
        end_media_id="img-end",
        aspect_ratio=L,
        video_quality="omni",
        duration_s=6,
    )
    assert out["operation_names"] == ["op-fl"]
    assert out["model_key"] == "omni_flash_i2v_6s_first_last"

    calls = fake.calls_for(fb.RPC_GEN_VIDEO_FIRST_LAST)
    assert len(calls) == 1
    assert calls[0]["captcha"] == fb.CAPTCHA_VIDEO
    envelope = calls[0]["freq"]
    assert "img-start" in envelope
    assert "img-end" in envelope, "the end frame never reached the payload"
    assert "omni_flash_i2v_6s_first_last" in envelope


@pytest.mark.asyncio
async def test_a_veo_start_end_dispatch_sends_nothing_at_all():
    """Better an error the UI can show than a clip that ignored the end frame
    and charged for it. The assertion that matters is the second one."""
    fake = BatchFakeClient()
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video(
        prompt="x",
        project_id="proj-1",
        start_media_id="img-start",
        end_media_id="img-end",
        aspect_ratio=L,
        video_quality="fast",
    )
    assert out["error"].startswith(flow_sdk.UNSUPPORTED_PREFIX + "veo_start_end")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_one_end_frame_shared_by_several_sources_is_refused():
    """A four-variant batch with one destination frame is never what the caller
    means, and dispatching it would bill four clips to find that out."""
    fake = BatchFakeClient()
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video(
        prompt="x",
        project_id="proj-1",
        start_media_ids=["a", "b"],
        end_media_id="img-end",
        video_quality="omni",
    )
    assert out["error"] == "end_media_id_requires_single_source"
    assert fake.calls == []


def test_the_refusal_cannot_trip_the_account_breaker():
    """It embeds caller-supplied values, like the other refusal messages, and
    the estimate endpoint is unauthenticated. Three POSTs carrying "403" in a
    lane name must not stop every dispatch in the app."""
    from flowboard.worker.processor import classify_error

    assert classify_error("unsupported_on_batch_veo_start_end_403") == "terminal"
    assert classify_error("unsupported_on_batch_lane_401_aspect_X") == "terminal"
