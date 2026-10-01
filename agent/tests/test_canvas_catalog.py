"""The catalogue must agree with the workflows that already exist.

The agent proposes wires from this table. If the table is narrower than
reality, the agent refuses a board a packaged workflow already draws; if it is
wider, the agent proposes a wire the run silently ignores. Both are invisible
until someone pays for a clip built the wrong way.

So the loop is closed from the evidence side: read the eleven packaged
workflows, collect every `(canvas type, dstPort)` pair a real file uses, and
require the catalogue to accept all of them. Deriving the check this way —
rather than restating the table — is what turned up `thumbnail_prompt` having
no field behind it in `postprod_plan.BRANCH_FIELDS`.
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict

import pytest

import flowboard.routes.templates as templates_mod
from flowboard.services import canvas_catalog
from flowboard.services.template_import import NODE_TYPE_MAP


def _workflow_wires() -> list[tuple[str, str, str, str, str]]:
    """`(file, src canvas type, srcPort, dst canvas type, dstPort)` per wire."""
    out: list[tuple[str, str, str, str, str]] = []
    for directory in templates_mod._template_dirs():
        for path in sorted(glob.glob(os.path.join(str(directory), "*.json"))):
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            by_id = {n.get("id"): n.get("type") for n in (doc.get("nodes") or [])}
            for wire in doc.get("connections") or []:
                src = NODE_TYPE_MAP.get(by_id.get(wire.get("srcNode")))
                dst = NODE_TYPE_MAP.get(by_id.get(wire.get("dstNode")))
                if not src or not dst:
                    continue
                out.append((
                    os.path.basename(path),
                    src, wire.get("srcPort"),
                    dst, wire.get("dstPort"),
                ))
    return out


WIRES = _workflow_wires()


def test_the_packaged_workflows_were_actually_read():
    """Guards the whole file: if the template format changes, every assertion
    below would pass vacuously on an empty list."""
    assert len(WIRES) > 20, f"only {len(WIRES)} wires found — format change?"


def test_every_incoming_socket_a_packaged_workflow_uses_is_accepted():
    bad: list[str] = []
    for name, _src, _sp, dst, dp in WIRES:
        check = canvas_catalog.accepts(dst, dp, lane="omni")
        if not check.ok:
            bad.append(f"{name}: {dst}.{dp} — {check.reason}")
    assert not bad, "catalogue refuses wires that ship in the workflows:\n" + "\n".join(
        sorted(set(bad))
    )


def test_every_outgoing_socket_a_packaged_workflow_uses_is_advertised():
    bad: list[str] = []
    for name, src, sp, _dst, _dp in WIRES:
        check = canvas_catalog.emits(src, sp)
        if not check.ok:
            bad.append(f"{name}: {src}.{sp} — {check.reason}")
    assert not bad, "catalogue omits outputs the workflows use:\n" + "\n".join(
        sorted(set(bad))
    )


def test_every_node_type_in_the_catalogue_is_one_the_route_accepts():
    """A type the catalogue offers but `POST /api/nodes` rejects would have the
    agent propose a node that 422s on apply."""
    from flowboard.routes.nodes import NodeType

    allowed = set(getattr(NodeType, "__args__", ()))
    assert allowed, "NodeType is no longer a Literal — update this check"
    assert set(canvas_catalog.NODE_TYPES) == allowed


def test_every_type_with_ports_is_a_known_type():
    for table in (canvas_catalog.PORTS_IN, canvas_catalog.PORTS_OUT):
        unknown = sorted(set(table) - set(canvas_catalog.NODE_TYPES))
        assert not unknown, f"ports declared for unknown types: {unknown}"


def test_every_branch_output_has_a_field_behind_it():
    """The catalogue's branch sockets and `branch_text`'s table are the same
    list by construction. Pinned so a later edit cannot split them: a socket
    with no field falls back to the node's plain prompt, which is how the cover
    image was generated from the scene description."""
    from flowboard.services.postprod_plan import BRANCH_FIELDS

    for node_type in ("prompt", "analyze_video"):
        branch = [p for p in canvas_catalog.PORTS_OUT[node_type] if p != "text"]
        assert branch
        assert all(p in BRANCH_FIELDS for p in branch)


@pytest.mark.parametrize("lane,expected", [(None, 3), ("omni", 10), ("lite", 3)])
def test_the_character_socket_ceiling_comes_from_the_lane(lane, expected):
    assert canvas_catalog.character_port_limit(lane) == expected
    assert canvas_catalog.accepts("video", f"character_{expected}", lane=lane).ok
    assert not canvas_catalog.accepts("video", f"character_{expected + 1}", lane=lane).ok


def test_a_registered_character_is_not_subject_to_the_numeric_ceiling():
    """A runtime socket carries a uuid, not an index — the ceiling is about how
    many reference slots the lane serves, not how the socket is spelled."""
    port = "character_" + "a1b2c3d4-5678-4abc-9def-0123456789ab"
    assert canvas_catalog.accepts("video", port, lane=None).ok


def test_a_character_socket_is_refused_off_a_video_node():
    assert not canvas_catalog.accepts("image", "character_1").ok


def test_a_type_with_no_inputs_refuses_even_an_unnamed_wire():
    """The graph would accept it and the run would read nothing — a wire that
    looks connected and does nothing."""
    for node_type in ("note", "visual_asset", "character"):
        assert not canvas_catalog.accepts(node_type, None).ok


def test_an_unnamed_wire_is_accepted_where_a_consumer_takes_one():
    """Every hand-drawn edge arrives unnamed, so refusing it would make the
    agent unable to describe a board a person can draw."""
    for node_type in ("video", "image", "edit_video", "merge_video"):
        assert canvas_catalog.accepts(node_type, None).ok


def test_describe_is_json_safe():
    """It goes into a model prompt and over HTTP."""
    body = canvas_catalog.describe()
    assert json.loads(json.dumps(body)) == body
    assert body["characterPortLimits"]["omni"] == 10
