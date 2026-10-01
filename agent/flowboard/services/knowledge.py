"""The packaged tool's own craft knowledge, loaded at run time.

`ASSET_ROOT` ships 413 KiB of markdown (423,317 bytes, counted on disk) this build has never opened: 36 files
of Vietnamese video-production skill under
``data_general/skill/video_skills`` (hook writing, McKee-derived story
structure, four drama genre formulas, a 146 KiB Veo 3 prompting guide, a
50-move camera library, a canonical negative-prompt block, a 100-point
director's rubric) plus the five ``workflow_ai_skills/*/SKILL.md`` files that
drive the packaged tool's own canvas chat.

Every AI step here — storyboard, prompt writing, clip review, the frame gate
— runs on a system prompt written from scratch instead. That is the gap this
module closes.

**Read, never copied.** The files stay in `ASSET_ROOT`. Copying them into the
repo would fork 413 KiB of prose that the packaged tool still updates, and
this build already depends on that folder for fifteen other asset groups, so
reading two more costs no new failure mode. A missing folder yields an empty
string and a logged warning, never an exception: knowledge makes a prompt
better, and a step that used to run without it must not start failing because
the packaged tool was uninstalled.

**Budgeted, and loud about it.** `cli_utils.MAX_PROMPT_BYTES` caps a prompt
at 100 KB; the guide alone is 146 KiB. Selections are therefore capped, and
`Knowledge.dropped` names what did not fit. A silent cap would read as "the
model was told everything" when it was told two thirds.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Bytes of knowledge one dispatch may carry. A quarter of the 100 KB prompt
#: ceiling, leaving the brief, the board snapshot and the model's own
#: instructions the rest.
DEFAULT_BUDGET_BYTES = 25_000


@dataclass(frozen=True)
class Source:
    """One file to draw from, optionally narrowed to named sections.

    ``headings`` holds substrings matched against the file's ``## `` lines
    after accent- and emoji-stripping — the packaged files spell the same
    heading as ``"## 🎯 BẢNG 7 DẠNG VIDEO CHUẨN"`` in one place and
    ``"## Bang 7 dang video chuan"`` in another, and an exact match finds
    neither reliably. Empty means the whole file.
    """

    path: str
    headings: tuple[str, ...] = ()


@dataclass
class Knowledge:
    """What was assembled, and what did not fit."""

    text: str = ""
    files: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.text)


_VIDEO_SKILLS = "video_skills"
_WORKFLOW_SKILLS = "workflow_skills"


def _roots() -> dict[str, Path]:
    """Resolved at call time so tests can redirect ``assets.ASSET_ROOT``."""
    from flowboard.services import assets

    return {
        _VIDEO_SKILLS: assets.DATA_DIR / "skill" / "video_skills",
        _WORKFLOW_SKILLS: assets.ASSET_ROOT / "workflow_ai_skills",
    }


#: Which sources each AI step draws on.
#:
#: Grouped by the job being done rather than by folder, because one folder
#: serves several jobs: the hook files help both a storyboard and a rewrite,
#: and the drama formulas help both scripting and review.
TASKS: dict[str, tuple[Source, ...]] = {
    # Turning an idea into a scene list. Hook craft decides whether the
    # first clip is watched at all, and structure decides whether the rest is.
    "storyboard": (
        Source(f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/three-layer-hook.md"),
        Source(f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/hook-archetypes.md"),
        Source(f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/hook-anti-patterns.md"),
        Source(f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/viral_retention.md"),
        Source(f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/mckee_storytelling_master.md"),
        # Sliced rather than whole: each of these earns a place with one or two
        # sections, and a whole file here costs the sections of every other.
        Source(
            f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/hook-tactics.md",
            headings=(
                "tactics to layer on a weak draft",
                "curiosity gaps with a defined shape",
                "in media res openers",
            ),
        ),
        Source(
            f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/hook-by-platform.md",
            headings=("tiktok", "youtube shorts", "cross platform repurposing"),
        ),
        Source(
            f"{_VIDEO_SKILLS}/01_HOOK_VIET_VIRAL/12_drama_thuc_te_hooks.md",
            headings=("quy tac mat do kich tinh",),
        ),
        Source(
            f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/04_script_video_tieng_viet.md",
            headings=(
                "nguyen tac cot loi",
                "cau truc ket qua kich ban phan canh",
                "checklist chat luong",
            ),
        ),
        Source(
            f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/viral_hooks.md",
            headings=("constructing the hook line", "pairing hooks to payoff"),
        ),
        Source(f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/viral_formats.md"),
        Source(
            f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/viral_platforms.md",
            headings=("quick platform fit checklist", "shared fundamentals"),
        ),
    ),
    # Writing the prompt a generator actually receives. The Veo guide is
    # narrowed to sections: whole, it is 146 KiB against a 100 KB ceiling.
    "write_prompt": (
        Source(f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/mx_shell_methodology.md"),
        Source(f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/negative-prompts.md"),
        Source(f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/camera-move-library.md"),
        Source(f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/atmosphere-prefabs.md"),
        Source(
            f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/veo3_prompting_guide.md",
            headings=("prompt structure", "dialogue", "camera", "audio"),
        ),
        Source(
            f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/genre-camera-sop.md",
            headings=("quick pick table", "how to use this with the 5 stage structure"),
        ),
        Source(
            f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/shortfilm_prompt_skill.md",
            headings=("seven hard rules", "what not to do", "output format"),
        ),
        Source(
            f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/multi-shot-narrative.md",
            headings=("skeleton", "five high yield multi shot techniques"),
        ),
    ),
    # Scoring a finished clip. The packaged tool grades against a 100-point
    # director's rubric and a 25-point viral score; this build invented its
    # own six dimensions without ever reading either.
    "review_clip": (
        Source(f"{_VIDEO_SKILLS}/04_PHAN_TICH_DANH_GIA_VIDEO/director_script_review.md"),
        Source(f"{_VIDEO_SKILLS}/04_PHAN_TICH_DANH_GIA_VIDEO/viral_score_and_retention_audit.md"),
        # What a number means and when to stop iterating. A scorer without
        # this reads every clip as a failure it can fix by trying again.
        Source(f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/viral_metrics-honesty.md"),
        Source(
            f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/06_brief_ugc_review.md",
            headings=("nguyen tac cot loi", "checklist chat luong"),
        ),
    ),
    # Emitting canvas actions. These are the packaged tool's own rules for
    # its chat: what may be created, how nodes wire, the tag syntax, and the
    # celebrity-name ban that keeps a prompt from tripping safety filters.
    "canvas": (
        Source(f"{_WORKFLOW_SKILLS}/workflow-node-knowledge/SKILL.md"),
        Source(f"{_WORKFLOW_SKILLS}/workflow-pipeline-selector/SKILL.md"),
        Source(
            f"{_WORKFLOW_SKILLS}/workflow-intake-planner/SKILL.md",
            headings=("gate before action", "wiring rules", "character naming"),
        ),
        Source(
            f"{_WORKFLOW_SKILLS}/workflow-script-to-canvas/SKILL.md",
            headings=("nguoi noi tieng",),
        ),
    ),
    # The rules a written prompt must not break. Small on purpose — this one
    # rides along with a draft, so it competes with the draft for budget.
    "prompt_rules": (
        Source(
            f"{_WORKFLOW_SKILLS}/workflow-script-to-canvas/SKILL.md",
            headings=("nguoi noi tieng",),
        ),
        Source(f"{_VIDEO_SKILLS}/03_VIET_PROMPT_AI_VIDEO/negative-prompts.md"),
        # NOT the copies of this policy in `04_script_video_tieng_viet.md` and
        # `shortfilm_prompt_skill.md`. Measured: both are the same section
        # verbatim, same thirteen names — adding them put the policy in front
        # of the model four times and spent 5 KB of a budget this task shares
        # with the draft it is checking.
    ),
}

#: Playbooks for ONE shape of video, loaded only when that shape is chosen.
#:
#: They do not belong in `TASKS`: a worked example of a found-footage horror
#: short is the wrong thing to put in front of a Buddhist parable, and putting
#: all four in every storyboard would take the budget from the craft notes that
#: apply to every board. Same mechanism `script_genres` uses — the caller picks
#: and `load_paths` reads.
FORMATS: dict[str, str] = {
    "multi_shot": f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/multi-shot-narrative.md",
    "micro_drama": f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/micro-drama.md",
    "movie_trailer": f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/movie-trailer.md",
    "found_footage": f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/found-footage-horror.md",
    "project_plan": f"{_VIDEO_SKILLS}/02_DUNG_KICH_BAN_STORYTELLING/project-planner.md",
}

#: Titles for the picker, in the order they are offered.
FORMAT_TITLES: dict[str, str] = {
    "multi_shot": "Nhiều shot liền mạch",
    "micro_drama": "Micro-drama (1 cảnh, 1 cú twist)",
    "movie_trailer": "Trailer phim",
    "found_footage": "Found footage / kinh dị",
    "project_plan": "Bảng kế hoạch nhiều shot",
}

#: A task may need more than the default. Storyboard and prompt-writing each
#: ride on ONE call whose whole-prompt ceiling is `cli_utils.MAX_PROMPT_BYTES`
#: (100 KB), and both now draw on a dozen sources; at 25 KB the measured split
#: cut the narrowest material to a tenth of itself. The metered provider is
#: last in every chain, so the extra bytes are usually free.
BUDGETS: dict[str, int] = {
    # Measured: the two lists want 49 KB and 69 KB respectively once sliced, so
    # 48 KB delivers the storyboard set whole and leaves nothing in the
    # prompt-writing set below half of what it asked for. The ceiling that
    # matters is the WHOLE prompt (`cli_utils.MAX_PROMPT_BYTES`, 100 KB) and
    # both of these calls carry a short brief beside the knowledge.
    "storyboard": 48_000,
    "write_prompt": 48_000,
}

_HEADING = re.compile(r"^##\s+(.*)$", re.M)


def _fold(text: str) -> str:
    """Accent-, emoji- and case-insensitive form for matching headings.

    The same heading is written three ways across these files — with
    diacritics, without them, and with a leading emoji — so matching the
    literal string finds one spelling and misses the rest.
    """
    stripped = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    # `Đ` is its own letter, not a D with a mark, so NFD leaves it alone — and
    # a heading like "TUYỆT ĐỐI" then folded to "tuyet đoi" and matched no
    # needle anyone would write. (It cost nothing until a selector needed one
    # of those headings; now two do.)
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    stripped = "".join(c if c.isalnum() or c.isspace() else " " for c in stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


#: Files already read, keyed by identity AND version.
#:
#: The key carries mtime and size so an edited skill file is re-read without a
#: restart. That matters here more than in most caches: these files belong to the
#: packaged tool, sit outside this repo, and the user updates them on their own
#: schedule — a cache keyed on the path alone would serve last week's prompt
#: rules until the server was restarted.
_CACHE: dict[tuple[str, int, int], Optional[str]] = {}

#: A ceiling, so a mistaken TASKS entry cannot grow this without bound.
_CACHE_MAX_FILES = 128


def _read(path: Path) -> Optional[str]:
    """The file's text, or None. Never raises — callers fall back to no
    knowledge rather than failing the request they were serving.

    Cached across calls, which is what `prompt_checks.check` has always claimed
    ("no I/O beyond reading the rule file once per process") and what this
    module's own docstring promised. Neither was true before: the 146 KiB Veo
    guide was re-read from disk on every prompt.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        logger.info("knowledge: cannot stat %s (%s)", path, exc)
        return None

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _CACHE:
        return _CACHE[key]

    try:
        text: Optional[str] = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.info("knowledge: cannot read %s (%s)", path, exc)
        text = None
    except UnicodeDecodeError as exc:
        # A skill file saved in another encoding used to raise straight through
        # storyboard, idea, review and revise — past a function whose whole
        # contract is "None, never an exception".
        logger.warning("knowledge: %s is not UTF-8 (%s), skipping it", path, exc)
        text = None

    if len(_CACHE) >= _CACHE_MAX_FILES:
        _CACHE.clear()
    _CACHE[key] = text
    return text


