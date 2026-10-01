"""What running a board will cost, before it runs.

An imported workflow is fifteen nodes that translate into dozens of billable
calls. Clicking Run without seeing that number is how someone spends a day's
credits on a board they meant to inspect.

What this endpoint promises is deliberately narrow, because the evidence is:

  * **Job counts are exact for a board run.** They come from the graph —
    every node `run_pipeline` will dispatch, and how many variants it asks
    for — so the headline number is the one the run produces, not a superset.
    This is the number that actually protects the user, and it is derived,
    not guessed.
  * **Prices are quoted only where a table exists.** OMNI Flash bills by
    length and the SDK carries that table. Veo and image lanes have no price
    table anywhere in this build, and none in the packaged tool either — it
    reads the balance from `/v1/credits` and never estimates. Inventing
    numbers here would put a confident total on a screen that the account
    would then not match.
  * **The balance is live**, so "34 billable jobs against 1040 credits" is
    something you can act on without a per-job price.

Local post-production steps are free and counted separately: ffmpeg runs on
this machine.

Transcription is the exception, and gets a count of its own. It is not a Flow
credit and it is not free — it spends the user's own speech-to-text quota or
dollars, one call per clip — and six of the nine shipped workflows turn
subtitles on, so a board can carry several of them without anyone having
asked.

Images drawn by OpenAI get the same separate treatment, and are the one place
a real price appears: unlike Veo, OpenAI publishes a per-image table, so that
line quotes dollars. It stays out of the credit total because it is billed to
a different account — a single number covering both would match neither.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, Edge, Node
from flowboard.services import flow_sdk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/boards", tags=["estimate"])

#: Node types that reach Google and are charged during a BOARD RUN.
#:
#: Deliberately the same set `run_pipeline` dispatches, not everything that
#: can cost money in general: the executor skips `character` and `Storyboard`
#: nodes, so counting them here quoted calls the run would never make. The
#: number someone is asked to approve has to be the number that happens.
#: (Running one of those nodes on its own from the canvas does dispatch; that
#: path has its own confirmation.)
BILLABLE_TYPES = {"image", "video"}

#: Node types that run ffmpeg here. Free, but worth counting: a board that is
#: mostly local work should not read as expensive.
LOCAL_TYPES = {
    "analyze_video",
    "merge_video",
    "edit_video",
    "extract_last_frame",
    "add_bgm",
    "create_voice",
    "align_video_voice",
    "sync_image_voice",
    # Runs a bundled executable rather than ffmpeg, but the same way: on
    # this machine, at no credit cost.
    "remove_watermark",
    # Costs no Flow credits, but one vision call per clip — counted below
    # the same way transcription is.
    "review_video",
}

#: Types that produce nothing on their own.
INERT_TYPES = {
    "prompt",
    "note",
    "visual_asset",
    # A board run skips these — `run_pipeline` dispatches image and video
    # only. They can cost money when run individually from the canvas, which
    # is a different action with its own confirmation.
    "character",
    "Storyboard",
}


class LineItem(BaseModel):
    nodeId: int
    shortId: str
    type: str
    title: str
    #: How many separate dispatches this node makes.
    jobs: int
    #: Credits, when this lane has a published price. None means unknown —
    #: NOT free. The UI must render the difference.
    credits: Optional[int] = None
    #: Why the price is unknown, when it is.
    note: Optional[str] = None


class BoardEstimate(BaseModel):
    boardId: int
    #: Generation nodes that will NOT run yet because they have no prompt —
    #: several shipped templates ship with the prompt boxes blank on purpose.
    #: Counted apart so the headline number stays the number that happens,
    #: while still telling the user why the board looks quiet.
    notReadyJobs: int
    #: Calls that reach Google and are charged.
    billableJobs: int
    #: Steps that run ffmpeg on this machine, at no cost.
    localJobs: int
    #: Transcription calls. Not Flow credits and not free either — each one
    #: spends the user's own Gemini quota, so it is counted apart from both.
    transcribeJobs: int
    #: Review calls, on the same footing as transcription: the user's own AI
    #: quota, never Flow credits. Counted apart because a board that reviews
    #: every clip doubles its AI spend, and that belongs in front of someone
    #: before the run rather than after it.
    #: Text and vision calls the run makes that are neither a review nor a
    #: transcription: a `gemini_prompt` node composing its three texts, an
    #: `analyze_video` node reading a clip. They were invisible because neither
    #: node ran at all — now they do, and an AI call nobody was told about is
    #: the same class of surprise as an uncounted credit.
    llmJobs: int = 0
    reviewJobs: int = 0
    #: Most vision calls the board could make, when the review loop is on and
    #: every clip needs every round. `reviewJobs` is the floor — the loop stops
    #: as soon as a clip clears the threshold — so one number alone would
    #: either under-state the spend or over-state it, and the dialog needs the
    #: range to be honest about either.
    reviewJobsMax: int = 0
    #: Which speech-to-text source would answer today, or "" when none is
    #: configured. A second counter would be wrong here — a transcription is
    #: one job whichever source serves it — but WHICH source is exactly what
    #: the number does not say, and the three differ: Gemini spends the
    #: user's own quota, whisper-1 costs about $0.006 a minute of audio, and
    #: local faster-whisper costs nothing but CPU.
    transcribeSource: str = ""
    #: Images drawn by OpenAI instead of Flow. Kept out of `billableJobs`
    #: because that number means Flow credits, and these are dollars on a
    #: different account entirely — adding them together would produce a
    #: figure that matches neither balance.
    openaiImageJobs: int = 0
    #: What those images will cost and how well that price is known. One
    #: prepared sentence rather than raw numbers, because the useful part is
    #: whether the figure is OpenAI's published table or a third-party
    #: estimate, and a bare float cannot say which.
    openaiImageNote: str = ""
    #: Narration sent to OpenAI instead of Gemini, counted in CHARACTERS
    #: because that is how `/v1/audio/speech` bills. A job count would be the
    #: wrong unit entirely: one narration can be a caption or a ten-minute
    #: story, and those differ by three orders of magnitude on the invoice.
    openaiTtsChars: int = 0
    #: How many `create_voice` nodes will use OpenAI, and how many of those
    #: have a script that only arrives at run time. The second number is the
    #: honest part: a node fed by a wire has no measurable length yet, and
    #: reporting its characters as 0 would read as free.
    openaiTtsNodes: int = 0
    openaiTtsUnknownNodes: int = 0
    openaiTtsNote: str = ""
    #: The part of the bill that has a published price.
    knownCredits: int
    #: How many billable jobs have no published price.
    unpricedJobs: int
    #: Live balance from /v1/credits, or None when the extension has not
    #: reported one yet.
    creditsAvailable: Optional[int]
    #: How many seconds ago that balance was reported. A cached number shown as
    #: current is the same failure as a guessed price, so the age travels with
    #: it and the dialog says "989, đọc N phút trước" rather than just "989".
    creditsAgeS: Optional[float] = None
    items: list[LineItem]


def _split_local_ops(node: Node) -> tuple[int, int, int]:
    """(ffmpeg passes, transcription calls, review calls) this node enables.

    Asked of the planner rather than assumed: a fully-configured
    `edit_video` is several passes, and reporting "1 local step" for it
    understates both the wait and the AI spend.

    `assume_ready` is set because an estimate runs BEFORE anything has
    produced media; without it every node would answer "not ready yet".
    """
    from flowboard.services import postprod_plan

    try:
        ops = postprod_plan.ops_for(node, [], assume_ready=True)
    except Exception:  # pragma: no cover — an estimate must never 500
        logger.warning("estimate: could not plan node %s", node.id, exc_info=True)
        return 1, 0, 0
    transcribes = sum(1 for op in ops if op.get("op") == "transcribe")
    reviews = sum(1 for op in ops if op.get("op") == "review")
    local = len(ops) - transcribes - reviews
    # An `edit_video` whose only enabled pass is transcription still costs a
    # step of wall-clock, and a node that plans nothing at all still shows a
    # row; the floor keeps both from reading as zero work.
    return max(0 if (transcribes or reviews) else 1, local), transcribes, reviews


def _openai_tts_load(node: Node, upstream: list) -> tuple[int, int, int]:
    """(nodes, measurable characters, nodes whose script is not here yet).

    Asked of the planner, like everything else in this module, so "which engine
    narrates" is answered by the same code that will answer it at run time
    rather than by re-reading the settings a second way.

    The node's REAL upstream is passed in, unlike `_split_local_ops` which only
    needs the op shape: here the script's length is the answer, and the script
    usually comes from a wired prompt node. Passing an empty list would make
    every node read as unmeasurable.
    """
    from flowboard.services import postprod_plan

    try:
        ops = postprod_plan.ops_for(node, upstream, assume_ready=True)
    except Exception:  # pragma: no cover — an estimate must never 500
        return 0, 0, 0

    nodes = chars = unknown = 0
    for op in ops:
        if op.get("op") != "narrate" or op.get("engine") != "openai":
            continue
        nodes += 1
        text = op.get("text")
        # `assume_ready` substitutes a placeholder for a script that will
        # arrive over a wire. Counting its length would be counting the
        # placeholder.
        if not isinstance(text, str) or postprod_plan.PENDING in text:
            unknown += 1
        else:
            chars += len(text.strip())
    return nodes, chars, unknown


def _openai_tts_note(nodes: int, chars: int, unknown: int) -> str:
    """One prepared sentence, or none when OpenAI narrates nothing here."""
    if nodes <= 0:
        return ""
    from flowboard.services import openai_tts

    parts = [f"{nodes} node đọc bằng OpenAI"]
    if chars:
        usd = chars / 1_000_000 * openai_tts.PRICE_PER_MILLION_CHARS_USD
        parts.append(
            f"{chars:,} ký tự đã biết ≈ ${usd:.4f} "
            f"(giá {openai_tts.resolve_model()} công bố "
            f"${openai_tts.PRICE_PER_MILLION_CHARS_USD}/1M ký tự, "
            f"tra ngày {openai_tts.PRICE_AS_OF})"
        )
    if unknown:
        parts.append(
            f"{unknown} node lấy lời thoại từ dây nên chưa đo được độ dài — "
            f"tính theo ký tự nên chi phí phụ thuộc kịch bản"
        )
    return " · ".join(parts) + ". Tiền OpenAI, không tốn credit Flow."


def _review_loop_calls(node: Node) -> tuple[int, int]:
    """(fewest, most) vision calls the review loop would make on this node.

    A range because it is one: the loop reviews the clip, and stops as soon as
    the score clears the threshold. So the floor is the single review that
    always happens when the loop is on, and the ceiling is `max_rounds`.

    Counted here because `review_loop`'s docstring says the estimate shows
    these before the run, and it did not — this module never consulted it, so
    up to four vision calls a node were invisible until the bill arrived.
    """
    if node.type != "video":
        return 0, 0
    from flowboard.services import node_settings, review_loop

    loop = review_loop.settings_from(node_settings.merged_settings(node))
    if not loop.enabled:
        return 0, 0
    return 1, max(1, loop.max_rounds)


def _transcribe_source() -> str:
    """The speech-to-text path that would answer, or "" when none would.

    Asked of `stt` rather than assumed to be Gemini: routing the transcribe
    op through the chain means the answer depends on what the user has
    configured, and an estimate that names the wrong source misstates the
    cost — Gemini quota, dollars per minute, and free are three different
    answers to "what will this run cost me".
    """
    from flowboard.services import stt

    available = stt.sources()
    for name in ("gemini", "whisper-1", "local"):
        if name in available:
            return name
    return ""


def _openai_image_note(jobs: int) -> str:
    """What `jobs` OpenAI images will cost, said as precisely as it is known.

    Two different degrees of knowledge get two different sentences. For the
    gpt-image-1 family OpenAI publishes a flat per-image table, so the total
    is a quote. For gpt-image-2 only token rates are published and the
    per-image figure comes from third-party arithmetic, so it is labelled as
    an approximation rather than dressed up as the same kind of number.
    """
    from flowboard.services import openai_images

    model = openai_images.model_name()
    price = openai_images.price_range()
    if price is None:
        return (
            f"{jobs} ảnh dựng bằng OpenAI ({model}) — tốn tiền tài khoản OpenAI "
            "của bạn, không tốn credit Flow. Chưa có bảng giá cho model này."
        )
    low, high, official = price
    span = f"${low:.3f}" if low == high else f"${low:.3f}–${high:.3f}"
    total = f"${low * jobs:.2f}" if low == high else f"${low * jobs:.2f}–${high * jobs:.2f}"
    source = (
        "giá công bố của OpenAI (tra ngày 03/09/2026)"
        if official
        else "ước tính từ nguồn thứ ba — OpenAI chưa công bố bảng giá phẳng cho model này"
    )
    return (
        f"{jobs} ảnh dựng bằng OpenAI ({model}, {openai_images.quality()}) — "
        f"khoảng {span}/ảnh, tổng ~{total}. Tiền tài khoản OpenAI, không tốn "
        f"credit Flow. Nguồn: {source}."
    )


def _image_engine_of(node: Node) -> Optional[str]:
    """Which service would draw this node's image, if it says."""
    from flowboard.services import node_settings

    return node_settings.image_engine(node_settings.merged_settings(node))


