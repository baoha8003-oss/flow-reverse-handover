"""Turn one of the packaged tool's workflow files into a real board.

The nine files under ``data_general/system_workflows`` are node graphs: 107
nodes and 129 wires between them, with the prompt text and the order of
operations someone spent real work tuning. Serving them only as a read-only
recipe threw the graph away and kept the prose.

The mapping below is the whole trick. All seventeen node types that appear in the shipped workflow files
either already exist on this canvas under a different name (its four
prompt-ish types are all one ``prompt`` node here, its three list-ish input
types are all ``visual_asset``) or now have a post-production node of their
own.

``UNSUPPORTED_TYPES`` is empty and the code path that reads it is kept
anyway: a template from a newer build of the exe would carry a type this map
has never seen, and importing it as a visible note beats dropping it.

Nothing here dispatches or costs anything. Import builds rows; running the
board is a separate, explicit act.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from flowboard.db.models import Board, Edge, Node, Plan
from flowboard.short_id import generate_unique_short_id

logger = logging.getLogger(__name__)

#: exe node type → this canvas's node type.
NODE_TYPE_MAP: dict[str, str] = {
    # Generation
    "gen_image": "image",
    "gen_video": "video",
    # Every prompt-ish type is one node here. The exe distinguishes them by
    # how the text is authored (typed, listed, templated, or written by
    # Gemini); the graph position and the payload are identical.
    "text_prompt": "prompt",
    "prompt_list": "prompt",
    "prompt_mau": "prompt",
    "gemini_prompt": "prompt",
    # Every input-list type is a bag of media.
    "upload_media": "visual_asset",
    "link_list": "visual_asset",
    "video_image_list": "visual_asset",
    # Post-production, one canvas type each.
    # Motion transfer. Its own type here rather than folded into `video`:
    # it has three named image slots and a local-only driving clip, and a
    # `video` node has no way to say either.
    # Motion Control lands as a note rather than a runnable node: this build
    # has no Edit Video endpoint, and importing it as something dispatchable
    # would spend credits on a reference-image request instead.
    "motion_control": "note",
    "analyze_video": "analyze_video",
    "merge_video": "merge_video",
    "edit_video": "edit_video",
    "extract_last_frame": "extract_last_frame",
    "add_bgm": "add_bgm",
    "create_voice": "create_voice",
    "align_video_voice": "align_video_voice",
    "sync_image_voice": "sync_image_voice",
}

#: Types with no path in this build. They import as notes rather than being
#: dropped: a step you can see and do by hand beats a hole in the graph.
UNSUPPORTED_TYPES: dict[str, str] = {}

#: Settings keys that hold the prompt text worth keeping on the node itself.
_PROMPT_KEYS = ("prompt", "custom_voice_prompt", "thumbnail_prompt")

#: The canvas draws nodes at these sizes; the exe's are close but not equal.
_MIN_W, _MIN_H = 160.0, 90.0
_MAX_W, _MAX_H = 2000.0, 2000.0
_COORD_LIMIT = 1_000_000.0


def _clamp(value: Any, lo: float, hi: float, fallback: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if num != num:  # NaN — comparisons below would silently pass it through
        return fallback
    return max(lo, min(hi, num))


def _title_of(raw: dict) -> str:
    label = raw.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()[:200]
    return str(raw.get("type") or "node")


def _prompt_of(settings: dict) -> Optional[str]:
    for key in _PROMPT_KEYS:
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def read_template(path: Path) -> dict:
    """Parse one workflow file. Raises ValueError on anything unusable."""
    try:
        # utf-8-sig: some of the shipped files carry a BOM.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable template: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("template is not an object")
    return raw


def import_template(session, raw: dict, *, name: str) -> dict:
    """Create a Board with the template's nodes and wires. Returns a summary.

    Coordinates are kept as-is so the board opens looking like the original —
    the layout is part of what makes these graphs readable.
    """
    board = Board(name=name)
    session.add(board)
    session.commit()
    session.refresh(board)
    board_id = board.id
    assert board_id is not None

    nodes_raw = raw.get("nodes")
    edges_raw = raw.get("connections")
    if not isinstance(nodes_raw, list):
        nodes_raw = []
    if not isinstance(edges_raw, list):
        edges_raw = []

    by_source_id: dict[str, Node] = {}
    unsupported: list[str] = []

    for entry in nodes_raw:
        if not isinstance(entry, dict):
            continue
        source_id = entry.get("id")
        exe_type = entry.get("type")
        if not isinstance(source_id, str) or not source_id:
            continue
        if not isinstance(exe_type, str):
            continue

        settings = entry.get("settings")
        if not isinstance(settings, dict):
            settings = {}

        canvas_type = NODE_TYPE_MAP.get(exe_type)
        if canvas_type is None:
            if exe_type not in UNSUPPORTED_TYPES:
                # An exe version we have not seen. Import it as a note too —
                # losing a node silently is the one outcome worth avoiding.
                logger.warning("template %s: unknown node type %r", name, exe_type)
            canvas_type = "note"
            unsupported.append(exe_type)

        data: dict[str, Any] = {"title": _title_of(entry)}
        prompt = _prompt_of(settings)
        if prompt:
            data["prompt"] = prompt
        if canvas_type == "note":
            data["text"] = UNSUPPORTED_TYPES.get(
                exe_type, f"Bước “{exe_type}” của tool gốc — bản này chưa có."
            )
        # Keep the original type and settings verbatim. The canvas ignores
        # them today, but they are the record of what the step was configured
        # to do, and re-deriving them from the exe later is not possible.
        data["sourceType"] = exe_type
        data["sourceSettings"] = settings

        node = Node(
            board_id=board_id,
            short_id=generate_unique_short_id(session, board_id),
            type=canvas_type,
            x=_clamp(entry.get("x"), -_COORD_LIMIT, _COORD_LIMIT, 0.0),
            y=_clamp(entry.get("y"), -_COORD_LIMIT, _COORD_LIMIT, 0.0),
            w=_clamp(entry.get("width"), _MIN_W, _MAX_W, 240.0),
            h=_clamp(entry.get("height"), _MIN_H, _MAX_H, 160.0),
            data=data,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        by_source_id[source_id] = node

    edges_made = 0
    dangling = 0
    for entry in edges_raw:
        if not isinstance(entry, dict):
            continue
        src = by_source_id.get(entry.get("srcNode"))
        dst = by_source_id.get(entry.get("dstNode"))
        if src is None or dst is None:
            # A wire whose endpoint did not import. Counted and reported, not
            # dropped in silence — it means the graph is incomplete.
            dangling += 1
            continue
        src_port = entry.get("srcPort")
        dst_port = entry.get("dstPort")
        session.add(
            Edge(
                board_id=board_id,
                source_id=src.id,
                target_id=dst.id,
                source_port=src_port if isinstance(src_port, str) else None,
                target_port=dst_port if isinstance(dst_port, str) else None,
            )
        )
        edges_made += 1
    session.commit()

    # A Plan over exactly these nodes, so the board can be run with the
    # executor that already exists rather than a second one written for
    # imports. `run_pipeline` reads `_materialized_node_ids` and nothing else
    # from the spec, and the nodes are already in the database — so the plan
    # is a handle onto them, not a copy of them.
    plan = Plan(
        board_id=board_id,
        status="approved",
        spec={
            "nodes": [],
            "edges": [],
            "_materialized_node_ids": [n.id for n in by_source_id.values()],
            "_source": "template_import",
            "_template": name,
        },
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)

    return {
        "board_id": board_id,
        "plan_id": plan.id,
        "name": name,
        "nodes": len(by_source_id),
        "nodes_in_file": len([n for n in nodes_raw if isinstance(n, dict)]),
        "edges": edges_made,
        "edges_in_file": len([e for e in edges_raw if isinstance(e, dict)]),
        "dangling_edges": dangling,
        "unsupported": sorted(set(unsupported)),
    }