def _slice_sections(text: str, headings: tuple[str, ...]) -> str:
    """The named ``## `` sections, one topic at a time.

    Returns an empty string when nothing matches, so a renamed heading shows
    up as missing knowledge rather than as the whole file smuggled in.

    **Ordered by topic, not by position in the file.** A caller naming four
    topics gets the first section of each, then the second of each, and so on.
    In file order the Veo guide's audio subsections — all six of them, first in
    the file — filled the whole allowance, and `camera` and `prompt structure`
    were never delivered at all even though the caller asked for them by name.
    A cut now costs the tail of every topic instead of three topics entirely.
    """
    if not headings:
        return text
    wanted = [_fold(h) for h in headings]
    matches = list(_HEADING.finditer(text))
    buckets: list[list[str]] = [[] for _ in wanted]
    for index, match in enumerate(matches):
        folded = _fold(match.group(1))
        slot = next((i for i, needle in enumerate(wanted) if needle in folded), None)
        if slot is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        buckets[slot].append(text[match.start():end].rstrip())

    out: list[str] = []
    for depth in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if depth < len(bucket):
                out.append(bucket[depth])
    return "\n\n".join(out)


def _size(text: str) -> int:
    """Bytes, not characters. The budget is in bytes and a Vietnamese
    character is three of them — counting characters was how "448 KB" came to
    be compared against a byte ceiling."""
    return len(text.encode("utf-8"))