def _local_job_count(node: Node) -> int:
    """How many ffmpeg passes this post-production node will run.

    Asked of the planner rather than assumed: a fully-configured `edit_video`
    is a chain of several passes, and reporting "1 local step" understates
    how long the board will take.

    `assume_ready` is set because an estimate runs BEFORE anything has
    produced media: without it every node would answer "not ready yet" and a
    multi-pass edit would be reported as one step.
    """
    from flowboard.services import postprod_plan

    try:
        return max(1, len(postprod_plan.ops_for(node, [], assume_ready=True)))
    except Exception:  # pragma: no cover — an estimate must never 500
        logger.warning("estimate: could not plan node %s", node.id, exc_info=True)
        return 1


#: Node types whose dispatch always produces one output per variant.
#:
#: Images unconditionally. Video is conditional and therefore not here: a clip
#: with a start frame is image-to-video, which fans out over `start_media_ids`
#: (and a board run resolves exactly one), while a bare clip is text-to-video —
#: and `_handle_gen_video_text` DOES read `variant_count`, submitting one call
#: each. The comment here used to claim no video handler read it at all, and the
#: quote was built on that: 1 job for a node that dispatched 4.
_FANS_OUT_PER_VARIANT = frozenset({"image"})


def _variant_count(data: dict) -> int:
    """How many outputs this node asks for. One unless it says otherwise.

    Clamped through the worker's own helper, because the ceiling is a property
    of the dispatch: two ceilings meant the estimate promised sixteen while
    `gen_image` drew fifty.
    """
    from flowboard.worker.processor import clamp_variant_count

    return clamp_variant_count(data.get("variantCount"))


