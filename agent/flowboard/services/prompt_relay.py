"""Three models in a relay, each doing the part it is best at.

`prompt_ensemble` asks every available provider the same question and has a
judge pick a winner. That is redundancy: three models do one job, and two of
the three answers are thrown away. This does something different — it splits
the job and hands each stage to the model suited to it.

1. **Claude plans.** A brief becomes a beat sheet: who is in the scene, the
   one action, the camera, the constraints the lane imposes. Structured JSON
   is what this stage has to produce, and it is what Claude is steadiest at.
2. **Codex writes.** The beat sheet becomes a prompt — Vietnamese dialogue
   that sounds spoken rather than translated, and an English visual
   description. Phrasing is the job here.
3. **The rules are checked for free.** `prompt_checks` runs first: no model,
   no network, no cost. It catches the failures that otherwise surface as a
   refused request — a character tag inside spoken text, a real name in the
   visual half, four characters on a three-character lane, a duration the
   lane does not offer.
4. **Claude verifies.** Only what a regular expression cannot judge: does
   the prompt still describe what the brief asked for?

Failures from 3 and 4 go back to the writer **once**. One round, not a loop:
a second rewrite that still breaks a mechanical rule means the rule needs
explaining better, not asking again — and each round is a real call.

Every stage's output is kept. A prompt you cannot see the reasoning behind
is one you cannot argue with, which is the same reason `prompt_ensemble`
keeps its losing drafts.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_REVISIONS = 1

_PLANNER_SYSTEM = """\
You turn a brief into a beat sheet for ONE video scene.

Answer with JSON and nothing else:
{"subject": "who or what is on screen, with the fixed features that must not \
drift between clips",
 "action": "the single continuous action, in one sentence",
 "camera": "what the camera does, or 'locked' if it does not move",
 "setting": "where, and the light",
 "must_include": ["concrete things the finished prompt has to contain"]}

Rules:
- ONE action. A scene listing three actions becomes a clip that does none.
- The subject description has to be concrete enough that a different model, \
given only this, would draw the same person.
- Do not write the prompt itself. That is the next stage's job."""

_WRITER_SYSTEM = """\
You write the generation prompt for one video scene, from a beat sheet.

Write:
- The visual description in ENGLISH: subject, setting, light, framing, camera.
- Any spoken line in VIETNAMESE, on its own line starting with "Lời thoại:".

Hard rules, in order of how expensive they are to break:
- Character tags (@name, @@name) go ONLY in the visual description. A tag in \
spoken text is read aloud as "at at".
- Do NOT name real people or copyrighted characters in the VISUAL \
description — describe appearance and clothing instead. The same name IS \
allowed in the spoken line, which a separate voice model reads.
- One continuous action. Do not restate the beat sheet as a list.
- Under 2000 characters.

Answer with the prompt and nothing else. No preamble, no fences."""

_VERIFIER_SYSTEM = """\
You check whether a generation prompt still says what its brief asked for.

You are NOT checking spelling, formatting or rule compliance — those are \
already checked mechanically. Judge only:
- Does the prompt describe the subject, action and setting the beat sheet \
specified?
- Did anything in `must_include` go missing?
- Did the writer add a second action, or change the intent?

Answer with JSON and nothing else:
{"ok": true|false, "faults": ["what is wrong, concretely"]}"""


@dataclass
class Stage:
    """One model's turn, kept so the result can be argued with."""

    name: str
    provider: str = ""
    output: str = ""
    error: str = ""


@dataclass
class Relay:
    prompt: str = ""
    beat_sheet: dict = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=list)
    #: Everything still wrong with the FINAL prompt: the mechanical findings
    #: and, as ``rule="semantic"``, whatever the verifier rejected. Non-empty
    #: means it survived the revision round and the caller has to decide.
    #:
    #: The verifier's half used to be dropped here, so a relay whose rewrite
    #: the verifier threw out came back `clean=True` with an empty list — the
    #: one answer that stops a caller from looking.
    findings: list = field(default_factory=list)
    revisions: int = 0

    @property
    def clean(self) -> bool:
        from flowboard.services import prompt_checks

        return not prompt_checks.errors(self.findings)


