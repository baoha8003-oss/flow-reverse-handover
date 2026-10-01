"""The canvas agent writes structure, never spend.

Two properties carry this file. The first is that no action of the agent's can
create a `Request` — that is the row a dispatch bills against, and putting it
behind a model's judgement takes the one decision the user must make. The second
is that undo only ever reverses structure: a node holding media has cost money,
and un-creating it throws the result away without being able to give it back.
"""
from __future__ import annotations

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import (
    Board,
    CanvasAction,
    Edge,
    Node,
    PipelineRun,
    Plan,
    Request,
)
from flowboard.short_id import generate_unique_short_id


def _board(nodes: list[tuple[str, dict]] | None = None) -> tuple[int, list[int]]:
    ids: list[int] = []
    with get_session() as s:
        board = Board(name="agent test")
        s.add(board)
        s.commit()
        s.refresh(board)
        for node_type, data in nodes or []:
            node = Node(
                board_id=board.id,
                short_id=generate_unique_short_id(s, board.id),
                type=node_type,
                data=data,
            )
            s.add(node)
            s.commit()
            s.refresh(node)
            ids.append(node.id)
        return board.id, ids


def _requests() -> int:
    with get_session() as s:
        return len(list(s.exec(select(Request)).all()))


def _apply(client, board: int, actions: list[dict], provider: str | None = None):
    return client.post(
        f"/api/agent/boards/{board}/apply",
        json={"actions": actions, "provider": provider},
    )


# ── the money rule ────────────────────────────────────────────────────


def test_no_action_the_agent_can_take_creates_a_request_row(client):
    """The whole action surface, in one board, checked against the table that
    bills. `Request` is what a dispatch charges against."""
    board, ids = _board([("image", {"prompt": "một cảnh"}), ("video", {"prompt": "clip"})])
    before = _requests()
    r = _apply(client, board, [
        {"kind": "create_node", "payload": {"ref": "n1", "type": "image", "title": "Ảnh mới"}},
        {"kind": "configure_node", "payload": {"node_id": ids[0], "data": {"prompt": "đổi"}}},
        {"kind": "write_script", "payload": {"node_id": ids[0], "text": "kịch bản"}},
        {"kind": "connect_nodes", "payload": {
            "source": "n1", "target": ids[1],
            "source_port": "image_out_1", "target_port": "start_frame"}},
        {"kind": "run_node", "payload": {"node_id": ids[1]}},
        {"kind": "run_workflow", "payload": {}},
    ])
    assert r.status_code == 200, r.text
    assert _requests() == before


def test_a_run_action_comes_back_as_an_intent_and_says_so(client):
    board, ids = _board([("video", {"prompt": "clip"})])
    body = _apply(client, board, [
        {"kind": "run_node", "payload": {"node_id": ids[0]}},
    ]).json()
    assert body["actions"] == []
    assert body["intents"][0]["nodeIds"] == [ids[0]]
    assert "Chưa chạy gì" in body["intents"][0]["note"]


def test_run_workflow_names_every_node_on_the_board(client):
    board, ids = _board([("image", {}), ("video", {}), ("note", {})])
    body = _apply(client, board, [{"kind": "run_workflow", "payload": {}}]).json()
    assert sorted(body["intents"][0]["nodeIds"]) == sorted(ids)


# ── editing under a live run ───────────────────────────────────────────


def _start_run(board: int) -> None:
    with get_session() as s:
        plan = Plan(board_id=board, status="running", spec={})
        s.add(plan)
        s.commit()
        s.refresh(plan)
        s.add(PipelineRun(plan_id=plan.id, status="running"))
        s.commit()


def test_applying_during_a_run_is_a_409(client):
    """A rewire between the estimate the user approved and the dispatch that
    follows charges them for a board they never saw."""
    board, _ = _board([("image", {})])
    _start_run(board)
    r = _apply(client, board, [
        {"kind": "create_node", "payload": {"type": "note"}},
    ])
    assert r.status_code == 409
    assert "run_in_flight" in r.json()["detail"]