def _fit(body: str, allowance: int) -> tuple[str, bool]:
    """Trim ``body`` to ``allowance`` bytes, cutting at a section boundary.

    Returns ``(text, was_truncated)``. Cutting mid-sentence would hand the
    model a rule that stops halfway, which is worse than not sending the
    rule — so the cut lands on the last whole ``## `` section that fits, and
    only falls back to a paragraph boundary when even the first section is
    too big.
    """
    if allowance <= 0:
        return "", True
    if len(body.encode("utf-8")) <= allowance:
        return body, False

    matches = list(_HEADING.finditer(body))
    for match in reversed(matches):
        candidate = body[: match.start()].rstrip()
        if candidate and len(candidate.encode("utf-8")) <= allowance:
            return candidate, True

    # One oversized section: fall back to whole paragraphs.
    kept: list[str] = []
    used = 0
    for para in body.split("\n\n"):
        size = len(para.encode("utf-8")) + 2
        if used + size > allowance:
            break
        kept.append(para)
        used += size
    return "\n\n".join(kept).rstrip(), True


def sections_for(task: str, *, budget_bytes: Optional[int] = None) -> Knowledge:
    """Assemble the knowledge for one AI step, within a byte budget.

    Everything cut, in whole or in part, is named in ``dropped``.
    """
    sources = TASKS.get(task)
    if sources is None:
        raise KeyError(
            f"unknown knowledge task {task!r}; known: {', '.join(sorted(TASKS))}"
        )
    if budget_bytes is None:
        budget_bytes = BUDGETS.get(task, DEFAULT_BUDGET_BYTES)
    result = _assemble(sources, budget_bytes)
    if result.dropped:
        logger.info("knowledge: %s dropped %s", task, "; ".join(result.dropped))
    if not result.files:
        logger.warning(
            "knowledge: nothing loaded for %s — is ASSET_ROOT installed?", task
        )
    return result


