"""A second image engine, next to Google Flow's.

Flow is the only thing that has ever drawn a frame in this app, so a Flow
outage, a quota wall, or a prompt its filter dislikes stops image work
dead. This adds OpenAI as an alternative source of the same thing: bytes,
which `image_ingest` then pushes to Flow to become a `media_id` like any
other. Downstream nothing changes — a start frame is a start frame.

**Both paths ship off.** Neither turns itself on when a key happens to be
present, because the whole point of the pair is that one of them spends
real money and the other spends the user's ChatGPT plan.

Two transports, and the order between them is a deliberate choice:

1. **API key** — `POST /v1/images/generations`. Documented, stable,
   pay-per-image. This is the primary path.
2. **ChatGPT OAuth relay** — `POST chatgpt.com/backend-api/codex/responses`
   with an `image_generation` tool block, authenticated with the Codex
   CLI's own access token.

   Two claims here, and they are not equally strong. That the ACCOUNT and the
   ENDPOINT can draw an image this way is measured: 2026-09-03 01:11,
   `auth_mode: chatgpt`, `OPENAI_API_KEY: null`, and a 2,066,233-byte PNG still
   sitting in `~/.codex/generated_images/`. That the REQUEST BODY below is one
   that endpoint accepts is **not** measured — that image was drawn by the
   Codex CLI, not by this module. The body here is reconstructed from
   `codex-imagegen-cli` and has never been sent from this code, so the first
   real run is what will confirm or refute it.

   It is in any case an internal backend with no published contract, which is
   the other reason it is **secondary and opt-in**.

The API key leads even though the relay costs no new money, for a reason
that is easy to miss: the relay bills the user's *ChatGPT plan* quota, at
roughly 3–5x a text turn, and that is the same quota they are using to
code. Quietly burning it to draw pictures is a cost they would not see
coming. `OPENAI_IMAGE_PREFER_RELAY` flips the order for anyone who would
rather spend quota than money.

**No OAuth refresh flow is implemented.** Refreshing against an
undocumented endpoint means guessing at a protocol we cannot verify, and a
wrong guess there fails in ways that look like something else. When the
token has expired the relay gets a 401, says so, and names the fix
(`codex login`) — the Codex CLI refreshes the file itself.

Nothing here ever logs, formats, or reprs a token or key.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from flowboard.services.llm import secrets
from flowboard.services.llm.base import safe_error_message

logger = logging.getLogger(__name__)

_API_URL = "https://api.openai.com/v1/images/generations"
_RELAY_URL = "https://chatgpt.com/backend-api/codex/responses"

#: Default model. NOT gpt-image-1-mini, despite it being cheaper per image:
#: OpenAI's published deprecation table shuts down the entire gpt-image-1
#: family (1, 1-mini, 1.5) on **2026-12-01**. Defaulting to something with a
#: known death date three months out just schedules the outage. The cost gap
#: closes anyway at `low` quality, which is the default below.
DEFAULT_MODEL = "gpt-image-2"

#: Cheapest tier, and the one where gpt-image-2 lands at roughly the same
#: price as the mini model it replaces. Users who want detail set it higher
#: and can see what that costs in the estimate.
DEFAULT_QUALITY = "low"

#: The three sizes every gpt-image model accepts (gpt-image-2 also takes
#: custom sizes; not exposed here — the board thinks in aspect ratios).
_SIZE_BY_ASPECT = {
    "IMAGE_ASPECT_RATIO_PORTRAIT": "1024x1536",
    "IMAGE_ASPECT_RATIO_LANDSCAPE": "1536x1024",
    "IMAGE_ASPECT_RATIO_SQUARE": "1024x1024",
}
_DEFAULT_SIZE = "1024x1024"

_VALID_QUALITIES = ("low", "medium", "high", "auto")

#: OpenAI caps the image prompt well above anything a board produces; this
#: only stops a runaway upstream node from paying for a rejected request.
_MAX_PROMPT_CHARS = 32_000

#: A generated image that does not fit the upload path is not useful, and
#: finding that out after paying is the wrong order.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

#: How many strings in a relay response are worth base64-decoding before
#: giving up. Generous next to any real payload, finite next to a hostile one.
_MAX_IMAGE_CANDIDATES = 32

#: USD per image → ``{model: {quality: (square, tall_or_wide)}}``.
#:
#: Retrieved from OpenAI's published pricing on 2026-09-03. Kept here rather
#: than in `routes/estimate` because it is a property of the engine, and kept
#: at all — unlike the Veo lanes, which have no published table anywhere —
#: because for these models one genuinely exists.
_PRICE_USD: dict[str, dict[str, tuple[float, float]]] = {
    "gpt-image-1": {
        "low": (0.011, 0.016),
        "medium": (0.042, 0.063),
        "high": (0.167, 0.25),
    },
    "gpt-image-1-mini": {
        "low": (0.005, 0.006),
        "medium": (0.011, 0.015),
        "high": (0.036, 0.052),
    },
}

#: gpt-image-2 publishes token rates but no flat per-image table that could
#: be retrieved from the primary source. These come from several independent
#: third-party calculators that agreed with each other — good enough to show
#: someone the order of magnitude, NOT good enough to present as official,
#: so the caller is told which it is rather than being handed a number that
#: looks like the other ones.
_PRICE_USD_ESTIMATED: dict[str, dict[str, tuple[float, float]]] = {
    "gpt-image-2": {
        "low": (0.005, 0.006),
        "medium": (0.040, 0.050),
        "high": (0.170, 0.210),
    },
}


class ImageGenError(RuntimeError):
    """Image generation failed. Never carries a token or key."""


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str
    #: ``"api-key"`` or ``"relay"`` — which transport actually paid.
    source: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        # The default dataclass repr would splat a megabyte of image bytes
        # into any log line that touches this object.
        return f"GeneratedImage(mime={self.mime!r}, source={self.source!r}, bytes={len(self.data)})"


# ── configuration ─────────────────────────────────────────────────────


def _setting(key: str) -> Any:
    from flowboard.services import settings_store

    return settings_store.get(key)


def model_name() -> str:
    raw = _setting("OPENAI_IMAGE_MODEL")
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_MODEL


def quality() -> str:
    raw = _setting("OPENAI_IMAGE_QUALITY")
    if isinstance(raw, str) and raw.strip().lower() in _VALID_QUALITIES:
        return raw.strip().lower()
    return DEFAULT_QUALITY


def size_for(aspect_ratio: Optional[str]) -> str:
    """Flow's aspect enum → an OpenAI size string."""
    if isinstance(aspect_ratio, str):
        return _SIZE_BY_ASPECT.get(aspect_ratio.upper(), _DEFAULT_SIZE)
    return _DEFAULT_SIZE


