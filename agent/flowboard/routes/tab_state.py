"""Per-tab form memory: read it on mount, write it on change.

One row per tab and global, the way the packaged tool keeps
`affiliate_state.json` and `data_clone.json` — those files hold a
`project_index` / `last_project` INSIDE them rather than being stored per
project, so a tab's memory is not a per-board thing here either.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.services import tab_state

router = APIRouter(prefix="/api/tab-state", tags=["tab-state"])


class TabStateBody(BaseModel):
    state: dict[str, Any]


@router.get("")
def list_tabs() -> dict:
    return {"tabs": tab_state.tabs()}


@router.get("/{tab}")
def read_tab(tab: str) -> dict:
    try:
        return {"tab": tab, "state": tab_state.load(tab)}
    except tab_state.TabStateError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/{tab}")
def write_tab(tab: str, body: TabStateBody) -> dict:
    try:
        stored = tab_state.save(tab, body.state)
    except tab_state.TabStateError as exc:
        # 422 rather than 500: an oversized or malformed state is the caller's
        # to fix, and the tab keeps working without its memory.
        raise HTTPException(422, str(exc)) from None
    return {"tab": tab, "state": stored}


@router.delete("/{tab}")
def clear_tab(tab: str) -> dict:
    try:
        return {"tab": tab, "cleared": tab_state.clear(tab)}
    except tab_state.TabStateError as exc:
        raise HTTPException(422, str(exc)) from None
