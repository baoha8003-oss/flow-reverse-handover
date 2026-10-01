"""Applying the canvas agent's proposals, and taking them back.

The agent describes graph edits; this module writes them and records enough to
reverse each one. Two rules shape everything here.

**The agent never spends money.** `run_node` and `run_workflow` return an
*intent* — a node id list the frontend hands to the existing cost dialog, which
calls `ensure_board_plan` and then `startRun`. Nothing in this module enqueues a
`Request`, and `tests/test_canvas_agent.py` asserts the table stays empty across
every action. Letting the agent dispatch would put the one decision the user
must make — "yes, charge me for this" — behind a model's judgement.

**Undo is structural only.** A node that has produced media has cost money;
un-creating it would throw away a paid result, and re-creating it would not
bring the media back. So undo refuses any action whose nodes are no longer
idle, and only ever reverses the newest un-undone action on that board —
reversing out of order would silently reinstate an edge the user deleted two
steps later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from flowboard.db import get_session
from flowboard.db.models import Board, CanvasAction, Edge, Node, PipelineRun, Plan
from flowboard.services import agent_rules, canvas_catalog
from flowboard.services.node_ops import delete_node_cascade, merge_node_data
from flowboard.short_id import generate_unique_short_id

logger = logging.getLogger(__name__)

#: Actions that only ever read, and whose result is a suggestion the user acts
#: on. Separated by name so a reader can see at a glance which half of this
#: module can change the board.
INTENT_ONLY: frozenset[str] = frozenset({"run_node", "run_workflow"})

#: Everything the agent may ask for.
ACTIONS: frozenset[str] = INTENT_ONLY | {
    "create_node", "configure_node", "connect_nodes", "disconnect_nodes",
    "write_script", "apply_canvas_plan",
}


class AgentError(RuntimeError):
    """The proposal cannot be applied. Carries a code the route maps to HTTP."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class Applied:
    """What an apply did, for the diff card and the log."""

    actions: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)


def _run_in_flight(s: Session, board_id: int) -> bool:
    """Whether this board has a run that has not finished.

    Editing under a live run is how a node gets rewired between the estimate
    the user approved and the dispatch that follows it — they would be charged
    for a board they never saw.
    """
    plan_ids = [
        p.id for p in s.exec(select(Plan).where(Plan.board_id == board_id)).all()
    ]
    if not plan_ids:
        return False
    pending = s.exec(
        select(PipelineRun).where(
            PipelineRun.plan_id.in_(plan_ids),  # type: ignore[attr-defined]
            PipelineRun.status.in_(("pending", "running")),  # type: ignore[attr-defined]
        )
    ).first()
    return pending is not None


def _as_proposal(s: Session, board_id: int, actions: list[dict]) -> tuple[list, list]:
    """The proposal as `agent_rules` sees it: existing board plus new nodes.

    The rules need the whole graph, not just the edit — a tag resolves against
    every title on the board, and the character ceiling counts the wires already
    there. Checking the edit alone would pass a board that the run refuses.
    """
    nodes = list(s.exec(select(Node).where(Node.board_id == board_id)).all())
    edges = list(s.exec(select(Edge).where(Edge.board_id == board_id)).all())
    proposed = [
        agent_rules.ProposedNode(
            ref=str(n.id), type=n.type,
            title=str((n.data or {}).get("title") or ""),
            prompt=str((n.data or {}).get("prompt") or ""),
        )
        for n in nodes
    ]
    proposed_edges = [
        agent_rules.ProposedEdge(
            source=str(e.source_id), target=str(e.target_id),
            source_port=e.source_port, target_port=e.target_port,
        )
        for e in edges
    ]
    for index, action in enumerate(actions):
        body = action.get("payload") or {}
        if action.get("kind") == "create_node":
            proposed.append(agent_rules.ProposedNode(
                ref=body.get("ref") or f"new-{index}",
                type=str(body.get("type") or ""),
                title=str(body.get("title") or ""),
                prompt=str(body.get("prompt") or ""),
            ))
        elif action.get("kind") == "connect_nodes":
            proposed_edges.append(agent_rules.ProposedEdge(
                source=str(body.get("source")), target=str(body.get("target")),
                source_port=body.get("source_port"),
                target_port=body.get("target_port"),
            ))
    return proposed, proposed_edges


def _lane_of(s: Session, board_id: int) -> Optional[str]:
    """The lane the board's video nodes ask for, when they agree on one.

    Disagreement returns None, which `character_ports` reads as the
    conservative default. Picking one of two lanes here would validate against
    a ceiling half the board does not have.
    """
    lanes = {
        str((n.data or {}).get("quality") or "").strip().lower()
        for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
        if n.type == "video"
    }
    lanes.discard("")
    return lanes.pop() if len(lanes) == 1 else None