#: How long a Veo image-to-video clip is, regardless of the duration control.
#: The length is baked into the key — there is no 4s or 6s i2v key on this
#: transport — so a node set to anything else renders 8 seconds anyway.
_VEO_I2V_SECONDS = 8


#: Lane → the label the user sees elsewhere in the app. Named here so the
#: estimate says which lane it could not price, rather than the same
#: sentence for every node on the board.
_LANE_LABELS = {
    "lite": "Veo 3.1 - Lite",
    "fast": "Veo 3.1 - Fast",
    "quality": "Veo 3.1 - Quality",
    "lite_relaxed": "Veo 3.1 - Lite [Lower Priority]",
    "fast_relaxed": "Veo 3.1 - Fast [Lower Priority]",
    "omni": "OMNI Flash",
}


def _raw_lane_of(node: Node) -> Optional[str]:
    """The lane as the node actually carries it — None when nothing chose one.

    `_lane_of` coerces that None to `DEFAULT_VIDEO_QUALITY`, which is right for
    the families that have a default and wrong for the character port, where an
    absent lane means Omni. Keeping the two apart makes the difference visible
    rather than hiding it inside one function serving two rules — the hiding is
    what quoted a character board at 0 credits and dispatched it at 75.
    """
    from flowboard.services import node_settings

    if (node.data or {}).get("videoModel") == "omni_flash":
        return "omni"
    return node_settings.video_quality(node_settings.merged_settings(node))


