"""Read a clip with a vision model and write the prompts that recreate it.

This was `routes/vision.analyze_video`'s body, and it stayed there — which
meant the canvas node named after it could not run. `analyze_video` is a node
type on the board, and a board runs in the worker; the worker must not import a
route (the rule `services/image_ingest` exists for). So the work lives here and
both callers ask the same function.

**Vision models take images, not video.** The clip is sampled into frames and
the model is told they are one sequence — without that framing it reports on
each picture separately and never mentions the motion, which is the part worth
knowing.

``mode`` selects one of the four system prompts the packaged tool ships in
``data_general/system_prompts/``. Those ask for a scene-by-scene breakdown, and
that breakdown is what makes the three branch sockets on the node worth having:
the image prompt, the motion prompt and the spoken line are three different
texts with three different consumers, and merged into one the voice reads the
camera directions aloud.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AnalysisError(RuntimeError):
    """Raised when the analysis cannot run at all.

    A model that ignored the JSON instruction is NOT this: that answer still
    carries the description, so it comes back as text rather than as a failure.
    """


@dataclass
class Scene:
    """One scene of a mode's breakdown.

    Every field is optional because the four bundled prompts do not agree on
    which they emit — the stick-figure one has no `visual_style`, the health
    one adds its own notes. Dropping a scene over a spelling difference would
    throw away the whole analysis.
    """

    scene_number: Optional[int] = None
    image_prompt: str = ""
    veo_prompt: str = ""
    dialogue: str = ""


@dataclass
class Analysis:
    description: str = ""
    prompt: str = ""
    frames: int = 0
    #: Which of the four modes actually ran. The caller passes a LABEL (emoji
    #: and all, as the workflow file writes it); this is what it resolved to.
    mode: str = "standard"
    scenes: list[Scene] = field(default_factory=list)
    script: str = ""
    #: The cover-frame prompt, when the mode asks for one.
    thumbnail_prompt: str = ""


#: Used when the asset root carries no system prompt for the chosen mode. A
#: smaller answer, but an answer — the node still produces something the board
#: can use, which is the difference between degraded and broken.
FALLBACK_SYSTEM = (
    "You are shown still frames sampled in order from ONE short video clip. "
    "They are moments from the same clip, not separate images — read them as "
    "a sequence and describe what happens over time.\n"
    "Reply as JSON with exactly two string fields:\n"
    '  "description": what happens, in Vietnamese, 2-4 sentences.\n'
    '  "prompt": an English generation prompt that would recreate this clip — '
    "subject, action, setting, lighting, camera movement, style. One "
    "paragraph, under 900 characters.\n"
    "Output ONLY the JSON object, no markdown fences."
)


async def analyze(
    source: Path,
    *,
    frames: int = 6,
    mode: str = "",
    language: str = "Vietnamese",
    scene_count: Optional[int] = None,
    style: str = "",
    voice: str = "",
    custom: str = "",
    timeout: float = 180.0,
) -> Analysis:
    """Sample ``source`` into frames and ask the pinned vision model about them.

    Raises ``AnalysisError`` when ffmpeg is missing, the clip yields no frames,
    or the provider cannot answer.
    """
    from flowboard.config import STORAGE_DIR
    from flowboard.services import analysis_modes, postprod
    from flowboard.services.llm import run_llm
    from flowboard.services.llm.base import LLMError

    if not postprod.available():
        raise AnalysisError("ffmpeg not found")

    resolved_mode, system = analysis_modes.build(
        mode,
        language=language,
        scene_count=scene_count,
        style=style,
        voice=voice,
        custom=custom,
    )
    if not system:
        logger.info(
            "video_analysis: no system prompt file for mode %r, using the built-in",
            resolved_mode,
        )
        system = FALLBACK_SYSTEM

    workdir = Path(tempfile.mkdtemp(prefix="analyze-", dir=STORAGE_DIR))
    try:
        try:
            extracted = await _to_thread(postprod.extract_frames, source, workdir, frames)
        except postprod.PostProdError as exc:
            raise AnalysisError(str(exc)) from None
        if not extracted:
            raise AnalysisError("No frames could be read from that video.")
        try:
            text = await run_llm(
                "vision",
                f"{len(extracted)} frames, in order, from one clip.",
                system_prompt=system,
                attachments=[str(p) for p in extracted],
                timeout=timeout,
            )
        except LLMError as exc:
            raise AnalysisError(str(exc)) from None
        frame_count = len(extracted)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return parse(text, frames=frame_count, mode=resolved_mode)


async def _to_thread(fn, *args):
    """`extract_frames` is a subprocess call; it must not hold the loop.

    `fastapi.concurrency.run_in_threadpool` would do, but importing a web
    framework into a service the worker calls is the coupling this module was
    extracted to remove.
    """
    import asyncio

    return await asyncio.to_thread(fn, *args)


def parse(text: str, *, frames: int = 0, mode: str = "standard") -> Analysis:
    """A model's answer, as an ``Analysis``. Never raises."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.lstrip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # A model that ignored the JSON instruction still produced something
        # useful — hand the raw text back rather than failing the call.
        return Analysis(description=raw, frames=frames, mode=mode)
    if not isinstance(parsed, dict):
        return Analysis(description=raw, frames=frames, mode=mode)

    scenes = _scenes_of(parsed)
    description = str(parsed.get("description") or "").strip()
    prompt = str(parsed.get("prompt") or "").strip()
    script = str(parsed.get("vietnamese_script") or "").strip()
    if not description:
        # The four bundled prompts answer with scenes, not a description.
        # Falling back to the script keeps the node's own text output
        # populated instead of showing an empty panel beside a full answer.
        description = script
    if not prompt and scenes:
        prompt = scenes[0].veo_prompt
    return Analysis(
        description=description,
        prompt=prompt,
        frames=frames,
        mode=mode,
        scenes=scenes,
        script=script,
        thumbnail_prompt=str(parsed.get("thumbnail_prompt") or "").strip(),
    )