def _node_on_board(s: Session, board_id: int, node_id: Any) -> Node:
    try:
        node = s.get(Node, int(node_id))
    except (TypeError, ValueError):
        raise AgentError("bad_node_id", f"node id không hợp lệ: {node_id!r}") from None
    if node is None or node.board_id != board_id:
        raise AgentError("node_not_on_board", f"node {node_id} không thuộc board này")
    return node


def apply_actions(
    board_id: int,
    actions: list[dict],
    *,
    provider: Optional[str] = None,
) -> Applied:
    """Validate, then write. All-or-nothing within one transaction.

    Validation runs against the board plus the whole proposal before anything
    is written, because a half-applied plan leaves the user reading a board
    that matches neither what they had nor what they approved.
    """
    unknown = sorted({str(a.get("kind")) for a in actions} - ACTIONS)
    if unknown:
        raise AgentError("unknown_action", f"không có hành động: {', '.join(unknown)}")

    result = Applied()
    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise AgentError("no_board", f"không có board {board_id}")
        if _run_in_flight(s, board_id):
            raise AgentError(
                "run_in_flight",
                "Board đang chạy — dừng hoặc đợi xong rồi mới sửa được.",
            )

        lane = _lane_of(s, board_id)
        nodes, edges = _as_proposal(s, board_id, actions)
        findings = agent_rules.check_plan(nodes, edges, lane=lane)
        result.findings = [
            {"rule": f.rule, "severity": f.severity, "message": f.message, "span": f.span}
            for f in findings
        ]
        if agent_rules.blocking(findings):
            raise AgentError("rules_blocked", "Kế hoạch có lỗi phải sửa trước khi áp dụng.")

        #: The agent's own handles, resolved to real ids as nodes are created,
        #: so a later `connect_nodes` in the same batch can name a node this
        #: batch just made.
        refs: dict[str, int] = {}
        rows: list[CanvasAction] = []

        for action in actions:
            kind = str(action.get("kind"))
            body = dict(action.get("payload") or {})
            if kind in INTENT_ONLY:
                result.intents.append(_intent(s, board_id, kind, body))
                continue
            row = _write_one(s, board_id, kind, body, refs, provider)
            if row is not None:
                # Added here rather than in `_write_one` so every branch of it
                # returns the record and exactly one place persists it.
                s.add(row)
                rows.append(row)

        s.commit()
        for row in rows:
            s.refresh(row)
            result.actions.append({
                "id": row.id, "kind": row.kind, "summary": row.summary,
                "payload": row.payload, "provider": row.provider,
            })
    return result


def _intent(s: Session, board_id: int, kind: str, body: dict) -> dict:
    """A run the USER will confirm. Reads only; enqueues nothing."""
    if kind == "run_workflow":
        ids = [
            n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
            if n.id is not None
        ]
    else:
        ids = [_node_on_board(s, board_id, body.get("node_id")).id]
    return {
        "kind": kind,
        "nodeIds": ids,
        # Said out loud in the payload the frontend reads, because "the agent
        # ran my board" is the misunderstanding this shape exists to prevent.
        "note": "Chưa chạy gì — bấm Chạy để xem giá rồi xác nhận.",
    }


