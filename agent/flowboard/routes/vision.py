"""Vision describe endpoint.

`POST /api/vision/describe { media_id }` returns a short text brief about
the image. Used by the frontend to auto-annotate visual_asset / character
nodes after upload, and as upstream context for auto-prompt synthesis.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.services import vision as vision_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vision", tags=["vision"])


class DescribeBody(BaseModel):
    media_id: str


class DescribeResponse(BaseModel):
    media_id: str
    description: str


@router.post("/describe", response_model=DescribeResponse)
async def describe(body: DescribeBody) -> DescribeResponse:
    try:
        text = await vision_service.describe_media(body.media_id)
    except vision_service.VisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return DescribeResponse(media_id=body.media_id, description=text)


class AnalyzeVideoBody(BaseModel):
    video: str
    # Each frame is another image in the request, so this is the cost dial.
    frames: int = Field(default=6, ge=2, le=12)
    # The workflow's own `analysis_mode` label, emoji and all. Selects one of
    # the four bundled system prompts; see `services.analysis_modes`.
    mode: str = ""
    language: str = "Vietnamese"
    # `lock_scene_count` in the packaged tool: pin the breakdown to a number
    # of scenes instead of letting the model choose.
    scene_count: Optional[int] = Field(default=None, ge=1, le=40)
    style: str = ""
    voice: str = ""
    custom: str = ""


class Scene(BaseModel):
    """One scene of a mode's breakdown.

    Every field is optional because the four prompts do not agree on which
    they emit — the stick-figure one has no `visual_style`, the health one
    adds its own notes. Dropping a scene for a missing field would throw
    away the whole analysis over a spelling difference.
    """

    scene_number: Optional[int] = None
    image_prompt: str = ""
    veo_prompt: str = ""
    dialogue: str = ""


class AnalyzeVideoResponse(BaseModel):
    description: str
    prompt: str
    frames: int
    # Which of the four modes actually ran. The request carries the user's
    # label; this is what it resolved to, and the two are worth comparing
    # when a workflow is imported from someone else's machine.
    mode: str = "standard"
    scenes: list[Scene] = Field(default_factory=list)
    script: str = ""


@router.post("/video", response_model=AnalyzeVideoResponse)
async def analyze_video(body: AnalyzeVideoBody) -> AnalyzeVideoResponse:
    """Describe a clip, and write a prompt that would recreate it.

    The work is `services/video_analysis`, because the canvas node named after
    this endpoint runs in the WORKER and a worker must not import a route. This
    is the HTTP face of it: resolve the media id, translate the service's errors
    into status codes.
    """
    from flowboard.routes.postprod import _existing
    from flowboard.services import video_analysis

    source = _existing(body.video, label="video")
    try:
        result = await video_analysis.analyze(
            source,
            frames=body.frames,
            mode=body.mode,
            language=body.language,
            scene_count=body.scene_count,
            style=body.style,
            voice=body.voice,
            custom=body.custom,
        )
    except video_analysis.AnalysisError as exc:
        message = str(exc)
        # "ffmpeg not found" and "no frames" are the caller's environment or
        # file; a provider that could not answer is upstream.
        status = 400 if "ffmpeg" in message else 422 if "frames" in message else 502
        raise HTTPException(status_code=status, detail=message) from None

    return AnalyzeVideoResponse(
        description=result.description,
        prompt=result.prompt,
        frames=result.frames,
        mode=result.mode,
        scenes=[
            Scene(
                scene_number=s.scene_number,
                image_prompt=s.image_prompt,
                veo_prompt=s.veo_prompt,
                dialogue=s.dialogue,
            )
            for s in result.scenes
        ],
        script=result.script,
    )
