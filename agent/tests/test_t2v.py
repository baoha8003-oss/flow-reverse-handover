"""Text-to-video: the Veo lanes, and Omni beside them.

This module has been wrong twice, in opposite directions, and both errors are
worth keeping in view because they were each reasonable at the time.

**First** it pinned a reconstructed tier x quality x aspect x duration slotting
over the packaged tool's `veo_3_1_t2v_*` constants, plus a substitution chain
that quietly degraded an unavailable "Lower Priority" lane down to lite.

**Then** the September 2026 transport migration deleted those tables, and this
module was rewritten to pin the opposite: Veo text-to-video refused outright,
Omni the only lane, on the belief that nobody had captured a Veo payload for
`YhhmEf`. Refusing was the right call given that belief — serving Omni instead
bills 15-30 credits to a board that asked for a lane the packaged tool lists as
free.

**The belief was never tested.** Measured live on a Pro account 19/09/2026:
`YhhmEf` answers `PUBLIC_ERROR_MODEL_ACCESS_DENIED` for
`veo_3_1_t2v_lite_low_priority` — a PLAN error, meaning the RPC parsed the Veo
key and objected to the entitlement. The payload was never missing.

So the Veo lanes dispatch again, from a table re-mined out of the exe. What
carries over unchanged from every version of this file is the money rule: the
lane a board asks for is the lane that is sent, and a lane with no key is
refused rather than swapped for a pricier one.
"""
from __future__ import annotations

import pytest

from flowboard.services import flow_batch as fb
from flowboard.services import flow_sdk
from tests.flow_fakes import BatchFakeClient, envelope

LANDSCAPE = "VIDEO_ASPECT_RATIO_LANDSCAPE"
PORTRAIT = "VIDEO_ASPECT_RATIO_PORTRAIT"
PROJECT = "11111111-1111-1111-1111-111111111111"


def _submit_reply(media_id: str, workflow_id: str = "wf-1"):
    """A `YhhmEf` answer: the submit returns a media/workflow record."""
    return [None, None, None, [[media_id, PROJECT, workflow_id, "PENDING"]]]


# ── the lane ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("lane", ["lite", "lite_relaxed", "fast"])
@pytest.mark.asyncio
async def test_every_veo_lane_sends_its_own_key(lane):
    """The board's choice reaches the wire. Previously all four were refused."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1"))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="a cat surfing", project_id=PROJECT,
        aspect_ratio=LANDSCAPE, video_quality=lane, duration_s=8,
    )
    expected = flow_sdk.VEO_T2V_LANES[lane][8]
    assert out["model_key"] == expected
    assert expected in fake.calls_for(fb.RPC_GEN_VIDEO_TEXT)[0]["freq"]


@pytest.mark.asyncio
async def test_a_lane_with_no_text_to_video_key_is_still_refused():
    """`quality` has no `veo_3_1_t2v_*` name in the exe at all. It is the one
    label here that is a wall rather than a lane, and the refusal names the
    alternatives instead of silently picking one."""
    fake = BatchFakeClient()
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="x", project_id=PROJECT, video_quality="quality", duration_s=8,
    )
    assert out["error"].startswith(flow_sdk.UNSUPPORTED_PREFIX + "t2v_lane_quality")
    assert "OMNI" in out["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_an_unset_lane_does_not_reach_omni():
    """Omni is the priciest option here and nobody chose it. An unset lane takes
    the same cheap Veo default the rest of the app uses."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1"))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="x", project_id=PROJECT, duration_s=8,
    )
    assert out["model_key"] == flow_sdk.VEO_T2V_LANES[flow_sdk.DEFAULT_VIDEO_QUALITY][8]
    assert not out["model_key"].startswith("abra_")


# ── the Omni lane, which does work ────────────────────────────────────


