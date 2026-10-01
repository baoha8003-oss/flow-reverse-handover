"""Importing a packaged workflow must not quietly lose part of the graph.

These run against the nine real files the tool ships with, not fixtures. A
mapping that drops a node type would still produce a board that opens and
looks plausible — the only way to catch it is to count what came out against
what went in.
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Edge, Node
from flowboard.routes.templates import TEMPLATE_DIR
from flowboard.services import template_import

TEMPLATE_FILES = sorted(p.name for p in TEMPLATE_DIR.glob("*.json"))


def _doc(name: str) -> dict:
    return json.loads((TEMPLATE_DIR / name).read_text(encoding="utf-8-sig"))


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
@pytest.mark.parametrize("name", TEMPLATE_FILES)
def test_every_node_and_wire_survives_the_import(client, name):
    """The count that matters. 107 nodes and 129 wires across the nine files;
    a type missing from the map would show up here as a shortfall."""
    doc = _doc(name)
    resp = client.post(f"/api/templates/{name}/import")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["nodes"] == len(doc["nodes"]), f"{name}: lost nodes"
    assert body["nodes"] == body["nodesInFile"]
    assert body["edges"] == len(doc["connections"]), f"{name}: lost wires"
    assert body["danglingEdges"] == 0, f"{name}: wire with a missing endpoint"


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
@pytest.mark.parametrize("name", TEMPLATE_FILES)
def test_the_rows_really_exist(client, name):
    """The response is a summary; this checks the database agrees with it."""
    body = client.post(f"/api/templates/{name}/import").json()
    with get_session() as s:
        nodes = list(s.exec(select(Node).where(Node.board_id == body["boardId"])).all())
        edges = list(s.exec(select(Edge).where(Edge.board_id == body["boardId"])).all())
    assert len(nodes) == body["nodes"]
    assert len(edges) == body["edges"]


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_no_shipped_node_type_is_unsupported_any_more():
    """The count tests above cannot catch a type dropped from the map: it
    would import as a note and the totals would still match. This is the
    check that fails. `sync_image_voice` used to be the one exception; it
    now has a Ken Burns implementation, so the note pile must be empty."""
    seen: set[str] = set()
    with get_session() as s:
        for name in TEMPLATE_FILES:
            summary = template_import.import_template(s, _doc(name), name=name)
            seen.update(summary["unsupported"])
    assert seen == set()


# Written out a second time, deliberately. Checking the import against
# NODE_TYPE_MAP would only prove the map equals itself — pointing `gen_video`
# at `image` would pass. This is the contract: a video step must arrive as a
# video node, or the board runs the wrong operation and bills for it.
EXPECTED_CANVAS_TYPE = {
    "gen_image": "image",
    "gen_video": "video",
    "text_prompt": "prompt",
    "prompt_list": "prompt",
    "prompt_mau": "prompt",
    "gemini_prompt": "prompt",
    "upload_media": "visual_asset",
    "link_list": "visual_asset",
    "video_image_list": "visual_asset",
    "analyze_video": "analyze_video",
    "merge_video": "merge_video",
    "edit_video": "edit_video",
    "extract_last_frame": "extract_last_frame",
    "add_bgm": "add_bgm",
    "create_voice": "create_voice",
    "align_video_voice": "align_video_voice",
    "sync_image_voice": "sync_image_voice",
}


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_each_node_lands_on_the_canvas_type_its_step_means():
    """Node-for-node, against the files. A mis-mapped type produces a board
    that looks right and runs the wrong operation."""
    covered: set[str] = set()
    with get_session() as s:
        for name in TEMPLATE_FILES:
            summary = template_import.import_template(s, _doc(name), name=name)
            nodes = list(
                s.exec(select(Node).where(Node.board_id == summary["board_id"])).all()
            )
            for node in nodes:
                exe_type = (node.data or {}).get("sourceType")
                assert exe_type in EXPECTED_CANVAS_TYPE, f"{name}: new type {exe_type}"
                assert node.type == EXPECTED_CANVAS_TYPE[exe_type], (
                    f"{name}: {exe_type} imported as {node.type}, "
                    f"expected {EXPECTED_CANVAS_TYPE[exe_type]}"
                )
                covered.add(exe_type)
    # Every type the shipped files use has been exercised above, so the table
    # cannot rot into covering only the easy cases.
    assert covered == set(EXPECTED_CANVAS_TYPE)


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_every_mapped_target_is_a_type_the_canvas_accepts():
    """A map entry pointing at a type the API rejects would import rows the
    canvas cannot then update."""
    from flowboard.routes.nodes import NodeType

    allowed = set(NodeType.__args__)  # type: ignore[attr-defined]
    assert set(template_import.NODE_TYPE_MAP.values()) <= allowed


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
@pytest.mark.parametrize("name", TEMPLATE_FILES)
def test_the_plan_points_at_exactly_the_imported_nodes(client, name):
    """The plan is how an imported board is run — `run_pipeline` reads
    `_materialized_node_ids` and nothing else. A plan listing the wrong ids
    would run some other board's nodes, or none."""
    from flowboard.db.models import Plan

    body = client.post(f"/api/templates/{name}/import").json()
    with get_session() as s:
        plan = s.get(Plan, body["planId"])
        node_ids = {
            n.id for n in s.exec(select(Node).where(Node.board_id == body["boardId"])).all()
        }
    assert plan is not None
    assert plan.board_id == body["boardId"]
    assert set(plan.spec["_materialized_node_ids"]) == node_ids
    # `draft` would make the run refuse; the import IS the approval here.
    assert plan.status == "approved"


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_running_an_imported_plan_reuses_its_nodes_instead_of_duplicating_them():
    """`POST /api/plans/{id}/run` calls `materialize_plan` first. An imported
    plan has no `spec.nodes` to build from — it points at rows that already
    exist. If materialise did not recognise that, every run would either
    build a second copy of the board or find nothing to run.

    This is the assumption the whole "run an imported workflow" path rests
    on, so it is checked rather than believed."""
    from flowboard.services.pipeline_executor import materialize_plan

    name = TEMPLATE_FILES[0]
    with get_session() as s:
        summary = template_import.import_template(s, _doc(name), name=name)
        before = list(
            s.exec(select(Node).where(Node.board_id == summary["board_id"])).all()
        )
        result = materialize_plan(s, summary["plan_id"])
        after = list(
            s.exec(select(Node).where(Node.board_id == summary["board_id"])).all()
        )

    assert result["created"] is False, "materialise built a second copy"
    assert set(result["node_ids"]) == {n.id for n in before}
    assert len(after) == len(before), "materialise added nodes to the board"


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_ports_are_carried_onto_the_edges():
    """Without ports the executor cannot tell a start frame from an end frame.
    Every wire in these files names both ends, so every imported edge must."""
    name = TEMPLATE_FILES[0]
    doc = _doc(name)
    with get_session() as s:
        summary = template_import.import_template(s, doc, name="port check")
        edges = list(
            s.exec(select(Edge).where(Edge.board_id == summary["board_id"])).all()
        )
    assert edges
    assert all(e.source_port and e.target_port for e in edges)

    # And they must be the file's own port names, not invented ones.
    wanted = {(c["srcPort"], c["dstPort"]) for c in doc["connections"]}
    got = {(e.source_port, e.target_port) for e in edges}
    assert got <= wanted


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_prompt_text_lands_on_the_node():
    """The tuned prompt paragraphs are the reason these files are worth
    keeping. An import that drops them keeps the shape and loses the value."""
    with get_session() as s:
        for name in TEMPLATE_FILES:
            doc = _doc(name)
            wanted = {
                n["settings"]["prompt"].strip()
                for n in doc["nodes"]
                if isinstance(n.get("settings"), dict)
                and isinstance(n["settings"].get("prompt"), str)
                and n["settings"]["prompt"].strip()
            }
            if not wanted:
                continue
            summary = template_import.import_template(s, doc, name=name)
            nodes = list(
                s.exec(select(Node).where(Node.board_id == summary["board_id"])).all()
            )
            got = {
                (n.data or {}).get("prompt", "").strip()
                for n in nodes
                if (n.data or {}).get("prompt")
            }
            assert wanted <= got, f"{name}: prompt text lost"
            return
    pytest.skip("no template carries prompt text")


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_an_unmapped_type_becomes_a_visible_note_not_a_hole():
    """A template from a newer build of the exe would carry a type this map
    has never seen. It must still appear on the board, saying what it was —
    dropping it would leave the graph looking complete while a step is
    missing."""
    doc = {
        "nodes": [
            {
                "id": "a",
                "type": "some_future_exe_node",
                "label": "Bước lạ",
                "x": 10,
                "y": 20,
                "width": 200,
                "height": 120,
                "settings": {"effect": "zoom", "zoom_speed": 2},
            }
        ],
        "connections": [],
    }
    with get_session() as s:
        summary = template_import.import_template(s, doc, name="unmapped")
        node = s.exec(
            select(Node).where(Node.board_id == summary["board_id"])
        ).first()
    assert summary["nodes"] == 1
    assert summary["unsupported"] == ["some_future_exe_node"]
    assert node.type == "note"
    assert node.data["text"]
    # The original configuration survives, because it cannot be re-derived.
    assert node.data["sourceType"] == "some_future_exe_node"
    assert node.data["sourceSettings"]["effect"] == "zoom"


