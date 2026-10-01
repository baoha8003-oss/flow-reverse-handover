"""The Flow SDK on the `batchexecute` transport.

Every test here drives `BatchFakeClient`, which answers with the real wire shape
— sentinel, length prefix, `["wrb.fr", rpcid, "<payload>"]` — so the production
parser runs rather than a convenient stand-in for it. And every assertion about
what was SENT reads the envelope out of `calls`, because the failure mode this
transport punishes is a payload Flow accepts, quietly ignores, and charges for:
from the outside that looks exactly like success.

This module replaced a REST-shaped one of the same size. The behaviours worth
keeping were kept and re-pointed; the ones that described `aisandbox-pa.googleapis.com`
response bodies went with the transport, because Flow stopped authenticating it.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import flow_batch as fb
from flowboard.services import flow_sdk
from flowboard.services.flow_sdk import FlowSDK
from tests.flow_fakes import (
    BatchFakeClient,
    envelope,
    image_reply,
    listing_window,
    media_reply,
    operation_reply,
)

PROJECT = "11111111-1111-4111-8111-111111111111"
MEDIA = "22222222-2222-4222-8222-222222222222"
OTHER_MEDIA = "33333333-3333-4333-8333-333333333333"
LANDSCAPE = "VIDEO_ASPECT_RATIO_LANDSCAPE"
PORTRAIT = "VIDEO_ASPECT_RATIO_PORTRAIT"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Strip the deliberate pacing so the suite does not sit through it.

    The real cadence matters — a burst of four submits looks different to
    Google's abuse detection than a person does, and the `[8]` retry waits out a
    cooldown because an immediate retry is refused again. Both are asserted by
    their own tests below rather than by elapsed time.
    """
    monkeypatch.setattr(flow_sdk, "IMAGE_SUBMIT_OFFSETS_S", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(flow_sdk, "IMAGE_TRANSIENT_RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(flow_sdk, "VIDEO_SUBMIT_GAP_S", 0.0)


# ── create_project ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_sends_the_title_and_reads_the_id_back():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_CREATE_PROJECT, [PROJECT, ["Board 1"]])
    out = await FlowSDK(fake).create_project("Board 1")

    assert out["project_id"] == PROJECT
    payload = fake.payload_for(fb.RPC_CREATE_PROJECT)
    assert payload[0] == "projects/*"
    assert payload[1][1] == ["Board 1"]
    # No captcha on this call; minting one would spend a single-use token for
    # nothing and serialise behind the generation calls that need them.
    assert fake.calls[0]["captcha"] is None


@pytest.mark.asyncio
async def test_a_project_id_in_an_unexpected_slot_is_a_failure():
    """A 200 with the id somewhere else is accepted and then silently useless.
    Scanning the envelope for the first uuid-looking string would "work" right
    up until it picked a scene id."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_CREATE_PROJECT, [["nested", PROJECT]])
    out = await FlowSDK(fake).create_project("Board 1")
    assert "create_project_failed" in out["error"]


@pytest.mark.asyncio
async def test_create_project_falls_back_to_the_pinned_id_and_says_so(monkeypatch):
    """A board that cannot get a project cannot generate at all, so a pinned
    `FLOW_PROJECT_ID` is used when Flow refuses. It is reported, because sharing
    one project between boards is a deliberate choice the user made and a
    silent one they did not."""
    monkeypatch.setattr(flow_sdk, "_pinned_project_id", lambda: PROJECT)
    fake = BatchFakeClient()
    fake.fail(fb.RPC_CREATE_PROJECT, ["PUBLIC_ERROR_X"])

    out = await FlowSDK(fake).create_project("Board 1")
    assert out["project_id"] == PROJECT
    assert out["pinned_fallback"] is True


@pytest.mark.asyncio
async def test_no_pinned_id_means_the_error_survives(monkeypatch):
    monkeypatch.setattr(flow_sdk, "_pinned_project_id", lambda: None)
    fake = BatchFakeClient()
    fake.fail(fb.RPC_CREATE_PROJECT, ["PUBLIC_ERROR_X"])
    out = await FlowSDK(fake).create_project("Board 1")
    assert out["error"].startswith("create_project_failed")
    assert "project_id" not in out


@pytest.mark.asyncio
async def test_a_tool_other_than_pinhole_is_refused_not_silently_defaulted():
    """The captured payload has no tool slot, so a caller asking for something
    else would be served the default and told nothing."""
    fake = BatchFakeClient()
    out = await FlowSDK(fake).create_project("Board 1", tool="VIDEO_FX")
    assert out["error"] == "unsupported_on_batch_tool_VIDEO_FX"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_the_project_listing_is_refused_rather_than_answered_empty():
    """No RPC for it was captured. An empty list reads as "you have no
    projects" and invites someone to recreate them all."""
    sdk = FlowSDK(BatchFakeClient())
    listed = await sdk.search_user_projects()
    assert listed["projects"] == []
    assert listed["error"] == flow_sdk.UNSUPPORTED_PROJECT_LISTING
    every = await sdk.list_user_projects_all()
    assert every["error"] == flow_sdk.UNSUPPORTED_PROJECT_LISTING


# ── images ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gen_image_sends_prompt_aspect_model_and_a_captcha():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, image_reply(MEDIA))
    out = await FlowSDK(fake).gen_image(
        prompt="a cat", project_id=PROJECT,
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
    )
    assert out["media_ids"] == [MEDIA]
    assert out["media_entries"][0]["url"].endswith("?sig=x")

    call = fake.calls_for(fb.RPC_GEN_IMAGE)[0]
    assert call["captcha"] == fb.CAPTCHA_IMAGE
    item = fb_item(fake)
    assert item[8] == [[["a cat"]]]
    assert item[4] == fb.resolve_aspect("IMAGE_ASPECT_RATIO_PORTRAIT")


def fb_item(fake: BatchFakeClient, index: int = 0):
    """The n-th request item out of an image envelope."""
    return fake.payload_for(fb.RPC_GEN_IMAGE, index)[1][0]


@pytest.mark.asyncio
async def test_gen_image_resolves_the_nickname_to_a_wire_id():
    """Settings stores `NANO_BANANA_PRO`; Flow only knows `GEM_PIX_2`. Sending
    the nickname is rejected, and sending an unknown one must not be attempted
    at all -- `flow_batch` checks it against a closed set."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, image_reply(MEDIA))
    await FlowSDK(fake).gen_image(
        prompt="a cat", project_id=PROJECT, image_model="NANO_BANANA_2",
    )
    assert fb_item(fake)[5] == "NARWHAL"


def test_an_unknown_image_nickname_falls_back_and_a_bad_wire_id_does_not():
    """Two different rules, for a reason. Every image model costs the same, so a
    stale frontend nickname may fall back rather than break dispatch. A wire id
    is checked against a closed set, so the fallback can never invent one."""
    assert flow_sdk.resolve_image_model("NO_SUCH_MODEL") == "GEM_PIX_2"
    with pytest.raises(ValueError):
        fb.image_request("a cat", PROJECT, model="NO_SUCH_MODEL")


@pytest.mark.asyncio
async def test_three_variants_are_three_rpcs_with_three_seeds():
    """There is no "how many" field. Flow's own composer submits one per
    variant, each with its own single-use captcha."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, image_reply(MEDIA))
    await FlowSDK(fake).gen_image(prompt="a cat", project_id=PROJECT, variant_count=3)

    calls = fake.calls_for(fb.RPC_GEN_IMAGE)
    assert len(calls) == 3
    assert len({fb_item(fake, i)[3] for i in range(3)}) == 3


@pytest.mark.asyncio
async def test_each_variant_can_carry_its_own_prompt():
    """Four variants as four stances rather than four seeds of one."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, image_reply(MEDIA))
    await FlowSDK(fake).gen_image(
        prompt="fallback", project_id=PROJECT, variant_count=2,
        prompts=["standing", "sitting"],
    )
    texts = [fb_item(fake, i)[8][0][0][0] for i in range(2)]
    assert texts == ["standing", "sitting"]


@pytest.mark.asyncio
async def test_a_transient_rejection_retries_once_and_keeps_what_landed():
    """`[8]` is Flow's generation side refusing under load. Measured upstream:
    an immediate retry is refused again, so one cooldown and one more attempt.
    Not a loop -- a retry that keeps going is a retry that keeps paying."""
    state = {"n": 0}

    def answer(_match):
        state["n"] += 1
        # The first variant's first attempt is rejected; everything else lands.
        if state["n"] == 1:
            return {"data": json.loads(json.dumps(_rpc_error_body()))}
        return {"data": envelope(fb.RPC_GEN_IMAGE, image_reply(MEDIA))}

    fake = BatchFakeClient()
    fake.responses[fb.RPC_GEN_IMAGE] = answer
    out = await FlowSDK(fake).gen_image(
        prompt="a cat", project_id=PROJECT, variant_count=2,
    )
    # Two variants asked for, one rejected then retried: three calls, two images.
    assert len(fake.calls_for(fb.RPC_GEN_IMAGE)) == 3
    assert out["media_ids"]
    assert "partial_error" not in out


def _rpc_error_body() -> str:
    chunk = json.dumps([["wrb.fr", fb.RPC_GEN_IMAGE, None, None, None, [8]]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


@pytest.mark.asyncio
async def test_a_partial_wave_keeps_the_images_it_paid_for():
    """Failing the whole batch to report one bad variant throws away three good
    ones that have already been charged for."""
    state = {"n": 0}

    def answer(_match):
        state["n"] += 1
        if state["n"] == 1:
            # A content refusal: terminal, so no retry, and not `[8]`.
            chunk = json.dumps([
                ["wrb.fr", fb.RPC_GEN_IMAGE, None, None, None, ["PUBLIC_ERROR_FILTER"]]
            ])
            return {"data": f")]}}'\n{len(chunk)}\n{chunk}"}
        return {"data": envelope(fb.RPC_GEN_IMAGE, image_reply(MEDIA))}

    fake = BatchFakeClient()
    fake.responses[fb.RPC_GEN_IMAGE] = answer
    out = await FlowSDK(fake).gen_image(
        prompt="a cat", project_id=PROJECT, variant_count=2,
    )
    assert out["media_ids"] == [MEDIA]
    assert "1/2 variants failed" in out["partial_error"]


@pytest.mark.asyncio
async def test_an_image_reply_with_no_url_is_an_error_not_an_empty_success():
    """A 200 carrying nothing usable is most often a silent content-filter
    rejection. Reporting it as zero images made that look like a board with
    nothing to draw."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, [[None]])
    out = await FlowSDK(fake).gen_image(prompt="a cat", project_id=PROJECT)
    assert "image_failed" in out["error"]


@pytest.mark.asyncio
async def test_a_transport_failure_is_reported_as_one():
    """The extension never got to send it, which is a different thing from Flow
    refusing -- one is fixed in the browser and the other is not."""
    fake = BatchFakeClient()
    fake.transport_error(fb.RPC_GEN_IMAGE, "NO_AT_TOKEN")
    out = await FlowSDK(fake).gen_image(prompt="a cat", project_id=PROJECT)
    assert "NO_AT_TOKEN" in out["error"]


@pytest.mark.asyncio
async def test_edit_image_uses_the_base_image_slot_and_drops_a_duplicate_ref():
    """Wire type 2 for the source, 1 for references. Flow accepts the wrong
    slot, ignores it, and charges -- which looks exactly like success."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_IMAGE, image_reply(MEDIA))
    await FlowSDK(fake).edit_image(
        prompt="warmer", project_id=PROJECT,
        source_media_id="src", ref_media_ids=["ref-1", "src"],
    )
    inputs = fb_item(fake)[2]
    assert inputs[0] == ["src", None, None, None, fb.BASE_TYPE_IMAGE]
    assert inputs[1] == ["ref-1", None, None, None, fb.REF_TYPE_IMAGE]
    assert len(inputs) == 2, "the source must not also ride as a reference"


@pytest.mark.asyncio
async def test_upload_image_carries_a_captcha_which_the_rest_path_did_not():
    """Worth stating: syncing a dozen references across projects now mints a
    dozen single-use tokens, which is why the mapping cache is load-bearing."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_UPLOAD_IMAGE, [[MEDIA, PROJECT, "op", "CAE"]])
    out = await FlowSDK(fake).upload_image("Ym9keQ==", "image/png", PROJECT, "a.png")
    assert out["media_id"] == MEDIA
    assert fake.calls_for(fb.RPC_UPLOAD_IMAGE)[0]["captcha"] == fb.CAPTCHA_IMAGE


# ── video dispatch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gen_video_sends_one_rpc_per_source_and_keeps_their_order():
    """A four-variant upstream becomes four clips from one call, and the caller
    pairs slot i of the result with source i."""
    fake = BatchFakeClient()
    replies = iter([
        {"data": envelope(fb.RPC_GEN_VIDEO, operation_reply(f"op-{i}", PROJECT))}
        for i in range(3)
    ])
    fake.responses[fb.RPC_GEN_VIDEO] = lambda _m: next(replies)

    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT,
        start_media_ids=["a", "b", "c"],
        aspect_ratio=LANDSCAPE, video_quality="lite",
    )
    assert out["operation_names"] == ["op-0", "op-1", "op-2"]
    calls = fake.calls_for(fb.RPC_GEN_VIDEO)
    assert [c["captcha"] for c in calls] == [fb.CAPTCHA_VIDEO] * 3
    assert ["a" in c["freq"] for c in calls] == [True, False, False]


@pytest.mark.asyncio
async def test_gen_video_sends_the_lane_key_and_the_aspect_slot():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO, operation_reply("op-1", PROJECT))
    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT, start_media_id="a",
        aspect_ratio=PORTRAIT, video_quality="lite_relaxed",
    )
    assert out["model_key"] == "veo_3_1_i2v_lite_low_priority"
    request = fake.payload_for(fb.RPC_GEN_VIDEO)[0][0]
    assert request[1] == "veo_3_1_i2v_lite_low_priority"
    # Video aspect is 1=portrait / 2=landscape -- a DIFFERENT encoding from the
    # image call, which is why it goes through the resolver rather than a copy.
    assert request[2] == fb.resolve_video_aspect(PORTRAIT)


