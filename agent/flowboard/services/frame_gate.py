"""Check the still before spending a video credit on it.

Image-to-video starts from a frame. If that frame is wrong — the wrong
subject, the wrong crop, a mangled hand — the clip built from it is wrong
too, and the clip is the expensive half. This looks at the still first.

The economics are the whole argument. A regenerated image is cheap or free;
a regenerated 8-second clip is the thing the credit table is about. Moving
the rejection one step earlier changes what a bad generation costs.

One vision call per check, against the user's own AI quota — the same
footing as transcription and review. It never spends a Flow credit itself,
and it never triggers a regeneration on its own: it answers, and the caller
decides.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class GateError(RuntimeError):
    """The check could not run. Never means "the frame is bad"."""


SYSTEM_PROMPT = """\
You are checking whether a still image is a usable first frame for a video \
that was asked for with this brief.

BRIEF: {brief}

Answer these, and nothing else:
1. Does the image show what the brief asks for — the right subject, doing \
or about to do the right thing, in the right setting?
2. Is it technically sound as a first frame — in focus, correctly framed, \
the subject not cropped through, hands and faces intact?
3. Is there anything that would make the video built from it unusable — \
text or a watermark burned in, a real brand logo, a visible artifact?

Be strict about the brief and forgiving about taste. A competent image that \
matches the brief passes even if you would have composed it differently; a \
beautiful image of the wrong subject fails.

Return ONLY this JSON:
{{"ok": true|false, "score": 0.0-10.0, "reasons": ["..."], \
"fix": "<one sentence in Vietnamese on what to change, or empty>"}}"""


@dataclass
class Verdict:
    ok: bool
    score: float
    reasons: list[str]
    fix: str = ""


def parse_verdict(raw: str) -> Verdict:
    """A model's answer as a Verdict.

    A failure to parse raises rather than defaulting either way. Defaulting
    to `ok` would let the gate wave through anything on a bad day; defaulting
    to `not ok` would block generation whenever the reviewer hiccuped. The
    caller has to be able to tell "the frame failed" from "the check did".
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(f"the checker did not answer with JSON: {exc}") from None
    if not isinstance(parsed, dict) or "ok" not in parsed:
        raise GateError("the checker's answer had no verdict in it")

    try:
        score = max(0.0, min(10.0, float(parsed.get("score", 0))))
    except (TypeError, ValueError):
        score = 0.0
    reasons = [
        str(r).strip()
        for r in (parsed.get("reasons") or [])
        if str(r).strip()
    ]
    return Verdict(
        ok=bool(parsed["ok"]),
        score=score,
        reasons=reasons,
        fix=str(parsed.get("fix") or "").strip(),
    )


async def check_frame(
    image: Path, *, brief: str, timeout: float = 120.0
) -> Verdict:
    """Is this still a usable first frame for the brief? One vision call."""
    from flowboard.services.llm import run_llm
    from flowboard.services.llm.base import LLMError

    source = Path(image)
    if not source.is_file():
        raise GateError(f"first frame not found: {source}")
    if not brief.strip():
        raise GateError("no brief to check the frame against")

    try:
        raw = await run_llm(
            "vision",
            "Khung hình đầu tiên, kiểm tra theo brief.",
            system_prompt=SYSTEM_PROMPT.format(brief=brief.strip()),
            attachments=[str(source)],
            timeout=timeout,
        )
    except LLMError as exc:
        raise GateError(str(exc)) from None

    verdict = parse_verdict(raw)
    logger.info(
        "frame gate: %s -> %s (%.1f)",
        source.name, "ok" if verdict.ok else "rejected", verdict.score,
    )
    return verdict
