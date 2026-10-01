"""The character registry of a board's Flow project.

A "Character Entity" is created in **Flow's own UI**, not here: the packaged
tool's own refusal tells the user to "tạo lại nhân vật trong đúng project", and
`createCharacter` / `/v1/characters` are zero hits in its binary. So these
endpoints register what Flow already made — the name a prompt tags, the voice,
the notes, an uploaded still, and the `entityId` the user pastes — and the run
sends it as `referenceEntities: [{entityId}]`.

Keyed by BOARD rather than by project id: the board is what the canvas has, and
which Flow project it belongs to is a backend detail (`BoardFlowProject`). An
entity id is only valid in the project it was made in, which is exactly why the
mapping is resolved here instead of trusted from the request.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.services import character_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/boards", tags=["characters"])


class CharacterBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    #: What Flow calls this character. Optional: without it the record still
    #: works over the reference-image path.
    entityId: str = ""
    voice: str = character_store.DEFAULT_VOICE
    voiceStyle: str = ""
    info: str = Field(default="", max_length=4000)
    #: An uploaded still, as a Flow media id.
    mediaId: str = ""
    #: Set to update an existing record instead of adding one.
    id: Optional[str] = None


class CharacterOut(BaseModel):
    id: str
    name: str
    projectId: str
    entityId: str
    voice: str
    voiceStyle: str
    info: str
    mediaId: str
    #: False when this record cannot be sent to its own project — an entity id
    #: from somewhere else. Surfaced so the card can say so before a run does.
    usable: bool


def _project_of(board_id: int) -> str:
    from flowboard.db import get_session
    from flowboard.db.models import Board, BoardFlowProject

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")
        row = s.get(BoardFlowProject, board_id)
    if row is None or not row.flow_project_id:
        raise HTTPException(
            409,
            "Board chưa gắn project Flow — mở board rồi bấm chạy một lần, "
            "hoặc gọi POST /api/boards/{id}/project trước.",
        )
    return row.flow_project_id


def _out(record: character_store.Character, project_id: str) -> CharacterOut:
    return CharacterOut(
        id=record.id,
        name=record.name,
        projectId=record.project_id,
        entityId=record.entity_id,
        voice=record.voice,
        voiceStyle=record.voice_style,
        info=record.info,
        mediaId=record.media_id,
        usable=record.usable_in(project_id),
    )


@router.get("/{board_id}/characters", response_model=list[CharacterOut])
def list_characters(board_id: int) -> list[CharacterOut]:
    project_id = _project_of(board_id)
    return [_out(c, project_id) for c in character_store.list_for(project_id)]


@router.post("/{board_id}/characters", response_model=CharacterOut)
def save_character(board_id: int, body: CharacterBody) -> CharacterOut:
    """Register or update one character of this board's project."""
    project_id = _project_of(board_id)
    existing = (
        character_store.get(project_id, body.id) if body.id else None
    )
    if body.id and existing is None:
        raise HTTPException(404, f"no character {body.id} in this project")

    record = existing or character_store.Character(name=body.name)
    record.name = body.name
    record.entity_id = body.entityId.strip()
    record.voice = body.voice.strip() or character_store.DEFAULT_VOICE
    record.voice_style = body.voiceStyle.strip()
    record.info = body.info.strip()
    record.media_id = body.mediaId.strip()
    try:
        saved = character_store.save(project_id, record)
    except ValueError as exc:
        # A name that normalises to nothing, or a name already taken. Both are
        # the caller's to fix, and both would otherwise surface as a prompt tag
        # that matches two characters or none.
        raise HTTPException(422, str(exc)) from None
    return _out(saved, project_id)


@router.post("/{board_id}/characters/create-on-flow", response_model=CharacterOut)
async def create_character_on_flow(board_id: int, body: CharacterBody) -> CharacterOut:
    """Make the Character in Flow, then store it with the id Flow gave back.

    Until now this app could only REFERENCE a character the user had created by
    hand in the Flow UI — the packaged tool has the same limit and says so out
    loud ("Nhân vật CHƯA ACTIVE trong Google Flow project hiện tại"). The RPC
    does exist; it is just not called `createCharacter`.

    Order matters: Flow first, store second. Storing first would leave a record
    with no `entity_id` that looks registered and cannot be dispatched — the
    exact "reads as configured, silently does nothing" failure this codebase
    keeps hunting. Zero credit: it writes a record, it generates nothing.
    """
    project_id = _project_of(board_id)
    from flowboard.services.flow_sdk import get_flow_sdk
    try:
        created = await get_flow_sdk().create_character(project_id, body.name)
    except Exception as exc:
        raise HTTPException(502, f"Flow refused the character: {str(exc)[:200]}") from None
    record = character_store.Character(name=body.name)
    record.entity_id = created["character_id"]
    record.voice = body.voice.strip() or character_store.DEFAULT_VOICE
    record.voice_style = body.voiceStyle.strip()
    record.info = body.info.strip()
    record.media_id = body.mediaId.strip()
    try:
        saved = character_store.save(project_id, record)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _out(saved, project_id)


@router.delete("/{board_id}/characters/{char_id}")
def delete_character(board_id: int, char_id: str) -> dict:
    project_id = _project_of(board_id)
    if not character_store.delete(project_id, char_id):
        raise HTTPException(404, f"no character {char_id} in this project")
    return {"deleted": char_id}
