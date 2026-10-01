"""Upscale and extend, from the worker's side: what refuses, and what it keeps.

Both of these act on a clip the user ALREADY PAID FOR, which makes their failure
modes different from a generation's. A generation that goes wrong costs the
generation. These can cost the original:

* upscale must never land on `mediaId`. It lands on its own key, and the
  frontend reads that key — the original is the thing that was paid for, and a
  "make this better" button that replaces it is a loss with no undo;
* extend must reference the scene's CLONE on link 1 and the PREVIOUS
  EXTENSION'S OPERATION id afterwards. The capture is explicit that using a
  media id for link 2+ is accepted and then fails NOT_FOUND — billed, no clip;
* a second scene for the same clip restarts the chain at position 1, so the
  scene is created once and handed back to be remembered.

The SDK is faked at the batch-client level, so every assertion about what was
sent reads the real envelope through the real builder.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import flow_batch as fb
from flowboard.services import flow_credits
from flowboard.services.flow_sdk import FlowSDK
from flowboard.worker import processor
from tests.flow_fakes import BatchFakeClient, envelope

PROJECT = "8c0c7ec4-ba4b-4a1f-9f0e-2d4f5a6b7c8d"

#: A Veo clip. Stated rather than omitted, because an extend now REFUSES a clip
#: whose provenance is unrecorded: a blank key does not start with `abra_`, so
#: silence used to read as "not Omni" and buy a submit Flow accepts and then
#: fails. Tests about extend behaviour therefore have to say what the clip is,
#: exactly like a real caller.
VEO_KEY = "veo_3_1_t2v_lite"


def _canned(rpcid: str, payload) -> dict:
    return {"data": envelope(rpcid, payload), "status": 200}


def _sent(fake: BatchFakeClient, rpcid: str):
    call = next(c for c in fake.calls if c["rpcid"] == rpcid)
    return json.loads(json.loads(call["freq"])[0][0][1])


#: A `p0UkFb` reply whose one record names the upscaled operation.
UPSCALE_OK = _canned("p0UkFb", [[[["op-1_upsampled"], "", None, None, 1]]])
#: An `fZytfe` reply with the record at slot 3, the shape `eb1hJf` uses.
EXTEND_OK = _canned(
    "fZytfe", [None, 988, [["m"]], [["op-ext-1", PROJECT, "m", "S"]]]
)
#: `rqZuUc`: a scene plus the clone extend must start from.
SCENE_OK = _canned("rqZuUc", [
    ["scene-1", "Scene 1", None, 0, 0, 2, None, []],
    [[["wf-1", None, None, ["t", 0, None, None, "clone-1", "x"], PROJECT],
      "scene-1", []]],
])


class _StubSdk:
    """Records what the handler asked for, without polling anything.

    The handlers hand their dispatch to `_poll_video_dispatch`, which sleeps
    between rounds. These tests are about the decision BEFORE that, so the
    dispatch is intercepted with an error return — the handler's own
    bookkeeping (the scene it made, the source it chose) still has to come out,
    which is exactly the property worth pinning.
    """

    def __init__(self, *, scene=None, dispatch_error="stop_here"):
        self.scene = scene or {"scene_id": "scene-1", "clone_media_id": "clone-1"}
        self.dispatch_error = dispatch_error
        self.scene_calls: list[tuple] = []
        self.extend_calls: list[dict] = []
        self.upscale_calls: list[tuple] = []
        self.credit_reads = 0

    async def get_credits(self) -> dict:
        """Reads the balance AND moves the meter, the way the real one does.

        `FlowSDK.get_credits` calls `flow_credits.remember`, and the price probe
        depends on that: the baseline it returns has to be the same number the
        meter holds, or the delta at the end has nothing to subtract from.
        """
        self.credit_reads += 1
        flow_credits.remember(989)
        return {"credits": 989, "raw_present": True}

    async def create_scene(self, project_id, media_ids, aspect=None):
        self.scene_calls.append((project_id, list(media_ids), aspect))
        return dict(self.scene)

    async def extend_video(self, prompt, project_id, scene_id, source_media_id,
                           **kw):
        self.extend_calls.append({
            "prompt": prompt, "project_id": project_id, "scene_id": scene_id,
            "source": source_media_id, **kw,
        })
        return {"raw": None, "error": self.dispatch_error, "operation_names": []}

    async def upscale_video(self, operation_id, media_id, project_id, **kw):
        self.upscale_calls.append((operation_id, media_id, project_id, kw))
        return {"raw": None, "error": self.dispatch_error, "operation_names": []}


def _poll_then_charge(amount: int):
    """A stubbed poll that lands a clip and moves the meter, the way the real one
    does: `jwpduf` carries the balance in slot 1 on every round."""

    async def _poll(_sdk, dispatch, _rid):
        known, _age = flow_credits.last_known()
        if known is not None:
            flow_credits.remember(known - amount)
        return (
            {"media_ids": ["e-1"], "operation_names": dispatch["operation_names"] or ["o-1"]},
            None,
        )

    return _poll


@pytest.fixture
def stub(monkeypatch):
    s = _StubSdk()
    monkeypatch.setattr(processor, "get_flow_sdk", lambda: s)
    return s


# ── the SDK layer: upscale ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upscale_returns_the_shape_the_poll_already_understands():
    """`operation_names` is the contract `_poll_video_dispatch` reads. Returning
    anything else would need a second poll loop for a clip Flow renders exactly
    like every other one."""
    fake = BatchFakeClient({"p0UkFb": UPSCALE_OK})
    out = await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    assert out["operation_names"] == ["op-1_upsampled"]
    assert out["model_key"] == fb.UPSCALE_MODEL_1080P
    assert not out.get("error")


@pytest.mark.asyncio
async def test_upscale_mints_no_captcha():
    """The payload has no slot for one, so minting a token would burn a
    single-use credential for nothing — and a replayed token is what Flow calls
    unusual activity."""
    fake = BatchFakeClient({"p0UkFb": UPSCALE_OK})
    await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    assert fake.calls[0]["captcha"] is None


@pytest.mark.asyncio
async def test_upscale_sends_the_operation_and_the_media_to_different_slots():
    """Slot 0 the operation, slot 4 the media. Swapping them is a request Flow
    accepts and then fails NOT_FOUND, which from outside looks like success."""
    fake = BatchFakeClient({"p0UkFb": UPSCALE_OK})
    await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    obj = _sent(fake, "p0UkFb")[0][0]
    assert obj[0] == [None, "op-1"]
    assert obj[4][1] == "media-1"


@pytest.mark.asyncio
async def test_upscale_without_an_operation_id_never_dispatches():
    """Deriving one from the media id is the swap above, with extra steps."""
    fake = BatchFakeClient({})
    out = await FlowSDK(fake).upscale_video("", "media-1", PROJECT)
    assert out["error"] == "missing_upscale_operation_id"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_upscale_without_a_media_id_never_dispatches():
    fake = BatchFakeClient({})
    out = await FlowSDK(fake).upscale_video("op-1", "", PROJECT)
    assert out["error"] == "missing_upscale_media_id"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_four_k_request_is_refused_at_the_sdk_not_dispatched():
    """Two captures disagree about the tier slot and 4K costs 50 credits, so a
    guess spends the user's money to find out who was right."""
    fake = BatchFakeClient({})
    out = await FlowSDK(fake).upscale_video(
        "op-1", "media-1", PROJECT, resolution="4k"
    )
    assert "4k" in out["error"].lower()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_an_upscale_whose_id_cannot_be_read_is_an_error():
    """The submit was accepted: a render is running and being billed at a price
    nobody has measured. Reporting success strands it."""
    fake = BatchFakeClient({"p0UkFb": _canned("p0UkFb", [[]])})
    out = await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    assert out["error"] == "upscale_no_operation_returned"


