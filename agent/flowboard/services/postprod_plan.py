"""Which local operations a post-production node runs, and on what.

The canvas has seven post-production node types; the worker has one
``postprod`` request type carrying an ``op``. This module is the bridge: it
reads a node, its settings and the wires coming into it, and answers with the
ordered list of ops to dispatch.

**Inputs are chosen by PORT, not by guessing from the upstream node's type.**
That distinction is not academic. In the shipped workflows an ``edit_video``
node has a ``create_voice`` node wired to its ``voice`` port — that is
narration. Picking "the first upstream that produces audio" took the
narration and mixed it in as background music at 0.9 volume. The wire already
said what it was; nothing was reading it. ``Edge.source_port`` /
``Edge.target_port`` exist for this.

Ports observed across the nine shipped files, which is where this table comes
from rather than from a guess:

    merge_video          media ← gen_video
    add_bgm              media ← merge_video          (no audio wire: settings)
    align_video_voice    media ← video, voice ← create_voice, text ← prompt
    edit_video           media ← video, voice ← create_voice,
                         title ← prompt, image_thumbnail ← image
    extract_last_frame   video ← gen_video
    create_voice         text  ← prompt
    analyze_video        video / image_1 / prompt

Most nodes are one op. ``edit_video`` is not — in the packaged tool it is a
box with fifty settings that runs several passes over the same clip — so it
answers with a chain, each pass consuming the previous pass's output.

A node whose inputs are not on the graph yet returns an empty list. That is
not an error: it is the same "nothing to do here" the executor already uses
for a generation node with no prompt, and it lets a partially wired board run
the parts that are ready.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from flowboard.services import node_settings, tts

logger = logging.getLogger(__name__)

#: Placeholder for "the previous op's output" inside a chain. The executor
#: substitutes the real media id once the earlier request settles.
PREVIOUS = "__previous__"

#: Prefix for "the output of step N" (1-based), for the case `PREVIOUS`
#: cannot express: a pass needing TWO earlier outputs.
#:
#: The subtitle pair is why this exists. `transcribe` produces an SRT, then
#: `subtitles` needs BOTH that SRT and the clip as it was before the
#: transcribe. With only `PREVIOUS` the burn's video input would resolve to
#: the SRT — it would try to burn subtitles onto a subtitle file.
STEP_PREFIX = "__step_"


def step_output(index: int) -> str:
    """Reference the output of the ``index``-th op in this chain (1-based)."""
    if index < 1:
        raise ValueError(f"step index is 1-based, got {index}")
    return f"{STEP_PREFIX}{index}__"

#: Ports that carry the clip being worked on. `None` is included because an
#: edge drawn by hand on the canvas has no port name, and the main input is
#: what someone dragging a wire into a merge node means.
VIDEO_PORTS: tuple[Optional[str], ...] = ("media", "video", "media_1", None)
#: Ports that carry spoken narration. Deliberately strict: an unnamed wire is
#: NOT assumed to be a voice. Guessing is what mixed narration in as music.
VOICE_PORTS: tuple[Optional[str], ...] = ("voice",)
#: Ports that carry text.
#:
#: The three named branches are one node with three answers, not three nodes.
#: A scene written by an AI produces an image description, a motion
#: description and a line of dialogue — different text for different
#: consumers, and the packaged tool keeps them on separate sockets for
#: exactly that reason. Merged into one output they all become the same
#: string, which means the voice reads the camera directions aloud.
IMAGE_PROMPT_PORTS: tuple[Optional[str], ...] = ("image_prompts", "image_prompt")
VIDEO_PROMPT_PORTS: tuple[Optional[str], ...] = ("video_prompts", "video_prompt")
VOICE_PROMPT_PORTS: tuple[Optional[str], ...] = ("voice_prompts", "voice_prompt")
TEXT_PORTS: tuple[Optional[str], ...] = (
    "text", "prompt", None,
    *IMAGE_PROMPT_PORTS, *VIDEO_PROMPT_PORTS, *VOICE_PROMPT_PORTS,
)

#: Which field on a prompt node each branch reads, when the node carries
#: more than one. Absent → the node's plain `prompt`, so a hand-typed node
#: with one text keeps working on every socket.
BRANCH_FIELDS: dict[str, str] = {
    "image_prompts": "imagePrompt", "image_prompt": "imagePrompt",
    "video_prompts": "videoPrompt", "video_prompt": "videoPrompt",
    "voice_prompts": "voicePrompt", "voice_prompt": "voicePrompt",
    # A fourth socket, found by deriving this table from the packaged
    # workflows instead of from memory: `nguoi_que_new` and
    # `sao_chep_nguoi_que_new_1706` both wire
    # `analyze_video.thumbnail_prompt -> text_prompt.text`, and the executor
    # does write `thumbnailPrompt`. Without the entry the wire fell through to
    # the node's plain `prompt`, which for an analyse node is the SCENE
    # DESCRIPTION — so the cover image was generated, and paid for, from the
    # wrong sentence. Same failure as `voice_prompts` reading the camera
    # directions aloud.
    "thumbnail_prompt": "thumbnailPrompt",
    "thumbnail_prompts": "thumbnailPrompt",
}
#: Ports that carry the still a video generation starts from. `None` is
#: included so a wire drawn by hand still works, as with VIDEO_PORTS.
START_FRAME_PORTS: tuple[Optional[str], ...] = ("start_frame", "image", None)
#: The cover frame appended to a finished clip. A PORT, not a settings path —
#: read off the shipped `nguoi_que_new` workflow, where it arrives as
#: `gen_image.image_out_1 -> edit_video.image_thumbnail`. Strict: an unnamed
#: wire into `edit_video` is the clip being edited, not a cover.
THUMBNAIL_PORTS: tuple[Optional[str], ...] = ("image_thumbnail",)

#: Settings keys naming a background-music file. It arrives as a setting, not
#: a wire — no shipped workflow wires audio into a BGM input.
BGM_KEYS = ("bgm_sample_file", "audio_path")


class Upstream:
    """One wire into a node: where it came from and which socket it landed on.

    A plain tuple would do, but the port is the half that is easy to drop,
    and dropping it is the bug this module was rewritten to fix.
    """

    __slots__ = ("node", "port")

    def __init__(self, node: Any, port: Optional[str]) -> None:
        self.node = node
        self.port = port


def _truthy(value: Any) -> bool:
    """The exe writes these flags as booleans, "1"/"0" strings and ints."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _number(value: Any) -> Optional[float]:
    """A real number, or None. `True` is an int in Python and would pass as
    a volume of 1.0, so booleans are refused explicitly."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _media_of(node) -> Optional[str]:
    mid = (node.data or {}).get("mediaId")
    return mid if isinstance(mid, str) and mid else None


def _all_media_of(node) -> list[str]:
    """Every output this node produced, variants included.

    A generation node holds `mediaIds` with one entry per variant and a
    positional `None` where a variant was blocked. `mediaId` is only the
    first of them — reading it alone is how a merge of a four-variant node
    saw one clip and decided there was nothing to join.
    """
    data = node.data or {}
    ids = data.get("mediaIds")
    if isinstance(ids, list):
        out = [i for i in ids if isinstance(i, str) and i]
        if out:
            return out
    single = _media_of(node)
    return [single] if single else []


def _on_ports(
    upstream: Iterable[Upstream], ports: tuple[Optional[str], ...]
) -> list[Upstream]:
    return [u for u in upstream if u.port in ports]


def _media_on(
    upstream: Iterable[Upstream], ports: tuple[Optional[str], ...]
) -> Optional[str]:
    """The first media id arriving on any of ``ports``.

    `_video_in` and `_voice_in` are this with their port list baked in; this
    is the general form, for a role that has exactly one port and no reason
    to earn a named helper of its own.
    """
    for u in _on_ports(upstream, ports):
        mid = _media_of(u.node)
        if mid:
            return mid
    return None


def _video_in(upstream: Iterable[Upstream]) -> Optional[str]:
    return _media_on(upstream, VIDEO_PORTS)


def _voice_in(upstream: Iterable[Upstream]) -> Optional[str]:
    for u in _on_ports(upstream, VOICE_PORTS):
        mid = _media_of(u.node)
        if mid:
            return mid
    return None


def branch_text(node, port: Optional[str]) -> Optional[str]:
    """The text a consumer on ``port`` should read from ``node``.

    Three answers can live on one prompt node — an image description, a motion
    description and a spoken line — and the socket says which one is wanted.
    Order matters and each step is a fix of its own:

    1. the branch field for this port, when the node carries one;
    2. ``composedPrompt`` — what the node's own model WROTE, as opposed to the
       instruction it was given. An imported `gemini_prompt` node's `prompt` is
       "Bạn là chuyên gia tạo kịch bản…", and passing that downstream told the
       generator to be an expert instead of describing a picture;
    3. ``prompt`` — the plain text, so a hand-typed node keeps working on every
       socket.

    One definition, used by both the plan layer and the executor. Two copies
    drifted apart once already: `create_voice` read step 3 only, so the voice
    read the camera directions aloud — the exact failure the branch sockets
    were built to prevent.
    """
    data = node.data or {}
    field = BRANCH_FIELDS.get(port or "")
    for key in ((field,) if field else ()) + ("composedPrompt", "prompt"):
        text = data.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _text_on(
    upstream: Iterable[Upstream], ports: tuple[Optional[str], ...]
) -> Optional[str]:
    for u in _on_ports(upstream, ports):
        text = branch_text(u.node, u.port)
        if text:
            return text
    return None


def _bgm_track(settings: dict) -> Optional[str]:
    """The background-music file named in this node's settings.

    The handler resolves it against the bundled library first
    (`assets.bgm_path`) and then the media cache, so a bare filename from the
    packaged tool works without being imported first.

    Falls back to the BASENAME of an absolute path, because the shipped
    workflows carry the original author's own machine paths — three of the
    four music references read
    ``d:\\ABCD\\VEO3_GROK_NEW\\data_general\\nhac_nen\\1. Nhạc.MP3``, a
    directory that does not exist here. That file's name *is* in this
    machine's `nhac_nen` library, so trimming the dead directory turns a
    guaranteed `missing_media` into a working music pass.

    Trimming is safe: `assets.bgm_path` confines its lookup to the library
    directory and refuses anything that escapes it, so a foreign path can
    only ever resolve to a file that was already there.
    """
    for key in BGM_KEYS:
        value = settings.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        # Windows and POSIX separators both appear in the shipped files.
        base = value.replace("\\", "/").rsplit("/", 1)[-1]
        return base or value
    return None


def _settings_of(node) -> dict:
    settings = (node.data or {}).get("sourceSettings") or {}
    return settings if isinstance(settings, dict) else {}


#: Stands in for an input that is not there yet, when the caller is asking
#: about SHAPE rather than about what to dispatch. Never reaches the worker:
#: `assume_ready` is only set by the cost estimate, which dispatches nothing.
PENDING = "__pending__"


def ops_for(node, upstream: list[Upstream], *, assume_ready: bool = False) -> list[dict]:
    """The ops this node dispatches, in order. Empty when nothing is ready.

    ``assume_ready`` answers a different question: how many passes do this
    node's settings enable, ignoring whether the inputs exist yet. The cost
    estimate needs that — it runs BEFORE anything has produced media, and a
    four-pass edit reported as "not ready" would show up as one step.

    It shares this function rather than counting flags separately, because a
    second implementation of "which passes are on" is exactly the kind of
    drift that put the planner and the handler in different languages.
    """
    settings = _settings_of(node)
    if assume_ready:
        upstream = _with_placeholders(node, upstream)

    if node.type == "merge_video":
        return _merge_ops(upstream, settings)

    if node.type == "extract_last_frame":
        video = _video_in(upstream)
        return [{"op": "last_frame", "video": video}] if video else []

    if node.type == "add_bgm":
        video = _video_in(upstream)
        track = _bgm_track(settings)
        if not video or not track:
            return []
        return [_bgm_op(video, track, settings, voice=False)]

    if node.type == "create_voice":
        text = _text_on(upstream, TEXT_PORTS) or (node.data or {}).get("prompt")
        if not isinstance(text, str) or not text.strip():
            return []
        # The voice name is resolved HERE, not at dispatch, because one of the
        # answers is "no narration at all" — eleven of the nine shipped
        # workflows store `🗣️ Không chọn` — and that answer has to remove the
        # pass, not produce a silent file the mix then lays over the video.
        plan = tts.plan_voice(
            node_settings.tts_engine(settings), settings.get("voice")
        )
        if plan.voice is None and not plan.note:
            logger.info(
                "create_voice: node asked for no narration (%r) — pass skipped",
                settings.get("voice"),
            )
            return []
        op: dict = {"op": "narrate", "text": text.strip(), "engine": plan.engine}
        if plan.voice:
            op["voice"] = plan.voice
        if plan.note:
            # Carried on the op so the card can say it. A substituted voice the
            # user never hears about is a voice that quietly changed.
            op["voiceNote"] = plan.note
            logger.info("create_voice: %s", plan.note)
        return [op]

    if node.type == "align_video_voice":
        video = _video_in(upstream)
        voice = _voice_in(upstream)
        if not video or not voice:
            return []
        chain: list[dict] = [{"op": "fit_narration", "video": video, "audio": voice}]
        if _truthy(settings.get("upscale_2k_4k")):
            chain.append(_upscale_op(PREVIOUS, settings))
        return chain

    if node.type == "edit_video":
        return _edit_chain(upstream, settings)

    if node.type == "remove_watermark":
        video = _video_in(upstream)
        if not video:
            return []
        op: dict = {"op": "remove_watermark", "video": video}
        # Optional on purpose. The bundled tool DETECTS the mark — that is
        # the whole reason to use it over `delogo`, which blurs a box the
        # caller nominates. Always passing a region would throw the
        # detection away and reproduce delogo's weakness with extra steps.
        region = settings.get("watermark_region")
        if isinstance(region, str) and region.strip():
            op["region"] = region.strip()
        # Where the mark is, for the CPU fallback ONLY. The detector is still
        # asked first and is still allowed to find it anywhere; this is the
        # box MI-GAN paints out when the detector declines, and it is why a
        # decline stops meaning "give up".
        box = _logo_box(settings)
        if box is not None:
            op["fallbackBox"] = box
        return [op]

    if node.type == "sync_image_voice":
        # `media` carries the still image here, not a clip — the node's whole
        # job is turning one frame into a video the length of the narration.
        image = _video_in(upstream)
        voice = _voice_in(upstream)
        if not image or not voice:
            return []
        width, height = _frame_for(settings.get("aspect_ratio"))
        op = {
            "op": "ken_burns",
            "image": image,
            "audio": voice,
            "zoomIn": "out" not in str(settings.get("effect", "")).lower(),
            "width": width,
            "height": height,
        }
        speed = _number(settings.get("zoom_speed"))
        if speed is not None:
            op["zoomSpeed"] = speed
        chain = [op]
        if _truthy(settings.get("upscale_2k_4k")):
            chain.append(_upscale_op(PREVIOUS, settings))
        return chain

    if node.type == "review_video":
        # One vision call that scores the clip against the prompt that asked
        # for it. Costs no Flow credits and is not free either, which is why
        # `routes.estimate` counts it on its own line.
        video = _video_in(upstream)
        if not video:
            return []
        op: dict = {"op": "review", "video": video}
        prompt = _text_on(upstream, TEXT_PORTS) or (node.data or {}).get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            op["prompt"] = prompt.strip()
        threshold = _number(settings.get("review_threshold"))
        if threshold is not None:
            op["threshold"] = threshold
        return [op]

    if node.type == "analyze_video":
        # Reads a video with Gemini and writes a script. Not ffmpeg — it goes
        # through /api/vision/video, a different request type. Returning a
        # local op here would make the node report success having done
        # something else entirely.
        return []

    logger.warning("postprod_plan: no ops defined for node type %r", node.type)
    return []


def _with_placeholders(node, upstream: list[Upstream]) -> list[Upstream]:
    """Fill in the wires this node type needs, for a shape-only question.

    Existing wires are kept as they are; only the missing roles get a stand-in
    so the pass count reflects the settings rather than the current state of
    the board.
    """

    class _Pending:
        type = "video"
        data = {"mediaId": PENDING, "mediaIds": [PENDING, PENDING]}

    class _PendingText:
        type = "prompt"
        data = {"prompt": PENDING}

    filled = list(upstream)
    if not _on_ports(filled, VIDEO_PORTS):
        filled.append(Upstream(_Pending(), "media"))
    if node.type in ("edit_video", "align_video_voice") and not _on_ports(
        filled, VOICE_PORTS
    ):
        filled.append(Upstream(_Pending(), "voice"))
    if (
        node.type == "create_voice"
        and not _text_on(filled, TEXT_PORTS)
        and not (node.data or {}).get("prompt")
    ):
        # Only when there is no script anywhere. A node with its own typed
        # script IS ready, and substituting the placeholder for it made the
        # estimate plan `narrate __pending__` — harmless while it only counted
        # passes, wrong as soon as anything measured the script's length.
        filled.append(Upstream(_PendingText(), "text"))
    # No placeholder for the title: unlike the voice and music passes it has
    # no enable flag, so it exists only if someone wired one. Assuming it
    # would count a pass the settings never asked for.
    return filled


def _merge_ops(upstream: list[Upstream], settings: dict) -> list[dict]:
    """Join the clips wired in, variants expanded, then retime if asked.

    In three of the nine shipped workflows the merge node has exactly ONE
    upstream — a generation node holding four variants. Reading one media id
    per upstream saw a single clip and did nothing.

    `video_speed` belongs to THIS node, not to `edit_video`. Checked against
    the imported boards rather than assumed: the two nodes carrying it are
    `merge_video` (1.2 and 1.25, as floats), while `edit_video` carries the
    differently-named `auto_video_speed` and `voice_speed`. The first
    version of this put the retiming in the edit chain, where no workflow
    would ever have triggered it.

    Retiming last, after the join: speeding each clip up before concatenating
    would re-encode every one of them separately and compound the loss.
    """
    clips: list[str] = []
    for u in _on_ports(upstream, VIDEO_PORTS):
        clips.extend(_all_media_of(u.node))
    # One clip is not a merge: concatenating it re-encodes for no gain.
    if len(clips) < 2:
        return []
    ops: list[dict] = [{"op": "concat", "clips": clips}]
    speed = _speed_op(PREVIOUS, settings)
    if speed is not None:
        ops.append(speed)
    return ops


def _frame_for(aspect: Any) -> tuple[int, int]:
    """Pixel size for the node's aspect setting.

    The shipped value is decorated — `"🖥️ 16:9"` — so this matches on the
    ratio inside rather than on the whole string.
    """
    text = str(aspect or "")
    if "9:16" in text:
        return 1080, 1920
    if "1:1" in text:
        return 1080, 1080
    return 1920, 1080


#: ffmpeg's `atempo` accepts 0.5–2.0 per stage and `change_audio_speed` chains
#: at most two, so anything outside this range fails the whole node at the
#: filter. `video_speed` has been range-checked since it was written;
#: `voice_speed` was forwarded raw, so a template saying 5.0 killed the edit
#: instead of being skipped with a note.
_VOICE_SPEED_RANGE = (0.25, 4.0)


def _voice_speed(settings: dict) -> Optional[float]:
    """A usable narration speed, or None to leave the narration alone."""
    speed = _number(settings.get("voice_speed"))
    if speed is None or abs(speed - 1.0) <= 1e-3:
        return None
    low, high = _VOICE_SPEED_RANGE
    if not (low <= speed <= high):
        logger.info(
            "postprod_plan: voice_speed %r outside %s — giữ tốc độ gốc",
            speed, _VOICE_SPEED_RANGE,
        )
        return None
    return speed


#: What `upscale_resolution` actually contains in the shipped workflows, and
#: the height each value means. They are STRINGS — `'2K'`, `'4K'`, `'None'` —
#: and the int-only check that used to guard this dropped every one, after
#: which the handler's default of 4 applied a 4x enlargement to a node that
#: had asked for 2K.
_RESOLUTION_HEIGHTS: dict[str, int] = {
    "1k": 1080, "1080": 1080, "1080p": 1080, "fhd": 1080,
    "2k": 1440, "1440": 1440, "1440p": 1440,
    "4k": 2160, "2160": 2160, "2160p": 2160, "uhd": 2160,
}


def _target_height(settings: dict) -> Optional[int]:
    """The height `upscale_resolution` asks for, whatever shape it arrives in."""
    raw = settings.get("upscale_resolution")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    text = str(raw or "").strip().lower()
    if not text or text == "none":
        return None
    if text in _RESOLUTION_HEIGHTS:
        return _RESOLUTION_HEIGHTS[text]
    digits = "".join(c for c in text if c.isdigit())
    if digits and int(digits) > 0:
        return int(digits)
    logger.info("postprod_plan: unmapped upscale_resolution %r", raw)
    return None


def _upscale_op(source: str, settings: dict) -> dict:
    """`_op_upscale` reads its input from `source`, not `video`.

    Two ways to say how much: a target height (`upscale_resolution`, what
    this canvas writes) or a multiplier (`scale`, what the packaged tool
    writes as `🔍 x2`). The height wins when both are present — it is the
    more specific answer, and a node carrying both has been edited here
    after being imported.
    """
    from flowboard.services import node_settings

    op: dict = {"op": "upscale", "source": source}
    height = _target_height(settings)
    if height is not None:
        op["targetHeight"] = height
        return op
    factor = node_settings.upscale_factor(settings)
    if factor is not None:
        # A MULTIPLIER, not a model scale. Both bundled models upscale by
        # exactly 4x and `upscale._validate` refuses anything else, so emitting
        # `scale: 2` failed the whole edit_video — the multiplier path every
        # packaged workflow writes could never run. The model still runs at its
        # native rate and the multiplier becomes the resample target, which is
        # the single resample path the engine was designed around.
        op["targetScale"] = float(factor)
    return op


def _bgm_op(video: str, track: str, settings: dict, *, voice: bool) -> dict:
    """Mix an audio track under a clip.

    The same op serves music and narration — ffmpeg does not care which — but
    the volumes come from different settings, and mixing narration at the
    music defaults would bury it.

    Parameter names are the handler's (`bgmVolume`, `origVolume`), not the
    packaged tool's (`bgm_volume`, `orig_volume`). Sending the exe's spelling
    meant every volume silently fell back to the default: music at 0.3 and
    the original audio at full, i.e. "mute the original" did not mute it.
    """
    op: dict = {"op": "bgm", "video": video, "track": track}

    level = _number(settings.get("voice_volume" if voice else "bgm_volume"))
    if level is not None:
        op["bgmVolume"] = level

    if _truthy(settings.get("mute_origin")) or _truthy(
        settings.get("mute_original_audio")
    ):
        op["origVolume"] = 0.0
    else:
        original = _number(settings.get("orig_volume"))
        if original is not None:
            op["origVolume"] = original

    for key, param in (("fade_in", "fadeIn"), ("fade_out", "fadeOut")):
        value = _number(settings.get(key))
        if value is not None:
            op[param] = value

    if not voice:
        # Where to start reading the music. The packaged tool calls it
        # `start_time` and ships 0; it matters for a track whose first
        # seconds are an intro nobody wants under every clip.
        offset = _number(settings.get("start_time"))
        if offset is not None and offset > 0:
            op["trackStart"] = offset
    return op


def _gemini_clean_op(video: str, settings: dict) -> dict:
    """Hand the clip to the detector, with the box as the fallback only.

    No box, still a pass: detecting the mark is what this tool is good at —
    it found `Veo-text 23x10` on a real clip and rebuilt the sky behind it —
    and nominating a region would throw that away. The box travels as
    ``fallbackBox`` for the one case where detection is not available: MI-GAN
    paints a hole someone points at, so without a box there is nothing for it
    to do and the pass simply reports the clip was left alone.
    """
    op: dict = {"op": "remove_watermark", "video": video, "optional": True}
    box = _logo_box(settings)
    if box is not None:
        op["fallbackBox"] = box
    return op


def _logo_op(video: str, settings: dict) -> Optional[dict]:
    """Remove the watermark, by whichever method the node asks for.

    The packaged tool offers two and defaults to `zoom`, which is what its
    shipped workflow uses: crop in so the mark falls outside the frame. The
    alternative, `delogo`, blurs it in place. They give visibly different
    results — zoom loses a border all round, delogo leaves a soft patch —
    so the setting decides rather than this module.
    """
    box = _logo_box(settings)
    if box is None:
        return None
    method = str(settings.get("veo_logo_method", "zoom")).strip().lower()
    if method == "zoom":
        op = {"op": "zoom_logo", "video": video, **box}
        zoom = _number(settings.get("veo_logo_zoom_percent"))
        if zoom is not None:
            op["zoomPercent"] = zoom
        return op
    return {"op": "delogo", "video": video, **box}


def _logo_box(settings: dict) -> Optional[dict]:
    """The watermark rectangle, as percentages of the frame.

    The packaged tool stores the box as percentages of the frame
    (`veo_logo_x_pct` = 78.0 and so on). They are passed through AS
    percentages: pixels cannot be computed here, because the frame size is
    not known until the handler probes the file.

    No box, no pass. `remove_logo` has no defaults on purpose — a guessed
    rectangle blurs the wrong part of the frame — so emitting the op without
    one would only produce a request that always fails.

    NOTE: the packaged tool's own default for this node is
    `veo_logo_method: "zoom"` — it crops in by `veo_logo_zoom_percent` to
    push the mark off the edge rather than blurring it. That is a different
    operation which this build does not have; blurring is what is offered
    here, and it does not give the same result.
    """
    box = {}
    for key, param in (
        ("veo_logo_x_pct", "xPct"),
        ("veo_logo_y_pct", "yPct"),
        ("veo_logo_w_pct", "widthPct"),
        ("veo_logo_h_pct", "heightPct"),
    ):
        value = _number(settings.get(key))
        if value is None:
            return None
        box[param] = value
    return box


def _subtitle_ops(source: str, settings: dict, *, base_index: int) -> list[dict]:
    """Transcribe the clip, then burn the result into it.

    Two passes, not one: the SRT arrives as its own media id so the burn
    step takes it the same way it takes any other input. Threading it
    through a private channel between the two would make this the only pair
    of ops in the module that talk to each other directly.

    `enable_sub` is set on six of the nine shipped workflows, so this is not
    an edge case — and unlike the rest of the chain it is NOT free: each
    transcription is one Gemini call against the user's own quota. The cost
    estimate counts it separately for that reason.

    `sub_style_type` picks between the two burns below: a static track, or
    the karaoke one that lights each word as it is spoken.
    """
    from flowboard.services import stt

    # Asked of `stt`, not of Gemini. Gemini used to be the only path that
    # took audio, so the gate was a key check — but there are now three
    # sources, and the check was left behind: with faster-whisper installed
    # and no Gemini key, subtitles were skipped on a machine that could
    # transcribe perfectly well, offline and for free.
    #
    # The gate itself stays. No source means the transcribe pass is a
    # guaranteed failure, and a failed pass fails the whole node —
    # discarding the passes that already succeeded. Skipping subtitles is
    # better than losing the edit.
    if not stt.sources():
        logger.info("postprod_plan: subtitles skipped, no speech-to-text source")
        return []

    # The transcribe pass is step `base_index + 1`; the burn that follows
    # needs its SRT *and* the clip that went into it, so both are named by
    # step rather than by "previous".
    # `wantWords` is the transcribe pass's only reason to prefer one source
    # over another: with it, `stt` drops Gemini from the chain entirely
    # (measured to invent timestamps) and uses whisper-1 or faster-whisper,
    # the two that align. Asked for only when a karaoke burn follows, because
    # a static track has no use for word timings and the reordering costs
    # Gemini's price advantage.
    ops: list[dict] = [{"op": "transcribe", "video": source}]
    if _is_karaoke(settings):
        ops[0]["wantWords"] = True
    srt_ref = step_output(base_index + 1)

    # "Karaoke ASS" lights each word as it is spoken; the plain style burns a
    # static line. Two different ops because they are two different ffmpeg
    # filters — `subtitles` with force_style versus `ass` with the styling
    # baked into the file, which is where the per-word timing lives.
    burn_op = "karaoke" if _is_karaoke(settings) else "subtitles"
    burn: dict = {"op": burn_op, "video": source, "srt": srt_ref}
    if burn_op == "karaoke":
        # The colour a word has BEFORE it is sung. Only karaoke has a use
        # for it, which is why it is read here and not in the shared loop.
        inactive = settings.get("sub_inactive_color")
        if isinstance(inactive, str) and inactive.strip():
            burn["inactiveColor"] = inactive.strip()
    for key, param, cast in (
        ("sub_font", "font", str),
        ("sub_size", "size", int),
        ("sub_color", "primaryColor", str),
        ("sub_outline_color", "outlineColor", str),
        ("sub_outline_width", "outlineWidth", float),
        ("sub_shadow", "shadow", float),
        ("sub_margin_v", "marginV", int),
    ):
        value = settings.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            burn[param] = cast(value)
        except (TypeError, ValueError):
            continue
    ops.append(burn)
    return ops


def _is_karaoke(settings: dict) -> bool:
    """Whether this node wants word-by-word highlighting.

    Matched on a substring rather than the whole value: the packaged tool
    writes `sub_style_type` with decoration, the same way every other label
    in these workflows arrives, so an equality test against "Karaoke ASS"
    would miss the emoji-prefixed spellings.
    """
    return "karaoke" in str(settings.get("sub_style_type") or "").lower()


def _aspect_op(source: str, settings: dict) -> Optional[dict]:
    """Re-frame the clip into another aspect ratio over a background.

    Not a letterbox — see `postprod.convert_aspect`. Two of the shipped
    workflows turn their 16:9 generations into the 9:16 card-on-a-backdrop
    layout with `enable_aspect_convert`, `aspect_output: "9:16"` and a
    `video_border_radius` of 69-81.

    The background image is passed through as written and resolved by the
    handler, which can reach both the media cache and the filesystem. The
    shipped workflows name the original author's own machine paths
    (`D:/TOOL/anh nv/...`), so it will usually be missing here; the handler
    falls back to `aspect_bg_color` and says so rather than failing a node
    over a backdrop.
    """
    ratio = settings.get("aspect_output")
    if not isinstance(ratio, str) or not ratio.strip():
        return None

    op: dict = {"op": "aspect", "video": source, "ratio": ratio.strip()}

    color = settings.get("aspect_bg_color")
    if isinstance(color, str) and color.strip():
        op["backgroundColor"] = color.strip()

    image = settings.get("aspect_bg_image")
    if isinstance(image, str) and image.strip():
        op["backgroundImage"] = image.strip()

    radius = _number(settings.get("video_border_radius"))
    if radius is not None:
        op["borderRadius"] = int(radius)

    zoom = _number(settings.get("video_zoom"))
    if zoom is not None:
        op["zoom"] = zoom
    return op


def _text_art_op(source: str, settings: dict, title: Optional[str]) -> Optional[dict]:
    """The styled captions the converted frame leaves room for.

    ``aspect_texts`` is a list of dicts written by the packaged tool's own
    dialog, and they are forwarded field for field — `postprod.TextArt` uses
    its spelling so there is no translation table to drift.

    ``{title}`` is a placeholder the shipped workflows use for the text
    coming in on the node's `title` port. A caption whose placeholder cannot
    be filled is DROPPED rather than drawn: burning a literal "{title}" into
    the picture is worse than leaving the space the layout already allows.
    """
    raw = settings.get("aspect_texts")
    if not isinstance(raw, list):
        return None

    captions: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if "{title}" in text:
            if not title:
                logger.info("postprod_plan: caption dropped, no title to fill it")
                continue
            text = text.replace("{title}", title)
        captions.append({**entry, "text": text.strip()})

    if not captions:
        return None
    return {"op": "text_art", "video": source, "texts": captions}


def _speed_op(source: str, settings: dict) -> Optional[dict]:
    """A retiming pass, when the workflow asks for one.

    Every packaged workflow that sets `video_speed` asks for 1.2 or 1.25, and
    this build ignored the key completely — so a board run came out
    noticeably longer than the same board through the packaged tool.

    Driven by `video_speed` alone. The packaged tool also carries
    `auto_video_speed` and `auto_adjust_video_speed`, and what those mean is
    NOT established: "auto" could enable this pass, or it could mean "fit the
    clip to the narration", which is a different operation with a different
    result. Reading them as an on/off switch would be a guess, and guessing
    wrong here changes every clip's length — so they are left alone until
    someone can say what the packaged tool does with them.
    """
    speed = _number(settings.get("video_speed"))
    if speed is None or abs(speed - 1.0) < 1e-6:
        return None
    if not 0.25 <= speed <= 4.0:
        logger.info("postprod_plan: ignoring out-of-range video_speed %r", speed)
        return None
    return {"op": "speed", "video": source, "speed": speed}


def _edit_chain(upstream: list[Upstream], settings: dict) -> list[dict]:
    """`edit_video` is several passes over one clip, driven by its flags.

    Ordered deliberately: the watermark goes before anything that adds pixels
    of our own, audio before the title, and the upscale last so the cheap
    passes run at the smaller size.

    The aspect conversion sits between those two groups, and the shipped
    workflows say where. The watermark box is stored as percentages of the
    SOURCE frame, so removal has to happen while that frame is still what it
    describes. Everything that draws — the title, the subtitles — has to come
    after, because the workflow positions them on the converted canvas:
    `nguoi_que_new` asks for `sub_margin_v: 357` on what becomes a 1280-tall
    frame, which is 28% up from the bottom, below the video card rather than
    on it. Burn first and the captions would be scaled down into the card.
    """
    video = _video_in(upstream)
    if not video:
        return []

    chain: list[dict] = []

    def source() -> str:
        # Only the first pass reads the original clip; every later pass takes
        # the previous pass's output, or they overwrite each other and only
        # the last one survives.
        return video if not chain else PREVIOUS

    # The generator's own watermark, taken out by the packaged tool's
    # DETECTOR — a different job from the VEO text logo below, and the
    # packaged tool runs both. Its own log line for this pass is "Xóa logo
    # Gemini: ưu tiên GPU, tự chuyển CPU nếu GPU không khả dụng": the GPU
    # executable first, the CPU model (MI-GAN, shipped beside it) when the GPU
    # one declines or cannot run. It goes first in the chain, as it does
    # there, so the detector sees the frame the clip arrived with.
    if _truthy(settings.get("auto_remove_gemini_video_watermark")):
        chain.append(_gemini_clean_op(source(), settings))

    if _truthy(settings.get("enable_remove_veo_logo")):
        op = _logo_op(source(), settings)
        if op is not None:
            chain.append(op)

    if _truthy(settings.get("enable_aspect_convert")):
        op = _aspect_op(source(), settings)
        if op is not None:
            chain.append(op)

    # Narration arrives on the `voice` port. It is NOT the background music,
    # and treating it as such mixed a voice-over in at the music volume.
    if _truthy(settings.get("enable_voice")):
        voice = _voice_in(upstream)
        if voice:
            # `auto_voice_speed` means "make the narration fit the clip" and
            # is therefore a different pass, not a number: the ratio is not
            # knowable until both durations are measured. A fixed
            # `voice_speed` is the manual alternative, and asking for both
            # would have the second undo the first.
            # The clip as it stands, named explicitly. Once an AUDIO pass is
            # appended, `source()` means that audio — and the mix needs the
            # video.
            video_in = source()
            video_step = len(chain)

            if _truthy(settings.get("auto_voice_speed")):
                chain.append(
                    {"op": "fit_narration", "video": video_in, "audio": voice}
                )
                # `fit_narration` returns the clip WITH the narration in it.
                # Mixing the same narration under its output laid the voice
                # over itself, a fraction of a second apart.
            else:
                speed = _voice_speed(settings)
                if speed is not None:
                    chain.append(
                        {"op": "voice_speed", "audio": voice, "speed": speed}
                    )
                    voice = PREVIOUS
                    video_in = (
                        video if video_step == 0 else step_output(video_step)
                    )
                chain.append(_bgm_op(video_in, voice, settings, voice=True))

    if _truthy(settings.get("enable_bgm")):
        track = _bgm_track(settings)
        if track:
            chain.append(_bgm_op(source(), track, settings, voice=False))

    title = _text_on(upstream, ("title",))
    # The styled captions and the plain `title` pass draw the same thing, so
    # only one of them runs. When the workflow supplies `aspect_texts` it has
    # said how the title should look — font, gradient, position — and falling
    # through to the plain drawtext as well would stack a second unstyled
    # copy on top of it.
    text_art = _text_art_op(source(), settings, title)
    if text_art is not None:
        chain.append(text_art)
    elif title:
        chain.append({"op": "title", "video": source(), "text": title})

    if _truthy(settings.get("enable_sub")):
        # `source()` is evaluated once and reused by both subtitle passes:
        # they must read the same clip, and calling it again after the
        # transcribe was appended would return the SRT.
        chain.extend(
            _subtitle_ops(
                video if not chain else step_output(len(chain)),
                settings,
                base_index=len(chain),
            )
        )

    # The cover frame goes on AFTER the captions. Burned earlier it would
    # carry a subtitle meant for the clip, and the cover is the one frame
    # people screenshot.
    if _truthy(settings.get("enable_thumbnail")):
        cover = _media_on(upstream, THUMBNAIL_PORTS)
        if cover:
            op: dict = {
                "op": "thumbnail_insert",
                "video": source(),
                "image": cover,
                "position": str(settings.get("thumbnail_position") or "end"),
            }
            hold = _number(settings.get("thumbnail_duration"))
            if hold is not None and hold > 0:
                op["seconds"] = hold
            chain.append(op)

    # `upscale_2k_4k` is this canvas's switch; `scale` is the packaged
    # tool's, and it has no separate enable flag — asking for x2 IS asking
    # for the pass. `upscale_factor` returns None for x1, so the twenty-six
    # shipped nodes that say `🔍 x1` add no pass and lose no quality to a
    # re-encode that would change nothing.
    from flowboard.services import node_settings as _ns

    if _truthy(settings.get("upscale_2k_4k")) or _ns.upscale_factor(settings):
        chain.append(_upscale_op(source(), settings))

    return chain