def _lane_of(node: Node) -> str:
    """The lane this video node will dispatch on, never None.

    An unset lane resolves the same way the SDK resolves it, so the quote and
    the dispatch cannot disagree about which lane ran. They did: the estimate
    read "no lane" and priced it unknown while the SDK picked its default.
    """
    return _raw_lane_of(node) or flow_sdk.DEFAULT_VIDEO_QUALITY


def _character_port_plan(node: Node) -> dict:
    """The key the character port will dispatch on, via the resolver the run uses."""
    from flowboard.services import node_settings

    settings = node_settings.merged_settings(node)
    return flow_sdk.resolve_character_port_plan(
        _raw_lane_of(node),
        node_settings.duration_s(settings),
        node_settings.resolution(settings),
    )


def _lane_refusal(node: Node, *, has_start_frame: bool, has_characters: bool) -> Optional[str]:
    """Why this node's lane cannot be dispatched at all, or None.

    Mirrors the family the executor picks -- characters, else a start frame,
    else text-to-video -- because the refusal depends on it. A Veo lane is fine
    for plain image-to-video and for text-to-video (measured 19/09/2026: the RPC
    parses Veo keys), and refused on the character port, where only Omni has a
    captured payload. Asking the same question a different way here is how the
    dialog came to quote credits for runs that were refused for free.
    """
    from flowboard.services import node_settings

    lane = _lane_of(node)
    if has_characters:
        # Asked first and asked through the resolver the dispatch uses, because
        # this family has its own rule for an unset lane. Deriving it a second
        # time here is what made the quote and the run disagree about the family
        # itself, not merely about the price.
        plan = _character_port_plan(node)
        if not plan["error"]:
            return None
        if plan["family"] == "veo_r2v":
            return (
                f"Cổng nhân vật: đường mới chỉ nhận {sorted(flow_sdk.VEO_R2V_LANES)} "
                f"của Veo (hoặc OMNI) — làn {_LANE_LABELS.get(lane, lane)} sẽ bị TỪ "
                "CHỐI, không tốn credit."
            )
        return (
            "Cổng nhân vật chạy trên OMNI nhưng chưa chọn được thời lượng hợp lệ "
            "(4/6/8/10 giây) — sẽ bị TỪ CHỐI, không tốn credit."
        )
    if lane in flow_sdk.REFUSED_VIDEO_LANES:
        return (
            f"{_LANE_LABELS.get(lane, lane)} không có trên đường mới của Flow "
            f"({flow_sdk.REFUSED_VIDEO_LANES[lane]}) — sẽ bị TỪ CHỐI, không tốn credit."
        )
    if lane == "omni":
        return None
    if not has_start_frame:
        # Text-to-video. Veo lanes dispatch here since `YhhmEf` was measured
        # parsing a Veo key, so only a lane with no key at all is refused.
        if lane not in flow_sdk.VEO_T2V_LANES:
            return (
                f"Text-to-video không có làn {_LANE_LABELS.get(lane, lane)} — "
                f"chỉ có {sorted(flow_sdk.VEO_T2V_LANES)} hoặc OMNI. "
                "Sẽ bị TỪ CHỐI, không tốn credit."
            )
        duration = node_settings.duration_s(node_settings.merged_settings(node))
        if duration is not None and duration not in flow_sdk.VEO_T2V_DURATIONS:
            # The duration is part of the key, so the nearest one bills a
            # different clip length than the board asked for.
            return (
                f"Làn {_LANE_LABELS.get(lane, lane)} chỉ có "
                f"{list(flow_sdk.VEO_T2V_DURATIONS)} giây cho text-to-video — "
                f"{duration}s sẽ bị TỪ CHỐI, không tốn credit."
            )
    return None


