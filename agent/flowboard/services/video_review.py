"""Score a generated clip against the prompt that asked for it.

Ported from flowkit's `video_reviewer.py`, which was built against a lot of
real Veo output; the parts worth keeping are its calibration, not its
plumbing. What came over:

* the six weighted dimensions and the verdict bands;
* the error rubric, with its severity tiers AND its frequency notes
  ("camera drift in ~60% of videos after 4s"). Those numbers are why a
  review comes back specific instead of politely vague — the model is told
  what to go looking for;
* ``usable_segments``, which turns "this clip is a 5.5" into "seconds 0-4
  are fine", the difference between rejecting a clip and trimming it;
* contact sheets rather than loose frames — see `postprod.contact_sheets`.

What did not come over: its own CLI spawning (this build has
`llm.registry.run_llm` with auth detection in front of three providers), its
database models, and its `os.symlink` frame staging, which needs Developer
Mode on Windows.

**This costs one vision call per clip**, which is why `routes.estimate`
counts it separately. Nothing here spends Flow credits.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ReviewError(RuntimeError):
    """The clip could not be reviewed. Never means "the clip is bad"."""


#: Six dimensions, weighted. Character consistency leads because it is both
#: the most common failure in generated video and the one a viewer notices
#: first — a face that changes mid-clip reads as broken even when everything
#: else is beautiful.
WEIGHTS: dict[str, float] = {
    "character_consistency": 0.25,
    "prompt_adherence": 0.20,
    "motion_quality": 0.20,
    "visual_fidelity": 0.15,
    "temporal_coherence": 0.10,
    "composition": 0.10,
}

#: Verdict bands. `acceptable` is the interesting boundary: at 6.0 a clip is
#: usable but not good, which is exactly where an automatic retry is worth
#: its cost and a manual one usually is not.
_BANDS: tuple[tuple[float, str], ...] = (
    (9.0, "excellent"),
    (7.5, "good"),
    (6.0, "acceptable"),
    (4.0, "poor"),
)


@dataclass
class Issue:
    severity: str  # CRITICAL / HIGH / MINOR
    time_range: str
    description: str


@dataclass
class Review:
    score: float
    verdict: str
    dimensions: dict[str, float]
    issues: list[Issue] = field(default_factory=list)
    usable_segments: list[dict] = field(default_factory=list)
    fix_hint: str = ""
    frames: int = 0
    sheets: int = 0

    @property
    def has_critical(self) -> bool:
        return any(i.severity.upper() == "CRITICAL" for i in self.issues)


SYSTEM_PROMPT = """\
You are a strict reviewer of AI-generated video. You are shown {frames} \
frames sampled at {fps}fps from one clip, tiled into {sheets} contact \
sheet(s) in chronological order, each frame stamped with its timestamp.

THE PROMPT THAT ASKED FOR THIS CLIP:
{prompt}

EXPECTED SUBJECTS: {subjects}

== SCORE EACH DIMENSION 0.0-10.0 ==
1. character_consistency — do the subjects stay the same person/creature? \
Stable face, clothing, limb count, proportions?
2. prompt_adherence — does the clip show what the prompt asked for?
3. motion_quality — smooth movement, no jitter, teleporting or reversal?
4. visual_fidelity — sharp, free of artifacts, no unwanted logos or text?
5. temporal_coherence — consistent lighting, shadows, background and scale?
6. composition — framing and camera behaviour as directed?

== WHAT TO LOOK FOR ==
These are the failures generated video actually produces, with how often.
Check for each one specifically rather than forming a general impression.

CRITICAL (score the affected dimension 0-3):
- Character drift: the subject morphs mid-clip — extra limbs, a changed \
face, a different breed. Most common after 3-4 seconds.
- Subject swap: two similar subjects trade places or roles.
- Role reversal: the wrong subject performs the action.
- Brand logo: a real trademark appears. A legal problem, not a cosmetic one.
- Wrong count: more or fewer subjects than the prompt asked for.

HIGH (score the affected dimension 4-6):
- Camera drift: unrequested zoom, rotation or angle change. Around 60% of \
clips after 4 seconds.
- Object morph: a held item changes shape.
- Reverse motion: an action is performed and then undone. Around 30%.
- Wrong anatomy: human hands on a non-human subject.
- Scale break: a subject suddenly too big or small for the scene.

