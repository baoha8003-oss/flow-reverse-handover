"""Rules a generation prompt must not break, checked without a model.

Every rule here comes from the packaged tool's own skill files, and every one
of them costs nothing to check: they are string work, they run before any
dispatch, and they catch the failures that otherwise surface as a rejected
request or a re-cut clip.

**The two headline rules point in opposite directions, and that is the whole
reason this module exists.**

============  ==================  ==================
              Visual description  Dialogue
============  ==================  ==================
``@@`` tags   required            **forbidden**
Real names    **forbidden**       allowed
============  ==================  ==================

A character tag inside spoken text gets read aloud as "at at Mai Anh". A
celebrity name inside a visual prompt trips Google's Safety / Celebrity /
Trademark filter and the request is refused — but the same name inside the
voiceover is fine, because the audio is spoken by a separate voice model
that filter never sees. `workflow-script-to-canvas/SKILL.md` states both,
and getting the pair backwards would flag every legitimate historical
voiceover while letting every visual prompt fail.

So the split between "visual" and "dialogue" is the load-bearing part of
this module, not the rule list.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    rule: str
    #: ``error`` blocks the prompt; ``warning`` is reported and passes.
    severity: str
    message: str
    #: The offending text, so a reader can find it without re-deriving.
    span: str = ""


#: Durations each lane accepts. `📉 LITE LOWER priority` is 4/6/8 only;
#: `🔥 OMNI flash` adds 10. Asking for 10 on a LITE lane is refused at
#: dispatch, which costs a round trip to discover.
LANE_DURATIONS: dict[str, frozenset[int]] = {
    "lite": frozenset({4, 6, 8}),
    "lite_relaxed": frozenset({4, 6, 8}),
    "fast": frozenset({4, 6, 8}),
    "fast_relaxed": frozenset({4, 6, 8}),
    "quality": frozenset({4, 6, 8}),
    "omni": frozenset({4, 6, 8, 10}),
}

#: Characters a scene may carry. Above this the generator blurs faces
#: together — the packaged tool caps LITE lanes at three and OMNI at ten.
LANE_MAX_CHARACTERS: dict[str, int] = {
    "omni": 10,
}
DEFAULT_MAX_CHARACTERS = 3

#: Longer than this and the generator starts ignoring the tail.
MAX_PROMPT_CHARS = 2_000

#: Words that introduce spoken text. Matched case- and accent-insensitively,
#: because these prompts are written by three different models and by hand.
#: Longest first: the alternation must not let `thoai` win inside
#: `loi thoai`, which would put "Lời" back in the description.
_DIALOGUE_MARKERS = (
    "loi thoai", "thuyet minh", "voice over", "voiceover", "narration",
    "dialogue", "thoai", "saying", "says", "noi", "doc", "vo",
)

#: A marker counts only as a LABEL — followed by a colon. As a bare substring
#: it was a trap: `thoải mái` folds to `thoai mai`, so an ordinary description
#: ("ánh nắng thoải mái") turned the rest of its own line into dialogue, and
#: a banned name after it stopped being checked at all. The inline form the
#: markers were there for (`she says "..."`) is caught by the quote rule
#: below, which is the reliable half of the pair.
_MARKER = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(m) for m in _DIALOGUE_MARKERS) + r")\s*:"
)

#: A new label ends a dialogue block. At most three words before the colon,
#: so a line of speech that happens to contain one ("Con về rồi đây mẹ: thật
#: sao") is not mistaken for a heading.
_LABEL = re.compile(r"^\s*\w+(?:\s+\w+){0,2}\s*:")

#: Tags are Unicode: `@@BàTrưng` is a name a writer will use. Matched ASCII
#: only, the span came back as `@@B` — reported as the offending text, and
#: compared against the cast list as the character "b".
_TAG = re.compile(r"@{1,2}(\w+)")

#: Straight and curly quotes, and the Vietnamese guillemets that show up in
#: text pasted out of Word. Two rules earn their keep here:
#:
#: * no newline inside a span — an unpaired quote used to swallow the rest of
#:   the prompt as "dialogue", which is the half the name check skips;
#: * a single quote only between non-letters, because `don't ... can't` is two
#:   apostrophes and was read as one quoted passage.
_QUOTED = re.compile(
    r"[\"“”«]([^\"“”»\n]{2,400})[\"“”»]"
    r"|(?<![^\W\d_])'([^'\n]{4,400})'(?![^\W\d_])"
)


def _fold(text: str) -> str:
    """Accent-free lowercase, **one character in for one character out**.

    The alignment is load-bearing: marker positions are found in the folded
    text and sliced out of the original, so a fold that changed the length
    would cut mid-word. Folding NFD input the obvious way does exactly that —
    `"a" + U+0301` is two characters that become one.

    `đ`/`Đ` fold to `d` as well. Without it "Trần Hưng Đạo" typed without its
    accents — how these prompts arrive about half the time — did not match the
    banned name read from the rule file, and the check quietly passed.
    """
    out: list[str] = []
    for ch in unicodedata.normalize("NFC", text):
        base = "".join(
            c for c in unicodedata.normalize("NFD", ch)
            if unicodedata.category(c) != "Mn"
        )
        base = base.replace("đ", "d").replace("Đ", "D")
        # Never empty (a lone combining mark) and never longer than one, or
        # the index into the fold stops being an index into the input.
        out.append((base or " ")[0].lower())
    return "".join(out)


def split_dialogue(prompt: str) -> tuple[str, str]:
    """Separate a prompt into (visual description, spoken text).

    Two things count as spoken: anything inside quotes, and anything after a
    dialogue marker on its own line. Both are needed — a model asked for
    dialogue writes ``Lời thoại: Con về rồi đây mẹ`` about as often as it
    writes ``she says "Con về rồi đây mẹ"``.

    Everything else is visual. The default matters: an unrecognised dialogue
    form is treated as visual, so a tag in it is flagged (noisy but safe)
    rather than a celebrity name in it being missed (silent and expensive).
    """
    prompt = unicodedata.normalize("NFC", prompt)
    spoken: list[str] = []
    remainder: list[str] = []
    #: True while inside a block opened by a label with nothing after it.
    carry = False

    for line in prompt.splitlines():
        folded = _fold(line)
        match = _MARKER.search(folded)
        if match:
            # Text before the label is still description.
            remainder.append(line[:match.start()])
            spoken.append(line[match.start():])
            # `Lời thoại:` with nothing after it means the speech is on the
            # lines BELOW. Read as one line, those lines were description —
            # so every name in a multi-line speech was flagged as a banned
            # visual name, and every tag in it went unflagged.
            carry = not line[match.end():].strip()
            continue
        if carry:
            if not line.strip() or _LABEL.match(folded):
                carry = False
            else:
                spoken.append(line)
                continue
        remainder.append(line)

    visual = "\n".join(remainder)
    for match in _QUOTED.finditer(visual):
        spoken.append(match.group(0))
    visual = _QUOTED.sub(" ", visual)
    return visual, "\n".join(spoken)


def banned_names() -> tuple[str, ...]:
    """The names the packaged tool refuses to put in a visual prompt.

    Read from `workflow-script-to-canvas/SKILL.md` at call time rather than
    copied here, so the list cannot drift from the file the tool itself
    follows. Falls back to the examples that file spells out when the asset
    library is missing — an empty list would silently disable the rule.
    """
    from flowboard.services import knowledge

    seeds = (
        "Hai Bà Trưng", "Ngô Quyền", "Trần Hưng Đạo", "Quang Trung", "Bác Hồ",
        "Tom & Jerry", "Tom and Jerry", "Hulk", "Spider-Man", "Spiderman",
        "Batman", "Iron Man", "Mickey Mouse", "Donald Trump", "Elon Musk",
    )
    try:
        text = knowledge.sections_for("prompt_rules").text
    except Exception:
        return seeds
    if not text:
        return seeds

    # The file lists its examples inside one italic run after "ví dụ:".
    match = re.search(r"ví dụ:\s*\*([^*]+)\*", text)
    if not match:
        return seeds
    found = tuple(
        part.strip(" .…")
        for part in match.group(1).split(",")
        if 2 < len(part.strip(" .…")) < 40
    )
    return found or seeds


def check(
    prompt: str,
    *,
    cast: Optional[Iterable[str]] = None,
    lane: Optional[str] = None,
    seconds: Optional[int] = None,
) -> list[Finding]:
    """Every rule, checked. Errors block; warnings are reported.

    Costs nothing — no model call, no I/O beyond reading the rule file once
    per process. Running this before dispatch turns a refused request into a
    line of text.
    """
    findings: list[Finding] = []
    visual, spoken = split_dialogue(prompt)
    known = {c.strip().lower() for c in (cast or []) if c and c.strip()}

    # 1. A tag inside spoken text is read aloud. "@@MaiAnh" becomes
    #    "at at Mai Anh" in the delivered audio.
    for match in _TAG.finditer(spoken):
        findings.append(Finding(
            rule="tag_in_dialogue",
            severity="error",
            message=(
                f"Tag {match.group(0)} nằm trong lời thoại — sẽ bị đọc thành "
                f"tiếng. Tag chỉ dùng ở phần mô tả hình."
            ),
            span=match.group(0),
        ))

    # 2. A tag naming nobody resolves to nothing: the generator gets a bare
    #    token where a character reference was meant.
    if known:
        for match in _TAG.finditer(visual):
            if match.group(1).lower() not in known:
                findings.append(Finding(
                    rule="unknown_tag",
                    severity="error",
                    message=(
                        f"Tag {match.group(0)} không khớp nhân vật nào "
                        f"({', '.join(sorted(known))})."
                    ),
                    span=match.group(0),
                ))

    # 3. Too many characters in one scene and the generator blends faces.
    entities = {m.group(1).lower() for m in re.finditer(r"@@([A-Za-z0-9_]+)", visual)}
    ceiling = LANE_MAX_CHARACTERS.get(lane or "", DEFAULT_MAX_CHARACTERS)
    if len(entities) > ceiling:
        findings.append(Finding(
            rule="too_many_characters",
            severity="error",
            message=(
                f"{len(entities)} nhân vật trong một cảnh, lane cho phép tối đa "
                f"{ceiling}. Tách cảnh ra."
            ),
            span=", ".join(sorted(entities)),
        ))

    # 4. A duration the lane does not offer is refused at dispatch — a round
    #    trip and a failed request to learn something checkable here.
    if lane and seconds is not None:
        allowed = LANE_DURATIONS.get(lane)
        if allowed and seconds not in allowed:
            findings.append(Finding(
                rule="bad_duration",
                severity="error",
                message=(
                    f"{seconds}s không hợp lệ cho lane {lane} "
                    f"(chỉ {sorted(allowed)})."
                ),
                span=f"{seconds}s",
            ))

    # 5. A real name in the VISUAL half trips the safety filter and the
    #    request is refused. The same name in the dialogue is fine, because
    #    the audio is spoken by a separate model that filter never sees —
    #    which is why this looks only at `visual`.
    folded_visual = _fold(visual)
    for name in banned_names():
        needle = _fold(name)
        if not needle:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", folded_visual):
            findings.append(Finding(
                rule="banned_name",
                severity="error",
                message=(
                    f"'{name}' trong phần mô tả hình sẽ bị bộ lọc từ chối. "
                    f"Tả bằng ngoại hình và trang phục thay vì gọi tên. "
                    f"(Trong lời thoại thì được phép.)"
                ),
                span=name,
            ))

    # 6. Past this length the generator starts ignoring the tail, so the
    #    detail the writer cared most about — usually written last — is the
    #    part that goes missing.
    if len(prompt) > MAX_PROMPT_CHARS:
        findings.append(Finding(
            rule="too_long",
            severity="warning",
            message=(
                f"Prompt dài {len(prompt)} ký tự (> {MAX_PROMPT_CHARS}); "
                f"phần cuối dễ bị bỏ qua."
            ),
        ))

    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


def as_feedback(findings: list[Finding]) -> str:
    """Findings as a correction note for the model that wrote the prompt."""
    return "\n".join(f"- [{f.rule}] {f.message}" for f in findings)