def _price_of(
    node: Node,
    *,
    has_characters: bool,
    has_start_frame: bool,
) -> tuple[Optional[int], Optional[str]]:
    """Credits for one dispatch of this node, and why not when unknown.

    Takes the family from the caller, which can see the wires. Guessing it from
    the lane alone meant one table answered for all four families: a
    text-to-video node on `lite_relaxed` was quoted 0 credits from an
    image-to-video key, and a character node was quoted from that same key while
    the dispatch sent Omni.

    Both flags are required rather than defaulted. A default would silently price
    one family's node from another family's table, which is the defect this
    parameter exists to remove — and the caller always knows, because it is the
    one holding the wires.

    Reads settings through `node_settings`, the same module the run uses.
    It used to read `data["videoQuality"]` directly, which imported
    workflows never carry — theirs live under `data["sourceSettings"]` with
    the packaged tool's own emoji labels — so every imported node priced as
    "unknown" and the confirmation dialog could never show a number.

    Zero is quoted for the free lane, and only from the same table the run
    reads (`flow_sdk.ZERO_CREDIT_MODEL_KEYS`, via the lane's model key). It was
    "unknown" before for a good reason that has since expired: the 0-credit
    lanes used to depend on the account tier AND on the dispatch family, and
    quoting 0 from a rule with unverified exceptions would under-quote. The
    batch path has one 0-credit key and no tier axis, so the two answers cannot
    drift apart. If Google refuses that key on this account the dispatch errors
    -- which still costs nothing, so the quote holds either way.

    No price is invented for the paid Veo lanes: nobody has published one and
    this build has not measured one, so they stay unpriced and say which lane it
    was. (The older reason given here — that the balance cannot be read — stopped
    being true at P16.1. The balance IS readable now, which is what makes the
    measurement possible: a real run reads it before and after and reports the
    delta as an observation. Until a lane has been through that, unpriced is the
    honest answer, and it is the safe direction.)
    """
    from flowboard.services import node_settings

    if node.type != "video":
        return None, "Chưa có bảng giá công bố cho lane này."

    lane = _lane_of(node)
    if has_characters:
        # Priced through the family that will actually run. This branch used to
        # be absent, and the fallthrough read the IMAGE-TO-VIDEO table for every
        # family: a character node on an unset lane was priced from
        # `veo_3_1_i2v_lite` while the dispatch sent `abra_r2v_<N>s`.
        plan = _character_port_plan(node)
        if plan["error"]:
            return 0, None
        if plan["family"] == "omni":
            price = flow_sdk.OMNI_FLASH_CREDIT_COST.get(
                node_settings.duration_s(node_settings.merged_settings(node))
            )
            if price is None:
                return None, "OMNI: chưa chọn thời lượng nên chưa biết giá."
            return price, None
        # Veo's one reference key: UNPRICED, not free.
        #
        # It was quoted at 0 because `_low_priority` is the free-queue marker
        # everywhere else on this transport. That is an inference, not a
        # measurement — the key is not in `ZERO_CREDIT_MODEL_KEYS`, nobody has
        # published a price, and the 20/09 probe could only establish that
        # `MZZa6b` understands the name and that THIS plan does not hold it, so
        # this account cannot run it to find out. Quoting 0 from a suffix is the
        # same class of guess as the 360p price the registry refuses to invent,
        # and it is the dangerous direction: a free label is what lets the review
        # loop re-run a clip unasked.
        return None, (
            f"{_LANE_LABELS.get(lane, lane)} (Veo reference) — hậu tố "
            "`_low_priority` thường là hàng đợi miễn phí, nhưng giá key này "
            "CHƯA ĐO. Gói hiện tại không có nó nên không đo được ở đây."
        )
    if lane == "omni":
        duration = node_settings.duration_s(node_settings.merged_settings(node))
        price = flow_sdk.OMNI_FLASH_CREDIT_COST.get(duration) if duration else None
        if price is None:
            return None, "OMNI: chưa chọn thời lượng nên chưa biết giá."
        return price, None
    if not has_start_frame:
        # Text-to-video has its own key table. Reading the i2v one here quoted
        # `lite_relaxed` at 0 credits from `veo_3_1_i2v_lite_low_priority` while
        # the dispatch sent `veo_3_1_t2v_lite_*_low_priority`, which
        # `is_zero_credit_model` does not call free -- so the dialog said 0 and
        # the retry policy said paid, the two answers this module claims cannot
        # drift apart.
        duration = node_settings.duration_s(node_settings.merged_settings(node))
        plan = flow_sdk.resolve_t2v_plan(lane, duration or 0)
        model_key = plan["model_key"]
        if model_key is None:
            return None, f"{_LANE_LABELS.get(lane, lane)} — không có trên đường mới."
        if flow_sdk.is_zero_credit_model(model_key):
            return 0, "Làn 0 credit (hàng đợi ưu tiên thấp)."
        return None, f"{_LANE_LABELS.get(lane, lane)} — chưa có bảng giá chốt."
    model_key = flow_sdk.BATCH_VIDEO_LANES.get(lane)
    if model_key is None:
        return None, f"{_LANE_LABELS.get(lane, lane)} — không có trên đường mới."
    # A Veo image-to-video key carries its own 8 seconds; the duration control
    # drives the OMNI lane and is silently dropped here. Said out loud rather than
    # refused: the lane runs, and refusing a clip that works would be worse. What
    # it must not do is show a 10s node and a price and mention neither.
    duration = node_settings.duration_s(node_settings.merged_settings(node))
    length_note = (
        f" Làn Veo luôn render 8 giây — {duration}s đã chọn không có tác dụng."
        if duration is not None and duration != _VEO_I2V_SECONDS
        else ""
    )
    if flow_sdk.is_zero_credit_model(model_key):
        return 0, "Làn 0 credit (hàng đợi ưu tiên thấp)." + length_note
    return (
        None,
        f"{_LANE_LABELS.get(lane, lane)} — chưa có bảng giá chốt." + length_note,
    )