@pytest.mark.parametrize("duration", [4, 6, 8, 10])
@pytest.mark.asyncio
async def test_omni_sends_the_key_for_the_length_asked_for(duration):
    """The duration IS the key here, so this is the assertion that stops a 4s
    request from being billed as 10."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("media-1"))
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video_text(
        prompt="a cat surfing", project_id=PROJECT,
        aspect_ratio=PORTRAIT, video_quality="omni", duration_s=duration,
    )
    assert out["model_key"] == f"abra_t2v_{duration}s"
    assert f"abra_t2v_{duration}s" in fake.calls_for(fb.RPC_GEN_VIDEO_TEXT)[0]["freq"]


@pytest.mark.asyncio
async def test_a_duration_omni_does_not_have_is_refused_not_rounded():
    fake = BatchFakeClient()
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="x", project_id=PROJECT, video_quality="omni", duration_s=5,
    )
    assert "unsupported" in out["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_the_submit_returns_workflows_not_operations():
    """This RPC answers with a media/workflow record rather than an operation
    handle, so it is polled through `as29s` instead of the operation RPC.
    `check_async` takes both and the caller cannot tell — which is the whole
    point of returning them under one key as well."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("media-1", "wf-7"))
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video_text(
        prompt="x", project_id=PROJECT, video_quality="omni", duration_s=8,
    )
    assert out["workflows"] == [{"name": "wf-7", "primary_media_id": "media-1"}]
    assert out["operation_names"] == ["wf-7"]


