"""Plan + PipelineRun routes.

The chat handler creates ``Plan`` rows in ``draft`` status. This module exposes:

- ``GET  /api/plans/{plan_id}`` — read-only fetch.
- ``GET  /api/boards/{board_id}/plan`` — the plan a board is run through, if
  it has one. An imported workflow gets one at import time; a hand-built
  board has none until the chat writes one.
- ``POST /api/plans/{plan_id}/run`` — materialise the plan onto the canvas
  (auto-laid-out Node + Edge rows) and kick off background execution.
- ``GET  /api/pipeline-runs/{run_id}`` — status row for the frontend poll.

POST is idempotent: if a run for the plan is already ``pending`` or
``running``, we return the existing row instead of starting a second.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.db import get_session
from flowboard.db.models import Board, PipelineRun, Plan
from flowboard.services.pipeline_executor import materialize_plan, run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plans"])

# Track in-flight executor tasks so we can avoid double-spawning if Python
# garbage-collects the future. asyncio.create_task returns Task objects that
# the event loop keeps alive while running, but holding a strong ref here is
# defence-in-depth and gives tests a hook.
_active_tasks: dict[int, asyncio.Task] = {}


@router.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    with get_session() as s:
        plan = s.get(Plan, plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
        return plan


@router.get("/api/boards/{board_id}/plan")
def get_board_plan(board_id: int):
    """The plan that runs this board, or null.

    Null is a normal answer, not an error: most boards are built by hand and
    are run a node at a time. The caller shows "nothing to run here" rather
    than an error banner.
    """
    from sqlmodel import select

    with get_session() as s:
        plan = s.exec(
            # Newest wins: re-importing a template writes a fresh plan, and
            # the stale one still points at the previous board's nodes.
            select(Plan)
            .where(Plan.board_id == board_id)
            .order_by(Plan.id.desc())  # type: ignore[attr-defined]
        ).first()
        return plan


class PlanScope(BaseModel):
    """Which nodes the next run is allowed to dispatch.

    Absent or empty means the whole board, which is what every existing caller
    sends. A subset is what the canvas agent needs to offer "run just these
    three" without the user paying for the twelve that already succeeded.
    """

    node_ids: Optional[list[int]] = None


@router.post("/api/boards/{board_id}/plan")
def ensure_board_plan(board_id: int, body: Optional[PlanScope] = None):
    """The plan for running this board, created or refreshed to match it.

    Two problems this solves, both of which would otherwise run the wrong
    node set:

    * A board built by hand has no plan at all, so "run the whole board"
      could never work — including for the post-production nodes now in the
      palette, which have no per-node run button.
    * An imported board's plan lists the nodes that existed at import. Add a
      node afterwards and it would be skipped, silently, forever.

    So the node list is rewritten from the board every time. This creates
    rows; it dispatches nothing.

    A `node_ids` subset scopes the run without touching the node list: the
    executor still loads the whole graph so wires resolve, and only dispatches
    inside the subset. Every id must belong to this board — an id that does not
    would match nothing in the executor's gate, so every node would be skipped
    and Run would report success having done nothing at all.
    """
    from sqlmodel import select

    from flowboard.db.models import Board, Node

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")
        node_ids = [
            n.id
            for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
            if n.id is not None
        ]
        scope = list(dict.fromkeys(body.node_ids or [])) if body else []
        stranger = [i for i in scope if i not in set(node_ids)]
        if stranger:
            raise HTTPException(
                400, f"node_ids not on board {board_id}: {sorted(stranger)}"
            )
        plan = s.exec(
            select(Plan)
            .where(Plan.board_id == board_id)
            .order_by(Plan.id.desc())  # type: ignore[attr-defined]
        ).first()

        if plan is None:
            plan = Plan(board_id=board_id, status="approved", spec={})
        # Replace the whole spec dict rather than mutating it: SQLModel's JSON
        # column does not track in-place changes, so a mutated dict would not
        # be written back and the stale node list would survive.
        plan.spec = {
            **(plan.spec or {}),
            "nodes": [],
            "edges": [],
            "_materialized_node_ids": node_ids,
            # Always rewritten, never merged: a previous scoped run ("run only
            # what failed") left a subset here, and leaving it would make the
            # NEXT full run silently skip every node that had succeeded — a Run
            # button that runs a third of the board and reports done. So an
            # absent scope means the whole board, explicitly.
            "_run_only_node_ids": scope,
        }
        # A finished run leaves the plan `done`; re-running needs it back.
        if plan.status in ("done", "failed"):
            plan.status = "approved"
        s.add(plan)
        s.commit()
        s.refresh(plan)
        return plan


@router.post("/api/plans/{plan_id}/run")
async def run_plan(plan_id: int):
    with get_session() as s:
        plan = s.get(Plan, plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")

        # Idempotency: if there's already an in-progress run for this plan,
        # return it instead of starting another.
        from sqlmodel import select

        existing = s.exec(
            select(PipelineRun)
            .where(PipelineRun.plan_id == plan_id)
            .where(PipelineRun.status.in_(("pending", "running")))  # type: ignore[attr-defined]
        ).first()
        if existing is not None:
            return existing

        # Materialise within this same transaction.
        try:
            summary = materialize_plan(s, plan_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        run = PipelineRun(plan_id=plan_id, status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id
        assert rid is not None
        logger.info(
            "plan %s: materialised %d node(s), pipeline run %s scheduled",
            plan_id, len(summary.get("node_ids") or []), rid,
        )
        run_row = run

    # Spawn the executor in the background.
    task = asyncio.create_task(run_pipeline(rid), name=f"pipeline-run-{rid}")
    _active_tasks[rid] = task

    def _cleanup(t: asyncio.Task) -> None:
        _active_tasks.pop(rid, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.exception("pipeline run %s crashed", rid, exc_info=exc)
            # Stamp the run as failed so the frontend doesn't hang.
            with get_session() as s2:
                row = s2.get(PipelineRun, rid)
                if row is not None and row.status not in ("done", "failed"):
                    row.status = "failed"
                    row.error = f"crash:{exc!r}"[:500]
                    row.finished_at = datetime.now(timezone.utc)
                    s2.add(row)
                    s2.commit()

    task.add_done_callback(_cleanup)
    return run_row


@router.get("/api/pipeline-runs/{run_id}")
def get_pipeline_run(run_id: int):
    with get_session() as s:
        row = s.get(PipelineRun, run_id)
        if row is None:
            raise HTTPException(404, "pipeline run not found")
        return row


# ── re-run only what failed, and stop everything ──────────────────────


@router.post("/api/boards/{board_id}/rerun-failed")
def rerun_failed(board_id: int):
    """Run only the nodes that errored, keeping every finished result.

    The alternative — running the board again — re-generates work that
    already succeeded, which on a fifteen-node board is most of the cost. The
    executor keeps the untouched nodes loaded so wires into the re-run set
    still resolve to their upstream media; they are simply not dispatched.

    Nodes stuck in `queued` count as failed here. A queue entry left behind
    by a run that died is indistinguishable from one that never started, and
    the harmless reading is to try it again.
    """
    from sqlmodel import select

    from flowboard.db.models import Board, Node

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")
        nodes = list(s.exec(select(Node).where(Node.board_id == board_id)).all())
        all_ids = [n.id for n in nodes if n.id is not None]
        retry_ids = [
            n.id for n in nodes
            if n.id is not None and n.status in ("error", "queued")
        ]
        if not retry_ids:
            raise HTTPException(400, "không có node nào đang lỗi")

        plan = s.exec(
            select(Plan)
            .where(Plan.board_id == board_id)
            .order_by(Plan.id.desc())  # type: ignore[attr-defined]
        ).first()
        if plan is None:
            plan = Plan(board_id=board_id, status="approved", spec={})
        plan.spec = {
            **(plan.spec or {}),
            "nodes": [],
            "edges": [],
            "_materialized_node_ids": all_ids,
            "_run_only_node_ids": retry_ids,
        }
        if plan.status in ("done", "failed"):
            plan.status = "approved"
        # Clear the error before re-running: a node left stamped `error`
        # while it is being retried reads as "failed again" on the canvas
        # for the whole duration of the attempt.
        for node in nodes:
            if node.id in retry_ids:
                node.status = "idle"
                node.data = {k: v for k, v in (node.data or {}).items() if k != "error"}
                s.add(node)
        s.add(plan)
        s.commit()
        s.refresh(plan)
        logger.info("board %s: re-running %d failed node(s)", board_id, len(retry_ids))
        return plan


@router.post("/api/boards/{board_id}/stop")
def stop_board(board_id: int):
    """Stop everything this board has in flight: run, queue and requests.

    Three layers, because stopping only one leaves the others going and the
    board keeps costing money after the user pressed stop:

    * every node left `running` or `queued`, stamped `error` so the board is
      not frozen and RUN Lỗi can retry it;
    * the executor task, cancelled;
    * the `PipelineRun` rows, stamped so the frontend poll stops waiting;
    * every `queued` Request, cancelled before the worker picks it up.

    Requests already `running` are NOT cancelled — that call is with Google
    and has been paid for. Throwing away its result would spend the credit
    and keep nothing.
    """
    from sqlmodel import select

    from flowboard.db.models import Board, Node, Request

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")

        plan_ids = [
            p.id for p in s.exec(select(Plan).where(Plan.board_id == board_id)).all()
            if p.id is not None
        ]
        runs = list(
            s.exec(
                select(PipelineRun).where(
                    PipelineRun.plan_id.in_(plan_ids),  # type: ignore[attr-defined]
                    PipelineRun.status.in_(("pending", "running")),  # type: ignore[attr-defined]
                )
            ).all()
        ) if plan_ids else []

        node_ids = [
            n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
            if n.id is not None
        ]
        requests = list(
            s.exec(
                select(Request).where(
                    Request.node_id.in_(node_ids),  # type: ignore[attr-defined]
                    Request.status == "queued",
                )
            ).all()
        ) if node_ids else []

        for run in runs:
            run.status = "failed"
            run.error = "stopped_by_user"
            run.finished_at = datetime.now(timezone.utc)
            s.add(run)
        for req in requests:
            # "canceled", one L — the exact spelling the worker re-checks for
            # in `_process_one` and `_handle_gen_video`. A near-miss here
            # writes a status nothing recognises, and the worker would pick
            # the request up and dispatch it anyway.
            req.status = "canceled"
            req.error = "stopped_by_user"
            req.finished_at = datetime.now(timezone.utc)
            s.add(req)

        # The fourth layer, and the one that was missing: the nodes. A node
        # caught mid-dispatch stayed `running` for good — the plan never
        # finished, RUN Lỗi found no `error` node and refused with 400, and the
        # only way out was to edit the database. `error` is the honest status
        # because the node did not produce what it was asked for, and it is
        # also the status RUN Lỗi looks for, so Stop then Retry works.
        stopped_nodes = 0
        for node in s.exec(
            select(Node).where(
                Node.board_id == board_id,
                Node.status.in_(("running", "queued")),  # type: ignore[attr-defined]
            )
        ).all():
            node.status = "error"
            node.data = {**(node.data or {}), "error": "stopped_by_user"}
            s.add(node)
            stopped_nodes += 1

        s.commit()
        stopped_runs = [r.id for r in runs]
        cancelled = len(requests)

    # Cancel the executor tasks last: the rows are already stamped, so a task
    # that notices the cancellation mid-loop cannot re-stamp them as running.
    for rid in stopped_runs:
        task = _active_tasks.get(rid)
        if task is not None and not task.done():
            task.cancel()

    logger.info(
        "board %s: stopped %d run(s), cancelled %d queued request(s), "
        "released %d node(s)",
        board_id, len(stopped_runs), cancelled, stopped_nodes,
    )
    return {
        "runsStopped": len(stopped_runs),
        "requestsCancelled": cancelled,
        "nodesReleased": stopped_nodes,
    }


# ── one of the seven shapes → an actual board ─────────────────────────


class ArchetypeBoardBody(BaseModel):
    #: One of `archetypes.ARCHETYPES`. Passed instead of a brief on purpose:
    #: classification is a separate, free call (`POST /api/prompt/archetypes/
    #: classify`) and its own answer can be `ask`, which must not silently
    #: become a board the user has to take apart.
    key: str
    scene_count: int = Field(default=3, ge=1, le=12)
    aspect: str = ""
    quality: str = ""


@router.post("/api/boards/{board_id}/archetype")
def build_archetype_board(board_id: int, body: ArchetypeBoardBody) -> dict:
    """Place the nodes and wires of one board shape. Dispatches nothing.

    Generation nodes land with NO prompt: the shape is what the archetype
    knows, and a prompt invented here would be a paid dispatch nobody asked
    for. The run refuses an empty node the same way it refuses a hand-built
    one, so the board is a scaffold, not a bill.
    """
    from flowboard.services import archetype_boards

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")
        try:
            spec = archetype_boards.build(
                body.key,
                scene_count=body.scene_count,
                aspect=body.aspect,
                quality=body.quality,
            )
        except archetype_boards.ArchetypeBoardError as exc:
            # 422, not 500: the shape is real, this build just cannot place it
            # (Motion Control needs an endpoint that does not exist here) or
            # the key is not one of the seven.
            raise HTTPException(422, str(exc)) from None

        plan = Plan(board_id=board_id, status="approved", spec=spec)
        s.add(plan)
        s.commit()
        s.refresh(plan)
        result = materialize_plan(s, plan.id)
        # `materialize_plan` says "caller commits", and it means it: without
        # this the route answered "8 node" and the board stayed empty.
        s.commit()
        plan_id = plan.id

    return {
        "planId": plan_id,
        "key": body.key,
        "nodes": len(result.get("node_ids") or []),
        "edges": result.get("edges_created", len(spec["edges"])),
    }


# ── prompt list → many generation nodes ───────────────────────────────


class FanOutBody(BaseModel):
    prompt_node_id: int


@router.get("/api/boards/{board_id}/fan-out/{prompt_node_id}")
def preview_fan_out(board_id: int, prompt_node_id: int) -> dict:
    """What the fan-out would do. Reads only.

    Every line becomes a billable node, so the count belongs in front of the
    user before the canvas changes rather than in the cost dialog afterwards.
    """
    from flowboard.services import fan_out as fan_out_service

    with get_session() as s:
        try:
            return fan_out_service.plan_fan_out(s, board_id, prompt_node_id)
        except fan_out_service.FanOutError as exc:
            raise HTTPException(exc.status, exc.message) from None


@router.post("/api/boards/{board_id}/fan-out")
def do_fan_out(board_id: int, body: FanOutBody) -> dict:
    """Give each line of a prompt node its own generation node.

    Creates rows and dispatches nothing. Deliberately a separate step from
    running: fanning out during execution would make the cost dialog quote a
    number that the run then exceeds.
    """
    from flowboard.services import fan_out as fan_out_service

    with get_session() as s:
        try:
            return fan_out_service.fan_out(s, board_id, body.prompt_node_id)
        except fan_out_service.FanOutError as exc:
            raise HTTPException(exc.status, exc.message) from None

# ── Reading one narration segment again ───────────────────────────────
#
# A preview GET and an action POST, the same pair fan-out uses above, and for the
# same reason: this one spends text-to-speech quota, so the user sees what it will
# read before anything is dispatched.


class RereadBody(BaseModel):
    node_id: int


@router.get("/api/boards/{board_id}/narration/{node_id}")
def preview_narration_reread(board_id: int, node_id: int) -> dict:
    """Which segments are missing, which are kept, and what re-reading costs.

    Reads only. Nothing is dispatched and no quota is spent.
    """
    from flowboard.services import narration_reread

    with get_session() as s:
        try:
            return narration_reread.plan_reread(s, board_id, node_id).as_dict()
        except narration_reread.RereadError as exc:
            raise HTTPException(exc.status, exc.message) from None


@router.post("/api/boards/{board_id}/narration")
async def reread_narration(board_id: int, body: RereadBody) -> dict:
    """Read the failed segments again, reusing the audio already paid for.

    The request that goes to the worker is built HERE, out of the board, not out
    of the body: the body names a node and nothing else. These endpoints are
    unauthenticated by explicit decision, so a route that accepted a script and a
    voice would be a paid text-to-speech call anyone on this machine could make.

    Goes through a real `Request` row rather than calling the op inline, which is
    what buys the spend record, the queue, cancellation, and the node landing —
    all of it already built.
    """
    from flowboard.services import narration_reread
    from flowboard.services.pipeline_executor import (
        _REQUEST_POLL_INTERVAL_S,
        _await_request,
        _cancel_if_queued,
        _create_request_row,
        _default_request_timeout_s,
        _stamp_node_status,
    )
    from flowboard.worker.processor import get_worker

    with get_session() as s:
        try:
            plan = narration_reread.plan_reread(s, board_id, body.node_id)
        except narration_reread.RereadError as exc:
            raise HTTPException(exc.status, exc.message) from None

    request_id = _create_request_row(body.node_id, "postprod", plan.params)
    _stamp_node_status(body.node_id, "running")
    get_worker().enqueue(request_id)
    try:
        settled = await _await_request(
            request_id,
            timeout_s=_default_request_timeout_s(),
            poll_s=_REQUEST_POLL_INTERVAL_S,
        )
    except asyncio.TimeoutError:
        _cancel_if_queued(request_id)
        _stamp_node_status(body.node_id, "error", error="timeout_narrate_reread")
        raise HTTPException(504, "Đọc lại quá lâu — xem lại rồi thử tiếp.") from None

    if settled.status != "done":
        error = settled.error or "narrate_reread_failed"
        _stamp_node_status(body.node_id, "error", error=error)
        raise HTTPException(502, error)

    result = settled.result or {}
    media_ids = result.get("media_ids")
    media_id = media_ids[0] if isinstance(media_ids, list) and media_ids else None
    # `error: None` is load-bearing. `_stamp_node_status` MERGES node data and
    # never clears a key it was not given, so a node that succeeds after a
    # failure keeps showing the old red line — which is exactly the state a
    # re-read exists to leave behind.
    _stamp_node_status(
        body.node_id, "done",
        data_patch={"mediaId": media_id, "mediaIds": [media_id], "error": None},
    )
    return {
        "requestId": request_id,
        "mediaId": media_id,
        "reread": plan.read,
        "kept": plan.kept,
    }