def _write_one(
    s: Session,
    board_id: int,
    kind: str,
    body: dict,
    refs: dict[str, int],
    provider: Optional[str],
) -> Optional[CanvasAction]:
    """One mutating action, plus the inverse that reverses it."""
    if kind == "create_node":
        node_type = str(body.get("type") or "")
        if node_type not in canvas_catalog.NODE_TYPES:
            raise AgentError("bad_node_type", f"không có loại node “{node_type}”")
        data = dict(body.get("data") or {})
        for key in ("title", "prompt"):
            if body.get(key):
                data.setdefault(key, body[key])
        node = Node(
            board_id=board_id,
            short_id=generate_unique_short_id(s, board_id),
            type=node_type,
            x=float(body.get("x") or 0.0), y=float(body.get("y") or 0.0),
            data=data,
        )
        s.add(node)
        s.flush()
        if body.get("ref"):
            refs[str(body["ref"])] = node.id
        return CanvasAction(
            board_id=board_id, kind=kind, provider=provider,
            summary=f"Thêm node {node_type}" + (f" “{data.get('title')}”" if data.get("title") else ""),
            payload={**body, "node_id": node.id},
            inverse={"delete_node": node.id},
        )

    if kind in ("configure_node", "write_script"):
        node = _node_on_board(s, board_id, _resolve(body.get("node_id"), refs))
        patch = dict(body.get("data") or {})
        if kind == "write_script" and body.get("text") is not None:
            patch["prompt"] = body["text"]
        if not patch:
            return None
        before = {k: (node.data or {}).get(k) for k in patch}
        node.data = merge_node_data(node.data, patch)
        s.add(node)
        return CanvasAction(
            board_id=board_id, kind=kind, provider=provider,
            summary=f"Sửa {', '.join(sorted(patch))} trên node {node.short_id}",
            payload={"node_id": node.id, "data": patch},
            # `None` in the patch means "delete this key", which is exactly
            # what restoring an absent key needs.
            inverse={"restore_data": {"node_id": node.id, "data": before}},
        )

    if kind == "connect_nodes":
        source = _node_on_board(s, board_id, _resolve(body.get("source"), refs))
        target = _node_on_board(s, board_id, _resolve(body.get("target"), refs))
        edge = Edge(
            board_id=board_id, source_id=source.id, target_id=target.id,
            kind=str(body.get("edge_kind") or "media"),
            source_port=body.get("source_port"), target_port=body.get("target_port"),
        )
        s.add(edge)
        s.flush()
        return CanvasAction(
            board_id=board_id, kind=kind, provider=provider,
            summary=f"Nối {source.short_id} → {target.short_id}"
                    + (f".{body['target_port']}" if body.get("target_port") else ""),
            payload={**body, "edge_id": edge.id},
            inverse={"delete_edge": edge.id},
        )

    if kind == "disconnect_nodes":
        edge = s.get(Edge, int(body.get("edge_id") or 0))
        if edge is None or edge.board_id != board_id:
            raise AgentError("edge_not_on_board", "dây không thuộc board này")
        snapshot = {
            "board_id": edge.board_id, "source_id": edge.source_id,
            "target_id": edge.target_id, "kind": edge.kind,
            "source_port": edge.source_port, "target_port": edge.target_port,
            "source_variant_idx": edge.source_variant_idx,
        }
        s.delete(edge)
        return CanvasAction(
            board_id=board_id, kind=kind, provider=provider,
            summary="Gỡ một dây",
            payload={"edge_id": body.get("edge_id")},
            inverse={"recreate_edge": snapshot},
        )

    if kind == "apply_canvas_plan":
        # A plan is a batch of the actions above, so it is expanded by the
        # caller rather than given a second write path here. Two paths that
        # build a board would drift, and the drift is a board the run refuses.
        raise AgentError(
            "expand_plan_first",
            "apply_canvas_plan phải được tách thành các hành động đơn lẻ",
        )

    raise AgentError("unknown_action", f"không có hành động: {kind}")


def _resolve(value: Any, refs: dict[str, int]) -> Any:
    """An id, or one of this batch's own handles."""
    return refs.get(str(value), value)


def undo_last(board_id: int) -> dict:
    """Reverse the newest un-undone action on this board.

    Refuses when the nodes it would touch are no longer idle: a node that has
    produced media has cost money, and un-creating it throws that away without
    being able to give it back.
    """
    with get_session() as s:
        row = s.exec(
            select(CanvasAction)
            .where(CanvasAction.board_id == board_id, CanvasAction.undone_at.is_(None))  # type: ignore[union-attr]
            .order_by(CanvasAction.id.desc())  # type: ignore[attr-defined]
        ).first()
        if row is None:
            raise AgentError("nothing_to_undo", "Không còn thay đổi nào để hoàn tác.")
        if _run_in_flight(s, board_id):
            raise AgentError("run_in_flight", "Board đang chạy — không hoàn tác được.")

        inverse = row.inverse or {}
        if "delete_node" in inverse:
            node = s.get(Node, int(inverse["delete_node"]))
            if node is not None:
                _refuse_if_spent(node)
                delete_node_cascade(s, node)
        elif "restore_data" in inverse:
            spec = inverse["restore_data"]
            node = s.get(Node, int(spec["node_id"]))
            if node is not None:
                _refuse_if_spent(node)
                node.data = merge_node_data(node.data, spec.get("data") or {})
                s.add(node)
        elif "delete_edge" in inverse:
            edge = s.get(Edge, int(inverse["delete_edge"]))
            if edge is not None:
                s.delete(edge)
        elif "recreate_edge" in inverse:
            s.add(Edge(**inverse["recreate_edge"]))
        else:
            raise AgentError("not_reversible", "Thay đổi này không hoàn tác được.")

        row.undone_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
        return {"undone": row.id, "kind": row.kind, "summary": row.summary}


def _refuse_if_spent(node: Node) -> None:
    """A node holding media or mid-flight is not structure any more."""
    data = node.data or {}
    if data.get("mediaId") or data.get("mediaIds"):
        raise AgentError(
            "node_has_media",
            f"Node {node.short_id} đã có kết quả — hoàn tác sẽ bỏ thứ đã trả tiền.",
        )
    if node.status != "idle":
        raise AgentError(
            "node_not_idle",
            f"Node {node.short_id} đang ở trạng thái “{node.status}”.",
        )


def history(board_id: int, limit: int = 30) -> list[dict]:
    """The log the panel shows. Includes undone rows, marked."""
    with get_session() as s:
        rows = s.exec(
            select(CanvasAction)
            .where(CanvasAction.board_id == board_id)
            .order_by(CanvasAction.id.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        return [
            {
                "id": r.id, "kind": r.kind, "summary": r.summary,
                "provider": r.provider,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
                "undone": r.undone_at is not None,
            }
            for r in rows
        ]
