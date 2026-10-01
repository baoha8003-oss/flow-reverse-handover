"""One prompt node with twenty lines, turned into twenty generation nodes.

Two properties carry the weight here, and both are about money.

**It happens before the run, not during it.** Fanning out inside the executor
would be fewer clicks and would make the cost dialog wrong: the user approves
three billable calls and the run discovers seventeen more. So the nodes are on
the canvas first and the estimate counts them.

**Pressing it twice does not double the board.** A clone remembers which line
of which prompt node it holds, so a second run after editing the list updates,
adds and removes. Without that, every press adds another full set beside the
last — each of which then dispatches.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Edge, Node
from flowboard.services import fan_out


def _board(client) -> int:
    return client.post("/api/boards", json={"name": "B"}).json()["id"]


def _node(client, board_id, node_type, **data) -> dict:
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": node_type, "x": data.pop("x", 0),
        "y": data.pop("y", 0), "data": data,
    }).json()


def _wire(client, board_id, src, dst, **kw) -> None:
    client.post("/api/edges", json={
        "board_id": board_id, "source_id": src, "target_id": dst, **kw,
    })


def _setup(client, lines: str, *, with_reference: bool = False):
    board_id = _board(client)
    prompt = _node(client, board_id, "prompt", title="Danh sách", prompt=lines)
    image = _node(client, board_id, "image", title="Ảnh", x=400)
    _wire(client, board_id, prompt["id"], image["id"], target_port="prompt")
    ref = None
    if with_reference:
        ref = _node(client, board_id, "character", title="Nhân vật", mediaId="m-face")
        _wire(client, board_id, ref["id"], image["id"])
    return board_id, prompt, image, ref


def _nodes(board_id: int, node_type: str) -> list[Node]:
    with get_session() as s:
        return list(
            s.exec(
                select(Node).where(Node.board_id == board_id, Node.type == node_type)
            ).all()
        )


# ── splitting the list ────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("một\nhai\nba", ["một", "hai", "ba"]),
    ("một\n\n\nhai", ["một", "hai"]),
    ("  một  \n hai ", ["một", "hai"]),
    ("một\n", ["một"]),
    ("", []),
    (None, []),
])
def test_blank_lines_are_separators_not_prompts(text, expected):
    """A trailing newline is how every text box ends. As an entry it becomes
    an empty prompt that dispatches and is refused as `missing_prompt`."""
    assert fan_out.split_lines(text) == expected


# ── the fan-out itself ────────────────────────────────────────────────


def test_each_line_gets_its_own_node(client):
    board_id, prompt, image, _ = _setup(client, "mèo\nchó\nchim")
    out = client.post(f"/api/boards/{board_id}/fan-out",
                      json={"prompt_node_id": prompt["id"]}).json()
    assert out["lines"] == 3
    assert (out["created"], out["updated"], out["deleted"]) == (2, 0, 0)
    with get_session() as s:
        assert out["templateShortId"] == s.get(Node, image["id"]).short_id
    prompts = sorted((n.data or {}).get("prompt") for n in _nodes(board_id, "image"))
    assert prompts == ["chim", "chó", "mèo"]


def test_the_first_line_stays_on_the_template(client):
    """A board that already ran once keeps its first result instead of being
    rebuilt from scratch around it."""
    board_id, prompt, image, _ = _setup(client, "mèo\nchó")
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        template = s.get(Node, image["id"])
    assert (template.data or {}).get("prompt") == "mèo"


def test_references_are_wired_into_every_clone(client):
    """A clone without the reference photo generates something else
    entirely — and charges for it."""
    board_id, prompt, image, ref = _setup(client, "mèo\nchó\nchim", with_reference=True)
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        from_ref = [
            e for e in s.exec(select(Edge).where(Edge.board_id == board_id)).all()
            if e.source_id == ref["id"]
        ]
    assert len(from_ref) == 3, "the reference must reach all three"


def test_the_prompt_wire_is_not_copied_to_the_clones(client):
    """Each clone carries its own line inline. A second prompt wire would
    make the executor read the WHOLE list as one prompt for that node."""
    board_id, prompt, image, _ = _setup(client, "mèo\nchó")
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        from_prompt = [
            e for e in s.exec(select(Edge).where(Edge.board_id == board_id)).all()
            if e.source_id == prompt["id"]
        ]
    assert len(from_prompt) == 1


def test_running_it_twice_does_not_double_the_board(client):
    board_id, prompt, _, _ = _setup(client, "mèo\nchó\nchim")
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    second = client.post(f"/api/boards/{board_id}/fan-out",
                         json={"prompt_node_id": prompt["id"]}).json()
    assert second["created"] == 0 and second["updated"] == 2
    assert len(_nodes(board_id, "image")) == 3


def test_editing_the_list_updates_in_place(client):
    board_id, prompt, _, _ = _setup(client, "mèo\nchó\nchim")
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    client.patch(f"/api/nodes/{prompt['id']}", json={"data": {"prompt": "mèo\nvịt"}})
    out = client.post(f"/api/boards/{board_id}/fan-out",
                      json={"prompt_node_id": prompt["id"]}).json()
    assert out["deleted"] == 1
    prompts = sorted((n.data or {}).get("prompt") for n in _nodes(board_id, "image"))
    assert prompts == ["mèo", "vịt"]


def test_a_removed_line_takes_its_wires_with_it(client):
    """A clone left behind keeps dispatching a prompt the user deleted."""
    board_id, prompt, _, ref = _setup(client, "mèo\nchó\nchim", with_reference=True)
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    client.patch(f"/api/nodes/{prompt['id']}", json={"data": {"prompt": "mèo"}})
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        edges = list(s.exec(select(Edge).where(Edge.board_id == board_id)).all())
        nodes = {n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()}
    assert all(e.source_id in nodes and e.target_id in nodes for e in edges)


def test_the_run_only_data_of_the_template_is_not_copied(client):
    """A clone that starts life holding the template's mediaId claims to
    have produced an image it never made."""
    board_id, prompt, image, _ = _setup(client, "mèo\nchó")
    client.patch(f"/api/nodes/{image['id']}",
                 json={"data": {"mediaId": "m-old", "renderedAt": "hôm qua"}})
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    clones = [n for n in _nodes(board_id, "image") if n.id != image["id"]]
    assert clones and all("mediaId" not in (n.data or {}) for n in clones)


# ── layout ────────────────────────────────────────────────────────────


def test_room_is_made_to_the_right_before_the_clones_land(client):
    """Without runway the clones land on top of whatever the board already
    has — which on a left-to-right board is most of it."""
    board_id, prompt, image, _ = _setup(client, "mèo\nchó\nchim")
    downstream = _node(client, board_id, "video", title="Sau", x=600)
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        moved = s.get(Node, downstream["id"])
    assert moved.x > 600, "the node to the right was not moved out of the way"


def test_pressing_it_again_does_not_shove_the_board_again(client):
    """The second press needs no new columns, so nothing should move."""
    board_id, prompt, _, _ = _setup(client, "mèo\nchó")
    downstream = _node(client, board_id, "video", title="Sau", x=600)
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        after_first = s.get(Node, downstream["id"]).x
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        after_second = s.get(Node, downstream["id"]).x
    assert after_first == after_second


def test_nodes_to_the_left_stay_where_they_are(client):
    """Dragging the whole board around would lose an arrangement someone
    made on purpose."""
    board_id, prompt, _, _ = _setup(client, "mèo\nchó")
    with get_session() as s:
        before = s.get(Node, prompt["id"]).x
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    with get_session() as s:
        assert s.get(Node, prompt["id"]).x == before


# ── refusals ──────────────────────────────────────────────────────────


def test_a_prompt_with_no_generation_node_says_what_to_connect(client):
    board_id = _board(client)
    prompt = _node(client, board_id, "prompt", title="P", prompt="mèo\nchó")
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 400
    assert "nối" in resp.json()["detail"]


def test_two_generation_targets_is_a_refusal_not_a_guess(client):
    """Which branch gets the lines has no answer the graph can give, and
    guessing fans out the wrong one — billably."""
    board_id, prompt, image, _ = _setup(client, "mèo\nchó")
    other = _node(client, board_id, "video", title="Khác", x=400)
    _wire(client, board_id, prompt["id"], other["id"], target_port="prompt")
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 400
    assert "2" in resp.json()["detail"]


def test_an_empty_prompt_node_is_refused(client):
    board_id, prompt, _, _ = _setup(client, "   \n  ")
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 400


def test_a_huge_paste_is_capped(client):
    """Every line is a billable call. A pasted script becoming three hundred
    of them is the accident this ceiling exists for."""
    board_id, prompt, _, _ = _setup(client, "\n".join(f"dòng {i}" for i in range(80)))
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 400
    assert str(fan_out.MAX_FANOUT) in resp.json()["detail"]


def test_only_a_prompt_node_can_be_fanned_out(client):
    board_id, _, image, _ = _setup(client, "mèo\nchó")
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": image["id"]})
    assert resp.status_code == 400


def test_a_node_from_another_board_is_not_reachable(client):
    board_id, prompt, _, _ = _setup(client, "mèo\nchó")
    other_board = _board(client)
    resp = client.post(f"/api/boards/{other_board}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 404


# ── the preview, and what the estimate then says ──────────────────────


def test_the_preview_says_what_will_change_without_changing_it(client):
    board_id, prompt, _, _ = _setup(client, "mèo\nchó\nchim")
    body = client.get(f"/api/boards/{board_id}/fan-out/{prompt['id']}").json()
    assert body["lines"] == 3 and body["willCreate"] == 2
    assert len(_nodes(board_id, "image")) == 1, "the preview changed the board"


def test_the_estimate_counts_the_fanned_out_nodes(client):
    """The reason this is a separate step: after fan-out the cost dialog
    quotes the number the run actually makes."""
    board_id, prompt, _, _ = _setup(client, "mèo\nchó\nchim")
    before = client.get(f"/api/boards/{board_id}/estimate").json()["billableJobs"]
    client.post(f"/api/boards/{board_id}/fan-out", json={"prompt_node_id": prompt["id"]})
    after = client.get(f"/api/boards/{board_id}/estimate").json()["billableJobs"]
    assert before == 1 and after == 3


# ── pressing it twice must not spend what was already bought ──────────


def _ran(node_id: int, media: str) -> None:
    """Mark a node as having produced a result someone paid for."""
    with get_session() as s:
        node = s.get(Node, node_id)
        node.status = "done"
        node.data = {**(node.data or {}), "mediaId": media, "renderedAt": "now"}
        s.add(node)
        s.commit()


def _data(node_id: int) -> dict:
    with get_session() as s:
        return dict(s.get(Node, node_id).data or {})


def _clones(board_id: int, prompt_short_id: str) -> list[Node]:
    return sorted(
        (
            n for n in _nodes(board_id, "image")
            if (n.data or {}).get("fanOutFrom") == prompt_short_id
        ),
        key=lambda n: (n.data or {}).get("fanOutIndex", 0),
    )


def test_a_second_press_keeps_every_result_already_paid_for(client):
    """`_clean` strips mediaId, which is right for seeding a new clone and
    wrong for a node that exists. Applied to the template and to every clone,
    the second press erased every image the board had already bought — and
    nothing in the product puts one back."""
    board_id, prompt, image, _ = _setup(client, "một\nhai\nba")
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})
    clones = _clones(board_id, prompt["short_id"])
    assert len(clones) == 2
    _ran(image["id"], "m-template")
    for i, clone in enumerate(clones):
        _ran(clone.id, f"m-clone-{i}")

    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})

    assert _data(image["id"]).get("mediaId") == "m-template"
    for i, clone in enumerate(clones):
        assert _data(clone.id).get("mediaId") == f"m-clone-{i}", (
            "a paid result was erased by the second press"
        )


def test_a_changed_line_keeps_its_old_result_until_it_is_re_run(client):
    """The result belongs to the old prompt, so it is stale — but deleting it
    here deletes the only copy. Re-running is the user's call, and the cost
    dialog is where that decision belongs."""
    board_id, prompt, image, _ = _setup(client, "một\nhai")
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})
    clone = _clones(board_id, prompt["short_id"])[0]
    _ran(clone.id, "m-old")

    client.patch(f"/api/nodes/{prompt['id']}", json={"data": {"prompt": "một\nkhác"}})
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})

    after = _data(clone.id)
    assert after["prompt"] == "khác"
    assert after.get("mediaId") == "m-old"


def test_shortening_the_list_after_a_run_does_not_500(client):
    """A clone that ran owns Request rows pointing at it. Deleting the node
    without releasing them is a FOREIGN KEY error, which reaches the user as a
    bare 500 the first time they shorten a list."""
    from flowboard.db.models import Request

    board_id, prompt, image, _ = _setup(client, "một\nhai\nba")
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})
    doomed = _clones(board_id, prompt["short_id"])[-1]
    with get_session() as s:
        s.add(Request(node_id=doomed.id, type="gen_image", params={}, status="done"))
        s.commit()

    client.patch(f"/api/nodes/{prompt['id']}", json={"data": {"prompt": "một"}})
    resp = client.post(f"/api/boards/{board_id}/fan-out",
                       json={"prompt_node_id": prompt["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2

    # The spend record survives the node: it is the only place the user can see
    # that the clip was charged for.
    with get_session() as s:
        rows = list(s.exec(select(Request)).all())
    assert len(rows) == 1 and rows[0].node_id is None


def test_a_reference_wired_after_the_first_press_reaches_old_clones(client):
    """Half the board generating against a photo the other half has never seen
    is the failure; the docstring already promised this and did not do it."""
    board_id, prompt, image, _ = _setup(client, "một\nhai")
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})
    clone = _clones(board_id, prompt["short_id"])[0]

    ref = _node(client, board_id, "character", title="NV", mediaId="m-ref")
    _wire(client, board_id, ref["id"], image["id"])
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})

    with get_session() as s:
        into_clone = [
            e for e in s.exec(select(Edge)).all()
            if e.target_id == clone.id and e.source_id == ref["id"]
        ]
    assert len(into_clone) == 1, "the new reference never reached the old clone"


def test_pressing_twice_does_not_wire_the_same_reference_twice(client):
    board_id, prompt, image, ref = _setup(client, "một\nhai", with_reference=True)
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})
    client.post(f"/api/boards/{board_id}/fan-out",
                json={"prompt_node_id": prompt["id"]})

    clone = _clones(board_id, prompt["short_id"])[0]
    with get_session() as s:
        dupes = [
            e for e in s.exec(select(Edge)).all()
            if e.target_id == clone.id and e.source_id == ref["id"]
        ]
    assert len(dupes) == 1, dupes
