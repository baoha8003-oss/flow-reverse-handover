from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Node, Request
from flowboard.worker.processor import get_worker

router = APIRouter(prefix="/api/requests", tags=["requests"])

# Read off the model so the fallback cannot drift from the column default.
_model_default = Request.model_fields["max_attempts"].default
DEFAULT_MAX_ATTEMPTS: int = _model_default if isinstance(_model_default, int) else 3


class RequestCreate(BaseModel):
    node_id: Optional[int] = None
    type: str = Field(min_length=1, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


def _retry_budget() -> int:
    """How many counted attempts a new request gets.

    The packaged tool exposes this as ``RETRY_WITH_ERROR`` (ships as 3), so
    the setting has to reach the worker rather than sit in the settings table
    looking authoritative while a hardcoded default decides the real
    behaviour. Falls back to the model default when unset or unusable.
    """
    from flowboard.services import settings_store

    try:
        value = settings_store.get("RETRY_WITH_ERROR")
    except Exception:
        return DEFAULT_MAX_ATTEMPTS
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_MAX_ATTEMPTS
    return value


@router.post("")
def create_request(body: RequestCreate):
    # Resolved before the session opens: reading settings uses a session of
    # its own, and nesting one inside the write below buys nothing.
    budget = _retry_budget()
    with get_session() as s:
        if body.node_id is not None and not s.get(Node, body.node_id):
            raise HTTPException(404, "node not found")
        req = Request(
            node_id=body.node_id,
            type=body.type,
            params=dict(body.params),
            status="queued",
            max_attempts=budget,
        )
        s.add(req)
        s.commit()
        s.refresh(req)
        rid = req.id
        row = req

    assert rid is not None
    get_worker().enqueue(rid)
    return row


# ── the queue as a whole ──────────────────────────────────────────────
#
# Declared above `/{request_id}` on purpose: routes match in definition
# order, so a later `/queue` would be swallowed by the id parameter and
# answer 422 for a path that looks fine.


@router.get("/queue")
def queue_status() -> dict:
    """What the queue is doing, and whether it is taking new work."""
    from sqlmodel import func, select as sql_select

    worker = get_worker()
    with get_session() as s:
        counts = dict(
            s.exec(
                sql_select(Request.status, func.count())  # type: ignore[arg-type]
                .group_by(Request.status)  # type: ignore[attr-defined]
            ).all()
        )
    return {
        "paused": worker.is_paused,
        "queued": int(counts.get("queued", 0)),
        "running": int(counts.get("running", 0)),
        "breaker": worker.breaker_state,
    }


@router.post("/queue/pause")
def pause_queue() -> dict:
    """Stop taking new work off the queue.

    Requests already in flight are left alone: that call is with Google, it
    has been paid for, and dropping it would spend the credit and keep
    nothing. Queued rows stay queued and resume where they were.
    """
    get_worker().pause()
    return {"paused": True}


@router.post("/queue/resume")
def resume_queue() -> dict:
    get_worker().resume()
    return {"paused": False}


@router.post("/queue/clear")
def clear_queue() -> dict:
    """Cancel every queued request. In-flight work is untouched.

    "Clear" rather than "delete": the rows stay, marked canceled, because
    they are the record of what was asked for. A queue that empties by
    forgetting leaves the user unable to see what they just discarded.
    """
    with get_session() as s:
        rows = list(s.exec(select(Request).where(Request.status == "queued")).all())
        for row in rows:
            row.status = "canceled"
            row.error = "cleared_by_user"
            row.finished_at = datetime.now(timezone.utc)
            s.add(row)
        s.commit()
    return {"cancelled": len(rows)}


@router.get("/{request_id}")
def get_request(request_id: int):
    with get_session() as s:
        req = s.get(Request, request_id)
        if req is None:
            raise HTTPException(404, "request not found")
        return req


@router.post("/{request_id}/cancel")
def cancel_request(request_id: int):
    """Cancel a ``queued`` or ``running`` request.

    Cancellation is cooperative on the running side: we can't abort an
    in-flight HTTP call to Flow, but the long-running handlers (notably
    ``_handle_gen_video``) re-read the row between polls and bail out
    when they see ``status == 'canceled'``. The worker's ``_process_one``
    also re-checks the row before its final stamp, so the canceled
    status is never overwritten by a late-arriving result.
    """
    with get_session() as s:
        req = s.get(Request, request_id)
        if req is None:
            raise HTTPException(404, "request not found")
        if req.status not in ("queued", "running"):
            raise HTTPException(
                409,
                f"only queued or running requests can be canceled (status={req.status})",
            )
        req.status = "canceled"
        req.error = "canceled"
        req.finished_at = datetime.now(timezone.utc)
        s.add(req)
        s.commit()
        s.refresh(req)
        return req
