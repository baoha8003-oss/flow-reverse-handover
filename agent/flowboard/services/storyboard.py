"""An idea in, a whole board out.

Typing an idea and getting a wired canvas — scenes, image nodes, video
nodes, the chain between them — instead of dragging eleven boxes and
connecting them by hand.

**Nothing new is built underneath.** `pipeline_executor.materialize_plan`
already turns a spec of ``{"tmp_id", "type", "params"}`` plus edges into
Node and Edge rows, resolves the layout, and is idempotent. This module's
only job is to get a model to produce that spec correctly. Every node type
it emits is checked against `_VALID_NODE_TYPES` before the spec is offered,
because a type the executor does not know is a node it silently skips —
which would show up as a board that is quietly missing a scene.

**It spends nothing.** One text call to draft the scenes, and then rows in
a database. No Flow dispatch happens until someone opens the board and
presses Run, where the existing estimate and its confirmation still stand
between them and the credits.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StoryboardError(RuntimeError):
    """The storyboard could not be drafted."""


MAX_SCENES = 12

#: The shapes a storyboard can take. `chain` is the one the packaged
#: workflows use everywhere: each clip starts from the previous clip's last
#: frame, which is what keeps a character continuous across a sequence.
STRUCTURES = ("chain", "independent")

_SYSTEM = """\
You are planning a short video as a sequence of {count} scenes, each about \
{seconds} seconds long.

For each scene write:
- "image": a prompt for the scene's FIRST FRAME as a still. Concrete and \
visual — subject, clothing, setting, light, framing. No camera motion here.
- "video": a prompt for what MOVES during the scene. One continuous action, \
plus camera behaviour. Do not restate the whole still.
- "title": three or four words naming the scene, in Vietnamese.

Rules:
- The same subject appears throughout. Describe their fixed features \
(face, hair, clothing) the same way in every scene so they do not drift.
- One action per scene. A scene that lists three actions becomes a clip \
that does none of them well.
- Write image and video prompts in English; titles in Vietnamese.
{extra}
Return ONLY this JSON:
{{"scenes": [{{"title": "...", "image": "...", "video": "..."}}]}}"""


def _system_prompt(
    count: int,
    seconds: int,
    structure: str,
    style: str,
    *,
    genre: Optional[str] = None,
    use_longform: bool = False,
    use_3act: bool = False,
    video_format: Optional[str] = None,
) -> str:
    """The instructions, plus the packaged tool's own craft knowledge.

    The rules above are this build's own and they are thin — "one action per
    scene", "keep the subject consistent". `ASSET_ROOT` ships the material
    that actually decides whether a short video works: hook construction,
    the retention spine, McKee-derived structure, and eight Vietnamese genre
    formulas. It has been sitting unread. Appending it here is the whole
    point of loading the knowledge base.

    Missing asset library degrades to the rules alone, which is exactly what
    ran before, so a storyboard never fails for want of knowledge.
    """
    extra = ""
    if structure == "chain":
        extra = (
            "- The scenes run continuously: each one begins where the last "
            "ended, same place and same subject, so the clips can be joined.\n"
        )
    if style.strip():
        extra += f"- Visual style throughout: {style.strip()}.\n"
    prompt = _SYSTEM.format(count=count, seconds=seconds, extra=extra)

    # One selector for both callers. Written twice, the two drifted: the idea
    # tab and the storyboard were reading different halves of the same library.
    from flowboard.services.prompt_synth import script_knowledge

    prompt += script_knowledge(
        genre,
        use_longform=use_longform,
        use_3act=use_3act,
        video_format=video_format,
    )
    return prompt


def parse_scenes(raw: str, *, limit: int = MAX_SCENES) -> list[dict]:
    """Scenes from a model's answer.

    A scene needs a video prompt to be worth a node; one with only a title
    is dropped rather than materialised into an empty box the user then has
    to find and delete.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoryboardError(f"the planner did not answer with JSON: {exc}") from None

    raw_scenes = parsed.get("scenes") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_scenes, list):
        raise StoryboardError("the planner's answer had no scene list in it")

    scenes: list[dict] = []
    for index, entry in enumerate(raw_scenes[:limit], start=1):
        if not isinstance(entry, dict):
            continue
        video = str(entry.get("video") or "").strip()
        image = str(entry.get("image") or "").strip()
        if not video:
            logger.info("storyboard: scene %d has no video prompt, dropping it", index)
            continue
        scenes.append({
            "title": str(entry.get("title") or f"Cảnh {index}").strip(),
            "image": image,
            "video": video,
        })
    if not scenes:
        raise StoryboardError("the planner produced no usable scenes")
    return scenes


