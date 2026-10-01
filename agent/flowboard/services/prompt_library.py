"""The bundled fashion-posing prompt library.

``prompt video thời trang nam.md`` sits in the asset root and holds 47
hand-written Veo prompts, ~5KB each: a locked camera, a silent-output
clause, and a second-by-second motion timeline for one 8-second fashion
pose. The packaged tool's affiliate tab picks from them by id, storing the
choice as ``selected_fashion_prompt_id: "fashion_male_07"``.

**Importing a fashion workflow never needed this.** Its `prompt_mau` nodes
carry the whole prompt inline, and `template_import` already maps them to a
`prompt` node — measured by diffing `thoi_trang_nam_3_canh.json`'s node text
against library entry 7, which matches to the character once the file's
``====`` separator is dropped. What was missing is the other direction:
choosing one of the 47 without opening a 252KB markdown file by hand.

The id scheme is the packaged tool's: ``fashion_male_<n>`` zero-padded to
two digits, where ``n`` is the heading's own PROMPT number.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from flowboard.services import assets

logger = logging.getLogger(__name__)

LIBRARY_FILE = assets.ASSET_ROOT / "prompt video thời trang nam.md"

_HEADING = re.compile(r"^##\s*TÊN PROMPT:\s*", re.M)
_NUMBER = re.compile(r"^PROMPT\s+(\d+)", re.I)
#: Entries are separated by a rule of equals signs, which is presentation
#: and not part of the prompt. Left in, it would be sent to Veo.
_RULE = re.compile(r"^={5,}\s*$", re.M)


@dataclass(frozen=True)
class Entry:
    id: str
    number: int
    title: str
    body: str

    @property
    def summary(self) -> str:
        """The heading's description, without the "PROMPT n — " prefix."""
        _, _, rest = self.title.partition("—")
        return (rest or self.title).strip()


def _parse(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for block in _HEADING.split(text)[1:]:
        heading, _, rest = block.partition("\n")
        match = _NUMBER.match(heading.strip())
        if not match:
            logger.info("prompt_library: skipping block with no number: %.60s", heading)
            continue
        number = int(match.group(1))
        # The body starts after the literal "PROMPT:" label.
        _, sep, body = rest.partition("PROMPT:")
        body = (body if sep else rest).strip()
        body = _RULE.sub("", body).strip()
        if not body:
            continue
        entries.append(
            Entry(
                id=f"fashion_male_{number:02d}",
                number=number,
                title=heading.strip(),
                body=body,
            )
        )
    return entries


@lru_cache(maxsize=1)
def entries() -> tuple[Entry, ...]:
    """Every prompt in the library, in file order.

    Empty rather than raising when the file is absent: the asset root points
    at someone else's install, and a missing optional library should grey out
    a picker, not break the screen that hosts it.
    """
    try:
        text = LIBRARY_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.info("prompt_library: cannot read %s (%s)", LIBRARY_FILE, exc)
        return ()
    return tuple(_parse(text))


def get(entry_id: str) -> Optional[Entry]:
    """One entry by its packaged-tool id, or None.

    Accepts an unpadded id as well: the workflows write `fashion_male_07`,
    but a caller composing one from an integer would naturally produce
    `fashion_male_7`, and failing that lookup would be a puzzle rather than
    an error.
    """
    wanted = str(entry_id or "").strip().lower()
    if not wanted:
        return None
    for entry in entries():
        if entry.id == wanted:
            return entry
    match = re.fullmatch(r"fashion_male_(\d+)", wanted)
    if match:
        number = int(match.group(1))
        for entry in entries():
            if entry.number == number:
                return entry
    return None
