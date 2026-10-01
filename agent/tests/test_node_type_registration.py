"""A post-production node type has to be registered everywhere, or it lies.

Adding one means touching eight places across two languages. Miss one and
the failure is silent in a specific, nasty way: the node appears in the
palette, drops onto the canvas, connects to its neighbours, and then does
nothing when the board runs — or worse, gets quoted as a billable Flow call
because the estimate never learned it was local.

These tests derive the expected set from `_POSTPROD_NODE_TYPES`, the
executor's own list, so a new type is checked automatically rather than
needing a test written for it. That is the point: the bug class is
"someone added a type and forgot a registry", and a test that has to be
remembered does not defend against forgetting.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from flowboard.routes.estimate import BILLABLE_TYPES, INERT_TYPES, LOCAL_TYPES
from flowboard.services.pipeline_executor import _POSTPROD_NODE_TYPES


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("node_type", sorted(_POSTPROD_NODE_TYPES))
def test_the_api_accepts_the_type(node_type):
    """`routes.nodes.NodeType` gates node creation. Missing here, the node
    cannot be added to a board at all — a 422 from the palette."""
    from flowboard.routes.nodes import NodeType

    assert node_type in NodeType.__args__


@pytest.mark.parametrize("node_type", sorted(_POSTPROD_NODE_TYPES))
def test_the_estimate_counts_it_as_local_and_free(node_type):
    """These run on this machine. Missing from LOCAL_TYPES, a node falls
    through to the unclassified branch and is quoted as BILLABLE — the cost
    dialog then warns about credits for an ffmpeg pass."""
    assert node_type in LOCAL_TYPES
    assert node_type not in BILLABLE_TYPES
    assert node_type not in INERT_TYPES


@pytest.mark.parametrize("node_type", sorted(_POSTPROD_NODE_TYPES))
def test_the_planner_recognises_the_type(node_type, caplog):
    """`ops_for` must have a branch for the type.

    Asserted through the planner's own fallthrough warning rather than by
    demanding a non-empty result, because an empty plan is legitimate for
    two different reasons and only one of them is a bug:

    * `analyze_video` returns [] BY DESIGN — it is not an ffmpeg op at all,
      it goes through `/api/vision/video`, and planning a local op for it
      would make the node report success having done something else;
    * `add_bgm` and `edit_video` return [] when nothing is configured,
      which is also right.

    An unregistered type also returns [], and looks identical from outside.
    The warning is what tells them apart.
    """
    import logging
    from types import SimpleNamespace

    from flowboard.services.postprod_plan import Upstream, ops_for

    clip = SimpleNamespace(
        id=2, type="video", data={"mediaId": "a", "mediaIds": ["a", "b"]}
    )
    voice = SimpleNamespace(id=3, type="create_voice", data={"mediaId": "v"})
    text = SimpleNamespace(id=4, type="prompt", data={"prompt": "hello there"})
    node = SimpleNamespace(id=1, type=node_type, data={"sourceSettings": {}})

    with caplog.at_level(logging.WARNING, logger="flowboard.services.postprod_plan"):
        ops_for(
            node,
            [Upstream(clip, "media"), Upstream(voice, "voice"), Upstream(text, "text")],
        )
    unhandled = [r for r in caplog.records if "no ops defined" in r.getMessage()]
    assert not unhandled, f"{node_type} has no branch in ops_for"


def test_a_type_with_no_branch_is_caught():
    """The detector above is only worth having if it fires. A type the
    planner has never heard of must trip the same warning."""
    import logging
    from types import SimpleNamespace

    from flowboard.services.postprod_plan import ops_for

    caplog_logger = logging.getLogger("flowboard.services.postprod_plan")
    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Catch()
    caplog_logger.addHandler(handler)
    try:
        ops_for(SimpleNamespace(id=1, type="not_a_real_node", data={}), [])
    finally:
        caplog_logger.removeHandler(handler)
    assert any("no ops defined" in r.getMessage() for r in records)


def test_every_planned_op_has_a_handler():
    """The planner names ops; the worker implements them. A name with no
    handler dispatches a request that fails with `unknown_op` at runtime —
    after the earlier passes in the chain have already run and spent time.

    Node types and op names are deliberately NOT the same thing (a
    `merge_video` node plans a `concat` op), so this checks the op names the
    planner actually emits rather than assuming they match.
    """
    from types import SimpleNamespace

    from flowboard.services.postprod_plan import Upstream, ops_for
    from flowboard.worker.postprod_handler import _OPS

    clip = SimpleNamespace(
        id=2, type="video", data={"mediaId": "a", "mediaIds": ["a", "b"]}
    )
    voice = SimpleNamespace(id=3, type="create_voice", data={"mediaId": "v"})
    text = SimpleNamespace(id=4, type="prompt", data={"prompt": "hello there"})

    missing: list[str] = []
    for node_type in sorted(_POSTPROD_NODE_TYPES):
        node = SimpleNamespace(id=1, type=node_type, data={"sourceSettings": {}})
        for op in ops_for(
            node,
            [Upstream(clip, "media"), Upstream(voice, "voice"), Upstream(text, "text")],
        ):
            if op["op"] not in _OPS:
                missing.append(f"{node_type} -> {op['op']}")
    assert not missing, f"planned ops with no handler: {missing}"


def test_the_frontend_knows_the_same_types():
    """The palette is a separate list in TypeScript. Missing there, the type
    is unreachable from the UI however well the backend supports it —
    present in the other direction, the palette offers a node the API
    rejects with a 422.
    """
    src = (_repo_root() / "frontend" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    block = re.search(
        r"POSTPROD_NODE_TYPES\s*=\s*\[(.*?)\]\s*as const", src, re.S
    )
    assert block, "POSTPROD_NODE_TYPES not found in client.ts"
    listed = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert listed == set(_POSTPROD_NODE_TYPES), (
        f"frontend/backend disagree — only in backend: "
        f"{set(_POSTPROD_NODE_TYPES) - listed}, only in frontend: "
        f"{listed - set(_POSTPROD_NODE_TYPES)}"
    )


@pytest.mark.parametrize(
    "component,pattern",
    [
        ("AddNodePalette.tsx", r'type:\s*"([a-z_]+)"'),
        ("NodeCard.tsx", r"^\s+([a-z_]+):\s*\""),
        ("store/board.ts", r"^\s+([a-z_]+):\s*\""),
    ],
)
def test_every_type_can_be_added_and_rendered(component, pattern):
    """Three more TypeScript files carry their own list: the palette a node
    is added from, the icon map it renders with, and the label map. A type
    missing from the palette cannot be created; missing from the others it
    renders blank."""
    path = _repo_root() / "frontend" / "src" / (
        component if "/" in component else f"canvas/{component}"
    )
    listed = set(re.findall(pattern, path.read_text(encoding="utf-8"), re.M))
    missing = set(_POSTPROD_NODE_TYPES) - listed
    assert not missing, f"{component} does not know about {sorted(missing)}"
