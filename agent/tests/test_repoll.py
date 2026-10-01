"""Re-polling a render that was already paid for.

The old money rule was "never re-dispatch a result that names operations": each
name is a generation running at Google and charged for, so a retry creates a
second one and bills twice. It held, and it left the first clip stranded — a
four-variant Omni batch abandoned at the executor's timeout is 60 credits of
video nobody could reach.

The batch transport changes that, because a finished media id can be found again
through the project listing. So the retry became a re-poll: `RUN Lỗi` and a
re-run both send the node through `poll_video`, which takes the operation names
the previous attempt recorded and waits on them instead of starting new ones.

Eligibility is deliberately narrow. Only operations that never resolved are
re-polled; one Google terminally refused is left refused, because polling it
again replaces a clear answer with a ten-minute wait for the same one.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, Node, Request
from flowboard.services.pipeline_executor import _unresolved_video_operations
from flowboard.short_id import generate_unique_short_id


#: The dispatch shape of an UNEDITED node: what the fixture's own request row
#: stored. Passing it says "nothing about this node changed since that attempt",
#: which is the precondition every test here was written under and none of them
#: could state before — the comparison did not exist.
_SAME = {"prompt": "move"}


def _node_with_request(
    *,
    params: dict | None = None,
    req_type: str = "gen_video",
    result: dict | None = None,
    # What the worker actually writes when a poll runs out: status `timeout`,
    # error `timeout_waiting_video`. The old default paired `failed` with a bare
    # `"timeout"`, a combination production never produces — so the tests were
    # exercising a fictional row.
    status: str = "timeout",
    error: str | None = "timeout_waiting_video",
) -> int:
    with get_session() as s:
        board = Board(name="repoll")
        s.add(board)
        s.commit()
        s.refresh(board)
        node = Node(
            board_id=board.id,
            short_id=generate_unique_short_id(s, board.id),
            type="video",
            data={"prompt": "move"},
        )
        s.add(node)
        s.commit()
        s.refresh(node)
        s.add(Request(
            node_id=node.id,
            type=req_type,
            params=dict(params or _SAME),
            status=status,
            error=error,
            result=result if result is not None else {},
        ))
        s.commit()
        return node.id


def test_a_timed_out_batch_is_offered_for_re_polling():
    node_id = _node_with_request(result={
        "operation_names": ["op-1", "op-2"],
        "media_ids": [None, None],
        "op_errors": {"op-1": "timeout_waiting_video", "op-2": "timeout_waiting_video"},
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None
    assert repoll["operation_names"] == ["op-1", "op-2"]


def test_a_slot_that_already_landed_is_not_polled_again():
    """`media_ids` is positional against `operation_names`, so slot 0 landing
    means operation 0 is done. Asking for it again would be harmless and slow;
    the reason to get it right is that the node's media comes back keyed by
    position, and a shifted list pairs clips with the wrong source images."""
    node_id = _node_with_request(result={
        "operation_names": ["op-1", "op-2"],
        "media_ids": ["media-1", None],
        "op_errors": {"op-2": "timeout_waiting_video"},
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll["operation_names"] == ["op-2"]


def test_an_operation_google_refused_is_not_re_polled():
    """A content-filter rejection is terminal per operation. Polling it again
    swaps a clear refusal for a long wait ending in the same refusal."""
    node_id = _node_with_request(result={
        "operation_names": ["op-1"],
        "media_ids": [None],
        "op_errors": {"op-1": "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"},
    })
    assert _unresolved_video_operations(node_id, current=_SAME) is None


def test_a_batch_where_everything_resolved_is_not_re_polled():
    """Both outcomes count as resolved: the clip landed, or Google said no.

    Nothing is re-polled — but `media-1` was paid for and this node does not have
    it, so it is claimed rather than abandoned. Re-polling and adopting are
    different answers: one waits on Google again, the other copies a finished
    result onto the node. The refused slot stays refused; only a changed prompt
    makes that one worth dispatching again.
    """
    node_id = _node_with_request(result={
        "operation_names": ["op-1", "op-2"],
        "media_ids": ["media-1", None],
        "op_errors": {"op-2": "PUBLIC_ERROR_X"},
    })
    out = _unresolved_video_operations(node_id, current=_SAME)
    assert out is not None
    assert "operation_names" not in out, "nothing here should be polled again"
    assert out["adopt_media_ids"] == ["media-1", None]


def test_a_resolved_batch_the_node_already_holds_is_left_alone():
    """The guard that keeps adoption from replacing a deliberate re-run.

    Same row as above, but the node already carries the media. Then this is not an
    orphan — it is the user pressing run again — and handing back the old clip
    would make regenerating impossible.
    """
    node_id = _node_with_request(result={
        "operation_names": ["op-1"],
        "media_ids": ["media-1"],
    })
    with get_session() as s:
        node = s.exec(select(Node).where(Node.id == node_id)).one()
        node.data = {**(node.data or {}), "mediaIds": ["media-1"],
                     "mediaId": "media-1"}
        s.add(node)
        s.commit()
    assert _unresolved_video_operations(node_id, current=_SAME) is None


def test_a_node_that_never_dispatched_has_nothing_to_re_poll():
    node_id = _node_with_request(result={}, error="missing_prompt")
    assert _unresolved_video_operations(node_id, current=_SAME) is None


def test_a_cancelled_row_still_counts_as_unfinished():
    """Stop, or Clear queue, leaves the render running at Google. It is charged
    for either way, so the names are still worth waiting on."""
    node_id = _node_with_request(
        status="canceled",
        error="canceled",
        result={"operation_names": ["op-1"], "media_ids": [None]},
    )
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll["operation_names"] == ["op-1"]


def test_a_workflow_submit_carries_its_media_pairing_into_the_re_poll():
    """Text-to-video has no operation handle — the media id it polls against
    lives in the workflow record, and without it the re-poll has nothing to
    ask `as29s` for."""
    node_id = _node_with_request(
        req_type="gen_video_text",
        result={
            "operation_names": ["wf-1"],
            "media_ids": [None],
            "workflows": [{"name": "wf-1", "primary_media_id": "media-9"}],
        },
    )
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll["workflows"] == [{"name": "wf-1", "primary_media_id": "media-9"}]


def test_the_workflow_pairing_is_recovered_from_the_raw_dispatch():
    """The failure path stores the dispatch rather than the lifted keys, so the
    pairing has to be found there too — otherwise a timed-out text-to-video
    re-polls with no media id and never completes."""
    node_id = _node_with_request(
        req_type="gen_video_text",
        result={
            "operation_names": ["wf-1"],
            "raw_dispatch": {
                "operation_names": ["wf-1"],
                "workflows": [{"name": "wf-1", "primary_media_id": "media-9"}],
            },
        },
    )
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll["workflows"] == [{"name": "wf-1", "primary_media_id": "media-9"}]


def test_the_newest_attempt_decides():
    """An older attempt's operations belong to a render this node has since
    superseded. Walking further back would wait on a clip nobody wants.

    The newest attempt here finished, so nothing is re-polled. Its media is
    adopted instead of being left on the row — but the point pinned is that
    `op-old` is never touched.
    """
    node_id = _node_with_request(result={
        "operation_names": ["op-old"],
        "media_ids": [None],
        "op_errors": {"op-old": "timeout_waiting_video"},
    })
    with get_session() as s:
        node = s.exec(select(Node).where(Node.id == node_id)).one()
        s.add(Request(
            node_id=node.id,
            type="gen_video",
            params=dict(_SAME),
            status="done",
            result={"operation_names": ["op-new"], "media_ids": ["media-new"]},
        ))
        s.commit()
    out = _unresolved_video_operations(node_id, current=_SAME)
    assert out is not None
    assert "operation_names" not in out, "walked back to a superseded attempt"
    assert out["adopt_media_ids"] == ["media-new"]


def test_the_model_key_travels_so_the_review_loop_can_read_it():
    """The re-poll result goes through the same settle path, and the loop asks
    whether the key that ran was free before it re-runs a clip unasked."""
    node_id = _node_with_request(result={
        "operation_names": ["op-1"],
        "media_ids": [None],
        "model_key": "veo_3_1_i2v_lite_low_priority",
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll["model_key"] == "veo_3_1_i2v_lite_low_priority"


# ── the handler ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_poll_handler_waits_without_dispatching_anything(monkeypatch):
    """No captcha, no submit, no credits: it re-enters the poll loop with names
    that are already running at Google."""
    from flowboard.worker import processor as proc

    monkeypatch.setattr(proc, "VIDEO_POLL_INTERVAL_S", 0.01)
    asked: dict = {}

    class FakeSDK:
        async def gen_video(self, **kw):  # pragma: no cover - must never run
            raise AssertionError("a re-poll must not dispatch")

        def remember_operations(self, names, project_id):
            # Part of the interface the handler calls: it re-seeds the project for
            # operations this process did not dispatch, which is what makes a
            # re-poll work at all after an agent restart.
            asked["project_id"] = project_id

        async def check_async(self, op_names, workflows=None):
            asked["names"] = list(op_names)
            asked["workflows"] = workflows
            return {"operations": [{
                "name": op_names[0],
                "done": True,
                "media_entries": [{"media_id": "v1", "url": "http://x/v"}],
            }]}

    monkeypatch.setattr(proc, "get_flow_sdk", lambda: FakeSDK())
    monkeypatch.setattr(proc.media_service, "ingest_urls", lambda e: None)

    result, err = await proc._handle_poll_video({
        "operation_names": ["op-1"],
        "workflows": [{"name": "op-1", "primary_media_id": "m-1"}],
        "model_key": "abra_t2v_4s",
    })
    assert err is None, result
    assert result["media_ids"] == ["v1"]
    assert asked["names"] == ["op-1"]
    assert asked["workflows"] == [{"name": "op-1", "primary_media_id": "m-1"}]


@pytest.mark.asyncio
async def test_the_poll_handler_refuses_an_empty_name_list():
    """Otherwise it would poll nothing forever and look like a stuck render."""
    from flowboard.worker import processor as proc

    _, err = await proc._handle_poll_video({"operation_names": []})
    assert err == "missing_operation_names"


def test_the_poll_kind_is_registered_and_its_failure_is_terminal():
    """`missing_operation_names` is one of our own validations: it must never
    classify as an auth failure, because the request endpoint is
    unauthenticated and three of those trip the account breaker."""
    from flowboard.worker.processor import _DEFAULT_HANDLERS, classify_error

    assert "poll_video" in _DEFAULT_HANDLERS
    assert classify_error("missing_operation_names") == "terminal"


# ── the executor branch ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_executor_re_polls_a_stranded_batch_instead_of_dispatching(
    client, monkeypatch
):
    """The end-to-end money property: RUN Lỗi on a timed-out clip must not
    create a second render.

    Driven through `run_pipeline` rather than the helpers above, because the
    decision lives in the executor: everything else could be correct and still
    bill twice if the branch is never taken.
    """
    import asyncio

    from flowboard.db.models import PipelineRun
    from flowboard.services import flow_sdk, pipeline_executor
    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    board = client.post("/api/boards", json={"name": "repoll run"}).json()
    with get_session() as s:
        from flowboard.db.models import BoardFlowProject

        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()

    image = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image",
        "data": {"prompt": "a cat", "mediaId": "m-still"},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video",
        "data": {"prompt": "move", "videoQuality": "lite"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": image["id"],
        "target_id": video["id"], "kind": "media", "target_port": "start_frame",
    })

    # The previous attempt: two renders started, neither collected.
    with get_session() as s:
        s.add(Request(
            node_id=video["id"], type="gen_video", params={},
            status="timeout", error="timeout_waiting_video",
            result={
                "operation_names": ["op-1", "op-2"],
                "media_ids": [None, None],
                "op_errors": {
                    "op-1": "timeout_waiting_video",
                    "op-2": "timeout_waiting_video",
                },
                "model_key": "veo_3_1_i2v_lite",
            },
        ))
        s.commit()

    dispatched: list[str] = []

    class _Recorder:
        async def gen_image(self, **kw):
            dispatched.append("gen_image")
            return {"raw": {}, "media_ids": ["m-still"], "media_entries": []}

        async def gen_video(self, **kw):  # pragma: no cover - must not run
            dispatched.append("gen_video")
            raise AssertionError("a stranded batch was re-dispatched")

        async def check_async(self, op_names, workflows=None):
            return {"operations": [
                {"name": n, "done": True,
                 "media_entries": [{"media_id": f"v-{n}", "url": "http://x/v"}]}
                for n in op_names
            ]}

    monkeypatch.setattr(flow_sdk, "_sdk", _Recorder())
    monkeypatch.setattr(proc, "get_flow_sdk", lambda: flow_sdk._sdk)
    monkeypatch.setattr(proc, "VIDEO_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(proc.media_service, "ingest_urls", lambda e: None)

    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(
            rid, request_timeout_s=5.0, poll_interval_s=0.05
        )
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)

    assert "gen_video" not in dispatched
    with get_session() as s:
        kinds = [
            r.type for r in s.exec(
                select(Request).where(Request.node_id == video["id"])
            ).all()
        ]
    assert "poll_video" in kinds, kinds
    assert kinds.count("gen_video") == 1, "a second generation was created"


# ── an edited node is a different render ─────────────────────────────────


def _timed_out_node(params: dict) -> int:
    return _node_with_request(params=params, result={
        "operation_names": ["op-1"],
        "media_ids": [None],
        "op_errors": {"op-1": "timeout_waiting_video"},
    })


def test_editing_the_prompt_dispatches_instead_of_returning_the_old_render():
    r"""The headline case, and nothing compared it before.

    `_unresolved_video_operations` keyed on `node_id` and read only
    `row.result` — `grep "\.params" pipeline_executor.py` returned zero hits, and
    no fingerprint helper existed anywhere. So a user who reworded a prompt and
    pressed RUN Lỗi got the OLD clip back under the new text, with nothing on the
    card to say so.

    Worse than the wrong clip: if that operation never finished at Google, the
    edit could never be dispatched. Every press re-polled the same dead operation
    for the full budget, and there is no force-dispatch in the route, the
    executor or the UI — the only way out was deleting the node.
    """
    node_id = _timed_out_node({"prompt": "một con mèo"})
    assert _unresolved_video_operations(
        node_id, current={"prompt": "một con chó"}
    ) is None


def test_an_unchanged_prompt_still_re_polls():
    """The other half. Re-polling a render already paid for is the whole point of
    this branch, so the comparison must not cost us that."""
    node_id = _timed_out_node({"prompt": "một con mèo"})
    repoll = _unresolved_video_operations(node_id, current={"prompt": "một con mèo"})
    assert repoll is not None
    assert repoll["operation_names"] == ["op-1"]


def test_trailing_whitespace_is_not_a_different_clip():
    node_id = _timed_out_node({"prompt": "một con mèo"})
    assert _unresolved_video_operations(
        node_id, current={"prompt": "một con mèo  \n"}
    ) is not None


@pytest.mark.parametrize("field,was,now", [
    ("video_quality", "lite", "fast"),
    ("duration_s", 4, 8),
    ("resolution", "720p", "360p"),
    ("aspect_ratio", "VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"),
    ("start_media_id", "m-old", "m-new"),
])
def test_changing_what_gets_rendered_dispatches_again(field, was, now):
    """Each of these selects a different model key, length, shape or source
    frame. Handing back the old operation would answer the previous question."""
    node_id = _timed_out_node({"prompt": "giữ nguyên", field: was})
    assert _unresolved_video_operations(
        node_id, current={"prompt": "giữ nguyên", field: now}
    ) is None


def test_a_field_the_old_row_never_stored_does_not_force_a_second_charge():
    """The asymmetry, pinned.

    Rows written before this comparison existed carry fewer params. Reading a
    missing field as "changed" would re-dispatch — and bill — every one of them,
    which is the exact failure this branch prevents. So absent means unknown,
    and unknown means keep re-polling.
    """
    node_id = _timed_out_node({"prompt": "giữ nguyên"})
    assert _unresolved_video_operations(
        node_id, current={"prompt": "giữ nguyên", "resolution": "720p",
                          "duration_s": 8, "video_quality": "lite"}
    ) is not None


# ── a re-poll must not drop the clips that already landed ────────────────


def test_a_partly_landed_batch_keeps_its_finished_clip_and_its_slot_order():
    """Re-polling only the open subset used to overwrite the whole node.

    `_handle_poll_video` polls the operations still open, so its `media_ids` is
    positional over THAT subset, while `_settle_generation_node` assigns
    `mediaIds` wholesale. A 3-variant batch whose slot 2 had landed came back as
    `[None, "media-B"]`: the clip in slot 2 — already paid for — disappeared from
    the node, and slots 0/1 no longer matched the source images they came from,
    the exact property `_poll_video_dispatch`'s docstring says must hold.
    """
    node_id = _node_with_request(result={
        "operation_names": ["op-a", "op-b", "op-c"],
        "media_ids": [None, None, "media-c"],
        "op_errors": {"op-a": "timeout_waiting_video", "op-b": "timeout_waiting_video"},
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None
    assert repoll["operation_names"] == ["op-a", "op-b"]
    # The slots those two occupy, and what is already on the node beside them.
    assert repoll["open_slots"] == [0, 1]
    assert repoll["landed_media_ids"] == [None, None, "media-c"]


def test_the_poll_result_is_rebuilt_against_the_original_slots():
    """The handler side of the same property, end to end over the merge."""
    import asyncio

    from flowboard.worker import processor

    async def fake_dispatch(sdk, dispatch, request_id):
        # Only op-b came back this round; positional over the OPEN subset.
        return {"media_ids": [None, "media-b"], "operation_names": dispatch[
            "operation_names"]}, None

    original = processor._poll_video_dispatch
    processor._poll_video_dispatch = fake_dispatch
    try:
        out, err = asyncio.run(processor._handle_poll_video({
            "operation_names": ["op-a", "op-b"],
            "open_slots": [0, 1],
            "landed_media_ids": [None, None, "media-c"],
        }))
    finally:
        processor._poll_video_dispatch = original
    assert err is None
    assert out["media_ids"] == [None, "media-b", "media-c"]


def test_a_failed_first_slot_does_not_stamp_a_null_media_id():
    """`if media_ids:` is true for `[None, "media-b"]`, so the shared settle wrote
    `mediaId = None` and still reported the node `done`. Downstream readers want a
    `str`, so the next node failed `missing_upstream_image` against a node showing
    success. The Veo path beside it guards on the value, and the shared helper's
    docstring claims the two cannot drift."""
    from flowboard.db.models import Request as Req
    from flowboard.services.pipeline_executor import _settle_generation_node

    node_id = _node_with_request()
    settled = Req(node_id=node_id, type="poll_video", params={}, status="done",
                  result={"media_ids": [None, "media-b"]})
    _settle_generation_node(node_id, settled, set(), {})
    with get_session() as s:
        data = s.exec(select(Node).where(Node.id == node_id)).one().data or {}

    # Both slots are kept, including the hole — losing it would shift the rest.
    assert data["mediaIds"] == [None, "media-b"]
    # And nothing was written into `mediaId` as if a null were a media id. Asked
    # as a single positive assertion: an absent key and a real id both pass, a
    # stamped `None` does not.
    assert isinstance(data.get("mediaId"), str) or "mediaId" not in data


# ── media that landed after the executor gave up is not bought twice ─────


def test_a_finished_request_the_node_never_received_is_adopted_not_re_dispatched():
    """The second of the three double-charge paths.

    The worker writes media onto the Request row; the only code that copies it
    onto a node is the executor, which by then has stamped the node `error` and
    moved on. So a clip that landed after `_await_request` timed out sat finished
    and PAID FOR on a row nobody read — and because its operations were all
    resolved, this function answered "nothing to resume", which let the caller
    dispatch a fresh set.

    `_cancel_if_queued` was supposed to prevent exactly this, and it does — for
    the `queued` half only. A row already `running` at the timeout is a call
    sitting at Google, and it finishes.
    """
    node_id = _node_with_request(status="done", error=None, result={
        "operation_names": ["op-1", "op-2"],
        "media_ids": ["media-1", "media-2"],
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None, "reported nothing to resume — the caller would pay again"
    assert repoll["adopt_media_ids"] == ["media-1", "media-2"]
    assert "operation_names" not in repoll, "adoption must not enqueue another poll"


def test_stop_then_run_errors_adopts_the_clip_that_arrived_after_the_stop():
    """`stop_board` leaves a `running` row alone by design, so the worker polls it
    to completion — while `task.cancel()` kills the executor with `CancelledError`,
    which the `except asyncio.TimeoutError` around the wait does not catch, so the
    settle never runs. Press Stop anywhere inside the ~10-minute poll window and
    the render usually lands afterwards. Unlike the timeout case this is not a
    narrow race."""
    node_id = _node_with_request(status="done", error=None, result={
        "operation_names": ["op-1"],
        "media_ids": ["media-1"],
    })
    with get_session() as s:
        node = s.exec(select(Node).where(Node.id == node_id)).one()
        node.status = "error"
        node.data = {**(node.data or {}), "error": "stopped_by_user"}
        s.add(node)
        s.commit()
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None
    assert repoll["adopt_media_ids"] == ["media-1"]


def test_a_request_that_finished_with_no_media_is_still_dispatched():
    """Adoption must not swallow a genuine failure: nothing landed, so there is
    nothing to claim and the node does need a real dispatch."""
    node_id = _node_with_request(status="failed", error=None, result={
        "operation_names": ["op-1"],
        "media_ids": [None],
        "op_errors": {"op-1": "PUBLIC_ERROR_UNSAFE_GENERATION"},
    })
    assert _unresolved_video_operations(node_id, current=_SAME) is None


@pytest.mark.asyncio
async def test_the_executor_dispatches_when_only_a_SETTING_changed(client, monkeypatch):
    """The prompt is untouched; the lane is not.

    A comparison built on the prompt alone would call this "the same render" and
    hand back the old clip on the lane the user just moved away from. The executor
    therefore has to pass the settings-derived shape, not just the text — and that
    is only observable from here, because `_unresolved_video_operations` cannot see
    what its caller chose to tell it.
    """
    import asyncio

    from flowboard.db.models import BoardFlowProject, PipelineRun
    from flowboard.services import flow_sdk, pipeline_executor
    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    board = client.post("/api/boards", json={"name": "repoll setting"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()

    image = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image",
        "data": {"prompt": "a cat", "mediaId": "m-still"},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video",
        # The node now asks for `lite`...
        "data": {"prompt": "move", "videoQuality": "lite"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": image["id"],
        "target_id": video["id"], "kind": "media", "target_port": "start_frame",
    })

    with get_session() as s:
        s.add(Request(
            node_id=video["id"], type="gen_video",
            # ...while the stranded attempt rendered `fast`. Same prompt.
            params={"prompt": "move", "video_quality": "fast"},
            status="timeout", error="timeout_waiting_video",
            result={
                "operation_names": ["op-1"],
                "media_ids": [None],
                "op_errors": {"op-1": "timeout_waiting_video"},
                "model_key": "veo_3_1_i2v_s_fast_ultra",
            },
        ))
        s.commit()

    dispatched: list[str] = []

    class _Recorder:
        async def gen_image(self, **kw):
            return {"raw": {}, "media_ids": ["m-still"], "media_entries": []}

        # Both families record, because the property under test is "a dispatch
        # happened carrying the NEW lane" — not which family served it.
        async def gen_video(self, **kw):
            dispatched.append(kw.get("video_quality") or "?")
            return {"raw": {}, "operation_names": ["op-new"],
                    "model_key": "veo_3_1_i2v_lite"}

        async def gen_video_text(self, **kw):
            dispatched.append(kw.get("video_quality") or "?")
            return {"raw": {}, "operation_names": ["op-new"],
                    "model_key": "veo_3_1_t2v_lite"}

        async def check_async(self, op_names, workflows=None):
            return {"operations": [
                {"name": n, "done": True,
                 "media_entries": [{"media_id": f"v-{n}", "url": "http://x/v"}]}
                for n in op_names
            ]}

    monkeypatch.setattr(flow_sdk, "_sdk", _Recorder())
    monkeypatch.setattr(proc, "get_flow_sdk", lambda: flow_sdk._sdk)
    monkeypatch.setattr(proc, "VIDEO_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(proc.media_service, "ingest_urls", lambda e: None)

    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(
            rid, request_timeout_s=5.0, poll_interval_s=0.05
        )
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)

    assert dispatched == ["lite"], (
        "re-polled the old `fast` render for a node that now asks for `lite`"
    )


def test_the_repoll_carries_the_project_the_operations_were_made_in():
    """Without it, a re-poll after an agent restart is a ten-minute no-op.

    `_op_projects` is in-memory and filled at dispatch, so a fresh process knows
    nothing about an operation an earlier run created. `_find_operation_media`
    then never asks the listing and every round reports pending until the budget
    runs out — on a clip that was already paid for. "The run died, I restarted the
    agent, press RUN Lỗi" is the headline recovery path, and it was dead.

    Read from the ROW, not from the board: Flow scopes media ids to the project
    they were made in, and a board's binding can change.
    """
    node_id = _node_with_request(
        params={"prompt": "move", "project_id": "proj-of-the-operations"},
        result={
            "operation_names": ["op-1"],
            "media_ids": [None],
            "op_errors": {"op-1": "timeout_waiting_video"},
        },
    )
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None
    assert repoll["project_id"] == "proj-of-the-operations"


def test_the_poll_handler_reseeds_the_project_before_looking_for_media():
    """The handler half: the params are useless unless the SDK is told."""
    import asyncio

    from flowboard.worker import processor

    seeded: list[tuple] = []

    class _Sdk:
        def remember_operations(self, names, project_id):
            seeded.append((tuple(names), project_id))

    async def fake_dispatch(sdk, dispatch, request_id):
        return {"media_ids": ["m-1"]}, None

    orig_sdk, orig_poll = processor.get_flow_sdk, processor._poll_video_dispatch
    processor.get_flow_sdk = lambda: _Sdk()
    processor._poll_video_dispatch = fake_dispatch
    try:
        asyncio.run(processor._handle_poll_video({
            "operation_names": ["op-1"],
            "project_id": "proj-9",
        }))
    finally:
        processor.get_flow_sdk = orig_sdk
        processor._poll_video_dispatch = orig_poll

    assert seeded == [(("op-1",), "proj-9")]


def test_an_operation_with_no_recorded_error_is_re_polled_on_a_given_up_row():
    """Where `gave_up` is actually load-bearing, and nothing covered it.

    The per-operation check catches an op whose `op_errors` entry says
    `timeout_waiting_video`. But a poll that ran out of budget can record entries
    for some operations and not others — the dict is built as answers arrive — so
    an op with NO entry falls through to `gave_up`, which reads the row's status.

    Without that fallback the op is neither landed, nor terminally refused, nor
    re-polled: a render already paid for is silently abandoned. Pinned because
    every other timeout test happens to record an entry for every operation, so
    dropping `timeout` from `_GAVE_UP_STATUSES` left the whole suite green.
    """
    node_id = _node_with_request(result={
        "operation_names": ["op-1", "op-2"],
        "media_ids": [None, None],
        # op-2 never got an entry: the budget ran out before its answer arrived.
        "op_errors": {"op-1": "timeout_waiting_video"},
    })
    repoll = _unresolved_video_operations(node_id, current=_SAME)
    assert repoll is not None
    assert repoll["operation_names"] == ["op-1", "op-2"], (
        "op-2 was abandoned: paid for, not landed, not refused, not re-polled"
    )