@pytest.mark.asyncio
async def test_gen_video_falls_back_to_the_single_source_field():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO, operation_reply("op-1", PROJECT))
    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT, start_media_id="only", video_quality="lite",
    )
    assert out["operation_names"] == ["op-1"]


@pytest.mark.asyncio
async def test_gen_video_with_no_source_at_all_is_refused():
    fake = BatchFakeClient()
    out = await FlowSDK(fake).gen_video(prompt="move", project_id=PROJECT)
    assert out["error"] == "missing_start_media_id"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_failure_midway_carries_out_the_operations_already_created():
    """The money rule. Each name is a render that is running and charged for, so
    dropping them on the error path lets the worker re-dispatch a
    partially-accepted batch and pay twice."""
    state = {"n": 0}

    def answer(_match):
        state["n"] += 1
        if state["n"] <= 2:
            return {"data": envelope(
                fb.RPC_GEN_VIDEO, operation_reply(f"op-{state['n']}", PROJECT)
            )}
        return {"error": "NO_INJECTION_RESULT"}

    fake = BatchFakeClient()
    fake.responses[fb.RPC_GEN_VIDEO] = answer
    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT,
        start_media_ids=["a", "b", "c"], video_quality="lite",
    )
    assert out["error"]
    assert out["operation_names"] == ["op-1", "op-2"]