#: Node types whose media the executor passes to `gen_image` as a reference.
_REFERENCE_SOURCE_TYPES = ("character", "image", "visual_asset")


def _has_reference_wire(node, wires) -> bool:
    """Whether any upstream node would hand this image a reference photo.

    Mirrors the executor's own collection at the `gen_image` branch. It
    matters only for the OpenAI engine, whose generations endpoint draws from
    text alone — so honouring the engine means dropping the photos, and the
    run refuses rather than buy a confident picture of the wrong person.
    """
    for wire in wires:
        upstream = wire.node
        if getattr(upstream, "type", None) not in _REFERENCE_SOURCE_TYPES:
            continue
        media = (upstream.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip():
            return True
    return False


def _start_frame_present(node, wires) -> bool:
    """Whether a start frame is already resolvable for this node.

    `character_ports.validate` needs to know, because a start frame plus
    character entities is the one combination the OMNI dispatch has no shape
    for. Asked through the executor's own resolver so the two cannot disagree.
    """
    from flowboard.services.pipeline_executor import _start_frame_media_id

    upstream_nodes = [w.node for w in wires]
    return bool(_start_frame_media_id(wires, upstream_nodes))


@router.get("/{board_id}/estimate", response_model=BoardEstimate)
def estimate_board(
    board_id: int,
    node_ids: Optional[list[int]] = Query(default=None),
) -> BoardEstimate:
    """Count what running this board would dispatch. Reads only.

    `node_ids` prices a scoped run — the same subset `ensure_board_plan` writes
    to `_run_only_node_ids`. The graph is still read WHOLE: `upstream_of` has to
    see the producers outside the subset or a scoped clip would be read as
    text-to-video and quoted under the wrong length rule. Only the pricing loop
    is narrowed, which is exactly where the executor narrows dispatch.

    Quoting the whole board for a subset run would be the more expensive
    mistake in the other direction: a user shown twelve jobs for a three-job
    run cancels a run they could afford.
    """
    from flowboard.services.pipeline_executor import (
        _prompt_for,
        _upstream_with_ports,
        has_start_frame_wire,
        missing_upload_ports,
        start_frame_unavailable,
    )

    with get_session() as s:
        if s.get(Board, board_id) is None:
            raise HTTPException(404, f"no board {board_id}")
        nodes = list(s.exec(select(Node).where(Node.board_id == board_id)).all())
        edges = list(s.exec(select(Edge).where(Edge.board_id == board_id)).all())

    # Built with the executor's own helper so the estimate resolves prompts
    # exactly the way the run will. A second implementation here would drift,
    # and the drift would show up as a cost quote that does not match.
    by_id = {n.id: n for n in nodes}
    upstream_of = {
        n.id: _upstream_with_ports(n.id, edges, by_id) for n in nodes if n.id
    }

    #: Mirrors `pipeline_executor`'s `run_only` gate. Empty means the whole
    #: board; ids not on the board are dropped rather than rejected, because a
    #: quote is read-only and a 400 here would block the dialog instead of the
    #: run. `ensure_board_plan` is the gate that refuses them.
    scope = {i for i in (node_ids or []) if i in by_id}

    #: Read once per quote, not per line: a balance that moved mid-loop would
    #: make two rows disagree about the same number.
    from flowboard.services import flow_credits
    _balance, _balance_age = flow_credits.last_known()

    items: list[LineItem] = []
    billable = local = known = unpriced = not_ready = transcribes = reviews = 0
    openai_images_jobs = 0
    openai_tts_nodes = 0
    openai_tts_chars = 0
    openai_tts_unknown = 0
    #: The loop can stop early, so its cost is a range: `reviews` is the floor
    #: and this is how much more it could be.
    review_extra = 0

    llm_jobs = 0

    for node in nodes:
        if scope and node.id not in scope:
            continue
        if node.type == "prompt":
            # A prompt node the user typed is inert. One that asks a model at
            # run time is a call on their AI quota, and the run now makes it.
            from flowboard.services.pipeline_executor import _is_llm_prompt_node

            if _is_llm_prompt_node(node):
                llm_jobs += 1
                items.append(
                    LineItem(
                        nodeId=node.id or 0,
                        shortId=node.short_id,
                        type=node.type,
                        title=str((node.data or {}).get("title") or node.type),
                        jobs=1,
                        credits=0,
                        note=(
                            "1 lần gọi AI viết prompt (tốn quota AI của bạn, "
                            "không tốn credit Flow)."
                        ),
                    )
                )
            continue
        if node.type in INERT_TYPES:
            continue
        data = node.data or {}
        jobs = (
            _variant_count(data) if node.type in _FANS_OUT_PER_VARIANT else 1
        )
        if node.type == "video" and not has_start_frame_wire(
            upstream_of.get(node.id, ())
        ):
            # Text-to-video, the one video family that submits once per variant.
            jobs = _variant_count(data)
        price, note = (None, None)

        if node.type in BILLABLE_TYPES:
            # A generation node with no prompt is skipped by the executor, so
            # counting it would quote a call the run never makes. The prompt
            # may live on the node OR arrive over a wire — the same resolution
            # the run itself does.
            if not _prompt_for(node, upstream_of.get(node.id, ())):
                not_ready += jobs
                items.append(
                    LineItem(
                        nodeId=node.id or 0,
                        shortId=node.short_id,
                        type=node.type,
                        title=str(data.get("title") or node.type),
                        jobs=0,
                        credits=0,
                        note="Chưa có prompt — sẽ bỏ qua. Điền prompt để chạy.",
                    )
                )
                continue
            # An upload socket left empty. Quoting this as billable was the
            # dangerous half of the old behaviour: the board read "sẵn sàng"
            # and the run then spent credits on a prompt naming photos it
            # had never been given.
            wires = upstream_of.get(node.id, ())
            empty_ports = missing_upload_ports(node, wires)
            blocked_note: Optional[str] = None
            if empty_ports:
                blocked_note = (
                    "Thiếu ảnh đầu vào ở cổng "
                    f"{', '.join(sorted(empty_ports))} — sẽ bỏ qua. "
                    "Chọn ảnh cho node Upload Media phía trên."
                )
            elif start_frame_unavailable(node, wires):
                blocked_note = (
                    "Chưa có ảnh khung hình đầu — sẽ bỏ qua. Chọn ảnh cho "
                    "node Upload Media, hoặc nối từ một node sinh ảnh."
                )
            elif node.type == "video":
                # The character-socket refusals, asked here with the executor's
                # own validator rather than a copy of its rules. This block was
                # missing entirely, so a board whose video node mixes a start
                # frame with character wires — which the run refuses for free —
                # was quoted as billable. A number that counts calls the run
                # will not make is worse than no number: it is the one the
                # confirmation dialog shows.
                from flowboard.services import character_ports, node_settings

                lane = node_settings.video_quality(
                    node_settings.merged_settings(node)
                )
                problem = character_ports.validate(
                    wires,
                    lane=lane,
                    has_start_frame=bool(_start_frame_present(node, wires)),
                )
                if problem:
                    blocked_note = character_ports.explain(problem) + " — sẽ bỏ qua."
                elif not character_ports.collect(wires) and character_ports.wired_ports(
                    wires
                ):
                    blocked_note = (
                        "Cổng nhân vật đã nối nhưng chưa có ảnh nào — sẽ bỏ "
                        "qua. Chạy node ảnh phía trên trước."
                    )
                else:
                    # A lane this transport cannot dispatch. Asked last, so a
                    # node with a real wiring problem still reports that first
                    # -- fixing the lane would not make it run.
                    blocked_note = _lane_refusal(
                        node,
                        # Is a start frame WIRED -- not "resolved yet". A
                        # storyboard clip whose image node has not run is still
                        # an image-to-video, and reading it as text-to-video
                        # refused every clip on every fresh board.
                        has_start_frame=has_start_frame_wire(wires),
                        has_characters=bool(character_ports.collect(wires)),
                    )
            if blocked_note:
                not_ready += jobs
                items.append(
                    LineItem(
                        nodeId=node.id or 0,
                        shortId=node.short_id,
                        type=node.type,
                        title=str(data.get("title") or node.type),
                        jobs=0,
                        credits=0,
                        note=blocked_note,
                    )
                )
                continue
            if node.type == "image" and _image_engine_of(node) == "openai":
                # The run refuses this node in two cases, and both were being
                # quoted as dollars the user was about to spend. The engine
                # being switched off is the commoner one: every board imported
                # while OpenAI is disabled read as "will cost $0.006 x N" and
                # then generated nothing at all.
                from flowboard.services import openai_images

                if not openai_images.available():
                    not_ready += jobs
                    items.append(
                        LineItem(
                            nodeId=node.id or 0,
                            shortId=node.short_id,
                            type=node.type,
                            title=str(data.get("title") or node.type),
                            jobs=0,
                            credits=0,
                            note=(
                                "Engine OpenAI đang tắt — sẽ bỏ qua. Bật "
                                "trong Cài đặt hoặc đổi engine về Flow."
                            ),
                        )
                    )
                    continue
                if _has_reference_wire(node, wires):
                    not_ready += jobs
                    items.append(
                        LineItem(
                            nodeId=node.id or 0,
                            shortId=node.short_id,
                            type=node.type,
                            title=str(data.get("title") or node.type),
                            jobs=0,
                            credits=0,
                            note=(
                                "Engine OpenAI chỉ vẽ từ chữ, node này lại có "
                                "ảnh tham chiếu — sẽ bỏ qua."
                            ),
                        )
                    )
                    continue
                # Counted, but on the OpenAI side of the ledger. Rolling it
                # into `billableJobs` would tell the user they need Flow
                # credits they do not need, and hide the dollars they do.
                openai_images_jobs += jobs
                items.append(
                    LineItem(
                        nodeId=node.id or 0,
                        shortId=node.short_id,
                        type=node.type,
                        title=str(data.get("title") or node.type),
                        jobs=jobs,
                        credits=0,
                        note="Dựng bằng OpenAI — không tốn credit Flow.",
                    )
                )
                continue
            billable += jobs
            # The review loop's vision calls, if this node switched it on. Not
            # Flow credits — the user's own AI provider quota — so they join
            # `reviewJobs` rather than `billableJobs`, the same ledger split the
            # post-production `review` op already uses.
            loop_min, loop_max = _review_loop_calls(node)
            reviews += loop_min
            # Only the rounds BEYOND the first, so the ceiling is the floor plus
            # this rather than a second full count.
            review_extra += max(0, loop_max - loop_min)
            from flowboard.services import character_ports

            price, note = _price_of(
                node,
                has_characters=bool(character_ports.collect(wires)),
                has_start_frame=has_start_frame_wire(wires),
            )
            if price is None:
                unpriced += jobs
            else:
                known += price * jobs
        elif node.type in LOCAL_TYPES:
            jobs, node_transcribes, node_reviews = _split_local_ops(node)
            local += jobs
            transcribes += node_transcribes
            reviews += node_reviews
            jobs += node_transcribes + node_reviews
            tts_nodes, tts_chars, tts_unknown = _openai_tts_load(
                node, upstream_of.get(node.id, [])
            )
            openai_tts_nodes += tts_nodes
            openai_tts_chars += tts_chars
            openai_tts_unknown += tts_unknown
            # Named per op, not flattened to "AI": transcription is gated on
            # a Gemini key and can only be Gemini, while a review goes to
            # whichever vision provider the registry has. Telling the user
            # "AI" for both would be vaguer than what is actually known.
            ai_notes = []
            if node.type == "analyze_video":
                # One vision call, with several frames attached. Counted apart
                # from `reviewJobs` because it is a different job: reading a
                # clip to rebuild it, not scoring one that was made.
                llm_jobs += 1
                ai_notes.append("1 lần đọc video bằng AI")
            if node_transcribes:
                ai_notes.append(f"{node_transcribes} lần phiên âm bằng Gemini")
            if node_reviews:
                ai_notes.append(f"{node_reviews} lần chấm clip bằng AI")
            if ai_notes:
                price, note = None, (
                    " + ".join(ai_notes)
                    + " (tốn quota AI của bạn, không tốn credit Flow)."
                )
            else:
                price, note = 0, "Chạy ffmpeg trên máy này — không tốn credit."
        else:
            # A type nobody has classified. Counted as billable, because
            # under-reporting a cost is the expensive direction to be wrong in.
            logger.warning("estimate: unclassified node type %r", node.type)
            billable += jobs
            unpriced += jobs
            note = "Loại node chưa phân loại — tính là có thể tốn credit."

        items.append(
            LineItem(
                nodeId=node.id or 0,
                shortId=node.short_id,
                type=node.type,
                title=str(data.get("title") or node.type),
                jobs=jobs,
                credits=None if price is None else price * jobs,
                note=note,
            )
        )

    return BoardEstimate(
        boardId=board_id,
        billableJobs=billable,
        notReadyJobs=not_ready,
        transcribeJobs=transcribes,
        transcribeSource=_transcribe_source() if transcribes else "",
        openaiImageJobs=openai_images_jobs,
        openaiTtsChars=openai_tts_chars,
        openaiTtsNodes=openai_tts_nodes,
        openaiTtsUnknownNodes=openai_tts_unknown,
        openaiTtsNote=_openai_tts_note(
            openai_tts_nodes, openai_tts_chars, openai_tts_unknown
        ),
        openaiImageNote=_openai_image_note(openai_images_jobs) if openai_images_jobs else "",
        llmJobs=llm_jobs,
        reviewJobs=reviews,
        reviewJobsMax=reviews + review_extra,
        localJobs=local,
        knownCredits=known,
        unpricedJobs=unpriced,
        # Readable again. `nzlxg` answers directly, and five other replies —
        # the poll among them — carry the balance for free, so this is usually
        # current without any extra call. Still None when nothing has reported
        # one yet, because None and 0 send a user in opposite directions.
        creditsAvailable=_balance,
        creditsAgeS=_balance_age,
        items=items,
    )
