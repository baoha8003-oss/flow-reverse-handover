from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.db import get_session
from flowboard.db.models import Edge, Node

router = APIRouter(prefix="/api/edges", tags=["edges"])

EdgeKind = Literal["ref", "hint"]

#: A port name is an identifier the executor matches on, not free text.
#: Bounded and normalised here so a blank string cannot become a socket that
#: no node has and every lookup then misses.
_MAX_PORT_CHARS = 60


def _port(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:_MAX_PORT_CHARS] if cleaned else None


class EdgeCreate(BaseModel):
    board_id: int
    source_id: int
    target_id: int
    kind: EdgeKind = "ref"
    # Optional pin to a specific variant of the source's `mediaIds[]`.
    # Frontend passes when the user picks a variant before drawing the
    # edge (or when right-click → pin variant on an existing edge).
    source_variant_idx: Optional[int] = None
    # Which socket each end plugs into. A node can have several inputs that
    # mean different things — a start frame is not an end frame, image_1 is
    # not image_2 — and the executor reads the port to tell them apart.
    #
    # These were missing, so a wire drawn on the canvas arrived with no
    # socket while an imported one kept its own: the same two nodes wired the
    # same way behaved differently depending on where the board came from.
    source_port: Optional[str] = None
    target_port: Optional[str] = None


class EdgePatch(BaseModel):
    """Partial update — currently only the variant pin is mutable;
    swapping source/target is a delete + create."""
    source_variant_idx: Optional[int] = None


@router.post("")
def create_edge(body: EdgeCreate):
    with get_session() as s:
        if body.source_id == body.target_id:
            raise HTTPException(400, "source_id and target_id must differ")
        source = s.get(Node, body.source_id)
        target = s.get(Node, body.target_id)
        if not source or not target:
            raise HTTPException(404, "source or target node not found")
        if source.board_id != body.board_id or target.board_id != body.board_id:
            raise HTTPException(400, "nodes must belong to the same board")
        edge = Edge(
            board_id=body.board_id,
            source_id=body.source_id,
            target_id=body.target_id,
            kind=body.kind,
            source_variant_idx=body.source_variant_idx,
            source_port=_port(body.source_port),
            target_port=_port(body.target_port),
        )
        s.add(edge)
        s.commit()
        s.refresh(edge)
        return edge


@router.patch("/{edge_id}")
def patch_edge(edge_id: int, body: EdgePatch):
    """Update an edge's variant pin without recreating the edge.

    Used by the variant-click flow: user picks a variant on an upstream
    multi-variant node → we PATCH the existing edge to that downstream
    so the next Generate uses the chosen ref. Passing
    ``source_variant_idx: null`` clears the pin (revert to mediaId).
    """
    with get_session() as s:
        edge = s.get(Edge, edge_id)
        if not edge:
            raise HTTPException(404, "edge not found")
        # Distinguish "unset" (don't touch) from "null" (clear). Pydantic
        # gives us model_fields_set for that.
        if "source_variant_idx" in body.model_fields_set:
            edge.source_variant_idx = body.source_variant_idx
        s.add(edge)
        s.commit()
        s.refresh(edge)
        return edge


@router.delete("/{edge_id}")
def delete_edge(edge_id: int):
    with get_session() as s:
        edge = s.get(Edge, edge_id)
        if not edge:
            raise HTTPException(404, "edge not found")
        s.delete(edge)
        s.commit()
        return {"ok": True}