@pytest.mark.asyncio
async def test_the_omni_lane_on_image_to_video_sends_omni_and_its_duration():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO, operation_reply("op-1", PROJECT))
    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT, start_media_id="a",
        video_quality="omni", duration_s=4,
    )
    assert out["model_key"] == "abra_i2v_4s"
    assert "abra_i2v_4s" in fake.calls_for(fb.RPC_GEN_VIDEO)[0]["freq"]


@pytest.mark.asyncio
async def test_the_substitution_channel_stays_wired_and_empty():
    """`jobs.ts` reads `model_substitutions` to warn about a lane swap. This
    build refuses instead of swapping, so the list is always empty -- asserted
    rather than assumed, because a swap arriving here silently is the failure."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO, operation_reply("op-1", PROJECT))
    out = await FlowSDK(fake).gen_video(
        prompt="move", project_id=PROJECT, start_media_id="a", video_quality="fast",
    )
    assert out["model_substitutions"] == []


# ── the three-signal poll ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_finished_clip_needs_all_three_signals():
    """The operation says it is done, the listing hands over the media id, and
    the media record grows a `/video/` url. Only then is it downloadable."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO, operation_reply("op-1", PROJECT))
    sdk = FlowSDK(fake)
    await sdk.gen_video(
        prompt="move", project_id=PROJECT, start_media_id="a", video_quality="lite",
    )

    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status="CAE"))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))

    round_one = await sdk.check_async(["op-1"])
    op = round_one["operations"][0]
    assert op["done"] is True
    assert op["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
    assert op["media_entries"] == [
        {"media_id": MEDIA, "url": f"https://{fb.MEDIA_HOST}/video/{MEDIA}?s=1",
         "mediaType": "video"}
    ]


@pytest.mark.asyncio
async def test_the_listing_is_always_asked_with_a_match_window():
    """That payload is past 17 MB and grows with every generation, so anything
    shipping it whole gets truncated and loses roughly half of all lookups."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status="CAE"))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    await sdk.check_async(["op-1"])

    listing = fake.calls_for(fb.RPC_PROJECT_MEDIA)
    assert listing, "the listing was never consulted"
    assert all(c["match"] == "op-1" for c in listing)


@pytest.mark.asyncio
async def test_a_poster_only_record_is_pending_not_done():
    """Flow serves the still before the clip exists. Downloading on the id alone
    saves a picture -- that was a real bug, not a hypothetical."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status="CAE"))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA, video=False))

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    op = (await sdk.check_async(["op-1"]))["operations"][0]
    assert op["done"] is False
    assert op["media_entries"] == []
    assert op["media_id"] == MEDIA, "found-but-rendering must be distinguishable"


@pytest.mark.asyncio
async def test_a_media_not_found_complaint_is_a_diagnosis_not_a_failure():
    """Measured: an operation can say exactly that and still deliver a finished
    eight-second clip. Reading it as terminal abandoned paid renders."""
    fake = BatchFakeClient()
    fake.reply(
        fb.RPC_OPERATION,
        operation_reply("op-1", PROJECT, complaint="Media not found."),
    )
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    op = (await sdk.check_async(["op-1"]))["operations"][0]
    assert op["done"] is True
    assert op["error"] is None


@pytest.mark.asyncio
async def test_the_complaint_rides_out_under_its_own_key_while_pending():
    """Not as `error`, which the worker treats as terminal."""
    fake = BatchFakeClient()
    fake.reply(
        fb.RPC_OPERATION,
        operation_reply("op-1", PROJECT, complaint="Media not found."),
    )
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", None)

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    op = (await sdk.check_async(["op-1"]))["operations"][0]
    assert op["done"] is False
    assert op["error"] is None
    assert op["complaint"] == "Media not found."


@pytest.mark.asyncio
async def test_the_expensive_listing_is_skipped_on_a_quiet_round():
    """It is the authority and the 17 MB call, so it is consulted when the
    operation reports something, when the poll is unreadable, or every third
    round regardless -- not on every round of a job that is plainly running."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status=None))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", None)

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    await sdk.check_async(["op-1"])
    await sdk.check_async(["op-1"])
    assert fake.calls_for(fb.RPC_PROJECT_MEDIA) == []
    await sdk.check_async(["op-1"])
    assert len(fake.calls_for(fb.RPC_PROJECT_MEDIA)) == 1


@pytest.mark.asyncio
async def test_an_unreadable_operation_poll_sends_us_to_the_listing():
    """An operation that decayed to a bare id still shows up there, so a failed
    poll is a reason to look rather than to stop."""
    fake = BatchFakeClient()
    fake.transport_error(fb.RPC_OPERATION, "NO_INJECTION_RESULT")
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    op = (await sdk.check_async(["op-1"]))["operations"][0]
    assert op["done"] is True


@pytest.mark.asyncio
async def test_with_no_project_known_the_operation_poll_is_the_fallback():
    """Nothing to look the media up in, so the round reports pending with the
    reason rather than raising."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", None, status="CAE"))

    op = (await FlowSDK(fake).check_async(["op-1"]))["operations"][0]
    assert op["done"] is False
    assert "project id" in (op.get("complaint") or "")
    assert fake.calls_for(fb.RPC_PROJECT_MEDIA) == []