def price_range(model: Optional[str] = None, size: Optional[str] = None) -> Optional[tuple[float, float, bool]]:
    """``(usd_low, usd_high, is_official)`` for one image, or None.

    The bool is not decoration: one of these tables is OpenAI's published
    per-image pricing and the other is third-party arithmetic, and a reader
    who cannot tell them apart will treat both as a quote.
    """
    m = (model or model_name()).strip()
    q = quality()
    if q == "auto":
        # "auto" resolves server-side, so the honest answer spans the range.
        table = _PRICE_USD.get(m) or _PRICE_USD_ESTIMATED.get(m)
        if not table:
            return None
        low = min(v[0] for v in table.values())
        high = max(v[1] for v in table.values())
        return low, high, m in _PRICE_USD
    tall = (size or _DEFAULT_SIZE) != "1024x1024"
    for table, official in ((_PRICE_USD, True), (_PRICE_USD_ESTIMATED, False)):
        entry = table.get(m, {}).get(q)
        if entry:
            price = entry[1] if tall else entry[0]
            return price, price, official
    return None


# ── availability ──────────────────────────────────────────────────────


def api_available() -> bool:
    """The documented path: switched on AND holding a real platform key.

    A ChatGPT OAuth login is not a platform key and cannot reach
    `api.openai.com` — the two are constantly confused, so the check is on
    the key alone.
    """
    if not _setting("OPENAI_IMAGE_ENABLED"):
        return False
    return bool(secrets.get_api_key("openai"))


def relay_available() -> bool:
    """The unofficial path: switched on AND a Codex ChatGPT login present."""
    if not _setting("OPENAI_IMAGE_RELAY_ENABLED"):
        return False
    return _codex_credentials() is not None