def test_layout_is_preserved():
    """These boards were arranged by hand and read left to right. Re-laying
    them out would destroy the thing that makes them followable."""
    doc = {
        "nodes": [
            {
                "id": "a",
                "type": "text_prompt",
                "label": "P",
                "x": -3472.5,
                "y": 118,
                "width": 320,
                "height": 200,
                "settings": {},
            }
        ],
        "connections": [],
    }
    with get_session() as s:
        summary = template_import.import_template(s, doc, name="layout")
        node = s.exec(
            select(Node).where(Node.board_id == summary["board_id"])
        ).first()
    assert (node.x, node.y) == (-3472.5, 118.0)
    assert (node.w, node.h) == (320.0, 200.0)


def test_a_wire_to_a_node_that_did_not_import_is_counted_not_hidden():
    doc = {
        "nodes": [
            {"id": "a", "type": "text_prompt", "label": "P", "x": 0, "y": 0,
             "width": 200, "height": 100, "settings": {}},
        ],
        "connections": [
            {"id": "e1", "srcNode": "a", "srcPort": "text",
             "dstNode": "ghost", "dstPort": "prompt"},
        ],
    }
    with get_session() as s:
        summary = template_import.import_template(s, doc, name="dangling")
    assert summary["edges"] == 0
    assert summary["dangling_edges"] == 1


def test_a_garbage_coordinate_does_not_reach_the_database():
    doc = {
        "nodes": [
            {"id": "a", "type": "text_prompt", "label": "P", "x": "nope",
             "y": float("nan"), "width": 0, "height": -5, "settings": {}},
        ],
        "connections": [],
    }
    with get_session() as s:
        summary = template_import.import_template(s, doc, name="garbage")
        node = s.exec(
            select(Node).where(Node.board_id == summary["board_id"])
        ).first()
    assert node.x == 0.0 and node.y == 0.0
    assert node.w >= 160.0 and node.h >= 90.0


def test_importing_twice_makes_two_boards():
    """These are starting points, not documents. A second import must not
    overwrite whatever was edited into the first."""
    doc = {"nodes": [], "connections": []}
    with get_session() as s:
        one = template_import.import_template(s, doc, name="twice")
        two = template_import.import_template(s, doc, name="twice")
    assert one["board_id"] != two["board_id"]


def test_unknown_template_name_is_a_404(client):
    assert client.post("/api/templates/nope.json/import").status_code == 404


def test_a_path_traversal_name_is_refused(client):
    resp = client.post("/api/templates/..%2F..%2Fsecrets.json/import")
    assert resp.status_code in (400, 404)
