"""HTTP for the canvas agent.

A thin layer on purpose: the rules live in `agent_rules`, the writes in
`canvas_agent`, and the catalogue in `canvas_catalog`. The only judgement here
is which `AgentError` code becomes which status — and the one that matters is
`run_in_flight` → **409**, because the frontend disables Apply on it. Editing a
board mid-run rewires nodes between the estimate the user approved and the
dispatch that follows, and they are charged for a board they never saw.

There is deliberately no endpoint that runs anything. `run_node` and
`run_workflow` come back as intents the existing cost dialog consumes.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.services import canvas_agent, canvas_catalog

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: Which codes are the caller's fault, and which are a conflict it can retry.
#: Anything unlisted is a 400 — an unmapped code reaching the client as a 500
#: reads as "the app broke" for what is usually a bad proposal.
_STATUS: dict[str, int] = {
    "no_board": 404,
    "node_not_on_board": 404,
    "edge_not_on_board": 404,
    "run_in_flight": 409,
    "nothing_to_undo": 409,
    "node_has_media": 409,
    "node_not_idle": 409,
}


class AgentAction(BaseModel):
    kind: str
    payload: dict[str, Any] = {}


class ApplyBody(BaseModel):
    actions: list[AgentAction] = []
    #: Which provider answered, recorded on each row. The chain can fall
    #: through to a different one than the panel showed, and "why does this
    #: plan look nothing like last time" has no answer without it.
    provider: Optional[str] = None


def _fail(exc: canvas_agent.AgentError) -> HTTPException:
    return HTTPException(_STATUS.get(exc.code, 400), f"{exc.code}: {exc.message}")


@router.get("/catalog")
def catalog() -> dict:
    """What may connect to what. Also what the agent's prompt is built from."""
    return canvas_catalog.describe()


@router.post("/boards/{board_id}/apply")
def apply(board_id: int, body: ApplyBody) -> dict:
    try:
        applied = canvas_agent.apply_actions(
            board_id,
            [a.model_dump() for a in body.actions],
            provider=body.provider,
        )
    except canvas_agent.AgentError as exc:
        raise _fail(exc) from None
    return {
        "actions": applied.actions,
        "intents": applied.intents,
        "findings": applied.findings,
    }


@router.post("/boards/{board_id}/undo")
def undo(board_id: int) -> dict:
    try:
        return canvas_agent.undo_last(board_id)
    except canvas_agent.AgentError as exc:
        raise _fail(exc) from None


@router.get("/boards/{board_id}/history")
def history(board_id: int, limit: int = 30) -> dict:
    return {"actions": canvas_agent.history(board_id, limit=max(1, min(limit, 200)))}
