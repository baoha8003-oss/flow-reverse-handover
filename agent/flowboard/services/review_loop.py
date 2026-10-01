"""Generate, score, revise the prompt, generate again.

The loop the packaged tool does not have. A clip comes back, the reviewer
scores it against the prompt that asked for it (`services.video_review`),
and if it falls short an LLM rewrites the prompt from the critique and the
node runs again.

**What stops it spending money.** Two independent limits, both of which have
to allow a retry:

1. ``max_rounds`` — a count, default 2, ceiling 4. Bounds the wall-clock and
   the AI spend even on a free lane.
2. **The lane must cost nothing.** Only ``lite_relaxed`` and ``fast_relaxed``
   are 0-credit (Ultra, low-priority queue). On any other lane the loop
   stops after the FIRST review and reports what it would have done, because
   spending the user's credits on a retry they did not individually approve
   is not this module's call to make. That was the user's explicit decision
   when this was planned, and it is enforced here rather than documented.

Reviewing itself always happens when asked — it is one vision call against
the user's own AI quota, the same footing as transcription, and
`routes.estimate` shows it before the run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

#: The lanes that cost nothing. Anything else spends Flow credits, and the
#: loop will not retry on it without being asked.
ZERO_CREDIT_LANES = frozenset({"lite_relaxed", "fast_relaxed"})

DEFAULT_THRESHOLD = 7.0
DEFAULT_MAX_ROUNDS = 2
MAX_ROUNDS_CEILING = 4

_REVISE_SYSTEM = """\
You rewrite prompts for an AI video generator. You are given the prompt that \
produced a clip and a reviewer's critique of what went wrong.

Rewrite the prompt so the same clip is attempted again WITHOUT those faults. \
Rules:
- Keep the subject, setting and intent unchanged. You are fixing execution, \
not redirecting the video.
- Address the critique concretely. "Character drift" means naming fixed \
features; "camera drift" means stating the camera is locked; "reverse \
motion" means describing one continuous action instead of several.
- Stay under 900 characters, one paragraph, no preamble.