def build_spec(
    scenes: list[dict],
    *,
    structure: str = "chain",
    aspect: str = "",
    quality: str = "",
    seconds: int = 8,
) -> dict:
    """Scenes as a spec `materialize_plan` accepts.

    Each scene becomes an image node and a video node wired to it. In
    ``chain`` structure an `extract_last_frame` sits between one scene's
    video and the next scene's video, which is how every packaged workflow
    keeps a subject continuous — the previous clip's final frame IS the next
    clip's first.

    Node settings are written under ``sourceSettings`` because that is where
    `node_settings` reads them from, the same place an imported workflow
    puts them. Writing them anywhere else would produce a board that looks
    configured and dispatches at Flow's defaults.

    The key SPELLINGS are `node_settings`' own — ``ratio``, ``quality``,
    ``duration`` — and not the plausible-looking `aspect_ratio` /
    `model_quality` / `duration_seconds`. A wrong spelling is not an error
    anywhere; it is a board that reads as configured and quietly runs
    landscape at the charged lane. Kept honest by
    `test_settings_land_where_node_settings_reads_them`, which asks
    `node_settings` rather than checking the dict.
    """
    from flowboard.services.pipeline_executor import _VALID_NODE_TYPES

    if structure not in STRUCTURES:
        raise StoryboardError(f"unknown structure {structure!r}")

    settings: dict[str, Any] = {}
    if aspect:
        settings["ratio"] = aspect
    if quality:
        settings["quality"] = quality
    settings["duration"] = seconds

    nodes: list[dict] = []
    edges: list[dict] = []
    previous_video: Optional[str] = None

    for index, scene in enumerate(scenes, start=1):
        video_id = f"v{index}"
        title = scene.get("title") or f"Cảnh {index}"

        if index == 1 or structure == "independent":
            # An opening scene has no previous frame to continue from, so it
            # gets its own still.
            image_id = f"i{index}"
            nodes.append({
                "tmp_id": image_id,
                "type": "image",
                "params": {
                    "title": f"{title} — khung đầu",
                    "prompt": scene.get("image") or scene["video"],
                    # A still has no duration; leaving it in would put a
                    # video setting on an image dispatch.
                    "sourceSettings": {k: v for k, v in settings.items()
                                       if k != "duration"},
                },
            })
            start_from = image_id
        else:
            frame_id = f"f{index}"
            nodes.append({
                "tmp_id": frame_id,
                "type": "extract_last_frame",
                "params": {"title": f"{title} — nối cảnh"},
            })
            edges.append({"from": previous_video, "to": frame_id, "kind": "media"})
            start_from = frame_id

        nodes.append({
            "tmp_id": video_id,
            "type": "video",
            "params": {
                "title": title,
                "prompt": scene["video"],
                "sourceSettings": dict(settings),
            },
        })
        edges.append({"from": start_from, "to": video_id, "kind": "start_frame"})
        previous_video = video_id

    unknown = {n["type"] for n in nodes} - set(_VALID_NODE_TYPES)
    if unknown:
        # A type the executor does not know is a node it SKIPS, which would
        # surface as a board quietly missing a scene rather than as an error.
        raise StoryboardError(f"spec would emit unknown node types: {sorted(unknown)}")

    return {"nodes": nodes, "edges": edges, "layout_hint": "left_to_right"}


async def draft(
    idea: str,
    *,
    scene_count: int = 3,
    seconds: int = 8,
    structure: str = "chain",
    style: str = "",
    aspect: str = "",
    quality: str = "",
    genre: Optional[str] = None,
    use_longform: bool = False,
    use_3act: bool = False,
    video_format: Optional[str] = None,
    timeout: float = 180.0,
) -> dict:
    """An idea to a spec, plus the scenes for a preview.

    Costs one text call. No Flow dispatch happens here or afterwards until
    someone opens the board and presses Run.
    """
    from flowboard.services.llm import run_llm
    from flowboard.services.llm.base import LLMError

    if not idea.strip():
        raise StoryboardError("no idea to plan")
    count = max(1, min(MAX_SCENES, int(scene_count)))

    try:
        raw = await run_llm(
            "planner",
            idea.strip(),
            system_prompt=_system_prompt(
                count, seconds, structure, style,
                genre=genre,
                use_longform=use_longform,
                use_3act=use_3act,
                video_format=video_format,
            ),
            timeout=timeout,
        )
    except LLMError as exc:
        raise StoryboardError(str(exc)) from None

    scenes = parse_scenes(raw, limit=count)
    spec = build_spec(
        scenes, structure=structure, aspect=aspect, quality=quality, seconds=seconds
    )
    logger.info("storyboard: %d scene(s), %d node(s)", len(scenes), len(spec["nodes"]))
    return {"scenes": scenes, "spec": spec}
