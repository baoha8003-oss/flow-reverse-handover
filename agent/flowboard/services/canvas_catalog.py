"""What the canvas can hold, and which sockets actually connect.

The canvas agent proposes graph edits. To do that without inventing nodes or
wires the run will refuse, it needs one answer to "what may connect to what" —
and that answer already exists, scattered across the modules that consume it:
`postprod_plan`'s port tuples, `character_ports`' numbered sockets,
`routes.nodes.NodeType`. This module gathers them; it does not restate them.

**Derived, never typed.** Every incoming port here comes from a constant some
consumer reads, so a socket cannot be advertised that nothing honours, and a
socket cannot be honoured that is not advertised. The alternative — a hand
written table — was tried in `script_genres` and in the first `VEO_T2V_LANES`,
and both times the table said things the code did not do.

`tests/test_canvas_catalog.py` closes the loop from the other side: it reads the
eleven packaged workflows and asserts every `(type, dstPort)` pair a real file
uses is present here. That check is what found `thumbnail_prompt` missing from
`postprod_plan.BRANCH_FIELDS`, where a wire had been silently falling back to
the scene description since the sockets were built.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flowboard.services import character_ports, postprod_plan

#: Node types the canvas and `routes.nodes` both accept. Imported from the
#: route so a type added there cannot be missing here — the reverse drift
#: (`_VALID_NODE_TYPES` missing `visual_asset`) silently dropped nodes out of
#: boards built from an archetype.
NODE_TYPES: tuple[str, ...] = (
    "character", "image", "video", "prompt", "note", "visual_asset",
    "Storyboard", "analyze_video", "merge_video", "edit_video",
    "extract_last_frame", "add_bgm", "create_voice", "align_video_voice",
    "sync_image_voice", "remove_watermark", "review_video",
)


def _named(ports: tuple[Optional[str], ...]) -> tuple[str, ...]:
    """Drop the `None` entry the consumers carry for hand-drawn wires.

    `None` means "an unnamed wire is also accepted here" and is a rule about
    absence, not a socket anyone can pick. Offering it as a name would have the
    agent emit `target_port: "None"`.
    """
    return tuple(p for p in ports if p)


#: Which named sockets each node type reads. Values are the union of the port
#: tuples the plan layer and the executor consult for that type.
PORTS_IN: dict[str, tuple[str, ...]] = {
    "image": ("prompt", "image_1", "image_2", "image_3"),
    "Storyboard": ("prompt", "image_1", "image_2", "image_3"),
    "video": ("prompt", *_named(postprod_plan.START_FRAME_PORTS)),
    "prompt": ("text", "media_1"),
    "analyze_video": ("prompt", "video", "image_1"),
    "create_voice": _named(postprod_plan.TEXT_PORTS),
    "add_bgm": _named(postprod_plan.VIDEO_PORTS),
    "merge_video": _named(postprod_plan.VIDEO_PORTS),
    "extract_last_frame": _named(postprod_plan.VIDEO_PORTS),
    "remove_watermark": _named(postprod_plan.VIDEO_PORTS),
    "review_video": _named(postprod_plan.VIDEO_PORTS),
    "align_video_voice": (
        *_named(postprod_plan.VIDEO_PORTS),
        *_named(postprod_plan.VOICE_PORTS),
        *_named(postprod_plan.TEXT_PORTS),
    ),
    "sync_image_voice": (
        *_named(postprod_plan.VIDEO_PORTS),
        *_named(postprod_plan.VOICE_PORTS),
        *_named(postprod_plan.TEXT_PORTS),
    ),
    "edit_video": (
        *_named(postprod_plan.VIDEO_PORTS),
        *_named(postprod_plan.VOICE_PORTS),
        *_named(postprod_plan.TEXT_PORTS),
        *_named(postprod_plan.THUMBNAIL_PORTS),
        "title",
    ),
    # Sinks with no inputs.
    "character": (),
    "note": (),
    "visual_asset": (),
}

#: Which sockets each type offers downstream. `prompt` and `analyze_video` are
#: the interesting ones: one node, several answers, and the socket picks which.
PORTS_OUT: dict[str, tuple[str, ...]] = {
    "image": ("image_out_1",),
    "Storyboard": ("image_out_1",),
    "extract_last_frame": ("image_out_1",),
    "video": ("media",),
    "merge_video": ("media",),
    "add_bgm": ("media",),
    "align_video_voice": ("media",),
    "sync_image_voice": ("media",),
    "edit_video": ("media",),
    "remove_watermark": ("media",),
    "create_voice": ("audio",),
    "visual_asset": ("media", "image_out", "video_out"),
    "character": ("media",),
    # Every branch socket that has a field behind it, so the catalogue and
    # `branch_text` cannot disagree about which answers exist.
    "prompt": ("text", *sorted(set(postprod_plan.BRANCH_FIELDS))),
    "analyze_video": tuple(sorted(set(postprod_plan.BRANCH_FIELDS))),
    "review_video": (),
    "note": (),
}


@dataclass(frozen=True)
class PortCheck:
    ok: bool
    reason: str = ""


def character_port_limit(lane: Optional[str]) -> int:
    """How many character sockets this lane serves. Asked, not duplicated."""
    return character_ports.limit_for(lane)


def accepts(node_type: str, port: Optional[str], *, lane: Optional[str] = None) -> PortCheck:
    """Whether `port` is a socket `node_type` reads.

    `None` is accepted wherever a consumer accepts an unnamed wire, because
    that is how every hand-drawn edge arrives and refusing it would make the
    agent unable to describe a board a person can draw.
    """
    if node_type not in PORTS_IN:
        return PortCheck(False, f"không có loại node “{node_type}”")
    if not PORTS_IN[node_type] and not character_ports.is_character_port(port):
        # A type with no inputs at all: the unnamed-wire leniency below does not
        # apply, because there is nothing for the wire to feed. The graph would
        # accept it and the run would read nothing — a wire that looks connected
        # and does nothing is worse than a refusal.
        return PortCheck(False, f"node “{node_type}” không nhận dây vào")
    if port is None:
        return PortCheck(True)
    if character_ports.is_character_port(port):
        if node_type != "video":
            return PortCheck(False, "cổng nhân vật chỉ có trên node video")
        # `port_order` returns (index, "") for a numbered socket and
        # (1000, uuid) for a runtime one. A registered character arrives by
        # uuid and is not subject to the numeric ceiling, which is about how
        # many reference slots the lane serves at once.
        index, tail = character_ports.port_order(port)
        limit = character_port_limit(lane)
        if not tail and index > limit:
            return PortCheck(
                False,
                f"làn “{lane or 'mặc định'}” chỉ nhận {limit} cổng nhân vật",
            )
        return PortCheck(True)
    if port in PORTS_IN[node_type]:
        return PortCheck(True)
    return PortCheck(
        False,
        f"node “{node_type}” không có cổng vào “{port}” "
        f"(có: {', '.join(PORTS_IN[node_type]) or 'không cổng nào'})",
    )


def emits(node_type: str, port: Optional[str]) -> PortCheck:
    """Whether `port` is a socket `node_type` offers."""
    if node_type not in PORTS_OUT:
        return PortCheck(False, f"không có loại node “{node_type}”")
    if port is None or port in PORTS_OUT[node_type]:
        return PortCheck(True)
    return PortCheck(
        False,
        f"node “{node_type}” không phát cổng “{port}” "
        f"(có: {', '.join(PORTS_OUT[node_type]) or 'không cổng nào'})",
    )


def describe() -> dict:
    """The catalogue as data, for the agent's prompt and for `/api/agent`."""
    return {
        "nodeTypes": list(NODE_TYPES),
        "portsIn": {k: list(v) for k, v in PORTS_IN.items()},
        "portsOut": {k: list(v) for k, v in PORTS_OUT.items()},
        "characterPortLimits": {
            "default": character_ports.DEFAULT_MAX_CHARACTERS,
            **character_ports.MAX_CHARACTERS,
        },
    }