@pytest.mark.asyncio
async def test_a_failed_upscale_never_reports_an_operation_it_did_not_start():
    """The retry gate refuses a result that names operations. Naming one here
    would block a retry that is in fact safe."""
    fake = BatchFakeClient({"p0UkFb": _canned("p0UkFb", [[]])})
    out = await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    assert out["operation_names"] == []


@pytest.mark.asyncio
async def test_an_upscale_that_could_not_be_SENT_reports_no_operation_either():
    """The bridge failed, so nothing started — and the id it was HANDED is the
    ORIGINAL clip's operation, not a new one.

    Two things go wrong if that id comes out as "created". The retry gate blocks
    a retry that is safe, because it believes a render is running. Worse, the
    re-poll path takes those names and waits on them: the original operation is
    already finished, so it resolves immediately and its media id lands as if it
    were the upscale. A fake 1080p file, from a request that never left."""
    fake = BatchFakeClient({"p0UkFb": {"error": "NO_FLOW_TAB", "status": 0}})
    out = await FlowSDK(fake).upscale_video("op-1", "media-1", PROJECT)
    assert out["error"]
    assert out["operation_names"] == []


# ── the handler layer: upscale ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_handler_refuses_a_clip_with_no_operation_id(stub):
    """Clips rendered before `operationNames` was recorded. Refused by name, so
    the card can say "chạy lại clip này" instead of spending a submit."""
    out, err = await processor._handle_upscale_video(
        {"media_id": "m", "project_id": PROJECT}
    )
    assert err == "missing_upscale_operation_id"
    assert stub.upscale_calls == []


