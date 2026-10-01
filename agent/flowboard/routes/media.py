"""Media cache routes.

`GET /media/:media_id` streams bytes (cache hit → immediate; miss → one-shot
fetch from GCS then cache). `GET /api/media/:media_id/status` exposes cache
state for the frontend to poll while it waits for a URL to arrive.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from flowboard.services import media as media_service

logger = logging.getLogger(__name__)

bytes_router = APIRouter(tags=["media"])
api_router = APIRouter(prefix="/api/media", tags=["media"])


@bytes_router.get("/media/{media_id:path}")
async def get_media_bytes(media_id: str):
    media_id = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(media_id):
        raise HTTPException(status_code=400, detail="invalid media_id")

    cached = media_service.cached_path(media_id)
    if cached is not None:
        return FileResponse(
            path=str(cached),
            media_type=media_service._mime_from_ext(cached.suffix),
        )

    # Cache miss — try one fetch through the stored URL.
    result = await media_service.fetch_and_cache(media_id)
    if result is None:
        status = media_service.status(media_id)
        return JSONResponse(status_code=404, content=status)
    _bytes, mime, path = result
    return FileResponse(path=str(path), media_type=mime)


@api_router.get("/{media_id}/status")
def get_media_status(media_id: str):
    media_id = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(media_id):
        return JSONResponse(
            status_code=400,
            content={"available": False, "has_url": False, "reason": "invalid_id"},
        )
    return media_service.status(media_id)


@api_router.get("/_debug/assets")
def debug_assets():
    """Dev-only dump of every Asset row so we can see what URLs the extension
    has actually pushed to the agent. Remove once media flow is stable.
    """
    from sqlmodel import select as _select

    from flowboard.db import get_session
    from flowboard.db.models import Asset

    with get_session() as s:
        rows = s.exec(_select(Asset)).all()
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id,
                    "media_id": r.uuid_media_id,
                    "has_url": bool(r.url),
                    "url_head": (r.url or "")[:80] if r.url else None,
                    "mime": r.mime,
                    "cached": bool(r.local_path),
                    "node_id": r.node_id,
                }
                for r in rows
            ],
        }


@api_router.post("/open-output-folder")
def open_output_folder() -> dict:
    """Open the folder finished media is written to, in the OS file manager.

    A local-only convenience, and the reason it is a POST rather than a GET:
    it has a side effect on the machine, and a GET that launches a process is
    something a stray prefetch can trigger.

    The path is NOT taken from the request. It is derived from settings by
    the same function the exporter uses, so this cannot be turned into
    "open any folder on this box" by a crafted body — and it opens the folder
    the files are actually in rather than one that merely looks right.
    """
    import os
    import subprocess
    import sys

    from flowboard.services import media as media_service

    target = media_service.output_dir()
    if target is None:
        raise HTTPException(
            400,
            "Chưa đặt thư mục lưu media. Mở Cài đặt → Lưu trữ để chọn.",
        )
    if not target.exists():
        # Created rather than refused: the folder appears on first export, so
        # before the first run "not there yet" is the normal state, and an
        # error would read as a misconfiguration.
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"Không tạo được thư mục: {exc}") from None
    elif not target.is_dir():
        # `VIDEO_OUTPUT_DIR` naming a FILE is a misconfiguration, not an
        # attack — but on Windows `os.startfile` means "open with the default
        # handler", and for a `.bat` or an `.exe` that is run it. The setting
        # arrives through the settings API and through imported configs, so it
        # is not worth trusting to be a directory.
        raise HTTPException(
            400,
            f"Đường dẫn lưu media không phải thư mục: {target}. "
            "Mở Cài đặt → Lưu trữ để chọn lại.",
        )

    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        raise HTTPException(500, f"Không mở được thư mục: {exc}") from None
    return {"path": str(target)}
