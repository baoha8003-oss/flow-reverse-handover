"""Grok (xAI) image and video generation through the user's grok.com session.

Mined from the packaged tool, which drives a signed-in Chrome over CDP and
calls grok.com's own endpoints — NOT ``api.x.ai`` and not an API key:

    POST /rest/app-chat/upload-file       {fileName, fileMimeType, content, fileSource}
                                          → fileMetadataId
    POST /rest/app-chat/conversations/new {message, temporary: true,
                                           toolOverrides: {videoGen: bool},
                                           modelMap: {videoGenModelConfig: {...}}}
    POST /rest/media/post/create          {mediaType, prompt | mediaUrl}
    POST /rest/media/video/upscale        {videoId}

Its four modes are ``MODE_GROK_{CREATE_IMAGE,IMAGE_TO_IMAGE,TEXT_TO_VIDEO,
IMAGE_TO_VIDEO}``.

**Verification status.** The endpoints, the request keys above and the four
modes are OBSERVED — lifted from strings in the binary. The exact response
shape is NOT: it arrives as a stream of concatenated JSON objects whose fields
could not be read from static strings alone. So ``_pick_media`` searches the
returned objects for anything URL-shaped rather than asserting a path, and
``probe`` exists to show a real response the first time someone runs this with
a live session. Nothing here has been exercised against a live Grok account.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE = "https://grok.com/rest"
UPLOAD_URL = f"{BASE}/app-chat/upload-file"
CONVERSATION_URL = f"{BASE}/app-chat/conversations/new"
POST_CREATE_URL = f"{BASE}/media/post/create"
VIDEO_UPSCALE_URL = f"{BASE}/media/video/upscale"

# Upload travels base64 inside a JSON body, so it is bounded well below the
# point where the page would stall building the string.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


class GrokError(RuntimeError):
    """Message is safe to show a user."""


class GrokNotConnected(GrokError):
    """No signed-in grok.com tab to run the request in."""


# Generation genuinely runs for minutes; a connectivity check must not.
GENERATE_TIMEOUT_S = 300.0
PROBE_TIMEOUT_S = 20.0


async def _call(
    url: str,
    body: Any = None,
    *,
    method: str = "POST",
    stream: bool = False,
    timeout: float = GENERATE_TIMEOUT_S,
) -> dict[str, Any]:
    from flowboard.services.flow_client import flow_client

    resp = await flow_client.grok_fetch(
        url=url, method=method, body=body, stream=stream, timeout=timeout
    )
    if not isinstance(resp, dict):
        raise GrokError("Extension trả về dữ liệu không đọc được.")
    error = resp.get("error")
    if error == "GROK_NO_TAB":
        raise GrokNotConnected(
            "Chưa thấy tab grok.com nào đang mở. Mở grok.com, đăng nhập, rồi "
            "thử lại — tool dùng chính phiên đó."
        )
    if error:
        raise GrokError(f"Grok lỗi: {str(error)[:200]}")
    status = resp.get("status")
    if isinstance(status, int) and status >= 400:
        # Carry grok's own words. A bare status tells the user nothing they
        # can act on, and tells whoever is debugging this even less: 401 and
        # 403 look identical from here but mean "sign in" versus "the request
        # is missing something the page normally sends".
        detail = _error_detail(resp)
        hint = (
            " Có vẻ chưa đăng nhập grok.com."
            if status in (401, 403)
            else ""
        )
        raise GrokError(
            f"grok.com trả về {status}.{hint}"
            + (f" Nội dung: {detail}" if detail else "")
        )
    return resp


def _error_detail(resp: dict[str, Any]) -> str:
    """Whatever grok said, trimmed. Handles both JSON and an HTML block page."""
    data = resp.get("data")
    if isinstance(data, dict):
        for key in ("error", "message", "detail", "code"):
            value = data.get(key)
            if value:
                return str(value)[:300]
        return str(data)[:300]
    text = resp.get("text")
    if isinstance(text, str) and text.strip():
        stripped = text.strip()
        # A Cloudflare / bot-check interstitial is HTML, and dumping the page
        # helps nobody — say what it is instead.
        if stripped[:1] == "<" or "<html" in stripped[:200].lower():
            return "trang HTML (thường là chặn bot hoặc trang đăng nhập)"
        return stripped[:300]
    return ""


SESSION_URL = "https://grok.com/api/auth/session"


async def whoami() -> dict[str, Any]:
    """Is this browser signed in to grok.com?

    The single most useful thing to know when a call comes back 403: signed
    out looks exactly like signed in but missing a header, and the fix for
    each is completely different. grok's own client asks this same endpoint.
    """
    resp = await _call(SESSION_URL, method="GET", timeout=PROBE_TIMEOUT_S)
    data = resp.get("data")
    user = data.get("user") if isinstance(data, dict) else None
    return {
        "signedIn": bool(user),
        # Field names only — never the user's email or id.
        "fields": sorted(data) if isinstance(data, dict) else [],
    }


async def probe() -> dict[str, Any]:
    """Confirm the page bridge works and show what Grok actually answers.

    The response shape could not be read from the binary's strings, so the
    first real reply is evidence rather than a guess — this returns it as-is
    instead of pretending to interpret it.
    """
    resp = await _call(
        CONVERSATION_URL,
        {
            "message": "ping",
            "temporary": True,
            "toolOverrides": {},
        },
        stream=True,
        # A probe that can hang for five minutes tells you nothing you could
        # not learn faster, and it blocks a worker thread while it does.
        timeout=PROBE_TIMEOUT_S,
    )
    objects = resp.get("objects") or []
    return {
        "ok": True,
        "status": resp.get("status"),
        "objectCount": len(objects),
        # Field names only — a reply could quote the prompt back, and the raw
        # body is not something to spray into a UI.
        "keys": sorted({k for o in objects if isinstance(o, dict) for k in o}),
    }


async def upload_image(path: Path) -> str:
    """Upload a reference image, returning its ``fileMetadataId``."""
    if not path.is_file():
        raise GrokError(f"Không tìm thấy ảnh: {path.name}")
    raw = path.read_bytes()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise GrokError(
            f"Ảnh {path.name} lớn hơn {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    mime, _ = mimetypes.guess_type(str(path))
    resp = await _call(
        UPLOAD_URL,
        {
            "fileName": path.name,
            "fileMimeType": mime or "image/png",
            "content": base64.b64encode(raw).decode("ascii"),
            "fileSource": "FILE_SOURCE_CHAT",
        },
    )
    data = resp.get("data") or {}
    for key in ("fileMetadataId", "fileMetadataUuid", "id"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value:
            return value
    raise GrokError("Grok nhận ảnh nhưng không trả về mã file.")


def _pick_media(objects: list[Any]) -> list[str]:
    """Every URL-shaped string in the streamed reply, in order, deduplicated.

    Deliberately shape-agnostic: the response layout is the one thing the
    binary's strings did not reveal, and a hardcoded path would fail silently
    the moment it is wrong. Anything pointing at Grok's asset host counts.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            low = node.lower()
            if low.startswith("https://") and (
                "assets.grok" in low
                or "imagine" in low
                or low.endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))
            ):
                if node not in found:
                    found.append(node)

    walk(objects)
    return found


