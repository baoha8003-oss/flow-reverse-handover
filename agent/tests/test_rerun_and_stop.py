"""Re-running only what failed, and stopping a board that is already going.

Both exist because the alternatives cost money. Running the whole board again
to fix one node re-generates everything that already worked, which on a
fifteen-node board is most of the bill. And a Stop button that only cancels
one of the three layers — executor task, run row, queued requests — leaves the
board spending after the user pressed it.

The sharpest edge is in the scoped run: nodes outside the subset must still be
LOADED, because a wire into the re-run set has to resolve to its upstream's
media, but must never be DISPATCHED. Getting that backwards either re-bills
the whole board or re-runs a node with nothing wired to it.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import (
    BoardFlowProject, Node, PipelineRun, Plan, Request,
)
from flowboard.services import pipeline_executor


def _board(client, name="B") -> dict:
    board = client.post("/api/boards", json={"name": name}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    return board


def _node(client, board_id, node_type, **data) -> dict:
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": node_type, "x": 0, "y": 0, "data": data,
    }).json()


def _set_status(node_id: int, status: str, **data) -> None:
    with get_session() as s:
        node = s.get(Node, node_id)
        node.status = status
        node.data = {**(node.data or {}), **data}
        s.add(node)
        s.commit()


# ── RUN Lỗi ───────────────────────────────────────────────────────────


def test_rerun_scopes_the_plan_to_the_failed_nodes(client):
    board = _board(client)
    ok = _node(client, board["id"], "image", title="Xong", prompt="a")
    bad = _node(client, board["id"], "image", title="Lỗi", prompt="b")
    _set_status(ok["id"], "done", mediaId="m-1")
    _set_status(bad["id"], "error", error="quota")

    plan = client.post(f"/api/boards/{board['id']}/rerun-failed").json()
    assert plan["spec"]["_run_only_node_ids"] == [bad["id"]]
    # The full set is still listed: the executor needs the finished node
    # loaded so a wire out of it resolves.
    assert sorted(plan["spec"]["_materialized_node_ids"]) == sorted([ok["id"], bad["id"]])


def test_rerun_clears_the_old_error_before_trying_again(client):
    """A node left stamped `error` while it is being retried reads as
    "failed again" on the canvas for the whole attempt."""
    board = _board(client)
    bad = _node(client, board["id"], "image", title="Lỗi", prompt="b")
    _set_status(bad["id"], "error", error="quota")

    client.post(f"/api/boards/{board['id']}/rerun-failed")
    with get_session() as s:
        node = s.get(Node, bad["id"])
    assert node.status == "idle"
    assert "error" not in (node.data or {})


def test_a_queued_node_counts_as_failed(client):
    """A queue entry left by a run that died is indistinguishable from one
    that never started. Trying it again is the harmless reading."""
    board = _board(client)
    stuck = _node(client, board["id"], "image", title="Kẹt", prompt="b")
    _set_status(stuck["id"], "queued")
    plan = client.post(f"/api/boards/{board['id']}/rerun-failed").json()
    assert plan["spec"]["_run_only_node_ids"] == [stuck["id"]]


def test_a_board_with_nothing_broken_says_so(client):
    board = _board(client)
    good = _node(client, board["id"], "image", title="Xong", prompt="a")
    _set_status(good["id"], "done", mediaId="m-1")
    resp = client.post(f"/api/boards/{board['id']}/rerun-failed")
    assert resp.status_code == 400
    assert "lỗi" in resp.json()["detail"]


def test_a_full_run_after_a_scoped_one_is_full_again(client):
    """The subset is left behind on the plan. Without clearing it, the next
    Run would silently skip everything that had succeeded and report done."""
    board = _board(client)
    ok = _node(client, board["id"], "image", title="Xong", prompt="a")
    bad = _node(client, board["id"], "image", title="Lỗi", prompt="b")
    _set_status(ok["id"], "done", mediaId="m-1")
    _set_status(bad["id"], "error", error="quota")

    client.post(f"/api/boards/{board['id']}/rerun-failed")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    assert plan["spec"]["_run_only_node_ids"] == []


@pytest.mark.asyncio
async def test_a_scoped_run_dispatches_only_the_subset(client, monkeypatch):
    """The behaviour the endpoint is buying. Both nodes are loaded; one is
    dispatched."""
    from flowboard.services import flow_sdk

    dispatched: list[str] = []

    class _Stub:
        async def gen_image(self, **kwargs):
            dispatched.append(kwargs["prompt"])
            return {"raw": {}, "media_ids": ["m-new"], "media_entries": []}

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    board = _board(client)
    ok = _node(client, board["id"], "image", title="Xong", prompt="đã xong")
    bad = _node(client, board["id"], "image", title="Lỗi", prompt="phải chạy lại")
    _set_status(ok["id"], "done", mediaId="m-old")
    _set_status(bad["id"], "error", error="quota")

    plan = client.post(f"/api/boards/{board['id']}/rerun-failed").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(rid, request_timeout_s=5.0, poll_interval_s=0.05)
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

    assert dispatched == ["phải chạy lại"]
    with get_session() as s:
        untouched = s.get(Node, ok["id"])
    assert untouched.status == "done"
    assert untouched.data.get("mediaId") == "m-old", "a finished result was overwritten"


# ── Dừng toàn bộ ──────────────────────────────────────────────────────


def test_stopping_cancels_queued_work_and_stamps_the_run(client):
    board = _board(client)
    node = _node(client, board["id"], "image", title="A", prompt="a")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        s.add(PipelineRun(plan_id=plan["id"], status="running"))
        s.add(Request(node_id=node["id"], type="gen_image", params={}, status="queued"))
        s.commit()

    out = client.post(f"/api/boards/{board['id']}/stop").json()
    assert out == {"runsStopped": 1, "requestsCancelled": 1, "nodesReleased": 0}

    with get_session() as s:
        run = s.exec(select(PipelineRun)).first()
        req = s.exec(select(Request)).first()
    assert run.status == "failed" and run.error == "stopped_by_user"
    # "canceled", one L — the exact spelling the worker re-checks for. A
    # near-miss writes a status nothing recognises and the row gets
    # dispatched anyway.
    assert req.status == "canceled"


def test_stopping_leaves_work_already_in_flight_alone(client):
    """That call is with Google and has been paid for. Throwing away its
    result spends the credit and keeps nothing."""
    board = _board(client)
    node = _node(client, board["id"], "image", title="A", prompt="a")
    with get_session() as s:
        s.add(Request(node_id=node["id"], type="gen_image", params={}, status="running"))
        s.commit()

    out = client.post(f"/api/boards/{board['id']}/stop").json()
    assert out["requestsCancelled"] == 0
    with get_session() as s:
        assert s.exec(select(Request)).first().status == "running"


def test_stopping_a_quiet_board_is_not_an_error(client):
    board = _board(client)
    out = client.post(f"/api/boards/{board['id']}/stop").json()
    assert out == {"runsStopped": 0, "requestsCancelled": 0, "nodesReleased": 0}


def test_stopping_only_touches_this_board(client):
    mine = _board(client, "của tôi")
    other = _board(client, "của người khác")
    other_node = _node(client, other["id"], "image", title="A", prompt="a")
    with get_session() as s:
        s.add(Request(node_id=other_node["id"], type="gen_image", params={}, status="queued"))
        s.commit()

    client.post(f"/api/boards/{mine['id']}/stop")
    with get_session() as s:
        assert s.exec(select(Request)).first().status == "queued"


# ── the queue as a whole ──────────────────────────────────────────────


def test_the_queue_reports_what_it_is_doing(client):
    node = _node(client, _board(client)["id"], "image", title="A", prompt="a")
    with get_session() as s:
        s.add(Request(node_id=node["id"], type="gen_image", params={}, status="queued"))
        s.add(Request(node_id=node["id"], type="gen_image", params={}, status="running"))
        s.commit()
    body = client.get("/api/requests/queue").json()
    assert body["queued"] == 1 and body["running"] == 1
    assert body["paused"] is False


def test_pause_and_resume_flip_the_worker(client):
    from flowboard.worker.processor import get_worker

    assert client.post("/api/requests/queue/pause").json() == {"paused": True}
    assert get_worker().is_paused is True
    assert client.get("/api/requests/queue").json()["paused"] is True
    assert client.post("/api/requests/queue/resume").json() == {"paused": False}
    assert get_worker().is_paused is False


def test_clearing_cancels_queued_rows_and_keeps_them_visible(client):
    """"Clear" rather than "delete": the rows are the record of what was
    asked for. A queue that empties by forgetting leaves the user unable to
    see what they just discarded."""
    node = _node(client, _board(client)["id"], "image", title="A", prompt="a")
    with get_session() as s:
        for _ in range(3):
            s.add(Request(node_id=node["id"], type="gen_image", params={}, status="queued"))
        s.add(Request(node_id=node["id"], type="gen_image", params={}, status="running"))
        s.commit()

    assert client.post("/api/requests/queue/clear").json() == {"cancelled": 3}
    with get_session() as s:
        rows = list(s.exec(select(Request)).all())
    assert len(rows) == 4, "cleared rows must still exist"
    assert sorted(r.status for r in rows) == ["canceled", "canceled", "canceled", "running"]


def test_the_queue_path_is_not_read_as_a_request_id(client):
    """`/queue` sits under the same prefix as `/{request_id}`. Declared in
    the wrong order it answers 422 for a path that looks perfectly fine."""
    assert client.get("/api/requests/queue").status_code == 200


# ── Stop leaves a board you can carry on with ─────────────────────────


def test_stop_releases_a_node_caught_mid_dispatch(client):
    """The fourth layer. Cancelling the run, the queue and the task left the
    node itself `running` for good: the plan never finished, RUN Lỗi found no
    `error` node and refused with 400, and the only way out was the database."""
    board = _board(client)
    node = _node(client, board["id"], "image", title="A", prompt="a")
    _set_status(node["id"], "running")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        s.add(PipelineRun(plan_id=plan["id"], status="running"))
        s.commit()

    out = client.post(f"/api/boards/{board['id']}/stop").json()
    assert out["nodesReleased"] == 1

    with get_session() as s:
        stopped = s.get(Node, node["id"])
    assert stopped.status == "error"
    assert stopped.data.get("error") == "stopped_by_user"


def test_after_stop_run_loi_can_retry_that_node(client):
    """The point of stamping `error` rather than `idle`: it is the status RUN
    Lỗi looks for, so Stop then Retry is a thing a user can actually do."""
    board = _board(client)
    node = _node(client, board["id"], "image", title="A", prompt="a")
    _set_status(node["id"], "running")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        s.add(PipelineRun(plan_id=plan["id"], status="running"))
        s.commit()

    client.post(f"/api/boards/{board['id']}/stop")
    retry = client.post(f"/api/boards/{board['id']}/rerun-failed")
    assert retry.status_code == 200, retry.text
    assert retry.json()["spec"]["_run_only_node_ids"] == [node["id"]]


def test_a_finished_node_is_not_disturbed_by_stop(client):
    """Stop is not a reset. A node that already produced its result keeps it —
    re-running it would spend the credits again."""
    board = _board(client)
    done = _node(client, board["id"], "image", title="Xong", prompt="a")
    _set_status(done["id"], "done", mediaId="m-1")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        s.add(PipelineRun(plan_id=plan["id"], status="running"))
        s.commit()

    out = client.post(f"/api/boards/{board['id']}/stop").json()
    assert out["nodesReleased"] == 0
    with get_session() as s:
        untouched = s.get(Node, done["id"])
    assert untouched.status == "done"
    assert untouched.data.get("mediaId") == "m-1"


@pytest.mark.asyncio
async def test_stop_cancels_the_executor_task_itself(client):
    """The third layer, which no test reached: without it the coroutine keeps
    walking the board after Stop, dispatching the nodes it had not got to yet.
    Cancelling the run row does not stop a coroutine already past that check."""
    from flowboard.routes import plans

    board = _board(client)
    _node(client, board["id"], "image", title="A", prompt="a")
    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="running")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    started = asyncio.Event()

    async def _long_run():
        started.set()
        await asyncio.sleep(30)

    task = asyncio.create_task(_long_run())
    plans._active_tasks[rid] = task
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        client.post(f"/api/boards/{board['id']}/stop")
        # The cancellation has to actually land, not merely be requested.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
    finally:
        plans._active_tasks.pop(rid, None)
        if not task.done():
            task.cancel()
