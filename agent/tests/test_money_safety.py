"""What the app is allowed to spend, proved at the layer that spends it.

Every test here reaches the worker or the SDK, because that is where money is
actually committed and it is precisely the layer the earlier tests stopped
short of. A service-level test can assert that `validate()` refuses and still
leave a dispatch that bills the wrong lane — which is what happened: the
character-socket path was billing Omni Flash for boards that asked for the
free queue, and every test was green.

Three rules are pinned:

* **The lane the board chose is the price the board agreed to.** When no model
  key exists for that lane, the dispatch is REFUSED — never quietly served by
  a pricier family.
* **Giving up waiting is not giving up spending.** A request the executor
  stopped waiting for is taken off the queue, or the worker dispatches it
  later and RUN Lỗi bills the same node twice.
* **A credential for one feature is not consent for another.** whisper-1 needs
  its own switch, not just an OpenAI key that happens to exist.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, PipelineRun, Request
from flowboard.services import pipeline_executor


# ── the lane decides the family ───────────────────────────────────────


def _omni_params(**over) -> dict:
    params = {
        "prompt": "cô gái cầm sản phẩm",
        "project_id": "abcd1234",
        "ref_media_ids": ["m-model"],
        "duration_s": 8,
        "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "paygate_tier": "PAYGATE_TIER_TWO",
    }
    params.update(over)
    return params


class _OmniSpy:
    """Records the model key the dispatch actually asked Flow for."""

    def __init__(self):
        self.calls: list[dict] = []

    async def gen_video_omni(self, **kwargs):
        self.calls.append(kwargs)
        return {"raw": {}, "operation_names": ["op-1"]}


@pytest.fixture
def omni(monkeypatch):
    from flowboard.services import flow_sdk, media_project_sync
    from flowboard.worker import processor

    spy = _OmniSpy()
    monkeypatch.setattr(flow_sdk, "_sdk", spy)

    async def _poll(sdk, dispatch, request_id):
        return {"media_ids": ["m-out"]}, None

    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll)

    async def _sync(ids, project_id):
        return list(ids), []

    monkeypatch.setattr(
        media_project_sync, "ensure_media_ids_in_project", _sync
    )
    return spy


@pytest.mark.asyncio
async def test_the_free_reference_lane_reaches_veos_own_zero_credit_key(omni):
    """The bug this file exists for, and where it finally landed.

    Three versions of this test, and the money rule never changed:

    1. a board on the free low-priority queue was billed 25 credits, because the
       reference dispatch ignored the lane and always resolved Omni Flash;
    2. the fix routed it to Veo's r2v family — then the transport migration made
       that family look unavailable, so the board was REFUSED instead. Same rule,
       worse outcome: refusing costs nothing but the free clip was unreachable;
    3. measured 19/09/2026 — `MZZa6b` parses `veo_3_1_r2v_lite_low_priority` and
       objects only to the plan. The key works. So the free lane dispatches the
       free key, which is what the board asked for all along.
    """
    from flowboard.worker.processor import _handle_gen_video_omni

    result, err = await _handle_gen_video_omni(
        _omni_params(video_quality="lite_relaxed")
    )
    assert err is None, err
    assert omni.calls[0]["model_key"] == "veo_3_1_r2v_lite_low_priority"
    # 0 credit, and read from the table the review loop consults.
    from flowboard.services import flow_sdk

    assert flow_sdk.is_zero_credit_model(result["model_key"]) is False, (
        "the i2v free-key set must not silently absorb the r2v one"
    )


@pytest.mark.asyncio
async def test_a_veo_reference_lane_with_no_key_is_still_refused(omni):
    """Only one Veo r2v name survives on this transport. The other three carry
    `portrait` and come back `[5]` NOT_FOUND, so offering them would spend a
    round trip to learn what the table already knows."""
    from flowboard.worker.processor import _handle_gen_video_omni

    _, err = await _handle_gen_video_omni(_omni_params(video_quality="fast"))
    assert err is not None and "fast" in err
    assert omni.calls == [], "a refused lane still reached Flow"


@pytest.mark.asyncio
async def test_the_omni_lane_reaches_omni_flash_with_a_named_key(omni):
    """The Settings-level "OMNI flash" choice is explicit and keeps working.

    The key is now resolved in the handler rather than left to the SDK, so the
    result can report what was billed without the caller re-deriving it from the
    lane label -- the review loop reads that key to decide whether another round
    is free, and a missing one reads as "not free".
    """
    from flowboard.worker.processor import _handle_gen_video_omni

    result, err = await _handle_gen_video_omni(_omni_params(video_quality="omni"))
    assert err is None
    assert omni.calls[0]["model_key"] == "abra_r2v_8s"
    assert result["model_key"] == "abra_r2v_8s"


@pytest.mark.asyncio
async def test_no_lane_at_all_is_omni_flash_as_before(omni):
    """The canvas dialog sends no lane when the user chose Omni Flash in
    Settings. That path predates this change and must not start refusing."""
    from flowboard.worker.processor import _handle_gen_video_omni

    result, err = await _handle_gen_video_omni(_omni_params())
    assert err is None
    assert omni.calls[0]["model_key"] == "abra_r2v_8s"
    assert result["resolution"] == "720p"


@pytest.mark.asyncio
async def test_a_duration_omni_does_not_have_is_refused(omni):
    """The duration IS part of the key, so the nearest one bills a different
    clip length than the board asked for."""
    from flowboard.worker.processor import _handle_gen_video_omni

    _, err = await _handle_gen_video_omni(
        _omni_params(video_quality="omni", duration_s=5)
    )
    assert err is not None
    assert omni.calls == []


@pytest.mark.asyncio
async def test_a_lane_with_no_r2v_key_is_refused_not_upgraded(omni):
    """`fast` costs more than `lite`, and Omni costs more than both.

    Every swap that would make this dispatch succeed spends credits the board
    never agreed to, so nothing is dispatched at all. On the batch path this
    covers every Veo lane rather than only the ones a tier lacked.
    """
    from flowboard.worker.processor import _handle_gen_video_omni

    result, err = await _handle_gen_video_omni(_omni_params(video_quality="lite"))
    assert err is not None and "lite" in err
    assert omni.calls == [], "a refused lane still reached Flow"


@pytest.mark.asyncio
async def test_a_pro_account_asking_for_a_free_lane_is_refused(omni):
    """No r2v lane is free here, on any account.

    Tier is no longer part of the answer -- the payload has no tier slot, so
    entitlements are Google's call at dispatch time. What survives from the
    original test is the property: a board that asked for free is never quietly
    served a paid lane.
    """
    from flowboard.worker.processor import _handle_gen_video_omni

    _, err = await _handle_gen_video_omni(
        _omni_params(paygate_tier="PAYGATE_TIER_ONE", video_quality="fast_relaxed")
    )
    assert err is not None
    assert omni.calls == []


@pytest.mark.asyncio
async def test_a_landscape_board_on_a_veo_lane_is_refused(omni):
    """Every Veo r2v key is portrait. Reusing one for a landscape board buys
    a clip in the wrong shape — paid for, and unusable."""
    from flowboard.worker.processor import _handle_gen_video_omni

    _, err = await _handle_gen_video_omni(
        _omni_params(
            video_quality="fast_relaxed",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
        )
    )
    assert err is not None and "OMNI" in err
    assert omni.calls == []


# ── giving up waiting is not giving up spending ───────────────────────


def _board(client) -> dict:
    board = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    return board


def _image_node(client, board_id, prompt="một con mèo") -> dict:
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": "image", "x": 0, "y": 0,
        "data": {"title": "A", "prompt": prompt},
    }).json()


@pytest.mark.asyncio
async def test_a_request_the_executor_gave_up_on_cannot_be_dispatched_later(
    client, monkeypatch
):
    """The double-bill. The queue is paused, so the executor times out and
    stamps the node `timeout`; the row used to stay `queued`, and resuming
    the queue dispatched it — Flow charging for a clip whose media never
    reached the node, after which RUN Lỗi paid for it a second time."""
    from flowboard.services import flow_sdk
    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    dispatched: list[str] = []

    class _Stub:
        async def gen_image(self, **kwargs):
            dispatched.append(kwargs["prompt"])
            return {"raw": {}, "media_ids": ["m-new"], "media_entries": []}

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    board = _board(client)
    _image_node(client, board["id"])
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    w.pause()
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(
            rid, request_timeout_s=0.4, poll_interval_s=0.05
        )
        with get_session() as s:
            rows = [(r.status, r.error) for r in s.exec(select(Request)).all()]
        assert rows == [("canceled", "executor_timeout")], rows

        # Resuming must not turn that row into a charge.
        w.resume()
        await asyncio.sleep(0.8)
        assert dispatched == [], "a request the executor abandoned was billed"
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


def test_a_running_request_is_left_alone(client):
    """The other half. A `running` row is a call already with Google;
    cancelling it locally would not stop the charge, it would only throw away
    the result — the same bargain `stop_board` refuses to make."""
    board = _board(client)
    node = _image_node(client, board["id"])
    with get_session() as s:
        row = Request(node_id=node["id"], type="gen_image", params={}, status="running")
        s.add(row)
        s.commit()
        s.refresh(row)
        rid = row.id

    assert pipeline_executor._cancel_if_queued(rid) is False
    with get_session() as s:
        assert s.get(Request, rid).status == "running"


@pytest.mark.asyncio
async def test_waiting_stops_when_the_user_cancels(client):
    """`canceled` is terminal. Without it the executor sat out its whole
    timeout — 722s a node — waiting for a row nobody was going to run."""
    board = _board(client)
    node = _image_node(client, board["id"])
    with get_session() as s:
        row = Request(
            node_id=node["id"], type="gen_image", params={},
            status="canceled", error="canceled",
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        rid = row.id

    settled = await asyncio.wait_for(
        pipeline_executor._await_request(rid, timeout_s=5.0, poll_s=0.05),
        timeout=2.0,
    )
    assert settled.status == "canceled"


def test_a_canceled_request_is_not_reported_as_a_failure(monkeypatch):
    """The node says what happened. "request_failed" for a Stop the user
    pressed sends them looking for a fault that is not there."""
    stamped: dict = {}

    class _Row:
        status = "canceled"
        error = "canceled"
        result = None

    monkeypatch.setattr(
        pipeline_executor, "_stamp_node_status",
        lambda nid, st, **kw: stamped.update({"status": st, **kw}),
    )
    failed: set[int] = set()
    pipeline_executor._settle_generation_node(1, _Row(), failed, {})
    assert stamped["status"] == "error"
    assert stamped["error"] == "canceled"
    assert failed == {1}


# ── a key for one feature is not consent for another ──────────────────


def test_whisper_needs_its_own_switch_not_just_a_key(monkeypatch):
    """An OpenAI key pasted in to write scripts with GPT used to enrol
    whisper-1 in the transcription chain, so a Gemini 429 mid-run started
    billing OpenAI per minute."""
    from flowboard.services import settings_store, stt
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda name: "sk-test")
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: False)
    assert stt.openai_available() is False
    assert "whisper-1" not in stt.sources()


def test_whisper_runs_when_both_halves_are_there(monkeypatch):
    from flowboard.services import settings_store, stt
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda name: "sk-test")
    monkeypatch.setattr(
        settings_store, "get",
        lambda key, default=None: key == "OPENAI_STT_ENABLED",
    )
    assert stt.openai_available() is True


def test_the_switch_alone_buys_nothing(monkeypatch):
    """No key, no transcription — the switch must not make the chain claim a
    source it cannot reach."""
    from flowboard.services import settings_store, stt
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda name: None)
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: True)
    assert stt.openai_available() is False


# ── the review loop judges what ran, not what was asked for ───────────


def _round(**over):
    from flowboard.services import review_loop

    kwargs = {
        "round_index": 1,
        "score": 40.0,
        "verdict": "kem",
        "settings": review_loop.Settings(enabled=True, threshold=70.0, max_rounds=3),
        "quality": "lite_relaxed",
        "fix_hint": "",
    }
    kwargs.update(over)
    return review_loop.decide(**kwargs)


def test_a_free_lane_served_by_a_paid_model_does_not_retry():
    """Pro has no 0-credit lane, so Flow runs the cheapest paid key instead.
    Reading the label, this loop called that free and spent up to three more
    clips per node."""
    decision = _round(model_key="veo_3_1_i2v_lite")
    assert decision.retried is False
    assert "veo_3_1_i2v_lite" in decision.reason


def test_a_free_lane_actually_served_free_still_retries():
    decision = _round(model_key="veo_3_1_i2v_lite_low_priority")
    assert decision.retried is True


def test_an_unknown_model_key_is_not_assumed_free():
    """A key the table has not heard of is new or misspelled. Guessing "free"
    on either is how automation starts spending without asking."""
    decision = _round(model_key="veo_9_9_i2v_something_new")
    assert decision.retried is False


def test_the_label_is_used_only_when_no_key_was_reported():
    """Post-production and failed-before-resolution dispatches report no key;
    those still fall back to the lane label rather than refusing outright."""
    decision = _round(model_key=None)
    assert decision.retried is True


def test_a_paid_lane_asked_for_outright_still_says_so():
    """The message must not blame a substitution that did not happen."""
    decision = _round(quality="fast", model_key="veo_3_1_i2v_s_fast")
    assert decision.retried is False
    assert "tốn credit" in decision.reason


def test_no_substitution_is_ever_reported_because_none_is_ever_made():
    """The mechanism the UI reads, now deliberately always empty.

    t2v reported lane substitutions from the start; i2v never did, and i2v is
    the path every imported board takes. The gap was real and the fix landed --
    and then the transport changed the answer: this build refuses a lane it
    cannot serve instead of swapping it, so there is never a substitution to
    report.

    The key stays in the result shape on purpose. `jobs.ts` reads
    `model_substitutions` to warn the user, and keeping the channel means no
    frontend change is needed if a future capture ever makes a swap honest. What
    must not happen is this list quietly filling with a swap nobody agreed to,
    so the emptiness is asserted rather than assumed.
    """
    from flowboard.services import flow_sdk

    for lane in ("lite_relaxed", "lite", "fast"):
        plan = flow_sdk.resolve_video_plan(lane, "VIDEO_ASPECT_RATIO_PORTRAIT")
        assert plan["error"] is None, lane
        assert plan["substitutions"] == []
        assert plan["effective_quality"] == lane

    # The one 0-credit key, reachable and recognised as free.
    free = flow_sdk.resolve_video_plan("lite_relaxed", "VIDEO_ASPECT_RATIO_PORTRAIT")
    assert flow_sdk.is_zero_credit_model(free["model_key"]) is True


def test_the_upstream_fold_that_would_bill_a_free_lane_is_not_ported():
    """Upstream's resolver folds an unrecognised key onto its default, and
    because it matches the substring "ultra", the 0-credit
    `veo_3_1_i2v_s_fast_ultra_relaxed` folds onto the PAID
    `veo_3_1_i2v_s_fast_ultra`. That is the one substitution this build never
    makes, so the guard is checked here as well as in the batch tests."""
    import pytest as _pt

    from flowboard.services import flow_batch as fb

    with _pt.raises(ValueError):
        fb.check_video_model("veo_3_1_i2v_s_fast_ultra_relaxed")


# ── an image paid for once is paid for once ───────────────────────────


@pytest.mark.asyncio
async def test_a_retry_uploads_the_image_it_already_bought(monkeypatch, tmp_path):
    """The Flow upload can fail for reasons that have nothing to do with the
    image — an expired extension token, a project id gone stale. Before this,
    that returned an error, the worker re-dispatched, and OpenAI was charged
    again for a picture already sitting in memory."""
    from flowboard.services import image_ingest, openai_images
    from flowboard.worker import processor

    monkeypatch.setattr(processor, "_PAID_IMAGE_DIR", tmp_path / "paid")

    drawn: list[str] = []

    async def _generate(prompt, aspect_ratio=None):
        drawn.append(prompt)
        return openai_images.GeneratedImage(
            data=b"\x89PNG-bytes", mime="image/png", source="api-key"
        )

    monkeypatch.setattr(openai_images, "generate", _generate)

    uploads = {"n": 0}

    async def _ingest(data, mime, project_id, name, node_id):
        uploads["n"] += 1
        if uploads["n"] == 1:
            raise image_ingest.IngestError("phiên Flow hết hạn")
        assert data == b"\x89PNG-bytes", "the retry uploaded different bytes"
        return {"media_id": "m-openai-1"}

    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    params = {
        "prompt": "một chiếc bánh",
        "project_id": "abcd1234",
        "__request_id": 77,
    }
    _, err = await processor._handle_gen_image_openai(params)
    assert err is not None and "tải lên Flow lỗi" in err
    assert drawn == ["một chiếc bánh"]

    # Same request row, second attempt: the worker re-dispatches.
    result, err = await processor._handle_gen_image_openai(params)
    assert err is None
    assert result["media_ids"] == ["m-openai-1"]
    assert drawn == ["một chiếc bánh"], "OpenAI was charged twice for one image"


@pytest.mark.asyncio
async def test_parked_bytes_are_dropped_once_flow_has_them(monkeypatch, tmp_path):
    """Otherwise every generated image stays on disk twice, forever."""
    from flowboard.services import image_ingest, openai_images
    from flowboard.worker import processor

    monkeypatch.setattr(processor, "_PAID_IMAGE_DIR", tmp_path / "paid")

    async def _generate(prompt, aspect_ratio=None):
        return openai_images.GeneratedImage(
            data=b"bytes", mime="image/png", source="api-key"
        )

    async def _ingest(data, mime, project_id, name, node_id):
        return {"media_id": "m-1"}

    monkeypatch.setattr(openai_images, "generate", _generate)
    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    _, err = await processor._handle_gen_image_openai({
        "prompt": "x", "project_id": "abcd1234", "__request_id": 5,
    })
    assert err is None
    assert list((tmp_path / "paid").glob("*")) == []


@pytest.mark.asyncio
async def test_a_request_without_a_row_id_still_works(monkeypatch, tmp_path):
    """Direct calls and tests have no request id. They must not crash, they
    just do not get the reuse."""
    from flowboard.services import image_ingest, openai_images
    from flowboard.worker import processor

    monkeypatch.setattr(processor, "_PAID_IMAGE_DIR", tmp_path / "paid")

    async def _generate(prompt, aspect_ratio=None):
        return openai_images.GeneratedImage(
            data=b"bytes", mime="image/png", source="api-key"
        )

    async def _ingest(data, mime, project_id, name, node_id):
        return {"media_id": "m-1"}

    monkeypatch.setattr(openai_images, "generate", _generate)
    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    result, err = await processor._handle_gen_image_openai(
        {"prompt": "x", "project_id": "abcd1234"}
    )
    assert err is None and result["media_ids"] == ["m-1"]


# ── one vendor's failure is not another vendor's fault ────────────────


def test_an_openai_403_does_not_trip_the_google_breaker():
    """Three 403s stop every Flow dispatch in the app. An OpenAI quota
    problem used to count toward that, so a spent OpenAI key took Flow down
    with it."""
    from flowboard.worker.processor import classify_error

    assert classify_error("API_403 insufficient_quota") == "403"
    assert (
        classify_error("API_403 insufficient_quota", request_type="gen_image_openai")
        == "terminal"
    )


def test_an_openai_401_is_a_wrong_key_not_an_expired_token():
    """The `free` bucket exists for the extension's token, which refreshes.
    An OpenAI 401 never will, and it retried twenty times against a wall."""
    from flowboard.worker.processor import classify_error

    assert classify_error("API_401", request_type="gen_image_openai") == "terminal"


def test_a_transient_openai_failure_is_still_retried():
    """The fix must not turn a rate limit into a dead node."""
    from flowboard.worker.processor import classify_error

    assert classify_error("API_429 slow down", request_type="gen_image_openai") == "counted"
    assert classify_error("request timed out", request_type="gen_image_openai") == "counted"
