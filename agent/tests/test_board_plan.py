"""The plan a board is run through, kept in step with the board.

`run_pipeline` reads `spec._materialized_node_ids` and nothing else. A plan
whose list has drifted from the board runs the wrong node set — and does it
quietly, because a skipped node just stays idle.
"""
from __future__ import annotations

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, Node, Plan
from flowboard.short_id import generate_unique_short_id


def _board(types: list[str]) -> int:
    with get_session() as s:
        board = Board(name="plan test")
        s.add(board)
        s.commit()
        s.refresh(board)
        for t in types:
            s.add(
                Node(
                    board_id=board.id,
                    short_id=generate_unique_short_id(s, board.id),
                    type=t,
                    data={},
                )
            )
        s.commit()
        return board.id


def _add_node(board_id: int, node_type: str) -> int:
    with get_session() as s:
        n = Node(
            board_id=board_id,
            short_id=generate_unique_short_id(s, board_id),
            type=node_type,
            data={},
        )
        s.add(n)
        s.commit()
        s.refresh(n)
        return n.id


def _node_ids(board_id: int) -> set[int]:
    with get_session() as s:
        return {n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()}


def test_a_hand_built_board_has_no_plan_until_asked(client):
    """Null, not an error: most boards are run a node at a time."""
    board = _board(["image"])
    resp = client.get(f"/api/boards/{board}/plan")
    assert resp.status_code == 200
    assert resp.json() is None


def test_ensuring_a_plan_covers_every_node_on_the_board(client):
    """Without this, the post-production nodes in the palette could never
    run: they have no per-node run button, only the board run."""
    board = _board(["video", "merge_video", "add_bgm"])
    body = client.post(f"/api/boards/{board}/plan").json()
    assert set(body["spec"]["_materialized_node_ids"]) == _node_ids(board)
    assert body["status"] == "approved"


def test_ensuring_again_refreshes_the_list_instead_of_making_a_second_plan(client):
    """A node added after the first ensure must not be skipped forever."""
    board = _board(["video"])
    first = client.post(f"/api/boards/{board}/plan").json()
    added = _add_node(board, "merge_video")

    second = client.post(f"/api/boards/{board}/plan").json()
    assert second["id"] == first["id"], "a second plan was created"
    assert added in second["spec"]["_materialized_node_ids"]
    assert set(second["spec"]["_materialized_node_ids"]) == _node_ids(board)

    with get_session() as s:
        plans = list(s.exec(select(Plan).where(Plan.board_id == board)).all())
    assert len(plans) == 1


def test_the_refreshed_list_is_actually_persisted(client):
    """SQLModel's JSON column does not track in-place mutation, so a plan
    updated by mutating `spec` in place would read back unchanged."""
    board = _board(["video"])
    plan_id = client.post(f"/api/boards/{board}/plan").json()["id"]
    added = _add_node(board, "add_bgm")
    client.post(f"/api/boards/{board}/plan")

    with get_session() as s:
        stored = s.get(Plan, plan_id)
    assert added in stored.spec["_materialized_node_ids"]


def test_a_finished_plan_becomes_runnable_again(client):
    board = _board(["video"])
    plan_id = client.post(f"/api/boards/{board}/plan").json()["id"]
    with get_session() as s:
        plan = s.get(Plan, plan_id)
        plan.status = "done"
        s.add(plan)
        s.commit()

    assert client.post(f"/api/boards/{board}/plan").json()["status"] == "approved"


def test_an_empty_board_gets_a_plan_that_runs_nothing(client):
    """Better than a 400: the run then reports "nothing to do" through the
    normal path instead of the button erroring."""
    board = _board([])
    body = client.post(f"/api/boards/{board}/plan").json()
    assert body["spec"]["_materialized_node_ids"] == []


def test_ensuring_a_plan_for_a_board_that_does_not_exist_is_a_404(client):
    assert client.post("/api/boards/999999/plan").status_code == 404


def test_ensure_does_not_dispatch_anything(client):
    """Building a plan must never cost money. Only the run does."""
    from flowboard.db.models import Request

    board = _board(["video", "image"])
    with get_session() as s:
        before = len(list(s.exec(select(Request)).all()))
    client.post(f"/api/boards/{board}/plan")
    with get_session() as s:
        after = len(list(s.exec(select(Request)).all()))
    assert after == before


# ── scoping a run to a subset of the board ────────────────────────────


def test_no_scope_means_the_whole_board(client):
    """Every existing caller posts no body, and must keep meaning "run all"."""
    board = _board(["image", "video"])
    body = client.post(f"/api/boards/{board}/plan").json()
    assert body["spec"]["_run_only_node_ids"] == []


def test_a_scope_reaches_the_key_the_executor_gates_on(client):
    board = _board(["image", "video", "video"])
    picked = sorted(_node_ids(board))[:2]
    body = client.post(
        f"/api/boards/{board}/plan", json={"node_ids": picked}
    ).json()
    assert body["spec"]["_run_only_node_ids"] == picked
    # The node list itself is untouched: the executor needs the whole graph
    # loaded so a scoped node's wires still resolve.
    assert set(body["spec"]["_materialized_node_ids"]) == _node_ids(board)


def test_a_node_from_another_board_is_refused(client):
    """An id the executor's gate can never match would skip every node, so Run
    would report success having dispatched nothing at all."""
    board = _board(["image"])
    other = _board(["image"])
    stranger = sorted(_node_ids(other))[0]
    r = client.post(f"/api/boards/{board}/plan", json={"node_ids": [stranger]})
    assert r.status_code == 400
    assert str(stranger) in r.json()["detail"]


def test_a_later_full_run_clears_an_earlier_scope(client):
    """The trap this key was written for: a leftover subset makes the next
    full run skip everything that had already succeeded."""
    board = _board(["image", "video"])
    picked = sorted(_node_ids(board))[:1]
    client.post(f"/api/boards/{board}/plan", json={"node_ids": picked})
    body = client.post(f"/api/boards/{board}/plan").json()
    assert body["spec"]["_run_only_node_ids"] == []


def test_a_duplicated_id_is_stored_once(client):
    board = _board(["image"])
    only = sorted(_node_ids(board))[0]
    body = client.post(
        f"/api/boards/{board}/plan", json={"node_ids": [only, only]}
    ).json()
    assert body["spec"]["_run_only_node_ids"] == [only]
