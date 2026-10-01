"""Grok (xAI) — image and video through the user's own grok.com session.

This used to store an ``api.x.ai`` key and probe that API. Mining the packaged
tool showed that is the wrong target: it never touches api.x.ai. It drives a
signed-in Chrome and calls grok.com's own endpoints, in four modes
(``MODE_GROK_{CREATE_IMAGE,IMAGE_TO_IMAGE,TEXT_TO_VIDEO,IMAGE_TO_VIDEO}``) —
so this build does the same thing through the extension's page bridge, which
needs no API key and no second browser.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.services import grok as grok_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/grok", tags=["grok"])

MAX_REFS = 4


class StatusResponse(BaseModel):
    bridgeReady: bool
    note: str


@router.get("/status", response_model=StatusResponse)
async def status() -> StatusResponse:
    """Whether the extension is connected at all.

    Whether a grok.com TAB is open cannot be known without asking the
    extension, so the real answer arrives from the first call.
    """
    from flowboard.services.flow_client import flow_client

    connected = flow_client.connected
    return StatusResponse(
        bridgeReady=connected,
        note=(
            "Mở grok.com và đăng nhập — tool dùng chính phiên đó, không cần API key."
            if connected
            else "Extension chưa kết nối."
        ),
    )


@router.post("/whoami")
async def whoami() -> dict:
    """Signed in to grok.com, or not — the thing a 403 cannot tell you."""
    try:
        return await grok_service.whoami()
    except grok_service.GrokNotConnected as exc:
        raise HTTPException(409, str(exc)) from None
    except grok_service.GrokError as exc:
        raise HTTPException(502, str(exc)) from None


@router.post("/probe")
async def probe() -> dict:
    """Send one harmless request through the page bridge and report the shape.

    The response layout is the one thing static analysis could not recover, so
    this exists to replace a guess with a real answer.
    """
    try:
        return await grok_service.probe()
    except grok_service.GrokNotConnected as exc:
        raise HTTPException(409, str(exc)) from None
    except grok_service.GrokError as exc:
        raise HTTPException(502, str(exc)) from None


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    video: bool = False
    #: Cached media ids to send as reference images (image-to-* modes).
    ref_media_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS)


class GenerateResponse(BaseModel):
    urls: list[str]
    objectCount: int


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateBody) -> GenerateResponse:
    """One call covering all four modes; `video` and refs pick which."""
    from flowboard.services import media as media_service

    paths: list[Path] = []
    for media_id in body.ref_media_ids:
        cached = media_service.cached_path(media_id)
        if cached is None:
            raise HTTPException(
                400,
                f"Ảnh {media_id[:8]} chưa có trong cache — mở nó một lần rồi thử lại.",
            )
        paths.append(cached)

    try:
        result = await grok_service.generate(
            body.prompt, video=body.video, image_paths=paths or None
        )
    except grok_service.GrokNotConnected as exc:
        raise HTTPException(409, str(exc)) from None
    except grok_service.GrokError as exc:
        raise HTTPException(502, str(exc)) from None
    return GenerateResponse(**result)