Output ONLY the rewritten prompt."""


@dataclass
class Settings:
    enabled: bool = False
    threshold: float = DEFAULT_THRESHOLD
    max_rounds: int = DEFAULT_MAX_ROUNDS


@dataclass
class Round:
    """One pass of the loop, for the run log."""

    index: int
    score: float
    verdict: str
    accepted: bool
    retried: bool
    reason: str = ""
    fix_hint: str = ""


@dataclass
class Outcome:
    rounds: list[Round] = field(default_factory=list)
    #: The prompt to dispatch next, when a retry is warranted AND allowed.
    next_prompt: Optional[str] = None
    #: Why the loop stopped, in Vietnamese, for the node's own panel.
    note: str = ""

    @property
    def best(self) -> Optional[Round]:
        return max(self.rounds, key=lambda r: r.score) if self.rounds else None


def settings_from(node_settings: dict) -> Settings:
    """Read the loop's settings off a node.

    Off unless asked for: a loop that runs by default would spend a vision
    call per clip on every board that never opted in.
    """
    raw = node_settings.get("review_loop")
    enabled = bool(raw) and str(raw).strip().lower() not in ("false", "0", "no")
    threshold = node_settings.get("review_threshold")
    rounds = node_settings.get("review_max_rounds")
    return Settings(
        enabled=enabled,
        threshold=_number(threshold, DEFAULT_THRESHOLD, low=0.0, high=10.0),
        max_rounds=int(
            _number(rounds, DEFAULT_MAX_ROUNDS, low=1, high=MAX_ROUNDS_CEILING)
        ),
    )


def _number(value: object, fallback: float, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


def is_free_lane(quality: Optional[str]) -> bool:
    """Whether a retry on this lane costs the user nothing."""
    return str(quality or "").strip().lower() in ZERO_CREDIT_LANES


def is_free_dispatch(
    *, model_key: Optional[str], quality: Optional[str]
) -> bool:
    """Whether another round is free, judged by what actually dispatched.

    The lane a board asks for is not always the lane it gets: on Pro there is
    no 0-credit key, so `flow_sdk.resolve_video_plan` serves the cheapest
    PAID one. Reading the label alone, this loop concluded "free" and spent up
    to three more paid clips per node — the exact thing the 0-credit rule
    exists to prevent.

    So the key wins whenever the dispatch reported one. Falling back to the
    label is for the paths that do not report a key yet (post-production, and
    any dispatch that failed before it resolved one); an unknown key is never
    treated as free.
    """
    if model_key:
        from flowboard.services.flow_sdk import is_zero_credit_model

        return is_zero_credit_model(model_key)
    return is_free_lane(quality)


def decide(
    *,
    round_index: int,
    score: float,
    verdict: str,
    settings: Settings,
    quality: Optional[str],
    fix_hint: str = "",
    model_key: Optional[str] = None,
    has_critical: bool = False,
) -> Round:
    """Whether to retry after one review, and why not when not.

    Split out from the executor so the policy is testable without a board, a
    worker and a Flow dispatch — this is the function that decides whether
    money gets spent.

    A named CRITICAL fault outranks the score. `video_review.parse_review`
    already caps `character_consistency` at 3.0 when the reviewer finds one,
    but that cap alone tops out at 8.25 — above the 7.0 default — so a clip
    whose other five dimensions are strong still passes carrying a defect the
    reviewer named out loud. That is the common shape, not a corner: a
    beautiful clip whose face changes once is the failure the rubric puts
    first. Between a number the model chose and a fault the model described,
    the description is the reliable half.

    This makes the loop stricter than the threshold alone, which is why every
    branch below says so rather than reporting a bare score — the threshold is
    a number the user set, and a rule that quietly overrides it would read as
    the score being wrong.
    """
    over_threshold = score >= settings.threshold
    if over_threshold and not has_critical:
        return Round(
            index=round_index, score=score, verdict=verdict,
            accepted=True, retried=False,
            reason=f"Đạt ngưỡng {settings.threshold:g}.",
            fix_hint=fix_hint,
        )

    # Why we are not accepting, in one phrase the branches below quote. Kept in
    # one place because "Điểm 7.8 dưới ngưỡng 7.0" is a sentence that sends
    # someone hunting for a bug in the scoring.
    shortfall = (
        f"Đạt ngưỡng {settings.threshold:g} nhưng reviewer nêu lỗi CRITICAL"
        if over_threshold
        else f"Điểm {score:g} dưới ngưỡng {settings.threshold:g}"
    )

    if round_index >= settings.max_rounds:
        return Round(
            index=round_index, score=score, verdict=verdict,
            accepted=False, retried=False,
            reason=f"{shortfall}, và đã thử {round_index} lần — dừng theo giới hạn.",
            fix_hint=fix_hint,
        )
    if not is_free_dispatch(model_key=model_key, quality=quality):
        billed = quality or "mặc định"
        if model_key and not is_free_lane(quality):
            detail = f"lane “{billed}” tốn credit"
        elif model_key:
            # Asked for free, was served paid. Say so: otherwise the user
            # reads "lane lite_relaxed" and cannot tell why it stopped.
            detail = (
                f"lane “{billed}” không có ở gói này nên Flow đã chạy model "
                f"“{model_key}” có tính tiền"
            )
        else:
            detail = f"lane “{billed}” tốn credit"
        return Round(
            index=round_index, score=score, verdict=verdict,
            accepted=False, retried=False,
            reason=(
                f"{shortfall}, và {detail} — không tự tạo "
                "lại. Đổi sang lane 0 credit (lite/fast lower priority) "
                "hoặc bấm tạo lại thủ công."
            ),
            fix_hint=fix_hint,
        )
    return Round(
        index=round_index, score=score, verdict=verdict,
        accepted=False, retried=True,
        reason=f"{shortfall} — sửa prompt và tạo lại (lane 0 credit).",
        fix_hint=fix_hint,
    )


async def revise_prompt(prompt: str, review, *, timeout: float = 120.0) -> Optional[str]:
    """Rewrite a prompt from the reviewer's critique.

    None when no provider answers or the answer is unusable, which the
    caller must treat as "stop", not as "retry with the same prompt" — the
    same prompt would produce the same clip and spend the round for nothing.
    """
    from flowboard.services.llm import run_llm_chain
    from flowboard.services.llm.base import LLMError

    faults = "\n".join(
        f"- [{i.severity}] {i.time_range}: {i.description}" for i in review.issues
    ) or "- Không nêu lỗi cụ thể; điểm thấp ở tổng thể."
    weakest = min(review.dimensions, key=lambda k: review.dimensions[k])
    ask = (
        f"PROMPT CŨ:\n{prompt}\n\n"
        f"ĐIỂM: {review.score} ({review.verdict}). "
        f"Yếu nhất: {weakest} = {review.dimensions[weakest]}.\n"
        f"LỖI:\n{faults}\n"
        + (f"GỢI Ý: {review.fix_hint}\n" if review.fix_hint else "")
    )
    # The rewrite has to obey the same rules the original prompt did — the
    # celebrity-name ban and the negative-prompt block, both of which live in
    # the packaged skill library. A rewrite that fixes camera drift by naming
    # a real actor trades one failure for a safety-filter rejection.
    from flowboard.services import knowledge

    rules = knowledge.sections_for("prompt_rules")
    system = _REVISE_SYSTEM
    if rules.text:
        system += "\n\n--- Ràng buộc bắt buộc ---\n" + rules.text

    try:
        text, provider = await run_llm_chain(
            "revise", ask, system_prompt=system, timeout=timeout
        )
    except LLMError as exc:
        logger.info("review_loop: no provider could revise the prompt (%s)", exc)
        return None
    logger.info("review_loop: prompt revised by %s", provider)
    revised = (text or "").strip()
    if revised.startswith("```"):
        revised = revised.strip("`").strip()
    if not revised or revised == prompt.strip():
        logger.info("review_loop: the revision was empty or unchanged")
        return None
    return revised