def test_a_finished_run_does_not_block_editing(client):
    board, _ = _board([("image", {})])
    with get_session() as s:
        plan = Plan(board_id=board, status="done", spec={})
        s.add(plan)
        s.commit()
        s.refresh(plan)
        s.add(PipelineRun(plan_id=plan.id, status="done"))
        s.commit()
    assert _apply(client, board, [
        {"kind": "create_node", "payload": {"type": "note"}},
    ]).status_code == 200


# ── validation before any write ────────────────────────────────────────


def test_a_blocked_plan_writes_nothing_at_all(client):
    """All-or-nothing: a half-applied plan leaves a board matching neither what
    the user had nor what they approved."""
    board, ids = _board([("video", {"quality": "lite"})])
    before = len(_nodes(board))
    r = _apply(client, board, [
        {"kind": "create_node", "payload": {"ref": "c1", "type": "character", "title": "NV1"}},
        {"kind": "create_node", "payload": {"ref": "c2", "type": "character", "title": "NV2"}},
        {"kind": "create_node", "payload": {"ref": "c3", "type": "character", "title": "NV3"}},
        {"kind": "create_node", "payload": {"ref": "c4", "type": "character", "title": "NV4"}},
    ] + [
        {"kind": "connect_nodes", "payload": {
            "source": f"c{i}", "target": ids[0],
            "source_port": "media", "target_port": f"character_{i}"}}
        for i in range(1, 5)
    ])
    assert r.status_code == 400
    assert "rules_blocked" in r.json()["detail"]
    assert len(_nodes(board)) == before


def test_an_unknown_action_is_refused_before_anything_is_written(client):
    board, _ = _board()
    r = _apply(client, board, [
        {"kind": "create_node", "payload": {"type": "note"}},
        {"kind": "delete_everything", "payload": {}},
    ])
    assert r.status_code == 400
    assert len(_nodes(board)) == 0


def test_an_unknown_node_type_is_refused(client):
    board, _ = _board()
    r = _apply(client, board, [{"kind": "create_node", "payload": {"type": "wormhole"}}])
    assert r.status_code == 400


def test_a_node_from_another_board_cannot_be_configured(client):
    board, _ = _board()
    other, other_ids = _board([("image", {})])
    r = _apply(client, board, [
        {"kind": "configure_node", "payload": {"node_id": other_ids[0], "data": {"prompt": "x"}}},
    ])
    assert r.status_code == 404


# ── the batch can name nodes it just made ─────────────────────────────


def _nodes(board: int) -> list[Node]:
    with get_session() as s:
        return list(s.exec(select(Node).where(Node.board_id == board)).all())


def _edges(board: int) -> list[Edge]:
    with get_session() as s:
        return list(s.exec(select(Edge).where(Edge.board_id == board)).all())


def test_a_wire_can_reference_a_node_created_in_the_same_batch(client):
    board, _ = _board()
    r = _apply(client, board, [
        {"kind": "create_node", "payload": {"ref": "img", "type": "image", "title": "Ảnh"}},
        {"kind": "create_node", "payload": {"ref": "vid", "type": "video", "title": "Clip"}},
        {"kind": "connect_nodes", "payload": {
            "source": "img", "target": "vid",
            "source_port": "image_out_1", "target_port": "start_frame"}},
    ])
    assert r.status_code == 200, r.text
    wires = _edges(board)
    assert len(wires) == 1
    assert wires[0].target_port == "start_frame"


def test_the_provider_that_answered_is_recorded(client):
    """The chain can fall through to a provider the panel never showed."""
    board, _ = _board()
    _apply(client, board, [
        {"kind": "create_node", "payload": {"type": "note"}},
    ], provider="gemini")
    body = client.get(f"/api/agent/boards/{board}/history").json()
    assert body["actions"][0]["provider"] == "gemini"


# ── undo ──────────────────────────────────────────────────────────────


def test_undo_removes_a_node_the_agent_created(client):
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "note"}}])
    assert len(_nodes(board)) == 1
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 200
    assert _nodes(board) == []


def test_undo_restores_the_previous_settings(client):
    board, ids = _board([("image", {"prompt": "cũ", "aspectRatio": "PORTRAIT"})])
    _apply(client, board, [
        {"kind": "configure_node", "payload": {"node_id": ids[0], "data": {"prompt": "mới"}}},
    ])
    client.post(f"/api/agent/boards/{board}/undo")
    with get_session() as s:
        data = s.get(Node, ids[0]).data
    assert data["prompt"] == "cũ"
    # The sibling the patch never mentioned is untouched throughout.
    assert data["aspectRatio"] == "PORTRAIT"