def _scenes_of(parsed: dict) -> list[Scene]:
    """The scene list, skipping entries that carry no prompt at all.

    Tolerant by design: the four prompts do not agree on their field names
    beyond `image_prompt` / `veo_prompt`, and a mode that adds a field must
    not cost the caller the whole breakdown.
    """
    raw = parsed.get("scenes")
    if not isinstance(raw, list):
        return []
    out: list[Scene] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        image = str(entry.get("image_prompt") or "").strip()
        veo = str(entry.get("veo_prompt") or "").strip()
        if not image and not veo:
            continue
        number = entry.get("scene_number")
        out.append(
            Scene(
                scene_number=number if isinstance(number, int) else index,
                image_prompt=image,
                veo_prompt=veo,
                dialogue=str(entry.get("dialogue") or "").strip(),
            )
        )
    return out


def branch_texts(analysis: Analysis) -> dict[str, str]:
    """The three branch fields a node writes for its downstream sockets.

    One line per scene, in order, because that is what the fan-out reads: a
    multi-line prompt node becomes N generation nodes. So a five-scene
    breakdown arrives as five image prompts, five motion prompts and five
    spoken lines, each list aligned with the others.

    An empty dialogue is kept as a blank LINE rather than dropped: dropping one
    would shift every later scene's dialogue onto the wrong picture. (Leading
    and trailing blanks do go — a breakdown that opens or closes on silence
    loses nothing by not saying so, and a text of pure newlines would read as
    content to every consumer.)
    """
    if not analysis.scenes:
        out = {}
        if analysis.prompt:
            out["videoPrompt"] = analysis.prompt
        if analysis.script:
            out["voicePrompt"] = analysis.script
        return out

    return {
        "imagePrompt": "\n".join(s.image_prompt for s in analysis.scenes).strip(),
        "videoPrompt": "\n".join(s.veo_prompt for s in analysis.scenes).strip(),
        "voicePrompt": "\n".join(s.dialogue for s in analysis.scenes).strip(),
    }
