"""Turn one of the seven board shapes into an actual board.

`archetypes.classify` decides WHICH shape a brief means and explains why. Until
now that was the end of the road: `Archetype.chain` is a sentence for a human
("gen_image → create_character (hub) → gen_video Thành Phần → merge → edit"),
so the classifier's answer could be read but not built. The user then wired the
seven nodes by hand — which is the work the archetype was supposed to save.

This module is the machine-readable half. Each blueprint emits a spec in
exactly the shape `materialize_plan` accepts (the same one `storyboard.build_spec`
produces), so nothing new has to understand it.

**Two shapes are deliberately absent, and saying which is the point:**

* ``motion_control`` — the node was taken off the canvas after the binary
  showed it is Flow's *Edit Video* (`abra_edit`, chunked video upload), which
  this build has no endpoint for. A blueprint that placed a node the executor
  refuses would be a board that cannot run.
* the ``create_character`` hub in ``character_drama`` — Flow has no
  create-entity call at all (mined: zero hits for `createCharacter`), so the
  blueprint uses the reference-image path, which is the one that works. The
  chain string still describes the hub because that is what the packaged tool
  does; the difference is recorded here rather than papered over.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ArchetypeBoardError(RuntimeError):
    pass


def _node(tmp_id: str, node_type: str, title: str, **params: Any) -> dict:
    return {
        "tmp_id": tmp_id,
        "type": node_type,
        "params": {"title": title, **params},
    }


def _edge(src: str, dst: str, kind: str = "media") -> dict:
    # `kind` is the TARGET PORT, not a category: `materialize_plan` writes it
    # to `Edge.target_port`, and the executor tells a start frame from a
    # reference by that name. "ref" is the unnamed default.
    return {"from": src, "to": dst, "kind": kind}


def _scene_settings(seconds: int, aspect: str, quality: str) -> dict:
    """`node_settings`' own key spellings — `ratio`, `quality`, `duration`.

    Not `aspect_ratio` / `model_quality` / `duration_seconds`: a wrong spelling
    is not an error anywhere, it is a board that reads as configured and runs
    landscape on the charged lane.
    """
    settings: dict[str, Any] = {"duration": seconds}
    if aspect:
        settings["ratio"] = aspect
    if quality:
        settings["quality"] = quality
    return settings


def build(
    key: str,
    *,
    scene_count: int = 3,
    seconds: int = 8,
    aspect: str = "",
    quality: str = "",
) -> dict:
    """The spec for one archetype. Raises for a shape this build cannot place.

    Nodes carry no prompts: the shape is the point, and a prompt invented here
    would be a paid dispatch nobody asked for. Every generation node lands
    empty, so a run refuses it for want of a prompt until the user fills it in —
    the same refusal a hand-built board gets.
    """
    from flowboard.services import archetypes
    from flowboard.services.pipeline_executor import _VALID_NODE_TYPES

    known = {a.key for a in archetypes.ARCHETYPES}
    if key not in known:
        raise ArchetypeBoardError(f"unknown archetype {key!r}; known: {sorted(known)}")
    builder = _BLUEPRINTS.get(key)
    if builder is None:
        raise ArchetypeBoardError(_UNBUILDABLE[key])

    count = max(1, min(12, int(scene_count)))
    spec = builder(count, _scene_settings(seconds, aspect, quality))
    unknown = {n["type"] for n in spec["nodes"]} - set(_VALID_NODE_TYPES)
    if unknown:
        # A type the executor does not know is a node it SKIPS — a board
        # quietly missing a step rather than an error anyone sees.
        raise ArchetypeBoardError(f"blueprint emits unknown node types: {sorted(unknown)}")
    spec["layout_hint"] = "left_to_right"
    return spec


#: Why a shape has no blueprint, in the words the user should read.
_UNBUILDABLE: dict[str, str] = {
    "motion_control": (
        "Dạng này cần Edit Video của Flow (upload video theo chunk, model "
        "abra_edit) — bản này chưa có endpoint đó, nên chưa dựng được board. "
        "Xem P9c trong kế hoạch."
    ),
}


def _character_drama(count: int, settings: dict) -> dict:
    """Character → reference images → component video → merge → edit.

    The hub in the chain string is Flow's Character Entity, which has no API.
    The reference-image path does the same job through `character_N` sockets,
    numbered in wiring order because that order is the order they appear in the
    prompt — swapping two of them is a different video.
    """
    nodes = [
        _node("char", "character", "Nhân vật chính"),
        _node("look", "image", "Ảnh tham chiếu (trang phục / bối cảnh)",
              sourceSettings={k: v for k, v in settings.items() if k != "duration"}),
    ]
    edges = []
    for index in range(1, count + 1):
        vid = f"v{index}"
        nodes.append(_node(vid, "video", f"Cảnh {index}", sourceSettings=dict(settings)))
        edges.append(_edge("char", vid, "character_1"))
        edges.append(_edge("look", vid, "character_2"))
    nodes.append(_node("merge", "merge_video", "Ghép cảnh"))
    for index in range(1, count + 1):
        edges.append(_edge(f"v{index}", "merge", "media"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge("merge", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


def _frame_chain(count: int, settings: dict) -> dict:
    """The continuity shape: each clip starts on the previous clip's last frame.

    This is how every packaged workflow keeps a subject continuous, and it is
    why `extract_last_frame` exists as a node type at all.
    """
    nodes = [
        _node("i1", "image", "Khung đầu",
              sourceSettings={k: v for k, v in settings.items() if k != "duration"}),
        _node("v1", "video", "Cảnh 1", sourceSettings=dict(settings)),
    ]
    edges = [_edge("i1", "v1", "start_frame")]
    for index in range(2, count + 1):
        frame, video = f"f{index}", f"v{index}"
        nodes.append(_node(frame, "extract_last_frame", f"Nối cảnh {index}"))
        nodes.append(_node(video, "video", f"Cảnh {index}", sourceSettings=dict(settings)))
        edges.append(_edge(f"v{index - 1}", frame, "video"))
        edges.append(_edge(frame, video, "start_frame"))
    nodes.append(_node("merge", "merge_video", "Ghép cảnh"))
    for index in range(1, count + 1):
        edges.append(_edge(f"v{index}", "merge", "media"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge("merge", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


def _slideshow(count: int, settings: dict) -> dict:
    """Stills plus narration, where the narration decides the length.

    `sync_image_voice` is the node that turns one frame into a clip as long as
    the voice takes — the count of images is the count of scenes, and no video
    generation is dispatched at all. The cheapest of the seven by far.
    """
    nodes = [_node("script", "prompt", "Lời thuyết minh")]
    nodes.append(_node("voice", "create_voice", "Đọc thuyết minh"))
    edges = [_edge("script", "voice", "text")]
    for index in range(1, count + 1):
        image, clip = f"i{index}", f"s{index}"
        nodes.append(_node(image, "image", f"Ảnh {index}",
                           sourceSettings={k: v for k, v in settings.items()
                                           if k != "duration"}))
        nodes.append(_node(clip, "sync_image_voice", f"Ảnh + giọng {index}"))
        edges.append(_edge(image, clip, "media"))
        edges.append(_edge("voice", clip, "voice"))
    nodes.append(_node("merge", "merge_video", "Ghép cảnh"))
    for index in range(1, count + 1):
        edges.append(_edge(f"s{index}", "merge", "media"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge("merge", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


def _affiliate(count: int, settings: dict) -> dict:
    """Product link → a model writes the scenes → image + video + voice.

    The `gemini_prompt` node in the chain is a `prompt` node that asks a model
    at RUN time: it carries the instruction, and the run fills its three branch
    sockets. Marked with `promptMode: "gemini"` so the executor knows to run it
    rather than pass the instruction downstream as if it were a prompt.
    """
    nodes = [
        _node("refs", "visual_asset", "Ảnh sản phẩm / link"),
        _node(
            "writer", "prompt", "Viết kịch bản bán hàng",
            prompt=(
                "Bạn là chuyên gia viết kịch bản video bán hàng ngắn. Dựa trên "
                "mô tả sản phẩm, trả về JSON với `scenes`: mỗi phần tử có "
                "`image_prompt` (tiếng Anh), `veo_prompt` (tiếng Anh) và "
                "`dialogue` (tiếng Việt, 28-30 từ)."
            ),
            promptMode="gemini",
        ),
    ]
    edges: list[dict] = []
    for index in range(1, count + 1):
        image, video = f"i{index}", f"v{index}"
        nodes.append(_node(image, "image", f"Ảnh cảnh {index}",
                           sourceSettings={k: v for k, v in settings.items()
                                           if k != "duration"}))
        nodes.append(_node(video, "video", f"Cảnh {index}", sourceSettings=dict(settings)))
        edges.append(_edge("writer", image, "image_prompts"))
        edges.append(_edge("refs", image, "ref"))
        edges.append(_edge("writer", video, "video_prompts"))
        edges.append(_edge(image, video, "start_frame"))
    nodes.append(_node("voice", "create_voice", "Đọc lời thoại"))
    edges.append(_edge("writer", "voice", "voice_prompts"))
    nodes.append(_node("merge", "merge_video", "Ghép cảnh"))
    for index in range(1, count + 1):
        edges.append(_edge(f"v{index}", "merge", "media"))
    nodes.append(_node("align", "align_video_voice", "Khớp giọng với video"))
    edges.append(_edge("merge", "align", "media"))
    edges.append(_edge("voice", "align", "voice"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge("align", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


def _remake(count: int, settings: dict) -> dict:
    """A reference clip → analyse it → rebuild it.

    `analyze_video` runs during the run and writes the three branch texts, so
    the image, video and voice nodes below have a source. Before that it
    returned no ops and reported success, which made this whole shape a board
    of empty boxes.
    """
    nodes = [
        _node("src", "visual_asset", "Video đối thủ"),
        _node("analyze", "analyze_video", "Phân tích video"),
    ]
    edges = [_edge("src", "analyze", "video")]
    for index in range(1, count + 1):
        image, video = f"i{index}", f"v{index}"
        nodes.append(_node(image, "image", f"Ảnh cảnh {index}",
                           sourceSettings={k: v for k, v in settings.items()
                                           if k != "duration"}))
        nodes.append(_node(video, "video", f"Cảnh {index}", sourceSettings=dict(settings)))
        edges.append(_edge("analyze", image, "image_prompts"))
        edges.append(_edge("analyze", video, "video_prompts"))
        edges.append(_edge(image, video, "start_frame"))
    nodes.append(_node("voice", "create_voice", "Đọc lời thoại"))
    edges.append(_edge("analyze", "voice", "voice_prompts"))
    nodes.append(_node("merge", "merge_video", "Ghép cảnh"))
    for index in range(1, count + 1):
        edges.append(_edge(f"v{index}", "merge", "media"))
    nodes.append(_node("align", "align_video_voice", "Khớp giọng với video"))
    edges.append(_edge("merge", "align", "media"))
    edges.append(_edge("voice", "align", "voice"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge("align", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


def _excel_batch(count: int, settings: dict) -> dict:
    """One prompt node per row, which is what the Excel import produces.

    The spreadsheet's three columns (`image_prompt | video_prompt | Lời Thoại`)
    are the three branch fields, so a row is a prompt node with all three and
    the fan-out turns a multi-line node into N of these.
    """
    nodes = [_node("rows", "prompt", "Prompt theo hàng (Excel)")]
    edges: list[dict] = []
    for index in range(1, count + 1):
        image, video = f"i{index}", f"v{index}"
        nodes.append(_node(image, "image", f"Ảnh hàng {index}",
                           sourceSettings={k: v for k, v in settings.items()
                                           if k != "duration"}))
        nodes.append(_node(video, "video", f"Video hàng {index}",
                           sourceSettings=dict(settings)))
        edges.append(_edge("rows", image, "image_prompts"))
        edges.append(_edge("rows", video, "video_prompts"))
        edges.append(_edge(image, video, "start_frame"))
    nodes.append(_node("voice", "create_voice", "Đọc lời thoại"))
    edges.append(_edge("rows", "voice", "voice_prompts"))
    nodes.append(_node("edit", "edit_video", "Hậu kỳ"))
    edges.append(_edge(f"v{count}", "edit", "media"))
    return {"nodes": nodes, "edges": edges}


_BLUEPRINTS: dict[str, Any] = {
    "character_drama": _character_drama,
    "frame_chain": _frame_chain,
    "slideshow": _slideshow,
    "affiliate": _affiliate,
    "remake": _remake,
    "excel_batch": _excel_batch,
}


def buildable(key: str) -> bool:
    return key in _BLUEPRINTS


def refusal(key: str) -> Optional[str]:
    """Why this shape has no board, or None when it has one."""
    return None if key in _BLUEPRINTS else _UNBUILDABLE.get(key)