@pytest.mark.asyncio
async def test_the_media_id_is_remembered_so_the_listing_is_asked_once():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status="CAE"))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA, video=False))

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    await sdk.check_async(["op-1"])
    await sdk.check_async(["op-1"])
    assert len(fake.calls_for(fb.RPC_PROJECT_MEDIA)) == 1
    assert len(fake.calls_for(fb.RPC_MEDIA)) == 2


@pytest.mark.asyncio
async def test_the_results_come_back_in_the_order_asked_for():
    """Slot i of the answer pairs with source i on the caller's side."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-b", PROJECT))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-b", None)

    out = await FlowSDK(fake).check_async(["op-a", "op-b", "op-c"])
    assert [o["name"] for o in out["operations"]] == ["op-a", "op-b", "op-c"]


@pytest.mark.asyncio
async def test_a_workflow_is_polled_through_the_media_record_not_the_operation():
    """Text-to-video submits never had operation handles."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))

    out = await FlowSDK(fake).check_async(
        ["wf-1"], workflows=[{"name": "wf-1", "primary_media_id": MEDIA}]
    )
    op = out["operations"][0]
    assert op["done"] is True
    assert op["media_entries"][0]["media_id"] == MEDIA
    assert fake.calls_for(fb.RPC_OPERATION) == []