@pytest.mark.asyncio
async def test_several_variants_are_several_rpcs_and_a_failure_keeps_the_rest():
    """Each variant is its own submit, each already charged for. Failing the
    whole batch to report the last one would throw away two paid renders."""
    fake = BatchFakeClient()
    replies = [
        {"data": envelope(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1", "wf-1"))},
        {"data": envelope(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-2", "wf-2"))},
        {"data": ""},  # the third submit comes back unreadable
    ]
    calls = {"n": 0}

    def answer(_match):
        index = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return replies[index]

    fake.responses[fb.RPC_GEN_VIDEO_TEXT] = answer
    sdk = flow_sdk.FlowSDK(client=fake)

    out = await sdk.gen_video_text(
        prompt="x", project_id=PROJECT, video_quality="omni",
        duration_s=4, variant_count=3,
    )
    assert out["operation_names"] == ["wf-1", "wf-2"]
    assert len(fake.calls_for(fb.RPC_GEN_VIDEO_TEXT)) == 3


@pytest.mark.asyncio
async def test_a_first_variant_failing_is_an_error_not_a_partial():
    """Nothing was accepted, so there is nothing to keep and the caller should
    see the reason rather than an empty success."""
    fake = BatchFakeClient()
    fake.fail(fb.RPC_GEN_VIDEO_TEXT, [8])
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="x", project_id=PROJECT, video_quality="omni", duration_s=4,
    )
    # `[8]` is Flow's transient generation rejection, marked so the worker
    # retries it instead of treating it as a content refusal.
    assert out["error"].startswith(flow_sdk.TRANSIENT_RPC_CODE)


def test_the_transient_marker_is_retryable_and_nothing_else_is():
    from flowboard.worker.processor import classify_error

    assert classify_error(f"{flow_sdk.TRANSIENT_RPC_CODE}: YhhmEf failed: [8]") == "counted"
    assert classify_error("RpcError: YhhmEf failed: ['PUBLIC_ERROR_X']") == "terminal"


# ── the handler around it ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_rejects_non_int_duration_instead_of_defaulting_to_8s():
    """Duration picks the model key, so coercing a malformed value to 8s
    dispatches a longer, pricier clip than the caller asked for."""
    from flowboard.worker import processor as proc

    _, err = await proc._handle_gen_video_text({
        "prompt": "x", "project_id": PROJECT,
        "paygate_tier": "PAYGATE_TIER_ONE", "duration_s": "8",
    })
    assert err is not None and err.startswith("invalid_duration_")


@pytest.mark.asyncio
async def test_handler_rejects_missing_prompt():
    from flowboard.worker import processor as proc

    _, err = await proc._handle_gen_video_text(
        {"project_id": PROJECT, "paygate_tier": "PAYGATE_TIER_ONE"}
    )
    assert err == "missing_prompt"


@pytest.mark.asyncio
async def test_handler_dispatches_and_polls(client, monkeypatch):
    """The t2v handler builds a request with no source image and polls to
    completion through the shared poller."""
    import asyncio

    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController

    monkeypatch.setattr(proc, "VIDEO_POLL_INTERVAL_S", 0.01)
    captured: dict = {}

    class FakeSDK:
        async def gen_video_text(self, **kw):
            captured.update(kw)
            return {
                "raw": {},
                "operation_names": ["wf-1"],
                "workflows": [{"name": "wf-1", "primary_media_id": "media-1"}],
                "model_key": "abra_t2v_8s",
            }

        async def check_async(self, op_names, workflows=None):
            assert workflows, "a workflow submit must be polled as a workflow"
            return {
                "operations": [{
                    "name": "wf-1",
                    "done": True,
                    "media_entries": [{"media_id": "vid_abc", "url": "http://x/v"}],
                }]
            }

    monkeypatch.setattr(proc, "get_flow_sdk", lambda: FakeSDK())
    monkeypatch.setattr(proc.media_service, "ingest_urls", lambda e: None)

    row = client.post("/api/requests", json={
        "type": "gen_video_text",
        "params": {
            "prompt": "a cat surfing",
            "project_id": PROJECT,
            "aspect_ratio": PORTRAIT,
            "video_quality": "omni",
            "duration_s": 8,
        },
    }).json()

    w = WorkerController(handlers={"gen_video_text": proc._handle_gen_video_text})
    task = asyncio.create_task(w.start())
    try:
        w.enqueue(row["id"])
        for _ in range(200):
            await asyncio.sleep(0.02)
            cur = client.get(f"/api/requests/{row['id']}").json()
            if cur["status"] not in ("queued", "running"):
                break
        assert cur["status"] == "done", cur
        assert cur["result"]["media_ids"] == ["vid_abc"]
        # The key that dispatched reaches the persisted row, because the review
        # loop reads it there to decide whether another round is free.
        assert cur["result"]["model_key"] == "abra_t2v_8s"
        assert "start_media_id" not in captured
        assert captured["duration_s"] == 8
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_no_substitution_is_ever_reported_because_none_is_made(client, monkeypatch):
    """The channel the UI reads stays wired and stays empty.

    This replaces a test that asserted a lane swap survived dispatch, poll and
    the persisted row. The swap itself is gone; what must not come back is a
    swap arriving silently, so the emptiness is asserted end to end rather than
    assumed from the SDK.
    """
    import asyncio

    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController

    monkeypatch.setattr(proc, "VIDEO_POLL_INTERVAL_S", 0.01)

    class FakeSDK:
        async def gen_video_text(self, **kw):
            return {
                "raw": {},
                "operation_names": ["wf-1"],
                "workflows": [{"name": "wf-1", "primary_media_id": "m-1"}],
                "model_key": "abra_t2v_4s",
                "model_substitutions": [],
            }

        async def check_async(self, op_names, workflows=None):
            return {
                "operations": [{
                    "name": "wf-1", "done": True,
                    "media_entries": [{"media_id": "v1", "url": "http://x/v"}],
                }]
            }

    monkeypatch.setattr(proc, "get_flow_sdk", lambda: FakeSDK())
    monkeypatch.setattr(proc.media_service, "ingest_urls", lambda e: None)

    row = client.post("/api/requests", json={
        "type": "gen_video_text",
        "params": {
            "prompt": "x", "project_id": PROJECT,
            "aspect_ratio": LANDSCAPE, "video_quality": "omni", "duration_s": 4,
        },
    }).json()

    w = WorkerController(handlers={"gen_video_text": proc._handle_gen_video_text})
    task = asyncio.create_task(w.start())
    try:
        w.enqueue(row["id"])
        for _ in range(200):
            await asyncio.sleep(0.02)
            cur = client.get(f"/api/requests/{row['id']}").json()
            if cur["status"] not in ("queued", "running"):
                break
        assert cur["status"] == "done", cur
        assert not cur["result"].get("model_substitutions")
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


# ── the Veo lanes, restored ───────────────────────────────────────────


class TestVeoTextToVideo:
    """The refusal came back off the wall.

    Measured live on a Pro account 19/09/2026: `YhhmEf` answers
    `PUBLIC_ERROR_MODEL_ACCESS_DENIED` for `veo_3_1_t2v_lite_low_priority`. That
    is a PLAN error, so the RPC parses Veo text-to-video keys — the payload was
    never missing, this account simply lacks that key. The sixteen names come
    from an mmap scan of the packaged exe, because the table this rebuilds was
    deleted with the REST transport and survives in neither git nor the tree.
    """

    def test_every_key_sent_is_one_the_binary_actually_contains(self):
        """The guard against a plausible-looking invented name.

        Not a copy of the scan — a shape check. Every name follows the exe's own
        spelling: the family, the optional duration, the queue suffix. A name
        assembled any other way is a guess wearing the table's authority.
        """
        import re

        pattern = re.compile(
            r"^veo_3_1_t2v_(lite|fast)(_[46]s)?"
            r"(_low_priority|_relaxed|_ultra_relaxed)?$"
        )
        for lane, keys in flow_sdk.VEO_T2V_LANES.items():
            for duration, key in keys.items():
                assert pattern.match(key), f"{lane}/{duration}: {key}"
                if duration != 8:
                    assert f"_{duration}s" in key, f"{lane}/{duration}: {key}"

    def test_no_portrait_name_is_ever_sent(self):
        """Aspect became its own payload slot, and the accepted image-to-video
        set dropped its portrait names the same way. Sending both would be one
        fact in two places, which is how they come to disagree."""
        for keys in flow_sdk.VEO_T2V_LANES.values():
            assert not any("portrait" in k for k in keys.values())

    def test_the_free_queues_are_reachable_and_marked(self):
        """`lite_relaxed` is denied on Pro — measured — but kept, because a
        different plan may hold it and refusing locally would hide that."""
        plan = flow_sdk.resolve_t2v_plan("lite_relaxed", 8)
        assert plan["model_key"] == "veo_3_1_t2v_lite_low_priority"
        assert plan["error"] is None

    def test_a_length_veo_does_not_have_is_refused_not_rounded(self):
        """Omni has 10s and Veo does not. The duration is part of the key, so
        the nearest one bills a different clip than the board asked for."""
        plan = flow_sdk.resolve_t2v_plan("lite", 10)
        assert plan["model_key"] is None
        assert "invalid_duration_10" in plan["error"]

    def test_a_lane_with_no_t2v_key_at_all_is_refused(self):
        plan = flow_sdk.resolve_t2v_plan("quality", 8)
        assert plan["model_key"] is None
        assert "quality" in plan["error"]

    @pytest.mark.asyncio
    async def test_a_veo_lane_now_dispatches_the_key_it_asked_for(self):
        """The behaviour change, asserted on the envelope: the lane the board
        chose is the key that goes out. Before this it was refused, and the
        alternatives were nothing or Omni at 15-30 credits."""
        fake = BatchFakeClient()
        fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1", "wf-1"))
        out = await flow_sdk.FlowSDK(fake).gen_video_text(
            prompt="x", project_id=PROJECT, video_quality="lite", duration_s=6,
        )
        assert out["model_key"] == "veo_3_1_t2v_lite_6s"
        assert "veo_3_1_t2v_lite_6s" in fake.calls_for(fb.RPC_GEN_VIDEO_TEXT)[0]["freq"]

    @pytest.mark.asyncio
    async def test_the_omni_lane_is_untouched_by_the_veo_table(self):
        fake = BatchFakeClient()
        fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1", "wf-1"))
        out = await flow_sdk.FlowSDK(fake).gen_video_text(
            prompt="x", project_id=PROJECT, video_quality="omni", duration_s=10,
        )
        # 10s exists for Omni and not for Veo, so this also pins that the two
        # duration tables did not get merged.
        assert out["model_key"] == "abra_t2v_10s"

    @pytest.mark.asyncio
    async def test_an_unset_lane_takes_the_cheapest_paid_veo_lane(self):
        """Not Omni: Omni is the priciest option here and nobody chose it. Not
        the free queue either — that one is plan-gated and would refuse."""
        fake = BatchFakeClient()
        fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1", "wf-1"))
        out = await flow_sdk.FlowSDK(fake).gen_video_text(
            prompt="x", project_id=PROJECT, duration_s=8,
        )
        assert out["model_key"] == flow_sdk.VEO_T2V_LANES[
            flow_sdk.DEFAULT_VIDEO_QUALITY
        ][8]

    @pytest.mark.asyncio
    async def test_a_refused_lane_sends_nothing(self):
        fake = BatchFakeClient()
        out = await flow_sdk.FlowSDK(fake).gen_video_text(
            prompt="x", project_id=PROJECT, video_quality="quality", duration_s=8,
        )
        assert out["error"]
        assert fake.calls == []


# ── the variant ceiling ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_text_to_video_node_cannot_submit_more_clips_than_the_ceiling():
    """`_handle_gen_video_text` was the one dispatch reading `variant_count` with
    only a floor.

    `gen_image` and `gen_image_openai` both clamp through `clamp_variant_count`;
    this one did `if not isinstance(...) or <= 0: variant_count = 1` and accepted
    anything above. So a node carrying 50 submitted fifty clips — 50 × 15-30
    credits on the Omni lane — while the cost dialog's own ceiling is 16, and the
    estimate quoted one job.
    """
    from flowboard.worker.processor import MAX_VARIANTS, _handle_gen_video_text

    submits: list[int] = []

    class _Spy:
        async def gen_video_text(self, **kwargs):
            submits.append(kwargs.get("variant_count"))
            return {"raw": {}, "operation_names": ["op-1"], "model_key": "abra_t2v_8s"}

    import flowboard.worker.processor as proc

    async def _no_poll(sdk, dispatch, request_id):
        return {"media_ids": []}, None

    original = proc.get_flow_sdk
    original_poll = proc._poll_video_dispatch
    proc.get_flow_sdk = lambda: _Spy()
    proc._poll_video_dispatch = _no_poll
    try:
        await _handle_gen_video_text({
            "prompt": "một con mèo",
            "project_id": "c1f5a517-cb11-43a8-8211-a0348412964b",
            "video_quality": "omni",
            "duration_s": 8,
            "variant_count": 50,
        })
    finally:
        proc.get_flow_sdk = original
        proc._poll_video_dispatch = original_poll

    assert submits == [MAX_VARIANTS]


@pytest.mark.asyncio
async def test_the_omni_lane_honours_the_resolution_it_was_given():
    """360p was sent, ignored at two layers, and rendered at 720p.

    The frontend supplies `resolution` for a t2v OMNI dispatch (`omniExtras`),
    `_handle_gen_video_text` never read it, and `gen_video_text` left
    `omni_model_key`'s third positional at its `720p` default. Meanwhile
    `SettingsTab` told the user "chỉ áp dụng cho làn OMNI" — a setting that reads
    as applied and does nothing. `omni_model_key` has built the `_360p` variant all
    along; only the wiring was missing.
    """
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1"))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="a cat", project_id=PROJECT, aspect_ratio=LANDSCAPE,
        video_quality="omni", duration_s=8, resolution="360p",
    )
    assert out["model_key"] == "abra_t2v_8s_360p"
    assert "abra_t2v_8s_360p" in fake.calls_for(fb.RPC_GEN_VIDEO_TEXT)[0]["freq"]


@pytest.mark.asyncio
async def test_an_unset_resolution_still_means_720p():
    """The default has to stay put: 360p is the cheaper render and nobody has
    measured its price, so it must be a choice rather than a fallback."""
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_TEXT, _submit_reply("m-1"))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_text(
        prompt="a cat", project_id=PROJECT, aspect_ratio=LANDSCAPE,
        video_quality="omni", duration_s=8,
    )
    assert out["model_key"] == "abra_t2v_8s"


@pytest.mark.asyncio
async def test_the_worker_passes_the_resolution_through():
    """The handler half — the SDK parameter is useless if nothing fills it."""
    from flowboard.worker import processor as proc

    seen: dict = {}

    class _Spy:
        async def gen_video_text(self, **kw):
            seen.update(kw)
            return {"raw": {}, "operation_names": ["op-1"], "model_key": "x"}

    async def _no_poll(sdk, dispatch, request_id):
        return {"media_ids": []}, None

    orig_sdk, orig_poll = proc.get_flow_sdk, proc._poll_video_dispatch
    proc.get_flow_sdk = lambda: _Spy()
    proc._poll_video_dispatch = _no_poll
    try:
        await proc._handle_gen_video_text({
            "prompt": "a cat", "project_id": PROJECT,
            "video_quality": "omni", "duration_s": 8, "resolution": "360p",
        })
    finally:
        proc.get_flow_sdk, proc._poll_video_dispatch = orig_sdk, orig_poll

    assert seen.get("resolution") == "360p"