@pytest.mark.asyncio
async def test_the_handler_refuses_an_upscale_with_no_project(stub):
    out, err = await processor._handle_upscale_video(
        {"operation_id": "op", "media_id": "m"}
    )
    assert err == "missing_project_id"
    assert stub.upscale_calls == []


@pytest.mark.asyncio
async def test_the_handler_passes_both_ids_through_untouched(stub):
    await processor._handle_upscale_video({
        "operation_id": "op-1", "media_id": "media-1", "project_id": PROJECT,
    })
    assert stub.upscale_calls[0][0] == "op-1"
    assert stub.upscale_calls[0][1] == "media-1"


@pytest.mark.asyncio
async def test_an_upscale_result_is_labelled_so_it_cannot_land_as_a_generation(
    monkeypatch,
):
    """`op_kind` is what stops a consumer that only knows how to settle a
    generation from writing this over `mediaId`.

    The poll is stubbed rather than run: it sleeps between rounds, and what is
    under test is the label the handler adds to whatever the poll returned.
    Via `monkeypatch` so the replacement cannot outlive this test — a leaked
    `_poll_video_dispatch` would silently make later tests pass without polling.
    """
    fake = BatchFakeClient({"p0UkFb": UPSCALE_OK})
    monkeypatch.setattr(processor, "get_flow_sdk", lambda: FlowSDK(fake))

    async def _no_poll(_sdk, dispatch, _rid):
        return (
            {"media_ids": ["up-1"], "operation_names": dispatch["operation_names"]},
            None,
        )

    monkeypatch.setattr(processor, "_poll_video_dispatch", _no_poll)
    out, err = await processor._handle_upscale_video({
        "operation_id": "op-1", "media_id": "media-1", "project_id": PROJECT,
    })
    assert err is None
    assert out["op_kind"] == "upscale"
    assert out["source_media_id"] == "media-1"
    # And it must NOT look like a generation result the settle path would adopt.
    assert "mediaId" not in out


# ── the handler layer: extend ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_extension_starts_from_the_scene_clone(stub):
    """Not from the clip's own media id. The capture says the UI references the
    clone, and this is link 1."""
    await processor._handle_extend_video({
        "prompt": "đi tiếp", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert stub.scene_calls[0][1] == ["media-1"]
    assert stub.extend_calls[0]["source"] == "clone-1"


@pytest.mark.asyncio
async def test_a_later_extension_starts_from_the_previous_operation_id(stub):
    """And NOT from a media id. Converting the operation id to one produces a
    request Flow accepts and then fails NOT_FOUND — billed, and no clip."""
    await processor._handle_extend_video({
        "prompt": "đi tiếp", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
        "scene_id": "scene-1", "scene_clone_media_id": "clone-1",
        "previous_operation_id": "op-ext-1", "position": 2,
    })
    assert stub.extend_calls[0]["source"] == "op-ext-1"
    assert stub.extend_calls[0]["position"] == 2


@pytest.mark.asyncio
async def test_a_known_scene_is_not_created_again(stub):
    """A second scene for the same clip restarts the chain at position 1 and
    orphans everything already extended."""
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
        "scene_id": "scene-1", "scene_clone_media_id": "clone-1",
    })
    assert stub.scene_calls == []