def available() -> bool:
    return api_available() or relay_available()


def sources() -> list[str]:
    """Transports that could answer right now, in the order they'd be tried."""
    order = ["relay", "api-key"] if _setting("OPENAI_IMAGE_PREFER_RELAY") else ["api-key", "relay"]
    checks = {"api-key": api_available, "relay": relay_available}
    return [name for name in order if checks[name]()]


# ── the Codex token, handled as carefully as it deserves ──────────────


def _codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def _codex_credentials() -> Optional[tuple[str, str]]:
    """``(access_token, account_id)`` from the Codex login, or None.

    The return value goes straight into request headers and nowhere else.
    Callers must not log it, embed it in an error, or return it over HTTP —
    the one job of every caller in this module.
    """
    path = _codex_home() / "auth.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    tokens = doc.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access = tokens.get("access_token")
    account = tokens.get("account_id")
    if not isinstance(access, str) or not access:
        return None
    return access, account if isinstance(account, str) else ""


# ── generation ────────────────────────────────────────────────────────


async def generate(
    prompt: str,
    *,
    aspect_ratio: Optional[str] = None,
    timeout: float = 180.0,
) -> GeneratedImage:
    """Draw one image on whichever OpenAI path is configured.

    Raises ``ImageGenError`` when every configured path failed, naming what
    was tried — the same shape `run_llm_chain` reports, and for the same
    reason: "it didn't work" is not actionable when there are two paths.
    """
    text = (prompt or "").strip()
    if not text:
        raise ImageGenError("prompt rỗng")
    if len(text) > _MAX_PROMPT_CHARS:
        raise ImageGenError(
            f"prompt quá dài: {len(text)} ký tự > {_MAX_PROMPT_CHARS}"
        )

    order = sources()
    if not order:
        raise ImageGenError(
            "Chưa bật đường tạo ảnh OpenAI nào. Mở Cài đặt: dán API key rồi bật "
            "OPENAI_IMAGE_ENABLED, hoặc bật OPENAI_IMAGE_RELAY_ENABLED nếu đã "
            "đăng nhập Codex bằng tài khoản ChatGPT."
        )

    size = size_for(aspect_ratio)
    attempts: list[str] = []
    for name in order:
        try:
            if name == "api-key":
                return await _via_api_key(text, size, timeout)
            return await _via_relay(text, size, timeout)
        except ImageGenError as exc:
            attempts.append(f"{name}: {exc}")
            logger.warning("openai_images: %s failed — %s", name, exc)

    raise ImageGenError("Mọi đường tạo ảnh OpenAI đều lỗi — " + " | ".join(attempts))