async def generate(
    prompt: str,
    *,
    video: bool = False,
    image_paths: Optional[list[Path]] = None,
) -> dict[str, Any]:
    """One generation call covering all four of the packaged tool's modes.

    ``video`` picks text/image→video over text/image→image; passing
    ``image_paths`` turns either into its image-to-* variant. That is the same
    two-axis split the binary's MODE_GROK_* constants describe.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise GrokError("Cần prompt.")

    attachments: list[str] = []
    for path in image_paths or []:
        attachments.append(await upload_image(path))

    body: dict[str, Any] = {
        "message": prompt,
        # The packaged tool always sets this — conversations are throwaway,
        # not saved into the user's Grok history.
        "temporary": True,
        "toolOverrides": {"videoGen": True} if video else {},
    }
    if attachments:
        body["fileAttachments"] = attachments
        # The binary threads the uploaded file's id through as parentPostId.
        body["modelMap"] = {
            "videoGenModelConfig": {"parentPostId": attachments[0]}
        } if video else {}

    resp = await _call(CONVERSATION_URL, body, stream=True)
    objects = resp.get("objects") or []
    urls = _pick_media(objects)
    if not urls:
        raise GrokError(
            "Grok trả lời nhưng không tìm thấy ảnh/video nào trong phản hồi. "
            "Chạy 'Dò kết nối' để xem Grok thực sự trả về gì."
        )
    return {"urls": urls, "objectCount": len(objects)}