def format_playbook(key: str, *, budget_bytes: int = 8_000) -> Knowledge:
    """One format playbook, by key. Empty ``Knowledge`` for an unknown key.

    Unknown is not an error: the key travels from a picker in the browser, and
    a stale option there must cost the caller its playbook, not its storyboard.
    """
    path = FORMATS.get(key)
    if path is None:
        logger.info("knowledge: no format playbook %r", key)
        return Knowledge()
    return load_paths([path], budget_bytes=budget_bytes)


def load_paths(
    paths: list[str], *, budget_bytes: int = DEFAULT_BUDGET_BYTES
) -> Knowledge:
    """Whole files by path, for callers that pick their own set.

    `script_genres` uses this: a genre names the on-disk files that cover it,
    and the caller switches supplements on, so the set is decided per call
    rather than declared in ``TASKS``.
    """
    return _assemble(tuple(Source(p) for p in paths), budget_bytes)


def _shares(needs: list[int], budget: int) -> list[int]:
    """Split ``budget`` across ``needs`` by need, not by headcount.

    An equal share per source read as fair and was not: a source that wants
    less than its share leaves the rest unspent, so the big file declared
    first was cut to a third of the budget while two small ones left the other
    two thirds empty. Measured on `write_prompt`, that is how the Veo guide —
    146 KiB, four named sections, the narrowest material in the set — came
    back at 3% of itself.

    So: everyone modest is satisfied first, and what they did not want is
    redivided among whoever still wants more, until nobody modest is left.
    """
    allowance = [0] * len(needs)
    remaining = budget
    pending = [i for i, need in enumerate(needs) if need > 0]

    while pending and remaining > 0:
        share = remaining // len(pending)
        if share <= 0:
            break
        modest = [i for i in pending if needs[i] <= share]
        if not modest:
            # Everyone left wants more than an equal share: split what is
            # left evenly and give the odd bytes to the first, which is the
            # most-useful one by declaration order.
            for i in pending:
                allowance[i] = share
            allowance[pending[0]] += remaining - share * len(pending)
            return allowance
        for i in modest:
            allowance[i] = needs[i]
            remaining -= needs[i]
            pending.remove(i)
    return allowance


