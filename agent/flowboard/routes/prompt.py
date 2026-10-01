"""Auto-prompt route.

`POST /api/prompt/auto { node_id }` returns a Claude-composed prompt built
from the immediate-upstream context (character / visual_asset / image
nodes' aiBriefs). Frontend calls this when the user clicks Generate
without typing a prompt.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flowboard.services import prompt_synth, styles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


class AutoPromptBody(BaseModel):
    node_id: int
    # Optional video-only constraint: e.g. "static" → synth uses the camera-
    # locked system prompt and avoids dolly/zoom suggestions.
    camera: Optional[str] = None
    # Optional visual-style preset name from the style library.
    style: Optional[str] = None


class AutoPromptResponse(BaseModel):
    node_id: int
    prompt: str


class StyleEntry(BaseModel):
    name: str
    description: str


@router.get("/styles", response_model=list[StyleEntry])
async def list_styles() -> list[StyleEntry]:
    """Visual style presets the UI can offer for auto-prompt."""
    return [StyleEntry(**s) for s in styles.video_styles()]


class GenreEntry(BaseModel):
    key: str
    title: str
    #: Words of dialogue per scene, as the packaged tool specifies per genre.
    #: Load-bearing rather than decorative: a scene is 5–8 seconds, so this
    #: is what a voice can actually deliver without the clip being re-cut.
    wordsPerScene: Optional[list[int]] = None


@router.get("/genres", response_model=list[GenreEntry])
async def list_script_genres() -> list[GenreEntry]:
    """The eight Vietnamese script formulas the packaged tool writes in."""
    from flowboard.services import script_genres

    return [GenreEntry(**g) for g in script_genres.list_genres()]


class LibraryEntry(BaseModel):
    id: str
    number: int
    title: str
    summary: str


class LibraryPrompt(LibraryEntry):
    body: str


@router.get("/library", response_model=list[LibraryEntry])
async def list_library() -> list[LibraryEntry]:
    """The bundled fashion-posing prompts, headings only.

    Bodies run ~5KB each and there are 47 of them; shipping all of it to
    populate a dropdown would be a quarter of a megabyte for a list the user
    reads three words of. `GET /library/{id}` fetches the one they pick.
    """
    from flowboard.services import prompt_library

    return [
        LibraryEntry(id=e.id, number=e.number, title=e.title, summary=e.summary)
        for e in prompt_library.entries()
    ]


@router.get("/library/{entry_id}", response_model=LibraryPrompt)
async def get_library_prompt(entry_id: str) -> LibraryPrompt:
    from flowboard.services import prompt_library

    entry = prompt_library.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no prompt {entry_id!r}")
    return LibraryPrompt(
        id=entry.id,
        number=entry.number,
        title=entry.title,
        summary=entry.summary,
        body=entry.body,
    )


@router.post("/auto", response_model=AutoPromptResponse)
async def auto_prompt(body: AutoPromptBody) -> AutoPromptResponse:
    try:
        text = await prompt_synth.auto_prompt(
            body.node_id, camera=body.camera, style=body.style
        )
    except prompt_synth.PromptSynthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AutoPromptResponse(node_id=body.node_id, prompt=text)


class EnsembleDraft(BaseModel):
    provider: str
    prompt: str = ""
    error: str = ""


class EnsembleResponse(BaseModel):
    node_id: int
    prompt: str
    #: Which provider judged. Empty when only one model answered, or when
    #: the judge itself could not.
    judge: str = ""
    #: One sentence on why this draft won, in Vietnamese.
    why: str = ""
    #: 1-based index into `drafts` of the draft the judge took most from.
    chosen: Optional[int] = None
    #: Every draft, including the ones that failed. A prompt whose
    #: alternatives you cannot see is one you cannot argue with.
    drafts: list[EnsembleDraft] = []


@router.post("/auto/ensemble", response_model=EnsembleResponse)
async def auto_prompt_ensemble(body: AutoPromptBody) -> EnsembleResponse:
    """Every available model drafts the prompt; one of them picks.

    Costs more than `/auto` in wall-clock, and almost nothing in money:
    Claude and Codex are signed in through OAuth, so their marginal cost is
    a subscription already paid for. Gemini bills per call.
    """
    try:
        result = await prompt_synth.auto_prompt_ensemble(
            body.node_id, camera=body.camera, style=body.style
        )
    except prompt_synth.PromptSynthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return EnsembleResponse(node_id=body.node_id, **result)


class RelayBody(BaseModel):
    brief: str = Field(min_length=1, max_length=8000)
    #: Character names the prompt may tag. A tag naming nobody resolves to
    #: nothing, so the check needs the list to be worth running.
    cast: list[str] = []
    #: Lane and duration decide two mechanical limits — how many characters
    #: a scene may carry, and which durations the lane accepts.
    lane: Optional[str] = None
    seconds: Optional[int] = Field(default=None, ge=1, le=60)


class RelayStage(BaseModel):
    name: str
    provider: str = ""
    output: str = ""
    error: str = ""


class RelayFinding(BaseModel):
    rule: str
    severity: str
    message: str
    span: str = ""


class RelayResponse(BaseModel):
    prompt: str
    #: What the planner decided before anything was written.
    beatSheet: dict = {}
    #: Every stage, kept: a prompt whose reasoning you cannot see is one you
    #: cannot argue with.
    stages: list[RelayStage] = []
    #: Mechanical findings on the FINAL prompt. Non-empty means they
    #: survived the revision round and the caller decides what to do.
    findings: list[RelayFinding] = []
    revisions: int = 0
    clean: bool = True


@router.post("/relay", response_model=RelayResponse)
async def prompt_relay_route(body: RelayBody) -> RelayResponse:
    """Three models in a relay, each doing the part it is best at.

    Different from `/auto/ensemble`, which asks every provider the same
    question and discards two thirds of the answers. Here the job is split:
    one model plans, another writes, the rules are checked for free, and the
    first verifies the meaning.

    The free check is the reason this costs less than it looks — a character
    tag inside spoken text, a real name in the visual half, or a duration
    the lane does not offer are all found without a model call, before the
    request that would have been refused.
    """
    from flowboard.services import prompt_relay
    from flowboard.services.llm.base import LLMError

    try:
        result = await prompt_relay.compose(
            body.brief, cast=body.cast, lane=body.lane, seconds=body.seconds
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RelayResponse(
        prompt=result.prompt,
        beatSheet=result.beat_sheet,
        stages=[RelayStage(**vars(s)) for s in result.stages],
        findings=[RelayFinding(**vars(f)) for f in result.findings],
        revisions=result.revisions,
        clean=result.clean,
    )


class StoryboardBody(BaseModel):
    board_id: int
    idea: str = Field(min_length=1, max_length=8000)
    #: Every scene becomes one video node, and every video node is one
    #: dispatch when the board is run — so the ceiling is a cost guard the
    #: same way `/idea`'s is, even though drafting itself spends nothing.
    scene_count: int = Field(default=3, ge=1, le=12)
    seconds: int = Field(default=8, ge=1, le=60)
    structure: str = "chain"
    style: str = ""
    aspect: str = ""
    quality: str = ""
    #: One of `script_genres.GENRES` — the packaged tool's eight Vietnamese
    #: script formulas (sử thi, Phật pháp, đạo lý, drama lật kèo…). Chosen,
    #: the genre's own structure and dialogue length replace the generic
    #: craft notes. Unset keeps the generic ones.
    genre: Optional[str] = None
    #: The README's other two skills — see `IdeaBody`.
    use_longform: bool = False
    use_3act: bool = False
    #: One of `knowledge.FORMATS`.
    format: Optional[str] = None
    #: False returns the spec for preview without writing any rows. The
    #: default, because a board that appears without being asked for is
    #: worse than one click.
    materialize: bool = False


class StoryboardScene(BaseModel):
    title: str
    image: str = ""
    video: str


class StoryboardResponse(BaseModel):
    scenes: list[StoryboardScene]
    #: Node and edge counts, so the caller can say what it is about to make.
    nodes: int
    edges: int
    #: The plan the board was written into, when `materialize` was set.
    plan_id: Optional[int] = None
    spec: dict = {}


@router.post("/storyboard", response_model=StoryboardResponse)
async def storyboard(body: StoryboardBody) -> StoryboardResponse:
    """An idea to a wired board: scenes, stills, clips and the chain between.

    Costs one text call and nothing else. Materialising writes rows; no Flow
    dispatch happens until someone opens the board and presses Run, where
    the estimate and its confirmation still stand between them and the
    credits.
    """
    from flowboard.db import get_session
    from flowboard.db.models import Board, Plan
    from flowboard.services import storyboard as storyboard_service
    from flowboard.services.pipeline_executor import materialize_plan

    with get_session() as s:
        if s.get(Board, body.board_id) is None:
            raise HTTPException(404, f"no board {body.board_id}")

    try:
        drafted = await storyboard_service.draft(
            body.idea,
            scene_count=body.scene_count,
            seconds=body.seconds,
            structure=body.structure,
            style=body.style,
            aspect=body.aspect,
            quality=body.quality,
            genre=body.genre,
            use_longform=body.use_longform,
            use_3act=body.use_3act,
            video_format=body.format,
        )
    except storyboard_service.StoryboardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    spec = drafted["spec"]
    plan_id: Optional[int] = None
    if body.materialize:
        with get_session() as s:
            plan = Plan(board_id=body.board_id, status="approved", spec=spec)
            s.add(plan)
            s.commit()
            s.refresh(plan)
            plan_id = plan.id
            materialize_plan(s, plan.id)
            s.commit()

    return StoryboardResponse(
        scenes=[StoryboardScene(**sc) for sc in drafted["scenes"]],
        nodes=len(spec["nodes"]),
        edges=len(spec["edges"]),
        plan_id=plan_id,
        spec=spec,
    )


class IdeaBody(BaseModel):
    idea: str = Field(min_length=1, max_length=8000)
    # Each scene becomes one paid video dispatch, so the ceiling is a cost
    # guard as much as a sanity one.
    scene_count: int = Field(default=1, ge=1, le=20)
    seconds_per_scene: int = Field(default=8, ge=1, le=60)
    style: Optional[str] = None
    dialogue_language: Optional[str] = None
    no_dialogue: bool = False
    #: One of `script_genres.GENRES`. Brings that genre's structure and
    #: per-scene dialogue length; unset uses the general craft notes.
    genre: Optional[str] = None
    #: The README's other two skills, which had no caller until now: sequence
    #: orchestration for a 10–30 minute piece, and the three-act eight-sequence
    #: structure. Off by default — each one costs prompt budget.
    use_longform: bool = False
    use_3act: bool = False
    #: One of `knowledge.FORMATS`: a playbook for one shape of video.
    format: Optional[str] = None


class FormatEntry(BaseModel):
    key: str
    title: str


@router.get("/formats", response_model=list[FormatEntry])
async def list_formats() -> list[FormatEntry]:
    """The format playbooks, for the picker.

    Separate from genres: a genre is HOW it is written (sử thi, Phật pháp), a
    format is WHAT it is (trailer, micro-drama, found footage). The packaged
    skill tree ships both and this build was reading neither.
    """
    from flowboard.services import knowledge

    return [
        FormatEntry(key=key, title=knowledge.FORMAT_TITLES.get(key, key))
        for key in knowledge.FORMATS
    ]


class PromptFinding(BaseModel):
    """One rule broken by one scene's prompt.

    Reported rather than fixed: the prompts come back editable and the user is
    the one who knows whether "Bác Hồ" belongs in the narration (allowed) or in
    the picture (refused by the filter). The executor blocks the two that cost
    money if they reach a dispatch unchanged.
    """

    #: 1-based, matching the numbering the tab shows beside each box.
    scene: int
    rule: str
    severity: str
    message: str


class IdeaResponse(BaseModel):
    prompts: list[str]
    #: What the free mechanical check found, per scene. Empty is the normal
    #: case; anything here is something a paid dispatch would charge for.
    findings: list[PromptFinding] = []


@router.post("/idea", response_model=IdeaResponse)
async def idea_to_prompts(body: IdeaBody) -> IdeaResponse:
    """Turn a free-text idea into an ordered shot list.

    The canvas's auto-prompt walks upstream nodes; this has none — the idea
    is the whole input, which is what the packaged tool's "Ý tưởng to Video"
    tab works from. The prompts come back for the user to edit before any
    of them is dispatched, because each one costs credits.
    """
    try:
        prompts = await prompt_synth.idea_to_prompts(
            body.idea,
            scene_count=body.scene_count,
            seconds_per_scene=body.seconds_per_scene,
            style=body.style,
            dialogue_language=body.dialogue_language,
            no_dialogue=body.no_dialogue,
            genre=body.genre,
            use_longform=body.use_longform,
            use_3act=body.use_3act,
            video_format=body.format,
        )
    except prompt_synth.PromptSynthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    from flowboard.services import prompt_checks

    findings = [
        PromptFinding(
            scene=index, rule=f.rule, severity=f.severity, message=f.message
        )
        for index, text in enumerate(prompts, start=1)
        for f in prompt_checks.check(text)
    ]
    return IdeaResponse(prompts=prompts, findings=findings)


class AutoPromptBatchBody(BaseModel):
    node_id: int
    count: int
    camera: Optional[str] = None
    # Same visual-style preset the single-prompt route accepts.
    style: Optional[str] = None


class AutoPromptBatchResponse(BaseModel):
    node_id: int
    prompts: list[str]


@router.post("/auto-batch", response_model=AutoPromptBatchResponse)
async def auto_prompt_batch(body: AutoPromptBatchBody) -> AutoPromptBatchResponse:
    """Return N pose-distinct prompts so that an N-variant image gen
    actually produces N different shots instead of N seeds of the same
    stance."""
    if body.count < 1 or body.count > 8:
        raise HTTPException(status_code=400, detail="count must be 1..8")
    try:
        prompts = await prompt_synth.auto_prompt_batch(
            body.node_id, body.count, camera=body.camera, style=body.style
        )
    except prompt_synth.PromptSynthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AutoPromptBatchResponse(node_id=body.node_id, prompts=prompts)


# ── which of the seven board shapes a brief means ─────────────────────


class ArchetypeEntry(BaseModel):
    key: str
    label: str
    chain: str


class ArchetypeChoice(BaseModel):
    #: ``explicit`` · ``inferred`` · ``ask``. The caller must not build a
    #: canvas on ``ask`` — that is the case where guessing produces a board
    #: the user has to take apart.
    reason: str
    archetype: Optional[ArchetypeEntry] = None
    #: What matched, so the explanation is evidence rather than assertion.
    matched: list[str] = []
    #: When ``ask``, the two or three worth offering.
    options: list[ArchetypeEntry] = []
    explanation: str = ""


@router.get("/archetypes", response_model=list[ArchetypeEntry])
def list_archetypes() -> list[ArchetypeEntry]:
    """The seven board shapes, with what each one builds."""
    from flowboard.services import archetypes

    return [
        ArchetypeEntry(key=a.key, label=a.label, chain=a.chain)
        for a in archetypes.ARCHETYPES
    ]


class ClassifyBody(BaseModel):
    brief: str = ""
    #: The user naming a shape outright. Wins whole — no scoring.
    requested: Optional[str] = None


@router.post("/archetypes/classify", response_model=ArchetypeChoice)
def classify_archetype(body: ClassifyBody) -> ArchetypeChoice:
    """Which shape to build, or which two or three to offer.

    Costs nothing and calls no model: the classification is keyword work on
    the packaged skill's own recognition notes. Reaching for an LLM here
    would make a free, deterministic decision slow and variable.
    """
    from flowboard.services import archetypes

    choice = archetypes.classify(body.brief, requested=body.requested)
    return ArchetypeChoice(
        reason=choice.reason,
        archetype=(
            ArchetypeEntry(
                key=choice.archetype.key,
                label=choice.archetype.label,
                chain=choice.archetype.chain,
            )
            if choice.archetype
            else None
        ),
        matched=list(choice.matched),
        options=[
            ArchetypeEntry(key=a.key, label=a.label, chain=a.chain)
            for a in choice.options
        ],
        explanation=archetypes.describe(choice),
    )