MINOR (score the affected dimension 7-8, still usable):
- Prop count changes, texture shifts, garbled background text, an accessory \
appearing or vanishing.

== RULES ==
- If ANY critical error is present, character_consistency must be 3.0 or below.
- Give every issue a time_range read off the burned-in timestamps, e.g. "3s-5s".
- usable_segments are the continuous stretches with no CRITICAL or HIGH issue. \
A clip that fails as a whole is often still usable in part, and that is the \
most useful thing this review can say.

Return ONLY this JSON, no markdown fence:
{{"dimensions": {{"character_consistency": N, "prompt_adherence": N, \
"motion_quality": N, "visual_fidelity": N, "temporal_coherence": N, \
"composition": N}}, "issues": [{{"severity": "CRITICAL|HIGH|MINOR", \
"time_range": "Xs-Ys", "description": "..."}}], \
"usable_segments": [{{"time_range": "Xs-Ys", "score": N}}]}}"""


def verdict_for(score: float) -> str:
    for threshold, name in _BANDS:
        if score >= threshold:
            return name
    return "unusable"


def overall(dimensions: dict[str, float]) -> float:
    """The weighted score. Missing dimensions count as zero, deliberately:
    a model that skipped one did not judge it, and treating silence as a
    pass is how a broken review reads as a good clip."""
    return round(sum(dimensions.get(k, 0.0) * w for k, w in WEIGHTS.items()), 2)


def _clamp(value: object) -> float:
    try:
        return max(0.0, min(10.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def fix_hint(dimensions: dict[str, float], issues: list[Issue]) -> str:
    """One sentence on what to change, in Vietnamese.

    Aimed at the weakest dimension rather than listing everything: a fix
    that addresses six things at once is a rewrite, and the point is to give
    the retry loop one concrete lever to pull.
    """
    if any(i.severity.upper() == "CRITICAL" for i in issues):
        return (
            "Có lỗi nghiêm trọng — mô tả nhân vật cụ thể hơn (khuôn mặt, "
            "trang phục, số lượng) và giữ nguyên qua mọi cảnh."
        )
    if not dimensions:
        return ""
    weakest = min(dimensions, key=lambda k: dimensions.get(k, 0.0))
    return {
        "character_consistency": (
            "Nhân vật đổi giữa chừng — tả rõ đặc điểm cố định và tránh "
            "hành động che mặt."
        ),
        "prompt_adherence": "Prompt chưa được làm đúng — nói thẳng hành động chính.",
        "motion_quality": (
            "Chuyển động giật hoặc lùi — mô tả một hành động liên tục, "
            "không nhiều động tác nối nhau."
        ),
        "visual_fidelity": "Ảnh mờ/nhiễu — thêm mô tả ánh sáng và độ nét.",
        "temporal_coherence": (
            "Ánh sáng/bối cảnh đổi giữa chừng — cố định thời điểm và "
            "nguồn sáng trong prompt."
        ),
        "composition": "Khung hình lệch — nói rõ cỡ cảnh và máy quay đứng yên.",
    }.get(weakest, "")


def parse_review(raw: str) -> Review:
    """A model's answer as a Review.

    Tolerant of a markdown fence and of missing keys, strict about the
    numbers: anything unparseable scores zero rather than defaulting to
    something flattering.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"the reviewer did not answer with JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ReviewError("the reviewer answered with JSON that is not an object")

    raw_dims = parsed.get("dimensions")
    dimensions = {
        key: _clamp((raw_dims or {}).get(key)) if isinstance(raw_dims, dict) else 0.0
        for key in WEIGHTS
    }

    issues: list[Issue] = []
    for entry in parsed.get("issues") or []:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        if not description:
            continue
        severity = str(entry.get("severity") or "MINOR").strip().upper()
        if severity not in ("CRITICAL", "HIGH", "MINOR"):
            # Normalised rather than rejected, because a whole review is worth
            # more than one mislabelled line. But said out loud: three things
            # branch on this exact string — the `character_consistency` cap
            # below, `has_critical`, and `fix_hint` — so a model that grades
            # with its own vocabulary silently disables all three, and the
            # clip passes carrying a defect it just described. A rising count
            # here means the reviewer is drifting off the rubric.
            logger.warning(
                "review: unknown severity %r normalised to MINOR (%s)",
                severity, description[:80],
            )
            severity = "MINOR"
        issues.append(
            Issue(
                severity=severity,
                time_range=str(entry.get("time_range") or "").strip(),
                description=description,
            )
        )

    segments = [
        {
            "time_range": str(s.get("time_range") or "").strip(),
            "score": _clamp(s.get("score")),
        }
        for s in (parsed.get("usable_segments") or [])
        if isinstance(s, dict) and str(s.get("time_range") or "").strip()
    ]

    # The prompt says a critical error caps character_consistency at 3.0.
    # Enforced here rather than trusted: a model that finds the error and
    # then scores the dimension 8 has contradicted itself, and the finding
    # is the more reliable half.
    if any(i.severity == "CRITICAL" for i in issues):
        dimensions["character_consistency"] = min(
            dimensions["character_consistency"], 3.0
        )

    score = overall(dimensions)
    return Review(
        score=score,
        verdict=verdict_for(score),
        dimensions=dimensions,
        issues=issues,
        usable_segments=segments,
        fix_hint=fix_hint(dimensions, issues),
    )


