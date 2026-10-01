"""What survives the trip from a plan spec onto the board.

`materialize_plan` was dropping two things, and both failed silently in the
way that costs money rather than the way that raises.

**Settings.** `storyboard.build_spec` writes generation settings under
``params["sourceSettings"]`` because that is the one place
`node_settings.merged_settings` reads them from. `materialize_plan` copied
only ``title`` and ``prompt`` onto the node, so the board looked configured
on the canvas — the user picked 9:16, 8 seconds, the free lane — while every
dispatch went out at Flow's defaults: landscape, default duration, and the
lane that charges credits.

**Ports.** `build_spec` writes ``{"kind": "start_frame"}`` and
``{"kind": "media"}``. Those name the destination PORT, not the edge's role.
`materialize_plan` coerced anything outside ``{ref, hint}`` to ``"ref"`` and
threw the name away, leaving a board where a start-frame wire and a plain
reference are indistinguishable.

These tests ask `node_settings` and read the Edge row rather than inspecting
the spec dict, so they fail if the value lands anywhere the real readers do
not look.
"""
from __future__ import annotations

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Edge, Node, Plan
from flowboard.services import node_settings, pipeline_executor


def _make_board(client, name="P") -> dict:
    return client.post("/api/boards", json={"name": name}).json()


def _make_plan(board_id: int, spec: dict) -> int:
    with get_session() as s:
        plan = Plan(board_id=board_id, spec=spec, status="draft")
        s.add(plan)
        s.commit()
        s.refresh(plan)
        return plan.id  # type: ignore[return-value]


def _materialize(client, spec: dict) -> int:
    board = _make_board(client)
    plan_id = _make_plan(board["id"], spec)
    with get_session() as s:
        pipeline_executor.materialize_plan(s, plan_id)
        s.commit()
    return board["id"]


def _nodes(board_id: int) -> dict[str, Node]:
    with get_session() as s:
        rows = list(s.exec(select(Node).where(Node.board_id == board_id)).all())
    return {(n.data or {}).get("title", ""): n for n in rows}


# ── settings reach the reader that dispatches ─────────────────────────


def test_settings_survive_onto_the_node(client):
    """The load-bearing assertion: `node_settings` — the module the
    executor and the cost estimate both call — must be able to read the
    values back off the materialised node."""
    board_id = _materialize(client, {
        "nodes": [{
            "tmp_id": "v1",
            "type": "video",
            "params": {
                "title": "Cảnh 1",
                "prompt": "một người mẫu bước tới",
                "sourceSettings": {
                    "ratio": "📱 9:16",
                    "quality": "📉 LITE LOWER priority",
                    "duration": 8,
                },
            },
        }],
        "edges": [],
    })
    node = _nodes(board_id)["Cảnh 1"]
    # `for_dispatch` is what the executor actually calls before it builds the
    # request, so asserting on it proves the settings reach dispatch rather
    # than merely reaching the row.
    params = node_settings.for_dispatch(node)
    assert params["aspect_ratio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"
    assert params["duration_s"] == 8
    # The money assertion. `lite_relaxed` is the 0-credit Ultra lane; reading
    # it as plain `lite` bills credits for a job that costs none.
    assert params["video_quality"] == "lite_relaxed"


def test_a_node_without_settings_stays_clean(client):
    """No empty `sourceSettings` key on a node that never had one — an
    empty dict reads as "configured with nothing" to anything that checks
    for the key's presence rather than its content."""
    board_id = _materialize(client, {
        "nodes": [{"tmp_id": "p1", "type": "prompt", "params": {"title": "Brief"}}],
        "edges": [],
    })
    assert "sourceSettings" not in (_nodes(board_id)["Brief"].data or {})


# ── ports survive onto the edge ───────────────────────────────────────


def _edges(board_id: int) -> list[Edge]:
    with get_session() as s:
        return list(s.exec(select(Edge).where(Edge.board_id == board_id)).all())


def test_a_port_named_in_kind_lands_on_target_port(client):
    """`build_spec` writes `kind: "start_frame"`. That is a port name, and
    it has to end up in `target_port` — not be flattened to "ref"."""
    board_id = _materialize(client, {
        "nodes": [
            {"tmp_id": "i1", "type": "image", "params": {"title": "Khung đầu"}},
            {"tmp_id": "v1", "type": "video", "params": {"title": "Cảnh"}},
        ],
        "edges": [{"from": "i1", "to": "v1", "kind": "start_frame"}],
    })
    edge = _edges(board_id)[0]
    assert edge.target_port == "start_frame"
    # `kind` still says what the edge IS; the port says where it lands.
    assert edge.kind == "ref"


def test_an_explicit_target_port_wins_over_kind(client):
    board_id = _materialize(client, {
        "nodes": [
            {"tmp_id": "a", "type": "image", "params": {"title": "A"}},
            {"tmp_id": "b", "type": "video", "params": {"title": "B"}},
        ],
        "edges": [{
            "from": "a", "to": "b", "kind": "ref",
            "source_port": "image_out_1", "target_port": "image_1",
        }],
    })
    edge = _edges(board_id)[0]
    assert (edge.source_port, edge.target_port) == ("image_out_1", "image_1")


def test_a_plain_ref_edge_has_no_port(client):
    """`ref` and `hint` are roles, not ports. Writing "ref" into
    `target_port` would invent a socket no node has."""
    board_id = _materialize(client, {
        "nodes": [
            {"tmp_id": "a", "type": "prompt", "params": {"title": "A"}},
            {"tmp_id": "b", "type": "image", "params": {"title": "B"}},
        ],
        "edges": [{"from": "a", "to": "b", "kind": "ref"}],
    })
    edge = _edges(board_id)[0]
    assert edge.target_port is None and edge.kind == "ref"


# ── the whole storyboard path, end to end ─────────────────────────────


def test_a_storyboard_spec_materialises_configured_and_wired(client):
    """The regression that started this: a board built by the storyboard
    route came out unconfigured and unwired. Build the spec through the
    real `build_spec` rather than a hand-written dict, so a change to its
    key spellings breaks this test too."""
    from flowboard.services import storyboard

    spec = storyboard.build_spec(
        [
            {"title": "Cảnh 1", "image": "a room", "video": "she walks in"},
            {"title": "Cảnh 2", "image": "", "video": "she turns"},
        ],
        structure="chain", aspect="📱 9:16",
        quality="📉 LITE LOWER priority", seconds=8,
    )
    board_id = _materialize(client, spec)

    videos = [n for n in _nodes(board_id).values() if n.type == "video"]
    assert len(videos) == 2
    for node in videos:
        params = node_settings.for_dispatch(node)
        assert params["duration_s"] == 8
        assert params["video_quality"] == "lite_relaxed"
        assert params["aspect_ratio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"

    ports = sorted(e.target_port or "" for e in _edges(board_id))
    # Scene 1: image → video.start_frame. Scene 2: video → frame.media,
    # then frame → video.start_frame.
    assert ports == ["media", "start_frame", "start_frame"]