@pytest.mark.asyncio
async def test_the_scene_comes_back_so_the_node_can_remember_it(stub):
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert out["scene_id"] == "scene-1"
    assert out["scene_clone_media_id"] == "clone-1"
    assert out["scene_created"] is True


@pytest.mark.asyncio
async def test_a_scene_made_before_a_failed_submit_is_still_reported(stub):
    """It exists on Flow. Losing it means the retry makes a second one."""
    stub.dispatch_error = "PUBLIC_ERROR_SOMETHING"
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert err == "PUBLIC_ERROR_SOMETHING"
    assert out["scene_id"] == "scene-1"


@pytest.mark.asyncio
async def test_the_free_lane_is_the_default_through_the_handler(stub):
    """The SDK defaults to free; the handler must not quietly pass paid."""
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert stub.extend_calls[0]["paid_lane"] is False


@pytest.mark.asyncio
async def test_paying_needs_the_flag_and_nothing_else_implies_it(stub):
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
        "paid_lane": True,
    })
    assert stub.extend_calls[0]["paid_lane"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("truthy", ["true", 1, "yes"])
async def test_a_truthy_non_boolean_does_not_buy_the_paid_lane(stub, truthy):
    """`params` arrives unvalidated over HTTP. A string "false" is truthy in
    Python, so anything looser than an identity check on True can be talked
    into spending."""
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
        "paid_lane": truthy,
    })
    assert stub.extend_calls[0]["paid_lane"] is False


@pytest.mark.asyncio
async def test_the_source_model_key_travels_to_the_sdk(stub):
    """The SDK refuses Omni on its own — it is a public surface and the handler is
    not its only caller. So the key has to arrive there even on the clips the
    handler already let through, or that second guard is decoration.

    Uses a Veo key on purpose: an Omni one never reaches the SDK any more, and a
    test that still sent one would be asserting on a path that no longer exists.
    """
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": "veo_3_1_i2v_lite",
    })
    assert stub.extend_calls[0]["source_model_key"] == "veo_3_1_i2v_lite"


@pytest.mark.asyncio
async def test_an_extension_without_a_prompt_never_reaches_flow(stub):
    out, err = await processor._handle_extend_video({
        "project_id": PROJECT, "media_id": "media-1",
    })
    assert err == "missing_prompt"
    assert stub.scene_calls == [] and stub.extend_calls == []


@pytest.mark.asyncio
async def test_an_extension_without_a_clip_never_reaches_flow(stub):
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT,
    })
    assert err == "missing_media_id"
    assert stub.scene_calls == []


@pytest.mark.asyncio
async def test_a_scene_that_cannot_be_made_stops_before_the_extension(stub):
    async def _boom(*_a, **_k):
        raise fb.FlowBatchError("rqZuUc: nope")

    stub.create_scene = _boom  # type: ignore[assignment]
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert err is not None and err.startswith("extend_scene_failed")
    assert stub.extend_calls == []


@pytest.mark.asyncio
async def test_a_scene_reply_with_no_id_stops_before_the_extension(stub):
    """Extending into a scene that has no id is a request three calls from a
    failure, and the failure would be billed."""
    stub.scene = {"scene_id": "", "clone_media_id": ""}
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert err == "extend_scene_returned_no_id"
    assert stub.extend_calls == []


# ── retry classification ──────────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "upscale_no_operation_returned",
    "extend_no_operation_returned",
])
def test_an_accepted_submit_with_an_unreadable_id_is_never_retried(code):
    """Retrying submits a SECOND render for a clip already rendering. Terminal is
    the answer that does not double the bill, and it must not depend on the
    catch-all default staying what it is today."""
    assert processor.classify_error(code) == "terminal"