def test_undo_of_a_field_that_did_not_exist_removes_it(client):
    """`None` in the restore patch is the delete sentinel — which is exactly
    what putting back an absent key needs."""
    board, ids = _board([("image", {"prompt": "cũ"})])
    _apply(client, board, [
        {"kind": "configure_node", "payload": {"node_id": ids[0], "data": {"aiBrief": "x"}}},
    ])
    client.post(f"/api/agent/boards/{board}/undo")
    with get_session() as s:
        assert "aiBrief" not in s.get(Node, ids[0]).data


def test_undo_puts_back_a_wire_the_agent_removed(client):
    board, ids = _board([("image", {}), ("video", {})])
    with get_session() as s:
        edge = Edge(board_id=board, source_id=ids[0], target_id=ids[1],
                    kind="media", source_port="image_out_1", target_port="start_frame")
        s.add(edge)
        s.commit()
        s.refresh(edge)
        edge_id = edge.id
    _apply(client, board, [{"kind": "disconnect_nodes", "payload": {"edge_id": edge_id}}])
    assert _edges(board) == []
    client.post(f"/api/agent/boards/{board}/undo")
    restored = _edges(board)
    assert len(restored) == 1
    assert restored[0].target_port == "start_frame"


def test_undo_refuses_a_node_that_has_produced_media(client):
    """Undoing it throws away something already paid for, and re-creating the
    node would not bring the media back."""
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "image"}}])
    created = _nodes(board)[0]
    with get_session() as s:
        node = s.get(Node, created.id)
        node.data = {**(node.data or {}), "mediaId": "abc123"}
        s.add(node)
        s.commit()
    r = client.post(f"/api/agent/boards/{board}/undo")
    assert r.status_code == 409
    assert "node_has_media" in r.json()["detail"]
    assert len(_nodes(board)) == 1


def test_undo_refuses_a_node_that_is_not_idle(client):
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "image"}}])
    created = _nodes(board)[0]
    with get_session() as s:
        node = s.get(Node, created.id)
        node.status = "running"
        s.add(node)
        s.commit()
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 409


def test_undo_only_reverses_the_newest_change(client):
    """Out of order it would reinstate an edge the user deleted two steps on."""
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "note"}}])
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "image"}}])
    client.post(f"/api/agent/boards/{board}/undo")
    left = _nodes(board)
    assert len(left) == 1
    assert left[0].type == "note"


def test_undo_twice_walks_back_two_changes(client):
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "note"}}])
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "image"}}])
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 200
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 200
    assert _nodes(board) == []


def test_undo_with_nothing_to_undo_is_a_409(client):
    board, _ = _board()
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 409


def test_an_undone_row_is_kept_and_marked(client):
    """A spend record and an audit trail are what the table is for, and a
    deleted row is neither."""
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "note"}}])
    client.post(f"/api/agent/boards/{board}/undo")
    with get_session() as s:
        rows = list(s.exec(select(CanvasAction).where(CanvasAction.board_id == board)).all())
    assert len(rows) == 1
    assert rows[0].undone_at is not None
    body = client.get(f"/api/agent/boards/{board}/history").json()
    assert body["actions"][0]["undone"] is True


def test_undo_during_a_run_is_refused(client):
    board, _ = _board()
    _apply(client, board, [{"kind": "create_node", "payload": {"type": "note"}}])
    _start_run(board)
    assert client.post(f"/api/agent/boards/{board}/undo").status_code == 409


# ── the catalogue endpoint ─────────────────────────────────────────────


def test_the_catalog_endpoint_serves_what_the_agent_is_prompted_with(client):
    body = client.get("/api/agent/catalog").json()
    assert "video" in body["portsIn"]
    assert "start_frame" in body["portsIn"]["video"]
    assert body["characterPortLimits"]["omni"] == 10


def test_applying_to_a_board_that_does_not_exist_is_a_404(client):
    assert _apply(client, 999999, []).status_code == 404
