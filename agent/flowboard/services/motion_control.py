"""Motion transfer: a reference clip, a model, an outfit, a background.

The packaged tool's "Hoán đổi cử động" node — dance videos, outfit swaps,
copying a movement from a clip someone already has.

**NOT REACHABLE IN THIS BUILD — read this before wiring it up again.**

An earlier pass concluded that the driving video never leaves the machine,
citing the log line ``[Thành Phần] Endpoint: … | Images= | Entities=`` and the
``veo_3_1_r2v_*`` model keys. Both of those belong to a different function:
they sit inside `_run_sync_character` (module ``API_sync_chactacter.py``),
which is the Component dispatch, not Motion Control. Re-reading the binary
around `run_motion_control` itself says the opposite, and specifically:

* its signature takes ``video_path`` **and an ``upload_video`` callable**;
* the constant beside it is
  ``FLOW_VIDEO_UPLOAD_ENDPOINT = https://labs.google/fx/api/upload-video``,
  with a ``FLOW_VIDEO_UPLOAD_CHUNK_SIZE`` — the clip is uploaded in chunks;
* the endpoint is ``/v1/video:batchAsyncGenerateVideoEditVideo`` and the model
  key is ``abra_edit``, neither of which exists anywhere in this build;
* the payload carries ``videoInput.{mediaId, startFrameIndex, endFrameIndex}``
  measured in ``VIDEO_INPUT_TIMELINE_FPS``, plus ``referenceImages`` under the
  aliases ``@nhan_vat`` / ``@trang_phuc`` / ``@background``.

So Motion Control is Flow's **Edit Video** feature: video in, video out, with
reference images alongside. Dispatching a reference-image request instead
produces a different generation at a different price, which is why this node
was taken off the canvas (`routes/nodes.NodeType`,
`pipeline_executor._GENERATION_NODE_TYPES`, `estimate.BILLABLE_TYPES`,
`AddNodePalette`) rather than left to spend money under a wrong label. An exe
workflow carrying one imports as a note.

What survives here is the part that was mined correctly and is still needed for
the rebuild: the port order, the four refusals, and the audio plan. Before
re-registering the node, the open questions are the upload protocol, the
response shape that yields the source clip's ``mediaId``, and the credit price
of ``abra_edit``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: The driving clip. Local-only, for the reasons in the module docstring.
VIDEO_PORTS: tuple[Optional[str], ...] = ("motion_input_video", "video")

#: The three image slots, in the order the packaged tool labels them:
#: Ảnh Nhân vật · Ảnh Sản Phẩm · Ảnh Background. Order is not decoration —
#: it is the order they enter the `Images=` list, and swapping the model with
#: the product is a different video.
IMAGE_PORTS: tuple[str, ...] = ("image_1", "image_2", "image_3")

IMAGE_LABELS: dict[str, str] = {
    "image_1": "Ảnh nhân vật",
    "image_2": "Ảnh sản phẩm",
    "image_3": "Ảnh background",
}

#: What the tool offers instead of a full run. `outfit_only` stops after the
#: still, `video_only` skips the outfit step — both are real user choices, so
#: an unknown value is not silently treated as "do everything".
MANUAL_ACTIONS: tuple[str, ...] = ("outfit_only", "video_only")

#: Durations the node offers: 4, 6, 8 or 10 seconds.
#:
#: This read `(5, 10)` first, taken from a UI label elsewhere in the packaged
#: tool. The binary states 4/6/8/10 for Motion Control in three separate
#: places, and this build's own worker accepts exactly that set — so `5`
#: could never have dispatched at all. A number mined from the wrong screen
#: looks just as measured as one mined from the right screen, which is why
#: the worker gate is the one that gets to be right.
VALID_DURATIONS: tuple[int, ...] = (4, 6, 8, 10)


def images_in(upstream) -> list[tuple[str, str]]:
    """``[(port, media_id)]`` for the filled image slots, in slot order.

    Slots are optional and independently useful: a dance video needs only
    the model, an outfit swap needs model plus garment. Requiring all three
    would refuse the two commonest uses.
    """
    found: dict[str, str] = {}
    for wire in upstream:
        port = wire.port if isinstance(wire.port, str) else ""
        if port not in IMAGE_PORTS:
            continue
        media = (wire.node.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip() and port not in found:
            found[port] = media.strip()
    # The comprehension is what actually keeps non-image wires out — it
    # walks IMAGE_PORTS, so nothing else can appear however `found` was
    # filled. The `continue` above is an early-out, not the guard; a
    # mutation removing it changes nothing, which is how that was
    # established. Remove THIS line and the driving clip reaches `Images=`.
    return [(p, found[p]) for p in IMAGE_PORTS if p in found]


def driving_video(upstream) -> Optional[str]:
    """The reference clip's media id, if one is wired.

    Never sent to Flow. Used for its audio and for the card preview.
    """
    for wire in upstream:
        if wire.port not in VIDEO_PORTS:
            continue
        media = (wire.node.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip():
            return media.strip()
    return None


def manual_action(settings: dict) -> Optional[str]:
    raw = str(settings.get("motion_manual_action") or "").strip().lower()
    if not raw:
        return None
    if raw in MANUAL_ACTIONS:
        return raw
    logger.info("motion_control: unknown manual action %r", raw)
    return None


def duration_s(settings: dict) -> Optional[int]:
    """5 or 10, or None. Not defaulted: duration is what an r2v dispatch is
    billed on, so guessing it bills a length nobody chose."""
    for key in ("motion_duration", "duration", "duration_s"):
        raw = settings.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw in VALID_DURATIONS:
            return raw
        if isinstance(raw, str):
            digits = "".join(c for c in raw if c.isdigit())
            if digits and int(digits) in VALID_DURATIONS:
                return int(digits)
    return None


def _flag(settings: dict, key: str) -> bool:
    value = settings.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "1", "on", "yes", "checked")


def audio_plan(settings: dict, video_media_id: Optional[str]) -> dict[str, Any]:
    """What to do with sound once the clip comes back.

    Three switches that interact, so they are resolved together rather than
    read one at a time by the caller:

    * ``motion_use_source_audio`` — carry the driving clip's audio over. The
      one thing the local video is genuinely needed for.
    * ``motion_mute_original_audio`` — drop whatever the generated clip has.
    * ``motion_enable_bgm`` — lay a music bed under it.

    "Use the source audio" without a source video is the contradiction worth
    catching here: it reads as configured and silently does nothing.
    """
    use_source = _flag(settings, "motion_use_source_audio")
    plan: dict[str, Any] = {
        "useSourceAudio": bool(use_source and video_media_id),
        "muteOriginal": _flag(settings, "motion_mute_original_audio"),
        "bgm": _flag(settings, "motion_enable_bgm"),
    }
    if use_source and not video_media_id:
        plan["warning"] = (
            "Đã bật dùng audio của video gốc nhưng chưa nối video vào — bỏ qua."
        )
    return plan


def validate(upstream, settings: dict) -> Optional[str]:
    """Why this node cannot dispatch, as a short code, or None.

    Checked before spending, in the same spirit as `character_ports.validate`:
    every refusal below is a board that would otherwise pay for a clip that
    cannot be what was asked for.
    """
    images = images_in(upstream)
    if not images:
        return "motion_no_images"

    if _flag(settings, "motion_replace_outfit_enabled") and len(images) < 2:
        # Swapping an outfit needs the outfit. With only the model wired,
        # the dispatch would return the model unchanged and bill for it.
        return "motion_outfit_without_garment"

    if _flag(settings, "motion_add_background_enabled") and "image_3" not in dict(images):
        return "motion_background_without_image"

    if duration_s(settings) is None:
        # r2v bills by length. A default here would bill a length nobody
        # chose — the same reason `node_settings.duration_s` refuses to
        # invent one.
        return "motion_no_duration"
    return None


def explain(code: str) -> str:
    return {
        "motion_no_images": (
            "Chưa nối ảnh nào vào node Motion Control. Cần ít nhất ảnh nhân vật "
            "ở cổng image_1."
        ),
        "motion_outfit_without_garment": (
            "Đã bật thay trang phục nhưng chưa có ảnh sản phẩm ở cổng image_2 — "
            "chạy sẽ ra đúng người mẫu cũ mà vẫn tính tiền."
        ),
        "motion_background_without_image": (
            "Đã bật thêm background nhưng chưa có ảnh ở cổng image_3."
        ),
        "motion_no_duration": (
            "Chưa chọn thời lượng (5s hoặc 10s). Lane r2v tính tiền theo thời "
            "lượng nên không tự chọn hộ."
        ),
    }.get(code, code)