def _json_from(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def compose(
    brief: str,
    *,
    cast: Optional[list[str]] = None,
    lane: Optional[str] = None,
    seconds: Optional[int] = None,
    timeout: float = 120.0,
) -> Relay:
    """Run the relay. Raises ``LLMError`` only when no stage can run at all.

    A stage that fails degrades rather than aborts: no beat sheet means the
    writer works from the brief directly, and no verifier means the
    mechanical checks stand alone. Losing the planner should cost quality,
    not the prompt.
    """
    from flowboard.services import knowledge, prompt_checks
    from flowboard.services.llm import run_llm_chain
    from flowboard.services.llm.base import LLMError

    brief = (brief or "").strip()
    if not brief:
        raise LLMError("no brief to work from")

    result = Relay()

    # ── 1. plan ───────────────────────────────────────────────────────
    beat_sheet: dict = {}
    stage = Stage(name="planner")
    try:
        raw, stage.provider = await run_llm_chain(
            "canvas_agent", brief, system_prompt=_PLANNER_SYSTEM, timeout=timeout
        )
        stage.output = raw
        beat_sheet = _json_from(raw) or {}
        if not beat_sheet:
            stage.error = "beat sheet was not JSON"
    except LLMError as exc:
        stage.error = str(exc)[:200]
    result.stages.append(stage)
    result.beat_sheet = beat_sheet

    # ── 2. write ──────────────────────────────────────────────────────
    craft = knowledge.sections_for("write_prompt")
    writer_system = _WRITER_SYSTEM
    if craft.text:
        writer_system += "\n\n--- Craft reference ---\n" + craft.text

    ask = _writer_ask(brief, beat_sheet, cast, lane, seconds)
    stage = Stage(name="writer")
    try:
        draft, stage.provider = await run_llm_chain(
            "script_writer", ask, system_prompt=writer_system, timeout=timeout
        )
        stage.output = draft = (draft or "").strip()
    except LLMError as exc:
        stage.error = str(exc)[:200]
        result.stages.append(stage)
        raise LLMError(f"the writer could not answer: {exc}") from None
    result.stages.append(stage)

    # ── 3. mechanical checks, then 4. semantic — one revision round ───
    for attempt in range(MAX_REVISIONS + 1):
        findings = prompt_checks.check(draft, cast=cast, lane=lane, seconds=seconds)
        faults = [f.message for f in prompt_checks.errors(findings)]

        semantic = await _verify(draft, beat_sheet, timeout=timeout)
        result.stages.append(semantic.stage)
        faults += semantic.faults

        result.prompt = draft
        result.findings = findings + [
            prompt_checks.Finding(
                rule="semantic", severity="error", message=fault
            )
            for fault in semantic.faults
        ]
        if not faults or attempt == MAX_REVISIONS:
            if faults:
                logger.info(
                    "prompt_relay: %d fault(s) survived the revision round", len(faults)
                )
            break

        # One round back to the writer, with the faults named.
        result.revisions += 1
        retry = Stage(name="rewrite")
        try:
            draft, retry.provider = await run_llm_chain(
                "script_writer",
                f"{ask}\n\nBẢN TRƯỚC:\n{draft}\n\nLỖI CẦN SỬA:\n"
                + "\n".join(f"- {f}" for f in faults),
                system_prompt=writer_system,
                timeout=timeout,
            )
            retry.output = draft = (draft or "").strip()
        except LLMError as exc:
            retry.error = str(exc)[:200]
            result.stages.append(retry)
            break
        result.stages.append(retry)

    return result


def _writer_ask(
    brief: str,
    beat_sheet: dict,
    cast: Optional[list[str]],
    lane: Optional[str],
    seconds: Optional[int],
) -> str:
    parts = [f"BRIEF:\n{brief}"]
    if beat_sheet:
        parts.append("BEAT SHEET:\n" + json.dumps(beat_sheet, ensure_ascii=False, indent=1))
    if cast:
        parts.append(
            "NHÂN VẬT (dùng đúng tên này khi tag):\n"
            + "\n".join(f"- @@{c}" for c in cast)
        )
    limits = []
    if lane:
        from flowboard.services.prompt_checks import (
            DEFAULT_MAX_CHARACTERS, LANE_MAX_CHARACTERS,
        )

        limits.append(
            f"tối đa {LANE_MAX_CHARACTERS.get(lane, DEFAULT_MAX_CHARACTERS)} nhân vật"
        )
    if seconds:
        limits.append(f"{seconds} giây")
    if limits:
        parts.append("GIỚI HẠN: " + ", ".join(limits))
    return "\n\n".join(parts)


@dataclass
class _Verdict:
    stage: Stage
    faults: list[str] = field(default_factory=list)


async def _verify(draft: str, beat_sheet: dict, *, timeout: float) -> _Verdict:
    """Ask whether the draft still says what the brief asked for.

    Skipped entirely without a beat sheet — there would be nothing to check
    the draft against, and a verifier with no reference invents faults.
    """
    from flowboard.services.llm import run_llm_chain
    from flowboard.services.llm.base import LLMError

    stage = Stage(name="verifier")
    if not beat_sheet:
        stage.error = "no beat sheet to verify against"
        return _Verdict(stage=stage)

    ask = (
        "BEAT SHEET:\n"
        + json.dumps(beat_sheet, ensure_ascii=False, indent=1)
        + f"\n\nPROMPT:\n{draft}"
    )
    try:
        raw, stage.provider = await run_llm_chain(
            "canvas_agent", ask, system_prompt=_VERIFIER_SYSTEM, timeout=timeout
        )
        stage.output = raw
    except LLMError as exc:
        # A verifier that cannot answer must not block the prompt: the
        # mechanical checks already ran and they are the ones with teeth.
        stage.error = str(exc)[:200]
        return _Verdict(stage=stage)

    parsed = _json_from(raw)
    if parsed is None:
        stage.error = "verdict was not JSON"
        return _Verdict(stage=stage)
    if parsed.get("ok") is True:
        return _Verdict(stage=stage)
    faults = [str(f) for f in (parsed.get("faults") or []) if str(f).strip()]
    return _Verdict(stage=stage, faults=faults or ["the verifier rejected it"])