async def _via_api_key(prompt: str, size: str, timeout: float) -> GeneratedImage:
    key = secrets.get_api_key("openai")
    if not key:
        raise ImageGenError("chưa có OpenAI API key")

    payload = {
        "model": model_name(),
        "prompt": prompt,
        "size": size,
        "quality": quality(),
        "n": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _API_URL,
                headers={
                    "authorization": f"Bearer {key}",
                    "content-type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"hết thời gian chờ sau {timeout}s") from exc
    except httpx.HTTPError as exc:
        raise ImageGenError(f"lỗi truyền tải: {exc}") from exc

    if resp.status_code != 200:
        raise ImageGenError(f"HTTP {resp.status_code}: {safe_error_message(resp)}")
    return _image_from_api_body(resp, "api-key")


async def _via_relay(prompt: str, size: str, timeout: float) -> GeneratedImage:
    creds = _codex_credentials()
    if creds is None:
        raise ImageGenError("chưa đăng nhập Codex bằng tài khoản ChatGPT")
    access, account = creds

    headers = {
        "authorization": f"Bearer {access}",
        "content-type": "application/json",
        "originator": "codex_cli_rs",
    }
    if account:
        headers["chatgpt-account-id"] = account

    payload = {
        "model": "gpt-5",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "tools": [
            {
                "type": "image_generation",
                "size": size,
                "quality": quality(),
            }
        ],
        "tool_choice": "auto",
        "store": False,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_RELAY_URL, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"relay hết thời gian chờ sau {timeout}s") from exc
    except httpx.HTTPError as exc:
        raise ImageGenError(f"relay lỗi truyền tải: {exc}") from exc

    if resp.status_code in (401, 403):
        # Deliberately specific. The access token expires and this is the
        # one failure a user can fix in five seconds — but only if told.
        raise ImageGenError(
            "relay bị từ chối (token Codex có thể đã hết hạn) — chạy "
            "`codex login` rồi thử lại"
        )
    if resp.status_code != 200:
        raise ImageGenError(
            f"relay HTTP {resp.status_code}: {safe_error_message(resp)}"
        )
    return _image_from_relay_body(resp, "relay")


# ── response parsing ──────────────────────────────────────────────────


def _decode(b64: str, source: str) -> GeneratedImage:
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImageGenError("ảnh trả về không phải base64 hợp lệ") from exc
    if not raw:
        raise ImageGenError("ảnh trả về rỗng")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageGenError(
            f"ảnh {len(raw) // (1024 * 1024)}MB vượt trần {MAX_IMAGE_BYTES // (1024 * 1024)}MB"
        )
    from flowboard.services import image_ingest

    mime = image_ingest.sniff_mime(raw)
    if mime is None:
        raise ImageGenError("dữ liệu trả về không phải ảnh nhận dạng được")
    return GeneratedImage(data=raw, mime=mime, source=source)


def _image_from_api_body(resp: httpx.Response, source: str) -> GeneratedImage:
    try:
        body = resp.json()
    except ValueError as exc:
        raise ImageGenError("phản hồi không phải JSON") from exc
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise ImageGenError("phản hồi không có ảnh nào")
    first = items[0]
    if not isinstance(first, dict):
        raise ImageGenError("phản hồi có dạng lạ")
    b64 = first.get("b64_json")
    if isinstance(b64, str) and b64:
        return _decode(b64, source)
    if first.get("url"):
        # Only the retired dall-e models ever answered this way. Saying so
        # beats a generic parse failure, because the fix is a setting.
        raise ImageGenError(
            f"model {model_name()!r} trả ảnh bằng URL chứ không phải dữ liệu — "
            "đặt OPENAI_IMAGE_MODEL về một model gpt-image"
        )
    raise ImageGenError("phản hồi không chứa dữ liệu ảnh")


def _image_from_relay_body(resp: httpx.Response, source: str) -> GeneratedImage:
    """Pull the image out of a Responses-API payload.

    Written to tolerate more than one shape on purpose. This endpoint is an
    internal one with no published schema, so pinning the parser to a single
    field is how it breaks silently after an update that only moved things
    around.
    """
    try:
        body = resp.json()
    except ValueError as exc:
        raise ImageGenError("relay trả về không phải JSON") from exc

    # Try every candidate, not just the first. `result` and `data` are
    # ordinary field names in this payload, so a status string can easily sit
    # ahead of the picture — and stopping at the first candidate would report
    # "no image" about a response that contained one. Bounded so a payload
    # full of strings cannot turn one response into thousands of decodes.
    failure: Optional[ImageGenError] = None
    tried = 0
    for b64 in _walk_for_image(body):
        if tried >= _MAX_IMAGE_CANDIDATES:
            break
        tried += 1
        try:
            return _decode(b64, source)
        except ImageGenError as exc:
            failure = failure or exc
    if failure is not None:
        raise failure
    raise ImageGenError("relay không trả về ảnh nào nhận ra được")


def _walk_for_image(node: Any, depth: int = 0):
    """Yield every string that could be base64 image data.

    Deliberately loose: this endpoint has no published schema, so the filter
    that decides what is an image is `_decode` (which sniffs magic bytes),
    not a guess about field names or lengths made up here. An earlier version
    only yielded strings over 256 characters, which looked like a safety
    check and was not one — the real protection is that a non-image simply
    fails to decode and the next candidate is tried.
    """
    if depth > 8:
        return
    if isinstance(node, dict):
        for field in ("result", "b64_json", "image_base64", "data"):
            val = node.get(field)
            if isinstance(val, str) and val:
                yield val
        for val in node.values():
            yield from _walk_for_image(val, depth + 1)
    elif isinstance(node, list):
        for val in node:
            yield from _walk_for_image(val, depth + 1)