@pytest.mark.parametrize("code", [
    "missing_upscale_operation_id",
    "missing_upscale_media_id",
])
def test_our_own_refusals_are_terminal_and_never_touch_the_breaker(code):
    """They embed nothing from the account, so three of them must not read as
    auth failures and stop every dispatch in the app."""
    assert processor.classify_error(code) == "terminal"

# ── measuring the unknown prices ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stale_meter_is_refreshed_before_the_run_so_it_can_be_priced(stub):
    """Neither 1080p upscale nor the extension lanes have a measured price here.
    The read is free (`nzlxg`), so the run that finally measures them is the
    first real one rather than a special errand."""
    flow_credits.forget()
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert stub.credit_reads == 1


@pytest.mark.asyncio
async def test_the_refreshed_balance_is_what_the_measurement_compares_against(
    stub, monkeypatch,
):
    """The first real run is the one that prices these, and on a cold process the
    meter is empty. So the refresh read must not just happen — its VALUE has to
    become the baseline. Discarding it leaves every run unpriceable, which reads
    exactly like "this is free information we cannot get" when in fact one free
    call gets it."""
    flow_credits.forget()
    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll_then_charge(15))
    stub.dispatch_error = None
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert stub.credit_reads == 1
    assert out["credits_before"] == 989
    assert out["credits_spent_observed"] == 15


@pytest.mark.asyncio
async def test_a_fresh_meter_is_not_re_read(stub):
    """The poll carries the balance for free every few seconds. Asking again is
    a call that buys nothing."""
    flow_credits.forget()
    flow_credits.remember(989)
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert stub.credit_reads == 0


@pytest.mark.asyncio
async def test_the_extension_price_observation_names_its_lane(stub, monkeypatch):
    """The two lanes are two prices. An observation labelled just "extend" would
    be copied into the wrong row."""
    flow_credits.forget()
    flow_credits.remember(989)
    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll_then_charge(15))
    stub.dispatch_error = None
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert out["credits_spent_observed"] == 15
    assert "free lane" in out["credits_note"]


@pytest.mark.asyncio
async def test_a_paid_extension_is_labelled_as_such(stub, monkeypatch):
    flow_credits.forget()
    flow_credits.remember(989)
    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll_then_charge(30))
    stub.dispatch_error = None
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
        "paid_lane": True,
    })
    assert "paid lane" in out["credits_note"]


@pytest.mark.asyncio
async def test_an_unmeasurable_run_says_so_rather_than_reporting_zero(stub, monkeypatch):
    """Zero is the number that would let a lane be called free. It must never be
    what "could not measure" looks like."""
    flow_credits.forget()

    async def _no_balance(_sdk, dispatch, _rid):
        flow_credits.forget()
        return {"media_ids": ["e-1"], "operation_names": ["o-1"]}, None

    async def _read_fails() -> dict:
        raise RuntimeError("nzlxg: no flow tab")

    stub.get_credits = _read_fails  # type: ignore[assignment]
    stub.dispatch_error = None
    monkeypatch.setattr(processor, "_poll_video_dispatch", _no_balance)
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    assert out["credits_spent_observed"] is None
    assert "chưa đo được giá" in out["credits_note"]


@pytest.mark.asyncio
async def test_the_upscale_observation_records_the_resolution_it_priced(stub, monkeypatch):
    """1080p and 4K are different prices, and 4K is refused for exactly that
    reason. An observation that did not say which one is not usable."""
    flow_credits.forget()
    flow_credits.remember(989)
    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll_then_charge(7))
    stub.dispatch_error = None
    out, _err = await processor._handle_upscale_video({
        "operation_id": "op-1", "media_id": "media-1", "project_id": PROJECT,
    })
    assert out["credits_spent_observed"] == 7
    assert "1080p" in out["credits_note"]


@pytest.mark.asyncio
async def test_an_observation_is_never_presented_as_a_settled_price(stub, monkeypatch):
    """The delta covers everything the account spent in the window. The field
    name and the note both have to carry that, or the number gets copied into a
    price table it has not earned."""
    flow_credits.forget()
    flow_credits.remember(989)
    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll_then_charge(15))
    stub.dispatch_error = None
    out, _err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": VEO_KEY,
    })
    # The field is named for what it is — an observation — and there is no
    # field that reads as a settled price.
    assert "credits_spent_observed" in out
    assert "credits_price" not in out and "price" not in out
    assert "nếu lúc đó không có render nào khác" in out["credits_note"]