@pytest.mark.asyncio
async def test_a_workflow_with_only_a_poster_is_still_pending():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA, video=False))
    out = await FlowSDK(fake).check_async(
        ["wf-1"], workflows=[{"name": "wf-1", "primary_media_id": MEDIA}]
    )
    assert out["operations"][0]["done"] is False


@pytest.mark.asyncio
async def test_operations_and_workflows_in_one_round_route_separately():
    """A board can hold both, and the caller cannot tell them apart."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_OPERATION, operation_reply("op-1", PROJECT, status="CAE"))
    fake.responses[fb.RPC_PROJECT_MEDIA] = listing_window("op-1", MEDIA)

    def media(_match):
        return {"data": envelope(fb.RPC_MEDIA, media_reply(MEDIA))}

    fake.responses[fb.RPC_MEDIA] = media

    sdk = FlowSDK(fake)
    sdk._remember_operation("op-1", PROJECT)
    out = await sdk.check_async(
        ["op-1", "wf-1"],
        workflows=[{"name": "wf-1", "primary_media_id": OTHER_MEDIA}],
    )
    assert [o["name"] for o in out["operations"]] == ["op-1", "wf-1"]
    assert all(o["done"] for o in out["operations"])


@pytest.mark.asyncio
async def test_one_bad_operation_does_not_fail_the_whole_round():
    """Every other clip in the batch is still rendering and still paid for."""
    def operation(_match):
        raise RuntimeError("boom")

    fake = BatchFakeClient()
    fake.responses[fb.RPC_OPERATION] = operation
    out = await FlowSDK(fake).check_async(["op-1"])
    assert out["operations"][0]["done"] is False


# ── media urls ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_download_url_prefers_the_clip_over_the_poster():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_MEDIA, media_reply(MEDIA))
    url = await FlowSDK(fake).media_download_url(MEDIA)
    assert "/video/" in url


@pytest.mark.asyncio
async def test_media_download_url_is_none_when_nothing_is_ready():
    fake = BatchFakeClient()
    fake.reply(fb.RPC_MEDIA, [])
    assert await FlowSDK(fake).media_download_url(MEDIA) is None
    assert await FlowSDK(fake).media_download_url("") is None


# ── the worker's half of the money rule ───────────────────────────────


@pytest.mark.asyncio
async def test_worker_refuses_to_retry_a_partially_accepted_batch(client):
    """A result naming operations is a render already charged for. The retry
    gate reads the names out of the result, which is why the SDK carries them
    out on the error path."""
    from flowboard.worker.processor import WorkerController

    w = WorkerController()
    row = client.post("/api/requests", json={
        "type": "gen_video",
        "params": {"prompt": "x", "project_id": PROJECT},
    }).json()

    from flowboard.db import get_session
    from flowboard.db.models import Request

    with get_session() as s:
        req = s.get(Request, row["id"])
        req.result = {"operation_names": ["op-1", "op-2"]}
        s.add(req)
        s.commit()
        s.refresh(req)

        # `NO_INJECTION_RESULT` is in the "free" bucket -- normally retried
        # without burning the attempt budget, because it is a browser-side
        # hiccup and costs nothing. Naming operations overrides that: the
        # renders are running and charged for.
        from flowboard.worker.processor import classify_error

        assert classify_error("NO_INJECTION_RESULT", request_type=req.type) == "free"
        w._apply_failure(req, "NO_INJECTION_RESULT", req.result)
        # Read the object, not a refreshed row: `_apply_failure` mutates and the
        # caller commits, so refreshing here would reload the pre-call status
        # and the assertion would pass for the wrong reason.
        assert req.status == "failed", "a charged batch must not be re-dispatched"
        assert req.free_retries == 0


# ── what an error looks like by the time a person reads it ────────────


LIVE_DENIED = [
    7, None,
    [["type.googleapis.com/google.rpc.ErrorInfo", ["PUBLIC_ERROR_MODEL_ACCESS_DENIED"]]],
]


def test_flows_own_code_leads_the_error_string():
    """Captured live on a Pro account 19/09/2026 by asking for the free lane.

    Before this, the string was `RpcError: eb1hJf failed: [7, None,
    [['type.googleapis.com/google.rpc.ErrorInfo', [...]]]]` — the same
    information, unreadable, and it is the error a Pro user hits most often
    because the UI marks that lane free and invites them to pick it.
    """
    text = flow_sdk._error_text(fb.RpcError("eb1hJf", LIVE_DENIED))
    assert text.startswith("PUBLIC_ERROR_MODEL_ACCESS_DENIED")
    # The rpcid survives for a bug report, but after the part a person reads.
    assert "eb1hJf" in text
    assert "googleapis.com" not in text


def test_the_string_a_person_reads_is_the_string_the_worker_classifies():
    """One string, not two. The retry policy and the message used to be able to
    disagree, because the tests for each wrote their own example."""
    from flowboard.worker.processor import classify_error

    denied = flow_sdk._error_text(fb.RpcError("eb1hJf", LIVE_DENIED))
    assert classify_error(denied) == "terminal"

    replayed = flow_sdk._error_text(
        fb.RpcError("ogiZ0b", [7, None, [["x", ["PUBLIC_ERROR_UNUSUAL_ACTIVITY"]]]])
    )
    # The trap: `PUBLIC_ERROR_` is a self-terminal prefix, so leading with the
    # clean code would have made a re-mintable captcha failure terminal.
    assert classify_error(replayed) == "captcha"

    transient = flow_sdk._error_text(fb.RpcError("ogiZ0b", [8]))
    assert classify_error(transient) == "counted"


def test_an_error_with_no_flow_code_still_names_the_layer_that_refused():
    """No guessing when the detail carries no reason: the exception type at
    least says whether the bridge or Flow said no."""
    text = flow_sdk._error_text(fb.RpcError("as29s", ["something unmapped"]))
    assert text.startswith("RpcError")
    assert "as29s" in text


@pytest.mark.asyncio
async def test_a_partial_wave_keeps_flows_code_inside_the_truncation():
    """The sentence under a partly-failed image node has to survive its own cut.

    The image paths built error strings from `str(exc)`, so a `[7]` arrived as a
    repr of a nested protobuf — `ogiZ0b failed: [7, None, [['type.googleapis.com/
    google.rpc.ErrorInfo', ['PUBLIC_...` — and the 80-character slice in
    `partial_error` landed in the middle of the code. The one actionable token was
    cut off, and `errorLabel` had nothing to translate.

    The video paths already led with the code through `_error_text`; the
    most-used node type in the app did not.
    """
    state = {"n": 0}

    def answer(_match):
        state["n"] += 1
        if state["n"] == 1:
            # The live `[7]` shape: three slots, with the code nested inside.
            chunk = json.dumps([[
                "wrb.fr", fb.RPC_GEN_IMAGE, None, None, None,
                [7, None, [["type.googleapis.com/google.rpc.ErrorInfo",
                            ["PUBLIC_ERROR_MODEL_ACCESS_DENIED"]]]],
            ]])
            return {"data": f")]}}'\n{len(chunk)}\n{chunk}"}
        return {"data": envelope(fb.RPC_GEN_IMAGE, image_reply(MEDIA))}

    fake = BatchFakeClient()
    fake.responses[fb.RPC_GEN_IMAGE] = answer
    out = await FlowSDK(fake).gen_image(
        prompt="a cat", project_id=PROJECT, variant_count=2,
    )
    assert out["media_ids"] == [MEDIA]
    assert "PUBLIC_ERROR_MODEL_ACCESS_DENIED" in out["partial_error"]