async def review_clip(
    video: Path,
    *,
    prompt: str = "",
    subjects: Optional[list[str]] = None,
    fps: float = 3.0,
    max_frames: int = 48,
    timeout: float = 240.0,
    storage_dir: Optional[Path] = None,
) -> Review:
    """Score one clip. One vision call; no Flow credits.

    **Why this cannot quietly grade a clip nobody looked at.** The system
    prompt below carries the rubric, the originating prompt and the subject
    names — enough material to write a complete, well-formed review without
    ever seeing a frame. Three things stop that from happening, and all three
    are load-bearing:

    * `postprod.contact_sheets` raises rather than returning an empty list, so
      there is no path where `attachments` is `[]` and the call still runs;
    * `registry.run_llm` refuses when `attachments` are present and the pinned
      provider has no vision capability, instead of dropping the images;
    * `claude_cli.run_claude` passes `--add-dir` per attachment parent **and**
      `--permission-mode bypassPermissions`, so the CLI's Read tool is not
      denied headlessly. Without those the CLI answers from the prompt alone —
      which is how flowkit measured this exact failure on 2026-09-20.

    Removing any of the three brings the blind review back, and the answer
    looks identical from the outside. `tests/test_claude_cli.py` pins the flags.
    """
    from fastapi.concurrency import run_in_threadpool

    from flowboard.services import knowledge, postprod
    from flowboard.services.llm import run_llm
    from flowboard.services.llm.base import LLMError

    if not postprod.available():
        raise ReviewError("ffmpeg not found — cannot sample the clip")

    workdir = Path(tempfile.mkdtemp(prefix="review-", dir=storage_dir))
    try:
        try:
            sheets, frames = await run_in_threadpool(
                postprod.contact_sheets,
                video,
                workdir,
                fps=fps,
                max_frames=max_frames,
            )
        except postprod.PostProdError as exc:
            raise ReviewError(str(exc)) from None

        system = SYSTEM_PROMPT.format(
            frames=frames,
            fps=fps,
            sheets=len(sheets),
            prompt=prompt.strip() or "(không có prompt — chấm theo chất lượng chung)",
            subjects=", ".join(subjects or []) or "không nêu",
        )
        # The six dimensions above are this build's own. The packaged tool
        # grades against a 100-point director's rubric and a 25-point viral
        # score that ship in the asset library and had never been read —
        # appending them gives the reviewer the same yardstick the original
        # tool uses, alongside the mechanical checks it already runs.
        rubric = knowledge.sections_for("review_clip")
        if rubric.text:
            system += (
                "\n\n--- Thang chấm của bộ công cụ gốc, dùng để đối chiếu ---\n"
                + rubric.text
            )
        try:
            raw = await run_llm(
                "vision",
                f"{len(sheets)} contact sheet(s), {frames} frames, in order.",
                system_prompt=system,
                attachments=[str(p) for p in sheets],
                timeout=timeout,
            )
        except LLMError as exc:
            raise ReviewError(str(exc)) from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    review = parse_review(raw)
    review.frames = frames
    review.sheets = len(sheets)
    logger.info(
        "reviewed %s: %.2f (%s), %d issue(s)",
        video.name, review.score, review.verdict, len(review.issues),
    )
    return review