def _assemble(sources: tuple[Source, ...], budget_bytes: int) -> Knowledge:
    """Read, slice, and share a byte budget across sources.

    Two passes, because the second needs what the first learns: how much each
    source actually wants. Sources are declared most-useful first, and that
    order decides who wins a tie — not who eats.
    """
    roots = _roots()
    result = Knowledge()

    # ── 1. read and slice, so the real sizes are known ────────────────
    prepared: list[tuple[Source, str, str]] = []
    for source in sources:
        root_key, _, relative = source.path.partition("/")
        root = roots.get(root_key)
        if root is None:  # pragma: no cover — guarded by a test on TASKS
            result.dropped.append(f"{source.path} (unknown root)")
            continue
        raw = _read(root / relative)
        if raw is None:
            result.dropped.append(f"{source.path} (missing)")
            continue
        body = _slice_sections(raw, source.headings).strip()
        if not body:
            result.dropped.append(f"{source.path} (no matching section)")
            continue
        prepared.append((source, f"\n\n===== {Path(relative).name} =====\n", body))

    # ── 2. allocate, fit, and hand the slack back once ────────────────
    needs = [_size(header) + _size(body) for _, header, body in prepared]
    allowance = _shares(needs, budget_bytes)
    fitted = [
        _fit(body, allowance[i] - _size(header))
        for i, (_, header, body) in enumerate(prepared)
    ]

    # `_fit` stops at a whole `## ` section, so a truncated source usually
    # takes less than it was given. Unspent bytes go back to whoever was cut,
    # in declaration order — without this the fair share is a ceiling that
    # nobody reaches and the budget is quietly under-used.
    slack = budget_bytes - sum(
        _size(header) + _size(text)
        for (_, header, _), (text, _) in zip(prepared, fitted, strict=True)
    )
    if slack > 0:
        for i, ((_, _header, body), (text, cut)) in enumerate(
            zip(prepared, fitted, strict=True)
        ):
            if not cut or slack <= 0:
                continue
            # What it holds now plus the slack — NOT its original allowance
            # plus the slack. The unspent part of its own share is already
            # inside `slack`, so adding both counted those bytes twice and
            # the assembled text went over budget.
            better, still_cut = _fit(body, _size(text) + slack)
            gained = _size(better) - _size(text)
            if gained > 0:
                slack -= gained
                fitted[i] = (better, still_cut)

    for (source, header, _), (text, cut) in zip(prepared, fitted, strict=True):
        if not text:
            result.dropped.append(f"{source.path} (no room left)")
            continue
        if cut:
            result.dropped.append(f"{source.path} (truncated to fit)")
        result.text += header + text
        result.files.append(source.path)

    result.text = result.text.strip()
    return result


def available() -> bool:
    """Whether the packaged knowledge base is present at all."""
    return all(root.is_dir() for root in _roots().values())