# ── the Omni refusal arrives before anything is written ───────────────


@pytest.mark.asyncio
async def test_an_omni_clip_is_refused_before_a_scene_is_created(stub):
    """The scene call is free, but it is a real write on the user's Flow project.
    Making one for a clip Flow will never extend leaves litter behind for a
    request that was always going to be refused."""
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": "abra_i2v_8s",
    })
    assert err is not None and err.startswith("unsupported_extend_omni_source")
    assert stub.scene_calls == []
    assert stub.extend_calls == []


@pytest.mark.asyncio
async def test_the_early_refusal_still_names_the_clip(stub):
    out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": "abra_r2v_4s",
    })
    assert "abra_r2v_4s" in err


@pytest.mark.asyncio
async def test_the_early_refusal_does_not_even_read_the_balance(stub):
    """A refusal costs nothing and should ask for nothing. Probing the balance
    for a request that never dispatches is a call that buys no information."""
    flow_credits.forget()
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": "abra_i2v_8s",
    })
    assert stub.credit_reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ABRA_I2V_8S", " abra_t2v_6s "])
async def test_the_omni_check_is_not_fooled_by_case_or_spacing(stub, key):
    """`sourceModelKey` is stored node data that arrives over HTTP unvalidated.
    A check tighter than the one the SDK uses would let a clip through here and
    be caught one call later — after the scene had been written."""
    _out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
        "source_model_key": key,
    })
    assert err is not None and err.startswith("unsupported_extend_omni_source")
    assert stub.scene_calls == []


def test_one_definition_decides_what_omni_means():
    """Two callers refuse at two different moments. Two copies of the rule would
    drift, and the half that drifted would spend a submit."""
    from flowboard.services.flow_sdk import is_omni_model_key

    assert is_omni_model_key("abra_i2v_8s") is True
    assert is_omni_model_key("ABRA_R2V_4S") is True
    assert is_omni_model_key("veo_3_1_i2v_lite") is False
    assert is_omni_model_key("") is False

# ── a clip with no recorded provenance ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   "])
async def test_a_clip_with_no_recorded_source_is_refused(stub, key):
    """The greyed-out button is not the only gate.

    `POST /api/requests` accepts any type over an unauthenticated local endpoint,
    so the handler has to hold this rule too. And the rule matters because a BLANK
    key does not start with `abra_`: before this, silence read as "not Omni" and
    bought a submit Flow accepts and then fails, after billing the attempt. Every
    clip rendered before P16 was in exactly that state.
    """
    params = {"prompt": "p", "project_id": PROJECT, "media_id": "media-1"}
    if key is not None:
        params["source_model_key"] = key
    _out, err = await processor._handle_extend_video(params)
    assert err is not None and err.startswith("extend_unknown_source")
    assert stub.scene_calls == []
    assert stub.extend_calls == []


@pytest.mark.asyncio
async def test_the_unknown_source_refusal_says_what_to_do(stub):
    """"Refused" with no way forward is a dead end. The clip can be re-run to
    gain a record, or extended in Flow's own UI."""
    _out, err = await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
    })
    assert "chạy lại clip" in err.lower() or "nối trong Flow" in err


@pytest.mark.asyncio
async def test_an_unknown_source_costs_no_balance_read(stub):
    """A refusal should ask for nothing. Probing the balance for a request that
    never dispatches is a call that buys no information."""
    flow_credits.forget()
    await processor._handle_extend_video({
        "prompt": "p", "project_id": PROJECT, "media_id": "media-1",
    })
    assert stub.credit_reads == 0


@pytest.mark.asyncio
async def test_upscale_does_not_care_about_provenance(stub):
    """Only EXTEND is Veo-only. Refusing an upscale for a missing model key would
    take a capability away from a clip the user already paid for, for no gain."""
    await processor._handle_upscale_video({
        "operation_id": "op-1", "media_id": "media-1", "project_id": PROJECT,
    })
    assert stub.upscale_calls != []

